"""Executor search must use the existing Assignment path authority."""

from pathlib import Path

import pytest

from minicode.permissions import PermissionDeniedError
from minicode.scheduler_permissions import SchedulerExecutorPermissions
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.task import task_tool
from minicode.turn_cancellation import TurnCancellationRequested


def _context(workspace: Path, *, read_roots=("src",), checkpoint=None):
    return ToolContext(
        cwd=str(workspace),
        permissions=SchedulerExecutorPermissions(
            str(workspace), read_roots=read_roots, write_paths=("src/app.py",),
            forbidden_paths=("src/private", "src/private.py"),
            operation_checkpoint=checkpoint,
        ),
    )


def _grep(context: ToolContext, path: str, **options):
    return grep_files_tool.run(
        grep_files_tool.validator({"path": path, "pattern": "MARKER", **options}),
        context,
    )


def _sources(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    (source / "app.py").write_text("before\nPUBLIC_MARKER = 1\nafter\n", encoding="utf-8")
    private = source / "private"
    private.mkdir()
    for target in (
        source / "private.py", source / "credentials.json", private / "app.py",
        source / "secrets.json",
    ):
        target.write_text("PRIVATE_MARKER\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "foreign.py").write_text("OUTSIDE_MARKER\n", encoding="utf-8")
    (source / "foreign.py").symlink_to(outside / "foreign.py")
    (source / "foreign-dir").symlink_to(outside, target_is_directory=True)
    (workspace / "other.py").write_text("OTHER_SCOPE_MARKER\n", encoding="utf-8")
    return workspace, outside


def test_recursive_executor_search_skips_denied_files_before_reading(tmp_path, monkeypatch):
    workspace, _outside = _sources(tmp_path)
    read_paths = []
    original = Path.read_text

    def record_read(path, *args, **kwargs):
        read_paths.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record_read)
    result = _grep(_context(workspace), "src", context_lines=1)

    assert result.ok
    assert "app.py:2: PUBLIC_MARKER = 1" in result.output
    assert "app.py:1:  before" in result.output
    assert "app.py:3:  after" in result.output
    for marker in ("PRIVATE_MARKER", "OUTSIDE_MARKER", "OTHER_SCOPE_MARKER", "credentials", "secrets"):
        assert marker not in result.output
    assert read_paths == [(workspace / "src/app.py").resolve()]


@pytest.mark.parametrize("relative", [
    "src/private.py", "src/private", "src/secrets.json", "src/credentials.json",
    "src/foreign.py", "src/foreign-dir", "other.py", "../outside",
])
def test_explicit_forbidden_or_out_of_scope_search_fails(tmp_path, relative):
    workspace, _outside = _sources(tmp_path)
    result = _grep(_context(workspace), relative)
    assert not result.ok
    assert "error[permission_denied]" in result.output
    assert "MARKER" not in result.output


def test_no_permission_manager_still_blocks_recursive_escape_symlinks(tmp_path):
    workspace, _outside = _sources(tmp_path)
    result = _grep(ToolContext(cwd=str(workspace)), "src")
    assert result.ok
    assert "PUBLIC_MARKER" in result.output
    assert "OUTSIDE_MARKER" not in result.output


def test_executor_search_keeps_file_globs_and_does_not_grant_edits(tmp_path):
    workspace, _outside = _sources(tmp_path)
    context = _context(workspace)
    assert "PUBLIC_MARKER" in _grep(context, "src/app.py", include="*.py").output
    assert _grep(context, "src/app.py", exclude="*.py").output == "No matches found."
    with pytest.raises(PermissionDeniedError):
        context.permissions.ensure_edit(str(workspace / "other.py"), "diff")
    with pytest.raises(PermissionDeniedError):
        context.permissions.ensure_command("python", [], str(workspace))


def test_search_does_not_swallow_operation_cancellation(tmp_path):
    workspace, _outside = _sources(tmp_path)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise TurnCancellationRequested("turn_" + "a" * 32)

    with pytest.raises(TurnCancellationRequested):
        _grep(_context(workspace, checkpoint=checkpoint), "src")


def test_production_executor_registry_exposes_guarded_search_only(tmp_path, monkeypatch):
    import minicode.tools.task as task_module

    workspace, _outside = _sources(tmp_path)
    observed = []

    def capture_turn(**kwargs):
        tools = kwargs["tools"]
        context = ToolContext(cwd=str(workspace), permissions=kwargs["permissions"])
        assert isinstance(context.permissions, SchedulerExecutorPermissions)
        assert tools.find("grep_files") is not None
        for unavailable in ("task", "run_command", "test_runner", "web_fetch", "ask_user"):
            assert tools.find(unavailable) is None
        result = tools.execute("grep_files", {"path": "src", "pattern": "MARKER"}, context)
        assert result.ok and "PUBLIC_MARKER" in result.output
        assert "PRIVATE_MARKER" not in result.output and "OUTSIDE_MARKER" not in result.output
        denied = tools.execute("grep_files", {"path": "other.py", "pattern": "MARKER"}, context)
        assert not denied.ok and "OTHER_SCOPE_MARKER" not in denied.output
        observed.append(result.output)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "Searched assigned source."}]

    monkeypatch.setattr(task_module, "run_agent_turn", capture_turn)
    result = task_tool.run(
        {"description": "scheduled fix", "prompt": "Perform the locked Assignment.", "agent_type": "general"},
        ToolContext(
            cwd=str(workspace), _runtime={"model": "fake"},
            _logical_agent_role="executor",
            _logical_agent_workspace_root=str(workspace),
            _logical_agent_scope={
                "read_roots": ("src",), "write_paths": ("src/app.py",),
                "forbidden_paths": ("src/private", "src/private.py"),
            },
        ),
    )
    assert result.ok, result.output
    assert len(observed) == 1
