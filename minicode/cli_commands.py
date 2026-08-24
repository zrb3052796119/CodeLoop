from __future__ import annotations

from dataclasses import dataclass

from minicode.config import (
    CLAUDE_SETTINGS_PATH,
    MINI_CODE_ENV_PATH,
    MINI_CODE_MCP_PATH,
    MINI_CODE_PERMISSIONS_PATH,
    MINI_CODE_SETTINGS_PATH,
    load_runtime_config,
    safe_runtime_summary,
    USER_MODEL_ENV_KEYS,
)
from minicode.env_file import update_private_env_file


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    usage: str
    description: str


SLASH_COMMANDS = [
    SlashCommand("/help", "/help", "Show available slash commands."),
    SlashCommand("/tools", "/tools", "List tools available to the coding agent and tool shortcuts."),
    SlashCommand("/state", "/state", "Show detailed application state and Store summary."),
    SlashCommand("/status", "/status", "Show application state summary and current model."),
    SlashCommand("/cost", "/cost [--detailed]", "Show API cost and usage report."),
    SlashCommand("/context", "/context", "Show context window usage."),
    SlashCommand("/cybernetics", "/cybernetics", "Show cybernetic control system status."),
    SlashCommand("/tasks", "/tasks", "Show current task list."),
    SlashCommand("/memory", "/memory", "Show memory system status."),
    SlashCommand("/config", "/config", "Show configuration diagnostics and validation."),
    SlashCommand("/history", "/history", "Show recent prompt history from ~/.mini-code/history.json."),
    SlashCommand("/clear", "/clear", "Clear the current transcript view."),
    SlashCommand("/retry", "/retry", "Retry the last natural-language prompt in this session."),
    SlashCommand("/transcript-save", "/transcript-save <path>", "Save the current session transcript to a text file."),
    SlashCommand("/model", "/model", "Show the current model."),
    SlashCommand("/model", "/model <model-name>", "Persist a model override into ~/.mini-code/.env."),
    SlashCommand("/config-paths", "/config-paths", "Show primary-model and compatibility config paths."),
    SlashCommand("/skills", "/skills", "List discovered SKILL.md workflows."),
    SlashCommand("/mcp", "/mcp", "Show configured MCP servers and connection state."),
    SlashCommand("/permissions", "/permissions", "Show mini-code permission storage path."),
    SlashCommand("/exit", "/exit", "Exit mini-code."),
    SlashCommand("/debug", "/debug", "Show scroll and terminal diagnostics."),
    SlashCommand("/user", "/user", "Show or manage user profile (preferences, coding style)."),
    SlashCommand("/ls", "/ls [path]", "List files in a directory."),
    SlashCommand("/grep", "/grep <pattern>::[path]", "Search text in files."),
    SlashCommand("/read", "/read <path>", "Read a file directly."),
    SlashCommand("/write", "/write <path>::<content>", "Write a file directly."),
    SlashCommand("/modify", "/modify <path>::<content>", "Replace a file, showing a reviewable diff before applying it."),
    SlashCommand("/edit", "/edit <path>::<search>::<replace>", "Edit a file by exact replacement."),
    SlashCommand("/patch", "/patch <path>::<search1>::<replace1>::<search2>::<replace2>...", "Apply multiple replacements to one file in one command."),
    SlashCommand("/cmd", "/cmd [cwd::]<command> [args...]", "Run an allowed development command directly."),
]


def format_slash_commands() -> str:
    return "\n".join(
        [
            "CodeLoop commands",
            "Project  /status /context /memory /tasks",
            "Inspect  /ls /grep /read /tools",
            "Edit     /write /edit /modify; /patch multiple",
            "Run      /cmd /retry",
            "Agent    /skills /mcp /permissions /user",
            "Session  /history /clear /exit",
            "Model    /model /cost /config",
            "More     /cybernetics /config-paths /transcript-save",
            "Type / to browse descriptions; Tab completes.",
        ]
    )


def find_matching_slash_commands(user_input: str) -> list[str]:
    """Find slash commands matching user input.

    Tries exact prefix first, falls back to fuzzy subsequence matching.
    """
    commands = [c.usage for c in SLASH_COMMANDS]
    prefix_matches = [c for c in commands if c.startswith(user_input)]
    if prefix_matches:
        return prefix_matches
    # Fuzzy fallback: subsequence match (e.g., "mem" matches "/memory")
    lower = user_input.lower()
    fuzzy = [c for c in commands if all(ch in c.lower() for ch in lower)]
    return fuzzy if fuzzy else commands


