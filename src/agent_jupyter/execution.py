"""Jupyter kernel connection and execution protocol handling."""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient


class AgentJupyterError(Exception):
    """Base class for expected agent-jupyter failures."""


class ConnectionError(AgentJupyterError):
    """The connection file could not be loaded or the kernel was not ready."""


class ExecutionTimeoutError(AgentJupyterError):
    """The kernel did not finish execution before the configured deadline."""


@dataclass(frozen=True)
class ExecutionResult:
    """The protocol-relevant result of one execute request."""

    msg_id: str
    status: str
    execution_count: int | None
    outputs: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Return the stable, JSON-serializable result envelope."""
        return {
            "status": self.status,
            "execution_count": self.execution_count,
            "outputs": self.outputs,
        }


ClientFactory = Callable[[], Any]


class KernelExecutor:
    """Connect to and execute code in an existing kernel.

    This class only manages the client channels. It deliberately never calls a
    kernel shutdown or interrupt operation; closing the channels leaves the
    externally managed kernel running.
    """

    def __init__(
        self,
        connection_file: str,
        *,
        connection_timeout: float = 10.0,
        execution_timeout: float = float("inf"),
        client_factory: ClientFactory = BlockingKernelClient,
    ) -> None:
        self.connection_file = connection_file
        self.connection_timeout = connection_timeout
        self.execution_timeout = execution_timeout
        self.client_factory = client_factory

    def execute(self, code: str, *, allow_stdin: bool = False) -> ExecutionResult:
        """Execute *code* and collect only messages belonging to its msg_id."""
        client = self.client_factory()
        try:
            self._connect(client)
            msg_id = client.execute(
                code,
                silent=False,
                store_history=True,
                allow_stdin=allow_stdin,
                stop_on_error=False,
            )
            return self._collect(client, msg_id)
        finally:
            # stop_channels only closes this client's channels. It does not
            # send a shutdown request to the kernel.
            # A connection failure should retain its useful original
            # exception and must not be masked by cleanup.
            with suppress(Exception):
                client.stop_channels()

    def _connect(self, client: Any) -> None:
        try:
            client.load_connection_file(self.connection_file)
            client.start_channels()
            client.wait_for_ready(timeout=self.connection_timeout)
        except Exception as exc:
            raise ConnectionError(
                f"could not connect to kernel using {self.connection_file!r}: {exc}"
            ) from exc

    def _collect(self, client: Any, msg_id: str) -> ExecutionResult:
        deadline = time.monotonic() + self.execution_timeout
        shell_reply: dict[str, Any] | None = None
        idle = False
        outputs: list[dict[str, Any]] = []

        while shell_reply is None or not idle:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExecutionTimeoutError(
                    f"execution did not finish within {self.execution_timeout:g} seconds"
                )
            poll_timeout = min(remaining, 0.05)

            if shell_reply is None:
                try:
                    message = client.get_shell_msg(timeout=poll_timeout)
                except queue.Empty:
                    message = None
                if (
                    message is not None
                    and _parent_msg_id(message) == msg_id
                    and message.get("msg_type") == "execute_reply"
                ):
                    shell_reply = message

            if not idle:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExecutionTimeoutError(
                        f"execution did not finish within {self.execution_timeout:g} seconds"
                    )
                try:
                    message = client.get_iopub_msg(timeout=min(remaining, 0.05))
                except queue.Empty:
                    message = None
                if message is not None and _parent_msg_id(message) == msg_id:
                    msg_type = message.get("msg_type")
                    if msg_type == "status":
                        if message.get("content", {}).get("execution_state") == "idle":
                            idle = True
                    elif msg_type in {
                        "stream",
                        "display_data",
                        "execute_result",
                        "error",
                    }:
                        outputs.append(_output_from_message(message))

        content = shell_reply.get("content", {})
        return ExecutionResult(
            msg_id=msg_id,
            status=content.get("status", "error"),
            execution_count=content.get("execution_count"),
            outputs=outputs,
        )


def _parent_msg_id(message: dict[str, Any]) -> str | None:
    parent = message.get("parent_header")
    if not isinstance(parent, dict):
        return None
    value = parent.get("msg_id")
    return value if isinstance(value, str) else None


def _output_from_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize the four output message types without losing their payload."""
    msg_type = message["msg_type"]
    content = message.get("content", {})
    if msg_type == "stream":
        return {
            "type": "stream",
            "name": content.get("name", "stdout"),
            "text": content.get("text", ""),
        }
    if msg_type in {"display_data", "execute_result"}:
        result: dict[str, Any] = {
            "type": msg_type,
            "data": content.get("data", {}),
            "metadata": content.get("metadata", {}),
        }
        if msg_type == "execute_result":
            result["execution_count"] = content.get("execution_count")
        if "transient" in content:
            result["transient"] = content["transient"]
        return result
    return {
        "type": "error",
        "ename": content.get("ename", "Error"),
        "evalue": content.get("evalue", ""),
        "traceback": content.get("traceback", []),
    }


def output_text(output: dict[str, Any]) -> str:
    """Render one normalized output for human-readable mode."""
    output_type = output["type"]
    if output_type == "stream":
        return str(output.get("text", ""))
    if output_type == "error":
        traceback = output.get("traceback") or []
        if isinstance(traceback, list) and traceback:
            return "\n".join(str(line) for line in traceback)
        return f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"

    data = output.get("data", {})
    if isinstance(data, dict) and "text/plain" in data:
        value = data["text/plain"]
        return "".join(str(item) for item in value) if isinstance(value, list) else str(value)
    return str(data)
