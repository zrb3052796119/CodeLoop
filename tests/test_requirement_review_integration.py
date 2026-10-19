"""Real independent probe retention across the same implementation review."""

import json
from pathlib import Path
import sys

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep


def test_failed_semantic_probe_blocks_approval_and_survives_candidate_repair(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner

    initial = ("def hold(available, amount):\n    return available - amount\n\n"
               "def release(available, amount):\n    return available\n")
    (tmp_path / "stock.py").write_text(initial)
    (tmp_path / "test_requested.py").write_text(
        "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n")
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({"cwd": Path(cwd), "argv": list(argv)})
            return argv

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
                            explicit_trusted_executables={"python3": sys.executable}))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    spec = ImplementationSpec(("stock.py", "test_requested.py"), (
        VerificationCheck("public", "tests",
                          ("python3", "-m", "pytest", "-q", "test_requested.py"), 20),
    ), "1. hold subtracts amount from available.\n2. release adds amount to available.")
    context = ToolContext(cwd=str(tmp_path))
    checks = direct_verification_capability(spec)
    reviewer = ImplementationCompletionReview(spec)
    ids = [row["id"] for row in reviewer.requirements]
    requests = []
    seen_probe = []

    def call(name, values):
        return AgentStep(type="tool_calls", calls=[
            {"id": "review-" + str(len(requests)), "toolName": name, "input": values}])

    def verdict(status):
        return {"status": status, "requirements": [
            {"id": identity, "status": "covered" if status == "approved" else "missing",
             "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
             "probeIds": seen_probe if status == "approved" else [],
             "rationale": ("Real hold/release assertions passed on this candidate." if status == "approved"
                           else "release is still a no-op; its requested addition is absent.")}
            for identity in ids], "findings": [] if status == "approved" else [
                "release(7, 3) returns 7 instead of adding the amount."]}

    class ReviewModel:
        def next(self, messages, **_kwargs):
            requests.append(list(messages))
            number = len(requests)
            if number == 1:
                return call("review_test", {"purpose": "acceptance", "criterionIds": ids,
                    "scenario": "Both original operations are checked directly.",
                    "code": "from stock import hold, release\ndef test_both():\n"
                            "    assert hold(10, 3) == 7\n    assert release(7, 3) == 10\n"})
            if number == 2:
                result = next(m for m in reversed(messages) if m.get("toolName") == "review_test")
                assert result["isError"]
                receipt = json.JSONDecoder().raw_decode(result["content"])[0]
                seen_probe.append(receipt["probeId"])
                assert receipt["receipt"]["status"] == "failed", receipt
                return call("completion_review_submit", verdict("approved"))
            if number == 3:
                result = next(m for m in reversed(messages) if m.get("toolName") == "completion_review_submit")
                assert result["isError"], "failed independent evidence must reject a claimed approval"
                assert "coverage" in result["content"]
                return call("completion_review_submit", verdict("changes_required"))
            assert number == 4, "the repaired candidate needs no redefinition of retained probes"
            prompt = next(json.loads(m["content"]) for m in messages
                          if m.get("role") == "user" and '"retainedProbes"' in m.get("content", ""))
            assert prompt["retainedProbes"] == [
                {"probeId": seen_probe[0], "criterionIds": ids, "status": "passed"}]
            return call("completion_review_submit", verdict("approved"))

    model = ReviewModel()
    kwargs = dict(model=model, tools=ToolRegistry([read_file_tool]), context=context,
                  agent_budget=None, event_sink=None, cancellation_token=None, deadline_monotonic=None)
    try:
        first_checks = checks.ensure_completion_checks(context)
        assert first_checks["status"] == "passed", "legacy/self-authored check cannot detect release"
        first = reviewer.assess(**kwargs, checks_receipt=first_checks)
        assert first["status"] == "changes_required"
        assert len(requests) == 3
        assert (tmp_path / "stock.py").read_text() == initial, "review itself cannot implement"
        (tmp_path / "stock.py").write_text(initial.replace(
            "def release(available, amount):\n    return available",
            "def release(available, amount):\n    return available + amount"))
        second_checks = checks.ensure_completion_checks(context)
        assert second_checks["status"] == "passed"
        assert first_checks["patchDigest"] != second_checks["patchDigest"]
        second = reviewer.assess(**kwargs, checks_receipt=second_checks)
        assert second["status"] == "approved", second
        assert len(requests) == 4
        states = reviewer.state
        assert len(states["probes"]) == 1
        assert len(states["receipts"]) == 2
        assert [r["receipt"]["status"] for r in states["receipts"]] == ["failed", "passed"]
        assert {r["candidateDigest"] for r in states["receipts"]} == {
            first_checks["patchDigest"], second_checks["patchDigest"]}
        before = len(requests)
        assert reviewer.assess(**kwargs, checks_receipt=second_checks) == second
        assert len(requests) == before, "unchanged approved evidence is reusable without a new model call"
    finally:
        checks.close()
    assert len(commands) == 4, "two real public executions and two independent probe executions"
    assert all(c["cwd"] != tmp_path and not c["cwd"].exists() for c in commands)
