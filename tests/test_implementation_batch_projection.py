"""Retire delivered check previews, preserving host provenance and latest failure."""

from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from minicode.implementation_recovery import project_implementation_progress_messages


MARKER = "=== IMPLEMENTATION BATCH CHECK FEEDBACK ===\n"
ARGV = ["pytest", "-q", "test_public.py"]


def receipt(identity="public", *, patch="a", sequence=1, status="failed", argv=None):
    argv = argv or ARGV
    return {"checkId": identity, "argv": argv, "patchDigest": "sha256:" + patch * 64,
            "status": status, "observationSequence": sequence,
            "verification": "implementation_feedback", "writeback": "pending",
            "evidence": {"checkId": identity, "argv": argv, "status": status,
                         "commandDigest": "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest(),
                         "exitCode": 0 if status == "passed" else 1,
                         "stdout": {"preview": f"actual {patch} assertion\n" * 300,
                                    "previewBase64": "YWJj" * 100, "byteCount": 9000,
                                    "digest": "sha256:" + "e" * 64, "truncated": True},
                         "stderr": {"preview": "", "byteCount": 0,
                                    "digest": "sha256:" + "f" * 64, "truncated": False}}}


def batch(identity, *, turn=None, patch="a", sequence=1, status="failed", argv=None):
    check = receipt(patch=patch, sequence=sequence, status=status, argv=argv)
    doc = {"batchVersion": 1, "batchId": hashlib.md5(identity.encode()).hexdigest(),
           "verification": "implementation_feedback", "status": status,
           "changedCallIds": [identity], "uncertainWriteCallIds": [],
           "patchDigest": check["patchDigest"], "checks": [check]}
    return [{"role": "assistant_tool_call", "assistantTurnId": turn or "turn-" + identity,
             "toolUseId": identity, "toolName": "edit_file",
             "input": {"path": "candidate.py", "old": identity + "-old", "new": identity + "-new"}},
            {"role": "tool_result", "toolUseId": identity, "toolName": "edit_file",
             "isError": False, "content": "Applied changes"},
            {"role": "user", "_implementation_batch_feedback": True,
             "content": MARKER + json.dumps(doc, sort_keys=True)}]


def project(messages):
    return project_implementation_progress_messages(messages, changed_paths=frozenset({"candidate.py"}))


def document(message):
    return json.loads(message["content"].removeprefix(MARKER))


def test_delivered_batch_previews_retire_without_changing_results_or_canonical():
    old, latest = batch("old", sequence=80), batch("latest", patch="b", sequence=1)
    messages = old + latest
    original = deepcopy(messages)
    projected = project(messages)
    assert messages == original
    assert projected[:2] == old[:2] and projected[3:] == latest
    before, after = document(old[2]), document(projected[2])
    assert "preview" not in after["checks"][0]["evidence"]["stdout"]
    assert after["checks"][0].pop("outputProjection")
    for name in ("stdout", "stderr"):
        before["checks"][0]["evidence"][name].pop("preview", None)
        before["checks"][0]["evidence"][name].pop("previewBase64", None)
    assert after == before
    assert project(projected) == projected


@pytest.mark.parametrize("mutation", ["marker-only", "wrong-role", "truthy-host", "prefix", "suffix",
    "unknown-call", "duplicate-call", "duplicate-result", "duplicate-batch", "result-after-feedback",
    "wrong-tool", "failed-changed-call", "wrong-patch", "wrong-command", "wrong-check", "wrong-status",
    "missing-sequence", "invalid-sequence", "duplicate-check", "must-deliver", "call-must-deliver",
    "result-must-deliver", "same-turn"])
