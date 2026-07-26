from __future__ import annotations

import copy
import json
import math
import socket
from collections import Counter
from pathlib import Path

import pytest

from scripts.memory_retrieval_evaluator import (
    ARMS,
    CATEGORIES,
    DatasetValidationError,
    REFERENCE_TIME,
    calculate_case_metrics,
    evaluate_arm,
    load_dataset,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    snapshot_formal_memory,
)


DATASET = Path(__file__).parent / "fixtures" / "memory_retrieval_golden"


def _cases() -> list[dict]:
    return load_dataset(DATASET)


def _case(case_id: str) -> dict:
    return next(case for case in _cases() if case["case_id"] == case_id)


def _write_document(tmp_path: Path, document: dict, name: str = "cases.json") -> Path:
    case_dir = tmp_path / "cases"
    case_dir.mkdir(exist_ok=True)
    (case_dir / name).write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def _document_for(case: dict) -> dict:
    return {
        "schema_version": "1.0",
        "synthetic_data": True,
        "reference_time": REFERENCE_TIME,
        "cases": [case],
    }


def test_dataset_has_eighty_balanced_synthetic_cases() -> None:
    cases = _cases()

    assert len(cases) == 80
    assert Counter(case["category"] for case in cases) == Counter({category: 8 for category in CATEGORIES})
    assert all(case["case_id"].startswith("mr-") for case in cases)


def test_dataset_load_order_is_stable() -> None:
    first = [case["case_id"] for case in _cases()]
    second = [case["case_id"] for case in _cases()]

    assert first == sorted(first)
    assert first == second


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    case = _case("mr-exact-01")
    document = _document_for(case)
    document["cases"].append(copy.deepcopy(case))

    with pytest.raises(DatasetValidationError, match="duplicate case_id"):
        load_dataset(_write_document(tmp_path, document))


def test_duplicate_memory_id_is_rejected(tmp_path: Path) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    case["memories"][1]["id"] = case["memories"][0]["id"]

    with pytest.raises(DatasetValidationError, match="duplicate memory IDs"):
        load_dataset(_write_document(tmp_path, _document_for(case)))


def test_unknown_expected_id_is_rejected(tmp_path: Path) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    case["must_include_ids"] = ["mr-does-not-exist"]

    with pytest.raises(DatasetValidationError, match="expected IDs do not exist"):
        load_dataset(_write_document(tmp_path, _document_for(case)))


