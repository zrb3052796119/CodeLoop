"""Best-effort top-level execution lifecycle observation.

Callers provide task identity and keep their existing execution code inside one
context manager.  This module owns RunJournal construction, lifecycle ordering,
safe title preparation, fixed terminal reasons, and complete Journal-failure
isolation.  It deliberately has no dependency on the Dashboard Web package.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from minicode.logging_config import get_logger
from minicode.run_journal import RunJournal
from minicode.turn_cancellation import TurnCancellationRequested


_MAX_TASK_TITLE_CHARS = 160
_MAX_TOOL_NAME_CHARS = 128
_MAX_ASSISTANT_CONTENT_LENGTH = 1_000_000
_FALLBACK_TITLE = "MiniCode task"
_FALLBACK_TOOL_NAME = "unknown"
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_logger = get_logger("run_lifecycle")


class _JournalRecord(Protocol):
    id: str


class _Journal(Protocol):
    def create_run(
        self,
        *,
        title: str,
        source: str,
        session_id: str | None,
    ) -> _JournalRecord: ...

    def transition(
        self,
        run_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> object: ...

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> object: ...

    def record_rendered_memory_ids(
        self,
        run_id: str,
        entry_ids: list[str],
    ) -> None: ...


JournalFactory = Callable[[Path], _Journal]


def _default_journal_factory(workspace: Path) -> RunJournal:
    return RunJournal(workspace)


def _task_title(value: str) -> str:
    normalized = " ".join(value.split()) if isinstance(value, str) else ""
    if not normalized:
        return _FALLBACK_TITLE
    if len(normalized) > _MAX_TASK_TITLE_CHARS:
        return normalized[:_MAX_TASK_TITLE_CHARS].rstrip() + "…"
    return normalized


def _safe_observation_warning(phase: str) -> None:
    try:
        _logger.warning("Run lifecycle observation unavailable during %s.", phase)
    except Exception:  # noqa: BLE001 - logging must never alter execution
        pass


def _safe_tool_name(value: str) -> str:
    if (
        isinstance(value, str)
        and len(value) <= _MAX_TOOL_NAME_CHARS
        and _TOOL_NAME_RE.fullmatch(value)
    ):
        return value
    return _FALLBACK_TOOL_NAME


class _BestEffortLifecycle:
    def __init__(
        self,
        *,
        workspace: str | Path,
        source: str,
        title: str,
        session_id: str | None,
        journal_factory: JournalFactory,
        enabled: bool,
    ) -> None:
        self._workspace = workspace
        self._source = source
        self._title = title
        self._session_id = session_id
        self._journal_factory = journal_factory
        self._enabled = enabled
        self._journal: _Journal | None = None
        self._run_id: str | None = None
        self._running = False

    def start(self) -> None:
        if not self._enabled:
            return
        try:
            workspace = Path(self._workspace).expanduser().resolve()
            journal = self._journal_factory(workspace)
            record = journal.create_run(
                title=_task_title(self._title),
                source=self._source,
                session_id=self._session_id,
            )
            self._journal = journal
            self._run_id = record.id
        except Exception:  # noqa: BLE001 - observability is optional
            self._journal = None
            self._run_id = None
            _safe_observation_warning("create")
            return
        try:
            journal.transition(record.id, "running")
            self._running = True
        except Exception:  # noqa: BLE001 - leave a truthful queued record
            self._running = False
            _safe_observation_warning("start")

    def finish(self, status: str, *, reason: str | None = None) -> None:
        if not self._running or self._journal is None or self._run_id is None:
            return
        try:
            self._journal.transition(self._run_id, status, reason=reason)
        except Exception:  # noqa: BLE001 - never replace business outcome
            _safe_observation_warning("terminal")
        finally:
            self._running = False

    def append_event(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        """Append one trace event while keeping observation strictly optional."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        try:
            self._journal.append_event(
                self._run_id,
                event_type,
                step=step,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 - trace failures never alter execution
            _safe_observation_warning("event")
            return False
        return True

    def record_rendered_memory_ids(self, entry_ids: list[str]) -> bool:
        """Persist this turn's rendered Memory IDs while keeping observation
        strictly optional, mirroring append_event's failure isolation."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        try:
            self._journal.record_rendered_memory_ids(self._run_id, entry_ids)
        except Exception:  # noqa: BLE001 - trace failures never alter execution
            _safe_observation_warning("memory_rendered_ids")
            return False
        return True


class RunObservation:
    """Small, no-throw handle for callback-derived execution trace metadata."""

    def __init__(self, lifecycle: _BestEffortLifecycle) -> None:
        self._lifecycle = lifecycle
        self._pending_tools: dict[str, deque[str]] = defaultdict(deque)
        self._assistant_attempted = False

    @property
    def run_id(self) -> str | None:
        """Return this observation's opaque Run ID, if Journal start succeeded."""
        return self._lifecycle._run_id if self._lifecycle._running else None

    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Adapt the structured Agent Event Sink to this Run's writer."""
        self._lifecycle.append_event(event_type, step=step, payload=payload)

    def record_rendered_memory_ids(self, entry_ids: list[str]) -> None:
        """Persist this turn's rendered Memory entry IDs against this Run."""
        self._lifecycle.record_rendered_memory_ids(entry_ids)

    def tool_started(self, tool_name: str) -> None:
        safe_name = _safe_tool_name(tool_name)
        operation_id = f"toolop_{uuid.uuid4().hex}"
        if self._lifecycle.append_event(
            "tool.started",
            payload={"toolName": safe_name, "operationId": operation_id},
        ):
            self._pending_tools[safe_name].append(operation_id)

    def tool_finished(self, tool_name: str, *, is_error: bool) -> None:
        safe_name = _safe_tool_name(tool_name)
        pending = self._pending_tools.get(safe_name)
        operation_id = pending.popleft() if pending else None
        if pending is not None and not pending:
            self._pending_tools.pop(safe_name, None)
        payload: dict[str, Any] = {
            "toolName": safe_name,
            "outcome": "error" if is_error is True else "success",
            "paired": operation_id is not None,
        }
        if operation_id is not None:
            payload["operationId"] = operation_id
        self._lifecycle.append_event("tool.finished", payload=payload)

    def assistant_completed(
        self,
        *,
        content_present: bool,
        content_length: int,
    ) -> None:
        if self._assistant_attempted:
            return
        self._assistant_attempted = True
        safe_present = content_present if isinstance(content_present, bool) else False
        safe_length = (
            min(content_length, _MAX_ASSISTANT_CONTENT_LENGTH)
            if isinstance(content_length, int)
            and not isinstance(content_length, bool)
            and content_length >= 0
            else 0
        )
        self._lifecycle.append_event(
            "assistant.completed",
            payload={
                "contentPresent": safe_present,
                "contentLength": safe_length,
                "kind": "returned_assistant",
            },
        )


@contextmanager
def observe_run(
    *,
    workspace: str | Path,
    source: str,
    title: str,
    session_id: str | None = None,
    journal_factory: JournalFactory | None = None,
    enabled: bool = True,
) -> Iterator[RunObservation]:
    """Observe one top-level task without changing its result or exceptions.

    ``create_run()`` records queued.  Context entry then records running.  A
    normal exit records completed, an ordinary ``Exception`` records failed,
    and ``KeyboardInterrupt``/``SystemExit`` record interrupted.  Every Journal
    or logging failure is isolated and turns observation into a no-op.
    """
    lifecycle = _BestEffortLifecycle(
        workspace=workspace,
        source=source,
        title=title,
        session_id=session_id,
        journal_factory=journal_factory or _default_journal_factory,
        enabled=enabled,
    )
    lifecycle.start()
    observation = RunObservation(lifecycle)
    try:
        yield observation
    except TurnCancellationRequested:
        lifecycle.finish("interrupted", reason="execution_cancelled")
        raise
    except (KeyboardInterrupt, SystemExit):
        lifecycle.finish("interrupted", reason="execution_interrupted")
        raise
    except Exception:
        lifecycle.finish("failed", reason="execution_failed")
        raise
    else:
        lifecycle.finish("completed")


__all__ = ["JournalFactory", "RunObservation", "observe_run"]
