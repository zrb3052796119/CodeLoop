from __future__ import annotations

from copy import deepcopy
import json

from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_evidence import TraceEvidenceExtractor


def _extract(trace: list[dict]):
    return TraceEvidenceExtractor().extract("Test task", trace)


def test_legacy_event_ids_are_stable_unique_and_do_not_mutate_input() -> None:
    trace = [
        {"type": "tool_call", "call_id": "c1", "tool_name": "read_file", "input": {"path": "src/a.py"}},
        {"type": "tool_result", "call_id": "c1", "tool_name": "read_file", "status": "success", "files": ["src/a.py"]},
    ]
    original = deepcopy(trace)

    first = _extract(trace)
    second = _extract(trace)

    assert first.to_dict() == second.to_dict()
    assert trace == original
    assert first.files_read[0].event_ids == (
        "legacy-event-000001",
        "legacy-event-000002",
    )


def test_file_roles_keep_event_and_call_ids_without_command_false_positive() -> None:
    trace = [
        {"event_id": "e1", "call_id": "read", "type": "tool_call", "tool_name": "read_file", "input": {"path": "src/a.py"}},
        {"event_id": "e2", "call_id": "edit", "type": "tool_result", "tool_name": "edit_file", "status": "success", "changed_files": ["src/b.py"]},
        {"event_id": "e3", "call_id": "test", "type": "tool_call", "tool_name": "run_command", "input": {"command": "pytest tests/test_b.py -q"}},
    ]

    evidence = _extract(trace)

    assert [(item.path, item.call_id) for item in evidence.files_read] == [("src/a.py", "read")]
    assert [(item.path, item.call_id) for item in evidence.files_changed] == [("src/b.py", "edit")]
    assert [item.path for item in evidence.referenced_files] == ["tests/test_b.py"]
    assert "pytest tests/test_b.py -q" not in str(evidence.to_dict())


def test_same_call_tool_result_and_error_merge_with_complete_sources() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "tool_result", "tool_name": "run_command", "status": "error", "is_error": True, "output_summary": "Expired token was accepted"},
        {"event_id": "e2", "call_id": "c1", "type": "error", "tool_name": "run_command", "error_type": "AssertionError", "message": "Expired token was accepted"},
    ]

    evidence = _extract(trace)

    assert len(evidence.errors) == 1
    assert evidence.errors[0].call_id == "c1"
    assert evidence.errors[0].error_type == "AssertionError"
    assert evidence.errors[0].source_event_ids == ("e1", "e2")


def test_same_error_message_from_different_calls_remains_distinct() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "error", "tool_name": "run_command", "message": "Connection timed out"},
        {"event_id": "e2", "call_id": "c2", "type": "error", "tool_name": "run_command", "message": "Connection timed out"},
    ]

    evidence = _extract(trace)

    assert [error.call_id for error in evidence.errors] == ["c1", "c2"]
    assert [error.error_id for error in evidence.errors] == ["error-000001", "error-000002"]


def test_recovery_suggestion_is_separate_from_real_recovery() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "error", "tool_name": "edit_file", "message": "context mismatch"},
        {"event_id": "e2", "call_id": "c1", "type": "recovery_suggestion", "suggestion": "Re-read and retry"},
        {"event_id": "e3", "call_id": "c2", "type": "recovery", "action": "Re-read the current block and applied a smaller patch", "related_error_call_ids": ["c1"], "files_changed": ["src/a.py"]},
    ]

    evidence = _extract(trace)

    assert len(evidence.recovery_suggestions) == 1
    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].related_error_ids == ("error-000001",)


def test_manifest_import_install_and_weak_mentions_have_distinct_strength() -> None:
    trace = [
        {"event_id": "e1", "call_id": "req", "type": "tool_call", "tool_name": "read_file", "input": {"path": "requirements.txt"}},
        {"event_id": "e2", "call_id": "req", "type": "tool_result", "tool_name": "read_file", "status": "success", "output_summary": "fastapi==0.115\nuvicorn>=0.31"},
        {"event_id": "e3", "call_id": "src", "type": "tool_result", "tool_name": "read_file", "status": "success", "files": ["src/model.py"], "output_summary": "from sklearn.model_selection import train_test_split"},
        {"event_id": "e4", "call_id": "pip", "type": "tool_call", "tool_name": "run_command", "command": "python -m pip install pytest"},
        {"event_id": "e5", "type": "assistant_step", "content": "Changing behavior; consider Django, but src/react.py is local."},
    ]

    evidence = _extract(trace)
    by_name = {item.name: item for item in evidence.libraries}

    assert {"fastapi", "uvicorn", "scikit-learn", "pytest"} <= set(by_name)
    assert all(by_name[name].status == "confirmed" for name in ("fastapi", "uvicorn", "scikit-learn", "pytest"))
    assert by_name["django"].status == "weak_mention"
    assert "gin" not in by_name
    assert "react" not in by_name


