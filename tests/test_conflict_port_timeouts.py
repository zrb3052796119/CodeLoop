"""Hard timeout behavior of the bounded conflict port runner."""

from __future__ import annotations

import threading

import pytest

from minicode.central_scheduler import CentralScheduler
from minicode.conflict_port_runner import (
    BoundedConflictPortRunner,
    ConflictPortCancelled,
    ConflictPortDeadlineExceeded,
)


def test_deadline_wins_over_an_ordinary_exception_that_finishes_late() -> None:
    now = [0.0]
    runner = BoundedConflictPortRunner(
        default_timeout_seconds=1.0,
        cancellation_poll_seconds=0.002,
        clock=lambda: now[0],
    )

    def fail_after_deadline():
        now[0] = 2.0
        raise RuntimeError("late callback failure")

    with pytest.raises(
        ConflictPortDeadlineExceeded,
        match="port_deadline_exceeded",
    ):
        runner.run(
            fail_after_deadline,
            port_name="run_adjudicator",
            operation_id="conflict_op_late_exception",
            timeout_seconds=None,
        )


@pytest.mark.parametrize("fatal_type", (KeyboardInterrupt, SystemExit))
def test_late_fatal_callback_exception_outweighs_the_deadline(fatal_type) -> None:
    def clock() -> float:
        if threading.current_thread().name.startswith("conflict-port-"):
            return 2.0
        return 0.0

    runner = BoundedConflictPortRunner(
        default_timeout_seconds=1.0,
        cancellation_poll_seconds=0.002,
        clock=clock,
    )

    def fail_fatally():
        raise fatal_type("fatal callback")

    with pytest.raises(fatal_type, match="fatal callback"):
        runner.run(
            fail_fatally,
            port_name="run_adjudicator",
            operation_id="conflict_op_late_fatal",
            timeout_seconds=None,
        )


@pytest.mark.parametrize("fatal_type", (KeyboardInterrupt, SystemExit))
def test_fatal_callback_outweighs_post_completion_clock_failure(
    fatal_type,
) -> None:
    def clock() -> float:
        if threading.current_thread().name.startswith("conflict-port-"):
            raise RuntimeError("clock failed after callback")
        return 0.0

    runner = BoundedConflictPortRunner(clock=clock)

    def fail_fatally():
        raise fatal_type("fatal callback")

    with pytest.raises(fatal_type, match="fatal callback"):
        runner.run(
            fail_fatally,
            port_name="run_adjudicator",
            operation_id="conflict_op_fatal_before_clock_failure",
            timeout_seconds=None,
        )


def test_late_typed_cancellation_outweighs_the_deadline() -> None:
    cause = RuntimeError("host cancelled conflict callback")

    def clock() -> float:
        if threading.current_thread().name.startswith("conflict-port-"):
            return 2.0
        return 0.0

    runner = BoundedConflictPortRunner(
        default_timeout_seconds=1.0,
        cancellation_poll_seconds=0.002,
        clock=clock,
    )

    def cancel():
        raise ConflictPortCancelled(cause=cause)

    with pytest.raises(ConflictPortCancelled) as raised:
        runner.run(
            cancel,
            port_name="run_adjudicator",
            operation_id="conflict_op_late_cancellation",
            timeout_seconds=None,
        )

    assert raised.value.cause is cause


def test_typed_cancellation_outweighs_post_completion_clock_failure() -> None:
    cause = RuntimeError("host cancelled conflict callback")

    def clock() -> float:
        if threading.current_thread().name.startswith("conflict-port-"):
            raise RuntimeError("clock failed after callback")
        return 0.0

    runner = BoundedConflictPortRunner(clock=clock)

    def cancel():
        raise ConflictPortCancelled(cause=cause)

    with pytest.raises(ConflictPortCancelled) as raised:
        runner.run(
            cancel,
            port_name="run_adjudicator",
            operation_id="conflict_op_cancel_before_clock_failure",
            timeout_seconds=None,
        )

    assert raised.value.cause is cause


@pytest.mark.parametrize("clock_failure", ("nan", "exception"))
def test_dynamic_invalid_clock_cannot_disable_the_hard_timeout(
    clock_failure: str,
) -> None:
    clock_calls = 0
    operation_release = threading.Event()
    finished = threading.Event()
    errors = []

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 0.0
        if clock_failure == "nan":
            return float("nan")
        raise RuntimeError("clock failed after startup")

    runner = BoundedConflictPortRunner(
        default_timeout_seconds=1.0,
        cancellation_poll_seconds=0.002,
        clock=clock,
    )

    def invoke_runner() -> None:
        try:
            runner.run(
                operation_release.wait,
                port_name="run_adjudicator",
                operation_id="conflict_op_dynamic_nan",
                timeout_seconds=None,
            )
        except BaseException as error:  # noqa: BLE001 - assert exact boundary
            errors.append(error)
        finally:
            finished.set()

    caller = threading.Thread(target=invoke_runner, daemon=True)
    caller.start()
    try:
        assert finished.wait(0.2)
        assert len(errors) == 1
        assert isinstance(errors[0], ConflictPortDeadlineExceeded)
        assert errors[0].reason == "port_clock_invalid"
    finally:
        operation_release.set()
        caller.join(0.2)


@pytest.mark.parametrize("fatal_type", (KeyboardInterrupt, SystemExit))
def test_runner_does_not_wrap_fatal_clock_exceptions(fatal_type) -> None:
    def fatal_clock() -> float:
        raise fatal_type()

    runner = BoundedConflictPortRunner(clock=fatal_clock)

    with pytest.raises(fatal_type):
        runner.run(
            lambda: None,
            port_name="run_adjudicator",
            operation_id="conflict_op_fatal_clock",
            timeout_seconds=None,
        )


def test_default_schedulers_share_one_process_authority_runner() -> None:
    first = CentralScheduler()
    second = CentralScheduler()

    assert first._conflict_port_runner is second._conflict_port_runner
    assert (
        first._conflict_observer_runner
        is second._conflict_observer_runner
    )
    assert first._conflict_observer_runner is not first._conflict_port_runner


def test_bounded_runner_requires_positive_finite_limits() -> None:
    with pytest.raises(ValueError, match="max_in_flight"):
        BoundedConflictPortRunner(max_in_flight=0)
    with pytest.raises(ValueError, match="default_timeout_seconds"):
        BoundedConflictPortRunner(default_timeout_seconds=float("inf"))
