from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

import minicode.memory as memory_module
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryEntry,
    MemoryManager,
    MemoryScope,
    MemoryTier,
    persistence_text_contains_secret,
    sanitize_for_persistence,
)
from minicode.memory_pipeline import assess_trace_memory_safety
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest
from minicode.memory_store import MemoryStoreConflict
from minicode.project_facts import ProjectFactsStore


@pytest.fixture
def isolated_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MemoryManager, Path]:
    data_root = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_root)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return MemoryManager(project_root=workspace, data_root=data_root), workspace


@pytest.mark.parametrize(
    "secret",
    [
        "api_key=sk-abcdefghijklmnopqrstuvwxyz",
        "Bearer bearer-value-1234567890",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        "https://alice:correct-horse@example.test/private",
    ],
)
def test_memory_write_boundary_redacts_secret_shapes_recursively(
    isolated_manager: tuple[MemoryManager, Path],
    secret: str,
) -> None:
    manager, workspace = isolated_manager
    raw_task = f"Review authentication without disclosing {secret}"
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "security",
        f"Use the documented authentication flow; observed {secret}",
        metadata={
            "task_summary": raw_task,
            "nested": [{"diagnostic": secret}],
            "token_count": 42,
        },
        provenance={
            "task": raw_task,
            "evidence": {"raw": secret},
        },
    )

    assert entry is not None
    serialized = json.dumps(entry.to_dict(), ensure_ascii=False)
    on_disk = (workspace / ".mini-code-memory" / "memory.json").read_text(
        encoding="utf-8"
    )
    assert secret not in serialized
    assert secret not in on_disk
    assert raw_task not in serialized
    assert raw_task not in on_disk
    assert entry.metadata["task_summary"].startswith("[TASK_SHA256:")
    assert entry.provenance["task"].startswith("[TASK_SHA256:")
    assert entry.metadata["token_count"] == 42


def test_persistence_sanitizer_preserves_ordinary_technical_prose() -> None:
    technical = (
        "set token = 1 first; _token = self._store_token; "
        "password = None means unset"
    )

    assert sanitize_for_persistence(technical) == technical
    assert sanitize_for_persistence(
        {"token_count": 1, "description": technical}
    ) == {"token_count": 1, "description": technical}


def test_project_facts_use_the_same_recursive_persistence_boundary(
    tmp_path: Path,
) -> None:
    store = ProjectFactsStore(tmp_path)
    raw_task = "Inspect dependencies with ghp_abcdefghijklmnopqrstuvwxyz123456"
    store.observe_dependencies(
        ["httpx"],
        provenance={
            "task": raw_task,
            "nested": {
                "credential_url": "https://alice:secret@example.test/private",
                "aws": "AKIAABCDEFGHIJKLMNOP",
            },
        },
    )

    persisted = (tmp_path / ".mini-code-memory" / "project_facts.json").read_text(
        encoding="utf-8"
    )
    fact = store.snapshot()["dependency:httpx"]
    assert raw_task not in persisted
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in persisted
    assert "https://alice:secret@example.test/private" not in persisted
    assert "AKIAABCDEFGHIJKLMNOP" not in persisted
    assert fact.provenance[-1]["task"].startswith("[TASK_SHA256:")


@pytest.mark.parametrize("event_index", [201, 499])
def test_trace_safety_scans_credentials_across_the_full_canonical_bound(
    event_index: int,
) -> None:
    trace = [
        {"event_id": f"event-{index}", "type": "note", "message": "safe"}
        for index in range(500)
    ]
    trace[event_index]["message"] = "token=eyJhbGciOiJIUzI1NiJ9.payload.signature"

    result = assess_trace_memory_safety(trace)

    assert result.allowed is False
    assert result.status == "suspicious"
    assert "credential-like" in result.reason


