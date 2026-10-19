"""Bounded trial-process cleanup, including verification's new sessions.

Install ProcessEvidence in the trial interpreter, then call stop_trial from its
guardian before judging files, even after a normal trial exit. This handles
ordinary nested subprocesses and recorded children orphaned by trial exit. It
is not an OS sandbox against malicious daemonization or PID-reuse attacks.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from unittest.mock import patch


@dataclass(frozen=True)
class _Process:
    pid: int
    ppid: int
    pgid: int
    state: str


class ProcessEvidence(AbstractContextManager):
    """Append fsynced PID/PGID receipts immediately after each Popen spawn."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._patch = None

    def _record(self, pid: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return  # An already-finished process cannot write after the trial.
        encoded = (json.dumps({
            "pid": pid, "pgid": pgid, "creatorPid": os.getpid(),
            "spawnedEpoch": time.time(),
        }, separators=(",", ":")) + "\n").encode()
        with self.lock:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600,
            )
            try:
                if os.write(descriptor, encoded) != len(encoded):
                    raise OSError("partial process evidence write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def __enter__(self):
        if self._patch is not None:
            raise RuntimeError("ProcessEvidence cannot be entered twice")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        original = subprocess.Popen
        evidence = self

        class TrackedPopen(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                evidence._record(self.pid)

        self._record(os.getpid())
        self._patch = patch.object(subprocess, "Popen", TrackedPopen)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        if self._patch is not None:
            self._patch.stop()
            self._patch = None
        return False


def _process_table(*, timeout_seconds: float = 2.0) -> dict[int, _Process]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="],
        capture_output=True, text=True, check=True, timeout=timeout_seconds,
    )
    rows = {}
    for line in result.stdout.splitlines():
        pid, ppid, pgid, state = line.split(maxsplit=3)
        row = _Process(int(pid), int(ppid), int(pgid), state)
        rows[row.pid] = row
    return rows


def _recorded(path: Path) -> tuple[dict[int, int], list[str]]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return {}, []
    except OSError:
        return {}, ["unreadable_process_evidence"]
    records, errors = {}, []
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
            pid, pgid = row["pid"], row["pgid"]
            if type(pid) is not int or type(pgid) is not int or min(pid, pgid) <= 1:
                raise ValueError("invalid process identity")
            records[pid] = pgid
        except (ValueError, TypeError, KeyError):
            errors.append(f"invalid_process_evidence_line_{index}")
    return records, errors


def _signal(pid: int, pgid: int, value: signal.Signals) -> bool:
    # PGID rechecking also prevents a stale receipt for an exited child from
    # targeting a reused PID that belongs to an unrelated process group.
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        if os.getpgid(pid) != pgid:
            return False
        os.kill(pid, value)
        return True
    except ProcessLookupError:
        return False


def _candidates(
    table: dict[int, _Process],
    identities: dict[int, int],
) -> dict[int, int]:
    """Discover only matching receipts/known PIDs and their current descendants."""
    candidates = {
        child: pgid
        for child, pgid in identities.items()
        if child in table and table[child].pgid == pgid and child != os.getpid()
    }
    while True:
        additions = {
            row.pid: row.pgid
            for row in table.values()
            if row.ppid in candidates
            and row.pid not in candidates
            and row.pid != os.getpid()
        }
        if not additions:
            return candidates
        candidates.update(additions)


def stop_trial(
    pid: int,
    evidence_path: str | Path,
    *,
    expected_root_pgid: int | None = None,
    timeout_seconds: float = 5.0,
    freeze_timeout_seconds: float | None = None,
    kill_confirm_timeout_seconds: float | None = None,
) -> dict:
    """Freeze a trial tree, kill verified PIDs, and confirm writers are gone.

    Each phase has its own bounded wait, defaulting to ``timeout_seconds``.
    Thus the default cleanup waiting allowance is up to 5 + 5 seconds; it is
    separate from the agent execution deadline. Per-scan ps timeouts use the
    remaining phase allowance. A freeze timeout/error is never upgraded to a
    success merely because every known PID subsequently disappears.

    The trial must have been launched with start_new_session=True. Repeated
    discovery closes the race between a parent's scan and SIGSTOP. Processes
    discovered only after freezing are reclaimed but leave cleanup incomplete.
    Zombies cannot write and need their owning parent to reap them. This is
    bounded housekeeping, not an OS sandbox against daemonization/PID reuse.
    """
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise ValueError("invalid trial PID")
    limits = {
        "freeze": timeout_seconds
        if freeze_timeout_seconds is None
        else freeze_timeout_seconds,
        "kill-confirm": timeout_seconds
        if kill_confirm_timeout_seconds is None
        else kill_confirm_timeout_seconds,
    }
    for value in (timeout_seconds, *limits.values()):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("cleanup timeout must be positive")
    expected_root_pgid = pid if expected_root_pgid is None else expected_root_pgid
    if type(expected_root_pgid) is not int or expected_root_pgid <= 1:
        raise ValueError("invalid expected trial PGID")

    started = time.monotonic()
    records: dict[int, int] = {}
    known = {pid: expected_root_pgid}
    frozen: dict[int, int] = {}
    killed: set[int] = set()
    late: set[int] = set()
    identity_mismatches: dict[int, dict] = {}
    scan_errors: list[str] = []
    evidence_errors: list[str] = []
    signal_events: list[dict] = []
    last_seen: dict[int, dict] = {}
    final_scan: list[dict] = []
    phases: dict[str, dict] = {}
    freeze_complete = False
    kill_confirm_complete = False
    kill_scanned = False
    survivors: list[int] = []

    def begin_phase(name: str) -> tuple[float, float]:
        now = time.monotonic()
        phases[name] = {
            "startedEpoch": time.time(),
            "limitSeconds": limits[name],
            "elapsedSeconds": 0.0,
            "scanCount": 0,
            "timedOut": False,
        }
        return now, now + limits[name]

    def end_phase(name: str, phase_started: float, *, timed_out: bool) -> None:
        phases[name].update(
            {
                "finishedEpoch": time.time(),
                "elapsedSeconds": max(0.0, time.monotonic() - phase_started),
                "timedOut": timed_out,
            }
        )

    def send(child: int, pgid: int, value: signal.Signals, phase: str) -> bool:
        event = {
            "pid": child,
            "pgid": pgid,
            "signal": value.name,
            "phase": phase,
            "attemptedEpoch": time.time(),
            "elapsedSeconds": max(0.0, time.monotonic() - started),
            "sent": False,
        }
        try:
            event["sent"] = _signal(child, pgid, value)
        except OSError as error:
            event["errorClass"] = type(error).__name__
            scan_errors.append(f"{phase}:{value.name}:{type(error).__name__}")
        finally:
            event["finishedEpoch"] = time.time()
            signal_events.append(event)
        return event["sent"]

    def scan(
        phase: str, deadline: float
    ) -> tuple[dict[int, _Process], dict[int, int]] | None:
        nonlocal final_scan
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            table = _process_table(timeout_seconds=min(2.0, remaining))
            observed_records, errors = _recorded(Path(evidence_path))
            records.update(observed_records)
            evidence_errors.extend(e for e in errors if e not in evidence_errors)
            # The launch contract owns the root identity, not a receipt that
            # might be stale or malformed. _signal rechecks each PGID again.
            observed_epoch = time.time()
            for child, expected_pgid in known.items():
                row = table.get(child)
                if row is not None and row.pgid != expected_pgid:
                    identity_mismatches.setdefault(child, {
                        "pid": child,
                        "expectedPgid": expected_pgid,
                        "observedPgid": row.pgid,
                        "ppid": row.ppid,
                        "state": row.state,
                        "phase": phase,
                        "observedEpoch": observed_epoch,
                    })
            # A previously observed descendant can reparent and change PGID
            # before STOP. Its new identity may also be a reused PID: neither
            # treat it as exited nor signal the replacement identity. An updated
            # receipt cannot erase this ambiguity after the fact.
            candidates = _candidates(
                {child: row for child, row in table.items()
                 if child not in identity_mismatches},
                {**records, **known, pid: expected_root_pgid},
            )
            phases[phase]["scanCount"] += 1
            final_scan = []
            for child in sorted(candidates):
                row = table[child]
                observation = {
                    "pid": child,
                    "ppid": row.ppid,
                    "pgid": row.pgid,
                    "state": row.state,
                    "phase": phase,
                    "observedEpoch": observed_epoch,
                }
                last_seen[child] = observation
                final_scan.append(observation)
            return table, candidates
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            scan_errors.append(f"{phase}:{type(error).__name__}")
            return None

    phase_started, deadline = begin_phase("freeze")
    try:
        if send(pid, expected_root_pgid, signal.SIGSTOP, "freeze"):
            frozen[pid] = expected_root_pgid
        while time.monotonic() < deadline:
            observed = scan("freeze", deadline)
            if observed is None:
                break
            table, candidates = observed
            known.update(candidates)
            new = set(candidates) - frozen.keys()
            for child in sorted(new):
                if send(child, candidates[child], signal.SIGSTOP, "freeze"):
                    frozen[child] = candidates[child]
            settled = all(
                child not in table
                or table[child].pgid != pgid
                or table[child].state.startswith(("T", "Z"))
                for child, pgid in known.items()
            )
            # This snapshot predates any STOP signals sent above. Confirm newly
            # discovered PIDs in a subsequent scan even when the signal succeeded.
            if not new and settled and not identity_mismatches and time.monotonic() < deadline:
                freeze_complete = True
                break
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    finally:
        end_phase(
            "freeze",
            phase_started,
            timed_out=not freeze_complete and time.monotonic() >= deadline,
        )

        # Start a fresh confirmation deadline even when freezing consumed all of
        # its allowance. Reclaim every verified identity, including STOP failures.
        phase_started, deadline = begin_phase("kill-confirm")
        for child in sorted(known, key=lambda value: (value == pid, value)):
            if child in identity_mismatches:
                continue
            if send(child, known[child], signal.SIGKILL, "kill-confirm"):
                killed.add(child)
    while time.monotonic() < deadline:
        observed = scan("kill-confirm", deadline)
        if observed is None:
            break
        table, candidates = observed
        kill_scanned = True
        new = set(candidates) - known.keys()
        if new:
            # A late discovery invalidates the earlier convergence proof.
            # Reclaim it without silently declaring the whole tree settled.
            late.update(new)
            freeze_complete = False
            known.update(candidates)
            for child in sorted(new):
                if send(child, candidates[child], signal.SIGSTOP, "kill-confirm"):
                    frozen[child] = candidates[child]
                if send(child, candidates[child], signal.SIGKILL, "kill-confirm"):
                    killed.add(child)
        survivors = [
            child
            for child, pgid in known.items()
            if child in table
            and table[child].pgid == pgid
            and not table[child].state.startswith("Z")
        ]
        if not survivors and time.monotonic() < deadline:
            kill_confirm_complete = True
            break
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    end_phase(
        "kill-confirm",
        phase_started,
        timed_out=not kill_confirm_complete and time.monotonic() >= deadline,
    )
    # An unsuccessful scan is unknown, not evidence that a PID survived.
    unconfirmed = sorted(set(known) if not kill_scanned else identity_mismatches)
    if identity_mismatches:
        freeze_complete = False
        kill_confirm_complete = False

    cleanup_complete = (
        freeze_complete
        and kill_confirm_complete
        and not (evidence_errors or scan_errors or late or identity_mismatches)
    )
    return {
        "rootPid": pid,
        "recordedPids": sorted(records),
        "stoppedPids": sorted(frozen),
        "killedPids": sorted(killed),
        "survivorPids": sorted(survivors),
        "evidenceErrors": evidence_errors,
        "processScanErrors": scan_errors,
        "cleanupComplete": cleanup_complete,
        "phase": "complete"
        if cleanup_complete
        else ("freeze" if not freeze_complete else "kill-confirm"),
        "freezeComplete": freeze_complete,
        "killConfirmComplete": kill_confirm_complete,
        "lateDiscoveredPids": sorted(late),
        "unconfirmedPids": unconfirmed,
        "identityMismatches": [
            identity_mismatches[child] for child in sorted(identity_mismatches)
        ],
        "phaseTimings": phases,
        "signalEvents": signal_events,
        "lastSeenProcesses": [last_seen[child] for child in sorted(last_seen)],
        "finalScanProcesses": final_scan,
    }
