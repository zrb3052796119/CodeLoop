"""Deterministic conflict priority, voting eligibility, and escalation.

Workers provide evidence and sealed ballots; this module alone decides which
authority layer may resolve a conflict.  A vote cannot authorize a write or
override a human, policy, or deterministic result.
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

from minicode.conflict_port_runner import (
    BoundedConflictPortRunner,
    ConflictPortCancelled,
    ConflictPortCapacityExhausted,
    ConflictPortDeadlineExceeded,
    ConflictPortRunner,
)

_PortResult = TypeVar("_PortResult")


class ResolutionRoute(str, Enum):
    HUMAN = "human"
    USER = "user"
    POLICY = "policy"
    DETERMINISTIC = "deterministic"
    VOTE = "vote"
    ADJUDICATOR = "adjudicator"


@dataclass(frozen=True, slots=True)
class ConflictBallot:
    voter_id: str
    independence_key: str
    question_id: str
    evidence_digest: str
    choice: str
    evidence_refs: tuple[str, ...]
    confidence: str
    sealed: bool

    def __post_init__(self) -> None:
        strings = (
            self.voter_id,
            self.independence_key,
            self.question_id,
            self.evidence_digest,
            self.choice,
        )
        if any(not isinstance(value, str) or not value.strip() for value in strings):
            raise ValueError("ballot identity fields must be non-empty")
        if (
            not isinstance(self.evidence_refs, tuple)
            or not self.evidence_refs
            or any(
                not isinstance(value, str) or not value.strip()
                for value in self.evidence_refs
            )
        ):
            raise ValueError("ballot evidence_refs must be a non-empty tuple")
        if self.confidence not in {"high", "medium", "low"}:
            raise ValueError("ballot confidence is invalid")
        if type(self.sealed) is not bool:
            raise ValueError("ballot sealed must be boolean")


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    status: str
    route: ResolutionRoute | None
    choice: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.status not in {
            "resolved",
            "unresolved",
            "escalated",
            "waiting_human",
        }:
            raise ValueError("conflict resolution status is invalid")
        if self.route is not None and not isinstance(self.route, ResolutionRoute):
            raise ValueError("conflict resolution route is invalid")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("conflict resolution reason must be non-empty")
        if self.status == "resolved":
            if self.route is None or not isinstance(self.choice, str) or not self.choice:
                raise ValueError("resolved conflict requires route and choice")
        elif self.choice is not None:
            raise ValueError("unresolved conflict cannot expose a choice")


@dataclass(frozen=True, slots=True)
class ConflictPortRequest:
    """Content-free correlation envelope for one host-owned authority port.

    The frozen package is passed separately to the port.  This envelope is
    safe to retain in scheduler traces because it contains identities and
    digests, never the question prompt, claims, or evidence contents.
    """

    execution_id: str
    workflow_id: str
    task_id: str
    conflict_id: str
    operation_id: str
    question_id: str
    evidence_digest: str
    route: ResolutionRoute | None
    ordinal: int
    voter_id: str | None = None
    independence_key: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictTransition:
    """Content-free audit record emitted by the conflict coordinator."""

    sequence: int
    execution_id: str
    workflow_id: str
    task_id: str
    conflict_id: str
    operation_id: str
    route: ResolutionRoute | None
    outcome: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictExecutionOutcome:
    """Terminal, mutation-free result of one centrally coordinated conflict."""

    execution_id: str
    workflow_id: str
    task_id: str
    conflict_id: str
    status: str
    route: ResolutionRoute | None
    choice: str | None
    reason: str
    ballots_collected: int
    adjudicator_calls: int
    human_calls: int
    transitions: tuple[ConflictTransition, ...]
    mutation_authorized: bool = False

    def __post_init__(self) -> None:
        if self.status not in {
            "resolved",
            "waiting_human",
            "escalated",
            "failed",
            "timed_out",
        }:
            raise ValueError("conflict execution status is invalid")
        if self.status == "resolved" and self.choice is None:
            raise ValueError("resolved conflict requires a choice")
        if self.status != "resolved" and self.choice is not None:
            raise ValueError("unresolved conflict cannot expose a choice")
        if self.mutation_authorized is not False:
            raise ValueError("conflict coordination cannot authorize mutation")


class _ConflictPortAbortError(BaseException):
    """Carry a registered host abort across a bounded runner unchanged."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class ConflictCoordinator:
    """Run one bounded conflict workflow through host-owned decision ports.

    The coordinator deliberately has no Worker or commit port.  Its only
    possible side effect is the optional delivery of content-free transition
    records to the host.  Those deliveries are non-authoritative observations:
    the complete local outcome is frozen first, then observations are delivered
    in order.  Delivery failure cannot alter the conflict decision.

    Passing ``deadline_monotonic`` without ``port_runner`` preserves the
    legacy synchronous API: the deadline is checked before and after each
    callback, but cannot interrupt a callback that never returns.  A host that
    requires hard deadlines must inject a ``ConflictPortRunner``.  The bundled
    ``BoundedConflictPortRunner`` provides an in-process compatibility boundary
    with explicitly bounded leaked-thread capacity; a process/RPC runner can
    provide stronger cancellation.  When a direct caller bounds authority
    ports and also supplies ``on_transition``, it must inject a distinct
    ``observer_runner``; the Coordinator never creates a hidden per-call pool.
    """

    _BALLOT_COUNT = 3

    def execute(
        self,
        package: Mapping[str, object],
        *,
        collect_ballot: Callable[
            [ConflictPortRequest, Mapping[str, object]], ConflictBallot
        ]
        | None = None,
        run_adjudicator: Callable[
            [ConflictPortRequest, Mapping[str, object]],
            ConflictResolution | None,
        ]
        | None = None,
        request_human: Callable[
            [ConflictPortRequest, Mapping[str, object]],
            ConflictResolution | None,
        ]
        | None = None,
        human_choice: str | None = None,
        user_choice: str | None = None,
        policy_choice: str | None = None,
        deterministic_choice: str | None = None,
        on_transition: Callable[[ConflictTransition], None] | None = None,
        deadline_monotonic: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        abort_exceptions: tuple[type[BaseException], ...] = (),
        port_runner: ConflictPortRunner | None = None,
        observer_runner: ConflictPortRunner | None = None,
        cancellation_token: object | None = None,
    ) -> ConflictExecutionOutcome:
        frozen = self._validated_package(package)
        if collect_ballot is not None and not callable(collect_ballot):
            raise TypeError("collect_ballot must be callable or null")
        if run_adjudicator is not None and not callable(run_adjudicator):
            raise TypeError("run_adjudicator must be callable or null")
        if request_human is not None and not callable(request_human):
            raise TypeError("request_human must be callable or null")
        if on_transition is not None and not callable(on_transition):
            raise TypeError("on_transition must be callable or null")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if port_runner is not None and not callable(
            getattr(port_runner, "run", None)
        ):
            raise TypeError("port_runner must implement run() or be null")
        if observer_runner is not None and not callable(
            getattr(observer_runner, "run", None)
        ):
            raise TypeError("observer_runner must implement run() or be null")
        if (
            on_transition is not None
            and port_runner is not None
            and observer_runner is None
        ):
            raise ValueError(
                "observer_runner is required with a bounded authority runner"
            )
        if (
            on_transition is not None
            and observer_runner is port_runner
            and port_runner is not None
        ):
            raise ValueError("observer_runner must have independent capacity")
        if deadline_monotonic is not None and (
            isinstance(deadline_monotonic, bool)
            or not isinstance(deadline_monotonic, (int, float))
        ):
            raise TypeError("deadline_monotonic must be numeric or null")
        if (
            not isinstance(abort_exceptions, tuple)
            or any(
                not isinstance(item, type)
                or not issubclass(item, BaseException)
                for item in abort_exceptions
            )
        ):
            raise TypeError("abort_exceptions must be exception classes")

        workflow_id = str(frozen["workflowId"])
        task_id = str(frozen["taskId"])
        conflict_id = str(frozen["conflictId"])
        question = frozen["question"]
        if not isinstance(question, Mapping):  # validated above; defensive only
            raise ValueError("ConflictPackage question is invalid")
        question_id = str(question["questionId"])
        choices = tuple(str(item) for item in question["choices"])
        allowed_evidence_refs = frozenset(
            str(reference)
            for claim in frozen["claims"]
            if isinstance(claim, Mapping)
            for reference in claim["evidenceRefs"]
        )
        evidence_digest = str(frozen["evidenceDigest"])
        namespace = hashlib.sha256(
            (
                f"{workflow_id}\0{task_id}\0{conflict_id}\0{evidence_digest}"
            ).encode("utf-8")
        ).hexdigest()[:20]
        execution_id = f"conflict_exec_{namespace}"
        transitions: list[ConflictTransition] = []
        pending_observations: list[
            tuple[ConflictPortRequest, ConflictTransition]
        ] = []
        ballots_collected = 0
        adjudicator_calls = 0
        human_calls = 0
        operation_counter = 0

        def port_package() -> Mapping[str, object]:
            # A port may be buggy or adversarial.  Give each call an isolated
            # copy so it cannot rewrite the scheduler's frozen decision facts
            # or influence a later independent ballot.
            return copy.deepcopy(frozen)

        def deadline_reached() -> bool:
            return (
                deadline_monotonic is not None
                and float(clock()) >= float(deadline_monotonic)
            )

        def invoke_with_runner(
            runner: ConflictPortRunner | None,
            request: ConflictPortRequest,
            port_name: str,
            operation: Callable[[], _PortResult],
        ) -> _PortResult:
            if runner is None:
                # Compatibility mode is intentionally synchronous.  The
                # surrounding deadline checkpoints do not claim to interrupt
                # an uncooperative callback.
                return operation()
            remaining_timeout = (
                None
                if deadline_monotonic is None
                else float(deadline_monotonic) - float(clock())
            )

            def protected_operation() -> _PortResult:
                try:
                    return operation()
                except abort_exceptions as error:
                    raise _ConflictPortAbortError(error) from error

            try:
                return runner.run(
                    protected_operation,
                    port_name=port_name,
                    operation_id=request.operation_id,
                    timeout_seconds=remaining_timeout,
                    cancellation_token=cancellation_token,
                )
            except _ConflictPortAbortError as error:
                raise error.cause from error

        def invoke_port(
            request: ConflictPortRequest,
            port_name: str,
            operation: Callable[[], _PortResult],
        ) -> _PortResult:
            return invoke_with_runner(
                port_runner,
                request,
                port_name,
                operation,
            )

        def propagate_cancellation(error: ConflictPortCancelled) -> None:
            if error.cause is not None:
                raise error.cause from error
            raise error

        def next_request(
            route: ResolutionRoute | None,
            *,
            ordinal: int,
        ) -> ConflictPortRequest:
            nonlocal operation_counter
            operation_counter += 1
            ballot_voter_id = (
                f"conflict_voter_{namespace}_{ordinal:02d}"
                if route is ResolutionRoute.VOTE and ordinal > 0
                else None
            )
            ballot_independence_key = (
                f"conflict_partition_{namespace}_{ordinal:02d}"
                if route is ResolutionRoute.VOTE and ordinal > 0
                else None
            )
            return ConflictPortRequest(
                execution_id=execution_id,
                workflow_id=workflow_id,
                task_id=task_id,
                conflict_id=conflict_id,
                operation_id=(
                    f"conflict_op_{namespace}_{operation_counter:04d}"
                ),
                question_id=question_id,
                evidence_digest=evidence_digest,
                route=route,
                ordinal=ordinal,
                voter_id=ballot_voter_id,
                independence_key=ballot_independence_key,
            )

        def emit(
            request: ConflictPortRequest,
            outcome: str,
            error_code: str | None = None,
        ) -> None:
            transition = ConflictTransition(
                sequence=len(transitions) + 1,
                execution_id=execution_id,
                workflow_id=workflow_id,
                task_id=task_id,
                conflict_id=conflict_id,
                operation_id=request.operation_id,
                route=request.route,
                outcome=outcome,
                error_code=error_code,
            )
            # Transitions are scheduler-owned facts.  The host callback is a
            # best-effort observer and cannot accept, reject, or replace them.
            transitions.append(transition)
            if on_transition is not None:
                pending_observations.append((request, transition))

        def deliver_observations() -> None:
            if on_transition is None:
                return
            for request, transition in pending_observations:
                try:
                    invoke_with_runner(
                        observer_runner,
                        request,
                        "on_transition",
                        lambda transition=transition: on_transition(transition),
                    )
                except ConflictPortCancelled as error:
                    propagate_cancellation(error)
                except (
                    ConflictPortCapacityExhausted,
                    ConflictPortDeadlineExceeded,
                ):
                    pass
                except abort_exceptions:
                    raise
                except Exception:  # noqa: BLE001,S110 - best-effort observer
                    # Observation is deliberately non-authoritative.  A late,
                    # unavailable, or buggy observer cannot rewrite the local
                    # transition ledger or the already-frozen decision.
                    pass

        def outcome(
            status: str,
            route: ResolutionRoute | None,
            choice: str | None,
            reason: str,
        ) -> ConflictExecutionOutcome:
            result = ConflictExecutionOutcome(
                execution_id=execution_id,
                workflow_id=workflow_id,
                task_id=task_id,
                conflict_id=conflict_id,
                status=status,
                route=route,
                choice=choice,
                reason=reason,
                ballots_collected=ballots_collected,
                adjudicator_calls=adjudicator_calls,
                human_calls=human_calls,
                transitions=tuple(transitions),
            )
            # Observers run only after the complete local outcome is frozen, so
            # their latency and bounded-runner capacity cannot influence which
            # authority route or decision won.
            deliver_observations()
            return result

        def timeout(
            route: ResolutionRoute | None,
            request: ConflictPortRequest | None = None,
            reason: str = "global_deadline_exceeded",
        ) -> ConflictExecutionOutcome:
            if request is None:
                request = next_request(route, ordinal=0)
            emit(request, "timed_out", reason)
            return outcome(
                "timed_out",
                route,
                None,
                reason,
            )

        def failed(
            route: ResolutionRoute,
            reason: str,
            request: ConflictPortRequest | None = None,
        ) -> ConflictExecutionOutcome:
            if request is not None:
                emit(request, "failed", reason)
            return outcome("failed", route, None, reason)

        def resolved_authority(
            route: ResolutionRoute,
            choice: str,
            reason: str,
        ) -> ConflictExecutionOutcome:
            request = next_request(route, ordinal=0)
            emit(request, "resolved")
            return outcome("resolved", route, choice, reason)

        if deadline_reached():
            return timeout(None)

        # Pre-existing authority artifacts are checked in the closed order.
        # For critical/requirement conflicts, only human authority is accepted.
        authority_values = (
            (human_choice, ResolutionRoute.HUMAN, "human_decision"),
            (user_choice, ResolutionRoute.USER, "explicit_user_constraint"),
            (policy_choice, ResolutionRoute.POLICY, "policy_authority"),
            (
                deterministic_choice,
                ResolutionRoute.DETERMINISTIC,
                "deterministic_evidence",
            ),
        )
        human_only = (
            frozen["risk"] == "critical" or frozen["kind"] == "requirement"
        )
        for choice, route, reason in authority_values:
            if choice is None or (human_only and route is not ResolutionRoute.HUMAN):
                continue
            if choice not in choices:
                request = next_request(route, ordinal=0)
                return failed(route, "authority_choice_invalid", request)
            return resolved_authority(route, choice, reason)

        def human_decision(reason: str) -> ConflictExecutionOutcome:
            nonlocal human_calls
            route = ResolutionRoute.HUMAN
            request = next_request(route, ordinal=1)
            if request_human is None:
                emit(request, "waiting_human", "human_port_unavailable")
                return outcome("waiting_human", route, None, reason)
            if deadline_reached():
                return timeout(route, request)
            try:
                emit(request, "dispatched")
                human_calls += 1
                decision = invoke_port(
                    request,
                    "request_human",
                    lambda: request_human(request, port_package()),
                )
            except ConflictPortCancelled as error:
                propagate_cancellation(error)
            except ConflictPortDeadlineExceeded as error:
                return timeout(route, request, error.reason)
            except ConflictPortCapacityExhausted:
                return failed(
                    route,
                    "port_runner_capacity_exhausted",
                    request,
                )
            except abort_exceptions:
                raise
            except Exception:  # noqa: BLE001 - authority port failures fail closed
                if deadline_reached():
                    return timeout(route, request)
                return failed(route, "human_port_failed", request)
            if deadline_reached():
                return timeout(route, request)
            selected = self._port_choice(
                decision,
                expected_route=route,
                choices=choices,
            )
            if selected is False:
                return failed(route, "human_result_invalid", request)
            if selected is None:
                emit(request, "waiting_human", "human_decision_pending")
                return outcome("waiting_human", route, None, reason)
            emit(request, "resolved")
            return outcome("resolved", route, selected, "human_decision")

        if human_only:
            return human_decision("human_authority_required")

        escalation_reason = "expert_judgment_required"
        if frozen["voteEligible"] is True:
            if collect_ballot is None:
                escalation_reason = "ballot_port_unavailable"
            else:
                ballots: list[ConflictBallot] = []
                voter_ids: set[str] = set()
                independence_keys: set[str] = set()
                for ordinal in range(1, self._BALLOT_COUNT + 1):
                    route = ResolutionRoute.VOTE
                    request = next_request(route, ordinal=ordinal)
                    if deadline_reached():
                        return timeout(route, request)
                    try:
                        emit(request, "dispatched")
                        ballot = invoke_port(
                            request,
                            "collect_ballot",
                            lambda request=request: collect_ballot(
                                request,
                                port_package(),
                            ),
                        )
                    except ConflictPortCancelled as error:
                        propagate_cancellation(error)
                    except ConflictPortDeadlineExceeded as error:
                        return timeout(route, request, error.reason)
                    except ConflictPortCapacityExhausted:
                        return failed(
                            route,
                            "port_runner_capacity_exhausted",
                            request,
                        )
                    except abort_exceptions:
                        raise
                    except Exception:  # noqa: BLE001 - ballot port fails closed
                        if deadline_reached():
                            return timeout(route, request)
                        return failed(route, "ballot_port_failed", request)
                    if deadline_reached():
                        return timeout(route, request)
                    if not isinstance(ballot, ConflictBallot):
                        return failed(route, "ballot_result_invalid", request)
                    if (
                        not ballot.sealed
                        or ballot.question_id != question_id
                        or ballot.evidence_digest != evidence_digest
                        or ballot.choice not in {*choices, "ABSTAIN"}
                        or ballot.voter_id != request.voter_id
                        or ballot.independence_key
                        != request.independence_key
                        or not set(ballot.evidence_refs).issubset(
                            allowed_evidence_refs
                        )
                    ):
                        return failed(
                            route, "ballot_contract_mismatch", request
                        )
                    if ballot.voter_id in voter_ids:
                        return failed(route, "duplicate_voter", request)
                    if ballot.independence_key in independence_keys:
                        return failed(
                            route,
                            "independence_not_demonstrated",
                            request,
                        )
                    voter_ids.add(ballot.voter_id)
                    independence_keys.add(ballot.independence_key)
                    ballots.append(ballot)
                    ballots_collected += 1
                    emit(request, "completed")

                vote = ConflictResolver().resolve(
                    frozen,
                    ballots=tuple(ballots),
                )
                if vote.status == "resolved" and vote.choice is not None:
                    request = next_request(ResolutionRoute.VOTE, ordinal=0)
                    emit(request, "resolved")
                    return outcome(
                        "resolved",
                        ResolutionRoute.VOTE,
                        vote.choice,
                        vote.reason,
                    )
                escalation_reason = vote.reason

        route = ResolutionRoute.ADJUDICATOR
        request = next_request(route, ordinal=1)
        if run_adjudicator is None:
            emit(request, "escalated", "adjudicator_port_unavailable")
            return outcome("escalated", route, None, escalation_reason)
        if deadline_reached():
            return timeout(route, request)
        try:
            emit(request, "dispatched")
            adjudicator_calls += 1
            decision = invoke_port(
                request,
                "run_adjudicator",
                lambda: run_adjudicator(request, port_package()),
            )
        except ConflictPortCancelled as error:
            propagate_cancellation(error)
        except ConflictPortDeadlineExceeded as error:
            return timeout(route, request, error.reason)
        except ConflictPortCapacityExhausted:
            return failed(
                route,
                "port_runner_capacity_exhausted",
                request,
            )
        except abort_exceptions:
            raise
        except Exception:  # noqa: BLE001 - expert port failures fail closed
            if deadline_reached():
                return timeout(route, request)
            return failed(route, "adjudicator_port_failed", request)
        if deadline_reached():
            return timeout(route, request)
        selected = self._port_choice(
            decision,
            expected_route=route,
            choices=choices,
        )
        if selected is False:
            return failed(route, "adjudicator_result_invalid", request)
        if selected is None:
            emit(request, "escalated", "adjudicator_inconclusive")
            return outcome("escalated", route, None, "adjudicator_inconclusive")
        emit(request, "resolved")
        return outcome(
            "resolved", route, selected, "expert_adjudication"
        )

    @staticmethod
    def _validated_package(
        package: Mapping[str, object],
    ) -> dict[str, object]:
        if not isinstance(package, Mapping):
            raise TypeError("package must be a validated ConflictPackage.v1")
        raw = dict(package)
        if raw.pop("resultType", None) != "ConflictPackage.v1":
            raise ValueError("a validated ConflictPackage.v1 is required")
        workflow_id = raw.get("workflowId")
        task_id = raw.get("taskId")
        if not isinstance(workflow_id, str) or not isinstance(task_id, str):
            raise ValueError("ConflictPackage identity is invalid")
        from minicode.multi_agent_contracts import parse_conflict_package

        frozen = parse_conflict_package(
            raw,
            workflow_id=workflow_id,
            task_id=task_id,
        )
        if frozen != dict(package):
            raise ValueError("ConflictPackage is not canonical")
        return frozen

    @staticmethod
    def _port_choice(
        decision: ConflictResolution | None,
        *,
        expected_route: ResolutionRoute,
        choices: tuple[str, ...],
    ) -> str | None | bool:
        if decision is None:
            return None
        if not isinstance(decision, ConflictResolution):
            return False
        if (
            decision.status == "resolved"
            and decision.route is expected_route
            and decision.choice in choices
        ):
            return decision.choice
        if (
            decision.choice is None
            and decision.status in {"escalated", "waiting_human"}
            and decision.route is expected_route
        ):
            return None
        return False