def complete_slash_command(line: str) -> tuple[list[str], str]:
    commands = [c.usage for c in SLASH_COMMANDS]
    hits = [c for c in commands if c.startswith(line)]
    if not hits and line:
        lower = line.lower()
        hits = [c for c in commands if all(ch in c.lower() for ch in lower)]
    return (hits if hits else commands, line)


def try_handle_local_command(user_input: str, tools=None, cwd: str | None = None) -> str | None:
    if user_input in {"/", "/help"}:
        return format_slash_commands()

    if user_input == "/config-paths":
        return "\n".join(
            [
                f"primary model env: {MINI_CODE_ENV_PATH}",
                f"mini-code settings fallback: {MINI_CODE_SETTINGS_PATH}",
                f"mini-code permissions: {MINI_CODE_PERMISSIONS_PATH}",
                f"mini-code mcp: {MINI_CODE_MCP_PATH}",
                f"claude settings fallback: {CLAUDE_SETTINGS_PATH}",
            ]
        )

    if user_input == "/permissions":
        return f"permission store: {MINI_CODE_PERMISSIONS_PATH}"

    if user_input == "/skills":
        skills = tools.get_skills() if tools else []
        if not skills:
            return "No skills discovered. Add skills under ~/.mini-code/skills/<name>/SKILL.md, .mini-code/skills/<name>/SKILL.md, .claude/skills/<name>/SKILL.md, or ~/.claude/skills/<name>/SKILL.md."
        return "\n".join(
            f"{skill['name']}  {skill['description']}  [{skill['source']}]"
            for skill in skills
        )

    if user_input == "/config":
        from minicode.config import format_config_diagnostic
        return format_config_diagnostic()

    if user_input == "/state":
        try:
            from minicode.state import handle_state_command
            return handle_state_command()
        except ImportError:
            return "State system not available. Please ensure state.py exists."

    if user_input == "/memory":
        # Memory system display
        try:
            from minicode.memory import MemoryManager
            from pathlib import Path
            memory_mgr = MemoryManager(project_root=Path(cwd) if cwd else Path.cwd())
            return memory_mgr.format_stats()
        except Exception as e:
            return f"Error loading memory: {e}"

    if user_input == "/context":
        # Context usage display
        try:
            from minicode.context_manager import load_context_state
            ctx_mgr = load_context_state()
            if ctx_mgr:
                return ctx_mgr.format_context_details()
            else:
                return "No context state available. Context tracking starts after first turn."
        except Exception as e:
            return f"Error loading context: {e}"

    if user_input == "/cybernetics":
        return format_cybernetics_status()

    if user_input == "/mcp":
        servers = tools.get_mcp_servers() if tools else []
        if not servers:
            return "No MCP servers configured. Add mcpServers to ~/.mini-code/settings.json, ~/.mini-code/mcp.json, or project .mcp.json."
        lines = []
        for server in servers:
            suffix = f"  error={server['error']}" if server.get("error") else ""
            protocol = f"  protocol={server['protocol']}" if server.get("protocol") else ""
            resources = f"  resources={server['resourceCount']}" if server.get("resourceCount") is not None else ""
            prompts = f"  prompts={server['promptCount']}" if server.get("promptCount") is not None else ""
            lines.append(
                f"{server['name']}  status={server['status']}  tools={server['toolCount']}{resources}{prompts}{protocol}{suffix}"
            )
        return "\n".join(lines)

    if user_input == "/status":
        try:
            runtime = load_runtime_config()
        except Exception as error:  # noqa: BLE001
            return f"runtime not configured: {error}"
        from minicode.model_registry import build_provider_config, detect_provider
        provider = detect_provider(runtime["model"], runtime)
        provider_config = build_provider_config(runtime["model"], runtime)
        safe_summary = safe_runtime_summary(runtime)
        return "\n".join(
            [
                f"model: {runtime['model']}",
                f"provider: {provider.value}",
                f"baseUrl: {provider_config.base_url}",
                f"auth: {'configured' if provider_config.api_key else 'none'}",
                (
                    "sub-agent auth: configured"
                    if safe_summary["credentials"]["subagentConfigured"]
                    else "sub-agent auth: none"
                ),
                (
                    "turn budget: "
                    f"{safe_summary['effectiveTurnBudget']['maxTokens']} tokens · "
                    f"{safe_summary['effectiveTurnBudget']['maxModelCalls']} calls · "
                    f"${safe_summary['effectiveTurnBudget']['maxCostUsd']}"
                ),
                f"mcp servers: {len(runtime.get('mcpServers', {}))}",
                runtime["sourceSummary"],
            ]
        )

    if user_input == "/model":
        try:
            runtime = load_runtime_config()
            from minicode.model_registry import format_model_status
            return format_model_status(runtime["model"], runtime)
        except Exception as error:  # noqa: BLE001
            return f"runtime not configured: {error}"

    if user_input.startswith("/model "):
        arg = user_input[len("/model "):].strip()
        if not arg:
            from minicode.model_registry import format_model_list
            return format_model_list()
        # Subcommands
        if arg in ("status", "info"):
            try:
                runtime = load_runtime_config()
                from minicode.model_registry import format_model_status
                return format_model_status(runtime["model"], runtime)
            except Exception as error:  # noqa: BLE001
                return f"runtime not configured: {error}"
        if arg in ("list", "ls"):
            from minicode.model_registry import format_model_list
            return format_model_list()
        # Provider filter: /model anthropic, /model openrouter, etc.
        from minicode.model_registry import (
            Provider,
            format_model_list,
            infer_model_provider,
        )
        for p in Provider:
            if arg.lower() == p.value:
                return format_model_list(provider=p)
        # Otherwise: set model name
        updates = {"MINI_CODE_MODEL": arg}
        inferred_provider = infer_model_provider(arg)
        if inferred_provider is not None and inferred_provider != Provider.MOCK:
            updates["MINI_CODE_PROVIDER"] = inferred_provider.value
        try:
            update_private_env_file(
                MINI_CODE_ENV_PATH,
                updates,
                allowed_keys=USER_MODEL_ENV_KEYS,
            )
        except (OSError, RuntimeError):
            return "\n".join(
                [
                    "model update failed: could not safely write the global model profile.",
                    f"path: {MINI_CODE_ENV_PATH}",
                    "Check that ~/.mini-code is 0700 and its .env is 0600, then retry.",
                ]
            )
        provider_note = (
            f" provider={inferred_provider.value}"
            if inferred_provider is not None
            else ""
        )
        return (
            f"saved model={arg}{provider_note} to {MINI_CODE_ENV_PATH}\n"
            "Restart MiniCode for the change to take effect."
        )

    if user_input == "/user" or user_input.startswith("/user "):
        from minicode.user_profile import handle_user_command
        args = user_input[len("/user"):].strip()
        return handle_user_command(args)

    return None


