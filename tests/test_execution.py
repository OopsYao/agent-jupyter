from __future__ import annotations

import queue
from collections import deque

import pytest

from agent_jupyter.execution import ExecutionTimeoutError, KernelExecutor


def message(msg_type: str, parent: str | None, **content: object) -> dict:
    return {
        "msg_type": msg_type,
        "parent_header": {} if parent is None else {"msg_id": parent},
        "content": content,
    }


class FakeClient:
    def __init__(self, shell: list[dict], iopub: list[dict]) -> None:
        self.shell = deque(shell)
        self.iopub = deque(iopub)
        self.ready_timeout = None
        self.execute_kwargs: dict[str, object] | None = None
        self.stopped = False

    def load_connection_file(self, path: str) -> None:
        self.connection_file = path

    def start_channels(self) -> None:
        pass

    def wait_for_ready(self, timeout: float) -> None:
        self.ready_timeout = timeout

    def execute(self, code: str, **kwargs: object) -> str:
        self.code = code
        self.execute_kwargs = kwargs
        return "wanted"

    def get_shell_msg(self, timeout: float) -> dict:
        if not self.shell:
            raise queue.Empty
        return self.shell.popleft()

    def get_iopub_msg(self, timeout: float) -> dict:
        if not self.iopub:
            raise queue.Empty
        return self.iopub.popleft()

    def stop_channels(self) -> None:
        self.stopped = True


def test_collects_only_matching_outputs_until_reply_and_idle() -> None:
    client = FakeClient(
        shell=[
            message("execute_reply", "other", status="ok", execution_count=99),
            message("execute_reply", "wanted", status="ok", execution_count=4),
        ],
        iopub=[
            message("stream", "other", name="stdout", text="ignore\n"),
            message("stream", "wanted", name="stdout", text="hello\n"),
            message(
                "display_data",
                "wanted",
                data={"text/plain": "shown"},
                metadata={},
            ),
            message(
                "execute_result",
                "wanted",
                execution_count=4,
                data={"text/plain": "42"},
                metadata={},
            ),
            message(
                "error",
                "wanted",
                ename="ValueError",
                evalue="bad",
                traceback=["ValueError: bad"],
            ),
            message("status", "wanted", execution_state="idle"),
        ],
    )

    result = KernelExecutor(
        "connection.json", connection_timeout=2, client_factory=lambda: client
    ).execute("print('hello')")

    assert result.status == "ok"
    assert result.execution_count == 4
    assert [output["type"] for output in result.outputs] == [
        "stream",
        "display_data",
        "execute_result",
        "error",
    ]
    assert client.execute_kwargs is not None
    assert client.execute_kwargs["allow_stdin"] is False
    assert client.stopped is True


def test_execution_timeout_does_not_interrupt_or_shutdown() -> None:
    client = FakeClient([], [])

    with pytest.raises(ExecutionTimeoutError):
        KernelExecutor(
            "connection.json", execution_timeout=0.001, client_factory=lambda: client
        ).execute("while True: pass")

    assert client.stopped is True
    assert not hasattr(client, "interrupt_called")
    assert not hasattr(client, "shutdown_called")
