from __future__ import annotations

from pathlib import Path

from minicode.prompt_pipeline import PromptPipeline, read_file_cached


def _maybe_read(path: Path) -> str | None:
    """Read file content with caching (reuses pipeline cache)."""
    return read_file_cached(path)


def _engineering_governance_rules() -> str:
    r"""Return engineering governance rules as system prompt section.

    These rules are mandatory and apply to all code generation activities.
    Based on: D:\Desktop\engineering-governance
    """
    return """## Engineering Governance Rules (MANDATORY)

These rules apply to ALL code you write. No exceptions.

### Iron Laws
1. **Theory first**: Read theory before any engineering activity
2. **Requirements first**: No code without design, no design without requirements
3. **1:1 binding**: Requirements and knowledge always appear in pairs
4. **Design-driven**: Code implements design, not independent creation
5. **Audit loop**: Execute audit after each phase, fail → fix → re-audit
6. **Single sink**: business/src/ must have exactly ONE sink file
7. **One-way dependencies**: All dependency flow is unidirectional, zero cycles
8. **No skipping**: Each phase's exit signals must be met before next phase

### Package Structure (Six Areas)
Every package must have:
- `port/port_entry/` — Entry points (can import anything)
- `wrap/src/` — External library adapters (import: port_entry, wrap/config, wrap/src)
- `business/src/` — Business logic (import: wrap sinks, business/config, business/src)
- `test/src/` — Tests (import: business/src, test/config, test/src)
- `business/config/` — Business config (zero dependencies)
- `wrap/config/` — Adapter config (zero dependencies)
- `test/config/` — Test config (zero dependencies)

### Dependency Direction Rules
- `business/src/` → `wrap/src/` sinks → `port/port_entry/` → `vendor/`
- `business/src/` CANNOT import vendor/, external libs directly
- `wrap/src/` CANNOT import business/src/
- Config imports always come LAST in import statements
- Cross-package: port_exit → port_entry (same language to same language)

### Sink Rule
- `business/src/`: EXACTLY ONE sink (file not imported by other business/src/ files)
- `wrap/src/`: Can have multiple sinks (each must be used by business/src/)
- `test/src/`: Can have multiple sinks (all must be used by port_exit)
- Multiple sinks in business/src/ = MUST split package

### Documentation System
- Requirements → Knowledge → Design → Code (strict one-way flow)
- Each requirement scenario has exactly one matching knowledge file (1:1 path mirror)
- Each design file cites: satisfied requirements, depended knowledge
- Code file paths must be isomorphic to design file paths

### Import Sorting Example
```python
# Non-config imports first
from package.wrap/src/adapter import Adapter
from package.business/src/service import Service

# Config imports LAST
from package.business/config import settings
```

### Audit Checklist (Execute After Code Changes)
Audit 0: Knowledge ↔ Requirements 1:1
Audit 1: Design ← Requirements + Knowledge coverage
Audit 2: Code ← Design isomorphism + Dependency compliance
Audit 3: business/src/ single sink + Package DAG

### Boundary Packaging (Legacy Code)
- When introducing legacy code: only through port_entry → wrap/src/ ([LEGACY] tag)
- Each [LEGACY] file must have expected cleanup date
- Legacy code can reference governance area via port_exit directly

### Repository Rules
- ZERO compositional dependencies between repositories
- Cross-repository needs: copy to local vendor/
- Vendor only imported by port_entry/"""


