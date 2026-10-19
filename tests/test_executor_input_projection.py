"""Display deduplication must retain every byte and all frozen authority."""

import json
from copy import deepcopy

import pytest
from test_multi_agent_v2_execution_contracts import (
    _executor_assignment,
    _implementation_order,
)

from minicode.executor_input_projection import project_executor_input
from minicode.multi_agent_contracts import parse_assignment, parse_implementation_order


def _project(task, texts, *, actions=None, objective=None):
    criteria = [{"id": f"ac_{index}", "kind": "behavior", "text": text}
                for index, text in enumerate(texts, 1)]
    assignment = parse_assignment(_executor_assignment(
        objective=task if objective is None else objective,
        acceptanceCriteria=criteria,
    ))
    raw_order = _implementation_order()
    raw_order["criterionIds"] = [item["id"] for item in criteria]
    for index, step in enumerate(raw_order["steps"]):
        step["action"] = task if actions is None else actions[index]
        step["criterionIds"] = list(raw_order["criterionIds"])
    order = parse_implementation_order(assignment, raw_order)
    before = deepcopy((assignment.to_dict(), order.to_dict()))
    result = project_executor_input(task, assignment, order,
                                    assignment_artifact_ref="artifact_executor_assignment")
    assert (assignment.to_dict(), order.to_dict()) == before
    return result, before


def _original_text(view, task):
    if "text" in view:
        return view["text"]
    span = view["originalTaskRange"]
    return task[span["start"]:span["end"]]


def test_one_readable_task_copy_preserves_unicode_whitespace_and_every_criterion():
    clauses = ["1. 保留工具结果，包括 emoji 🧪。", "2. Reject invalid reports, even when tests pass."]
    task = "Task:\r\n\r\n" + clauses[0] + "\r\n  \r\n" + clauses[1]
    projected, (assignment, order) = _project(task, clauses)
    restored = projected.original_task
    for criterion in projected.assignment["acceptanceCriteria"]:
        restored = restored.replace(f"[criterion {criterion['id']}]\n", "", 1)
    assert restored == task, "The rendered task may insert labels but cannot drop any original character."
    assert [_original_text(item, task) for item in projected.assignment["acceptanceCriteria"]] == clauses
    for index, clause in enumerate(clauses, 1):
        assert f"[criterion ac_{index}]\n{clause}" in projected.original_task
        assert clause not in projected.render_contracts()
    assert "objective" not in projected.assignment
    assert projected.assignment["originalTaskRef"] == "ORIGINAL TASK"
    assert all("action" not in step and step["originalTaskRef"] == "ORIGINAL TASK"
               for step in projected.order["steps"])
    assert projected.assignment["fullAssignmentArtifactRef"] == "artifact_executor_assignment"
    assert projected.order["fullOrderArtifactRef"] == order["orderId"]
    for field in ("assignmentId", "workflowId", "taskId", "scope", "constraints", "budget",
                  "risk", "role", "mode", "inputRefs", "expectedResultType"):
        assert projected.assignment[field] == assignment[field]
    for field in ("orderId", "assignmentId", "baseDigest", "allowedPaths", "criterionIds", "verificationRequests"):
        assert projected.order[field] == order[field]
    for actual, original in zip(projected.order["steps"], order["steps"]):
        assert {key: actual[key] for key in ("id", "allowedPaths", "criterionIds")} == {
            key: original[key] for key in ("id", "allowedPaths", "criterionIds")
        }


@pytest.mark.parametrize("task,texts", [
    ("Use exact matching only.", ["An extra requirement absent from the task."]),
    ("Keep repeated words; keep repeated words.", ["repeated words"]),
    ("Keep every requirement.", ["Keep every requirement.", "every requirement"]),
    ("Keep every requirement.", ["every requirement", "every requirement"]),
])
def test_unmatched_repeated_or_overlapping_criteria_remain_verbatim(task, texts):
    projected, _ = _project(task, texts)
    assert projected.original_task == task
    assert [item["text"] for item in projected.assignment["acceptanceCriteria"]] == texts
    assert all("textRef" not in item for item in projected.assignment["acceptanceCriteria"])


