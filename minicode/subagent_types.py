"""Closed sub-agent type definitions shared by task dispatch and workflows."""

from __future__ import annotations

import os
import re

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
            "Read-only agent for a bounded investigation or design decision. "
            "Use workflow directly when delegating implementation."
        ),
        "system_prompt": (
            "You are a planning agent. Answer the assigned question using "
            "read-only tools. Start with the named files and relevant callers "
            "or tests; expand the search only to resolve a concrete uncertainty. "
            "Stop exploring once the evidence is sufficient for the requested "
            "decision. Return concise findings with repository paths and line "
            "ranges, the smallest supported next steps, relevant checks, and "
            "any unresolved blockers. Do not survey unrelated code, repeat "
            "completed reads without a reason, or draft implementation patches. "
            "A plan is advisory, not permission or workflow evidence."
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
            "isolated execution, final deterministic verification and "
            "host-selected review. Use it when delegating work that creates or modifies files."
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

# The closed universe of tools a project-defined read-only agent profile
# may reference: the union of the built-in explore and plan tool sets, plus
# the read-only vision tool (referenced by judge profiles, not part of the
# general explorers' default sets).
READ_ONLY_TOOL_WHITELIST = (
    frozenset(AGENT_TYPES["explore"]["allowed_tools"])
    | frozenset(AGENT_TYPES["plan"]["allowed_tools"])
    | frozenset({"inspect_image"})
)


def _read_max_turns_override(env_name: str) -> str | None:
    """Read one override from the process env, then the private user env store.

    The private store (``~/.mini-code/.env``) is the same trusted source
    ``load_runtime_config`` uses for the other ``MINI_CODE_SUBAGENT_*`` knobs;
    no configured value means no turn ceiling.
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
    except Exception as error:  # noqa: BLE001 - do not reinterpret unreadable policy
        raise ValueError("sub-agent turn limit configuration is unreadable") from error
    return values.get(env_name)


def resolve_max_turns(agent_type: str) -> int | None:
    """Effective model-turn limit for a sub-agent type.

    Missing or zero settings mean no cumulative turn limit. Positive settings
    are honored exactly; malformed settings are rejected. The legacy numeric
    AGENT_TYPES values remain available to explicitly bounded callers only.
    """
    if agent_type not in _MAX_TURNS_ENV_NAMES:
        raise KeyError(f"{agent_type} has no model-turn limit")
    return configured_max_turns(agent_type)


def configured_max_turns(agent_type: str) -> int | None:
    """Return a positive configured ceiling, or None for missing/zero."""
    env_name = _MAX_TURNS_ENV_NAMES.get(agent_type)
    if env_name is None:
        return None
    raw = _read_max_turns_override(env_name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str) or re.fullmatch(r"\+?[0-9]+", raw.strip()) is None:
        raise ValueError(f"{env_name} must be a non-negative integer")
    return int(raw.strip(), 10) or None


__all__ = (
    "AGENT_TYPES",
    "MAX_AGENT_DEPTH",
    "READ_ONLY_TOOL_WHITELIST",
    "resolve_max_turns",
    "configured_max_turns",
)