class ConflictResolver:
    """Apply the closed authority order to one normalized ConflictPackage."""

    def resolve(
        self,
        package: Mapping[str, object],
        *,
        ballots: tuple[ConflictBallot, ...] = (),
        human_choice: str | None = None,
        user_choice: str | None = None,
        policy_choice: str | None = None,
        deterministic_choice: str | None = None,
    ) -> ConflictResolution:
        package = ConflictCoordinator._validated_package(package)
        (
            choices,
            question_id,
            evidence_digest,
            allowed_evidence_refs,
        ) = self._package_facts(package)

        if human_choice is not None:
            if human_choice not in choices:
                raise ValueError("human_decision selected an unknown choice")
            return ConflictResolution(
                "resolved",
                ResolutionRoute.HUMAN,
                human_choice,
                "human_decision",
            )

        risk = package.get("risk")
        kind = package.get("kind")
        if risk == "critical" or kind == "requirement":
            return ConflictResolution(
                "waiting_human",
                ResolutionRoute.HUMAN,
                None,
                "human_authority_required",
            )

        for choice, route, reason in (
            (user_choice, ResolutionRoute.USER, "explicit_user_constraint"),
            (policy_choice, ResolutionRoute.POLICY, "policy_authority"),
            (
                deterministic_choice,
                ResolutionRoute.DETERMINISTIC,
                "deterministic_evidence",
            ),
        ):
            if choice is None:
                continue
            if choice not in choices:
                raise ValueError(f"{reason} selected an unknown choice")
            return ConflictResolution("resolved", route, choice, reason)

        if package.get("voteEligible") is True:
            vote = self._resolve_vote(
                ballots,
                choices=choices,
                question_id=question_id,
                evidence_digest=evidence_digest,
                allowed_evidence_refs=allowed_evidence_refs,
            )
            if vote.status == "resolved":
                return vote
            return ConflictResolution(
                "escalated",
                ResolutionRoute.ADJUDICATOR,
                None,
                vote.reason,
            )

        return ConflictResolution(
            "escalated",
            ResolutionRoute.ADJUDICATOR,
            None,
            "expert_judgment_required",
        )

    @staticmethod
    def _package_facts(
        package: Mapping[str, object],
    ) -> tuple[tuple[str, ...], str, str, frozenset[str]]:
        if not isinstance(package, Mapping) or package.get("resultType") != (
            "ConflictPackage.v1"
        ):
            raise ValueError("a validated ConflictPackage.v1 is required")
        question = package.get("question")
        if not isinstance(question, Mapping):
            raise ValueError("ConflictPackage question is invalid")
        raw_choices = question.get("choices")
        question_id = question.get("questionId")
        evidence_digest = package.get("evidenceDigest")
        claims = package.get("claims")
        if (
            not isinstance(raw_choices, list)
            or not 2 <= len(raw_choices) <= 5
            or any(not isinstance(item, str) for item in raw_choices)
            or not isinstance(question_id, str)
            or not isinstance(evidence_digest, str)
            or not isinstance(claims, list)
        ):
            raise ValueError("ConflictPackage voting facts are invalid")
        allowed_evidence_refs = frozenset(
            str(reference)
            for claim in claims
            if isinstance(claim, Mapping)
            for reference in claim.get("evidenceRefs", ())
        )
        return (
            tuple(raw_choices),
            question_id,
            evidence_digest,
            allowed_evidence_refs,
        )

    @staticmethod
    def _resolve_vote(
        ballots: tuple[ConflictBallot, ...],
        *,
        choices: tuple[str, ...],
        question_id: str,
        evidence_digest: str,
        allowed_evidence_refs: frozenset[str],
    ) -> ConflictResolution:
        if not isinstance(ballots, tuple) or any(
            not isinstance(ballot, ConflictBallot) for ballot in ballots
        ):
            raise TypeError("ballots must be a tuple of ConflictBallot")
        if len(ballots) < 3:
            return ConflictResolution(
                "unresolved",
                ResolutionRoute.VOTE,
                None,
                "insufficient_ballots",
            )
        voter_ids = [ballot.voter_id for ballot in ballots]
        independence = [ballot.independence_key for ballot in ballots]
        if len(set(voter_ids)) != len(voter_ids):
            return ConflictResolution(
                "unresolved",
                ResolutionRoute.VOTE,
                None,
                "duplicate_voter",
            )
        if len(set(independence)) != len(independence):
            return ConflictResolution(
                "unresolved",
                ResolutionRoute.VOTE,
                None,
                "independence_not_demonstrated",
            )
        if any(
            not ballot.sealed
            or ballot.question_id != question_id
            or ballot.evidence_digest != evidence_digest
            or ballot.choice not in {*choices, "ABSTAIN"}
            or not set(ballot.evidence_refs).issubset(allowed_evidence_refs)
            for ballot in ballots
        ):
            return ConflictResolution(
                "unresolved",
                ResolutionRoute.VOTE,
                None,
                "ballot_contract_mismatch",
            )

        non_abstaining = [
            ballot.choice for ballot in ballots if ballot.choice != "ABSTAIN"
        ]
        if not non_abstaining:
            return ConflictResolution(
                "unresolved",
                ResolutionRoute.VOTE,
                None,
                "all_ballots_abstained",
            )
        counts = Counter(non_abstaining)
        winner, count = counts.most_common(1)[0]
        if count >= 2 and count > len(non_abstaining) / 2:
            return ConflictResolution(
                "resolved",
                ResolutionRoute.VOTE,
                winner,
                "eligible_majority",
            )
        return ConflictResolution(
            "unresolved",
            ResolutionRoute.VOTE,
            None,
            "no_majority",
        )


__all__ = [
    "BoundedConflictPortRunner",
    "ConflictBallot",
    "ConflictCoordinator",
    "ConflictExecutionOutcome",
    "ConflictPortCancelled",
    "ConflictPortCapacityExhausted",
    "ConflictPortDeadlineExceeded",
    "ConflictPortRequest",
    "ConflictPortRunner",
    "ConflictResolution",
    "ConflictResolver",
    "ConflictTransition",
    "ResolutionRoute",
]
