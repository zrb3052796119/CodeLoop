from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import minicode.memory as memory_mod
import minicode.web.change_feed as change_feed_mod
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority
from minicode.mcp_current_state import McpCurrentStateRegistry
from minicode.permission_approval import PermissionApprovalBroker
from minicode.permissions import PermissionManager
from minicode.run_journal import stable_workspace_id
from minicode.turn_cancellation import TurnCancellationToken
from minicode.web.change_feed import DashboardChangeFeed


RESOURCE_NAMES = {
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
}


def _feed(tmp_path: Path, **kwargs) -> DashboardChangeFeed:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return DashboardChangeFeed(workspace, data_dir=tmp_path / "data", **kwargs)


def _revisions(feed: DashboardChangeFeed) -> dict[str, str]:
    return {
        name: resource["revision"]
        for name, resource in feed.snapshot()["resources"].items()
    }


def _assert_only_changed(
    before: dict[str, str], after: dict[str, str], expected: str
) -> None:
    assert {name for name in RESOURCE_NAMES if before[name] != after[name]} == {
        expected
    }


def test_change_feed_has_stable_content_free_public_contract(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    moments = iter(
        [
            datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone.utc),
            datetime(2026, 7, 19, 1, 2, 4, tzinfo=timezone.utc),
        ]
    )
    feed = DashboardChangeFeed(
        workspace,
        data_dir=data_dir,
        clock=lambda: next(moments),
    )

    first = feed.snapshot()
    second = feed.snapshot()

    assert first.keys() == {
        "schemaVersion",
        "generatedAt",
        "mode",
        "pollAfterMs",
        "resources",
        "diagnostics",
    }
    assert first["schemaVersion"] == 2
    assert first["mode"] == "read-only"
    assert first["pollAfterMs"] == 2000
    assert first["generatedAt"] != second["generatedAt"]
    assert first["resources"] == second["resources"]
    assert set(first["resources"]) == RESOURCE_NAMES
    assert first["diagnostics"] == []
    for name, resource in first["resources"].items():
        assert resource["status"] == (
            "unavailable" if name == "permissions" else "live"
        )
        assert re.fullmatch(r"rev_[0-9a-f]{64}", resource["revision"])


def test_permission_revision_is_hashed_and_changes_only_permissions(tmp_path) -> None:
    raw_revision = ["permissionrev_" + "a" * 32]
    feed = _feed(
        tmp_path,
        permission_revision_loader=lambda: raw_revision[0],
    )

    first_payload = feed.snapshot()
    first = {
        name: resource["revision"]
        for name, resource in first_payload["resources"].items()
    }
    raw_revision[0] = "permissionrev_" + "b" * 32
    second_payload = feed.snapshot()
    second = {
        name: resource["revision"]
        for name, resource in second_payload["resources"].items()
    }

    assert list(first_payload["resources"]) == [
        "runs",
        "sessions",
        "turns",
        "memory",
        "skills",
        "connections",
        "permissions",
    ]
    assert first_payload["resources"]["permissions"]["status"] == "live"
    assert raw_revision[0] not in json.dumps(first_payload)
    _assert_only_changed(first, second, "permissions")


@pytest.mark.parametrize("terminal", ["allow", "deny", "cancel", "timeout"])
def test_permission_lifecycle_invalidates_only_permissions(
    tmp_path, terminal: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + {"allow": "1", "deny": "2", "cancel": "3", "timeout": "4"}[terminal] * 32
    broker = PermissionApprovalBroker(
        workspace,
        timeout_seconds=0.04 if terminal == "timeout" else 2,
        poll_interval=0.005,
    )
    feed = DashboardChangeFeed(
        workspace,
        data_dir=tmp_path / "data",
        permission_revision_loader=broker.revision,
    )
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("write_file")
        try:
            manager.ensure_edit(
                str(workspace / "safe.txt"),
                "--- a/safe.txt\n+++ b/safe.txt\n@@ -0,0 +1 @@\n+safe",
            )
            outcome["allowed"] = True
        except BaseException as error:  # noqa: BLE001 - terminal cases are expected
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    before = _revisions(feed)
    worker = threading.Thread(target=prompt)
    worker.start()
    deadline = time.monotonic() + 1
    item = None
    while time.monotonic() < deadline:
        items = broker.snapshot()["items"]
        if items:
            item = items[0]
            break
        time.sleep(0.002)
    assert item is not None
    requested = _revisions(feed)
    _assert_only_changed(before, requested, "permissions")

    if terminal in {"allow", "deny"}:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="allow_once" if terminal == "allow" else "deny_once",
        )
    elif terminal == "cancel":
        broker.cancel_turn(turn_id)
    worker.join(timeout=1)
    assert not worker.is_alive()
    decided = _revisions(feed)
    _assert_only_changed(requested, decided, "permissions")
    assert (outcome.get("allowed") is True) is (terminal == "allow")
    session.close()
    broker.close()


