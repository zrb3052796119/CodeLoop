"""Closed sub-agent type definitions shared by task dispatch and workflows."""

from __future__ import annotations

# The workflow orchestrator is the only path allowed to descend to depth two;
# all other sub-agent tool sets remain unable to recurse.
MAX_AGENT_DEPTH = 2


AGENT_TYPES = {
    "explore": {
        "name": "Explore",
        "description": "Fast, read-only agent for codebase exploration and search",
        "system_prompt": (
            "You are an exploration agent. Your job is to quickly search and "
            "understand codebases. You should be fast and focused on finding "
            "relevant files and understanding structure. "
            "You can only use read-only tools. "
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
            "load_skill",
            "subagent_note_read",
            "subagent_note_write",
            "subagent_note_list",
        },
        # A real exploration needs a tree/grep pass plus several reads before
        # it can summarize; 5 turns ran out mid-survey.
        "max_turns": 12,
    },
    "plan": {
        "name": "Plan",
        "description": "Thorough agent for gathering context and understanding code",
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
            "code_review",
            "load_skill",
            "subagent_note_read",
            "subagent_note_write",
            "subagent_note_list",
        },
        "max_turns": 8,
    },
    "general": {
        "name": "General",
        "description": "Full-featured agent for complex multi-step tasks",
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
            "isolated execution, deterministic verification and review gates"
        ),
        "system_prompt": "",
        "allowed_tools": None,
        "max_phases": 4,
    },
}


__all__ = ("AGENT_TYPES", "MAX_AGENT_DEPTH")
