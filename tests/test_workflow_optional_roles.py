"""Bounded tasks skip redundant analysis but retain semantic completion review."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.central_scheduler import CentralScheduler, DispatchStage, TaskRisk
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.multi_agent_contracts import ContractValidationError, parse_workflow_request
from minicode.permissions import PermissionManager
from minicode.scheduler_audit import project_scheduler_audit
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolResult
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module

from test_workflow_v2_structured_routing import (
    _request,
    _run,
    _worker_role,
    _write_analyst_result,
    _write_executor_declaration,
    _write_reviewer_result,
)


@pytest.mark.parametrize("complexity", [False, True])
def test_declared_coverage_keeps_direct_executor_and_both_completion_gates(
    complexity: bool,
) -> None:
    request = parse_workflow_request(
        _request(risk="low", complex=complexity), legacy_task_id="task_optional"
    )

    assert request.task_profile.complex is complexity
    assert request.task_profile.acceptance_executable is True
    assert CentralScheduler().plan(request.task_profile).stages == (
        DispatchStage.EXECUTOR, DispatchStage.VERIFIER, DispatchStage.REVIEWER,
    )


@pytest.mark.parametrize("case", ["medium", "high", "sensitive", "unknown", "uncovered", "broad"])
def test_complexity_change_does_not_remove_risk_or_acceptance_gates(case: str) -> None:
    raw = _request(risk="low", complex=True)
    if case in {"medium", "high"}:
        raw["profile"]["risk"] = case
    elif case == "sensitive":
        raw["scope"]["writePaths"] = ["auth.py"]
    elif case == "unknown":
        raw["scope"]["writePaths"] = []
    elif case == "uncovered":
        raw["criteria"].append({"id": "ac_unchecked", "kind": "behavior", "text": "Another requirement."})
    elif case == "broad":
        raw["scope"]["writePaths"] = ["a.py", "b.py", "c.py"]
    if case in {"unknown", "uncovered"}:
        expected = "bounded write paths" if case == "unknown" else "executable coverage"
        with pytest.raises(ContractValidationError, match=expected):
            parse_workflow_request(raw, legacy_task_id="task_optional")
        return
    profile = parse_workflow_request(raw, legacy_task_id="task_optional").task_profile
    plan = CentralScheduler().plan(profile)

    assert plan.stages[0] is DispatchStage.ANALYST
    assert DispatchStage.VERIFIER in plan.stages
    assert DispatchStage.REVIEWER in plan.stages
    if profile.risk is TaskRisk.HIGH:
        assert plan.requires_human
        assert plan.stages.index(DispatchStage.HUMAN) < plan.stages.index(DispatchStage.EXECUTOR)


@pytest.mark.parametrize("accepted", [True, False])
def test_complex_scoped_workflow_still_earns_real_verification_and_sealed_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_accepted():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n",
        encoding="utf-8",
    )
    roles: list[str] = []
    commands: list[tuple[str, ...]] = []
    audits = []
    seals = []

    class Sink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

        def record_scheduler_commit_ready(self, ready):
            seals.append(ready)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after" if accepted else "incorrect", encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="Worker claims completion.")

    def approve_command(_self, command, args, _cwd):
        assert command == "pytest" and args == ["-q"]

    def command_runner(argv, **kwargs):
        commands.append(tuple(argv))
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    verifier = DeterministicVerifier(command_runner=command_runner)
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(PermissionManager, "ensure_command", approve_command)
    monkeypatch.setattr(
        runner_module, "_deterministic_verifier_for_context",
        lambda *_args, **_kwargs: verifier,
    )

    result = _run(
        tmp_path, _request(risk="low", complex=True), monkeypatch, event_sink=Sink(),
    )

    assert result.ok is accepted, result.output
    assert commands, "The final host verification must execute an actual test."
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == (
        "after" if accepted else "before"
    )
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["committed"] is accepted
    assert trace["ruleId"] == "route.scoped_change"
    assert roles and set(roles) == ({"executor", "reviewer"} if accepted else {"executor"})
    assert bool(seals) is accepted
    assert len(audits) == 1
    replay = project_scheduler_audit(
        profile=audits[0].profile, plan=audits[0].plan,
        trace=audits[0].trace, artifacts=audits[0].artifacts, contract_mode="v2",
    )
    assert replay.trace == audits[0].trace
    if accepted:
        # A syntactically linked trace cannot erase the v4 completion gate
        # while retaining commit authority. The old sequence is nevertheless
        # valid under its recorded v3 policy, which proves this rejection is
        # about the missing mandatory role rather than broken link syntax.
        stripped = deepcopy(trace)
        stripped["transitions"] = [item for item in stripped["transitions"]
                                   if item["stage"] != "reviewer"]
        for index, transition in enumerate(stripped["transitions"], start=1):
            transition["sequence"] = index
            transition["parentDecisionId"] = (
                stripped["transitions"][index - 2]["decisionId"] if index > 1 else None
            )
        render = lambda value: "=== SCHEDULER TRACE v1 ===\n" + json.dumps(value)
        assert extract_scheduler_trace(render(stripped)) is None
        stripped["policyVersion"] = 3
        assert extract_scheduler_trace(render(stripped)) is not None
