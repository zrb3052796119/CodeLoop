"""The fix-verified loop must be recoverable from the trace the agent records.

No runtime code path emits a ``recovery``/``fix`` event -- only benchmark
fixtures do -- so ``RecoveryEvidence`` was never produced in production. Every
claim built on it (``recovery``, and ``root_cause`` at ``confirmed``) was
therefore unreachable, which left bare ``error_pattern`` as the only claim type
with a live feed and explains why a real store filled up with nothing else.

The loop is already fully present in the trace: a call fails, files change, the
same call succeeds. These tests pin reading it back out, and pin the boundaries
that keep the reading honest.
"""

from __future__ import annotations

from typing import Any

from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_synthesis import (
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _call(
    index: int,
    call_id: str,
    tool: str,
    *,
    ok: bool,
    command: str | None = None,
    path: str | None = None,
    changed: bool = False,
    message: str = "",
) -> list[dict[str, Any]]:
    """Emit the tool_call/tool_result(/error) triple agent_loop.py writes."""
    tool_input: dict[str, Any] = {}
    if command is not None:
        tool_input["command"] = command
    if path is not None:
        tool_input["path"] = path
    file_fields: dict[str, Any] = {}
    if path is not None:
        file_fields["files"] = [path]
        file_fields["files_changed" if changed else "files_read"] = [path]
    events: list[dict[str, Any]] = [
        {
            "event_id": f"e{index}",
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool,
            "input": tool_input,
            **file_fields,
        },
        {
            "event_id": f"e{index + 1}",
            "type": "tool_result",
            "call_id": call_id,
            "tool_name": tool,
            "status": "success" if ok else "error",
            "is_error": not ok,
            "output_summary": message or ("ok" if ok else "failed"),
            **file_fields,
        },
    ]
    if not ok:
        events.append(
            {
                "event_id": f"e{index + 2}",
                "type": "error",
                "call_id": call_id,
                "tool_name": tool,
                "error_type": "CommandError",
                "message": message or "failed",
                **file_fields,
            }
        )
    return events


def _done(index: int) -> dict[str, Any]:
    return {
        "event_id": f"e{index}",
        "type": "task_result",
        "status": "success",
        "had_errors": True,
        "errors_recovered": True,
        "tool_error_count": 1,
    }


FAILING_TEST = "FAILED tests/test_lease.py::test_renew - StaleToken not refreshed"
PYTEST = "pytest tests/test_lease.py -q"


def _fix_verified_trace() -> list[dict[str, Any]]:
    return [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(20, "c3", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(30),
    ]


def _evaluate(trace: list[dict[str, Any]], task: str = "Fix the failing lease test"):
    evidence = TraceEvidenceExtractor().extract(task, trace)
    candidate = RuleReflectionSynthesizer().synthesize(task, evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    return evidence, ReflectionValueGate().evaluate(candidate, validation, evidence)


def test_fix_then_passing_rerun_yields_a_confirmed_recovery() -> None:
    evidence, decision = _evaluate(_fix_verified_trace())

    assert len(evidence.recoveries) == 1
    recovery = evidence.recoveries[0]
    assert recovery.epistemic_status == "confirmed"
    assert recovery.files_changed == ("src/lease.py",)
    assert recovery.related_error_ids
    # The payload a durable memory needs: what broke, what was changed, what
    # then passed -- not "web_search reported an error".
    assert "src/lease.py" in recovery.action
    assert "pytest tests/test_lease.py" in recovery.action

    assert decision.accepted is True
    assert decision.durable_signals == [
        "confirmed_error_recovery_verified",
        "verified_solution",
    ]


def test_the_recovery_excludes_the_passing_rerun_from_its_own_evidence() -> None:
    """Otherwise the verification cannot be seen as following the fix.

    ``_passed_verifications_after`` compares max event positions, so folding
    the successful re-run into the recovery makes the two simultaneous and the
    claim never reaches ``confirmed``.
    """
    evidence, _ = _evaluate(_fix_verified_trace())

    recovery = evidence.recoveries[0]
    positions = evidence.event_positions
    recovery_end = max(positions[event_id] for event_id in recovery.event_ids)
    passed = [item for item in evidence.verification if item.result == "passed"]
    assert passed
    assert all(
        max(positions[event_id] for event_id in item.event_ids) > recovery_end
        for item in passed
    )


def test_a_bare_retry_is_flakiness_not_a_recovery() -> None:
    """Same call, no intervening change: nothing was learned, nothing persists."""
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(20),
    ]

    evidence, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert "verified_solution" not in decision.durable_signals


def _transient_failure_with_unrelated_edit(message: str) -> list[dict[str, Any]]:
    return [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=message),
        *_call(10, "c2", "edit_file", ok=True, path="src/unrelated.py", changed=True),
        *_call(20, "c3", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(30),
    ]


def test_http_503_then_unrelated_file_edit_is_not_a_confirmed_recovery() -> None:
    evidence, decision = _evaluate(
        _transient_failure_with_unrelated_edit("HTTP 503 Service Unavailable")
    )

    assert evidence.recoveries == []
    assert decision.accepted is False
    assert "verified_solution" not in decision.durable_signals


def test_network_timeout_then_unrelated_file_edit_is_not_a_recovery() -> None:
    evidence, decision = _evaluate(
        _transient_failure_with_unrelated_edit(
            "Connection timed out while loading the remote fixture"
        )
    )

    assert evidence.recoveries == []
    assert decision.accepted is False
    assert "verified_solution" not in decision.durable_signals


def test_lock_contention_then_unrelated_file_edit_is_not_a_recovery() -> None:
    evidence, decision = _evaluate(
        _transient_failure_with_unrelated_edit("database table is locked")
    )

    assert evidence.recoveries == []
    assert decision.accepted is False
    assert "verified_solution" not in decision.durable_signals


def test_success_on_a_different_target_is_not_a_recovery() -> None:
    """A later unrelated command must not be read as having fixed the failure."""
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(20, "c3", "run_command", ok=True, command="ruff check .", message="ok"),
        _done(30),
    ]

    evidence, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_flag_only_differences_still_match_the_same_target() -> None:
    """`pytest -q x` and `pytest x --lf` are the same target; flags are noise."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest -q tests/test_lease.py",
            message=FAILING_TEST,
        ),
        *_call(10, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest tests/test_lease.py --lf",
            message="1 passed",
        ),
        _done(30),
    ]

    evidence, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1


def test_targetless_tools_never_pair_into_a_recovery() -> None:
    """web_search names no file and no command, so two of them cannot pair.

    This is what stops the environment failures that filled the real store from
    re-entering through the recovery path once an unrelated edit happens to sit
    between them.
    """
    trace = [
        *_call(
            1,
            "c1",
            "web_search",
            ok=False,
            message="error[search_unavailable]: bing=redirect_blocked",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(20, "c3", "web_search", ok=True, message="3 results"),
        _done(30),
    ]

    evidence, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_one_failure_is_claimed_by_at_most_one_recovery() -> None:
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(20, "c3", "run_command", ok=True, command=PYTEST, message="1 passed"),
        *_call(30, "c4", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(40),
    ]

    evidence, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1


def test_repeated_failures_before_one_fix_yield_a_single_recovery() -> None:
    """One root cause must not fragment into one entry per failed attempt."""
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(20, "c3", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(30, "c4", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(40),
    ]

    evidence, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    # Both attempts are still credited to the one repair.
    assert len(evidence.recoveries[0].related_error_ids) >= 2
    assert decision.accepted is True


def test_a_change_that_did_not_stop_the_failure_is_not_the_repair() -> None:
    """An edit followed by another failure demonstrably did not fix anything."""
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(10, "c2", "edit_file", ok=True, path="src/wrong.py", changed=True),
        *_call(20, "c3", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        *_call(30, "c4", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(40, "c5", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(50),
    ]

    evidence, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].files_changed == ("src/lease.py",)


def test_an_explicit_recovery_event_is_still_honoured() -> None:
    """The declared contract keeps working; derivation only adds to it."""
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        {
            "event_id": "e10",
            "type": "recovery",
            "call_id": "c1",
            "tool_name": "run_command",
            "action": "Refreshed the fencing token before renewal.",
            "related_error_call_ids": ["c1"],
        },
        _done(20),
    ]

    evidence, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].action == "Refreshed the fencing token before renewal."


def test_derivation_does_not_double_count_an_explicitly_reported_recovery() -> None:
    trace = [
        *_call(1, "c1", "run_command", ok=False, command=PYTEST, message=FAILING_TEST),
        {
            "event_id": "e10",
            "type": "recovery",
            "call_id": "c1",
            "tool_name": "run_command",
            "action": "Refreshed the fencing token before renewal.",
            "related_error_call_ids": ["c1"],
        },
        *_call(20, "c2", "edit_file", ok=True, path="src/lease.py", changed=True),
        *_call(30, "c3", "run_command", ok=True, command=PYTEST, message="1 passed"),
        _done(40),
    ]

    evidence, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1
