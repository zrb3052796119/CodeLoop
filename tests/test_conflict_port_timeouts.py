"""Hard timeout behavior for host-owned conflict authority ports."""

from __future__ import annotations

import threading
import time

import pytest

import minicode.central_scheduler as central_scheduler_module
from minicode.central_scheduler import CentralScheduler
from minicode.conflict_resolution import (
    BoundedConflictPortRunner,
    ConflictBallot,
    ConflictCoordinator,
    ConflictPortCancelled,
    ConflictPortDeadlineExceeded,
    ConflictResolution,
    ResolutionRoute,
)
from minicode.multi_agent_contracts import parse_conflict_package
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)


def _package(
    *,
    risk: str = "low",
    kind: str = "fact",
    vote: bool = True,
):
    return parse_conflict_package(
        {
            "conflictVersion": 1,
            "conflictId": "conflict_timeout1",
            "workflowId": "wf_timeout1",
            "taskId": "task_timeout1",
            "kind": kind,
            "risk": risk,
            "question": {
                "questionId": "question_timeout1",
                "prompt": "Choose the supported option.",
                "choices": ["A", "B"],
            },
            "claims": [
                {
                    "claimId": "claim_timeout_a",
                    "choice": "A",
                    "statement": "Evidence supports A.",
                    "evidenceRefs": ["artifact_a"],
                },
                {
                    "claimId": "claim_timeout_b",
                    "choice": "B",
                    "statement": "Evidence supports B.",
                    "evidenceRefs": ["artifact_b"],
                },
            ],
            "evidenceDigest": "sha256:" + "d" * 64,
            "voteEligible": vote,
        },
        workflow_id="wf_timeout1",
        task_id="task_timeout1",
    )


def _runner(*, max_in_flight: int = 4) -> BoundedConflictPortRunner:
    return BoundedConflictPortRunner(
        max_in_flight=max_in_flight,
        default_timeout_seconds=0.03,
        cancellation_poll_seconds=0.002,
    )


def _assert_hard_timeout(started: threading.Event, result, elapsed: float) -> None:
    assert started.wait(0.2)
    assert elapsed < 0.5
    assert result.status == "timed_out"
    assert result.reason == "port_deadline_exceeded"
    assert result.choice is None
    assert sum(
        transition.outcome == "timed_out"
        for transition in result.transitions
    ) == 1


def test_never_returning_ballot_port_is_hard_timed_out() -> None:
    started = threading.Event()
    release = threading.Event()

    def collect(request, _frozen_package):
        started.set()
        release.wait()
        assert request.voter_id is not None
        assert request.independence_key is not None
        return ConflictBallot(
            voter_id=request.voter_id,
            independence_key=request.independence_key,
            question_id=request.question_id,
            evidence_digest=request.evidence_digest,
            choice="A",
            evidence_refs=("artifact_a",),
            confidence="high",
            sealed=True,
        )

    before = time.monotonic()
    result = CentralScheduler(conflict_port_runner=_runner()).execute_conflict(
        _package(),
        collect_ballot=collect,
        abort_exceptions=(TimeoutError, RuntimeError),
    )
    elapsed = time.monotonic() - before

    _assert_hard_timeout(started, result, elapsed)
    release.set()


def test_never_returning_adjudicator_port_is_hard_timed_out() -> None:
    started = threading.Event()
    release = threading.Event()

    def adjudicate(_request, _frozen_package):
        started.set()
        release.wait()
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "late_adjudication",
        )

    before = time.monotonic()
    result = ConflictCoordinator().execute(
        _package(vote=False),
        run_adjudicator=adjudicate,
        port_runner=_runner(),
    )
    elapsed = time.monotonic() - before

    _assert_hard_timeout(started, result, elapsed)
    release.set()


