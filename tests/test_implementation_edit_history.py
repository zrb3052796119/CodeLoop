"""Current source remains reconstructable after disjoint edits of one file."""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_execution import ImplementationSpec
from minicode.implementation_recovery import (
    project_implementation_progress_messages,
    project_recovery_messages,
)
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage

FIRST = "def first():\n" + "    # Preserve this independent function's current implementation.\n" * 40 + "    return 'first-old'\n"
FIRST_EDITED = FIRST.replace("'first-old'", "'first-v2'")
SECOND = "def second():\n    return 'second-old'\n"
SECOND_EDITED = SECOND.replace("'second-old'", "'second-v2'")


def pair(identity, name, values, output, *, error=False, turn=None):
    return [{"role": "assistant_tool_call", "toolUseId": identity, "toolName": name,
             "assistantTurnId": turn or "turn-" + identity, "input": values},
            {"role": "tool_result", "toolUseId": identity, "toolName": name,
             "isError": error, "content": output}]


def edits():
    return [*pair("first", "edit_file", {"path": "app.py", "old": FIRST, "new": FIRST_EDITED}, "Applied first edit"),
            *pair("second", "edit_file", {"path": "app.py", "old": SECOND, "new": SECOND_EDITED}, "Applied second edit")]


def full_read(tmp_path, *, path="app.py", identity="current", values=None):
    values = values or {"path": path}
    result = read_file_tool.run(read_file_tool.validator(values), ToolContext(cwd=str(tmp_path)))
    assert result.ok
    return pair(identity, "read_file", values, result.output)


def assert_paired(messages):
    calls = [m["toolUseId"] for m in messages if m.get("role") == "assistant_tool_call"]
    results = [m["toolUseId"] for m in messages if m.get("role") == "tool_result"]
    assert sorted(calls) == sorted(results) and len(calls) == len(set(calls))


@pytest.mark.parametrize("projection", ["progress", "recovery"])
def test_disjoint_later_edit_cannot_retire_current_anchor(projection):
    messages = edits()
    original = deepcopy(messages)
    if projection == "progress":
        projected = project_implementation_progress_messages(messages, changed_paths=frozenset({"app.py"}))
    else:
        projected = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert messages == original
    assert projected == messages, "Another region's edit cannot supersede this current anchor"
    assert_paired(projected)


def test_complete_independent_current_read_can_replace_whole_edit_turn(tmp_path):
    (tmp_path / "app.py").write_text(FIRST_EDITED + "\n" + SECOND_EDITED)
    messages = edits() + full_read(tmp_path)
    original = deepcopy(messages)
    projected = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert messages == original
    assert any(m.get("_implementation_recovery_summary") for m in projected)
    assert projected[-2:] == messages[-2:], "The complete replacement source must remain visible"
    assert FIRST_EDITED in projected[-1]["content"] and SECOND_EDITED in projected[-1]["content"]
    assert_paired(projected)
    assert project_recovery_messages(projected, changed_paths=frozenset({"app.py"})) == projected


@pytest.mark.parametrize("mode", ["partial", "line-mode", "failed", "other-path", "same-turn",
                                  "duplicate", "unpaired", "stub", "wrong-size", "wrong-path",
                                  "later-partial", "later-grep", "authority"])
def test_incomplete_or_unreliable_read_never_retires_edit_history(tmp_path, mode):
    source = FIRST_EDITED + "\n" + SECOND_EDITED
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "other.py").write_text(source)
    messages = edits()
    if mode == "partial":
        read = full_read(tmp_path, values={"path": "app.py", "limit": 100})
    elif mode == "line-mode":
        read = full_read(tmp_path, values={"path": "app.py", "start_line": 1, "end_line": 200})
    elif mode == "other-path":
        read = full_read(tmp_path, path="other.py")
    else:
        read = full_read(tmp_path)
    if mode == "failed":
        read[-1]["isError"] = True
    elif mode == "same-turn":
        read[0]["assistantTurnId"] = messages[0]["assistantTurnId"]
    elif mode == "unpaired":
        read.pop(0)
    elif mode == "stub":
        read[-1]["content"] = "[Contents unchanged; see an earlier result]"
    elif mode == "wrong-size":
        read[-1]["content"] += "not the declared source"
    elif mode == "wrong-path":
        read[-1]["content"] = read[-1]["content"].replace("FILE: app.py", "FILE: other.py", 1)
    elif mode == "authority":
        messages[1]["_must_deliver"] = True
    messages += read
    if mode == "duplicate":
        messages += deepcopy(read)
    if mode == "later-partial":
        messages += full_read(tmp_path, identity="later", values={"path": "app.py", "offset": 100, "limit": 100})
    elif mode == "later-grep":
        messages += pair("later", "grep_files", {"path": "app.py", "pattern": "first"}, "matched one line")
    # Make an obsolete full read eligible for the late read-window projection;
    # it must never retire edits before it itself leaves the final request.
    for index in range(6):
        messages += pair(f"check-{index}", "executor_check", {"check": 1}, "check observed")
    original = deepcopy(messages)
    projected = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert messages == original
    assert any(m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "first" for m in projected)
    assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "first" for m in projected)


