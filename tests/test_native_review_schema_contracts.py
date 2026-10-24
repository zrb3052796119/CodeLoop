"""Native tool descriptions disclose existing constraints without relaxing them."""

from copy import deepcopy
import json

import pytest

from minicode.openai_adapter import OpenAIModelAdapter
from minicode.coverage_audit_state import record_document
from test_implementation_completion_review import _fixture, _probe, PROBE
import test_completion_coverage_audit as native
import test_independent_coverage_inspection as inspection


CORRECTION_FIELDS = ('correctsProbeId', 'correctionReason', 'sourceEvidence')


def _correction():
    return {'correctsProbeId': 'review_probe_not_retained',
        'correctionReason': 'Remove the unused fixture dependency.',
        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}]}


def test_provider_schema_advertises_each_optional_correction_dependency(tmp_path, monkeypatch):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, _child, _packet):
        adapter = OpenAIModelAdapter({'model': 'local-schema-test'}, tools)
        functions = {row['function']['name']: row['function'] for row in adapter.request_tool_schemas()}
        schema = functions['review_test']['parameters']
        assert set(schema['required']) == {'purpose', 'criterionIds', 'scenario', 'code'}
        for name in CORRECTION_FIELDS:
            description = schema['properties'][name].get('description', '')
            assert all(field in description for field in CORRECTION_FIELDS)
            assert 'together' in description and 'all omitted' in description
        # The shared anchor shape must not advertise correction-only rules on
        # ordinary completion evidence, where those fields do not exist.
        anchor = functions['completion_review_submit']['parameters']['properties']['requirements']['items']['properties']['sourceEvidence']
        assert 'correctsProbeId' not in json.dumps(anchor)

    try:
        assert assess(driver)['status'] == 'inconclusive'
    finally:
        capability.close()


@pytest.mark.parametrize('supplied', [
    (field,) for field in CORRECTION_FIELDS
] + [tuple(other for other in CORRECTION_FIELDS if other != field) for field in CORRECTION_FIELDS])
def test_partial_correction_names_exact_missing_fields_without_execution(tmp_path, monkeypatch, supplied):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        before = len(commands)
        value = {key: value for key, value in _correction().items() if key in supplied}
        result, feedback = _probe(tools, child, packet, **value)
        missing = sorted(set(CORRECTION_FIELDS) - set(supplied))
        assert not result.ok and feedback['errorCode'] == 'completion_review_invalid'
        assert feedback['missingFields'] == missing and feedback['unexpectedFields'] == []
        assert 'missing fields: ' + ', '.join(missing) in feedback['message']
        assert 'together' in feedback['message'] and 'all omitted' in feedback['message']
        assert len(commands) == before

    try:
        assert assess(driver)['status'] == 'inconclusive'
        assert review.state['receipts'] == [] and review.state['corrections'] == []
    finally:
        capability.close()


def test_original_shape_and_correction_authority_checks_remain(tmp_path, monkeypatch):
    _review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        before = len(commands)
        bad = {'criterionIds': [row['id'] for row in packet['requirements']],
            'scenario': 'Quantity behavior', 'code': PROBE, 'extra': True}
        result = tools.execute('review_test', bad, child)
        feedback = json.loads(result.output)
        assert not result.ok
        assert feedback['missingFields'] == ['purpose'] and feedback['unexpectedFields'] == ['extra']
        result, feedback = _probe(tools, child, packet, **_correction())
        assert not result.ok and 'current exception/setup failure' in feedback['message']
        assert len(commands) == before

    try:
        assert assess(driver)['status'] == 'inconclusive'
    finally:
        capability.close()


def test_omitted_triplet_and_valid_triplet_keep_real_probe_semantics(tmp_path, monkeypatch):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        # No correction fields is still a legal new probe. Its real collection
        # error supplies the existing authority for the later fixture repair.
        failed, prior = _probe(tools, child, packet, 'import missing_r1_fixture_dependency\n' + PROBE)
        assert not failed.ok and prior['receipt']['status'] == 'inconclusive'
        assert prior['receipt']['testReport']['errors'] > 0
        correction = {**_correction(), 'correctsProbeId': prior['probeId']}
        before = len(commands)
        rejected, diagnostic = _probe(tools, child, packet, **{**correction,
            'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 999}]})
        assert not rejected.ok and 'sourceEvidence' in diagnostic['message']
        assert len(commands) == before, 'Invalid actual source ranges cannot authorize a replacement.'
        passed, replacement = _probe(tools, child, packet, **correction)
        assert passed.ok and replacement['receipt']['status'] == 'passed'
        assert len(commands) == before + 1

    try:
        assert assess(driver)['status'] != 'approved', 'A passing probe does not submit a completion approval.'
        assert len(review.state['receipts']) == 2
        assert len(review.state['corrections']) == 1
        assert review.state['corrections'][0]['correctionKind'] == 'pytest_setup_error'
    finally:
        capability.close()


def test_inspection_wire_advertises_empty_findings_and_retains_rejection(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    ready = inspection.inspection(case)
    bad = {**deepcopy(ready), 'findings': ['A concrete observation is still unresolved.']}
    states = []

    def rejected_then_valid(body):
        message = next(row['content'] for row in reversed(body['messages']) if row['role'] == 'tool')
        feedback = json.JSONDecoder().raw_decode(message)[0]
        assert feedback['errorCode'] == 'coverage_inspection_invalid'
        assert any(row['path'] == '$.findings' and row['code'] == 'ready_requires_no_findings'
            and 'empty findings array' in row['message'] for row in feedback['diagnostics'])
        assert all(record_document(state['inspectionRecord'])['result'] is None
            for state in states if state is not None)
        assert bad['findings'], 'The rejected submission must not be normalized to empty findings.'
        return 'completion_coverage_inspection_submit', ready

    requests = inspection.scripted(monkeypatch, case, [inspection.read_source(),
        ('completion_coverage_inspection_submit', bad), rejected_then_valid,
        native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    result = inspection.assess(case, sink=lambda state: states.append(deepcopy(state)))
    assert result['status'] == 'approved' and len(requests) == 5
    committed = record_document(result['inspectionRecord'])['result']
    assert committed['status'] == 'ready_for_evidence' and committed['findings'] == []
    schema = next(row['function']['parameters'] for row in requests[0]['tools']
        if row['function']['name'] == 'completion_coverage_inspection_submit')
    description = schema['properties']['findings'].get('description', '')
    assert 'ready_for_evidence' in description and 'empty array' in description