def format_cybernetics_status() -> str:
    """Format cybernetic controller inventory and persisted state hints."""
    from minicode.cybernetic_supervisor import CyberneticSupervisor, load_supervisor_report
    from minicode.context_manager import load_context_state

    controllers = [
        ("ContextCyberneticsOrchestrator", "context pressure PID + prediction"),
        ("CostControlLoop", "budget PID for tool-result persistence"),
        ("VerificationController", "risk-adaptive verification planning"),
        ("ToolSchedulerController", "error/latency-aware concurrency control"),
        ("MemoryInjectionController", "context-aware memory injection"),
        ("ModelSelectionController", "cost/latency/failure-aware model routing"),
        ("ProgressController", "health/stall task progress control"),
        ("CyberneticSupervisor", "global health and risk aggregation"),
    ]

    ctx = load_context_state()
    snapshots = []
    if ctx:
        stats = ctx.get_stats()
        usage = stats.usage_percentage / 100.0
        snapshots.append(CyberneticSupervisor().snapshot_from_context({
            "sensor": {"current_usage": usage},
            "predictor": {"urgency": 0.0},
        }))
    persisted_report = load_supervisor_report()
    report = persisted_report or CyberneticSupervisor().report(snapshots)

    lines = [
        "Cybernetic Control System",
        "=" * 50,
        f"overall_health: {report.overall_health:.2f}",
        f"risk_level: {report.risk_level.value}",
        f"source: {'latest agent-loop report' if persisted_report else 'current persisted context'}",
        "",
        "Controllers:",
    ]
    for name, desc in controllers:
        lines.append(f"  - {name}: {desc}")
    lines.extend([
        "",
        "Runtime aggregation:",
        "  - pipeline outputs: progress_control + verification_plan + cybernetic_supervisor",
        "  - agent loop logs: context + cost + tool scheduling supervisor report",
    ])
    if report.recommended_actions:
        lines.append("")
        lines.append("Current actions:")
        for action in report.recommended_actions[:5]:
            lines.append(f"  - {action}")
    return "\n".join(lines)