def test_structured_package_manifest_dependencies_are_confirmed() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "tool_call", "tool_name": "read_file", "input": {"path": "package.json"}},
        {"event_id": "e2", "call_id": "c1", "type": "tool_result", "tool_name": "read_file", "status": "success", "structured_result": {"dependencies": {"react": "19", "zod": "4"}}},
    ]

    libraries = _extract(trace).libraries

    assert [(item.name, item.status) for item in libraries] == [
        ("react", "confirmed"),
        ("zod", "confirmed"),
    ]


def test_verification_requires_check_behavior_and_result() -> None:
    trace = [
        {"event_id": "e1", "call_id": "read", "type": "tool_result", "tool_name": "read_file", "status": "success", "output_summary": "read ok"},
        {"event_id": "e2", "call_id": "test", "type": "tool_call", "tool_name": "run_command", "command": "pytest tests/test_auth.py -q"},
        {"event_id": "e3", "call_id": "test", "type": "tool_result", "tool_name": "run_command", "status": "success", "output_summary": "14 passed"},
        {"event_id": "e4", "call_id": "lint", "type": "tool_result", "tool_name": "ruff", "status": "error", "output_summary": "lint failed"},
    ]

    verification = _extract(trace).verification

    assert [(item.command_kind, item.scope, item.result) for item in verification] == [
        ("test", "targeted", "passed"),
        ("lint", "unknown", "failed"),
    ]
    assert verification[0].event_ids == ("e2", "e3")


def test_routine_assistant_step_is_not_a_decision_but_constraints_are() -> None:
    trace = [
        {"event_id": "e1", "type": "assistant_step", "content": "I will read the file and start by listing files."},
        {"event_id": "e2", "type": "user_constraint", "content": "Do not change the public parse() API or return type."},
        {"event_id": "e3", "type": "assistant_step", "content": "I choose Python 3.11-compatible syntax because pyproject.toml requires it."},
    ]

    decisions = _extract(trace).decisions

    assert len(decisions) == 2
    assert "public parse() API" in decisions[0].statement
    assert "Python 3.11-compatible" in decisions[1].statement


def test_outcome_distinguishes_historical_errors_from_final_result() -> None:
    recovered = _extract([
        {"event_id": "e1", "call_id": "c1", "type": "error", "message": "failed first"},
        {"event_id": "e2", "call_id": "c2", "type": "recovery", "action": "fixed it", "related_error_call_ids": ["c1"]},
        {"event_id": "e3", "call_id": "c3", "type": "tool_result", "tool_name": "run_command", "status": "success", "output_summary": "1 passed"},
        {"event_id": "e4", "type": "task_result", "status": "success"},
    ])
    unknown = _extract([
        {"event_id": "e1", "call_id": "c1", "type": "error", "message": "failed first"},
    ])

    assert recovered.outcome == "success"
    assert recovered.had_errors is True
    assert recovered.errors_recovered is True
    assert unknown.outcome == "unknown"
    assert unknown.had_errors is True


def test_failed_verification_after_early_success_result_wins() -> None:
    evidence = _extract([
        {"event_id": "e1", "type": "task_result", "status": "success"},
        {"event_id": "e2", "call_id": "c1", "type": "tool_call", "tool_name": "run_command", "command": "pytest -q"},
        {"event_id": "e3", "call_id": "c1", "type": "tool_result", "tool_name": "run_command", "status": "error", "output_summary": "2 tests failed"},
    ])

    assert evidence.outcome == "failed"


def test_deep_cycles_bad_string_values_and_secrets_are_bounded() -> None:
    class BadString:
        def __str__(self) -> str:
            raise RuntimeError("cannot stringify")

    cyclic: dict = {"password": "secret-value", "bad": BadString()}
    cyclic["self"] = cyclic
    trace = [
        {"type": "tool_call", "call_id": "c1", "tool_name": "inspect_payload", "input": cyclic},
        {
            "type": "error",
            "call_id": "c1",
            "tool_name": "inspect_payload",
            "message": (
                "token=secret-value OPENAI_API_KEY=environment-key "
                "SECRET_KEY=another-value " + "x" * 5_000
            ),
        },
    ]

    evidence = _extract(trace)
    serialized = str(evidence.to_dict())

    assert "secret-value" not in serialized
    assert "environment-key" not in serialized
    assert "another-value" not in serialized
    assert len(evidence.errors[0].message) <= 640
    assert "truncated" in evidence.errors[0].message


def test_reflection_engine_exposes_evidence_and_derives_legacy_context() -> None:
    trace = [
        {"event_id": "e1", "call_id": "read", "type": "tool_call", "tool_name": "read_file", "input": {"path": "src/a.py"}},
        {"event_id": "e2", "call_id": "edit", "type": "tool_result", "tool_name": "edit_file", "status": "success", "changed_files": ["src/b.py"]},
        {"event_id": "e3", "type": "task_result", "status": "success"},
    ]

    result = ReflectionEngine().reflect("Update files", trace)

    assert result.task_evidence is not None
    assert [item.path for item in result.task_evidence.files_read] == ["src/a.py"]
    assert [item.path for item in result.task_evidence.files_changed] == ["src/b.py"]
    assert result.task_context["files_read"] == ["src/a.py"]
    assert result.task_context["files_changed"] == ["src/b.py"]
    assert result.task_context["files"] == ["src/a.py", "src/b.py"]


