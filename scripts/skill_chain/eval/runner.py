"""Real Agent Loop evaluation. Live calls are opt-in; expected labels stay outside run_case.

All ordinary tools execute their production implementations on a read-only,
path-contained fixture. This is a diagnosis/explanation benchmark, not an
arbitrary coding-task completion benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from scripts.skill_chain.eval.fixture_builder import SKILLS, build_fixture

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

SAFE_TOOLS = frozenset({'read_file', 'list_files', 'file_tree', 'grep_files', 'load_skill', 'browse_skill_directory', 'search_skills'})


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def json_sha(value: object) -> str:
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_cases() -> list[dict]:
    raw = (HERE / 'cases.json').read_bytes()
    expected_digest = (HERE / 'cases.sha256').read_text().split()[0]
    if sha(raw) != expected_digest:
        raise RuntimeError('Frozen cases.json digest mismatch; do not relabel after live runs')
    return json.loads(raw)['cases']


def implementation_hashes() -> dict[str, str]:
    sources = {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in sorted((REPO / 'minicode').rglob('*.py'))}
    sources.update({f'evaluator/{p.name}': sha(p.read_bytes()) for p in HERE.glob('*.py')})
    return sources


class RecordingModel:
    """Observe actual adapter requests and responses; never select any tool."""
    def __init__(self, adapter, result: dict, run_dir: Path, output_cap: int = 8000):
        self.adapter = adapter
        self.result = result
        self.run_dir = run_dir
        self.output_cap = output_cap
        self.used_output = 0

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def next(self, messages, on_stream_chunk=None, store=None):
        if self.used_output >= self.output_cap:
            self.result['budget_exhausted'] = 'output_tokens'
            raise RuntimeError('evaluation_output_token_budget_exhausted')
        adapter_runtime = getattr(self.adapter, 'runtime', None)
        if isinstance(adapter_runtime, dict):
            adapter_runtime['maxOutputTokens'] = min(1600, self.output_cap - self.used_output)
        index = len(self.result['model_calls']) + 1
        snapshot = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
        call = {'index': index, 'completed': False, 'input_sha256': json_sha(snapshot), 'input_file': f'requests/{index:02d}.json', 'body_digests_present': []}
        for load in self.result['loads']:
            if load['ok'] and load['body_integrity_ok'] and any(load['body'] in str(m.get('content', '')) for m in snapshot):
                call['body_digests_present'].append(load['body_sha256'])
                load['request_attempts_with_body'].append(index)
        self.result['model_calls'].append(call)
        write_json(self.run_dir / call['input_file'], snapshot)
        write_json(self.run_dir / 'run.json', self.result)
        started = time.monotonic()
        try:
            step = self.adapter.next(messages, on_stream_chunk=on_stream_chunk, store=store)
        except BaseException as exc:
            call.update(error_type=type(exc).__name__, elapsed_ms=round((time.monotonic() - started) * 1000, 2))
            # Provider exception strings can contain URLs or credentials. Keep
            # exception class and timeout classification, never raw runtime.
            call['timeout'] = 'timeout' in type(exc).__name__.lower() or 'timed out' in str(exc).lower() or 'timeout' in str(exc).lower()
            write_json(self.run_dir / 'run.json', self.result)
            raise
        call.update(completed=True, elapsed_ms=round((time.monotonic() - started) * 1000, 2), type=step.type, content=step.content, calls=step.calls)
        if step.usage is not None:
            call['usage'] = asdict(step.usage)
            self.used_output += int(step.usage.output_tokens or 0)
        else:
            # Offline smoke may not provide usage; no live result substitutes
            # estimated usage for the provider's token accounting.
            call['usage'] = None
        for load in self.result['loads']:
            if index in load['request_attempts_with_body']:
                load['confirmed_handoff_requests'].append(index)
        write_json(self.run_dir / f'responses/{index:02d}.json', call)
        write_json(self.run_dir / 'run.json', self.result)
        return step


class SmokeModel:
    """Explicit offline script, never included in live accuracy statistics."""
    def __init__(self, scenario: str):
        from minicode.types import AgentStep
        def calls(*items):
            return AgentStep(type='tool_calls', calls=[{'id': f'{len(steps)}-{i}', 'toolName': n, 'input': a} for i, (n, a) in enumerate(items)])
        steps = []
        steps.append(calls(('list_files', {'path': '.'}), ('read_file', {'path': 'README.md'})))
        if scenario == 'denied':
            steps.append(calls(('read_file', {'path': 'shop/settings.py'}), ('read_file', {'path': 'evidence/config.json'})))
            steps.append(AgentStep(type='assistant', content='<final>APP_PORT=9200, from environment.'))
        else:
            steps.append(calls(('load_skill', {'name': 'queue/idempotency'}), ('read_file', {'path': 'evidence/queue.json'})))
            # This read must be retried after the production load barrier.
            steps.append(calls(('read_file', {'path': 'evidence/queue.json'})))
            steps.append(AgentStep(type='assistant', content='<final>Delivery boundary: processing_ledger and shipment_outbox must commit atomically before acknowledgement.'))
        self.steps = iter(steps)

    def next(self, messages, on_stream_chunk=None, store=None):
        return next(self.steps)


def instrument_registry(registry, workspace: Path, result: dict):
    """Keep production ToolDefinitions; wrap only containment and observation."""
    from minicode.tooling import ToolRegistry, ToolResult
    selected = []
    lock = threading.Lock()
    for definition in registry.list():
        if definition.name not in SAFE_TOOLS:
            continue
        original = definition.run
        name = definition.name

        def observed(data, context, *, original=original, name=name):
            model_index = len(result['model_calls'])
            path = data.get('path', '.') if name in {'read_file', 'list_files', 'file_tree', 'grep_files'} else None
            if path is not None:
                target = (workspace / str(path)).resolve()
                if not target.is_relative_to(workspace):
                    # Actual sandbox denial only; ordinary in-fixture reads
                    # and listing execute normally and are never fabricated.
                    return ToolResult(ok=False, output='error[outside_fixture]: path is outside this evaluation workspace')
            started = time.monotonic()
            output = original(data, context)
            text = str(output.output)
            record = {'model_index': model_index, 'name': name, 'input': data, 'ok': bool(output.ok), 'output_sha256': sha(text), 'output': text, 'elapsed_ms': round((time.monotonic() - started) * 1000, 2)}
            with lock:
                record['ordinal'] = len(result['executions']) + 1
                result['executions'].append(record)
                if name == 'load_skill':
                    qualified = str(data.get('name', ''))
                    body = ''
                    integrity = False
                    if output.ok:
                        for line in text.splitlines():
                            if line.startswith('PATH: '):
                                skill_path = Path(line[6:]).resolve()
                                if skill_path.is_relative_to(workspace / '.mini-code/skills'):
                                    qualified = str(skill_path.parent.relative_to(workspace / '.mini-code/skills'))
                                    body = skill_path.read_text(encoding='utf-8')
                                    integrity = body in text
                                    break
                    result['loads'].append({'model_index': model_index, 'requested_name': data.get('name'), 'qualified_name': qualified, 'ok': bool(output.ok), 'body_integrity_ok': integrity, 'body': body, 'body_sha256': sha(body), 'output_sha256': sha(text), 'request_attempts_with_body': [], 'confirmed_handoff_requests': []})
            return output
        selected.append(replace(definition, run=observed))
    return ToolRegistry(selected, skills=registry.get_skills())


def run_case(*, prompt: str, fixture: str, run_dir: Path, runtime: dict,
             scale: str = 'small', mode: str = 'model', hints: bool = False,
             prefetch: bool = False, fake: bool = False, smoke_scenario: str = 'positive',
             max_steps: int = 8, output_cap: int = 8000) -> dict:
    """Execution side receives no labels, target IDs, required evidence or marker."""
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn
    from minicode.auto_mode import PermissionMode
    from minicode.capability_registry import build_tool_capability_snapshot
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.skill_router import SkillRouter, skill_load_policy_for_routing
    from minicode.skill_routing_turn import (
        SkillRoutingTurnAdapters,
        SkillRoutingTurnRequest,
        route_skill_turn,
    )
    from minicode.task_outcome import AgentOutcomeCapture
    from minicode.tools import create_default_tool_registry

    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace = run_dir / 'workspace'
    manifest = build_fixture(workspace, fixture, 0 if scale == 'small' else 1000 - len(SKILLS))
    write_json(run_dir / 'fixture-manifest.json', manifest)
    result = {'schema': 'skill-chain-run/v1', 'fake': fake, 'scale': scale, 'mode': mode, 'hints': hints, 'prefetch': prefetch, 'workspace': str(workspace), 'prompt': prompt, 'catalog_count': manifest['skill_count'], 'fixture_sha256': manifest['sha256'], 'model_calls': [], 'executions': [], 'loads': [], 'completed': False, 'timeout': False, 'max_steps': max_steps, 'output_cap': output_cap, 'stop_reason': 'running'}
    started = time.monotonic()
    local_runtime = dict(runtime)
    local_runtime.update(modelTimeoutSeconds=60, modelMaxRetries=0, maxOutputTokens=min(1600, output_cap), mcpServers={})
    local_runtime['skillSelection'] = {**runtime.get('skillSelection', {}), 'mode': mode, 'intentHints': hints, 'intentPrefetch': prefetch}
    result['runtime_public'] = {'model': local_runtime.get('model'), 'modelTimeoutSeconds': 60, 'modelMaxRetries': 0, 'maxOutputTokens': local_runtime['maxOutputTokens'], 'tool_allowlist': sorted(SAFE_TOOLS)}
    # The user's HOME is never changed, copied or serialized. Only the Skill
    # discovery home dependency is injected, excluding personal Skill trees.
    empty_skill_home = run_dir / 'empty-skill-home'
    empty_skill_home.mkdir()
    capture = AgentOutcomeCapture()
    owner = None
    try:
        with patch('minicode.skills._home_dir', return_value=empty_skill_home):
            owner = create_default_tool_registry(str(workspace), runtime=local_runtime, include_mcp=False, include_user_interaction=False)
            tools = instrument_registry(owner, workspace, result)
            catalog = tools.get_skills()
            result['actual_catalog_count'] = len(catalog)
            if len(catalog) != manifest['skill_count']:
                raise RuntimeError('Skill catalog isolation/count mismatch')
            decision = route_skill_turn(SkillRoutingTurnRequest(prompt=prompt, skills=catalog, registry=build_tool_capability_snapshot(tools)), SkillRoutingTurnAdapters(router=SkillRouter()), controls={'skill_selection_mode': mode, 'skill_flat_catalog_chars': 8000, 'skill_page_chars': 4000, 'skill_intent_hints': hints, 'skill_intent_prefetch': prefetch})
            result['routing'] = decision.routing.to_dict()
            result['prompt_mode'] = decision.prompt_mode
            result['routing_outcome'] = decision.outcome
            policy = skill_load_policy_for_routing(decision.routing)
            permissions = PermissionManager(str(workspace), prompt=lambda request: {'decision': 'deny_once'}, auto_mode=PermissionMode.PLAN)
            system = build_system_prompt(str(workspace), permissions.get_summary(), {'skills': list(decision.prompt_skills), 'skill_routing': decision.routing.to_dict(), 'mcpServers': [], 'memory_context': '', 'user_interaction_available': False})
            (run_dir / 'system-prompt.txt').write_text(system, encoding='utf-8')
            write_json(run_dir / 'tool-schemas.json', [{'name': t.name, 'description': t.description, 'input_schema': t.input_schema} for t in tools.list()])
            adapter = SmokeModel(smoke_scenario) if fake else create_model_adapter(local_runtime['model'], tools, runtime=local_runtime)
            if not fake and type(adapter).__name__ != 'OpenAIModelAdapter':
                raise RuntimeError('Live evaluation requires the authorized OpenAI-compatible adapter; mock and other providers are rejected')
            result['adapter'] = type(adapter).__name__
            model = RecordingModel(adapter, result, run_dir, output_cap)
            messages = run_agent_turn(model=model, tools=tools, messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}], cwd=str(workspace), permissions=permissions, max_steps=max_steps, runtime=local_runtime, system_prompt=system, enable_work_chain=False, memory_manager=None, context_manager=None, allow_user_interaction=False, max_steps_soft_landing=False, required_skill_names=decision.routing.required_skill_names(), skill_load_policy=policy, parsed_intent=decision.intent, agent_budget=AgentTurnBudget(max_model_calls=max_steps, max_total_tokens=180000), budget_exhausted_policy='raise', deadline_monotonic=time.monotonic() + max_steps * 60 + 20, outcome_capture=capture, defer_curator_maintenance=True)
            write_json(run_dir / 'messages-at-stop.json', messages)
            result['final'] = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'assistant'), '')
            result['outcome'] = asdict(capture.outcome) if capture.outcome is not None else None
            result['completed'] = bool(capture.outcome and capture.outcome.completion_succeeded and result['final'])
            result['stop_reason'] = 'assistant_completed' if result['completed'] else 'loop_stopped_without_completion'
    except BaseException as exc:  # noqa: BLE001 - preserve interrupted/failed evaluation evidence
        if isinstance(exc, KeyboardInterrupt):
            result['stop_reason'] = 'interrupted'
        else:
            result['stop_reason'] = 'exception'
        result['error_type'] = type(exc).__name__
        if type(exc).__name__ == 'AgentBudgetExceeded':
            result['budget_exhausted'] = 'agent_budget'
        if fake:
            result['offline_error'] = str(exc)
        result['timeout'] = 'timeout' in type(exc).__name__.lower() or 'timeout' in str(exc).lower() or 'timed out' in str(exc).lower()
    finally:
        if owner is not None:
            owner.dispose()
        result['timeout'] = result['timeout'] or any(c.get('timeout') for c in result['model_calls'])
        result['infrastructure_error'] = any(c.get('error_type') and not c.get('completed') for c in result['model_calls'])
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        result['model_call_count'] = len(result['model_calls'])
        result['confirmed_handoff_count'] = sum(bool(l['confirmed_handoff_requests']) for l in result['loads'])
        result['fixture_unchanged'] = all((workspace / name).is_file() and sha((workspace / name).read_bytes()) == digest for name, digest in manifest['files'].items())
        write_json(run_dir / 'run.json', result)
    return result


def score_run(run: dict, expected: dict) -> dict:
    """Deterministic score after execution; keep metrics and denominators distinct."""
    request_error = any(c.get('error_type') and not c.get('completed') for c in run.get('model_calls', []))
    infrastructure_error = bool(run.get('infrastructure_error') or request_error)
    unclassified_exception = run.get('stop_reason') == 'exception' and not run.get('budget_exhausted')
    valid = not (run.get('timeout') or infrastructure_error or unclassified_exception or run.get('fake') or run.get('stop_reason') == 'interrupted')
    loaded = [x for x in run.get('loads', []) if x['ok'] and x['body_integrity_ok']]
    loaded_names = {x['qualified_name'] for x in loaded}
    allowed = set(expected['allowed_skills'])
    wrong = sorted(loaded_names - allowed)
    final = str(run.get('final', ''))
    forbidden_output = any(t.lower() in final.lower() for t in expected.get('forbidden_output_tokens', []))
    stages = []
    previous_evidence_step = 0
    def evidence_path(value: object) -> str:
        path = Path(str(value))
        workspace = run.get('workspace')
        if workspace and path.is_absolute():
            try:
                return str(path.resolve().relative_to(Path(workspace).resolve()))
            except ValueError:
                return str(path)
        return str(path)
    for spec in expected['stages']:
        matching = [x for x in loaded if x['qualified_name'] in spec['skills']]
        load = matching[0] if matching else None
        reads = [x for x in run['executions'] if x['name'] == 'read_file' and x['ok'] and evidence_path(x['input'].get('path', '')) == spec['evidence']]
        after = [r for r in reads if load and r['model_index'] > load['model_index'] and any(h <= r['model_index'] for h in load['confirmed_handoff_requests'])]
        read = after[0] if after else None
        output_ok = spec['report_label'].lower() in final.lower() and all(t.lower() in final.lower() for t in spec['required_tokens'])
        ordered = bool(load and load['model_index'] > previous_evidence_step)
        stages.append({'skills': spec['skills'], 'load_hit': bool(load), 'handoff': bool(load and load['confirmed_handoff_requests']), 'evidence_read_after_handoff': bool(read), 'phase_order': ordered, 'report_matches': output_ok, 'load_model_index': load['model_index'] if load else None, 'evidence_model_index': read['model_index'] if read else None})
        previous_evidence_step = read['model_index'] if read else 10**9
    negative = bool(expected['negative'])
    any_hit = bool(loaded_names & allowed)
    all_hit = bool(stages) and all(s['load_hit'] for s in stages)
    strict_load = all_hit and not wrong
    stage_followed = bool(stages) and all(s['handoff'] and s['evidence_read_after_handoff'] and s['phase_order'] and s['report_matches'] for s in stages)
    result_ok = bool(run.get('completed') and not forbidden_output and run.get('fixture_unchanged'))
    if negative:
        result_ok = result_ok and all(t.lower() in final.lower() for t in expected.get('required_tokens', []))
    return {'valid_live': valid, 'infrastructure_error': infrastructure_error, 'budget_exhausted': run.get('budget_exhausted'), 'negative': negative, 'timeout': bool(run.get('timeout')), 'completed': bool(run.get('completed')), 'any_hit': any_hit, 'all_required_loads': all_hit, 'strict_load': strict_load, 'wrong_extra_loads': wrong, 'forbidden_loaded': sorted(loaded_names & set(expected.get('forbidden_skills', []))), 'body_handoff_count': run.get('confirmed_handoff_count', 0), 'stages': stages, 'result_ok': result_ok, 'negative_false_trigger': bool(loaded_names) if negative else None, 'negative_clean': bool(valid and result_ok and not loaded_names) if negative else None, 'strict_end_to_end': bool(valid and result_ok and strict_load and stage_followed) if not negative else None}


def summarize(rows: list[dict]) -> dict:
    live = [r for r in rows if not r['fake']]
    positives = [r for r in live if not r['score']['negative']]
    negatives = [r for r in live if r['score']['negative']]
    valid_pos = [r for r in positives if r['score']['valid_live']]
    valid_neg = [r for r in negatives if r['score']['valid_live']]
    return {'attempted_live': len(live), 'fake_excluded': sum(r['fake'] for r in rows), 'positive_attempted': len(positives), 'positive_valid': len(valid_pos), 'negative_attempted': len(negatives), 'negative_valid': len(valid_neg), 'timeouts': sum(r['score']['timeout'] for r in live), 'infrastructure_errors': sum(r['score'].get('infrastructure_error', False) for r in live), 'any_hit': sum(r['score']['any_hit'] for r in valid_pos), 'strict_load': sum(r['score']['strict_load'] for r in valid_pos), 'strict_end_to_end': sum(r['score']['strict_end_to_end'] for r in valid_pos), 'negative_false_triggers': sum(r['score']['negative_false_trigger'] for r in valid_neg), 'negative_clean': sum(r['score']['negative_clean'] for r in valid_neg), 'body_handoff_count': sum(r['score']['body_handoff_count'] for r in live), 'note': 'Counts only. Positive valid denominator is separate from attempted; timeout never counts as success. Negative clean is not a body handoff.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cases', default='C01,C17', help='Comma-separated IDs, all, dev, or holdout')
    parser.add_argument('--scale', choices=['small', 'large'], default='small')
    parser.add_argument('--mode', choices=['model', 'routed'], default='model')
    parser.add_argument('--hints', action='store_true')
    parser.add_argument('--prefetch', action='store_true')
    parser.add_argument('--runtime-cwd', type=Path, default=REPO)
    args = parser.parse_args()
    cases = load_cases()
    if args.cases in {'dev', 'holdout'}:
        split_raw = (HERE / 'splits.json').read_bytes()
        if sha(split_raw) != (HERE / 'splits.sha256').read_text().split()[0]:
            raise RuntimeError('Frozen split digest mismatch')
        args.cases = ','.join(json.loads(split_raw)[args.cases])
    selected = cases if args.cases == 'all' else [c for c in cases if c['id'] in args.cases.split(',')]
    if not selected:
        parser.error('No matching case IDs')
    if not args.live and any(c['id'] not in {'C01', 'C17'} for c in selected):
        parser.error('Offline scripts exist only for C01/C17; they are pipeline checks, never live scores')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = implementation_hashes()
    write_json(args.output / 'implementation-freeze.json', source_hashes)
    write_json(args.output / 'evaluation-input-freeze.json', {'cases_sha256': sha((HERE / 'cases.json').read_bytes()), 'splits_sha256': sha((HERE / 'splits.json').read_bytes()), 'selected_ids': [c['id'] for c in selected], 'scale': args.scale, 'mode': args.mode, 'hints': args.hints, 'prefetch': args.prefetch, 'fake': not args.live})
    # Config is read once in the parent process, remains only in memory, and
    # is never serialized, copied into fixtures, or included in reports.
    if args.live:
        from minicode.config import load_runtime_config
        runtime = load_runtime_config(str(args.runtime_cwd))
        if runtime.get('model') != 'glm-5.3-flash':
            raise RuntimeError('This evaluation is authorized only for runtime model glm-5.3-flash')
    else:
        runtime = {'model': 'offline-smoke'}
    rows = []
    for case in selected:
        if implementation_hashes() != source_hashes:
            raise RuntimeError('Implementation changed during batch; preserve existing runs and begin a separately identified batch')
        run = run_case(prompt=case['prompt'], fixture=case['fixture'], run_dir=args.output / case['id'], runtime=runtime, scale=args.scale, mode=args.mode, hints=args.hints, prefetch=args.prefetch, fake=not args.live, smoke_scenario='denied' if case['id'] == 'C17' else 'positive')
        row = {'id': case['id'], 'fake': run['fake'], 'score': score_run(run, case['expected'])}
        rows.append(row)
        write_json(args.output / 'results.json', rows)
        write_json(args.output / 'summary.json', summarize(rows))
        print(json.dumps({'id': case['id'], 'stop': run['stop_reason'], 'loads': [l['qualified_name'] for l in run['loads'] if l['ok']], 'handoff': run['confirmed_handoff_count'], 'score': row['score']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
