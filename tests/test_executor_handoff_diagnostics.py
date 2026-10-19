"""Replay real blocked handoffs and deliver host-observed failure evidence."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_executor_result_submission import _bound, _execute


@pytest.mark.parametrize("case", json.loads(
    (Path(__file__).parent / "fixtures/executor_submit_v14.json").read_text()
), ids=lambda case: f"v14-request-{case['sourceCall']}")
def test_real_blocked_handoff_survives_diagnostic_formatting(case):
    assignment, order, store, channel, tool = _bound()
    result = _execute(tool, channel, case["input"])
    assert result.ok, result.output
    assert result._implementation_submitted
    document = json.loads(store.read(channel.write_key).content)
    assert document["status"] == "blocked"
    assert document["selfChecks"] == []
    assert "changeSet" not in document
    assert [(s["stepId"], s["status"]) for s in document["stepResults"]] == [
        (order.steps[s["step"] - 1].step_id, s["status"])
        for s in case["input"]["steps"]
    ]
    if case["sourceCall"] == 18:
        assert len(document["deviations"]) == 1
        assert document["deviations"][0]["impact"] == "medium"
        assert all(item["reason"] in document["deviations"][0]["reason"]
                   for item in case["input"]["deviations"])
    else:
        assert len(document["blockers"][0]) <= 500
        assert "truncated" in document["blockers"][0]


def test_blocked_diagnostic_merge_does_not_change_semantics_or_input():
    _assignment, _order, store, channel, tool = _bound()
    value = {
        "status": "blocked", "steps": [{"step": 1, "status": "failed"}, {"step": 2, "status": "skipped"}],
        "deviations": [{"step": 1, "reason": "unfinished source " * 200, "impact": "low"},
                       {"step": 1, "reason": "unverified result " * 200, "impact": "high"}],
        "blockers": ["Cannot deliver safely."],
    }
    original = json.dumps(value)
    result = _execute(tool, channel, value)
    assert result.ok, result.output
    assert json.dumps(value) == original
    document = json.loads(store.read(channel.write_key).content)
    assert document["status"] == "blocked"
    assert document["deviations"][0]["impact"] == "high"
    assert len(document["deviations"][0]["reason"]) <= 4000
    assert "truncated" in document["deviations"][0]["reason"]
    schema = tool.input_schema["properties"]
    assert schema["blockers"]["items"]["maxLength"] == 500
    assert schema["deviations"]["items"]["properties"]["reason"]["maxLength"] == 4000


@pytest.mark.parametrize("change", [
    {"steps": [{"step": 1, "status": "failed"}, {"step": 1, "status": "failed"}]},
    {"deviations": [{"step": 3, "reason": "outside order", "impact": "high"}]},
    {"deviations": [{"step": 1, "reason": "unfinished", "impact": "critical"}]},
    {"blockers": ["secret\x00invalid"]},
    {"blockers": []},
    {"blockers": ["too large " * 2000]},
    {"status": "completed"},
])
def test_diagnostic_normalization_never_repairs_invalid_semantics(change):
    _assignment, _order, store, channel, tool = _bound()
    value = {"status": "blocked", "steps": [{"step": 1, "status": "failed"}, {"step": 2, "status": "skipped"}],
             "deviations": [], "blockers": ["unfinished"]}
    value.update(change)
    result = _execute(tool, channel, value)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None


def test_parent_observations_are_bounded_complete_json_and_redacted():
    from minicode.workflow_runner import _executor_observation_text
    _assignment, _order, _store, channel, _tool = _bound()
    channel.record_validation_error(ValueError("api_key=synthetic-private-value " + "context " * 300))
    assert "synthetic-private-value" not in str(channel.validation_errors)
    state = SimpleNamespace(
        implementation_check_runs=40,
        implementation_check_statuses={"passed": 39, "failed": 1},
        implementation_check_latest={str(i): {"checkId": str(i), "status": "failed", "stdoutPreview": "\x01" * 500}
                                     for i in range(40)},
        executor_submission_errors=[{"error": message} for message in channel.validation_errors],
        verification_result=None,
    )
    block = _executor_observation_text(state)
    payload = block.splitlines()[1]
    assert len(payload) <= 3000
    document = json.loads(payload)
    assert document["omissionCountsScope"] == "retained_recent_windows"
    assert document["retentionLimits"] == {"latestChecks": 40, "submissionErrors": 4}
    assert document["implementationChecks"]["runs"] == 40
    assert document["implementationChecks"]["statuses"] == {"passed": 39, "failed": 1}
    assert document["implementationChecks"]["latestOmitted"] > 0


def test_real_failed_workflow_returns_host_checks_and_submission_error(tmp_path, monkeypatch):
    from minicode import subagent_model_routing
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.permissions import PermissionManager
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.write_file import write_file_tool
    from minicode.types import AgentStep, ModelAdapter
    import minicode.workflow_runner as runner
    from test_workflow_v2_structured_routing import _request, _run

    (tmp_path / "allowed.txt").write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool, write_file_tool]))
    commands = []

    def command_runner(_argv, **kwargs):
        commands.append(kwargs["cwd"])
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    class Model(ModelAdapter):
        calls = 0

        def next(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "candidate", "toolName": "edit_file",
                    "input": {"path": "allowed.txt", "old": "before", "new": "almost"},
                }])
            if self.calls == 2:
                assert "1 failed" in str(messages)
                return AgentStep(type="tool_calls", calls=[{
                    "id": "invalid", "toolName": "executor_submit", "input": {
                        "status": "approved", "steps": [{"step": 1, "status": "completed"}],
                        "deviations": [], "blockers": [],
                    },
                }])
            assert self.calls == 3
            return AgentStep(type="assistant", kind="final", content="No checks were executed; everything passed.")

    model = Model()
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: model)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, commit_audit_waived=True)
    assert not result.ok
    assert (tmp_path / "allowed.txt").read_text() == "before"
    assert model.calls == 3
    assert len(commands) == 1, "only the real implementation check ran; formal Verifier did not"
    marker = "=== EXECUTOR OBSERVATIONS v1 ===\n"
    assert marker in result.output, result.output
    observation = json.loads(result.output.split(marker, 1)[1].splitlines()[0])
    assert observation["formalVerificationStatus"] == "not_run"
    assert observation["implementationChecks"]["runs"] == 1
    check = observation["implementationChecks"]["latest"][0]
    assert check["status"] == "failed" and check["exitCode"] == 1
    assert check["argv"] == ["pytest", "-q"]
    assert "1 failed" in check["stdoutPreview"]
    assert "ExecutionResult status is invalid" in str(observation["submissionErrors"])
    assert "No checks were executed" not in json.dumps(observation)
    assert "=== SCHEDULER TRACE v1 ===" in result.output
    assert "=== SUBAGENT RESULT v1 ===" in result.output
    from minicode.subagent_result import extract_subagent_result
    assert extract_subagent_result(result.output)["outcome"] == "failed"
