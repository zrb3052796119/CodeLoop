#!/usr/bin/env python3
"""Two fresh public tasks, six serial same-model observations of demand-led work.

Prepare/freeze/validate never call a model. Private task/oracle bytes are passed
programmatically to the existing isolated runner and common judge, never printed.
Historical runners and result directories remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from functools import wraps
import importlib.util
import itertools
import json
import inspect
import math
from pathlib import Path
import random
import re
import sys
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from scripts import run_continuous_three_arm as previous  # noqa: E402

continuous, progress, shared = previous.continuous, previous.progress, previous.shared
ENTRY = ROOT/'scripts/run_collaboration_demand_pilot.py'
DEFAULT = ROOT/'artifacts/collaboration-overhead-2026-10-18/demand-pilot-v3'
SOURCE = ROOT/'artifacts/collaboration-overhead-2026-10-18/cases-v2'
ORIGINAL_SOURCE = ROOT/'artifacts/collaboration-overhead-2026-10-18/cases'
PROTOCOL = 'collaboration-demand-pilot-v3'
PROVIDER_INTERVAL_BOUNDARY = 'http-open-to-adapter-terminal-v1'
CASES = ('real-storage-portability', 'real-memory-corroboration')
ARMS = ('single', 'auto', 'explicit')
SEED = 20261018
LIMITS, ROLE_OVERRIDES = dict(continuous.LIMITS), dict(continuous.ROLE_OVERRIDES)
REASONING_EFFORT = 'high'
HARNESS_FILES = tuple(dict.fromkeys((*previous.HARNESS_FILES, ENTRY.name)))
COMMON_REQUIREMENT = previous.COMMON_REQUIREMENT + """
内部公开验证运行在既有生产沙盒：不允许监听本地 HTTP/网络端口、创建子进程或多进程信号量。
asyncio 事件循环和 socketpair 本身已实测可用，但不能据此使用被禁止的网络或子进程能力。
新增 tests/test_eval_regression.py 须可在该沙盒运行，可验证纯函数、协议和边界行为。
依赖真实多进程/HTTP 的完整既有集成测试仍由最终外部验收执行；内部命令中的预登记排除只表示环境不支持，不降低最终质量门。
"""
COMMON_EXECUTION = previous.COMMON_EXECUTION
EXPLICIT_RECIPE = """\n\n本组执行方式：按任务的独立调查边界进行并行协作，由主 Agent 负责整合、实现和验证。
先识别至少两个互不依赖的具体调查问题，以 task action='spawn'、agent_type='explore' 启动只读子任务。
每个委派只给相关目标、公开接口、允许读取范围和所需证据，避免重复全仓扫描。连续启动后主 Agent 可同时处理自己的独立工作。
用 task action='poll'、对应 subagent_id、wait_seconds（0到30秒）取得真实结果；完成交付前确认实际结果已送达。
子 Agent 只调查，主 Agent 拥有全部共同读写/验证工具，整合证据后直接实施并运行共同检查。
不要求创建固定 workflow、额外 Analyst 或固定 Reviewer；只有实际风险、验证缺口或失败需要时再增加协作。
依据真实工具回执交付，记录协作是否发生、是否并行；协作失败时仍尽力完成任务，并如实报告。
"""
ANALYSIS_PLAN = """# Demand-led collaboration pilot v3

Two newly authored tasks, one single/auto/explicit execution each. Six serial
trials are globally shuffled once by seed 20261018 (not separately per case).
No reruns, replacement tasks, best-of
selection, or failure omission. Each starts in a fresh copy of its frozen public
baseline. Historical calibration results are not mixed into this pilot.

All parents retain the same implementation/read/verification capabilities;
single alone lacks task. Auto may delegate. Explicit asks for at least two
independent asynchronous read-only explore tasks, followed by primary-owned
implementation. No mandatory workflow/Analyst/Reviewer chain. Real provider
overlap and durable host delivery are measured separately from final quality.
Overlap uses direct epoch timestamps at the real HTTP open and adapter terminal
boundaries. Admission timestamps and adapter-wide duration have different starts
and must never be added to synthesize an end. Missing, invalid or failed provider
intervals cannot establish successful parallel collaboration. v2 was frozen with
no model calls, superseded before launch, and is retained separately.

