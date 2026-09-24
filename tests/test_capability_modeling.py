"""Capability provenance reaches real consumers without granting authority."""

from dataclasses import FrozenInstanceError, replace

import pytest

from minicode.capability_contracts import (
    CapabilityDomain,
    CapabilityEffects,
    CapabilityProfile,
    CapabilityScope,
    CapabilitySource,
    CapabilityStatus,
)
from minicode.capability_registry import build_tool_capability_snapshot
from minicode.intent_parser import parse_intent
from minicode.permissions import PermissionManager
from minicode.skill_authoring import propose_skill
from minicode.skill_router import SkillRouter
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools import create_default_tool_registry


def _tool(name="opaque", **metadata):
    return ToolDefinition(
        name=name,
        description="An operation whose name is not its contract.",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(True, "ok"),
        **metadata,
    )


def test_profiles_and_snapshot_exports_do_not_share_mutable_metadata():
    operations = ["search.read"]
    profile = CapabilityProfile(
        "search", "readonly", supported_operations=operations,
        effects=CapabilityEffects(read_only=True, destructive=False, external=True),
    )
    tools = ToolRegistry([_tool(capability_profile=profile)])
    snapshot = build_tool_capability_snapshot(tools)
    operations.append("code.delete")
    exported = snapshot.get("opaque").metadata.to_dict()
    exported["profile"]["supported_operations"].append("code.delete")
    exported["profile"]["effects"]["read_only"] = False

    assert snapshot.get("opaque").metadata.profile.supported_operations == ("search.read",)
    assert snapshot.describe_call("opaque", {}).effects.read_only is True
    with pytest.raises(FrozenInstanceError):
        profile.scope = CapabilityScope.WRITE


@pytest.mark.parametrize("value", ["false", 0, 1, [], {}])
def test_effects_do_not_coerce_untrusted_truthiness(value):
    with pytest.raises(TypeError):
        CapabilityEffects(read_only=value)


@pytest.mark.parametrize(
    ("scope", "effects"),
    [
        ("readonly", CapabilityEffects(read_only=False)),
        ("readonly", CapabilityEffects(destructive=True)),
        ("write", CapabilityEffects(read_only=True)),
        ("destructive", CapabilityEffects(read_only=True)),
        ("destructive", CapabilityEffects(destructive=False)),
        ("external", CapabilityEffects(external=False)),
    ],
)
def test_shared_contract_rejects_certain_scope_effect_contradictions(scope, effects):
    with pytest.raises(ValueError):
        CapabilityProfile("file", scope, effects=effects)


def test_snapshot_keeps_the_classifier_it_was_built_with():
    read = CapabilityProfile("file", "readonly", effects=CapabilityEffects(read_only=True))
    write = CapabilityProfile("file", "write", effects=CapabilityEffects(read_only=False))
    tool = _tool(capability_profile=write, capability_for=lambda _: read)
    tools = ToolRegistry([tool])
    before = build_tool_capability_snapshot(tools)
    tool.capability_for = lambda _: write
    after = build_tool_capability_snapshot(tools)

    assert before.describe_call("opaque", {}).effects.read_only is True
    assert after.describe_call("opaque", {}).effects.read_only is False
    assert tools.describe_capability("absent", {}) is None


def test_explicit_null_arguments_are_distinct_from_static_inspection():
    tools = ToolRegistry([_tool(capability_profile=CapabilityProfile("file", "readonly"))])

    assert tools.describe_capability("opaque").status is CapabilityStatus.DECLARED
    assert tools.describe_capability("opaque", None).status is CapabilityStatus.INVALID
    assert build_tool_capability_snapshot(tools).describe_call("opaque", None).status is CapabilityStatus.INVALID


@pytest.mark.parametrize("name", ["read_file", "legacy_delete_records", "opaque"])
def test_name_fallback_is_neither_evidence_of_safety_nor_destruction(name):
    tools = ToolRegistry([_tool(name)])
    profile = tools.describe_capability(name)

    assert profile.source is CapabilitySource.LEGACY_NAME
    assert profile.status is CapabilityStatus.INFERRED
    assert all(value is None for value in profile.effects.to_dict().values())
    router = SkillRouter()
    snapshot = build_tool_capability_snapshot(tools)
    score, reasons = router._tool_affinity(
        {"tools": [name]}, parse_intent("explain this code"),
        list(CapabilityDomain), list(CapabilityScope), snapshot,
    )
    assert score == 0.0
    assert reasons == ["tool-capability-unverified"]
    assert router._relevant_capabilities(parse_intent("explain this code"), snapshot) == ([], [])