def build_system_prompt(
    cwd: str,
    permission_summary: list[str] | None = None,
    extras: dict | None = None,
) -> str:
    """Build the system prompt using dynamic paragraph assembly.

    Implements cache boundaries:
    - Static prefix (role, governance rules) is cacheable across turns.
    - Dynamic suffix (skills, MCP, CLAUDE.md) is re-evaluated per turn.

    Args:
        cwd: Current working directory
        permission_summary: Permission context for the prompt
        extras: Optional extras dict with skills, mcpServers, etc.
    """
    cwd_path = Path(cwd)
    permission_summary = permission_summary or []
    extras = extras or {}

    pipeline = PromptPipeline()

    # --- Static Prefix (Cacheable) ---
    pipeline.register_static(
        "role",
        "You are mini-code, a terminal coding assistant.\n"
        "Default behavior: inspect the repository, use tools, make code changes when appropriate, and explain results clearly.\n"
        "Prefer reading files, searching code, editing files, and running verification commands over giving purely theoretical advice.\n"
        f"Current cwd: {cwd}\n"
        "You can inspect or modify paths outside the current cwd when the user asks, but tool permissions may pause for approval first.\n"
        "When making code changes, keep them minimal, practical, and working-oriented.\n"
        "If the user clearly asked you to build, modify, optimize, or generate something, do the work instead of stopping at a plan.\n"
        "If you need user clarification, call the ask_user tool with one concise question and wait for the user reply. Do not ask clarifying questions as plain assistant text.\n"
        "Do not choose subjective preferences such as colors, visual style, copy tone, or naming unless the user explicitly told you to decide yourself.\n"
        "When using read_file, pay attention to the header fields. If it says TRUNCATED: yes, continue reading with a larger offset before concluding that the file itself is cut off.\n"
        "If the user names a skill or clearly asks for a workflow that matches a listed skill, call load_skill before following it.\n"
        "Skill authoring policy: when the user asks to create, add, install, generate, or write a NEW Skill or SKILL.md, call propose_skill first if that tool is available.\n"
        "Do not call write_file for a new Skill until propose_skill has returned a target_path/frontmatter proposal and the user has confirmed that proposal.\n"
        "If propose_skill is unavailable, explain that automatic Skill directory routing is unavailable before writing any Skill files.\n"
        "\n"
        "## Sub-agent (task tool) usage guide\n"
        "You have access to the 'task' tool which can spawn sub-agents for complex work. Use it when:\n"
        "- You need to explore a large codebase without bloating the main context (agent_type='explore')\n"
        "- You need thorough analysis of a codebase area before acting (agent_type='plan')\n"
        "- You need to do multi-step work that benefits from isolation (agent_type='general')\n"
        "Do NOT use the task tool for simple lookups — use read_file/grep_files directly.\n"
        "Do NOT use the task tool just to avoid work — use it when it genuinely improves efficiency.\n"
        "\n"
        "Structured response protocol:\n"
        "- When you are still working and will continue with more tool calls, start your text with <progress>.\n"
        "- Only when the task is actually complete and you are ready to hand control back, start your text with <final>.\n"
        "- Use ask_user when clarification is required; that tool ends the turn and waits for user input.\n"
        "- Do not stop after a progress update. After a <progress> message, continue the task in the next step.\n"
        "- Plain assistant text without <progress> is treated as a completed assistant message for this turn.",
    )

    pipeline.register_static(
        "governance",
        _engineering_governance_rules(),
    )

    # --- Dynamic Suffix (Per-turn) ---
    # Permission context
    if permission_summary:
        perm_text = "Permission context:\n" + "\n".join(permission_summary)
        pipeline.register_dynamic("permissions", lambda: perm_text)

    # Skills section with conditional injection
    skills = extras.get("skills", [])
    if skills:
        skill_routing = extras.get("skill_routing")
        def _build_skills():
            used_fallback = False
            selected_directories = []
            if isinstance(skill_routing, dict):
                used_fallback = bool(skill_routing.get("used_fallback", False))
                selected_directories = skill_routing.get("selected_directories", [])
            elif skill_routing is not None:
                used_fallback = bool(getattr(skill_routing, "used_fallback", False))
                selected_directories = getattr(skill_routing, "selected_directories", [])

            if skill_routing is not None and not used_fallback:
                if isinstance(skill_routing, dict):
                    intent_type = skill_routing.get("intent_type", "unknown")
                    action_type = skill_routing.get("action_type", "unknown")
                    domains = skill_routing.get("capability_domains", [])
                    scopes = skill_routing.get("capability_scopes", [])
                else:
                    intent_type = getattr(skill_routing, "intent_type", "unknown")
                    action_type = getattr(skill_routing, "action_type", "unknown")
                    domains = getattr(skill_routing, "capability_domains", [])
                    scopes = getattr(skill_routing, "capability_scopes", [])
                lines = [f"Routed skills for intent: {intent_type}/{action_type}"]
                if domains:
                    lines.append("Capability domains: " + ", ".join(str(domain) for domain in domains))
                if scopes:
                    lines.append("Capability scopes: " + ", ".join(str(scope) for scope in scopes))
                if selected_directories:
                    lines.extend(["", "Routed skill directories:"])
                    for directory in selected_directories:
                        if isinstance(directory, dict):
                            dir_name = directory.get("name", "")
                            dir_description = directory.get("description", "")
                        else:
                            dir_name = getattr(directory, "name", "")
                            dir_description = getattr(directory, "description", "")
                        lines.append(f"- {dir_name}: {dir_description}")
                lines.extend(["", "Routed skills:"])
            else:
                lines = ["Available skills:"]
            for skill in skills:
                skill_name = skill.get("qualified_name") or skill.get("name", "")
                skill_description = skill.get("description", "")
                tools = skill.get("tools") or []
                if skill_routing is not None and not used_fallback:
                    lines.append(f"- {skill_name}: {skill_description}")
                    if tools:
                        lines.append("  likely tools: " + ", ".join(str(tool) for tool in tools))
                else:
                    lines.append(f"- {skill_name}: {skill_description}")
            if skill_routing is not None and not used_fallback:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- These are the top routed skills for the current task.",
                    "- If a routed skill matches your workflow, call load_skill with its qualified name before following it.",
                    "- If none of the routed skills applies, proceed with normal tools and reasoning.",
                ])
            else:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- If a listed skill matches the task, call load_skill with its name before following it.",
                    "- If none applies, proceed with normal tools and reasoning.",
                ])
            return "\n".join(lines)

        pipeline.register_dynamic("skills", _build_skills)
    else:
        pipeline.register_dynamic(
            "no_skills",
            lambda: (
                "Available skills:\n- none discovered\n"
                "Tip: Install skills via `npx superpowers-zh` in your project directory"
            ),
        )

    # MCP servers section
    mcp_servers = extras.get("mcpServers", [])
    if mcp_servers:
        def _build_mcp():
            lines = ["Configured MCP servers:"]
            lines.extend(
                "- "
                + server["name"]
                + f": {server['status']}, tools={server['toolCount']}"
                + (f", resources={server['resourceCount']}" if server.get("resourceCount") is not None else "")
                + (f", prompts={server['promptCount']}" if server.get("promptCount") is not None else "")
                + (f", protocol={server['protocol']}" if server.get("protocol") else "")
                + (f" ({server['error']})" if server.get("error") else "")
                for server in mcp_servers
            )
            if any(server.get("status") == "connected" for server in mcp_servers):
                lines.append(
                    "Connected MCP tools are already exposed in the tool list with names prefixed like mcp__server__tool. "
                    "Use list_mcp_resources/read_mcp_resource and list_mcp_prompts/get_mcp_prompt when a server exposes those capabilities."
                )
            # Sequential thinking server detection
            sequential_servers = [
                server for server in mcp_servers
                if "sequential" in server.get("name", "").lower()
                or "branch-thinking" in server.get("name", "").lower()
                or "think" in server.get("name", "").lower()
            ]
            if any(server.get("status") == "connected" for server in sequential_servers):
                lines.extend([
                    "",
                    "SEQUENTIAL THINKING MCP SERVER IS CONNECTED!",
                    "When to use sequential_thinking tool:",
                    "- Breaking down complex implementation problems",
                    "- Multi-step debugging or investigation",
                    "- Architectural decisions requiring structured analysis",
                    "- Migration or refactoring planning",
                    "- Any situation requiring step-by-step reasoning",
                    "",
                    "Usage: Call 'sequential_thinking' with structured thoughts before complex tool sequences",
                ])
            return "\n".join(lines)

        pipeline.register_dynamic("mcp", _build_mcp, cache_ttl=60.0)

    memory_context = str(extras.get("memory_context") or "").strip()
    if memory_context:
        pipeline.register_dynamic(
            "memory",
            lambda: (
                "## Project Memory & Context\n\n"
                "The following information has been accumulated from previous sessions. "
                "Use it to preserve project conventions and decisions:\n\n"
                f"{memory_context}"
            ),
            cache_ttl=30.0,
        )

    # Global CLAUDE.md (file-cached)
    global_claude_md = _maybe_read(Path.home() / ".claude" / "CLAUDE.md")
    if global_claude_md:
        pipeline.register_dynamic(
            "global_claude_md",
            lambda: f"Global instructions from ~/.claude/CLAUDE.md:\n{global_claude_md}",
            cache_ttl=600.0,
        )

    # Project CLAUDE.md (file-cached)
    project_claude_md = _maybe_read(cwd_path / "CLAUDE.md")
    if project_claude_md:
        pipeline.register_dynamic(
            "project_claude_md",
            lambda: f"Project instructions from {cwd_path / 'CLAUDE.md'}:\n{project_claude_md}",
            cache_ttl=300.0,
        )

    return pipeline.build()
