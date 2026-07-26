from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest


def _workspace_id(path: Path) -> str:
    return "ws_" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _store(snapshot: dict[str, object], store_id: str) -> dict[str, object]:
    return next(store for store in snapshot["stores"] if store["id"] == store_id)


def test_empty_roots_return_a_strict_read_only_inventory_without_creating_files(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "home" / ".mini-code"
    workspace.mkdir()
    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))

    snapshot = PersistenceHealthReader(
        workspace,
        data_dir=data_dir,
    ).snapshot()

    assert snapshot["schemaVersion"] == 1
    assert snapshot["mode"] == "read-only"
    assert snapshot["status"] == "live"
    assert snapshot["workspace"]["name"] == "workspace"
    assert snapshot["workspace"]["id"].startswith("ws_")
    assert snapshot["summary"] == {
        "storeCount": 25,
        "knownRecordCount": 0,
        "knownByteCount": 0,
        "issueCount": 0,
    }
    assert [store["id"] for store in snapshot["stores"]] == [
        "sessions",
        "conversation-turns",
        "run-journal",
        "deletion-coordination",
        "memory-user",
        "memory-project",
        "memory-local",
        "memory-approval-user",
        "memory-approval-project",
        "memory-approval-local",
        "memory-pipeline-state",
        "tool-results",
        "permissions",
        "configuration",
        "mcp-configuration",
        "user-profile",
        "project-profile",
        "skills-user",
        "skills-project",
        "user-runtime-artifacts",
        "workspace-runtime-artifacts",
        "permission-broker",
        "mcp-current-registry",
        "gateway-runtime",
        "working-memory",
    ]
    assert snapshot["maintenancePlan"]["status"] == "planning"
    assert snapshot["maintenancePlan"]["destructiveActionsAvailable"] is False
    assert snapshot["maintenancePlan"]["blockers"] == []
    assert snapshot["diagnostics"] == []
    assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) < 256 * 1024
    assert (
        tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
        == before
    )


@pytest.mark.parametrize("value", [True, -1, (2**53), 1.25])
def test_strict_snapshot_validator_rejects_unsafe_count_values(value: object) -> None:
    from minicode.storage_health import (
        PersistenceHealthContractError,
        validate_persistence_health_snapshot,
    )

    payload = {
        "schemaVersion": 1,
        "generatedAt": "2026-07-23T00:00:00.000Z",
        "mode": "read-only",
        "status": "live",
        "workspace": {"id": "ws_0123456789abcdef", "name": "workspace"},
        "summary": {
            "storeCount": value,
            "knownRecordCount": 0,
            "knownByteCount": 0,
            "issueCount": 0,
        },
        "stores": [],
        "maintenancePlan": {
            "status": "planning",
            "destructiveActionsAvailable": False,
            "eligibleStoreIds": [],
            "excludedStoreIds": [],
            "blockers": [],
        },
        "diagnostics": [],
    }

    with pytest.raises(PersistenceHealthContractError):
        validate_persistence_health_snapshot(payload)


