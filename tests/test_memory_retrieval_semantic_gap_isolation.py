from __future__ import annotations

from pathlib import Path

from scripts.memory_retrieval_semantic_gap_evaluator import (
    evaluate_case,
    load_dataset,
    snapshot_tree,
    verify_frozen_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap"


def test_single_case_uses_temporary_home_and_preserves_real_state(minicode_real_home: Path) -> None:
    dataset = load_dataset(DATASET_ROOT)
    case = next(case for case in dataset["cases"] if case["case_id"] == "sg-pos-context-01")
    formal_root = minicode_real_home / ".mini-code"
    before = snapshot_tree(formal_root)
    result = evaluate_case(case, dataset["background"])
    after = snapshot_tree(formal_root)
    assert before == after
    assert result["diagnostic_side_effects"] == {
        "counters_unchanged": True,
        "filesystem_unchanged": True,
        "scope_saves": 0,
    }
    assert result["temporary_file_count"] > 0


def test_single_case_evaluation_does_not_modify_frozen_dataset() -> None:
    before = verify_frozen_dataset(DATASET_ROOT)
    dataset = load_dataset(DATASET_ROOT)
    evaluate_case(dataset["cases"][0], dataset["background"])
    after = verify_frozen_dataset(DATASET_ROOT)
    assert before == after
