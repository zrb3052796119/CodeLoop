"""Runner support for the memory-pollution study.

Two extensions over the frozen north-star runner contract:

1. A case-level ``env`` whitelist so one manifest can carry the reviewer
   ablation arms (``MINI_CODE_MEMORY_CONTRIBUTION_REVIEW`` = off/rule/llm)
   with the same save/restore discipline as ``NORTH_STAR_PYTHON``.
2. A post-turn memory-pool snapshot (private evidence) recording each
   entry's lifecycle/counters plus which entries were rendered per turn, so
   the study analyzer can measure pool self-correction without exposing any
   memory text in public results.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

import scripts.run_north_star_live as runner
import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from scripts.run_north_star_live import TurnEvidence, _execute_case


def _case(**extra: object) -> dict:
    case = {
        "id": "mpol-x1-support",
        "category": "memory-pollution",
        "mutability": "read_only",
        "files": {"README.md": "synthetic\n"},
        "turns": [{"prompt": "find the marker"}],
        "oracleIds": ["marker"],
        "oracles": [
            {"id": "marker", "kind": "response_contains", "values": ["MARKER"]}
        ],
    }
    case.update(extra)
    return case


def _manifest(**extra: object) -> dict:
    document = {"schemaVersion": 1, "suiteId": "suite-x", "cases": [_case(**extra)]}
    return document


# ── manifest env whitelist ─────────────────────────────────────────


@pytest.mark.parametrize("value", ["off", "rule", "llm"])
def test_manifest_accepts_review_env_whitelist(value: str) -> None:
    cases = runner._validate_manifest(
        _manifest(env={"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": value})
    )
    assert cases[0]["env"] == {"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": value}


def test_manifest_rejects_foreign_env_keys_and_values() -> None:
    with pytest.raises(ValueError, match="env"):
        runner._validate_manifest(_manifest(env={"OTHER_VARIABLE": "1"}))
    with pytest.raises(ValueError, match="env"):
        runner._validate_manifest(
            _manifest(env={"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": "maybe"})
        )
    with pytest.raises(ValueError, match="env"):
        runner._validate_manifest(_manifest(env=["not", "a", "dict"]))


# ── case execution applies and restores env ────────────────────────


def _turn_evidence() -> TurnEvidence:
    return TurnEvidence(
        run_id="run_" + "0" * 32,
        response="MARKER",
        event_types=(),
        events=(),
        model_calls=1,
        input_tokens=10,
        output_tokens=5,
    )


def test_execute_case_applies_and_restores_case_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW", raising=False)
    captured: dict[str, object] = {}

    def fake_run_turn(**kwargs: object) -> tuple[TurnEvidence, list[dict]]:
        captured["env"] = os.environ.get("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW")
        return _turn_evidence(), []

    monkeypatch.setattr(runner, "_run_turn", fake_run_turn)
    monkeypatch.setattr(
        runner, "_evaluate_oracle", lambda oracle, **kwargs: True
    )
    result, private = _execute_case(
        _case(env={"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": "off"}),
        suite_root=tmp_path / "suite",
        python_executable=sys.executable,
        runtime_profile_contract=None,
    )
    assert captured["env"] == "off"
    assert "MINI_CODE_MEMORY_CONTRIBUTION_REVIEW" not in os.environ
    assert result["status"] == "passed"
    assert "memoryPool" in private


def test_execute_case_restores_preexisting_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW", "llm")

    def fake_run_turn(**kwargs: object) -> tuple[TurnEvidence, list[dict]]:
        return _turn_evidence(), []

    monkeypatch.setattr(runner, "_run_turn", fake_run_turn)
    monkeypatch.setattr(
        runner, "_evaluate_oracle", lambda oracle, **kwargs: True
    )
    _execute_case(
        _case(env={"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": "off"}),
        suite_root=tmp_path / "suite",
        python_executable=sys.executable,
        runtime_profile_contract=None,
    )
    assert os.environ["MINI_CODE_MEMORY_CONTRIBUTION_REVIEW"] == "llm"


# ── memory pool snapshot ───────────────────────────────────────────


def test_tree_digest_ignores_runtime_lock_artifacts(tmp_path: Path) -> None:
    """The Memory authority lock is a runtime artifact, not an agent edit."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("synthetic\n", encoding="utf-8")
    before = runner._tree_digest(workspace)
    (workspace / ".minicode-memory-authority.lock").write_text("", encoding="utf-8")
    assert runner._tree_digest(workspace) == before


def _seeded_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, str]]:
    monkeypatch.setattr(
        memory_module, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    stale = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        "The policy lives at policies/auth-policy.md",
        tags=["auth"],
        source="seed",
    )
    correct = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        "The policy lives at docs/policies/auth-policy.md",
        tags=["auth"],
        source="seed",
    )
    assert stale is not None and correct is not None
    manager.record_corroborated_feedback(
        [stale.id],
        False,
        source="independent_verification",
        observation_id="obs-1",
        expected_versions=[(stale.id, stale.approval_content_hash)],
    )
    manager.record_corroborated_feedback(
        [stale.id],
        False,
        source="independent_verification",
        observation_id="obs-2",
        expected_versions=[(stale.id, stale.approval_content_hash)],
    )
    return workspace, {"stale": stale.id, "correct": correct.id}


def test_memory_pool_snapshot_captures_quarantine_and_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, ids = _seeded_workspace(tmp_path, monkeypatch)
    snapshot = runner._memory_pool_snapshot(
        workspace, rendered_ids_by_turn={0: (ids["stale"],), 1: ()}
    )
    assert snapshot is not None
    entries = {entry["id"]: entry for entry in snapshot["entries"]}
    stale_entry = entries[ids["stale"]]
    assert stale_entry["lifecycle_status"] == "rejected"
    assert stale_entry["corroborated_failure_count"] == 2
    assert stale_entry["content_sha256"] == hashlib.sha256(
        "The policy lives at policies/auth-policy.md".encode()
    ).hexdigest()
    assert "content" not in stale_entry
    correct_entry = entries[ids["correct"]]
    assert correct_entry["lifecycle_status"] != "rejected"
    assert snapshot["renderedByTurn"] == {"0": [ids["stale"]]}


def test_memory_pool_snapshot_empty_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "empty-ws"
    workspace.mkdir()
    snapshot = runner._memory_pool_snapshot(workspace, rendered_ids_by_turn={})
    assert snapshot == {"entries": [], "renderedByTurn": {}}
