from __future__ import annotations

import json
from pathlib import Path

from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from scripts.reflection_llm_evaluator import (
    evaluate_holdout,
    load_holdout_dataset,
    write_report,
)


HOLDOUT = Path(__file__).parent / "fixtures" / "reflection_llm_holdout"


def _known_event_ids(evidence) -> set[str]:
    ids: set[str] = set()
    for item in evidence.files_read + evidence.files_changed + evidence.referenced_files:
        ids.update(item.event_ids)
    for item in evidence.tool_calls:
        ids.add(item.call_event_id)
        ids.update(item.result_event_ids)
    for item in evidence.libraries:
        ids.update(item.event_ids)
    for item in evidence.errors:
        ids.update(item.source_event_ids)
    for item in evidence.recoveries:
        ids.update(item.event_ids)
    for item in evidence.recovery_suggestions:
        ids.update(item.event_ids)
    for item in evidence.decisions:
        ids.update(item.event_ids)
    for item in evidence.verification:
        ids.update(item.event_ids)
    return ids


def test_holdout_is_independent_large_and_covers_required_classes() -> None:
    cases = load_holdout_dataset(HOLDOUT)
    categories = {case["category"] for case in cases}

    assert len(cases) >= 30
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {
        "causal_paraphrase",
        "cross_event_decision",
        "multilingual",
        "partial_recovery",
        "causal_trap",
        "prompt_injection",
        "routine_low_value",
        "ambiguous_failure",
    } <= categories


def test_holdout_manual_claim_labels_reference_extracted_task_evidence() -> None:
    extractor = TraceEvidenceExtractor()
    for case in load_holdout_dataset(HOLDOUT):
        evidence = extractor.extract(case["task_description"], case["trace"])
        event_ids = _known_event_ids(evidence)
        for claim in case["expected_claims"]:
            assert set(claim["evidence_ids"]) <= event_ids, case["case_id"]
        script = case["llm_script"]
        if script["kind"] == "candidate":
            raw = json.dumps(
                {
                    "task_summary": case["task_description"][:200],
                    "outcome": evidence.outcome,
                    "claims": script["claims"],
                },
                ensure_ascii=False,
            )
            # Candidate fixtures that model parser attacks are assigned a
            # non-candidate script kind and therefore do not pass this check.
            parse_llm_candidate(raw, case["task_description"], evidence, ReflectionLLMConfig())


def test_holdout_evaluation_is_deterministic_and_keeps_shadow_rule_equal() -> None:
    first = evaluate_holdout(HOLDOUT)
    second = evaluate_holdout(HOLDOUT)

    assert first == second
    assert first["modes"]["rule"] == first["modes"]["llm_shadow_rule"]
    assert first["evaluation_client"] == "scripted_offline_fixture"
    assert first["case_count"] >= 30


def test_holdout_report_preserves_zero_false_write_boundary(tmp_path: Path) -> None:
    report = evaluate_holdout(HOLDOUT)
    rule = report["modes"]["rule"]
    shadow_llm = report["modes"]["llm_shadow_llm"]

    assert rule["value_quality"]["precision"] == 1.0
    assert rule["value_quality"]["low_value_false_write_rate"] == 0.0
    assert shadow_llm["value_quality"]["low_value_false_write_rate"] == 0.0
    assert shadow_llm["invalid_evidence_references"] == 0
    assert shadow_llm["epistemic_mismatches"] == 0
    assert report["comparative_cases"]["llm_only_correct_claims"]

    output = tmp_path / "report.json"
    write_report(report, output)
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] >= 30
