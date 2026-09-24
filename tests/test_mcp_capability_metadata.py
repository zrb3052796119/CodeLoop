"""Offline contracts for MCP capability provenance and execution separation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minicode.capability_contracts import (
    CapabilityDomain,
    CapabilityScope,
    CapabilitySource,
    CapabilityStatus,
)
from minicode.config import merge_settings, read_mcp_config_file
from minicode.mcp import StdioMcpClient, create_mcp_backed_tools
from minicode.tooling import ToolContext, ToolDefinition, ToolResult


@pytest.fixture
def offline_discovery(monkeypatch: pytest.MonkeyPatch):
    """Exercise production wrapping without starting a server or a model."""
    def forbid_start(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability discovery must stay offline")

    monkeypatch.setattr(StdioMcpClient, "start", forbid_start)
    monkeypatch.setattr(StdioMcpClient, "list_resources", lambda _self: [])
    monkeypatch.setattr(StdioMcpClient, "list_prompts", lambda _self: [])

    def discover(
        descriptor: dict[str, Any], config: dict[str, Any] | None = None,
    ) -> ToolDefinition:
        monkeypatch.setattr(StdioMcpClient, "list_tools", lambda _self: [descriptor])
        bundle = create_mcp_backed_tools(
            cwd="/tmp/minicode-offline-capabilities",
            mcp_servers={"fixture": {"command": "unused", **(config or {})}},
        )
        try:
            assert bundle["servers"][0]["status"] == "connected"
            assert len(bundle["tools"]) == 1
            return bundle["tools"][0]
        finally:
            bundle["dispose"]()

    return discover


@pytest.mark.parametrize("name", ["read_file", "safe_search", "delete_everything"])
def test_name_is_not_evidence_for_undeclared_mcp_tool(offline_discovery, name):
    tool = offline_discovery({"name": name})
    profile = tool.capability_profile
    assert profile is not None
    assert (profile.domain, profile.scope) == (
        CapabilityDomain.UNKNOWN, CapabilityScope.EXTERNAL,
    )
    assert (profile.source, profile.status) == (
        CapabilitySource.UNKNOWN, CapabilityStatus.UNKNOWN,
    )
    assert profile.effects.to_dict() == {
        "read_only": None, "destructive": None, "external": True,
        "executes_code": None, "delegates": None,
    }


def test_server_readonly_hint_never_enables_concurrent_execution(offline_discovery):
    tool = offline_discovery({
        "name": "read_file",
        "annotations": {"readOnlyHint": True, "destructiveHint": True},
    })
    profile = tool.capability_profile
    assert profile is not None
    assert (profile.source, profile.status) == (
        CapabilitySource.MCP_ANNOTATIONS, CapabilityStatus.HINTED,
    )
    assert profile.effects.read_only is True
    assert profile.effects.destructive is False
    assert tool.metadata is None
    assert tool.is_read_only is False
    assert tool.is_concurrency_safe is False
    assert tool.call_is_concurrency_safe({}) is False


@pytest.mark.parametrize("open_world", [True, False])
def test_open_world_hint_does_not_infer_domain_or_erase_transport_boundary(
    offline_discovery, open_world,
):
    tool = offline_discovery({
        "name": "memory_read",
        "annotations": {"openWorldHint": open_world},
    })
    profile = tool.capability_profile
    assert profile is not None
    assert profile.domain is CapabilityDomain.UNKNOWN
    assert profile.effects.external is True
    assert profile.effects.read_only is None
    assert profile.effects.destructive is None
    assert f"open_world:{str(open_world).lower()}" in profile.evidence


def test_missing_hint_values_are_not_filled_with_protocol_defaults(offline_discovery):
    profile = offline_discovery({
        "name": "lookup", "annotations": {"readOnlyHint": False},
    }).capability_profile
    assert profile is not None
    assert profile.effects.read_only is False
    assert profile.effects.destructive is None
    assert profile.effects.executes_code is None


def test_idempotent_hint_is_descriptive_and_ignored_for_readonly(offline_discovery):
    profile = offline_discovery({
        "name": "write", "annotations": {"idempotentHint": False},
    }).capability_profile
    assert profile is not None
    assert "idempotent:false" in profile.evidence
    readonly = offline_discovery({
        "name": "read",
        "annotations": {"readOnlyHint": True, "idempotentHint": False},
    }).capability_profile
    assert readonly is not None
    assert "idempotent:false" not in readonly.evidence


@pytest.mark.parametrize("annotations", [
    None, [], "readonly", False,
    {"readOnlyHint": "false"}, {"readOnlyHint": 1},
    {"destructiveHint": 0}, {"openWorldHint": None},
    {"idempotentHint": "yes"}, {"title": 123},
])
def test_invalid_annotations_stay_unknown_and_do_not_drop_tool(
    offline_discovery, annotations,
):
    profile = offline_discovery({
        "name": "read_file", "annotations": annotations,
    }).capability_profile
    assert profile is not None
    assert profile.status is CapabilityStatus.INVALID
    assert profile.source is CapabilitySource.MCP_ANNOTATIONS
    assert profile.effects.read_only is None
    assert profile.effects.destructive is None


@pytest.mark.parametrize("annotations", [{}, {"title": "Safe tool"}, {"futureHint": True}])
def test_display_and_unknown_annotations_do_not_establish_effects(
    offline_discovery, annotations,
):
    profile = offline_discovery({
        "name": "tool", "annotations": annotations,
    }).capability_profile
    assert profile is not None
    assert profile.status is CapabilityStatus.UNKNOWN
    assert profile.source is CapabilitySource.UNKNOWN


def test_local_declaration_wins_and_keeps_disagreement_visible(offline_discovery):
    tool = offline_discovery(
        {"name": "read_file", "annotations": {"readOnlyHint": True}},
        {"toolCapabilities": {"read_file": {
            "domain": "communication", "scope": "destructive",
            "effects": {"executes_code": False, "delegates": False},
            "purposes": ["message cleanup"],
            "supported_operations": ["write.delete"],
        }}},
    )
    profile = tool.capability_profile
    assert profile is not None
    assert (profile.domain, profile.scope) == (
        CapabilityDomain.COMMUNICATION, CapabilityScope.DESTRUCTIVE,
    )
    assert (profile.source, profile.status) == (
        CapabilitySource.LOCAL_CONFIG, CapabilityStatus.DECLARED,
    )
    assert profile.effects.read_only is False
    assert profile.effects.destructive is True
    assert profile.effects.external is True
    assert profile.purposes == ("message cleanup",)
    assert profile.supported_operations == ("write.delete",)
    assert "mcp_annotations_conflict" in profile.evidence
    assert tool.is_read_only is False
    assert tool.is_concurrency_safe is False


def test_valid_local_declaration_survives_invalid_remote_hint(offline_discovery):
    profile = offline_discovery(
        {"name": "query", "annotations": {"readOnlyHint": "SECRET"}},
        {"toolCapabilities": {"query": {"domain": "search", "scope": "readonly"}}},
    ).capability_profile
    assert profile is not None
    assert profile.status is CapabilityStatus.DECLARED
    assert "mcp_annotations_invalid" in profile.evidence
    assert "SECRET" not in json.dumps(profile.to_dict())


def test_local_external_profile_can_explicitly_leave_effects_unknown(offline_discovery):
    profile = offline_discovery(
        {"name": "calculate"},
        {"toolCapabilities": {"calculate": {
            "domain": "analysis", "scope": "external",
            "effects": {"read_only": True, "executes_code": True, "delegates": None},
        }}},
    ).capability_profile
    assert profile is not None
    assert profile.status is CapabilityStatus.DECLARED
    assert profile.effects.read_only is True
    assert profile.effects.executes_code is True
    assert profile.effects.delegates is None


@pytest.mark.parametrize("declaration", [
    None, [], "readonly", {},
    {"domain": "search"}, {"domain": "database", "scope": "readonly"},
    {"domain": "search", "scope": "safe"},
    {"domain": "search", "scope": "readonly", "extra": True},
    {"domain": "search", "scope": "readonly", "effects": []},
    {"domain": "search", "scope": "readonly", "effects": {"read_only": "true"}},
    {"domain": "search", "scope": "readonly", "effects": {"destructive": True}},
    {"domain": "search", "scope": "readonly", "effects": {"read_only": False}},
    {"domain": "search", "scope": "readonly", "effects": {"external": False}},
    {"domain": "search", "scope": "readonly", "effects": {"invented": True}},
    {"domain": "file", "scope": "destructive", "effects": {"destructive": False}},
    {"domain": "file", "scope": "write", "effects": {"read_only": True}},
    {"domain": "search", "scope": "readonly", "purposes": "search"},
    {"domain": "search", "scope": "readonly", "purposes": [None]},
    {"domain": "search", "scope": "readonly", "supported_operations": ["read"]},
])
def test_invalid_local_declaration_does_not_fallback_to_server_or_tool_name(
    offline_discovery, declaration,
):
    profile = offline_discovery(
        {"name": "safe_search", "annotations": {"readOnlyHint": True}},
        {"toolCapabilities": {"safe_search": declaration}},
    ).capability_profile
    assert profile is not None
    assert (profile.domain, profile.scope) == (
        CapabilityDomain.UNKNOWN, CapabilityScope.EXTERNAL,
    )
    assert (profile.source, profile.status) == (
        CapabilitySource.LOCAL_CONFIG, CapabilityStatus.INVALID,
    )
    assert profile.effects.read_only is None


@pytest.mark.parametrize("declarations", [None, [], "invalid", False])
def test_invalid_local_container_does_not_remove_discovered_tool(
    offline_discovery, declarations,
):
    profile = offline_discovery(
        {"name": "tool"}, {"toolCapabilities": declarations},
    ).capability_profile
    assert profile is not None
    assert profile.status is CapabilityStatus.INVALID
    assert profile.evidence == ("local_tool_capabilities_invalid",)


def test_override_uses_original_exact_tool_name(offline_discovery):
    config = {"toolCapabilities": {
        "Browse Results": {"domain": "search", "scope": "readonly"},
    }}
    original = offline_discovery({"name": "Browse Results"}, config)
    sanitized = offline_discovery({"name": "browse_results"}, config)
    assert original.name == sanitized.name
    assert original.capability_profile.status is CapabilityStatus.DECLARED
    assert sanitized.capability_profile.status is CapabilityStatus.UNKNOWN


def test_declarations_detach_from_mutable_config(offline_discovery):
    declaration = {
        "domain": "search", "scope": "readonly", "purposes": ["lookup"],
        "supported_operations": ["read.search"],
    }
    tool = offline_discovery({"name": "query"}, {"toolCapabilities": {"query": declaration}})
    declaration["purposes"].append("changed")
    declaration["supported_operations"].clear()
    assert tool.capability_profile.purposes == ("lookup",)
    assert tool.capability_profile.supported_operations == ("read.search",)


def test_same_tool_name_on_other_server_does_not_inherit_local_override(
    offline_discovery, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(StdioMcpClient, "list_tools", lambda _self: [{"name": "query"}])
    bundle = create_mcp_backed_tools(cwd="/tmp", mcp_servers={
        "declared": {"command": "unused", "toolCapabilities": {
            "query": {"domain": "search", "scope": "readonly"},
        }},
        "other": {"command": "unused"},
    })
    try:
        profiles = {tool.name: tool.capability_profile for tool in bundle["tools"]}
        assert profiles["mcp__declared__query"].status is CapabilityStatus.DECLARED
        assert profiles["mcp__other__query"].status is CapabilityStatus.UNKNOWN
    finally:
        bundle["dispose"]()


def test_bad_declaration_keeps_other_tools_and_does_not_expose_its_value(
    offline_discovery, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(StdioMcpClient, "list_tools", lambda _self: [
        {"name": "query"}, {"name": "other"},
    ])
    secret = "fixture-private-declaration-value"
    bundle = create_mcp_backed_tools(cwd="/tmp", mcp_servers={"fixture": {
        "command": "unused", "toolCapabilities": {
            "query": {"domain": secret, "scope": "readonly"},
            "other": {"domain": "analysis", "scope": "readonly"},
        },
    }})
    try:
        assert bundle["servers"][0]["status"] == "connected"
        assert bundle["servers"][0]["toolCount"] == 2
        profiles = {tool.name: tool.capability_profile for tool in bundle["tools"]}
        assert profiles["mcp__fixture__query"].status is CapabilityStatus.INVALID
        assert profiles["mcp__fixture__other"].status is CapabilityStatus.DECLARED
        assert secret not in caplog.text
        assert secret not in json.dumps(bundle["servers"])
        assert secret not in json.dumps([profile.to_dict() for profile in profiles.values()])
    finally:
        bundle["dispose"]()


def test_capability_metadata_does_not_change_mcp_call_arguments(
    offline_discovery, monkeypatch: pytest.MonkeyPatch,
):
    calls = []

    def call_tool(self, name, arguments, **_kwargs):
        calls.append((self.server_name, name, arguments))
        return ToolResult(ok=True, output="fixture result")

    monkeypatch.setattr(StdioMcpClient, "call_tool", call_tool)
    tool = offline_discovery(
        {"name": "query", "annotations": {"readOnlyHint": True}},
        {"toolCapabilities": {"query": {"domain": "search", "scope": "readonly"}}},
    )
    result = tool.run({"term": "hello"}, ToolContext(cwd="/tmp"))
    assert result.ok is True
    assert calls == [("fixture", "query", {"term": "hello"})]
    assert tool.is_read_only is False


def test_existing_config_loader_preserves_per_tool_declarations(tmp_path: Path):
    config_path = tmp_path / ".mcp.json"
    capability = {"domain": "search", "scope": "readonly"}
    config_path.write_text(json.dumps({"mcpServers": {"fixture": {
        "command": "unused", "toolCapabilities": {"query": capability},
    }}}), encoding="utf-8")
    servers = read_mcp_config_file(config_path)
    effective = merge_settings(
        {"mcpServers": servers}, {"mcpServers": {"fixture": {"env": {"FLAG": "1"}}}},
    )
    assert effective["mcpServers"]["fixture"]["toolCapabilities"]["query"] == capability


def test_cached_inventory_tools_have_explicit_local_readonly_metadata(
    offline_discovery, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(StdioMcpClient, "list_tools", lambda _self: [])
    monkeypatch.setattr(StdioMcpClient, "list_resources", lambda _self: [{"uri": "fake://one"}])
    monkeypatch.setattr(StdioMcpClient, "list_prompts", lambda _self: [{"name": "example"}])
    bundle = create_mcp_backed_tools(cwd="/tmp", mcp_servers={"fixture": {"command": "unused"}})
    try:
        for name in ("list_mcp_resources", "list_mcp_prompts"):
            tool = next(item for item in bundle["tools"] if item.name == name)
            assert (tool.capability_domain, tool.capability_scope) == ("system", "readonly")
        for name in ("read_mcp_resource", "get_mcp_prompt"):
            tool = next(item for item in bundle["tools"] if item.name == name)
            assert (tool.capability_domain, tool.capability_scope) == ("unknown", "external")
    finally:
        bundle["dispose"]()
