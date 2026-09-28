from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SAMPLE_BEHAVIOR_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "sample_behavior.py"
)
_SPEC = importlib.util.spec_from_file_location("sample_behavior", _SAMPLE_BEHAVIOR_PATH)
assert _SPEC and _SPEC.loader
sample_behavior = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sample_behavior)


def _event(event_type: str, **fields: object) -> str:
    return json.dumps({"type": event_type, **fields})


def test_manifest_has_unique_explicit_rows_and_no_cartesian_runtime() -> None:
    runs = sample_behavior._load_runs()
    assert len(runs) == len(sample_behavior.SCENARIOS) * 4
    assert len({run["run_id"] for run in runs}) == len(runs)
    assert {run["scenario"] for run in runs} == set(sample_behavior.SCENARIOS)
    assert all(
        set(run) == {"run_id", "provider", "model", "thinking", "scenario"}
        for run in runs
    )


def test_scenario_and_common_criteria_are_separate() -> None:
    assert sample_behavior.COMMON_CRITERIA
    assert all(
        "prompt" in scenario and "criteria" in scenario
        for scenario in sample_behavior.SCENARIOS.values()
    )


def test_skill_read_audit_requires_matching_successful_end_and_reports_order() -> None:
    skill_path = str(sample_behavior.SKILL_PATH)
    raw = "\n".join(
        [
            _event(
                "tool_execution_start",
                toolCallId="read-skill",
                toolName="read",
                args={"path": skill_path},
            ),
            _event(
                "tool_execution_end",
                toolCallId="read-skill",
                toolName="read",
                isError=False,
            ),
            _event(
                "tool_execution_start",
                toolCallId="bash-1",
                toolName="bash",
                args={"command": "true"},
            ),
            _event(
                "tool_execution_end",
                toolCallId="bash-1",
                toolName="bash",
                isError=True,
                result={
                    "content": [
                        {"type": "text", "text": "Behavior sampler gate blocked: test"}
                    ]
                },
            ),
        ]
    )
    audit = sample_behavior._skill_read_audit(raw, {"bash-1": "blocked"})
    assert audit["attempted"] is True
    assert audit["completed_successfully"] is True
    assert audit["matching_end"] is True
    assert audit["is_error_false"] is True
    assert audit["before_first_bash_attempt"] is True
    assert audit["before_first_gate"] is True

    failed = "\n".join(
        [
            _event(
                "tool_execution_start",
                toolCallId="read-skill",
                toolName="read",
                args={"path": skill_path},
            ),
            _event(
                "tool_execution_end",
                toolCallId="other-id",
                toolName="read",
                isError=False,
            ),
        ]
    )
    failed_audit = sample_behavior._skill_read_audit(failed)
    assert failed_audit["attempted"] is True
    assert failed_audit["completed_successfully"] is False
    assert failed_audit["matching_end"] is False


def test_details_preserves_repeated_attempts_and_gate_order() -> None:
    raw = "\n".join(
        [
            "not json",
            _event(
                "tool_execution_start",
                toolCallId="read-1",
                toolName="read",
                args={"path": "skill"},
            ),
            _event(
                "tool_execution_end",
                toolCallId="read-1",
                toolName="read",
                result={"content": [{"type": "text", "text": "ok"}]},
                isError=False,
            ),
            _event(
                "tool_execution_start",
                toolCallId="bash-1",
                toolName="bash",
                args={"command": "pwd"},
            ),
            _event(
                "tool_execution_end",
                toolCallId="bash-1",
                toolName="bash",
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": "Behavior sampler gate blocked: disabled",
                        }
                    ]
                },
                isError=True,
            ),
            _event(
                "tool_execution_start",
                toolCallId="read-2",
                toolName="read",
                args={"path": "outside"},
            ),
            _event(
                "tool_execution_end",
                toolCallId="read-2",
                toolName="read",
                result={},
                isError=True,
            ),
            _event(
                "message_end",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "final"}],
                },
            ),
        ]
    )
    attempts, reply = sample_behavior._details(raw, {"bash-1": "disabled"})
    assert [a["tool_name"] for a in attempts] == ["read", "bash", "read"]
    assert [a["status"] for a in attempts] == ["completed", "blocked", "error"]
    assert reply == "final"