def test_workspace_persistence_counts_are_isolated_and_content_is_not_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    other_workspace = (tmp_path / "other").resolve()
    data_dir = tmp_path / "home" / ".mini-code"
    workspace.mkdir(parents=True)
    other_workspace.mkdir()
    sessions_dir = data_dir / "sessions"
    deltas_dir = sessions_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    current_session = {
        "session_id": "session-current",
        "workspace": str(workspace),
        "persistence_generation": 2,
        "messages": [{"role": "user", "content": "SESSION_SECRET_BODY"}],
    }
    foreign_session = {
        "session_id": "session-foreign",
        "workspace": str(other_workspace),
        "persistence_generation": 1,
        "messages": [{"role": "user", "content": "FOREIGN_SECRET_BODY"}],
    }
    current_session_path = sessions_dir / "session-current.json"
    current_session_path.write_text(
        json.dumps(current_session), encoding="utf-8"
    )
    foreign_session_path = sessions_dir / "session-foreign.json"
    foreign_session_path.write_text(
        json.dumps(foreign_session), encoding="utf-8"
    )
    sessions_index_path = data_dir / "sessions_index.json"
    sessions_index_path.write_text(
        json.dumps(
            {
                "session-current": {
                    "session_id": "session-current",
                    "workspace": str(workspace),
                },
                "session-foreign": {
                    "session_id": "session-foreign",
                    "workspace": str(other_workspace),
                },
            }
        ),
        encoding="utf-8",
    )
    current_delta_dir = deltas_dir / "session-current"
    current_delta_dir.mkdir()
    current_delta_path = current_delta_dir / "delta_0000.json"
    current_delta_path.write_text(
        json.dumps(
            {
                "session_id": "session-current",
                "persistence_generation": 2,
                "messages": [{"role": "assistant", "content": "DELTA_SECRET_BODY"}],
            }
        ),
        encoding="utf-8",
    )

    workspace_id = _workspace_id(workspace)
    turns_root = data_dir / "dashboard" / "workspaces" / workspace_id / "turns"
    turns_root.mkdir(parents=True)
    turn_id = "turn_" + ("a" * 32)
    (turns_root / f"{turn_id}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "turnId": turn_id,
                "workspaceId": workspace_id,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    runs_root = data_dir / "dashboard" / "workspaces" / workspace_id / "runs"
    run_id = "run_" + ("b" * 32)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "id": run_id,
                "workspaceId": workspace_id,
                "title": "RUN_SECRET_TITLE",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.ndjson").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runId": run_id,
                "workspaceId": workspace_id,
                "payload": {"toolInput": "TOOL_SECRET_BODY"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (runs_root / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "workspaceId": workspace_id,
                "runIds": [run_id],
            }
        ),
        encoding="utf-8",
    )

    memory_roots = {
        "user": data_dir / "memory",
        "project": workspace / ".mini-code-memory",
        "local": workspace / ".mini-code-memory-local",
    }
    for scope, root in memory_roots.items():
        root.mkdir(parents=True)
        memory_id = f"{scope}-entry"
        (root / "memory.json").write_text(
            json.dumps(
                {
                    "scope": scope,
                    "entries": [
                        {
                            "id": memory_id,
                            "scope": scope,
                            "content": f"{scope.upper()}_MEMORY_SECRET",
                            "related_to": [],
                            "approval_status": "approved",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / "approval_audit.json").write_text(
            json.dumps(
                {
                    "scope": scope,
                    "records": [{"entry_id": memory_id, "action": "write"}],
                }
            ),
            encoding="utf-8",
        )

    reader = PersistenceHealthReader(workspace, data_dir=data_dir)
    original_read_file = reader._read_file

    def guarded_read(path: Path, **kwargs: object) -> bytes | None:
        assert path != foreign_session_path
        return original_read_file(path, **kwargs)

    monkeypatch.setattr(reader, "_read_file", guarded_read)
    snapshot = reader.snapshot()

    assert snapshot["status"] == "live"
    assert _store(snapshot, "sessions")["recordCount"] == 1
    assert _store(snapshot, "sessions")["byteCount"] == sum(
        path.stat().st_size
        for path in (
            sessions_index_path,
            current_session_path,
            current_delta_path,
        )
    )
    assert _store(snapshot, "conversation-turns")["recordCount"] == 1
    assert _store(snapshot, "run-journal")["recordCount"] == 1
    assert _store(snapshot, "memory-user")["recordCount"] == 1
    assert _store(snapshot, "memory-project")["recordCount"] == 1
    assert _store(snapshot, "memory-local")["recordCount"] == 1
    assert _store(snapshot, "memory-approval-user")["recordCount"] == 1
    assert _store(snapshot, "memory-approval-project")["recordCount"] == 1
    assert _store(snapshot, "memory-approval-local")["recordCount"] == 1
    assert _store(snapshot, "permissions")["scope"] == "user"
    assert _store(snapshot, "configuration")["durability"] == "source"
    assert _store(snapshot, "permission-broker")["durability"] == "process-local"
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for hidden in (
        str(tmp_path),
        "SESSION_SECRET_BODY",
        "FOREIGN_SECRET_BODY",
        "DELTA_SECRET_BODY",
        "RUN_SECRET_TITLE",
        "TOOL_SECRET_BODY",
        "USER_MEMORY_SECRET",
        "PROJECT_MEMORY_SECRET",
        "LOCAL_MEMORY_SECRET",
    ):
        assert hidden not in serialized


def test_corrupt_json_and_ndjson_degrade_only_the_affected_stores(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    workspace.mkdir()
    workspace_id = _workspace_id(workspace)
    sessions = data_dir / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "broken.json").write_text("{", encoding="utf-8")
    project_memory = workspace / ".mini-code-memory"
    project_memory.mkdir()
    (project_memory / "memory.json").write_text("{", encoding="utf-8")
    run_id = "run_" + ("c" * 32)
    run_dir = data_dir / "dashboard" / "workspaces" / workspace_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "id": run_id,
                "workspaceId": workspace_id,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.ndjson").write_text(
        '{"schemaVersion":1}\n{\n',
        encoding="utf-8",
    )

    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    assert snapshot["status"] == "partial"
    assert _store(snapshot, "sessions")["status"] == "partial"
    assert _store(snapshot, "run-journal")["status"] == "partial"
    assert _store(snapshot, "memory-project")["status"] == "partial"
    assert _store(snapshot, "memory-user")["status"] == "live"
    assert _store(snapshot, "memory-local")["status"] == "live"
    assert {item["code"] for item in snapshot["diagnostics"]} >= {
        "invalid_json",
    }


def test_oversized_files_and_global_entry_budget_are_bounded(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    workspace.mkdir()
    turns = data_dir / "dashboard" / "workspaces" / _workspace_id(workspace) / "turns"
    turns.mkdir(parents=True)
    for suffix in ("a", "b", "c"):
        turn_id = "turn_" + (suffix * 32)
        (turns / f"{turn_id}.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "turnId": turn_id,
                    "workspaceId": _workspace_id(workspace),
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )
    (data_dir / "permissions.json").write_text(
        json.dumps({"padding": "x" * 200}),
        encoding="utf-8",
    )

    budget_snapshot = PersistenceHealthReader(
        workspace,
        data_dir=data_dir,
        max_directory_entries=2,
    ).snapshot()
    oversized_snapshot = PersistenceHealthReader(
        workspace,
        data_dir=data_dir,
        max_parsed_file_bytes=64,
    ).snapshot()

    assert _store(budget_snapshot, "conversation-turns")["status"] == "partial"
    assert _store(budget_snapshot, "conversation-turns")["recordCount"] == 2
    assert any(
        item["storeId"] == "conversation-turns" and item["code"] == "scan_limited"
        for item in budget_snapshot["diagnostics"]
    )
    assert _store(oversized_snapshot, "permissions")["status"] == "partial"
    assert _store(oversized_snapshot, "permissions")["recordCount"] == 0
    assert _store(oversized_snapshot, "permissions")["byteCount"] > 64
    assert any(
        item["storeId"] == "permissions" and item["code"] == "oversized_file"
        for item in oversized_snapshot["diagnostics"]
    )


def test_symlink_escape_and_special_files_are_never_followed(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    workspace.mkdir()
    data_dir.mkdir()
    outside.mkdir()
    (outside / "memory.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [
                    {
                        "id": "outside",
                        "content": "OUTSIDE_ESCAPE_SECRET",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mini-code-memory").symlink_to(outside, target_is_directory=True)
    tool_results = workspace / ".mini-code-tool-results"
    tool_results.mkdir()
    fifo = tool_results / "result.pipe"
    os.mkfifo(fifo)

    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    assert _store(snapshot, "memory-project")["status"] == "unavailable"
    assert _store(snapshot, "memory-project")["recordCount"] is None
    assert _store(snapshot, "tool-results")["status"] == "partial"
    assert "OUTSIDE_ESCAPE_SECRET" not in json.dumps(snapshot)
    assert {item["code"] for item in snapshot["diagnostics"]} >= {
        "root_unsafe",
        "entry_unsafe",
    }


def test_directory_failure_is_isolated_from_other_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.storage_health import PersistenceHealthReader
    import minicode.storage_health as storage_health

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    workspace.mkdir()
    turns = data_dir / "dashboard" / "workspaces" / _workspace_id(workspace) / "turns"
    turns.mkdir(parents=True)
    original_scandir = storage_health.os.scandir

    def failing_scandir(path: object):
        if Path(path) == turns:
            raise PermissionError("not allowed")
        return original_scandir(path)

    monkeypatch.setattr(storage_health.os, "scandir", failing_scandir)
    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    assert _store(snapshot, "conversation-turns")["status"] == "unavailable"
    assert _store(snapshot, "run-journal")["status"] == "live"
    assert _store(snapshot, "memory-project")["status"] == "live"


@pytest.mark.parametrize(
    "timestamp",
    [
        True,
        "",
        "2026-07-23",
        "2026-07-23T00:00:00Z",
        "2026-07-23T00:00:00.000+00:00",
        "99999-01-01T00:00:00.000Z",
    ],
)
def test_strict_snapshot_validator_rejects_invalid_generated_times(
    timestamp: object,
) -> None:
    from minicode.storage_health import (
        PersistenceHealthContractError,
        validate_persistence_health_snapshot,
    )

    payload = {
        "schemaVersion": 1,
        "generatedAt": timestamp,
        "mode": "read-only",
        "status": "live",
        "workspace": {"id": "ws_0123456789abcdef", "name": "workspace"},
        "summary": {
            "storeCount": 0,
            "knownRecordCount": 0,
            "knownByteCount": 0,
            "issueCount": 0,
        },
        "stores": [],
        "maintenancePlan": {
            "status": "planning",
            "destructiveActionsAvailable": False,
            "eligibleStoreIds": [],
            "excludedStoreIds": [],
            "blockers": [],
        },
        "diagnostics": [],
    }

    with pytest.raises(PersistenceHealthContractError):
        validate_persistence_health_snapshot(payload)


def test_reader_preserves_hash_mtime_inode_and_directory_contents(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    project_memory = workspace / ".mini-code-memory"
    project_memory.mkdir(parents=True)
    memory_path = project_memory / "memory.json"
    memory_path.write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [
                    {
                        "id": "project-entry",
                        "scope": "project",
                        "content": "BODY_MUST_REMAIN_PRIVATE",
                        "related_to": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    data_dir.mkdir()
    permissions = data_dir / "permissions.json"
    permissions.write_text("{}", encoding="utf-8")

    def evidence(path: Path) -> tuple[str, int, int]:
        info = path.stat()
        return (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            info.st_mtime_ns,
            info.st_ino,
        )

    before_files = {
        path.relative_to(tmp_path): evidence(path)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    before_entries = tuple(
        sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    )

    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    after_files = {
        path.relative_to(tmp_path): evidence(path)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    after_entries = tuple(
        sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    )
    assert snapshot["mode"] == "read-only"
    assert before_files == after_files
    assert before_entries == after_entries
    assert not any(
        path.name.endswith((".tmp", ".bak", ".backup"))
        or path.name in {"session-store.lock", "memory-store.lock"}
        for path in tmp_path.rglob("*")
    )


def test_source_configuration_and_process_local_stores_are_classified_without_content(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "home" / ".mini-code"
    workspace.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (data_dir / "permissions.json").write_text(
        json.dumps({"rules": [{"pattern": "PRIVATE_PERMISSION"}]}),
        encoding="utf-8",
    )
    (data_dir / "settings.json").write_text(
        json.dumps({"apiKey": "PRIVATE_PROVIDER_CREDENTIAL"}),
        encoding="utf-8",
    )
    claude_settings = data_dir.parent / ".claude" / "settings.json"
    claude_settings.parent.mkdir()
    claude_settings.write_text(
        json.dumps({"env": {"PRIVATE": "PRIVATE_COMPAT_CREDENTIAL"}}),
        encoding="utf-8",
    )
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "private": {
                        "command": "PRIVATE_MCP_COMMAND",
                        "env": {"KEY": "PRIVATE_MCP_ENV"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {}}),
        encoding="utf-8",
    )
    (data_dir / "USER.md").write_text("PRIVATE_USER_PROFILE", encoding="utf-8")
    project_profile = workspace / ".mini-code" / "USER.md"
    project_profile.parent.mkdir()
    project_profile.write_text("PRIVATE_PROJECT_PROFILE", encoding="utf-8")
    for root, body in (
        (data_dir / "skills" / "native", "PRIVATE_USER_SKILL"),
        (data_dir.parent / ".claude" / "skills" / "compat", "PRIVATE_COMPAT_SKILL"),
        (workspace / ".mini-code" / "skills" / "native", "PRIVATE_PROJECT_SKILL"),
        (workspace / ".claude" / "skills" / "compat", "PRIVATE_PROJECT_COMPAT"),
    ):
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(body, encoding="utf-8")

    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    expected = {
        "permissions": ("user", "persistent", "excluded", 1),
        "configuration": ("configuration", "source", "excluded", 2),
        "mcp-configuration": ("configuration", "source", "excluded", 2),
        "user-profile": ("user", "source", "excluded", 1),
        "project-profile": ("configuration", "source", "excluded", 1),
        "skills-user": ("user", "source", "excluded", 2),
        "skills-project": ("configuration", "source", "excluded", 2),
    }
    for store_id, (scope, durability, disposition, count) in expected.items():
        store = _store(snapshot, store_id)
        assert (
            store["scope"],
            store["durability"],
            store["resetDisposition"],
            store["recordCount"],
        ) == (scope, durability, disposition, count)
    for store_id in (
        "permission-broker",
        "mcp-current-registry",
        "gateway-runtime",
        "working-memory",
    ):
        store = _store(snapshot, store_id)
        assert store["scope"] == "process"
        assert store["durability"] == "process-local"
        assert store["resetDisposition"] == "not-applicable"
        assert store["recordCount"] is None
        assert store["byteCount"] is None
        assert store["updatedAt"] is None
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for private in (
        "PRIVATE_PERMISSION",
        "PRIVATE_PROVIDER_CREDENTIAL",
        "PRIVATE_COMPAT_CREDENTIAL",
        "PRIVATE_MCP_COMMAND",
        "PRIVATE_MCP_ENV",
        "PRIVATE_USER_PROFILE",
        "PRIVATE_PROJECT_PROFILE",
        "PRIVATE_USER_SKILL",
        "PRIVATE_COMPAT_SKILL",
        "PRIVATE_PROJECT_SKILL",
        "PRIVATE_PROJECT_COMPAT",
    ):
        assert private not in serialized


def test_source_and_tool_content_is_counted_with_stat_without_being_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "home" / ".mini-code"
    workspace.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    profile = data_dir / "USER.md"
    profile.write_text("DO_NOT_READ_PROFILE_BODY", encoding="utf-8")
    skill = data_dir / "skills" / "safe" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("DO_NOT_READ_SKILL_BODY", encoding="utf-8")
    tool_result = workspace / ".mini-code-tool-results" / "result.txt"
    tool_result.parent.mkdir()
    tool_result.write_text("DO_NOT_READ_TOOL_BODY", encoding="utf-8")
    forbidden = {profile, skill, tool_result}
    reader = PersistenceHealthReader(workspace, data_dir=data_dir)
    original = reader._read_file

    def guarded_read(path: Path, **kwargs: object) -> bytes | None:
        assert path not in forbidden
        return original(path, **kwargs)

    monkeypatch.setattr(reader, "_read_file", guarded_read)
    snapshot = reader.snapshot()

    assert _store(snapshot, "user-profile")["recordCount"] == 1
    assert _store(snapshot, "skills-user")["recordCount"] == 1
    assert _store(snapshot, "tool-results")["recordCount"] == 1


def test_temp_backup_and_active_deletion_fence_are_reported_but_never_changed(
    tmp_path: Path,
) -> None:
    from minicode.storage_health import PersistenceHealthReader

    workspace = (tmp_path / "workspace").resolve()
    data_dir = tmp_path / "data"
    workspace.mkdir()
    deletion_root = (
        data_dir / "dashboard" / "workspaces" / _workspace_id(workspace) / "deletions"
    )
    deletion_root.mkdir(parents=True)
    identity = "d" * 64
    fence = deletion_root / f"conversation-{identity}.fence.json"
    fence.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "conversation",
                "targetId": "session-safe",
                "deletionRevision": "delrev_" + ("e" * 64),
                "status": "in_progress",
                "createdAt": "2026-07-23T00:00:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    temporary = deletion_root / ".deletion-recovery.tmp"
    temporary.write_text("PRIVATE_TEMP_BODY", encoding="utf-8")
    before = {
        path.name: (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
            path.stat().st_ino,
        )
        for path in (fence, temporary)
    }

    snapshot = PersistenceHealthReader(workspace, data_dir=data_dir).snapshot()

    store = _store(snapshot, "deletion-coordination")
    assert store["status"] == "partial"
    assert store["recordCount"] == 1
    assert {item["code"] for item in snapshot["diagnostics"]} >= {
        "temporary_artifact",
    }
    assert {
        (item["code"], item["storeId"])
        for item in snapshot["maintenancePlan"]["blockers"]
    } >= {
        ("store_not_live", "deletion-coordination"),
        ("active_maintenance_fence", "deletion-coordination"),
    }
    assert snapshot["maintenancePlan"]["destructiveActionsAvailable"] is False
    assert "PRIVATE_TEMP_BODY" not in json.dumps(snapshot)
    assert before == {
        path.name: (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
            path.stat().st_ino,
        )
        for path in (fence, temporary)
    }


def test_reader_never_constructs_managers_or_calls_maintenance_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.storage_health import PersistenceHealthReader
    import minicode.conversation_turn_store as turn_module
    import minicode.memory as memory_module
    import minicode.run_journal as run_module
    import minicode.session as session_module

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("writer or maintenance authority was called")

    monkeypatch.setattr(memory_module.MemoryManager, "__init__", forbidden)
    monkeypatch.setattr(turn_module.ConversationTurnStore, "__init__", forbidden)
    monkeypatch.setattr(run_module.RunJournal, "__init__", forbidden)
    monkeypatch.setattr(session_module, "cleanup_old_sessions", forbidden)
    monkeypatch.setattr(
        turn_module.ConversationTurnStore, "_enforce_retention", forbidden
    )
    monkeypatch.setattr(run_module.RunJournal, "enforce_retention", forbidden)
    for name in (
        "repair_corrupted_memory",
        "migrate_legacy_memory",
    ):
        if hasattr(memory_module, name):
            monkeypatch.setattr(memory_module, name, forbidden)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = PersistenceHealthReader(
        workspace,
        data_dir=tmp_path / "data",
    ).snapshot()

    assert snapshot["mode"] == "read-only"
    assert snapshot["maintenancePlan"]["destructiveActionsAvailable"] is False
