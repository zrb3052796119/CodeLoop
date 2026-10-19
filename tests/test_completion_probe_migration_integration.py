"""Real semantic classification feeds a host queue, never direct approval."""

import json
from pathlib import Path

from minicode.agent_budget import AgentTurnBudget
from minicode.completion_probe_migration import CompletionProbeMigration
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep
from test_completion_probe_purposes import _seed_legacy
from test_implementation_completion_review import _fixture, _submit, PROBE, SOURCE


def test_independent_migration_then_real_replacement_pytest_approves_repaired_candidate(tmp_path, monkeypatch):
    import minicode.agent_loop as loop

    real_loop = loop.run_agent_turn
    legacy = _seed_legacy(tmp_path, monkeypatch)
    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, legacy))
    old_probe = legacy["probes"][0]
    before = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}
    requests = []
    budget = AgentTurnBudget()

    class MigrationModel:
        def next(self, messages, **kwargs):
            requests.append(messages)
            assert len(requests) == 1
            names = set(kwargs["tools_override"].list_all())
            assert names == {"read_file", "completion_probe_migration_read", "completion_probe_migration_submit",
                             "completion_probe_migration_record"}
            packet = next(json.loads(message["content"]) for message in messages
                if isinstance(message.get("content"), str)
                and message["content"].startswith("{")
                and "legacyProbes" in json.loads(message["content"]))
            assert packet["legacyProbes"][0]["code"] == old_probe["code"]
            assert packet["history"][0]["receipts"][0]["status"] == "passed"
            return AgentStep(type="tool_calls", calls=[{"id": "legacy-purpose", "toolName":
                "completion_probe_migration_submit", "input": {"decisions": [{
                    "probeId": old_probe["probeId"], "purpose": "diagnostic",
                    "reason": "The assertion observes the old no-op; the objective requires addition.",
                    "objectiveEvidence": [{"startLine": 4, "endLine": 4}],
                    "probeEvidence": [{"startLine": 3, "endLine": 3}],
                    "acceptanceProbes": [{"criterionIds": old_probe["criterionIds"],
                        "scenario": "Both original inventory operations work after repair", "code": PROBE}]}]}}])

    monkeypatch.setattr(loop, "run_agent_turn", real_loop)
    result = CompletionProbeMigration(review.spec).assess(legacy_state=review.state,
        model=MigrationModel(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
        context=ToolContext(cwd=str(tmp_path)), agent_budget=budget, event_sink=None,
        cancellation_token=None, deadline_monotonic=None)
    assert result["status"] == "resolved" and len(requests) == 1
    assert review.has_legacy_probes and commands == []
    assert len(review.state["receipts"]) == 1
    assert Path(result["resolution"]["reviewEvidenceRef"]).is_file()
    review.queue_legacy_resolution(result["resolution"])
    assert review.has_legacy_probes, "queueing semantic judgment is not acceptance"

    def complete(tools, child, packet):
        assert not review.has_legacy_probes
        assert len(packet["retainedProbes"]) == 1
        assert packet["retainedProbes"][0]["status"] == "passed"
        assert _submit(tools, child, packet, ids=[packet["retainedProbes"][0]["probeId"]]).ok

    try:
        accepted = assess(complete)
        assert accepted["status"] == "approved"
        assert len(commands) == 3  # public check, old diagnostic replay, replacement pytest
        state = review.state
        old_receipts = [r for r in state["receipts"] if r["probeId"] == old_probe["probeId"]]
        assert [r["receipt"]["status"] for r in old_receipts] == ["passed", "failed"]
        assert old_receipts[0]["candidateDigest"] != old_receipts[1]["candidateDigest"]
        assert state["probePurposes"][old_probe["probeId"]]["purpose"] == "diagnostic"
        acceptance_ids = {pid for pid, role in state["probePurposes"].items() if role["purpose"] == "acceptance"}
        assert acceptance_ids
        assert all(r["receipt"]["status"] == "passed" for r in accepted["probeReceipts"]
                   if r["probeId"] in acceptance_ids)
        assert state["legacyResolutions"][-1]["status"] == "activated"
        assert {name: (tmp_path / name).read_bytes() for name in before} == before
        assert (tmp_path / "stock.py").read_text() == SOURCE
    finally:
        cap.close()