def test_never_returning_human_port_is_hard_timed_out() -> None:
    started = threading.Event()
    release = threading.Event()

    def request_human(_request, _frozen_package):
        started.set()
        release.wait()
        return ConflictResolution(
            "resolved",
            ResolutionRoute.HUMAN,
            "A",
            "late_human_decision",
        )

    before = time.monotonic()
    result = ConflictCoordinator().execute(
        _package(risk="critical", vote=False),
        request_human=request_human,
        port_runner=_runner(),
        abort_exceptions=(TimeoutError, RuntimeError),
    )
    elapsed = time.monotonic() - before

    _assert_hard_timeout(started, result, elapsed)
    release.set()


def test_global_deadline_budget_does_not_cross_clock_domains() -> None:
    authority_calls = []
    runner = BoundedConflictPortRunner(
        default_timeout_seconds=30.0,
        cancellation_poll_seconds=0.002,
        clock=lambda: 1_000_000.0,
    )

    def adjudicate(_request, _frozen_package):
        authority_calls.append(True)
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "expert_adjudication",
        )

    result = ConflictCoordinator().execute(
        _package(vote=False),
        run_adjudicator=adjudicate,
        deadline_monotonic=105.0,
        clock=lambda: 100.0,
        port_runner=runner,
    )

    assert result.status == "resolved"
    assert result.choice == "A"
    assert authority_calls == [True]


def test_late_transition_observer_cannot_change_the_frozen_local_outcome() -> None:
    started = threading.Event()
    release = threading.Event()
    delivered = []

    def on_transition(transition):
        started.set()
        release.wait()
        delivered.append(transition)

    try:
        before = time.monotonic()
        result = ConflictCoordinator().execute(
            _package(vote=False),
            deterministic_choice="A",
            on_transition=on_transition,
            observer_runner=_runner(),
        )
        elapsed = time.monotonic() - before

        assert started.wait(0.2)
        assert elapsed < 0.5
        assert result.status == "resolved"
        assert result.route is ResolutionRoute.DETERMINISTIC
        assert result.choice == "A"
        assert [item.outcome for item in result.transitions] == ["resolved"]
        assert delivered == []
        frozen_transitions = result.transitions
    finally:
        release.set()

    deadline = time.monotonic() + 0.2
    while not delivered and time.monotonic() < deadline:
        time.sleep(0.001)
    assert tuple(delivered) == frozen_transitions


def test_transition_observer_failure_does_not_rewrite_the_decision() -> None:
    delivered = []

    def broken_observer(transition):
        delivered.append(transition)
        raise RuntimeError("observer unavailable after delivery")

    result = ConflictCoordinator().execute(
        _package(vote=False),
        deterministic_choice="A",
        on_transition=broken_observer,
        observer_runner=_runner(),
    )

    assert result.status == "resolved"
    assert result.choice == "A"
    assert tuple(delivered) == result.transitions


def test_transition_observer_preserves_registered_abort_exceptions() -> None:
    class HostCancelled(RuntimeError):
        pass

    def cancel_observer(_transition):
        raise HostCancelled("host cancelled observer")

    with pytest.raises(HostCancelled, match="host cancelled"):
        ConflictCoordinator().execute(
            _package(vote=False),
            deterministic_choice="A",
            on_transition=cancel_observer,
            observer_runner=_runner(),
            abort_exceptions=(HostCancelled,),
        )


def test_observer_runner_infrastructure_failure_is_non_authoritative_with_broad_abort() -> None:
    started = threading.Event()
    release = threading.Event()

    def hung_observer(_transition):
        started.set()
        release.wait()

    try:
        result = ConflictCoordinator().execute(
            _package(vote=False),
            deterministic_choice="A",
            on_transition=hung_observer,
            observer_runner=_runner(),
            abort_exceptions=(TimeoutError, RuntimeError),
        )

        assert started.wait(0.2)
        assert result.status == "resolved"
        assert result.choice == "A"
    finally:
        release.set()


def test_direct_legacy_observer_without_runners_remains_synchronous() -> None:
    delivered = []

    result = ConflictCoordinator().execute(
        _package(vote=False),
        deterministic_choice="A",
        on_transition=delivered.append,
    )

    assert tuple(delivered) == result.transitions


