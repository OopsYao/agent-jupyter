"""Sample agent-jupyter Skill behavior with four explicit pi runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROMPT = "请在我当前的 notebook/Jupyter kernel 中执行 `print(40 + 2)`。"
SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agent_jupyter/resources/agent_jupyter_skill.md"
)
TIMEOUT_SECONDS = 120
RUNS = (
    ("moonshotai-kimi-k2.5-off", "moonshotai/kimi-k2.5", "off"),
    ("moonshotai-kimi-k2.5-medium", "moonshotai/kimi-k2.5", "medium"),
    ("qwen-qwen3.6-plus-off", "qwen/qwen3.6-plus", "off"),
    ("qwen-qwen3.6-plus-medium", "qwen/qwen3.6-plus", "medium"),
)
GATE_EXTENSION_PATH = Path(__file__).resolve().with_name("sample_gate.ts")
BLOCKED_REASON_PREFIX = "Behavior sampler gate blocked:"


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


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
    if not isinstance(result, dict):
        return ""
    return _text_content(result.get("content"))


def _tool_status(event: dict[str, Any]) -> str:
    is_error = event.get("isError")
    if isinstance(is_error, bool):
        if not is_error:
            return "completed"
        if _tool_result_text(event.get("result")).startswith(BLOCKED_REASON_PREFIX):
            return "blocked"
        return "error"
    return "unknown"


def _details(lines: str) -> tuple[list[dict[str, Any]], str]:
    """Parse tool lifecycle events and the last assistant reply from JSONL.

    Tool starts define attempt order. End events only update the matching attempt;
    no attempt is removed or collapsed when the same tool is called repeatedly.
    """
    tool_attempts: list[dict[str, Any]] = []
    attempts_by_id: dict[str, dict[str, Any]] = {}
    assistant_replies: list[str] = []
    for line in lines.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "tool_execution_start":
            attempt: dict[str, Any] = {
                "tool_name": _tool_event_name(event),
                "arguments": event.get("args"),
                "status": "unknown",
            }
            tool_call_id = event.get("toolCallId")
            if isinstance(tool_call_id, str):
                attempt["tool_call_id"] = tool_call_id
                attempts_by_id[tool_call_id] = attempt
            tool_attempts.append(attempt)
        elif event_type == "tool_execution_end":
            tool_call_id = event.get("toolCallId")
            matched_attempt: dict[str, Any] | None = (
                attempts_by_id.get(tool_call_id)
                if isinstance(tool_call_id, str)
                else None
            )
            if matched_attempt is None:
                # Be conservative if a producer emits an end without its start.
                unmatched_attempt = {
                    "tool_name": _tool_event_name(event),
                    "arguments": event.get("args"),
                    "status": "unknown",
                }
                if isinstance(tool_call_id, str):
                    unmatched_attempt["tool_call_id"] = tool_call_id
                    attempts_by_id[tool_call_id] = unmatched_attempt
                tool_attempts.append(unmatched_attempt)
                matched_attempt = unmatched_attempt
            matched_attempt["status"] = _tool_status(event)

        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _text_content(message.get("content"))
            if text:
                assistant_replies.append(text)
        if event.get("role") == "assistant":
            text = _text_content(event.get("content"))
            if text:
                assistant_replies.append(text)
    return tool_attempts, assistant_replies[-1] if assistant_replies else ""


def _fence(text: str) -> tuple[str, str]:
    longest = max(
        (
            len(part)
            for part in text.split("\n")
            if part.startswith("`") and part.endswith("`")
        ),
        default=0,
    )
    return "`" * max(3, longest + 1), text


def _run(output_dir: Path, label: str, model: str, thinking: str) -> dict[str, Any]:
    command = [
        "pi",
        "--provider",
        "openrouter",
        "--model",
        model,
        "--thinking",
        thinking,
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
        PROMPT,
    ]
    started = datetime.now(timezone.utc)
    start = time.monotonic()
    timed_out = False
    error = ""
    try:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        raw = completed.stdout
        returncode = completed.returncode
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        raw = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        returncode = None
    except OSError as exc:
        raw = ""
        stderr = ""
        returncode = None
        error = repr(exc)
    raw_path = output_dir / f"{label}.jsonl"
    raw_path.write_text(raw, encoding="utf-8")
    tool_attempts, reply = _details(raw)
    return {
        "label": label,
        "model": model,
        "thinking": thinking,
        "provider": "openrouter",
        "prompt": PROMPT,
        "timeout_seconds": TIMEOUT_SECONDS,
        "started_at": started.isoformat(),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "stderr": stderr,
        "raw_stream": raw_path.name,
        "tool_attempts": tool_attempts,
        "final_assistant_reply": reply,
    }


def _write_report(output_dir: Path, results: list[dict[str, Any]]) -> None:
    report = [
        "# Behavior sampling report",
        "",
        "Generated mechanically from the saved JSONL streams.",
        "",
    ]
    for result in results:
        report.extend(
            [
                f"## {result['label']}",
                "",
                "### Run metadata",
                "",
                "```json",
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in {"tool_attempts", "final_assistant_reply"}
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
                "### Tool-call attempts",
                "",
                "```json",
                json.dumps(result["tool_attempts"], ensure_ascii=False, indent=2),
                "```",
                "",
                "### Final assistant reply",
                "",
            ]
        )
        fence, reply = _fence(result["final_assistant_reply"])
        report.extend([fence, reply, fence, ""])
    (output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("behavior-sampling"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = [_run(args.output, *run) for run in RUNS]
    _write_report(args.output, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
