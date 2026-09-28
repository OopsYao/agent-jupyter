"""Sample agent-jupyter Skill behavior using explicit manifest rows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "src/agent_jupyter/resources/agent_jupyter_skill.md"
TIMEOUT_SECONDS = 120
MANIFEST_PATH = Path(__file__).with_name("sample_manifest.jsonl")
SCENARIOS_PATH = Path(__file__).with_name("sample_scenarios.json")
CRITERIA_PATH = Path(__file__).with_name("sample_criteria.json")
GATE_EXTENSION_PATH = Path(__file__).with_name("sample_gate.ts")
BLOCKED_REASON_PREFIX = "Behavior sampler gate blocked:"
GATE_AUDIT_PREFIX = "AGENT_JUPYTER_SAMPLE_GATE:"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid sampling config {path}: {exc}") from exc


def _load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, dict[str, Any]]:
    value = _load_json(path)
    if not isinstance(value, list):
        raise ValueError("Scenario config must be a list")
    scenarios = {}
    for entry in value:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not isinstance(entry.get("prompt"), str)
        ):
            raise ValueError("Each scenario requires string id and prompt")
        if entry["id"] in scenarios:
            raise ValueError(f"Duplicate scenario id: {entry['id']}")
        scenarios[entry["id"]] = entry
    return scenarios


def _load_criteria(path: Path = CRITERIA_PATH) -> list[Any]:
    value = _load_json(path)
    if not isinstance(value, list):
        raise ValueError("Common criteria config must be a list")
    return value


def _load_runs(path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], ...]:
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            required = ("run_id", "provider", "model", "thinking", "scenario")
            if not isinstance(entry, dict) or any(
                not isinstance(entry.get(k), str) for k in required
            ):
                raise ValueError(
                    "run_id, model configuration, and scenario are required"
                )
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*", entry["run_id"]
            ) or entry["run_id"] in {".", ".."}:
                raise ValueError("run_id must be a safe directory name")
            if entry["run_id"] in seen:
                raise ValueError(f"duplicate run_id {entry['run_id']}")
            seen.add(entry["run_id"])
            runs.append(entry)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid sampling manifest line {line_number}: {exc}"
            ) from exc
    return tuple(runs)


SCENARIOS = _load_scenarios()
COMMON_CRITERIA = _load_criteria()
RUNS = _load_runs()


def _as_text(value: str | bytes | None) -> str:
    return (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else value or ""
    )


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _tool_event_name(event: dict[str, Any]) -> str | None:
    name = event.get("toolName") or event.get("tool_name") or event.get("name")
    return name if isinstance(name, str) else None


def _tool_result_text(result: Any) -> str:
    return _text_content(result.get("content")) if isinstance(result, dict) else ""


def _tool_status(event: dict[str, Any]) -> str:
    is_error = event.get("isError")
    if isinstance(is_error, bool):
        if not is_error:
            return "completed"
        return (
            "blocked"
            if _tool_result_text(event.get("result")).startswith(BLOCKED_REASON_PREFIX)
            else "error"
        )
    return "unknown"


def _gate_blocks(stderr: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for line in stderr.splitlines():
        if not line.startswith(GATE_AUDIT_PREFIX):
            continue
        try:
            record = json.loads(line.removeprefix(GATE_AUDIT_PREFIX))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(record, dict)
            and isinstance(record.get("toolCallId"), str)
            and isinstance(record.get("reason"), str)
        ):
            blocks[record["toolCallId"]] = record["reason"]
    return blocks


def _details(
    lines: str, gate_blocks: dict[str, str] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Return every tool attempt in stream order and the last assistant reply."""
    attempts: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    replies: list[str] = []
    for line in lines.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_execution_start":
            attempt = {
                "tool_name": _tool_event_name(event),
                "arguments": event.get("args"),
                "status": "unknown",
            }
            call_id = event.get("toolCallId")
            if isinstance(call_id, str):
                attempt["tool_call_id"] = call_id
                by_id[call_id] = attempt
            attempts.append(attempt)
        elif event.get("type") == "tool_execution_end":
            call_id = event.get("toolCallId")
            attempt = by_id.get(call_id) if isinstance(call_id, str) else None
            if attempt is None:
                attempt = {
                    "tool_name": _tool_event_name(event),
                    "arguments": event.get("args"),
                    "status": "unknown",
                }
                if isinstance(call_id, str):
                    attempt["tool_call_id"] = call_id
                    by_id[call_id] = attempt
                attempts.append(attempt)
            attempt["status"] = _tool_status(event)
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _text_content(message.get("content"))
            if text:
                replies.append(text)
        if event.get("role") == "assistant":
            text = _text_content(event.get("content"))
            if text:
                replies.append(text)
    for call_id, reason in (gate_blocks or {}).items():
        if call_id in by_id:
            by_id[call_id]["status"] = "blocked"
            by_id[call_id]["blocked_reason"] = reason
    return attempts, replies[-1] if replies else ""


