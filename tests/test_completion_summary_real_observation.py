"""Offline regression against actual RunObservation output; no model or oracle.

This file deliberately remains outside tests/ while the paid run is frozen.
The success case must fail against the frozen runner until its metadata reader
uses the real tool.finished outcome contract.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import socket

import pytest

from minicode.run_events import emit_event_safely
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run


ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "scripts/run_requirement_completion_diagnostic.py").is_file())
SPEC = importlib.util.spec_from_file_location(
    "completion_summary_real_observation", ROOT / "scripts/run_requirement_completion_diagnostic.py"
)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)

OWNER = "implementation_" + "a" * 32
OTHER_OWNER = "implementation_" + "b" * 32
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
REQUIREMENT = "req_" + "c" * 20
# The reader validates its one public schedule ID; all contents and paths below
# still belong exclusively to this generic pytest temporary directory.
TRIAL = diagnostic.schedule()[0]["id"]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("This regression must not make network calls")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def journal_for(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "private" / TRIAL / "journal")
    return workspace, journal


def emit_decision(observation, *, attempt, approved, owner=OWNER, stop=False):
    # Uses the exact host event field names, and the real observation/journal
    # sanitizer. No JSON event files are fabricated by this test.
    payload = {
        "completionVersion": 1,
        "executorId": owner,
        "attempt": attempt,
        "status": "approved" if approved else "changes_required",
        "continuesSameExecutor": not approved and not stop,
        "patchDigest": None if stop else DIGEST_B if approved else DIGEST_A,
        "requirements": [{"id": REQUIREMENT, "status": "covered" if approved else "missing"}],
        "findings": [] if approved else ["A generic public requirement remains uncovered."],
    }
    if stop:
        payload["canContinue"] = False
        payload["checks"] = {"status": "inconclusive", "reason": "completion_checks_unavailable"}
    emit_event_safely(observation, "implementation.completion_decided", step=attempt + 1, payload=payload)


def observe_repair(tmp_path, *, is_error=False, approved_owner=OWNER):
    workspace, journal = journal_for(tmp_path)
    with observe_run(workspace=workspace, source="headless", title="Generic metadata regression",
                     journal_factory=lambda _: journal) as observation:
        run_id = observation.run_id
        assert run_id is not None
        emit_decision(observation, attempt=1, approved=False)
        observation.tool_started("edit_file")
        observation.tool_finished("edit_file", is_error=is_error)
        emit_decision(observation, attempt=2, approved=True, owner=approved_owner)
    # Reload persisted data through production, rather than inspecting a fake
    # observation sink or constructing a hand-written tool event schema.
    events = journal.list_events(run_id, limit=100).items
    started = next(event for event in events if event.type == "tool.started")
    finished = next(event for event in events if event.type == "tool.finished")
    assert finished.payload["outcome"] == ("error" if is_error else "success")
    assert "isError" not in finished.payload
    assert finished.payload["paired"] is True
    assert finished.payload["operationId"] == started.payload["operationId"]
    assert started.sequence < finished.sequence
    assert finished.step is None  # Real tool callback carries no Agent step.
    return diagnostic.completion_summary(tmp_path, TRIAL), run_id


@pytest.mark.parametrize("is_error,expected_successes", [(False, 1), (True, 0)])
def test_real_observation_tool_outcome_counts_success_only(tmp_path, is_error, expected_successes):
    summary, _ = observe_repair(tmp_path, is_error=is_error)
    assert summary["sameOwnerRepairPairs"][0]["successfulWriteToolEvents"] == expected_successes


def test_real_observation_preserves_owner_attempt_and_decision_fields(tmp_path):
    summary, run_id = observe_repair(tmp_path)
    assert summary["errors"] == []
    assert summary["decisionCount"] == 2
    assert summary["executorCount"] == 1
    assert summary["approvedFinalObserved"] is True
    first, second = summary["decisions"]
    assert [item["runId"] for item in (first, second)] == [run_id, run_id]
    assert [item["executorId"] for item in (first, second)] == [OWNER, OWNER]
    assert [item["attempt"] for item in (first, second)] == [1, 2]
    assert [item["status"] for item in (first, second)] == ["changes_required", "approved"]
    assert [item["step"] for item in (first, second)] == [2, 3]
    assert first["sequence"] < second["sequence"]
    assert [item["continuesSameExecutor"] for item in (first, second)] == [True, False]
    assert [item["canContinue"] for item in (first, second)] == [True, True]
    assert [item["requirementCount"] for item in (first, second)] == [1, 1]
    assert [item["requirementsOmitted"] for item in (first, second)] == [0, 0]
    assert [item["findingCount"] for item in (first, second)] == [1, 0]
    # Review decisions do not embed the public checks receipt. Missing metadata
    # is unknown, not a failed check or a fabricated passed count.
    assert [item["publicChecksStatus"] for item in (first, second)] == [None, None]
    assert first["patchDigest"] == DIGEST_A
    assert second["patchDigest"] == DIGEST_B
    pair = summary["sameOwnerRepairPairs"][0]
    assert pair["executorId"] == OWNER
    assert (pair["rejectedAttempt"], pair["acceptedAttempt"]) == (1, 2)
    assert pair["candidateDigestChanged"] is True


def test_other_owner_is_not_counted_as_same_executor_repair(tmp_path):
    summary, _ = observe_repair(tmp_path, approved_owner=OTHER_OWNER)
    assert summary["executorCount"] == 2
    assert summary["sameOwnerRepairPairs"] == []


def test_unavailable_checks_stop_preserves_null_digest_without_fake_repair(tmp_path):
    workspace, journal = journal_for(tmp_path)
    with observe_run(workspace=workspace, source="headless", title="Generic unavailable check",
                     journal_factory=lambda _: journal) as observation:
        emit_decision(observation, attempt=1, approved=False, stop=True)
    summary = diagnostic.completion_summary(tmp_path, TRIAL)
    assert summary["errors"] == []
    assert summary["decisionCount"] == 1
    assert summary["approvedFinalObserved"] is False
    assert summary["sameOwnerRepairPairs"] == []
    decision = summary["decisions"][0]
    assert decision["patchDigest"] is None
    assert decision["continuesSameExecutor"] is False
    assert decision["canContinue"] is False
    assert decision["publicChecksStatus"] == "inconclusive"
