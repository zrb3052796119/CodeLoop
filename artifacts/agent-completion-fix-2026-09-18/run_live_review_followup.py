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
RUN = ARTIFACT / 'continuation' / 'review-followup' / 'live-run'
HISTORY_PATH = ARTIFACT / 'continuation' / 'review-followup' / 'handoff.json'
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
    'maxTokens': 420000, 'maxModelCalls': 20, 'maxCostUsd': 2,
}
configured['memoryHybridEnabled'] = False
config.load_runtime_config = lambda cwd=None: copy.deepcopy(configured)

from minicode.agent_runtime import create_agent_turn_runtime, prepare_conversation_messages, final_response_text
from minicode.memory import MemoryManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
import run_north_star_live as ns
import run_multi_agent_cost_live as cost

PATHS = ('.',)
PROMPT = '''继续完成现有分支的 test_runner 修复与交付。当前已有未提交的源码和3个新增回归，独立验收确认12文件末位失败场景已正确执行全部文件，相关测试36项通过，但审查用真实pytest发现三个新增回归，需要修正：
1. _build_pytest_command 的201文件目录分支丢掉了 verbose/coverage/pattern；200文件时这些参数正常。
2. 构造201个test_selected_*.py通过文件，以及test_other.py中一个失败函数test_selected_outside_filename；pattern=selected时原先文件名筛选不应选中test_other.py。目录分支却执行了它，即使补回-k也仍然扩大测试集合。
3. 一个通过测试里warnings.warn('1 failed historical request; 2 errors already recovered')，真实pytest退出0、1 passed 1 warning；新逻辑却把这个警告当成测试失败并返回ok=False。
请处理这些实际回归并补有针对性的测试，保留原始漏跑修复，完成必要验证和lint，只提交相关改动。随后创建GitHub PR；若未登录则保留本地提交并写好PR标题/描述，准确报告阻塞和未完成项目。只依据实际执行汇报。'''
(RUN / 'prompt.txt').write_text(PROMPT, encoding='utf-8')
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
signal.alarm(660)
os.chdir(WORKSPACE)
started = time.monotonic()
runtime = None
run_id = None
failure = None
messages = []
try:
    with observe_run(workspace=WORKSPACE, source='headless', title='Review feedback correction and delivery', journal_factory=lambda _: journal, enabled=True) as observation:
        run_id = observation.run_id
        print(json.dumps({'event': 'run_started', 'runId': run_id}), flush=True)
        runtime = create_agent_turn_runtime(workspace=WORKSPACE, prompt=PROMPT, include_mcp=False, allow_user_interaction=False, event_sink=observation, deadline_monotonic=time.monotonic() + 600)
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
    if runtime is not None:
        runtime.dispose()
    summary = {'historySource': str(HISTORY_PATH), 'historyMessageCount': len(history) if 'history' in locals() else 0, 'runId': run_id, 'failure': failure, 'durationSeconds': round(time.monotonic()-started, 2), 'workflowApprovals': approval.records, 'permissions': approval_log}
    if run_id:
        events = ns._all_events(journal, run_id)
        calls, input_tokens, output_tokens = ns._usage_from_events(journal, run_id)
        summary.update(modelCalls=calls, inputTokens=input_tokens, outputTokens=output_tokens, subagents=cost._subagent_rows(journal, [run_id]), eventTypes=[e.type for e in events])
    (RUN / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)

sys.exit(1 if failure else 0)