def test_direct_bounded_authority_requires_an_independent_observer_runner() -> None:
    with pytest.raises(ValueError, match="observer_runner"):
        ConflictCoordinator().execute(
            _package(vote=False),
            deterministic_choice="A",
            on_transition=lambda _transition: None,
            port_runner=_runner(),
        )


def test_hung_transition_observer_cannot_consume_authority_capacity() -> None:
    runner = _runner(max_in_flight=1)
    observer_started = threading.Event()
    observer_release = threading.Event()
    authority_calls = []

    def observe(_transition):
        observer_started.set()
        observer_release.wait()

    def adjudicate(_request, _frozen_package):
        authority_calls.append(True)
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "expert_adjudication",
        )

    try:
        result = ConflictCoordinator().execute(
            _package(vote=False),
            run_adjudicator=adjudicate,
            on_transition=observe,
            port_runner=runner,
            observer_runner=_runner(max_in_flight=1),
        )

        assert observer_started.wait(0.2)
        assert result.status == "resolved"
        assert result.choice == "A"
        assert authority_calls == [True]
    finally:
        observer_release.set()


def test_runner_capacity_prevents_unbounded_hung_port_threads() -> None:
    runner = _runner(max_in_flight=1)
    first_started = threading.Event()
    first_release = threading.Event()
    second_called = threading.Event()

    def first_port(_request, _frozen_package):
        first_started.set()
        first_release.wait()

    first = ConflictCoordinator().execute(
        _package(vote=False),
        run_adjudicator=first_port,
        port_runner=runner,
    )
    assert first_started.wait(0.2)
    assert first.status == "timed_out"

    def second_port(_request, _frozen_package):
        second_called.set()

    before = time.monotonic()
    second = ConflictCoordinator().execute(
        _package(vote=False),
        run_adjudicator=second_port,
        port_runner=runner,
    )

    assert time.monotonic() - before < 0.2
    assert second.status == "failed"
    assert second.reason == "port_runner_capacity_exhausted"
    assert not second_called.is_set()
    first_release.set()


def test_runner_timeout_is_not_intercepted_by_a_broad_abort_tuple() -> None:
    started = threading.Event()
    release = threading.Event()

    def adjudicate(_request, _frozen_package):
        started.set()
        release.wait()

    try:
        result = ConflictCoordinator().execute(
            _package(vote=False),
            run_adjudicator=adjudicate,
            port_runner=_runner(),
            abort_exceptions=(TimeoutError,),
        )

        assert started.wait(0.2)
        assert result.status == "timed_out"
        assert result.reason == "port_deadline_exceeded"
    finally:
        release.set()


def test_runner_capacity_is_not_intercepted_by_a_broad_abort_tuple() -> None:
    runner = _runner(max_in_flight=1)
    started = threading.Event()
    release = threading.Event()

    def hung_adjudicator(_request, _frozen_package):
        started.set()
        release.wait()

    try:
        first = ConflictCoordinator().execute(
            _package(vote=False),
            run_adjudicator=hung_adjudicator,
            port_runner=runner,
        )
        assert started.wait(0.2)
        assert first.status == "timed_out"

        second = ConflictCoordinator().execute(
            _package(vote=False),
            run_adjudicator=lambda _request, _package: None,
            port_runner=runner,
            abort_exceptions=(RuntimeError,),
        )

        assert second.status == "failed"
        assert second.reason == "port_runner_capacity_exhausted"
    finally:
        release.set()


def test_single_slot_runner_reuses_capacity_after_each_completed_call() -> None:
    choices = iter(("A", "A", "B"))

    def collect(request, _frozen_package):
        assert request.voter_id is not None
        assert request.independence_key is not None
        return ConflictBallot(
            voter_id=request.voter_id,
            independence_key=request.independence_key,
            question_id=request.question_id,
            evidence_digest=request.evidence_digest,
            choice=next(choices),
            evidence_refs=("artifact_a",),
            confidence="high",
            sealed=True,
        )

    result = ConflictCoordinator().execute(
        _package(),
        collect_ballot=collect,
        on_transition=lambda _transition: None,
        port_runner=_runner(max_in_flight=1),
        observer_runner=_runner(max_in_flight=1),
    )

    assert result.status == "resolved"
    assert result.route is ResolutionRoute.VOTE
    assert result.choice == "A"


