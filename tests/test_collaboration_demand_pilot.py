"""Synthetic, offline contract checks; never read a real task/oracle/provider."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import random
import time
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('demand_pilot', ROOT / 'scripts/run_collaboration_demand_pilot.py')
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def source_fixture(tmp_path):
    source = tmp_path / 'source'
    document = {'schemaVersion': 1, 'shared_files': {}, 'cases': [
        {'id': name, 'category': 'synthetic', 'prompt': 'Add the requested public behavior.',
         'files': {'minicode/example.py': 'VALUE = 1\n', 'tests/test_public.py': 'def test_baseline(): assert True\n'},
         'authorized_paths': ['minicode/example.py', 'tests/test_eval_regression.py'],
         'verification_argv': ['python3', '-m', 'pytest', '-q', 'tests/test_public.py'],
         'oracle_source': 'OPAQUE SYNTHETIC PAYLOAD: DO NOT INTERPRET'} for name in pilot.CASES]}
    pilot.shared.write_json(source / 'original/private/tasks.json', document)
    for case in document['cases']:
        case['external_verification_argv']=list(case['verification_argv'])
        case['verification_excluded_node_ids']=['tests/test_public.py::test_environment_requires_fork']
        case['external_verification_node_ids']=['test_public.py::test_baseline',*case['verification_excluded_node_ids']]
        case['verification_argv']+=['-k','not test_environment_requires_fork']
    pilot.shared.write_json(source / 'private/tasks.json', document)
    pilot.shared.write_json(source / 'manifest.json', {'fixture': True})
    pilot.shared.write_json(source / 'public-cases.json', {'cases': [{'id': x} for x in pilot.CASES]})
    return source, document


def test_six_cells_are_preregistered_without_global_random_effect():
    state = random.getstate()
    rows = pilot.schedule()
    assert random.getstate() == state and rows == pilot.schedule()
    assert len(rows) == len({r['id'] for r in rows}) == 6
    assert {(r['case'], r['arm']) for r in rows} == {(c, a) for c in pilot.CASES for a in pilot.ARMS}


def test_every_parent_has_implementation_tools_and_only_single_lacks_task(tmp_path):
    from minicode.tooling import ToolDefinition, ToolRegistry
    module = pilot.runner(tmp_path)
    names = ['task', 'todo_write', 'read_file', 'write_file', 'edit_file', 'run_command']
    owner = ToolRegistry([ToolDefinition(n, '', {}, lambda x:x, lambda *_:None) for n in names])
    try:
        views = {a:module.coordinator_registry(module.independent_registry(owner,a),a) for a in pilot.ARMS}
        assert set(views['explicit'].list_all()) == set(views['auto'].list_all()) == set(names)
        assert set(views['single'].list_all()) == set(names)-{'task'}
        assert pilot.shared.coordinator_registry is not module.coordinator_registry
    finally:
        owner.dispose()


def test_explicit_recipe_uses_supported_async_read_work_without_forced_workflow():
    prompts = {a:pilot.prompt_for_arm('PUBLIC TASK', a) for a in pilot.ARMS}
    assert prompts['single'] == prompts['auto']
    assert prompts['explicit'] == prompts['single'] + pilot.EXPLICIT_RECIPE
    assert "action='spawn'" in pilot.EXPLICIT_RECIPE and "action='poll'" in pilot.EXPLICIT_RECIPE
    assert "agent_type='explore'" in pilot.EXPLICIT_RECIPE
    assert '只负责派发' not in pilot.EXPLICIT_RECIPE
    from minicode.tools.task import _validate
    assert _validate({'action':'spawn','agent_type':'explore','description':'Inspect one boundary','prompt':'Inspect this independent public module and return evidence.'})['action'] == 'spawn'
    assert _validate({'action':'poll','subagent_id':'sub_'+'a'*32,'wait_seconds':0})['wait_seconds'] == 0


def test_prepare_is_opaque_copy_and_binds_every_public_baseline(tmp_path):
    source, document = source_fixture(tmp_path)
    out = tmp_path/'out'
    pilot.prepare(out, source, source/'original')
    assert (out/'tasks.json').read_bytes() == (source/'private/tasks.json').read_bytes()
    metadata = pilot.shared.read_json(out/'source-provenance.json')
    assert set(metadata['baselineFilesSha256']) == set(pilot.CASES)
    assert 'OPAQUE SYNTHETIC' not in json.dumps(metadata)
    restored, _ = pilot.prepared_document(out)
    assert restored == document
    with pytest.raises(ValueError, match='empty'):
        pilot.prepare(out, source, source/'original')


@pytest.mark.parametrize('target', ['private/tasks.json','manifest.json','public-cases.json'])
def test_source_mutation_blocks_frozen_source_reuse(tmp_path, target):
    source,_ = source_fixture(tmp_path)
    out=tmp_path/'out';pilot.prepare(out,source,source/'original')
    (source/target).write_text('{}')
    with pytest.raises(ValueError,match='source'):
        pilot.prepared_document(out)


def test_escaping_baseline_rejected_before_prepare_writes(tmp_path):
    source,d = source_fixture(tmp_path)
    d['cases'][0]['files']['../outside.py']='bad'
    pilot.shared.write_json(source/'private/tasks.json',d)
    with pytest.raises(ValueError,match='baseline'):
        pilot.prepare(tmp_path/'out',source,source/'original')
    assert not (tmp_path/'out').exists()


def terminal_fixture(out, row, success=False):
    cleanup={'rootPid':12345,'cleanupComplete':True,'phase':'complete','freezeComplete':True,
             'killConfirmComplete':True, **{k:[] for k in ('survivorPids','evidenceErrors','processScanErrors',
             'lateDiscoveredPids','unconfirmedPids','identityMismatches')}}
    pilot.shared.write_json(out/'private'/row['id']/'execution-start.json',{'epoch':1000,'deadlineEpoch':None})
    pilot.shared.write_json(out/'private'/row['id']/'process-cleanup.json',cleanup)
    pilot.shared.write_json(out/'trials'/(row['id']+'.json'),{**row,'success':success,'errorClass':None,
        'durationSeconds':1,'modelCalls':1,'processCleanup':cleanup})


def test_no_skip_no_overwrite_and_failed_predecessor_is_retained(tmp_path):
    first,second,*_=pilot.schedule()
    with pytest.raises(ValueError,match='preordered predecessor'):
        pilot.assert_fresh_attempt(tmp_path,second['id'])
    terminal_fixture(tmp_path,first,success=False)
    pilot.assert_fresh_attempt(tmp_path,second['id'])
    with pytest.raises(ValueError,match='terminal trial'):
        pilot.assert_fresh_attempt(tmp_path,first['id'])
    assert pilot.shared.read_json(tmp_path/'trials'/(first['id']+'.json'))['success'] is False
    pilot.shared.write_json(tmp_path/'private'/second['id']/'execution-start.json',{'epoch':2000})
    with pytest.raises(ValueError,match='prior trial evidence'):
        pilot.assert_fresh_attempt(tmp_path,second['id'])


def test_holey_result_prefix_is_rejected(tmp_path):
    terminal_fixture(tmp_path,pilot.schedule()[1])
    with pytest.raises(ValueError,match='prefix'):
        pilot.validate_run_prefix(tmp_path)


def delivered(actor,start,end):
    return {'subagentId':actor,'agentType':'explore','role':'explore','isContainer':False,
            'parentSubagentId':None,'startedEpoch':start,'returnedEpoch':end,'returned':True,
            'deliveredEpoch':end+0.1,'deliveryEvidence':'task_async_owner_ack','resultSha256':'a'*64,
            'toolOk':True,'projected':{'outcome':'completed','partial':False}}


@pytest.mark.parametrize('overlap',[True,False])
def test_parallel_claim_requires_real_overlapping_provider_calls_and_ack(overlap):
    records=[delivered('a',1,5),delivered('b',2,6)]
    requests=[{'subagentId':'a','event':'completed','startedEpoch':1,'seconds':3},
              {'subagentId':'b','event':'completed','startedEpoch':2 if overlap else 4.5,'seconds':1}]
    for request in requests:
        request.update(providerStartedEpoch=request['startedEpoch'],
            providerTerminalEpoch=request['startedEpoch']+request['seconds'],
            providerIntervalBoundary='http-open-to-adapter-terminal-v1')
    result=pilot.collaboration_metrics(records,requests,[{'content':'I did collaborate'}],['a','b'])
    assert result['actualLeafRuns']==result['deliveredLeafResults']==2
    assert result['collaborationProtocolSatisfied'] is overlap
    records[1].pop('deliveryEvidence')
    assert not pilot.collaboration_metrics(records,requests,[],['a','b'])['collaborationProtocolSatisfied']


def test_long_adapter_prepare_cannot_turn_adjacent_sequential_http_into_parallel():
    records=[delivered('a',1,3),delivered('b',3.1,3.5)]
    requests=[{'subagentId':'a','event':'completed','startedEpoch':1,'seconds':3,
        'providerStartedEpoch':1,'providerTerminalEpoch':3,'providerIntervalBoundary':'http-open-to-adapter-terminal-v1'},
        {'subagentId':'b','event':'completed','startedEpoch':3.1,'seconds':0.4,
        'providerStartedEpoch':3.1,'providerTerminalEpoch':3.5,'providerIntervalBoundary':'http-open-to-adapter-terminal-v1'}]
    result=pilot.collaboration_metrics(records,requests,[],['a','b'])
    assert not result['collaborationProtocolSatisfied']
    assert result['parallelReadLeafPairs']==[]


@pytest.mark.parametrize('invalid',['missing_start','missing_end','missing_boundary','reversed','nan','infinite','bool','failed'])
def test_parallel_metric_never_reconstructs_missing_or_invalid_provider_interval(invalid):
    records=[delivered('a',1,5),delivered('b',2,6)]
    requests=[{'subagentId':actor,'event':'completed','startedEpoch':start,'seconds':3,
        'providerStartedEpoch':start,'providerTerminalEpoch':start+3,
        'providerIntervalBoundary':'http-open-to-adapter-terminal-v1'} for actor,start in [('a',1),('b',2)]]
    request=requests[0]
    if invalid=='missing_start':request.pop('providerStartedEpoch')
    elif invalid=='missing_end':request.pop('providerTerminalEpoch')
    elif invalid=='missing_boundary':request.pop('providerIntervalBoundary')
    elif invalid=='reversed':request['providerTerminalEpoch']=0.5
    elif invalid=='nan':request['providerTerminalEpoch']=float('nan')
    elif invalid=='infinite':request['providerTerminalEpoch']=float('inf')
    elif invalid=='bool':request['providerStartedEpoch']=True
    else:request['event']='failed'
    assert not pilot.collaboration_metrics(records,requests,[],['a','b'])['collaborationProtocolSatisfied']


def test_no_delegation_is_a_valid_observed_state_not_invented_protocol_success():
    r=pilot.collaboration_metrics([],[],[{'content':'delegated'}],[])
    assert r['actualLeafRuns']==0 and not r['collaborationProtocolSatisfied']


def test_unlimited_contract_and_distinct_actual_role_policy():
    c=pilot.static_contract()
    assert all(c['limits'][k] is None for k in ('maxTokens','maxModelCalls','maxCostUsd','wallSeconds','requestTimeoutSeconds'))
    assert c['limits']['maxOutputTokens']==131072 and c['roleTurnLimits']=={'explore':0,'plan':0,'general':0}
    assert c['model']=='glm-5.3-flash' and c['temperature']==0
    assert c['publicPytestReportRequired'] is True


def test_async_ack_is_persisted_only_at_real_lifecycle_delivery_seam(tmp_path, monkeypatch):
    from minicode.tools import task
    from minicode.tooling import ToolResult
    from minicode.subagent_lifecycle import AsyncSubagentLifecycle
    monkeypatch.setattr(task, '_run', lambda *_:ToolResult(ok=True,output='public completed investigation'))
    # Use the production lifecycle job admission and completion, then its actual ack.
    lifecycle=AsyncSubagentLifecycle()
    try:
        with pilot.DemandCollaborationEvidence(tmp_path/'collaboration.json') as evidence:
            snapshot=lifecycle.spawn(agent_type='explore',runner=lambda actor,_cancel:task._run(
                {'agent_type':'explore','_subagent_id':actor},SimpleNamespace()))
            actor=snapshot['subagentId']
            terminal=lifecycle.poll(actor,wait_seconds=5)
            assert terminal['terminal'] is True
            before=pilot.shared.read_json(tmp_path/'collaboration.json')
            assert actor not in before['deliveredIds']
            assert 'deliveredEpoch' not in before['runs'][0]
            lifecycle.acknowledge_delivery((actor,))
            after=pilot.shared.read_json(tmp_path/'collaboration.json')
            record=after['runs'][0]
            assert actor in after['deliveredIds']
            assert record['deliveryEvidence']=='task_async_owner_ack'
            assert record['returnedEpoch']<=record['deliveredEpoch']
    finally:
        lifecycle.shutdown()


def machine_report(document, output, **kwargs):
    assert kwargs['require_pytest_report'] is True
    cases=[{'nodeId':'test_public.py::test_baseline','outcome':'passed','failureKind':None}]
    digest='sha256:'+'1'*64
    report={'collected':1,'passed':1,'failed':0,'errors':0,'skipped':0,'reportDigest':digest,
        'caseReport':{'cases':cases,'digest':'sha256:'+pilot.shared.sha(pilot.previous._canonical({'junitDigest':digest,'cases':cases}))}}
    receipt={'preflightVersion':1,'passed':True,'pytestReportRequired':True,
        'policy':pilot.progress.PUBLIC_FIXTURE_POLICY,'fixtures':pilot.progress.public_fixture_manifest(document),
        'taskDocumentSha256':pilot.shared.sha(json.dumps(document,sort_keys=True,separators=(',',':')).encode()),
        'cases':[{'case':c['id'],'argv':c['verification_argv'],'status':'passed','verification':{'status':'passed'},
            'evidence':[{'status':'passed','exitCode':0,'argv':c['verification_argv']}],
            'pytestReport':deepcopy(report)} for c in document['cases']]}
    pilot.shared.write_json(output/'receipt.json',receipt)
    return receipt


@pytest.fixture
def frozen_fixture(tmp_path,monkeypatch):
    source,_=source_fixture(tmp_path);out=tmp_path/'out';pilot.prepare(out,source,source/'original')
    code_root=tmp_path/'code';(code_root/'minicode').mkdir(parents=True);(code_root/'scripts').mkdir()
    (code_root/'minicode/__init__.py').write_text('# archive fixture\n')
    for name in pilot.HARNESS_FILES:(code_root/'scripts'/name).write_text('# archive fixture\n')
    monkeypatch.setattr(pilot,'ROOT',code_root)
    runtime={'model':'glm-5.3-flash','maxOutputTokens':131072,'modelTimeoutSeconds':None}
    module=SimpleNamespace(PRICE=pilot.shared.PRICE,frozen_runtime=lambda:runtime,
        route_hash=lambda _: '2'*64,resolved_transport_route=lambda _: '3'*64,
        production_sha=lambda:'4'*64,harness_sha=lambda:'5'*64)
    monkeypatch.setattr(pilot,'runner',lambda _:module)
    monkeypatch.setattr(pilot.previous,'request_policy',lambda _:{'syntheticPolicy':True,'supportedReasoningEfforts':['high']})
    monkeypatch.setattr(pilot.progress,'run_public_preflight',machine_report)
    return out


def test_freeze_validate_preserve_bytes_and_refuse_overwrite(frozen_fixture):
    out=frozen_fixture;pilot.freeze(out);manifest=pilot.validate(out)
    assert manifest['schedule']==pilot.schedule()
    with zipfile.ZipFile(out/'frozen-code.zip') as archive:
        assert 'scripts/run_collaboration_demand_pilot.py' in archive.namelist()
    before=(out/'manifest.json').read_bytes()
    with pytest.raises(ValueError,match='never replace'):pilot.freeze(out)
    assert (out/'manifest.json').read_bytes()==before
    assert not (out/'private').exists()


def test_missing_machine_report_blocks_archive_and_model_work(frozen_fixture,monkeypatch):
    def missing(document,out,**kwargs):
        receipt=machine_report(document,out,**kwargs)
        receipt['cases'][0].pop('pytestReport')
        pilot.shared.write_json(out/'receipt.json',receipt)
        return receipt
    monkeypatch.setattr(pilot.progress,'run_public_preflight',missing)
    with pytest.raises(Exception,match='preflight'):pilot.freeze(frozen_fixture)
    assert not (frozen_fixture/'manifest.json').exists()
    assert not (frozen_fixture/'frozen-code.zip').exists()


def test_frozen_schedule_drift_blocks_validation(frozen_fixture):
    out=frozen_fixture;pilot.freeze(out)
    manifest=pilot.shared.read_json(out/'manifest.json');manifest['schedule'].reverse()
    pilot.shared.write_json(out/'manifest.json',manifest)
    with pytest.raises(ValueError,match='schedule'):pilot.validate(out)


@pytest.mark.parametrize('change',['oracle','external_argv','baseline','scope','undeclared_filter'])
def test_layered_validation_cannot_weaken_original_quality_gate(tmp_path,change):
    source,document=source_fixture(tmp_path)
    case=document['cases'][0]
    if change=='oracle':case['oracle_source']='CHANGED SYNTHETIC JUDGE'
    elif change=='external_argv':
        case['external_verification_argv']+=['-k','test_baseline']
        case['verification_argv']=case['external_verification_argv'][:-1]+['(test_baseline) and not test_environment_requires_fork']
    elif change=='baseline':case['files']['minicode/example.py']='VALUE = 2\n'
    elif change=='scope':case['authorized_paths']+=['outside.py']
    else:case['verification_argv'][-1]+=' and not test_baseline'
    pilot.shared.write_json(source/'private/tasks.json',document)
    with pytest.raises(ValueError):pilot.prepare(tmp_path/'out',source,source/'original')
    assert not (tmp_path/'out').exists()


def test_layered_validation_preserves_original_filter_and_declared_node_scope(tmp_path):
    source,document=source_fixture(tmp_path)
    original=pilot.shared.read_json(source/'original/private/tasks.json')
    for case,old in zip(document['cases'],original['cases']):
        old['verification_argv']+=['-k','not test_old_private_import']
        case['external_verification_argv']=list(old['verification_argv'])
        case['verification_argv']=old['verification_argv'][:-1]+['(not test_old_private_import) and not test_environment_requires_fork']
    layers=pilot.layered_validation(document,original)
    assert layers[pilot.CASES[0]]['externalArgv']==original['cases'][0]['verification_argv']
    assert layers[pilot.CASES[0]]['sharedJudgeSeconds']==120
    assert layers[pilot.CASES[0]]['excludedNodeIds']==['tests/test_public.py::test_environment_requires_fork']


def test_machine_collection_must_match_declared_environment_difference(tmp_path):
    _,document=source_fixture(tmp_path)
    receipt=machine_report(document,tmp_path/'receipt',require_pytest_report=True)
    pilot.validate_internal_scope(document,receipt)
    receipt['cases'][0]['pytestReport']['caseReport']['cases'][0]['nodeId']='tests/test_public.py::test_environment_requires_fork'
    with pytest.raises(ValueError,match='declared environment exclusions'):
        pilot.validate_internal_scope(document,receipt)


def test_delivered_failures_are_attempts_not_successful_collaboration():
    records=[delivered('a',1,5),delivered('b',2,6)]
    for record in records:
        record['toolOk']=False
        record['projected']['outcome']='failed'
    requests=[{'subagentId':a,'event':'completed','startedEpoch':1,'seconds':3} for a in ('a','b')]
    result=pilot.collaboration_metrics(records,requests,[],['a','b'])
    assert result['collaborationAttempted'] and result['deliveredLeafResults']==2
    assert not result['collaborationProtocolSatisfied']


@pytest.mark.parametrize('role,provider_result',[(role,'success') for role in ['main','explore','plan','general','reviewer']]+[('main','open_error'),('main','stream_error')])
def test_every_role_has_real_high_wire_metadata_despite_low_call_override(tmp_path,monkeypatch,role,provider_result):
    from minicode import openai_adapter as adapter
    from test_evaluation_transport import ScriptedResponse, event, TEXT, USAGE, DONE, MESSAGES
    from evaluation_transport import evaluation_context, route_fingerprint
    runtime={'model':'glm-5.3-flash','openaiBaseUrl':'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'openaiApiKey':'offline-fake-key','modelTimeoutSeconds':None,'maxOutputTokens':131072,
        '_resolvedOpenAIProvider':'openai','_supportedReasoningEfforts':('low','high','max')}
    cfg=pilot.shared.TransportConfig(ledger_path=str(tmp_path/'ledger.sqlite'),
        expected_model=runtime['model'],expected_route_sha256=route_fingerprint(runtime),
        deadline_epoch=None,max_model_calls=None,max_total_tokens=None,request_timeout_seconds=None,max_output_tokens=131072)
    bodies=[];timestamps={}
    class ObservedResponse(ScriptedResponse):
        def read1(self,amount):
            timestamps['bodyRead']=time.time()
            return super().read1(amount)
    def http(request,*,timeout):
        timestamps['http']=time.time()
        bodies.append(json.loads(request.data))
        if provider_result=='open_error':raise OSError('offline open error')
        return ObservedResponse([OSError('offline stream error')] if provider_result=='stream_error' else [event({**TEXT,'usage':USAGE}),DONE])
    monkeypatch.setattr(adapter,'open_verified_url',http)
    original=adapter.OpenAIModelAdapter.next
    with pilot.UniformReasoningTransport(cfg) as transport:
        with evaluation_context(role=role,subagent_id=None if role=='main' else 'sub_'+'1'*32):
            if provider_result=='success':
                adapter.OpenAIModelAdapter(runtime,None).next(MESSAGES,reasoning_effort_override='low',temperature_override=0.9)
            else:
                with pytest.raises(Exception):
                    adapter.OpenAIModelAdapter(runtime,None).next(MESSAGES,reasoning_effort_override='low',temperature_override=0.9)
        timestamps['adapterReturned']=time.time()
        snapshot=transport.ledger.snapshot()
    assert adapter.OpenAIModelAdapter.next is original
    assert bodies[0]['reasoning_effort']=='high'
    assert bodies[0]['temperature']==0
    assert snapshot['requests'][0]['reasoningEffort']=='high'
    assert snapshot['requests'][0]['temperature']==0
    row=snapshot['requests'][0]
    assert row['role']==role and snapshot['usageComplete'] is (provider_result=='success')
    assert row['event']==('completed' if provider_result=='success' else 'failed')
    assert row['providerIntervalBoundary']=='http-open-to-adapter-terminal-v1'
    assert row['startedEpoch']<=row['providerStartedEpoch']<=timestamps['http']<=row['providerTerminalEpoch']<=timestamps['adapterReturned']
    if provider_result!='open_error':
        assert timestamps['bodyRead']<=row['providerTerminalEpoch']
