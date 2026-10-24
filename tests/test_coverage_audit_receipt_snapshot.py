"""Restore a pending replacement audit against its original receipt snapshot.

Semantic responses are scripted. Both independent pytest receipts, real parent
and child turns, native checkpoints and post-audit execution remain production.
"""
from copy import deepcopy

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import OBJECTIVE, _call, _result
from test_completion_failure_adjudication_resume import packet_with
from test_implementation_completion_review import _native_coverage_step, _inspection_step
from test_semantic_supersession_continuation import (
    BROKEN_FIXTURE, VALID_FIXTURE, seed_native_fixture_failure,
)

inventory = chronology.inventory


def probe_count(case):
    return sum('test_public.py' not in row['argv'] for row in case['commands'])


def interrupted_replacement_audit(case, monkeypatch, *, runtime_failure=False):
    # Receipt replay invariants are exercised through complete strict continuation.
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "full")
    if runtime_failure:
        # LANG is deliberately inherited by the verifier. Use a real runtime
        # change to fail the later invocation without editing candidate bytes.
        monkeypatch.setenv('LANG', 'C')
    initial, parent, old_id = seed_native_fixture_failure(case, monkeypatch, product_gap=False)
    continuation = HostImplementationContinuation.from_document(initial, spec=case['spec'], messages=parent)
    ids = [row['id'] for row in requirement_units(OBJECTIVE)]
    captured = {}
    replacement_code = VALID_FIXTURE + ("\ndef test_runtime_ready():\n    assert __import__('os').environ.get('LANG') == 'C'\n" if runtime_failure else "")

    class Seed:
        rejected = False

        def next(self, actual, **kwargs):
            inspection_step = _inspection_step(actual, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            if 'completion_probe_adjudication_submit' in names:
                packet = packet_with(actual, 'probe')
                assert packet['probe']['probeId'] == old_id and packet['probe']['code'] == BROKEN_FIXTURE
                if not any(m.get('toolUseId') == 'fixture-source' for m in actual):
                    return _call('fixture-source', 'read_file', path='stock.py')
                return _call('correct-fixture', 'completion_probe_adjudication_submit', verdict='probe_error',
                    reason='The helper returns a backend instance, so the second call prevents both unchanged assertions.',
                    objectiveEvidence=[{'startLine': 1, 'endLine': 2}],
                    sourceEvidence=[{'path': 'stock.py', 'startLine': 1, 'endLine': 5}],
                    replacement={'criterionIds': ids, 'scenario': packet['probe']['scenario'], 'code': replacement_code})
            if 'completion_review_submit' in names:
                packet = packet_with(actual, 'retainedProbes')
                passed = [row['probeId'] for row in packet['retainedProbes'] if row['status'] == 'passed']
                assert len(passed) == 1 and passed[0] != old_id
                captured['newId'] = passed[0]
                return _call('replacement-proposal', 'completion_review_submit', status='approved', findings=[],
                    requirements=[{'id': identity, 'status': 'covered', 'probeIds': passed,
                        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}],
                        'rationale': 'The actual replacement quantity assertions passed.'} for identity in ids])
            if 'completion_coverage_audit_submit' in names:
                if self.rejected:
                    assert _result(actual, 'rejected-replacement-audit')['isError']
                    captured['childMessages'] = deepcopy(actual)
                    raise KeyboardInterrupt('save the actual replacement audit after native rejection')
                step = _native_coverage_step(actual, stable_evidence=True)
                if step.calls[0]['toolName'] == 'completion_coverage_audit_submit':
                    value = step.calls[0]['input']
                    assert len(value['supersessions']) == 1 and value['supersessions'][0]['status'] == 'accepted'
                    value['findings'] = ['Positive observation belongs in the requirement reasons.']
                    step.calls[0]['id'] = 'rejected-replacement-audit'
                    self.rejected = True
                return step
            pytest.fail('The incomplete replacement audit must not return to the writer.')

    with pytest.raises(KeyboardInterrupt, match='actual replacement audit'):
        run_agent_turn(model=Seed(), messages=parent, implementation_continuation=continuation, **case['options'])
    document, parent = deepcopy(case['checkpoints'][-1])
    pending = document['reviewState']['pendingCoverageAudit']
    assert pending is not None and len(document['reviewState']['supersessions']) == 1
    assert record_document(pending['messagesRecord'])['messages'] == captured['childMessages']
    captured.update(oldId=old_id, document=document, parent=parent, probeCount=probe_count(case))
    return captured