def test_ambiguous_unbound_or_undelivered_batches_remain_exact(mutation):
    old, latest = batch("old"), batch("latest", patch="b", sequence=2)
    doc = document(old[2])
    if mutation == "marker-only":
        old[2].pop("_implementation_batch_feedback")
    elif mutation == "wrong-role":
        old[2]["role"] = "assistant"
    elif mutation == "truthy-host":
        old[2]["_implementation_batch_feedback"] = 1
    elif mutation == "prefix":
        old[2]["content"] = "model text " + old[2]["content"]
    elif mutation == "suffix":
        old[2]["content"] += "\nextra unbound text"
    elif mutation == "unknown-call":
        doc["changedCallIds"] = ["unknown"]
    elif mutation == "duplicate-call":
        old.insert(0, deepcopy(old[0]))
    elif mutation == "duplicate-result":
        old.insert(1, deepcopy(old[1]))
    elif mutation == "duplicate-batch":
        latest_doc = document(latest[2])
        latest_doc["batchId"] = doc["batchId"]
        latest[2]["content"] = MARKER + json.dumps(latest_doc)
    elif mutation == "result-after-feedback":
        old[1], old[2] = old[2], old[1]
    elif mutation == "wrong-tool":
        old[1]["toolName"] = "read_file"
    elif mutation == "failed-changed-call":
        old[1]["isError"] = True
    elif mutation == "wrong-patch":
        doc["checks"][0]["patchDigest"] = "sha256:" + "c" * 64
    elif mutation == "wrong-command":
        doc["checks"][0]["evidence"]["commandDigest"] = "sha256:" + "c" * 64
    elif mutation == "wrong-check":
        doc["checks"][0]["evidence"]["checkId"] = "other"
    elif mutation == "wrong-status":
        doc["checks"][0]["evidence"]["status"] = "passed"
    elif mutation == "missing-sequence":
        doc["checks"][0].pop("observationSequence")
    elif mutation == "invalid-sequence":
        doc["checks"][0]["observationSequence"] = True
    elif mutation == "duplicate-check":
        doc["checks"].append(deepcopy(doc["checks"][0]))
    elif mutation == "must-deliver":
        old[2]["_must_deliver"] = True
    elif mutation == "call-must-deliver":
        old[0]["_must_deliver"] = True
    elif mutation == "result-must-deliver":
        old[1]["_must_deliver"] = True
    elif mutation == "same-turn":
        latest[0]["assistantTurnId"] = old[0]["assistantTurnId"]
    if mutation in {"unknown-call", "wrong-patch", "wrong-command", "wrong-check", "wrong-status",
                    "missing-sequence", "invalid-sequence", "duplicate-check"}:
        old[2]["content"] = MARKER + json.dumps(doc)
    messages = old + latest
    assert project(messages) == messages


def test_different_commands_and_latest_failed_patch_remain_complete():
    old = batch("old", patch="a", sequence=90)
    other = batch("other", patch="b", sequence=1, argv=["pytest", "-q", "test_other.py"])
    assert project(old + other) == old + other
    latest = batch("latest", patch="c", sequence=1)
    projected = project(old + other + latest)
    assert projected[3:] == other + latest
    assert "preview" not in document(projected[2])["checks"][0]["evidence"]["stdout"]


def test_invalid_later_batch_cannot_supersede_valid_old_failure():
    old, newest = batch("old"), batch("newest", patch="b")
    doc = document(newest[2])
    doc["checks"][0]["patchDigest"] = "sha256:" + "c" * 64
    newest[2]["content"] = MARKER + json.dumps(doc)
    assert project(old + newest) == old + newest


def test_batch_and_explicit_check_share_latest_observation_without_sequence_reset_loss():
    old, newest = batch("old", sequence=80), batch("newest", patch="b", sequence=1)
    doc = document(newest[2])
    newest = [{**newest[0], "toolName": "executor_check", "input": {"check": 1}},
              {**newest[1], "toolName": "executor_check", "isError": True,
               "_implementation_checks_failed": True, "content": json.dumps(doc["checks"][0])}]
    projected = project(old + newest)
    assert "preview" not in document(projected[2])["checks"][0]["evidence"]["stdout"]
    assert projected[3:] == newest


def test_uncertain_write_still_preserves_error_while_retiring_only_old_check_preview():
    old, newest = batch("old"), batch("newest", patch="b")
    doc = document(old[2])
    doc["uncertainWriteCallIds"], doc["changedCallIds"] = doc["changedCallIds"], []
    old[2]["content"] = MARKER + json.dumps(doc)
    old[1].update(isError=True, content="Write crashed after changing bytes; result is uncertain")
    projected = project(old + newest)
    assert projected[:2] == old[:2] and projected[3:] == newest
    assert "preview" not in document(projected[2])["checks"][0]["evidence"]["stdout"]


