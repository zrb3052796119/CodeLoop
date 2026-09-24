"""Regression tests for the 2026-09 tool-calling module review fixes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minicode.auto_mode import PermissionMode
from minicode.file_review import apply_reviewed_file_change
from minicode.local_tool_shortcuts import parse_local_tool_shortcut
from minicode.mcp import _authorize_mcp_tool_call
from minicode.permissions import PermissionDeniedError, PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.batch_ops import batch_copy_tool, batch_move_tool
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.list_files import list_files_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.task import _call_is_concurrency_safe
from minicode.tools.write_file import write_file_tool


def _allow_all_manager(tmp_path: Path) -> PermissionManager:
    return PermissionManager(
        str(tmp_path),
        prompt=lambda request: {"decision": "allow_once"},
    )


# ---------------------------------------------------------------------------
# batch_copy / batch_move overlap guards
# ---------------------------------------------------------------------------


def test_batch_copy_rejects_copy_onto_itself(tmp_path: Path) -> None:
    tree = tmp_path / "x"
    tree.mkdir()
    (tree / "keep.txt").write_text("data", encoding="utf-8")

    result = batch_copy_tool.run(
        {"source": "x", "destination": "x"},
        ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path)),
    )

    assert result.ok is False
    assert "same path" in result.output
    # The source must survive the rejected operation.
    assert (tree / "keep.txt").read_text(encoding="utf-8") == "data"


def test_batch_copy_rejects_destination_inside_source(tmp_path: Path) -> None:
    tree = tmp_path / "x"
    tree.mkdir()
    (tree / "keep.txt").write_text("data", encoding="utf-8")

    result = batch_copy_tool.run(
        {"source": "x", "destination": "x/sub"},
        ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path)),
    )

    assert result.ok is False
    assert "inside the source" in result.output
    assert (tree / "keep.txt").exists()


def test_batch_copy_rejects_source_inside_destination(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("data", encoding="utf-8")

    result = batch_copy_tool.run(
        {"source": "a/f.txt", "destination": "a"},
        ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path)),
    )

    assert result.ok is False
    assert "inside the destination" in result.output
    assert (tmp_path / "a" / "f.txt").exists()


def test_batch_move_rejects_destination_inside_source(tmp_path: Path) -> None:
    tree = tmp_path / "x"
    tree.mkdir()
    (tree / "keep.txt").write_text("data", encoding="utf-8")

    result = batch_move_tool.run(
        {"source": "x", "destination": "x/sub"},
        ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path)),
    )

    assert result.ok is False
    assert "inside the source" in result.output
    assert (tree / "keep.txt").exists()


# ---------------------------------------------------------------------------
# Read-modify-write conflict detection + atomic write
# ---------------------------------------------------------------------------


def test_edit_file_reports_conflict_when_file_changed_underneath(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("original", encoding="utf-8")
    context = ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path))

    # Simulate a concurrent writer touching the file after the edit's base
    # read but before the reviewed write lands.
    import minicode.tools.edit_file as edit_file_module

    real_load = edit_file_module.load_existing_file

    def racing_load(path):
        content = real_load(path)
        if Path(path).name == "f.txt" and "original" in content:
            # External mutation between the edit's read and the write.
            Path(path).write_text("changed elsewhere", encoding="utf-8")
        return content

    edit_file_module.load_existing_file = racing_load
    try:
        result = edit_file_tool.run(
            {
                "path": "f.txt",
                "search": "original",
                "replace": "edited",
                "replace_all": False,
                "fuzzy": False,
            },
            context,
        )
    finally:
        edit_file_module.load_existing_file = real_load

    assert result.ok is False
    assert "error[edit_conflict]" in result.output
    assert target.read_text(encoding="utf-8") == "changed elsewhere"


def test_write_file_is_atomic_and_preserves_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o644)

    result = write_file_tool.run(
        {"path": "f.txt", "content": "new content"},
        ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path)),
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "new content"
    assert (os.stat(target).st_mode & 0o777) == 0o644
    # No temp-file litter next to the target.
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# read_file windowed reads
# ---------------------------------------------------------------------------


def test_read_file_window_does_not_need_whole_file(tmp_path: Path) -> None:
    content = "abcdef" * 60_000  # 360 KB, above the whole-file cache cap
    target = tmp_path / "big.txt"
    target.write_text(content, encoding="utf-8")

    result = read_file_tool.run(
        {"path": "big.txt", "offset": 100, "limit": 50},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert "TRUNCATED: yes" in result.output
    assert content[100:150] in result.output


def test_read_file_small_file_reports_exact_total(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("hello", encoding="utf-8")

    result = read_file_tool.run(
        {"path": "small.txt", "offset": 0, "limit": 8000},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert "TOTAL_CHARS: 5" in result.output
    assert "TRUNCATED: no" in result.output


# ---------------------------------------------------------------------------
# MCP permission gating
# ---------------------------------------------------------------------------


def test_mcp_call_blocked_without_permission_manager(tmp_path: Path) -> None:
    with pytest.raises(PermissionDeniedError):
        _authorize_mcp_tool_call(
            ToolContext(cwd=str(tmp_path)),
            tool_id="mcp__srv__write",
            server_name="srv",
            tool_name="write",
            declared_read_only=False,
        )


def test_mcp_declared_read_only_passes_without_manager(tmp_path: Path) -> None:
    # Must not raise.
    _authorize_mcp_tool_call(
        ToolContext(cwd=str(tmp_path)),
        tool_id="mcp__srv__lookup",
        server_name="srv",
        tool_name="lookup",
        declared_read_only=True,
    )


def test_mcp_plan_mode_blocks_undeclared_tool(tmp_path: Path) -> None:
    permissions = PermissionManager(str(tmp_path), auto_mode=PermissionMode.PLAN)
    with pytest.raises(PermissionDeniedError):
        permissions.ensure_mcp_tool(
            "mcp__srv__write",
            server_name="srv",
            tool_name="write",
            declared_read_only=False,
        )


def test_mcp_prompt_allow_once_caches_session_decision(tmp_path: Path) -> None:
    prompts: list[dict] = []

    def recording_prompt(request: dict) -> dict:
        prompts.append(request)
        return {"decision": "allow_once"}

    permissions = PermissionManager(str(tmp_path), prompt=recording_prompt)
    context = ToolContext(cwd=str(tmp_path), permissions=permissions)
    call = {
        "tool_id": "mcp__srv__write",
        "server_name": "srv",
        "tool_name": "write",
        "declared_read_only": False,
    }

    _authorize_mcp_tool_call(context, **call)
    _authorize_mcp_tool_call(context, **call)

    assert len(prompts) == 1  # second call reused the session decision


def test_mcp_deny_once_blocks_next_call(tmp_path: Path) -> None:
    decisions = iter([{"decision": "deny_once"}, {"decision": "allow_once"}])
    permissions = PermissionManager(str(tmp_path), prompt=lambda request: next(decisions))
    context = ToolContext(cwd=str(tmp_path), permissions=permissions)
    call = {
        "tool_id": "mcp__srv__write",
        "server_name": "srv",
        "tool_name": "write",
        "declared_read_only": False,
    }

    with pytest.raises(PermissionDeniedError):
        _authorize_mcp_tool_call(context, **call)
    # Denied once stays denied for the session despite a would-be allow.
    with pytest.raises(PermissionDeniedError):
        _authorize_mcp_tool_call(context, **call)


# ---------------------------------------------------------------------------
# task cancel serial execution
# ---------------------------------------------------------------------------


def test_task_cancel_is_not_concurrency_safe() -> None:
    assert _call_is_concurrency_safe({"action": "poll", "subagent_id": "x"}) is True
    assert _call_is_concurrency_safe({"action": "cancel", "subagent_id": "x"}) is False


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def test_ls_shortcut_requires_word_boundary() -> None:
    assert parse_local_tool_shortcut("/ls") is not None
    assert parse_local_tool_shortcut("/ls foo") is not None
    assert parse_local_tool_shortcut("/lsfoo") is None


def test_nat64_wells_cannot_reach_private_ipv4() -> None:
    from minicode.tools.network_safety import NetworkSafetyError, _public_address

    # 64:ff9b::/96 translates to an arbitrary IPv4 literal at the network
    # edge; the embedded private targets must stay blocked regardless of
    # how the running Python's ipaddress classifies the prefix itself.
    for private_nat64 in ("64:ff9b::a00:1", "64:ff9b:1::a00:1"):
        with pytest.raises(NetworkSafetyError):
            _public_address(private_nat64)


def test_list_files_prompts_once_per_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("a", encoding="utf-8")
    (outside / "b.txt").write_text("b", encoding="utf-8")

    permission_calls: list[tuple[str, str]] = []

    class RecordingPermissions(PermissionManager):
        def ensure_path_access(self, target_path: str, intent: str) -> None:
            permission_calls.append((target_path, intent))
            return super().ensure_path_access(target_path, intent)

    permissions = RecordingPermissions(
        str(tmp_path), prompt=lambda request: {"decision": "allow_once"}
    )
    result = list_files_tool.run(
        {"path": str(outside)},
        ToolContext(cwd=str(tmp_path), permissions=permissions),
    )

    assert result.ok is True
    assert "a.txt" in result.output
    assert "b.txt" in result.output
    # Exactly one approval (the directory itself), never one per child.
    assert len(permission_calls) == 1


def test_apply_reviewed_change_detects_approval_window_conflict(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("v1", encoding="utf-8")

    original = apply_reviewed_file_change

    def racing_apply(context, file_path, target_path, next_content, **kwargs):
        # File changes after the diff is built but before the write lands.
        result = None

        class RacingManager:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def ensure_edit(self, path, diff):
                Path(path).write_text("external change", encoding="utf-8")
                return self._real.ensure_edit(path, diff)

        context.permissions = RacingManager(context.permissions)
        result = original(context, file_path, target_path, next_content, **kwargs)
        return result

    context = ToolContext(cwd=str(tmp_path), permissions=_allow_all_manager(tmp_path))
    import minicode.tools.write_file as write_file_module

    real_apply = write_file_module.apply_reviewed_file_change
    write_file_module.apply_reviewed_file_change = racing_apply
    try:
        result = write_file_module.write_file_tool.run(
            {"path": "f.txt", "content": "v2"},
            context,
        )
    finally:
        write_file_module.apply_reviewed_file_change = real_apply

    assert result.ok is False
    assert "error[edit_conflict]" in result.output
    assert target.read_text(encoding="utf-8") == "external change"
