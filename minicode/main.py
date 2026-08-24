from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.cli_commands import try_handle_local_command
from minicode.config import load_runtime_config
from minicode.history import load_history_entries, save_history_entries
from minicode.local_tool_shortcuts import parse_local_tool_shortcut
from minicode.manage_cli import maybe_handle_management_command
from minicode.model_registry import create_model_adapter
from minicode.permissions import PermissionManager
from minicode.prompt import build_system_prompt
from minicode.run_events import emit_skill_routing_safely
from minicode.run_lifecycle import observe_run
from minicode.skill_router import required_skill_names_for_routing
from minicode.tools import create_default_tool_registry
from minicode.tooling import ToolContext
from minicode.tui.transcript import format_transcript_text
from minicode.tui.types import TranscriptEntry
from minicode.tty_app import run_tty_app
from minicode.workspace import resolve_tool_path


def _handle_local_command(user_input: str, tools) -> str | None:
    if user_input == "/tools":
        return "\n".join(f"{tool.name}: {tool.description}" for tool in tools.list())
    local_result = try_handle_local_command(user_input, tools=tools, cwd=str(Path.cwd()))
    return local_result


def _render_banner(runtime: dict | None, cwd: str, permission_summary: list[str], counts: dict[str, int]) -> str:
    model = runtime["model"] if runtime else "unconfigured"
    project = Path(cwd).name or cwd
    lines = [
        f"CodeLoop · {project} · {model}",
        (
            f"workspace: {cwd}\n"
            f"skills: {counts['skillCount']} · mcp: {counts['mcpCount']}"
        ),
    ]
    if permission_summary:
        lines.append(f"access: {permission_summary[0]}")
    return "\n".join(lines)


def _render_quick_start() -> str:
    """Render a concise guide for line-oriented, non-interactive use."""
    return (
        "Try: 帮我分析这个项目的结构\n"
        "Commands: /help · /skills · /status · /exit"
    )


def _render_startup_prelude(
    runtime: dict | None,
    cwd: str,
    permission_summary: list[str],
    counts: dict[str, int],
    *,
    interactive: bool,
) -> str:
    """Return startup text only for the line-oriented fallback interface."""
    if interactive:
        return ""
    parts = [_render_banner(runtime, cwd, permission_summary, counts)]
    if os.environ.get("MINI_CODE_SHOW_GUIDE", "1") == "1":
        parts.append(_render_quick_start())
    return "\n\n".join(parts)


def _setup_cli_logging(level: str, *, interactive: bool):
    """Keep diagnostic logs out of the alternate-screen TUI."""
    from minicode.logging_config import setup_logging

    return setup_logging(level=level, log_to_console=not interactive)


def _append_transcript(transcript: list[TranscriptEntry], **kwargs) -> None:
    transcript.append(TranscriptEntry(id=len(transcript) + 1, **kwargs))


def _make_cli_permission_prompt():
    """Create a simple CLI-based permission prompt for non-TTY fallback."""
    def _prompt(request: dict) -> dict:
        print(f"\n{request.get('summary', 'Permission Request')}")
        choices = request.get("choices", [])
        if choices:
            for choice in choices:
                print(f"  [{choice.get('key', '')}] {choice.get('label', '')}")
            answer = input("Choose: ").strip()
            for choice in choices:
                if answer == choice.get("key"):
                    return {"decision": choice.get("decision", "allow_once")}
        answer = input("Allow? (y/n): ").strip().lower()
        return {"decision": "allow_once" if answer in ("y", "yes") else "deny_once"}
    return _prompt


def _configure_stdio_for_unicode() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _save_transcript_file(cwd: str, permissions, transcript: list[TranscriptEntry], output_path: str) -> str:
    target = resolve_tool_path(ToolContext(cwd=cwd, permissions=permissions), output_path, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_transcript_text(transcript), encoding="utf-8")
    return str(target)


def _route_skills_for_prompt(
    cwd: str,
    tools,
    user_input: str | None,
) -> tuple[list[dict], object | None]:
    if not user_input:
        return tools.get_skills(), None
    from minicode.capability_registry import get_registry, register_tool_capabilities
    from minicode.intent_parser import parse_intent
    from minicode.skill_router import build_skill_router

    register_tool_capabilities(tools)
    intent = parse_intent(user_input)
    routing = build_skill_router(cwd).route(
        tools.get_skills(), intent, get_registry()
    )
    # Abstention means "no routing evidence", not "no skills" — the prompt
    # must still see the full inventory or the model believes none exist.
    if getattr(routing, "used_fallback", False):
        return tools.get_skills(), routing
    return routing.selected_skill_dicts(), routing


