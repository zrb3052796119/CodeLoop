"""Ordinary task results retain their host-owned author and survive sibling activity."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox, SubagentMailboxError
from minicode.subagent_result import extract_subagent_result
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools.subagent_notes import (
    subagent_note_read_tool,
    subagent_note_write_tool,
)


def _report(summary: str) -> str:
    return json.dumps({
        "resultVersion": 1,
        "summary": summary,
        "files": [],
        "risks": [],
        "verification": {"status": "not_run", "checks": []},
    })


def _result_key(kwargs) -> str:
    match = re.search(
        r"result mailbox key `([^`]+)`", kwargs["messages"][0]["content"],
    )
    assert match is not None
    return match.group(1)


def _note_context(kwargs) -> ToolContext:
    return ToolContext(
        cwd=kwargs["cwd"], _agent_depth=1,
        _subagent_mailbox=kwargs["subagent_mailbox"],
    )


def _write(context: ToolContext, key: str, content: str):
    return subagent_note_write_tool.run({"key": key, "content": content}, context)


def _finish(kwargs, summary="finished"):
    kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
    return [*kwargs["messages"], {"role": "assistant", "content": summary}]


@pytest.fixture
def task_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Keep the actual task setup, filtered registry, note tools, projection
    # and closing path. Only optional prompt/model construction avoids providers.
    monkeypatch.setattr(
        task_module, "_build_sub_agent_system_prompt",
        lambda **kwargs: (kwargs["agent_def"]["system_prompt"], None),
    )
    monkeypatch.setattr(
        "minicode.subagent_model_routing.create_subagent_model_adapter",
        lambda *args, **kwargs: SimpleNamespace(runtime={"model": "fake"}),
    )
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *args, **kwargs: None,
    )
    mailbox = SubagentMailbox()
    context = ToolContext(
        cwd=str(tmp_path), _runtime={"model": "fake"}, _subagent_mailbox=mailbox,
    )

    def dispatch(prompt="Victim survey"):
        return task_module.task_tool.run({
            "description": "ownership probe", "prompt": prompt,
            "agent_type": "explore",
        }, context)

    return mailbox, dispatch


def test_sibling_and_raw_mailbox_cannot_replace_another_task_result(
    monkeypatch: pytest.MonkeyPatch, task_runtime,
) -> None:
    mailbox, dispatch = task_runtime
    victim_keys: list[str] = []
    sibling_attempts = []
    raw_rejected = []

    def run_turn(**kwargs):
        own_key = _result_key(kwargs)
        child = _note_context(kwargs)
        if kwargs["messages"][-1]["content"] == "Sibling survey":
            for key in victim_keys:
                sibling_attempts.append(_write(child, key, _report("foreign findings")))
            shared = subagent_note_read_tool.run({"key": "shared_handoff"}, child)
            assert shared.ok and "first finding" in shared.output
            assert _write(child, "shared_handoff", "revised finding").ok
            assert _write(child, own_key, _report("sibling findings")).ok
        else:
            victim_keys.extend((own_key, own_key + "_detail"))
            assert _write(child, own_key, _report("victim findings")).ok
            assert _write(child, own_key + "_detail", "complete victim list").ok
            assert _write(child, "shared_handoff", "first finding").ok
            sibling_result = dispatch("Sibling survey")
            assert sibling_result.ok, sibling_result.output
            for key in victim_keys:
                # Knowing and claiming the original author is not a capability.
                author = mailbox.read(key).author
                try:
                    mailbox.write(key, _report("forged host write"), author=author)
                except SubagentMailboxError:
                    raw_rejected.append(True)
                else:
                    raw_rejected.append(False)
            shared = subagent_note_read_tool.run({"key": "shared_handoff"}, child)
            assert shared.ok and "revised finding" in shared.output
        return _finish(kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = dispatch()
    projected = extract_subagent_result(result.output)
    assert result.ok
    assert len(sibling_attempts) == 2 and all(not item.ok for item in sibling_attempts)
    assert raw_rejected == [True, True]
    assert projected is not None
    assert projected["contractStatus"] == "reported"
    assert projected["summary"] == "victim findings"
    assert mailbox.read(victim_keys[0]).author == projected["subagentId"]
    assert mailbox.read(victim_keys[1]).content == "complete victim list"


def test_finished_task_seals_results_and_revokes_late_shared_note_writes(
    monkeypatch: pytest.MonkeyPatch, task_runtime,
) -> None:
    mailbox, dispatch = task_runtime
    child_contexts = []
    keys = []

    def run_turn(**kwargs):
        child = _note_context(kwargs)
        child_contexts.append(child)
        key = _result_key(kwargs)
        keys.append(key)
        assert _write(child, key, _report("finished findings")).ok
        assert _write(child, key + "_detail", "finished detail").ok
        return _finish(kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = dispatch()
    assert result.ok, result.output
    for key in (keys[0], keys[0] + "_detail", "late_shared_note"):
        late = _write(child_contexts[0], key, "late overwrite")
        assert not late.ok
    assert mailbox.read("late_shared_note") is None
    assert mailbox.read(keys[0] + "_detail").content == "finished detail"


def test_shared_note_churn_cannot_evict_a_task_result_or_detail(
    monkeypatch: pytest.MonkeyPatch, task_runtime,
) -> None:
    mailbox, dispatch = task_runtime
    keys = []

    def run_turn(**kwargs):
        key = _result_key(kwargs)
        keys.append(key)
        child = _note_context(kwargs)
        assert _write(child, key, _report("retained findings")).ok
        assert _write(child, key + "_detail", "retained full list").ok
        for index in range(120):
            mailbox.write(f"shared_{index}", "normal collaboration", author="sibling")
        return _finish(kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = dispatch()
    assert mailbox.read(keys[0]) is not None
    assert mailbox.read(keys[0] + "_detail") is not None
    projected = extract_subagent_result(result.output)
    assert result.ok and projected is not None
    assert projected["contractStatus"] == "reported"
    assert projected["summary"] == "retained findings"
    assert mailbox.read(keys[0] + "_detail").content == "retained full list"
    assert len(mailbox.snapshot()) <= 100


def test_owner_can_correct_its_malformed_note_before_returning(
    monkeypatch: pytest.MonkeyPatch, task_runtime,
) -> None:
    mailbox, dispatch = task_runtime
    keys = []

    def run_turn(**kwargs):
        key = _result_key(kwargs)
        keys.append(key)
        child = _note_context(kwargs)
        assert _write(child, key, "malformed first attempt").ok
        assert _write(child, key, _report("corrected findings")).ok
        return _finish(kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = dispatch()
    projected = extract_subagent_result(result.output)
    assert result.ok and projected is not None
    assert projected["contractStatus"] == "reported"
    assert projected["summary"] == "corrected findings"
    assert mailbox.read(keys[0]).version == 2
