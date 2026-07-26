from __future__ import annotations

import math
import json
import copy
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from scripts.reflection_evaluator import (
    DatasetValidationError,
    evaluate_case,
    evaluate_dataset,
    load_dataset,
    precision_recall_f1,
    render_comparison_report,
    write_json_report,
)


GOLDEN_DATASET = Path(__file__).parent / "fixtures" / "reflection_golden"


def _known_defect_case() -> dict:
    return {
        "schema_version": 1,
        "case_id": "known-defects-001",
        "category": "error_deduplication",
        "description": "Known extraction defects remain visible in the baseline",
        "task_description": "Run the focused auth test",
        "trace": [
            {
                "event_id": "event-1",
                "call_id": "call-1",
                "type": "tool_call",
                "tool_name": "run_command",
                "input": {"command": "pytest tests/test_auth.py -q"},
            },
            {
                "event_id": "event-2",
                "call_id": "call-1",
                "type": "tool_result",
                "tool_name": "run_command",
                "status": "error",
                "is_error": True,
                "output_summary": "Expired token was accepted",
            },
            {
                "event_id": "event-3",
                "call_id": "call-1",
                "type": "error",
                "tool_name": "run_command",
                "error_type": "AssertionError",
                "message": "Expired token was accepted",
            },
            {
                "event_id": "event-4",
                "call_id": "call-1",
                "type": "recovery",
                "tool_name": "edit_file",
                "action": "Re-read src/auth.py before applying the smaller patch.",
                "files": ["src/auth.py"],
            },
            {
                "event_id": "event-5",
                "type": "assistant_step",
                "content": "The implementation is changing the validation order.",
            },
            {"event_id": "event-6", "type": "task_result", "status": "success"},
        ],
        "expected_evidence": {
            "files_read": [],
            "files_changed": ["src/auth.py"],
            "tools": ["run_command"],
            "libraries": [
                {"name": "gin", "status": "not_dependency", "evidence_ids": ["event-5"]}
            ],
            "errors": [
                {
                    "error_id": "error-1",
                    "call_id": "call-1",
                    "tool_name": "run_command",
                    "error_type": "AssertionError",
                    "message_key": "expired_token_accepted",
                    "required_terms": ["expired token", "accepted"],
                    "source_event_ids": ["event-2", "event-3"],
                }
            ],
            "recoveries": [
                {
                    "recovery_id": "recovery-1",
                    "error_id": "error-1",
                    "required_terms": ["re-read", "smaller patch"],
                    "evidence_ids": ["event-4"],
                }
            ],
            "decisions": [],
            "verification": [],
            "outcome": "success",
        },
        "expected_value": {
            "should_write_memory": True,
            "reasons": ["error_recovered"],
        },
        "expected_claims": [
            {
                "claim_id": "claim-1",
                "claim_type": "recovery",
                "semantic_key": "reread_before_smaller_patch",
                "evidence_ids": ["event-4"],
                "epistemic_status": "confirmed",
                "required_terms": ["re-read", "smaller patch"],
                "forbidden_terms": [],
            }
        ],
        "forbidden_claims": [],
        "notes": "The current defects are expected baseline observations, not labels.",
    }


def test_precision_recall_f1_handles_matches_and_empty_sets() -> None:
    metrics = precision_recall_f1(
        expected={"src/auth.py", "tests/test_auth.py"},
        actual={"src/auth.py", "pytest tests/test_auth.py -q"},
    )

    assert metrics == {
        "true_positives": 1,
        "false_positives": 1,
        "false_negatives": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }

    empty = precision_recall_f1(expected=set(), actual=set())
    assert empty["precision"] == 1.0
    assert empty["recall"] == 1.0
    assert empty["f1"] == 1.0
    assert all(not math.isnan(value) for value in empty.values() if isinstance(value, float))


