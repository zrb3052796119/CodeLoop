from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from scripts.audit_formal_memory_contamination import (
    AuditInputs,
    StaticMemoryFixture,
    audit_formal_state,
    build_argument_parser,
    classify_memory_entry,
    output_contains_secret,
)
from scripts.create_formal_memory_snapshot import create_snapshot
from tests.global_state_isolation import snapshot_paths


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_formal_fixture(home: Path) -> dict[str, Path]:
    mini_code = home / ".mini-code"
    memory_dir = mini_code / "memory"
    memory_dir.mkdir(parents=True)
    memory_path = memory_dir / "memory.json"
    audit_path = memory_dir / "approval_audit.json"
    markdown_path = memory_dir / "MEMORY.md"
    sessions_path = mini_code / "sessions_index.json"
    memory_path.write_text(
        json.dumps(
            {
                "scope": "user",
                "entries": [
                    {
                        "id": "user-test-1",
                        "scope": "user",
                        "category": "shell",
                        "content": "Known test fixture",
                        "tags": [],
                        "created_at": 1000.0,
                        "updated_at": 1000.0,
                        "source": "",
                        "provenance": {"private": "must-not-leak"},
                    },
                    {
                        "id": "user-private-1",
                        "scope": "user",
                        "category": "private",
                        "content": "private user memory secret-value-123",
                        "tags": [],
                        "created_at": 1.0,
                        "updated_at": 1.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    audit_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "entry_id": "user-test-1",
                        "content_hash": hashlib.sha256(b"Known test fixture").hexdigest(),
                        "created_at": 1000.0,
                        "action": "write",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    markdown_path.write_text("# Derived memory\n", encoding="utf-8")
    sessions_path.write_text(
        json.dumps(
            {
                "test-integration-001": {
                    "session_id": "test-integration-001",
                    "created_at": 1000.0,
                    "updated_at": 1000.0,
                    "first_message": "private session text",
                    "last_message": "more private session text",
                    "message_count": 2,
                    "workspace": "/private/tmp/pytest-of-user/test_session0",
                },
                "real-session": {
                    "session_id": "real-session",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                    "first_message": "protected conversation",
                    "last_message": "protected conversation",
                    "message_count": 2,
                    "workspace": "/Users/private/project",
                },
            }
        ),
        encoding="utf-8",
    )
    return {
        "memory": memory_path,
        "markdown": markdown_path,
        "approval": audit_path,
        "sessions": sessions_path,
    }


def test_snapshot_copies_current_state_with_secure_permissions_and_matching_hashes(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    source_paths = _write_formal_fixture(home)

    result = create_snapshot(
        real_home=home,
        backup_root=tmp_path / "backups",
        timestamp="20260715-120000",
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert result.backup_dir.name == "memory-test-isolation-20260715-120000"
    assert result.file_count == 4
    assert result.all_hashes_match is True
    assert manifest["snapshot_kind"] == "current_post_contamination_state"
    assert all(item["source_hash"] == item["backup_hash"] for item in manifest["files"])
    if os.name == "posix":
        assert stat.S_IMODE(result.backup_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(result.manifest_path.stat().st_mode) == 0o600
        for source in source_paths.values():
            assert stat.S_IMODE((result.backup_dir / source.name).stat().st_mode) == 0o600


def test_confirmed_memory_requires_two_independent_evidence_groups() -> None:
    entry = {
        "id": "user-1",
        "scope": "user",
        "category": "test",
        "content": "Entry",
        "tags": [],
        "created_at": 1000.0,
        "updated_at": 1000.0,
    }
    fixture = StaticMemoryFixture(
        content="Entry",
        category="test",
        tags=(),
        source_file="tests/example.py",
        source_line=10,
    )

    ambiguous = classify_memory_entry(
        entry,
        fixtures=[fixture],
        approval_records=[],
        known_window=None,
    )
    confirmed = classify_memory_entry(
        entry,
        fixtures=[fixture],
        approval_records=[
            {
                "entry_id": "user-1",
                "content_hash": hashlib.sha256(b"Entry").hexdigest(),
                "created_at": 1000.0,
            }
        ],
        known_window=(990.0, 1010.0),
    )

    assert ambiguous["classification"] == "ambiguous"
    assert ambiguous["proposed_action"] == "manual_review"
    assert confirmed["classification"] == "confirmed_test_artifact"
    assert {"fixture_content_exact", "phase1_test_window", "approval_entry_hash_match"}.issubset(
        confirmed["evidence_codes"]
    )


def test_protected_user_data_never_receives_cleanup_action() -> None:
    result = classify_memory_entry(
        {
            "id": "protected-1",
            "scope": "user",
            "category": "private",
            "content": "unrelated private memory",
            "tags": [],
            "created_at": 1.0,
            "updated_at": 1.0,
        },
        fixtures=[],
        approval_records=[],
        known_window=None,
    )

    assert result["classification"] == "protected_user_data"
    assert result["proposed_action"] == "no_action"
    assert result["requires_user_approval"] is True
    assert "preview" not in result


def test_audit_is_read_only_private_bounded_and_all_actions_unapproved(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source_paths = _write_formal_fixture(home)
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_fixture.py").write_text(
        "manager.add_entry(MemoryScope.USER, 'shell', 'Known test fixture')\n",
        encoding="utf-8",
    )
    phase1 = project / "phase1.json"
    phase1.write_text(
        json.dumps(
            {
                "formal_memory_snapshot_after": {
                    "user/memory.json": {"mtime_ns": 1000 * 1_000_000_000}
                }
            }
        ),
        encoding="utf-8",
    )
    before = snapshot_paths(list(source_paths.values()))
    output = tmp_path / "inventory.json"
    markdown = tmp_path / "audit.md"

    report = audit_formal_state(
        AuditInputs(
            real_home=home,
            project_root=project,
            phase1_artifact=phase1,
            output_path=output,
            markdown_path=markdown,
        )
    )

    assert snapshot_paths(list(source_paths.values())) == before
    assert report["formal_files_unchanged"] is True
    assert report["dry_run"] is True
    assert all(action["approved"] is False for action in report["proposed_recovery_plan"])
    serialized = output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert "private user memory secret-value-123" not in serialized
    assert "private session text" not in serialized
    assert "protected conversation" not in serialized
    assert "must-not-leak" not in serialized
    assert output_contains_secret(serialized) is False


def test_session_inventory_uses_hashed_id_and_workspace_type_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_formal_fixture(home)
    project = tmp_path / "project"
    project.mkdir()
    phase1 = project / "phase1.json"
    phase1.write_text("{}", encoding="utf-8")
    report = audit_formal_state(
        AuditInputs(
            real_home=home,
            project_root=project,
            phase1_artifact=phase1,
            output_path=tmp_path / "inventory.json",
            markdown_path=tmp_path / "audit.md",
        )
    )

    session_records = [item for item in report["inventory"] if item["record_type"] == "session"]
    assert session_records
    assert all("session_id" not in item for item in session_records)
    assert all(set(item) <= {
        "record_type",
        "session_id_sha256",
        "workspace_type",
        "classification",
        "confidence",
        "evidence_codes",
        "created_at",
        "updated_at",
        "proposed_action",
        "requires_user_approval",
    } for item in session_records)


def test_audit_cli_has_no_mutating_arguments() -> None:
    parser = build_argument_parser()
    option_names = {option for action in parser._actions for option in action.option_strings}

    assert {"--delete", "--apply", "--restore", "--execute"}.isdisjoint(option_names)
    with pytest.raises(SystemExit):
        parser.parse_args(["--delete"])


def test_audit_output_secret_scanner_detects_credential_shapes() -> None:
    assert output_contains_secret("OPENAI_API_KEY=sk-live-private-value") is True
    assert output_contains_secret('{"authorization": "Bearer private-value"}') is True
    assert output_contains_secret("Known test fixture") is False
