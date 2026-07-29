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
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.run_lifecycle import RunObservation
    from minicode.turn_cancellation import TurnCancellationToken
    from minicode.permission_approval import PermissionApprovalSession


class AgentRuntimeConfigError(RuntimeError):
    """Runtime configuration could not be loaded before Agent execution."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("Agent runtime configuration is unavailable.")
        self.cause = cause


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

    def execute(
        self,
        messages: list[dict[str, Any]],
        observation: RunObservation,
        *,
        cancellation_token: TurnCancellationToken | None = None,
        presentation: ConversationPresentation | None = None,
        approval_session: PermissionApprovalSession | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the existing Agent Loop once with this runtime."""
        from minicode.agent_loop import run_agent_turn
        from minicode.conversation_presentation import (
            emit_assistant_delta_safely,
            emit_tool_finished_safely,
            emit_tool_started_safely,
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
            event_sink=observation,
            cancellation_token=cancellation_token,
            # Presentation-only channel so tools running their own nested
            # loop (the sub-agent tool) can stream live progress to the UI
            # without writing into the Run journal or the approval session.
            presentation=presentation,
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


def create_agent_turn_runtime(
    *,
    workspace: Path,
    prompt: str,
    mcp_current_state_registry: McpCurrentStateRegistry | None = None,
) -> AgentTurnRuntime:
    """Construct the canonical local runtime used by Headless and Dashboard Chat."""
    from minicode.capability_registry import get_registry, register_tool_capabilities
    from minicode.config import load_runtime_config
    from minicode.intent_parser import parse_intent
    from minicode.memory import MemoryManager
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.skill_router import SkillRouter
    from minicode.tools import create_default_tool_registry

    resolved = Path(workspace).expanduser().resolve()
    try:
        runtime = load_runtime_config(str(resolved))
    except Exception as error:  # noqa: BLE001 - caller owns response policy
        raise AgentRuntimeConfigError(error) from error

    tools = None
    try:
        if mcp_current_state_registry is None:
            tools = create_default_tool_registry(str(resolved), runtime=runtime)
        else:
            tools = create_default_tool_registry(
                str(resolved),
                runtime=runtime,
                mcp_current_state_registry=mcp_current_state_registry,
            )
        permissions = PermissionManager(str(resolved), prompt=None)
        memory_manager = MemoryManager(project_root=resolved)
        model = create_model_adapter(
            model=runtime.get("model", ""),
            tools=tools,
            runtime=runtime,
        )
        register_tool_capabilities(tools)
        intent = parse_intent(prompt)
        skill_routing = SkillRouter().route(
            tools.get_skills(), intent, get_registry()
        )
        system_prompt = build_system_prompt(
            str(resolved),
            permissions.get_summary(),
            {
                "skills": skill_routing.selected_skill_dicts(),
                "skill_routing": skill_routing.to_dict(),
                "mcpServers": tools.get_mcp_servers(),
                "memory_context": "",
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
    "create_agent_turn_runtime",
    "prepare_conversation_messages",
]
