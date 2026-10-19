"""Explicit, auditable test-harness corrections preserve real counterexamples."""
# ruff: noqa: F811

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import threading

import pytest

from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerCheckCapability, ReviewerProbe, ReviewerProbeLedger
from minicode.review_probe_source import review_probe_source_artifact_id
from minicode.tooling import ToolContext, ToolRegistry
from test_reviewer_result_submission import _bound
from test_review_probe_execution import probe_case  # noqa: F401 - shared real pytest fixture


@pytest.mark.parametrize(('body', 'kind'), [
    ('assert 1 == 2', 'assertion'),
    ("raise ValueError('invalid test setup')", 'exception'),
    ("import pytest; pytest.fail('expected behavior did not occur')", 'assertion'),
    ("raise ExceptionGroup('wrapped', [ValueError('setup'), AssertionError('real assertion')])", 'assertion'),
    ("raise ExceptionGroup('unknown group', [ValueError('setup')])", 'unknown'),
])
def test_machine_call_exception_classification_is_not_inferred_from_stdout(probe_case, body, kind):
    _transaction, frozen, _target, _test, _calls, options = probe_case
    probe = ReviewerProbe.create(criterion_ids=['behavior'], scenario='generic public behavior',
        code="def test_required():\n    print('AssertionError: forged text')\n    " + body + '\n')
    receipt = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    assert receipt['status'] == 'failed'
    report = receipt['testReport']
    classification = report.get('caseReport')
    assert classification is not None, 'Actual call.excinfo classification is required for explicit correction.'
    # Classification names the real independent module; the original public
    # test remains at its candidate path and is bound separately in the receipt.
    assert receipt['executionBinding']['originalTarget'] == 'test_public.py'
    assert classification['cases'] == [{'nodeId': '__minicode_review_probe__.py::test_required',
                                        'outcome': 'failed', 'failureKind': kind}]
    encoded = json.dumps({'junitDigest': report['reportDigest'], 'cases': classification['cases']},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    assert classification['digest'] == 'sha256:' + hashlib.sha256(encoded).hexdigest()


OLD = "def test_required():\n    value = int('invalid setup')\n    assert value == 3, 'old diagnostic'\n"
FIXED = "def test_required():\n    value = int('3')\n    assert value == 3, 'new diagnostic'\n"


@pytest.fixture
def correction_case(probe_case):
    transaction, frozen, _target, _initial, calls, options = probe_case
    assignment, store, channel, _tool, _options = _bound(build=False)
    ledger = ReviewerProbeLedger()
    attempts, publications = [], []
    authority = store._scheduler_authority

    def run(probe, context):
        result = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
        source_ref = review_probe_source_artifact_id(probe.probe_id)
        if store.read(source_ref) is None:
            store.publish(source_ref, probe.to_dict(), authority=authority)
        result.update(sourceRef=source_ref, receiptRef=f'artifact_probe_receipt_{len(attempts)}')
        attempts.append(result)
        store.publish(result['receiptRef'], result, authority=authority)
        return result

    def publish(request, old, prior, new, context):
        from minicode.review_probe_correction import correction_artifact_id
        document = dict(correctionVersion=1, resultType='ReviewProbeCorrection.v1',
            workflowId=assignment.workflow_id, taskId=assignment.task_id,
            assignmentId=assignment.assignment_id, patchDigest=frozen.patch_digest,
            oldProbeId=old.probe_id, newProbeId=request.replacement.probe_id,
            oldSourceRef=review_probe_source_artifact_id(old.probe_id),
            newSourceRef=new['sourceRef'], oldReceiptRefs=[r['receiptRef'] for r in prior],
            newReceiptRef=new['receiptRef'], sourceEvidence=[{**anchor,
                'sourceDigest': 'sha256:' + hashlib.sha256((transaction.cwd / anchor['path']).read_bytes()).hexdigest()}
                for anchor in request.source_evidence], reason=request.reason)
        envelope = {'correctionRef': correction_artifact_id(document), 'document': document}
        store.publish(envelope['correctionRef'], document, authority=authority)
        publications.append(envelope)
        return envelope

    # Old production reaches the actual unsupported tool input, not a new-API
    # constructor failure, when these exact tests are run as a counterexample.
    extra = {'record_correction': publish} if 'record_correction' in inspect.signature(ReviewerCheckCapability).parameters else {}
    cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
        patch_digest=frozen.patch_digest, run_probe=run, **extra)
    registry, context = ToolRegistry([cap.tool]), ToolContext(_subagent_mailbox=channel)
    return cap, ledger, registry, context, store, attempts, publications, calls