def test_run_metadata_and_event_append_only_change_runs_revision(tmp_path) -> None:
    feed = _feed(tmp_path)
    before = _revisions(feed)
    run_root = (
        tmp_path
        / "data"
        / "dashboard"
        / "workspaces"
        / stable_workspace_id(tmp_path / "workspace")
        / "runs"
        / ("run_" + "a" * 32)
    )
    run_root.mkdir(parents=True)
    metadata = run_root / "metadata.json"
    metadata.write_text("not parsed and possibly corrupt", encoding="utf-8")
    after_metadata = _revisions(feed)
    _assert_only_changed(before, after_metadata, "runs")

    (run_root / "events.ndjson").write_bytes(b"\xff\x00")
    after_event = _revisions(feed)
    _assert_only_changed(after_metadata, after_event, "runs")


def test_session_base_delta_and_index_each_change_sessions_revision(tmp_path) -> None:
    feed = _feed(tmp_path)
    before = _revisions(feed)
    data_dir = tmp_path / "data"
    sessions = data_dir / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session-a.json").write_text("{}", encoding="utf-8")
    after_base = _revisions(feed)
    _assert_only_changed(before, after_base, "sessions")
    session_snapshot = feed.snapshot()
    assert session_snapshot["resources"]["sessions"]["status"] == "partial"
    assert any(
        item["code"] == "workspace_scope_conservative"
        for item in session_snapshot["diagnostics"]
    )

    delta_dir = sessions / "deltas" / "session-a"
    delta_dir.mkdir(parents=True)
    (delta_dir / "delta_0001.json").write_text("{}", encoding="utf-8")
    after_delta = _revisions(feed)
    _assert_only_changed(after_base, after_delta, "sessions")

    (data_dir / "sessions_index.json").write_text("corrupt", encoding="utf-8")
    after_index = _revisions(feed)
    _assert_only_changed(after_delta, after_index, "sessions")


def test_turn_update_only_changes_turns_revision(tmp_path) -> None:
    feed = _feed(tmp_path)
    before = _revisions(feed)
    root = (
        tmp_path
        / "data"
        / "dashboard"
        / "workspaces"
        / stable_workspace_id(tmp_path / "workspace")
        / "turns"
    )
    root.mkdir(parents=True)
    turn = root / ("turn_" + "b" * 32 + ".json")
    turn.write_text("accepted", encoding="utf-8")
    after_create = _revisions(feed)
    _assert_only_changed(before, after_create, "turns")

    turn.write_text("completed-with-more-bytes", encoding="utf-8")
    after_update = _revisions(feed)
    _assert_only_changed(after_create, after_update, "turns")


def test_only_legal_memory_files_change_memory_revision(tmp_path) -> None:
    feed = _feed(tmp_path)
    before = _revisions(feed)
    memory_root = tmp_path / "workspace" / ".mini-code-memory"
    memory_root.mkdir()
    (memory_root / "ignored.txt").write_text("secret ignored", encoding="utf-8")
    assert _revisions(feed) == before

    (memory_root / "MEMORY.md").write_text("private memory body", encoding="utf-8")
    after = _revisions(feed)
    _assert_only_changed(before, after, "memory")

    (memory_root / "approval_audit.json").write_text(
        '{"records": []}', encoding="utf-8"
    )
    after_audit = _revisions(feed)
    _assert_only_changed(after, after_audit, "memory")