def test_reflection_uses_deduplicated_errors_and_ignores_recovery_suggestions() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "tool_result", "tool_name": "run_command", "status": "error", "output_summary": "AssertionError: expired token accepted"},
        {"event_id": "e2", "call_id": "c1", "type": "error", "tool_name": "run_command", "error_type": "AssertionError", "message": "AssertionError: expired token accepted"},
        {"event_id": "e3", "call_id": "c1", "type": "recovery_suggestion", "suggestion": "Try a smaller command"},
        {"event_id": "e4", "type": "task_result", "status": "failed"},
    ]

    result = ReflectionEngine().reflect("Run auth tests", trace)

    assert len(result.errors_encountered) == 1
    assert result.task_evidence is not None
    assert result.task_evidence.recoveries == []
    assert len(result.task_evidence.recovery_suggestions) == 1
    assert not any("Recovery action" in lesson for lesson in result.lessons_learned)


def test_memory_entry_contains_bounded_redacted_task_evidence() -> None:
    trace = [
        {"event_id": "e1", "call_id": "c1", "type": "error", "tool_name": "run_command", "message": "Authorization: Bearer synthetic.secret.value token=raw-value"},
        {"event_id": "e2", "type": "task_result", "status": "failed"},
    ]

    result = ReflectionEngine().reflect("Inspect failure", trace)
    entry = result.to_memory_entry()
    serialized = str(entry)

    assert "synthetic.secret.value" not in serialized
    assert "raw-value" not in serialized
    assert entry["metadata"]["task_evidence"]["errors"][0]["source_event_ids"] == ("e1",)


def test_duplicate_event_and_conflicting_call_ids_are_diagnostic_not_fatal() -> None:
    evidence = _extract([
        {"event_id": "duplicate", "call_id": "c1", "type": "tool_call", "tool_name": "read_file"},
        {"event_id": "duplicate", "call_id": "c1", "type": "tool_result", "tool_name": "edit_file", "status": "success"},
    ])

    assert any("duplicate event_id" in item for item in evidence.diagnostics)
    assert any("conflicting tool names" in item for item in evidence.diagnostics)
    assert evidence.tool_calls[0].call_event_id == "duplicate"
    assert evidence.tool_calls[0].result_event_ids == ("legacy-event-000002",)


def test_thousand_event_trace_is_bounded_and_deterministic() -> None:
    trace = [
        {
            "event_id": f"event-{index:06d}",
            "call_id": "same-call",
            "type": "tool_result",
            "tool_name": "run_command",
            "status": "error",
            "output_summary": "TimeoutError: repeated timeout",
        }
        for index in range(1_000)
    ]

    first = _extract(trace)
    second = _extract(trace)

    assert first.to_dict() == second.to_dict()
    assert len(first.errors) == 1
    assert len(first.errors[0].source_event_ids) == 64
    assert any("truncated at 500 events" in item for item in first.diagnostics)


def test_plain_library_mentions_are_weak_and_negated_mentions_are_ignored() -> None:
    evidence = _extract([
        {"event_id": "e1", "type": "assistant_step", "content": "Django may be useful, but do not add gin and src/react.py is local."},
    ])

    assert [(item.name, item.status) for item in evidence.libraries] == [
        ("django", "weak_mention"),
    ]


def test_later_passed_verification_overrides_stale_failed_task_result() -> None:
    evidence = _extract([
        {"event_id": "e1", "type": "task_result", "status": "failed"},
        {"event_id": "e2", "call_id": "c1", "type": "tool_call", "tool_name": "run_command", "command": "pytest tests/test_a.py -q"},
        {"event_id": "e3", "call_id": "c1", "type": "tool_result", "tool_name": "run_command", "status": "success"},
    ])

    assert evidence.verification[0].result == "passed"
    assert evidence.outcome == "success"


def test_ungrounded_root_cause_statement_is_inferred() -> None:
    evidence = _extract([
        {"event_id": "e1", "type": "assistant_step", "content": "The cache race caused the failure."},
    ])

    assert evidence.decisions[0].epistemic_status == "inferred"


def test_memory_task_evidence_metadata_has_total_size_bound() -> None:
    trace = [
        {
            "event_id": f"event-{index:06d}",
            "call_id": f"call-{index:06d}",
            "type": "error",
            "tool_name": "run_command",
            "error_type": "ToolError",
            "message": f"failure {index} " + "x" * 2_000,
        }
        for index in range(64)
    ]

    metadata = ReflectionEngine().reflect("Large trace", trace).to_memory_entry()["metadata"]
    encoded = json.dumps(metadata["task_evidence"], ensure_ascii=False)

    assert len(encoded) <= 32_768
