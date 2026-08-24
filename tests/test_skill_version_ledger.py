from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from minicode.advisory_lock import WINDOWS_LOCK_SENTINEL
from minicode.skill_versions import (
    SkillVersionLedger,
    SkillVersionLedgerError,
    observe_skill_catalog_safely,
)
from minicode.tools import create_default_tool_registry


def _skill(digest: str) -> dict[str, object]:
    return {
        "name": "memory-audit",
        "qualified_name": "auditing/memory-audit",
        "source": "project",
        "directory": "auditing",
        "content_digest": digest,
        "path": "/private/workspace/password=skill-secret/SKILL.md",
        "description": "password=private-description",
    }


def _empty_evidence() -> dict[str, object]:
    return {
        "ledgerVersion": 1,
        "mode": "shadow",
        "evaluations": [],
        "promotionEligible": False,
    }


def _positive_evidence(
    *,
    treatment_cost: str = "500",
    control_cost: str = "600",
    treatment_duration: int = 1_000,
    control_duration: int = 1_200,
) -> dict[str, object]:
    return {
        "ledgerVersion": 1,
        "mode": "shadow",
        "evaluations": [
            {
                "skill": {
                    "qualifiedName": "auditing/memory-audit",
                    "source": "project",
                    "directory": "auditing",
                    "contentDigest": "a" * 64,
                },
                "profile": {
                    "intentType": "review",
                    "actionType": "analyze",
                },
                "treatment": {
                    "runs": 5,
                    "cost": {
                        "observedRuns": 5,
                        "totalNanoUsd": treatment_cost,
                        "coverageComplete": True,
                    },
                    "latency": {
                        "observedRuns": 5,
                        "totalDurationMs": treatment_duration,
                        "coverageComplete": True,
                    },
                    "verification": {
                        "observedRuns": 0,
                        "passedRuns": 0,
                        "failedRuns": 0,
                        "coverageComplete": False,
                    },
                    "userSignal": {
                        "observedRuns": 0,
                        "acceptedRuns": 0,
                        "correctedRuns": 0,
                        "rejectedRuns": 0,
                        "coverageComplete": False,
                    },
                },
                "control": {
                    "runs": 5,
                    "cost": {
                        "observedRuns": 5,
                        "totalNanoUsd": control_cost,
                        "coverageComplete": True,
                    },
                    "latency": {
                        "observedRuns": 5,
                        "totalDurationMs": control_duration,
                        "coverageComplete": True,
                    },
                    "verification": {
                        "observedRuns": 0,
                        "passedRuns": 0,
                        "failedRuns": 0,
                        "coverageComplete": False,
                    },
                    "userSignal": {
                        "observedRuns": 0,
                        "acceptedRuns": 0,
                        "correctedRuns": 0,
                        "rejectedRuns": 0,
                        "coverageComplete": False,
                    },
                },
                "sampleGatePassed": True,
                "shadowStatus": "positive_signal",
                "promotionEligible": False,
            }
        ],
        "promotionEligible": False,
    }


def test_catalog_observation_persists_immutable_digest_lineage(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readings = iter(
        (
            datetime(2026, 7, 27, 1, 2, 3, tzinfo=timezone.utc),
            datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc),
        )
    )
    ledger = SkillVersionLedger(workspace, clock=lambda: next(readings))

    ledger.observe_catalog([_skill("a" * 64)])
    ledger.observe_catalog([_skill("b" * 64)])
    snapshot = SkillVersionLedger(workspace).snapshot(
        [_skill("b" * 64)],
        _empty_evidence(),
    )

    assert snapshot["ledgerVersion"] == 1
    assert snapshot["mode"] == "shadow"
    assert snapshot["promotionLocked"] is True
    assert len(snapshot["versions"]) == 2
    first, second = snapshot["versions"]
    assert re.fullmatch(r"skillv_[0-9a-f]{32}", first["versionId"])
    assert re.fullmatch(r"skillv_[0-9a-f]{32}", second["versionId"])
    assert first["versionId"] != second["versionId"]
    assert first["skill"] == {
        "qualifiedName": "auditing/memory-audit",
        "source": "project",
        "directory": "auditing",
        "contentDigest": "a" * 64,
    }
    assert second["skill"]["contentDigest"] == "b" * 64
    assert first["parentVersionId"] is None
    assert first["rollbackToVersionId"] is None
    assert second["parentVersionId"] == first["versionId"]
    assert second["rollbackToVersionId"] == first["versionId"]
    assert first["catalogCurrent"] is False
    assert second["catalogCurrent"] is True
    assert [item["firstObservedAt"] for item in snapshot["versions"]] == [
        "2026-07-27T01:02:03.000Z",
        "2026-07-28T01:02:03.000Z",
    ]
    assert all(item["status"] == "observed" for item in snapshot["versions"])
    assert all(item["createdFromRuns"] == [] for item in snapshot["versions"])
    assert snapshot["evaluation"]["versionCount"] == 2
    assert snapshot["evaluation"]["promotionCandidateCount"] == 0

    storage = workspace / ".mini-code" / "skill_versions.json"
    lock_path = workspace / ".mini-code" / ".skill_versions.lock"
    assert storage.exists()
    assert lock_path.read_bytes() == (
        WINDOWS_LOCK_SENTINEL if os.name == "nt" else b""
    )
    if os.name != "nt":
        assert storage.stat().st_mode & 0o777 == 0o600
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    serialized = storage.read_text(encoding="utf-8")
    json.loads(serialized)
    assert "password=skill-secret" not in serialized
    assert "private-description" not in serialized
    assert str(workspace) not in serialized


