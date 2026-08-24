"""Session-backed synchronous conversation-turn transaction.

The service owns conversation truth: workspace isolation, exactly one linked Run,
one Agent execution, finished-turn Session projection, one commit attempt, and
fixed domain failures.  HTTP and frontend concerns deliberately stay outside.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
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


class RuntimeFactory(Protocol):
    def __call__(
        self,
        *,
        workspace: Path,
        prompt: str,
        mcp_current_state_registry: McpCurrentStateRegistry | None = None,
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
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
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


def _apply_corroborated_memory_feedback(
    workspace: Path,
    journal: Any,
    run_id: str,
    signal: str,
) -> None:
    """Best-effort: bind a freshly recorded explicit user signal to this
    Run's rendered Memory entries as corroborated feedback. Kept separate
    from Memory's whole-turn success/failure counters, and never allowed to
    change the outcome of recording the user signal itself."""
    try:
        rendered_ids = journal.get_rendered_memory_ids(run_id)
    except Exception:  # noqa: BLE001 - corroboration is optional
        return
    if not rendered_ids:
        return
    try:
        from minicode.memory import MemoryManager

        manager = MemoryManager(project_root=workspace)
        source = {
            "accept": "explicit_user_accept",
            "correct": "explicit_user_correction",
            "reject": "explicit_user_reject",
        }[signal]
        manager.record_corroborated_feedback(
            list(rendered_ids),
            signal == "accept",
            source=source,
            observation_id=run_id,
        )
    except Exception:  # noqa: BLE001 - corroboration must not fail feedback
        pass


def _apply_written_memory_verdict(
    workspace: Path,
    journal: Any,
    run_id: str,
    signal: str,
) -> None:
    """Best-effort: let an explicit user verdict reach the lesson this Run wrote.

    The rendered sidecar answers "did the memories shown to this turn help?".
    This answers a different question: "should the conclusion this turn drew
    be trusted at all?". A turn the user marked wrong has no business leaving
    a durable lesson sitting in the approval queue as if it had gone well.

    Rejection is chosen over a score nudge because the costs are asymmetric: a
    wrong lesson that gets approved is injected into every later run, while a
    sound lesson that gets rejected is merely unused. Both directions are
    recorded in the approval audit, so the decision stays reviewable.
    """
    try:
        written_ids = journal.get_written_memory_ids(run_id)
    except Exception:  # noqa: BLE001 - the verdict is optional
        return
    if not written_ids:
        return
    try:
        from minicode.memory import MemoryManager

        manager = MemoryManager(project_root=workspace)
        if signal == "accept":
            manager.record_corroborated_feedback(
                list(written_ids),
                True,
                source="explicit_user_accept",
                observation_id=run_id,
            )
            return
        for entry_id in written_ids:
            manager.reject_entry(
                entry_id,
                actor="user_signal",
                reason=f"the user marked this turn '{signal}'",
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
        return runtime.execute(messages, observation, **kwargs)

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
        assistant_message = session.messages[assistant_index]
        assistant = assistant_message.get("content")
        if assistant_message.get("role") != "assistant" or not isinstance(assistant, str) or not assistant:
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
        authoritative = self._authoritative_result(record)
        try:
            if authoritative is not None:
                return self._turn_store.recover_completed(
                    record.turn_id,
                    commit_marker=authoritative[1],
                )
            if record.status == "cancel_requested":
                return self._turn_store.mark_cancelled(record.turn_id)
            return self._turn_store.mark_interrupted(record.turn_id)
        except Exception as error:  # noqa: BLE001 - fixed safe storage boundary
            raise ConversationTurnFailed() from error

    def _terminal_result(self, record: TurnRecord) -> ConversationTurnResult:
        if record.status == "completed":
            authoritative = self._authoritative_result(record)
            if authoritative is not None:
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
        _apply_corroborated_memory_feedback(
            self.workspace, journal, record.run_id, stored.signal
        )
        _apply_written_memory_verdict(
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
                    runtime = self._runtime_factory(
                        workspace=self.workspace,
                        prompt=message,
                        mcp_current_state_registry=self._mcp_current_state_registry,
                    )
                except (KeyboardInterrupt, SystemExit):
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
                try:
                    self._turn_store.mark_completed(
                        turn_id,
                        commit_marker=self._store_marker(session_marker),
                    )
                except Exception as error:  # noqa: BLE001
                    raise _TurnCompletionWriteFailed() from error
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
