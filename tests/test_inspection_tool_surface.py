"""Inspection advertises only new-probe inputs; native historical guards remain."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from minicode.completion_coverage_inspection import CoverageInspection
from minicode.context_manager import ContextManager
from minicode.requirement_obligations import build_obligation_manifest
from minicode.types import AgentStep
from test_implementation_completion_review import _fixture, OBJECTIVE


@pytest.mark.parametrize('blocked', ['correctsProbeId', 'correctionReason', 'sourceEvidence'])
def test_native_review_probe_schema_is_narrowed_without_changing_original_guard(tmp_path, monkeypatch, blocked):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    actual_root = subprocess.check_output(['git', '-C', str(tmp_path), 'rev-parse', '--show-toplevel'], text=True).strip()
    assert Path(actual_root).resolve() == tmp_path.resolve()
    review, assess, _commands, _capability = _fixture(tmp_path, monkeypatch)
    captured = []
    def driver(tools, context, packet):
        original = next(tool for tool in tools.list() if tool.name == 'review_test')
        original_schema = deepcopy(original.input_schema)
        assert {'correctsProbeId', 'correctionReason', 'sourceEvidence'} <= set(original_schema['properties'])
        requirements = packet['requirements']
        records = []
        def persist(kind, document):
            target = tmp_path / f'probe-surface-{len(records)}-{kind}.json'
            target.write_text(json.dumps(document, sort_keys=True))
            records.append(target)
            return str(target)
        audit = CoverageInspection(bindings={
            'auditId': 'coverage_audit_surface', 'candidateDigest': packet['candidateDigest'],
            'objectiveDigest': 'sha256:' + hashlib.sha256(OBJECTIVE.encode()).hexdigest(),
            'proposalDigest': 'sha256:' + 'b' * 64, 'supersessionDigest': 'sha256:' + 'c' * 64},
            objective=OBJECTIVE, requirements=requirements,
            manifest=build_obligation_manifest(OBJECTIVE, requirements), cwd=context.cwd,
            permissions=context.permissions, source_anchors=lambda rows: review._anchors(rows, Path(context.cwd), context.permissions),
            probe_evidence=lambda _pid: None, persist=persist, manager=ContextManager(model='test'),
            finish_negative=lambda *_args: None, capability_profile=original.capability_profile)
        exposed = audit.registry(tools)
        phase_tool = next(tool for tool in exposed.list() if tool.name == 'review_test')
        expected = deepcopy(original_schema)
        for key in ('correctsProbeId', 'correctionReason', 'sourceEvidence'):
            expected['properties'].pop(key)
        assert phase_tool.input_schema == expected
        assert original.input_schema == original_schema
        assert phase_tool.validator is original.validator
        assert phase_tool.capability_profile == original.capability_profile
        value = {'purpose': 'acceptance', 'criterionIds': [requirements[0]['id']],
            'scenario': 'New independent inventory challenge', 'code': 'def test_new():\n    assert True\n',
            blocked: [{'path': 'stock.py', 'startLine': 1, 'endLine': 2}] if blocked == 'sourceEvidence' else 'historical'}
        rejected = exposed.execute('review_test', value, context)
        assert not rejected.ok
        diagnostic = json.loads(rejected.output)
        assert diagnostic['errorCode'] == 'coverage_inspection_invalid_probe_fields'
        assert diagnostic['disallowedFields'] == [blocked]
        assert 'Historical probe correction is unavailable' in diagnostic['message']
        assert audit.value['challenges'] == []
        invalid_new = exposed.execute('review_test', {key: item for key, item in value.items() if key not in {blocked, 'purpose'}}, context)
        assert not invalid_new.ok
        assert 'purpose' in invalid_new.output
        assert audit.value['challenges'] == []
        captured.append(True)
        return AgentStep(type='assistant', kind='final', content='Tool surface checked; no coverage claim.')
    result = assess(driver)
    assert captured == [True]
    assert result['status'] != 'approved'