def test_corrupt_history_is_never_overwritten_by_catalog_observation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    storage = workspace / ".mini-code" / "skill_versions.json"
    storage.parent.mkdir(parents=True)
    corrupt = b'{"schemaVersion":1,"password":"history-secret"}\n'
    storage.write_bytes(corrupt)

    with pytest.raises(SkillVersionLedgerError):
        SkillVersionLedger(workspace).observe_catalog([_skill("a" * 64)])
    observe_skill_catalog_safely(workspace, [_skill("a" * 64)])

    assert storage.read_bytes() == corrupt


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics are POSIX-only")
def test_broken_storage_symlink_is_rejected_without_replacement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    storage = workspace / ".mini-code" / "skill_versions.json"
    storage.parent.mkdir(parents=True)
    storage.symlink_to(workspace / "missing-version-history.json")

    with pytest.raises(SkillVersionLedgerError):
        SkillVersionLedger(workspace).observe_catalog([_skill("a" * 64)])

    assert storage.is_symlink()
    assert not (workspace / "missing-version-history.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics are POSIX-only")
def test_symlinked_runtime_state_root_cannot_escape_the_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external-state"
    external.mkdir()
    (workspace / ".mini-code").symlink_to(
        external,
        target_is_directory=True,
    )

    with pytest.raises(SkillVersionLedgerError):
        SkillVersionLedger(workspace).observe_catalog([_skill("a" * 64)])

    assert list(external.iterdir()) == []


def test_runtime_catalog_construction_observes_real_skill_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_path = (
        workspace
        / ".mini-code"
        / "skills"
        / "memory-audit"
        / "SKILL.md"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: memory-audit\n"
        "description: Review persistent memory.\n"
        "---\n"
        "# Memory Audit\n",
        encoding="utf-8",
    )

    tools = create_default_tool_registry(str(workspace), runtime={})
    snapshot = SkillVersionLedger(workspace).snapshot(
        tools.get_skills(),
        _empty_evidence(),
    )

    assert len(snapshot["versions"]) == 1
    assert snapshot["versions"][0]["catalogCurrent"] is True
    assert snapshot["versions"][0]["skill"]["qualifiedName"] == "memory-audit"
    assert snapshot["versions"][0]["skill"]["contentDigest"] == tools.get_skills()[
        0
    ]["content_digest"]


def test_cross_skill_parent_linkage_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = {
        **_skill("b" * 64),
        "name": "pytest-debugging",
        "qualified_name": "debugging/pytest-debugging",
        "directory": "debugging",
    }
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64), other])
    storage = workspace / ".mini-code" / "skill_versions.json"
    raw = json.loads(storage.read_text(encoding="utf-8"))
    raw["versions"][1]["parentVersionId"] = raw["versions"][0]["versionId"]
    storage.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SkillVersionLedgerError):
        SkillVersionLedger(workspace).snapshot(
            [_skill("a" * 64), other],
            _empty_evidence(),
        )


def test_version_lineage_cannot_drop_the_immediate_parent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])
    ledger.observe_catalog([_skill("b" * 64)])
    storage = workspace / ".mini-code" / "skill_versions.json"
    raw = json.loads(storage.read_text(encoding="utf-8"))
    raw["versions"][1]["parentVersionId"] = None
    storage.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SkillVersionLedgerError):
        SkillVersionLedger(workspace).snapshot(
            [_skill("b" * 64)],
            _empty_evidence(),
        )


