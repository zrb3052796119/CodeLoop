"""Deterministic cleanup phase behavior; no model calls or real signals."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest

from scripts import three_arm_processes as guardian

assert Path(guardian.__file__).resolve() == (
    Path(__file__).resolve().parents[1] / "scripts/three_arm_processes.py"
)

ROOT = 57101
CHILD = 57102


class ClockTree:
    def __init__(self, *, freeze_after=0.0, exit_after=0.0):
        self.now = 0.0
        self.freeze_after = freeze_after
        self.exit_after = exit_after
        self.killed_at = {}
        self.signal_calls = []
        self.scan_limits = []
        self.scan_failure_phase = None
        self.late_child = False
        self.freeze_child = False
        self.stale_child = False
        self.records = {ROOT: ROOT}

    def send(self, pid, pgid, value):
        self.signal_calls.append((pid, pgid, value, self.now))
        if value == signal.SIGKILL:
            self.killed_at[pid] = self.now
        return True

    def sleep(self, duration):
        assert duration >= 0
        self.now += duration

    def table(self, *, timeout_seconds=2.0):
        self.scan_limits.append(timeout_seconds)
        phase = "kill-confirm" if ROOT in self.killed_at else "freeze"
        if self.scan_failure_phase == phase:
            self.scan_failure_phase = None
            self.now += timeout_seconds
            raise subprocess.TimeoutExpired("ps", timeout_seconds)
        rows = {}
        if ROOT not in self.killed_at:
            state = "T" if self.now >= self.freeze_after else "S"
            rows[ROOT] = guardian._Process(ROOT, 1, ROOT, state)
        elif self.now - self.killed_at[ROOT] < self.exit_after:
            rows[ROOT] = guardian._Process(ROOT, 1, ROOT, "S")
        if self.stale_child:
            rows[CHILD] = guardian._Process(CHILD, 1, CHILD + 1, "S")
        elif self.freeze_child or (self.late_child and phase == "kill-confirm"):
            if CHILD not in self.killed_at:
                stopped = any(
                    pid == CHILD and sig == signal.SIGSTOP
                    for pid, _, sig, _ in self.signal_calls
                )
                rows[CHILD] = guardian._Process(
                    CHILD, ROOT, CHILD, "T" if stopped else "S"
                )
            elif self.now - self.killed_at[CHILD] < self.exit_after:
                rows[CHILD] = guardian._Process(CHILD, ROOT, CHILD, "S")
        return rows

    def install(self, monkeypatch):
        monkeypatch.setattr(guardian.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(guardian.time, "time", lambda: 1_700_000_000 + self.now)
        monkeypatch.setattr(guardian.time, "sleep", self.sleep)
        monkeypatch.setattr(guardian, "_signal", self.send)
        monkeypatch.setattr(guardian, "_process_table", self.table)
        monkeypatch.setattr(guardian, "_recorded", lambda _path: (self.records, []))


def test_slow_successful_freeze_does_not_consume_kill_confirmation_allowance(
    monkeypatch,
):
    tree = ClockTree(freeze_after=0.04, exit_after=0.04)
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.05)
    assert result["cleanupComplete"] is True
    assert result["phase"] == "complete"
    assert result["freezeComplete"] and result["killConfirmComplete"]
    assert result["survivorPids"] == []
    assert tree.now >= 0.08  # Longer than the old shared 0.05 second deadline.
    assert tree.now <= 0.1
    assert all(0 < value <= 0.05 for value in tree.scan_limits)
    phases = result["phaseTimings"]
    assert phases["freeze"]["elapsedSeconds"] >= 0.04
    assert phases["kill-confirm"]["elapsedSeconds"] >= 0.04
    assert not phases["freeze"]["timedOut"]
    assert not phases["kill-confirm"]["timedOut"]
    assert [item["signal"] for item in result["signalEvents"]] == ["SIGSTOP", "SIGKILL"]
    assert result["signalEvents"][1]["elapsedSeconds"] >= 0.04
    assert result["lastSeenProcesses"][0]["phase"] == "kill-confirm"
    assert result["finalScanProcesses"] == []


def test_freeze_timeout_still_gets_exit_wait_but_cannot_become_success(monkeypatch):
    tree = ClockTree(freeze_after=999, exit_after=0.04)
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.05)
    assert result["cleanupComplete"] is False
    assert result["phase"] == "freeze"
    assert result["freezeComplete"] is False
    assert result["killConfirmComplete"] is True
    assert result["survivorPids"] == []
    assert result["phaseTimings"]["freeze"]["timedOut"] is True
    assert tree.now >= 0.09 and tree.now <= 0.11


def test_independent_phase_limits_and_late_exit_timeout_are_visible(monkeypatch):
    tree = ClockTree(exit_after=999)
    tree.install(monkeypatch)
    result = guardian.stop_trial(
        ROOT, "unused", freeze_timeout_seconds=0.03, kill_confirm_timeout_seconds=0.07
    )
    assert result["freezeComplete"] is True
    assert result["killConfirmComplete"] is False
    assert result["cleanupComplete"] is False
    assert result["phase"] == "kill-confirm"
    assert result["survivorPids"] == [ROOT]
    assert result["phaseTimings"]["kill-confirm"]["timedOut"] is True
    assert tree.now == pytest.approx(0.07)


def test_new_descendant_after_convergence_is_reclaimed_but_not_accepted(monkeypatch):
    tree = ClockTree(exit_after=0.01)
    tree.late_child = True
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert result["lateDiscoveredPids"] == [CHILD]
    assert result["killedPids"] == [ROOT, CHILD]
    assert result["survivorPids"] == []
    assert result["killConfirmComplete"] is True
    assert result["freezeComplete"] is False
    assert result["cleanupComplete"] is False
    child_events = [e for e in result["signalEvents"] if e["pid"] == CHILD]
    assert [e["signal"] for e in child_events] == ["SIGSTOP", "SIGKILL"]
    assert all(e["phase"] == "kill-confirm" for e in child_events)


def test_discovered_descendant_requires_fresh_stop_snapshot_and_root_killed_last(
    monkeypatch,
):
    tree = ClockTree()
    tree.freeze_child = True
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert result["cleanupComplete"] is True
    assert result["phaseTimings"]["freeze"]["scanCount"] >= 2
    kills = [pid for pid, _, sig, _ in tree.signal_calls if sig == signal.SIGKILL]
    assert kills == [CHILD, ROOT]
    assert result["lateDiscoveredPids"] == []


@pytest.mark.parametrize("phase", ["freeze", "kill-confirm"])
def test_scan_error_is_not_upgraded_by_known_process_exit(monkeypatch, phase):
    tree = ClockTree()
    tree.scan_failure_phase = phase
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.05)
    assert result["cleanupComplete"] is False
    assert result["killedPids"] == [ROOT]
    assert result["processScanErrors"] == [f"{phase}:TimeoutExpired"]
    assert result["unconfirmedPids"] == ([ROOT] if phase == "kill-confirm" else [])
    assert tree.now <= 0.1


def test_stale_receipt_does_not_signal_new_owner_of_reused_pid(monkeypatch):
    tree = ClockTree()
    tree.stale_child = True
    tree.records[CHILD] = CHILD
    tree.install(monkeypatch)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert result["cleanupComplete"] is True
    assert all(pid != CHILD for pid, _, _, _ in tree.signal_calls)


def test_evidence_errors_remain_fail_closed_even_when_all_known_processes_exit(
    monkeypatch,
):
    tree = ClockTree()
    tree.install(monkeypatch)
    monkeypatch.setattr(
        guardian,
        "_recorded",
        lambda _path: ({ROOT: ROOT}, ["invalid_process_evidence_line_2"]),
    )
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert result["freezeComplete"] and result["killConfirmComplete"]
    assert result["cleanupComplete"] is False
    assert result["survivorPids"] == []
    assert result["evidenceErrors"] == ["invalid_process_evidence_line_2"]


def test_interruption_during_freeze_still_attempts_kill(monkeypatch):
    tree = ClockTree()
    tree.install(monkeypatch)

    def interrupt(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(guardian, "_process_table", interrupt)
    with pytest.raises(KeyboardInterrupt):
        guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert ROOT in tree.killed_at


def test_signal_error_is_recorded_without_skipping_best_effort_kill(monkeypatch):
    tree = ClockTree()
    tree.install(monkeypatch)
    original = tree.send
    failed = False

    def denied_once(pid, pgid, value):
        nonlocal failed
        if value == signal.SIGSTOP and not failed:
            failed = True
            raise PermissionError("not model-visible diagnostic detail")
        return original(pid, pgid, value)

    monkeypatch.setattr(guardian, "_signal", denied_once)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.1)
    assert result["killedPids"] == [ROOT]
    assert result["killConfirmComplete"] is True
    assert result["cleanupComplete"] is False
    assert result["processScanErrors"] == ["freeze:SIGSTOP:PermissionError"]
    assert result["signalEvents"][0]["sent"] is False
    assert result["signalEvents"][0]["errorClass"] == "PermissionError"
    assert "not model-visible" not in str(result)


def test_snapshot_returned_after_freeze_deadline_cannot_claim_convergence(monkeypatch):
    tree = ClockTree()
    tree.install(monkeypatch)
    original = tree.table
    first = True

    def late_snapshot(**kwargs):
        nonlocal first
        if first:
            first = False
            tree.now += kwargs["timeout_seconds"] + 0.001
        return original(**kwargs)

    monkeypatch.setattr(guardian, "_process_table", late_snapshot)
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.05)
    assert result["freezeComplete"] is False
    assert result["phaseTimings"]["freeze"]["timedOut"] is True
    assert result["killConfirmComplete"] is True
    assert result["cleanupComplete"] is False


@pytest.mark.parametrize("updated_receipt", [False, True])
def test_observed_orphan_changing_pgid_cannot_disappear_from_cleanup_proof(
    monkeypatch, updated_receipt
):
    tree = ClockTree()
    tree.install(monkeypatch)
    parent = CHILD
    orphan = CHILD + 1
    escaped = False
    alive = {ROOT, parent, orphan}

    def table(**kwargs):
        rows = {}
        if ROOT in alive:
            rows[ROOT] = guardian._Process(ROOT, 1, ROOT, "T")
        if parent in alive:
            rows[parent] = guardian._Process(parent, ROOT, ROOT, "S")
        if orphan in alive:
            rows[orphan] = guardian._Process(
                orphan, 1 if escaped else parent, orphan if escaped else ROOT, "S"
            )
        return rows

    def send(pid, pgid, value):
        nonlocal escaped
        tree.signal_calls.append((pid, pgid, value, tree.now))
        if pid == parent and value == signal.SIGSTOP:
            # The intermediate parent exits while the discovered child both
            # reparents and setsid before its own STOP can be sent.
            alive.discard(parent)
            escaped = True
            return False
        current = table().get(pid)
        if current is None or current.pgid != pgid:
            return False
        if value == signal.SIGKILL:
            alive.discard(pid)
        return True

    monkeypatch.setattr(guardian, "_process_table", table)
    monkeypatch.setattr(guardian, "_signal", send)
    monkeypatch.setattr(guardian, "_recorded", lambda _: (
        {ROOT: ROOT, **({orphan: orphan} if updated_receipt and escaped else {})}, []
    ))
    result = guardian.stop_trial(ROOT, "unused", timeout_seconds=0.03)
    assert orphan in alive  # No actual OS process or signal is used by this test.
    assert result["cleanupComplete"] is False
    assert result["freezeComplete"] is False
    assert result["killConfirmComplete"] is False
    assert result["unconfirmedPids"] == [orphan]
    assert result["identityMismatches"][0]["pid"] == orphan
    assert result["identityMismatches"][0]["expectedPgid"] == ROOT
    assert result["identityMismatches"][0]["observedPgid"] == orphan
    assert all(not (pid == orphan and pgid == orphan)
               for pid, pgid, _, _ in tree.signal_calls)
