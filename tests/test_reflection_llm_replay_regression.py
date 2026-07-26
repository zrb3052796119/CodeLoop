from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import LLMReflectionSynthesizer, ReflectionLLMConfig
from minicode.reflection_replay import ReplayStructuredGenerationClient
from minicode.reflection_shadow_metrics import reflection_task_identifier
from minicode.reflection_synthesis import ReflectionClaimValidator, ReflectionValueGate
from scripts.reflection_llm_evaluator import load_holdout_dataset


HOLDOUT = Path(__file__).parent / "fixtures" / "reflection_llm_holdout"
REPLAY = Path(__file__).parent / "fixtures" / "reflection_llm_replay"


def _fixtures() -> dict[str, dict]:
    payload = json.loads((REPLAY / "responses.json").read_text(encoding="utf-8"))
    return {item["case_id"]: item for item in payload["responses"]}


def _attempt(case_id: str):
    fixture = _fixtures()[case_id]
    case = next(
        item for item in load_holdout_dataset(HOLDOUT) if item["case_id"] == case_id
    )
    response = json.dumps(fixture["response"], ensure_ascii=False)
    record = {
        "task_identifier": reflection_task_identifier(case["task_description"]),
        "sanitized_response": response,
        "replay_response_hash": hashlib.sha256(response.encode("utf-8")).hexdigest(),
        "usage_source": "unavailable",
    }
    evidence = TraceEvidenceExtractor().extract(
        case["task_description"], case["trace"]
    )
    attempt = LLMReflectionSynthesizer(
        ReplayStructuredGenerationClient([record]),
        ReflectionLLMConfig(prompt_version=fixture["prompt_version"]),
    ).attempt(case["task_description"], evidence)
    return fixture, evidence, attempt


@pytest.mark.parametrize(
    "case_id",
    [
        "holdout-verified-recovery-007",
        "holdout-redacted-secret-error-028",
    ],
)
def test_reviewed_old_deepseek_response_remains_strictly_rejected(
    case_id: str,
) -> None:
    fixture, _evidence, attempt = _attempt(case_id)

    assert attempt.success is False
    assert attempt.failure_code == fixture["expected_parser_failure_code"]
    assert (
        attempt.failure_detail_code
        == fixture["expected_parser_failure_detail_code"]
    )
    assert len(attempt.failure_detail_code or "") < 80


@pytest.mark.parametrize(
    "case_id",
    [
        "holdout-unverified-recovery-024",
        "holdout-partial-recovery-008",
    ],
)
def test_reviewed_deepseek_recovery_laundering_replay_is_value_rejected(
    case_id: str,
) -> None:
    fixture, evidence, attempt = _attempt(case_id)

    assert attempt.success is True
    assert attempt.candidate is not None
    validation = ReflectionClaimValidator().validate(attempt.candidate, evidence)
    decision = ReflectionValueGate().evaluate(
        attempt.candidate,
        validation,
        evidence,
    )
    assert decision.accepted is False
    assert fixture["expected_value_reason_code"] in decision.reason_codes
    assert {claim.claim_type for claim in validation.valid_claims} == {
        "error_pattern"
    }


def test_reviewed_replay_pipeline_does_not_create_memory_files(
    tmp_path: Path,
) -> None:
    _attempt("holdout-unverified-recovery-024")

    assert list(tmp_path.iterdir()) == []
