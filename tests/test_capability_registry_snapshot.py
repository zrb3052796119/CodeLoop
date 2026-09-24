from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minicode.capability_registry as capability_registry_module
from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
    RegisteredCapability,
    build_tool_capability_snapshot,
    get_registry,
    register_tool_capabilities,
)
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools import create_default_tool_registry


def _tool(name: str, output: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output=output),
    )


def test_explicit_tool_capability_metadata_overrides_name_inference() -> None:
    tool = ToolDefinition(
        name="read_file",
        description="A deliberately misleading legacy-style name.",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="ok"),
        capability_domain="analysis",
        capability_scope="write",
    )

    capability = build_tool_capability_snapshot(ToolRegistry([tool])).get("read_file")

    assert capability is not None
    assert capability.metadata.domain is CapabilityDomain.ANALYSIS
    assert capability.metadata.scope is CapabilityScope.WRITE


def test_core_tools_publish_complete_capability_metadata(tmp_path) -> None:
    tools = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "core"},
        include_mcp=False,
    )
    snapshot = build_tool_capability_snapshot(tools)

    expected = {
        "ask_user": ("communication", "external"),
        "list_files": ("file", "readonly"),
        "grep_files": ("search", "readonly"),
        "read_file": ("file", "readonly"),
        "file_line_count": ("file", "readonly"),
        "write_file": ("file", "write"),
        "edit_file": ("file", "write"),
        "patch_file": ("file", "write"),
        "batch_copy": ("file", "destructive"),
        "batch_move": ("file", "destructive"),
        "batch_delete": ("file", "destructive"),
        "run_command": ("execution", "destructive"),
        "web_fetch": ("web", "external"),
        "web_search": ("search", "external"),
        "todo_write": ("system", "write"),
        "task": ("execution", "external"),
        "subagent_note_write": ("communication", "write"),
        "subagent_note_read": ("communication", "readonly"),
        "subagent_note_list": ("communication", "readonly"),
        "git": ("code", "write"),
        "find_symbols": ("search", "readonly"),
        "find_references": ("search", "readonly"),
        "get_ast_info": ("analysis", "readonly"),
        "code_review": ("analysis", "readonly"),
        "file_tree": ("file", "readonly"),
        "diff_viewer": ("code", "readonly"),
        "test_runner": ("execution", "destructive"),
        "load_skill": ("system", "readonly"),
        "browse_skill_directory": ("system", "readonly"),
        "search_skills": ("system", "readonly"),
        "propose_skill": ("system", "readonly"),
    }

    assert set(tools.list_all()) == set(expected)
    assert {
        name: (
            snapshot.get(name).metadata.domain.value,
            snapshot.get(name).metadata.scope.value,
        )
        for name in tools.list_all()
    } == expected
    assert all(
        tool.capability_domain is not None and tool.capability_scope is not None
        for tool in tools.list()
    )
    todo_write = tools.find("todo_write")
    assert todo_write is not None
    assert todo_write.is_read_only is False
    assert todo_write.is_concurrency_safe is False


def test_full_profile_tools_publish_complete_capability_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINI_CODE_TOOL_PROFILE", raising=False)
    tools = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "full"},
        include_mcp=False,
    )
    snapshot = build_tool_capability_snapshot(tools)
    expected = {
        "http_request": ("web", "external"),
        "json_format": ("analysis", "readonly"),
        "json_parse": ("analysis", "readonly"),
        "regex_test": ("analysis", "readonly"),
        "regex_replace": ("analysis", "readonly"),
        "base64_encode": ("analysis", "readonly"),
        "base64_decode": ("analysis", "readonly"),
        "url_encode": ("web", "readonly"),
        "url_decode": ("web", "readonly"),
        "current_time": ("system", "readonly"),
        "timestamp_convert": ("system", "readonly"),
        "hash": ("analysis", "readonly"),
        "hmac": ("analysis", "readonly"),
        "gzip_compress": ("file", "write"),
        "gzip_decompress": ("file", "write"),
        "tar_create": ("file", "write"),
        "tar_extract": ("file", "write"),
        "zip_create": ("file", "write"),
        "zip_extract": ("file", "write"),
        "csv_parse": ("analysis", "readonly"),
        "csv_create": ("analysis", "readonly"),
        "uuid_generate": ("system", "readonly"),
        "text_sort": ("analysis", "readonly"),
        "text_dedupe": ("analysis", "readonly"),
        "text_join": ("analysis", "readonly"),
        "line_count": ("analysis", "readonly"),
        "random_string": ("system", "readonly"),
    }

    assert {
        name: (
            snapshot.get(name).metadata.domain.value,
            snapshot.get(name).metadata.scope.value,
        )
        for name in expected
    } == expected
    assert all(
        tool.capability_domain is not None and tool.capability_scope is not None
        for tool in tools.list()
    )


def test_capability_scope_is_not_an_execution_permission() -> None:
    tool = ToolDefinition(
        name="opaque_mutator",
        description="Routing metadata must not authorize execution.",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="ok"),
        capability_domain="file",
        capability_scope="readonly",
    )

    assert tool.is_read_only is False


def test_legacy_tool_without_explicit_metadata_keeps_compatibility_fallback() -> None:
    snapshot = build_tool_capability_snapshot(
        ToolRegistry([_tool("legacy_web_fetch", "ok")])
    )

    capability = snapshot.get("legacy_web_fetch")
    assert capability is not None
    assert capability.metadata.domain is CapabilityDomain.WEB
    assert capability.metadata.scope is CapabilityScope.EXTERNAL