def main() -> None:
    _configure_stdio_for_unicode()

    parser = argparse.ArgumentParser(
        description="CodeLoop - A lightweight terminal coding assistant",
        add_help=True,
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        default=None,
        metavar="SESSION_ID",
        help="Resume a previous session (use 'latest' or session ID)",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List all saved sessions and exit",
    )
    parser.add_argument(
        "--session",
        default=None,
        metavar="SESSION_ID",
        help="Start with a specific session ID",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Run the interactive installer",
    )
    parser.add_argument(
        "--validate-config",
        "--valid-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: WARNING)",
    )

    args, remaining_argv = parser.parse_known_args()
    if remaining_argv and not any(not arg.startswith("--") for arg in remaining_argv):
        parser.error(f"unrecognized arguments: {' '.join(remaining_argv)}")

    interactive = sys.stdin.isatty()

    # The full-screen TUI owns stderr as part of the terminal surface. Keep
    # warnings in minicode.log so background health diagnostics cannot corrupt
    # a permission prompt or make the application appear stuck.
    _setup_cli_logging(args.log_level, interactive=interactive)

    # Run config validation if requested
    if args.validate_config:
        from minicode.config import format_config_diagnostic
        print(format_config_diagnostic())
        return
    
    # Run installer if requested
    if args.install:
        from minicode.install import main as install_main
        install_main()
        return
    
    cwd = str(Path.cwd())
    argv = remaining_argv
    
    # ``argparse`` already consumed top-level options. Preserve every
    # remaining management option together with its value; dropping only the
    # ``--name`` token turns ``--option value`` into a stray positional value.
    management_argv = list(argv)
    if maybe_handle_management_command(cwd, management_argv):
        return

    runtime = None
    try:
        runtime = load_runtime_config(cwd)
    except Exception as e:  # noqa: BLE001
        runtime = None
        print(
            f"❌ Failed to load runtime config: {e}\n",
            file=sys.stderr,
        )
        print(
            "🔧 How to fix this:\n"
            "  1. Edit ~/.mini-code/.env\n"
            "  2. Set MINI_CODE_MODEL, MINI_CODE_PROVIDER, and the matching API key\n"
            "  3. Ensure ~/.mini-code is 0700 and ~/.mini-code/.env is 0600\n"
            "  4. Restart CodeLoop\n\n"
            "📖 For more info: https://github.com/zrb3052796119/CodeLoop\n",
            file=sys.stderr,
        )
        if os.environ.get("MINI_CODE_MODEL_MODE", "").strip().lower() != "mock":
            raise SystemExit(2) from e

    prompt_handler = _make_cli_permission_prompt() if sys.stdin.isatty() else None
    tools = create_default_tool_registry(cwd, runtime=runtime)
    permissions = PermissionManager(cwd, prompt=prompt_handler)
    
    # Use unified model registry for adapter creation
    force_mock = runtime is None
    model = create_model_adapter(
        model=runtime.get("model", "") if runtime else "",
        tools=tools,
        runtime=runtime,
        force_mock=force_mock,
    )
    
    # Initialize ContextManager for context window management
    from minicode.context_manager import ContextManager
    from minicode.logging_config import get_logger
    logger = get_logger("main")
    context_mgr = None
    if runtime:
        context_mgr = ContextManager(model=runtime.get("model", "default"))
        logger.info("Context manager initialized for model: %s", runtime.get("model", "unknown"))
    
    # Initialize MemoryManager for cross-session knowledge retention
    from minicode.memory import MemoryManager
    memory_mgr = MemoryManager(project_root=Path(cwd))
    logger.info("Memory manager initialized")
    
    # Initialize UserProfileManager for user preferences
    from minicode.user_profile import UserProfileManager
    profile_manager = UserProfileManager(cwd=cwd)
    profile_manager.load_merged()
    logger.info("User profile manager initialized (global=%s, project=%s)",
                profile_manager.global_path.exists(),
                profile_manager.project_path.exists())
    
    # Initialize Store for global state management (inspired by Claude Code's Zustand store)
    from minicode.state import create_app_store
    app_store = create_app_store(
        initial={
            "session_id": args.session or "new",
            "workspace": cwd,
            "model": runtime.get("model", "mock") if runtime else "mock",
        }
    )
    logger.info("Store initialized with session: %s", app_store.get_state().session_id)
    
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                cwd,
                permissions.get_summary(),
                {
                    "skills": tools.get_skills(),
                    "mcpServers": tools.get_mcp_servers(),
                    "memory_context": "",
                },
            ),
        }
    ]
    history = load_history_entries()
    transcript: list[TranscriptEntry] = []

    startup_prelude = _render_startup_prelude(
        runtime,
        cwd,
        permissions.get_summary(),
        {
            "transcriptCount": 0,
            "messageCount": len(messages),
            "skillCount": len(tools.get_skills()),
            "mcpCount": len(tools.get_mcp_servers()),
        },
        interactive=interactive,
    )
    if startup_prelude:
        print(startup_prelude)

    try:
        if not interactive:
            for raw_input in sys.stdin:
                user_input = raw_input.strip()
                if not user_input:
                    continue
                if user_input == "/exit":
                    break
                if user_input.startswith("/transcript-save "):
                    output_path = user_input[len("/transcript-save ") :].strip()
                    if not output_path:
                        print("Usage: /transcript-save <path>")
                        continue
                    saved_path = _save_transcript_file(cwd, permissions, transcript, output_path)
                    print(f"Saved transcript to {saved_path}")
                    continue
                memory_result = memory_mgr.handle_user_memory_input(user_input)
                if memory_result is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    _append_transcript(transcript, kind="assistant", body=memory_result)
                    print(memory_result)
                    continue
                local_result = _handle_local_command(user_input, tools)
                if local_result is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    _append_transcript(transcript, kind="assistant", body=local_result)
                    print(local_result)
                    continue
                shortcut = parse_local_tool_shortcut(user_input)
                if shortcut is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    result = tools.execute(
                        shortcut["toolName"],
                        shortcut["input"],
                        context=ToolContext(cwd=cwd, permissions=permissions),
                    )
                    _append_transcript(
                        transcript,
                        kind="tool",
                        body=result.output,
                        toolName=shortcut["toolName"],
                        status="success" if result.ok else "error",
                    )
                    print(result.output)
                    continue
                _append_transcript(transcript, kind="user", body=user_input)
                messages.append({"role": "user", "content": user_input})
                history.append(user_input)
                save_history_entries(history)
                routed_skills, skill_routing = _route_skills_for_prompt(
                    cwd, tools, user_input
                )
                messages[0] = {
                    "role": "system",
                    "content": build_system_prompt(
                        cwd,
                        permissions.get_summary(),
                        {
                            "skills": routed_skills,
                            "skill_routing": skill_routing,
                            "mcpServers": tools.get_mcp_servers(),
                            "memory_context": "",
                        },
                    ),
                }
                permissions.begin_turn()
                try:
                    with observe_run(
                        workspace=cwd,
                        source="tui",
                        title=user_input,
                        session_id=None,
                    ) as observation:
                        emit_skill_routing_safely(observation, skill_routing)
                        messages = run_agent_turn(
                            model=model,
                            tools=tools,
                            messages=messages,
                            cwd=cwd,
                            permissions=permissions,
                            on_tool_start=lambda tool_name, _tool_input: (
                                observation.tool_started(tool_name)
                            ),
                            on_tool_result=lambda tool_name, _output, is_error: (
                                observation.tool_finished(
                                    tool_name, is_error=is_error
                                )
                            ),
                            store=app_store,
                            context_manager=context_mgr,
                            runtime=runtime,
                            memory_manager=memory_mgr,
                            event_sink=observation,
                            required_skill_names=required_skill_names_for_routing(
                                skill_routing
                            ),
                        )
                        returned_assistant = next(
                            (
                                message
                                for message in reversed(messages)
                                if message.get("role") == "assistant"
                            ),
                            None,
                        )
                        returned_content = (
                            returned_assistant.get("content")
                            if isinstance(returned_assistant, dict)
                            else None
                        )
                        observation.assistant_completed(
                            content_present=(
                                isinstance(returned_content, str)
                                and bool(returned_content)
                            ),
                            content_length=(
                                len(returned_content)
                                if isinstance(returned_content, str)
                                else 0
                            ),
                        )
                finally:
                    permissions.end_turn()
                
                # Log context usage after turn
                if context_mgr:
                    stats = context_mgr.get_stats()
                    logger.debug("After turn: %d tokens (%.0f%%)", stats.total_tokens, stats.usage_percentage)
                last_assistant = next((message for message in reversed(messages) if message["role"] == "assistant"), None)
                if last_assistant:
                    _append_transcript(transcript, kind="assistant", body=last_assistant["content"])
                    print(last_assistant["content"])
            return

        run_tty_app(
            runtime=runtime,
            tools=tools,
            model=model,
            messages=messages,
            cwd=cwd,
            permissions=permissions,
            resume_session=args.resume,
            list_sessions_only=args.list_sessions,
            memory_manager=memory_mgr,
            context_manager=context_mgr,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Shutting down gracefully...")
    finally:
        # Graceful shutdown: clean up all resources
        from minicode.logging_config import get_logger
        logger = get_logger("main")
        logger.info("Shutting down...")
        
        # Dispose tools (closes MCP connections)
        try:
            tools.dispose()
            logger.info("Tools disposed successfully")
        except Exception as e:
            logger.warning("Error disposing tools: %s", e)
        
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