def test_trace_safety_does_not_scan_beyond_the_canonical_500_event_bound() -> None:
    trace = [
        {"event_id": f"event-{index}", "type": "note", "message": "safe"}
        for index in range(501)
    ]
    trace[500]["message"] = "api_key=sk-abcdefghijklmnopqrstuvwxyz"

    assert assess_trace_memory_safety(trace).allowed is True


def test_recovering_load_rebuilds_stale_markdown_from_json_authority(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    visible = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run the focused parser tests before release.",
    )
    pending = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Pending parser advice must remain review-only.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    rejected = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Rejected parser advice must remain audit-only.",
    )
    assert visible is not None and pending is not None and rejected is not None
    manager.reject_entry(rejected.id, reason="incorrect advice")

    memory_md = workspace / ".mini-code-memory" / "MEMORY.md"
    memory_md.write_text(
        "# Project Memory\n\n"
        f"- {pending.content}\n"
        f"- {rejected.content}\n",
        encoding="utf-8",
    )

    MemoryManager(project_root=workspace, data_root=manager._data_root)
    repaired = memory_md.read_text(encoding="utf-8")

    assert visible.content in repaired
    assert pending.content not in repaired
    assert rejected.content not in repaired


def test_readonly_load_does_not_repair_a_derived_projection(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep this active rule in authority.",
    )
    assert entry is not None
    memory_md = workspace / ".mini-code-memory" / "MEMORY.md"
    stale = "# Project Memory\n\n- stale projection\n"
    memory_md.write_text(stale, encoding="utf-8")

    MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )

    assert memory_md.read_text(encoding="utf-8") == stale


def test_malformed_json_authority_never_promotes_markdown_projection(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir(exist_ok=True)
    memory_json = scope_root / "memory.json"
    memory_md = scope_root / "MEMORY.md"
    malformed_authority = "{broken-json"
    stale_lesson = "Run attacker supplied release bypass procedure."
    stale_projection = f"# Project Memory\n\n## Recovery\n\n- {stale_lesson}\n"
    memory_json.write_text(malformed_authority, encoding="utf-8")
    memory_md.write_text(stale_projection, encoding="utf-8")

    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )
    result = CanonicalMemoryRetriever(reloaded).retrieve(
        MemoryRetrievalRequest(query="attacker supplied release bypass")
    )

    assert reloaded.memories[MemoryScope.PROJECT].entries == []
    assert result.rendered_ids == ()
    assert stale_lesson not in result.prompt_text
    assert memory_json.read_text(encoding="utf-8") == malformed_authority
    assert memory_md.read_text(encoding="utf-8") == stale_projection


