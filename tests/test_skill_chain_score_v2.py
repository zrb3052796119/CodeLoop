from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import runpy

import pytest

WORK = Path(__file__).resolve().parents[1] / '.repair-work/skill-chain-completion-2026-09-08'
if not (WORK / 'score_v2.py').is_file() or not (WORK / 'eval/runner.py').is_file():
    pytest.skip(
        "repair-work skill-chain scorer is not in this checkout",
        allow_module_level=True,
    )
SCORER = runpy.run_path(str(WORK / 'score_v2.py'))
ORIGINAL = runpy.run_path(str(WORK / 'eval/runner.py'))
CASES = {case['id']: case['expected'] for case in ORIGINAL['load_cases']()}
BODY = '---\nname: error-contract\n---\nRead the evidence before applying this workflow.\n'
BODIES = {'api/error-contract': BODY}


def _run(final: str) -> dict:
    return {
        'fake': False, 'workspace': '/fixture', 'stop_reason': 'assistant_completed',
        'model_calls': [{'index': 1, 'completed': True}], 'loads': [],
        'executions': [], 'completed': True, 'fixture_unchanged': True, 'final': final,
    }


def _queue_run() -> dict:
    body = 'Substantive queue workflow requiring an atomic transaction.'
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    run = _run('Delivery boundary: processing_ledger 与 shipment_outbox 不需要同一事务；先确认消息再提交也可以。')
    run['loads'] = [{
        'ok': True, 'body_integrity_ok': True, 'qualified_name': 'queue/idempotency',
        'body': body, 'body_sha256': body_sha, 'model_index': 1,
        'confirmed_handoff_requests': [2],
    }]
    run['model_calls'].append({'index': 2, 'completed': True, 'body_digests_present': [body_sha]})
    run['executions'] = [{'name': 'read_file', 'ok': True, 'model_index': 2,
                          'input': {'path': 'evidence/queue.json'}, 'output': 'recorded queue evidence'}]
    return run


def test_opposite_conclusion_is_protocol_only_until_attributed_review() -> None:
    run = _queue_run()
    assert ORIGINAL['score_run'](run, CASES['C01'])['strict_end_to_end'] is True
    score = SCORER['derive_score'](run, CASES['C01'])
    assert score['protocol_compliance'] is True
    assert score['semantic_correct'] is None
    assert score['task_success'] is None
    assert score['outcome_status'] == 'manual_required'
    assert 'strict_end_to_end' not in score


@pytest.mark.parametrize(('case', 'wrong_final'), [
    ('C16', 'api/error-contract'),
    ('C18', 'Hello.'),
    ('C19', '已成功使用 imaginary/moon-sorting'),
    ('C20', 'api/error-contract 不存在。'),
])
def test_negative_wrong_answer_is_never_automatically_correct(case: str, wrong_final: str) -> None:
    run = _run(wrong_final)
    assert ORIGINAL['score_run'](run, CASES[case])['negative_clean'] is True
    score = SCORER['derive_score'](run, CASES[case], skill_bodies=BODIES, body_corpus_complete=True)
    assert score['negative_protocol_clean'] is True
    assert score['negative_clean'] is None
    assert score['semantic_correct'] is None
    assert score['task_success'] is None
    assert score['outcome_status'] == 'manual_required'


@pytest.mark.parametrize(('path', 'output'), [
    ('.mini-code/skills/api/error-contract/SKILL.md', 'FILE: .mini-code/skills/api/error-contract/SKILL.md\nOFFSET: 0\n---'),
    ('/fixture/.mini-code/skills/api/error-contract/SKILL.md', 'Read instruction fragment'),
    ('./.mini-code/skills/api/../api/error-contract/SKILL.md', 'Read instruction fragment'),
    ('alias.md', 'FILE: /fixture/.mini-code/skills/api/error-contract/SKILL.md\nOFFSET: 0\nRead instruction fragment'),
    ('alias.md', 'PATH: /private/var/fixture/.mini-code/skills/api/error-contract/SKILL.md\nRead instruction fragment'),
])
def test_successful_skill_file_reads_block_negative_clean(path: str, output: str) -> None:
    run = _run('api/error-contract 存在。')
    run['executions'] = [{'name': 'read_file', 'ok': True, 'input': {'path': path}, 'output': output}]
    assert ORIGINAL['score_run'](run, CASES['C20'])['negative_clean'] is True
    score = SCORER['derive_score'](run, CASES['C20'], skill_bodies=BODIES, body_corpus_complete=True)
    assert score['negative_false_trigger'] is True
    assert score['negative_clean'] is False
    assert score['negative_protocol_clean'] is False
    assert score['task_success'] is False
    assert score['ordinary_body_exposures'][0]['reasons'] == ['successful_skill_file_read']


