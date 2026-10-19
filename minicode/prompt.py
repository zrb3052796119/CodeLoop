from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from minicode.memory_retrieval import MEMORY_USE_POLICY
from minicode.prompt_pipeline import PromptPipeline, read_file_cached


def _directory_card_ref(digests: list[str]) -> str:
    """Short aggregate hash over a directory's child content digests.

    SKILL_DIR.md has no version field; the sorted-digest hash is the stable
    snapshot identifier quoted on directory cards instead.
    """
    payload = "\n".join(sorted(digest for digest in digests if digest))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _hierarchical_skill_lines(skills: list) -> list[str]:
    """Directory cards plus fully-described root-level skills."""
    by_directory: dict[str, dict] = {}
    root_skills: list[dict] = []
    for skill in skills:
        directory = str(skill.get("directory") or "")
        if directory:
            card = by_directory.setdefault(
                directory,
                {
                    "description": str(
                        skill.get("directory_description") or ""
                    ),
                    "count": 0,
                    "digests": [],
                },
            )
            card["count"] += 1
            card["digests"].append(str(skill.get("content_digest") or ""))
        else:
            root_skills.append(skill)
    lines = ["Available skill directories:"]
    for directory in sorted(by_directory):
        card = by_directory[directory]
        description = f": {card['description']}" if card["description"] else ""
        lines.append(
            f"- {directory}{description} "
            f"({card['count']} skills, ref {_directory_card_ref(card['digests'])})"
        )
    if root_skills:
        lines.extend(["", "Undirectoried skills:"])
        for skill in root_skills:
            name = skill.get("qualified_name") or skill.get("name", "")
            lines.append(f"- {name}: {skill.get('description', '')}")
    lines.extend(
        [
            "",
            "SKILL USAGE GUIDE:",
            "- Match skills to the current task stage and the system being worked on. For conditional requests, establish the condition before choosing a later-stage workflow.",
            "- Call browse_skill_directory with one or several directory names to expand their skill descriptions; batched expansion is allowed.",
            "- Compare the descriptions of plausible skills before loading. Directory names are organizational hints; related skills may live elsewhere.",
            "- Call search_skills with a query for cross-directory lexical search over all authorized skills.",
            "- Both tools return descriptions only; call load_skill with the exact qualified name for the full skill body.",
            "- An empty page does not prove a directory has no match: paginate with offset or search instead.",
            "- You may inspect project files to clarify the task, then return to skill discovery. Read an applicable skill before carrying out its specialized workflow.",
            "- Load the smallest sufficient set of skills. Similar names do not imply the same scope; a later task stage can discover and load more skills.",
            "- Choosing zero skills is also valid; proceed with normal tools and reasoning.",
        ]
    )
    return lines


def _record_catalog_prompt_chars(skill_routing, text: str) -> None:
    """Make the rendered catalog size observable on the routing result."""
    if isinstance(skill_routing, dict):
        skill_routing["catalog_prompt_chars"] = len(text)
    elif skill_routing is not None:
        try:
            skill_routing.catalog_prompt_chars = len(text)
        except AttributeError:
            pass


