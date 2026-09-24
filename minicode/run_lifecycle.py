"""Best-effort top-level execution lifecycle observation.

Callers provide task identity and keep their existing execution code inside one
context manager.  This module owns RunJournal construction, lifecycle ordering,
safe title preparation, fixed terminal reasons, and complete Journal-failure
isolation.  It deliberately has no dependency on the Dashboard Web package.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol

from minicode.logging_config import get_logger
from minicode.memory_provenance import MemoryProvenanceBatch
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
        memory_provenance_gaps: tuple[str, ...] = (),
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

    def record_rendered_memory_versions(
        self,
        run_id: str,
        versions: list[tuple[str, str]],
    ) -> None: ...

    def record_written_memory_ids(
        self,
        run_id: str,
        entry_ids: list[str],
    ) -> None: ...

    def record_written_memory_versions(
        self,
        run_id: str,
        versions: list[tuple[str, str]],
    ) -> None: ...

    def record_memory_provenance(
        self,
        run_id: str,
        batch: MemoryProvenanceBatch,
        *,
        actor_kind: str = "run_root",
        subagent_id: str | None = None,
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

    def finish(
        self,
        status: str,
        *,
        reason: str | None = None,
        memory_provenance_gaps: tuple[str, ...] = (),
    ) -> None:
        if not self._running or self._journal is None or self._run_id is None:
            return
        try:
            if memory_provenance_gaps:
                self._journal.transition(
                    self._run_id,
                    status,
                    reason=reason,
                    memory_provenance_gaps=memory_provenance_gaps,
                )
            else:
                self._journal.transition(
                    self._run_id,
                    status,
                    reason=reason,
                )
            enforce_retention = getattr(
                self._journal,
                "enforce_retention_if_due",
                None,
            )
            if callable(enforce_retention):
                try:
                    enforce_retention()
                except Exception:  # noqa: BLE001 - lifecycle remains primary
                    _safe_observation_warning("retention")
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

    def record_rendered_memory_versions(
        self,
        versions: list[tuple[str, str]],
    ) -> bool:
        """Persist version-bound prompt materializations, best-effort."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        recorder = getattr(
            self._journal,
            "record_rendered_memory_versions",
            None,
        )
        if not callable(recorder):
            return False
        try:
            recorder(self._run_id, versions)
        except Exception:  # noqa: BLE001 - observation is optional
            _safe_observation_warning("memory_rendered_versions")
            return False
        return True

    def record_written_memory_ids(self, entry_ids: list[str]) -> bool:
        """Persist the Memory IDs this turn produced, under the same
        strictly-optional observation contract as the rendered IDs."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        try:
            self._journal.record_written_memory_ids(self._run_id, entry_ids)
        except Exception:  # noqa: BLE001 - trace failures never alter execution
            _safe_observation_warning("memory_written_ids")
            return False
        return True

    def record_written_memory_versions(
        self,
        versions: list[tuple[str, str]],
    ) -> bool:
        """Persist exact versions produced by the Run, best-effort."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        recorder = getattr(
            self._journal,
            "record_written_memory_versions",
            None,
        )
        if not callable(recorder):
            return False
        try:
            recorder(self._run_id, versions)
        except Exception:  # noqa: BLE001 - observation is optional
            _safe_observation_warning("memory_written_versions")
            return False
        return True

    def record_memory_provenance(
        self,
        batch: MemoryProvenanceBatch,
        *,
        actor_kind: str = "run_root",
        subagent_id: str | None = None,
    ) -> bool:
        """Persist one exact Memory effect with its trusted Run actor."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        recorder = getattr(self._journal, "record_memory_provenance", None)
        if not callable(recorder):
            return False
        try:
            recorder(
                self._run_id,
                batch,
                actor_kind=actor_kind,
                subagent_id=subagent_id,
            )
        except Exception:  # noqa: BLE001 - observation is optional
            _safe_observation_warning("memory_provenance")
            return False
        return True

    def record_scheduler_commit_ready(self, ready: object) -> bool:
        """Persist one COMMIT_READY seal without exposing its Run writer."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        recorder = getattr(self._journal, "record_scheduler_commit_ready", None)
        if not callable(recorder):
            _safe_observation_warning("scheduler_commit_ready")
            return False
        try:
            recorder(self._run_id, ready)
        except Exception:  # noqa: BLE001 - authorization observation is optional
            _safe_observation_warning("scheduler_commit_ready")
            return False
        return True

    def record_scheduler_audit(self, audit: object) -> bool:
        """Persist one sealed scheduler audit without exposing its Run writer."""
        if not self._running or self._journal is None or self._run_id is None:
            return False
        recorder = getattr(self._journal, "record_scheduler_audit", None)
        if not callable(recorder):
            _safe_observation_warning("scheduler_audit")
            return False
        try:
            recorder(self._run_id, audit)
        except Exception:  # noqa: BLE001 - audit observation is best effort
            _safe_observation_warning("scheduler_audit")
            return False
        return True

    def open_subagent_journal(self):
        """Open this Run's bounded sub-agent journal, or ``None``.

        Only the journal opener is returned -- never the RunJournal object --
        so callers cannot write arbitrary parent Run events.
        """
        if not self._running or self._journal is None or self._run_id is None:
            return None
        try:
            return self._journal.open_subagent_journal(self._run_id)
        except Exception:  # noqa: BLE001 - sub-journal is optional observation
            _safe_observation_warning("subagent_journal")
            return None

    def reconcile_dashboard_curator_completions(self, schedule: object) -> bool:
        """Drain Dashboard-owned receipts before advancing a CLI producer."""

        if not self._running or self._journal is None:
            return True
        workspace = getattr(self._journal, "workspace", None)
        data_dir = getattr(self._journal, "data_dir", None)
        if workspace is None or data_dir is None:
            # Compatibility journals do not own the production durable stores.
            return True
        try:
            from minicode.conversation_turn_store import ConversationTurnStore
            from minicode.curator_outbox import reconcile_turn_curator_outbox

            store = ConversationTurnStore(workspace, data_dir=data_dir)
            return reconcile_turn_curator_outbox(store, schedule)
        except Exception:
            _safe_observation_warning("dashboard_curator_receipt_replay")
            return False

    def replay_pending_curator_completions(
        self,
        deliver: Callable[[str], object],
        *,
        defer_completion_id: str | None = None,
        release: Callable[[str], object] | None = None,
    ) -> bool:
        """Drain the bounded CLI Run outbox through one delivery seam.

        ``False`` is a fail-closed signal: the caller may durably enqueue its
        current receipt first, but must not deliver it in the same turn. This
        prevents an acknowledged scheduler write whose Run-side
        acknowledgement failed from being silently pushed toward the
        scheduler's bounded dedup horizon by later work.
        """

        if not self._running or self._journal is None or self._run_id is None:
            return True
        if defer_completion_id is not None and (
            not isinstance(defer_completion_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", defer_completion_id) is None
        ):
            return False
        scanner = getattr(
            self._journal,
            "scan_pending_curator_completions",
            None,
        )
        getter = getattr(self._journal, "pending_curator_completions", None)
        acknowledger = getattr(
            self._journal,
            "acknowledge_curator_completion",
            None,
        )
        beginner = getattr(
            self._journal,
            "begin_curator_completion_delivery",
            None,
        )
        settler = getattr(
            self._journal,
            "settle_curator_completion",
            None,
        )
        if (
            not callable(scanner)
            and not callable(getter)
        ) or not callable(acknowledger):
            return True
        try:
            initial_scan = scanner(limit=1_000) if callable(scanner) else None
            pending = (
                initial_scan.items
                if initial_scan is not None
                else getter(limit=1_000)
            )
        except Exception:  # noqa: BLE001 - maintenance remains best effort
            _safe_observation_warning("curator_receipt_replay_read")
            return False
        release_items = (
            getattr(initial_scan, "release_items", ())
            if initial_scan is not None
            else ()
        )
        released_ids: set[str] = set()
        if release is not None:
            if not callable(settler):
                _safe_observation_warning("curator_receipt_replay_settle")
                return False
            for run_id, completion_id in release_items:
                try:
                    release(completion_id)
                except Exception:
                    _safe_observation_warning("curator_receipt_replay_release")
                    return False
                try:
                    if not settler(run_id, completion_id):
                        _safe_observation_warning("curator_receipt_replay_settle")
                        return False
                except Exception:
                    _safe_observation_warning("curator_receipt_replay_settle")
                    return False
                released_ids.add(completion_id)
        for run_id, completion_id in pending:
            if completion_id == defer_completion_id:
                continue
            if callable(beginner):
                try:
                    if not beginner(run_id, completion_id):
                        _safe_observation_warning(
                            "curator_receipt_replay_begin"
                        )
                        return False
                except Exception:
                    _safe_observation_warning(
                        "curator_receipt_replay_begin"
                    )
                    return False
            try:
                deliver(completion_id)
            except Exception:  # the durable item remains pending for next Run
                _safe_observation_warning("curator_receipt_replay_delivery")
                return False
            try:
                if not acknowledger(run_id, completion_id):
                    _safe_observation_warning("curator_receipt_replay_ack")
                    return False
            except Exception:  # scheduler dedup makes later redelivery safe
                _safe_observation_warning("curator_receipt_replay_ack")
                return False
            if release is not None:
                try:
                    release(completion_id)
                except Exception:
                    _safe_observation_warning("curator_receipt_replay_release")
                    return False
                try:
                    if not settler(run_id, completion_id):
                        _safe_observation_warning("curator_receipt_replay_settle")
                        return False
                except Exception:
                    _safe_observation_warning("curator_receipt_replay_settle")
                    return False

        # The journal intentionally bounds each scan.  Do not mistake a full
        # page for a completely drained outbox and advance the current Run.
        try:
            if callable(scanner):
                remaining = scanner(limit=1)
                blocking_items = tuple(
                    item
                    for item in remaining.items
                    if item[1] != defer_completion_id
                )
                backlog_drained = bool(
                    remaining.exhaustive
                    and not blocking_items
                    and not getattr(remaining, "release_items", ())
                )
            else:
                remaining = getter(limit=1)
                backlog_drained = not any(
                    item[1] != defer_completion_id
                    for item in remaining
                )
        except Exception:  # noqa: BLE001 - unknown backlog is not safe to skip
            _safe_observation_warning("curator_receipt_replay_read")
            return False
        if not backlog_drained:
            return False
        if defer_completion_id is None:
            return True
        if defer_completion_id in released_ids:
            return True

        # Keep the current delivery inside the same durable protocol. The
        # ``delivering`` state is a write-ahead barrier: a crash or failed ack
        # makes every later replay retry this receipt before pending newcomers.
        if callable(beginner):
            try:
                if not beginner(self._run_id, defer_completion_id):
                    _safe_observation_warning("curator_receipt_replay_begin")
                    return False
            except Exception:
                _safe_observation_warning("curator_receipt_replay_begin")
                return False
        try:
            deliver(defer_completion_id)
        except Exception:
            _safe_observation_warning("curator_receipt_replay_delivery")
            return False
        try:
            if not acknowledger(self._run_id, defer_completion_id):
                _safe_observation_warning("curator_receipt_replay_ack")
                return False
        except Exception:
            _safe_observation_warning("curator_receipt_replay_ack")
            return False
        if release is not None:
            try:
                release(defer_completion_id)
            except Exception:
                _safe_observation_warning("curator_receipt_replay_release")
                return False
            try:
                if not settler(self._run_id, defer_completion_id):
                    _safe_observation_warning("curator_receipt_replay_settle")
                    return False
            except Exception:
                _safe_observation_warning("curator_receipt_replay_settle")
                return False
        return True

    def acknowledge_curator_completion(self, completion_id: str) -> bool:
        """Acknowledge delivery for this currently running CLI Run."""

        if not self._running or self._journal is None or self._run_id is None:
            return False
        acknowledger = getattr(
            self._journal,
            "acknowledge_curator_completion",
            None,
        )
        if not callable(acknowledger):
            return False
        try:
            return bool(acknowledger(self._run_id, completion_id))
        except Exception:  # noqa: BLE001 - durable pending outbox remains
            _safe_observation_warning("curator_receipt_ack")
            return False

    def settle_curator_completion(self, completion_id: str) -> bool:
        """Mark this Run's producer receipt settled after scheduler release."""

        if not self._running or self._journal is None or self._run_id is None:
            return False
        settler = getattr(self._journal, "settle_curator_completion", None)
        if not callable(settler):
            return False
        try:
            return bool(settler(self._run_id, completion_id))
        except Exception:
            _safe_observation_warning("curator_receipt_settle")
            return False

    def prepare_curator_completion(
        self,
        completion_id: str,
    ) -> bool | Literal["settled"] | None:
        """Activate this Run's durable outbox before scheduler delivery."""

        if not self._running or self._journal is None or self._run_id is None:
            return None
        preparer = getattr(
            self._journal,
            "prepare_curator_completion",
            None,
        )
        if not callable(preparer):
            return None
        try:
            result = preparer(self._run_id, completion_id)
            if result == "settled":
                return "settled"
            return None if result is None else bool(result)
        except Exception:  # noqa: BLE001 - observation is optional
            _safe_observation_warning("curator_receipt_prepare")
            return False


class RunObservation:
    """Small, no-throw handle for callback-derived execution trace metadata."""

    def __init__(self, lifecycle: _BestEffortLifecycle) -> None:
        self._lifecycle = lifecycle
        self._pending_tools: dict[str, deque[str]] = defaultdict(deque)
        # Concurrent tool workers call tool_started/tool_finished from their
        # own threads; the pending map must not race or start/finish events
        # of same-named tools pair to the wrong operation IDs.
        self._pending_tools_lock = threading.Lock()
        self._assistant_attempted = False
        # Provenance names real prompt/write effects.  Unlike presentation
        # telemetry, a transient Journal failure must not be mistaken for a
        # durable acknowledgement: retain the strict content-free batch and
        # retry it before the Run becomes terminal.
        self._memory_provenance_lock = threading.Lock()
        self._pending_memory_provenance: list[
            tuple[str, str | None, MemoryProvenanceBatch]
        ] = []
        self._acknowledged_memory_provenance: set[
            tuple[str, str | None, MemoryProvenanceBatch]
        ] = set()

    @property
    def run_id(self) -> str | None:
        """Return this observation's opaque Run ID, if Journal start succeeded."""
        return self._lifecycle._run_id if self._lifecycle._running else None

    @property
    def curator_completion_id(self) -> str | None:
        """Return the Run-bound identity used by the CLI curator outbox."""

        run_id = self.run_id
        if run_id is None:
            return None
        from minicode.curator_schedule import curator_completion_id

        return curator_completion_id(run_id)

    @property
    def observation_active(self) -> bool:
        """Whether model lifecycle events can currently reach durable storage."""

        return (
            self._lifecycle._running
            and self._lifecycle._journal is not None
            and self._lifecycle._run_id is not None
        )

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

    def record_rendered_memory_versions(
        self,
        versions: list[tuple[str, str]],
    ) -> None:
        """Persist exact rendered Memory versions against this Run."""
        self._lifecycle.record_rendered_memory_versions(versions)

    def record_written_memory_ids(self, entry_ids: list[str]) -> None:
        """Persist the Memory entry IDs this turn produced against this Run."""
        self._lifecycle.record_written_memory_ids(entry_ids)

    def record_written_memory_versions(
        self,
        versions: list[tuple[str, str]],
    ) -> None:
        """Persist exact written Memory versions against this Run."""
        self._lifecycle.record_written_memory_versions(versions)

    def record_memory_provenance(
        self,
        batch: MemoryProvenanceBatch,
    ) -> bool:
        """Bind one root-agent Memory effect to this Run."""
        return self._queue_memory_provenance(
            "run_root",
            None,
            batch,
        )

    def record_subagent_memory_provenance(
        self,
        subagent_id: str,
        batch: MemoryProvenanceBatch,
    ) -> bool:
        """Bind one Memory effect to a parent-assigned sub-agent identity."""
        return self._queue_memory_provenance(
            "subagent",
            subagent_id,
            batch,
        )

    def _queue_memory_provenance(
        self,
        actor_kind: str,
        subagent_id: str | None,
        batch: MemoryProvenanceBatch,
    ) -> bool:
        if not isinstance(batch, MemoryProvenanceBatch):
            return False
        item = (actor_kind, subagent_id, batch)
        with self._memory_provenance_lock:
            if item in self._acknowledged_memory_provenance:
                return True
            if item not in self._pending_memory_provenance:
                self._pending_memory_provenance.append(item)
        self.flush_memory_provenance()
        with self._memory_provenance_lock:
            return item in self._acknowledged_memory_provenance

    def flush_memory_provenance(self, *, final: bool = False) -> bool:
        """Retry unacknowledged batches and close gaps before terminal state.

        When a final exact retry still fails, an effect-scoped ``unknown``
        batch is attempted.  If another exact batch was already recorded this
        makes the Run aggregate ``partial`` instead of falsely ``complete``.
        """
        with self._memory_provenance_lock:
            pending = tuple(self._pending_memory_provenance)
        for item in pending:
            actor_kind, subagent_id, batch = item
            if self._lifecycle.record_memory_provenance(
                batch,
                actor_kind=actor_kind,
                subagent_id=subagent_id,
            ):
                with self._memory_provenance_lock:
                    if item in self._pending_memory_provenance:
                        self._pending_memory_provenance.remove(item)
                    self._acknowledged_memory_provenance.add(item)

        if final:
            with self._memory_provenance_lock:
                unresolved = tuple(self._pending_memory_provenance)
            degraded: set[tuple[str, str | None, str, str]] = set()
            for actor_kind, subagent_id, batch in unresolved:
                degradation_key = (
                    actor_kind,
                    subagent_id,
                    batch.effect,
                    batch.origin,
                )
                if degradation_key in degraded:
                    continue
                degraded.add(degradation_key)
                unknown = MemoryProvenanceBatch(
                    effect=batch.effect,
                    origin=batch.origin,
                    authority_versions=(),
                    coverage="unknown",
                )
                if self._lifecycle.record_memory_provenance(
                    unknown,
                    actor_kind=actor_kind,
                    subagent_id=subagent_id,
                ):
                    with self._memory_provenance_lock:
                        self._pending_memory_provenance = [
                            item
                            for item in self._pending_memory_provenance
                            if not (
                                item[0] == actor_kind
                                and item[1] == subagent_id
                                and item[2].effect == batch.effect
                                and item[2].origin == batch.origin
                            )
                        ]

        with self._memory_provenance_lock:
            return not self._pending_memory_provenance

    @property
    def unresolved_memory_provenance_effects(self) -> tuple[str, ...]:
        """Effects whose exact and fallback provenance remain unacknowledged."""
        with self._memory_provenance_lock:
            return tuple(
                sorted({item[2].effect for item in self._pending_memory_provenance})
            )

    def record_scheduler_commit_ready(self, ready: object) -> bool:
        """Persist COMMIT_READY through the narrow Run lifecycle seam."""
        return self._lifecycle.record_scheduler_commit_ready(ready)

    def record_scheduler_audit(self, audit: object) -> bool:
        """Persist one scheduler audit through the narrow Run lifecycle seam."""
        return self._lifecycle.record_scheduler_audit(audit)

    def open_subagent_journal(self):
        """Open the parent Run's sub-agent sidecar journal, if available."""
        return self._lifecycle.open_subagent_journal()

    def replay_pending_curator_completions(
        self,
        deliver: Callable[[str], object],
        *,
        defer_completion_id: str | None = None,
        release: Callable[[str], object] | None = None,
    ) -> bool:
        """Drain older CLI Run receipts before current maintenance."""

        return self._lifecycle.replay_pending_curator_completions(
            deliver,
            defer_completion_id=defer_completion_id,
            release=release,
        )

    def reconcile_dashboard_curator_completions(self, schedule: object) -> bool:
        """Release or recover Turn-owned pins through the current workspace."""

        return self._lifecycle.reconcile_dashboard_curator_completions(schedule)

    def acknowledge_curator_completion(self, completion_id: str) -> bool:
        """Acknowledge this Run after its scheduler receipt is durable."""

        return self._lifecycle.acknowledge_curator_completion(completion_id)

    def settle_curator_completion(self, completion_id: str) -> bool:
        """Mark this Run receipt settled after its scheduler pin is released."""

        return self._lifecycle.settle_curator_completion(completion_id)

    def prepare_curator_completion(
        self,
        completion_id: str,
    ) -> bool | Literal["settled"] | None:
        """Activate this Run's durable scheduler outbox."""

        return self._lifecycle.prepare_curator_completion(completion_id)

    def tool_started(self, tool_name: str) -> None:
        safe_name = _safe_tool_name(tool_name)
        operation_id = f"toolop_{uuid.uuid4().hex}"
        if self._lifecycle.append_event(
            "tool.started",
            payload={"toolName": safe_name, "operationId": operation_id},
        ):
            with self._pending_tools_lock:
                self._pending_tools[safe_name].append(operation_id)

    def tool_finished(self, tool_name: str, *, is_error: bool) -> None:
        safe_name = _safe_tool_name(tool_name)
        with self._pending_tools_lock:
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
        observation.flush_memory_provenance(final=True)
        lifecycle.finish(
            "interrupted",
            reason="execution_cancelled",
            memory_provenance_gaps=(
                observation.unresolved_memory_provenance_effects
            ),
        )
        raise
    except (KeyboardInterrupt, SystemExit):
        observation.flush_memory_provenance(final=True)
        lifecycle.finish(
            "interrupted",
            reason="execution_interrupted",
            memory_provenance_gaps=(
                observation.unresolved_memory_provenance_effects
            ),
        )
        raise
    except Exception:
        observation.flush_memory_provenance(final=True)
        lifecycle.finish(
            "failed",
            reason="execution_failed",
            memory_provenance_gaps=(
                observation.unresolved_memory_provenance_effects
            ),
        )
        raise
    else:
        observation.flush_memory_provenance(final=True)
        lifecycle.finish(
            "completed",
            memory_provenance_gaps=(
                observation.unresolved_memory_provenance_effects
            ),
        )


__all__ = ["JournalFactory", "RunObservation", "observe_run"]
