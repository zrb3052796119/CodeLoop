"""Behavioral evidence checks: a request or container is not delivered work."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.subagent_result import project_subagent_result, render_subagent_result
from minicode.tooling import ToolResult

PLAN = "sub_" + "1" * 32
WORKFLOW = "sub_" + "2" * 32
EXECUTOR = "sub_" + "3" * 32
OTHER_WORKFLOW = "sub_" + "4" * 32


@pytest.fixture
def protocol(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("three_arm_protocol")


def projection(actor, kind="plan", outcome="completed"):
    return project_subagent_result(
        None,
        subagent_id=actor,
        agent_type=kind,
        outcome=outcome,
        fallback_summary="Host-projected child result with evidence.",
    )


def output(actor, kind="plan", outcome="completed"):
    return render_subagent_result(projection(actor, kind, outcome))


def message(actor, kind="plan", outcome="completed", role="tool_result"):
    return {"role": role, "toolName": "task", "content": output(actor, kind, outcome)}


def evidence():
    records = [
        {
            "subagentId": PLAN,
            "agentType": "plan",
            "role": "plan",
            "isContainer": False,
            "parentSubagentId": None,
            "startedEpoch": 1.0,
            "returnedEpoch": 2.0,
            "returned": True,
            "toolOk": True,
            "projected": projection(PLAN),
        },
        {
            "subagentId": WORKFLOW,
            "agentType": "workflow",
            "role": "workflow",
            "isContainer": True,
            "parentSubagentId": None,
            "startedEpoch": 3.0,
            "returnedEpoch": 6.0,
            "returned": True,
            "toolOk": True,
            "projected": projection(WORKFLOW, "workflow"),
        },
        {
            "subagentId": EXECUTOR,
            "agentType": "general",
            "role": "Executor",
            "isContainer": False,
            "parentSubagentId": WORKFLOW,
            "startedEpoch": 4.0,
            "returnedEpoch": 5.0,
            "returned": True,
            "toolOk": True,
            "projected": projection(EXECUTOR, "general"),
        },
    ]
    requests = [
        {"subagentId": PLAN, "event": "completed"},
        {"subagentId": EXECUTOR, "event": "completed"},
    ]
    messages = [message(PLAN), message(WORKFLOW, "workflow")]
    return records, requests, messages


def test_task_request_and_workflow_container_do_not_count_as_leaf_execution(protocol):
    records, _, messages = evidence()
    # The parent may request task and a container may itself have a request;
    # neither is evidence that an analysis/implementation leaf ran.
    requests = [
        {"subagentId": None, "event": "completed", "toolName": "task"},
        {"subagentId": WORKFLOW, "event": "completed"},
    ]
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["actualLeafRuns"] == 0
    assert result["leafModelCalls"] == 0
    assert result["deliveredLeafResults"] == 0
    assert result["collaborationProtocolSatisfied"] is False


@pytest.mark.parametrize("event", ["started", "failed"])
def test_leaf_attempt_without_completed_response_does_not_activate(protocol, event):
    records, requests, messages = evidence()
    for request in requests:
        request["event"] = event
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["leafModelCalls"] == 2, (
        "Failed/in-flight attempts remain resource use"
    )
    assert result["actualLeafRuns"] == 0
    assert result["collaborationExecuted"] is False
    assert result["collaborationProtocolSatisfied"] is False


def test_two_real_role_calls_with_spawn_ids_do_not_prove_delivery(protocol):
    records, requests, _ = evidence()
    messages = [
        {
            "role": "tool_result",
            "content": json.dumps(
                {"subagentId": actor, "status": "running", "terminal": False}
            ),
        }
        for actor in (PLAN, WORKFLOW, EXECUTOR)
    ]
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["actualLeafRuns"] == 2
    assert result["deliveredLeafResults"] == 0
    assert result["collaborationProtocolSatisfied"] is False


@pytest.mark.parametrize("role", ["tool_result", "tool"])
def test_canonical_tool_result_projection_proves_ordered_recipe_delivery(
    protocol, role
):
    records, requests, _ = evidence()
    messages = [message(PLAN, role=role), message(WORKFLOW, "workflow", role=role)]
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["actualLeafRuns"] == 2
    assert result["successfulLeafRuns"] == 2
    assert result["readAnalysisLeafRuns"] == 1
    assert result["implementationLeafRuns"] == 1
    assert result["deliveredLeafResults"] == 2
    assert result["collaborationProtocolSatisfied"] is True


@pytest.mark.parametrize(
    "invalid",
    [
        {"resultVersion": 1, "subagentId": PLAN, "outcome": "completed"},
        {"subagentId": PLAN, "status": "completed", "terminal": True},
        {"resultVersion": 1, "subagentId": "not-a-host-id", "outcome": "completed"},
    ],
)
def test_incomplete_or_forged_result_projection_is_not_delivery(protocol, invalid):
    assert protocol.result_ids(invalid) == set()
    assert protocol.result_ids(json.dumps(invalid)) == set()


def test_canonical_projection_in_non_tool_prose_is_not_delivery(protocol):
    records, requests, _ = evidence()
    messages = [
        message(PLAN, role="assistant"),
        message(WORKFLOW, "workflow", role="user"),
    ]
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["deliveredLeafResults"] == 0
    assert result["collaborationProtocolSatisfied"] is False


def test_executor_must_start_after_analysis_returned(protocol):
    records, requests, messages = evidence()
    records[0]["returnedEpoch"] = 4.5  # Executor began at 4.0.
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["deliveredLeafResults"] == 2
    assert result["collaborationProtocolSatisfied"] is False


def test_executor_must_start_after_owner_ack_when_delivery_time_is_available(protocol):
    records, requests, messages = evidence()
    records[0]['deliveredEpoch'] = 4.5  # Returned at 2.0, delivered after Executor began.
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result['deliveredLeafResults'] == 2
    assert result['collaborationProtocolSatisfied'] is False


def test_executor_delivery_without_own_workflow_result_is_not_complete_recipe(protocol):
    records, requests, _ = evidence()
    result = protocol.collaboration_metrics(
        records, requests, [message(PLAN), message(EXECUTOR, "general")]
    )
    assert result["deliveredWorkflowResults"] == 0
    assert result["collaborationProtocolSatisfied"] is False


def test_verifier_failure_preserves_executed_and_delivered_collaboration(protocol):
    records, requests, _ = evidence()
    records[1].update(
        toolOk=False, projected=projection(WORKFLOW, "workflow", "failed")
    )
    records[2].update(toolOk=False, projected=projection(EXECUTOR, "general", "failed"))
    result = protocol.collaboration_metrics(
        records, requests, [message(PLAN), message(WORKFLOW, "workflow", "failed")]
    )
    assert result["successfulLeafRuns"] == 1
    assert result["actualLeafRuns"] == 2
    assert result["deliveredLeafResults"] == 2
    assert result["collaborationProtocolSatisfied"] is True


@pytest.mark.parametrize("direct_executor_receipt", [False, True])
def test_unrelated_workflow_cannot_claim_executor_by_time_or_direct_id(
    protocol, direct_executor_receipt
):
    records, requests, _ = evidence()
    other = copy.deepcopy(records[1])
    other.update(
        subagentId=OTHER_WORKFLOW, projected=projection(OTHER_WORKFLOW, "workflow")
    )
    records.append(other)  # Same time window, different actual parent.
    messages = [message(PLAN), message(OTHER_WORKFLOW, "workflow")]
    if direct_executor_receipt:
        messages.append(message(EXECUTOR, "general"))
    result = protocol.collaboration_metrics(records, requests, messages)
    assert result["deliveredWorkflowResults"] == 1
    assert result["collaborationProtocolSatisfied"] is False


def test_owner_ack_persists_delivery_ids_across_compaction(protocol, tmp_path, monkeypatch):
    from minicode import agent_loop
    import minicode.tools.task as task_module

    records, requests, _ = evidence()
    destination = tmp_path / "collaboration.json"
    acknowledged = []

    def original(data, context):
        return ToolResult(ok=False, output="[Workflow execute phase failed]" if data['agent_type'] == 'workflow' else output(PLAN), _delivery_ack=lambda: acknowledged.append(data['_subagent_id']))

    monkeypatch.setattr(task_module, "_run", original)
    with protocol.CollaborationEvidence(destination):
        for actor, kind in ((PLAN, 'plan'), (WORKFLOW, 'workflow')):
            result = task_module._run({'agent_type': kind, '_subagent_id': actor}, SimpleNamespace())
            before = json.loads(destination.read_text())
            assert actor not in before['deliveredIds'], 'Returning does not prove parent delivery'
            agent_loop._acknowledge_tool_delivery(result)
    snapshot = json.loads(destination.read_text())
    assert set(snapshot["deliveredIds"]) == {PLAN, WORKFLOW}
    assert acknowledged == [PLAN, WORKFLOW]
    assert all(r['deliveryEvidence'] == 'task_result_owner_ack' for r in snapshot['runs'])
    assert all(r['deliveredEpoch'] >= r['returnedEpoch'] for r in snapshot['runs'])
    result = protocol.collaboration_metrics(
        records, requests, [], delivered_ids=snapshot["deliveredIds"]
    )
    assert result["collaborationProtocolSatisfied"] is True


def test_other_tool_cannot_forge_delivery_with_canonical_task_text(protocol, tmp_path):
    from minicode import agent_loop

    with protocol.CollaborationEvidence(tmp_path / 'unrelated.json') as observer:
        agent_loop._acknowledge_tool_delivery(ToolResult(ok=True, output=output(PLAN)))
        assert observer.delivered == set()
    records, requests, _ = evidence()
    messages = [dict(message(PLAN), toolName='read_file'), dict(message(WORKFLOW, 'workflow'), toolName='subagent_note_read')]
    metrics = protocol.collaboration_metrics(records, requests, messages)
    assert metrics['deliveredLeafResults'] == 0


def test_observer_binds_nested_executor_to_its_actual_workflow_parent(
    protocol, tmp_path, monkeypatch
):
    import minicode.tools.task as task_module

    def original(data, context):
        if data["agent_type"] == "workflow":
            task_module._run(
                {"agent_type": "general", "_subagent_id": EXECUTOR},
                SimpleNamespace(_logical_agent_role="Executor"),
            )
            return ToolResult(ok=True, output=output(WORKFLOW, "workflow"))
        return ToolResult(ok=True, output=output(EXECUTOR, "general"))

    monkeypatch.setattr(task_module, "_run", original)
    with protocol.CollaborationEvidence(tmp_path / "nested.json") as observer:
        task_module._run(
            {"agent_type": "workflow", "_subagent_id": WORKFLOW}, SimpleNamespace()
        )
    by_id = {record["subagentId"]: record for record in observer.records}
    assert by_id[WORKFLOW]["parentSubagentId"] is None
    assert by_id[EXECUTOR]["parentSubagentId"] == WORKFLOW
    assert by_id[EXECUTOR]["role"] == "Executor"
    assert by_id[WORKFLOW]["isContainer"] is True
    assert by_id[EXECUTOR]["isContainer"] is False
