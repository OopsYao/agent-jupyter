from __future__ import annotations

import json
import queue
import time
from collections import deque
from pathlib import Path

import pytest

import agent_jupyter.lifecycle as lifecycle
from agent_jupyter.lifecycle import (
    connection_files,
    discover_kernels,
    kernel_info,
    shutdown_kernel,
)


class FakeControlChannel:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, message: dict) -> None:
        self.messages.append(message)


class FakeSession:
    def msg(self, msg_type: str, content: dict) -> dict:
        return {"msg_type": msg_type, "content": content}


class FakeClient:
    def __init__(self, shell: list[dict] | None = None) -> None:
        self.shell = deque(shell or [])
        self.control_channel = FakeControlChannel()
        self.session = FakeSession()
        self.stopped = False
        self.shutdown_restart: bool | None = None

    def load_connection_file(self, path: str) -> None:
        self.connection = path

    def start_channels(self) -> None:
        pass

    def wait_for_ready(self, timeout: float) -> None:
        self.ready_timeout = timeout

    def kernel_info(self) -> str:
        return "info-request"

    def get_shell_msg(self, timeout: float) -> dict:
        if not self.shell:
            raise queue.Empty
        return self.shell.popleft()

    def shutdown(self, restart: bool) -> None:
        self.shutdown_restart = restart

    def stop_channels(self) -> None:
        self.stopped = True


def test_connection_files_scans_explicit_directory_without_deleting_stale(
    tmp_path: Path,
) -> None:
    connection = tmp_path / "kernel-example.json"
    connection.write_text("{}", encoding="utf-8")
    (tmp_path / "not-a-kernel.json").write_text("{}", encoding="utf-8")

    found = connection_files([tmp_path])

    assert str(connection) in found
    assert connection.exists()


def _write_connection(path: Path, *, hb_port: int = 10005) -> None:
    path.write_text(
        json.dumps(
            {
                "transport": "tcp",
                "ip": "127.0.0.1",
                "shell_port": hb_port - 4,
                "iopub_port": hb_port - 3,
                "stdin_port": hb_port - 2,
                "control_port": hb_port - 1,
                "hb_port": hb_port,
            }
        ),
        encoding="utf-8",
    )


def test_discover_kernels_omits_unreachable_and_malformed_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_connection(tmp_path / "kernel-live.json")
    _write_connection(tmp_path / "kernel-dead.json", hb_port=10015)
    (tmp_path / "kernel-malformed.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "jupyter_runtime_dir", lambda: str(tmp_path))

    def heartbeat(config: dict, timeout: float) -> bool:
        return config["hb_port"] == 10005

    result = discover_kernels(
        timeout=0.1,
        discovery_timeout=1.0,
        heartbeat_probe=heartbeat,
        client_factory=FakeClient,
    )

    assert result == {
        "kernels": [
            {
                "connection": str(tmp_path / "kernel-live.json"),
                "status": "responsive",
            }
        ],
        "complete": True,
    }


def test_discover_kernels_returns_compact_kernel_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = tmp_path / "kernel-live.json"
    _write_connection(connection)
    monkeypatch.setattr(lifecycle, "jupyter_runtime_dir", lambda: str(tmp_path))

    client = FakeClient(
        [
            {
                "msg_type": "kernel_info_reply",
                "parent_header": {"msg_id": "info-request"},
                "content": {
                    "implementation": "ipykernel",
                    "implementation_version": "6.0",
                    "language_info": {
                        "name": "python",
                        "version": "3.12.0",
                        "mimetype": "text/x-python",
                    },
                    "raw_only": "not exposed",
                },
            }
        ]
    )

    result = discover_kernels(
        timeout=0.1,
        discovery_timeout=1.0,
        heartbeat_probe=lambda config, timeout: True,
        client_factory=lambda: client,
    )

    assert result == {
        "kernels": [
            {
                "connection": str(connection),
                "status": "identified",
                "language": {"name": "python", "version": "3.12.0"},
                "implementation": {"name": "ipykernel", "version": "6.0"},
            }
        ],
        "complete": True,
    }
    assert "kernel_info" not in result["kernels"][0]


def test_discover_kernels_returns_partial_results_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_connection(tmp_path / "kernel-slow.json")
    monkeypatch.setattr(lifecycle, "jupyter_runtime_dir", lambda: str(tmp_path))

    def slow_heartbeat(config: dict, timeout: float) -> bool:
        time.sleep(0.1)
        return False

    result = discover_kernels(
        timeout=0.2,
        discovery_timeout=0.01,
        heartbeat_probe=slow_heartbeat,
    )

    assert result == {"kernels": [], "complete": False}


def test_kernel_info_filters_reply_by_parent_message_id() -> None:
    client = FakeClient(
        [
            {
                "msg_type": "kernel_info_reply",
                "parent_header": {"msg_id": "other"},
                "content": {"implementation": "wrong"},
            },
            {
                "msg_type": "kernel_info_reply",
                "parent_header": {"msg_id": "info-request"},
                "content": {"implementation": "ipython"},
            },
        ]
    )

    info = kernel_info("kernel.json", client_factory=lambda: client)

    assert info["implementation"] == "ipython"
    assert client.stopped is True


def test_shutdown_is_explicit_and_does_not_remove_connection_file(
    tmp_path: Path,
) -> None:
    connection = tmp_path / "kernel-example.json"
    connection.write_text("{}", encoding="utf-8")
    client = FakeClient()

    shutdown_kernel(str(connection), client_factory=lambda: client)

    assert client.shutdown_restart is False
    assert connection.exists()
    assert client.stopped is True