Same GLM-5.3-Flash route, temperature 0, explicitly high reasoning on every
provider call (including primary, read leaves and optional workflow roles), no cumulative
token/call/cost/execution-time cap. Role overrides 0, max output 131072, no total
request timeout, idle120/connect30. Same model does not mean identical role mix
or inference budget. All physical calls, failures, unknown usage, cache and
API-equivalent cost remain visible. Unknown usage is not filled with zero.

The internal public argv retains all original file selections and adds only
preregistered environment exclusions for checks requiring localhost listeners,
subprocesses or multiprocessing semaphores. The default production sandbox is
unchanged; asyncio event loops and socketpair themselves are supported. The
internal subset must pass the real sandbox/machine-report preflight. Every arm
adds a sandbox-compatible tests/test_eval_regression.py. The unchanged private
oracle still executes the complete original public suite (175/173 tests) and
independent feature checks; its bytes and full public argv are bound to v1.
That oracle and the separate generated-regression check share the same 120-second
post-execution deadline, including staging, receipts and process cleanup. Existing
reference acceptance took about 35 seconds, leaving room within that fixed gate.
Scope, output reader, cleanup and report requirements remain unchanged. The
private oracle is never exposed to an agent. v1's failed no-model preflight is
retained separately and never pooled with v2 observations. This protocol is not
a complete unrestricted development environment.