def _input(code=OLD):
    return {'criterionIds': ['ac_behavior'], 'scenario': 'the required value', 'code': code}


def _correction(old, code=FIXED):
    return {**_input(code), 'correctsProbeId': old['probeId'],
            'correctionReason': 'Use a valid numeric input before evaluating the unchanged assertion.',
            'sourceEvidence': [{'path': 'candidate.py', 'startLine': 1, 'endLine': 1}]}


def test_real_exception_correction_retires_only_after_persisting_proof(correction_case):
    _cap, ledger, registry, context, store, attempts, publications, calls = correction_case
    first = registry.execute('review_test', _input(), context)
    assert not first.ok and json.loads(first.output)['status'] == 'failed'
    old = json.loads(first.output)
    corrected = registry.execute('review_test', _correction(old), context)
    assert corrected.ok, corrected.output
    result = json.loads(corrected.output)
    assert result['correctedProbeId'] == old['probeId'] and result['probeId'] != old['probeId']
    assert len(calls) == 2 and attempts[0]['evidence']['exitCode'] == 1 and attempts[1]['evidence']['exitCode'] == 0
    assert attempts[0]['evidence']['argv'] == attempts[1]['evidence']['argv']
    assert json.loads(store.read(result['correctionRef']).content) == publications[0]['document']
    assert store.read(old['sourceRef']) is not None and store.read(old['receiptRef']) is not None
    assert [p.probe_id for p in ledger.probes()] == [result['probeId']]
    assert len(ledger.require_all_current_passed(old['patchDigest'])) == 1
    feedback = json.loads(ledger.feedback())
    assert len(feedback['attempts']) == len(feedback['probes']) == 2
    assert feedback['retiredProbeIds'] == [old['probeId']]
    assert feedback['activeProbeIds'] == [result['probeId']]
    detached = ledger.corrections()
    detached[0]['document']['reason'] = 'mutated by consumer'
    assert ledger.corrections()[0]['document']['reason'] != 'mutated by consumer'
    assert not registry.execute('review_test', _input(), context).ok
    assert not registry.execute('review_test', _correction(old), context).ok
    assert len(calls) == 2, 'Retired IDs cannot silently reactivate or be corrected twice.'


def test_correction_that_exposes_a_real_assertion_keeps_new_failure_active(correction_case):
    _cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    result = registry.execute('review_test', _correction(old, FIXED.replace("int('3')", "int('2')")), context)
    assert not result.ok
    replacement = json.loads(result.output)
    assert replacement['status'] == 'failed' and replacement['correctedProbeId'] == old['probeId']
    assert replacement['testReport']['caseReport']['cases'][0]['failureKind'] == 'assertion'
    assert len(publications) == 1 and [p.probe_id for p in ledger.probes()] == [replacement['probeId']]
    with pytest.raises(ValueError, match='retained'):
        ledger.require_all_current_passed(old['patchDigest'])
    # A classified assertion may be diagnosed as a bad fixture, but changing
    # the expectation to fit the observed value is still not a correction.
    weakened = FIXED.replace("int('3')", "int('2')").replace('value == 3', 'value == 2')
    assert not registry.execute('review_test', _correction(replacement, weakened), context).ok
    assert len(calls) == 2