def test_positive_outcome_and_non_regressing_economics_still_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])

    version = ledger.snapshot(
        [_skill("a" * 64)],
        _positive_evidence(),
    )["versions"][0]

    assert version["evaluation"] == {
        "gatePolicyVersion": 2,
        "evidenceProfiles": 1,
        "gates": [
            {
                "name": "outcome",
                "status": "pass",
                "reason": "positive_signal_without_negative_profile",
            },
            {
                "name": "verification",
                "status": "unavailable",
                "reason": "verification_coverage_incomplete",
            },
            {
                "name": "user",
                "status": "unavailable",
                "reason": "user_signal_coverage_incomplete",
            },
            {
                "name": "cost",
                "status": "pass",
                "reason": "mean_cost_not_regressed",
            },
            {
                "name": "latency",
                "status": "pass",
                "reason": "mean_latency_not_regressed",
            },
        ],
        "allRequiredGatesPassed": False,
        "promotionCandidate": False,
        "promotionLocked": True,
    }


def test_complete_positive_signals_form_only_a_locked_shadow_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])
    evidence = _positive_evidence()
    treatment = evidence["evaluations"][0]["treatment"]
    treatment["verification"] = {
        "observedRuns": 5,
        "passedRuns": 5,
        "failedRuns": 0,
        "coverageComplete": True,
    }
    treatment["userSignal"] = {
        "observedRuns": 5,
        "acceptedRuns": 5,
        "correctedRuns": 0,
        "rejectedRuns": 0,
        "coverageComplete": True,
    }

    evaluation = ledger.snapshot(
        [_skill("a" * 64)],
        evidence,
    )["versions"][0]["evaluation"]
    gates = {gate["name"]: gate for gate in evaluation["gates"]}

    assert gates["verification"] == {
        "name": "verification",
        "status": "pass",
        "reason": "all_treatment_runs_verified",
    }
    assert gates["user"] == {
        "name": "user",
        "status": "pass",
        "reason": "all_treatment_runs_explicitly_accepted",
    }
    assert evaluation["allRequiredGatesPassed"] is True
    assert evaluation["promotionCandidate"] is True
    assert evaluation["promotionLocked"] is True


def test_failed_verification_and_user_correction_fail_their_gates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])
    evidence = _positive_evidence()
    treatment = evidence["evaluations"][0]["treatment"]
    treatment["verification"] = {
        "observedRuns": 5,
        "passedRuns": 4,
        "failedRuns": 1,
        "coverageComplete": True,
    }
    treatment["userSignal"] = {
        "observedRuns": 5,
        "acceptedRuns": 4,
        "correctedRuns": 1,
        "rejectedRuns": 0,
        "coverageComplete": True,
    }

    evaluation = ledger.snapshot(
        [_skill("a" * 64)],
        evidence,
    )["versions"][0]["evaluation"]
    gates = {gate["name"]: gate for gate in evaluation["gates"]}

    assert gates["verification"] == {
        "name": "verification",
        "status": "fail",
        "reason": "verification_failed",
    }
    assert gates["user"] == {
        "name": "user",
        "status": "fail",
        "reason": "user_correction_or_rejection_observed",
    }
    assert evaluation["promotionCandidate"] is False


@pytest.mark.parametrize(
    ("metric_name", "metric"),
    [
        (
            "verification",
            {
                "observedRuns": 5,
                "passedRuns": 5,
                "failedRuns": 1,
                "coverageComplete": True,
            },
        ),
        (
            "userSignal",
            {
                "observedRuns": 5,
                "acceptedRuns": 5,
                "correctedRuns": 1,
                "rejectedRuns": 0,
                "coverageComplete": True,
            },
        ),
    ],
)
def test_signal_gates_reject_inconsistent_counts(
    tmp_path: Path,
    metric_name: str,
    metric: dict[str, object],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])
    evidence = _positive_evidence()
    evidence["evaluations"][0]["treatment"][metric_name] = metric

    with pytest.raises(SkillVersionLedgerError):
        ledger.snapshot([_skill("a" * 64)], evidence)


def test_cost_or_latency_regression_blocks_the_corresponding_gate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])

    version = ledger.snapshot(
        [_skill("a" * 64)],
        _positive_evidence(
            treatment_cost="700",
            control_cost="600",
            treatment_duration=1_300,
            control_duration=1_200,
        ),
    )["versions"][0]
    gates = {
        gate["name"]: gate
        for gate in version["evaluation"]["gates"]
    }

    assert gates["outcome"]["status"] == "pass"
    assert gates["cost"] == {
        "name": "cost",
        "status": "fail",
        "reason": "mean_cost_regressed",
    }
    assert gates["latency"] == {
        "name": "latency",
        "status": "fail",
        "reason": "mean_latency_regressed",
    }
    assert version["evaluation"]["promotionCandidate"] is False


def test_gate_projection_rejects_unknown_task_profile_values(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)
    ledger.observe_catalog([_skill("a" * 64)])
    evidence = _positive_evidence()
    evidence["evaluations"][0]["profile"]["intentType"] = "invented-intent"

    with pytest.raises(SkillVersionLedgerError):
        ledger.snapshot([_skill("a" * 64)], evidence)
