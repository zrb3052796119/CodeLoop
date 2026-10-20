"""Convenience delegation preserves the brief as explicit verification duties."""

from __future__ import annotations

import pytest

from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task as task_module


def _payload(prompt):
    return {
        "agent_type": "workflow",
        "description": "Repair the example",
        "prompt": prompt,
        "write_paths": ["src/example.py", "tests/test_example.py"],
        "verification_commands": [
            ["python3", "-m", "pytest", "-q", "tests/test_example.py"],
            ["python3", "-m", "compileall", "src/example.py"],
        ],
    }


@pytest.mark.parametrize(
    ("prompt", "clauses"),
    [
        (
            "Repair the parser.\nKeep its public interface.\n\nRetain the diagnostic details.",
            ["Repair the parser.\nKeep its public interface.", "Retain the diagnostic details."],
        ),
        (
            "Preserve the original requirements:\n1. Accept ordinary records.\n   Keep their order.\n2) Reject invalid records.",
            ["Preserve the original requirements:", "1. Accept ordinary records.\n   Keep their order.", "2) Reject invalid records."],
        ),
        (
            "- Retain the response.\n  Include its detail.\n* Check the failure path.\n+ Deliver the result.",
            ["- Retain the response.\n  Include its detail.", "* Check the failure path.", "+ Deliver the result."],
        ),
        (
            "一、保留正常行为。\n  包括原有返回内容。\n二、覆盖错误分支。\n（1）保留诊断。\n（2）交付修复。",
            ["一、保留正常行为。\n  包括原有返回内容。", "二、覆盖错误分支。", "（1）保留诊断。", "（2）交付修复。"],
        ),
        (
            "1、保留已有字段。\n2、校验缺失字段。\n• 运行公开测试。",
            ["1、保留已有字段。", "2、校验缺失字段。", "• 运行公开测试。"],
        ),
        (
            "Keep version 3.14 intact.\nDo not split ordinary wrapped prose.",
            ["Keep version 3.14 intact.\nDo not split ordinary wrapped prose."],
        ),
        (
            "- Preserve this behavior:\n  - Include its nested detail.\n  Keep the original explanation.\n- Test the other behavior.",
            ["- Preserve this behavior:\n  - Include its nested detail.\n  Keep the original explanation.", "- Test the other behavior."],
        ),
        (
            "Preserve this literal example:\n```text\n1. Example item.\n\n- Example continuation.\n```\n\nCheck the actual behavior.",
            ["Preserve this literal example:\n```text\n1. Example item.\n\n- Example continuation.\n```", "Check the actual behavior."],
        ),
        (
            "Keep both lines.\r\nPreserve their contents.\r\n\r\nRun the check.",
            ["Keep both lines.\r\nPreserve their contents.", "Run the check."],
        ),
        ("Repeat this requirement.\n\nRepeat this requirement.", ["Repeat this requirement.", "Repeat this requirement."]),
    ],
)
def test_simplified_entry_preserves_clauses_and_links_all_check_obligations(prompt, clauses):
    validated = task_module.task_tool.validator(_payload(prompt))
    request, error = task_module._auto_construct_workflow_request(validated)

    assert error is None
    assert [criterion["text"] for criterion in request["criteria"]] == clauses
    criterion_ids = [f"c{index}" for index in range(1, len(clauses) + 1)]
    assert [criterion["id"] for criterion in request["criteria"]] == criterion_ids
    assert all(check["criterionIds"] == criterion_ids for check in request["verificationChecks"])
    assert request["scope"]["writePaths"] == ["src/example.py", "tests/test_example.py"]


@pytest.mark.parametrize("prompt", [None, "", " \n\t "])
def test_missing_prompt_cannot_be_replaced_by_display_description(prompt):
    request, error = task_module._auto_construct_workflow_request(_payload(prompt))
    assert request is None
    assert "prompt is required" in error


def test_workers_are_shown_the_forwarded_user_request():
    from types import SimpleNamespace

    from minicode.workflow_runner import _model_task_text

    context = SimpleNamespace(_workflow_user_request="full spec " * 20)
    rendered = _model_task_text("short objective", context)
    assert rendered.startswith("USER REQUEST")
    assert "full spec" in rendered
    assert "DELEGATION PROMPT:\nshort objective" in rendered
    same = _model_task_text("same", SimpleNamespace(_workflow_user_request="same"))
    assert same == "same"


def test_one_long_clause_is_split_without_dropping_characters():
    prompt = "x" * 4001
    request, error = task_module._auto_construct_workflow_request(_payload(prompt))
    assert error is None
    texts = [item["text"] for item in request["criteria"]]
    assert "".join(texts) == prompt
    assert all(len(item) <= 4000 for item in texts)
    assert len(texts) == 2


def test_too_many_clauses_are_still_rejected():
    prompt = "\n".join(f"{index}. Requirement {index}." for index in range(1, 42))
    request, error = task_module._auto_construct_workflow_request(_payload(prompt))
    assert request is None
    assert "41 criteria exceed the limit of 40" in error


@pytest.mark.parametrize("clauses", [["x" * 4000], [f"{index}. Requirement {index}." for index in range(1, 41)]])
def test_exact_criterion_bounds_preserve_all_text(clauses):
    request, error = task_module._auto_construct_workflow_request(_payload("\n".join(clauses)))
    assert error is None
    assert [item["text"] for item in request["criteria"]] == clauses


def test_explicit_workflow_request_keeps_its_original_criteria(monkeypatch):
    captured = {}

    def capture(input_data, _context):
        captured.update(input_data)
        return ToolResult(ok=True, output="captured")

    monkeypatch.setattr(task_module, "_SYNC_RUN", capture)
    payload = {
        "agent_type": "workflow", "description": "Repair example",
        "prompt": "1. First clause.\n2. Second clause.",
        "workflow_request": task_module._WORKFLOW_REQUEST_EXAMPLE,
    }
    validated = task_module.task_tool.validator(payload)
    result = task_module.task_tool.run(validated, ToolContext(_central_workflow_v2_enforced=True))
    assert result.ok
    assert captured["workflow_request"]["criteria"] == task_module._WORKFLOW_REQUEST_EXAMPLE["criteria"]
    assert "_workflow_auto_constructed" not in captured


def test_rejected_convenience_request_cannot_start_a_worker(monkeypatch):
    monkeypatch.setattr(task_module, "_SYNC_RUN", lambda *_args: pytest.fail("worker must not run"))
    prompt = "\n".join(f"{index}. Requirement {index}." for index in range(1, 42))
    validated = task_module.task_tool.validator(_payload(prompt))
    result = task_module.task_tool.run(validated, ToolContext(_central_workflow_v2_enforced=True))
    assert not result.ok
    assert "41 criteria exceed the limit of 40" in result.output