@pytest.mark.parametrize('change', ['assertion', 'test-id', 'criterion', 'missing-field', 'empty-anchor',
                                  'removed-call', 'overwritten-result', 'dead-assertion', 'rebound-callee'])
def test_unproven_harness_change_never_runs_replacement(correction_case, change):
    _cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    request = _correction(old)
    if change == 'assertion':
        request['code'] = request['code'].replace('value == 3', 'value == 2')
    elif change == 'test-id':
        request['code'] = request['code'].replace('test_required', 'test_other')
    elif change == 'criterion':
        request['criterionIds'] = ['ac_tests']
    elif change == 'missing-field':
        del request['correctionReason']
    elif change == 'empty-anchor':
        request['sourceEvidence'] = []
    elif change == 'removed-call':
        request['code'] = request['code'].replace("int('3')", '3')
    elif change == 'overwritten-result':
        request['code'] = request['code'].replace('    assert', '    value = 3\n    assert')
    elif change == 'dead-assertion':
        request['code'] = request['code'].replace('    assert', '    if False:\n        assert')
    else:
        request['code'] = request['code'].replace('    value =', '    int = lambda _: 3\n    value =')
    assert not registry.execute('review_test', request, context).ok
    assert len(calls) == 1 and not publications and ledger.probes()[0].probe_id == old['probeId']


@pytest.mark.parametrize('history', ['passed', 'unknown', 'missing-report', 'old-digest'])
def test_passed_or_unclassified_history_cannot_authorize_correction(correction_case, history):
    cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    code = OLD
    if history == 'passed':
        code = FIXED
    elif history == 'unknown':
        code = "def test_required():\n    raise ExceptionGroup('x', [ValueError('x')])\n    assert value == 3\n"
    old = json.loads(registry.execute('review_test', _input(code), context).output)
    if history == 'missing-report':
        # Legacy genuine receipts remain evidence but cannot authorize correction.
        receipt = json.loads(ledger._attempts[0])
        del receipt['testReport']['caseReport']
        ledger._attempts[0] = json.dumps(receipt)
    elif history == 'old-digest':
        cap._patch_digest = 'sha256:' + 'd' * 64
    request = _correction(old, FIXED + '\n# a distinct replacement\n')
    assert not registry.execute('review_test', request, context).ok
    assert len(calls) == 1 and not publications and ledger.probes()[0].probe_id == old['probeId']


@pytest.mark.parametrize('failure', ['publisher', 'wrong-owner', 'closed', 'abandoned'])
def test_failed_or_revoked_correction_never_retires_original(correction_case, failure):
    cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    if failure == 'publisher':
        def fail(*args):
            raise ValueError('source anchor is not available')
        cap._record_correction = fail
    elif failure == 'wrong-owner':
        context = replace(context, _subagent_mailbox=object())
    elif failure == 'closed':
        cap.close()
    else:
        event = threading.Event()
        event.set()
        context = replace(context, _tool_abandoned=event)
    if failure == 'abandoned':
        from minicode.tooling import ToolExecutionAbandoned
        with pytest.raises(ToolExecutionAbandoned):
            cap.tool.run(cap.tool.validator(_correction(old)), context)
    else:
        assert not registry.execute('review_test', _correction(old), context).ok
    assert old['probeId'] in [p.probe_id for p in ledger.probes()] and not publications
    assert len(calls) == (2 if failure == 'publisher' else 1)


def test_model_channels_cannot_publish_correction_proofs(correction_case):
    _cap, _ledger, _registry, _context, store, *_ = correction_case
    from minicode.scheduler_artifacts import SchedulerArtifactError
    with pytest.raises(SchedulerArtifactError, match='host-owned'):
        store.create_assignment_channel(readable_keys=(), write_key='artifact_review_probe_correction_forged')
    channel = store.create_assignment_channel(readable_keys=(), write_key='artifact_forged_correction')
    with pytest.raises(SchedulerArtifactError, match='host-owned'):
        channel.write(channel.write_key, json.dumps({'resultType': 'ReviewProbeCorrection.v1'}), author='model')