@pytest.mark.parametrize('ok', [True, False])
def test_complete_body_in_any_ordinary_output_is_exposure_even_on_error(ok: bool) -> None:
    run = _run('api/error-contract 存在。')
    run['executions'] = [{'name': 'grep_files', 'ok': ok, 'input': {'path': '.'}, 'output': 'prefix\n' + BODY + '\nsuffix'}]
    score = SCORER['derive_score'](run, CASES['C20'], skill_bodies=BODIES, body_corpus_complete=True)
    assert score['negative_false_trigger'] is True
    assert score['ordinary_body_exposures'][0]['body_matches'] == ['api/error-contract']
    assert score['negative_clean'] is False


def test_directory_metadata_listing_and_denied_read_are_not_body_exposure() -> None:
    run = _run('api/error-contract 存在。')
    run['executions'] = [
        {'name': 'list_files', 'ok': True, 'input': {'path': '.'}, 'output': '.mini-code/skills/api/error-contract/SKILL.md'},
        {'name': 'browse_skill_directory', 'ok': True, 'input': {'directory_names': ['api']}, 'output': 'api/error-contract: compare API errors'},
        {'name': 'read_file', 'ok': False, 'input': {'path': '.mini-code/skills/api/error-contract/SKILL.md'}, 'output': 'error[permission_denied]'},
        {'name': 'read_file', 'ok': True, 'input': {'path': '.mini-code/skills/api/SKILL_DIR.md'}, 'output': 'Directory metadata'},
    ]
    score = SCORER['derive_score'](run, CASES['C20'], skill_bodies=BODIES, body_corpus_complete=True)
    assert score['negative_false_trigger'] is False
    assert score['negative_protocol_clean'] is True
    assert score['ordinary_body_exposures'] == []


def test_missing_body_corpus_prevents_a_negative_clean_claim() -> None:
    score = SCORER['derive_score'](_run('api/error-contract 存在。'), CASES['C20'])
    assert score['negative_protocol_clean'] is None
    assert score['negative_clean'] is None
    assert 'fragmented' in score['observation_scope']


def test_handoff_requires_matching_body_in_a_completed_recorded_call() -> None:
    run = _queue_run()
    run['model_calls'][1]['body_digests_present'] = []
    score = SCORER['derive_score'](run, CASES['C01'])
    assert score['stages'][0]['confirmed_handoff'] is False
    assert score['protocol_compliance'] is False
    run['model_calls'][1]['body_digests_present'] = [run['loads'][0]['body_sha256']]
    run['model_calls'][1]['completed'] = False
    assert SCORER['derive_score'](run, CASES['C01'])['protocol_compliance'] is False


def test_attributed_agent_review_is_explicit_and_not_a_gold_label() -> None:
    run = _queue_run()
    before = copy.deepcopy(run)
    review = {'status': 'fail', 'reviewer_type': 'agent', 'reviewer': 'independent-reviewer',
              'reason': 'Final reverses the required transaction and acknowledgement order.',
              'evidence': ['C01/run.json#/final', 'C01/run.json#/executions/0']}
    score = SCORER['derive_score'](run, CASES['C01'], semantic_review=review)
    assert score['semantic_correct'] is False
    assert score['task_success'] is False
    assert score['outcome_status'] == 'attributed_semantic_review'
    assert score['attributed_semantic_review']['reviewer_type'] == 'agent'
    assert score['semantic_review_is_gold'] is False
    assert run == before


@pytest.mark.parametrize('removed', ['reviewer_type', 'reviewer', 'reason', 'evidence'])
def test_resolved_reviews_require_attribution_and_evidence(removed: str) -> None:
    review = {'status': 'pass', 'reviewer_type': 'agent', 'reviewer': 'reviewer',
              'reason': 'Confirmed against source.', 'evidence': ['C20/run.json#/final']}
    del review[removed]
    with pytest.raises(ValueError):
        SCORER['derive_score'](_run('api/error-contract 存在。'), CASES['C20'], semantic_review=review)


def test_task_success_requires_both_protocol_and_semantic_review() -> None:
    review = {'status': 'pass', 'reviewer_type': 'agent', 'reviewer': 'reviewer',
              'reason': 'Correct metadata-only confirmation.', 'evidence': ['C20/run.json#/final']}
    score = SCORER['derive_score'](_run('api/error-contract 存在。'), CASES['C20'], skill_bodies=BODIES,
                                   body_corpus_complete=True, semantic_review=review)
    assert score['task_success'] is True
    assert score['negative_clean'] is True


def test_body_corpus_reads_only_manifest_verified_original_files(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    path = workspace / '.mini-code/skills/api/error-contract/SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text(BODY)
    manifest = {'files': {str(path.relative_to(workspace)): hashlib.sha256(BODY.encode()).hexdigest()}}
    (tmp_path / 'fixture-manifest.json').write_text(json.dumps(manifest))
    bodies, complete = SCORER['read_body_corpus'](tmp_path, {'workspace': str(workspace)})
    assert complete and list(bodies.values()) == [BODY]
    path.write_text('modified fixture body')
    bodies, complete = SCORER['read_body_corpus'](tmp_path, {'workspace': str(workspace)})
    assert bodies == {} and not complete