@pytest.mark.parametrize("task,criterion", [
    ('expected = "preserve all findings"', "preserve all findings"),
    ("Preserve every result\nincluding failed commands.", "Preserve every result"),
    ("Preserve every result\nincluding failed commands.", "including failed commands."),
    ("- Preserve every result\n  including failed commands.", "- Preserve every result"),
    ("    preserve all findings", "preserve all findings"),
    ("Example:\n\n```text\npreserve all findings\n```", "preserve all findings"),
    ("Example:\n\n~~~text\npreserve all findings\n~~~", "preserve all findings"),
    ("Example:\n\n```text\n\npreserve all findings", "preserve all findings"),
])
def test_unsafe_inline_partial_and_code_examples_remain_verbatim(task, criterion):
    projected, _ = _project(task, [criterion])
    assert projected.original_task == task
    assert projected.assignment["acceptanceCriteria"][0]["text"] == criterion
    assert "textRef" not in projected.assignment["acceptanceCriteria"][0]


def test_complete_list_items_keep_their_wrapped_lines_and_receive_external_labels():
    criteria = ["- Preserve every result\n  including failed commands.", "- Preserve receipts."]
    task = "Requirements:\n" + "\n".join(criteria) + "\n\nEnd."
    projected, _ = _project(task, criteria)
    assert projected.original_task == (
        "Requirements:\n[criterion ac_1]\n" + criteria[0]
        + "\n[criterion ac_2]\n" + criteria[1] + "\n\nEnd."
    )
    assert [_original_text(item, task) for item in projected.assignment["acceptanceCriteria"]] == criteria


def test_distinct_analyst_actions_and_assignment_objective_are_not_replaced():
    task = "Keep the public task exactly as requested."
    actions = ["Implement the frozen Analyst finding.", "Run the specified regression tests."]
    objective = "A distinct, stricter Assignment objective."
    projected, _ = _project(task, ["Keep the public task exactly as requested."], actions=actions, objective=objective)
    assert projected.assignment["objective"] == objective
    assert [step["action"] for step in projected.order["steps"]] == actions
    assert "originalTaskRef" not in projected.assignment


def test_contract_normalization_does_not_remove_original_task_whitespace():
    task = "\n  Keep the original spacing exactly.\n  "
    projected, (assignment, order) = _project(task, [task.strip()])
    assert projected.original_task.replace("[criterion ac_1]\n", "", 1) == task
    assert projected.assignment["objective"] == assignment["objective"]
    assert [step["action"] for step in projected.order["steps"]] == [
        step["action"] for step in order["steps"]
    ]
    assert "originalTaskRef" not in projected.assignment


def test_workflow_supplies_views_but_preserves_readable_original_contract_artifacts(tmp_path, monkeypatch):
    from test_workflow_v2_structured_routing import (
        _prompt_json,
        _request,
        _verification_result,
        _write_executor_declaration,
        _write_reviewer_result,
    )

    import minicode.tools.task as task_module
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.tooling import ToolContext, ToolResult

    clauses = ["Update allowed.txt from before to after.", "Preserve the original verification command."]
    task = "\n\n".join(clauses)
    request = _request(risk="low", complex=False)
    request["criteria"] = [{"id": "ac1", "kind": "behavior", "text": task}]
    (tmp_path / "allowed.txt").write_text("before")
    seen = []

    def worker(input_data, context):
        if context._logical_agent_role == "executor":
            prompt = input_data["prompt"]
            assignment_view = _prompt_json(prompt, "EXECUTOR ASSIGNMENT")
            order_view = _prompt_json(prompt, "LOCKED IMPLEMENTATION ORDER")
            original_assignment = json.loads(context._subagent_mailbox.read(
                assignment_view["fullAssignmentArtifactRef"]).content)["assignment"]
            original_order = json.loads(context._subagent_mailbox.read(
                order_view["fullOrderArtifactRef"]).content)
            assert original_assignment["objective"] == task
            assert original_assignment["acceptanceCriteria"][0]["text"] == task
            assert all(step["action"] == task for step in original_order["steps"])
            assert prompt.count(clauses[0]) == 1 and prompt.count(clauses[1]) == 1
            assert assignment_view["scope"] == original_assignment["scope"]
            assert assignment_view["budget"] == original_assignment["budget"]
            assert order_view["verificationRequests"] == original_order["verificationRequests"]
            seen.append(assignment_view)
            from pathlib import Path
            Path(context.cwd, "allowed.txt").write_text("after")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="Candidate received.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", lambda _self, **request: _verification_result(request))
    result = task_module.task_tool.run({
        "agent_type": "workflow", "description": "contract display projection",
        "prompt": task, "workflow_request": request,
    }, ToolContext(cwd=str(tmp_path), _runtime={"model": "fixture"}, _workflow_commit_audit_waived=True))
    assert result.ok, result.output
    assert len(seen) == 1
    assert (tmp_path / "allowed.txt").read_text() == "after"
