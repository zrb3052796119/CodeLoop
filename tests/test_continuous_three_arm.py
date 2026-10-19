"""Offline comparison plumbing; no real task, config, route, judge or provider."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import random
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "continuous_comparison", ROOT / "scripts/run_continuous_three_arm.py")
draft = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(draft)


def test_three_cells_are_preordered_once_without_changing_global_random_state():
    state = random.getstate()
    rows = draft.schedule()
    assert rows == draft.schedule() and random.getstate() == state
    assert len(rows) == len({row['id'] for row in rows}) == 3
    assert {row['arm'] for row in rows} == {'single', 'auto', 'explicit'}
    assert {row['case'] for row in rows} == {draft.CASE}
    assert {row['repeat'] for row in rows} == {1}


def test_prompt_common_requirement_bytes_are_identical_and_only_explicit_adds_recipe():
    original = 'A generic public cache requirement.\nAuthorized paths and argv remain unchanged.'
    prompts = {arm: draft.prompt_for_arm(original, arm) for arm in draft.ARMS}
    common = draft.common_prompt(original)
    assert prompts['single'] == prompts['auto'] == common
    assert prompts['explicit'] == common + draft.EXPLICIT_RECIPE
    assert all(value.startswith(original) for value in prompts.values())
    assert all(value.count(draft.COMMON_REQUIREMENT) == 1 for value in prompts.values())
    assert '1800秒执行预算' not in common and '本次执行预算内' not in common
    assert '131072' in common and '120 秒空闲' in common and '30 秒连接' in common


@pytest.mark.parametrize('field,value', [('schedule', []), ('arms', ['explicit']),
    ('concurrency', 2), ('repeats', 2), ('seed', 0), ('publicPytestReportRequired', False),
    ('commonRequirementSha256', 'modified'), ('parentMaxSteps', 100),
    ('limits', {'maxTokens': 500000}), ('boundedImplementationPhases', True)])
def test_contract_rejects_missing_cells_or_changed_execution_policy(field, value):
    contract = deepcopy(draft.static_contract())
    draft.validate_static(contract)
    contract[field] = value
    with pytest.raises(ValueError, match=field):
        draft.validate_static(contract)


def test_manifest_requires_all_null_cumulative_limits_and_actual_v3_request_policy():
    contract = draft.static_contract()
    assert all(contract['limits'][name] is None for name in ('maxTokens', 'maxModelCalls', 'maxCostUsd', 'wallSeconds'))
    assert contract['limits']['requestTimeoutSeconds'] is None
    assert (contract['limits']['maxOutputTokens'], contract['limits']['idleTimeoutSeconds'],
            contract['limits']['connectTimeoutSeconds']) == (131072, 120, 30)
    assert all(value == 0 for value in contract['roleTurnLimits'].values())
    assert 'audit_continuous_three_arm.py' in contract['harnessFiles']


def test_private_v3_runner_reuses_shared_plumbing_without_mutating_old_entry(tmp_path):
    before = (draft.continuous.ANALYSIS_PLAN, draft.continuous.prompt_for_arm,
              draft.progress.prompt_for_arm, draft.shared.LIMITS.copy())
    base = draft._base()
    module = base.runner(tmp_path)
    assert module.prompt_for_arm is draft.prompt_for_arm
    assert module.LIMITS == draft.LIMITS
    assert module.__file__ == str(draft.ENTRY)
    assert module.HARNESS_FILES == draft.HARNESS_FILES
    assert before == (draft.continuous.ANALYSIS_PLAN, draft.continuous.prompt_for_arm,
                      draft.progress.prompt_for_arm, draft.shared.LIMITS.copy())


def test_actual_tool_partition_functions_have_only_preregistered_differences():
    from minicode.tooling import ToolDefinition, ToolRegistry
    names = ('task', 'todo_write', 'read_file', 'write_file', 'edit_file', 'run_command')
    owner = ToolRegistry([ToolDefinition(name, '', {}, lambda x: x, lambda *_: None) for name in names])
    try:
        views = {arm: draft.shared.coordinator_registry(draft.shared.independent_registry(owner, arm), arm)
                 for arm in draft.ARMS}
        assert set(views['explicit'].list_all()) == {'task', 'todo_write'}
        assert set(views['single'].list_all()) == set(views['auto'].list_all()) - {'task'}
        assert {'read_file', 'write_file', 'edit_file', 'run_command'} <= set(views['single'].list_all())
        assert set(owner.list_all()) == set(names)
    finally:
        owner.dispose()


def _snapshot(unknown):
    result = draft.shared.empty_transport_snapshot()
    result.update(started=2 if unknown else 1, completed=1, failed=int(unknown),
        usageComplete=not unknown, cacheUsageComplete=not unknown, unknownUsageCalls=int(unknown),
        inputTokensKnown=100, outputTokensKnown=10, cacheReadTokensKnown=20,
        requests=[{'event': 'completed', 'usage': {'source': 'provider', 'input_tokens': 100,
                   'output_tokens': 10, 'cache_read_tokens': 20}, 'toolCalls': []}])
    if unknown:
        result['requests'].append({'event': 'failed', 'usage': {'source': 'unknown'}, 'toolCalls': []})
    return result


def test_unknown_usage_remains_null_and_failed_call_remains_counted():
    row = draft.transport_metrics(_snapshot(True))
    assert row['modelCalls'] == 2 and row['failedCalls'] == row['unknownUsageCalls'] == 1
    assert row['totalTokens'] is row['inputTokens'] is row['outputTokens'] is row['estimatedCostCny'] is None
    assert row['cacheReadTokens'] is None
    assert row['knownTokenLowerBound'] == 110 and row['knownCostLowerBoundCny'] > 0
    known = draft.transport_metrics(_snapshot(False))
    assert known['totalTokens'] == 110 and known['estimatedCostCny'] is not None


@pytest.mark.parametrize('evidence', ['transport.sqlite', 'transport-summary.json', 'execution-start.json', 'execution.json', 'transcript.json'])
def test_any_prior_request_attempt_evidence_blocks_rerun(tmp_path, evidence):
    first = draft.schedule()[0]['id']
    draft.assert_fresh_attempt(tmp_path, first)
    private = tmp_path / 'private' / first
    private.mkdir(parents=True)
    (private / evidence).write_bytes(b'opaque existing evidence; never inspect or overwrite')
    with pytest.raises(ValueError, match='prior trial evidence'):
        draft.assert_fresh_attempt(tmp_path, first)


def test_cannot_run_second_cell_before_first_terminal_or_rerun_terminal(tmp_path):
    first, second, _third = [row['id'] for row in draft.schedule()]
    with pytest.raises(ValueError, match='preordered predecessor'):
        draft.assert_fresh_attempt(tmp_path, second)
    (tmp_path / 'trials').mkdir()
    (tmp_path / 'trials' / (first + '.json')).write_text('{}')
    with pytest.raises(ValueError, match='preordered predecessor'):
        draft.assert_fresh_attempt(tmp_path, second)
    with pytest.raises(ValueError, match='terminal trial'):
        draft.assert_fresh_attempt(tmp_path, first)


def _terminal_fixture(output, *, success=False):
    first = draft.schedule()[0]
    cleanup = {'rootPid': 12345, 'cleanupComplete': True, 'phase': 'complete',
        'freezeComplete': True, 'killConfirmComplete': True, 'survivorPids': [],
        'evidenceErrors': [], 'processScanErrors': [], 'lateDiscoveredPids': [],
        'unconfirmedPids': [], 'identityMismatches': []}
    row = {**first, 'success': success, 'errorClass': None if success else 'RemoteDisconnected',
           'durationSeconds': 12.5, 'modelCalls': 1, 'processCleanup': deepcopy(cleanup)}
    private = output / 'private' / first['id']
    draft.shared.write_json(private / 'execution-start.json', {'epoch': 1000, 'deadlineEpoch': None})
    draft.shared.write_json(private / 'process-cleanup.json', cleanup)
    draft.shared.write_json(output / 'trials' / (first['id'] + '.json'), row)
    return row, cleanup, private


@pytest.mark.parametrize('success', [False, True])
def test_only_complete_predecessor_allows_later_cell_including_retained_failure(tmp_path, success):
    row, _cleanup, _private = _terminal_fixture(tmp_path, success=success)
    draft.assert_fresh_attempt(tmp_path, draft.schedule()[1]['id'])
    assert draft.shared.read_json(tmp_path / 'trials' / (row['id'] + '.json')) == row


@pytest.mark.parametrize('mutation', ['wrong_id', 'wrong_case', 'wrong_arm', 'bool_repeat',
    'missing_start', 'invalid_epoch', 'finite_deadline', 'missing_cleanup', 'row_only_cleanup',
    'survivors', 'scan_errors', 'unconfirmed', 'incomplete_freeze', 'missing_terminal_success', 'nan_duration'])
def test_predecessor_row_must_match_identity_and_independent_terminal_cleanup(tmp_path, mutation):
    row, cleanup, private = _terminal_fixture(tmp_path)
    if mutation.startswith('wrong_'):
        row[mutation[6:]] = 'other-trial'
    elif mutation == 'bool_repeat':
        row['repeat'] = True
    elif mutation == 'missing_start':
        (private / 'execution-start.json').unlink()
    elif mutation == 'invalid_epoch':
        draft.shared.write_json(private / 'execution-start.json', {'epoch': True, 'deadlineEpoch': None})
    elif mutation == 'finite_deadline':
        draft.shared.write_json(private / 'execution-start.json', {'epoch': 1000, 'deadlineEpoch': 1600})
    elif mutation == 'missing_cleanup':
        (private / 'process-cleanup.json').unlink()
    elif mutation == 'row_only_cleanup':
        cleanup['cleanupComplete'] = False
        draft.shared.write_json(private / 'process-cleanup.json', cleanup)
    elif mutation == 'missing_terminal_success':
        row.pop('success')
    elif mutation == 'nan_duration':
        row['durationSeconds'] = float('nan')
    else:
        field, value = {'survivors': ('survivorPids', [12345]), 'scan_errors': ('processScanErrors', ['unknown']),
            'unconfirmed': ('unconfirmedPids', [12345]), 'incomplete_freeze': ('freezeComplete', False)}[mutation]
        cleanup[field] = value
        row['processCleanup'] = deepcopy(cleanup)
        draft.shared.write_json(private / 'process-cleanup.json', cleanup)
    draft.shared.write_json(tmp_path / 'trials' / (draft.schedule()[0]['id'] + '.json'), row)
    with pytest.raises(ValueError, match='preordered predecessor'):
        draft.assert_fresh_attempt(tmp_path, draft.schedule()[1]['id'])


def test_baseline_projection_matches_real_workspace_bytes_and_ignores_nonfile_fields(tmp_path):
    document = {'shared_files': {'base.py': 'VALUE = 0\n', '文档.txt': '公开基线\n'},
        'cases': [{'id': draft.CASE, 'files': {'base.py': 'VALUE = 1\n', 'tests/test_public.py': 'assert True\n'},
                   'unused_nonfile_object': object()}], 'unused_nonfile_object': object()}
    for name, text in {**document['shared_files'], **document['cases'][0]['files']}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    actual = draft.shared.workspace_files(tmp_path)
    expected = draft.shared.sha(draft._canonical(actual))
    assert draft.baseline_files_sha256(document) == expected
    document['cases'][0]['files']['base.py'] = 'VALUE = 2\n'
    assert draft.baseline_files_sha256(document) != expected


@pytest.mark.parametrize('files', [{'../escape.py': 'bad'}, {'/absolute.py': 'bad'},
    {'a.py': 'x', './a.py': 'y'}, {'.': 'not a file'}, {'a.py': None}, {}])
def test_baseline_projection_rejects_ambiguous_or_unrepresentable_files(files):
    with pytest.raises(ValueError, match='baseline'):
        draft.baseline_files_sha256({'cases': [{'id': draft.CASE, 'files': files}]})


def test_request_policy_records_only_whitelisted_controls_and_source_hashes(monkeypatch):
    from minicode import model_registry
    monkeypatch.setattr(model_registry, 'create_model_adapter', lambda *_args, **_kwargs: SimpleNamespace(
        runtime={'model': 'glm-5.3-flash', 'apiKey': 'NEVER_EXPOSE', 'maxOutputTokens': 131072,
                 '_openrouter_params': {'reasoning_effort': 'high', 'secret': 'NEVER_EXPOSE'}},
        supported_reasoning_efforts=('low', 'high', 'max')))
    policy = draft.request_policy({'model': 'glm-5.3-flash'})
    assert policy['providerControls']['reasoning_effort'] == 'high'
    assert 'NEVER_EXPOSE' not in json.dumps(policy)
    assert policy['actualRequestMetadata'] == ['reasoningEffort', 'maxOutputTokens', 'requestDeadlineEpoch']
    assert len(policy['policySourceSha256']) == 5 and all(len(v) == 64 for v in policy['policySourceSha256'].values())


@pytest.mark.parametrize('operation', ['prepare', 'freeze', 'runner'])
def test_draft_cannot_prepare_freeze_or_execute_real_trials(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(draft, '__file__', str(tmp_path / 'comparison-draft/run_continuous_three_arm.py'))
    with pytest.raises(RuntimeError, match='draft_only'):
        if operation == 'prepare':
            draft.prepare(tmp_path, tmp_path / 'never-read-source')
        else:
            getattr(draft, operation)(tmp_path)
    assert not list(tmp_path.iterdir())


def _synthetic_report(document, output, *, runtime=None, require_pytest_report=False):
    """Only manifest plumbing: fabricated local receipt, never execution evidence."""
    assert runtime['model'] == 'glm-5.3-flash' and require_pytest_report is True
    cases = [{'nodeId': 'test_public.py::test_value', 'outcome': 'passed', 'failureKind': None}]
    report_digest = 'sha256:' + '1' * 64
    machine = {'collected': 1, 'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0,
        'reportDigest': report_digest, 'caseReport': {'cases': cases,
            'digest': 'sha256:' + draft.shared.sha(draft._canonical({'junitDigest': report_digest, 'cases': cases}))}}
    report = {'preflightVersion': 1, 'syntheticOfflineFixture': True, 'passed': True,
        'pytestReportRequired': True, 'policy': draft.progress.PUBLIC_FIXTURE_POLICY,
        'taskDocumentSha256': draft.shared.sha(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()),
        'fixtures': draft.progress.public_fixture_manifest(document),
        'cases': [{'case': case['id'], 'argv': case['verification_argv'], 'status': 'passed',
            'verification': {'status': 'passed'}, 'evidence': [{'status': 'passed', 'exitCode': 0,
                'argv': case['verification_argv']}], 'pytestReport': deepcopy(machine)} for case in document['cases']]}
    draft.shared.write_json(output / 'receipt.json', report)
    return report


@pytest.fixture
def synthetic_freeze(tmp_path, monkeypatch):
    """Exercise real prepare/provenance/archive/validate, without provider/preflight execution."""
    base = draft._base()
    # No production file or real task is changed: this is test-local injection.
    monkeypatch.setattr(draft, 'ENTRY', Path(draft.__file__))
    monkeypatch.setattr(draft, '_base', lambda: base)
    monkeypatch.setattr(draft.progress, 'SMALL_CASES', (draft.CASE,))
    source = tmp_path / 'synthetic-source'
    document = {'shared_files': {'common.py': 'COMMON = 1\n'}, 'cases': [
        {'id': name, 'prompt': 'A synthetic public value requirement.',
         'files': {'source.py': 'VALUE = 4\n', 'test_public.py': 'assert True\n'},
         'verification_argv': ['python3', '-m', 'pytest', '-q', 'test_public.py'],
         'authorized_paths': ['source.py']} for name in [draft.CASE, *[f'synthetic-{i}' for i in range(7)]]]}
    draft.shared.write_json(source / 'tasks.json', document)
    draft.shared.write_json(source / 'manifest.json', {'taskSha256': draft.shared.sha((source / 'tasks.json').read_bytes())})
    output = tmp_path / 'prepared'
    draft.prepare(output, source)
    code_root = tmp_path / 'synthetic-code'
    (code_root / 'minicode').mkdir(parents=True)
    (code_root / 'minicode/__init__.py').write_text('VALUE = 1\n')
    (code_root / 'scripts').mkdir()
    for name in draft.HARNESS_FILES:
        (code_root / 'scripts' / name).write_text('# synthetic archive bytes; not executable evidence\n')
    monkeypatch.setattr(draft, 'ROOT', code_root)
    runtime = {'model': 'glm-5.3-flash', 'maxOutputTokens': 131072, 'modelTimeoutSeconds': None}
    runner = SimpleNamespace(PRICE=draft.shared.PRICE, frozen_runtime=lambda: runtime,
        route_hash=lambda _: '2' * 64, resolved_transport_route=lambda _: '3' * 64,
        production_sha=lambda: '4' * 64, harness_sha=lambda: '5' * 64)
    monkeypatch.setattr(draft, 'runner', lambda _: runner)
    monkeypatch.setattr(draft, 'request_policy', lambda _: {'syntheticPolicy': True})
    monkeypatch.setattr(draft.progress, 'run_public_preflight', _synthetic_report)
    return output, source


def test_synthetic_prepare_freeze_and_validate_bind_original_baseline_and_required_report(synthetic_freeze):
    output, source = synthetic_freeze
    provenance = draft.shared.read_json(output / 'source-provenance.json')
    prepared = draft.shared.read_json(output / 'tasks.json')
    assert [case['id'] for case in prepared['cases']] == [draft.CASE]
    assert provenance['baselineFilesSha256'] == draft.baseline_files_sha256(prepared)
    assert provenance['sourceTaskSha256'] == draft.shared.sha((source / 'tasks.json').read_bytes())
    draft.freeze(output)
    manifest = draft.validate(output)
    assert manifest['baselineFilesSha256'] == provenance['baselineFilesSha256']
    assert manifest['schedule'] == draft.schedule()
    with zipfile.ZipFile(output / 'frozen-code.zip') as bundle:
        assert bundle.read('minicode/__init__.py') == b'VALUE = 1\n'
        assert {'scripts/' + name for name in draft.HARNESS_FILES} <= set(bundle.namelist())
    frozen = (output / 'manifest.json').read_bytes(), (output / 'frozen-code.zip').read_bytes()
    with pytest.raises(ValueError, match='never replace'):
        draft.freeze(output)
    assert frozen == ((output / 'manifest.json').read_bytes(), (output / 'frozen-code.zip').read_bytes())
    assert not (output / 'private').exists() and not (output / 'trials').exists()


@pytest.mark.parametrize('mutation', ['missing_report', 'invalid_report_digest', 'failing_report'])
def test_synthetic_freeze_rejects_incomplete_machine_report_before_freezing(synthetic_freeze, monkeypatch, mutation):
    output, _source = synthetic_freeze
    def bad_report(document, directory, **kwargs):
        report = _synthetic_report(document, directory, **kwargs)
        if mutation == 'missing_report':
            report['cases'][0].pop('pytestReport')
        elif mutation == 'invalid_report_digest':
            report['cases'][0]['pytestReport']['caseReport']['digest'] = 'sha256:' + '0' * 64
        else:
            report['cases'][0]['pytestReport']['failed'] = 1
        draft.shared.write_json(directory / 'receipt.json', report)
        return report
    monkeypatch.setattr(draft.progress, 'run_public_preflight', bad_report)
    with pytest.raises(Exception, match='preflight'):
        draft.freeze(output)
    assert not (output / 'manifest.json').exists() and not (output / 'frozen-code.zip').exists()


@pytest.mark.parametrize('target', ['provenance', 'manifest', 'prepared'])
def test_synthetic_frozen_baseline_cannot_drift(synthetic_freeze, target):
    output, _source = synthetic_freeze
    draft.freeze(output)
    name = {'provenance': 'source-provenance.json', 'manifest': 'manifest.json', 'prepared': 'tasks.json'}[target]
    value = draft.shared.read_json(output / name)
    if target == 'prepared':
        value['cases'][0]['files']['source.py'] = 'VALUE = 99\n'
    else:
        value['baselineFilesSha256'] = '0' * 64
    draft.shared.write_json(output / name, value)
    with pytest.raises(ValueError):
        draft.validate(output)
