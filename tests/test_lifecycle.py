from __future__ import annotations

import queue
from collections import deque
from pathlib import Path

from agent_jupyter.lifecycle import (
    connection_files,
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


def test_connection_files_scans_explicit_directory_without_deleting_stale(tmp_path: Path) -> None:
    connection = tmp_path / "kernel-example.json"
    connection.write_text("{}", encoding="utf-8")
    (tmp_path / "not-a-kernel.json").write_text("{}", encoding="utf-8")

    found = connection_files([tmp_path])

    assert str(connection) in found
    assert connection.exists()


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


def test_shutdown_is_explicit_and_does_not_remove_connection_file(tmp_path: Path) -> None:
    connection = tmp_path / "kernel-example.json"
    connection.write_text("{}", encoding="utf-8")
    client = FakeClient()

    shutdown_kernel(str(connection), client_factory=lambda: client)

    assert client.shutdown_restart is False
    assert connection.exists()
    assert client.stopped is True