def _intent_hint_lines(skill_routing) -> list[str]:
    """Render the advisory intent-hint block for hierarchical discovery.

    Experiment arm B: everything here is derived from automated intent
    parsing and is explicitly labeled correctable; the original request in
    the conversation stays authoritative. Returns [] when no payload exists
    (flag off, flat mode, or non-dict hints), so output is unchanged.
    """
    if isinstance(skill_routing, dict):
        hints = skill_routing.get("intent_hints")
    elif skill_routing is not None:
        hints = getattr(skill_routing, "intent_hints", None)
    else:
        hints = None
    if not isinstance(hints, dict):
        return []

    lines = [
        "Task clues (advisory - derived from automated intent parsing, "
        "may be wrong; the user's original request is authoritative):",
        "These partial clues may omit task order, conditions, or context. "
        "They are not an execution plan; resolve the current stage from the original request.",
    ]
    stages = hints.get("stages") or []
    if stages:
        lines.append("Recognized operations (partial, not ordered stages):")
        for index, stage in enumerate(stages, 1):
            if isinstance(stage, dict):
                operation = str(stage.get("operation") or "")
                evidence = str(stage.get("evidence") or "")
            else:
                operation = str(stage)
                evidence = ""
            line = f"  {index}. {operation}"
            if evidence:
                line += f' (evidence: "{evidence}")'
            lines.append(line)
    entities = [str(entity) for entity in hints.get("entities") or []]
    if entities:
        lines.append("Entities: " + ", ".join(entities))
    constraints = [str(item) for item in hints.get("constraints") or []]
    if constraints:
        lines.append("Constraints (explicitly ruled out): " + "; ".join(constraints))
    suggested = [str(name) for name in hints.get("suggested_directories") or []]
    if suggested:
        lines.append("Suggested starting directories: " + ", ".join(suggested))
    prefetched_directory = str(hints.get("prefetched_directory") or "")
    prefetched_skills = [
        str(line) for line in hints.get("prefetched_skills") or []
    ]
    prefetched_page = hints.get("prefetched_page")
    if isinstance(prefetched_page, str) and prefetched_page:
        # The producer budgets this complete block, including its heading and
        # pagination marker. Do not add another unbudgeted wrapper here.
        lines.append(prefetched_page)
    elif prefetched_directory and prefetched_skills:
        lines.append(
            f'Prefetched descriptions for directory "{prefetched_directory}" '
            "(partial, first page):"
        )
        lines.extend(prefetched_skills)
        if hints.get("prefetch_truncated"):
            try:
                next_offset = int(hints.get("prefetch_next_offset"))
            except (TypeError, ValueError):
                next_offset = len(prefetched_skills)
            lines.append(
                "TRUNCATED: yes - call browse_skill_directory again "
                f"with offset {next_offset}"
            )
    return lines


def _maybe_read(path: Path) -> str | None:
    """Read file content with caching (reuses pipeline cache)."""
    return read_file_cached(path)


def _current_date_section() -> str:
    """Tell the model what day it is, in the user's local time zone.

    The model otherwise has no clock: it cannot distinguish "now" from its
    training cutoff, so questions about the date, or any reasoning about
    recency, deadlines, or how old a dependency is, silently use a stale
    year.
    """
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    formatted_offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    return (
        "## Current date\n"
        f"Today is {now:%Y-%m-%d} ({now:%A}), {formatted_offset}.\n"
        "Use this instead of assuming a date from your training data. "
        "Your knowledge cutoff is earlier than today, so treat anything you "
        "recall about recent events as possibly out of date."
    )