def test_load_dataset_validates_and_returns_stable_case_order(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    cases = [
        {
            "schema_version": 1,
            "case_id": "case-b",
            "category": "low_value_tasks",
            "description": "Routine read",
            "task_description": "Read a file",
            "trace": [
                {"event_id": "event-1", "type": "tool_call", "tool_name": "read_file"},
                {"event_id": "event-2", "type": "task_result", "status": "success"},
            ],
            "expected_evidence": {
                "files_read": [],
                "files_changed": [],
                "tools": ["read_file"],
                "libraries": [],
                "errors": [],
                "recoveries": [],
                "decisions": [],
                "verification": [],
                "outcome": "success",
            },
            "expected_value": {
                "should_write_memory": False,
                "reasons": ["routine_read_only"],
            },
            "expected_claims": [],
            "forbidden_claims": [],
            "notes": "Manual label",
        },
        {
            "schema_version": 1,
            "case_id": "case-a",
            "category": "decisions_and_constraints",
            "description": "Confirmed decision",
            "task_description": "Keep the public API stable",
            "trace": [
                {"event_id": "event-1", "type": "assistant_step", "content": "I will keep the public API stable."},
                {"event_id": "event-2", "type": "task_result", "status": "success"},
            ],
            "expected_evidence": {
                "files_read": [],
                "files_changed": [],
                "tools": [],
                "libraries": [],
                "errors": [],
                "recoveries": [],
                "decisions": [
                    {
                        "decision_id": "decision-1",
                        "semantic_key": "preserve_public_api",
                        "evidence_ids": ["event-1"],
                        "epistemic_status": "confirmed",
                        "required_terms": ["public api", "stable"],
                    }
                ],
                "verification": [],
                "outcome": "success",
            },
            "expected_value": {
                "should_write_memory": True,
                "reasons": ["stable_project_constraint"],
            },
            "expected_claims": [
                {
                    "claim_id": "claim-1",
                    "claim_type": "constraint",
                    "semantic_key": "preserve_public_api",
                    "evidence_ids": ["event-1"],
                    "epistemic_status": "confirmed",
                    "required_terms": ["public api", "stable"],
                    "forbidden_terms": [],
                }
            ],
            "forbidden_claims": [],
            "notes": "Manual label",
        },
    ]
    (cases_dir / "cases.json").write_text(
        json.dumps({"schema_version": 1, "cases": cases}),
        encoding="utf-8",
    )

    loaded = load_dataset(tmp_path)

    assert [case["case_id"] for case in loaded] == ["case-a", "case-b"]


def test_task_evidence_adapter_eliminates_known_precision_defects() -> None:
    result = evaluate_case(_known_defect_case())

    assert "pytest tests/test_auth.py -q" not in result["actual_evidence"]["files_read"]
    assert result["actual_evidence"]["files_changed"] == ["src/auth.py"]
    assert "gin" not in result["actual_evidence"]["libraries"]
    assert result["error_deduplication"]["expected_logical_errors"] == 1
    assert result["error_deduplication"]["actual_error_records"] == 1
    assert result["error_deduplication"]["duplicate_error_records"] == 0
    assert result["error_deduplication"]["call_id_association_errors"] == 0
    assert "error_call_id_association" not in result["capability_gaps"]


def test_evaluator_uses_task_evidence_references_and_verification() -> None:
    case = copy.deepcopy(_known_defect_case())
    case["trace"][0]["command"] = "pytest tests/test_auth.py -q"
    case["expected_evidence"]["verification"] = [
        {
            "verification_id": "verify-1",
            "result": "failed",
            "required_terms": ["expired token", "accepted"],
            "evidence_ids": ["event-1", "event-2"],
        }
    ]

    result = evaluate_case(case)

    assert result["evidence_metrics"]["verification"]["true_positives"] == 1
    assert result["evidence_reference_errors"] == 0
    assert result["actual_evidence"]["errors"][0]["call_id"] == "call-1"


def test_evaluator_prefers_structured_claims_and_value_decision() -> None:
    case = next(
        item
        for item in load_dataset(GOLDEN_DATASET)
        if item["case_id"] == "low-value-read-file-001"
    )

    result = evaluate_case(case)

    assert result["confidence"] >= 0.5
    assert result["predicted_write"] is False
    assert result["claims"]["generated_claims"] == 0
    assert result["claims"]["valid_claims"] == 0
    assert result["claims"]["rejected_claims"] == 0
    assert result["claims"]["persistable_claims"] == 0
    assert "routine_read_only" in result["value_reason_codes"]


def test_evaluator_validation_probe_exercises_rejected_candidate_claims() -> None:
    case = copy.deepcopy(
        next(
            item
            for item in load_dataset(GOLDEN_DATASET)
            if item["case_id"] == "decision-user-public-api-001"
        )
    )
    case["expected_value"] = {
        "should_write_memory": False,
        "reasons": ["invalid_evidence_reference"],
    }
    case["expected_claims"] = []
    case["validation_probe_claims"] = [
        {
            "claim_id": "probe-1",
            "claim_type": "constraint",
            "semantic_key": "forged_constraint",
            "statement": "Project constraint: preserve the public parse API.",
            "evidence_ids": ["event-does-not-exist"],
            "epistemic_status": "confirmed",
        }
    ]

    result = evaluate_case(case)

    assert result["predicted_write"] is False
    assert result["claims"]["generated_claims"] == 1
    assert result["claims"]["valid_claims"] == 0
    assert result["claims"]["rejected_claims"] == 1
    assert "invalid_evidence_reference" in {
        issue["code"] for issue in result["claims"]["validation_issues"]
    }


def test_evaluate_dataset_is_deterministic_and_writes_parseable_json(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    cases_dir = dataset / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / "known.json").write_text(
        json.dumps({"schema_version": 1, "cases": [_known_defect_case()]}),
        encoding="utf-8",
    )

    first = evaluate_dataset(dataset)
    second = evaluate_dataset(dataset)
    output = tmp_path / "artifacts" / "baseline.json"
    write_json_report(first, output)

    assert first == second
    assert json.loads(output.read_text(encoding="utf-8")) == first
    assert first["case_count"] == 1
    assert first["value_selection"]["confusion_matrix"]["should_write_but_rejected"] == 1
    assert not (tmp_path / ".mini-code-memory").exists()
    assert not (tmp_path / ".mini-code-memory-local").exists()


def test_golden_dataset_has_balanced_valid_cases() -> None:
    cases = load_dataset(GOLDEN_DATASET)

    assert len(cases) >= 40
    assert {case["category"] for case in cases} == {
        "path_extraction",
        "library_detection",
        "error_deduplication",
        "recovery_and_verification",
        "low_value_tasks",
        "decisions_and_constraints",
        "security_and_redaction",
        "multilingual_and_edge_cases",
    }
    category_counts = Counter(case["category"] for case in cases)
    assert min(category_counts.values()) >= 5


def test_bad_case_reports_source_and_duplicate_event_id(tmp_path: Path) -> None:
    case = copy.deepcopy(_known_defect_case())
    case["case_id"] = "bad-duplicate-event"
    case["trace"][1]["event_id"] = case["trace"][0]["event_id"]
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    source = cases_dir / "broken.json"
    source.write_text(
        json.dumps({"schema_version": 1, "cases": [case]}),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError) as exc_info:
        load_dataset(tmp_path)

    message = str(exc_info.value)
    assert "broken.json" in message
    assert "bad-duplicate-event" in message
    assert "duplicate event_id" in message


def test_dataset_rejects_unredacted_synthetic_secret(tmp_path: Path) -> None:
    case = copy.deepcopy(_known_defect_case())
    case["case_id"] = "bad-secret"
    case["trace"][0]["input"]["api_key"] = "sk-synthetic-secret-123456"
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "secret.json").write_text(
        json.dumps({"schema_version": 1, "cases": [case]}),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="unredacted secret"):
        load_dataset(tmp_path)


