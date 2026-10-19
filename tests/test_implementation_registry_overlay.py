"""Implementation feedback preserves the registry's bindings and owner."""

from dataclasses import replace

import pytest

from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools import create_default_tool_registry


def _feedback():
    return ExecutorCheckCapability(
        [VerificationCheck("tests", "tests", ("pytest", "-q"), 10)],
        lambda *_a: {"status": "passed"}, ["tests/test_regression.py"],
    )


def _proposal(registry, root):
    result = registry.execute("propose_skill", {
        "name": "implementation-checks",
        "description": "Run approved checks for an implementation.",
        "tools": ["executor_check"], "domains": ["execution"], "scopes": ["write"],
    }, ToolContext(cwd=str(root)))
    assert result.ok, result.output
    return result.output


def test_feedback_rebinds_real_proposal_to_its_actual_tool_surface(tmp_path):
    owner = create_default_tool_registry(str(tmp_path), include_mcp=False)
    original_edit = owner.find("edit_file")
    bound = _feedback().bind_registry(owner)
    assert "- executor_check: known (execution/write)" in _proposal(bound, tmp_path)
    assert "- executor_check: unknown" in _proposal(owner, tmp_path)
    assert bound.find("propose_skill") is not owner.find("propose_skill")
    assert owner.find("edit_file") is original_edit
    assert bound.find("edit_file") is not original_edit


def test_feedback_keeps_disk_skill_refresh_and_file_tool_wrappers(tmp_path):
    owner = create_default_tool_registry(str(tmp_path), include_mcp=False)
    bound = _feedback().bind_registry(owner)
    wrapped_edit = bound.find("edit_file")
    old_skills = owner.get_skills()
    path = tmp_path / ".mini-code" / "skills" / "overlay-regression" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: overlay-regression\ndescription: New test catalog entry\n---\nInspect the tests.\n")
    assert bound.refresh_skill_catalog() is True
    assert any(skill["name"] == "overlay-regression" for skill in bound.get_skills())
    assert owner.get_skills() == old_skills
    assert bound.find("edit_file") is wrapped_edit
    assert "- executor_check: known (execution/write)" in _proposal(bound, tmp_path)
    assert bound.refresh_skill_catalog() is False


def _tool(name, output="original"):
    return ToolDefinition(name=name, description=name, input_schema={"type": "object"},
                          validator=lambda values: values,
                          run=lambda values, context: ToolResult(ok=True, output=output))


def test_only_owner_disposes_shared_resources_even_after_nested_overlay_and_filter(monkeypatch):
    disposed = []
    monkeypatch.setattr("minicode.background_tasks.shutdown_background_tasks", lambda: disposed.append("background"))
    owner = ToolRegistry([_tool("edit_file")], disposer=lambda: disposed.append("mcp"))
    overlay = owner.overlay([_tool("executor_check")])
    nested = overlay.overlay([_tool("executor_test")])
    filtered = nested.filtered(lambda _tool: True)
    for borrowed in (overlay, nested, filtered):
        borrowed.dispose()
    assert disposed == []
    owner.dispose()
    assert disposed == ["background", "mcp"]


def test_overlay_replacements_and_refresh_do_not_resurrect_removed_tools():
    versions = ["v1"]
    def skills():
        return [{"name": versions[0]}], [_tool("load_skill", versions[0])]
    owner = ToolRegistry([_tool("edit_file"), _tool("load_skill"), _tool("executor_test")],
                         skills=skills()[0], skill_catalog_factory=skills)
    replacement = replace(owner.find("edit_file"), description="wrapped writer")
    overlay = owner.overlay([replacement, _tool("executor_check")],
                            remove_names=frozenset({"load_skill", "executor_test"}))
    versions[0] = "v2"
    assert overlay.refresh_skill_catalog() is True
    assert overlay.find("load_skill") is None
    assert overlay.find("executor_test") is None
    assert overlay.find("edit_file") is replacement
    assert owner.find("edit_file").description == "edit_file"
    assert owner.find("load_skill") is not None
    assert owner.get_skills() == [{"name": "v1"}]


def test_overlay_rejects_ambiguous_duplicate_replacements_without_disposing_owner(monkeypatch):
    disposed = []
    monkeypatch.setattr("minicode.background_tasks.shutdown_background_tasks", lambda: disposed.append("background"))
    owner = ToolRegistry([_tool("edit_file")], disposer=lambda: disposed.append("mcp"))
    with pytest.raises(ValueError, match="duplicate"):
        owner.overlay([_tool("edit_file"), _tool("edit_file")])
    assert disposed == []
