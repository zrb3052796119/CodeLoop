from __future__ import annotations

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.coding_progress import CodingProgressTracker, is_implementation_task
from minicode.context_compactor import ToolResultBudgetManager
from minicode.intent_parser import parse_intent
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


@pytest.mark.parametrize(
    "prompt_text",
    [
        "Plan how to fix the bug; do not edit.",
        "Review app.py, do not modify it.",
        "Only analyze how to fix this bug",
        "先分析问题，暂时不要修改代码",
        "Explain how you would fix this bug.",
        "What does the function do?",
    ],
)
def test_readonly_requests_are_not_pushed_to_edit(prompt_text):
    assert not is_implementation_task(parse_intent(prompt_text))


@pytest.mark.parametrize(
    "prompt_text",
    [
        "Fix the bug in app.py and add a regression test.",
        "Fix app.py but do not change existing tests.",
        "修复 app.py 的问题并补测试",
    ],
)
def test_explicit_changes_activate_progress_feedback(prompt_text):
    assert is_implementation_task(parse_intent(prompt_text))


def test_budget_feedback_is_early_bounded_and_not_permission():
    tracker = CodingProgressTracker(enabled=True)
    budget = AgentTurnBudget(max_total_tokens=100_000, max_model_calls=20)
    for _ in range(3):
        tracker.observe(successful_read=True, changed=False, verified=False)
    assert tracker.before_request(budget.snapshot(), 10_000) is None
    tracker.observe(successful_read=True, changed=False, verified=False)
    notice = tracker.before_request(budget.snapshot(), 10_000)
    assert notice is not None
    assert "smallest authorized change" in notice.text
    assert "If a specific fact is still missing" in notice.text
    assert "not a reservation" in notice.text
    assert tracker.before_request(budget.snapshot(), 10_000) is None
    for _ in range(20):
        tracker.observe(successful_read=True, changed=False, verified=False)
        tracker.before_request(budget.snapshot(), 10_000)
    assert tracker._notice_count == 3


def test_failures_and_todos_cannot_fake_successful_reading():
    tracker = CodingProgressTracker(enabled=True)
    for _ in range(20):
        tracker.observe(successful_read=False, changed=False, verified=False)
    assert tracker.before_request(None, 1) is None
    assert tracker.read_batches == 0


def test_real_change_resets_inspection_streak():
    tracker = CodingProgressTracker(enabled=True)
    for _ in range(4):
        tracker.observe(successful_read=True, changed=False, verified=False)
    tracker.observe(successful_read=False, changed=True, verified=False)
    assert tracker.before_request(None, 1) is None
    assert tracker.mutations == 1


def _history(count: int, size: int = 6000):
    messages = [{"role": "user", "content": "Fix the requested bug; preserve the API."}]
    for i in range(count):
        messages.extend(
            [
                {
                    "role": "assistant_tool_call",
                    "toolName": "read_file",
                    "toolUseId": str(i),
                    "assistantTurnId": f"turn-{i}",
                    "input": {"path": f"file{i}.py"},
                },
                {
                    "role": "tool_result",
                    "toolName": "read_file",
                    "toolUseId": str(i),
                    "content": f"SOURCE {i}\n" + "x" * size,
                    "isError": False,
                },
            ]
        )
    return messages


def test_old_read_projection_keeps_call_arguments_and_current_batch_intact(tmp_path):
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages = _history(15)
    protected = {str(i) for i in range(7, 15)}  # batch larger than recent-six policy
    reduced, saved = manager.compact_read_history(
        messages,
        eligible_ids={str(i) for i in range(15)},
        protected_ids=protected,
    )
    assert saved > 10_000
    assert len(reduced) == len(messages)
    for old, new in zip(messages, reduced):
        if new.get("_read_history_compacted"):
            assert "current contents may differ" in new["content"]
            assert old["content"][:450] in new["content"]
            assert new["toolUseId"] not in protected
        else:
            assert old == new
    assert all(not m.get("_read_history_compacted") for m in messages)
    assert not list(tmp_path.iterdir())


def test_authority_errors_skills_and_unobserved_results_are_not_trimmed(tmp_path):
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages = _history(16)
    for index, name in enumerate(("task", "edit_file", "test_runner", "load_skill")):
        messages[2 + index * 2]["toolName"] = name
    messages[10]["isError"] = True
    # ID 5 is deliberately absent from the host's successful-read set.
    eligible = {str(i) for i in range(16)} - {"5"}
    reduced, saved = manager.compact_read_history(
        messages,
        eligible_ids=eligible,
        protected_ids={"15"},
        history_budget=1000,
    )
    assert saved > 0
    assert reduced[:13] == messages[:13]


def test_projection_never_writes_even_with_existing_archive_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".mini-code-tool-results").symlink_to(outside, target_is_directory=True)
    messages = _history(12)
    manager = ToolResultBudgetManager(workspace=tmp_path)
    reduced, saved = manager.compact_read_history(
        messages,
        eligible_ids={str(i) for i in range(12)},
        protected_ids={"11"},
    )
    assert saved > 0 and reduced != messages
    assert not list(outside.iterdir())
    assert all("_persisted_path" not in m for m in messages)


class InspectThenAct(ModelAdapter):
    def __init__(self, *, expect_notice=True):
        self.calls = 0
        self.expect_notice = expect_notice

    def next(self, messages, on_stream_chunk=None):
        self.calls += 1
        if self.calls <= 4:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": str(self.calls),
                        "toolName": "read_file",
                        "input": {"path": "app.py"},
                    }
                ],
            )
        if self.calls == 5:
            notices = [m for m in messages if m.get("_coding_progress")]
            assert bool(notices) is self.expect_notice
            if not self.expect_notice:
                return AgentStep(
                    type="assistant", content="Read-only investigation complete."
                )
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "edit",
                        "toolName": "edit_file",
                        "input": {
                            "path": "app.py",
                            "old": "return 0",
                            "new": "return 1",
                        },
                    }
                ],
            )
        return AgentStep(type="assistant", content="Modified app.py; not yet tested.")


def test_headless_loop_delivers_checkpoint_before_budget_exhaustion_and_real_edit(
    tmp_path,
):
    target = tmp_path / "app.py"
    target.write_text("def value():\n    return 0\n")
    messages = run_agent_turn(
        model=InspectThenAct(),
        tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[
            {"role": "user", "content": "Fix the bug in app.py: value must return 1."}
        ],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=8,
        permissions=PermissionManager(
            str(tmp_path), prompt=lambda _: {"decision": "allow_once"}
        ),
        agent_budget=AgentTurnBudget(max_total_tokens=50_000),
    )
    assert "return 1" in target.read_text()
    assert any(m.get("_coding_progress") for m in messages)


def test_plan_role_never_receives_implementation_nudge_even_with_fix_in_task(tmp_path):
    (tmp_path / "app.py").write_text("def value():\n    return 0\n")
    run_agent_turn(
        model=InspectThenAct(expect_notice=False),
        tools=ToolRegistry([read_file_tool]),
        messages=[
            {"role": "user", "content": "Fix the bug in app.py: value must return 1."}
        ],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=8,
    )
    assert "return 0" in (tmp_path / "app.py").read_text()
