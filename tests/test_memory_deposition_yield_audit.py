"""Acceptance checks through real synthesis, persistence and prompt rendering."""

from pathlib import Path

import pytest

from scripts.memory_deposition_yield_audit import (
    FORMS,
    reconstructed_sessions,
    run_audit,
)


@pytest.fixture(scope="module")
def audit(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return run_audit(tmp_path_factory.mktemp("yield-audit") / "fresh")


def test_reconstructed_five_sessions_capture_all_six_forms(audit: dict) -> None:
    assert audit["audit_kind"] == "reconstructed_real_pipeline"
    assert audit["live_model_calls"] == 0
    assert len(reconstructed_sessions()) == 5
    result = audit["modes"]["inject"]
    assert set(result["forms"]) == set(FORMS)
    assert result["captured_immediately"] == 4
    assert result["captured_forms"] == 6
    assert result["capture_rate"] == 1.0
    assert result["forms"]["policy"]["durable_ids"]
    assert result["forms"]["syntax_repair"]["stored_draft_ids"]
    assert len(result["funnel_logs"]) == 5


def test_aged_failures_render_without_changing_pending_authority(audit: dict) -> None:
    result = audit["modes"]["inject"]
    assert result["pending_entries_unchanged"]
    assert result["pending_entries_checked"] >= 2
    for name in ("single_tool_failure", "permission_denied"):
        form = result["forms"][name]
        assert form["fresh_prompt_draft_ids"] == []
        assert form["aged_prompt_draft_ids"]
        assert all(entry["approval_status"] == "pending" for entry in form["entries"])
        assert all(not entry["metadata"].get("deposition_draft") for entry in form["entries"])
        assert form["aged_prompt_draft_ids"][0] not in result["eight_day_render"]["canonical_ids"]


def test_mixed_technical_work_keeps_user_preference_and_declared_dependency_honest(audit: dict) -> None:
    result = audit["modes"]["inject"]
    preference = result["forms"]["user_preference"]
    assert preference["entries"]
    assert all(entry["scope"] == "user" for entry in preference["entries"])
    assert preference["entries"][0]["id"] in result["fresh_render"]["canonical_ids"]
    dependency = result["forms"]["declared_dependency"]
    assert dependency["fresh_prompt_draft_ids"]
    assert dependency["durable_ids"] == []
    for entry in dependency["entries"]:
        assert entry["approval_status"] == "pending"
        claim = entry["metadata"]["structured_reflection"]["claims"][0]
        assert claim["epistemic_status"] == "inferred"
        assert "not yet exercised" in " ".join(claim["limitations"])


def test_strict_and_capture_never_render_pending_drafts(audit: dict) -> None:
    strict = audit["modes"]["strict"]
    assert not strict["forms"]["user_preference"]["stored"]
    assert not strict["forms"]["declared_dependency"]["stored"]
    for mode in ("strict", "capture"):
        result = audit["modes"][mode]
        assert result["fresh_render"]["draft_ids"] == []
        assert result["eight_day_render"]["draft_ids"] == []
        assert result["pending_entries_unchanged"]
    capture = audit["modes"]["capture"]
    assert capture["forms"]["user_preference"]["entries"][0]["approval_status"] == "pending"


def test_audit_refuses_to_reset_an_existing_workspace(tmp_path: Path) -> None:
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("existing user data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_audit(tmp_path)
    assert sentinel.read_text(encoding="utf-8") == "existing user data"
