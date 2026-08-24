"""Kernel discovery, inspection, and explicit lifecycle operations."""

from __future__ import annotations

import json
import platform
import queue
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from pathlib import Path
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient
from jupyter_client.manager import KernelManager
from jupyter_core.paths import jupyter_runtime_dir
from zmq import POLLIN, REQ, Context

from .execution import AgentJupyterError, ConnectionError


class KernelLifecycleError(AgentJupyterError):
    """An expected failure while discovering or managing a kernel."""


class UnsupportedPlatformError(KernelLifecycleError):
    """The requested operation is not supported on this platform."""


ClientFactory = Callable[[], Any]
HeartbeatProbe = Callable[[dict[str, Any], float], bool]

_REQUIRED_CONNECTION_FIELDS = (
    "transport",
    "ip",
    "shell_port",
    "iopub_port",
    "stdin_port",
    "control_port",
    "hb_port",
)


def connection_files(runtime_dirs: Iterable[str | Path] = ()) -> list[str]:
    """Return connection files from the standard and explicitly named dirs.

    Discovery is deliberately filesystem-only: stale files are retained and
    are reported by callers when their kernel-info probe fails.
    """
    directories: list[Path] = [Path(jupyter_runtime_dir())]
    directories.extend(Path(directory) for directory in runtime_dirs)

    result: list[str] = []
    seen: set[Path] = set()
    for directory in directories:
        try:
            directory = directory.expanduser().resolve()
        except OSError:
            continue
        if directory in seen or not directory.is_dir():
            continue
        seen.add(directory)
        try:
            matches = sorted(directory.glob("kernel-*.json"))
        except OSError:
            continue
        for path in matches:
            if path.is_file():
                result.append(str(path))
    return result


def kernel_info(
    connection: str,
    *,
    timeout: float = 10.0,
    client_factory: ClientFactory = BlockingKernelClient,
) -> dict[str, Any]:
    """Connect to *connection* and return the kernel-info reply content."""
    client = client_factory()
    try:
        _connect(client, connection, timeout)
        msg_id = client.kernel_info()
        return _get_kernel_info_reply(client, msg_id, timeout)
    finally:
        with suppress(Exception):
            client.stop_channels()