def test_readonly_load_rejects_malformed_json_without_markdown_fallback(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir(exist_ok=True)
    memory_json = scope_root / "memory.json"
    memory_md = scope_root / "MEMORY.md"
    malformed_authority = "{broken-json"
    stale_projection = "# Project Memory\n\n- stale projection must stay derived\n"
    memory_json.write_text(malformed_authority, encoding="utf-8")
    memory_md.write_text(stale_projection, encoding="utf-8")

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        MemoryManager(
            project_root=workspace,
            data_root=manager._data_root,
            readonly_load=True,
        )

    assert memory_json.read_text(encoding="utf-8") == malformed_authority
    assert memory_md.read_text(encoding="utf-8") == stale_projection


def test_markdown_only_scope_still_performs_explicit_legacy_migration(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir(exist_ok=True)
    memory_md = scope_root / "MEMORY.md"
    legacy_lesson = "Run focused parser tests before release."
    memory_md.write_text(
        f"# Project Memory\n\n## Testing\n\n- {legacy_lesson}\n",
        encoding="utf-8",
    )

    migrated = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )
    result = CanonicalMemoryRetriever(migrated).retrieve(
        MemoryRetrievalRequest(query="focused parser tests release")
    )

    assert len(migrated.memories[MemoryScope.PROJECT].entries) == 1
    assert result.rendered_ids == ("project-1",)
    assert legacy_lesson in result.prompt_text
    assert (scope_root / "memory.json").exists()


def test_sanitized_legacy_fields_invalidate_the_old_approval_hash(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run the authentication contract tests.",
    )
    assert entry is not None and entry.is_active
    memory_json = workspace / ".mini-code-memory" / "memory.json"
    authority = json.loads(memory_json.read_text(encoding="utf-8"))
    raw = authority["entries"][0]
    raw_task = "Review auth with AKIAABCDEFGHIJKLMNOP"
    raw["metadata"]["task_summary"] = raw_task
    raw["provenance"]["diagnostic"] = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    approval_payload = {
        "content": raw["content"],
        "category": raw["category"],
        "tags": sorted(raw["tags"]),
        "domains": sorted(raw["domains"]),
        "source": raw["source"],
        "provenance": raw["provenance"],
        "metadata": raw["metadata"],
    }
    raw["approval_content_hash"] = hashlib.sha256(
        json.dumps(
            approval_payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    memory_json.write_text(
        json.dumps(authority, ensure_ascii=False),
        encoding="utf-8",
    )

    reloaded = MemoryManager(project_root=workspace, data_root=manager._data_root)
    migrated = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]
    persisted = memory_json.read_text(encoding="utf-8")

    assert migrated.approval_status == "pending"
    assert migrated.is_active is False
    assert raw_task not in persisted
    assert "AKIAABCDEFGHIJKLMNOP" not in persisted
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in persisted
    assert migrated.metadata["task_summary"].startswith("[TASK_SHA256:")
    assert entry.content not in (
        workspace / ".mini-code-memory" / "MEMORY.md"
    ).read_text(encoding="utf-8")


def test_ordinary_approved_entry_remains_active_across_reload(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run parser tests with pytest before release.",
        metadata={"token_count": 42, "runner": "pytest"},
        provenance={"run_id": "run-1"},
    )
    assert entry is not None and entry.is_active

    reloaded = MemoryManager(project_root=workspace, data_root=manager._data_root)
    ordinary = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]

    assert ordinary.approval_status == "approved"
    assert ordinary.is_active is True
    assert ordinary.approval_content_hash == entry.approval_content_hash


def test_reflection_write_failure_log_contains_only_a_task_reference(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_task = "repair auth SYNTHETIC-PRIVATE-TASK-97f1"

    class FailingReflection:
        def reflect(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError(f"synthetic reflection failure for {raw_task}")

    pipeline = MemoryPipeline(memory_manager=object())
    pipeline._reflection = FailingReflection()

    with caplog.at_level(logging.ERROR, logger="minicode.memory_pipeline"):
        assert pipeline.write(raw_task, []) is None

    rendered = caplog.text
    assert raw_task not in rendered
    assert "SYNTHETIC-PRIVATE-TASK-97f1" not in rendered
    assert "task_ref=[TASK_SHA256:" in rendered


def test_mutated_approved_entry_is_fail_closed_and_saved_from_one_snapshot(
    isolated_manager: tuple[MemoryManager, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "security",
        "Use the documented authentication policy.",
        metadata={"owner": "platform"},
    )
    assert entry is not None and entry.is_active
    original_hash = entry.approval_content_hash
    content_secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    metadata_secret = "sk-abcdefghijklmnopqrstuvwxyz"

    # Callers receive the live object today, so treat direct mutation as an
    # adversarial stale-approval state rather than assuming encapsulation.
    entry.content = f"Use leaked credential {content_secret} for auth."
    entry.metadata["client_secret_value"] = metadata_secret

    original_to_dict = type(entry).to_dict
    serialized_ids: list[str] = []

    def counted_to_dict(candidate: MemoryEntry) -> dict[str, Any]:
        serialized_ids.append(candidate.id)
        return original_to_dict(candidate)

    monkeypatch.setattr(type(entry), "to_dict", counted_to_dict)

    assert entry.approval_content_hash == original_hash
    assert entry.is_active is False
    assert manager.search("leaked credential") == []

    manager._save_scope(MemoryScope.PROJECT)

    memory_json = workspace / ".mini-code-memory" / "memory.json"
    memory_md = workspace / ".mini-code-memory" / "MEMORY.md"
    authority = json.loads(memory_json.read_text(encoding="utf-8"))
    persisted = authority["entries"][0]
    markdown = memory_md.read_text(encoding="utf-8")

    assert content_secret not in memory_json.read_text(encoding="utf-8")
    assert metadata_secret not in memory_json.read_text(encoding="utf-8")
    assert content_secret not in markdown
    assert metadata_secret not in markdown
    assert entry.content == persisted["content"]
    assert entry.metadata == persisted["metadata"]
    assert entry.approval_status == persisted["approval_status"] == "pending"
    assert entry.approval_content_hash == persisted["approval_content_hash"]
    assert entry.is_active is False
    assert entry.content not in markdown
    # The first pass sanitizes the adversarial live object; its resulting
    # approval/lifecycle transition changes the raw shape, so the safe
    # optimization must take the fresh-sanitization fallback.
    assert serialized_ids == [entry.id, entry.id]


def test_canonicalized_payloads_match_fresh_serialization_and_reload(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    first = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run the parser contract tests before release.",
        metadata={"runner": "pytest"},
    )
    second = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep the experimental parser rule pending review.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert first is not None and second is not None

    serialized = manager._canonicalize_scope_authority(MemoryScope.PROJECT)

    assert serialized == [
        entry.to_dict()
        for entry in manager.memories[MemoryScope.PROJECT].entries
    ]

    manager._save_scope(MemoryScope.PROJECT)
    authority = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )
    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )

    assert authority["entries"] == [
        entry.to_dict()
        for entry in reloaded.memories[MemoryScope.PROJECT].entries
    ]


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "client_secret_value",
        "client-secret-value",
        "credential_value",
        "refreshTokenValue",
        "apiKeyValue",
        "auth_token_value",
        "passwordValue",
        "servicePasswdValue",
        "database_pwd_value",
    ],
)
def test_sensitive_key_tokenization_redacts_value_variants(
    sensitive_key: str,
) -> None:
    marker = "SYNTHETIC-CREDENTIAL-4f80c3"

    sanitized = sanitize_for_persistence({sensitive_key: marker})

    assert sanitized == {sensitive_key: "[REDACTED]"}


