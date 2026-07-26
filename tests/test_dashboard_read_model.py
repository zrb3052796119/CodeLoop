from __future__ import annotations

import json
from pathlib import Path

import minicode.session
from minicode.run_journal import RunJournal
from minicode.session import SessionMetadata
from minicode.skills import SkillSummary
from minicode.web.read_model import DashboardReadModel


def test_empty_workspace_snapshot_distinguishes_zero_from_unavailable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()

    assert snapshot["schemaVersion"] == 1
    assert snapshot["mode"] == "read-only"
    assert snapshot["workspace"]["name"] == "workspace"
    assert snapshot["workspace"]["path"] is None
    assert str(workspace.resolve()) not in json.dumps(snapshot)
    assert snapshot["workspace"]["status"] == "live"
    assert snapshot["overview"]["sessions"]["count"] == 0
    assert snapshot["overview"]["memory"]["totalCount"] == 0
    assert snapshot["overview"]["skills"]["count"] == 0
    assert snapshot["overview"]["runs"] == {
        "status": "live",
        "count": 0,
        "byStatus": {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "interrupted": 0,
            "cancel_requested": 0,
            "cancelled": 0,
        },
        "latestUpdatedAt": None,
        "coverage": {
            "journal": "live",
            "tui": "live",
            "headless": "live",
            "gateway": "live",
            "historical": "partial",
                "scope": "lifecycle-model-usage-cost-tool-assistant-skill-memory-context",
            "model": "live",
            "tool": "live",
            "assistant": "live",
                "usage": "live",
                "cost": "live",
            "memory": "live",
            "skills": "live",
            "context": "partial",
            "workingMemory": "partial",
            "mcpRuntime": "partial",
            "mcpRuntimeScope": "run-scoped observation",
            "mcpRuntimeHistorical": "partial",
            "mcpRuntimeCurrent": "unavailable",
            "mcpRuntimeCrossProcess": "unavailable",
        },
    }
    assert snapshot["overview"]["usage"]["costUsd"] is None
    assert snapshot["overview"]["usage"]["cost"]["status"] == "unavailable"
    assert snapshot["overview"]["usage"]["cost"]["value"] is None
    assert snapshot["sources"]["sessions"]["status"] == "live"
    assert snapshot["sources"]["runs"]["status"] == "live"
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_snapshot_counts_memory_by_scope_tier_and_category(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    scope_fixtures = {
        data_dir / "memory": (
            "user",
            "long_term",
            "preference",
        ),
        workspace / ".mini-code-memory": (
            "project",
            "short_term",
            "architecture",
        ),
        workspace / ".mini-code-memory-local": (
            "local",
            "working",
            "testing",
        ),
    }
    for memory_dir, (scope, tier, category) in scope_fixtures.items():
        memory_dir.mkdir(parents=True)
        (memory_dir / "memory.json").write_text(
            json.dumps(
                {
                    "scope": scope,
                    "entries": [
                        {
                            "id": f"{scope}-1",
                            "scope": scope,
                            "tier": tier,
                            "category": category,
                            "content": f"safe {scope} memory",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()
    memory = snapshot["overview"]["memory"]

    assert memory["totalCount"] == 3
    assert memory["scopes"]["user"]["count"] == 1
    assert memory["scopes"]["project"]["count"] == 1
    assert memory["scopes"]["local"]["count"] == 1
    assert memory["tiers"] == {
        "working": 1,
        "short_term": 1,
        "long_term": 1,
        "archival": 0,
    }
    assert memory["categories"] == {
        "architecture": 1,
        "preference": 1,
        "testing": 1,
    }


def test_corrupt_session_index_is_localized_to_the_session_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "sessions_index.json").write_text(
        '{"session": password=hidden-value',
        encoding="utf-8",
    )

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()

    assert snapshot["overview"]["sessions"] == {
        "status": "error",
        "count": None,
        "latestUpdatedAt": None,
    }
    assert snapshot["sources"]["sessions"]["status"] == "error"
    assert snapshot["sources"]["memory"]["status"] == "live"
    assert "hidden-value" not in json.dumps(snapshot)


def test_corrupt_memory_scope_does_not_mutate_files_or_hide_other_scopes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    user_dir = data_dir / "memory"
    project_dir = workspace / ".mini-code-memory"
    user_dir.mkdir(parents=True)
    project_dir.mkdir()
    corrupt = user_dir / "memory.json"
    corrupt.write_text('{"entries": [Bearer very-secret-token', encoding="utf-8")
    project_memory = project_dir / "memory.json"
    project_memory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "project-1",
                        "scope": "project",
                        "category": "testing",
                        "tier": "short_term",
                        "content": "safe project memory",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    before = corrupt.read_bytes()

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()
    memory = snapshot["overview"]["memory"]

    assert memory["status"] == "partial"
    assert memory["totalCount"] is None
    assert memory["knownCount"] == 1
    assert memory["scopes"]["user"]["count"] is None
    assert memory["scopes"]["project"]["count"] == 1
    assert snapshot["sources"]["memory"]["status"] == "error"
    assert corrupt.read_bytes() == before
    assert not corrupt.with_suffix(".json.bak").exists()
    assert "very-secret-token" not in json.dumps(snapshot)


def test_sessions_are_filtered_to_the_resolved_current_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    sessions = [
        SessionMetadata(
            session_id="current",
            created_at=10.0,
            updated_at=20.0,
            workspace=str(workspace / ".." / "workspace"),
            first_message="Session transcript secret should never be returned",
        ),
        SessionMetadata(
            session_id="other",
            created_at=30.0,
            updated_at=40.0,
            workspace=str(other_workspace),
        ),
    ]

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: sessions,
        skill_loader=lambda _workspace: [],
    ).snapshot()
    serialized = json.dumps(snapshot)

    assert snapshot["overview"]["sessions"]["count"] == 1
    assert snapshot["overview"]["sessions"]["latestUpdatedAt"] == (
        "1970-01-01T00:00:20Z"
    )
    assert "transcript secret" not in serialized
    assert "current" not in serialized
    assert "other" not in serialized


def test_skills_are_counted_by_source_without_exposing_content_or_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret_path = tmp_path / "sk-test-secret" / "SKILL.md"
    skills = [
        SkillSummary(
            name="safe-project-skill",
            description="password=hidden-value",
            path=str(secret_path),
            source="project",
        ),
        SkillSummary(
            name="safe-user-skill",
            description="Bearer very-secret-token",
            path=str(secret_path),
            source="user",
        ),
    ]

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=lambda _workspace: skills,
    ).snapshot()
    serialized = json.dumps(snapshot)

    assert snapshot["overview"]["skills"] == {
        "status": "live",
        "count": 2,
        "bySource": {"project": 1, "user": 1},
    }
    for secret in ("sk-test-secret", "hidden-value", "very-secret-token"):
        assert secret not in serialized


def test_connections_report_configured_mcp_without_claiming_live_status(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "global": {
                        "command": "secret-command",
                        "env": {"API_KEY": "sk-test-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "project": {
                        "args": ["Bearer very-secret-token"],
                        "env": {"PASSWORD": "hidden-value"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()
    serialized = json.dumps(snapshot)

    assert snapshot["overview"]["connections"] == {
        "status": "live",
        "gateway": {"status": "live"},
        "mcp": {
            "status": "unavailable",
            "configuredCount": 2,
            "liveCount": None,
        },
    }
    assert snapshot["sources"]["connections"]["status"] == "live"
    for secret in (
        "secret-command",
        "sk-test-secret",
        "very-secret-token",
        "hidden-value",
    ):
        assert secret not in serialized


def test_one_source_failure_keeps_other_sources_live(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_skills(_workspace: str | Path) -> list[SkillSummary]:
        raise RuntimeError("Bearer very-secret-token")

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=fail_skills,
    ).snapshot()

    assert snapshot["overview"]["skills"] == {
        "status": "error",
        "count": None,
        "bySource": {},
    }
    assert snapshot["sources"]["skills"]["status"] == "error"
    assert snapshot["sources"]["sessions"]["status"] == "live"
    assert snapshot["sources"]["memory"]["status"] == "live"
    assert any(item["source"] == "skills" for item in snapshot["diagnostics"])
    assert "very-secret-token" not in json.dumps(snapshot)


def test_snapshot_is_recursively_redacted_before_serialization(tmp_path: Path) -> None:
    workspace = tmp_path / "password=hidden-value" / "sk-test-secret"
    workspace.mkdir(parents=True)
    skills = [
        SkillSummary(
            name="irrelevant",
            description="irrelevant",
            path="irrelevant",
            source="Bearer very-secret-token",
        )
    ]

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=lambda _workspace: skills,
    ).snapshot()
    serialized = json.dumps(snapshot)

    for secret in ("hidden-value", "sk-test-secret", "very-secret-token"):
        assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert snapshot["overview"]["usage"]["tokensIn"] is None


def test_nonexistent_workspace_has_a_stable_id_and_error_source(tmp_path: Path) -> None:
    workspace = tmp_path / "missing-workspace"
    model = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    )

    first = model.snapshot()
    second = model.snapshot()

    assert first["workspace"]["status"] == "error"
    assert first["sources"]["workspace"]["status"] == "error"
    assert first["workspace"]["id"] == second["workspace"]["id"]
    assert first["overview"]["runs"]["count"] == 0


def test_snapshot_aggregates_only_journaled_run_lifecycle_statuses(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    completed = journal.create_run(title="Completed", source="headless")
    journal.transition(completed.id, "running")
    journal.transition(completed.id, "completed")
    failed = journal.create_run(title="Failed", source="gateway")
    journal.transition(failed.id, "running")
    journal.transition(failed.id, "failed", reason="execution_failed")
    interrupted = journal.create_run(title="Interrupted", source="tui")
    journal.transition(interrupted.id, "running")
    journal.transition(
        interrupted.id, "interrupted", reason="execution_interrupted"
    )
    running = journal.create_run(title="Running", source="tui")
    journal.transition(running.id, "running")

    snapshot = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
        run_journal=journal,
    ).snapshot()
    runs = snapshot["overview"]["runs"]

    assert runs["status"] == "live"
    assert runs["count"] == 4
    assert runs["byStatus"]["running"] == 1
    assert runs["byStatus"]["completed"] == 1
    assert runs["byStatus"]["failed"] == 1
    assert runs["byStatus"]["interrupted"] == 1
    assert runs["latestUpdatedAt"] is not None
    assert runs["coverage"]["historical"] == "partial"
    assert snapshot["overview"]["usage"] == {
        "status": "unavailable",
        "inputTokens": None,
        "outputTokens": None,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
        "providerCalls": 0,
        "estimatedCalls": 0,
        "unavailableCalls": 0,
        "provenance": "unavailable",
        "durationMs": None,
        "costUsd": None,
        "cost": {
            "status": "unavailable",
            "value": None,
            "coverage": {
                "completedCalls": 0,
                "pricedCalls": 0,
                "unavailableCalls": 0,
                "missingCalls": 0,
                "failedAttempts": 0,
                "invalidEvents": 0,
                "duplicateEvents": 0,
                "conflictEvents": 0,
                "orphanEvents": 0,
                "historical": "partial",
                "scope": "retained-run-journal",
                "limited": False,
            },
        },
        "tools": {
            "status": "unavailable",
            "value": None,
            "coverage": {
                "danglingStarts": 0,
                "unpairedFinishes": 0,
                "duplicateEvents": 0,
                "conflictingOperations": 0,
                "orphanFinishes": 0,
                "invalidEvents": 0,
                "historical": "partial",
                "scope": "retained-run-journal",
                "limited": False,
            },
        },
        "failures": {
            "status": "complete",
            "value": {
                "affectedRuns": 1,
                "toolErrors": 0,
                "modelFailures": 0,
                "runFailures": 1,
                "interruptedRuns": 1,
                "cancelledRuns": 0,
                "hasObservedFailure": True,
            },
            "coverage": {
                "observedRuns": 4,
                "invalidEvents": 0,
                "duplicateEvents": 0,
                "conflictingOperations": 0,
                "historical": "partial",
                "scope": "retained-run-journal",
                "limited": False,
            },
        },
        "coverage": "retained-run-journal",
        "historical": "partial",
        "tokensIn": None,
        "tokensOut": None,
        "toolCalls": None,
        "errors": None,
    }


def test_snapshot_localizes_run_journal_failure_to_runs_source(
    tmp_path: Path,
) -> None:
    class BrokenJournal:
        def list_runs(self, **_kwargs):
            raise OSError("Bearer very-secret-token")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
        run_journal=BrokenJournal(),  # type: ignore[arg-type]
    ).snapshot()

    assert snapshot["overview"]["runs"]["status"] == "error"
    assert snapshot["overview"]["runs"]["count"] is None
    assert snapshot["sources"]["runs"]["status"] == "error"
    assert snapshot["sources"]["sessions"]["status"] == "live"
    assert snapshot["sources"]["memory"]["status"] == "live"
    assert snapshot["sources"]["skills"]["status"] == "live"
    assert "very-secret-token" not in json.dumps(snapshot)


def test_default_session_adapter_uses_list_sessions_without_transcripts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    other = (tmp_path / "other").resolve()
    other.mkdir()
    data_dir = tmp_path / "isolated-home" / ".mini-code"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(minicode.session, "MINI_CODE_DIR", data_dir)
    (data_dir / "sessions_index.json").write_text(
        json.dumps(
            {
                "current": {
                    "session_id": "current",
                    "created_at": 1.0,
                    "updated_at": 2.0,
                    "first_message": "sk-test-secret transcript",
                    "last_message": "password=hidden-value",
                    "message_count": 2,
                    "workspace": str(workspace),
                },
                "other": {
                    "session_id": "other",
                    "created_at": 3.0,
                    "updated_at": 4.0,
                    "message_count": 1,
                    "workspace": str(other),
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        skill_loader=lambda _workspace: [],
    ).snapshot()
    serialized = json.dumps(snapshot)

    assert snapshot["overview"]["sessions"]["count"] == 1
    assert "sk-test-secret" not in serialized
    assert "hidden-value" not in serialized


def test_workspace_environment_override_wins_over_startup_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    configured = tmp_path / "configured"
    cwd.mkdir()
    configured.mkdir()

    model = DashboardReadModel.from_environment(
        environ={"MINI_CODE_DASHBOARD_WORKSPACE": str(configured)},
        cwd=cwd,
        data_dir=tmp_path / "home" / ".mini-code",
    )

    assert model.workspace == configured.resolve()


def test_oversized_memory_file_is_rejected_as_a_local_source_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    memory_dir = workspace / ".mini-code-memory"
    memory_dir.mkdir()
    (memory_dir / "memory.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "oversized",
                        "scope": "project",
                        "content": "x" * (2 * 1024 * 1024),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    snapshot = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).snapshot()

    assert snapshot["overview"]["memory"]["scopes"]["project"]["status"] == (
        "error"
    )
    assert snapshot["sources"]["memory"]["status"] == "error"