def _connection_config(connection: str) -> dict[str, Any] | None:
    """Load enough connection metadata to reject clearly unusable files."""
    try:
        value = json.loads(Path(connection).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if not all(field in value for field in _REQUIRED_CONNECTION_FIELDS):
        return None
    if value["transport"] not in {"tcp", "ipc"} or not isinstance(value["ip"], str):
        return None
    if not all(
        isinstance(value[field], int) and value[field] >= 0
        for field in _REQUIRED_CONNECTION_FIELDS[2:]
    ):
        return None
    return value


def _heartbeat_responds(config: dict[str, Any], timeout: float) -> bool:
    """Perform one strictly bounded Jupyter heartbeat exchange."""
    transport = config["transport"]
    endpoint = (
        f"tcp://{config['ip']}:{config['hb_port']}"
        if transport == "tcp"
        else f"{transport}://{config['ip']}-{config['hb_port']}"
    )
    socket = Context.instance().socket(REQ)
    socket.linger = 0
    try:
        socket.connect(endpoint)
        socket.send(b"ping")
        if not socket.poll(max(0, int(timeout * 1000)), POLLIN):
            return False
        return socket.recv() == b"ping"
    except Exception:
        return False
    finally:
        socket.close()


def _kernel_summary(info: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Extract the stable, compact summary exposed by kernel discovery."""
    summary: dict[str, dict[str, str]] = {}

    language_info = info.get("language_info")
    if isinstance(language_info, dict):
        language = {
            field: language_info[field]
            for field in ("name", "version")
            if isinstance(language_info.get(field), str)
        }
        if language:
            summary["language"] = language

    implementation: dict[str, str] = {}
    if isinstance(info.get("implementation"), str):
        implementation["name"] = info["implementation"]
    if isinstance(info.get("implementation_version"), str):
        implementation["version"] = info["implementation_version"]
    if implementation:
        summary["implementation"] = implementation

    return summary


def probe_connection(
    connection: str,
    *,
    timeout: float = 2.0,
    client_factory: ClientFactory = BlockingKernelClient,
    heartbeat_probe: HeartbeatProbe = _heartbeat_responds,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a responsive connection, optionally with compact identifying information."""
    config = config or _connection_config(connection)
    if config is None or not heartbeat_probe(config, timeout):
        return None

    entry: dict[str, Any] = {"connection": connection, "status": "responsive"}
    client = client_factory()
    try:
        _start_client(client, connection)
        msg_id = client.kernel_info()
        info = _get_kernel_info_reply(client, msg_id, timeout)
        entry.update(_kernel_summary(info))
        entry["status"] = "identified"
    except Exception:
        return entry
    finally:
        with suppress(Exception):
            client.stop_channels()
    return entry


def discover_kernels(
    runtime_dirs: Iterable[str | Path] = (),
    *,
    timeout: float = 1.0,
    discovery_timeout: float = 3.0,
    max_concurrency: int = 8,
    client_factory: ClientFactory = BlockingKernelClient,
    heartbeat_probe: HeartbeatProbe = _heartbeat_responds,
) -> dict[str, Any]:
    """Discover usable kernels concurrently within an overall deadline."""
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for connection in connection_files(runtime_dirs):
        config = _connection_config(connection)
        if config is None:
            continue
        try:
            modified = Path(connection).stat().st_mtime
        except OSError:
            continue
        candidates.append((modified, connection, config))
    candidates.sort(reverse=True)

    if not candidates:
        return {"kernels": [], "complete": True}

    deadline = time.monotonic() + discovery_timeout
    executor = ThreadPoolExecutor(max_workers=max(1, max_concurrency))
    futures: set[Future[dict[str, Any] | None]] = {
        executor.submit(
            probe_connection,
            connection,
            timeout=min(timeout, max(0.0, deadline - time.monotonic())),
            client_factory=client_factory,
            heartbeat_probe=heartbeat_probe,
            config=config,
        )
        for _, connection, config in candidates
    }
    kernels: list[dict[str, Any]] = []
    pending = futures
    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(
                pending, timeout=remaining, return_when=FIRST_COMPLETED
            )
            for future in done:
                with suppress(Exception):
                    entry = future.result()
                    if entry is not None:
                        kernels.append(entry)
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    order = {connection: index for index, (_, connection, _) in enumerate(candidates)}
    kernels.sort(key=lambda entry: order[entry["connection"]])
    return {"kernels": kernels, "complete": not pending}


def interrupt_kernel(
    connection: str,
    *,
    client_factory: ClientFactory = BlockingKernelClient,
) -> None:
    """Send one explicit interrupt request to an existing kernel."""
    client = client_factory()
    try:
        _start_client(client, connection)
        message = client.session.msg("interrupt_request", content={})
        client.control_channel.send(message)
    except Exception as exc:
        raise ConnectionError(
            f"could not interrupt kernel using {connection!r}: {exc}"
        ) from exc
    finally:
        with suppress(Exception):
            client.stop_channels()


def shutdown_kernel(
    connection: str,
    *,
    client_factory: ClientFactory = BlockingKernelClient,
) -> None:
    """Send one explicit shutdown request without deleting its connection file."""
    client = client_factory()
    try:
        _start_client(client, connection)
        client.shutdown(restart=False)
    except Exception as exc:
        raise ConnectionError(
            f"could not shutdown kernel using {connection!r}: {exc}"
        ) from exc
    finally:
        with suppress(Exception):
            client.stop_channels()


def start_kernel(
    kernel_name: str = "python3",
    *,
    startup_timeout: float = 60.0,
    runtime_dir: str | Path | None = None,
    manager_factory: Callable[..., Any] = KernelManager,
) -> str:
    """Start an independent kernel and return its retained connection file."""
    if platform.system() not in {"Linux", "Darwin"}:
        raise UnsupportedPlatformError(
            "kernel start is supported only on Linux and macOS"
        )

    target_dir = (
        Path(runtime_dir) if runtime_dir is not None else Path(jupyter_runtime_dir())
    )
    target_dir = target_dir.expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise KernelLifecycleError(
            f"could not create runtime directory: {exc}"
        ) from exc

    connection = target_dir / f"kernel-{uuid.uuid4().hex}.json"
    manager = manager_factory(kernel_name=kernel_name, connection_file=str(connection))
    client: Any | None = None
    try:
        # independent=True prevents ipykernel's parent monitor from shutting
        # down the child when this short-lived CLI process exits.
        manager.start_kernel(
            independent=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started_client = manager.client()
        client = started_client
        started_client.start_channels()
        started_client.wait_for_ready(timeout=startup_timeout)
        connection_file = str(manager.connection_file)
        # KernelManager.__del__ otherwise removes files it wrote.  This start
        # command intentionally leaves the file available for later commands.
        if hasattr(manager, "_connection_file_written"):
            manager._connection_file_written = False
        return connection_file
    except Exception as exc:
        if client is not None:
            with suppress(Exception):
                client.stop_channels()
        with suppress(Exception):
            manager.shutdown_kernel()
        if isinstance(exc, AgentJupyterError):
            raise
        raise KernelLifecycleError(
            f"could not start kernel {kernel_name!r}: {exc}"
        ) from exc
    finally:
        if client is not None:
            with suppress(Exception):
                client.stop_channels()


def _start_client(client: Any, connection: str) -> None:
    client.load_connection_file(connection)
    client.start_channels()


def _connect(client: Any, connection: str, timeout: float) -> None:
    try:
        _start_client(client, connection)
        client.wait_for_ready(timeout=timeout)
    except Exception as exc:
        raise ConnectionError(
            f"could not connect to kernel using {connection!r}: {exc}"
        ) from exc


def _get_kernel_info_reply(client: Any, msg_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectionError(
                f"kernel_info did not finish within {timeout:g} seconds"
            )
        try:
            message = client.get_shell_msg(timeout=remaining)
        except queue.Empty as exc:
            raise ConnectionError(
                f"kernel_info did not finish within {timeout:g} seconds"
            ) from exc
        parent = message.get("parent_header", {})
        if (
            isinstance(parent, dict)
            and parent.get("msg_id") == msg_id
            and message.get("msg_type") == "kernel_info_reply"
        ):
            content = message.get("content", {})
            if not isinstance(content, dict):
                raise ConnectionError("kernel_info reply had invalid content")
            return content


def json_text(value: object) -> str:
    """Encode a command result as one stable JSON line."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