def test_schema_and_claim_event_references_are_enforced(tmp_path: Path) -> None:
    schema = json.loads((GOLDEN_DATASET / "schema.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["case"]["properties"]["schema_version"]["const"] == 1

    case = copy.deepcopy(_known_defect_case())
    case["case_id"] = "bad-claim-reference"
    case["expected_claims"][0]["evidence_ids"] = ["event-does-not-exist"]
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "bad-reference.json").write_text(
        json.dumps({"schema_version": 1, "cases": [case]}),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="event-does-not-exist"):
        load_dataset(tmp_path)


def test_error_type_must_be_grounded_in_referenced_events(tmp_path: Path) -> None:
    case = copy.deepcopy(_known_defect_case())
    case["case_id"] = "bad-invented-error-type"
    case["expected_evidence"]["errors"][0]["error_type"] = "InventedError"
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "invented-error.json").write_text(
        json.dumps({"schema_version": 1, "cases": [case]}),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="InventedError"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        (lambda case: case["trace"][0].pop("type"), "missing type"),
        (
            lambda case: case["expected_evidence"]["libraries"].append(
                {"name": "example", "status": "maybe", "evidence_ids": ["event-1"]}
            ),
            "library status",
        ),
        (lambda case: case["expected_claims"][0].pop("claim_id"), "claim_id"),
    ],
)
def test_schema_validator_rejects_invalid_nested_fields(
    tmp_path: Path, mutate, expected_message: str
) -> None:
    case = copy.deepcopy(_known_defect_case())
    case["case_id"] = f"bad-schema-{expected_message.replace(' ', '-')}"
    mutate(case)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "bad-schema.json").write_text(
        json.dumps({"schema_version": 1, "cases": [case]}),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match=expected_message):
        load_dataset(tmp_path)


