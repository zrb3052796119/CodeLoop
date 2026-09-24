from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.run_multi_agent_cost_live import (
    _BoundedWorkflowApprovalProvider,
    _evaluate_cost_oracle,
    _validate_executable_case,
)
from scripts.run_north_star_live import TurnEvidence


def _turn(run_id: str = "run_" + "a" * 32) -> TurnEvidence:
    return TurnEvidence(
        run_id=run_id,
        response="",
        event_types=(),
        events=(),
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
    )


def _journal_with_subagents(rows: list[object]) -> SimpleNamespace:
    return SimpleNamespace(list_subagent_runs=lambda run_id: list(rows))


_DELEGATION_ORACLE = {
    "id": "delegation-rejected",
    "kind": "delegation_rejected",
    "errorCode": "central_scheduler_required",
}


def test_delegation_rejected_passes_on_sealed_refusal(tmp_path) -> None:
    assert (
        _evaluate_cost_oracle(
            _DELEGATION_ORACLE,
            workspace=tmp_path,
            turns=[_turn()],
            before_digest="",
            journal=_journal_with_subagents([]),
            observed_task_error_codes=("central_scheduler_required",),
        )
        is True
    )


def test_delegation_rejected_fails_when_delegation_was_not_refused(tmp_path) -> None:
    assert (
        _evaluate_cost_oracle(
            _DELEGATION_ORACLE,
            workspace=tmp_path,
            turns=[_turn()],
            before_digest="",
            journal=_journal_with_subagents([]),
            observed_task_error_codes=(),
        )
        is False
    )


def test_delegation_rejected_fails_on_error_code_mismatch(tmp_path) -> None:
    assert (
        _evaluate_cost_oracle(
            _DELEGATION_ORACLE,
            workspace=tmp_path,
            turns=[_turn()],
            before_digest="",
            journal=_journal_with_subagents([]),
            observed_task_error_codes=("workflow_request_required",),
        )
        is False
    )


def test_delegation_rejected_fails_when_a_subagent_started(tmp_path) -> None:
    subagent_row = SimpleNamespace(agent_type="general")
    assert (
        _evaluate_cost_oracle(
            _DELEGATION_ORACLE,
            workspace=tmp_path,
            turns=[_turn()],
            before_digest="",
            journal=_journal_with_subagents([subagent_row]),
            observed_task_error_codes=("central_scheduler_required",),
        )
        is False
    )


def test_delegation_rejected_rejects_invalid_oracle_contract(tmp_path) -> None:
    with pytest.raises(ValueError):
        _evaluate_cost_oracle(
            {"id": "delegation-rejected", "kind": "delegation_rejected"},
            workspace=tmp_path,
            turns=[_turn()],
            before_digest="",
            journal=_journal_with_subagents([]),
            observed_task_error_codes=("central_scheduler_required",),
        )


def _executable_case(oracles: list[dict]) -> dict:
    return {
        "id": "mac-rejection-case",
        "mutability": "read_only",
        "turns": [{"prompt": "delegate one bounded task"}],
        "files": {},
        "oracleIds": [oracle["id"] for oracle in oracles],
        "oracles": oracles,
        "authorizedPaths": [],
    }


def test_validate_executable_case_accepts_delegation_rejected() -> None:
    case = _validate_executable_case(
        _executable_case([dict(_DELEGATION_ORACLE)]), suite="cost"
    )
    assert case["oracles"][0]["kind"] == "delegation_rejected"


@pytest.mark.parametrize("error_code", ["", "Central_Scheduler", 42, None])
def test_validate_executable_case_rejects_bad_error_code(error_code) -> None:
    oracle = dict(_DELEGATION_ORACLE, errorCode=error_code)
    with pytest.raises(ValueError):
        _validate_executable_case(_executable_case([oracle]), suite="cost")


def test_capture_task_result_records_bounded_error_code() -> None:
    provider = _BoundedWorkflowApprovalProvider(())
    provider.capture_task_result(
        SimpleNamespace(
            ok=False,
            output=(
                "error[central_scheduler_required]: direct state-changing "
                "sub-agents are disabled"
            ),
        )
    )
    assert provider.task_error_codes == ["central_scheduler_required"]


def test_capture_task_result_ignores_non_error_results() -> None:
    provider = _BoundedWorkflowApprovalProvider(())
    provider.capture_task_result(
        SimpleNamespace(ok=True, output="error[not_an_error]: ok results pass")
    )
    provider.capture_task_result(
        SimpleNamespace(ok=False, output="plain failure prose without a code")
    )
    provider.capture_task_result(SimpleNamespace(ok=False, output=None))
    provider.capture_task_result(SimpleNamespace(output="error[missing_ok]: x"))
    assert provider.task_error_codes == []