def replay_actual_receipt(case, seed):
    """Run a legitimate later host check; never edit its original audit view."""
    import minicode.workflow_runner as runner
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.permissions import PermissionManager
    from minicode.review_probe_execution import run_isolated_review_probe
    from minicode.reviewer_verification import ReviewerProbeLedger
    from minicode.tooling import ToolContext
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction
    document = deepcopy(seed['document'])
    review = ImplementationCompletionReview.from_state(case['spec'], document['reviewState'])
    probe = review._probes[seed['newId']]
    context = ToolContext(cwd=str(case['workspace']), permissions=PermissionManager(str(case['workspace']), prompt=None))
    verifier = runner._deterministic_verifier_for_context(context)
    with WorkflowWorkspaceTransaction(str(case['workspace'])) as transaction:
        frozen = transaction.freeze(allowed_paths=case['spec'].write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=case['spec'].checks[0],
            verifier=verifier, test_path='test_public.py', read_roots=('.',), forbidden_paths=('.git', '.env'),
            run_kwargs={'workflow_id': 'wf_completion_review', 'task_id': 'task_completion_review',
                'execution_assignment_id': 'asg_completion_review', 'patch_digest': frozen.patch_digest,
                'permissions': context.permissions, 'permission_cwd': context.cwd})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt['status'] == 'passed'
    wrapped = {'candidateDigest': document['candidateDigest'], 'probeId': probe.probe_id,
        'sourceRef': review._persist('probe-source', probe.to_dict()), 'receipt': receipt}
    wrapped['receiptRef'] = review._persist('probe-receipt', wrapped)
    state = review.state
    state['receipts'].append(wrapped)
    document['reviewState'] = state
    # The production state loader must validate this complete real receipt.
    ImplementationCompletionReview.from_state(case['spec'], state)
    seed['document'] = document
    seed['probeCount'] = probe_count(case)
    return wrapped


