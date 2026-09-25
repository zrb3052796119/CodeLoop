"""Closed sub-agent type definitions shared by task dispatch and workflows."""

from __future__ import annotations

import os

# The workflow orchestrator is the only path allowed to descend to depth two;
# all other sub-agent tool sets remain unable to recurse.
MAX_AGENT_DEPTH = 2


AGENT_TYPES = {
    "explore": {
        "name": "Explore",
        "description": (
            "Fast, read-only agent for codebase exploration and search. "
            "Use it for broad code search or understanding; several explore "
            "agents issued in one step run in parallel."
        ),
        "system_prompt": (
            "You are an exploration agent. Your job is to quickly search and "
            "understand codebases. You should be fast and focused on finding "
            "relevant files and understanding structure. "
            "You can only use read-only tools. "
            "Adapt your search breadth to the thoroughness the caller "
            "states in the task prompt: default 'medium' covers the "
            "primary locations and naming conventions; 'very thorough' "
            "additionally sweeps alternate naming variants, adjacent "
            "directories, and near-duplicate call sites before "
            "concluding. "
            "When asked to enumerate items across the repository (callers, "
            "definitions, files matching a pattern), converge with grep_files "
            "FIRST instead of reading files one by one, spot-check a sample, "
            "and put the COMPLETE list in your result — a count or a partial "
            "list without the full enumeration is a failed answer. "
            "When done, provide a concise summary of your findings."
        ),
        "allowed_tools": {
            "read_file",
            "list_files",
            "grep_files",
            "file_tree",
            "find_symbols",
            "find_references",
            "get_ast_info",
            "todo_write",
            "load_skill",
            "browse_skill_directory",
            "search_skills",
            "subagent_note_read",
            "subagent_note_write",
            "subagent_note_list",
        },
        # A real exploration needs a tree/grep pass plus several reads before
        # it can summarize; 5 turns ran out mid-survey. Hosts can raise the
        # ceiling via MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS (see
        # resolve_max_turns); exceeding the limit now soft-lands into a
        # partial summary instead of discarding the run.
        "max_turns": 12,
    },
    "plan": {
        "name": "Plan",
        "description": (
            "Thorough agent for gathering context and understanding code. "
            "Use it for deep analysis or design before making changes."
        ),
        "system_prompt": (
            "You are a planning agent. Your job is to thoroughly understand "
            "the codebase and task before acting. Read multiple files, trace "
            "code paths, and build a complete mental model. "
            "You can only use read-only tools. "
            "When done, provide a detailed analysis with actionable recommendations."
        ),
        "allowed_tools": {
            "read_file",
            "list_files",
            "grep_files",
            "file_tree",
            "find_symbols",
            "find_references",
            "get_ast_info",
            "todo_write",
            "code_review",
            "load_skill",
            "browse_skill_directory",
            "search_skills",
            "subagent_note_read",
            "subagent_note_write",
            "subagent_note_list",
        },
        "max_turns": 8,
    },
    "general": {
        "name": "General",
        "description": (
            "Legacy full-featured agent for complex multi-step tasks; kept "
            "for legacy embedders and rejected by the production Agent Loop "
            "— use 'workflow' for state-changing work instead."
        ),
        "system_prompt": (
            "You are a general-purpose coding agent. You can read, write, "
            "and modify code. Follow best practices and explain your changes. "
            "Break complex tasks into smaller steps. "
            "When done, provide a summary of what you did and any important findings."
        ),
        "allowed_tools": None,  # None = all tools allowed
        "max_turns": 15,
    },
    "workflow": {
        "name": "Workflow",
        "description": (
            "Central Scheduler workflow with immutable role contracts, "
            "isolated execution, deterministic verification and review "
            "gates. Use it when delegating work that creates or modifies files."
        ),
        "system_prompt": "",
        "allowed_tools": None,
        "max_phases": 4,
    },
}


# Host-facing per-type turn-limit knobs, resolved at dispatch time so a
# long-running process picks up changes. Naming follows the existing
# MINI_CODE_SUBAGENT_<TYPE>_MODEL convention in config.py.
_MAX_TURNS_ENV_NAMES = {
    "explore": "MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS",
    "plan": "MINI_CODE_SUBAGENT_PLAN_MAX_TURNS",
    "general": "MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS",
}

# An override above this ceiling is treated as a typo, not a real budget.
_MAX_TURNS_OVERRIDE_CEILING = 200

# The closed universe of tools a project-defined read-only agent profile
# may reference: the union of the built-in explore and plan tool sets.
READ_ONLY_TOOL_WHITELIST = frozenset(
    AGENT_TYPES["explore"]["allowed_tools"]
) | frozenset(AGENT_TYPES["plan"]["allowed_tools"])


def _read_max_turns_override(env_name: str) -> str | None:
    """Read one override from the process env, then the private user env store.

    The private store (``~/.mini-code/.env``) is the same trusted source
    ``load_runtime_config`` uses for the other ``MINI_CODE_SUBAGENT_*`` knobs;
    any read failure fails closed to "no override".
    """
    raw = os.environ.get(env_name)
    if raw is not None:
        return raw
    try:
        from minicode.config import MINI_CODE_ENV_PATH, USER_MODEL_ENV_KEYS
        from minicode.env_file import read_private_env_file

        values = read_private_env_file(
            MINI_CODE_ENV_PATH,
            allowed_keys=USER_MODEL_ENV_KEYS,
        )
    except Exception:  # noqa: BLE001 - a broken env store means no override
        return None
    return values.get(env_name)


def resolve_max_turns(agent_type: str) -> int:
    """Effective model-turn limit for a sub-agent type.

    Defaults to the calibrated ``AGENT_TYPES`` value. A host may override it
    with ``MINI_CODE_SUBAGENT_<TYPE>_MAX_TURNS``; a missing, non-numeric, or
    out-of-range value fails closed to the default rather than disabling or
    unbounding the limit.
    """
    default = AGENT_TYPES[agent_type]["max_turns"]
    if not isinstance(default, int) or isinstance(default, bool):
        raise KeyError(f"{agent_type} has no model-turn limit")
    env_name = _MAX_TURNS_ENV_NAMES.get(agent_type)
    if env_name is None:
        return default
    raw = _read_max_turns_override(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip(), 10)
    except ValueError:
        return default
    if not 1 <= value <= _MAX_TURNS_OVERRIDE_CEILING:
        return default
    return value


__all__ = (
    "AGENT_TYPES",
    "MAX_AGENT_DEPTH",
    "READ_ONLY_TOOL_WHITELIST",
    "resolve_max_turns",
)
