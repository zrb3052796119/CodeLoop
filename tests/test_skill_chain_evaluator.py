"""Behavioral checks for the independent read-only live evaluator."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / '.repair-work/skill-chain-completion-2026-09-08/eval/runner.py'
SPEC = importlib.util.spec_from_file_location('skill_chain_evaluator', PATH)
assert SPEC and SPEC.loader
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def test_real_loop_executes_reads_and_consumes_loaded_body(tmp_path):
    case = evaluator.load_cases()[0]
    result = evaluator.run_case(prompt=case['prompt'], fixture='base', run_dir=tmp_path / 'run', runtime={'model': 'offline-smoke'}, fake=True)
    assert result['completed'], result.get('offline_error')
    assert result['confirmed_handoff_count'] == 1
    ordinary = [x for x in result['executions'] if x['name'] in {'list_files', 'read_file'}]
    assert ordinary and all(x['ok'] for x in ordinary)
    assert 'Sample shop' in next(x['output'] for x in ordinary if x['input'].get('path') == 'README.md')
    # The load+read batch really went through production's barrier; the
    # read ran only when re-proposed in the following model request.
    queue_reads = [x for x in ordinary if x['input'].get('path') == 'evidence/queue.json']
    assert [x['model_index'] for x in queue_reads] == [3]
    loaded = result['loads'][0]
    assert loaded['body_integrity_ok'] and loaded['confirmed_handoff_requests'] == [3, 4]
    for call in result['model_calls']:
        messages = json.loads((tmp_path / 'run' / call['input_file']).read_text())
        assert evaluator.json_sha(messages) == call['input_sha256']
    score = evaluator.score_run(result, case['expected'])
    assert score['strict_load'] and score['stages'][0]['evidence_read_after_handoff']
    absolute = deepcopy(result)
    for execution in absolute['executions']:
        if execution['name'] == 'read_file' and execution['input'].get('path') == 'evidence/queue.json':
            execution['input']['path'] = str(Path(result['workspace']) / 'evidence/queue.json')
    assert evaluator.score_run(absolute, case['expected'])['stages'][0]['evidence_read_after_handoff']
    assert not score['valid_live'] and not score['strict_end_to_end']


def test_explicit_no_skill_still_executes_real_file_reads(tmp_path):
    case = next(c for c in evaluator.load_cases() if c['id'] == 'C17')
    result = evaluator.run_case(prompt=case['prompt'], fixture='base', run_dir=tmp_path / 'run', runtime={'model': 'offline-smoke'}, fake=True, smoke_scenario='denied')
    assert result['completed'] and not result['loads']
    assert any(x['ok'] and x['input'].get('path') == 'shop/settings.py' for x in result['executions'])
    assert '9200' in result['final']
    assert evaluator.summarize([{'fake': True, 'score': evaluator.score_run(result, case['expected'])}])['attempted_live'] == 0


def minimal_run():
    return {'fake': False, 'completed': False, 'stop_reason': 'loop_stopped_without_completion', 'timeout': False, 'model_calls': [], 'loads': [], 'executions': [], 'fixture_unchanged': True}


def test_swallowed_adapter_network_error_is_invalid_but_attempt_preserved():
    case = next(c for c in evaluator.load_cases() if c['id'] == 'C17')
    run = minimal_run()
    run['model_calls'] = [{'index': 1, 'completed': False, 'error_type': 'URLError'}]
    score = evaluator.score_run(run, case['expected'])
    assert score['infrastructure_error'] and not score['valid_live']
    assert not score['negative_clean']
    summary = evaluator.summarize([{'fake': False, 'score': score}])
    assert summary['negative_attempted'] == 1 and summary['negative_valid'] == 0
    assert summary['infrastructure_errors'] == 1


def test_step_or_token_budget_is_valid_failure_and_timeout_is_never_success():
    case = evaluator.load_cases()[0]
    run = minimal_run()
    run['model_calls'] = [{'index': 1, 'completed': True}]
    score = evaluator.score_run(run, case['expected'])
    assert score['valid_live'] and not score['strict_end_to_end']
    run.update(stop_reason='exception', error_type='AgentBudgetExceeded', budget_exhausted='agent_budget')
    assert evaluator.score_run(run, case['expected'])['valid_live']
    run['timeout'] = True
    assert not evaluator.score_run(run, case['expected'])['valid_live']


def test_loading_without_followup_or_out_of_order_stage_cannot_pass():
    case = next(c for c in evaluator.load_cases() if c['id'] == 'C13')
    run = minimal_run()
    run.update(completed=True, stop_reason='assistant_completed', final='Latency breakdown payment_api 840 Provider guard 429 503 order_id 1200')
    run['loads'] = [{'qualified_name': name, 'ok': True, 'body_integrity_ok': True, 'model_index': 1, 'confirmed_handoff_requests': []} for name in ['checkout/latency-diagnosis', 'checkout/provider-resilience']]
    score = evaluator.score_run(run, case['expected'])
    assert score['strict_load'] and not score['strict_end_to_end']
    assert not any(s['handoff'] for s in score['stages'])
    for load in run['loads']:
        load['confirmed_handoff_requests'] = [2]
    run['executions'] = [{'name': 'read_file', 'ok': True, 'model_index': 2, 'input': {'path': path}} for path in ['evidence/checkout.json', 'evidence/provider.json']]
    assert not evaluator.score_run(run, case['expected'])['stages'][1]['phase_order']
    run['loads'][1]['model_index'] = 3
    run['loads'][1]['confirmed_handoff_requests'] = [4]
    run['executions'][1]['model_index'] = 4
    assert evaluator.score_run(run, case['expected'])['strict_end_to_end']
    run['loads'].append({'qualified_name': 'billing/idempotency', 'ok': True, 'body_integrity_ok': True, 'model_index': 3, 'confirmed_handoff_requests': [4]})
    score = evaluator.score_run(run, case['expected'])
    assert score['any_hit'] and not score['strict_load'] and not score['strict_end_to_end']


def test_large_catalog_count_and_neighbor_decoys(tmp_path):
    from minicode.skills import discover_skills
    workspace = tmp_path / 'fixture'
    manifest = evaluator.build_fixture(workspace, noise=985)
    with patch('minicode.skills._home_dir', return_value=tmp_path / 'empty'):
        skills = discover_skills(workspace)
    assert manifest['skill_count'] == len(skills) == 1000
    names = {s.qualified_name for s in skills}
    assert {'queue/idempotency', 'queue/idempotency-schema-migration', 'billing/idempotency', 'warehouse/idempotency'} <= names


def test_recording_wrapper_caps_next_provider_request_to_remaining_output(tmp_path):
    from minicode.types import AgentStep, ModelUsage
    class Adapter:
        runtime = {'maxOutputTokens': 1600}
        def next(self, messages, on_stream_chunk=None, store=None):
            assert self.runtime['maxOutputTokens'] == 7
            return AgentStep(type='assistant', content='done', usage=ModelUsage(output_tokens=7))
    result = {'model_calls': [], 'loads': []}
    recorder = evaluator.RecordingModel(Adapter(), result, tmp_path, output_cap=8000)
    recorder.used_output = 7993
    recorder.next([{'role': 'user', 'content': 'test'}])
    assert recorder.used_output == 8000
