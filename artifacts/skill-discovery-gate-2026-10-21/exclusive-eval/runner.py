"""One isolated full-Agent-Loop trial for the directory-presel verification.

Adapted from the frozen three-arm runner: a single hierarchical arm with
advisory intent hints and directory-level semantic preselection enabled.
The scheduler owns repetition and dispatch. Run in a fresh process.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import importlib
import json
import os
from pathlib import Path
import sys
import time

from recording import RecordingModel, RunRecorder, error_category, json_sha, sha, write_json

HERE = Path(__file__).resolve().parent
SAFE_TOOLS = frozenset({'read_file', 'list_files', 'file_tree', 'grep_files', 'load_skill', 'browse_skill_directory', 'search_skills'})
DEFAULT_LIMITS = {
    'max_steps': 16, 'max_model_calls': 16, 'call_timeout_seconds': 60,
    'turn_timeout_seconds': 420, 'max_output_tokens': 2048,
    'output_token_cap': 12000, 'total_token_cap': 500000,
}
ARM_P_SELECTION = {
    'mode': 'model',
    'flatCatalogCharBudget': 8000,
    'skillPageCharBudget': 4000,
    'intentHints': True,
    'intentPrefetch': False,
    'directoryPresel': True,
}


def bind_snapshot(snapshot: Path, home: Path, environment: dict[str, str]) -> dict:
    if any(name == 'minicode' or name.startswith('minicode.') for name in sys.modules):
        raise RuntimeError('minicode_preloaded_before_snapshot_isolation')
    snapshot = snapshot.resolve(strict=True)
    if not (snapshot / 'minicode' / '__init__.py').is_file():
        raise ValueError('snapshot_missing_minicode_package')
    home.mkdir(parents=True, exist_ok=False)
    for name in list(os.environ):
        if name.startswith(('MINI_CODE_', 'MINICODE_', 'OPENAI_', 'ANTHROPIC_',
                            'OPENROUTER_', 'CUSTOM_API_', 'DEEPSEEK_', 'DASHSCOPE_', 'QWEN_')):
            os.environ.pop(name)
    for name, value in environment.items():
        if not isinstance(value, str):
            raise TypeError('private_environment_values_must_be_strings')
        os.environ[name] = value
    os.environ.update(HOME=str(home), USERPROFILE=str(home), XDG_CACHE_HOME=str(home / '.cache'),
                      XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(snapshot))
    package = importlib.import_module('minicode')
    if not Path(package.__file__).resolve().is_relative_to(snapshot):
        raise RuntimeError('snapshot_import_origin_mismatch')
    sources = {str(path.relative_to(snapshot)): sha(path.read_bytes())
               for path in sorted(snapshot.rglob('*'))
               if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.pyo'}}
    return {'snapshot_root': str(snapshot), 'import_origin': str(Path(package.__file__).resolve()),
            'file_count': len(sources), 'files_sha256': json_sha(sources)}


def _changed_sources(workspace: Path, original: dict[str, str]) -> list[str]:
    return [name for name, digest in original.items()
            if not (workspace / name).is_file() or sha((workspace / name).read_bytes()) != digest]


def run_trial(*, snapshot: Path, private: dict, case_id: str,
              repeat: int, output: Path, live: bool,
              gate: bool = False) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = private.get('runtime', private)
    environment = private.get('environment', {}) if 'runtime' in private else {}
    imported = bind_snapshot(snapshot, output / 'home', environment)
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn
    from minicode.auto_mode import PermissionMode
    from minicode.capability_registry import build_tool_capability_snapshot
    from minicode.context_manager import ContextManager
    from minicode.intent_semantic_classifier import build_semantic_intent_adapter
    from minicode.model_registry import create_model_adapter
    from minicode.permissions import PermissionManager
    from minicode.prompt import build_system_prompt
    from minicode.skill_directory_presel import DirectoryPreselMatcher
    from minicode.skill_router import build_skill_router, required_skill_names_for_routing, skill_load_policy_for_routing, suggested_skill_directories_for_routing
    from minicode.skill_routing_turn import SkillRoutingTurnAdapters, SkillRoutingTurnRequest, route_skill_turn
    from minicode.task_outcome import AgentOutcomeCapture
    from minicode.tools import create_default_tool_registry
    from case_bundle import build_fixture, execution_case

    allowlist = set(SAFE_TOOLS)
    limits = {**DEFAULT_LIMITS}
    case = execution_case(case_id)
    if set(case) != {'case_id', 'prompt', 'fixture'} or case['case_id'] != case_id:
        raise ValueError('execution_case_must_not_supply_labels_or_extra_fields')
    workspace = output / 'workspace'
    local = copy.deepcopy(runtime)
    selection = dict(local.get('skillSelection') or {})
    selection.update(ARM_P_SELECTION)
    local['skillSelection'] = selection
    local.update(modelTimeoutSeconds=limits['call_timeout_seconds'], maxOutputTokens=limits['max_output_tokens'])
    result = {
        'schema': 'skill-exclusive-eval-run/v1', 'case_id': case_id, 'arm': 'P',
        'repeat': repeat, 'fake': not live, 'workspace': str(workspace), 'prompt': case['prompt'],
        'fixture': case['fixture'], 'code_snapshot': imported,
        'limits': limits, 'model_calls': [], 'provider_requests': [], 'executions': [], 'loads': [],
        'events': [], 'phase_timings_ms': {}, 'completed': False, 'timeout': False,
        'infrastructure_error': False, 'stop_reason': 'running', 'final': '', 'outcome': None,
        'embedding_cache': 'none_model_first_directory_presel_cold_start',
        'runtime_public': {key: local.get(key) for key in (
            'model', 'provider', 'modelTimeoutSeconds', 'modelMaxRetries', 'maxOutputTokens',
            'temperature', 'enableThinking', 'skillSelection')},
        'tool_allowlist': sorted(allowlist),
    }
    observer = RunRecorder(result, output, live=live)
    started = time.monotonic()
    deadline = started + limits['turn_timeout_seconds']
    owner, manifest = None, None
    capture = AgentOutcomeCapture()
    observer.save()
    try:
        with observer.observe_http():
            with observer.timed_phase('fixture'):
                manifest = build_fixture(workspace, case_id)
                write_json(output / 'fixture-manifest.json', manifest)
                result['fixture_sha256'] = manifest['sha256']
                result['catalog_count'] = manifest['skill_count']
            with observer.timed_phase('registry'):
                owner = create_default_tool_registry(str(workspace), runtime=local, include_mcp=False, include_user_interaction=False)
                tools = observer.instrument_registry(owner, workspace, allowlist)
                catalog = tools.get_skills()
                result['actual_catalog_count'] = len(catalog)
                if len(catalog) != manifest['skill_count']:
                    raise RuntimeError('catalog_count_mismatch')
                write_json(output / 'catalog.json', catalog)
                write_json(output / 'tool-schemas.json', [{'name': t.name, 'description': t.description, 'input_schema': t.input_schema} for t in tools.list()])
                adapter = create_model_adapter(local['model'], tools, runtime=local) if live else None
                if live and type(adapter).__name__ != 'OpenAIModelAdapter':
                    raise RuntimeError('verification_requires_frozen_openai_compatible_adapter')
                result['adapter'] = type(adapter).__name__ if live else 'SmokeModel'
            with observer.timed_phase('preparation'):
                controls = {
                    'agent_budget': AgentTurnBudget(max_model_calls=limits['max_model_calls'], max_total_tokens=limits['total_token_cap']),
                    'event_sink': observer, 'deadline_monotonic': deadline,
                    'skill_selection_mode': 'model',
                    'skill_flat_catalog_chars': ARM_P_SELECTION['flatCatalogCharBudget'],
                    'skill_page_chars': ARM_P_SELECTION['skillPageCharBudget'],
                    'skill_intent_hints': True,
                    'skill_intent_prefetch': False,
                    'skill_directory_presel': True,
                }
                router = build_skill_router(str(workspace))
                presel = DirectoryPreselMatcher.from_environment(str(workspace))
                result['embedding_configured'] = getattr(router, '_embedding', None) is not None
                result['presel_configured'] = presel is not None
                if presel is None and live:
                    raise RuntimeError('presel_matcher_unavailable_in_trial')
                model = RecordingModel(adapter, observer, output_cap=limits['output_token_cap'],
                                       per_call_output=limits['max_output_tokens'], call_timeout=limits['call_timeout_seconds']) if live else _SmokeModel(catalog, local)
                budget = controls['agent_budget']
                permissions = PermissionManager(str(workspace), prompt=None, auto_mode=PermissionMode.PLAN)
                decision = route_skill_turn(
                    SkillRoutingTurnRequest(prompt=case['prompt'], skills=catalog, registry=build_tool_capability_snapshot(tools)),
                    SkillRoutingTurnAdapters(router=router,
                        semantic_intent=build_semantic_intent_adapter(model, runtime=local) if live else None,
                        candidate_reranker=None,
                        directory_presel=presel),
                    controls=controls,
                )
                result.update(routing=decision.routing.to_dict(), prompt_mode=decision.prompt_mode,
                              routing_outcome=decision.outcome, intent=asdict(decision.intent))
                gate_suggested = suggested_skill_directories_for_routing(decision.routing)
                result['gate_suggested'] = sorted(gate_suggested)
                result['gate_enabled'] = bool(gate)
                hints = decision.routing.to_dict().get('intent_hints') or {}
                cache_file = workspace / '.mini-code' / 'skill-directory-embeddings.json'
                vectors = {}
                if cache_file.is_file():
                    try:
                        vectors = json.loads(cache_file.read_text(encoding='utf-8')).get('vectors', {})
                    except (ValueError, OSError):
                        vectors = {}
                result['presel_telemetry'] = {
                    'suggested_directories': hints.get('suggested_directories', []),
                    'suggested_directory_sources': hints.get('suggested_directory_sources', {}),
                    'directory_vectors_cached': len(vectors),
                }
                system = build_system_prompt(str(workspace), permissions.get_summary(), {
                    'skills': list(decision.prompt_skills), 'skill_routing': decision.routing.to_dict(),
                    'mcpServers': [], 'memory_context': '', 'user_interaction_available': False,
                })
                result['system_prompt_chars'] = len(system)
                result['system_prompt_bytes'] = len(system.encode('utf-8'))
                (output / 'system-prompt.txt').write_text(system, encoding='utf-8')
            with observer.timed_phase('agent_loop'):
                messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': case['prompt']}]
                context_manager = ContextManager(model=local.get('model', 'default'))
                context_manager.messages = messages
                messages = run_agent_turn(
                    model=model, tools=tools, messages=messages, cwd=str(workspace), permissions=permissions,
                    max_steps=limits['max_steps'], runtime=local, system_prompt=system,
                    enable_work_chain=False, memory_manager=None, context_manager=context_manager,
                    allow_user_interaction=False, max_steps_soft_landing=False, enable_bounded_completion=True,
                    required_skill_names=required_skill_names_for_routing(decision.routing),
                    skill_load_policy=skill_load_policy_for_routing(decision.routing), parsed_intent=decision.intent,
                    agent_budget=budget, budget_exhausted_policy='raise', deadline_monotonic=deadline,
                    outcome_capture=capture, event_sink=observer, defer_curator_maintenance=True,
                    suggested_skill_directories=gate_suggested,
                    skill_discovery_gate=bool(gate),
                )
                write_json(output / 'messages-at-stop.json', messages)
                final_message = next((m for m in reversed(messages) if m.get('role') == 'assistant'), {})
                result['final'] = final_message.get('content', '')
                result['outcome'] = asdict(capture.outcome) if capture.outcome is not None else None
                result['completed'] = bool(capture.outcome and capture.outcome.status == 'success'
                                           and result['final'] and not final_message.get('_partial'))
                result['stop_reason'] = 'completed' if result['completed'] else 'loop_returned_without_successful_final'
                if result.get('budget_exhausted'):
                    result['completed'] = False
                    result['stop_reason'] = 'budget'
    except BaseException as exc:
        category = error_category(exc)
        result.update(error_type=type(exc).__name__, error_category=category,
                      timeout=category == 'timeout', infrastructure_error=category not in {'budget', 'timeout'},
                      stop_reason=category if category != 'unclassified' else 'execution_error')
        if capture.outcome is not None:
            result['outcome'] = asdict(capture.outcome)
    finally:
        if owner is not None:
            owner.dispose()
        result['wall_seconds'] = round(time.monotonic() - started, 3)
        result['model_call_count'] = len(result['model_calls'])
        result['source_changed_paths'] = _changed_sources(workspace, manifest['files']) if manifest else None
        result['cost'] = observer.cost_summary()
        first_wire = next((r for r in result['provider_requests'] if r['phase'] == 'agent_loop' and r['kind'] == 'chat'), None)
        result['first_agent_request'] = ({k: first_wire.get(k) for k in (
            'index', 'request_bytes', 'tools_json', 'messages_json', 'tool_count', 'usage')}
            if first_wire else None)
        observer.save()
    return result


class _SmokeModel:
    """Offline instrument smoke, excluded from the live denominator."""
    def __init__(self, catalog, runtime):
        from minicode.types import AgentStep
        self.steps = iter([
            AgentStep(type='tool_calls', calls=[
                {'id': 'smoke-browse', 'toolName': 'browse_skill_directory', 'input': {'directory': 'http'}},
                {'id': 'smoke-read', 'toolName': 'read_file', 'input': {'path': 'README.md'}},
            ]),
            AgentStep(type='assistant', content='{"offline_smoke":true}'),
        ])

    def next(self, messages, **kwargs):
        return next(self.steps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=HERE / 'snapshot')
    parser.add_argument('--runtime-file', type=Path, required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--gate', action='store_true')
    args = parser.parse_args()
    private = json.loads(args.runtime_file.read_text(encoding='utf-8'))
    try:
        result = run_trial(snapshot=args.snapshot, private=private,
                           case_id=args.case_id, repeat=args.repeat,
                           output=args.output, live=args.live, gate=args.gate)
    except BaseException as exc:
        result = {'schema': 'skill-exclusive-eval-run/v1', 'case_id': args.case_id,
                  'arm': 'P', 'repeat': args.repeat, 'fake': not args.live,
                  'completed': False, 'stop_reason': 'worker_setup_error',
                  'model_call_count': 0, 'wall_seconds': None, 'timeout': False,
                  'infrastructure_error': True, 'error_type': type(exc).__name__,
                  'error_category': error_category(exc)}
        if not (args.output / 'run.json').exists():
            write_json(args.output / 'run.json', result)
    print(json.dumps({key: result.get(key) for key in ('case_id', 'arm', 'repeat', 'fake', 'completed', 'stop_reason', 'model_call_count', 'wall_seconds')}))
    return 0 if result['completed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
