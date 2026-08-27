from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SAMPLE_BEHAVIOR_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "sample_behavior.py"
)
_SAMPLE_BEHAVIOR_SPEC = importlib.util.spec_from_file_location(
    "sample_behavior", _SAMPLE_BEHAVIOR_PATH
)
assert _SAMPLE_BEHAVIOR_SPEC is not None
assert _SAMPLE_BEHAVIOR_SPEC.loader is not None
sample_behavior = importlib.util.module_from_spec(_SAMPLE_BEHAVIOR_SPEC)
_SAMPLE_BEHAVIOR_SPEC.loader.exec_module(sample_behavior)


def _event(event_type: str, **fields: object) -> str:
    return json.dumps({"type": event_type, **fields})


def test_details_lists_repeated_attempts_in_jsonl_order() -> None:
    blocked_result = {
        "content": [
            {
                "type": "text",
                "text": (
                    f"{sample_behavior.BLOCKED_REASON_PREFIX} bash is disabled"
                ),
            }
        ]
    }
    raw = "\n".join(
        [
            "not json",
            _event(
                "tool_execution_start",
                toolCallId="read-1",
                toolName="read",
                args={"path": str(sample_behavior.SKILL_PATH), "limit": 10},
            ),
            _event(
                "tool_execution_end",
                toolCallId="read-1",
                toolName="read",
                result={"content": [{"type": "text", "text": "skill"}]},
                isError=False,
            ),
            _event(
                "tool_execution_start",
                toolCallId="bash-1",
                toolName="bash",
                args={"command": "pwd", "timeout": 3},
            ),
            _event(
                "tool_execution_end",
                toolCallId="bash-1",
                toolName="bash",
                result=blocked_result,
                isError=True,
            ),
            _event(
                "tool_execution_start",
                toolCallId="read-2",
                toolName="read",
                args={"path": "/outside/allowed/path"},
            ),
            _event(
                "tool_execution_end",
                toolCallId="read-2",
                toolName="read",
                result={"content": [{"type": "text", "text": "denied"}]},
                isError=True,
            ),
            _event(
                "message_end",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "final reply"}],
                },
            ),
        ]
    )

    attempts, reply = sample_behavior._details(raw)

    assert [attempt["tool_name"] for attempt in attempts] == [
        "read",
        "bash",
        "read",
    ]
    assert [attempt["status"] for attempt in attempts] == [
        "completed",
        "blocked",
        "error",
    ]
    assert attempts[0]["arguments"] == {
        "path": str(sample_behavior.SKILL_PATH),
        "limit": 10,
    }
    assert attempts[1]["arguments"] == {"command": "pwd", "timeout": 3}
    assert reply == "final reply"


def test_report_keeps_full_attempts_and_final_reply(tmp_path: Path) -> None:
    result = {
        "label": "sample",
        "scenario": "abstract-kernel",
        "raw_stream": "sample-abstract-kernel.jsonl",
        "run_status": "completed",
        "tool_attempts": [
            {
                "tool_call_id": "call-1",
                "tool_name": "read",
                "arguments": {"path": str(sample_behavior.SKILL_PATH)},
                "status": "completed",
            },
            {
                "tool_call_id": "call-2",
                "tool_name": "read",
                "arguments": {"path": "/outside/allowed/path"},
                "status": "blocked",
            },
        ],
        "final_assistant_reply": "preserved final reply",
    }

    sample_behavior._write_report(tmp_path, [result])
    report = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert report.count('"tool_name": "read"') == 2
    assert str(sample_behavior.SKILL_PATH) in report
    assert '"status": "blocked"' in report
    assert "preserved final reply" in report
    assert "raw_stream" in report
    assert "Completed: 1" in report
    assert "exit status describes sampling completeness" in report
    assert "Attempts through the first gate block" in report
    assert "Attempts after the first gate block" in report


def test_run_wires_only_the_explicit_gate_with_read_and_bash(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(sample_behavior.subprocess, "run", fake_run)

    sample_behavior._run(
        tmp_path,
        "sample",
        "provider/model",
        "off",
        "explicit-connection",
        "请通过 `/tmp/kernel-example.json` 执行 `print(40 + 2)`。",
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert "--no-extensions" in command
    assert command[command.index("--extension") + 1] == str(
        sample_behavior.GATE_EXTENSION_PATH
    )
    assert command[command.index("--tools") + 1] == "read,bash"
    assert command.count("--extension") == 1
    assert command.count("--skill") == 1
    assert command[-1] == "请通过 `/tmp/kernel-example.json` 执行 `print(40 + 2)`。"


def test_run_marks_nonzero_pi_exit_as_failed(tmp_path: Path, monkeypatch) -> None:
    class Completed:
        stdout = ""
        stderr = "provider failed"
        returncode = 1

    monkeypatch.setattr(sample_behavior.subprocess, "run", lambda *args, **kwargs: Completed())

    result = sample_behavior._run(
        tmp_path,
        "sample",
        "provider/model",
        "off",
        "explicit-connection",
        "prompt",
    )

    assert result["run_status"] == "failed"
    assert result["returncode"] == 1


def test_main_exit_code_describes_sampling_completeness(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sample_behavior, "RUNS", (("sample", "model", "off"),))
    monkeypatch.setattr(sample_behavior, "SCENARIOS", (("scenario", "prompt"),))
    monkeypatch.setattr(sys, "argv", ["sample_behavior", "--output", str(tmp_path)])
    monkeypatch.setattr(
        sample_behavior,
        "_run",
        lambda *args, **kwargs: {
            "label": "sample",
            "scenario": "scenario",
            "run_status": "timed_out",
            "tool_attempts": [],
            "final_assistant_reply": "",
        },
    )

    assert sample_behavior.main() == 1