@pytest.mark.parametrize(
    ("name", "domain", "scope"),
    [
        (
            "mcp__mail__send_email",
            CapabilityDomain.COMMUNICATION,
            CapabilityScope.EXTERNAL,
        ),
        (
            "mcp__records__delete_records",
            CapabilityDomain.UNKNOWN,
            CapabilityScope.DESTRUCTIVE,
        ),
        (
            "legacy_delete_records",
            CapabilityDomain.UNKNOWN,
            CapabilityScope.DESTRUCTIVE,
        ),
        (
            "opaque_tool",
            CapabilityDomain.UNKNOWN,
            CapabilityScope.DESTRUCTIVE,
        ),
        (
            "mcp__codex_app__create_thread",
            CapabilityDomain.UNKNOWN,
            CapabilityScope.EXTERNAL,
        ),
        (
            "mcp__sheets__format_spreadsheet",
            CapabilityDomain.UNKNOWN,
            CapabilityScope.EXTERNAL,
        ),
    ],
)
def test_dynamic_tool_fallback_never_mislabels_external_or_destructive_effects(
    name: str,
    domain: CapabilityDomain,
    scope: CapabilityScope,
) -> None:
    snapshot = build_tool_capability_snapshot(ToolRegistry([_tool(name, "ok")]))

    capability = snapshot.get(name)
    assert capability is not None
    assert capability.metadata.domain is domain
    assert capability.metadata.scope is scope


def _reset_process_registry(monkeypatch: pytest.MonkeyPatch) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    monkeypatch.setattr(capability_registry_module, "_registry", registry)
    return registry


def test_compat_registration_removes_tools_missing_from_current_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_registry = _reset_process_registry(monkeypatch)
    process_registry.register(
        CapabilityMetadata(
            name="custom_capability",
            domain=CapabilityDomain.ANALYSIS,
            scope=CapabilityScope.READONLY,
            description="not owned by a ToolRegistry",
        ),
        lambda **_: None,
    )

    register_tool_capabilities(
        ToolRegistry([_tool("read_file", "read"), _tool("write_file", "write")])
    )
    assert set(process_registry.list_all()) == {
        "custom_capability",
        "read_file",
        "write_file",
    }

    register_tool_capabilities(ToolRegistry([_tool("read_file", "read-current")]))

    assert set(process_registry.list_all()) == {"custom_capability", "read_file"}
    assert process_registry.get("write_file") is None


def test_compat_registration_refreshes_same_name_metadata_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_registry = _reset_process_registry(monkeypatch)
    register_tool_capabilities(ToolRegistry([_tool("read_file", "first")]))

    first = process_registry.get("read_file")
    assert first is not None
    assert first.metadata.description == "read_file description"
    with pytest.raises(RuntimeError, match="routing metadata only"):
        first.execute({})

    refreshed = ToolDefinition(
        name="read_file",
        description="refreshed description",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="second"),
    )
    register_tool_capabilities(ToolRegistry([refreshed]))

    current = process_registry.get("read_file")
    assert current is not None
    assert current.metadata.description == "refreshed description"
    with pytest.raises(RuntimeError, match="routing metadata only"):
        current.execute({})


def test_tool_capability_snapshots_are_frozen_and_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_registry = _reset_process_registry(monkeypatch)

    first = build_tool_capability_snapshot(ToolRegistry([_tool("alpha", "from-a")]))
    second = build_tool_capability_snapshot(ToolRegistry([_tool("beta", "from-b")]))

    assert first is not second
    assert first.is_frozen is True
    assert second.is_frozen is True
    assert first.list_all() == ["alpha"]
    assert second.list_all() == ["beta"]
    assert not any(
        isinstance(capability, RegisteredCapability)
        for capability in first._capabilities.values()
    )
    assert process_registry.list_all() == []
    assert get_registry() is process_registry

    alpha = first.get("alpha")
    beta = second.get("beta")
    assert alpha is not None
    assert beta is not None
    assert not hasattr(alpha, "_registered")
    assert not hasattr(beta, "_registered")
    with pytest.raises(RuntimeError, match="routing metadata only"):
        alpha.execute({})
    with pytest.raises(RuntimeError, match="routing metadata only"):
        beta.execute({})

    with pytest.raises(FrozenInstanceError):
        alpha.metadata.domain = CapabilityDomain.CODE
    with pytest.raises(FrozenInstanceError):
        alpha.metadata.tags = ("mutated",)
    with pytest.raises(AttributeError):
        alpha.handler = lambda **_: None

    exported = first.to_dict()
    exported["capabilities"]["alpha"]["metadata"]["tags"].append("mutated")
    assert alpha.metadata.tags == ("tool", "alpha")
    assert first.list_by_domain(CapabilityDomain.UNKNOWN) == ["alpha"]
    assert first.list_by_tag("alpha") == ["alpha"]

    with pytest.raises(RuntimeError, match="snapshot is frozen"):
        first.unregister("alpha")
    with pytest.raises(RuntimeError, match="snapshot is frozen"):
        second.register(
            CapabilityMetadata(
                name="gamma",
                domain=CapabilityDomain.UNKNOWN,
                scope=CapabilityScope.READONLY,
                description="late mutation",
            ),
            lambda **_: None,
        )


def test_frozen_capability_view_keeps_detached_execution_statistics() -> None:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityMetadata(
            name="detached",
            domain=CapabilityDomain.ANALYSIS,
            scope=CapabilityScope.READONLY,
            description="detached frozen view",
        ),
        lambda value: value + 1,
    )
    registry.freeze()

    capability = registry.get("detached")

    assert capability is not None
    assert capability.execute({"value": 1}) == 2
    assert capability.call_count == 1
    assert registry.get("detached") is capability
    assert registry.get_stats()["most_used"] == [("detached", 1)]
    assert not hasattr(capability, "_registered")