def test_complete_unicode_read_counts_characters_not_bytes(tmp_path):
    source = FIRST_EDITED + "\n" + SECOND_EDITED + "# 保留中文与完整源码\n"
    (tmp_path / "app.py").write_text(source)
    messages = edits() + full_read(tmp_path)
    projected = project_recovery_messages(messages)
    assert any(m.get("_implementation_recovery_summary") for m in projected)
    assert projected[-1]["content"].endswith(source)


def exact_chain():
    final = FIRST_EDITED.replace("first-v2", "first-v3")
    return [*pair("first", "edit_file", {"path": "app.py", "old": FIRST, "new": FIRST_EDITED}, "Applied first edit"),
            *pair("replacement", "edit_file", {"path": "app.py", "old": FIRST_EDITED, "new": final}, "Applied exact replacement")]


def test_consecutive_exact_whole_block_chain_keeps_its_visible_endpoint():
    messages = exact_chain()
    previous = messages[-2]["input"]["new"]
    messages += pair("endpoint", "edit_file", {"path": "app.py", "old": previous,
                     "new": previous.replace("first-v3", "first-v4")}, "Applied final replacement")
    original = deepcopy(messages)
    projected = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert messages == original
    assert len(projected) == 4
    assert all(m.get("_implementation_recovery_summary") for m in projected[:2])
    assert "Applied first edit" in projected[0]["content"]
    assert "Applied exact replacement" in projected[1]["content"]
    assert projected[-2:] == messages[-2:]
    assert "first-v4" in projected[-2]["input"]["new"]
    assert_paired(projected)
    assert project_recovery_messages(projected, changed_paths=frozenset({"app.py"})) == projected


@pytest.mark.parametrize("mode", ["fuzzy-first", "fuzzy-later", "replace_all", "replaceAll",
                                  "search-alias", "replace-alias", "empty", "crlf",
                                  "same-turn", "failed", "duplicate", "unpaired",
                                  "partial-block", "different-path", "mixed-turn",
                                  "read-barrier", "write-alias-barrier", "opaque-barrier",
                                  "unpaired-result-barrier"])
def test_exact_replacement_requires_unambiguous_complete_uninterrupted_edits(mode):
    messages = exact_chain()
    later = messages[2]
    if mode == "fuzzy-first":
        messages[0]["input"]["fuzzy"] = True
    elif mode == "fuzzy-later":
        later["input"]["fuzzy"] = True
    elif mode in {"replace_all", "replaceAll"}:
        later["input"][mode] = True
    elif mode == "search-alias":
        later["input"]["search"] = "actually another region"
    elif mode == "replace-alias":
        messages[0]["input"]["replace"] = "actually a different inserted block"
    elif mode == "empty":
        messages[0]["input"]["new"] = later["input"]["old"] = ""
    elif mode == "crlf":
        messages[0]["input"]["new"] = later["input"]["old"] = FIRST_EDITED.replace("\n", "\r\n")
    elif mode == "same-turn":
        later["assistantTurnId"] = messages[0]["assistantTurnId"]
    elif mode == "failed":
        messages[3]["isError"] = True
    elif mode == "duplicate":
        messages += deepcopy(messages[2:])
    elif mode == "unpaired":
        messages.pop()
    elif mode == "partial-block":
        later["input"]["old"] = "return 'first-v2'"
    elif mode == "different-path":
        later["input"]["path"] = "other.py"
    elif mode == "mixed-turn":
        messages += pair("mixed", "executor_check", {"check": 1}, "checked", turn=later["assistantTurnId"])
    elif mode == "unpaired-result-barrier":
        messages[2:2] = [{"role": "tool_result", "toolUseId": "unknown", "toolName": "write_file",
                          "content": "Applied unknown write", "isError": False}]
    else:
        tool = {"read-barrier": "read_file", "write-alias-barrier": "write_file",
                "opaque-barrier": "shell"}[mode]
        messages[2:2] = pair("intervening", tool, {"path": "./app.py"}, "intervening operation")
    original = deepcopy(messages)
    projected = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert messages == original
    assert projected[:2] == messages[:2], "No proven whole-block replacement: preserve the earlier edit"


