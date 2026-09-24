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
RUN = ARTIFACT / 'live-run'
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
    'maxTokens': 400000, 'maxModelCalls': 8, 'maxCostUsd': 5,
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
PROMPT = '''我发现 test_runner 跑目录时会误报通过：一个包含12个 test_*.py 文件的目录里，有一个用例失败，直接用 pytest 能看到失败，但工具却报告通过。请帮我定位并修复，补充能防止复发的回归测试，运行相关检查，最后说明原因、改了什么和验证结果。'''
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
                safe = args[0] in ('status', 'diff', 'log', 'show')
                if safe:
                    decision = {'decision': 'allow_once'}
    approval_log.append({'kind': request.get('kind'), 'decision': decision.get('decision')})
    return decision

def timeout(_signum, _frame):
    raise TimeoutError('live experiment exceeded wall time')

signal.signal(signal.SIGALRM, timeout)
signal.alarm(360)
os.chdir(WORKSPACE)
started = time.monotonic()
runtime = None
run_id = None
failure = None
messages = []
try:
    with observe_run(workspace=WORKSPACE, source='headless', title='Primary completion live regression', journal_factory=lambda _: journal, enabled=True) as observation:
        run_id = observation.run_id
        print(json.dumps({'event': 'run_started', 'runId': run_id}), flush=True)
        runtime = create_agent_turn_runtime(workspace=WORKSPACE, prompt=PROMPT, include_mcp=False, allow_user_interaction=False, event_sink=observation, deadline_monotonic=time.monotonic() + 330)
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
        initial = prepare_conversation_messages([], system_prompt=runtime.system_prompt, user_message=PROMPT)
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
    summary = {'runId': run_id, 'failure': failure, 'durationSeconds': round(time.monotonic()-started, 2), 'workflowApprovals': approval.records, 'permissions': approval_log}
    if run_id:
        events = ns._all_events(journal, run_id)
        calls, input_tokens, output_tokens = ns._usage_from_events(journal, run_id)
        summary.update(modelCalls=calls, inputTokens=input_tokens, outputTokens=output_tokens, subagents=cost._subagent_rows(journal, [run_id]), eventTypes=[e.type for e in events])
    (RUN / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)

sys.exit(1 if failure else 0)