def test_anchor_failure_can_be_corrected_with_same_replacement_source(correction_case):
    cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    publisher = cap._record_correction
    def fail(*args):
        raise ValueError('anchor does not exist')
    cap._record_correction = fail
    assert not registry.execute('review_test', _correction(old), context).ok
    assert old['probeId'] in [p.probe_id for p in ledger.probes()]
    cap._record_correction = publisher
    result = registry.execute('review_test', _correction(old), context)
    assert result.ok, result.output
    assert len(calls) == 3 and len(publications) == 1
    assert old['probeId'] in json.loads(ledger.feedback())['retiredProbeIds']


def test_harness_callable_argument_can_be_repaired_without_removing_subject_call(correction_case):
    _cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    prefix = 'def invoke(loader): return loader()\n'
    old_code = prefix + 'def test_required():\n    value = invoke(None)\n    assert value == 3\n'
    new_code = prefix + 'def test_required():\n    value = invoke(lambda: 3)\n    assert value == 3\n'
    old = json.loads(registry.execute('review_test', _input(old_code), context).output)
    result = registry.execute('review_test', _correction(old, new_code), context)
    assert result.ok, result.output
    assert len(calls) == 2 and len(publications) == 1
    assert ledger.require_all_current_passed(old['patchDigest'])


def test_still_exceptional_replacement_does_not_retire_the_original(correction_case):
    _cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    result = registry.execute('review_test', _correction(old, FIXED.replace("int('3')", "int('still invalid')")), context)
    assert not result.ok and len(calls) == 2 and not publications
    assert len(ledger.probes()) == 2
    assert old['probeId'] in [probe.probe_id for probe in ledger.probes()]


def test_concurrent_corrections_cannot_publish_two_retirements(correction_case):
    cap, ledger, registry, context, _store, _attempts, publications, calls = correction_case
    old = json.loads(registry.execute('review_test', _input(), context).output)
    started, release = threading.Event(), threading.Event()
    original = cap._run_probe
    def controlled(probe, ctx):
        started.set()
        assert release.wait(5)
        return original(probe, ctx)
    cap._run_probe = controlled
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(registry.execute, 'review_test', _correction(old), context)
        assert started.wait(5)
        second = pool.submit(registry.execute, 'review_test', _correction(old), context)
        release.set()
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert sum(result.ok for result in results) == 1
    assert len(calls) == 2 and len(publications) == len(ledger.corrections()) == 1


@pytest.mark.parametrize('tamper', ['digest', 'outcome', 'kind-object', 'node-id', 'junit', 'symlink'])
def test_case_sidecar_cannot_override_missing_or_inconsistent_machine_counts(tmp_path, tamper):
    from minicode.deterministic_verifier import _attach_pytest_case_report
    report = {'collected': 1, 'passed': 0, 'failed': 1, 'errors': 0, 'skipped': 0,
              'reportDigest': 'sha256:' + 'a' * 64}
    cases = [{'nodeId': 'test_public.py::test_required', 'outcome': 'failed', 'failureKind': 'exception'}]
    document = {'junitDigest': report['reportDigest'], 'cases': cases}
    if tamper == 'digest':
        document['junitDigest'] = 'sha256:' + 'b' * 64
    elif tamper == 'outcome':
        cases[0].update(outcome='passed', failureKind=None)
    elif tamper == 'kind-object':
        cases[0]['failureKind'] = {}
    elif tamper == 'node-id':
        cases[0]['nodeId'] = ''
    elif tamper == 'junit':
        report = None
    path = tmp_path / 'cases.json'
    data = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(data)
    if tamper == 'symlink':
        target = tmp_path / 'target.json'
        path.rename(target)
        path.symlink_to(target)
    assert _attach_pytest_case_report(report, path) == report