def test_late_port_result_cannot_replace_the_terminal_timeout() -> None:
    release = threading.Event()
    callback_finished = threading.Event()

    def adjudicate(_request, _frozen_package):
        release.wait()
        callback_finished.set()
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "late_adjudication",
        )

    result = ConflictCoordinator().execute(
        _package(vote=False),
        run_adjudicator=adjudicate,
        port_runner=_runner(),
    )
    terminal_snapshot = result

    release.set()
    assert callback_finished.wait(0.2)
    time.sleep(0.01)

    assert result == terminal_snapshot
    assert result.status == "timed_out"
    assert [
        item.outcome
        for item in result.transitions
        if item.outcome in {"resolved", "failed", "timed_out"}
    ] == ["timed_out"]


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


def test_late_registered_abort_exception_preserves_its_identity() -> None:
    class HostCancelled(RuntimeError):
        pass

    def clock() -> float:
        if threading.current_thread().name.startswith("conflict-port-"):
            return 2.0
        return 0.0

    runner = BoundedConflictPortRunner(
        default_timeout_seconds=1.0,
        cancellation_poll_seconds=0.002,
        clock=clock,
    )
    cancellation = HostCancelled("host cancelled adjudication")

    def cancel_adjudication(_request, _frozen_package):
        raise cancellation

    with pytest.raises(HostCancelled) as raised:
        ConflictCoordinator().execute(
            _package(vote=False),
            run_adjudicator=cancel_adjudication,
            port_runner=runner,
            abort_exceptions=(HostCancelled,),
        )

    assert raised.value is cancellation


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


def test_runner_propagates_host_cancellation_and_drops_late_result() -> None:
    class HostCancelled(RuntimeError):
        pass

    class CancellationToken:
        def __init__(self) -> None:
            self.requested = threading.Event()

        def raise_if_requested(self) -> None:
            if self.requested.is_set():
                raise HostCancelled("host cancelled conflict port")

    token = CancellationToken()
    release = threading.Event()

    def adjudicate(_request, _frozen_package):
        token.requested.set()
        release.wait()

    try:
        with pytest.raises(HostCancelled, match="host cancelled"):
            ConflictCoordinator().execute(
                _package(vote=False),
                run_adjudicator=adjudicate,
                port_runner=_runner(),
                cancellation_token=token,
                abort_exceptions=(HostCancelled,),
            )
    finally:
        release.set()


def test_runner_has_a_typed_fallback_for_simple_cancellation_tokens() -> None:
    class CancellationToken:
        def __init__(self) -> None:
            self.requested = threading.Event()

        def is_requested(self) -> bool:
            return self.requested.is_set()

    token = CancellationToken()
    release = threading.Event()

    def adjudicate(_request, _frozen_package):
        token.requested.set()
        release.wait()

    try:
        with pytest.raises(ConflictPortCancelled, match="cancelled"):
            ConflictCoordinator().execute(
                _package(vote=False),
                run_adjudicator=adjudicate,
                port_runner=_runner(),
                cancellation_token=token,
            )
    finally:
        release.set()


def test_central_scheduler_bounds_ports_by_default() -> None:
    class CancellationToken:
        def is_requested(self) -> bool:
            return True

    authority_calls = []

    def adjudicate(_request, _frozen_package):
        authority_calls.append(True)
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "expert_adjudication",
        )

    with pytest.raises(ConflictPortCancelled, match="cancelled"):
        CentralScheduler().execute_conflict(
            _package(vote=False),
            run_adjudicator=adjudicate,
            cancellation_token=CancellationToken(),
        )

    assert authority_calls == []


def test_default_schedulers_share_one_process_authority_runner() -> None:
    first = CentralScheduler()
    second = CentralScheduler()

    assert first._conflict_port_runner is second._conflict_port_runner
    assert (
        first._conflict_observer_runner
        is second._conflict_observer_runner
    )
    assert first._conflict_observer_runner is not first._conflict_port_runner


