"""Bound executor and reviewer model context without dropping the latest work.

Earlier rounds become a findings summary. The reviewer keeps the frozen diff
and the relevant source section; bulky execution and probe transcripts do not
ride along in full.
"""

from __future__ import annotations

import json
import re
from typing import Any


EXECUTOR_PRIOR_CHAR_LIMIT = 48_000
REVIEWER_PROMPT_CHAR_LIMIT = 80_000
PROBE_FEEDBACK_CHAR_LIMIT = 8_000

_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_JSON_HEADERS = (
    "\n\nEXECUTION RESULT:\n",
    "\n\nFROZEN DIFF:\n",
    "\n\nVERIFICATION RESULT:\n",
    "\n\nACCEPTANCE EVIDENCE MATRIX:\n",
)


def _chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        else:
            total += len(json.dumps(content, ensure_ascii=False, default=str))
    return total


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [summarised]"


def _failure_lines(text: str, *, limit: int = 6) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED ") or "AssertionError" in stripped or stripped.startswith("E "):
            if stripped not in found:
                found.append(stripped[:240])
        if len(found) >= limit:
            break
    return found


def _summarise_round(messages: list[dict[str, Any]]) -> str:
    calls: list[str] = []
    paths: list[str] = []
    failures: list[str] = []
    assistant = ""
    for message in messages:
        role = message.get("role")
        if role == "assistant_tool_call":
            name = str(message.get("toolName") or "tool")
            raw_input = message.get("input")
            path = raw_input.get("path") if isinstance(raw_input, dict) else None
            label = name if not isinstance(path, str) else f"{name} {path}"
            if label not in calls:
                calls.append(label)
            if isinstance(path, str) and path not in paths and name in {"edit_file", "write_file", "patch_file"}:
                paths.append(path)
        content = message.get("content")
        if isinstance(content, str):
            if role == "tool_result":
                for line in _failure_lines(content):
                    if line not in failures:
                        failures.append(line)
            elif role == "assistant" and content.strip():
                assistant = content.strip()
    lines = ["EARLIER ROUNDS SUMMARY (full transcripts are not replayed):"]
    if calls:
        lines.append("- calls: " + ", ".join(calls[:24]))
    if paths:
        lines.append("- files: " + ", ".join(paths[:24]))
    if failures:
        lines.append("- findings:")
        lines.extend(f"  {line}" for line in failures[:12])
    if assistant:
        lines.append("- assistant: " + _clip(assistant, 500).replace("\n", " "))
    if len(lines) == 1:
        lines.append("- no earlier tool findings were recorded")
    return "\n".join(lines)


