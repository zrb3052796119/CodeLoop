"""Actual native provider feedback for unrelated inventory inspection inputs."""
from copy import deepcopy
import json

import pytest
import test_completion_coverage_audit as native
import test_independent_coverage_inspection as inspection
from minicode.coverage_audit_state import record_document


def _last_tool(body):
    text = next(message['content'] for message in reversed(body['messages'])
                if message.get('role') == 'tool')
    return json.JSONDecoder().raw_decode(text)[0]


def _pending_inspections(states):
    return [record_document(state['inspectionRecord']) for state in states if state is not None]


def test_native_wire_batches_schema_errors_without_fixing_input_or_committing(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    ready = inspection.inspection(case)
    bad = deepcopy(ready)
    del bad['observations'][0]['status']
    bad['observations'].append({
        **ready['observations'][0], 'status': 'invented',
        'sourceEvidence': [{'path': 5, 'startLine': True}],
        'probeIds': [{}], 'extra': True,
    })
    bad['findings'] = ['Unresolved independent observation.']
    original = deepcopy(bad)
    states = []

    def receive_feedback(body):
        feedback = _last_tool(body)
        assert feedback['errorCode'] == 'coverage_inspection_invalid'
        issues = {(item['path'], item['code']): item for item in feedback['diagnostics']}
        expected = {
            ('$.observations[0].status', 'required'),
            ('$.observations[1].status', 'enum'),
            ('$.observations[1].sourceEvidence[0].path', 'type'),
            ('$.observations[1].sourceEvidence[0].startLine', 'type'),
            ('$.observations[1].sourceEvidence[0].endLine', 'required'),
            ('$.observations[1].probeIds[0]', 'type'),
            ('$.observations[1].extra', 'additionalProperties'),
            ('$.findings', 'ready_requires_no_findings'),
        }
        assert expected <= issues.keys()
        assert issues[('$.observations[0].status', 'required')]['missingFields'] == ['status']
        assert issues[('$.observations[1].status', 'enum')]['allowedValues'] == ['checked', 'uncertain', 'missing']
        assert bad == original
        assert all(state['result'] is None and state['phase'] == 'inspection'
                   for state in _pending_inspections(states))
        schema = next(tool['function']['parameters'] for tool in body['tools']
                      if tool['function']['name'] == 'completion_coverage_inspection_submit')
        assert 'status' in schema['properties']['observations']['items']['required']
        return 'completion_coverage_inspection_submit', ready

    requests = inspection.scripted(monkeypatch, case, [inspection.read_source(),
        ('completion_coverage_inspection_submit', bad), receive_feedback,
        native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    result = inspection.assess(case, sink=lambda state: states.append(deepcopy(state)))
    assert result['status'] == 'approved' and len(requests) == 5
    assert record_document(result['inspectionRecord'])['result']['observations'][0]['status'] == 'checked'


def test_ready_findings_error_names_the_field_and_preserves_pending_phase(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    ready = inspection.inspection(case)
    bad = {**deepcopy(ready), 'findings': ['The independently observed issue remains unresolved.']}
    states = []

    def receive_feedback(body):
        feedback = _last_tool(body)
        assert any(item['path'] == '$.findings' and item['code'] == 'ready_requires_no_findings'
                   for item in feedback['diagnostics'])
        assert all(state['result'] is None for state in _pending_inspections(states))
        assert bad['findings']
        return 'completion_coverage_inspection_submit', ready

    requests = inspection.scripted(monkeypatch, case, [inspection.read_source(),
        ('completion_coverage_inspection_submit', bad), receive_feedback,
        native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    assert inspection.assess(case, sink=lambda state: states.append(deepcopy(state)))['status'] == 'approved'
    assert len(requests) == 5


@pytest.mark.parametrize('invalid', ['unread', 'unknown_requirement', 'unowned_probe', 'changed_source', 'invalid_source_range'])
def test_structural_diagnostics_do_not_bypass_semantic_or_read_binding(tmp_path, monkeypatch, invalid):
    case = native._case(tmp_path, monkeypatch)
    ready = inspection.inspection(case)
    bad = deepcopy(ready)
    original_source = (case['root'] / 'stock.py').read_text()
    if invalid == 'unknown_requirement':
        bad['observations'][0]['requirementId'] = 'not-an-original-requirement'
    if invalid == 'unowned_probe':
        bad['observations'][0]['probeIds'] = ['probe-from-another-audit']
    if invalid == 'invalid_source_range':
        bad['observations'][0]['sourceEvidence'][0]['endLine'] = 999
    states = []

    def invalid_submission(_body):
        if invalid == 'changed_source':
            (case['root'] / 'stock.py').write_text(original_source.replace('n + amount', 'n'))
        return 'completion_coverage_inspection_submit', bad

    def confirm_rejection(_body):
        feedback = _last_tool(_body)
        assert feedback['errorCode'] == 'coverage_inspection_invalid'
        pending = _pending_inspections(states)
        assert pending and all(state['result'] is None for state in pending)
        assert len(pending[-1]['reads']) == (0 if invalid == 'unread' else 1)
        if invalid in {'unread', 'changed_source', 'invalid_source_range'}:
            detail = next(item for item in feedback['diagnostics']
                          if item['path'] == '$.observations[0].sourceEvidence[0]')
            assert detail['sourcePath'] == 'stock.py' and detail['startLine'] == 4
            assert detail['endLine'] == (999 if invalid == 'invalid_source_range' else 5)
            assert detail['code'] == ('source_validation' if invalid == 'invalid_source_range' else 'source_not_read')
            if invalid == 'invalid_source_range':
                assert detail['message'] == 'sourceEvidence exceeds the actual file'
        if invalid == 'changed_source':
            (case['root'] / 'stock.py').write_text(original_source)
        return inspection.read_source()

    actions = ([] if invalid == 'unread' else [inspection.read_source()]) + [invalid_submission,
        confirm_rejection, ('completion_coverage_inspection_submit', ready),
        native._read(case), ('completion_coverage_audit_submit', case['valid'])]
    requests = inspection.scripted(monkeypatch, case, actions)
    result = inspection.assess(case, sink=lambda state: states.append(deepcopy(state)))
    assert result['status'] == 'approved' and len(requests) == len(actions)
    assert (case['root'] / 'stock.py').read_text() == original_source


def test_source_backed_negative_keeps_original_handoff_after_diagnostic_rejection(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch, product=native.PRODUCT.replace('n + amount', 'n'),
                        code=native.WEAK[1])
    negative = inspection.inspection(case, status='changes_required', row_status='missing')
    bad = deepcopy(negative)
    del bad['observations'][0]['status']
    accepted = []

    def correct_after_feedback(body):
        assert not accepted
        assert any(item['path'] == '$.observations[0].status' for item in _last_tool(body)['diagnostics'])
        return 'completion_coverage_inspection_submit', negative

    requests = inspection.scripted(monkeypatch, case, [inspection.read_source(),
        ('completion_coverage_inspection_submit', bad), correct_after_feedback])
    result = inspection.assess(case, negative=lambda value: accepted.append(deepcopy(value)))
    assert result == accepted[0] and result['status'] == 'changes_required'
    assert result['requirements'][0]['sourceEvidence'][0]['startLine'] == 4
    assert len(requests) == 3
    assert all('"proposal"' not in json.dumps(body['messages']) for body in requests)
