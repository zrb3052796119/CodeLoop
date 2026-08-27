"""Deterministic conflict priority, voting, and escalation policy."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from minicode.central_scheduler import CentralScheduler
from minicode.conflict_resolution import (
    ConflictBallot,
    ConflictResolution,
    ConflictResolver,
    ResolutionRoute,
)
from minicode.multi_agent_contracts import parse_conflict_package


def _package(*, risk: str = "low", kind: str = "fact", vote: bool = True):
    return parse_conflict_package(
        {
            "conflictVersion": 1,
            "conflictId": "conflict_policy1",
            "workflowId": "wf_policy1",
            "taskId": "task_policy1",
            "kind": kind,
            "risk": risk,
            "question": {
                "questionId": "question_policy1",
                "prompt": "Choose the supported option.",
                "choices": ["A", "B"],
            },
            "claims": [
                {
                    "claimId": "claim_policy_a",
                    "choice": "A",
                    "statement": "Evidence supports A.",
                    "evidenceRefs": ["artifact_a"],
                },
                {
                    "claimId": "claim_policy_b",
                    "choice": "B",
                    "statement": "Evidence supports B.",
                    "evidenceRefs": ["artifact_b"],
                },
            ],
            "evidenceDigest": "sha256:" + "d" * 64,
            "voteEligible": vote,
        },
        workflow_id="wf_policy1",
        task_id="task_policy1",
    )


def _ballot(
    voter: str,
    choice: str,
    *,
    independence: str | None = None,
    evidence_refs: tuple[str, ...] = ("artifact_a",),
    sealed: bool = True,
):
    return ConflictBallot(
        voter_id=voter,
        independence_key=independence or voter,
        question_id="question_policy1",
        evidence_digest="sha256:" + "d" * 64,
        choice=choice,
        evidence_refs=evidence_refs,
        confidence="high",
        sealed=sealed,
    )


def _requested_ballot(request, choice: str, **kwargs):
    assert request.voter_id is not None
    assert request.independence_key is not None
    return _ballot(
        request.voter_id,
        choice,
        independence=request.independence_key,
        **kwargs,
    )


def test_two_of_three_independent_sealed_ballots_resolve_low_risk_question() -> None:
    result = CentralScheduler().resolve_conflict(
        _package(),
        ballots=(_ballot("v1", "A"), _ballot("v2", "A"), _ballot("v3", "B")),
    )

    assert result.route is ResolutionRoute.VOTE
    assert result.status == "resolved"
    assert result.choice == "A"


def test_two_agents_or_a_tie_never_masquerades_as_a_majority() -> None:
    result = ConflictResolver().resolve(
        _package(),
        ballots=(_ballot("v1", "A"), _ballot("v2", "B")),
    )

    assert result.route is ResolutionRoute.ADJUDICATOR
    assert result.status == "escalated"
    assert result.choice is None


def test_correlated_or_unsealed_ballots_are_not_vote_eligible() -> None:
    correlated = (
        _ballot("v1", "A", independence="same-model-context"),
        _ballot("v2", "A", independence="same-model-context"),
        _ballot("v3", "B", independence="different"),
    )

    result = ConflictResolver().resolve(_package(), ballots=correlated)

    assert result.route is ResolutionRoute.ADJUDICATOR
    assert result.reason == "independence_not_demonstrated"


def test_three_unsealed_majority_ballots_cannot_resolve_the_vote() -> None:
    unsealed_majority = (
        _ballot("v1", "A", sealed=False),
        _ballot("v2", "A", sealed=False),
        _ballot("v3", "A", sealed=False),
    )

    result = ConflictResolver().resolve(
        _package(),
        ballots=unsealed_majority,
    )

    assert result.status == "escalated"
    assert result.route is ResolutionRoute.ADJUDICATOR
    assert result.choice is None
    assert result.reason == "ballot_contract_mismatch"


def test_deterministic_evidence_has_priority_over_model_vote() -> None:
    result = ConflictResolver().resolve(
        _package(),
        deterministic_choice="B",
        ballots=(_ballot("v1", "A"), _ballot("v2", "A"), _ballot("v3", "A")),
    )

    assert result.route is ResolutionRoute.DETERMINISTIC
    assert result.choice == "B"


def test_requirement_or_critical_conflict_escalates_to_human() -> None:
    requirement = _package(kind="requirement", vote=False)
    critical = _package(risk="critical", vote=False)

    assert ConflictResolver().resolve(requirement).route is ResolutionRoute.HUMAN
    assert ConflictResolver().resolve(critical).route is ResolutionRoute.HUMAN


def test_human_only_conflict_ignores_lower_authority_shortcuts() -> None:
    result = CentralScheduler().resolve_conflict(
        _package(risk="critical", vote=False),
        user_choice="A",
        policy_choice="A",
        deterministic_choice="A",
    )

    assert result.status == "waiting_human"
    assert result.route is ResolutionRoute.HUMAN
    assert result.choice is None


def test_explicit_human_choice_has_highest_authority() -> None:
    result = ConflictResolver().resolve(
        _package(),
        human_choice="A",
        deterministic_choice="B",
    )

    assert result.route is ResolutionRoute.HUMAN
    assert result.status == "resolved"
    assert result.choice == "A"


def test_central_conflict_execution_collects_exactly_three_sealed_ballots() -> None:
    requests = []
    transitions = []
    choices = iter(("A", "A", "B"))

    def collect(request, frozen_package):
        requests.append(request)
        assert frozen_package["conflictId"] == "conflict_policy1"
        return _requested_ballot(request, next(choices))

    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=collect,
        on_transition=transitions.append,
    )

    assert result.status == "resolved"
    assert result.route is ResolutionRoute.VOTE
    assert result.choice == "A"
    assert result.ballots_collected == 3
    assert result.adjudicator_calls == 0
    assert result.human_calls == 0
    assert result.mutation_authorized is False
    assert [item.ordinal for item in requests] == [1, 2, 3]
    assert tuple(transitions) == result.transitions
    assert all(
        not ({"prompt", "claims", "choice", "evidenceRefs"} & asdict(item).keys())
        for item in result.transitions
    )


def test_central_execution_applies_authority_before_calling_any_model_port() -> None:
    port_calls = []

    def forbidden_port(*_args):
        port_calls.append(True)
        raise AssertionError("an authority decision must short-circuit ports")

    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=forbidden_port,
        run_adjudicator=forbidden_port,
        request_human=forbidden_port,
        user_choice="B",
        policy_choice="A",
        deterministic_choice="A",
    )

    assert result.status == "resolved"
    assert result.route is ResolutionRoute.USER
    assert result.choice == "B"
    assert port_calls == []


def test_vote_without_majority_calls_adjudicator_exactly_once() -> None:
    choices = iter(("A", "B", "ABSTAIN"))
    adjudicator_calls = []

    def collect(request, _package):
        return _requested_ballot(request, next(choices))

    def adjudicate(request, frozen_package):
        adjudicator_calls.append((request, frozen_package))
        return ConflictResolution(
            "resolved",
            ResolutionRoute.ADJUDICATOR,
            "B",
            "expert_adjudication",
        )

    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=collect,
        run_adjudicator=adjudicate,
    )

    assert result.status == "resolved"
    assert result.route is ResolutionRoute.ADJUDICATOR
    assert result.choice == "B"
    assert result.ballots_collected == 3
    assert result.adjudicator_calls == 1
    assert len(adjudicator_calls) == 1


def test_critical_conflict_uses_only_host_human_port() -> None:
    called = {"ballot": 0, "adjudicator": 0, "human": 0}

    def forbidden_ballot(*_args):
        called["ballot"] += 1
        raise AssertionError("critical conflict must not vote")

    def forbidden_adjudicator(*_args):
        called["adjudicator"] += 1
        raise AssertionError("critical conflict must not use an expert")

    def human(_request, frozen_package):
        called["human"] += 1
        assert frozen_package["risk"] == "critical"
        return ConflictResolution(
            "resolved",
            ResolutionRoute.HUMAN,
            "A",
            "human_decision",
        )

    result = CentralScheduler().execute_conflict(
        _package(risk="critical", vote=False),
        collect_ballot=forbidden_ballot,
        run_adjudicator=forbidden_adjudicator,
        request_human=human,
        deterministic_choice="B",
    )

    assert result.status == "resolved"
    assert result.route is ResolutionRoute.HUMAN
    assert result.choice == "A"
    assert result.human_calls == 1
    assert called == {"ballot": 0, "adjudicator": 0, "human": 1}


def test_missing_authority_ports_return_explicit_non_resolved_states() -> None:
    waiting = CentralScheduler().execute_conflict(
        _package(kind="requirement", vote=False)
    )
    escalated = CentralScheduler().execute_conflict(
        _package(vote=False)
    )

    assert waiting.status == "waiting_human"
    assert waiting.route is ResolutionRoute.HUMAN
    assert waiting.choice is None
    assert escalated.status == "escalated"
    assert escalated.route is ResolutionRoute.ADJUDICATOR
    assert escalated.choice is None


def test_conflict_port_exception_fails_closed_without_expert_fallback() -> None:
    expert_calls = []

    def broken_ballot(*_args):
        raise RuntimeError("worker unavailable")

    def expert(*_args):
        expert_calls.append(True)
        return "A"

    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=broken_ballot,
        run_adjudicator=expert,
    )

    assert result.status == "failed"
    assert result.reason == "ballot_port_failed"
    assert result.choice is None
    assert expert_calls == []


def test_conflict_execution_checks_deadline_after_each_port_call() -> None:
    now = [0.0]

    def collect(request, _package):
        now[0] = 10.0
        return _requested_ballot(request, "A")

    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=collect,
        deadline_monotonic=5.0,
        clock=lambda: now[0],
    )

    assert result.status == "timed_out"
    assert result.reason == "global_deadline_exceeded"
    assert result.choice is None
    assert result.ballots_collected == 0
    assert result.transitions[-1].outcome == "timed_out"


def test_authority_ports_reject_untyped_string_decisions() -> None:
    result = CentralScheduler().execute_conflict(
        _package(vote=False),
        run_adjudicator=lambda _request, _package: "A",  # type: ignore[return-value]
    )

    assert result.status == "failed"
    assert result.reason == "adjudicator_result_invalid"
    assert result.choice is None


@pytest.mark.parametrize(
    (
        "package_kwargs",
        "authority_port",
        "returned_route",
        "returned_choice",
        "expected_reason",
    ),
    [
        pytest.param(
            {"vote": False},
            "adjudicator",
            ResolutionRoute.HUMAN,
            "A",
            "adjudicator_result_invalid",
            id="adjudicator-wrong-route",
        ),
        pytest.param(
            {"vote": False},
            "adjudicator",
            ResolutionRoute.ADJUDICATOR,
            "outside-package",
            "adjudicator_result_invalid",
            id="adjudicator-unknown-choice",
        ),
        pytest.param(
            {"risk": "critical", "vote": False},
            "human",
            ResolutionRoute.ADJUDICATOR,
            "A",
            "human_result_invalid",
            id="critical-human-wrong-route",
        ),
        pytest.param(
            {"kind": "requirement", "vote": False},
            "human",
            ResolutionRoute.HUMAN,
            "outside-package",
            "human_result_invalid",
            id="requirement-human-unknown-choice",
        ),
    ],
)
def test_typed_contradictory_authority_decisions_fail_closed(
    package_kwargs,
    authority_port,
    returned_route,
    returned_choice,
    expected_reason,
) -> None:
    unexpected_calls = []

    def contradictory_decision(_request, _package):
        return ConflictResolution(
            "resolved",
            returned_route,
            returned_choice,
            "typed_but_contradictory",
        )

    def forbidden_port(*_args):
        unexpected_calls.append(True)
        raise AssertionError("an invalid authority result must fail closed")

    if authority_port == "adjudicator":
        result = CentralScheduler().execute_conflict(
            _package(**package_kwargs),
            run_adjudicator=contradictory_decision,
            request_human=forbidden_port,
        )
        expected_route = ResolutionRoute.ADJUDICATOR
        expected_calls = (1, 0)
    else:
        result = CentralScheduler().execute_conflict(
            _package(**package_kwargs),
            collect_ballot=forbidden_port,
            run_adjudicator=forbidden_port,
            request_human=contradictory_decision,
        )
        expected_route = ResolutionRoute.HUMAN
        expected_calls = (0, 1)

    assert result.status == "failed"
    assert result.route is expected_route
    assert result.choice is None
    assert result.reason == expected_reason
    assert (result.adjudicator_calls, result.human_calls) == expected_calls
    assert unexpected_calls == []


def test_ballot_cannot_cite_evidence_outside_the_frozen_package() -> None:
    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=lambda request, _package: _requested_ballot(
            request,
            "A",
            evidence_refs=("artifact_not_in_package",),
        ),
    )

    assert result.status == "failed"
    assert result.reason == "ballot_contract_mismatch"
    assert result.ballots_collected == 0


def test_ballot_identity_must_echo_scheduler_owned_partition() -> None:
    result = CentralScheduler().execute_conflict(
        _package(),
        collect_ballot=lambda _request, _package: _ballot(
            "self_selected_voter",
            "A",
            independence="self_selected_partition",
        ),
    )

    assert result.status == "failed"
    assert result.reason == "ballot_contract_mismatch"


def test_central_conflict_convenience_rejects_noncanonical_package() -> None:
    malformed = {**_package(), "unexpected": "open schema"}

    with pytest.raises(ValueError, match="ConflictPackage"):
        CentralScheduler().resolve_conflict(malformed)
