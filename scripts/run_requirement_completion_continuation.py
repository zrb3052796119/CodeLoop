#!/usr/bin/env python3
"""Explicit cross-process continuation of one interrupted implementation session.

No baseline hydration and no operator repair prompt. Preparation is local and
retains actual parent messages, current candidate, original scope and review
history. The predecessor remains immutable. Paid execution is a separate mode.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts import run_requirement_completion_diagnostic as diagnostic  # noqa: E402
from scripts import run_continuous_agent_diagnostic as continuous  # noqa: E402
from scripts import run_collaboration_demand_pilot as demand  # noqa: E402
from scripts import run_implementation_progress_live as progress  # noqa: E402
from scripts import run_three_arm_real as shared  # noqa: E402
from evaluation_transport import TransportConfig  # noqa: E402

ENTRY = Path(__file__).resolve()
PROTOCOL = 'requirement-completion-cross-process-continuation-v1'
DEFAULT_PREDECESSOR = ROOT / 'artifacts/requirement-completion-2026-10-18/memory-explicit-v3'
DEFAULT_OUTPUT = ROOT / 'artifacts/requirement-completion-2026-10-18/memory-explicit-v3-continuation-v1'
TRIAL_ID = diagnostic.schedule()[0]['id']
LIMITS = dict(continuous.LIMITS)
HARNESS_FILES = (*diagnostic.HARNESS_FILES, ENTRY.name)
ANALYSIS_PLAN = '''# Cross-process continuation diagnostic

This is a new process segment of the same logical implementation session, not
an uninterrupted Python loop, a fresh-baseline task, or an independent trial.
The predecessor remains operator-aborted and immutable. Resume the existing
candidate directory and byte-identical current parent context, including its
compaction boundary and actual tool results. Restore the host owner and attempt
counter from durable journal events, original ImplementationSpec from frozen
public task fields, and validated review source/receipt/history. An interrupted
review is not an accepted decision. Prior approved caches are not transferable.
Current public checks and the repaired production completion mechanism decide
whether the same Executor must continue. No operator repair prompt is appended.

The original public task, common requirement, explicit collaboration recipe,
GLM-5.3-Flash route/high reasoning/temperature 0, no cumulative token/call/cost/
execution-wall cap, output 131072, idle120/connect30 are retained. Predecessor
and continuation physical ledgers are both reported; unknown usage stays null
with known lower bounds. The reset Python budget is a segment counter only.
Any same-model legacy review migration calls belong to this segment's ledger.

The prior hidden oracle, its output and final judgement are never recovery
inputs. Its unchanged opaque bytes may only reach the common terminal judge
once this segment terminates. All final results remain separately labelled;
no original result or denominator is rewritten. Current candidate preservation
is post-terminal/post-oracle, never claimed as an earlier execution snapshot.
'''


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def private_json(path: Path, value: object) -> None:
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n')


def safe_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('unsafe checkpoint reference')
    path = root / rel
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('checkpoint reference escapes root')
    return path


def implementation_spec(case: dict):
    from minicode.deterministic_verifier import VerificationCheck
    from minicode.implementation_execution import ImplementationSpec
    return ImplementationSpec(write_paths=tuple(case['authorized_paths']), objective=case['prompt'], checks=(
        VerificationCheck('public_existing', 'tests', tuple(case['verification_argv']), 120),
        VerificationCheck('public_regression', 'tests', ('python3', '-m', 'pytest', '-q', 'tests/test_eval_regression.py'), 120),
    ))


def completion_identity(root: Path) -> dict:
    events = []
    for path in sorted((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson')):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get('type') not in {'implementation.completion_review_started', 'implementation.completion_decided'}:
                continue
            payload = event.get('payload', {})
            owner, attempt = payload.get('executorId'), payload.get('attempt')
            digest = payload.get('patchDigest')
            if (not isinstance(owner, str) or not re.fullmatch(r'implementation_[0-9a-f]{32}', owner)
                    or type(attempt) is not int or attempt < 1
                    or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)):
                raise ValueError('invalid durable completion identity')
            events.append({key: event[key] for key in ('runId', 'sequence', 'type')}
                          | {key: payload.get(key) for key in ('executorId', 'attempt', 'patchDigest', 'status')})
    if not events or len({(x['runId'], x['executorId']) for x in events}) != 1:
        raise ValueError('continuation requires one unambiguous implementation owner')
    events.sort(key=lambda x: x['sequence'])
    starts = [x for x in events if x['type'].endswith('review_started')]
    if not starts or [x['attempt'] for x in starts] != list(range(1, len(starts) + 1)):
        raise ValueError('noncontiguous durable review attempts')
    if any(x.get('status') == 'approved' for x in events):
        raise ValueError('approved delivery is not an interrupted continuation')
    last = starts[-1]
    return {'executorId': last['executorId'], 'attempt': last['attempt'],
            'candidateDigest': last['patchDigest'], 'predecessorRunId': last['runId'],
            'lastAttemptInterrupted': not any(x['type'].endswith('completion_decided') and x['attempt'] == last['attempt'] for x in events),
            'completionEvents': events}


def verify_archive(root: Path) -> Path:
    archive = json.loads((root / 'completion-review-archive.json').read_bytes())
    if archive.get('archiveComplete') is not True or archive.get('errors') or not archive.get('files'):
        raise ValueError('incomplete review archive')
    directories, paths = set(), set()
    for row in archive['files']:
        path = safe_path(root, row['file'])
        if not path.is_file() or sha_file(path) != row['sha256'] or path.stat().st_size != row['bytes']:
            raise ValueError('review archive hash mismatch')
        if path in paths:
            raise ValueError('duplicate review archive reference')
        paths.add(path)
        directories.add(path.parent)
    if len(directories) != 1:
        raise ValueError('ambiguous review evidence lineage')
    directory = directories.pop()
    if set(directory.iterdir()) != paths:
        raise ValueError('unlisted review archive evidence')
    return directory


def candidate_manifest(workspace: Path, authorized: list[str]) -> list[dict]:
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError('existing candidate workspace is unavailable')
    result = []
    for name in authorized:
        path = safe_path(workspace, name)
        if not path.exists():
            result.append({'path': name, 'exists': False})
        elif not path.is_file():
            raise ValueError('nonregular authorized candidate')
        else:
            result.append({'path': name, 'exists': True, 'sha256': sha_file(path), 'bytes': path.stat().st_size})
    return result


def predecessor_inputs(predecessor: Path) -> dict:
    root = predecessor / 'private' / TRIAL_ID
    manifest = json.loads((predecessor / 'manifest.json').read_bytes())
    if manifest['schedule'] != diagnostic.schedule() or manifest['limits'] != LIMITS:
        raise ValueError('different task schedule or budget')
    execution = json.loads((root / 'execution.json').read_bytes())
    if execution.get('errorClass') not in {'KeyboardInterrupt', 'SystemExit'}:
        raise ValueError('predecessor must be explicitly interrupted')
    if json.loads((root / 'process-cleanup.json').read_bytes()).get('cleanupComplete') is not True:
        raise ValueError('predecessor cleanup incomplete')
    evidence = json.loads((root / 'execution-evidence.json').read_bytes())
    transcript = root / 'interrupted-transcript.json'
    if (evidence.get('source') != 'parent_runtime_context_manager' or evidence.get('available') is not True
            or evidence.get('errors') or sha_file(transcript) != evidence.get('sha256')):
        raise ValueError('actual parent context evidence is unavailable or changed')
    messages = json.loads(transcript.read_bytes())
    if not isinstance(messages, list) or len(messages) != evidence.get('messageCount'):
        raise ValueError('parent context count mismatch')
    case = next(x for x in json.loads((predecessor / 'tasks.json').read_bytes())['cases'] if x['id'] == diagnostic.CASE)
    if sha_file(predecessor / 'tasks.json') != manifest['taskSha256']:
        raise ValueError('frozen task bytes changed')
    if sha_file(predecessor / 'frozen-code.zip') != manifest['frozenCodeArchiveSha256']:
        raise ValueError('predecessor frozen source archive changed')
    workspace = Path(json.loads((root / 'workspace.json').read_bytes())['path'])
    review_directory = verify_archive(root)
    return {'root': root, 'manifest': manifest, 'case': case, 'messages': messages,
            'workspace': workspace, 'reviewDirectory': review_directory, 'identity': completion_identity(root),
            'candidateFiles': candidate_manifest(workspace, case['authorized_paths'])}


def prepare(output: Path, predecessor: Path = DEFAULT_PREDECESSOR) -> None:
    output, predecessor = output.resolve(), predecessor.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('continuation preparation requires a new empty directory')
    inputs = predecessor_inputs(predecessor)
    root = inputs['root']
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    for name in ('tasks.json', 'public-cases.json', 'source-provenance.json', 'public-preflight/receipt.json'):
        private_write(output / name, (predecessor / name).read_bytes())
    private_write(output / 'analysis-plan.md', ANALYSIS_PLAN.encode())
    private_write(output / 'private/parent-context.json', (root / 'interrupted-transcript.json').read_bytes())
    references = {str(p.relative_to(predecessor)): sha_file(p) for p in (
        predecessor / 'manifest.json', predecessor / 'tasks.json', predecessor / 'frozen-code.zip',
        root / 'execution-evidence.json', root / 'interrupted-transcript.json', root / 'execution.json',
        root / 'workspace.json', root / 'baseline-files.json', root / 'process-cleanup.json',
        root / 'transport.sqlite', root / 'transport-config.json', root / 'completion-review-archive.json')}
    for path in (root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'):
        references[str(path.relative_to(predecessor))] = sha_file(path)
    checkpoint = {'version': 1, 'kind': 'cross-process-logical-implementation-continuation',
        'createdAt': datetime.now(timezone.utc).isoformat(), 'predecessorDirectory': str(predecessor),
        'predecessorArtifactSha256': references, 'workspace': str(inputs['workspace']),
        'identity': inputs['identity'], 'parentContextSha256': sha_file(output / 'private/parent-context.json'),
        'parentMessageCount': len(inputs['messages']), 'candidateFiles': inputs['candidateFiles'],
        'reviewArchiveDirectory': str(inputs['reviewDirectory']),
        'spec': asdict(implementation_spec(inputs['case'])),
        'originalModelPolicy': {key: inputs['manifest'].get(key) for key in ('model', 'temperature', 'reasoningEffortPolicy',
            'routeSha256', 'resolvedTransportRouteSha256', 'requestPolicy', 'limits', 'roleTurnLimits',
            'commonRequirementSha256', 'explicitRecipeSha256')},
        'unrestoredState': ['original Python stack', 'in-flight reviewer conversation', 'in-memory compactor controller',
                           'approved review cache', 'public-check cache'],
        'privateJudgeUsedForRecovery': False, 'freshBaselineHydration': False}
    private_json(output / 'checkpoint.json', checkpoint)
    private_json(output / 'preparation.json', {'protocol': PROTOCOL, 'modelRequests': 0,
        'checkpointSha256': sha_file(output / 'checkpoint.json'), 'parentContextSha256': checkpoint['parentContextSha256'],
        'taskSha256': sha_file(output / 'tasks.json'), 'existingCandidate': True, 'freshBaselineHydration': False})


def load_checkpoint(output: Path, *, verify_candidate: bool = True) -> tuple[dict, dict, list[dict]]:
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    if checkpoint.get('version') != 1 or checkpoint.get('kind') != 'cross-process-logical-implementation-continuation':
        raise ValueError('unsupported continuation checkpoint')
    predecessor = Path(checkpoint['predecessorDirectory'])
    for relative, expected in checkpoint['predecessorArtifactSha256'].items():
        if sha_file(safe_path(predecessor, relative)) != expected:
            raise ValueError('immutable predecessor evidence changed')
    inputs = predecessor_inputs(predecessor)
    if checkpoint['identity'] != inputs['identity'] or checkpoint['workspace'] != str(inputs['workspace']):
        raise ValueError('continuation owner or workspace changed')
    if asdict(implementation_spec(inputs['case'])) != checkpoint['spec']:
        # JSON changes tuple containers to arrays; compare canonical JSON.
        if canonical(asdict(implementation_spec(inputs['case']))) != canonical(checkpoint['spec']):
            raise ValueError('public ImplementationSpec changed')
    if sha_file(output / 'private/parent-context.json') != checkpoint['parentContextSha256']:
        raise ValueError('saved parent context changed')
    if (output / 'private/parent-context.json').read_bytes() != (inputs['root'] / 'interrupted-transcript.json').read_bytes():
        raise ValueError('recovery context is not the actual parent snapshot')
    if sha_file(output / 'tasks.json') != inputs['manifest']['taskSha256']:
        raise ValueError('continuation task differs from frozen predecessor')
    if verify_candidate and inputs['candidateFiles'] != checkpoint['candidateFiles']:
        raise ValueError('candidate changed after continuation preparation')
    return checkpoint, inputs, inputs['messages']


def production_sha() -> str:
    return shared.production_sha()


def harness_sha() -> str:
    return hashlib.sha256(b''.join(name.encode() + b'\0' + (ROOT / 'scripts' / name).read_bytes() for name in HARNESS_FILES)).hexdigest()


def continuation_capabilities() -> dict:
    from minicode.agent_runtime import AgentTurnRuntime, create_agent_turn_runtime
    from minicode.agent_loop import run_agent_turn
    from minicode.implementation_continuation import HostImplementationContinuation
    from minicode.implementation_completion_review import ImplementationCompletionReview
    if (not callable(getattr(HostImplementationContinuation, 'from_document', None))
            or not callable(getattr(ImplementationCompletionReview, 'from_archive', None))
            or 'implementation_continuation' not in inspect.signature(create_agent_turn_runtime).parameters
            or 'implementation_continuation' not in inspect.signature(run_agent_turn).parameters
            or 'implementation_continuation=self.implementation_continuation' not in inspect.getsource(AgentTurnRuntime.execute)):
        raise RuntimeError('production continuation integration is unavailable')
    paths = ('minicode/implementation_continuation.py', 'minicode/agent_runtime.py',
             'minicode/agent_loop.py', 'minicode/implementation_completion_review.py')
    return {'version': 1, 'sourceSha256': {name: sha_file(ROOT / name) for name in paths},
            'entrypointSha256': sha_file(ENTRY), 'restoration': 'host capability; cross-process logical owner'}


def freeze(output: Path) -> None:
    checkpoint, inputs, messages = load_checkpoint(output)
    from minicode.implementation_completion_review import ImplementationCompletionReview
    restore = getattr(ImplementationCompletionReview, 'from_archive', None)
    if not callable(restore):
        raise RuntimeError('production review restoration is not available')
    capabilities = continuation_capabilities()
    restored = restore(implementation_spec(inputs['case']), inputs['reviewDirectory'])
    private_json(output / 'private/review-state.json', restored.state)
    restored_continuation(output, implementation_spec(inputs['case']), messages)
    manifest = {**inputs['manifest'], 'protocolVersion': PROTOCOL, 'comparison': 'cross-process continuation; not an independent trial',
        'continuationCheckpointSha256': sha_file(output / 'checkpoint.json'),
        'continuationReviewStateSha256': sha_file(output / 'private/review-state.json'),
        'predecessorDirectory': checkpoint['predecessorDirectory'], 'runner': 'scripts/' + ENTRY.name,
        'harnessFiles': list(HARNESS_FILES), 'productionSha256': production_sha(), 'harnessSha256': harness_sha(),
        'analysisPlanSha256': sha_file(output / 'analysis-plan.md'), 'createdAt': datetime.now(timezone.utc).isoformat(),
        'crossProcessContinuation': True, 'freshBaselineHydration': False, 'privateJudgeUsedForRecovery': False,
        'continuationMechanism': capabilities, 'completionMechanism': diagnostic.completion_mechanism_contract()}
    archive = output / 'frozen-code.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / 'minicode').rglob('*.py')):
            bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in HARNESS_FILES:
            bundle.write(ROOT / 'scripts' / name, 'scripts/' + name)
    archive.chmod(0o600)
    manifest['frozenCodeArchiveSha256'] = sha_file(archive)
    private_json(output / 'manifest.json', manifest)


def validate(output: Path, *, verify_candidate: bool = True) -> dict:
    checkpoint, inputs, _ = load_checkpoint(output, verify_candidate=verify_candidate)
    manifest = json.loads((output / 'manifest.json').read_bytes())
    expected = {'protocolVersion': PROTOCOL, 'productionSha256': production_sha(), 'harnessSha256': harness_sha(),
        'continuationCheckpointSha256': sha_file(output / 'checkpoint.json'),
        'continuationReviewStateSha256': sha_file(output / 'private/review-state.json'),
        'taskSha256': sha_file(output / 'tasks.json'), 'analysisPlanSha256': sha_file(output / 'analysis-plan.md'),
        'frozenCodeArchiveSha256': sha_file(output / 'frozen-code.zip'),
        'continuationMechanism': continuation_capabilities()}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('frozen continuation contract changed')
    if manifest['limits'] != LIMITS or any(manifest[key] != inputs['manifest'][key] for key in checkpoint['originalModelPolicy']):
        raise ValueError('continuation model, scope, recipe or budget drift')
    return manifest


def services(output: Path):
    """Private module instances expose the unchanged scope, transport and judge."""
    module = diagnostic._library().runner(output)
    module.HARNESS_FILES = HARNESS_FILES
    return module


def restored_continuation(output: Path, spec, messages: list[dict]):
    from minicode.implementation_continuation import (
        HostImplementationContinuation, implementation_spec_digest, continuation_digest,
    )
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    state = json.loads((output / 'private/review-state.json').read_bytes())
    identity = checkpoint['identity']
    feedback = next((m for m in reversed(messages) if m.get('_implementation_completion_feedback')), None)
    return HostImplementationContinuation.from_document({
        'continuationVersion': 1, 'executorId': identity['executorId'], 'attempt': identity['attempt'],
        'candidateDigest': identity['candidateDigest'], 'specDigest': implementation_spec_digest(spec),
        'messagesDigest': continuation_digest(messages), 'reviewState': state, 'completionFeedback': feedback,
    }, spec=spec, messages=messages)


def checkpoint_writer(root: Path):
    """Keep the production finally checkpoint private and immutable."""
    def save(document: dict, messages: list[dict]) -> None:
        private_json(root / 'implementation-checkpoint.json', document)
        private_json(root / 'implementation-checkpoint-messages.json', messages)
        private_json(root / 'implementation-checkpoint-receipt.json', {
            'version': 1, 'available': True, 'source': 'production_parent_finally',
            'documentSha256': sha_file(root / 'implementation-checkpoint.json'),
            'messagesSha256': sha_file(root / 'implementation-checkpoint-messages.json'),
            'executorId': document['executorId'], 'attempt': document['attempt'],
            'candidateDigest': document['candidateDigest'], 'messageCount': len(messages),
        })
    return save


def execute_segment(output: Path) -> None:
    from minicode.agent_runtime import (
        create_agent_turn_runtime,
        final_response_text,
    )
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.memory import MemoryManager
    from minicode import tools as tool_module
    from minicode.permissions import PermissionMode
    from minicode.auto_mode import AutoModeChecker
    import run_north_star_live as north
    from run_multi_agent_cost_live import _BoundedWorkflowApprovalProvider

    manifest = validate(output)
    checkpoint, inputs, saved_messages = load_checkpoint(output)
    trial_id = TRIAL_ID
    service = services(output)
    read_json, write_json, sha = shared.read_json, shared.write_json, shared.sha
    workspace_files, route_hash = shared.workspace_files, shared.route_hash
    workflow_scope, independent_registry = shared.workflow_scope, shared.independent_registry
    guard_registry, install_verification_guards = shared.guard_registry, shared.install_verification_guards
    def coordinator_registry(registry, arm):
        return registry

    VerificationScope, ProcessEvidence = shared.VerificationScope, shared.ProcessEvidence
    EvaluationTransport, CollaborationEvidence = demand.UniformReasoningTransport, demand.DemandCollaborationEvidence
    spec = implementation_spec(inputs['case'])
    host_continuation = restored_continuation(output, spec, saved_messages)
    public_inputs = progress.bind_public_fixtures(read_json(output / 'tasks.json'), diagnostic.CASE)
    fixture_sink = progress.fixture_receipt_writer(output / 'private' / trial_id / 'public-fixture-receipts.jsonl')
    trial = next(x for x in manifest["schedule"] if x["id"] == trial_id)
    case = next(
        x for x in read_json(output / "tasks.json")["cases"] if x["id"] == trial["case"]
    )
    runtime_config = service.frozen_runtime()
    if (route_hash(runtime_config) != manifest['routeSha256']
            or production_sha() != manifest['productionSha256']
            or harness_sha() != manifest['harnessSha256']
            or manifest['limits'] != LIMITS or manifest['price'] != service.PRICE
            or sha((output / 'tasks.json').read_bytes()) != manifest['taskSha256']):
        raise ValueError('frozen continuation runtime changed')
    root = output / "private" / trial_id
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    if (root / "transport.sqlite").exists():
        raise RuntimeError(
            "trial_has_prior_call_evidence; preserve it instead of rerunning"
        )
    workspace = inputs['workspace']
    # Reuse current candidate in place. Never copy any task baseline file.
    private_write(root / 'workspace.json', canonical({'path': str(workspace)}))
    private_write(root / 'baseline-files.json', (inputs['root'] / 'baseline-files.json').read_bytes())
    private_write(root / 'segment-start-files.json', canonical(workspace_files(workspace)))
    journal = RunJournal(workspace, data_dir=root / "journal")
    start = time.monotonic()
    execution_epoch = time.time()
    transport_config = TransportConfig(
        ledger_path=str((root / "transport.sqlite").resolve()),
        expected_model=runtime_config["model"],
        expected_route_sha256=manifest["resolvedTransportRouteSha256"],
        deadline_epoch=(execution_epoch + LIMITS["wallSeconds"]
                        if LIMITS["wallSeconds"] is not None else None),
        max_model_calls=LIMITS["maxModelCalls"],
        max_total_tokens=LIMITS["maxTokens"],
        max_output_tokens=LIMITS["maxOutputTokens"],
        connect_timeout_seconds=LIMITS["connectTimeoutSeconds"],
        idle_timeout_seconds=LIMITS["idleTimeoutSeconds"],
        request_timeout_seconds=LIMITS["requestTimeoutSeconds"],
    )
    write_json(root / "transport-config.json", asdict(transport_config))
    write_json(root / "execution-start.json", {"epoch": execution_epoch, "deadlineEpoch": transport_config.deadline_epoch})
    transport = EvaluationTransport(transport_config)
    collaboration = CollaborationEvidence(root / "collaboration.json")
    original_factory = tool_module.create_default_tool_registry
    error_class = None
    execution_error = None
    response = ""
    runtime = None
    budget = None
    subagents = []
    approval = _BoundedWorkflowApprovalProvider(tuple(case["authorized_paths"]))
    owned_registries = []
    verification_scope = VerificationScope(case["verification_argv"])

    def registry(*args, **kwargs):
        kwargs["include_mcp"] = False
        kwargs["include_user_interaction"] = False
        registry_ = original_factory(*args, **kwargs)
        is_parent = not owned_registries
        owned_registries.append(registry_)
        selected = independent_registry(registry_, trial["arm"])
        if is_parent:
            selected = coordinator_registry(selected, trial["arm"])
        return guard_registry(selected, verification_scope)

    def memory(*args, **kwargs):
        kwargs["data_root"] = root / "memory"
        return MemoryManager(*args, **kwargs)

    prompt = (
        case["prompt"]
        + "\n\n宿主预授权的可修改文件仅有："
        + json.dumps(case["authorized_paths"], ensure_ascii=False)
        + "。其余路径只读。run_command 只允许下面的公开验证命令，以及 python3 -m pytest -q tests/test_eval_regression.py；不要使用 shell 拼接或其他外部命令。可通过 read_file/grep_files 等工具阅读源码。test_runner 映射到相同验证。工作完成后直接返回结果。"
    )
    if case.get("verification_argv"):
        prompt += "\n项目可见验证命令：" + shlex.join(case["verification_argv"])
    prompt = demand.prompt_for_arm(prompt, trial["arm"])
    messages = saved_messages
    with ExitStack() as stack:
        stack.enter_context(progress.bind_verifier_public_inputs(public_inputs, receipt_sink=fixture_sink))
        stack.enter_context(continuous.role_overrides(manifest))
        stack.enter_context(diagnostic.ParentExecutionEvidenceArchive(root))
        stack.enter_context(diagnostic.CompletionEvidenceArchive(root))
        stack.enter_context(ProcessEvidence(root / "processes.jsonl"))
        stack.enter_context(transport)
        stack.enter_context(collaboration)
        install_verification_guards(stack, verification_scope, workspace)
        # Keep module-level persistence paths local without changing HOME or
        # changing the credential route that was resolved before this point.
        state_root = root / "state"
        for target in (
            "minicode.config.MINI_CODE_DIR",
            "minicode.context_manager.MINI_CODE_DIR",
            "minicode.logging_config.MINI_CODE_DIR",
            "minicode.memory.MINI_CODE_DIR",
        ):
            stack.enter_context(patch(target, state_root))
        stack.enter_context(
            patch(
                "minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH",
                state_root / "cybernetic_supervisor.json",
            )
        )
        stack.enter_context(
            patch("minicode.logging_config.LOG_FILE", state_root / "minicode.log")
        )
        stack.enter_context(
            patch(
                "minicode.permissions.MINI_CODE_PERMISSIONS_PATH",
                state_root / "permissions.json",
            )
        )
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "MINI_CODE_TURN_BUDGET_TOKENS": str(LIMITS["maxTokens"] if LIMITS["maxTokens"] is not None else 0),
                    "MINI_CODE_TURN_BUDGET_MODEL_CALLS": str(LIMITS["maxModelCalls"] if LIMITS["maxModelCalls"] is not None else 0),
                    "MINI_CODE_TURN_BUDGET_COST_USD": str(LIMITS["maxCostUsd"] if LIMITS["maxCostUsd"] is not None else 0),
                    "MINICODE_TASK_TIMEOUT_SECONDS": str(LIMITS["wallSeconds"] if LIMITS["wallSeconds"] is not None else 0),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MINI_CODE_MODEL_MODE": "real",
                    **{f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS": str(limit) for role, limit in manifest["roleTurnLimits"].items()},
                },
            )
        )
        stack.enter_context(
            patch(
                "minicode.config.load_runtime_config",
                side_effect=lambda *a, **k: deepcopy(runtime_config),
            )
        )
        stack.enter_context(
            patch("minicode.tools.create_default_tool_registry", side_effect=registry)
        )
        stack.enter_context(patch("minicode.skills.discover_skills", return_value=[]))
        stack.enter_context(patch("minicode.tools.discover_skills", return_value=[]))
        stack.enter_context(
            patch(
                "minicode.subagent_profiles.load_agent_profiles", return_value=({}, [])
            )
        )
        stack.enter_context(
            patch("minicode.tools.task.load_agent_profiles", return_value=({}, []))
        )
        stack.enter_context(patch("minicode.prompt._maybe_read", return_value=""))
        stack.enter_context(
            patch("minicode.permissions._read_permission_store", return_value={})
        )
        stack.enter_context(patch("minicode.memory.MemoryManager", side_effect=memory))
        stack.enter_context(north._working_directory(workspace))
        try:
            with observe_run(
                workspace=workspace,
                source="headless",
                title=case["id"],
                journal_factory=lambda _: journal,
                enabled=True,
            ) as observation:
                runtime = create_agent_turn_runtime(
                    workspace=workspace,
                    prompt=prompt,
                    include_mcp=False,
                    allow_user_interaction=False,
                    event_sink=observation,
                    deadline_monotonic=transport.deadline,
                    implementation_spec=spec,
                    implementation_continuation=host_continuation,
                    implementation_checkpoint_sink=checkpoint_writer(root),
                )
                if north._runtime_profile_from_adapter(
                    runtime.model, runtime_config
                ) != north._runtime_profile_from_runtime(runtime_config):
                    raise RuntimeError("evaluation_model_route_drift")
                runtime.permissions.auto_checker = AutoModeChecker(
                    PermissionMode.DEFAULT
                )
                runtime.permissions.prompt = lambda request: (
                    north._isolated_write_approval(
                        workspace,
                        request,
                        tuple(Path(p) for p in case["authorized_paths"]),
                    )
                )
                if trial["arm"] == "single":
                    runtime.system_prompt = shared.single_prompt(runtime.system_prompt)
                task = runtime.tools.find("task")
                if task is not None:
                    original_task = task.run

                    def observed_task(call_input, context):
                        if not workflow_scope(call_input, case["authorized_paths"]):
                            from minicode.tooling import ToolResult

                            denied = ToolResult(
                                ok=False,
                                output="error[evaluation_write_scope_denied]: workflow write paths must be within the host-authorized file list.",
                            )
                            approval.capture_task_result(denied)
                            return denied
                        approval.capture_task_call(call_input)
                        if (
                            isinstance(call_input, dict)
                            and call_input.get("agent_type") == "workflow"
                            and not call_input.get("workflow_request")
                            and isinstance(call_input.get("write_paths"), list)
                        ):
                            approval._delegations[-1]["writePaths"] = call_input[
                                "write_paths"
                            ]
                            approval._delegations[-1]["taskId"] = "*"
                        result = original_task(call_input, context)
                        approval.capture_task_result(result)
                        return result

                    task.run = observed_task
                    original_approval = approval.consume_approval

                    def consume_approval(**kwargs):
                        if (
                            approval._delegations
                            and approval._delegations[-1]["taskId"] == "*"
                        ):
                            approval._delegations[-1]["taskId"] = kwargs["task_id"]
                        return original_approval(**kwargs)

                    approval.consume_approval = consume_approval
                    runtime.runtime["_workflowHumanApprovalProvider"] = approval
                write_json(
                    root / "runtime-contract.json",
                    {
                        "tools": runtime.tools.list_all(),
                        "systemPrompt": runtime.system_prompt,
                        "model": runtime_config["model"],
                        "routeSha256": route_hash(runtime_config),
                    },
                )
                # The exact saved parent state, including tool results and compaction
                # boundary, enters the new process. No new task or repair message.
                messages = saved_messages
                messages = runtime.execute(messages, observation)
                response = final_response_text(messages) or ""
                write_json(root / "transcript.json", messages)
                subagents = [
                    asdict(x) for x in journal.list_subagent_runs(observation.run_id)
                ]
        except BaseException as error:
            error_class = type(error).__name__
            execution_error = error
            # Locally retained stack locations aid harness diagnosis without secret exception text.
            import traceback

            write_json(
                root / "error.json",
                {
                    "class": error_class,
                    "frames": [
                        {"file": f.filename, "line": f.lineno, "name": f.name}
                        for f in traceback.extract_tb(error.__traceback__)
                    ],
                },
            )
        finally:
            if runtime is not None:
                budget = asdict(runtime.agent_budget.snapshot())
                runtime.dispose()
            for owner in owned_registries:
                owner.dispose()
    if execution_error is None:
        private_write(root / "response.txt", response.encode())
    write_json(root / "subagents.json", subagents)
    write_json(root / "execution.json", {
        "durationSeconds": time.monotonic() - start,
        "crossProcessContinuation": True,
        "executorId": checkpoint["identity"]["executorId"],
        "resumedAfterAttempt": checkpoint["identity"]["attempt"],
        "errorClass": error_class,
        "budget": budget,
        "taskErrorCodes": approval.task_error_codes,
        "approvalDecisions": approval.records,
    })

    if execution_error is not None:
        raise execution_error


def combined_metering(predecessor_roots: list[Path], current_root: Path) -> dict:
    """Read immutable physical ledgers; never initialize or mutate old databases."""
    records, segments, rejections = [], [], []
    for index, root in enumerate([*predecessor_roots, current_root]):
        path = root / 'transport.sqlite'
        if not path.is_file():
            raise ValueError('continuation segment is missing its physical ledger')
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT call,data,charge_tokens FROM requests ORDER BY call').fetchall()
            rejections.extend(json.loads(row[0]) for row in db.execute('SELECT data FROM admission_rejections ORDER BY rejection'))
        current = [{'call': call, **json.loads(data), 'chargedTokens': charge, 'continuationSegment': index}
                   for call, data, charge in rows]
        records.extend(current)
        execution = json.loads((root / 'execution.json').read_bytes())
        segments.append({'root': str(root), 'ledgerSha256': sha_file(path), 'modelCalls': len(current),
            'durationSeconds': execution['durationSeconds'], 'errorClass': execution.get('errorClass')})
    known = [r for r in records if r.get('usage', {}).get('source') == 'provider']
    cache_known = [r for r in known if r['usage'].get('cache_read_tokens') is not None]
    snapshot = {'requests': records, 'started': len(records),
        'completed': sum(r['event'] == 'completed' for r in records),
        'failed': sum(r['event'] == 'failed' for r in records),
        'inFlight': sum(r['event'] == 'started' for r in records),
        'providerUsageCalls': len(known), 'unknownUsageCalls': len(records) - len(known),
        'usageComplete': bool(records) and len(known) == len(records),
        'inputTokensKnown': sum(r['usage']['input_tokens'] for r in known),
        'outputTokensKnown': sum(r['usage']['output_tokens'] for r in known),
        'cacheReadTokensKnown': sum(r['usage']['cache_read_tokens'] for r in cache_known),
        'cacheUsageComplete': bool(records) and len(cache_known) == len(records),
        'chargedTokens': sum(r['chargedTokens'] for r in records),
        'admissionRejectedCount': len(rejections), 'admissionRejections': rejections,
        'admissionRejectedByReason': dict(Counter(row['reason'] for row in rejections))}
    return {'segments': segments, 'durationSeconds': sum(row['durationSeconds'] for row in segments),
            **demand.previous.transport_metrics(snapshot),
            'costMeaning': 'API-equivalent estimate, not actual subscription charge; unknown predecessor usage is retained'}


def run_suite(output: Path) -> dict:
    manifest = validate(output)
    checkpoint, inputs, _ = load_checkpoint(output)
    root = output / 'private' / TRIAL_ID
    if any((root / name).exists() for name in ('execution-start.json', 'transport.sqlite', 'execution.json', 'workspace.json')):
        raise ValueError('segment has prior execution evidence; never retry or overwrite')
    if (output / 'trials' / (TRIAL_ID + '.json')).exists():
        raise ValueError('terminal continuation cannot be rerun')
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    service = services(output)
    started = time.monotonic()
    with (root / 'process.log').open('x') as log:
        proc = subprocess.Popen([sys.executable, str(ENTRY), 'child', '--output', str(output)],
            stdout=log, stderr=log, cwd=ROOT, start_new_session=True)
        code = service.wait_trial_process(proc, root)
    stopped = time.time()
    if not (root / 'process-cleanup.json').exists():
        cleanup = service.stop_trial(proc.pid, root / 'processes.jsonl')
        cleanup['cleanupSeconds'] = time.time() - stopped
        shared.write_json(root / 'process-cleanup.json', cleanup)
    # Source/evidence contracts remain pinned; only candidate files may now differ.
    validate(output, verify_candidate=False)
    row = service.finalize_trial(output, manifest['schedule'][0], process_code=code,
        process_seconds=time.monotonic() - started, stopped_epoch=stopped)
    summary = diagnostic.completion_summary(output, TRIAL_ID)
    row.update(crossProcessContinuation=True, samePythonLoop=False,
        originalExecutorId=checkpoint['identity']['executorId'], resumedAfterAttempt=checkpoint['identity']['attempt'],
        predecessorDirectory=checkpoint['predecessorDirectory'], predecessorClassification='operator-aborted',
        completionMechanism=summary, cumulativeMetering=combined_metering([inputs['root']], root))
    # A passing external judgement cannot turn an interrupted/unapproved process
    # into a production-approved delivery.
    decisions = summary['decisions']
    current_approval = bool(decisions and summary['approvedFinalObserved']
        and decisions[-1]['executorId'] == checkpoint['identity']['executorId']
        and decisions[-1]['attempt'] > checkpoint['identity']['attempt'])
    row['sameOwnerApprovalAfterResume'] = current_approval
    row['success'] = bool(row.get('taskQualityPassed') and row.get('errorClass') is None and current_approval)
    row['classification'] = 'delivered' if row['success'] else 'operator-aborted' if row.get('errorClass') in {
        'KeyboardInterrupt', 'SystemExit'} else 'not-delivered'
    shared.write_json(output / 'trials' / (TRIAL_ID + '.json'), row)
    service.checkpoint_results(output, manifest)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'freeze', 'validate', 'run', 'child'))
    parser.add_argument('--predecessor', type=Path, default=DEFAULT_PREDECESSOR)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == 'prepare':
        prepare(output, args.predecessor)
    elif args.mode == 'freeze':
        freeze(output)
    elif args.mode == 'validate':
        validate(output)
        print(json.dumps({'valid': True, 'protocol': PROTOCOL, 'crossProcessContinuation': True}))
    elif args.mode == 'child':
        execute_segment(output)
    else:
        row = run_suite(output)
        print(json.dumps({key: row.get(key) for key in ('id', 'classification', 'success', 'cumulativeMetering')}, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--worker-bootstrap':
        raise SystemExit(demand.worker_bootstrap(sys.argv[2:]))
    main()
