"""V18's last-edit boundary through the real delivery stack.

Only model decisions and provider/network bootstrap are scripted. File edits,
phase admission, the shared ledger, isolated pytest checks, native submission,
the formal Verifier, durable audit and parent promotion are production code.
The prefix is a small executable analogue of the recorded investigation; its
two unknown-usage requests are allocated the observed aggregate charge. This
is a deterministic execution regression, not a model-quality result. Its three
historical repair cases explicitly retain policy 3; the separate policy 5 bounded
case preserves the mandatory-review floor and the exact spent prefix.
The frozen prefix also retains its pre-V24 request view, so its historical
provider charges are not calibrated against a newly compressed request body.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from minicode import subagent_model_routing
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import _estimated_context_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelAdapter, ModelUsage
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module

from test_workflow_v2_structured_routing import _request


BOUNDARY = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "v18-edit-failure-boundary.json").read_text(encoding="utf-8"))
SOURCE_CONTEXT = (Path(__file__).resolve().parents[1] /
    "artifacts/implementation-progress-2026-10-15/edit-failure-recovery/v18-source-context.txt"
).read_text(encoding="utf-8")
PREFIX_USAGE = [
    (15020, 276), (17914, 287), (20946, 793), (20834, 31),
    # The two missing provider usages are not zero. Only their aggregate is
    # established by the journal checkpoint; distributing it here does not
    # assert provider measurements for either original failed request.
    (23474, 0), (21346, 1013), (22870, 357), (24128, 215),
    (25244, 293), (26438, 464), (27802, 803), (26754, 1218),
    (29972, 871), (28610, 0), (31986, 868), (35061, 161),
]
HEADER = (
    "from __future__ import annotations\n\n"
    "def outcome():\n"
    "    # implementation marker: original\n"
    "    message_model_turns = 1\n"
    "    reminder_model_turns = 2\n"
    "    model_turns = message_model_turns\n"
    "    return model_turns\n\n"
    "def other_outcome():\n"
    "    message_model_turns = 9\n"
    "    model_turns = message_model_turns\n"
    "    return model_turns\n"
)
AMBIGUOUS = "    model_turns = message_model_turns"
UNIQUE = "    reminder_model_turns = 2\n    model_turns = message_model_turns"
REPAIRED = "    reminder_model_turns = 2\n    model_turns = message_model_turns + reminder_model_turns"


def _exercise(tmp_path, monkeypatch, *, candidate_stays_bad=False, cancel_repair=False,
              historical_policy=True):
    from minicode.central_scheduler import CentralScheduler
    current_plan = CentralScheduler.plan
    replay_policy = 3 if historical_policy else 5

    def historical_plan(self, profile, *, policy_version=replay_policy):
        return current_plan(self, profile, policy_version=policy_version)

    monkeypatch.setattr(CentralScheduler, "plan", historical_plan)
    if not historical_policy:
        # This negative is the recorded policy-5 bounded handoff, not the
        # current policy-6 continuous execution default.
        import minicode.workflow_review_policy as review_policy
        current_decision = review_policy.review_routing_decision
        def historical_decision(**kwargs):
            return current_decision(**{**kwargs, "policy_version": 5})
        monkeypatch.setattr(review_policy, "review_routing_decision", historical_decision)
        actual_turn = task_module.run_agent_turn
        def bounded_turn(**kwargs):
            kwargs["bounded_implementation_phases"] = True
            return actual_turn(**kwargs)
        monkeypatch.setattr(task_module, "run_agent_turn", bounded_turn)
    # Real source reads leave a large, compressible history. They are not fake
    # messages injected into a controller, and the latest edit error remains
    # small enough to preserve in a bounded repair view.
    initial = HEADER + "\n\n" + SOURCE_CONTEXT
    target = tmp_path / "candidate.py"
    target.write_text(initial, encoding="utf-8")
    (tmp_path / "test_acceptance.py").write_text(
        "from candidate import outcome, other_outcome\n"
        "def test_reminder_is_counted_once():\n    assert outcome() == 3\n"
        "def test_other_counter_is_unchanged():\n    assert other_outcome() == 9\n",
        encoding="utf-8",
    )
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    cancellation = TurnCancellationToken("turn_" + "a" * 32)
    for input_tokens, output_tokens in [(5363, 1111), (6553, 660)]:
        reservation = budget.reserve_model_call(input_tokens)
        budget.record_model_call(input_tokens=input_tokens, output_tokens=output_tokens,
                                 reservation=reservation)
    assert budget.snapshot().used_total_tokens == 13687
    calls, commands, model_instances = [], [], []
    # V18's fixed provider charges belong to the historical unprojected
    # prefix. Pairing them with V24's smaller view invents provider overhead.
    # Freeze that policy-3 prefix only; the repair and policy-5 negative case
    # use the current real projection. New early projection also has separate
    # real-edit integration coverage.
    import minicode.implementation_recovery as recovery
    from minicode.turn_completion import TurnCompletionPolicy

    canonical_estimates = {}
    progress_projection = recovery.project_implementation_progress_messages
    observed_paths = TurnCompletionPolicy.observed_changed_paths

    def historical_prefix_paths(observer):
        if historical_policy and len(calls) < len(PREFIX_USAGE):
            return frozenset()
        return observed_paths.fget(observer)

    monkeypatch.setattr(TurnCompletionPolicy, "observed_changed_paths", property(historical_prefix_paths))

    def observed_projection(messages, *, changed_paths):
        index = len(calls)
        canonical_estimates[index] = max(
            canonical_estimates.get(index, 0), _estimated_context_tokens(messages),
        )
        return progress_projection(messages, changed_paths=changed_paths)

    monkeypatch.setattr(recovery, "project_implementation_progress_messages", observed_projection)

    def command_runner(_argv, **kwargs):
        before = target.read_text(encoding="utf-8")
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)
        commands.append({"cwd": kwargs["cwd"], "exit_code": completed.returncode,
                         "parent": before, "stdout": completed.stdout})
        return completed

    class ScriptedModel(ModelAdapter):
        def next(self, messages, tools_override=None, **kwargs):
            index = len(calls)
            names = {tool.name for tool in tools_override.list()}
            snapshot = budget.snapshot()
            calls.append({"messages": deepcopy(messages), "tools": names,
                          "budget": snapshot, "estimate": _estimated_context_tokens(messages),
                          "canonical_estimate": canonical_estimates.get(index, _estimated_context_tokens(messages))})
            if index < 16:
                used_input, used_output = PREFIX_USAGE[index]
                usage = ModelUsage(input_tokens=used_input, output_tokens=used_output,
                                   source="estimated" if index in {4, 13} else "provider")
            else:
                # Pair the last historical provider input with the actual
                # view sent for that request, as production calibration does.
                # A separate historical view's 26572 estimate cannot impose
                # additional overhead on this executable analogue. All 16
                # prefix charges above remain exact; continuation is estimated.
                residual = max(0, 35061 - calls[15]["estimate"])
                calibrated = _estimated_context_tokens(messages) + residual
                usage = ModelUsage(input_tokens=calibrated, output_tokens=2048,
                                   source="estimated")
            if index < 11:
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": f"read-{index}", "toolName": "read_file", "input": {
                        "path": "candidate.py", "offset": index * 7000, "limit": 8000,
                    },
                }])
            if index < 15:
                old = "original" if index == 11 else f"draft-{index - 1}"
                # A broad exact anchor carries the original historical code,
                # as the real failed run's old/new edit payloads did. The
                # semantic change remains the marker used by this small
                # boundary regression; no invented padding fills the prompt.
                prior = (HEADER + "\n\n" + SOURCE_CONTEXT[:6824]).replace(
                    "implementation marker: original", f"implementation marker: {old}")
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": f"draft-{index}", "toolName": "edit_file", "input": {
                        "path": "candidate.py", "old": prior,
                        "new": prior.replace(f"implementation marker: {old}",
                                             f"implementation marker: draft-{index}"),
                    },
                }])
            if index == 15:
                # Reservation counts this call already. The remaining-token
                # assertion is independent of its input reservation size.
                assert snapshot.used_total_tokens == BOUNDARY["productionBudget"]["beforeCall18"]["usedTotalTokens"]
                assert snapshot.used_model_calls == 18
                # The historical context still exists intact; its old length
                # floor cannot constrain a smaller production request view.
                # PREFIX_USAGE keeps the original spent-budget checkpoint,
                # and later calls still pay the calibrated current view.
                assert calls[-1]["canonical_estimate"] >= BOUNDARY["calls"][1]["estimatedInputTokens"] - 128
                assert calls[-1]["estimate"] <= calls[-1]["canonical_estimate"]
                if not historical_policy:
                    assert names == {"edit_file", "executor_submit"}
                    return AgentStep(type="tool_calls", usage=usage, calls=[{
                        "id": "v4-unaffordable-prefix", "toolName": "executor_submit", "input": {
                            "status": "blocked", "steps": [{"step": 1, "status": "failed"}],
                            "deviations": [], "blockers": ["This historical fixture does not propose a combined checked repair."],
                        },
                    }])
                assert "edit_file" in names
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": "v18-ambiguous-edit", "toolName": "edit_file", "input": {
                        "path": "candidate.py", "old": AMBIGUOUS,
                        "new": "    model_turns = message_model_turns + reminder_model_turns",
                    },
                }])
            if index == 16:
                assert snapshot.used_total_tokens == BOUNDARY["productionBudget"]["beforeCall19"]["usedTotalTokens"]
                assert snapshot.used_model_calls == 19
                assert "edit_file" in names, (
                    "The last ambiguous edit consumed the optional round; the same Executor "
                    "must receive an admitted repair view instead of submit-only tools"
                )
                errors = [m for m in messages if m.get("role") == "tool_result"
                          and m.get("toolUseId") == "v18-ambiguous-edit"]
                assert errors and "Found 2 matches" in errors[-1]["content"]
                assert calls[-1]["estimate"] < calls[-2]["estimate"], (
                    "A full-context repair and handoff cannot fit V18's 60,812 remaining tokens"
                )
                if cancel_repair:
                    cancellation.request()
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": "unique-anchor-repair", "toolName": "edit_file", "input": {
                        "path": "candidate.py", "old": UNIQUE,
                        "new": REPAIRED if not candidate_stays_bad else REPAIRED.replace("+ reminder_model_turns", "+ 0"),
                    },
                }])
            if index == 17:
                results = [m for m in messages if m.get("role") == "tool_result"
                           and m.get("toolUseId") == "unique-anchor-repair"]
                assert results, "The final declaration needs the actual repair/check result"
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": "submit-repaired-candidate", "toolName": "executor_submit", "input": {
                        "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                        "deviations": [], "blockers": [],
                    },
                }])
            if candidate_stays_bad and index == 18:
                assert len(model_instances) == 1, "The repair must reuse the original Executor adapter"
                assert commands and all(command["exit_code"] == 1 for command in commands)
                return AgentStep(type="tool_calls", usage=usage, calls=[{
                    "id": "submit-still-failing-candidate", "toolName": "executor_submit", "input": {
                        "status": "blocked", "steps": [{"step": 1, "status": "failed"}],
                        "deviations": [], "blockers": ["The actual public checks still fail."],
                    },
                }])
            raise AssertionError("The replay must not start a replacement Executor or workflow")

    def model_factory(*_a, **_k):
        model = ScriptedModel()
        model_instances.append(model)
        return model

    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", model_factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([read_file_tool, edit_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    request = _request(risk="low", complex=False)
    request["scope"]["writePaths"] = ["candidate.py"]
    request["criteria"][0]["text"] = "Count the reminder once while retaining the other counter."
    data_dir = tmp_path.parent / f"{tmp_path.name}-journal"
    with observe_run(workspace=tmp_path, source="headless", title="V18 last-edit replay",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run(task_module.task_tool.validator({
            "description": "V18 last-edit delivery regression",
            "prompt": ("Fix candidate.py and verify the reminder counter."
                + (" Independently review the resulting change." if not historical_policy else "")),
            "agent_type": "workflow", "workflow_request": request,
        }), ToolContext(cwd=str(tmp_path), _runtime={"model": "fake", "maxOutputTokens": 8192},
            _event_sink=observation, _central_workflow_v2_enforced=True,
            _subagent_mailbox=SubagentMailbox(), _agent_budget=budget,
            _cancellation_token=cancellation,
            _logical_agent_preserve_total_tokens=BOUNDARY["productionBudget"]["parentPreserveTokens"],
            _task_deadline_monotonic=time.monotonic() + 600))
    return result, calls, commands, model_instances, budget, target, initial, data_dir


def _durable_recovery_evidence(data_dir, calls=None):
    projection_types = {
        "execution.repair_context_projected", "execution.implementation_context_projected",
    }
    executor_runs = []
    for path in data_dir.rglob("subagent-runs/*.jsonl"):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        sessions = [row for row in rows if row.get("type") == "execution.executor_session"]
        run_projections = [row for row in rows if row.get("type") in projection_types]
        if not sessions:
            assert not run_projections, "A projection must belong to an Executor Assignment"
            continue
        assert len(sessions) == 1
        # An Assignment may first retire changed source, then retire old read
        # windows after a real check failure. Each transition is announced once;
        # a later Assignment can announce its inherited view in its own journal.
        kinds = [row["type"] for row in run_projections]
        assert kinds in (["execution.implementation_context_projected"],
                         ["execution.repair_context_projected"],
                         ["execution.implementation_context_projected", "execution.repair_context_projected"]), (
            f"Expected each durable view transition once, in order: {path.name}", kinds)
        starts = [row for row in rows if row.get("recordType") == "start"]
        assert len(starts) == 1 and starts[0]["agentType"] == "general"
        executor_runs.append((sessions[0], starts[0]["subagentId"], run_projections))
        for projection in run_projections:
            payload = projection["payload"]
            assert payload["projectedInputEstimate"] < payload["canonicalInputEstimate"]
            if projection["type"] == "execution.repair_context_projected":
                assert payload["calibratedInputEstimate"] >= payload["projectedInputEstimate"]
            else:
                assert payload["changedPathCount"] == 1
    assert executor_runs, "The original Executor recovery decision must be retained"
    executor_runs.sort(key=lambda run: run[0]["timestamp"])
    assert len({run[1] for run in executor_runs}) == len(executor_runs)
    assert len({run[0]["payload"]["sessionId"] for run in executor_runs}) == 1
    for index, (session, _subagent_id, _projection) in enumerate(executor_runs):
        assert session["payload"]["resumed"] is (index > 0)
        assert session["payload"]["modelReused"] is (index > 0)
        assert (session["payload"]["priorMessageCount"] > 0) is (index > 0)
    projected = [projection for run in executor_runs for projection in run[2]]
    evidence = [json.loads(line) for path in data_dir.rglob("tool-evidence/*.tools.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]
    started = [row for row in evidence if row.get("type") == "tool.started"
               and row.get("toolName") == "edit_file"
               and row.get("input", {}).get("old") == AMBIGUOUS]
    assert len(started) == 1
    assert started[0]["input"] == {
        "path": "candidate.py", "old": AMBIGUOUS,
        "new": "    model_turns = message_model_turns + reminder_model_turns",
    }
    finished = [row for row in evidence if row.get("type") == "tool.finished"
                and row.get("invocationId") == started[0]["invocationId"]]
    assert len(finished) == 1
    assert finished[0]["isError"] is True and finished[0]["outputTruncated"] is False
    assert "Found 2 matches" in finished[0]["output"]
    assert "Matches at lines:" in finished[0]["output"]
    if calls is not None:
        model_error = next(message for message in calls[16]["messages"]
                           if message.get("role") == "tool_result"
                           and message.get("toolUseId") == "v18-ambiguous-edit")
        # Existing recovery guidance may be appended after the tool's raw
        # output; its complete original prefix must survive byte-for-byte.
        assert model_error["content"].startswith(finished[0]["output"]), (
            "The repair view must retain the complete original edit failure before any host guidance"
        )
    # Projection retires large old successful edit arguments only from the
    # model view. Their original full payload remains in the tool journal.
    original_edits = [row for row in evidence if row.get("type") == "tool.started"
                      and row.get("toolName") == "edit_file"
                      and "implementation marker: draft-11" in row.get("input", {}).get("new", "")]
    assert original_edits and len(original_edits[0]["input"]["old"]) > 6000
    return {"projectionEvent": projected[0], "projectionEvents": projected,
            "failureInput": started[0]["input"],
            "failureOutput": finished[0]["output"], "failureOutputTruncated": False,
            "canonicalOldEditArgumentsRetained": True}


def test_last_ambiguous_edit_can_repair_test_and_deliver_with_original_shared_budget(tmp_path, monkeypatch):
    result, calls, commands, models, budget, target, initial, data_dir = _exercise(tmp_path, monkeypatch)
    assert len(calls) >= 17, result.output
    assert "edit_file" in calls[16]["tools"], "V18 replay closed the same Executor immediately after its ambiguous edit"
    assert len(calls) == 18, result.output
    assert len(models) == 1
    assert result.ok, result.output
    assert REPAIRED in target.read_text(encoding="utf-8")
    # A failed single edit also checks the unchanged candidate before repair.
    assert [c["exit_code"] for c in commands] == [1, 1, 1, 1, 1, 0, 0]
    assert all(c["parent"] == initial for c in commands)
    assert len({c["cwd"] for c in commands}) == len(commands)
    final = budget.snapshot()
    assert final.limit_total_tokens == 500000 and final.limit_model_calls == 40
    assert final.used_total_tokens <= 500000 - BOUNDARY["productionBudget"]["parentPreserveTokens"]
    assert final.used_model_calls == 20 and final.reserved_total_tokens == 0
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is True
    assert trace["policyVersion"] == 3
    assert {t["stage"] for t in trace["transitions"]} == {"executor", "verifier", "commit"}
    manifests = list(data_dir.rglob("scheduler-workflows/*/manifest.json"))
    assert len(manifests) == 1, "Recovery must stay inside the original workflow"
    audit = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert audit["schedulerTrace"]["committed"] is True
    assert audit["dispatchPlan"]["policyVersion"] == 3
    assert audit["dispatchPlan"]["stages"] == ["executor", "verifier"]
    dispatches = [t for t in audit["schedulerTrace"]["transitions"]
                  if t["stage"] == "executor" and t["outcome"] == "dispatched"]
    assert len(dispatches) == 1, "Correction must stay in the original Executor Assignment"
    _durable_recovery_evidence(data_dir, calls)


def test_repaired_edit_cannot_promote_a_candidate_whose_real_checks_still_fail(tmp_path, monkeypatch):
    result, calls, commands, models, budget, target, initial, data_dir = _exercise(
        tmp_path, monkeypatch, candidate_stays_bad=True,
    )
    assert len(calls) >= 18, result.output
    assert len(models) == 1
    assert "edit_file" in calls[16]["tools"]
    assert commands and all(c["exit_code"] == 1 for c in commands)
    assert not result.ok
    assert target.read_text(encoding="utf-8") == initial
    assert budget.snapshot().limit_total_tokens == 500000
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().used_total_tokens <= 500000 - BOUNDARY["productionBudget"]["parentPreserveTokens"]
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is False
    _durable_recovery_evidence(data_dir, calls)


def test_cancelled_repair_cannot_bypass_the_final_gate_or_parent_writeback(tmp_path, monkeypatch):
    with pytest.raises(TurnCancellationRequested):
        _exercise(tmp_path, monkeypatch, cancel_repair=True)
    content = (tmp_path / "candidate.py").read_text(encoding="utf-8")
    assert content.startswith(HEADER)
    assert REPAIRED not in content
    _durable_recovery_evidence(tmp_path.parent / f"{tmp_path.name}-journal")


def test_policy5_bounded_review_keeps_review_floor_with_historical_spent_prefix(tmp_path, monkeypatch):
    result, calls, _commands, models, budget, target, initial, data_dir = _exercise(
        tmp_path, monkeypatch, historical_policy=False)
    assert len(calls) == 16 and len(models) == 1
    # Conditional repair may now share the final response. This fixture still
    # returns its explicit blocker; the exact original downstream floor and
    # ledger below must survive the changed view. It is not a success replay.
    assert calls[-1]["tools"] == {"edit_file", "executor_submit"}
    assert not result.ok and target.read_text(encoding="utf-8") == initial
    trace = extract_scheduler_trace(result.output)
    assert trace["policyVersion"] == 5 and not trace["committed"]
    assert trace["terminalReason"] == "executor_blocked"
    snapshot = budget.snapshot()
    assert snapshot.limit_total_tokens == 500000 and snapshot.limit_model_calls == 40
    assert snapshot.used_model_calls == 18
    assert snapshot.used_total_tokens == BOUNDARY["productionBudget"]["beforeCall19"]["usedTotalTokens"]
    starts = [json.loads(line) for path in data_dir.rglob("subagent-runs/*.jsonl")
              for line in path.read_text().splitlines()]
    executor_start = next(row for row in starts
                          if row.get("recordType") == "start" and row.get("agentType") == "general")
    assert executor_start["budgetAtStart"]["limitTotalTokens"] == 427054
    assert 500000 - executor_start["budgetAtStart"]["limitTotalTokens"] == 72946
    manifests = list(data_dir.rglob("scheduler-workflows/*/manifest.json"))
    assert len(manifests) == 1, result.output
    manifest = json.loads(manifests[0].read_text())
    assert manifest["dispatchPlan"]["policyVersion"] == 5
    assert manifest["dispatchPlan"]["stages"] == ["executor", "verifier", "reviewer"]
    assert list(data_dir.rglob("failure-candidate.json"))
