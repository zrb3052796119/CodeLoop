"""Bounded, content-free sidecar journal for one parent Run's sub-agents.

The parent Run stream keeps a single ``subagent.completed`` summary so readers
that require exactly one ``task.outcome`` per Run stay valid. The detailed
nested-loop telemetry lives here, under the parent Run directory, and is
deleted together with that Run by normal retention.

Records are append-only JSONL. Payload strings are bounded and only a fixed
event whitelist is accepted; task text, findings, paths, prompts and tool
arguments must never enter this journal.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.agent_budget import AgentBudgetSnapshot
from minicode.concurrent_tool_terminal import (
    CONCURRENT_TOOL_TERMINAL_EVENT_TYPE,
    MAX_CONCURRENT_TOOL_TERMINAL_EVENTS,
    normalize_concurrent_tool_terminal,
)

JOURNAL_VERSION = 1
_SUBAGENT_ID_RE = re.compile(r"^sub_[0-9a-f]{32}$")
_MAX_SUBAGENTS_PER_RUN = 100
_MAX_STANDARD_FILE_BYTES = 1024 * 1024
_MAX_TERMINAL_RECORD_BYTES = 512
_MAX_FINISH_RECORD_BYTES = 4 * 1024
MAX_STANDARD_EVENTS_PER_SUBAGENT = 500
MAX_TERMINAL_EVENTS_PER_SUBAGENT = MAX_CONCURRENT_TOOL_TERMINAL_EVENTS
_MAX_TERMINAL_FILE_BYTES = (
    MAX_TERMINAL_EVENTS_PER_SUBAGENT * _MAX_TERMINAL_RECORD_BYTES
)
_MAX_FILE_BYTES = _MAX_STANDARD_FILE_BYTES + _MAX_TERMINAL_FILE_BYTES
_MAX_EVENTS_PER_SUBAGENT = (
    MAX_STANDARD_EVENTS_PER_SUBAGENT
    + MAX_TERMINAL_EVENTS_PER_SUBAGENT
)
_MAX_STRING_CHARS = 240
_MAX_NESTING = 4
_MAX_LIST_ITEMS = 40
_MAX_DICT_ITEMS = 40

_ALLOWED_EVENT_TYPES = frozenset(
    {
        "model.started",
        "model.completed",
        "model.failed",
        "model.costed",
        "tool.started",
        "tool.finished",
        CONCURRENT_TOOL_TERMINAL_EVENT_TYPE,
        "task.outcome",
        "skill.routed",
        "skill.loaded",
        "skill.attributed",
        "memory.retrieved",
        "memory.rendered",
        "context.compacted",
        "context.compaction.failed",
        "recovery.started",
        "recovery.completed",
        "verification.completed",
    }
)

# Defense in depth: even a bug in a projection must not be able to smuggle
# task content into the journal. Any key containing these substrings is
# dropped before serialization.
_SENSITIVE_KEY_NAMES = {
    "content",
    "summary",
    "text",
    "prompt",
    "input",
    "output",
    "command",
    "path",
    "title",
    "file",
    "files",
}


class SubagentJournalError(RuntimeError):
    """Base error for sub-agent journal storage failures."""


def _bounded_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)[:_MAX_STRING_CHARS]


def _sanitize_value(value: object, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING_CHARS]
    if isinstance(value, Mapping):
        if depth >= _MAX_NESTING:
            return {}
        safe: dict[str, object] = {}
        for key, nested in list(value.items())[:_MAX_DICT_ITEMS]:
            key_text = _bounded_string(key)
            lowered = key_text.lower()
            if lowered in _SENSITIVE_KEY_NAMES:
                continue
            safe[key_text] = _sanitize_value(nested, depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        if depth >= _MAX_NESTING:
            return []
        return [
            _sanitize_value(item, depth + 1)
            for item in list(value)[:_MAX_LIST_ITEMS]
        ]
    return _bounded_string(value)


def sanitize_subagent_event_payload(
    event_type: str,
    payload: Mapping[str, object] | None,
) -> dict[str, object]:
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise ValueError("event type is not allowed in a sub-agent journal")
    sanitized = _sanitize_value(payload or {})
    if not isinstance(sanitized, dict):
        return {}
    if event_type == CONCURRENT_TOOL_TERMINAL_EVENT_TYPE:
        normalized_terminal = normalize_concurrent_tool_terminal(sanitized)
        if normalized_terminal is None:
            raise ValueError("concurrent tool terminal payload is invalid")
        return normalized_terminal
    return sanitized


@dataclass(frozen=True, slots=True)
class SubagentJournalEvent:
    sequence: int
    timestamp: str
    type: str
    step: int | None
    payload: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "type": self.type,
            "step": self.step,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SubagentRunSummary:
    subagent_id: str
    parent_run_id: str
    agent_type: str
    outcome: str
    started_at: float
    completed_at: float
    model_turns: int
    tool_calls: int
    duration_ms: int
    max_turns: int
    limit_kind: str
    result_truncated: bool
    budget_snapshot: dict[str, object]
    event_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    # True when the run soft-landed: it exhausted its model-turn limit and
    # its result is a partial summary produced by one tool-free closing turn.
    # Absent on journals written before the marker existed (reads as False).
    partial: bool = False

    def to_dict(self) -> dict[str, object]:
        summary = {
            "subagentId": self.subagent_id,
            "parentRunId": self.parent_run_id,
            "agentType": self.agent_type,
            "outcome": self.outcome,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "modelTurns": self.model_turns,
            "toolCalls": self.tool_calls,
            "durationMs": self.duration_ms,
            "maxTurns": self.max_turns,
            "limitKind": self.limit_kind,
            "resultTruncated": self.result_truncated,
            "budget": dict(self.budget_snapshot),
            "eventCount": self.event_count,
        }
        # Optional attributed-token telemetry: absent on journals written
        # before per-sub-agent attribution existed.
        if self.input_tokens is not None and self.output_tokens is not None:
            summary["inputTokens"] = self.input_tokens
            summary["outputTokens"] = self.output_tokens
        if self.partial:
            summary["partial"] = True
        return summary


class SubagentRunJournal:
    """Append/read one bounded journal directory for sub-agents of a Run."""

    def __init__(self, run_dir: Path, *, clock=time.time) -> None:
        self._run_dir = Path(run_dir)
        self._root = self._run_dir / "subagent-runs"
        self._clock = clock
        self._write_lock = threading.Lock()
        self._byte_usage: dict[str, tuple[int, int]] = {}

    @property
    def parent_run_id(self) -> str:
        return self._run_dir.name

    def _ensure_root(self) -> None:
        if self._root.is_symlink():
            raise SubagentJournalError("sub-agent journal root is a symlink")
        self._root.mkdir(mode=0o700, exist_ok=True)
        if self._root.is_symlink() or not self._root.is_dir():
            raise SubagentJournalError("sub-agent journal root is unsafe")

    def _path(self, subagent_id: str) -> Path:
        if not isinstance(subagent_id, str) or not _SUBAGENT_ID_RE.fullmatch(
            subagent_id
        ):
            raise SubagentJournalError("sub-agent id is invalid")
        return self._root / f"{subagent_id}.jsonl"

    def _load_byte_usage(self, path: Path) -> tuple[int, int]:
        try:
            path_info = os.lstat(path)
        except FileNotFoundError:
            return 0, 0
        except OSError as exc:
            raise SubagentJournalError(
                "sub-agent journal usage is unavailable"
            ) from exc
        if (
            not stat.S_ISREG(path_info.st_mode)
            or path_info.st_size > _MAX_FILE_BYTES
        ):
            raise SubagentJournalError(
                "sub-agent journal file is unsafe"
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        standard_bytes = 0
        terminal_bytes = 0
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise SubagentJournalError(
                "sub-agent journal usage is unavailable"
            ) from exc
        try:
            descriptor_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(descriptor_info.st_mode)
                or descriptor_info.st_dev != path_info.st_dev
                or descriptor_info.st_ino != path_info.st_ino
                or descriptor_info.st_size > _MAX_FILE_BYTES
            ):
                raise SubagentJournalError(
                    "sub-agent journal file is unsafe"
                )
            chunks: list[bytes] = []
            remaining = _MAX_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw_journal = b"".join(chunks)
            if (
                len(raw_journal) > _MAX_FILE_BYTES
                or len(raw_journal) != descriptor_info.st_size
            ):
                raise SubagentJournalError(
                    "sub-agent journal changed while reading usage"
                )
        except OSError as exc:
            raise SubagentJournalError(
                "sub-agent journal usage is unavailable"
            ) from exc
        finally:
            os.close(descriptor)
        lines = raw_journal.splitlines(keepends=True)
        for raw in lines:
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise SubagentJournalError(
                    "sub-agent journal contains an invalid record"
                ) from exc
            terminal = (
                isinstance(record, dict)
                and record.get("recordType") == "event"
                and record.get("type")
                == CONCURRENT_TOOL_TERMINAL_EVENT_TYPE
            )
            if terminal:
                terminal_bytes += len(raw)
            else:
                standard_bytes += len(raw)
        return standard_bytes, terminal_bytes

    def _append_line(self, subagent_id: str, record: Mapping[str, object]) -> None:
        self._ensure_root()
        path = self._path(subagent_id)
        if path.is_symlink():
            raise SubagentJournalError("sub-agent journal file is a symlink")
        encoded = (
            json.dumps(
                dict(record),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        terminal = (
            record.get("recordType") == "event"
            and record.get("type") == CONCURRENT_TOOL_TERMINAL_EVENT_TYPE
        )
        standard_event = (
            record.get("recordType") == "event" and not terminal
        )
        if terminal and len(encoded) > _MAX_TERMINAL_RECORD_BYTES:
            raise SubagentJournalError("terminal event exceeds its byte bound")
        with self._write_lock:
            usage = self._byte_usage.get(subagent_id)
            if usage is None:
                usage = self._load_byte_usage(path)
            standard_bytes, terminal_bytes = usage
            next_standard_bytes = standard_bytes + (
                0 if terminal else len(encoded)
            )
            next_terminal_bytes = terminal_bytes + (
                len(encoded) if terminal else 0
            )
            standard_limit = (
                _MAX_STANDARD_FILE_BYTES - _MAX_FINISH_RECORD_BYTES
                if standard_event
                else _MAX_STANDARD_FILE_BYTES
            )
            if (
                next_standard_bytes > standard_limit
                or next_terminal_bytes > _MAX_TERMINAL_FILE_BYTES
                or next_standard_bytes + next_terminal_bytes > _MAX_FILE_BYTES
            ):
                raise SubagentJournalError("sub-agent journal is full")
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise SubagentJournalError(
                        "sub-agent journal file is unsafe"
                    )
                if info.st_size != standard_bytes + terminal_bytes:
                    raise SubagentJournalError(
                        "sub-agent journal changed outside its writer"
                    )
                if os.write(descriptor, encoded) != len(encoded):
                    raise SubagentJournalError(
                        "short sub-agent journal write"
                    )
            finally:
                os.close(descriptor)
            self._byte_usage[subagent_id] = (
                next_standard_bytes,
                next_terminal_bytes,
            )

    def start(
        self,
        *,
        parent_run_id: str | None = None,
        subagent_id: str,
        agent_type: str,
        max_turns: int,
        budget: AgentBudgetSnapshot | None,
    ) -> None:
        parent_run_id = parent_run_id or self.parent_run_id
        if not isinstance(parent_run_id, str) or not re.fullmatch(
            r"run_[0-9a-f]{32}", parent_run_id
        ):
            raise SubagentJournalError("parent run id is invalid")
        if agent_type not in {"explore", "plan", "general", "workflow"}:
            raise SubagentJournalError("agent type is invalid")
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
            raise SubagentJournalError("max turns is invalid")
        path = self._path(subagent_id)
        if path.exists():
            raise SubagentJournalError("sub-agent journal already exists")
        self._append_line(
            subagent_id,
            {
                "recordType": "start",
                "journalVersion": JOURNAL_VERSION,
                "subagentId": subagent_id,
                "parentRunId": parent_run_id,
                "agentType": agent_type,
                "maxTurns": max_turns,
                "limitKind": (
                    "phases" if agent_type == "workflow" else "model_turns"
                ),
                "startedAt": self._clock(),
                "budgetAtStart": budget.to_dict() if budget else {},
            },
        )

    def append_event(
        self,
        subagent_id: str,
        *,
        sequence: int,
        event_type: str,
        step: int | None,
        payload: Mapping[str, object] | None,
    ) -> None:
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not 1 <= sequence <= _MAX_EVENTS_PER_SUBAGENT
        ):
            raise SubagentJournalError("event sequence is invalid")
        if step is not None and (
            isinstance(step, bool)
            or not isinstance(step, int)
            or step < 0
            or step > 1_000_000
        ):
            raise SubagentJournalError("event step is invalid")
        safe_payload = sanitize_subagent_event_payload(event_type, payload)
        self._append_line(
            subagent_id,
            {
                "recordType": "event",
                "journalVersion": JOURNAL_VERSION,
                "sequence": sequence,
                "timestamp": self._clock(),
                "type": event_type,
                "step": step,
                "payload": safe_payload,
            },
        )

    def finish(
        self,
        subagent_id: str,
        *,
        outcome: str,
        model_turns: int,
        tool_calls: int,
        duration_ms: int,
        max_turns: int,
        result_truncated: bool,
        budget: AgentBudgetSnapshot | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        partial: bool = False,
    ) -> None:
        if outcome not in {
            "completed",
            "failed",
            "budget_exceeded",
            "timed_out",
            "cancelled",
        }:
            raise SubagentJournalError("outcome is invalid")
        if not isinstance(partial, bool):
            raise SubagentJournalError("partial is invalid")
        # A soft-landed run is a completion with partial results; any other
        # outcome claiming the marker is a projection bug, not a partial run.
        if partial and outcome != "completed":
            raise SubagentJournalError("partial requires a completed outcome")
        for name, value in (
            ("model_turns", model_turns),
            ("tool_calls", tool_calls),
            ("duration_ms", duration_ms),
            ("max_turns", max_turns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SubagentJournalError(f"{name} is invalid")
        # Attributed token deltas are optional (older writers never recorded
        # them) but must arrive as a pair of non-negative ints when present.
        if (input_tokens is None) != (output_tokens is None):
            raise SubagentJournalError("attributed tokens must come as a pair")
        for name, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise SubagentJournalError(f"{name} is invalid")
        record: dict[str, object] = {
            "recordType": "finish",
            "journalVersion": JOURNAL_VERSION,
            "outcome": outcome,
            "modelTurns": model_turns,
            "toolCalls": tool_calls,
            "durationMs": duration_ms,
            "maxTurns": max_turns,
            "resultTruncated": bool(result_truncated),
            "completedAt": self._clock(),
            "budgetAtFinish": budget.to_dict() if budget else {},
        }
        if input_tokens is not None and output_tokens is not None:
            record["inputTokens"] = input_tokens
            record["outputTokens"] = output_tokens
        if partial:
            record["partial"] = True
        self._append_line(subagent_id, record)

    def list_runs(self) -> tuple[SubagentRunSummary, ...]:
        if not self._root.exists():
            return ()
        if self._root.is_symlink() or not self._root.is_dir():
            return ()
        summaries: list[SubagentRunSummary] = []
        try:
            entries = sorted(self._root.iterdir(), key=lambda p: p.name)
        except OSError:
            return ()
        for entry in entries[:_MAX_SUBAGENTS_PER_RUN]:
            if entry.is_symlink() or not entry.is_file():
                continue
            if not _SUBAGENT_ID_RE.fullmatch(entry.stem):
                continue
            try:
                summary = self._read_summary(entry)
            except Exception:
                continue
            if summary is not None:
                summaries.append(summary)
        return tuple(summaries)

    def list_events(
        self, subagent_id: str
    ) -> tuple[SubagentJournalEvent, ...]:
        """Read one sub-agent's bounded event stream, content-free."""
        self._path(subagent_id)
        path = self._root / f"{subagent_id}.jsonl"
        try:
            info = os.lstat(path)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > _MAX_FILE_BYTES
            ):
                return ()
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeError):
            return ()
        events: list[SubagentJournalEvent] = []
        if len(lines) > _MAX_EVENTS_PER_SUBAGENT + 2:
            return ()
        standard_event_count = 0
        terminal_event_count = 0
        for raw in lines:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                return ()
            if not isinstance(record, dict) or record.get("recordType") != "event":
                continue
            sequence = record.get("sequence")
            event_type = record.get("type")
            step = record.get("step")
            payload = record.get("payload")
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or not 1 <= sequence <= _MAX_EVENTS_PER_SUBAGENT
                or event_type not in _ALLOWED_EVENT_TYPES
                or (
                    step is not None
                    and (
                        isinstance(step, bool)
                        or not isinstance(step, int)
                        or step < 0
                        or step > 1_000_000
                    )
                )
                or not isinstance(payload, dict)
            ):
                return ()
            if event_type == CONCURRENT_TOOL_TERMINAL_EVENT_TYPE:
                terminal_event_count += 1
                if (
                    terminal_event_count
                    > MAX_TERMINAL_EVENTS_PER_SUBAGENT
                ):
                    return ()
                normalized_terminal = normalize_concurrent_tool_terminal(
                    payload
                )
                if normalized_terminal is None:
                    return ()
                payload = normalized_terminal
            else:
                standard_event_count += 1
                if standard_event_count > MAX_STANDARD_EVENTS_PER_SUBAGENT:
                    return ()
            events.append(
                SubagentJournalEvent(
                    sequence=sequence,
                    timestamp=str(record.get("timestamp", "") or ""),
                    type=event_type,
                    step=step,
                    payload=payload,
                )
            )
        return tuple(events)

    def _read_summary(self, path: Path) -> SubagentRunSummary | None:
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeError):
            return None
        if len(lines) > _MAX_EVENTS_PER_SUBAGENT + 2:
            return None
        start: dict[str, Any] | None = None
        finish: dict[str, Any] | None = None
        event_count = 0
        for raw in lines:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if not isinstance(record, dict):
                return None
            record_type = record.get("recordType")
            if record_type == "start":
                start = record
            elif record_type == "finish":
                finish = record
            elif record_type == "event":
                event_count += 1
            else:
                return None
        if start is None or finish is None:
            return None
        subagent_id = start.get("subagentId")
        parent_run_id = start.get("parentRunId")
        agent_type = start.get("agentType")
        max_turns = start.get("maxTurns")
        limit_kind = start.get("limitKind", "model_turns")
        if (
            not isinstance(subagent_id, str)
            or not _SUBAGENT_ID_RE.fullmatch(subagent_id)
            or not isinstance(parent_run_id, str)
            or agent_type not in {"explore", "plan", "general", "workflow"}
            or isinstance(max_turns, bool)
            or not isinstance(max_turns, int)
            or max_turns <= 0
            or limit_kind
            != ("phases" if agent_type == "workflow" else "model_turns")
        ):
            return None
        outcome = finish.get("outcome")
        if outcome not in {
            "completed",
            "failed",
            "budget_exceeded",
            "timed_out",
            "cancelled",
        }:
            return None
        # Optional soft-landing marker, absent on pre-partial journals; an
        # invalid value fails the read rather than silently downgrading.
        partial = finish.get("partial", False)
        if not isinstance(partial, bool) or (partial and outcome != "completed"):
            return None
        counts = {
            key: finish.get(key)
            for key in ("modelTurns", "toolCalls", "durationMs")
        }
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        ):
            return None
        budget = finish.get("budgetAtFinish")
        if not isinstance(budget, dict):
            budget = {}
        # Optional attributed-token telemetry, absent on pre-attribution
        # journals; an invalid or unpaired value degrades to "not recorded"
        # rather than failing the whole summary read.
        token_counts = {
            key: finish.get(key)
            for key in ("inputTokens", "outputTokens")
        }
        if any(value is None for value in token_counts.values()) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in token_counts.values()
        ):
            token_counts = {"inputTokens": None, "outputTokens": None}
        return SubagentRunSummary(
            subagent_id=subagent_id,
            parent_run_id=parent_run_id,
            agent_type=agent_type,
            outcome=outcome,
            started_at=float(start.get("startedAt", 0.0) or 0.0),
            completed_at=float(finish.get("completedAt", 0.0) or 0.0),
            model_turns=int(counts["modelTurns"]),
            tool_calls=int(counts["toolCalls"]),
            duration_ms=int(counts["durationMs"]),
            max_turns=max_turns,
            limit_kind=limit_kind,
            result_truncated=bool(finish.get("resultTruncated", False)),
            budget_snapshot=budget,
            event_count=event_count,
            input_tokens=token_counts["inputTokens"],
            output_tokens=token_counts["outputTokens"],
            partial=partial,
        )


def new_subagent_id() -> str:
    return f"sub_{uuid.uuid4().hex}"


def open_subagent_journal(context: object) -> SubagentRunJournal | None:
    """Open the parent Run's sidecar without exposing its journal writer."""
    sink = getattr(context, "_event_sink", None)
    opener = getattr(sink, "open_subagent_journal", None)
    if not callable(opener):
        return None
    try:
        journal = opener()
    except Exception:  # noqa: BLE001 - journaling is optional observation
        return None
    return journal if isinstance(journal, SubagentRunJournal) else None


__all__ = [
    "JOURNAL_VERSION",
    "MAX_STANDARD_EVENTS_PER_SUBAGENT",
    "MAX_TERMINAL_EVENTS_PER_SUBAGENT",
    "SubagentJournalError",
    "SubagentJournalEvent",
    "SubagentRunJournal",
    "SubagentRunSummary",
    "new_subagent_id",
    "open_subagent_journal",
    "sanitize_subagent_event_payload",
]
