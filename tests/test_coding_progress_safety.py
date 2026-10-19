"""Progress feedback must not weaken source, delivery, or worker boundaries."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.auto_mode import PermissionMode
from minicode.context_compactor import ReadDedupManager, ToolResultBudgetManager
from minicode.permissions import PermissionManager
from minicode.scheduler_permissions import SchedulerExecutorPermissions
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.turn_completion import TurnCompletionPolicy
from minicode.types import AgentStep, ModelAdapter
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


_SOURCE = "# first source marker\n" + "# source context\n" * 360 + "value = 0\n"


class ReadingModel(ModelAdapter):
    def __init__(self, *, reads: int = 12, edit: bool = False, allow_dedup: bool = False) -> None:
        self.reads = reads
        self.edit = edit
        self.allow_dedup = allow_dedup
        self.sent_reads = 0
        self.sent_edit = False
        self.first_seen: set[str] = set()
        self.notices: list[dict] = []

    def next(self, messages, on_stream_chunk=None):
        self.notices = [message for message in messages if message.get("_coding_progress")]
        for message in messages:
            identity = message.get("toolUseId")
            if (
                message.get("role") == "tool_result"
                and message.get("toolName") == "read_file"
                and identity not in self.first_seen
            ):
                assert message["isError"] is False
                if self.allow_dedup and message["content"].startswith("[Read deduplicated:"):
                    assert any(_SOURCE in prior.get("content", "") for prior in messages)
                else:
                    assert _SOURCE in message["content"], "first model view lost source bytes"
                self.first_seen.add(identity)
        if self.sent_reads < self.reads:
            self.sent_reads += 1
            return AgentStep(type="tool_calls", calls=[{
                "id": f"read-{self.sent_reads}", "toolName": "read_file",
                "input": {"path": "app.py", "limit": 8000},
            }])
        if self.edit and not self.sent_edit:
            self.sent_edit = True
            return AgentStep(type="tool_calls", calls=[{
                "id": "edit", "toolName": "edit_file",
                "input": {"path": "app.py", "old": "value = 0", "new": "value = 1"},
            }])
        return AgentStep(type="assistant", content="Finished the requested work.")


def test_executor_inspection_cannot_create_out_of_scope_archive(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        permissions = SchedulerExecutorPermissions(
            str(transaction.cwd), read_roots=("app.py",),
            write_paths=("app.py",), forbidden_paths=(),
        )
        model = ReadingModel(reads=4, edit=True)
        messages = run_agent_turn(
            model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
            messages=[{"role": "user", "content": "Perform the locked Assignment."}],
            cwd=str(transaction.cwd), permissions=permissions,
            implementation_task=True, enable_work_chain=False, max_steps=16,
        )
        assert model.notices
        assert len(model.first_seen) == 4
        assert "value = 1" in Path(transaction.cwd, "app.py").read_text()
        assert {path.name for path in transaction.cwd.iterdir()} == {"app.py", ".git"}
        # The real transaction must still accept the exact authorized patch.
        frozen = transaction.freeze(allowed_paths=("app.py",), paths_relative_to_cwd=True)
        assert frozen.change_set_document["files"]
        assert not any("_persisted_path" in message for message in messages)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == _SOURCE


def test_first_model_view_is_full_and_compaction_never_mutates_input_messages(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    model = ReadingModel()
    inputs = [{"role": "user", "content": "Fix app.py after identifying the cause."}]
    original = copy.deepcopy(inputs)
    result = run_agent_turn(
        model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=inputs, cwd=str(tmp_path), enable_work_chain=False, max_steps=16,
        enable_implementation_phases=False,  # isolate first-view/compaction semantics
    )
    assert inputs == original
    assert len(model.first_seen) == 12
    old_read = next(message for message in result if message.get("toolUseId") == "read-1" and message["role"] == "tool_result")
    assert len(old_read["content"]) < len(_SOURCE)
    assert not any("_persisted_path" in message for message in result)
    assert not (tmp_path / ".mini-code-tool-results").exists()


def test_shortened_source_cannot_remain_a_read_dedup_authority(tmp_path: Path) -> None:
    history = []
    dedup = ReadDedupManager()
    for index in range(12):
        path = f"file-{index}.py"
        history.extend([
            {"role": "assistant_tool_call", "toolUseId": str(index), "toolName": "read_file", "input": {"path": path}},
            {"role": "tool_result", "toolUseId": str(index), "toolName": "read_file", "content": _SOURCE, "isError": False},
        ])
        dedup.register_read(path, _SOURCE, len(history) - 1)
    assert dedup.should_dedup("file-0.py", _SOURCE)
    reduced, saved = ToolResultBudgetManager(workspace=tmp_path).compact_read_history(
        history, eligible_ids={str(index) for index in range(12)}, protected_ids={"11"},
    )
    assert saved > 0
    assert dedup.reconcile(reduced) > 0
    assert not dedup.should_dedup("file-0.py", _SOURCE)
    assert dedup.should_dedup("file-11.py", _SOURCE)
    assert history[1]["content"] == _SOURCE
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("plan_mode", [False, True])
def test_readonly_user_or_plan_mode_never_gets_change_checkpoint(
    tmp_path: Path, plan_mode: bool,
) -> None:
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    model = ReadingModel()
    result = run_agent_turn(
        model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{
            "role": "user",
            "content": "Fix app.py." if plan_mode else "Review app.py only; do not modify files.",
        }],
        cwd=str(tmp_path), enable_work_chain=False, max_steps=16,
        permissions=PermissionManager(
            str(tmp_path), auto_mode=PermissionMode.PLAN if plan_mode else PermissionMode.DEFAULT,
        ),
    )
    assert not model.notices
    assert not any(message.get("_coding_progress") for message in result)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == _SOURCE
    assert not (tmp_path / ".mini-code-tool-results").exists()


def test_must_deliver_receipt_remains_exact_after_later_read_compaction(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    receipt = "COMMITTED AUTHORITY\n" + "frozen evidence\n" * 800
    acknowledgements = []
    reader = ReadingModel()

    class ReceiptThenRead(ModelAdapter):
        def __init__(self) -> None:
            self.delivered = False

        def next(self, messages, on_stream_chunk=None):
            if not self.delivered:
                self.delivered = True
                return AgentStep(type="tool_calls", calls=[{
                    "id": "committed", "toolName": "task", "input": {},
                }])
            exact = [message for message in messages if message.get("toolUseId") == "committed" and message["role"] == "tool_result"]
            assert len(exact) == 1 and exact[0]["content"] == receipt
            return reader.next(messages, on_stream_chunk)

    task = ToolDefinition(
        name="task", description="fixture finalized workflow",
        input_schema={"type": "object"}, validator=lambda value: value,
        run=lambda _input, _context: ToolResult(
            ok=True, output=receipt, _must_deliver=True, _continue_parent=True,
            _delivery_ack=lambda: acknowledgements.append("delivered"),
        ),
    )
    result = run_agent_turn(
        model=ReceiptThenRead(), tools=ToolRegistry([read_file_tool, edit_file_tool, task]),
        messages=[{"role": "user", "content": "Fix both requested modules."}],
        cwd=str(tmp_path), enable_work_chain=False, max_steps=16,
        enable_implementation_phases=False,  # inspect old history after authority delivery
        agent_budget=AgentTurnBudget(max_total_tokens=200_000),
    )
    assert acknowledgements == ["delivered"]
    assert any(message.get("content") == receipt for message in result)
    assert len(reader.first_seen) == 12


def test_production_task_executor_activates_feedback_for_typed_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.subagent_model_routing as routing
    from minicode.tools.task import task_tool

    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    model = ReadingModel(reads=4, edit=True, allow_dedup=True)
    monkeypatch.setattr(routing, "create_subagent_model_adapter", lambda *_args, **_kwargs: model)
    result = task_tool.run(
        {"description": "scheduled step", "prompt": "Perform the locked Assignment.", "agent_type": "general"},
        ToolContext(
            cwd=str(tmp_path), _runtime={"model": "fake"},
            _logical_agent_role="executor",
            _logical_agent_scope={
                "read_roots": ("app.py",), "write_paths": ("app.py",), "forbidden_paths": (),
            },
            _logical_agent_workspace_root=str(tmp_path),
        ),
    )
    assert result.ok is True, result.output
    assert model.notices, "real task dispatch did not enable implementation feedback"
    assert "value = 1" in (tmp_path / "app.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("target_name", [".env", "outside-scope.py"])
def test_progress_fingerprinting_does_not_read_forbidden_assignment_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_name: str,
) -> None:
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    forbidden = tmp_path / target_name
    forbidden.write_text("PRIVATE_MARKER=secret\n", encoding="utf-8")
    fingerprinted = []
    original = TurnCompletionPolicy._fingerprint

    def observe_fingerprint(self, target):
        fingerprinted.append(Path(target))
        return original(self, target)

    monkeypatch.setattr(TurnCompletionPolicy, "_fingerprint", observe_fingerprint)

    class DeniedEdit(ModelAdapter):
        def __init__(self) -> None:
            self.sent = False

        def next(self, messages, on_stream_chunk=None):
            if not self.sent:
                self.sent = True
                return AgentStep(type="tool_calls", calls=[{
                    "id": "denied", "toolName": "edit_file",
                    "input": {"path": target_name, "old": "secret", "new": "changed"},
                }])
            return AgentStep(type="assistant", content="Blocked by the Assignment scope.")

    result = run_agent_turn(
        model=DeniedEdit(), tools=ToolRegistry([edit_file_tool]),
        messages=[{"role": "user", "content": "Perform the locked Assignment."}],
        cwd=str(tmp_path), enable_work_chain=False, implementation_task=True, max_steps=3,
        permissions=SchedulerExecutorPermissions(
            str(tmp_path), read_roots=("app.py",), write_paths=("app.py",), forbidden_paths=(),
        ),
    )
    assert any(message.get("isError") is True for message in result)
    assert forbidden not in fingerprinted
    assert not any("PRIVATE_MARKER=secret" in message.get("content", "") for message in result)
    assert forbidden.read_text(encoding="utf-8") == "PRIVATE_MARKER=secret\n"
