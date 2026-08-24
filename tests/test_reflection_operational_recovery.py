"""Regression coverage for command/tool recovery lessons.

These traces mirror a real review run: no project file changed, but the agent
corrected how it invoked a verifier and obtained a concrete passing result.
That is reusable knowledge, unlike a bare retry or an unrelated later success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import minicode.memory as memory_mod
from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_synthesis import (
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _attempt(
    index: int,
    call_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    ok: bool,
    output: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "event_id": f"e{index}",
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool_name,
            "input": tool_input,
        },
        {
            "event_id": f"e{index + 1}",
            "type": "tool_result",
            "call_id": call_id,
            "tool_name": tool_name,
            "status": "success" if ok else "error",
            "is_error": not ok,
            "output_summary": output,
        },
    ]
    if not ok:
        events.append(
            {
                "event_id": f"e{index + 2}",
                "type": "error",
                "call_id": call_id,
                "tool_name": tool_name,
                "error_type": "ToolError",
                "message": output,
            }
        )
    return events


def _done(index: int) -> dict[str, Any]:
    return {
        "event_id": f"e{index}",
        "type": "task_result",
        "status": "success",
        "had_errors": True,
        "errors_recovered": True,
        "tool_error_count": 1,
    }


def _evaluate(trace: list[dict[str, Any]]):
    task = "Review and verify the project"
    evidence = TraceEvidenceExtractor().extract(task, trace)
    candidate = RuleReflectionSynthesizer().synthesize(task, evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return evidence, candidate, decision


def test_changed_shell_invocation_yields_an_actionable_lint_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "ruff-failed",
            "run_command",
            {
                "command": "ruff",
                "args": ["check", "src/rag_workbench", "2>&1", "|", "tail", "-40"],
                "cwd": "/workspace/backend",
            },
            ok=False,
            output="error: unexpected argument '-4' found",
        ),
        *_attempt(
            20,
            "ruff-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "cd /workspace/backend && .venv-canonical/bin/ruff check "
                    "src/rag_workbench 2>&1 | tail -40",
                ],
            },
            ok=True,
            output="All checks passed!",
        ),
        _done(30),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    recovery = evidence.recoveries[0]
    assert recovery.files_changed == ()
    assert recovery.epistemic_status == "confirmed"
    assert recovery.related_error_ids == ("error-000001",)
    assert "bash" in recovery.action
    assert ".venv-canonical/bin/ruff" in recovery.action
    assert "src/rag_workbench" in recovery.action
    assert recovery.action.startswith("Verified recovery: use the corrected lint invocation")
    assert "do not reuse the failed invocation" in recovery.action
    assert evidence.errors_recovered is True
    assert any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert decision.accepted is True
    assert "verified_solution" in decision.durable_signals


def test_command_argument_error_still_yields_a_corrected_invocation_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "pytest-invalid-argument",
            "run_command",
            {
                "command": "pytest",
                "args": ["tests/test_auth.py", "--bad-option"],
            },
            ok=False,
            output="UsageError: unrecognized arguments: --bad-option",
        ),
        *_attempt(
            10,
            "pytest-passed",
            "run_command",
            {
                "command": "python",
                "args": ["-m", "pytest", "tests/test_auth.py", "-q"],
            },
            ok=True,
            output="3 passed in 0.08s",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].epistemic_status == "confirmed"
    assert "python -m pytest tests/test_auth.py -q" in evidence.recoveries[0].action
    assert any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" in decision.durable_signals


def test_transient_command_failure_is_not_laundered_by_a_changed_invocation() -> None:
    trace = [
        *_attempt(
            1,
            "pytest-service-unavailable",
            "run_command",
            {"command": "pytest", "args": ["tests/test_auth.py", "--maxfail=1"]},
            ok=False,
            output="HTTP 503 Service Unavailable while loading a test dependency",
        ),
        *_attempt(
            10,
            "pytest-passed",
            "run_command",
            {
                "command": "python",
                "args": ["-m", "pytest", "tests/test_auth.py", "-q"],
            },
            ok=True,
            output="3 passed in 0.08s",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_switching_from_test_runner_to_project_python_is_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "runner-failed",
            "test_runner",
            {"path": "/workspace/backend/tests/unit/test_hybrid_fusion.py", "timeout": 120},
            ok=False,
            output="Passed: 0\nFailed: 0\nErrors: 0",
        ),
        *_attempt(
            10,
            "pytest-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "cd /workspace/backend && .venv-canonical/bin/python -m pytest "
                    "tests/unit/test_hybrid_fusion.py -q",
                ],
            },
            ok=True,
            output="9 passed in 0.09s",
        ),
        _done(20),
    ]

    evidence, _, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    recovery = evidence.recoveries[0]
    assert recovery.related_error_ids == ("error-000001",)
    assert "test_runner" in recovery.action
    assert ".venv-canonical/bin/python -m pytest" in recovery.action
    assert decision.accepted is True


def test_changed_but_unrelated_success_is_not_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "tests-failed",
            "run_command",
            {"command": "pytest", "args": ["tests/test_auth.py", "-q"]},
            ok=False,
            output="1 failed",
        ),
        *_attempt(
            10,
            "lint-passed",
            "run_command",
            {"command": "ruff", "args": ["check", "src"]},
            ok=True,
            output="All checks passed!",
        ),
        _done(20),
    ]

    evidence, _, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert "verified_solution" not in decision.durable_signals


def test_same_kind_on_a_different_file_is_not_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "auth-failed",
            "run_command",
            {"command": "pytest", "args": ["tests/test_auth.py", "-q"]},
            ok=False,
            output="1 failed",
        ),
        *_attempt(
            10,
            "billing-passed",
            "run_command",
            {"command": "python", "args": ["-m", "pytest", "tests/test_billing.py", "-q"]},
            ok=True,
            output="3 passed",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_shell_masked_no_tests_run_is_not_passing_verification_or_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "runner-failed",
            "test_runner",
            {"path": "/workspace/backend/tests/unit/test_hybrid_fusion.py"},
            ok=False,
            output="Passed: 0\nFailed: 0\nErrors: 0",
        ),
        *_attempt(
            10,
            "masked-failure",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "python -m pytest tests/unit/test_hybrid_fusion.py | tail -20",
                ],
            },
            ok=True,
            output="ERROR: file or directory not found: tests/unit/test_hybrid_fusion.py\n"
            "no tests ran in 0.00s",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    masked = next(item for item in evidence.verification if item.call_id == "masked-failure")
    assert masked.result == "failed"
    assert evidence.recoveries == []


def test_operational_recovery_redacts_secrets_from_the_method() -> None:
    trace = [
        *_attempt(
            1,
            "ruff-failed",
            "run_command",
            {
                "command": "ruff",
                "args": ["check", "src/rag_workbench", "TOKEN=synthetic-secret"],
            },
            ok=False,
            output="invalid argument",
        ),
        *_attempt(
            10,
            "ruff-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "TOKEN=another-secret .venv/bin/ruff check src/rag_workbench",
                ],
            },
            ok=True,
            output="All checks passed!",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    serialized = str(evidence.recoveries[0])
    assert "synthetic-secret" not in serialized
    assert "another-secret" not in serialized
    assert "[REDACTED]" in serialized


def test_real_review_shapes_produce_two_persistable_recovery_methods() -> None:
    """Replay the two recovery chains observed in session db311802a29a."""
    trace = [
        *_attempt(
            1,
            "ruff-failed",
            "run_command",
            {
                "command": "ruff",
                "args": ["check", "src/rag_workbench", "2>&1", "|", "tail", "-40"],
                "cwd": "/workspace/backend",
            },
            ok=False,
            output="error: unexpected argument '-4' found",
        ),
        *_attempt(
            20,
            "ruff-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "cd /workspace/backend && .venv-canonical/bin/ruff check "
                    "src/rag_workbench 2>&1 | tail -40",
                ],
            },
            ok=True,
            output="All checks passed!",
        ),
        *_attempt(
            30,
            "runner-failed",
            "test_runner",
            {"path": "/workspace/backend/tests/unit/test_hybrid_fusion.py"},
            ok=False,
            output="Passed: 0\nFailed: 0\nErrors: 0",
        ),
        *_attempt(
            40,
            "pytest-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    "cd /workspace/backend && .venv-canonical/bin/python -m pytest "
                    "tests/unit/test_hybrid_fusion.py -q",
                ],
            },
            ok=True,
            output="9 passed in 0.09s",
        ),
        _done(50),
    ]

    result = ReflectionEngine().reflect("Review and verify the project", trace)

    recovery_claims = [
        claim for claim in result.structured_claims if claim.claim_type == "recovery"
    ]
    assert len(recovery_claims) == 2
    assert result.value_decision.accepted is True
    assert set(result.final_persistable_claim_ids) >= {
        claim.claim_id for claim in recovery_claims
    }
    content = result.to_memory_entry()["content"]
    assert ".venv-canonical/bin/ruff" in content
    assert ".venv-canonical/bin/python -m pytest" in content


def test_verified_operational_recovery_is_approved_and_retrievable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The method must reach live Memory, not stop at reflection metadata."""
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    trace = [
        *_attempt(
            1,
            "ruff-failed",
            "run_command",
            {
                "command": "ruff",
                "args": ["check", "src/rag_workbench", "2>&1", "|", "tail", "-40"],
            },
            ok=False,
            output="error: unexpected argument '-4' found",
        ),
        *_attempt(
            10,
            "ruff-passed",
            "run_command",
            {
                "command": "bash",
                "args": [
                    "-lc",
                    ".venv-canonical/bin/ruff check src/rag_workbench 2>&1 | tail -40",
                ],
            },
            ok=True,
            output="All checks passed!",
        ),
        _done(20),
    ]

    entry_id = pipeline.write("Review and verify the project", trace)

    assert entry_id is not None
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.approval_status == "approved"
    assert entry.is_active is True
    assert ".venv-canonical/bin/ruff" in entry.content
    matches = manager.search("ruff invocation pipe tail", min_relevance=0.0)
    assert any(item.id == entry_id for item in matches)
