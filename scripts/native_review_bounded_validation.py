#!/usr/bin/env python3
"""Prepare/freeze two fixed bounded-review trials; execution requires registration.

The host is the original trigger archive plus explicit committed overlays.
No working-tree production byte is silently added to the execution archive.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from scripts import run_multi_agent_trigger as trigger

foundation, shared = trigger.foundation, trigger.shared
DEFAULT = ROOT / 'artifacts/native-review-acceptance-2026-10-23/run-v1'
SOURCE = ROOT / 'artifacts/multi-agent-trigger-2026-10-23/run-v1'
PROTOCOL_VERSION = 'native-review-bounded-v1'
CASES = ('trigger-stock-allocation', 'trigger-config-overlay')
ARMS = ('auto',)
SEED = 20261023
MODEL = 'glm-5.3-flash'
LIMITS = {'maxTokens': 500000, 'maxModelCalls': 40, 'maxCostUsd': '5',
    'wallSeconds': 600, 'maxOutputTokens': 8192, 'requestTimeoutSeconds': 300,
    'idleTimeoutSeconds': 120, 'connectTimeoutSeconds': 30}
ROLE_TURN_LIMITS = {'explore': 0, 'plan': 0, 'general': 0}
ROLE_TURN_POLICY = 'explicit-zero-role-ceilings-shared-budget-v1'
HARNESS_FILES = (
    '__init__.py', 'run_three_arm_real.py', 'evaluation_transport.py',
    'three_arm_protocol.py', 'three_arm_scope.py', 'three_arm_scope_metadata.py',
    'three_arm_processes.py', 'run_north_star_live.py', 'run_multi_agent_cost_live.py',
    'analyze_three_arm_real.py', 'run_implementation_progress_live.py',
    'audit_three_arm_real.py', 'audit_implementation_progress.py',
    'analyze_implementation_progress.py', 'implementation_judge.py',
    'public_verification_fixtures.py', 'run_multi_agent_gen2.py',
    'audit_multi_agent_gen2.py', 'analyze_multi_agent_gen2.py',
    'run_multi_agent_trigger.py', 'audit_multi_agent_trigger.py',
    'analyze_multi_agent_trigger.py', 'prepare_multi_agent_trigger_tasks.py',
    'native_review_bounded_validation.py', 'audit_native_review_bounded.py',
)
PRODUCTION_OVERLAYS = (
    'minicode/agent_budget.py', 'minicode/workflow_runner.py', 'minicode/agent_loop.py',
    'minicode/anthropic_adapter.py', 'minicode/implementation_completion_review.py',
    'minicode/completion_coverage_inspection.py', 'minicode/completion_coverage_audit.py',
    'minicode/completion_probe_migration.py', 'minicode/probe_failure_adjudication.py',
    'minicode/tooling.py', 'minicode/completion_review_limits.py',
)
BASELINE_ARCHIVE_SHA256 = 'a118a493563821da50b8d2e15385f9cdc686ee433c36e44b8f0a3990b88d7d3d'
ORIGINAL_MANIFEST_SHA256 = 'a740668dd53ca4ce15b1f07156ae498ee335b2c8873c5ff5b5b344d83b1d91ab'
ORIGINAL_TASK_SHA256 = '78550c227d36e719514f55c3d540a721a11aad8535de34a6a5155d57c8553937'
JUDGE_POLICY = trigger.JUDGE_POLICY
TLS_TRUST = {'path': '/etc/ssl/cert.pem', 'sha256': '9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994', 'certificateVerification': True}
REQUEST_EVIDENCE_POLICY = 'complete-parent-provider-body-before-send-ledger-call-v1'
GATE_POLICY = {'expectedTrials': 2, 'gatedCase': 'trigger-stock-allocation',
    'descriptiveCase': 'trigger-config-overlay', 'acceptedReviewTerminals': ['approved', 'rejected'],
    'budgetExhaustedPasses': False, 'approvedRequiresAcceptedFinal': True,
    'requireAllTerminal': True, 'requireIndependentAudit': True}
REVIEW_POLICY = {'mode': 'bounded', 'phaseLimits': {'review': [8, 90.0],
    'inspection': [8, 150.0], 'reconciliation': [4, 60.0]},
    'closingRequests': 2, 'adapterRetries': 0, 'reentryResetsAllowance': False}
ANALYSIS_PLAN = '''# Native review bounded validation preregistration

Exactly the original stock-allocation then config-overlay cells, once each.
The public prompt, source, public checks, hidden oracle and reference are exact
objects selected from the original frozen trigger suite. No new task or prompt.
Model GLM-5.3-Flash; temperature 0; adapter retry 0; seed 20261023. Shared per-cell
40 physical requests, 500000 tokens, USD 5 safety ceiling and 600 execution seconds;
8192 output tokens, request/idle/connect timeouts 300/120/30 seconds. Native review
mode is bounded, with the frozen 8/90s, 8/150s, 4/60s stage caps and parent reserve.
The old implementation phase quota mode remains off. TLS certificate verification
stays enabled, with SSL_CERT_FILE=/etc/ssl/cert.pem and registered SHA256
9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994.

The last actual native review attempt determines the stock gate: it must reach
approved or rejected within its chain and stage limits. A later parent-only timeout
does not erase a rejected review; a later native budget_exhausted does fail. A cached
negative handoff counts only with the same candidate and byte-equivalent substantive
decision as an earlier bounded real native result; only the established outer
envelope/storage fields may differ. Changed findings or candidates never inherit
that authority. A delayed exact cached handoff uses its original native authority
time, with delayedHandoff reported separately. Approved also requires an accepted final from the host.
Budget exhaustion, inconclusive, absent review or missing evidence fails. Config
has no behavioral gate. Both observations must remain valid audited terminal rows;
unknown usage is retained and labeled unverifiable; it is never fabricated as zero or an extra config behavioral gate. Private
oracle repair success is separately reported and never substitutes for acceptance.
No reruns, replacement, model change, additional attempt or selective reporting.
This is a two-cell engineering validation, not a causal or collaboration claim.

The execution host starts with the old immutable trigger archive and applies only
explicit files read from the source host commit. Every byte is inventoried with
its baseline/commit origin. Registration then commits the full archive, tasks,
source proof, environment, preregistration and public preflight before execution.
Only public files hydrate the task workspace; all oracles, reference fixes and
execution-host files stay outside it. Existing physical usage, scope, process
cleanup and independent generated-regression/oracle judge remain unchanged.

The evaluator persists existing completion events before RunJournal projection,
and observes existing create/select review allowance calls without changing their
return values or exceptions. Phase wall time spans actual selection to the next
selection or completion; interrupted spans are censored at process stop, never
called completed. Actual native HTTP requests are separately classified by their
provider-visible terminal-tool schema and checked against the physical ledger.
All observations are private; they are not prompts or production events.
'''

canonical = foundation.canonical
safe_relative = foundation.safe_relative
inventory_for = foundation.inventory_for
environment_fingerprint = foundation.environment_fingerprint
assert_host_imports = foundation.assert_host_imports
PUBLIC_FIXTURE_POLICY = foundation.PUBLIC_FIXTURE_POLICY
public_fixture_manifest = foundation.public_fixture_manifest
run_public_preflight = foundation.run_public_preflight
validate_public_preflight = foundation.validate_public_preflight
verify_archive = foundation.verify_archive
verify_host = foundation.verify_host
unpack_host = foundation.unpack_host
prompt_for_arm = trigger.prompt_for_arm


def schedule_for():
    return [{'case': c, 'arm': 'auto', 'repeat': 1, 'id': f'{c}-r1-auto'} for c in CASES]


def static_contract():
    return {'schemaVersion': 1, 'protocolVersion': PROTOCOL_VERSION, 'phase': 'native-review-bounded',
        'cases': list(CASES), 'arms': list(ARMS), 'repeats': 1, 'scheduleSeed': SEED,
        'schedule': schedule_for(), 'concurrency': 1, 'model': MODEL, 'temperature': 0,
        'limits': LIMITS, 'price': shared.PRICE, 'roleTurnLimits': ROLE_TURN_LIMITS,
        'roleTurnPolicy': ROLE_TURN_POLICY, 'judgementPolicy': JUDGE_POLICY,
        'gatePolicy': GATE_POLICY, 'reviewPolicy': REVIEW_POLICY, 'tlsTrust': TLS_TRUST,
        'harnessFiles': list(HARNESS_FILES), 'runner': 'scripts/native_review_bounded_validation.py',
        'implementationCapabilities': foundation.progress.IMPLEMENTATION_CAPABILITIES,
        'autoRecipe': 'Parent retains direct tools and task; no forced delegation',
        'implementationPhasePolicy': 'production-continuous-default',
        'requestEvidencePolicy': REQUEST_EVIDENCE_POLICY,
        'nativeObservationPolicy': 'existing-host-events-and-allowance-selection-v1',
        'hostProvenancePolicy': 'original-trigger-archive-plus-committed-explicit-overlay-v1',
        'baselineArchiveSha256': BASELINE_ARCHIVE_SHA256,
        'originalTaskSha256': ORIGINAL_TASK_SHA256,
        'comparison': 'Fixed reused stock/config engineering validation; no causal claim'}


def _foundation():
    spec = importlib.util.spec_from_file_location('_native_bounded_foundation', foundation.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('runner', 'validate_tasks', 'validate', 'validate_offline_proof'):
        setattr(module, '_original_' + name, getattr(module, name))
    module.__file__ = str(Path(__file__).resolve())
    for name in ('ROOT', 'DEFAULT', 'PROTOCOL_VERSION', 'CASES', 'ARMS', 'SEED', 'MODEL',
        'LIMITS', 'ROLE_TURN_LIMITS', 'ROLE_TURN_POLICY', 'HARNESS_FILES', 'JUDGE_POLICY',
        'GATE_POLICY', 'ANALYSIS_PLAN', 'static_contract', 'schedule_for', 'validate_tasks',
        'prompt_for_arm', 'runner', 'probe_host', 'validate', 'validate_offline_proof', 'host_files',
        'verify_archive', 'verify_host', 'unpack_host', 'run_public_preflight', 'validate_public_preflight'):
        setattr(module, name, globals()[name])
    return module


def validate_tasks(document):
    if document.get('generation') != 'trigger-v1' or [c['id'] for c in document['cases']] != list(CASES):
        raise ValueError('expected exactly the original stock/config cases, in that order')
    _foundation()._original_validate_tasks(document)


def validate_offline_proof(output, document):
    _foundation()._original_validate_offline_proof(output, document)
    original = output / 'materials/original-trigger'
    manifest_raw, tasks_raw = (original / 'manifest.json').read_bytes(), (original / 'tasks.json').read_bytes()
    if shared.sha(manifest_raw) != ORIGINAL_MANIFEST_SHA256 or shared.sha(tasks_raw) != ORIGINAL_TASK_SHA256:
        raise ValueError('original frozen trigger source changed')
    original_tasks = json.loads(tasks_raw)
    selected = {**original_tasks, 'cases': [next(c for c in original_tasks['cases'] if c['id'] == name) for name in CASES]}
    if document != selected:
        raise ValueError('public task or hidden oracle differs from the original selected object')
    source = shared.read_json(output / 'materials/source-manifest.json')
    for key, name in (('taskSha256', 'tasks.json'), ('offlineProofSha256', 'materials/offline-proof.json'),
                       ('referenceFixesSha256', 'materials/reference-fixes.json')):
        if source.get(key) != shared.sha((output / name).read_bytes()):
            raise ValueError('source proof hash changed: ' + key)
    for name, key in (('offline-proof.json', 'case'), ('reference-fixes.json', 'id'), ('source-manifest.json', 'id')):
        old = shared.read_json(original / name)
        rows = old if isinstance(old, list) else old['cases']
        expected = [next(r for r in rows if r[key] == c) for c in CASES]
        now = shared.read_json(output / 'materials' / name)
        if (now if isinstance(now, list) else now['cases']) != expected:
            raise ValueError('selected source proof differs: ' + name)
        original_manifest = json.loads(manifest_raw)
        if shared.sha((original / name).read_bytes()) != original_manifest['registrationFiles']['materials/' + name]:
            raise ValueError('original source proof is not frozen: ' + name)


def prepare(output, source=SOURCE):
    if output.exists() and any(output.iterdir()):
        raise ValueError('prepare requires a new empty directory')
    original_manifest = (source / 'manifest.json').read_bytes()
    original_tasks = (source / 'tasks.json').read_bytes()
    if shared.sha(original_manifest) != ORIGINAL_MANIFEST_SHA256 or shared.sha(original_tasks) != ORIGINAL_TASK_SHA256:
        raise ValueError('historical source identity mismatch')
    document = json.loads(original_tasks)
    document['cases'] = [next(c for c in document['cases'] if c['id'] == name) for name in CASES]
    validate_tasks(document)
    shared.write_json(output / 'tasks.json', document)
    originals = output / 'materials/original-trigger'
    originals.mkdir(parents=True)
    for name, origin in [('manifest.json', source / 'manifest.json'), ('tasks.json', source / 'tasks.json'),
        *[(n, source / 'materials' / n) for n in ('source-manifest.json', 'offline-proof.json', 'reference-fixes.json')]]:
        (originals / name).write_bytes(origin.read_bytes())
    for name, key in (('offline-proof.json', 'case'), ('reference-fixes.json', 'id'), ('source-manifest.json', 'id')):
        value = shared.read_json(originals / name)
        rows = value if isinstance(value, list) else value['cases']
        selected = [next(r for r in rows if r[key] == c) for c in CASES]
        shared.write_json(output / 'materials' / name, selected if isinstance(value, list) else {**value, 'cases': selected})
    source_manifest = shared.read_json(output / 'materials/source-manifest.json')
    source_manifest.update(taskSha256=shared.sha((output / 'tasks.json').read_bytes()),
        offlineProofSha256=shared.sha((output / 'materials/offline-proof.json').read_bytes()),
        referenceFixesSha256=shared.sha((output / 'materials/reference-fixes.json').read_bytes()))
    shared.write_json(output / 'materials/source-manifest.json', source_manifest)
    (output / 'analysis-plan.md').write_text(ANALYSIS_PLAN)
    validate_offline_proof(output, document)


class NativeObservation:
    """Evaluator-only transparent observation; no product event is introduced."""
    def __init__(self, path):
        self.path, self.lock, self.sequence = path, threading.Lock(), 0

    def append(self, kind, payload):
        with self.lock:
            self.sequence += 1
            record = {'sequence': self.sequence, 'kind': kind, 'epoch': time.time(),
                'monotonic': time.monotonic(), 'payload': payload}
            with self.path.open('ab') as stream:
                stream.write(canonical(record) + b'\n')
                stream.flush()
                os.fsync(stream.fileno())

    def install(self, stack):
        from minicode import completion_review_limits as limits
        from minicode.run_lifecycle import RunObservation
        original_create, original_select, original_emit = limits.create_review_limits, limits.select_review_phase, RunObservation.emit
        recorder = self

        def create(host, **kwargs):
            result = original_create(host, **kwargs)
            if result is not None:
                recorder.append('chain_created', {'agentDepth': host._agent_depth, 'deadline': result['deadline'],
                    'ancestorDeadline': result['ancestorDeadline'], 'closingSeconds': result['closingSeconds'],
                    'closingTokens': kwargs.get('closing_tokens', 0), 'parentBudget': asdict(result['parent'].snapshot())})
            return result

        def select(host, phase):
            try:
                return original_select(host, phase)
            finally:
                state = getattr(host, '_completion_review_limits', None)
                if state is not None:
                    stage = state['stages'].get(phase)
                    recorder.append('phase_selected', {'agentDepth': host._agent_depth, 'phase': phase, 'deadline': host._task_deadline_monotonic,
                        'chainDeadline': state['deadline'], 'stageCalls': stage['calls'] if stage else None,
                        'admittedCalls': stage['budget'].admitted_model_calls if stage else None})

        def emit(owner, event_type, *, step=None, payload=None):
            if event_type.startswith('implementation.completion_') or event_type == 'task.outcome':
                recorder.append('host_event', {'eventType': event_type, 'step': step,
                    'runId': owner.run_id, 'actor': trigger.transport._CURRENT_ACTOR.get(), 'data': payload or {}})
            return original_emit(owner, event_type, step=step, payload=payload)

        stack.enter_context(patch.object(limits, 'create_review_limits', create))
        stack.enter_context(patch.object(limits, 'select_review_phase', select))
        stack.enter_context(patch.object(RunObservation, 'emit', emit))


def runner(output, *, commit=None):
    module = _foundation()._original_runner(output, commit=commit)
    module.EvaluationTransport = trigger.ParentRequestTransport
    module.CollaborationEvidence = trigger.TriggerCollaborationEvidence
    module.collaboration_metrics = trigger.collaboration_metrics
    original = module.execute_trial
    def execute(trial_output, trial_id):
        root = Path(trial_output) / 'private' / trial_id
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'MINI_CODE_COMPLETION_REVIEW_MODE': 'bounded'}))
            observer = NativeObservation(root / 'native-review-observation.jsonl')
            observer.install(stack)
            observer.append('observer_started', {'mode': 'bounded', 'trialId': trial_id})
            try:
                return original(trial_output, trial_id)
            finally:
                observer.append('observer_finished', {'mode': 'bounded', 'trialId': trial_id})
    module.execute_trial = execute
    return module


def host_files():
    raise ValueError('host bytes require an explicit source commit and baseline archive; working tree snapshots are forbidden')


def assemble_host(source_commit, overlay_manifest, baseline_archive):
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('source host commit must be a full SHA')
    overlays = shared.read_json(overlay_manifest)['paths']
    expected = set(PRODUCTION_OVERLAYS) | {'scripts/native_review_bounded_validation.py', 'scripts/audit_native_review_bounded.py'}
    if not isinstance(overlays, list) or len(overlays) != len(set(overlays)) or set(overlays) != expected:
        raise ValueError('overlay manifest must name exactly the 11 production files and two new harness files')
    baseline = baseline_archive.read_bytes()
    if shared.sha(baseline) != BASELINE_ARCHIVE_SHA256:
        raise ValueError('baseline archive changed')
    with zipfile.ZipFile(baseline_archive) as archive:
        files = {safe_relative(n): archive.read(n) for n in archive.namelist()}
    origins = {n: {'origin': 'baseline_archive', 'sha256': shared.sha(data)} for n, data in files.items()}
    for name in overlays:
        safe_relative(name)
        data = subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{source_commit}:{name}'])
        files[name] = data
        origins[name] = {'origin': 'commit', 'sha256': shared.sha(data), 'sourceHostCommit': source_commit}
    if any('scripts/' + n not in files for n in HARNESS_FILES):
        raise ValueError('host lacks a declared harness dependency')
    return files, {'schemaVersion': 1, 'sourceHostCommit': source_commit,
        'baselineArchiveSha256': BASELINE_ARCHIVE_SHA256, 'overlayPaths': overlays,
        'files': {name: origins[name] for name in sorted(origins)}}


def verify_tls_trust():
    if shared.sha(Path(TLS_TRUST['path']).read_bytes()) != TLS_TRUST['sha256']:
        raise ValueError('registered TLS trust bytes changed')


def probe_host(root, output):
    verify_tls_trust()
    value = subprocess.run([sys.executable, '-I', '-B', str(root / 'scripts/native_review_bounded_validation.py'),
        '_probe', '--output', str(output)], cwd=root, capture_output=True, text=True, check=True, timeout=60,
        env={**os.environ, 'SSL_CERT_FILE': TLS_TRUST['path']})
    return json.loads(value.stdout)


def freeze(output, source_commit, overlay_manifest, baseline_archive=SOURCE / 'execution-host.zip'):
    if any((output / n).exists() for n in ('manifest.json', 'private', 'execution-host.zip')):
        raise ValueError('freeze refuses existing execution/freeze evidence')
    verify_tls_trust()
    files, origins = assemble_host(source_commit, overlay_manifest, baseline_archive)
    shared.write_json(output / 'materials/host-source.json', origins)
    (output / 'materials/original-execution-host.zip').write_bytes(baseline_archive.read_bytes())
    module = _foundation()
    module.host_files = lambda: files
    module.static_contract = lambda: {**static_contract(), 'sourceHostCommit': source_commit,
        'hostSourceSha256': shared.sha((output / 'materials/host-source.json').read_bytes())}
    return module.freeze(output)


def validate(output, *, host_root=None):
    manifest = _foundation()._original_validate(output, host_root=host_root)
    origins = shared.read_json(output / 'materials/host-source.json')
    if (manifest.get('sourceHostCommit') != origins['sourceHostCommit']
        or manifest.get('hostSourceSha256') != shared.sha((output / 'materials/host-source.json').read_bytes())):
        raise ValueError('host provenance binding changed')
    return manifest


def dispatch(output, mode, commit, *, trial=None, execution_host=None):
    verify_tls_trust()
    os.environ['SSL_CERT_FILE'] = TLS_TRUST['path']
    module = _foundation()
    commit = module.require_committed_registration(output, commit)
    manifest = validate(output)
    following = foundation.next_trial(output, manifest)
    if mode == 'child' and (not trial or trial != following):
        raise ValueError('child must execute exactly the next frozen cell')
    if following and any((output / 'private' / following / n).exists() for n in
                         ('execution-host.json', 'transport.sqlite', 'execution-start.json')):
        raise ValueError('durable evidence exists: never rerun')
    host = (output / 'private/execution-host').resolve()
    if execution_host is None:
        unpack_host(output, host)
        argv = [sys.executable, '-I', '-B', str(host / 'scripts/native_review_bounded_validation.py'), mode,
            '--output', str(output), '--preregistered-commit', commit, '--execution-host', str(host)]
        if trial:
            argv += ['--trial', trial]
        env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'}}
        env.update(PYTHONDONTWRITEBYTECODE='1', MINI_CODE_COMPLETION_REVIEW_MODE='bounded')
        os.execve(sys.executable, argv, env)
    if execution_host.resolve() != host or ROOT.resolve() != host:
        raise ValueError('execution entry point is outside the frozen host')
    validate(output, host_root=host)
    engine = runner(output, commit=commit)
    runtime = engine.frozen_runtime()
    for key, actual in [('productionSha256', engine.production_sha()), ('harnessSha256', engine.harness_sha()),
        ('routeSha256', engine.route_hash(runtime)), ('resolvedTransportRouteSha256', engine.resolved_transport_route(runtime))]:
        if manifest[key] != actual:
            raise ValueError('execution host or route drifted: ' + key)
    if mode == 'child':
        assert_host_imports(host)
        module.execution_host_receipt(output, trial, commit, manifest)
        engine.execute_trial(output, trial)
    else:
        engine.run_suite(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'freeze', 'run', 'child', '_probe'))
    parser.add_argument('--output', type=Path, default=DEFAULT)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--source-host-commit')
    parser.add_argument('--overlay-manifest', type=Path)
    parser.add_argument('--baseline-archive', type=Path, default=SOURCE / 'execution-host.zip')
    parser.add_argument('--trial')
    parser.add_argument('--preregistered-commit')
    parser.add_argument('--execution-host', type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.mode == 'prepare':
        prepare(output, args.source.resolve())
    elif args.mode == 'freeze':
        if not args.source_host_commit or not args.overlay_manifest:
            parser.error('freeze requires --source-host-commit and --overlay-manifest')
        freeze(output, args.source_host_commit, args.overlay_manifest, args.baseline_archive)
    elif args.mode == '_probe':
        engine = runner(output)
        runtime = engine.frozen_runtime()
        assert_host_imports(ROOT)
        print(json.dumps({'model': runtime['model'], 'routeSha256': engine.route_hash(runtime),
            'resolvedTransportRouteSha256': engine.resolved_transport_route(runtime),
            'productionSha256': engine.production_sha(), 'harnessSha256': engine.harness_sha(),
            'environment': environment_fingerprint()}))
    else:
        dispatch(output, args.mode, args.preregistered_commit, trial=args.trial, execution_host=args.execution_host)


if __name__ == '__main__':
    main()
