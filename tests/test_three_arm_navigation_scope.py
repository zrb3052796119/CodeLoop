"""Restored navigation tools retain evaluator and production role boundaries."""

from pathlib import Path

import pytest

import minicode.subagent_model_routing as routing
import minicode.tools as tool_module
import minicode.tools.task as task_module
from minicode.tooling import ToolContext
from scripts import run_three_arm_real as live


NAVIGATION = frozenset({
    "file_line_count", "get_ast_info", "find_symbols", "find_references",
})
FROZEN = ("python3", "-m", "pytest", "-q", "tests/test_visible.py")
EXTERNAL_MARKER = "EXTERNAL_NAVIGATION_SECRET"
SOURCE = "import math\n\ndef public_symbol(value):\n    return math.floor(value)\n"


class _PermissiveHost:
    # Keep the test's denial attributable to the evaluator guard, rather than
    # the production fallback that also normally denies absolute outside paths.
    def ensure_path_access(self, *args, **kwargs):
        return None


@pytest.fixture
def navigation(tmp_path):
    workspace = tmp_path / "workspace"
    package = workspace / "package"
    package.mkdir(parents=True)
    (package / "source.py").write_text(SOURCE)
    outside = tmp_path / "private"
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text(
        f"import {EXTERNAL_MARKER}\n"
        f"def public_symbol():\n    '''{EXTERNAL_MARKER}'''\n    return 1\n"
    )
    owner = tool_module.create_default_tool_registry(
        str(workspace), runtime={"model": "fake"}, include_mcp=False,
        include_user_interaction=False,
    )
    try:
        yield workspace, secret, owner
    finally:
        owner.dispose()


def _registry(owner, arm="auto"):
    return live.guard_registry(
        live.independent_registry(owner, arm), live.VerificationScope(FROZEN),
    )


def _input(name, path):
    data = {"file_path" if name == "get_ast_info" else "path": str(path)}
    if name == "find_references":
        data["symbol_name"] = "public_symbol"
    return data


def _invoke(registry, name, path, workspace):
    tool = registry.find(name)
    assert tool is not None, f"restored navigation tool is missing: {name}"
    return tool.run(
        tool.validator(_input(name, path)),
        ToolContext(cwd=str(workspace), permissions=_PermissiveHost()),
    )


@pytest.mark.parametrize("arm", live.ARMS)
def test_all_arms_share_navigation_but_explicit_parent_remains_coordinator(
    navigation, arm,
):
    workspace, _, owner = navigation
    originals = {name: owner.find(name).run for name in NAVIGATION}
    registry = _registry(owner, arm)
    assert NAVIGATION <= set(registry.list_all())
    parent = live.coordinator_registry(registry, arm)
    if arm == "explicit":
        assert set(parent.list_all()) == {"task", "todo_write"}
    else:
        assert NAVIGATION <= set(parent.list_all())
    assert (parent.find("task") is None) is (arm == "single")
    for name in NAVIGATION:
        result = _invoke(registry, name, "package/source.py", workspace)
        assert result.ok, result.output
        if name == "file_line_count":
            assert "LINES: 4\n" in result.output
        elif name == "get_ast_info":
            assert "Functions: 1" in result.output and "import math" in result.output
        else:
            assert "public_symbol" in result.output
    assert all(owner.find(name).run is run for name, run in originals.items())


@pytest.mark.parametrize("name", sorted(NAVIGATION))
def test_navigation_denies_external_files_and_file_symlinks_before_reading(
    navigation, monkeypatch, name,
):
    workspace, secret, owner = navigation
    escape = workspace / "escape.py"
    escape.symlink_to(secret)
    original_read = Path.read_text
    reads = []

    def observed_read(path, *args, **kwargs):
        if path.resolve() == secret.resolve():
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observed_read)
    registry = _registry(owner)
    for target in (secret, escape):
        result = _invoke(registry, name, target, workspace)
        assert not result.ok and "evaluation_scope_denied" in result.output
        assert EXTERNAL_MARKER not in result.output
    assert reads == []