def _run(
    output_dir: Path,
    run: dict[str, Any],
    scenario: dict[str, Any],
    common_criteria: list[Any],
) -> dict[str, Any]:
    run_id = run["run_id"]
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    case = {
        "run_id": run_id,
        "model": {
            "provider": run["provider"],
            "model": run["model"],
            "thinking": run["thinking"],
        },
        "prompt": scenario["prompt"],
        "criteria": {
            "common": common_criteria,
            "scenario": scenario.get("criteria", []),
        },
    }
    (run_dir / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    command = [
        "pi",
        "--provider",
        run["provider"],
        "--model",
        run["model"],
        "--thinking",
        run["thinking"],
        "--mode",
        "json",
        "--print",
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-context-files",
        "--no-tools",
        "--tools",
        "read,bash",
        "--skill",
        str(SKILL_PATH),
        "--extension",
        str(GATE_EXTENSION_PATH),
        scenario["prompt"],
    ]
    started = datetime.now(timezone.utc)
    start = time.monotonic()
    timed_out = False
    error = ""
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        raw, returncode, stderr = (
            completed.stdout,
            completed.returncode,
            completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out, raw, stderr, returncode = (
            True,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            None,
        )
    except OSError as exc:
        raw, stderr, returncode, error = "", "", None, repr(exc)
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text(raw, encoding="utf-8")
    gate_blocks = _gate_blocks(stderr)
    attempts, reply = _details(raw, gate_blocks)
    status = (
        "timed_out"
        if timed_out
        else "launch_error"
        if error
        else "completed"
        if returncode == 0
        else "failed"
    )
    return {
        "run_id": run_id,
        "model": case["model"],
        "scenario": scenario["id"],
        "started_at": started.isoformat(),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "returncode": returncode,
        "run_status": status,
        "timed_out": timed_out,
        "error": error,
        "stderr": stderr,
        "case_path": str(Path(run_id) / "case.json"),
        "trace_path": str(Path(run_id) / "trace.jsonl"),
        "tool_attempts": attempts,
        "final_assistant_reply": reply,
        "skill_read_audit": _skill_read_audit(raw, gate_blocks),
    }


def _first_gate_block_index(attempts: list[dict[str, Any]]) -> int | None:
    return next(
        (i for i, attempt in enumerate(attempts) if attempt.get("status") == "blocked"),
        None,
    )


def _skill_read_audit(
    lines: str, gate_blocks: dict[str, str] | None = None
) -> dict[str, Any]:
    """Mechanically describe the exact Skill read; infer no behavioral compliance."""
    events: list[dict[str, Any]] = []
    for line in lines.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)

    reads: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        event_type = event.get("type")
        if event_type == "tool_execution_start":
            args = event.get("args")
            if (
                _tool_event_name(event) == "read"
                and isinstance(args, dict)
                and args.get("path") == str(SKILL_PATH)
            ):
                call_id = event.get("toolCallId")
                read = {
                    "start_event": index,
                    "tool_call_id": call_id if isinstance(call_id, str) else None,
                    "end_event": None,
                    "matching_end": False,
                    "is_error_false": False,
                }
                reads.append(read)
                if isinstance(call_id, str):
                    by_id[call_id] = read
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            read = by_id.get(call_id) if isinstance(call_id, str) else None
            if read is not None:
                read["end_event"] = index
                read["matching_end"] = True
                read["is_error_false"] = isinstance(
                    event.get("isError"), bool
                ) and not event.get("isError")

    first_bash = next(
        (
            i
            for i, event in enumerate(events)
            if event.get("type") == "tool_execution_start"
            and _tool_event_name(event) == "bash"
        ),
        None,
    )
    gate_call_ids = set(gate_blocks or {})
    first_gate = next(
        (
            i
            for i, event in enumerate(events)
            if event.get("type") == "tool_execution_end"
            and (
                (
                    isinstance(event.get("toolCallId"), str)
                    and event.get("toolCallId") in gate_call_ids
                )
                or _tool_status(event) == "blocked"
            )
        ),
        None,
    )
    for read in reads:
        end = read["end_event"]
        read["before_first_bash_attempt"] = first_bash is None or (
            end is not None and end < first_bash
        )
        read["before_first_gate"] = first_gate is None or (
            end is not None and end < first_gate
        )
    selected = next(
        (read for read in reads if read["matching_end"] and read["is_error_false"]),
        reads[0] if reads else None,
    )
    return {
        "skill_path": str(SKILL_PATH),
        "attempted": bool(reads),
        "completed_successfully": bool(
            selected and selected["matching_end"] and selected["is_error_false"]
        ),
        "matching_end": selected["matching_end"] if selected else False,
        "is_error_false": selected["is_error_false"] if selected else False,
        "before_first_bash_attempt": (
            selected["before_first_bash_attempt"] if selected else None
        ),
        "before_first_gate": selected["before_first_gate"] if selected else None,
        "first_bash_attempt_event": first_bash,
        "first_gate_event": first_gate,
        "read_count": len(reads),
    }


def _fence(text: str) -> str:
    """Return a backtick fence longer than any backtick run in *text*."""
    longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _write_report(output_dir: Path, result: dict[str, Any]) -> None:
    """Write the self-contained report for one run, beside its artifacts."""
    attempts = result["tool_attempts"]
    first = _first_gate_block_index(attempts)
    primary = attempts if first is None else attempts[: first + 1]
    later = [] if first is None else attempts[first + 1 :]
    reply_fence = _fence(result["final_assistant_reply"])
    skill_read_audit = result.get(
        "skill_read_audit",
        {
            "attempted": False,
            "completed_successfully": False,
            "matching_end": False,
            "is_error_false": False,
            "before_first_bash_attempt": None,
            "before_first_gate": None,
        },
    )
    report = [
        "# Behavior sampling report",
        "",
        "Generated mechanically from this run's `trace.jsonl`; the case and trace artifacts are linked below.",
        "",
        "The sampler exit status describes sampling completeness, not behavioral compliance. Assess the first substantive intent before the gate intervenes; attempts after the first gate block are retained only for audit.",
        "",
        f"## {result['run_id']} / {result['scenario']}",
        "",
        "- Case: [`case.json`](case.json)",
        "- Trace: [`trace.jsonl`](trace.jsonl)",
        "",
        "### Exact project Skill read (mechanical)",
        "",
        "This records tool-event facts only; it does not infer Skill compliance.",
        "",
        "```json",
        json.dumps(skill_read_audit, ensure_ascii=False, indent=2),
        "```",
        "",
        "### Attempts through the first gate block",
        "",
        "```json",
        json.dumps(primary, ensure_ascii=False, indent=2),
        "```",
        "",
        "### Attempts after the first gate block",
        "",
        "Retained for audit; do not use these attempts to assess Skill induction.",
        "",
        "```json",
        json.dumps(later, ensure_ascii=False, indent=2),
        "```",
        "",
        "### Final assistant reply",
        "",
        reply_fence,
        result["final_assistant_reply"],
        reply_fence,
        "",
    ]
    run_dir = output_dir / result["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text("\n".join(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("behavior-sampling"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for run in RUNS:
        try:
            scenario = SCENARIOS[run["scenario"]]
        except KeyError as exc:
            raise ValueError(
                f"Manifest references unknown scenario: {run['scenario']}"
            ) from exc
        result = _run(args.output, run, scenario, COMMON_CRITERIA)
        results.append(result)
        _write_report(args.output, result)
    return 0 if all(result["run_status"] == "completed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