def test_pending_and_decision_reuse_existing_memory_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_dir)
    feed = DashboardChangeFeed(workspace, data_dir=data_dir)
    before = _revisions(feed)
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Review change feed integration",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    pending = _revisions(feed)
    _assert_only_changed(before, pending, "memory")
    authority = MemoryApprovalAuthority(workspace)
    item = authority.snapshot()["items"][0]
    authority.decide(
        memory_id=entry.id,
        decision="approve",
        review_revision=item["reviewRevision"],
    )
    approved = _revisions(feed)
    _assert_only_changed(pending, approved, "memory")
    assert set(approved) == RESOURCE_NAMES


def test_only_legal_skill_summaries_change_skills_revision(tmp_path) -> None:
    feed = _feed(tmp_path)
    before = _revisions(feed)
    skills_root = tmp_path / "workspace" / ".mini-code" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "ignored.txt").write_text("secret ignored", encoding="utf-8")
    assert _revisions(feed) == before

    skill = skills_root / "alpha"
    skill.mkdir()
    (skill / "ordinary.txt").write_text("ignored", encoding="utf-8")
    without_summary = _revisions(feed)
    (skill / "SKILL.md").write_text("# private body", encoding="utf-8")
    after = _revisions(feed)
    _assert_only_changed(without_summary, after, "skills")


def test_windows_skill_scan_uses_paths_and_observes_skill_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = _feed(tmp_path)
    skills_root = tmp_path / "workspace" / ".mini-code" / "skills"
    skills_root.mkdir(parents=True)
    scanned: list[Path] = []
    original_scandir = change_feed_mod.os.scandir

    def tracked_scandir(path):
        assert not isinstance(path, int)
        scanned.append(Path(path))
        return original_scandir(path)

    monkeypatch.setattr(change_feed_mod, "_platform_name", lambda: "nt")
    monkeypatch.setattr(change_feed_mod.os, "scandir", tracked_scandir)
    before = _revisions(feed)

    skill = skills_root / "alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("# Alpha\n", encoding="utf-8")
    after = _revisions(feed)

    _assert_only_changed(before, after, "skills")
    assert skills_root in scanned


def test_connection_config_and_stable_process_state_change_connections(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    registry = McpCurrentStateRegistry(clock=lambda: now, token_factory=lambda: "one")
    feed = _feed(
        tmp_path,
        mcp_current_state_loader=lambda: registry.snapshot().to_dict(),
    )
    before = _revisions(feed)
    config = tmp_path / "workspace" / ".mcp.json"
    config.write_text("contains credentials but is never read", encoding="utf-8")
    after_config = _revisions(feed)
    _assert_only_changed(before, after_config, "connections")

    key = "mcpsrv_" + "c" * 32
    handle = registry.register(key, probe=lambda: True)
    assert handle is not None
    after_process = _revisions(feed)
    _assert_only_changed(after_config, after_process, "connections")


def test_volatile_mcp_timestamps_do_not_change_connections_revision(tmp_path) -> None:
    timestamps = iter(("2026-07-19T00:00:00Z", "2026-07-19T00:00:01Z"))

    def load() -> dict[str, object]:
        checked_at = next(timestamps)
        return {
            "schemaVersion": 1,
            "stateVersion": 1,
            "scope": "process",
            "current": "process-local",
            "checkedAt": checked_at,
            "servers": [],
            "coverage": {
                "scope": "gateway-process",
                "crossProcess": "unavailable",
                "heartbeat": False,
                "limited": False,
            },
            "diagnostics": [],
        }

    feed = _feed(tmp_path, mcp_current_state_loader=load)
    assert _revisions(feed) == _revisions(feed)


def test_workspace_persisted_resources_are_isolated(tmp_path) -> None:
    data_dir = tmp_path / "data"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = DashboardChangeFeed(first_workspace, data_dir=data_dir)
    second = DashboardChangeFeed(second_workspace, data_dir=data_dir)
    second_before = _revisions(second)
    run_root = (
        data_dir
        / "dashboard"
        / "workspaces"
        / stable_workspace_id(first_workspace)
        / "runs"
        / ("run_" + "d" * 32)
    )
    run_root.mkdir(parents=True)
    (run_root / "metadata.json").write_text("{}", encoding="utf-8")

    assert _revisions(first)["runs"]
    assert _revisions(second) == second_before


def test_unsafe_symlink_is_partial_and_never_exposes_source_details(tmp_path) -> None:
    feed = _feed(tmp_path)
    external = tmp_path / "outside-secret"
    external.mkdir()
    (external / "SKILL.md").write_text("Bearer secret-token", encoding="utf-8")
    skills_root = tmp_path / "workspace" / ".mini-code" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "escape").symlink_to(external, target_is_directory=True)

    payload = feed.snapshot()
    rendered = str(payload)

    assert payload["resources"]["skills"]["status"] == "partial"
    assert [item["code"] for item in payload["diagnostics"]] == ["unsafe_symlink"]
    assert "outside-secret" not in rendered
    assert "secret-token" not in rendered
    assert str(tmp_path) not in rendered


