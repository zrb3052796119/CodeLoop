from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from minicode.capability_contracts import (
    CapabilityEffects,
    CapabilityProfile,
    CapabilitySource,
    effects_for_scope,
)
from minicode.mcp import create_mcp_backed_tools
from minicode.skill_versions import observe_skill_catalog_safely
from minicode.skills import discover_skills
from minicode.tooling import (
    ToolCapabilityDomain,
    ToolCapabilityScope,
    ToolDefinition,
    ToolRegistry,
)
from minicode.tools.ask_user import ask_user_tool
from minicode.tools.batch_ops import batch_copy_tool, batch_delete_tool, batch_move_tool
from minicode.tools.code_nav import (
    find_references_tool,
    find_symbols_tool,
    get_ast_info_tool,
)
from minicode.tools.code_review import code_review_tool
from minicode.tools.diff_viewer import diff_viewer_tool
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.file_line_count import file_line_count_tool
from minicode.tools.file_tree import file_tree_tool
from minicode.tools.git import git_tool
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.list_files import list_files_tool
from minicode.tools.load_skill import create_load_skill_tool
from minicode.tools.patch_file import patch_file_tool
from minicode.tools.propose_skill import create_propose_skill_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.run_command import run_command_tool
from minicode.tools.subagent_notes import (
    subagent_note_list_tool,
    subagent_note_read_tool,
    subagent_note_write_tool,
)
from minicode.tools.task import task_tool
from minicode.tools.test_runner import test_runner_tool
from minicode.tools.todo_write import todo_write_tool
from minicode.tools.web_fetch import web_fetch_tool
from minicode.tools.web_search import web_search_tool
from minicode.tools.write_file import write_file_tool

if TYPE_CHECKING:
    from minicode.capability_registry import CapabilityRegistry
    from minicode.mcp_current_state import McpCurrentStateRegistry


def _with_capability(
    tool: ToolDefinition,
    domain: ToolCapabilityDomain,
    scope: ToolCapabilityScope,
    *,
    effects: CapabilityEffects | None = None,
    validate_call: bool = False,
) -> ToolDefinition:
    """Attach routing metadata without changing tool identity or authorization."""
    # Built-in definitions are process singletons and callers historically
    # compare them by identity.  Their routing classification is invariant, so
    # enrich the original definition instead of replacing it with a copy.
    tool.capability_domain = domain
    tool.capability_scope = scope
    tool.capability_profile = CapabilityProfile(
        domain=domain,
        scope=scope,
        source=CapabilitySource.BUILTIN,
        effects=effects if effects is not None else replace(
            effects_for_scope(scope),
            external=scope == "external",
            executes_code=False,
            delegates=False,
        ),
        evidence=("builtin-tool-contract",),
    )
    if validate_call:
        # Only opt in reviewed, pure built-in validators. Dynamic tool
        # validators can perform I/O and must not run during inspection.
        validator = tool.validator
        profile = tool.capability_profile

        def classify(arguments):
            validator(dict(arguments))
            return profile

        tool.capability_for = classify
    return tool


_CORE_TOOLS = [
    # User interaction
    _with_capability(ask_user_tool, "communication", "external"),
    # File operations
    _with_capability(list_files_tool, "file", "readonly"),
    _with_capability(grep_files_tool, "search", "readonly"),
    _with_capability(read_file_tool, "file", "readonly"),
    _with_capability(file_line_count_tool, "file", "readonly"),
    _with_capability(write_file_tool, "file", "write"),
    # modify_file_tool removed: identical to write_file (same _run/_validate)
    _with_capability(edit_file_tool, "file", "write"),
    _with_capability(patch_file_tool, "file", "write"),
    # Batch operations
    _with_capability(batch_copy_tool, "file", "destructive"),
    _with_capability(batch_move_tool, "file", "destructive"),
    _with_capability(batch_delete_tool, "file", "destructive"),
    # Command execution
    _with_capability(
        run_command_tool, "execution", "destructive",
        effects=CapabilityEffects(executes_code=True, delegates=False),
        validate_call=True,
    ),
    # Web tools
    _with_capability(web_fetch_tool, "web", "external"),
    _with_capability(web_search_tool, "search", "external"),
    # Task management
    _with_capability(todo_write_tool, "system", "write"),
    # Sub-agent
    _with_capability(
        task_tool, "execution", "external",
        effects=CapabilityEffects(external=True, delegates=True),
    ),
    # Explicit sub-agent collaboration notes
    _with_capability(subagent_note_write_tool, "communication", "write"),
    _with_capability(subagent_note_read_tool, "communication", "readonly"),
    _with_capability(subagent_note_list_tool, "communication", "readonly"),
    # Git workflow
    _with_capability(
        git_tool, "code", "write",
        effects=CapabilityEffects(executes_code=True, delegates=False),
    ),
    # Code intelligence
    _with_capability(find_symbols_tool, "search", "readonly"),
    _with_capability(find_references_tool, "search", "readonly"),
    _with_capability(get_ast_info_tool, "analysis", "readonly"),
    _with_capability(code_review_tool, "analysis", "readonly"),
    # Visualization
    _with_capability(file_tree_tool, "file", "readonly"),
    _with_capability(diff_viewer_tool, "code", "readonly"),
    # Testing
    _with_capability(
        test_runner_tool, "execution", "destructive",
        effects=CapabilityEffects(executes_code=True, delegates=False),
    ),
]

def _resolve_tool_profile(runtime: dict | None) -> str:
    configured = (
        os.environ.get("MINI_CODE_TOOL_PROFILE")
        or (runtime or {}).get("toolProfile")
        or "core"
    )
    return str(configured).strip().lower()