All six outcomes remain in the denominator. This is descriptive evidence on two
tasks and one trial per cell, not a causal or reliability estimate.
"""


def schedule() -> list[dict]:
    rows = [{'case':c,'arm':a,'repeat':1,'id':f'{c}-r1-{a}'} for c,a in itertools.product(CASES,ARMS)]
    random.Random(SEED).shuffle(rows)
    return rows


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm not in ARMS:
        raise ValueError('unknown comparison arm')
    return prompt+COMMON_REQUIREMENT+COMMON_EXECUTION+(EXPLICIT_RECIPE if arm=='explicit' else '')


def static_contract() -> dict:
    return {'schemaVersion':2,'protocolVersion':PROTOCOL,'phase':'pilot','seed':SEED,
        'cases':list(CASES),'schedule':schedule(),'arms':list(ARMS),'repeats':1,'concurrency':1,
        'sameBudgetComparison':False,'model':'glm-5.3-flash','temperature':0,
        'reasoningEffortPolicy':{'everyPhysicalRequest':REASONING_EFFORT,
            'enforcedAt':'harness adapter-call boundary in parent and subprocess bootstrap',
            'audit':'SQLite provider-visible reasoningEffort for every call; no raw prose'},
        'limits':LIMITS,'roleTurnLimits':ROLE_OVERRIDES,'parentMaxSteps':None,
        'boundedImplementationPhases':False,'runner':'scripts/'+ENTRY.name,'harnessFiles':list(HARNESS_FILES),
        'publicPytestReportRequired':True,'judgementPolicy':progress.JUDGE_POLICY,
        'validationPolicy':{'internal':'default production sandbox; declared environment exclusions only',
            'external':'unchanged v1 private oracle includes complete original public integration suite',
            'generatedRegression':'separate unfiltered run; must also be sandbox-compatible',
            'sharedPostExecutionDeadlineSeconds':120,'modelCallsBeforePreregistration':0,
            'unsupportedInternalChecksAreModelFailures':False},
        'implementationCapabilities':progress.IMPLEMENTATION_CAPABILITIES,
        'publicVerificationFixturePolicy':progress.PUBLIC_FIXTURE_POLICY,
        'commonRequirementSha256':shared.sha((COMMON_REQUIREMENT+COMMON_EXECUTION).encode()),
        'explicitRecipeSha256':shared.sha(EXPLICIT_RECIPE.encode()),
        'successPolicy':{'taskQualityPassed':'oracle + scope + common regression + judge/process cleanup',
            'success':'taskQualityPassed for every arm; protocol satisfaction is separate',
            'measurementComplete':'all physical token/cache usage known and no inflight calls'},
        'armIntervention':{'single':'full primary implementation tools minus task',
            'auto':'same full implementation tools plus optional task; router disabled',
            'explicit':'same full tools; async independent read-only exploration; primary implementation'},
        'collaborationEvidencePolicy':'at least two host-acknowledged read leaves with overlapping completed provider intervals',
        'providerIntervalPolicy':{'boundary':PROVIDER_INTERVAL_BOUNDARY,'startField':'providerStartedEpoch',
            'terminalField':'providerTerminalEpoch','clock':'direct time.time() at both host boundaries',
            'legacyDurationFallback':False,'failedOrIncompleteIntervalsCountAsParallel':False}}


def require_integrated() -> None:
    if Path(__file__).resolve()!=ENTRY.resolve():
        raise RuntimeError('draft_only: run from the integrated scripts entrypoint')


def baseline_hashes(document: dict) -> dict[str,str]:
    if [c['id'] for c in document['cases']]!=list(CASES):
        raise ValueError('case identities/order differ from preregistration')
    hashes={}
    for case in document['cases']:
        files={**document.get('shared_files',{}),**case['files']};projection={}
        for name,contents in files.items():
            p=Path(name)
            if not isinstance(contents,str) or not name or '\0' in name or p.is_absolute() or '..' in p.parts or p.as_posix()=='.' or p.as_posix() in projection:
                raise ValueError('invalid or ambiguous public baseline path')
            projection[p.as_posix()]=['file',shared.sha(contents.encode())]
        if not projection:
            raise ValueError('empty public baseline')
        if 'tests/test_eval_regression.py' not in case['authorized_paths']:
            raise ValueError('common regression path not authorized')
        if not isinstance(case['category'],str) or not case['verification_argv']:
            raise ValueError('missing public case metadata')
        hashes[case['id']]=shared.sha(previous._canonical(projection))
    return hashes


def layered_validation(document: dict, original: dict) -> dict:
    """Bind only public projections and opaque judge hashes across protocol versions."""
    if [c['id'] for c in original['cases']] != list(CASES):
        raise ValueError('original case identities differ')
    result={}
    for case,old in zip(document['cases'],original['cases']):
        external=case.get('external_verification_argv')
        excluded=case.get('verification_excluded_node_ids')
        nodes=case.get('external_verification_node_ids')
        if external!=old['verification_argv'] or not isinstance(excluded,list) or not excluded or len(excluded)!=len(set(excluded)):
            raise ValueError('external validation or declared environment exclusions changed')
        if (not isinstance(nodes,list) or any(not isinstance(node,str) for node in nodes)
                or len(nodes)!=len(set(nodes)) or not set(excluded)<set(nodes)):
            raise ValueError('excluded nodes must be a strict subset of the complete public collection')
        names=sorted({node.split('::')[-1].split('[')[0] for node in excluded if isinstance(node,str) and '::' in node})
        if not names or any(not re.fullmatch(r'test_[A-Za-z0-9_]+',name) for name in names) or any(not isinstance(node,str) or '::' not in node for node in excluded):
            raise ValueError('invalid excluded public pytest node')
        expected=list(external);expression=' and '.join('not '+name for name in names)
        if '-k' in expected:
            index=expected.index('-k')+1
            expected[index]='('+expected[index]+') and '+expression
        else:expected+=['-k',expression]
        if case['verification_argv']!=expected:
            raise ValueError('internal validation must only add declared environment exclusions')
        judge=shared.sha(case['oracle_source'].encode())
        if judge!=shared.sha(old['oracle_source'].encode()):
            raise ValueError('independent oracle changed across protocols')
        files={**document.get('shared_files',{}),**case['files']}
        old_files={**original.get('shared_files',{}),**old['files']}
        if ({k:v for k,v in files.items() if k!='PUBLIC_TASK.md'}!={k:v for k,v in old_files.items() if k!='PUBLIC_TASK.md'}
                or case['authorized_paths']!=old['authorized_paths']):
            raise ValueError('implementation baseline or scope changed across protocols')
        result[case['id']]={'internalArgv':expected,'internalArgvSha256':shared.sha(previous._canonical(expected)),
            'externalArgv':external,'externalArgvSha256':shared.sha(previous._canonical(external)),
            'excludedNodeIds':excluded,'excludedTestNames':names,'unchangedOracleSha256':judge,
            'externalNodeIdsSha256':shared.sha(previous._canonical(sorted(nodes))),
            'internalNodeIdsSha256':shared.sha(previous._canonical(sorted(set(nodes)-set(excluded)))),
            'externalCollected':len(nodes),'internalCollected':len(nodes)-len(excluded),
            'externalGate':'complete original public suite included in unchanged independent oracle',
            'generatedRegressionPath':'tests/test_eval_regression.py','sharedJudgeSeconds':120}
    return result


def validate_internal_scope(document: dict, receipt: dict) -> None:
    """Actual machine test identities must equal the preregistered set difference."""
    if len(receipt.get('cases',[]))!=len(document['cases']):
        raise ValueError('internal public collection is missing')
    for case,item in zip(document['cases'],receipt['cases']):
        expected=set(case['external_verification_node_ids'])-set(case['verification_excluded_node_ids'])
        actual=[row['nodeId'] for row in item.get('pytestReport',{}).get('caseReport',{}).get('cases',[])]
        if item.get('case')!=case['id'] or len(actual)!=len(set(actual)) or set(actual)!=expected:
            raise ValueError('internal public collection differs from declared environment exclusions')


def prepare(output: Path, source: Path=SOURCE, original_source: Path=ORIGINAL_SOURCE) -> None:
    require_integrated()
    if output.exists() and any(output.iterdir()):
        raise ValueError('prepare requires an empty new output directory')
    source=source.resolve()
    raw=(source/'private/tasks.json').read_bytes()
    document=json.loads(raw)  # Opaque oracle field is only carried through, never inspected.
    baselines=baseline_hashes(document)
    original_source=original_source.resolve()
    original_raw=(original_source/'private/tasks.json').read_bytes()
    layers=layered_validation(document,json.loads(original_raw))
    hashes={name:shared.sha((source/name).read_bytes()) for name in ('private/tasks.json','manifest.json','public-cases.json')}
    output.mkdir(parents=True,exist_ok=True)
    (output/'tasks.json').write_bytes(raw)
    (output/'tasks.json').chmod(0o600)
    (output/'public-cases.json').write_bytes((source/'public-cases.json').read_bytes())
    (output/'analysis-plan.md').write_text(ANALYSIS_PLAN)
    shared.write_json(output/'source-provenance.json',{'sourceDirectory':str(source),
        'sourceHashes':hashes,'preparedTaskSha256':shared.sha(raw),'baselineFilesSha256':baselines,
        'originalSourceDirectory':str(original_source),'originalTaskSha256':shared.sha(original_raw),
        'validationLayers':layers})


def prepared_document(output: Path) -> tuple[dict,dict]:
    provenance=shared.read_json(output/'source-provenance.json')
    source=Path(provenance['sourceDirectory'])
    for name,expected in provenance['sourceHashes'].items():
        if name not in ('private/tasks.json','manifest.json','public-cases.json') or shared.sha((source/name).read_bytes())!=expected:
            raise ValueError('case source changed')
    raw=(output/'tasks.json').read_bytes()
    if shared.sha(raw)!=provenance['preparedTaskSha256'] or shared.sha(raw)!=provenance['sourceHashes']['private/tasks.json']:
        raise ValueError('prepared source task bytes changed')
    if shared.sha((output/'public-cases.json').read_bytes())!=provenance['sourceHashes']['public-cases.json']:
        raise ValueError('public source specification changed')
    document=json.loads(raw)
    original_raw=(Path(provenance['originalSourceDirectory'])/'private/tasks.json').read_bytes()
    if (shared.sha(original_raw)!=provenance['originalTaskSha256']
            or layered_validation(document,json.loads(original_raw))!=provenance['validationLayers']):
        raise ValueError('original quality gate or validation layers changed')
    if baseline_hashes(document)!=provenance['baselineFilesSha256'] or (output/'analysis-plan.md').read_text()!=ANALYSIS_PLAN:
        raise ValueError('public baseline or analysis plan changed')
    return document,provenance


class DemandCollaborationEvidence(shared.CollaborationEvidence):
    """Add timing to the existing real async transcript-insertion ack seam."""
    def __enter__(self):
        super().__enter__()
        from minicode.subagent_lifecycle import AsyncSubagentLifecycle
        original=AsyncSubagentLifecycle.acknowledge_delivery
        def acknowledged(lifecycle,ids):
            original(lifecycle,ids)
            with self.lock:
                for record in self.records:
                    if record['subagentId'] in ids and record.get('returned') is True:
                        record.setdefault('deliveredEpoch',time.time())
                        record['deliveryEvidence']='task_async_owner_ack'
                self._save()
        self.stack.enter_context(patch.object(AsyncSubagentLifecycle,'acknowledge_delivery',acknowledged))
        return self


class UniformReasoningTransport(shared.EvaluationTransport):
    """Freeze a comparison control without changing production model defaults."""
    def __enter__(self):
        from minicode import openai_adapter as adapter, subagent_supervisor
        from minicode.tools import task
        transport_module=sys.modules[shared.EvaluationTransport.__module__]
        if transport_module._ACTIVE_TRANSPORT is not None:
            raise RuntimeError('evaluation transport already installed')
        original_next=adapter.OpenAIModelAdapter.next
        original_spawn=subagent_supervisor.run_subagent_process
        signature=inspect.signature(original_next)
        @wraps(original_next)
        def high(model,*args,**kwargs):
            bound=signature.bind(model,*args,**kwargs)
            bound.arguments['reasoning_effort_override']=REASONING_EFFORT
            bound.arguments['temperature_override']=0
            return original_next(*bound.args,**bound.kwargs)
        self._patch(adapter.OpenAIModelAdapter,'next',high)
        try:
            super().__enter__()
            actual_open=self._original_open
            original_update=self.ledger.update
            @wraps(actual_open)
            def provider_open(*args,**kwargs):
                state=transport_module._CURRENT_CALL.get()
                if state is None or state.get('call') is None:
                    raise RuntimeError('provider interval lacks admitted call')
                state['providerStartedEpoch']=time.time()
                return actual_open(*args,**kwargs)
            self._original_open=provider_open
            def terminal_update(call,fields):
                if fields.get('event') in ('completed','failed'):
                    fields={**fields,'providerTerminalEpoch':time.time(),
                        'providerIntervalBoundary':PROVIDER_INTERVAL_BOUNDARY}
                return original_update(call,fields)
            self._patch(self.ledger,'update',terminal_update)
            config_path=Path(self.config.ledger_path).with_suffix('.transport.json')
            @wraps(original_spawn)
            def spawn(request,**kwargs):
                if kwargs.get('command') is not None:
                    raise RuntimeError('custom worker command bypasses uniform reasoning bootstrap')
                kwargs['command']=[sys.executable,str(ENTRY),'--worker-bootstrap',str(config_path)]
                return original_spawn(request,**kwargs)
            self._patch(subagent_supervisor,'run_subagent_process',spawn)
            self._patch(task,'run_subagent_process',spawn)
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def _open(self,request,**kwargs):
        controls=json.loads(request.data)
        if controls.get('reasoning_effort')!=REASONING_EFFORT or controls.get('temperature')!=0:
            raise RuntimeError('frozen provider controls drifted')
        try:
            return super()._open(request,**kwargs)
        finally:
            state=sys.modules[shared.EvaluationTransport.__module__]._CURRENT_CALL.get()
            if state is not None and state.get('call') is not None:
                fields={'temperature':0,'uniformReasoningPolicy':REASONING_EFFORT}
                if 'providerStartedEpoch' in state:
                    fields.update(providerStartedEpoch=state['providerStartedEpoch'],
                        providerIntervalBoundary=PROVIDER_INTERVAL_BOUNDARY)
                self.ledger.update(state['call'],fields)


def worker_bootstrap(argv: list[str]) -> int:
    config_path,*worker_args=argv
    raw=shared.read_json(Path(config_path))
    request_path=Path(worker_args[worker_args.index('--request')+1])
    # Opaque production worker request is passed only to its existing entry;
    # only its actor identifier is selected for metering.
    request=shared.read_json(request_path)
    raw['worker_id']=request.get('subagent_id')
    from minicode.subagent_worker import main as worker_main
    with UniformReasoningTransport(shared.TransportConfig(**raw)):
        return worker_main(worker_args)


def collaboration_metrics(records: list[dict], requests: list[dict], messages: list[dict], delivered_ids=()) -> dict:
    del messages  # Only host acknowledgements and physical usage, never model claims.
    acknowledged=set(delivered_ids);leaves=[];calls_by={}
    for r in records:
        actor=r['subagentId'];calls=[c for c in requests if c.get('subagentId')==actor]
        calls_by[actor]=calls
        if r.get('isContainer') or not any(c.get('event')=='completed' for c in calls):
            continue
        dates=[r.get(k) for k in ('startedEpoch','returnedEpoch','deliveredEpoch')]
        delivered=bool(r.get('returned') is True and actor in acknowledged
            and r.get('deliveryEvidence') in ('task_result_owner_ack','task_async_owner_ack')
            and isinstance(r.get('resultSha256'),str) and re.fullmatch('[0-9a-f]{64}',r['resultSha256'])
            and all(type(t) in (int,float) and math.isfinite(t) for t in dates) and dates[0]<=dates[1]<=dates[2])
        successful=bool(r.get('toolOk') is True and isinstance(r.get('projected'),dict)
            and r['projected'].get('outcome')=='completed' and not r['projected'].get('partial',False))
        leaves.append({**r,'delivered':delivered,'successful':successful})
    read=[r for r in leaves if r['delivered'] and r['successful'] and r.get('agentType') in ('explore','plan')]
    def completed_interval(call):
        start,end=call.get('providerStartedEpoch'),call.get('providerTerminalEpoch')
        if (call.get('event')=='completed' and call.get('providerIntervalBoundary')==PROVIDER_INTERVAL_BOUNDARY
                and all(type(v) in (int,float) and math.isfinite(v) for v in (start,end))
                and 0<start<=end):
            return start,end
        return None
    pairs=[]
    for left,right in itertools.combinations(read,2):
        overlap=any((a:=completed_interval(c)) is not None and (b:=completed_interval(d)) is not None
            and max(a[0],b[0])<min(a[1],b[1])
            for c in calls_by[left['subagentId']] for d in calls_by[right['subagentId']])
        if overlap:pairs.append([left['subagentId'],right['subagentId']])
    ids={r['subagentId'] for r in leaves}
    return {'actualLeafRuns':len(leaves),'leafModelCalls':sum(c.get('subagentId') in ids for c in requests),
        'deliveredLeafResults':sum(r['delivered'] for r in leaves),'readAnalysisLeafRuns':len(read),
        'successfulLeafRuns':sum(r['successful'] for r in leaves),
        'implementationLeafRuns':sum(r.get('role')=='executor' for r in leaves),
        'collaborationExecuted':any(r['delivered'] for r in leaves),
        'collaborationAttempted':bool(records),
        'collaborationProtocolSatisfied':bool(pairs),'parallelReadLeafPairs':pairs,
        'deliveredWorkflowResults':sum(r.get('isContainer') and r['subagentId'] in acknowledged for r in records)}


def assert_fresh_attempt(output: Path, trial_id: str) -> None:
    rows=schedule();ids=[r['id'] for r in rows]
    if trial_id not in ids:raise ValueError('unknown trial')
    for prior in rows[:ids.index(trial_id)]:previous._terminal_predecessor(output,prior)
    if (output/'trials'/(trial_id+'.json')).exists():raise ValueError('terminal trial cannot run again')
    root=output/'private'/trial_id
    if any((root/name).exists() for name in ('transport.sqlite','transport-summary.json','execution-start.json','execution.json','transcript.json','process-cleanup.json')):
        raise ValueError('prior trial evidence exists; recover explicitly without a new model call')


def validate_run_prefix(output: Path) -> None:
    missing=False
    for row in schedule():
        if (output/'trials'/(row['id']+'.json')).exists():
            if missing:raise ValueError('terminal results must form a scheduled prefix')
            previous._terminal_predecessor(output,row)
        else:
            missing=True


def runner(output: Path):
    require_integrated()
    spec=importlib.util.spec_from_file_location('_demand_continuous',ROOT/'scripts/run_continuous_agent_diagnostic.py')
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    base.__file__=str(ENTRY);base.validate=validate;base.prompt_for_arm=prompt_for_arm;base.HARNESS_FILES=HARNESS_FILES
    module=base.runner(output)
    module.coordinator_registry=lambda registry,arm:registry
    module.collaboration_metrics=collaboration_metrics
    module.CollaborationEvidence=DemandCollaborationEvidence
    module.EvaluationTransport=UniformReasoningTransport
    module.transport_metrics=previous.transport_metrics
    original_runtime=module.frozen_runtime
    def frozen_runtime():
        runtime=original_runtime()
        runtime['_openrouter_params']={**runtime.get('_openrouter_params',{}),'reasoning_effort':REASONING_EFFORT}
        return runtime
    module.frozen_runtime=frozen_runtime
    original_execute,original_suite,original_finalize=module.execute_trial,module.run_suite,module.finalize_trial
    def execute(trial_output,trial_id):
        assert_fresh_attempt(Path(trial_output),trial_id)
        return original_execute(trial_output,trial_id)
    def suite(trial_output,selected=None):
        if selected is not None:raise ValueError('partial selection would skip the preregistered schedule')
        validate_run_prefix(Path(trial_output))
        return original_suite(trial_output)
    def finalize(*args,**kwargs):
        row=original_finalize(*args,**kwargs)
        row['taskQualityPassed']=bool(row.get('oraclePassed') and row.get('scopePassed')
            and row.get('regressionTestPassed') and row.get('processCleanup',{}).get('cleanupComplete')
            and isinstance(row.get('judgement'),dict) and row['judgement'].get('cleanupComplete'))
        row['measurementComplete']=bool(row.get('usageComplete') and row.get('tokenUsageComplete')
            and row.get('cacheUsageComplete') and row.get('inFlightCalls')==0 and not row.get('ledgerDamaged'))
        row['success']=row['taskQualityPassed']
        provenance=shared.read_json(Path(args[0])/'source-provenance.json')
        row['validationProtocol']=PROTOCOL
        row['validationLayersSha256']=shared.sha(previous._canonical(provenance['validationLayers']))
        row['externalQualityGate']='unchanged original oracle includes full public integration suite; separate generated regression'
        row['taskToolCalls']=row['delegationAttempts']
        row['delegationAttemptSemantics']='legacy task-tool calls include spawn/poll; actualLeafRuns is executed child count'
        shared.write_json(Path(args[0])/'trials'/(row['id']+'.json'),row)
        return row
    module.execute_trial,module.run_suite,module.finalize_trial=execute,suite,finalize
    return module


def current_contract(output: Path) -> tuple[dict,dict,dict]:
    document,provenance=prepared_document(output)
    module=runner(output)
    with continuous.role_overrides({'roleTurnLimits':ROLE_OVERRIDES}):
        runtime=continuous._execution_contract(module)
    policy=previous.request_policy(runtime)
    if REASONING_EFFORT not in policy.get('supportedReasoningEfforts',()):
        raise ValueError('model route does not support uniform high reasoning')
    contract={**static_contract(),'price':module.PRICE,'priceMeaning':'API equivalent, not subscription incremental billing',
        'routeSha256':module.route_hash(runtime),'resolvedTransportRouteSha256':module.resolved_transport_route(runtime),
        'requestPolicy':policy,'requestPolicySha256':shared.sha(previous._canonical(policy)),
        'taskSha256':provenance['preparedTaskSha256'],'sourceHashes':provenance['sourceHashes'],
        'originalTaskSha256':provenance['originalTaskSha256'],'validationLayers':provenance['validationLayers'],
        'baselineFilesSha256':provenance['baselineFilesSha256'],'productionSha256':module.production_sha(),
        'harnessSha256':module.harness_sha(),'analysisPlanSha256':shared.sha((output/'analysis-plan.md').read_bytes()),
        'publicVerificationFixtures':progress.public_fixture_manifest(document)}
    # Host-native Windows execution is explicitly distinct from portable
    # off-platform import/locking checks; this host has not run Windows.
    contract['nativeWindowsValidation']={'status':'not_run','reason':'pilot host is not native Windows'}
    return contract,document,runtime


def freeze(output: Path) -> None:
    require_integrated()
    if (output/'manifest.json').exists() or (output/'frozen-code.zip').exists():
        raise ValueError('never replace frozen evidence')
    contract,document,runtime=current_contract(output)
    receipt=progress.run_public_preflight(document,output/'public-preflight',runtime=runtime,require_pytest_report=True)
    if not receipt['passed']:raise ValueError('public baseline/machine-report preflight failed')
    digest=shared.sha((output/'public-preflight/receipt.json').read_bytes())
    progress.validate_public_preflight(document,output/'public-preflight/receipt.json',digest,require_pytest_report=True)
    validate_internal_scope(document,receipt)
    archive=output/'frozen-code.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted((ROOT/'minicode').rglob('*.py')):bundle.write(p,p.relative_to(ROOT).as_posix())
        for name in HARNESS_FILES:bundle.write(ROOT/'scripts'/name,'scripts/'+name)
        bundle.write(output/'analysis-plan.md','analysis-plan.md')
    shared.write_json(output/'manifest.json',{**contract,'createdAt':datetime.now(UTC).isoformat(),
        'publicPreflightSha256':digest,'frozenCodeArchiveSha256':shared.sha(archive.read_bytes())})


def validate(output: Path) -> dict:
    require_integrated()
    manifest=shared.read_json(output/'manifest.json')
    expected,document,_=current_contract(output)
    expected['frozenCodeArchiveSha256']=shared.sha((output/'frozen-code.zip').read_bytes())
    for key,value in expected.items():
        if previous._canonical(manifest.get(key))!=previous._canonical(value):
            raise ValueError('frozen demand comparison changed: '+key)
    progress.validate_public_preflight(document,output/'public-preflight/receipt.json',manifest.get('publicPreflightSha256'),require_pytest_report=True)
    validate_internal_scope(document,shared.read_json(output/'public-preflight/receipt.json'))
    return manifest


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','prepare','freeze','validate','run','child'))
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--original-source',type=Path,default=ORIGINAL_SOURCE)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--trial')
    args=parser.parse_args();output=args.output.resolve()
    if args.mode=='plan':print(json.dumps(static_contract(),ensure_ascii=False,indent=2))
    elif args.mode=='prepare':prepare(output,args.source.resolve(),args.original_source.resolve())
    elif args.mode=='freeze':freeze(output)
    elif args.mode=='validate':
        validate(output);print(json.dumps({'valid':True,'protocol':PROTOCOL,'trials':6}))
    else:
        validate(output);module=runner(output)
        if args.mode=='child':module.execute_trial(output,args.trial)
        else:module.run_suite(output)


if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1]=='--worker-bootstrap':
        raise SystemExit(worker_bootstrap(sys.argv[2:]))
    main()
