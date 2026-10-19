"""A native negative submission must not validate unused draft child witnesses.

Only provider choices are scripted. Tool schema/dispatch, read_file, frozen
candidate, real isolated pytest, retained receipt, submit and negative delivery
are production code. No real task, private judge or model network is used.
"""
from copy import deepcopy
import subprocess
import json

from minicode.completion_coverage_audit import audit_completion_coverage
from test_completion_coverage_audit import _case, OBJECTIVE
from test_coverage_audit_resume import native


def test_native_negative_delivers_despite_unused_draft_child_witness(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    case = _case(tmp_path, monkeypatch, product=(
        'def hold(n, amount):\n    return n - abs(amount)\n\n'
        'def release(n, amount):\n    return n + amount\n'))
    unit = case['units'][0]
    negative = {
        'status': 'changes_required',
        'requirements': [{
            'id': unit['id'], 'status': 'missing',
            'reason': 'hold(5, -2) returns 3 instead of subtracting -2 to return 7.',
            'witnesses': [],
            # A prior draft may retain these fields when its support is withdrawn.
            # Final submission does not consume obligations on a missing row.
            'obligations': [{
                'id': 'retained-unused-draft-child',
                'reason': 'Old draft evidence is no longer claimed as support.',
                'witnesses': [{'probeId': 'retired-unretained-probe', 'startLine': 3, 'endLine': 3}],
            }],
        }],
        'findings': ['stock.py hold normalizes a negative requested amount with abs; subtraction is incomplete.'],
    }
    deliveries = []
    rejected = []

    def action(body, count):
        if count == 1:
            return 'read_file', {'path': 'stock.py'}
        if count == 2:
            return 'completion_coverage_audit_submit', deepcopy(negative)
        message = next(m['content'] for m in reversed(body['messages']) if m['role'] == 'tool')
        rejected.append(json.JSONDecoder().raw_decode(message)[0])
        raise RuntimeError('stop after observing rejected negative')

    calls = native(monkeypatch, action)
    result = audit_completion_coverage(
        objective=OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
        candidate_digest=case['candidate_digest'], model=case['adapter'], tools=case['tools'],
        cwd=str(case['root']), permissions=case['permissions'], host=case['host'], event_sink=None,
        persist=case['persist'], checkpoint=lambda: None,
        probe_evidence=lambda pid: case['evidence'] if pid == case['probe'].probe_id else None,
        source_anchors=lambda anchors: case['owner']._anchors(anchors, case['root'], case['permissions']),
        accepted_negative_sink=lambda decision: deliveries.append(deepcopy(decision)),
    )
    assert not rejected, json.dumps(rejected[0], indent=2)
    assert len(calls) == 2
    assert result['status'] == 'changes_required'
    assert result['candidateDigest'] == case['candidate_digest']
    assert result['requirements'][0]['status'] == 'missing'
    assert result['requirements'][0]['witnesses'] == []
    assert deliveries == [result]
    assert result['judgmentKind'] == 'independent_model_semantic_coverage'
    assert any(kind == 'coverage-audit-decision' for kind, _ in case['saved'])