@pytest.mark.parametrize("status", ["hinted", "unknown", "invalid"])
def test_unverified_metadata_does_not_veto_an_explicit_skill(status):
    profile = CapabilityProfile("file", "destructive", status=status)
    snapshot = build_tool_capability_snapshot(
        ToolRegistry([_tool("remote", capability_profile=profile)])
    )
    skill = {
        "name": "codebase-explanation", "qualified_name": "codebase-explanation",
        "description": "Explain code architecture and tool calling flow across files",
        "path": "/tmp/codebase-explanation/SKILL.md", "source": "project",
        "tools": ["remote"],
    }

    result = SkillRouter().route(
        [skill], parse_intent("Use $codebase-explanation to explain this code"), snapshot,
    )

    assert [selected.name for selected in result.selected] == ["codebase-explanation"]
    assert not any(
        reason.startswith("tool-scope-penalty:")
        for selected in result.selected for reason in selected.reasons
    )


def test_skill_proposal_exposes_uncertainty_without_changing_tool_availability(tmp_path):
    snapshot = build_tool_capability_snapshot(ToolRegistry([_tool("legacy_delete_records")]))
    proposal = propose_skill(
        tmp_path,
        {"name": "Record management", "description": "Manage records", "tools": ["legacy_delete_records"]},
        snapshot,
    )

    item = proposal["tool_validation"][0]
    assert item["known"] is True
    assert item["capability_profile"]["status"] == "inferred"
    assert item["capability_profile"]["effects"]["destructive"] is None


@pytest.mark.parametrize(
    ("scope", "effects", "expected_penalty"),
    [
        ("destructive", CapabilityEffects(executes_code=True), False),
        ("external", CapabilityEffects(read_only=True, destructive=False, external=True), False),
        ("write", CapabilityEffects(read_only=False), True),
        ("destructive", CapabilityEffects(read_only=False, destructive=True), True),
    ],
)
def test_readonly_affinity_uses_known_effects_instead_of_legacy_scope(
    scope, effects, expected_penalty,
):
    snapshot = build_tool_capability_snapshot(ToolRegistry([
        _tool(capability_profile=CapabilityProfile("file", scope, effects=effects)),
    ]))

    score, reasons = SkillRouter()._tool_affinity(
        {"tools": ["opaque"]}, parse_intent("explain this code"), [], [], snapshot,
    )

    assert score == (-1.0 if expected_penalty else 0.0)
    assert any(reason.startswith("tool-scope-penalty:") for reason in reasons) is expected_penalty


def test_real_read_result_carries_descriptive_metadata(tmp_path):
    (tmp_path / "example.txt").write_text("example", encoding="utf-8")
    tools = create_default_tool_registry(str(tmp_path), include_mcp=False)

    result = tools.execute("read_file", {"path": "example.txt"}, ToolContext(cwd=str(tmp_path)))

    assert result.ok
    assert "example" in result.output
    assert result.capability["domain"] == "file"
    assert result.capability["effects"]["read_only"] is True


def test_false_readonly_declaration_cannot_bypass_real_write_permission(tmp_path):
    tools = create_default_tool_registry(str(tmp_path), include_mcp=False)
    # A separate definition keeps process singleton tools unchanged.
    tool = replace(
        tools.find("write_file"), capability_domain="file", capability_scope="readonly",
        capability_profile=CapabilityProfile(
            "file", "readonly", effects=CapabilityEffects(read_only=True, destructive=False),
        ),
    )
    permission_requests = []

    def deny(request):
        permission_requests.append(request)
        return {"decision": "deny"}

    context = ToolContext(
        cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path), prompt=deny),
    )
    result = ToolRegistry([tool]).execute("write_file", {"path": "denied.txt", "content": "no"}, context)

    assert not result.ok
    assert not (tmp_path / "denied.txt").exists()
    assert permission_requests
    assert tool.is_read_only is False
    assert tool.is_concurrency_safe is False


@pytest.mark.parametrize(
    ("name", "arguments", "valid"),
    [
        ("run_command", {"command": "ls", "args": [";", "rm", "-rf", "."]}, True),
        ("run_command", {"command": "ls", "args": "--help"}, False),
        ("tar_create", {"source": "a", "destination": "b", "mode": "list"}, False),
        ("zip_extract", {"source": "a.zip", "mode": "list"}, True),
        ("gzip_compress", {"source": "a"}, False),
    ],
)
def test_command_and_archive_profiles_obey_actual_wrapper_validation(
    tmp_path, monkeypatch, name, arguments, valid,
):
    monkeypatch.delenv("MINI_CODE_TOOL_PROFILE", raising=False)
    tools = create_default_tool_registry(
        str(tmp_path), runtime={"toolProfile": "full"}, include_mcp=False,
    )
    profile = tools.describe_capability(name, arguments)

    assert (profile.status is not CapabilityStatus.INVALID) is valid
    if not valid:
        assert all(value is None for value in profile.effects.to_dict().values())
    elif name == "run_command":
        assert profile.effects.read_only is None
        assert profile.effects.destructive is None
        assert profile.effects.executes_code is True
    else:
        # zip_extract ignores mode; it still writes extracted files.
        assert profile.effects.read_only is False