def test_scan_budget_is_bounded_partial_and_deterministic(tmp_path) -> None:
    feed = _feed(tmp_path, max_scan_entries=1)

    first = feed.snapshot()
    second = feed.snapshot()

    assert first["resources"] == second["resources"]
    assert any(resource["status"] == "partial" for resource in first["resources"].values())
    assert {item["code"] for item in first["diagnostics"]} == {
        "scan_limit_reached"
    }


def test_current_state_failure_is_isolated_to_connections(tmp_path) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("provider secret and machine path")

    payload = _feed(tmp_path, mcp_current_state_loader=fail).snapshot()

    assert payload["resources"]["connections"]["status"] == "partial"
    assert all(
        payload["resources"][name]["status"] == "live"
        for name in RESOURCE_NAMES - {"connections", "permissions"}
    )
    assert payload["resources"]["permissions"]["status"] == "unavailable"
    assert payload["diagnostics"] == [
        {
            "resource": "connections",
            "code": "current_state_unavailable",
            "message": "Current connection state is temporarily unavailable.",
        }
    ]


def test_permission_revision_failure_is_local_fixed_and_content_free(tmp_path) -> None:
    sensitive = "Bearer permission-secret /Users/private/workspace"

    def fail() -> str:
        raise RuntimeError(sensitive)

    payload = _feed(tmp_path, permission_revision_loader=fail).snapshot()
    rendered = json.dumps(payload)

    assert payload["resources"]["permissions"]["status"] == "error"
    assert all(
        payload["resources"][name]["status"] == "live"
        for name in RESOURCE_NAMES - {"permissions"}
    )
    assert payload["diagnostics"] == [
        {
            "resource": "permissions",
            "code": "permission_revision_unavailable",
            "message": "Permission change state is temporarily unavailable.",
        }
    ]
    assert sensitive not in rendered


def test_snapshot_never_reads_or_writes_persisted_content(tmp_path) -> None:
    feed = _feed(tmp_path)
    memory = tmp_path / "data" / "memory" / "memory.json"
    memory.parent.mkdir(parents=True)
    content = b"Bearer top-secret /Users/private prompt body\x00"
    memory.write_bytes(content)
    before = {
        path.relative_to(tmp_path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    rendered = json.dumps(feed.snapshot(), sort_keys=True)
    after = {
        path.relative_to(tmp_path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert before == after
    assert memory.read_bytes() == content
    assert "top-secret" not in rendered
    assert "prompt body" not in rendered
    assert str(tmp_path) not in rendered


def test_revision_is_independent_of_python_hash_seed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    memory = data_dir / "memory" / "memory.json"
    memory.parent.mkdir(parents=True)
    memory.write_text("private", encoding="utf-8")
    script = (
        "import json,sys;"
        "from minicode.web.change_feed import DashboardChangeFeed;"
        "print(json.dumps(DashboardChangeFeed(sys.argv[1], data_dir=sys.argv[2])"
        ".snapshot()['resources'], sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "987654"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script, str(workspace), str(data_dir)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