@pytest.mark.parametrize('later_execution', [False, True], ids=['first-resume', 'later-host-receipt'])
def test_pending_supersession_resumes_before_replay_then_rechecks_and_restores(inventory, monkeypatch, later_execution):
    case = inventory
    seed = interrupted_replacement_audit(case, monkeypatch)
    newer = replay_actual_receipt(case, seed) if later_execution else None
    original = deepcopy(seed['document'])
    continuation = HostImplementationContinuation.from_document(original, spec=case['spec'], messages=seed['parent'])

    class Resume:
        audit_calls = 0
        parent_calls = 0

        def next(self, actual, **kwargs):
            inspection_step = _inspection_step(actual, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            if 'completion_coverage_audit_submit' in names:
                self.audit_calls += 1
                assert self.audit_calls == 1
                assert probe_count(case) == seed['probeCount'], 'No replacement execution may rewrite receipt refs before the original audit resumes.'
                assert actual[:len(seed['childMessages'])] == seed['childMessages']
                step = _native_coverage_step(actual, stable_evidence=True)
                assert step.calls[0]['toolName'] == 'completion_coverage_audit_submit'
                return step
            assert 'completion_review_submit' not in names and 'completion_probe_adjudication_submit' not in names
            self.parent_calls += 1
            assert self.parent_calls == 1
            assert probe_count(case) > seed['probeCount'], 'Historical audit acceptance does not replace current final execution.'
            return AgentStep(type='assistant', kind='final', content='The actual inventory operations are verified.')

    resume = Resume()
    result = run_agent_turn(model=resume, messages=seed['parent'], implementation_continuation=continuation, **case['options'])
    assert resume.audit_calls == 1 and result[-1]['content'] == 'The actual inventory operations are verified.'
    final, messages = case['checkpoints'][-1]
    assert final['executorId'] == original['executorId'] and final['candidateDigest'] == original['candidateDigest']
    review = HostImplementationContinuation.from_document(final, spec=case['spec'], messages=messages).restore_review(case['spec'])
    assert seed['oldId'] in review.state['retiredProbeIds']
    assert review.state['pendingCoverageAudit'] is None
    assert review.state['reviews'][-1]['status'] == 'approved'
    receipts = [row for row in review.state['receipts'] if row['probeId'] == seed['newId']]
    assert len(receipts) >= 2 and all(row['receipt']['status'] == 'passed' for row in receipts)
    assert len({row['receiptRef'] for row in receipts}) == len(receipts)
    if newer is not None:
        assert newer in receipts, 'Restore must retain later host evidence, never roll it back to the audit snapshot.'
    audit = review.state['reviews'][-1]['coverageAudit']
    from pathlib import Path
    assert Path(audit['authorityRef']).parent == Path(audit['evidenceRef']).parent
    assert audit['authorityProvenance']['originalAuthorityRef'] == original['reviewState']['pendingCoverageAudit']['authorityRecord']['ref']
    assert Path(audit['authorityRef']).read_text() == original['reviewState']['pendingCoverageAudit']['authorityRecord']['rawJson']


def test_new_post_audit_failure_blocks_delivery_after_original_context_resumes(inventory, monkeypatch):
    case = inventory
    seed = interrupted_replacement_audit(case, monkeypatch, runtime_failure=True)
    continuation = HostImplementationContinuation.from_document(seed['document'], spec=case['spec'], messages=seed['parent'])
    observed = []
    original_candidate = (case['workspace'] / 'stock.py').read_bytes()
    original_read = deepcopy(_result(seed['parent'], 'original-read'))
    class Resume:
        def next(self, actual, **kwargs):
            inspection_step = _inspection_step(actual, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            if 'completion_coverage_audit_submit' in names:
                observed.append('coverage')
                assert actual[:len(seed['childMessages'])] == seed['childMessages']
                assert probe_count(case) == seed['probeCount']
                step = _native_coverage_step(actual, stable_evidence=True)
                assert step.calls[0]['toolName'] == 'completion_coverage_audit_submit'
                monkeypatch.setenv('LANG', 'C.UTF-8')
                return step
            # A previously passing acceptance is still an obligation. Its new
            # real failure returns to the same implementer; it is not eligible
            # for retirement via a fresh "bad generated test" attribution.
            assert {'write_file', 'executor_check'} <= names
            observed.append('same-executor-repair')
            decisions = [row['payload'] for row in case['events']
                         if row['type'] == 'implementation.completion_decided']
            current = next(row for row in decisions[-1]['probeReceipts'] if row['probeId'] == seed['newId'])
            assert current['receipt']['status'] == 'failed'
            assert current['receipt']['testReport']['failed'] > 0
            assert any(row['outcome'] == 'failed' for row in current['receipt']['testReport']['caseReport']['cases'])
            assert decisions[-1]['status'] == 'changes_required'
            assert decisions[-1]['executorId'] == seed['document']['executorId']
            assert _result(actual, 'original-read') == original_read
            feedbacks = [m for m in actual if m.get('_implementation_completion_feedback')]
            assert feedbacks, [(m.get('role'), m.get('toolUseId'), sorted(m)) for m in actual]
            assert seed['newId'] in feedbacks[-1]['content'] and 'failed' in feedbacks[-1]['content']
            raise KeyboardInterrupt('preserve the current failure returned to its original executor')
    with pytest.raises(KeyboardInterrupt, match='original executor'):
        run_agent_turn(model=Resume(), messages=seed['parent'], implementation_continuation=continuation, **case['options'])
    assert observed == ['coverage', 'same-executor-repair']
    final, messages = case['checkpoints'][-1]
    assert final['executorId'] == seed['document']['executorId']
    assert final['candidateDigest'] == seed['document']['candidateDigest']
    assert (case['workspace'] / 'stock.py').read_bytes() == original_candidate
    assert _result(messages, 'original-read') == original_read
    review = HostImplementationContinuation.from_document(final, spec=case['spec'], messages=messages).restore_review(case['spec'])
    assert review.state['reviews'][-1]['status'] != 'approved'
    receipts = [row for row in review.state['receipts'] if row['probeId'] == seed['newId']]
    assert receipts[-1]['receipt']['status'] == 'failed' and any(row['receipt']['status'] == 'passed' for row in receipts)