def test_current_engine_runs_every_golden_case_without_crashing() -> None:
    report = evaluate_dataset(GOLDEN_DATASET)

    assert report["case_count"] == 78
    assert report["dataset_slices"]["original_shared_40"]["case_count"] == 40
    assert report["dataset_slices"]["task_evidence_48"]["case_count"] == 48
    assert report["dataset_slices"]["claim_value_30"]["case_count"] == 30
    assert report["engine_errors"] == []


def test_comparison_uses_immutable_shared_case_ids() -> None:
    baseline_path = Path(__file__).parent.parent / "artifacts" / "reflection-accuracy-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    shared_ids = {case["case_id"] for case in baseline["cases"]}

    current_shared = evaluate_dataset(GOLDEN_DATASET, shared_ids)
    current_full = evaluate_dataset(GOLDEN_DATASET)
    comparison = render_comparison_report(baseline, current_shared, current_full)

    assert current_shared["case_count"] == 40
    assert current_full["case_count"] == 78
    assert current_shared["outcome_accuracy"]["correct"] == 38
    assert current_full["outcome_accuracy"]["correct"] == 76
    assert "Before F1" in comparison
    assert "Current full cases: `78`" in comparison
    assert "78.4%" in comparison


def test_report_redacts_synthetic_secrets(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    write_json_report(
        {
            "api_key": "sk-synthetic-secret-123456",
            "header": "Authorization: Bearer synthetic.header.value",
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "sk-synthetic-secret-123456" not in text
    assert "synthetic.header.value" not in text
    assert "REDACTED" in text


def test_deep_input_is_reported_without_recursion_crash() -> None:
    case = copy.deepcopy(_known_defect_case())
    nested: dict = {}
    cursor = nested
    for _ in range(2_000):
        child: dict = {}
        cursor["next"] = child
        cursor = child
    case["trace"][0]["input"] = nested

    result = evaluate_case(case)

    assert result["case_id"] == case["case_id"]
    assert "engine_error" in result


def test_cli_generates_machine_and_human_reports(tmp_path: Path) -> None:
    json_output = tmp_path / "baseline.json"
    markdown_output = tmp_path / "baseline.md"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_reflection.py",
            "--dataset",
            str(GOLDEN_DATASET),
            "--output",
            str(json_output),
            "--markdown",
            str(markdown_output),
        ],
        cwd=Path(__file__).parent.parent,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path / "isolated-home")},
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(json_output.read_text(encoding="utf-8"))["case_count"] == 78
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Reflection Accuracy - ReflectionValueGate" in markdown
    assert "command interpreted as a path" in markdown
    assert "changing interpreted as gin" in markdown
    assert "duplicate error records" in markdown
    assert not (tmp_path / "isolated-home" / ".mini-code").exists()
