"""Bounded retention of delivered source windows; fake models only."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.context_compactor import ToolResultBudgetManager
from minicode.permissions import PermissionManager
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter

SOURCE = (
    "# start\n"
    + "# surrounding code\n" * 150
    + "def answer():\n    return 0  # EXACT_EDIT_TARGET\n"
    + "# following code\n" * 150
)


def add_read(
    messages, identity, path, content=SOURCE, *, error=False, tool="read_file"
):
    messages.extend(
        [
            {
                "role": "assistant_tool_call",
                "toolUseId": identity,
                "toolName": tool,
                "input": {"path": path},
            },
            {
                "role": "tool_result",
                "toolUseId": identity,
                "toolName": tool,
                "content": content,
                "isError": error,
            },
        ]
    )


def result(messages, identity):
    return next(
        m
        for m in messages
        if m.get("role") == "tool_result" and m.get("toolUseId") == identity
    )


def surround(messages, count=8):
    for i in range(count):
        add_read(messages, f"other-{i}", f"notes/note-{i}.txt", "notes\n" + "x" * 6000)
    return messages


def project(tmp_path, messages, **kwargs):
    calls = {
        m["toolUseId"]: m["input"]["path"]
        for m in messages
        if m.get("role") == "assistant_tool_call"
    }
    eligible = {
        m["toolUseId"]
        for m in messages
        if m.get("role") == "tool_result" and not m["isError"]
    }
    return ToolResultBudgetManager(workspace=tmp_path).compact_read_history(
        messages,
        eligible_ids=eligible,
        protected_ids={"other-7"},
        source_paths=calls,
        **kwargs,
    )[0]


def test_target_window_survives_eight_other_reads_without_a_second_allowance(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    surround(messages)
    before = deepcopy(messages)
    reduced = project(tmp_path, messages)
    assert result(reduced, "target")["content"] == SOURCE
    assert result(reduced, "other-0").get("_read_history_compacted") is True
    assert messages == before
    assert not list(tmp_path.iterdir())
    reads = [m for m in reduced if m.get("role") == "tool_result"]
    assert (
        sum(
            len(m["content"])
            for m in reads[:-2]
            if not m.get("_read_history_compacted")
        )
        <= 16_000
    )


def test_only_two_recent_source_paths_and_one_complete_window_per_path(tmp_path):
    messages = []
    for identity, path in [
        ("a", "src/a.py"),
        ("b-old", "src/b.py"),
        ("c", "src/c.py"),
        ("b-new", "src/./b.py"),
    ]:
        add_read(messages, identity, path, SOURCE + identity)
    surround(messages)
    reduced = project(tmp_path, messages)
    assert result(reduced, "b-new")["content"] == SOURCE + "b-new"
    assert result(reduced, "c")["content"] == SOURCE + "c"
    assert result(reduced, "a").get("_read_history_compacted") is True
    assert result(reduced, "b-old").get("_read_history_compacted") is True
    reads = [m for m in reduced if m.get("role") == "tool_result"]
    assert (
        sum(
            len(m["content"])
            for m in reads[:-2]
            if not m.get("_read_history_compacted")
        )
        <= 16_000
    )


def test_failed_read_does_not_replace_previous_complete_source_window(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    add_read(messages, "failed", "src/app.py", "error[permission_denied]", error=True)
    surround(messages)
    reduced = project(tmp_path, messages, history_budget=6000)
    assert result(reduced, "target")["content"] == SOURCE
    assert result(reduced, "failed") == result(messages, "failed")


def test_new_large_window_retires_prior_small_anchor_even_after_shortening(tmp_path):
    messages = []
    add_read(messages, "old", "src/app.py")
    add_read(messages, "new", "src/app.py", "new source\n" + "z" * 20_000)
    surround(messages)
    reduced = project(tmp_path, messages, history_budget=2000)
    assert result(reduced, "old").get("_read_history_compacted") is True
    assert result(reduced, "new").get("_read_history_compacted") is True
    # A shortened latest result cannot make an older window authoritative again.
    reduced[1] = deepcopy(messages[1])
    again = project(tmp_path, reduced, history_budget=1000)
    assert result(again, "old").get("_read_history_compacted") is True


def test_successful_byte_change_retires_old_anchor_and_new_read_can_replace_it(
    tmp_path,
):
    messages = []
    add_read(messages, "target", "src/app.py")
    surround(messages)
    reduced = project(
        tmp_path, messages, invalidated_ids={"target"}, history_budget=1000
    )
    assert result(reduced, "target").get("_read_history_compacted") is True
    add_read(reduced, "fresh", "src/app.py", SOURCE.replace("return 0", "return 1"))
    surround(reduced)
    fresh = project(tmp_path, reduced, invalidated_ids={"target"}, history_budget=6000)
    assert result(fresh, "fresh")["content"] == SOURCE.replace("return 0", "return 1")


def test_deduplicated_receipt_does_not_displace_its_complete_source_window(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    add_read(
        messages,
        "dedup",
        "src/app.py",
        "[Read deduplicated: src/app.py]\nUse earlier result.",
    )
    surround(messages)
    reduced = project(tmp_path, messages, history_budget=6000)
    assert result(reduced, "target")["content"] == SOURCE


def test_no_new_disk_observation_or_write_authority(tmp_path, monkeypatch):
    messages = []
    add_read(messages, "target", "src/app.py")
    surround(messages)
    manager = ToolResultBudgetManager(workspace=tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("retention must not access the filesystem")

    for method in ("open", "stat", "resolve", "read_text", "write_text"):
        monkeypatch.setattr(Path, method, forbidden)
    reduced, _ = manager.compact_read_history(
        messages,
        eligible_ids={"target", *(f"other-{i}" for i in range(8))},
        protected_ids={"other-7"},
        source_paths={"target": "src/app.py"},
    )
    assert result(reduced, "target")["content"] == SOURCE
    assert "authorized to write" not in str(reduced)


def test_header_text_cannot_claim_source_identity_or_evict_real_source(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    for i in range(8):
        add_read(
            messages,
            f"other-{i}",
            f"docs/page-{i}.txt",
            "FILE: src/new.py\n" + "x" * 6000,
        )
    reduced = project(tmp_path, messages)
    assert result(reduced, "target")["content"] == SOURCE


def test_tests_and_docs_do_not_evict_production_window(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    for i in range(8):
        add_read(messages, f"other-{i}", f"tests/test_{i}.py", "# test\n" + "x" * 6000)
    reduced = project(tmp_path, messages)
    assert result(reduced, "target")["content"] == SOURCE


def test_latest_large_batch_and_nonread_receipt_are_unchanged(tmp_path):
    messages = []
    add_read(messages, "target", "src/app.py")
    add_read(
        messages, "receipt", "src/receipt.py", "AUTHORITY\n" + "a" * 20000, tool="task"
    )
    for i in range(8):
        add_read(
            messages, f"other-{i}", f"src/new{i}.py", "# full new batch\n" + "z" * 10000
        )
    protected = {f"other-{i}" for i in range(8)}
    reduced, _ = ToolResultBudgetManager(workspace=tmp_path).compact_read_history(
        messages,
        eligible_ids={"target", "receipt", *protected},
        protected_ids=protected,
        source_paths={
            "target": "src/app.py",
            "receipt": "src/receipt.py",
            **{i: f"src/{i}.py" for i in protected},
        },
        history_budget=1000,
    )
    assert result(reduced, "receipt") == result(messages, "receipt")
    assert all(
        result(reduced, identity) == result(messages, identity)
        for identity in protected
    )


class EvidenceThenEdit(ModelAdapter):
    """An edit depends on the original exact window after unrelated inspection."""

    def __init__(self, *, edit=True, change=True):
        self.step = 0
        self.edit = edit
        self.change = change

    def next(self, messages, on_stream_chunk=None):
        step = self.step
        self.step += 1
        if step == 0:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "target",
                        "toolName": "read_file",
                        "input": {"path": "app.py"},
                    }
                ],
            )
        if step <= 8:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"before-{step}",
                        "toolName": "read_file",
                        "input": {"path": f"notes/{step}.txt"},
                    }
                ],
            )
        if step == 9:
            evidence = result(messages, "target")["content"]
            assert SOURCE in evidence, (
                "target edit window disappeared after eight other reads"
            )
            if not self.edit:
                return AgentStep(
                    type="assistant", content="Read-only fixture complete."
                )
            old = next(
                line for line in evidence.splitlines() if "EXACT_EDIT_TARGET" in line
            )
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "edit",
                        "toolName": "edit_file",
                        "input": {
                            "path": "app.py",
                            "old": old,
                            "new": old.replace("return 0", "return 1")
                            if self.change
                            else old,
                        },
                    }
                ],
            )
        if step <= 17:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"after-{step}",
                        "toolName": "read_file",
                        "input": {"path": f"notes/{step - 9}.txt"},
                    }
                ],
            )
        assert (
            bool(result(messages, "target").get("_read_history_compacted"))
            is self.change
        ), "anchor invalidation must follow byte changes, not successful write requests"
        return AgentStep(
            type="assistant",
            content="Changed the requested return value; no tests were requested by this fixture.",
        )


@pytest.mark.parametrize("change", [False, True])
def test_real_loop_preserves_exact_edit_window_and_invalidates_after_actual_write(
    tmp_path, change
):
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / "notes").mkdir()
    for i in range(1, 9):
        (tmp_path / "notes" / f"{i}.txt").write_text("# background\n" + "x" * 6000)
    result_messages = run_agent_turn(
        model=EvidenceThenEdit(change=change),
        tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py so answer returns 1."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        enable_implementation_phases=False,  # isolate source-window retention under a long read history
        max_steps=22,
        permissions=PermissionManager(
            str(tmp_path), prompt=lambda _: {"decision": "allow_once"}
        ),
    )
    assert (
        f"return {int(change)}  # EXACT_EDIT_TARGET"
        in (tmp_path / "app.py").read_text()
    )
    assert result(result_messages, "edit")["isError"] is False
    assert not (tmp_path / ".mini-code-tool-results").exists()


def test_committed_child_invalidates_anchors_without_rewriting_its_receipt(tmp_path):
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / "notes").mkdir()
    for i in range(1, 9):
        (tmp_path / "notes" / f"{i}.txt").write_text("# background\n" + "x" * 6000)
    receipt = "COMMITTED CHANGE\n" + "authority evidence\n" * 700
    delivered = []

    class ChildCommitModel(EvidenceThenEdit):
        def next(self, messages, on_stream_chunk=None):
            if self.step > 9:
                assert result(messages, "commit")["content"] == receipt
            next_step = super().next(messages, on_stream_chunk)
            if self.step == 10:
                return AgentStep(
                    type="tool_calls",
                    calls=[{"id": "commit", "toolName": "task", "input": {}}],
                )
            return next_step

    def commit(_inputs, context):
        # A fixture representing the host's already finalized child commit.
        (tmp_path / "app.py").write_text(SOURCE.replace("return 0", "return 1"))
        return ToolResult(
            ok=True,
            output=receipt,
            _must_deliver=True,
            _continue_parent=True,
            _delivery_ack=lambda: delivered.append(True),
        )

    task = ToolDefinition(
        name="task",
        description="fixed committed-child fixture",
        input_schema={"type": "object"},
        validator=lambda data: data,
        run=commit,
    )
    messages = run_agent_turn(
        model=ChildCommitModel(),
        tools=ToolRegistry([read_file_tool, edit_file_tool, task]),
        messages=[{"role": "user", "content": "Fix app.py so answer returns 1."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        enable_implementation_phases=False,  # isolate authority receipt and anchor projection
        max_steps=22,
    )
    assert delivered == [True]
    assert result(messages, "commit")["content"] == receipt
    assert result(messages, "target").get("_read_history_compacted") is True


def test_two_recent_results_reduce_rolling_text_while_retaining_both_edit_windows(
    tmp_path,
):
    messages = []
    add_read(messages, "a", "src/a.py", SOURCE)
    add_read(messages, "b", "src/b.py", SOURCE.replace("answer", "other_answer"))
    for i in range(8):
        add_read(messages, f"other-{i}", f"notes/{i}.txt", "n" * 20_000)
    reduced = project(tmp_path, messages)
    old_rolling_six = project(tmp_path, messages, keep_recent=6)
    assert result(reduced, "a")["content"] == SOURCE
    assert result(reduced, "b")["content"] == SOURCE.replace("answer", "other_answer")
    assert result(reduced, "other-6") == result(messages, "other-6")
    assert result(reduced, "other-7") == result(messages, "other-7")
    assert result(reduced, "other-5").get("_read_history_compacted") is True
    assert not result(old_rolling_six, "other-5").get("_read_history_compacted")
    retained = lambda history: sum(len(m.get("content", "")) for m in history)
    assert retained(reduced) < retained(old_rolling_six) * 0.6


def test_workspace_ancestor_named_tests_does_not_exclude_source_paths(tmp_path):
    workspace = tmp_path / "tests" / "real-project"
    messages = []
    add_read(messages, "target", "src/app.py")
    surround(messages)
    reduced = project(workspace, messages, history_budget=6000)
    assert result(reduced, "target")["content"] == SOURCE
