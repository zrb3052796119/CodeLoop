from __future__ import annotations

import json
import time

import pytest

from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_evidence import DecisionEvidence, TaskEvidence
from minicode.reflection_synthesis import (
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _constraint_evidence(count: int, *, duplicate: bool = False) -> TaskEvidence:
    decisions = []
    for index in range(count):
        suffix = "shared" if duplicate else str(index)
        decisions.append(
            DecisionEvidence(
                f"decision-{index}",
                f"Keep project interface {suffix} stable",
                None,
                (f"event-{index}",),
                "confirmed",
                "user_constraint",
            )
        )
    return TaskEvidence(
        decisions=decisions,
        outcome="success",
        event_positions={f"event-{index}": index for index in range(count)},
    )


@pytest.mark.parametrize("count", [1, 10, 100])
def test_synthesis_validation_and_value_gate_scale_to_claim_count(count: int) -> None:
    evidence = _constraint_evidence(count)
    synthesizer = RuleReflectionSynthesizer()
    validator = ReflectionClaimValidator()
    gate = ReflectionValueGate()

    started = time.perf_counter()
    candidate = synthesizer.synthesize("Preserve project interfaces", evidence)
    validation = validator.validate(candidate, evidence)
    decision = gate.evaluate(candidate, validation, evidence)
    elapsed = time.perf_counter() - started

    assert len(candidate.claims) == count
    assert len(validation.valid_claims) == count
    assert decision.accepted is True
    assert len(decision.accepted_claim_ids) == count
    assert elapsed < 0.25


def test_hundred_duplicate_semantic_keys_merge_without_quadratic_state() -> None:
    evidence = _constraint_evidence(100, duplicate=True)
    claims = [
        ReflectionClaim(
            f"claim-{index}",
            "constraint",
            "shared_interface",
            "Project constraint: Keep project interface shared stable.",
            [f"event-{index}"],
            "confirmed",
        )
        for index in range(100)
    ]

    validation = ReflectionClaimValidator().validate(
        ReflectionCandidate("Duplicate constraints", "success", claims),
        evidence,
    )

    assert len(validation.valid_claims) == 1
    assert len(validation.valid_claims[0].evidence_ids) == 100
    assert validation.issues[0].code == "duplicate_semantic_key_merged"


def test_hundred_invalid_evidence_references_are_all_diagnosed() -> None:
    evidence = _constraint_evidence(1)
    claims = [
        ReflectionClaim(
            f"claim-{index}",
            "constraint",
            f"invalid_{index}",
            "Project constraint: Keep project interface 0 stable.",
            [f"missing-event-{index}"],
            "confirmed",
        )
        for index in range(100)
    ]

    validation = ReflectionClaimValidator().validate(
        ReflectionCandidate("Invalid references", "success", claims),
        evidence,
    )

    assert len(validation.rejected_claims) == 100
    assert sum(
        issue.code == "invalid_evidence_reference" for issue in validation.issues
    ) == 100


def test_extreme_claim_text_and_cyclic_limitation_are_bounded() -> None:
    cyclic: dict[str, object] = {"name": "cycle"}
    cyclic["self"] = cyclic
    statement = "Keep project interface 0 stable " + "x" * 50_000
    evidence = _constraint_evidence(1)
    claim = ReflectionClaim(
        "claim-1",
        "constraint",
        "long_constraint",
        statement,
        ["event-0"],
        "confirmed",
        limitations=[cyclic],  # type: ignore[list-item]
    )

    validation = ReflectionClaimValidator().validate(
        ReflectionCandidate("Long claim", "success", [claim]),
        evidence,
    )

    serialized = json.dumps(validation.to_dict(include_rejected_text=True))
    assert len(serialized) < 10_000
    assert "truncated" in serialized


def test_structured_claim_metadata_remains_below_32_kib() -> None:
    trace = [
        {
            "event_id": f"event-{index}",
            "type": "user_constraint",
            "content": f"Keep project interface {index} stable for compatibility.",
        }
        for index in range(100)
    ]
    trace.append(
        {"event_id": "event-result", "type": "task_result", "status": "success"}
    )

    metadata = ReflectionEngine().reflect("Preserve interfaces", trace).to_memory_entry()[
        "metadata"
    ]["structured_reflection"]

    assert len(json.dumps(metadata, ensure_ascii=False)) <= 32_768