@pytest.mark.parametrize("name", ["find_symbols", "find_references"])
@pytest.mark.parametrize("escape_kind", ["absolute_directory", "directory_link",
                                        "nested_file_link", "nested_directory_link"])
def test_recursive_navigation_checks_all_descendant_symlinks(
    navigation, monkeypatch, name, escape_kind,
):
    workspace, secret, owner = navigation
    if escape_kind == "absolute_directory":
        target = secret.parent
    elif escape_kind == "directory_link":
        target = workspace / "linked"
        target.symlink_to(secret.parent, target_is_directory=True)
    else:
        nested = workspace / "package" / ".nested"
        nested.mkdir()
        if escape_kind == "nested_file_link":
            (nested / "secret.py").symlink_to(secret)
        else:
            (nested / "linked").symlink_to(secret.parent, target_is_directory=True)
        target = workspace / "package"
    original_read = Path.read_text
    reads = []

    def observed_read(path, *args, **kwargs):
        if path.resolve() == secret.resolve():
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observed_read)
    result = _invoke(_registry(owner), name, target, workspace)
    assert not result.ok and "evaluation_scope_denied" in result.output
    assert EXTERNAL_MARKER not in result.output and reads == []


@pytest.mark.parametrize("name", ["file_line_count", "get_ast_info"])
def test_single_file_navigation_does_not_scan_unrelated_sibling_links(navigation, name):
    workspace, secret, owner = navigation
    (workspace / "package" / "escape.py").symlink_to(secret)
    result = _invoke(_registry(owner), name, "package/source.py", workspace)
    assert result.ok, result.output


@pytest.mark.parametrize("kind, role, expected", [
    ("plan", None, {"get_ast_info", "find_symbols", "find_references"}),
    ("explore", None, {"get_ast_info", "find_symbols", "find_references"}),
    ("tiny-reader", None, {"find_symbols"}),
    ("plan", "analyst", {"file_line_count", "get_ast_info"}),
    ("general", "executor", {"file_line_count", "get_ast_info"}),
])
def test_real_task_role_filter_is_not_widened_by_restored_host_tools(
    navigation, monkeypatch, kind, role, expected,
):
    workspace, _, _ = navigation
    if kind == "tiny-reader":
        profile = workspace / ".mini-code" / "agents" / "tiny-reader.md"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "---\nname: tiny-reader\ndescription: Inspect symbols.\n"
            "max_turns: 2\nallowed_tools: find_symbols\n---\nInspect symbols.\n"
        )
    original_factory = tool_module.create_default_tool_registry
    owners = []
    captured = []

    def evaluator_factory(*args, **kwargs):
        kwargs.update(include_mcp=False, include_user_interaction=False)
        owner = original_factory(*args, **kwargs)
        owners.append(owner)
        return _registry(owner, "explicit")

    class RegistryCaptured(Exception):
        pass

    def capture_before_model(*args, tools, **kwargs):
        captured.append(set(tools.list_all()))
        raise RegistryCaptured  # No provider, prompt routing, or model call.

    monkeypatch.setattr(tool_module, "create_default_tool_registry", evaluator_factory)
    monkeypatch.setattr(routing, "create_subagent_model_adapter", capture_before_model)
    context = ToolContext(
        cwd=str(workspace), _runtime={"model": "fake"},
        _logical_agent_role=role,
    )
    try:
        with pytest.raises(RegistryCaptured):
            task_module._run({
                "agent_type": kind, "description": "inspect navigation",
                "prompt": "Inspect the local module using your assigned tools.",
            }, context)
    finally:
        for owner in owners:
            owner.dispose()
    assert len(captured) == 1
    assert captured[0] & NAVIGATION == expected
    assert "task" not in captured[0] and "run_command" not in captured[0]
    if role != "executor":
        assert not ({"write_file", "edit_file"} & captured[0])