def test_real_loop_uses_first_edits_current_anchor_after_disjoint_second_edit(tmp_path):
    target = tmp_path / "app.py"
    target.write_text(FIRST + "\n" + SECOND)
    (tmp_path / "test_public.py").write_text(
        "from pathlib import Path\ndef test_both_functions():\n"
        "    ns = {}\n    exec(Path('app.py').read_text(), ns)\n"
        "    assert ns['first']() == 'first-final'\n    assert ns['second']() == 'second-v2'\n")
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    seen, costs, check_exits = [], [], []

    def check(_check, context):
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_public.py"], cwd=context.cwd,
            capture_output=True, text=True, check=False, timeout=30,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        check_exits.append(result.returncode)
        return {"checkId": "public", "status": "passed" if result.returncode == 0 else "failed",
                "stdout": result.stdout, "stderr": result.stderr}

    capability = ExecutorCheckCapability((VerificationCheck("public", "tests", ("pytest", "-q", "test_public.py"), 30),), check)
    registry = capability.bind_registry(ToolRegistry([edit_file_tool, read_file_tool]))

    class Model:
        def next(self, messages, tools_override=None, **_kwargs):
            seen.append(deepcopy(messages))
            number = len(seen)
            if number == 1:
                actions = [{"id": "original", "toolName": "read_file", "input": {"path": "app.py"}}]
            elif number == 2:
                actions = [{"id": "first-edit", "toolName": "edit_file", "input": {"path": "app.py", "old": FIRST, "new": FIRST_EDITED}}]
            elif number == 3:
                actions = [{"id": "second-edit", "toolName": "edit_file", "input": {"path": "app.py", "old": SECOND, "new": SECOND_EDITED}}]
            elif number == 4:
                retained = next((m for m in messages if m.get("role") == "assistant_tool_call"
                                 and m.get("toolUseId") == "first-edit"), None)
                if retained:
                    anchor = retained["input"]["new"]
                else:
                    # Model choices depend only on the actual view. On the
                    # buggy projection, its only retained source is stale;
                    # this produces a real anchor failure, not an assertion
                    # or fabricated tool error inside the model fixture.
                    read = next(m["content"] for m in messages if m.get("role") == "tool_result"
                                and m.get("toolUseId") == "original")
                    anchor = read[read.index("def first():"):read.index("def second():")].rstrip("\n") + "\n"
                replacement = anchor.replace("'first-v2'", "'first-final'").replace("'first-old'", "'first-final'")
                actions = [{"id": "third-edit", "toolName": "edit_file", "input": {"path": "app.py", "old": anchor, "new": replacement}}]
            else:
                assert number == 5
                actions = []
            tools = tools_override or registry
            schemas = [{"name": t.name, "description": t.description, "parameters": t.input_schema} for t in tools.list()]
            inp = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            out = estimate_tokens(json.dumps(actions) if actions else "<final>Checked.")
            costs.append(inp + out)
            usage = ModelUsage(input_tokens=inp, output_tokens=out, source="estimated")
            return AgentStep(type="tool_calls", calls=actions, usage=usage) if actions else AgentStep(
                type="assistant", content="<final>Checked.", usage=usage)

    try:
        canonical = run_agent_turn(model=Model(), tools=registry,
            messages=[{"role": "user", "content": "Update both functions, then finish first() using its current source and run tests."}],
            cwd=str(tmp_path), implementation_task=True, enable_work_chain=False, max_steps=20,
            agent_budget=budget, deadline_monotonic=time.monotonic() + 1800,
            implementation_spec=ImplementationSpec(("app.py",), (), "Update both functions"))
    finally:
        capability.close()
    third = next(m for m in canonical if m.get("role") == "tool_result" and m.get("toolUseId") == "third-edit")
    print(json.dumps({"thirdEditFailed": third.get("isError"), "checkExits": check_exits,
                      "usedTokens": budget.snapshot().used_total_tokens, "usageSource": "estimated"}))
    assert third.get("isError") is False, "A non-overlapping edit retired the current anchor and left only stale source"
    assert target.read_text() == FIRST_EDITED.replace("'first-v2'", "'first-final'") + "\n" + SECOND_EDITED
    assert check_exits[-1] == 0
    assert len(seen) == 5 and budget.snapshot().used_model_calls == 5
    assert budget.snapshot().used_total_tokens == sum(costs)
    assert budget.snapshot().limit_total_tokens == 500000 and budget.snapshot().limit_model_calls == 40
    assert next(m for m in canonical if m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "first-edit")["input"]["new"] == FIRST_EDITED
    assert any(m.get("role") == "assistant_tool_call" and m.get("toolUseId") == "first-edit" for m in seen[3])
