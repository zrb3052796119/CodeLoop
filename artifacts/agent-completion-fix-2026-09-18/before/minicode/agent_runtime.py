"""Shared composition for one synchronous top-level Agent turn.

This module builds the existing Model/Tool/Permission/Memory/Skill runtime once
and exposes a small execution object.  Presentation entry points own lifecycle,
Session, and response policy; they do not duplicate component construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from minicode.conversation_presentation import ConversationPresentation
    from minicode.intent_parser import ParsedIntent
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.permission_approval import PermissionApprovalSession
    from minicode.run_lifecycle import RunObservation
    from minicode.turn_cancellation import TurnCancellationToken
    from minicode.user_evidence import TrustedUserEvidenceSeed


class AgentRuntimeConfigError(RuntimeError):
    """Runtime configuration could not be loaded before Agent execution."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("Agent runtime configuration is unavailable.")
        self.cause = cause


class CommittedUserEvidencePersistenceUnavailable(RuntimeError):
    """A committed user-evidence batch has no durable Memory outcome."""

    def __init__(self, operation_id: str, reason_codes: tuple[str, ...]) -> None:
        super().__init__("committed user evidence persistence unavailable")
        self.operation_id = operation_id
        self.reason_codes = reason_codes


@dataclass(slots=True)
class AgentTurnRuntime:
    """Owned runtime components for one top-level Agent turn."""

    workspace: Path
    runtime: dict[str, Any]
    tools: Any
    permissions: Any
    memory_manager: Any
    model: Any
    skill_routing: Any
    system_prompt: str
    context_manager: Any = None
    allow_user_interaction: bool = True
    agent_budget: Any = None
    cancellation_token: Any = None
    deadline_monotonic: float | None = None
    parsed_intent: ParsedIntent | None = None
    trusted_user_evidence: tuple[TrustedUserEvidenceSeed, ...] = ()

    def take_trusted_user_evidence_for_commit(
        self,
        *,
        direct_user_message: str,
        current_turn_id: str,
        current_run_id: str | None,
    ) -> tuple[TrustedUserEvidenceSeed, ...]:
        """Remove strong user evidence from execution and bind it to this Turn.

        Dashboard conversation truth is committed after Agent execution.  Its
        correction/constraint evidence must therefore be held outside normal
        reflection until the Session compare-and-swap has succeeded.
        """
        from minicode.user_evidence import bind_trusted_user_evidence_to_prompt

        candidate = self.trusted_user_evidence
        # Clear first so malformed evidence also fails closed and can never
        # reach the ordinary end-of-Agent reflection path.
        self.trusted_user_evidence = ()
        return bind_trusted_user_evidence_to_prompt(
            candidate,
            direct_user_message,
            agent_depth=0,
            current_turn_id=current_turn_id,
            current_run_id=current_run_id,
            bind_missing_current=True,
        )

    def persist_committed_user_evidence(
        self,
        evidence: tuple[TrustedUserEvidenceSeed, ...],
        observation: RunObservation,
        *,
        direct_user_message: str,
        current_turn_id: str,
        current_run_id: str | None,
    ) -> Any | None:
        """Persist exact user evidence after its Session Turn is authoritative."""
        from minicode.agent_reflection import ReflectionEngine
        from minicode.memory import persistence_text_contains_secret
        from minicode.memory_pipeline import MemoryPipeline
        from minicode.memory_provenance import MemoryProvenanceBatch
        from minicode.reflection_evidence import append_trace_event
        from minicode.run_events import (
            record_memory_provenance_safely,
            record_written_memory_ids_safely,
            record_written_memory_versions_safely,
        )
        from minicode.user_evidence import (
            bind_trusted_user_evidence_to_prompt,
            committed_user_evidence_operation_id,
        )

        bound = bind_trusted_user_evidence_to_prompt(
            evidence,
            direct_user_message,
            agent_depth=0,
            current_turn_id=current_turn_id,
            current_run_id=current_run_id,
            bind_missing_current=True,
        )
        if not bound or any(
            persistence_text_contains_secret(seed.statement) for seed in bound
        ):
            return None

        trace: list[dict[str, Any]] = []
        for seed in bound:
            append_trace_event(trace, seed.to_trace_event())
        append_trace_event(
            trace,
            {
                "type": "task_result",
                "status": "success",
                "final_outcome": "success",
                "completion_succeeded": True,
                "goal_achieved": True,
                "had_errors": False,
                "errors_recovered": False,
            },
        )
        operation_id = committed_user_evidence_operation_id(
            current_turn_id,
            current_run_id,
        )
        pipeline = MemoryPipeline(self.memory_manager)
        try:
            pipeline.initialize(
                workspace_path=str(self.workspace),
                reflection_engine=ReflectionEngine(),
            )
            result = pipeline.write_result(
                "\n".join(seed.statement for seed in bound),
                trace,
                operation_id=operation_id,
                event_sink=observation,
                provenance={
                    "run_id": current_run_id,
                    "turn_id": current_turn_id,
                    "agent_depth": 0,
                    "origin": "committed_user_evidence",
                },
            )
        except Exception as error:
            raise CommittedUserEvidencePersistenceUnavailable(
                operation_id,
                ("pipeline_exception",),
            ) from error
        if result.authority_commit is None:
            raise CommittedUserEvidencePersistenceUnavailable(
                operation_id,
                result.reason_codes or ("authority_unavailable",),
            )
        versions = result.authority_versions
        entry_ids = tuple(entry_id for entry_id, _digest in versions)
        # Exact provenance must establish complete coverage before the legacy
        # ID/version bridges touch their compatibility sidecar.  Reversing
        # this order permanently downgrades the snapshot to ``partial``.
        if versions:
            record_memory_provenance_safely(
                observation,
                MemoryProvenanceBatch(
                    effect="written",
                    origin="reflection",
                    authority_versions=versions,
                ),
            )
        record_written_memory_versions_safely(observation, versions)
        record_written_memory_ids_safely(observation, entry_ids)
        return result

    def maintain_committed_turn(
        self,
        observation: RunObservation,
        *,
        current_turn_id: str,
        current_run_id: str | None,
        completion_already_recorded: bool = False,
    ) -> Any | None:
        """Advance curator scheduling only after Dashboard Session commit."""

        from minicode.agent_reflection import ReflectionEngine
        from minicode.curator_schedule import curator_completion_id
        from minicode.memory_pipeline import MemoryPipeline

        completion_id = curator_completion_id(current_run_id, current_turn_id)
        if completion_id is None:
            return None
        pipeline = MemoryPipeline(self.memory_manager)
        pipeline.initialize(
            model_adapter=self.model,
            workspace_path=str(self.workspace),
            reflection_engine=ReflectionEngine(),
        )
        return pipeline.maintain(
            completion_id=completion_id,
            completion_already_recorded=completion_already_recorded,
            agent_depth=0,
            agent_budget=self.agent_budget,
            event_sink=observation,
            deadline_monotonic=self.deadline_monotonic,
        )

    def execute(
        self,
        messages: list[dict[str, Any]],
        observation: RunObservation,
        *,
        cancellation_token: TurnCancellationToken | None = None,
        presentation: ConversationPresentation | None = None,
        approval_session: PermissionApprovalSession | None = None,
        defer_curator_maintenance: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute the existing Agent Loop once with this runtime."""
        from minicode.agent_loop import run_agent_turn
        from minicode.conversation_presentation import (
            emit_assistant_delta_safely,
            emit_tool_finished_safely,
            emit_tool_started_safely,
        )
        from minicode.skill_router import (
            required_skill_names_for_routing,
            skill_load_policy_for_routing,
        )

        def tool_started(tool_name: str, _tool_input: object) -> None:
            if approval_session is not None:
                try:
                    approval_session.tool_started(tool_name)
                except BaseException:  # noqa: BLE001 - approval context is isolated
                    pass
            try:
                observation.tool_started(tool_name)
            except BaseException:  # noqa: BLE001 - observation is optional
                pass
            emit_tool_started_safely(presentation, tool_name)

        def tool_finished(
            tool_name: str,
            _output: object,
            is_error: bool,
        ) -> None:
            try:
                observation.tool_finished(tool_name, is_error=is_error)
            except BaseException:  # noqa: BLE001 - observation is optional
                pass
            emit_tool_finished_safely(
                presentation,
                tool_name,
                is_error=is_error,
            )
            if approval_session is not None:
                try:
                    approval_session.tool_finished(tool_name)
                except BaseException:  # noqa: BLE001 - approval context is isolated
                    pass

        from minicode.skill_catalog_context import retire_stale_skill_context

        catalog_reader = getattr(self.tools, "get_skills", None)
        if callable(catalog_reader):
            # Routing already bound this snapshot; no second discovery scan.
            messages = retire_stale_skill_context(messages, catalog_reader())

        effective_cancellation_token = (
            cancellation_token
            if cancellation_token is not None
            else self.cancellation_token
        )
        return run_agent_turn(
            model=self.model,
            tools=self.tools,
            messages=messages,
            cwd=str(self.workspace),
            permissions=self.permissions,
            on_tool_start=tool_started,
            on_tool_result=tool_finished,
            on_assistant_stream_chunk=(
                (lambda text: emit_assistant_delta_safely(presentation, text))
                if presentation is not None
                else None
            ),
            memory_manager=self.memory_manager,
            context_manager=self.context_manager,
            runtime=self.runtime,
            event_sink=observation,
            agent_budget=self.agent_budget,
            cancellation_token=effective_cancellation_token,
            deadline_monotonic=self.deadline_monotonic,
            parsed_intent=self.parsed_intent,
            # Presentation-only channel so tools running their own nested
            # loop (the sub-agent tool) can stream live progress to the UI
            # without writing into the Run journal or the approval session.
            presentation=presentation,
            required_skill_names=required_skill_names_for_routing(self.skill_routing),
            skill_load_policy=skill_load_policy_for_routing(self.skill_routing),
            allow_user_interaction=self.allow_user_interaction,
            trusted_user_evidence=self.trusted_user_evidence,
            defer_curator_maintenance=defer_curator_maintenance,
        )

    def dispose(self) -> None:
        """Best-effort release of runtime-owned Tool/MCP resources."""
        try:
            self.tools.dispose()
        except Exception:  # noqa: BLE001 - cleanup must not replace business state
            pass


def prepare_conversation_messages(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str,
    user_message: str,
) -> list[dict[str, Any]]:
    """Copy history, safely update/insert one system prompt, and append one user."""
    prepared = [dict(message) for message in messages if isinstance(message, dict)]
    system_index = next(
        (
            index
            for index, message in enumerate(prepared)
            if message.get("role") == "system"
        ),
        None,
    )
    current_system = {"role": "system", "content": system_prompt}
    if system_index is None:
        prepared.insert(0, current_system)
    else:
        prepared[system_index] = current_system
    prepared.append({"role": "user", "content": user_message})
    return prepared


def final_response_text(messages: list[dict[str, Any]]) -> str | None:
    """Project one finished turn into the user-facing final text.

    Use the latest answer or tool receipt in the current turn. A committed
    workflow may end without closing assistant text when cancellation or
    finalization failure prevents continuation. Older assistant text must
    never hide that newer receipt or answer a new, still-unanswered request.
    """

    for message in reversed(messages):
        if message.get("role") == "user":
            return None
        if message.get("role") in {"assistant", "tool_result"}:
            content = message.get("content")
            if isinstance(content, str) and content:
                return content
    return None


def create_agent_turn_runtime(
    *,
    workspace: Path,
    prompt: str,
    mcp_current_state_registry: McpCurrentStateRegistry | None = None,
    include_mcp: bool = True,
    allow_user_interaction: bool = True,
    event_sink: Any = None,
    cancellation_token: TurnCancellationToken | None = None,
    deadline_monotonic: float | None = None,
    intent_context: Any = None,
    trusted_user_evidence: tuple[TrustedUserEvidenceSeed, ...] = (),
) -> AgentTurnRuntime:
    """Construct the canonical local runtime used by Headless and Dashboard Chat."""
    from minicode.agent_budget import AgentTurnBudget
    from minicode.capability_registry import (
        build_tool_capability_snapshot,
    )
    from minicode.config import load_runtime_config
    from minicode.context_manager import ContextManager
    from minicode.intent_semantic_classifier import (
        build_semantic_intent_adapter,
    )
    from minicode.memory import MemoryManager
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.skill_candidate_reranker import (
        build_skill_candidate_reranker,
    )
    from minicode.skill_routing_turn import (
        SkillRoutingTurnAdapters,
        SkillRoutingTurnRequest,
        route_skill_turn,
    )
    from minicode.tools import create_default_tool_registry
    from minicode.user_evidence import (
        MAX_USER_EVIDENCE_SEEDS,
        normalize_trusted_user_evidence,
        stable_constraint_seeds,
    )

    resolved = Path(workspace).expanduser().resolve()
    supplied_user_evidence = normalize_trusted_user_evidence(
        trusted_user_evidence,
        agent_depth=0,
    )
    # One explicitly bound correction already gives the current message its
    # strongest accurate provenance. Do not persist it a second time as a
    # project constraint merely because the sentence also contains "must".
    generated_constraints = (
        ()
        if any(
            seed.kind == "followup_correction"
            for seed in supplied_user_evidence
        )
        else stable_constraint_seeds(prompt)
    )
    remaining = max(0, MAX_USER_EVIDENCE_SEEDS - len(supplied_user_evidence))
    effective_user_evidence = (
        *supplied_user_evidence,
        *generated_constraints[:remaining],
    )
    try:
        runtime = load_runtime_config(str(resolved))
    except Exception as error:  # noqa: BLE001 - caller owns response policy
        raise AgentRuntimeConfigError(error) from error

    agent_budget = AgentTurnBudget.from_runtime(runtime)
    tools = None
    try:
        if mcp_current_state_registry is None:
            tools = create_default_tool_registry(
                str(resolved),
                runtime=runtime,
                include_mcp=include_mcp,
                include_user_interaction=allow_user_interaction,
            )
        else:
            tools = create_default_tool_registry(
                str(resolved),
                runtime=runtime,
                mcp_current_state_registry=mcp_current_state_registry,
                include_mcp=include_mcp,
                include_user_interaction=allow_user_interaction,
            )
        permissions = PermissionManager(str(resolved), prompt=None)
        memory_manager = MemoryManager(project_root=resolved)
        model = create_model_adapter(
            model=runtime.get("model", ""),
            tools=tools,
            runtime=runtime,
        )
        # Headless/Dashboard turns used to run without any ContextManager, so
        # the whole compaction pipeline was disabled on those entrypoints.
        # Own one here and pass it into the agent loop.
        context_manager = ContextManager(model=runtime.get("model", "default"))
        semantic_adapter = build_semantic_intent_adapter(
            model,
            runtime=runtime,
        )
        skill_candidate_reranker = build_skill_candidate_reranker(
            model,
            runtime=runtime,
        )
        routing_controls = {
            "agent_budget": agent_budget,
            "event_sink": event_sink,
            "cancellation_token": cancellation_token,
            "deadline_monotonic": deadline_monotonic,
            "skill_selection_mode": str(
                (runtime or {}).get("skillSelection", {}).get("mode", "routed")
                or "routed"
            ),
            "skill_flat_catalog_chars": int(
                (runtime or {}).get("skillSelection", {}).get(
                    "flatCatalogCharBudget", 8000
                )
                or 8000
            ),
            "skill_page_chars": int(
                (runtime or {}).get("skillSelection", {}).get(
                    "skillPageCharBudget", 4000
                )
                or 4000
            ),
            "skill_intent_hints": bool(
                (runtime or {}).get("skillSelection", {}).get(
                    "intentHints", False
                )
            ),
            "skill_intent_prefetch": bool(
                (runtime or {}).get("skillSelection", {}).get(
                    "intentPrefetch", False
                )
            ),
        }
        from minicode.skill_router import build_skill_router

        turn_routing = route_skill_turn(
            SkillRoutingTurnRequest(
                prompt=prompt,
                context=intent_context,
                skills=tools.get_skills(),
                registry=build_tool_capability_snapshot(tools),
            ),
            SkillRoutingTurnAdapters(
                router=build_skill_router(str(resolved)),
                semantic_intent=semantic_adapter,
                candidate_reranker=skill_candidate_reranker,
            ),
            controls=routing_controls,
        )
        intent = turn_routing.intent
        skill_routing = turn_routing.routing
        skills_for_prompt = list(turn_routing.prompt_skills)
        system_prompt = build_system_prompt(
            str(resolved),
            permissions.get_summary(),
            {
                "skills": skills_for_prompt,
                "skill_routing": skill_routing.to_dict(),
                "mcpServers": tools.get_mcp_servers(),
                "memory_context": "",
                "user_interaction_available": allow_user_interaction,
            },
        )
        return AgentTurnRuntime(
            workspace=resolved,
            runtime=runtime,
            tools=tools,
            permissions=permissions,
            memory_manager=memory_manager,
            model=model,
            skill_routing=skill_routing,
            system_prompt=system_prompt,
            context_manager=context_manager,
            allow_user_interaction=allow_user_interaction,
            agent_budget=agent_budget,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            parsed_intent=intent,
            trusted_user_evidence=effective_user_evidence,
        )
    except BaseException:
        if tools is not None:
            try:
                tools.dispose()
            except Exception:  # noqa: BLE001 - preserve original exception
                pass
        raise


__all__ = [
    "AgentRuntimeConfigError",
    "AgentTurnRuntime",
    "CommittedUserEvidencePersistenceUnavailable",
    "create_agent_turn_runtime",
    "final_response_text",
    "prepare_conversation_messages",
]