def test_sensitive_key_tokenization_preserves_obvious_descriptors() -> None:
    ordinary = {
        "token_count": 42,
        "inputTokens": 1200,
        "credential_type": "oauth2",
        "password_policy": "minimum-length-12",
        "auth_method": "oauth2",
        "api_key_name": "DASHSCOPE_API_KEY",
        "secret_scanner_enabled": True,
    }

    assert sanitize_for_persistence(ordinary) == ordinary


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Authorization: Basic dXNlcjpwYXNz trailing-secret",
            "Authorization: Basic [REDACTED]",
        ),
        (
            "Authorization=Token token-part-one token-part-two",
            "Authorization=Token [REDACTED]",
        ),
        (
            "password = Basic correct horse battery staple",
            "password = [REDACTED]",
        ),
        (
            "Authorization=Bearer page-secret-token",
            "Authorization=Bearer [REDACTED]",
        ),
        (
            "password: correct horse, battery; staple",
            "password: [REDACTED]",
        ),
        (
            "Authorization: Token segment-one,segment-two;param=x",
            "Authorization: Token [REDACTED]",
        ),
    ],
    ids=[
        "basic",
        "token",
        "multiword-password",
        "bearer",
        "punctuated-password",
        "punctuated-authorization",
    ],
)
def test_secret_assignment_redacts_the_full_value(
    raw: str,
    expected: str,
) -> None:
    assert persistence_text_contains_secret(raw) is True
    assert sanitize_for_persistence(raw) == expected
    assert persistence_text_contains_secret(expected) is False