def _is_full_tool_profile(profile: str) -> bool:
    return profile in {"full", "utility", "utilities", "all"}


def _load_utility_wrapper_tools():
    # Lazy import keeps normal coding sessions from paying startup/import cost
    # for rarely used wrappers and keeps the default model tool surface small.
    from minicode.tools.archive_utils import (
        gzip_compress_tool,
        gzip_decompress_tool,
        tar_create_tool,
        tar_extract_tool,
        zip_create_tool,
        zip_extract_tool,
    )
    from minicode.tools.crypto_utils import (
        current_time_tool,
        hash_tool,
        hmac_tool,
        timestamp_tool,
    )
    from minicode.tools.csv_utils import csv_create_tool, csv_parse_tool
    from minicode.tools.encoding_utils import (
        base64_decode_tool,
        base64_encode_tool,
        url_decode_tool,
        url_encode_tool,
    )
    from minicode.tools.http_utils import http_request_tool
    from minicode.tools.json_utils import json_format_tool, json_parse_tool
    from minicode.tools.regex_utils import regex_replace_tool, regex_test_tool
    from minicode.tools.text_utils import (
        line_count_tool,
        random_string_tool,
        text_dedupe_tool,
        text_join_tool,
        text_sort_tool,
        uuid_generate_tool,
    )

    return [
        _with_capability(http_request_tool, "web", "external"),
        _with_capability(json_format_tool, "analysis", "readonly"),
        _with_capability(json_parse_tool, "analysis", "readonly"),
        _with_capability(regex_test_tool, "analysis", "readonly"),
        _with_capability(regex_replace_tool, "analysis", "readonly"),
        _with_capability(base64_encode_tool, "analysis", "readonly"),
        _with_capability(base64_decode_tool, "analysis", "readonly"),
        _with_capability(url_encode_tool, "web", "readonly"),
        _with_capability(url_decode_tool, "web", "readonly"),
        _with_capability(current_time_tool, "system", "readonly"),
        _with_capability(timestamp_tool, "system", "readonly"),
        _with_capability(hash_tool, "analysis", "readonly"),
        _with_capability(hmac_tool, "analysis", "readonly"),
        _with_capability(gzip_compress_tool, "file", "write", validate_call=True),
        _with_capability(gzip_decompress_tool, "file", "write", validate_call=True),
        _with_capability(tar_create_tool, "file", "write", validate_call=True),
        _with_capability(tar_extract_tool, "file", "write", validate_call=True),
        _with_capability(zip_create_tool, "file", "write", validate_call=True),
        _with_capability(zip_extract_tool, "file", "write", validate_call=True),
        _with_capability(csv_parse_tool, "analysis", "readonly"),
        _with_capability(csv_create_tool, "analysis", "readonly"),
        _with_capability(
            uuid_generate_tool, "system", "readonly",
            # uuid1 may invoke a system helper on Python/platform fallbacks.
            effects=CapabilityEffects(
                read_only=True, destructive=False, external=False, delegates=False,
            ),
        ),
        _with_capability(text_sort_tool, "analysis", "readonly"),
        _with_capability(text_dedupe_tool, "analysis", "readonly"),
        _with_capability(text_join_tool, "analysis", "readonly"),
        _with_capability(line_count_tool, "analysis", "readonly"),
        _with_capability(random_string_tool, "system", "readonly"),
    ]


def create_default_tool_registry(
    cwd: str,
    runtime: dict | None = None,
    *,
    mcp_current_state_registry: McpCurrentStateRegistry | None = None,
    include_mcp: bool = True,
    include_user_interaction: bool = True,
) -> ToolRegistry:
    skills = [asdict(skill) for skill in discover_skills(cwd)]
    observe_skill_catalog_safely(cwd, skills)
    if include_mcp:
        mcp_servers = dict(runtime.get("mcpServers", {})) if runtime else {}
        if mcp_current_state_registry is None:
            mcp = create_mcp_backed_tools(cwd=cwd, mcp_servers=mcp_servers)
        else:
            mcp = create_mcp_backed_tools(
                cwd=cwd,
                mcp_servers=mcp_servers,
                state_registry=mcp_current_state_registry,
            )
    else:
        # Read-only sub-agents never expose MCP tools; constructing the MCP
        # client layer here would still eagerly start every configured server
        # per sub-agent (and per parallel sub-agent), amplifying processes and
        # cost for no functional benefit.
        mcp = {
            "tools": [],
            "servers": [],
            "dispose": lambda: None,
        }
    profile = _resolve_tool_profile(runtime)
    tools = [
        tool
        for tool in _CORE_TOOLS
        if include_user_interaction or tool.name != "ask_user"
    ]
    if _is_full_tool_profile(profile):
        tools.extend(_load_utility_wrapper_tools())
    tools.append(
        _with_capability(
            create_load_skill_tool(cwd, skills),
            "system",
            "readonly",
        )
    )
    tools.extend(mcp["tools"])

    def proposal_tool_factory(
        capability_registry_provider: Callable[[], CapabilityRegistry],
    ) -> ToolDefinition:
        return _with_capability(
            create_propose_skill_tool(
                cwd,
                capability_registry_provider=capability_registry_provider,
            ),
            "system",
            "readonly",
        )

    return ToolRegistry(
        tools,
        skills=skills,
        mcp_servers=mcp["servers"],
        disposer=mcp["dispose"],
        mcp_current_state_registry=mcp_current_state_registry,
        registry_bound_tool_factories=[proposal_tool_factory],
    )