def test_real_loop_retires_delivered_batch_preview_and_keeps_latest_failure(tmp_path, monkeypatch):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn
    from minicode.context_manager import estimate_message_tokens, estimate_tokens
    from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
    from minicode.implementation_execution import ImplementationSpec
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.permissions import PermissionManager
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.types import AgentStep, ModelUsage
    import minicode.workflow_runner as runner

    def approve(_self, *, checks_receipt, **_kwargs):
        # Keep this test about delivered batch projections and their measured
        # request cost. Independent semantic review has its own integration
        # test; current candidate checks below still execute for every batch.
        assert checks_receipt["status"] == "passed"
        return {"status": "approved", "patchDigest": checks_receipt["patchDigest"],
                "requirements": [], "findings": []}

    monkeypatch.setattr(ImplementationCompletionReview, "assess", approve)
    (tmp_path / "candidate.py").write_text("value = 0\nversion = 0\n")
    (tmp_path / "test_public.py").write_text(
        "from pathlib import Path\ndef test_value():\n"
        "    print('detailed public assertion context\\n' * 200)\n"
        "    assert Path('candidate.py').read_text() == 'value = 3\\nversion = 3\\n'\n")
    exits, views, costs = [], [], []

    def command(argv, **kwargs):
        kwargs["env"] = {**os.environ, **kwargs.get("env", {}),
                         "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
        result = subprocess.run([sys.executable, "-m", *argv], **kwargs)
        exits.append(result.returncode)
        return result

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context", lambda *_a, **_k:
                        DeterministicVerifier(command_runner=command))
    registry = ToolRegistry([edit_file_tool])

    class Model:
        def next(self, messages, tools_override=None, **_kwargs):
            views.append(deepcopy(messages))
            number = len(views)
            calls = ([{"id": f"write-{number}-{name}", "toolName": "edit_file", "input": {
                "path": "candidate.py", "old": f"{name} = {number - 1}", "new": f"{name} = {number}"}}
                for name in ("value", "version")]
                if number <= 3 else [])
            tools = tools_override or registry
            schemas = [{"name": t.name, "description": t.description, "parameters": t.input_schema} for t in tools.list()]
            inp = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            out = estimate_tokens(json.dumps(calls) if calls else "<final>Checked.")
            costs.append(inp + out)
            usage = ModelUsage(input_tokens=inp, output_tokens=out, source="estimated")
            return AgentStep(type="tool_calls", calls=calls, usage=usage) if calls else AgentStep(
                type="assistant", content="<final>Checked.", usage=usage)

    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    canonical = run_agent_turn(model=Model(), tools=registry,
        messages=[{"role": "user", "content": "Set the candidate value to 3 and verify the existing public test."}],
        cwd=str(tmp_path), implementation_task=True, implementation_analysis_complete=True,
        implementation_spec=ImplementationSpec(("candidate.py", "test_public.py"),
            (VerificationCheck("public", "tests", tuple(ARGV), 30),), "Set value to 3"),
        agent_budget=budget, deadline_monotonic=time.monotonic() + 1800, enable_work_chain=False, max_steps=20)
    assert exits == [1, 1, 0]
    assert len(views) == budget.snapshot().used_model_calls == 4
    assert budget.snapshot().used_total_tokens == sum(costs)
    assert budget.snapshot().limit_total_tokens == 500000 and budget.snapshot().limit_model_calls == 40
    assert (tmp_path / "candidate.py").read_text() == "value = 3\nversion = 3\n"
    first_seen = [m for m in views[1] if m.get("_implementation_batch_feedback")]
    later_seen = [m for m in views[2] if m.get("_implementation_batch_feedback")]
    assert len(first_seen) == 1 and len(later_seen) == 2
    assert "assert Path" in document(first_seen[0])["checks"][0]["evidence"]["stdout"]["preview"]
    assert "preview" not in document(later_seen[0])["checks"][0]["evidence"]["stdout"], "Already-delivered batch preview is recharged in every request"
    assert "assert Path" in document(later_seen[1])["checks"][0]["evidence"]["stdout"]["preview"]
    all_feedback = [m for m in canonical if m.get("_implementation_batch_feedback")]
    assert len(all_feedback) == 3 and all_feedback[0] == first_seen[0]
    assert all("preview" in document(m)["checks"][0]["evidence"]["stdout"] for m in all_feedback)
    print(json.dumps({"checkExits": exits, "requests": len(views), "estimatedTokens": sum(costs),
                      "retiredPreviewChars": len(first_seen[0]["content"]) - len(later_seen[0]["content"])}))