def test_default_schedulers_share_authority_capacity_across_instances(
    monkeypatch,
) -> None:
    shared_runner = _runner(max_in_flight=1)
    monkeypatch.setattr(
        central_scheduler_module,
        "_DEFAULT_CONFLICT_AUTHORITY_RUNNER",
        shared_runner,
    )
    first = CentralScheduler()
    second = CentralScheduler()
    first_started = threading.Event()
    first_release = threading.Event()
    second_called = threading.Event()

    def first_authority(_request, _frozen_package):
        first_started.set()
        first_release.wait()

    def second_authority(_request, _frozen_package):
        second_called.set()

    try:
        first_result = first.execute_conflict(
            _package(vote=False),
            run_adjudicator=first_authority,
        )
        second_result = second.execute_conflict(
            _package(vote=False),
            run_adjudicator=second_authority,
        )

        assert first_started.wait(0.2)
        assert first_result.status == "timed_out"
        assert second_result.status == "failed"
        assert second_result.reason == "port_runner_capacity_exhausted"
        assert not second_called.is_set()
    finally:
        first_release.set()


def test_hung_shared_observer_cannot_exhaust_authority_capacity(
    monkeypatch,
) -> None:
    authority_runner = _runner(max_in_flight=1)
    observer_runner = _runner(max_in_flight=1)
    monkeypatch.setattr(
        central_scheduler_module,
        "_DEFAULT_CONFLICT_AUTHORITY_RUNNER",
        authority_runner,
    )
    monkeypatch.setattr(
        central_scheduler_module,
        "_DEFAULT_CONFLICT_OBSERVER_RUNNER",
        observer_runner,
    )
    first = CentralScheduler()
    second = CentralScheduler()
    observer_started = threading.Event()
    observer_release = threading.Event()
    second_observer_called = threading.Event()
    authority_calls = []

    def hung_observer(_transition):
        observer_started.set()
        observer_release.wait()

    def adjudicate(_request, _frozen_package):
        authority_calls.append(True)
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "expert_adjudication",
        )

    try:
        first_result = first.execute_conflict(
            _package(vote=False),
            deterministic_choice="A",
            on_transition=hung_observer,
        )
        second_result = second.execute_conflict(
            _package(vote=False),
            run_adjudicator=adjudicate,
            on_transition=lambda _transition: second_observer_called.set(),
        )

        assert observer_started.wait(0.2)
        assert first_result.status == "resolved"
        assert second_result.status == "resolved"
        assert second_result.choice == "A"
        assert authority_calls == [True]
        assert not second_observer_called.is_set()
    finally:
        observer_release.set()


def test_central_scheduler_preserves_turn_cancellation_without_abort_config() -> None:
    turn_id = "turn_" + "c" * 32
    cancellation_token = TurnCancellationToken(turn_id)
    cancellation_token.request()
    authority_calls = []

    def adjudicate(_request, _frozen_package):
        authority_calls.append(True)
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "expert_adjudication",
        )

    with pytest.raises(TurnCancellationRequested) as raised:
        CentralScheduler().execute_conflict(
            _package(vote=False),
            run_adjudicator=adjudicate,
            cancellation_token=cancellation_token,
        )

    assert raised.value.turn_id == turn_id
    assert authority_calls == []


def test_central_scheduler_runner_cannot_be_overridden_per_call() -> None:
    scheduler = CentralScheduler(conflict_port_runner=_runner())

    with pytest.raises(TypeError, match="port_runner"):
        scheduler.execute_conflict(
            _package(vote=False),
            port_runner=_runner(),  # type: ignore[call-arg]
        )


def test_bounded_runner_requires_positive_finite_limits() -> None:
    with pytest.raises(ValueError, match="max_in_flight"):
        BoundedConflictPortRunner(max_in_flight=0)
    with pytest.raises(ValueError, match="default_timeout_seconds"):
        BoundedConflictPortRunner(default_timeout_seconds=float("inf"))
