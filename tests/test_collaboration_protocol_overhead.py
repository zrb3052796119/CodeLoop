"""Actual host boundaries report all fixable metadata errors in one response."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from minicode.reviewer_result_submission import create_reviewer_submit_tool
from minicode.reviewer_verification import ReviewerProbeLedger
from minicode.tooling import ToolContext, ToolRegistry
from minicode.workflow_runner import _WorkflowRunSession
from test_review_coverage_feedback import _record
from test_reviewer_result_submission import _approval, _bound
from test_reviewer_verification import PATCH
from test_verification_output_workflow import _bootstrap, _source, _test
from minicode.deterministic_verifier import VerificationCheck
from minicode.implementation_execution import ImplementationSpec, direct_verification_capability


def test_native_submit_reports_every_bad_criterion_without_publishing_partial_coverage(tmp_path):
    assignment, store, channel, _tool, kwargs = _bound(build=False)
    ledger = ReviewerProbeLedger()
    behavior = _record(ledger, ['ac_behavior'], 'behavior')
    tests = _record(ledger, ['ac_tests'], 'tests')
    state = SimpleNamespace(frozen_workspace_change=SimpleNamespace(patch_digest=PATCH),
                            reviewer_probes=ledger, artifact_store=store)

    class Host:
        _publish_review_coverage = _WorkflowRunSession._publish_review_coverage
        _review_source_evidence = _WorkflowRunSession._review_source_evidence

        def _execution_state(self):
            return state

    host = Host()
    tool = create_reviewer_submit_tool(assignment, channel, **kwargs,
        coverage_ref=f'artifact_review_coverage_{assignment.assignment_id}',
        coverage_guard=lambda raw, declaration, ctx: host._publish_review_coverage(assignment, raw, declaration, ctx))
    value = _approval()
    value['criteria'][0].update(rationale='Inspect behavior.', probeIds=[tests.probe_id])
    value['criteria'][1].update(rationale='Inspect tests.', probeIds=[behavior.probe_id])
    before = ledger.feedback(), store.list_artifact_ids()
    result = ToolRegistry([tool]).execute(tool.name, value,
        ToolContext(cwd=str(tmp_path), _subagent_mailbox=channel))
    assert not result.ok and result._review_evidence_incomplete
    details = json.loads(result.output.split(': ', 1)[1])
    assert [row['criterionId'] for row in details.get('criterionErrors', [])] == ['ac_behavior', 'ac_tests'], details
    assert all(row['errors'][0]['invalidProbeReferences'][0]['reasons'] == ['criterion_mismatch']
               for row in details['criterionErrors'])
    assert {row['probeId']: row['criterionIds'] for row in details['availablePassedProbes']} == {
        behavior.probe_id: ['ac_behavior'], tests.probe_id: ['ac_tests']}
    assert (ledger.feedback(), store.list_artifact_ids()) == before
    assert store.read(channel.write_key) is None


def test_public_log_has_compact_reference_index_and_typed_wrong_reference_recovery(tmp_path, monkeypatch):
    _bootstrap(monkeypatch)
    (tmp_path / 'candidate.py').write_text(_source(1))
    (tmp_path / 'test_regression.py').write_text(_test(2))
    spec = ImplementationSpec(('candidate.py',), (VerificationCheck('regression', 'tests',
        ('python', '-m', 'pytest', '-q', '-s', 'test_regression.py'), 20),), 'Diagnose failed public check')
    cap = direct_verification_capability(spec)
    context = ToolContext(cwd=str(tmp_path))
    registry = cap.bind_registry(ToolRegistry([]))
    try:
        check = registry.execute('executor_check', {'check': 1}, context)
        assert not check.ok
        manifest = json.loads(check.output)['verificationOutputs'][0]
        bad = registry.execute('verification_output_read', {'outputRef': 'artifact_verification_wrong'}, context)
        assert not bad.ok
        assert 'tool_crashed' not in bad.output, bad.output
        recovery = json.loads(bad.output)['recovery']
        listing = registry.execute(recovery['tool'], recovery['input'], context)
        assert listing.ok, listing.output
        index = json.loads(listing.output)
        assert index['nextOffset'] is None and len(index['outputs']) == 1
        assert index['outputs'][0]['streams']['stderr']['outputRef'] == manifest['streams']['stderr']['outputRef']
        assert len(listing.output) < 2000
        assert 'contentBase64' not in index and 'text' not in index
        filtered = registry.execute('verification_output_read', {'action': 'list', 'checkId': 'absent'}, context)
        assert json.loads(filtered.output)['outputs'] == []
        for invalid in ({'action': 'list', 'outputRef': 'artifact_wrong'},
                        {'action': 'list', 'limit': 21}, {'action': 'list', 'offset': True}):
            assert not registry.execute('verification_output_read', invalid, context).ok
        page = registry.execute('verification_output_read',
            {'outputRef': index['outputs'][0]['streams']['stderr']['outputRef'], 'offset': 6000, 'limit': 30}, context)
        assert page.ok and 'MIDDLE_DIAGNOSTIC' in json.loads(page.output)['text']
        # Reading the byte directory remains available for existing callers.
        assert registry.execute('verification_output_read', {}, context).ok
    finally:
        cap.close()
    assert not registry.execute('verification_output_read', {'action': 'list'}, context).ok


@pytest.mark.parametrize('escaped_identifiers', [False, True])
def test_compact_log_index_paginates_stream_references_without_losing_failed_runs(tmp_path, escaped_identifiers):
    from minicode.verification_output_tool import VerificationOutputLedger, VerificationOutputAccess

    ledger = VerificationOutputLedger('workflow_public')
    context = ToolContext(cwd=str(tmp_path))
    access = VerificationOutputAccess(ledger, checkpoint=lambda _ctx: None, owns_ledger=True)
    access.bind_direct_owner(context)
    registry = ToolRegistry([access.tool])
    try:
        store = ledger.store(context)
        expected = []

        def stream(data):
            return {'byteCount': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}

        for index in range(23):
            assignment = 'a' + chr(1) * 255 if escaped_identifiers else 'assignment_public'
            check = 'c' + chr(1) * 255 if escaped_identifiers else f'check_{index}'
            capture = store.begin(workflow_id='workflow_public', assignment_id=assignment,
                                  check_id=check, patch_digest=PATCH)
            capture.add('stderr', b'failure detail')
            manifest = capture.finish(stdout=stream(b''), stderr=stream(b'failure detail'), complete=True)
            ledger.record(manifest)
            expected.append(manifest['streams']['stderr']['outputRef'])
        actual, offset = [], 0
        while True:
            result = registry.execute('verification_output_read', {'action': 'list', 'offset': offset, 'limit': 20}, context)
            assert result.ok and len(result.output) <= 28000
            page = json.loads(result.output)
            actual.extend(item['streams']['stderr']['outputRef'] for item in page['outputs'])
            offset = page['nextOffset']
            if offset is None:
                break
        assert actual == expected
    finally:
        access.close()
