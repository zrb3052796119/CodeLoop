"""A saved pre-binding execution is audit history, never rewritten current evidence."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.review_probe_execution import derive_review_probe_check, validate_review_probe_execution_binding
from test_completion_replacement_fixture import BAD, GOOD, fixture_adjudicator
from test_implementation_completion_review import SOURCE, _fixture, _submit


_SEED_PRE_BINDING_PROCESS = r'''
import json
from pathlib import Path
import sys
import pytest

repository, candidate, state_path = map(Path, sys.argv[1:])
sys.path[:0] = [str(repository), str(repository / 'tests')]
import minicode.implementation_completion_review as completion
from minicode.types import AgentStep
from test_completion_replacement_fixture import BAD
from test_implementation_completion_review import _fixture, _probe

# Materialize the earlier host format using a genuine authorized command and
# its machine reports. No argv, node IDs, digests, or verdicts are synthesized.
def pre_binding_execution(*, probe, frozen, check, test_path, verifier,
                          read_roots, forbidden_paths, run_kwargs):
    evidence, reports = [], []
    with frozen.verification_workspace(read_roots=read_roots, forbidden_paths=forbidden_paths) as cwd:
        (Path(cwd) / test_path).write_text(probe.code)
        result = verifier.run(checks=(check,), cwd=str(cwd), **run_kwargs,
            evidence_sink=lambda _key, value: evidence.append(value),
            pytest_report_sink=reports.append)
    assert len(evidence) == len(reports) == 1
    return {'probeId': probe.probe_id, 'patchDigest': frozen.patch_digest,
            'status': result['status'], 'evidence': evidence[0], 'testReport': reports[0]}

with pytest.MonkeyPatch.context() as monkeypatch:
    monkeypatch.setattr(completion, 'run_isolated_review_probe', pre_binding_execution)
    review, assess, commands, cap = _fixture(candidate, monkeypatch)
    def seed(tools, child, packet):
        failed, result = _probe(tools, child, packet, BAD)
        assert 'receipt' in result, failed.output
        assert not failed.ok and result['receipt']['status'] == 'failed'
        assert 'executionBinding' not in result['receipt']
        assert result['receipt']['testReport']['caseReport']['cases'][0]['failureKind'] == 'exception'
        return AgentStep(type='assistant', kind='final', content='Interrupted before native completion')
    try:
        assert assess(seed)['status'] == 'inconclusive'
        state_path.write_text(json.dumps({'pid': __import__('os').getpid(), 'state': review.state}))
    finally:
        cap.close()
'''


def test_saved_pre_binding_exception_reexecutes_current_command_before_independent_correction(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[1]
    candidate, state_path = tmp_path / "candidate", tmp_path / "pre-binding-state.json"
    candidate.mkdir()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, "-c", _SEED_PRE_BINDING_PROCESS, str(repository), str(candidate), str(state_path)],
        cwd=repository, env=environment, capture_output=True, text=True, timeout=45, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    saved = json.loads(state_path.read_text())
    assert saved["pid"] != os.getpid(), "The initial receipt belongs to a completed earlier process."
    state = saved["state"]
    old = state["receipts"][0]
    old_file = Path(old["receiptRef"])
    old_hash = hashlib.sha256(old_file.read_bytes()).hexdigest()
    assert len(state["receipts"]) == 1 and "executionBinding" not in old["receipt"]
    assert old["receipt"]["evidence"]["argv"][-1] == "test_public.py"
    assert old["receipt"]["testReport"]["caseReport"]["cases"][0]["nodeId"] == "test_public.py::test_inventory"

    calls = []
    review, assess, commands, cap = _fixture(candidate, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, state),
        adjudication_driver=fixture_adjudicator(calls))

    def approve(tools, child, packet):
        assert calls == [BAD]
        assert len(packet["retainedProbes"]) == 1
        assert packet["retainedProbes"][0]["status"] == "passed"
        result = _submit(tools, child, packet, ids=[packet["retainedProbes"][0]["probeId"]])
        assert result.ok, result.output

    try:
        result = assess(approve)
        assert result["status"] == "approved" and result["repairProbes"] == []
        assert len(commands) == 3, "Current public check, old probe replay and corrected probe all execute."
        assert (candidate / "stock.py").read_text() == SOURCE
        current = review.state
        assert current["receipts"][0] == old
        assert hashlib.sha256(old_file.read_bytes()).hexdigest() == old_hash
        assert len(current["receipts"]) == 3
        replay, successor = current["receipts"][1:]
        assert replay["probeId"] == old["probeId"] and replay["receiptRef"] != old["receiptRef"]
        assert [row["receipt"]["status"] for row in current["receipts"]] == ["failed", "failed", "passed"]
        derived, target = derive_review_probe_check(review.spec.checks[0], "test_public.py")
        for wrapped in (replay, successor):
            source = next(probe for probe in current["probes"] if probe["probeId"] == wrapped["probeId"])
            validate_review_probe_execution_binding(wrapped["receipt"], review.spec.checks,
                probe_source_digest=source["sourceDigest"])
            assert wrapped["receipt"]["evidence"]["argv"] == list(derived.argv)
            assert wrapped["receipt"]["testReport"]["caseReport"]["cases"][0]["nodeId"] == target + "::test_inventory"
        assert next(probe for probe in current["probes"] if probe["probeId"] == successor["probeId"])["code"] == GOOD
        correction = current["corrections"][0]
        assert correction["originalReceipt"] == replay["receiptRef"]
        assert correction["replacementReceipt"] == successor["receiptRef"]
        assert current["failureAdjudications"][0]["receiptRef"] == replay["receiptRef"]
        restored = ImplementationCompletionReview.from_state(review.spec, current)
        assert restored.state["receipts"] == current["receipts"]
        assert restored.state["corrections"] == current["corrections"]
    finally:
        cap.close()