def _engineering_governance_rules() -> str:
    """Return repository-neutral defaults; explicit project rules stay in force."""
    return """## Engineering practice

- Follow explicit user and project instructions, the existing architecture, and local code/test conventions.
- For a bug fix or small maintenance change, inspect the relevant behavior and tests, then make the smallest supported change once the cause and target are clear. Read more only to resolve a specific remaining uncertainty.
- Use a brief design when the change introduces a new capability or materially changes architecture. Separate planning documents or a new directory layout are required only when the user or project calls for them.
- Read-only investigations stay read-only. A request to change code does not grant access beyond the approved paths or bypass tool permissions.
- Run focused verification for the affected behavior. Report actual changes, checks, and remaining failures; if evidence, scope, or permission blocks progress, state the concrete blocker without inventing a fix."""


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
    user_interaction_available = bool(
        extras.get("user_interaction_available", True)
    )
    if user_interaction_available:
        interaction_rule = (
            "If progress is impossible without new information or a decision "
            "from the user, call the ask_user tool with one concise question "
            "and wait for the reply. Never use ask_user to deliver an answer, "
            "result, status update, or confirmation. Do not ask blocking "
            "clarifying questions as plain assistant text."
        )
        structured_interaction_rule = (
            "- Use ask_user only when new user information is required; that "
            "tool ends the turn and waits for user input."
        )
    else:
        interaction_rule = (
            "ask_user is unavailable in this non-interactive execution. Work "
            "from the supplied context and make safe, explicit assumptions "
            "when possible. Return results through the assistant final response."
        )
        structured_interaction_rule = (
            "- User interaction is unavailable; return the result through the "
            "assistant final response and do not attempt to pause for input."
        )

    pipeline = PromptPipeline()

    # --- Static Prefix (Cacheable) ---
    pipeline.register_static(
        "role",
        "You are CodeLoop, a terminal coding assistant.\n"
        "Default behavior: inspect the repository, use tools, make code changes when appropriate, and explain results clearly.\n"
        "Prefer reading files, searching code, editing files, and running verification commands over giving purely theoretical advice.\n"
        f"Current cwd: {cwd}\n"
        "You can inspect or modify paths outside the current cwd when the user asks, but tool permissions may pause for approval first.\n"
        "When making code changes, keep them minimal, practical, and working-oriented.\n"
        "If the user clearly asked you to build, modify, optimize, or generate something, do the work instead of stopping at a plan.\n"
        f"Persistent {MEMORY_USE_POLICY}\n"
        f"{interaction_rule}\n"
        "Do not choose subjective preferences such as colors, visual style, copy tone, or naming unless the user explicitly told you to decide yourself.\n"
        "For read_file, TRUNCATED: yes means the returned window ends before EOF; it does not require reading the whole file. "
        "Use grep_files to locate relevant definitions, callers, or tests, then read_file start_line/end_line to read only the needed windows. "
        "Once the target and expected behavior are clear, make the smallest supported change.\n"
        "If the user names a skill or clearly asks for a workflow that matches a listed skill, call load_skill before following it.\n"
        "Skill authoring policy: when the user asks to create, add, install, generate, or write a NEW Skill or SKILL.md, call propose_skill first if that tool is available.\n"
        "Do not call write_file for a new Skill until propose_skill has returned a target_path/frontmatter proposal and the user has confirmed that proposal.\n"
        "If propose_skill is unavailable, explain that automatic Skill directory routing is unavailable before writing any Skill files.\n"
        "\n"
        "## Sub-agent (task tool) usage guide\n"
        "You have access to the 'task' tool which can spawn sub-agents for complex work. Use it when:\n"
        "- You need to explore a large codebase without bloating the main context (agent_type='explore')\n"
        "- You need an analysis-only answer or must resolve a specific design uncertainty (agent_type='plan'); give it a bounded question, not a request to understand the entire repository\n"
        "- Adaptive delegation when child models are routed: two or more source files to change, or an explicit request for a separate implementer and reviewer, goes to agent_type='workflow' before you edit those files. A single-file or single-function change stays with you. Do not add a plan or explore agent to a change you already know how to make. If a permission decision requires workflow, delegate the remaining work through that route\n"
        "- For delegated changes, call agent_type='workflow' directly with write_paths and verification_commands (the scheduler constructs WorkflowRequest.v1 for you) or an explicit workflow_request. Do not routinely run Plan first: the scheduler selects Analyst when the scope and risk require it, and narrow fully-verified scopes use Executor->Verifier. Verification, approval, review, and commit gates remain scheduler-owned\n"
        "- Keep the workflow prompt within 3800 characters: state the objective, observed failure, constraints, and acceptance outcome; put authorized paths and verification argv in their dedicated fields. Do not paste source files or exploration transcripts. Preserve essential requirements when shortening the handoff\n"
        "- Once the relevant code and expected behavior are clear, make or delegate the smallest supported change. Read more only to resolve a concrete uncertainty; if scope, evidence, or permission blocks the change, report that blocker accurately\n"
        "- agent_type='general' is a deprecated legacy-embedder compatibility route and is rejected by the production Agent Loop\n"
        "- A committed workflow completes that child task; continue any remaining user-requested work, collect owned async results, and summarize without repeating committed changes\n"
        "Do NOT use the task tool for simple lookups — use read_file/grep_files directly.\n"
        "If you did delegate exploration to a sub-agent, do not repeat that search yourself in the main context — wait for its result and build on it.\n"
        "Do NOT use the task tool just to avoid work — use it when it genuinely improves efficiency.\n"
        "Structured workflow roles do not negotiate or exchange free-form notes; they read Assignment-authorized source paths and scheduler-assigned artifacts, and write one exact result artifact. "
        "For structured roles the note tools transport only authorized artifacts; independent explore/plan agents may use them for bounded shared notes.\n"
        "\n"
        "Structured response protocol:\n"
        "- When you are still working and will continue with more tool calls, start your text with <progress>.\n"
        "- Only when the task is actually complete and you are ready to hand control back, start your text with <final>.\n"
        f"{structured_interaction_rule}\n"
        "- Do not stop after a progress update. After a <progress> message, continue the task in the next step.\n"
        "- Plain assistant text without <progress> is treated as a completed assistant message for this turn.",
    )

    pipeline.register_static(
        "coding_verification",
        "## Coding verification scope\n"
        "- Start with tests relevant to the changed behavior and the project's applicable lint/type checks. "
        "Expand verification for a concrete unresolved risk, failure, cross-module impact, or an explicit project/user requirement. "
        "Honor user-requested full-suite runs; focused checks do not replace them.\n"
        "- If verification cannot run because dependencies are missing, inspect the project's development dependencies and configured environment first. "
        "When authorized, make one justified dependency/environment repair and retry the blocked check. "
        "If repair is unavailable or the retry remains blocked, report that check as unverified with the blocker; continue independent work.\n"
        "- Do not repeatedly add --ignore, skip tests, or narrow collection merely to obtain a passing result. "
        "Report any exclusions and their effect on the requested verification scope.\n"
        "- Once a check passes, do not repeat it without new changes, a relevant environment change, or concrete unresolved evidence that requires another run. "
        "Choose verification tools and their order to fit the task.\n"
        "- Before the final answer, summarize completed work, verification evidence (commands, scope, and results), "
        "and anything incomplete or unverified with its reason. Do not claim full verification from partial checks.",
    )

    pipeline.register_static(
        "governance",
        _engineering_governance_rules(),
    )

    # --- Dynamic Suffix (Per-turn) ---
    # Current date. Without this the model has no clock at all and silently
    # falls back to its training cutoff — it will state a wrong year with
    # full confidence rather than admit it cannot know. Registered dynamic
    # (never static) so it stays behind the prompt-cache boundary, and with
    # cache_ttl=0 so a clock section can never serve a stale value.
    pipeline.register_dynamic("current_date", _current_date_section, cache_ttl=0.0)

    delegation_note = str(extras.get("delegation_note") or "").strip()
    if delegation_note:
        pipeline.register_dynamic(
            "delegation",
            lambda: "## This turn's delegation\n" + delegation_note,
            cache_ttl=0.0,
        )

    # Permission context
    if permission_summary:
        perm_text = "Permission context:\n" + "\n".join(permission_summary)
        pipeline.register_dynamic("permissions", lambda: perm_text)

    # Skills section with conditional injection
    skills = extras.get("skills", [])
    skill_routing = extras.get("skill_routing")
    if isinstance(skill_routing, dict):
        routing_prompt_mode = str(skill_routing.get("prompt_mode", ""))
    else:
        routing_prompt_mode = str(
            getattr(skill_routing, "prompt_mode", "") or ""
        )
    if routing_prompt_mode == "suppressed":
        pipeline.register_dynamic(
            "skills_suppressed",
            lambda: (
                "Skill routing is suppressed for this turn by the user's "
                "explicit instruction. Do not load, infer, or suggest a Skill."
            ),
        )
    elif skills:
        def _build_skills():
            used_fallback = False
            selected_directories = []
            outcome = ""
            if isinstance(skill_routing, dict):
                used_fallback = bool(skill_routing.get("used_fallback", False))
                selected_directories = skill_routing.get("selected_directories", [])
                outcome = str(skill_routing.get("turn_outcome", ""))
            elif skill_routing is not None:
                used_fallback = bool(getattr(skill_routing, "used_fallback", False))
                selected_directories = getattr(skill_routing, "selected_directories", [])
                outcome = str(getattr(skill_routing, "turn_outcome", ""))

            if routing_prompt_mode == "hierarchical":
                # Budget escalation of model-first selection: directoried
                # skills collapse into cards, root-level skills keep full
                # descriptions, and bodies/descriptions beyond the cards are
                # discovered via browse_skill_directory/search_skills.
                lines = _hierarchical_skill_lines(skills)
                hint_lines = _intent_hint_lines(skill_routing)
                if hint_lines:
                    lines.extend(["", *hint_lines])
                text = "\n".join(lines)
                _record_catalog_prompt_chars(skill_routing, text)
                return text

            abstained = skill_routing is not None and used_fallback
            clarify = outcome == "clarify"
            model_catalog = routing_prompt_mode == "model"
            if model_catalog:
                # Model-first selection: the whole authorized catalog is
                # listed with full descriptions and the model itself decides
                # which skills, if any, to load.
                lines = ["Available skills:"]
            elif skill_routing is not None and not used_fallback:
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
            elif clarify:
                lines = [
                    "Available skills (clarification required; names only):"
                ]
            elif abstained:
                # Router abstained: keep a compact name-only inventory so the
                # model still knows Skills exist, without spending the context
                # budget on every description/tool list for an off-topic task.
                lines = [
                    "Available skills (no routing evidence; names only):"
                ]
            else:
                lines = ["Available skills:"]
            for skill in skills:
                skill_name = skill.get("qualified_name") or skill.get("name", "")
                skill_description = skill.get("description", "")
                tools = skill.get("tools") or []
                if model_catalog:
                    lines.append(f"- {skill_name}: {skill_description}")
                elif skill_routing is not None and not used_fallback:
                    lines.append(f"- {skill_name}: {skill_description}")
                    if tools:
                        lines.append("  likely tools: " + ", ".join(str(tool) for tool in tools))
                elif abstained:
                    lines.append(f"- {skill_name}")
                else:
                    lines.append(f"- {skill_name}: {skill_description}")
            if model_catalog:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- You decide which of the listed skills apply to this task: zero, one, or several.",
                    "- To use a skill, call load_skill with its exact qualified name before following it.",
                    "- Multi-stage tasks may load further skills as the work progresses; you do not need to choose them all up front.",
                    "- For conditional tasks, establish the condition before choosing a later-stage workflow. Inspect project context when needed and load only the smallest sufficient set of applicable skills.",
                    "- Names not listed above are not available and will be rejected.",
                    "- If no skill applies, proceed with normal tools and reasoning.",
                ])
            elif clarify:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- The requested Skill or referent could not be resolved safely.",
                    "- Ask one concise clarifying question before choosing or loading a Skill.",
                    "- Do not silently substitute a different Skill.",
                ])
            elif skill_routing is not None and not used_fallback:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- These are the top routed skills for the current task.",
                    "- If a routed skill matches your workflow, call load_skill with its qualified name before following it.",
                    "- If none of the routed skills applies, proceed with normal tools and reasoning.",
                ])
            elif abstained:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- No skill matched this request with enough routing evidence.",
                    "- The names above are inventory only; load one only if the user explicitly asks for it or the task clearly matches its name.",
                    "- Otherwise proceed with normal tools and reasoning.",
                ])
            else:
                lines.extend([
                    "",
                    "SKILL USAGE GUIDE:",
                    "- If a listed skill matches the task, call load_skill with its name before following it.",
                    "- If none applies, proceed with normal tools and reasoning.",
                ])
            text = "\n".join(lines)
            if model_catalog:
                _record_catalog_prompt_chars(skill_routing, text)
            return text

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