def _shrink_latest_round(messages: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if _chars(messages) <= limit:
        return messages
    result_indexes = [index for index, message in enumerate(messages) if message.get("role") == "tool_result"]
    protected = set(result_indexes[-4:])
    shrunk: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if (index in result_indexes and index not in protected and isinstance(content, str)
                and len(content) > 400):
            highlights = _failure_lines(content)
            replacement = "[Earlier tool result summarised]"
            if highlights:
                replacement += "\n" + "\n".join(highlights)
            copied = dict(message)
            copied["content"] = replacement
            shrunk.append(copied)
        else:
            shrunk.append(message)
    return shrunk


def _host_continuity(message: dict[str, Any]) -> bool:
    """Keep host projections the same Executor must see on the next round."""

    return (
        message.get("_implementation_context") is True
        or message.get("_implementation_batch_feedback") is True
    )


def _shrink_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if _host_continuity(message) or not isinstance(content, str) or len(content) <= 2_000:
        return message
    copied = dict(message)
    if message.get("role") == "tool_result":
        highlights = _failure_lines(content)
        replacement = "[Earlier tool result summarised]"
        if highlights:
            replacement += "\n" + "\n".join(highlights)
        copied["content"] = replacement
        return copied
    copied["content"] = _clip(content.strip(), 500)
    return copied


def bound_executor_transcript(
    messages: list[dict[str, Any]],
    *,
    limit: int = EXECUTOR_PRIOR_CHAR_LIMIT,
) -> list[dict[str, Any]]:
    """Summarise bulky earlier text and keep host continuity plus the latest round."""

    copied = [dict(message) for message in messages]
    if _chars(copied) <= limit:
        return copied
    user_indexes = [index for index, message in enumerate(copied) if message.get("role") == "user"]
    start = user_indexes[-1] if user_indexes else len(copied)
    earlier = [_shrink_message(message) for message in copied[:start]]
    if _chars(earlier) > limit // 2:
        protected = [message for message in earlier if _host_continuity(message)]
        summary = _summarise_round(
            [message for message in copied[:start] if not _host_continuity(message)]
        )
        earlier = [{"role": "user", "content": summary}, *protected]
    latest = _shrink_latest_round(copied[start:], limit=max(0, limit - _chars(earlier)))
    bounded = [*earlier, *latest]
    if _chars(bounded) <= limit:
        return bounded
    # Last resort: one findings summary, then the continuity messages and the
    # latest user round. Small tool results inside that round stay intact.
    summary = _summarise_round(
        [message for message in copied[:start] if not _host_continuity(message)]
    )
    protected = [message for message in copied[:start] if _host_continuity(message)]
    latest = _shrink_latest_round(copied[start:], limit=max(0, limit - len(summary) - _chars(protected)))
    return [{"role": "user", "content": summary}, *protected, *latest]


def bound_probe_feedback(raw: str, *, limit: int = PROBE_FEEDBACK_CHAR_LIMIT) -> str:
    """Shrink retained probe transcripts to ids, status, and assertion lines."""

    if not isinstance(raw, str) or len(raw) <= limit:
        return raw
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return _clip(raw, limit)
    if not isinstance(document, dict):
        return _clip(raw, limit)
    probes = []
    by_id: dict[str, dict[str, Any]] = {}
    for probe in document.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        row = {
            "probeId": probe.get("probeId"),
            "criterionIds": probe.get("criterionIds"),
            "scenario": _clip(str(probe.get("scenario") or ""), 240),
        }
        probes.append(row)
        if isinstance(row["probeId"], str):
            by_id[row["probeId"]] = row
    for attempt in document.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        probe_id = attempt.get("probeId")
        row = by_id.get(probe_id) if isinstance(probe_id, str) else None
        if row is None:
            continue
        row["status"] = attempt.get("status")
        evidence = attempt.get("evidence") if isinstance(attempt.get("evidence"), dict) else {}
        texts = []
        for name in ("stdout", "stderr"):
            stream = evidence.get(name)
            if isinstance(stream, dict) and isinstance(stream.get("preview"), str):
                texts.append(stream["preview"])
        highlights = _failure_lines("\n".join(texts))
        report = attempt.get("testReport") if isinstance(attempt.get("testReport"), dict) else {}
        cases = (report.get("caseReport") or {}).get("cases") if isinstance(report.get("caseReport"), dict) else []
        failed = [case.get("nodeId") for case in cases
                  if isinstance(case, dict) and case.get("outcome") == "failed"
                  and isinstance(case.get("nodeId"), str)]
        if failed:
            row["failedNodeIds"] = failed[:20]
        if highlights:
            row["assertionLines"] = highlights[:8]
    summary = {
        "summarised": True,
        "activeProbeIds": document.get("activeProbeIds"),
        "retiredProbeIds": document.get("retiredProbeIds"),
        "probes": probes,
    }
    text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else _clip(text, limit)


def _json_section(text: str, header: str) -> tuple[int, int, object] | None:
    start = text.find(header)
    if start < 0:
        return None
    body_start = start + len(header)
    end = len(text)
    for marker in _JSON_HEADERS:
        if marker == header:
            continue
        at = text.find(marker, body_start)
        if 0 <= at < end:
            end = at
    source_at = text.find("\n=== REVIEW SOURCE CONTEXT ===", body_start)
    if 0 <= source_at < end:
        end = source_at
    raw = text[body_start:end].strip()
    try:
        return body_start, end, json.loads(raw)
    except (TypeError, ValueError):
        return None


def _replace_span(text: str, body_start: int, end: int, replacement: str) -> str:
    suffix = text[end:]
    if suffix.startswith("\n"):
        return text[:body_start] + replacement + suffix
    return text[:body_start] + replacement + "\n" + suffix


def _summarise_execution(value: object) -> str:
    if not isinstance(value, dict):
        return json.dumps({"summarised": True}, sort_keys=True)
    steps = value.get("steps")
    step_rows = []
    if isinstance(steps, list):
        for step in steps[:20]:
            if isinstance(step, dict):
                step_rows.append({"step": step.get("step"), "status": step.get("status")})
    summary = {
        "summarised": True,
        "status": value.get("status"),
        "patchDigest": value.get("patchDigest"),
        "steps": step_rows,
        "blockers": value.get("blockers") if isinstance(value.get("blockers"), list) else [],
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _summarise_verification(value: object) -> str:
    if not isinstance(value, dict):
        return json.dumps({"summarised": True}, sort_keys=True)
    checks = []
    for check in value.get("checks") or []:
        if not isinstance(check, dict):
            continue
        checks.append({"checkId": check.get("checkId"), "status": check.get("status")})
    summary = {
        "summarised": True,
        "status": value.get("status"),
        "patchDigest": value.get("patchDigest"),
        "checks": checks[:20],
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _summarise_matrix(value: object) -> str:
    if not isinstance(value, dict):
        return json.dumps({"summarised": True}, sort_keys=True)
    rows = []
    for row in value.get("criteria") or []:
        if isinstance(row, dict):
            rows.append({"criterionId": row.get("criterionId"), "checkIds": row.get("checkIds")})
    return json.dumps({"summarised": True, "criteria": rows[:40]}, ensure_ascii=False, sort_keys=True)


def _truncate_diff_document(value: dict, *, budget: int) -> str:
    diff = value.get("diff")
    if not isinstance(diff, str):
        return json.dumps({"summarised": True, "complete": False}, sort_keys=True)
    other = {key: item for key, item in value.items() if key != "diff"}
    other["complete"] = False
    marker = "\n...<truncated>...\n"
    room = max(0, budget - len(json.dumps({**other, "diff": ""}, ensure_ascii=False)))
    if room <= len(marker) + 2:
        shown = ""
    else:
        half = max(0, (room - len(marker)) // 2)
        shown = diff if len(diff) <= room else diff[:half] + marker + diff[-half:]
    return json.dumps({**other, "diff": shown}, ensure_ascii=False, sort_keys=True)


def _summarise_feedback_tail(tail: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(tail):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(tail[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("probes" in value or "attempts" in value):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return tail[:index] + bound_probe_feedback(encoded) + tail[index + end:]
    return tail if len(tail) <= PROBE_FEEDBACK_CHAR_LIMIT else _clip(tail, PROBE_FEEDBACK_CHAR_LIMIT)


def bound_reviewer_prompt(prompt: str, *, limit: int = REVIEWER_PROMPT_CHAR_LIMIT) -> str:
    """Keep the diff and relevant files; summarise the rest once the prompt is large."""

    if not isinstance(prompt, str) or len(prompt) <= limit:
        return prompt
    text = prompt
    replacements = (
        ("\n\nEXECUTION RESULT:\n", _summarise_execution),
        ("\n\nACCEPTANCE EVIDENCE MATRIX:\n", _summarise_matrix),
        ("\n\nVERIFICATION RESULT:\n", _summarise_verification),
    )
    for header, summarise in replacements:
        if len(text) <= limit:
            break
        found = _json_section(text, header)
        if found is None:
            continue
        body_start, end, value = found
        text = _replace_span(text, body_start, end, summarise(value))
    feedback_at = text.find("\n\nINDEPENDENT REQUIREMENT CHECKS:\n")
    if feedback_at >= 0 and len(text) > limit:
        tail = text[feedback_at + len("\n\nINDEPENDENT REQUIREMENT CHECKS:\n"):]
        text = text[:feedback_at] + "\n\nINDEPENDENT REQUIREMENT CHECKS:\n" + _summarise_feedback_tail(tail)
    source_at = text.find("\n=== REVIEW SOURCE CONTEXT ===")
    if source_at >= 0 and len(text) > limit:
        overflow = len(text) - limit
        source = text[source_at:]
        keep = max(0, len(source) - overflow - 80)
        text = text[:source_at] + source[:keep] + "\n... [review source context summarised]\n"
    if len(text) > limit:
        found = _json_section(text, "\n\nFROZEN DIFF:\n")
        if found is not None and isinstance(found[2], dict):
            body_start, end, value = found
            budget = max(1000, limit - (len(text) - (end - body_start)))
            text = _replace_span(text, body_start, end, _truncate_diff_document(value, budget=budget))
    return text


__all__ = [
    "EXECUTOR_PRIOR_CHAR_LIMIT",
    "PROBE_FEEDBACK_CHAR_LIMIT",
    "REVIEWER_PROMPT_CHAR_LIMIT",
    "bound_executor_transcript",
    "bound_probe_feedback",
    "bound_reviewer_prompt",
]
