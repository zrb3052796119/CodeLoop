"""Live production-runtime experiment; no simulated model or worker decisions."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import signal
import sys
import time

SOURCE = Path(__file__).resolve().parents[2]
ARTIFACT = Path(__file__).resolve().parent
WORKSPACE = Path('/private/tmp/codeloop-completion-recheck-20260918')
RUN = ARTIFACT / 'resume-2' / 'live-run'
HISTORY_PATH = ARTIFACT / 'continuation' / 'review-retry' / 'live-run' / 'messages.json'
RUN.mkdir(exist_ok=True)
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'scripts'))

# Read configured providers once, in memory. No credential file is copied.
import minicode.config as config

configured = config.load_runtime_config(SOURCE)
isolated_home = RUN / 'isolated-home'
isolated_home.mkdir(exist_ok=True)
real_home = Path.home()
Path.home = classmethod(lambda cls: isolated_home)
for key, value in list(vars(config).items()):
    if isinstance(value, Path) and value.is_relative_to(real_home):
        setattr(config, key, isolated_home / value.relative_to(real_home))

configured['agentTurnBudget'] = {
    'maxTokens': 1000000, 'maxModelCalls': 40, 'maxCostUsd': 5,
}
configured['memoryHybridEnabled'] = False
configured['modelTimeoutSeconds'] = 180
configured['maxOutputTokens'] = 8192
configured['modelMaxRetries'] = 1
assert config.safe_runtime_summary(configured)['effectiveTurnBudget'] == {'maxTokens': 1000000, 'maxModelCalls': 40, 'maxCostUsd': '5'}
config.load_runtime_config = lambda cwd=None: copy.deepcopy(configured)

from minicode.agent_runtime import create_agent_turn_runtime, prepare_conversation_messages, final_response_text
from minicode.memory import MemoryManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
import run_north_star_live as ns
import run_multi_agent_cost_live as cost

PATHS = ('.',)
PROMPT = '继续完成现有分支test_runner修复、回归测试、必要检查与Git/PR交付。前两次审查续办都在模型请求阶段超时，没有改动目标代码，本次运行框架已补有界超时恢复，工作区保留原修复。待修复的实测问题：1. 201文件时丢失verbose/coverage/pattern参数；2. 201个test_selected_*.py通过文件加一个test_other.py内含失败函数test_selected_outside_filename，pattern=selected时不应跑test_other.py，目录收集却扩大集合，即使补-k也错误；3. 一个通过测试警告含1 failed historical request; 2 errors already recovered，pytest退出0但工具误报失败。请自主修正并补针对性回归，保留第12文件不漏跑修复。完成相关pytest、CI式lint和diff自查后只提交相关源码与测试；再真实创建PR。如gh未登录，仍完成本地提交，提供准确PR标题、描述和阻塞，别把未执行说成已完成。只依据实际执行结果汇报。'
(RUN / 'prompt.txt').write_text(PROMPT, encoding='utf-8')
(RUN / 'request-controls.json').write_text(json.dumps({key: configured.get(key) for key in ('model', 'provider', 'maxOutputTokens', 'modelTimeoutSeconds', 'modelMaxRetries', 'enableThinking', 'temperature')}, indent=2), encoding='utf-8')
(RUN / 'configuration.json').write_text(json.dumps(config.safe_runtime_summary(configured), ensure_ascii=False, indent=2), encoding='utf-8')

approval = cost._BoundedWorkflowApprovalProvider(PATHS)
journal = RunJournal(WORKSPACE, data_dir=RUN / 'journal')
approval_log = []

def permissions(request):
    review = request.get('review', {})
    decision = ns._isolated_write_approval(WORKSPACE, request, tuple(map(Path, PATHS)))
    if request.get('kind') == 'edit' and isinstance(review, dict):
        target = review.get('targetPath')
        if isinstance(target, str):
            resolved = Path(target).resolve()
            if resolved.is_relative_to(WORKSPACE) and '.git' not in resolved.relative_to(WORKSPACE).parts:
                decision = {'decision': 'allow_once'}
    if request.get('kind') == 'command' and isinstance(review, dict):
        command, args, cwd = review.get('command'), review.get('args'), review.get('cwd')
        if isinstance(command, str) and isinstance(args, list) and cwd == str(WORKSPACE):
            if Path(command).name == 'git' and args:
                safe = args[0] in ('status', 'diff', 'log', 'show', 'rev-parse')
                if args[0] == 'add':
                    paths = [arg for arg in args[1:] if arg != '--']
                    safe = bool(paths) and all(not p.startswith('-') and p not in ('.', '*') and (WORKSPACE / p).resolve().is_relative_to(WORKSPACE) and '.git' not in Path(p).parts and not p.endswith('.lock') for p in paths)
                if args[0] == 'commit':
                    safe = not any(a in args for a in ('--amend', '--no-verify'))
                if args[0] in ('switch', 'checkout'):
                    safe = len(args) == 3 and args[1] in ('-c', '-b') and args[2].startswith('codex/')
                if args[0] == 'push':
                    import subprocess
                    branch = subprocess.run(['git', 'branch', '--show-current'], cwd=WORKSPACE, text=True, capture_output=True, check=True).stdout.strip()
                    safe = branch.startswith('codex/') and args[1:] in (['origin', branch], ['-u', 'origin', branch], ['--set-upstream', 'origin', branch], ['origin', 'HEAD'], ['-u', 'origin', 'HEAD'])
                if safe:
                    decision = {'decision': 'allow_once'}
            if Path(command).name == 'gh' and args:
                safe = args[:2] in (['auth', 'status'], ['repo', 'view'], ['pr', 'list'], ['pr', 'view'], ['pr', 'create'])
                for flag in ('--repo', '-R'):
                    if flag in args:
                        i = args.index(flag)
                        safe = safe and i + 1 < len(args) and args[i + 1] == 'zrb3052796119/CodeLoop'
                for arg in args:
                    if arg.startswith('--repo='):
                        safe = safe and arg.split('=', 1)[1] == 'zrb3052796119/CodeLoop'
                    if arg.startswith('-R') and arg != '-R':
                        safe = safe and arg[2:] == 'zrb3052796119/CodeLoop'
                if safe:
                    decision = {'decision': 'allow_once'}
    approval_log.append({'kind': request.get('kind'), 'decision': decision.get('decision')})
    return decision

def timeout(_signum, _frame):
    raise TimeoutError('live experiment exceeded wall time')

signal.signal(signal.SIGALRM, timeout)
signal.alarm(1260)
os.chdir(WORKSPACE)
started = time.monotonic()
runtime = None
run_id = None
failure = None
messages = []
try:
    with observe_run(workspace=WORKSPACE, source='headless', title='CodeLoop bounded timeout recovery and real review correction', journal_factory=lambda _: journal, enabled=True) as observation:
        run_id = observation.run_id
        print(json.dumps({'event': 'run_started', 'runId': run_id}), flush=True)
        runtime = create_agent_turn_runtime(workspace=WORKSPACE, prompt=PROMPT, include_mcp=False, allow_user_interaction=False, event_sink=observation, deadline_monotonic=time.monotonic() + 1200)
        initial_budget = runtime.agent_budget.snapshot().to_dict()
        assert (initial_budget['limitModelCalls'], initial_budget['limitTotalTokens'], initial_budget['limitCostUsd']) == (40, 1000000, '5')
        (RUN / 'initial-budget.json').write_text(json.dumps(initial_budget, indent=2), encoding='utf-8')
        runtime.permissions.prompt = permissions
        runtime.memory_manager = MemoryManager(project_root=WORKSPACE, data_root=RUN / 'state')
        runtime.runtime['_workflowHumanApprovalProvider'] = approval
        for definition in runtime.tools.list():
            original = definition.run
            def observed(inputs, context, _run=original, _name=definition.name):
                print(json.dumps({'event': 'tool_started', 'tool': _name}), flush=True)
                if _name == 'task':
                    approval.capture_task_call(inputs)
                result = _run(inputs, context)
                if _name == 'task':
                    approval.capture_task_result(result)
                print(json.dumps({'event': 'tool_finished', 'tool': _name, 'ok': result.ok}), flush=True)
                return result
            definition.run = observed
        history = json.loads(HISTORY_PATH.read_text(encoding='utf-8'))
        initial = prepare_conversation_messages(history, system_prompt=runtime.system_prompt, user_message=PROMPT)
        messages = runtime.execute(initial, observation)
        response = final_response_text(messages) or ''
        (RUN / 'response.md').write_text(response, encoding='utf-8')
        (RUN / 'messages.json').write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
        observation.assistant_completed(content_present=bool(response), content_length=len(response))
except BaseException as error:
    failure = type(error).__name__
    print(json.dumps({'event': 'failure', 'type': failure}), flush=True)
    import traceback
    # Stack locations only; provider exceptions may contain private payloads.
    (RUN / 'error-stack.json').write_text(json.dumps([{'file': f.filename, 'line': f.lineno, 'name': f.name} for f in traceback.extract_tb(error.__traceback__)], indent=2), encoding='utf-8')
finally:
    signal.alarm(0)
    final_budget = runtime.agent_budget.snapshot().to_dict() if runtime is not None else None
    if runtime is not None:
        runtime.dispose()
    summary = {'historySource': str(HISTORY_PATH), 'historyMessageCount': len(history) if 'history' in locals() else 0, 'runId': run_id, 'failure': failure, 'durationSeconds': round(time.monotonic()-started, 2), 'workflowApprovals': approval.records, 'permissions': approval_log}
    summary['budgetSnapshot'] = final_budget
    summary['budgetCaveat'] = 'Internal admission counters include charged estimates for timeout; unpriced default-cost estimate is not actual billing; provider usage excludes failed requests without receipts.'
    if run_id:
        events = ns._all_events(journal, run_id)
        calls, input_tokens, output_tokens = ns._usage_from_events(journal, run_id)
        summary['taskOutcome'] = next((e.payload for e in reversed(events) if e.type == 'task.outcome'), None)
        summary['modelFailures'] = [e.payload for e in events if e.type == 'model.failed']
        summary['failedModelCallsWithoutUsageReceipt'] = len(summary['modelFailures'])
        summary.update(modelCalls=calls, inputTokens=input_tokens, outputTokens=output_tokens, subagents=cost._subagent_rows(journal, [run_id]), eventTypes=[e.type for e in events])
    (RUN / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)

sys.exit(1 if failure else 0)
