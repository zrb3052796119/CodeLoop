"""Session-backed synchronous conversation-turn transaction.

The service owns conversation truth: workspace isolation, exactly one linked Run,
one Agent execution, finished-turn Session projection, one commit attempt, and
fixed domain failures.  HTTP and frontend concerns deliberately stay outside.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from minicode import session as session_module
from minicode.agent_runtime import (
    AgentTurnRuntime,
    create_agent_turn_runtime,
    prepare_conversation_messages,
)
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    TurnRecord,
    create_turn_id,
    request_fingerprint,
    validate_turn_id,
)
from minicode.run_events import emit_skill_routing_safely
from minicode.run_journal import (
    RunJournal,
    RunJournalError,
    RunJournalUserSignalConflictError,
)
from minicode.run_lifecycle import JournalFactory, observe_run
from minicode.session import (
    SessionData,
    SessionStoreBusyError,
    SessionWriteConflictError,
    create_new_session,
    find_turn_commit,
    load_session,
    save_session,
    validate_turn_commits,
)
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
    raise_if_cancelled,
)

if TYPE_CHECKING:
    from minicode.conversation_presentation import ConversationPresentation
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.permission_approval import PermissionApprovalBroker
    from minicode.user_evidence import TrustedUserEvidenceSeed


class RuntimeFactory(Protocol):
    def __call__(
        self,
        *,
        workspace: Path,
        prompt: str,
        mcp_current_state_registry: McpCurrentStateRegistry | None = None,
        event_sink: Any | None = None,
        cancellation_token: TurnCancellationToken | None = None,
        trusted_user_evidence: tuple[TrustedUserEvidenceSeed, ...] = (),
    ) -> AgentTurnRuntime: ...


class ConversationError(RuntimeError):
    """Safe domain failure with a fixed public code."""

    code = "turn_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class ConversationSessionNotFound(ConversationError):
    code = "session_not_found"


class ConversationSessionConflict(ConversationError):
    code = "session_conflict"


class ConversationSessionBusy(ConversationError):
    code = "session_busy"


class ConversationRuntimeUnavailable(ConversationError):
    code = "runtime_unavailable"


class ConversationTurnFailed(ConversationError):
    code = "turn_failed"


class ConversationTurnIdConflict(ConversationError):
    code = "turn_id_conflict"


class ConversationTurnInProgress(ConversationError):
    code = "turn_in_progress"


class ConversationTurnInterrupted(ConversationError):
    code = "turn_interrupted"


class ConversationTurnCancelled(ConversationError):
    code = "turn_cancelled"


class ConversationTurnNotFound(ConversationError):
    code = "turn_not_found"


class ConversationFeedbackConflict(ConversationError):
    code = "feedback_conflict"


class ConversationFeedbackUnavailable(ConversationError):
    code = "feedback_unavailable"


class _TurnCompletionWriteFailed(RuntimeError):
    """Session committed, but the necessary Turn completion write did not."""


@dataclass(frozen=True, slots=True)
class ConversationTurnResult:
    turn_id: str
    session_id: str
    created: bool
    assistant: str
    updated_at: str
    run_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationTurnStatus:
    turn_id: str
    status: str
    session_id: str | None
    created_session: bool | None
    run_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    error_code: str | None
    result_available: bool


@dataclass(frozen=True, slots=True)
class ConversationCancellationResult:
    turn_id: str
    status: str
    cancellation_accepted: bool
    session_id: str | None
    created_session: bool | None
    run_id: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class ConversationFeedbackResult:
    turn_id: str
    run_id: str
    signal: str
    source: str
    recorded_at: str


def _iso_timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _same_workspace(candidate: str, workspace: Path) -> bool:
    try:
        return Path(candidate).expanduser().resolve() == workspace
    except (OSError, RuntimeError, ValueError):
        return False


_TURN_MESSAGE_ID_FIELD = "_conversation_turn_id"


def _turn_result(
    messages: object,
    *,
    turn_id: str,
) -> tuple[str, int, int] | None:
    """Project only the result produced after this turn's user message.

    A committed Workflow intentionally ends with an authoritative
    ``tool_result`` rather than a closing assistant message.  That result is
    valid only when it is paired with the immediately preceding
    ``assistant_tool_call``; this prevents an unrelated historical tool output
    from becoming the current turn's answer.
    """
    if not isinstance(messages, list):
        return None

    user_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and message.get("role") == "user"
        and message.get(_TURN_MESSAGE_ID_FIELD) == turn_id
    ]
    if len(user_indexes) != 1:
        return None
    user_index = user_indexes[0]

    for index in range(len(messages) - 1, user_index, -1):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if message.get("role") == "assistant":
            if isinstance(content, str) and content:
                return content, user_index, index
            continue
        if message.get("role") != "tool_result":
            continue
        if not isinstance(content, str) or not content:
            continue
        if index <= user_index:
            continue
        previous = messages[index - 1]
        if not isinstance(previous, dict):
            continue
        if (
            previous.get("role") == "assistant_tool_call"
            and message.get("toolUseId")
            and previous.get("toolUseId") == message.get("toolUseId")
        ):
            return content, user_index, index
    return None


def _error_for_code(code: str | None) -> ConversationError:
    errors: dict[str, type[ConversationError]] = {
        "session_not_found": ConversationSessionNotFound,
        "session_conflict": ConversationSessionConflict,
        "session_busy": ConversationSessionBusy,
        "runtime_unavailable": ConversationRuntimeUnavailable,
        "turn_failed": ConversationTurnFailed,
        "turn_interrupted": ConversationTurnInterrupted,
        "turn_cancelled": ConversationTurnCancelled,
    }
    return errors.get(code or "", ConversationTurnFailed)()


def _apply_run_outcome_memory_feedback(
    workspace: Path,
    journal: Any,
    run_id: str,
    signal: str,
) -> None:
    """Best-effort weak response feedback for exact rendered versions.

    The Dashboard signal evaluates the whole response, not an individual
    Memory claim. It can therefore update ordinary usefulness evidence but
    can never corroborate or quarantine every rendered entry. Legacy ID-only
    Runs fail closed because they cannot prove which content was evaluated.
    """
    try:
        rendered_versions = journal.get_rendered_memory_versions(run_id)
    except Exception:  # noqa: BLE001 - response attribution is optional
        return
    if not rendered_versions:
        return
    try:
        from minicode.memory import MemoryManager

        manager = MemoryManager(project_root=workspace)
        manager.record_feedback(
            list(dict.fromkeys(entry_id for entry_id, _ in rendered_versions)),
            signal == "accept",
            observation_id=run_id,
            source="explicit_run_outcome",
            expected_versions=rendered_versions,
        )
    except Exception:  # noqa: BLE001 - attribution must not fail feedback
        pass


def _apply_written_run_outcome_feedback(
    workspace: Path,
    journal: Any,
    run_id: str,
    signal: str,
) -> None:
    """Best-effort weak response feedback for exact written versions.

    A response-level signal does not prove that every lesson extracted from
    that response is true or false. Version-bound ordinary evidence is the
    strongest safe attribution until a concrete claim is shown to, and
    explicitly judged by, the user.
    """
    try:
        written_versions = journal.get_written_memory_versions(run_id)
    except Exception:  # noqa: BLE001 - the verdict is optional
        return
    if not written_versions:
        return
    try:
        from minicode.memory import MemoryManager

        manager = MemoryManager(project_root=workspace)
        manager.record_feedback(
            list(dict.fromkeys(entry_id for entry_id, _ in written_versions)),
            signal == "accept",
            observation_id=run_id,
            source="explicit_run_outcome",
            expected_versions=written_versions,
        )
    except Exception:  # noqa: BLE001 - the verdict must not fail feedback
        pass


class ConversationTurnService:
    """Execute and durably commit one synchronous Dashboard conversation turn."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        runtime_factory: RuntimeFactory = create_agent_turn_runtime,
        session_loader: Callable[[str], SessionData | None] = load_session,
        session_creator: Callable[[str], SessionData] = create_new_session,
        session_saver: Callable[[SessionData], None] = save_session,
        journal_factory: JournalFactory | None = None,
        observation_enabled: bool = True,
        mcp_current_state_registry: McpCurrentStateRegistry | None = None,
        turn_store: ConversationTurnStore | None = None,
        approval_broker: PermissionApprovalBroker | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._runtime_factory = runtime_factory
        self._session_loader = session_loader
        self._session_creator = session_creator
        self._session_saver = session_saver
        self._journal_factory = journal_factory
        self._observation_enabled = observation_enabled
        self._mcp_current_state_registry = mcp_current_state_registry
        self._approval_broker = approval_broker
        self._turn_store = turn_store or ConversationTurnStore(
            self.workspace,
            data_dir=session_module.MINI_CODE_DIR,
        )

    def _session(self, session_id: str | None) -> tuple[SessionData, bool]:
        if session_id is None:
            return self._session_creator(str(self.workspace)), True
        session = self._session_loader(session_id)
        if session is None or not _same_workspace(session.workspace, self.workspace):
            raise ConversationSessionNotFound()
        return session, False

    @staticmethod
    def _sync_session(
        session: SessionData,
        *,
        messages: list[dict[str, Any]],
        user_message: str,
        runtime: Any,
    ) -> None:
        # Runtime-only turn identity lets the transaction relocate the current
        # user after compaction/reordering.  It must never become part of the
        # persisted or externally rendered message schema.
        session.messages = [
            {
                key: value
                for key, value in message.items()
                if key != _TURN_MESSAGE_ID_FIELD
            }
            for message in messages
        ]
        session.history = [*session.history, user_message]
        session.permissions_summary = runtime.permissions.get_summary()
        session.skills = runtime.tools.get_skills()
        session.mcp_servers = runtime.tools.get_mcp_servers()

    def _commit(
        self,
        session: SessionData,
        *,
        messages: list[dict[str, Any]],
        user_message: str,
        runtime: Any,
        turn_marker: dict[str, Any] | None = None,
    ) -> None:
        self._sync_session(
            session,
            messages=messages,
            user_message=user_message,
            runtime=runtime,
        )
        if turn_marker is not None:
            if find_turn_commit(session, str(turn_marker.get("turnId", ""))) is not None:
                raise ConversationTurnFailed()
            session.turn_commits = [*session.turn_commits, dict(turn_marker)]
        try:
            self._session_saver(session)
        except SessionWriteConflictError as error:
            raise ConversationSessionConflict() from error
        except SessionStoreBusyError as error:
            raise ConversationSessionBusy() from error
        except Exception as error:  # noqa: BLE001 - fixed safe service boundary
            raise ConversationTurnFailed() from error

    def _best_effort_user_commit(
        self,
        session: SessionData,
        *,
        messages: list[dict[str, Any]],
        user_message: str,
        runtime: Any,
        turn_id: str,
        cancellation_token: TurnCancellationToken,
    ) -> None:
        raise_if_cancelled(cancellation_token)
        try:
            gate = self._turn_store.begin_commit(turn_id)
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        if not gate.commit_allowed:
            raise TurnCancellationRequested(turn_id)
        try:
            self._commit(
                session,
                messages=messages,
                user_message=user_message,
                runtime=runtime,
                turn_marker=None,
            )
        except ConversationError:
            pass

    @staticmethod
    def _runtime_execute(
        runtime: Any,
        messages: list[dict[str, Any]],
        observation: Any,
        cancellation_token: TurnCancellationToken,
        presentation: ConversationPresentation | None,
        approval_session: object | None,
    ) -> list[dict[str, Any]]:
        """Pass optional seams only to runtimes that explicitly support them."""
        try:
            parameters = tuple(inspect.signature(runtime.execute).parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        names = {parameter.name for parameter in parameters}
        kwargs: dict[str, object] = {}
        if has_kwargs or "cancellation_token" in names:
            kwargs["cancellation_token"] = cancellation_token
        if presentation is not None and (has_kwargs or "presentation" in names):
            kwargs["presentation"] = presentation
        if approval_session is not None and (has_kwargs or "approval_session" in names):
            kwargs["approval_session"] = approval_session
        if has_kwargs or "defer_curator_maintenance" in names:
            # ConversationTurnService owns the Session compare-and-swap. A
            # losing pre-commit execution must not count as a completed task.
            kwargs["defer_curator_maintenance"] = True
        return runtime.execute(messages, observation, **kwargs)

    def _create_runtime(
        self,
        *,
        message: str,
        observation: Any,
        cancellation_token: TurnCancellationToken,
        intent_context: object | None = None,
        trusted_user_evidence: tuple[TrustedUserEvidenceSeed, ...] = (),
    ) -> Any:
        """Pass lifecycle seams only when an injected factory supports them."""
        try:
            parameters = tuple(
                inspect.signature(self._runtime_factory).parameters.values()
            )
        except (TypeError, ValueError):
            parameters = ()
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        names = {parameter.name for parameter in parameters}
        kwargs: dict[str, object] = {
            "workspace": self.workspace,
            "prompt": message,
            "mcp_current_state_registry": self._mcp_current_state_registry,
        }
        if has_kwargs or "event_sink" in names:
            kwargs["event_sink"] = observation
        if has_kwargs or "cancellation_token" in names:
            kwargs["cancellation_token"] = cancellation_token
        if intent_context is not None and (
            has_kwargs or "intent_context" in names
        ):
            kwargs["intent_context"] = intent_context
        if trusted_user_evidence and (
            has_kwargs or "trusted_user_evidence" in names
        ):
            kwargs["trusted_user_evidence"] = trusted_user_evidence
        return self._runtime_factory(**kwargs)

    def _trusted_correction_evidence(
        self,
        *,
        session: SessionData,
        message: str,
        current_turn_id: str,
        current_run_id: str | None,
    ) -> tuple[TrustedUserEvidenceSeed, ...]:
        """Join an adjacent explicit ``correct`` signal to its follow-up text.

        Every durable identity in this chain is independently checked. Any
        stale, malformed, cross-session, non-adjacent, or non-correction state
        yields no strong evidence and never blocks the user's Turn.
        """
        if current_run_id is None:
            return ()
        try:
            from minicode.user_evidence import (
                TrustedUserEvidenceSeed,
                looks_like_correction,
            )

            if not looks_like_correction(message.strip()):
                return ()
            markers = validate_turn_commits(
                session.turn_commits,
                messages=session.messages,
            )
            if not markers:
                return ()
            marker = max(markers, key=lambda item: item["assistantMessageIndex"])
            conversational_indexes = [
                index
                for index, item in enumerate(session.messages)
                if isinstance(item, dict)
                and item.get("role") in {"user", "assistant"}
            ]
            if (
                not conversational_indexes
                or conversational_indexes[-1] != marker["assistantMessageIndex"]
            ):
                return ()
            prior_turn_id = str(marker["turnId"])
            record = self._turn_store.get(prior_turn_id)
            store_marker = self._store_marker(marker)
            if (
                record is None
                or record.status != "completed"
                or record.session_id != session.session_id
                or record.run_id is None
                or record.commit_marker != store_marker
                or self._authoritative_result(record) is None
            ):
                return ()
            journal = (
                self._journal_factory(self.workspace)
                if self._journal_factory is not None
                else RunJournal(self.workspace)
            )
            prior_run = journal.get_run(record.run_id)
            signal = journal.get_user_signal(record.run_id)
            if (
                prior_run is None
                or prior_run.id != record.run_id
                or prior_run.status != "completed"
                or prior_run.source != "gateway"
                or prior_run.session_id != session.session_id
                or signal is None
                or signal.signal != "correct"
                or signal.source != "explicit_user_action"
            ):
                return ()
            return (
                TrustedUserEvidenceSeed.followup_correction(
                    message,
                    current_turn_id=current_turn_id,
                    current_run_id=current_run_id,
                    prior_turn_id=prior_turn_id,
                    prior_run_id=record.run_id,
                ),
            )
        except Exception:  # noqa: BLE001 - strong evidence fails closed
            return ()

    @staticmethod
    def _defer_runtime_user_evidence(
        runtime: Any,
        *,
        message: str,
        turn_id: str,
        run_id: str | None,
    ) -> tuple[TrustedUserEvidenceSeed, ...]:
        """Take strong evidence out of pre-commit Agent reflection."""
        take = getattr(runtime, "take_trusted_user_evidence_for_commit", None)
        if not callable(take):
            return ()
        try:
            value = take(
                direct_user_message=message,
                current_turn_id=turn_id,
                current_run_id=run_id,
            )
        except Exception:  # noqa: BLE001 - evidence fails closed, Turn continues
            return ()
        return value if isinstance(value, tuple) else ()

    @staticmethod
    def _persist_committed_runtime_user_evidence(
        runtime: Any,
        evidence: tuple[TrustedUserEvidenceSeed, ...],
        observation: Any,
        *,
        message: str,
        turn_id: str,
        run_id: str | None,
    ) -> None:
        """Best-effort post-Session effect; committed conversation truth wins."""
        if not evidence:
            return
        persist = getattr(runtime, "persist_committed_user_evidence", None)
        if not callable(persist):
            return
        try:
            persist(
                evidence,
                observation,
                direct_user_message=message,
                current_turn_id=turn_id,
                current_run_id=run_id,
            )
        except BaseException as error:  # committed Session remains authoritative
            try:
                from minicode.agent_runtime import (
                    CommittedUserEvidencePersistenceUnavailable,
                )
                from minicode.memory_persistence_event import (
                    MEMORY_PERSISTENCE_FAILURE_EVENT_TYPE,
                    memory_persistence_failure_payload,
                )
                from minicode.user_evidence import (
                    committed_user_evidence_operation_id,
                )

                operation_id = committed_user_evidence_operation_id(turn_id, run_id)
                reason_codes = ("unexpected_failure",)
                if isinstance(
                    error,
                    CommittedUserEvidencePersistenceUnavailable,
                ):
                    operation_id = error.operation_id
                    reason_codes = error.reason_codes
                observation.emit(
                    MEMORY_PERSISTENCE_FAILURE_EVENT_TYPE,
                    payload=memory_persistence_failure_payload(
                        phase="committed_user_evidence",
                        operation_id=operation_id,
                        reason_codes=reason_codes,
                    ),
                )
            except BaseException:
                pass

    @staticmethod
    def _maintain_committed_runtime_turn(
        runtime: Any,
        observation: Any,
        *,
        turn_id: str,
        run_id: str | None,
    ) -> None:
        """Best-effort maintenance strictly after Session CAS success."""

        maintain = getattr(runtime, "maintain_committed_turn", None)
        if not callable(maintain):
            return
        try:
            parameters = tuple(inspect.signature(maintain).parameters.values())
        except (TypeError, ValueError):
            return
        supports_recorded = any(
            parameter.name == "completion_already_recorded"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if not supports_recorded:
            # Calling an older implementation would make it re-record the
            # already settled Dashboard completion through the CLI facade.
            return
        try:
            maintain(
                observation,
                current_turn_id=turn_id,
                current_run_id=run_id,
                completion_already_recorded=True,
            )
        except Exception:
            # Conversation truth and its user-visible response are already
            # committed. Durable scheduler state retains or leases any debt.
            pass

    def _record_committed_curator_completion(
        self,
        *,
        turn_id: str,
        run_id: str | None,
    ) -> bool:
        """Persist the recoverable completion debt without claiming work."""

        try:
            from minicode.curator_schedule import (
                CuratorScheduleStore,
                curator_completion_id,
            )

            completion_id = curator_completion_id(run_id, turn_id)
            if completion_id is None:
                return False
            schedule = CuratorScheduleStore(self.workspace)
            with schedule.delivery_fence():
                schedule.record_completion(
                    completion_id,
                    pin=True,
                    promote_existing=True,
                )
            return True
        except Exception:
            # The committed Turn record remains a durable reconstruction
            # source. Recovery and terminal replay retry this exact receipt.
            return False

    def _reconcile_cli_curator_receipts(self, schedule: object) -> bool:
        """Drain CLI-owned workspace receipts before a Dashboard delivery."""

        try:
            from minicode.curator_outbox import reconcile_run_curator_outbox

            journal = (
                self._journal_factory(self.workspace)
                if self._journal_factory is not None
                else RunJournal(
                    self.workspace,
                    data_dir=self._turn_store.data_dir,
                )
            )
            return reconcile_run_curator_outbox(journal, schedule)
        except Exception:
            return False

    def _ensure_committed_curator_completion(
        self,
        record: TurnRecord,
    ) -> TurnRecord:
        """Deliver and acknowledge a committed Turn's durable receipt."""

        try:
            from minicode.curator_schedule import (
                CuratorScheduleStore,
                curator_completion_id,
            )

            completion_id = curator_completion_id(
                record.run_id,
                record.turn_id,
            )
            if completion_id is None:
                return record
            schedule = CuratorScheduleStore(self.workspace)
            with schedule.delivery_fence():
                if not self._reconcile_cli_curator_receipts(schedule):
                    return record
                # The caller may have scanned before another worker completed
                # the handshake. Re-read under the delivery fence so a stale
                # snapshot can never resurrect an already-settled receipt.
                current = self._turn_store.get(record.turn_id)
                if current is None:
                    return record
                if (
                    current.curator_outbox_version is None
                    or current.status not in {"committing", "completed"}
                    or current.session_id is None
                    or current.run_id is None
                    or curator_completion_id(
                        current.run_id,
                        current.turn_id,
                    )
                    != completion_id
                ):
                    return current
                if current.curator_receipt_settled:
                    return current
                if not current.curator_receipt_recorded:
                    if not self._record_committed_curator_completion(
                        turn_id=current.turn_id,
                        run_id=current.run_id,
                    ):
                        return current
                    current = self._turn_store.mark_curator_receipt_recorded(
                        current.turn_id
                    )
                schedule.acknowledge_completion(completion_id)
                return self._turn_store.mark_curator_receipt_settled(
                    current.turn_id
                )
        except Exception:
            # The unsettled Turn stays retention-protected and is retried by
            # terminal replay or the bounded release reconciler.
            return record

    def _reconcile_curator_receipts(self) -> bool:
        """Best-effort recovery of committed Dashboard completion receipts."""

        try:
            from minicode.curator_outbox import (
                reconcile_turn_curator_outbox,
                turn_recovery_scan_budget,
            )
            from minicode.curator_schedule import CuratorScheduleStore

            if not reconcile_turn_curator_outbox(
                self._turn_store,
                CuratorScheduleStore(self.workspace),
            ):
                return False
            # Bound this invocation by the store's own durable scan horizon.
            # Counting unique Turns (rather than arbitrary pages) makes a
            # complete recovery pass independent of page size while repeated
            # unresolved records fail closed instead of spinning.
            scan_budget = turn_recovery_scan_budget(self._turn_store)
            observed_turn_ids: set[str] = set()
            while True:
                remaining = scan_budget - len(observed_turn_ids)
                scan = self._turn_store.curator_receipt_recovery_records(
                    limit=min(1_000, max(1, remaining)),
                    include_settled_committing=True,
                )
                if not scan.items:
                    return bool(scan.exhaustive)
                page_turn_ids = {record.turn_id for record in scan.items}
                newly_observed = page_turn_ids - observed_turn_ids
                if len(newly_observed) > remaining:
                    return False
                observed_turn_ids.update(page_turn_ids)
                progressed = False
                actionable = False
                for record in scan.items:
                    current = record
                    if current.status == "committing":
                        # Once this fence is acquired, no live owner can still
                        # publish the Session marker for this Turn.  Resolve
                        # every stable committing record here so abandoned
                        # receipts cannot occupy the bounded recovery page and
                        # starve newer authoritative commits forever.
                        with self._turn_store.commit_fence():
                            refreshed = self._turn_store.get(current.turn_id)
                            if refreshed is None:
                                progressed = True
                                continue
                            current = refreshed
                            if current.status == "committing":
                                authoritative = self._authoritative_result(
                                    current
                                )
                                if authoritative is None:
                                    self._turn_store.mark_interrupted(
                                        current.turn_id
                                    )
                                    progressed = True
                                    continue
                                current = self._turn_store.recover_completed(
                                    current.turn_id,
                                    commit_marker=authoritative[1],
                                )
                            elif current.status != "completed":
                                progressed = True
                                continue
                        actionable = True
                    else:
                        actionable = True
                    settled = self._ensure_committed_curator_completion(
                        current
                    )
                    progressed = (
                        progressed or settled.curator_receipt_settled
                    )
                if not actionable:
                    if progressed:
                        # Terminalized stable no-marker records disappear from
                        # the next page. Continue until the bounded scan proves
                        # there is no authoritative commit hidden behind them.
                        continue
                    unsafe_diagnostics = set(scan.diagnostics) - {
                        "curator_receipt_page_limited"
                    }
                    return bool(scan.exhaustive) and not unsafe_diagnostics
                if not progressed:
                    return False
                if not newly_observed:
                    return False
        except Exception:
            # Each source record remains durable and non-retention-eligible.
            return False

    @staticmethod
    def _store_marker(session_marker: dict[str, Any]) -> dict[str, int]:
        return {
            "schemaVersion": 1,
            "userMessageIndex": int(session_marker["userMessageIndex"]),
            "assistantMessageIndex": int(session_marker["assistantMessageIndex"]),
        }

    def _authoritative_result(
        self,
        record: TurnRecord,
    ) -> tuple[ConversationTurnResult, dict[str, int]] | None:
        if record.session_id is None or record.created_session is None:
            return None
        session = self._session_loader(record.session_id)
        if session is None or not _same_workspace(session.workspace, self.workspace):
            return None
        marker = find_turn_commit(session, record.turn_id)
        if marker is None:
            return None
        store_marker = self._store_marker(marker)
        if record.commit_marker is not None and record.commit_marker != store_marker:
            return None
        assistant_index = marker["assistantMessageIndex"]
        if (
            isinstance(assistant_index, bool)
            or not isinstance(assistant_index, int)
            or assistant_index < 0
            or assistant_index >= len(session.messages)
        ):
            return None
        result_message = session.messages[assistant_index]
        assistant = result_message.get("content")
        if (
            result_message.get("role") == "assistant"
            and isinstance(assistant, str)
            and assistant
        ):
            pass
        elif (
            result_message.get("role") == "tool_result"
            and isinstance(assistant, str)
            and assistant
            and assistant_index > 0
            and isinstance(session.messages[assistant_index - 1], dict)
            and session.messages[assistant_index - 1].get("role")
            == "assistant_tool_call"
            and result_message.get("toolUseId")
            and session.messages[assistant_index - 1].get("toolUseId")
            == result_message.get("toolUseId")
        ):
            pass
        else:
            return None
        return (
            ConversationTurnResult(
                turn_id=record.turn_id,
                session_id=session.session_id,
                created=record.created_session,
                assistant=assistant,
                updated_at=_iso_timestamp(session.updated_at),
                run_id=record.run_id,
            ),
            store_marker,
        )

    def _reconcile_active(self, record: TurnRecord) -> TurnRecord:
        try:
            if record.status == "committing":
                # Status/feedback/cancel endpoints can reach this recovery
                # without the new-Turn preflight.  First close only delivery
                # work already evidenced by a producer ack or scheduler pin;
                # the shared reconciler deliberately does not count a bare
                # committing Turn.
                from minicode.curator_outbox import (
                    reconcile_turn_curator_outbox,
                )
                from minicode.curator_schedule import CuratorScheduleStore

                if not reconcile_turn_curator_outbox(
                    self._turn_store,
                    CuratorScheduleStore(self.workspace),
                ):
                    raise ConversationTurnFailed()
                # A live committer holds this fence across Session CAS and the
                # terminal Turn write. Once acquired, a missing Session marker
                # is stable and can be interrupted without racing publication.
                with self._turn_store.commit_fence():
                    current = self._turn_store.get(record.turn_id)
                    if current is None:
                        raise ConversationTurnFailed()
                    if current.status in {
                        "completed",
                        "failed",
                        "interrupted",
                        "cancelled",
                    }:
                        return current
                    authoritative = self._authoritative_result(current)
                    if authoritative is not None:
                        recovered = self._turn_store.recover_completed(
                            current.turn_id,
                            commit_marker=authoritative[1],
                        )
                        return self._ensure_committed_curator_completion(
                            recovered
                        )
                    return self._turn_store.mark_interrupted(current.turn_id)
            authoritative = self._authoritative_result(record)
            if authoritative is not None:
                recovered = self._turn_store.recover_completed(
                    record.turn_id,
                    commit_marker=authoritative[1],
                )
                return self._ensure_committed_curator_completion(recovered)
            if record.status == "cancel_requested":
                return self._turn_store.mark_cancelled(record.turn_id)
            return self._turn_store.mark_interrupted(record.turn_id)
        except Exception as error:  # noqa: BLE001 - fixed safe storage boundary
            raise ConversationTurnFailed() from error

    def _terminal_result(self, record: TurnRecord) -> ConversationTurnResult:
        if record.status == "completed":
            authoritative = self._authoritative_result(record)
            if authoritative is not None:
                self._ensure_committed_curator_completion(record)
                return authoritative[0]
            raise ConversationTurnInterrupted()
        raise _error_for_code(record.error_code)

    def _record_failure(self, turn_id: str, error: ConversationError) -> None:
        try:
            decision = self._turn_store.mark_failed(turn_id, error_code=error.code)
        except Exception as storage_error:  # noqa: BLE001
            self._turn_store.release_claim(turn_id)
            raise ConversationTurnFailed() from storage_error
        if not decision.failure_recorded:
            raise ConversationTurnCancelled()

    def status(self, turn_id: str) -> ConversationTurnStatus:
        try:
            validated = validate_turn_id(turn_id)
            record = self._turn_store.get(validated)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        if record is None:
            raise ConversationTurnNotFound()
        if (
            record.status in {"accepted", "running", "cancel_requested", "committing"}
            and (
                record.owner_id != self._turn_store.owner_id
                or not self._turn_store.is_active(record.turn_id)
            )
        ):
            try:
                live_owner = self._turn_store.has_live_owner(record.turn_id)
            except Exception as error:
                raise ConversationTurnFailed() from error
            if not live_owner:
                record = self._reconcile_active(record)
        result_available = (
            record.status == "completed"
            and self._authoritative_result(record) is not None
        )
        return ConversationTurnStatus(
            turn_id=record.turn_id,
            status=record.status,
            session_id=record.session_id,
            created_session=record.created_session,
            run_id=record.run_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
            error_code=record.error_code,
            result_available=result_available,
        )

    def record_feedback(
        self,
        turn_id: str,
        signal: str,
    ) -> ConversationFeedbackResult:
        """Bind one explicit user action to one authoritative completed Turn."""
        if signal not in {"accept", "correct", "reject"}:
            raise ValueError("invalid feedback signal")
        try:
            validated = validate_turn_id(turn_id)
            record = self._turn_store.get(validated)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ConversationFeedbackUnavailable() from error
        if record is None:
            raise ConversationTurnNotFound()
        if (
            record.status in {"accepted", "running", "cancel_requested", "committing"}
            and (
                record.owner_id != self._turn_store.owner_id
                or not self._turn_store.is_active(record.turn_id)
            )
        ):
            try:
                live_owner = self._turn_store.has_live_owner(record.turn_id)
            except Exception as error:
                raise ConversationFeedbackUnavailable() from error
            if not live_owner:
                record = self._reconcile_active(record)
        if (
            record.status != "completed"
            or record.run_id is None
            or self._authoritative_result(record) is None
        ):
            raise ConversationFeedbackUnavailable()
        try:
            journal = (
                self._journal_factory(self.workspace)
                if self._journal_factory is not None
                else RunJournal(self.workspace)
            )
            stored = journal.record_user_signal(record.run_id, signal)
        except RunJournalUserSignalConflictError as error:
            raise ConversationFeedbackConflict() from error
        except RunJournalError as error:
            raise ConversationFeedbackUnavailable() from error
        except Exception as error:  # noqa: BLE001 - fixed safe domain boundary
            raise ConversationFeedbackUnavailable() from error
        # Re-apply on every idempotent Run-signal replay. Memory owns a durable
        # observation receipt, so this safely closes the crash window between
        # the immutable journal write and its best-effort Memory side effect.
        _apply_run_outcome_memory_feedback(
            self.workspace, journal, record.run_id, stored.signal
        )
        _apply_written_run_outcome_feedback(
            self.workspace, journal, record.run_id, stored.signal
        )
        return ConversationFeedbackResult(
            turn_id=record.turn_id,
            run_id=record.run_id,
            signal=stored.signal,
            source=stored.source,
            recorded_at=stored.recorded_at,
        )

    def cancel(self, turn_id: str) -> ConversationCancellationResult:
        """Request cooperative cancellation without exposing internal identity facts."""
        try:
            validated = validate_turn_id(turn_id)
            record = self._turn_store.get(validated)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        if record is None:
            raise ConversationTurnNotFound()
        if (
            record.status in {"accepted", "running", "cancel_requested", "committing"}
            and (
                record.owner_id != self._turn_store.owner_id
                or not self._turn_store.is_active(record.turn_id)
            )
        ):
            try:
                live_owner = self._turn_store.has_live_owner(record.turn_id)
            except Exception as error:
                raise ConversationTurnFailed() from error
            if not live_owner:
                record = self._reconcile_active(record)
        try:
            decision = self._turn_store.request_cancel(record.turn_id)
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        decided = decision.record
        if decision.cancellation_accepted and self._approval_broker is not None:
            try:
                self._approval_broker.cancel_turn(decided.turn_id)
            except BaseException:  # noqa: BLE001 - durable cancel remains authoritative
                pass
        return ConversationCancellationResult(
            turn_id=decided.turn_id,
            status=decided.status,
            cancellation_accepted=decision.cancellation_accepted,
            session_id=decided.session_id,
            created_session=decided.created_session,
            run_id=decided.run_id,
            updated_at=decided.updated_at,
        )

    def _execute_claimed(
        self,
        *,
        message: str,
        session_id: str | None,
        turn_id: str,
        cancellation_token: TurnCancellationToken,
        presentation: ConversationPresentation | None,
        curator_backlog_safe: bool,
    ) -> ConversationTurnResult:
        raise_if_cancelled(cancellation_token)
        session, created = self._session(session_id)
        raise_if_cancelled(cancellation_token)
        try:
            self._turn_store.attach_session(
                turn_id,
                session_id=session.session_id,
                created_session=created,
            )
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        raise_if_cancelled(cancellation_token)
        runtime: Any | None = None
        approval_session: Any | None = None
        try:
            with observe_run(
                workspace=self.workspace,
                source="gateway",
                title=message,
                session_id=session.session_id,
                journal_factory=self._journal_factory,
                enabled=self._observation_enabled,
            ) as observation:
                try:
                    self._turn_store.attach_run(turn_id, run_id=observation.run_id)
                except Exception as error:  # noqa: BLE001
                    raise ConversationTurnFailed() from error
                raise_if_cancelled(cancellation_token)
                if self._approval_broker is not None:
                    try:
                        approval_session = self._approval_broker.begin_turn(
                            turn_id=turn_id,
                            run_id=observation.run_id,
                            cancellation_token=cancellation_token,
                            event_sink=lambda event_type, payload: observation.emit(
                                event_type, payload=payload
                            ),
                        )
                    except (KeyboardInterrupt, SystemExit, TurnCancellationRequested):
                        raise
                    except Exception as error:  # noqa: BLE001
                        raise ConversationRuntimeUnavailable() from error
                try:
                    from minicode.intent_parser import IntentContext

                    trusted_user_evidence = self._trusted_correction_evidence(
                        session=session,
                        message=message,
                        current_turn_id=turn_id,
                        current_run_id=observation.run_id,
                    )
                    runtime = self._create_runtime(
                        message=message,
                        observation=observation,
                        cancellation_token=cancellation_token,
                        intent_context=IntentContext.from_messages(
                            session.messages
                        ),
                        trusted_user_evidence=trusted_user_evidence,
                    )
                    deferred_user_evidence = self._defer_runtime_user_evidence(
                        runtime,
                        message=message,
                        turn_id=turn_id,
                        run_id=observation.run_id,
                    )
                except (KeyboardInterrupt, SystemExit, TurnCancellationRequested):
                    raise
                except Exception as error:  # noqa: BLE001
                    raise ConversationRuntimeUnavailable() from error
                raise_if_cancelled(cancellation_token)

                previous_prompt = getattr(runtime.permissions, "prompt", None)
                previous_checkpoint = getattr(
                    runtime.permissions, "operation_checkpoint", None
                )
                if approval_session is not None:
                    runtime.permissions.prompt = approval_session.prompt
                    runtime.permissions.operation_checkpoint = (
                        approval_session.check_operation
                    )

                emit_skill_routing_safely(observation, runtime.skill_routing)
                prepared = prepare_conversation_messages(
                    session.messages,
                    system_prompt=runtime.system_prompt,
                    user_message=message,
                )
                prepared[-1][_TURN_MESSAGE_ID_FIELD] = turn_id
                try:
                    runtime.permissions.begin_turn()
                    try:
                        raise_if_cancelled(cancellation_token)
                        result_messages = self._runtime_execute(
                            runtime,
                            prepared,
                            observation,
                            cancellation_token,
                            presentation,
                            approval_session,
                        )
                        raise_if_cancelled(cancellation_token)
                    finally:
                        try:
                            runtime.permissions.end_turn()
                        except Exception:  # noqa: BLE001 - cleanup is isolated
                            pass
                        if approval_session is not None:
                            runtime.permissions.prompt = previous_prompt
                            runtime.permissions.operation_checkpoint = (
                                previous_checkpoint
                            )
                except (KeyboardInterrupt, SystemExit, TurnCancellationRequested):
                    raise
                except Exception as error:  # noqa: BLE001
                    raise_if_cancelled(cancellation_token)
                    self._best_effort_user_commit(
                        session,
                        messages=prepared,
                        user_message=message,
                        runtime=runtime,
                        turn_id=turn_id,
                        cancellation_token=cancellation_token,
                    )
                    raise ConversationTurnFailed() from error

                turn_result = _turn_result(
                    result_messages,
                    turn_id=turn_id,
                )
                if turn_result is None:
                    self._best_effort_user_commit(
                        session,
                        messages=prepared,
                        user_message=message,
                        runtime=runtime,
                        turn_id=turn_id,
                        cancellation_token=cancellation_token,
                    )
                    raise ConversationTurnFailed()
                if not isinstance(result_messages, list) or any(
                    not isinstance(item, dict) for item in result_messages
                ):
                    raise ConversationTurnFailed()
                assistant, user_index, assistant_index = turn_result
                raise_if_cancelled(cancellation_token)
                session_marker = {
                    "schemaVersion": 1,
                    "turnId": turn_id,
                    "userMessageIndex": user_index,
                    "assistantMessageIndex": assistant_index,
                }
                curator_receipt_settled = False
                # Fence the publication sequence so another Gateway cannot
                # observe ``committing`` between Session CAS and the terminal
                # Turn write, then overwrite receipt flags from a stale view.
                with self._turn_store.commit_fence():
                    try:
                        gate = self._turn_store.begin_commit(turn_id)
                    except Exception as error:  # noqa: BLE001
                        raise ConversationTurnFailed() from error
                    if not gate.commit_allowed:
                        raise TurnCancellationRequested(turn_id)
                    self._commit(
                        session,
                        messages=result_messages,
                        user_message=message,
                        runtime=runtime,
                        turn_marker=session_marker,
                    )
                    if curator_backlog_safe:
                        curator_record = (
                            self._ensure_committed_curator_completion(
                                gate.record
                            )
                        )
                        curator_receipt_settled = bool(
                            curator_record.curator_receipt_settled
                        )
                    try:
                        self._turn_store.mark_completed(
                            turn_id,
                            commit_marker=self._store_marker(session_marker),
                        )
                    except Exception as error:  # noqa: BLE001
                        raise _TurnCompletionWriteFailed() from error
                self._persist_committed_runtime_user_evidence(
                    runtime,
                    deferred_user_evidence,
                    observation,
                    message=message,
                    turn_id=turn_id,
                    run_id=observation.run_id,
                )
                if curator_backlog_safe and curator_receipt_settled:
                    self._maintain_committed_runtime_turn(
                        runtime,
                        observation,
                        turn_id=turn_id,
                        run_id=observation.run_id,
                    )
                observation.assistant_completed(
                    content_present=True,
                    content_length=len(assistant),
                )
                result = ConversationTurnResult(
                    turn_id=turn_id,
                    session_id=session.session_id,
                    created=created,
                    assistant=assistant,
                    updated_at=_iso_timestamp(session.updated_at),
                    run_id=observation.run_id,
                )
            return result
        finally:
            if approval_session is not None:
                try:
                    approval_session.close()
                except BaseException:  # noqa: BLE001 - cleanup cannot replace outcome
                    pass
            if runtime is not None:
                try:
                    runtime.dispose()
                except Exception:  # noqa: BLE001 - cleanup cannot replace outcome
                    pass

    def turn(
        self,
        *,
        message: str,
        session_id: str | None,
        turn_id: str | None = None,
        presentation: ConversationPresentation | None = None,
    ) -> ConversationTurnResult:
        curator_backlog_safe = self._reconcile_curator_receipts()
        validated_turn_id = create_turn_id() if turn_id is None else validate_turn_id(turn_id)
        fingerprint = request_fingerprint(
            workspace_id=self._turn_store.workspace_id,
            session_id=session_id,
            message=message,
        )
        try:
            claim = self._turn_store.claim(
                turn_id=validated_turn_id,
                fingerprint=fingerprint,
            )
        except Exception as error:  # noqa: BLE001
            raise ConversationTurnFailed() from error
        if claim.disposition == "conflict":
            raise ConversationTurnIdConflict()
        if claim.disposition == "in_progress":
            raise ConversationTurnInProgress()
        if claim.disposition == "recover":
            return self._terminal_result(self._reconcile_active(claim.record))
        if claim.disposition == "terminal":
            return self._terminal_result(claim.record)
        try:
            start = self._turn_store.mark_running(validated_turn_id)
            if not start.execution_started:
                raise ConversationTurnCancelled()
            cancellation_token = self._turn_store.cancellation_token(
                validated_turn_id
            )
        except ConversationTurnCancelled:
            raise
        except Exception as error:  # noqa: BLE001
            self._turn_store.release_claim(validated_turn_id)
            raise ConversationTurnFailed() from error
        try:
            return self._execute_claimed(
                message=message,
                session_id=session_id,
                turn_id=validated_turn_id,
                cancellation_token=cancellation_token,
                presentation=presentation,
                curator_backlog_safe=curator_backlog_safe,
            )
        except _TurnCompletionWriteFailed as error:
            # Preserve committing + Session marker so a new process can reconcile
            # the committed content without ever executing Agent again.
            self._turn_store.release_claim(validated_turn_id)
            raise ConversationTurnFailed() from error
        except TurnCancellationRequested as error:
            try:
                self._turn_store.mark_cancelled(validated_turn_id)
            except Exception as storage_error:  # noqa: BLE001
                self._turn_store.release_claim(validated_turn_id)
                raise ConversationTurnFailed() from storage_error
            raise ConversationTurnCancelled() from error
        except (KeyboardInterrupt, SystemExit):
            try:
                current = self._turn_store.get(validated_turn_id)
                if current is not None and current.status == "committing":
                    # Session CAS may already be durable. Reconcile under the
                    # commit fence instead of destroying the recoverable
                    # producer receipt with an ``interrupted`` transition.
                    # Execution is ending, so first relinquish its lease. This
                    # also lets the generic outbox path join an already-durable
                    # scheduler pin without replaying the producer callback.
                    self._turn_store.release_claim(validated_turn_id)
                    self._reconcile_active(current)
                elif current is not None and current.status not in {
                    "completed",
                    "failed",
                    "interrupted",
                    "cancelled",
                }:
                    self._turn_store.mark_interrupted(validated_turn_id)
            except Exception:  # noqa: BLE001 - process exit remains primary
                self._turn_store.release_claim(validated_turn_id)
            raise
        except ConversationError as error:
            self._record_failure(validated_turn_id, error)
            raise
        except Exception as error:  # noqa: BLE001
            failure = ConversationTurnFailed()
            self._record_failure(validated_turn_id, failure)
            raise failure from error


__all__ = [
    "ConversationError",
    "ConversationCancellationResult",
    "ConversationFeedbackConflict",
    "ConversationFeedbackResult",
    "ConversationFeedbackUnavailable",
    "ConversationRuntimeUnavailable",
    "ConversationSessionBusy",
    "ConversationSessionConflict",
    "ConversationSessionNotFound",
    "ConversationTurnFailed",
    "ConversationTurnCancelled",
    "ConversationTurnResult",
    "ConversationTurnService",
]