def test_live_pending_entry_cannot_self_approve(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run the focused parser contract tests.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None and entry.approval_status == "pending"

    entry.approval_status = "approved"

    assert entry.is_active is False
    assert manager.search("parser contract") == []
    manager._save_scope(MemoryScope.PROJECT)
    persisted = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )["entries"][0]
    markdown = (workspace / ".mini-code-memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert entry.approval_status == persisted["approval_status"] == "pending"
    assert entry.is_active is False
    assert entry.content not in markdown


def test_live_rejected_entry_cannot_restore_its_own_authority(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Never use the obsolete parser mode.",
    )
    assert entry is not None
    manager.reject_entry(entry.id, reason="obsolete guidance")
    assert entry.approval_status == "rejected"

    entry.approval_status = "approved"
    entry.lifecycle_status = "active"

    assert entry.is_active is False
    manager._save_scope(MemoryScope.PROJECT)
    persisted = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )["entries"][0]
    assert entry.approval_status == persisted["approval_status"] == "rejected"
    assert entry.lifecycle_status == persisted["lifecycle_status"] == "rejected"
    assert entry.is_active is False


def test_live_entry_cannot_remove_authoritative_lifecycle_or_curator_guards(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Archived parser advice requires explicit restore.",
        tier=MemoryTier.ARCHIVAL,
        lifecycle_status="deprecated",
    )
    assert entry is not None and entry.is_active is False
    previous_approval = entry.approval_status
    previous_lifecycle = entry.lifecycle_status
    entry.curator_locked = True
    manager._append_approval_audit(
        MemoryScope.PROJECT,
        entry,
        action="test_curator_archive",
        actor="curator",
        previous_approval=previous_approval,
        previous_lifecycle=previous_lifecycle,
        reason="synthetic authoritative archive",
    )
    manager._save_scope(MemoryScope.PROJECT)

    entry.lifecycle_status = "active"
    entry.curator_locked = False
    entry.tier = MemoryTier.SHORT_TERM

    assert entry.is_active is False
    manager._save_scope(MemoryScope.PROJECT)
    persisted = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )["entries"][0]
    markdown = (workspace / ".mini-code-memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert entry.lifecycle_status == persisted["lifecycle_status"] == "deprecated"
    assert entry.curator_locked is persisted["curator_locked"] is True
    assert entry.is_active is False
    assert entry.content not in markdown


def test_live_archival_entry_cannot_expand_authority_by_changing_only_tier(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Archived-only parser migration advice.",
        tier=MemoryTier.ARCHIVAL,
        lifecycle_status="active",
    )
    assert entry is not None
    assert entry.approval_status == "approved"
    assert entry.lifecycle_status == "active"
    assert entry.curator_locked is False
    assert entry.is_active is False

    entry.tier = MemoryTier.SHORT_TERM

    assert entry.is_active is False
    manager._save_scope(MemoryScope.PROJECT)
    persisted = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )["entries"][0]
    assert entry.tier == MemoryTier.ARCHIVAL
    assert persisted["tier"] == "archival"
    assert entry.is_active is False


def test_live_entry_cannot_forge_content_and_matching_approval_hash(
    isolated_manager: tuple[MemoryManager, Path],
) -> None:
    manager, workspace = isolated_manager
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use the reviewed parser migration sequence.",
    )
    assert entry is not None and entry.is_active

    entry.content = "Use an unreviewed parser migration sequence."
    entry.approval_content_hash = memory_module._approval_hash_for_entry(entry)

    assert entry.is_active is False
    manager._save_scope(MemoryScope.PROJECT)
    persisted = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )["entries"][0]
    assert entry.approval_status == persisted["approval_status"] == "pending"
    assert entry.is_active is False