def test_overlapping_include_and_exclude_id_is_rejected(tmp_path: Path) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    case["must_exclude_ids"].append(case["primary_id"])

    with pytest.raises(DatasetValidationError, match="overlap"):
        load_dataset(_write_document(tmp_path, _document_for(case)))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scope", "team", "illegal scope"),
        ("tier", "forever", "illegal tier"),
        ("lifecycle_status", "sleeping", "illegal lifecycle"),
        ("safety_status", "trusted", "illegal safety"),
        ("approval_status", "allowed", "illegal approval"),
    ],
)
def test_illegal_memory_states_are_rejected(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    case["memories"][0][field] = value

    with pytest.raises(DatasetValidationError, match=message):
        load_dataset(_write_document(tmp_path, _document_for(case)))


def test_missing_grade_is_rejected(tmp_path: Path) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    del case["memories"][0]["graded_relevance"]

    with pytest.raises(DatasetValidationError, match="keys mismatch"):
        load_dataset(_write_document(tmp_path, _document_for(case)))


def test_non_synthetic_document_is_rejected(tmp_path: Path) -> None:
    document = _document_for(_case("mr-exact-01"))
    document["synthetic_data"] = False

    with pytest.raises(DatasetValidationError, match="synthetic_data must be true"):
        load_dataset(_write_document(tmp_path, document))


def test_ambiguous_timestamp_is_rejected(tmp_path: Path) -> None:
    case = copy.deepcopy(_case("mr-exact-01"))
    case["memories"][0]["updated_at"] += 1

    with pytest.raises(DatasetValidationError, match="timestamps are not fixed"):
        load_dataset(_write_document(tmp_path, _document_for(case)))


def test_rank_metrics_match_hand_calculation() -> None:
    grades = {"a": 3, "b": 2, "c": 0, "d": 1}
    output = ["b", "c", "a"]

    assert precision_at_k(output, grades, 1) == 1.0
    assert precision_at_k(output, grades, 3) == pytest.approx(2 / 3)
    assert recall_at_k(output, grades, 1) == pytest.approx(1 / 3)
    assert recall_at_k(output, grades, 3) == pytest.approx(2 / 3)
    assert reciprocal_rank(output, grades) == 1.0
    expected_dcg = 3 / math.log2(2) + 0 / math.log2(3) + 7 / math.log2(4)
    ideal_dcg = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(output, grades, 5) == pytest.approx(expected_dcg / ideal_dcg)


def test_recall_and_ndcg_are_unavailable_without_relevant_labels() -> None:
    grades = {"a": 0, "b": 0}

    assert recall_at_k(["a"], grades, 5) is None
    assert ndcg_at_k(["a"], grades, 5) is None
    assert reciprocal_rank(["a"], grades) == 0.0


def test_negative_false_injection_and_exclude_violation_are_counted() -> None:
    case = _case("mr-negative-01")
    emitted = [case["memories"][0]["id"]]
    metrics = calculate_case_metrics(case, emitted)

    assert metrics["negative_false_injection"] is True
    assert metrics["must_exclude_violation"] is True


def test_inactive_leakage_is_counted_from_fixture_state() -> None:
    case = _case("mr-life-04")
    metrics = calculate_case_metrics(case, ["mr-life-04-archival"])

    assert metrics["inactive_memory_leakage"] is True
    assert metrics["inactive_leaked_ids"] == ["mr-life-04-archival"]


def test_duplicate_content_is_counted() -> None:
    case = _case("mr-budget-01")
    metrics = calculate_case_metrics(
        case, ["mr-budget-01-primary", "mr-budget-01-dup-local"]
    )

    assert metrics["duplicate_injection"] is True
    assert metrics["duplicate_count"] == 1


def test_count_and_token_budget_violations_are_separate() -> None:
    case = _case("mr-budget-05")
    metrics = calculate_case_metrics(
        case,
        ["mr-budget-05-primary", "mr-budget-05-secondary"],
        memory_tokens=case["max_tokens"] + 1,
    )

    assert metrics["max_memories_violation"] is True
    assert metrics["token_budget_violation"] is True


def test_returned_rendered_recorded_and_feedback_ids_remain_separate() -> None:
    case = _case("mr-budget-06")
    returned = [memory["id"] for memory in case["memories"][:7]]
    rendered = returned[:5]
    recorded = returned
    metrics = calculate_case_metrics(
        case,
        returned,
        rendered_ids=rendered,
        recorded_ids=recorded,
        feedback_ids=recorded,
    )

    assert metrics["returned_rendered_disagreement"] is True
    assert metrics["rendered_recorded_disagreement"] is True
    assert metrics["feedback_attribution_precision"] == pytest.approx(5 / 7)


def test_all_four_arms_use_distinct_manager_isolation_ids() -> None:
    case = _case("mr-entry-01")
    results = [evaluate_arm(case, arm) for arm in ARMS]

    isolation_ids = {result["manager_isolation_id"] for result in results}
    assert len(isolation_ids) == 4
    assert {result["arm"] for result in results} == set(ARMS)


def test_fixed_time_makes_arm_core_result_repeatable() -> None:
    case = _case("mr-entry-01")
    first = evaluate_arm(case, "pipeline_inject")
    second = evaluate_arm(case, "pipeline_inject")
    first.pop("latency_ms")
    second.pop("latency_ms")

    assert first == second


def test_scope_save_observation_uses_deterministic_counts() -> None:
    result = evaluate_arm(_case("mr-entry-01"), "pipeline_inject")

    assert result["save_scopes"] == sorted(result["save_scopes"])
    assert sum(result["save_scope_counts"].values()) == result["save_count"]


def test_evaluator_does_not_mutate_case_input() -> None:
    case = _case("mr-entry-04")
    original = copy.deepcopy(case)

    for arm in ARMS:
        evaluate_arm(case, arm)

    assert case == original


def test_arm_execution_does_not_modify_formal_memory() -> None:
    project_root = Path(__file__).resolve().parents[1]
    before = snapshot_formal_memory(project_root)

    evaluate_arm(_case("mr-entry-01"), "pipeline_inject")

    assert snapshot_formal_memory(project_root) == before


def test_pipeline_arms_do_not_open_a_network_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

    evaluate_arm(_case("mr-entry-01"), "pipeline_read")
    evaluate_arm(_case("mr-entry-01"), "pipeline_inject")
    assert calls == []


def test_context_ids_are_from_exact_markers_not_fuzzy_substrings() -> None:
    result = evaluate_arm(_case("mr-entry-01"), "manager_context_query")

    assert result["returned_ids"] == result["rendered_ids"]
    assert all(entry_id.startswith("mr-") for entry_id in result["rendered_ids"])