def test_run_writes_case_and_trace_in_run_directory(
    tmp_path: Path, monkeypatch
) -> None:
    class Completed:
        stdout = "\n".join(
            [
                _event(
                    "tool_execution_start",
                    toolCallId="read-skill",
                    toolName="read",
                    args={"path": str(sample_behavior.SKILL_PATH)},
                ),
                _event(
                    "tool_execution_end",
                    toolCallId="read-skill",
                    toolName="read",
                    isError=False,
                ),
                _event(
                    "message_end",
                    message={
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    },
                ),
            ]
        )
        stderr = ""
        returncode = 0

    monkeypatch.setattr(
        sample_behavior.subprocess, "run", lambda *args, **kwargs: Completed()
    )
    run = {
        "run_id": "unique-run",
        "provider": "p",
        "model": "m",
        "thinking": "off",
        "scenario": "s",
    }
    scenario = {"id": "s", "prompt": "prompt", "criteria": ["scenario criterion"]}
    result = sample_behavior._run(tmp_path, run, scenario, ["common criterion"])
    case = json.loads((tmp_path / "unique-run" / "case.json").read_text())
    assert case == {
        "run_id": "unique-run",
        "model": {"provider": "p", "model": "m", "thinking": "off"},
        "prompt": "prompt",
        "criteria": {
            "common": ["common criterion"],
            "scenario": ["scenario criterion"],
        },
    }
    assert (tmp_path / "unique-run" / "trace.jsonl").exists()
    assert result["case_path"] == "unique-run/case.json"
    assert result["trace_path"] == "unique-run/trace.jsonl"
    assert result["skill_read_audit"]["completed_successfully"] is True
    sample_behavior._write_report(tmp_path, result)
    report = (tmp_path / "unique-run" / "report.md").read_text()
    assert '"completed_successfully": true' in report


def test_each_run_gets_an_independent_report_and_no_aggregate(tmp_path: Path) -> None:
    results = [
        {
            "run_id": run_id,
            "scenario": "s",
            "run_status": "completed",
            "case_path": f"{run_id}/case.json",
            "trace_path": f"{run_id}/trace.jsonl",
            "tool_attempts": [],
            "final_assistant_reply": reply,
        }
        for run_id, reply in (("run-1", "reply one"), ("run-2", "reply two"))
    ]
    for result in results:
        (tmp_path / result["run_id"] / "case.json").parent.mkdir()
        sample_behavior._write_report(tmp_path, result)

    assert not (tmp_path / "report.md").exists()
    for run_id, reply in (("run-1", "reply one"), ("run-2", "reply two")):
        report = (tmp_path / run_id / "report.md").read_text()
        assert "case.json" in report and "trace.jsonl" in report
        assert reply in report
        assert "criteria" not in report
        assert "exit status describes sampling completeness" in report
        assert "Attempts after the first gate block" in report


def test_report_includes_mechanical_skill_read_section(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sample_behavior._write_report(
        tmp_path,
        {
            "run_id": "run",
            "scenario": "s",
            "run_status": "completed",
            "case_path": "run/case.json",
            "trace_path": "run/trace.jsonl",
            "tool_attempts": [],
            "final_assistant_reply": "reply",
            "skill_read_audit": {
                "attempted": False,
                "completed_successfully": False,
                "matching_end": False,
                "is_error_false": False,
                "before_first_bash_attempt": None,
                "before_first_gate": None,
            },
        },
    )
    report = (run_dir / "report.md").read_text()
    assert "### Exact project Skill read (mechanical)" in report
    assert '"completed_successfully": false' in report
    assert "does not infer Skill compliance" in report


def test_report_uses_a_safe_fence_for_replies_with_backticks(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sample_behavior._write_report(
        tmp_path,
        {
            "run_id": "run",
            "scenario": "s",
            "run_status": "completed",
            "case_path": "run/case.json",
            "trace_path": "run/trace.jsonl",
            "tool_attempts": [],
            "final_assistant_reply": "before\n```\nafter",
        },
    )
    report = (run_dir / "report.md").read_text()
    assert "````\nbefore\n```\nafter\n````" in report


def test_main_returns_nonzero_for_incomplete_sampling(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        sample_behavior,
        "RUNS",
        (
            {
                "run_id": "r",
                "provider": "p",
                "model": "m",
                "thinking": "off",
                "scenario": "s",
            },
        ),
    )
    monkeypatch.setattr(
        sample_behavior,
        "SCENARIOS",
        {"s": {"id": "s", "prompt": "prompt", "criteria": []}},
    )
    monkeypatch.setattr(sample_behavior, "COMMON_CRITERIA", [])
    monkeypatch.setattr(
        sample_behavior,
        "_run",
        lambda *args, **kwargs: {
            "run_id": "r",
            "scenario": "s",
            "run_status": "timed_out",
            "tool_attempts": [],
            "final_assistant_reply": "",
            "case_path": "r/case.json",
            "trace_path": "r/trace.jsonl",
        },
    )
    assert sample_behavior.main.__name__ == "main"
    # Invoke the parser without touching the real sampler process.
    import sys

    monkeypatch.setattr(sys, "argv", ["sample_behavior", "--output", str(tmp_path)])
    assert sample_behavior.main() == 1
