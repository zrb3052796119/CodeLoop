#!/usr/bin/env python3
"""Explicit cross-process continuation of one interrupted implementation session.

No baseline hydration and no operator repair prompt. Preparation is local and
retains actual parent messages, current candidate, original scope and review
history. The predecessor remains immutable. Paid execution is a separate mode.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import ExitStack, closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
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
PROTOCOL = 'requirement-completion-native-checkpoint-v1'
DEFAULT_PREDECESSOR = ROOT / 'artifacts/requirement-completion-2026-10-18/memory-explicit-v3-continuation-v1'
DEFAULT_OUTPUT = ROOT / 'artifacts/requirement-completion-2026-10-18/memory-explicit-v3-continuation-v2-idle600'
TRIAL_ID = diagnostic.schedule()[0]['id']
LIMITS = {**continuous.LIMITS, 'idleTimeoutSeconds': 600}
HARNESS_FILES = (*diagnostic.HARNESS_FILES, 'run_requirement_completion_continuation.py',
                 'coverage_interruption_bridge.py', ENTRY.name)
ANALYSIS_PLAN = '''# Cross-process continuation diagnostic

This is a new process segment of the same logical implementation session, not
an uninterrupted Python loop, a fresh-baseline task, or an independent trial.
The predecessor remains terminal, unsuccessful and immutable. Resume the existing
candidate directory and byte-identical current parent context, including its
compaction boundary and actual tool results. Restore the host owner and attempt
counter and complete reviewState from the latest production finally checkpoint,
with original ImplementationSpec and byte-identical actual parent messages.
The former migration child dialogue is not part of that checkpoint; persisted
read-only tool-evidence remains separate audit material, not a fabricated transcript. An interrupted
review is not an accepted decision. Prior approved caches are not transferable.
Current public checks and the repaired production completion mechanism decide
whether the same Executor must continue. No operator repair prompt is appended.

The original public task, common requirement, explicit collaboration recipe,
GLM-5.3-Flash route/high reasoning/temperature 0, no cumulative token/call/cost/
execution-wall cap, output 131072 and connect30 are retained. Transport idle is
600 seconds: an original 120-second predecessor is upgraded once; an existing
600-second predecessor retains its exact transport policy. The manifest records
which occurred. Production source changes are separately frozen. Predecessor
and continuation physical ledgers are both reported; unknown usage stays null
with known lower bounds. The reset Python budget is a segment counter only.
Any same-model legacy review migration calls belong to this segment's ledger.

The prior hidden oracle, its output and final judgement are never recovery
inputs. Its unchanged opaque bytes may only reach the common terminal judge
once this segment terminates. All final results remain separately labelled;
no original result or denominator is rewritten. Current candidate preservation
is post-terminal/post-oracle, never claimed as an earlier execution snapshot.
'''
REVALIDATION_PLAN = ANALYSIS_PLAN.replace(
    'The predecessor remains terminal, unsuccessful and immutable.',
    'The predecessor remains terminal, production-approved and immutable. Its external '
    'evaluation result neither authorizes nor prevents this explicit public requirement revalidation.') + '''

The host explicitly reopens public requirement review using a typed revalidation
request bound to the original approved checkpoint. The first resumed action is
current public verification and independent read-only review. Only new public
evidence may request repair by the existing owner. The previous approval and
result remain historical facts. No private judge result is a repair instruction.
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


class MigrationEvidenceArchive:
    """Retain opaque migration persistence independently of completion reviews.

    This hook observes successful _persist returns, including calls made from a
    production finally block. It neither interprets documents nor replaces their
    references, model context, result or exception. The private manifest contains
    only file identity, hashes, lengths and retention failures.
    """
    artifact_kind = 'migration'
    owner_label = 'CompletionProbeMigration'

    @staticmethod
    def _owner_type():
        from minicode.completion_probe_migration import CompletionProbeMigration
        return CompletionProbeMigration

    def __init__(self, trial_root: Path):
        self.root = Path(trial_root)
        self.manifest = self.root / f'{self.artifact_kind}-evidence-archive.json'
        self.records = {}
        self.errors = []
        self.lock = threading.RLock()
        self._patch = None
        self._manifest_sha = None

    def _failure(self, operation: str, error: Exception) -> None:
        self.errors.append({'operation': operation, 'errorClass': type(error).__name__})

    def _document(self) -> dict:
        return {'archiveVersion': 1, 'archiveComplete': not self.errors,
            'recordingMethod': f'opaque copy of successful {self.owner_label}._persist output',
            f'{self.artifact_kind}Directories': len({row[f'{self.artifact_kind}Id'] for row in self.records.values()}),
            'files': list(self.records.values()), 'errors': list(self.errors)}

    def _save(self) -> None:
        temporary = None
        try:
            if (self.manifest.is_symlink() or self._manifest_sha is None
                    or sha_file(self.manifest) != self._manifest_sha):
                raise ValueError('migration archive manifest changed outside its owner')
            raw = json.dumps(self._document(), ensure_ascii=False, indent=2).encode() + b'\n'
            fd, name = tempfile.mkstemp(prefix='.migration-archive-', dir=self.root)
            temporary = Path(name)
            with os.fdopen(fd, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
            os.replace(temporary, self.manifest)
            self._manifest_sha = hashlib.sha256(raw).hexdigest()
        except Exception as error:
            self._failure('manifest', error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except Exception as error:
                    self._failure('temporary_cleanup', error)

    def _copy(self, owner, path: str) -> None:
        source = Path(path)
        directory = getattr(owner, '_directory', None)
        if (not isinstance(directory, Path) or directory.is_symlink() or source.is_symlink()
                or source.parent.resolve() != directory.resolve()
                or not re.fullmatch(r'\d{5,}-[a-z_-]+\.json', source.name)):
            raise ValueError('unexpected production migration artifact')
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError('migration artifact must be a regular file')
            raw = handle.read()
        digest = hashlib.sha256(raw).hexdigest()
        identity = self.artifact_kind + '_' + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:24]
        relative = Path(f'{self.artifact_kind}-evidence') / identity / source.name
        destination = safe_path(self.root, str(relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.parent.chmod(0o700)
        destination.parent.chmod(0o700)
        if destination.exists():
            if sha_file(destination) != digest:
                raise ValueError('persisted migration evidence changed')
        else:
            private_write(destination, raw)
        self.records[str(relative)] = {f'{self.artifact_kind}Id': identity, 'file': str(relative),
            'sha256': digest, 'bytes': len(raw)}

    def _record(self, owner, path: str) -> None:
        with self.lock:
            try:
                self._copy(owner, path)
            except Exception as error:
                self._failure('copy', error)
            self._save()

    def __enter__(self):
        owner_type = self._owner_type()
        if self.root.is_symlink():
            raise ValueError('migration archive root cannot be a symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        if self.manifest.exists() or self.manifest.is_symlink() or (self.root / f'{self.artifact_kind}-evidence').exists():
            raise ValueError('migration archive already exists; preserve earlier evidence')
        private_json(self.manifest, self._document())
        self._manifest_sha = sha_file(self.manifest)
        original = owner_type._persist

        @wraps(original)
        def persisted(owner, *args, **kwargs):
            path = original(owner, *args, **kwargs)
            self._record(owner, path)
            return path

        self._patch = patch.object(owner_type, '_persist', persisted)
        self._patch.__enter__()
        return self

    def __exit__(self, *args):
        try:
            return self._patch.__exit__(*args)
        finally:
            with self.lock:
                self._save()


class AdjudicationEvidenceArchive(MigrationEvidenceArchive):
    """Retain independent failure-review evidence without rewriting authority."""

    artifact_kind = 'adjudication'
    owner_label = 'ProbeFailureAdjudicator'

    @staticmethod
    def _owner_type():
        from minicode.probe_failure_adjudication import ProbeFailureAdjudicator
        return ProbeFailureAdjudicator


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


def recover_submission_draft(root: Path, document: dict, spec) -> dict | None:
    """Read a rejected proposal only from the authenticated native archive.

    This does not reconstruct the lost reviewer conversation. The original
    checkpoint and parent messages remain untouched; the result is reference
    material for the production gate, never a decision or executable replay.
    """
    from minicode.completion_review_draft import create_submission_draft

    state = document['reviewState']
    if state.get('pendingSubmissionDraft') is not None:
        return None  # Prefer the complete native state; do not replace it.
    manifest_path = root / 'completion-review-archive.json'
    if manifest_path.is_symlink():
        raise ValueError('submission draft archive manifest cannot be a symlink')
    if not manifest_path.exists():
        return None
    directory = verify_archive(root)
    manifest = json.loads(manifest_path.read_bytes())
    authenticated = {str(safe_path(root, row['file'])): row for row in manifest['files']}
    records = {}
    for path in sorted(directory.glob('*-tool-evidence.json')):
        raw = path.read_bytes()
        row = authenticated[str(path)]
        if hashlib.sha256(raw).hexdigest() != row['sha256'] or len(raw) != row['bytes']:
            raise ValueError('review archive changed while recovering rejected draft')
        value = json.loads(raw)
        if isinstance(value, dict) and value.get('toolName') == 'completion_review_submit':
            records[path.name] = (path, raw, value)
    if not records:
        return None
    original = state.get('evidenceDirectory')
    if not isinstance(original, str) or not Path(original).is_absolute():
        raise ValueError('submission draft lacks original host evidence directory')
    digest = document['candidateDigest']
    review_index = len(state['reviews'])
    review_id = 'completion_review_' + hashlib.sha256(f'{digest}:{review_index}'.encode()).hexdigest()[:24]
    objective_digest = 'sha256:' + hashlib.sha256(spec.objective.encode()).hexdigest()
    if state.get('objective') != spec.objective:
        raise ValueError('submission draft original objective differs from host specification')
    drafts, used = [], set()
    for name, (path, raw, value) in records.items():
        if value.get('reviewId') != review_id or value.get('phase') != 'finished':
            continue
        invocation = value.get('invocationId')
        if not isinstance(invocation, str) or Path(invocation).parent != Path(original):
            raise ValueError('submission draft rejection points outside original evidence')
        source = records.get(Path(invocation).name)
        if source is None or invocation != str(Path(original) / source[0].name) or invocation in used:
            raise ValueError('submission draft has missing or duplicate native pair')
        used.add(invocation)
        if value.get('isError') is not True:
            continue  # An accepted call is not an unaccepted recovery draft.
        drafts.append(create_submission_draft(source[1], raw,
            started_ref=invocation, finished_ref=str(Path(original) / name),
            candidate_digest=digest, objective_digest=objective_digest, review_index=review_index))
    return drafts[-1] if drafts else None


def recover_coverage_audit(root: Path, document: dict) -> dict | None:
    """Bind the last unfinished actual audit, never synthesize child dialogue."""
    state = document['reviewState']
    if state.get('pendingCoverageAudit') is not None:
        return None  # The production checkpoint is the primary source.
    manifest_path = root / 'completion-review-archive.json'
    if not manifest_path.exists():
        return None
    directory = verify_archive(root)
    message_paths = sorted(directory.glob('*-coverage-audit-messages.json'))
    if not message_paths:
        return None
    original = state.get('evidenceDirectory')
    if (not isinstance(original, str) or not Path(original).is_absolute()
            or '..' in Path(original).parts or str(Path(original)) != original):
        raise ValueError('coverage audit lacks its original host evidence directory')
    messages_path = message_paths[-1]
    messages = json.loads(messages_path.read_bytes())
    if messages.get('candidateDigest') != document['candidateDigest']:
        return None  # A stale audit must not replace the current candidate's review.
    bindings = {key: messages.get(key) for key in
        ('objectiveDigest', 'candidateDigest', 'proposalDigest', 'supersessionDigest')}
    authorities = []
    for path in directory.glob('*-coverage-audit-authority.json'):
        ref = str(Path(original) / path.name)
        expected_id = 'coverage_audit_' + hashlib.sha256(ref.encode()).hexdigest()[:24]
        if messages.get('auditId') == expected_id:
            authorities.append(path)
    if len(authorities) != 1:
        raise ValueError('coverage context has missing or ambiguous original authority')
    authority_path = authorities[0]
    authority_ref = str(Path(original) / authority_path.name)
    for path in directory.glob('*-coverage-audit-decision.json'):
        decision = json.loads(path.read_bytes())
        if decision.get('authorityRef') == authority_ref:
            return None  # An audit with a terminal decision is not resumable.
    documents = [path for path in directory.glob('*-coverage-audit-evidence-documents.json')
        if authority_path.name < path.name < messages_path.name
        and all(json.loads(path.read_bytes()).get(key) == value for key, value in bindings.items())]
    if len(documents) != 1:
        raise ValueError('coverage context has missing or ambiguous immutable evidence documents')

    def record(path):
        raw = path.read_bytes()
        return {'ref': str(Path(original) / path.name),
            'sha256': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'rawJson': raw.decode('utf-8')}

    # Native snapshots retain their phase and delivery sidecars separately.
    # Select only this audit's authenticated originals; never downgrade a new
    # inspection dialogue to v1 or reconstruct its messages/read credit.
    def latest_audit_record(suffix, *, before_messages=False):
        matches = []
        for path in sorted(directory.glob('*' + suffix)):
            if path.name <= authority_path.name or (before_messages and path.name >= messages_path.name):
                continue
            value = json.loads(path.read_bytes())
            if value.get('auditId') != messages['auditId']:
                continue
            if any(value.get(key) != expected for key, expected in bindings.items()):
                raise ValueError('coverage sidecar changed original audit bindings')
            matches.append(path)
        return record(matches[-1]) if matches else None

    inspection = latest_audit_record('-coverage-inspection.json', before_messages=True)
    delivery = latest_audit_record('-coverage-audit-delivery.json')
    materials = []
    for path in directory.glob('*-coverage-audit-review-materials.json'):
        value = json.loads(path.read_bytes())
        if value.get('originalAuthorityRef') != authority_ref:
            continue
        if (not authority_path.name < path.name < messages_path.name
                or any(value.get(key) != expected for key, expected in bindings.items())):
            raise ValueError('coverage review materials changed original audit bindings')
        materials.append(path)
    if len(materials) > 1:
        raise ValueError('coverage context has ambiguous original review materials')
    legacy = None
    if inspection is not None and json.loads(inspection['rawJson'])['origin'] == 'legacy_new_context':
        previous = max((path.name for path in directory.glob('*-coverage-audit-authority.json')
                        if path.name < authority_path.name), default='')
        epochs = [path for path in directory.glob('*-coverage-audit-legacy-epoch.json')
                  if previous < path.name < authority_path.name]
        if len(epochs) != 1 or any(json.loads(epochs[0].read_bytes()).get(key) != expected
                                   for key, expected in bindings.items()):
            raise ValueError('coverage context has missing or ambiguous original legacy epoch')
        legacy = record(epochs[0])

    from minicode.coverage_audit_state import build_coverage_audit_state, validate_coverage_audit_state
    pending = build_coverage_audit_state(authority_record=record(authority_path),
        documents_record=record(documents[0]), messages_record=record(messages_path),
        delivery_record=delivery, inspection_record=inspection,
        review_materials_record=record(materials[0]) if materials else None,
        legacy_audit_record=legacy)
    validate_coverage_audit_state(pending, objective=state['objective'],
        candidate_digest=document['candidateDigest'])
    return pending


def recovered_host_document(output: Path) -> dict:
    """Add authenticated recovery sidecars to a copy; original bytes stay intact."""
    from minicode.completion_review_draft import validate_submission_draft

    document = json.loads((output / 'private/host-continuation.json').read_bytes())
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    restoration = checkpoint.get('submissionDraftRestoration')
    if restoration is not None:
        if (not isinstance(restoration, dict) or set(restoration) != {'kind', 'file', 'sha256'}
            or restoration['kind'] != 'unaccepted_native_tool_pair_reference'
            or restoration['file'] != 'private/pending-submission-draft.json'):
            raise ValueError('invalid submission draft restoration contract')
        path = safe_path(output, restoration['file'])
        if sha_file(path) != restoration['sha256']:
            raise ValueError('saved unaccepted submission draft changed')
        draft = json.loads(path.read_bytes())
        objective_digest = 'sha256:' + hashlib.sha256(document['reviewState']['objective'].encode()).hexdigest()
        validate_submission_draft(draft, objective_digest=objective_digest,
            candidate_digest=document['candidateDigest'])
        if document['reviewState'].get('pendingSubmissionDraft') is not None:
            raise ValueError('draft restoration must not replace existing native state')
        document['reviewState']['pendingSubmissionDraft'] = draft
    restoration = checkpoint.get('coverageAuditRestoration')
    if restoration is not None:
        if (not isinstance(restoration, dict) or set(restoration) != {'kind', 'file', 'sha256'}
                or restoration['kind'] != 'actual_native_coverage_audit_context'
                or restoration['file'] != 'private/pending-coverage-audit.json'):
            raise ValueError('invalid coverage audit restoration contract')
        path = safe_path(output, restoration['file'])
        if sha_file(path) != restoration['sha256']:
            raise ValueError('saved actual coverage audit changed')
        pending = json.loads(path.read_bytes())
        predecessor = Path(checkpoint['predecessorDirectory'])
        for relative, expected in checkpoint['predecessorArtifactSha256'].items():
            if sha_file(safe_path(predecessor, relative)) != expected:
                raise ValueError('immutable predecessor evidence changed')
        expected = recover_coverage_audit(predecessor / 'private' / TRIAL_ID, document)
        if pending != expected or document['reviewState'].get('pendingCoverageAudit') is not None:
            raise ValueError('coverage audit differs from the authenticated actual child context')
        document['reviewState']['pendingCoverageAudit'] = pending
    return document


_COVERAGE_RECOVERY_FILES = {
    'coverage-audit-recovery-delivery': 'private/coverage-recovery-delivery.json',
    'coverage-audit-recovery-receipt': 'private/coverage-recovery-receipt.json',
}
_INTERRUPTION_RECOVERY_FILES = {'coverage-audit-recovery-receipt': 'private/coverage-recovery-receipt.json'}
_REJECTION_RECOVERY_FILES = {**_COVERAGE_RECOVERY_FILES,
    'coverage-audit-recovery-rejection': 'private/coverage-recovery-rejection.json',
    'coverage-audit-recovery-messages': 'private/coverage-recovery-messages.json'}


def coverage_rejection_originals(directory: Path, records: dict, pending: dict) -> tuple[dict, dict]:
    """Select one original invocation by content/identity inside a verified archive."""
    from minicode.coverage_audit_state import host_record, record_document
    original = Path(pending['messagesRecord']['ref']).parent
    if directory.name != 'review_' + hashlib.sha256(str(original.resolve()).encode()).hexdigest()[:24]:
        raise ValueError('rejected coverage archive differs from its original native directory')
    messages = record_document(records['messagesRecord'])['messages']
    if len(messages) < 2:
        raise ValueError('rejected coverage archive has no delivered invocation')
    call, result = messages[-2:]
    if (call.get('role') != 'assistant_tool_call' or call.get('toolName') != 'completion_coverage_audit_submit'
            or result.get('role') != 'tool_result' or result.get('isError') is not True
            or result.get('toolUseId') != call.get('toolUseId')):
        raise ValueError('rejected coverage archive must end in an actual failed submit')
    matches = []
    drafts = [(path, json.loads(path.read_bytes())) for path in directory.glob('*-coverage-audit-submission-draft.json')]
    for finished_path in directory.glob('*-coverage-audit-tool-evidence.json'):
        finished = json.loads(finished_path.read_bytes())
        if (finished.get('auditId') != pending['auditId'] or finished.get('phase') != 'finished'
                or finished.get('toolName') != call['toolName'] or finished.get('isError') is not True
                or not isinstance(finished.get('output'), str)
                or not result.get('content', '').startswith(finished['output'])
                or not isinstance(finished.get('invocationId'), str)):
            continue
        started_ref = Path(finished['invocationId'])
        if started_ref.parent != original:
            continue
        started_path = directory / started_ref.name
        if not started_path.is_file():
            continue
        started = json.loads(started_path.read_bytes())
        if started.get('input') != call.get('input') or started.get('phase') != 'started':
            continue
        for draft_path, draft in drafts:
            if (draft.get('auditId') == pending['auditId'] and draft.get('originalInput') == call['input']
                    and started_path.name < draft_path.name < finished_path.name):
                matches.append({'startedRecord': started_path, 'finishedRecord': finished_path, 'draftRecord': draft_path})
    if len(matches) != 1:
        raise ValueError('rejected coverage invocation has missing or ambiguous original records')
    originals, identities = {}, {}
    for key, path in matches[0].items():
        raw = path.read_bytes()
        originals[key] = host_record(str(original / path.name), raw)
        identities[key] = {'file': str(path), 'ref': originals[key]['ref'],
                           'sha256': originals[key]['sha256'], 'bytes': len(raw)}
    return originals, identities


def coverage_recovery_source(source: Path, *, predecessor: Path, document: dict) -> tuple[dict, dict]:
    """Authenticate one explicitly selected ancestor; never replace native state."""
    source, predecessor = source.resolve(), predecessor.resolve()
    chain = predecessor_chain(predecessor)
    if source not in chain:
        raise ValueError('coverage recovery source is outside the authenticated predecessor chain')
    pending = document['reviewState'].get('pendingCoverageAudit')
    if pending is None:
        import sys
        from scripts.coverage_interruption_bridge import interrupted_coverage_source
        return interrupted_coverage_source(sys.modules[__name__], source, predecessor, document, chain)
    if not isinstance(pending, dict):
        raise ValueError('explicit coverage recovery requires an existing native pending audit')
    root = source / 'private' / TRIAL_ID
    receipt_path = root / 'implementation-checkpoint-receipt.json'
    source_receipt = json.loads(receipt_path.read_bytes())
    if (source_receipt.get('available') is not True
            or source_receipt.get('source') != 'production_parent_finally'
            or source_receipt.get('executorId') != document['executorId']
            or source_receipt.get('candidateDigest') != document['candidateDigest']
            or type(source_receipt.get('attempt')) is not int
            or not 0 <= source_receipt['attempt'] <= document['attempt']
            or sha_file(root / 'implementation-checkpoint.json') != source_receipt.get('documentSha256')
            or sha_file(root / 'implementation-checkpoint-messages.json') != source_receipt.get('messagesSha256')):
        raise ValueError('coverage recovery ancestor changed native owner, candidate or checkpoint')
    source_document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    source_pending = (source_document.get('reviewState') or {}).get('pendingCoverageAudit')
    if (any(source_document.get(key) != document[key] for key in ('executorId', 'candidateDigest', 'specDigest'))
            or source_document.get('attempt') != source_receipt['attempt']
            or not isinstance(source_pending, dict) or source_pending.get('auditId') != pending['auditId']
            or source_pending.get('authorityRecord') != pending['authorityRecord']):
        raise ValueError('coverage recovery source checkpoint has a different owner or audit authority')
    del source_document, source_pending
    archive_path = root / 'completion-review-archive.json'
    if archive_path.is_symlink():
        raise ValueError('coverage recovery archive cannot be a symlink')
    directory = verify_archive(root)  # Every listed file, byte length and SHA.
    rejection_mode = (pending.get('coverageAuditStateVersion') == 3
        and json.loads(pending['inspectionRecord']['rawJson']).get('phase') == 'reconciliation')
    original = Path(pending['messagesRecord' if rejection_mode else 'authorityRecord']['ref']).parent
    anchor_record = pending['messagesRecord' if rejection_mode else 'authorityRecord']
    authority_path = directory / Path(anchor_record['ref']).name
    if (not authority_path.is_file()
            or 'sha256:' + sha_file(authority_path) != anchor_record['sha256']
            or authority_path.read_bytes() != anchor_record['rawJson'].encode()):
        raise ValueError('coverage recovery archive does not contain the original native anchor record')
    records, identities = {}, {}
    for key, suffix in (('messagesRecord', '-coverage-audit-messages.json'),
                        ('inspectionRecord', '-coverage-inspection.json'),
                        ('deliveryRecord', '-coverage-audit-delivery.json')):
        matches = []
        for path in sorted(directory.glob('*' + suffix)):
            raw = path.read_bytes()
            value = json.loads(raw)
            if value.get('auditId') == pending['auditId']:
                matches.append((path, raw))
        if not matches:
            raise ValueError('coverage recovery archive lacks a current audit record')
        path, raw = matches[-1]
        reference = str(original / path.name)
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        records[key] = {'ref': reference, 'sha256': digest, 'rawJson': raw.decode('utf-8')}
        if rejection_mode and key == 'inspectionRecord' and raw.decode('utf-8') == pending['inspectionRecord']['rawJson']:
            records[key] = pending['inspectionRecord']
        identities[key] = {'file': str(path.relative_to(source)), 'ref': records[key]['ref'],
                           'sha256': digest, 'bytes': len(raw)}
    for path in directory.glob('*-coverage-audit-decision.json'):
        decision = json.loads(path.read_bytes())
        if (decision.get('authorityRef') == pending['authorityRecord']['ref']
                or decision.get('auditId') == pending['auditId']):
            raise ValueError('coverage recovery cannot reopen a terminal audit')
    descriptor = {'version': 1, 'kind': 'explicit_same_audit_archive_recovery',
        'sourceDirectory': str(source), 'sourceArchiveSha256': sha_file(archive_path),
        'sourceNativeReceiptSha256': sha_file(receipt_path),
        'sourceNativeCheckpointSha256': source_receipt['documentSha256'],
        'sourceNativeMessagesSha256': source_receipt['messagesSha256'],
        'executorId': document['executorId'], 'candidateDigest': document['candidateDigest'],
        'auditId': pending['auditId'], 'records': identities}
    if rejection_mode:
        originals, metadata = coverage_rejection_originals(directory, records, pending)
        records['rejectionRecords'] = originals
        descriptor['rejectionRecords'] = {key: {**row, 'file': str(Path(row['file']).relative_to(source))}
                                          for key, row in metadata.items()}
        descriptor['recoveryReason'] = 'truncated_rejection_checkpoint_lag'
    return descriptor, records


def prepare_coverage_recovery(output: Path, source: Path, *, predecessor: Path,
                              document: dict, spec, messages: list[dict]) -> dict:
    from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
    from minicode.implementation_continuation import HostImplementationContinuation

    descriptor, records = coverage_recovery_source(source, predecessor=predecessor, document=document)
    original = HostImplementationContinuation.from_document(document, spec=spec, messages=messages)
    generated = {}

    def persist(kind: str, value: dict) -> str:
        relative = (_INTERRUPTION_RECOVERY_FILES if 'interruptionRecords' in records else
                    _REJECTION_RECOVERY_FILES if 'rejectionRecords' in records else _COVERAGE_RECOVERY_FILES)[kind]
        path = safe_path(output, relative)
        private_json(path, value)
        generated[kind] = {'file': relative, 'sha256': sha_file(path)}
        return str(path.resolve())

    recovery = HostCoverageAuditRecovery.from_records(continuation=original, spec=spec,
        messages_record=records['messagesRecord'], inspection_record=records['inspectionRecord'],
        delivery_record=records['deliveryRecord'], source_archive_digest='sha256:' + descriptor['sourceArchiveSha256'],
        persist=persist, rejection_records=records.get('rejectionRecords'),
        interruption_records=records.get('interruptionRecords'))
    recovery.validate_for(document, spec=spec)
    descriptor['generatedRecords'] = generated
    path = output / 'private/coverage-recovery.json'
    private_json(path, descriptor)
    return {'kind': 'typed_same_audit_archive_recovery', 'file': 'private/coverage-recovery.json',
            'sha256': sha_file(path)}


def saved_coverage_recovery(output: Path, *, spec, messages: list[dict], document: dict | None = None):
    """Recompute the frozen host capability with a read-only persistence callback."""
    from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
    from minicode.implementation_continuation import HostImplementationContinuation

    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    restoration = checkpoint.get('coverageAuditRecovery')
    if restoration is None:
        return None
    if (not isinstance(restoration, dict) or set(restoration) != {'kind', 'file', 'sha256'}
            or restoration['kind'] != 'typed_same_audit_archive_recovery'
            or restoration['file'] != 'private/coverage-recovery.json'
            or any(checkpoint.get(key) is not None for key in
                   ('coverageAuditRestoration', 'submissionDraftRestoration', 'approvedRevalidation', 'hostBootstrapRecovery'))):
        raise ValueError('invalid explicit coverage recovery contract')
    path = safe_path(output, restoration['file'])
    if sha_file(path) != restoration['sha256']:
        raise ValueError('coverage recovery descriptor changed')
    descriptor = json.loads(path.read_bytes())
    if (checkpoint.get('coverageCheckpointFailureContinuation') is not None
            and Path(descriptor['sourceDirectory']).resolve() != Path(checkpoint['predecessorDirectory']).resolve()):
        raise ValueError('failed coverage checkpoint recovery must retain its actual failed predecessor')
    if document is None:
        document = recovered_host_document(output)
    expected, records = coverage_recovery_source(Path(descriptor['sourceDirectory']),
        predecessor=Path(checkpoint['predecessorDirectory']), document=document)
    generated = descriptor.get('generatedRecords')
    expected_files = (_INTERRUPTION_RECOVERY_FILES if 'interruptionRecords' in records else
                      _REJECTION_RECOVERY_FILES if 'rejectionRecords' in records else _COVERAGE_RECOVERY_FILES)
    if (not isinstance(generated, dict) or set(generated) != set(expected_files)
            or descriptor != {**expected, 'generatedRecords': generated}):
        raise ValueError('coverage recovery no longer binds the authenticated ancestor records')
    original = HostImplementationContinuation.from_document(document, spec=spec, messages=messages)

    def persist(kind: str, value: dict) -> str:
        metadata = generated[kind]
        if (not isinstance(metadata, dict) or set(metadata) != {'file', 'sha256'}
                or metadata['file'] != expected_files[kind]):
            raise ValueError('invalid generated coverage recovery record')
        record_path = safe_path(output, metadata['file'])
        if sha_file(record_path) != metadata['sha256'] or json.loads(record_path.read_bytes()) != value:
            raise ValueError('coverage recovery derived record changed')
        return str(record_path.resolve())

    recovery = HostCoverageAuditRecovery.from_records(continuation=original, spec=spec,
        messages_record=records['messagesRecord'], inspection_record=records['inspectionRecord'],
        delivery_record=records['deliveryRecord'], source_archive_digest='sha256:' + expected['sourceArchiveSha256'],
        persist=persist, rejection_records=records.get('rejectionRecords'),
        interruption_records=records.get('interruptionRecords'))
    recovery.validate_for(document, spec=spec)
    return recovery


def approved_review_evidence(root: Path, document: dict) -> dict:
    """Authenticate the latest actual approved review without reading a judge."""
    state = document['reviewState']
    reviews = state.get('reviews')
    if not isinstance(reviews, list) or not reviews or not isinstance(reviews[-1], dict):
        raise ValueError('approved revalidation requires the complete latest native review')
    approved = reviews[-1]
    if approved.get('status') != 'approved' or approved.get('patchDigest') != document['candidateDigest']:
        raise ValueError('approved revalidation requires current native approval')
    reference = approved.get('evidenceRef')
    original = state.get('evidenceDirectory')
    if (not isinstance(reference, str) or not isinstance(original, str)
            or not Path(reference).is_absolute() or '..' in Path(reference).parts
            or str(Path(reference)) != reference
            or Path(reference).parent != Path(original)
            or approved.get('evidenceDirectory') != original
            or not re.fullmatch(r'[0-9]{5,}-review\.json', Path(reference).name)):
        raise ValueError('approved revalidation lacks its original host review reference')
    manifest_path = root / 'completion-review-archive.json'
    if manifest_path.is_symlink():
        raise ValueError('approved review archive manifest cannot be a symlink')
    directory = verify_archive(root)
    path = directory / Path(reference).name
    manifest = json.loads(manifest_path.read_bytes())
    rows = [row for row in manifest['files'] if safe_path(root, row['file']) == path]
    if len(rows) != 1:
        raise ValueError('approved review is missing from its authenticated archive')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != rows[0]['sha256'] or len(raw) != rows[0]['bytes']:
        raise ValueError('approved review archive changed during authentication')
    if json.loads(raw) != {key: value for key, value in approved.items()
                          if key not in {'evidenceRef', 'evidenceDirectory'}}:
        raise ValueError('approved archive record does not match the native checkpoint')
    return {'approvalEvidenceRef': reference, 'archiveFile': rows[0]['file'],
        'archiveSha256': rows[0]['sha256']}


def revalidation_request(document: dict) -> dict:
    """Only public host checkpoint facts form a revalidation request."""
    from minicode.implementation_continuation import continuation_digest

    approval = document['reviewState']['reviews'][-1]
    return {'revalidationVersion': 1, 'reason': 'public_requirement_revalidation',
        'checkpointDigest': continuation_digest(document),
        **{key: document[key] for key in ('executorId', 'attempt', 'candidateDigest', 'specDigest', 'messagesDigest')},
        'approvalEvidenceRef': approval['evidenceRef'], 'approvalDigest': continuation_digest(approval)}


def saved_revalidation_request(output: Path) -> dict | None:
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    restoration = checkpoint.get('approvedRevalidation')
    if restoration is None:
        return None
    if (not isinstance(restoration, dict) or set(restoration) != {'kind', 'file', 'sha256'}
            or restoration['kind'] != 'explicit_public_requirement_revalidation'
            or restoration['file'] != 'private/approved-revalidation.json'):
        raise ValueError('invalid approved revalidation contract')
    path = safe_path(output, restoration['file'])
    if sha_file(path) != restoration['sha256']:
        raise ValueError('saved approved revalidation request changed')
    request = json.loads(path.read_bytes())
    if request != revalidation_request(recovered_host_document(output)):
        raise ValueError('approved revalidation request does not bind the original checkpoint')
    return request


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


def transport_change_policy(prior: dict) -> dict:
    if prior not in (continuous.LIMITS, LIMITS):
        raise ValueError('only frozen idle120-to-600 or unchanged idle600 is authorized')
    return {**prior, 'idleTimeoutSeconds': 600}


def transport_change_record(prior: dict) -> dict:
    current = transport_change_policy(prior)
    before, after = prior['idleTimeoutSeconds'], current['idleTimeoutSeconds']
    return {'field': 'idleTimeoutSeconds', 'before': before, 'after': after,
        'classification': ('transport policy retained' if before == after
                           else 'transport-threshold change, not identical-transport replay')}


def host_checkpoint_inputs(root: Path, spec, workspace: Path, *, verify_candidate: bool = True) -> tuple[dict, list[dict], Path]:
    from minicode.implementation_continuation import HostImplementationContinuation
    receipt_path = safe_path(root, 'implementation-checkpoint-receipt.json')
    receipt = json.loads(receipt_path.read_bytes())
    document_path = safe_path(root, 'implementation-checkpoint.json')
    messages_path = safe_path(root, 'implementation-checkpoint-messages.json')
    if (receipt.get('version') != 1 or receipt.get('available') is not True
            or receipt.get('source') != 'production_parent_finally'
            or sha_file(document_path) != receipt.get('documentSha256')
            or sha_file(messages_path) != receipt.get('messagesSha256')):
        raise ValueError('latest production checkpoint is unavailable or changed')
    document = json.loads(document_path.read_bytes())
    messages = json.loads(messages_path.read_bytes())
    if not isinstance(messages, list) or len(messages) != receipt.get('messageCount'):
        raise ValueError('latest parent checkpoint message count changed')
    host = HostImplementationContinuation.from_document(document, spec=spec, messages=messages)
    if verify_candidate:
        validate_native_candidate(host, spec, workspace)
    if any(document[key] != receipt.get(key) for key in ('executorId', 'attempt', 'candidateDigest')):
        raise ValueError('checkpoint identity differs from its production receipt')
    # Restoring this state validates all old evidence but grants no old approval.
    from minicode.implementation_completion_review import ImplementationCompletionReview
    if document.get('reviewState') is None:
        raise ValueError('this follow-up requires the complete host review state')
    ImplementationCompletionReview.from_state(spec, document['reviewState'])
    return document, messages, messages_path


def validate_native_candidate(host, spec, workspace: Path) -> None:
    """Use exactly the empty persistent permission store used by execute_segment.

    No model, registry or user approval is constructed. Mandatory forbidden paths
    still apply; a read-only dependency change invalidates the same host digest.
    """
    from minicode.permissions import PermissionManager
    from minicode.tooling import ToolContext
    with patch('minicode.permissions._read_permission_store', return_value={}):
        permissions = PermissionManager(str(workspace.resolve()), prompt=None)
        host.validate_candidate(spec=spec,
            context=ToolContext(cwd=str(workspace.resolve()), permissions=permissions))


def last_transport_metadata(root: Path) -> dict | None:
    """Read only physical transport scalars, never request/response/tool text."""
    path = safe_path(root, 'transport.sqlite')
    if not path.is_file():
        raise ValueError('terminal physical ledger is missing')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        columns = ('event', 'errorClass', 'errorPhase', 'seconds', 'lastByteSeconds', 'streamDone', 'httpStatus')
        row = db.execute('SELECT call,' + ','.join("json_extract(data, '$." + key + "')" for key in columns)
            + ' FROM requests ORDER BY call DESC LIMIT 1').fetchone()
        active = db.execute("SELECT count(*) FROM requests WHERE json_extract(data, '$.event') = 'started'").fetchone()[0]
    if active:
        raise ValueError('predecessor has unfinished physical requests')
    return ({'call': row[0], **dict(zip(columns, row[1:]))} if row else None)


def proxy_header_partial_evidence(root: Path, messages: list[dict], model_events: list[dict]) -> dict:
    """Recognize one legacy host fallback, never arbitrary provider_error prose.

    Old transport rows lack model operation IDs. Match every parent model attempt
    to a main physical call by order/count and terminal status, and authenticate
    the journal's started/failed operation pair. Do not claim a nonexistent ID join.
    """
    expected = 'Model API error (URLError): <urlopen error Tunnel connection failed: 503 Service Unavailable>'
    if (len(messages) < 2 or messages[-2].get('role') != 'assistant'
            or messages[-2].get('content') != expected
            or messages[-1].get('role') != 'assistant' or messages[-1].get('_partial') is not True
            or messages[-1].get('_host_implementation_receipt') is not True):
        raise ValueError('no exact saved host proxy503 error and partial receipt')
    marker = '=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n'
    content = messages[-1].get('content')
    if not isinstance(content, str) or content.count(marker) != 1:
        raise ValueError('missing actual host partial receipt')
    receipt = json.loads(content.split(marker, 1)[1])
    if (receipt.get('implementationDeliveryVersion') != 1 or receipt.get('executionEnded') is not True
            or receipt.get('delivery') != 'partial' or receipt.get('requirementsAccepted') is not False
            or receipt.get('continuesAfterResponse') is not False):
        raise ValueError('host receipt does not confirm incomplete delivery')
    if (not model_events or len({row['runId'] for row in model_events}) != 1
            or len({row['sequence'] for row in model_events}) != len(model_events)):
        raise ValueError('missing or ambiguous parent model journal')
    model_events = sorted(model_events, key=lambda row: row['sequence'])
    with closing(sqlite3.connect(safe_path(root, 'transport.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        columns = ('event', 'actorId', 'role', 'parentToolOperationId', 'errorClass',
                   'errorPhase', 'httpStatus', 'lastByteSeconds')
        rows = db.execute('SELECT call,' + ','.join("json_extract(data, '$." + key + "')" for key in columns)
            + ' FROM requests ORDER BY call').fetchall()
    physical = [{'call': row[0], **dict(zip(columns, row[1:]))} for row in rows]
    if not physical or len(model_events) != len(physical) * 2:
        raise ValueError('parent model attempts do not match physical call count')
    seen = set()
    for index, call in enumerate(physical):
        start, end = model_events[index * 2:index * 2 + 2]
        operation = start.get('operationId')
        if (call['actorId'] != 'main' or call['role'] != 'main' or call['parentToolOperationId'] is not None
                or start['type'] != 'model.started' or end['type'] not in {'model.completed', 'model.failed'}
                or not isinstance(operation, str) or not re.fullmatch(r'modelop_[0-9a-f]{32}', operation)
                or end.get('operationId') != operation or operation in seen
                or call['event'] != end['type'].split('.')[1]):
            raise ValueError('parent model journal and physical call order disagree')
        seen.add(operation)
    last = physical[-1]
    failure = model_events[-1]
    if (last['event'] != 'failed' or last['errorClass'] != 'URLError' or last['errorPhase'] != 'headers'
            or last['httpStatus'] is not None or last['lastByteSeconds'] is not None
            or failure['type'] != 'model.failed' or failure.get('failureKind') != 'provider_error'):
        raise ValueError('last provider failure is not the evidenced legacy proxy503 header error')
    return {'errorClass': 'URLError', 'transportCause': 'proxy_tunnel_http_503',
        'originalErrorConvertedToPartial': True, 'parentMessagesTailSha256': hashlib.sha256(canonical(messages[-2:])).hexdigest(),
        'journalModelOperationId': failure['operationId'], 'journalRunId': failure['runId'],
        'journalFailureSequence': failure['sequence'], 'physicalCall': last['call'],
        'physicalAssociation': 'main attempt order/count/status; old ledger has no model operation ID'}


def parent_model_partial_evidence(root: Path, messages: list[dict], model_events: list[dict],
                                  *, after_sequence: int) -> dict:
    """Authenticate a swallowed parent API error, including legacy ledgers.

    Legacy transport has no model operation IDs, and incorrectly labels nested
    review and compaction calls as main.  Require a unique provider interval
    inside the actual ordinary parent model operation; later compaction calls
    must each belong to their own purpose-bound journal operation.  Neither the
    last physical call nor a matching error string alone identifies the parent.
    """
    allowed_errors = {'EvaluationIdleTimeout', 'TimeoutError', 'EvaluationStreamError',
        'ConnectionError', 'ConnectionResetError', 'BrokenPipeError', 'URLError', 'HTTPError'}
    if (len(messages) < 2 or messages[-2].get('role') != 'assistant'
            or messages[-1].get('role') != 'assistant' or messages[-1].get('_partial') is not True
            or messages[-1].get('_host_implementation_receipt') is not True):
        raise ValueError('model failure recovery requires the actual host API error and partial receipt')
    error = re.fullmatch(r'Model API error \(([A-Za-z][A-Za-z0-9_]*)\): ([\s\S]+)',
        messages[-2].get('content') or '')
    if error is None or error[1] not in allowed_errors:
        raise ValueError('host fallback is not a recognized provider failure')
    marker = '=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n'
    content = messages[-1].get('content')
    if not isinstance(content, str) or content.count(marker) != 1:
        raise ValueError('missing actual host partial receipt')
    receipt = json.loads(content.split(marker, 1)[1])
    if (receipt.get('implementationDeliveryVersion') != 1 or receipt.get('executionEnded') is not True
            or receipt.get('delivery') != 'partial' or receipt.get('requirementsAccepted') is not False
            or receipt.get('continuesAfterResponse') is not False):
        raise ValueError('host receipt does not establish incomplete ended execution')
    if (not model_events or len({row['runId'] for row in model_events}) != 1
            or len({row['sequence'] for row in model_events}) != len(model_events)):
        raise ValueError('missing or ambiguous parent model journal')

    def epoch(value):
        if not isinstance(value, str):
            raise ValueError('model journal lacks a timestamp')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('model journal timestamp lacks a timezone')
        return stamp.timestamp()

    grouped = {}
    for row in sorted(model_events, key=lambda item: item['sequence']):
        operation = row.get('operationId')
        if (not isinstance(operation, str) or not re.fullmatch(r'modelop_[0-9a-f]{32}', operation)
                or row.get('purpose') not in {None, 'context_compaction'}):
            raise ValueError('unknown parent model operation or purpose')
        grouped.setdefault(operation, []).append(row)
    pairs = []
    for operation, rows in grouped.items():
        if (len(rows) != 2 or rows[0]['type'] != 'model.started'
                or rows[1]['type'] not in {'model.completed', 'model.failed'}
                or rows[0].get('purpose') != rows[1].get('purpose')):
            raise ValueError('model operation lacks its exact start/terminal journal pair')
        start, end = rows
        start_epoch, end_epoch = epoch(start.get('timestamp')), epoch(end.get('timestamp'))
        if start_epoch > end_epoch:
            raise ValueError('model journal interval is reversed')
        pairs.append({'operation': operation, 'start': start, 'end': end,
            'startEpoch': start_epoch, 'endEpoch': end_epoch})
    ordinary = [pair for pair in pairs if pair['start'].get('purpose') is None]
    if not ordinary:
        raise ValueError('no ordinary parent model failure is recorded')
    target = max(ordinary, key=lambda pair: pair['start']['sequence'])
    failure = target['end']
    if (failure['type'] != 'model.failed' or failure.get('failureKind') != 'provider_error'
            or target['start']['sequence'] <= after_sequence):
        raise ValueError('latest ordinary parent request is not the post-review API failure')
    following = [pair for pair in pairs if pair['start']['sequence'] > failure['sequence']]
    if any(pair['start'].get('purpose') != 'context_compaction' for pair in following):
        raise ValueError('ordinary model work continued after the claimed terminal failure')

    columns = ('event', 'actorId', 'role', 'parentToolOperationId', 'subagentId', 'errorClass',
        'errorPhase', 'httpStatus', 'providerStartedEpoch', 'providerTerminalEpoch', 'toolNames',
        'modelOperationId', 'operationId')
    with closing(sqlite3.connect(safe_path(root, 'transport.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT call,' + ','.join("json_extract(data, '$." + key + "')" for key in columns)
            + ' FROM requests ORDER BY call').fetchall()
    physical = [{'call': row[0], **dict(zip(columns, row[1:]))} for row in rows]
    if not physical or any(row['event'] not in {'completed', 'failed'} for row in physical):
        raise ValueError('physical ledger contains missing or in-flight terminal evidence')

    def inside(row, pair):
        started, ended = row['providerStartedEpoch'], row['providerTerminalEpoch']
        if (type(started) not in (int, float) or type(ended) not in (int, float)
                or not math.isfinite(started) or not math.isfinite(ended) or started > ended):
            return False
        # RunJournal truncates UTC timestamps to milliseconds. This tolerance
        # accounts only for that representation, not a free timing window.
        return pair['startEpoch'] - .001 <= started <= ended <= pair['endEpoch'] + .001

    def associated(pair):
        matches = [row for row in physical if inside(row, pair)]
        if len(matches) != 1:
            raise ValueError('model interval does not identify exactly one physical request')
        row = matches[0]
        if (row['actorId'] != 'main' or row['role'] != 'main' or row['subagentId'] is not None
                or row['parentToolOperationId'] is not None
                or row['event'] != pair['end']['type'].split('.')[1]):
            raise ValueError('physical request is not the journaled main actor and outcome')
        if any(row[key] is not None and row[key] != pair['operation']
               for key in ('modelOperationId', 'operationId')):
            raise ValueError('physical model operation identity contradicts the journal')
        return row

    original = associated(target)
    tool_names = json.loads(original['toolNames']) if isinstance(original['toolNames'], str) else original['toolNames']
    if (original['errorClass'] != error[1] or original['errorPhase'] not in {'headers', 'stream'}
            or not isinstance(tool_names, list) or 'executor_check' not in tool_names
            or not {'write_file', 'edit_file'}.intersection(tool_names)):
        raise ValueError('physical failure does not bind the host error and original Executor capability')
    auxiliaries = [associated(pair) for pair in following]
    if ({row['call'] for row in physical if row['call'] > original['call']}
            != {row['call'] for row in auxiliaries} or len(auxiliaries) != len({row['call'] for row in auxiliaries})):
        raise ValueError('later physical work is not exclusively authenticated compaction')
    return {'errorClass': error[1], 'originalErrorConvertedToPartial': True,
        'parentMessagesTailSha256': hashlib.sha256(canonical(messages[-2:])).hexdigest(),
        'journalModelOperationId': target['operation'], 'journalRunId': failure['runId'],
        'journalFailureSequence': failure['sequence'], 'physicalCall': original['call'],
        'physicalHttpStatus': original['httpStatus'],
        'associationMethod': ('matching_operation_id_and_unique_time_envelope'
            if original['modelOperationId'] is not None or original['operationId'] is not None
            else 'legacy_unique_time_envelope'),
        'directOperationIdAvailable': original['modelOperationId'] is not None or original['operationId'] is not None,
        'excludedCompactionPhysicalCalls': [row['call'] for row in auxiliaries]}


def ledger_content_sha256(path: Path) -> str:
    """Hash an opaque, consistent SQLite view including committed WAL content.

    Raw data strings are framed and hashed; their model text is never interpreted,
    printed or injected. No PRAGMA checkpoint, backup, admission or database write.
    """
    digest = hashlib.sha256()
    queries = (
        ('settings', 'SELECT id,config FROM settings ORDER BY id'),
        ('requests', 'SELECT call,status,charge_tokens,data FROM requests ORDER BY call'),
        ('admission_rejections', 'SELECT rejection,data FROM admission_rejections ORDER BY rejection'),
        ('sqlite_sequence', 'SELECT name,seq FROM sqlite_sequence ORDER BY name'),
    )
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for name, query in queries:
            for value in (name, *[row[0] for row in db.execute(
                    'SELECT sql FROM sqlite_master WHERE name=?', (name,))]):
                raw = canonical(value)
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
            for row in db.execute(query):
                raw = canonical(row)
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
    return digest.hexdigest()


def terminal_evidence(predecessor: Path, document: dict, execution: dict,
                      messages: list[dict], prior_identity: dict, *,
                      revalidate_approved: bool = False,
                      resume_host_failure: bool = False,
                      resume_model_failure: bool = False,
                      recover_rejected_coverage: bool = False, spec=None) -> tuple[list[dict], dict]:
    """Classify a real terminal using host/journal/transport evidence, not prose.

    Results are used only as a completed-suite receipt; no oracle result, failed
    node, source, request or response is a recovery instruction.
    """
    root = predecessor / 'private' / TRIAL_ID
    result_path = safe_path(predecessor, 'results.json')
    if not result_path.is_file():
        raise ValueError('predecessor suite must finish before recovery')
    results = json.loads(result_path.read_bytes())
    rows = results.get('trials')
    if (results.get('manifestSha256') != sha_file(predecessor / 'manifest.json')
            or results.get('expectedTrials') != 1 or not isinstance(rows, list) or len(rows) != 1
            or rows[0].get('id') != TRIAL_ID
            or (type(rows[0].get('success')) is not bool if revalidate_approved else rows[0].get('success') is not False)
            or rows[0].get('errorClass') != execution.get('errorClass')):
        raise ValueError('terminal suite receipt is incomplete or inconsistent')
    process_cleanup = rows[0].get('processCleanup')
    judge_cleanup = rows[0].get('judgement')
    if (not isinstance(process_cleanup, dict) or process_cleanup.get('cleanupComplete') is not True
            or not isinstance(judge_cleanup, dict) or judge_cleanup.get('cleanupComplete') is not True):
        raise ValueError('both execution and terminal judgement cleanup must be complete')
    events, model_events = [], []
    for path in sorted((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson')):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get('type') in {'model.started', 'model.completed', 'model.failed'}:
                payload = event.get('payload', {})
                if not isinstance(event.get('runId'), str) or type(event.get('sequence')) is not int:
                    raise ValueError('invalid parent model journal identity')
                model_events.append({key: event.get(key) for key in ('runId', 'sequence', 'type', 'timestamp')}
                    | {key: payload.get(key) for key in ('operationId', 'failureKind', 'purpose')})
            if event.get('type') not in {'implementation.completion_review_started', 'implementation.completion_decided'}:
                continue
            payload = event.get('payload', {})
            if (payload.get('executorId') != document['executorId']
                    or type(payload.get('attempt')) is not int
                    or not 1 <= payload['attempt'] <= document['attempt']
                    or (payload.get('status') == 'approved' and not revalidate_approved)
                    or not isinstance(event.get('runId'), str)
                    or type(event.get('sequence')) is not int):
                raise ValueError('terminal journal contradicts unapproved host checkpoint')
            events.append({key: event.get(key) for key in ('runId', 'sequence', 'type')}
                | {key: payload.get(key) for key in ('executorId', 'attempt', 'patchDigest', 'status', 'canContinue', 'continuesSameExecutor')})
    if events and len({x['runId'] for x in events}) != 1:
        raise ValueError('missing or ambiguous parent completion journal')
    events.sort(key=lambda item: item['sequence'])
    if len({x['sequence'] for x in events}) != len(events):
        raise ValueError('duplicate completion journal sequence')
    starts = [x for x in events if x['type'] == 'implementation.completion_review_started']
    if events and (not starts or any(starts[-1][key] != document[name] for key, name in (
            ('executorId', 'executorId'), ('attempt', 'attempt'), ('patchDigest', 'candidateDigest')))):
        raise ValueError('last started review does not bind the final host checkpoint')
    last = events[-1] if events else None
    if last and (last['attempt'] != document['attempt'] or last['patchDigest'] != document['candidateDigest']):
        raise ValueError('latest completion evidence is stale')
    if not events and any(prior_identity.get(key) != document[key]
                          for key in ('executorId', 'attempt', 'candidateDigest')):
        raise ValueError('no-review segment must preserve its exact frozen input identity')
    physical = last_transport_metadata(root)
    error_class = execution.get('errorClass')
    details = {'originalErrorClass': error_class, 'lastCompletionEvent': last,
        'lastPhysicalCall': physical, 'checkpointSource': 'production_parent_finally',
        'cleanup': {'execution': True, 'judgement': True}, 'hiddenJudgeUsed': False}
    if recover_rejected_coverage and error_class == 'JSONDecodeError':
        from minicode.coverage_rejection_recovery import derive_rejection_recovery
        error = json.loads(safe_path(root, 'error.json').read_bytes())
        frames = [(tuple(Path(frame.get('file', '')).parts[-2:]), frame.get('name'))
                  for frame in error.get('frames', []) if isinstance(frame, dict)
                  and isinstance(frame.get('file'), str) and Path(frame['file']).parent.name == 'minicode']
        if (revalidate_approved or resume_host_failure or resume_model_failure or spec is None
                or error.get('class') != 'JSONDecodeError'
                or not last or last['type'] != 'implementation.completion_review_started'
                or physical is None or physical['event'] != 'completed'
                or rows[0].get('scopePassed') is not True or rows[0].get('unauthorizedChanges') != []
                or rows[0].get('verificationInducedChanges') != []
                or frames[-6:] != [
                    (('minicode', 'completion_coverage_audit.py'), 'checkpoint_context'),
                    (('minicode', 'completion_coverage_audit.py'), 'messages_checkpoint'),
                    (('minicode', 'coverage_audit_state.py'), 'build_coverage_audit_state'),
                    (('minicode', 'coverage_audit_state.py'), 'validate_coverage_audit_state'),
                    (('minicode', 'coverage_audit_state.py'), 'restore_evidence_documents'),
                    (('minicode', 'coverage_audit_submission.py'), 'restore_rejection')]):
            raise ValueError('rejected coverage recovery lacks its exact failed checkpoint boundary')
        descriptor, records = coverage_recovery_source(predecessor, predecessor=predecessor, document=document)
        originals = records.get('rejectionRecords')
        if originals is None:
            raise ValueError('rejected coverage recovery lacks an authenticated native rejection')
        _diagnostic, receipt, _derived, _pending = derive_rejection_recovery(document, spec,
            {key: records[key] for key in ('messagesRecord', 'inspectionRecord', 'deliveryRecord')},
            originals, 'sha256:' + descriptor['sourceArchiveSha256'])
        details.update(classification='coverage-checkpoint-failed',
            reason='explicit_truncated_rejection_checkpoint_resume',
            sourceArchiveSha256=descriptor['sourceArchiveSha256'],
            recoveredRejectedCallId=receipt['recoveredRejectedCallId'],
            completionIdentitySource='latest_review_started_and_native_rejection')
        return events, details
    if resume_model_failure:
        reviews = document['reviewState'].get('reviews', [])
        if (revalidate_approved or resume_host_failure or error_class is not None
                or rows[0].get('scopePassed') is not True
                or rows[0].get('unauthorizedChanges') != []
                or rows[0].get('verificationInducedChanges') != []
                or not last or last['type'] != 'implementation.completion_decided'
                or last['status'] not in {'inconclusive', 'changes_required'}
                or (reviews and reviews[-1].get('status') == 'approved')
                or (last['canContinue'] is not None and last['canContinue'] is not True)
                or last['continuesSameExecutor'] is not True):
            raise ValueError('model failure recovery requires an unapproved continuing owner and clean public scope')
        evidence = parent_model_partial_evidence(root, messages, model_events, after_sequence=last['sequence'])
        if evidence['journalRunId'] != last['runId']:
            raise ValueError('model failure belongs to a different parent run')
        details.update(classification='transport-blocked', reason='explicit_parent_model_failure_resume',
            partialTransportEvidence=evidence, completionIdentitySource='latest_unapproved_review_and_parent_failure')
        return events, details
    if resume_host_failure:
        state = document['reviewState']
        pending = state.get('pendingCoverageAudit')
        if (revalidate_approved or not isinstance(error_class, str) or not error_class
                or error_class in {'KeyboardInterrupt', 'SystemExit'}
                or physical is not None or model_events
                or rows[0].get('scopePassed') is not True
                or rows[0].get('unauthorizedChanges') != []
                or rows[0].get('verificationInducedChanges') != []
                or not isinstance(pending, dict) or pending.get('candidateDigest') != document['candidateDigest']
                or any(prior_identity.get(key) != document[key] for key in ('executorId', 'candidateDigest'))):
            raise ValueError('host failure recovery requires an unchanged zero-call pending audit and clean public scope')
        error = json.loads(safe_path(root, 'error.json').read_bytes())
        if error.get('class') != error_class:
            raise ValueError('original host execution error receipt is missing or inconsistent')
        details.update(classification='host-failed', reason='explicit_host_failure_resume',
            predecessorClassification=rows[0].get('classification'), providerCalls=0,
            completionIdentitySource='latest_review_started' if last else 'unchanged_frozen_input_checkpoint')
        return events, details
    if revalidate_approved:
        if (error_class is not None or not last or last['type'] != 'implementation.completion_decided'
                or last['status'] != 'approved' or last['continuesSameExecutor'] is not False
                or physical is None or physical['event'] != 'completed'):
            raise ValueError('explicit revalidation requires a normally completed latest approval')
        details.update(classification='approved-revalidation',
            completionIdentitySource='latest_native_approval',
            reason='public_requirement_revalidation',
            approvalEvidence=approved_review_evidence(root, document))
        return events, details
    if error_class is not None:
        error = json.loads(safe_path(root, 'error.json').read_bytes())
        if error.get('class') != error_class:
            raise ValueError('original execution error receipt is missing or inconsistent')
        if error_class in {'KeyboardInterrupt', 'SystemExit'}:
            details['classification'] = 'operator-aborted'
            details['completionIdentitySource'] = ('latest_review_started' if last
                                                   else 'unchanged_frozen_input_checkpoint')
        elif (error_class in {'EvaluationIdleTimeout', 'TimeoutError', 'EvaluationStreamError',
                'ConnectionError', 'ConnectionResetError', 'BrokenPipeError', 'URLError', 'HTTPError'}
                and physical and physical['event'] == 'failed' and physical['errorClass'] == error_class):
            details['classification'] = 'transport-blocked'
        elif (error_class == 'ScopeDenied' and physical is None
                and last and last['type'] == 'implementation.completion_review_started'
                and all(prior_identity.get(key) == document[key]
                        for key in ('executorId', 'candidateDigest'))
                and isinstance(error.get('frames'), list)
                and [(tuple(Path(frame.get('file', '')).parts[-2:]), frame.get('name'))
                     for frame in error['frames'][-4:]
                     if isinstance(frame, dict) and isinstance(frame.get('file'), str)] == [
                    (('minicode', 'review_probe_execution.py'), 'run_isolated_review_probe'),
                    (('scripts', 'run_three_arm_real.py'), 'verify'),
                    (('scripts', 'three_arm_scope.py'), 'require_verification'),
                    (('scripts', 'three_arm_scope.py'), 'require_command')]):
            # The original grant was checked before the provider or probe ran.
            # Resume only the unchanged typed checkpoint; the new dispatch must
            # still pass the ordinary scope guard, not a replacement grant.
            details['classification'] = 'host-verification-blocked'
            details['completionIdentitySource'] = 'latest_review_started'
            details['boundary'] = 'scoped_review_probe_dispatch'
        else:
            raise ValueError('terminal error is not an evidenced transport interruption')
    elif (last and last['type'] == 'implementation.completion_decided'
            and last['status'] in {'inconclusive', 'changes_required'}
            and last['canContinue'] is False and last['continuesSameExecutor'] is False):
        details['classification'] = 'completion-blocked'
    else:
        if last and last['type'] != 'implementation.completion_review_started':
            raise ValueError('natural terminal lacks a host stopping decision or original transport error')
        evidence = proxy_header_partial_evidence(root, messages, model_events)
        if last and (evidence['journalRunId'] != last['runId']
                     or evidence['journalFailureSequence'] <= last['sequence']):
            raise ValueError('proxy failure belongs to a different parent run')
        details.update(classification='transport-blocked', partialTransportEvidence=evidence,
            completionIdentitySource='latest_review_started' if last else 'unchanged_frozen_input_checkpoint')
    return events, details


def predecessor_chain(predecessor: Path) -> list[Path]:
    """Chronological ledger lineage, authenticated by each frozen checkpoint."""
    chain, seen = [], set()
    current = predecessor.resolve()
    while True:
        if current in seen:
            raise ValueError('cyclic continuation lineage')
        seen.add(current)
        chain.append(current)
        manifest = json.loads((current / 'manifest.json').read_bytes())
        if not manifest.get('crossProcessContinuation'):
            break
        checkpoint_path = current / 'checkpoint.json'
        if sha_file(checkpoint_path) != manifest.get('continuationCheckpointSha256'):
            raise ValueError('predecessor lineage checkpoint changed')
        checkpoint = json.loads(checkpoint_path.read_bytes())
        parent = Path(checkpoint['predecessorDirectory']).resolve()
        if str(parent) != manifest.get('predecessorDirectory'):
            raise ValueError('predecessor lineage directory disagrees')
        for relative, expected in checkpoint['predecessorArtifactSha256'].items():
            if sha_file(safe_path(parent, relative)) != expected:
                raise ValueError('ancestor evidence changed')
        current = parent
    return list(reversed(chain))


_PRETRANSPORT_ABSENT = ('execution-start.json', 'transport.sqlite', 'transport-config.json',
    'workspace.json', 'baseline-files.json', 'segment-start-files.json', 'execution.json', 'error.json',
    'implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
    'implementation-checkpoint-receipt.json', 'journal', 'collaboration.json',
    'transcript.json', 'interrupted-transcript.json', 'response.txt', 'calls.jsonl',
    'completion-review-archive.json', 'completion-review', 'public-fixture-receipts.jsonl', 'subagents.json')


def startup_failure_evidence(output: Path) -> dict:
    """Authenticate a failed frozen child before transport/runtime initialization.

    Missing SQLite alone is never usage evidence. The immutable entrypoint must
    write its start marker before constructing transport or the agent runtime.
    This returns a separate host attestation; it creates no ledger/checkpoint.
    """
    output = output.resolve()
    root = output / 'private' / TRIAL_ID
    manifest = json.loads(safe_path(output, 'manifest.json').read_bytes())
    checkpoint = json.loads(safe_path(output, 'checkpoint.json').read_bytes())
    if (manifest.get('crossProcessContinuation') is not True
            or manifest.get('continuationCheckpointSha256') != sha_file(output / 'checkpoint.json')
            or manifest.get('frozenCodeArchiveSha256') != sha_file(output / 'frozen-code.zip')
            or any(os.path.lexists(root / name) for name in _PRETRANSPORT_ABSENT)):
        raise ValueError('startup failure lacks a frozen pre-transport boundary')
    with zipfile.ZipFile(output / 'frozen-code.zip') as archive:
        entry = 'scripts/run_requirement_completion_checkpoint.py'
        if archive.namelist().count(entry) != 1:
            raise ValueError('startup failure archive lacks one native child entrypoint')
        source = archive.read(entry)
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute_segment']
    if len(functions) != 1:
        raise ValueError('startup failure has no unambiguous frozen child contract')
    body = functions[0].body
    markers = [node for node in body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name) and node.value.func.id in {'write_json', 'private_json'}
        and node.value.args and isinstance(node.value.args[0], ast.BinOp)
        and isinstance(node.value.args[0].op, ast.Div) and isinstance(node.value.args[0].left, ast.Name)
        and node.value.args[0].left.id == 'root' and isinstance(node.value.args[0].right, ast.Constant)
        and node.value.args[0].right.value == 'execution-start.json']
    transports = [node for node in body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name) and node.value.func.id == 'EvaluationTransport']
    runtime_calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == 'create_agent_turn_runtime')
             or (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 'runtime' and node.func.attr == 'execute'))]
    if (len(markers) != 1 or len(transports) != 1 or not runtime_calls
            or not markers[0].lineno < transports[0].lineno < min(node.lineno for node in runtime_calls)):
        raise ValueError('frozen child does not prove the pre-transport marker ordering')
    cleanup = json.loads(safe_path(root, 'process-cleanup.json').read_bytes())
    trial_path = output / 'trials' / (TRIAL_ID + '.json')
    trial = json.loads(safe_path(output, str(trial_path.relative_to(output))).read_bytes())
    if (cleanup.get('cleanupComplete') is not True or cleanup.get('freezeComplete') is not True
            or cleanup.get('killConfirmComplete') is not True
            or any(cleanup.get(key) for key in ('survivorPids', 'unconfirmedPids', 'evidenceErrors',
                'processScanErrors', 'identityMismatches'))
            or type(cleanup.get('rootPid')) is not int or cleanup['rootPid'] <= 0
            or trial.get('id') != TRIAL_ID or trial.get('success') is not False
            or trial.get('errorClass') not in {'HardTimeout', 'ChildFailure'}
            or trial.get('processCleanup') != cleanup or type(trial.get('modelCalls')) is not int
            or trial['modelCalls'] != 0
            or trial.get('judgement') is not None
            or type(trial.get('processSeconds')) not in (int, float)
            or not math.isfinite(trial['processSeconds']) or trial['processSeconds'] < 0):
        raise ValueError('startup failure lacks a clean actual failed terminal receipt')
    if sha_file(output / 'private/host-continuation.json') != checkpoint['hostContinuationSha256']:
        raise ValueError('startup failure prepared checkpoint changed')
    if sha_file(output / 'private/parent-context.json') != checkpoint['parentContextSha256']:
        raise ValueError('startup failure prepared parent context changed')
    paths = [output / 'manifest.json', output / 'checkpoint.json', output / 'frozen-code.zip',
        output / 'private/host-continuation.json', output / 'private/parent-context.json',
        root / 'process-cleanup.json', trial_path]
    recovery = checkpoint.get('coverageAuditRecovery')
    recovery_identity = None
    if recovery is not None:
        descriptor_path = safe_path(output, recovery['file'])
        if sha_file(descriptor_path) != recovery['sha256']:
            raise ValueError('startup failure prepared recovery changed')
        descriptor = json.loads(descriptor_path.read_bytes())
        recovery_identity = {key: value for key, value in descriptor.items() if key != 'generatedRecords'}
        paths.append(descriptor_path)
        for metadata in descriptor['generatedRecords'].values():
            path = safe_path(output, metadata['file'])
            if sha_file(path) != metadata['sha256']:
                raise ValueError('startup failure derived recovery changed')
            paths.append(path)
    for name in ('process.log', 'transport-summary.json', 'completion-summary.json'):
        path = root / name
        if path.exists():
            paths.append(path)
    return {'startupFailureVersion': 1, 'kind': 'frozen_child_pretransport_failure',
        'sourceDirectory': str(output), 'predecessorDirectory': checkpoint['predecessorDirectory'],
        'identity': checkpoint['identity'], 'hostContinuationSha256': checkpoint['hostContinuationSha256'],
        'parentContextSha256': checkpoint['parentContextSha256'], 'workspace': checkpoint['workspace'],
        'spec': checkpoint['spec'], 'originalModelPolicy': checkpoint['originalModelPolicy'],
        'recoveryIdentity': recovery_identity,
        'sourceArtifactSha256': {str(path.relative_to(output)): sha_file(path) for path in paths},
        'entrypointProof': {'sourceSha256': hashlib.sha256(source).hexdigest(),
            'startMarkerLine': markers[0].lineno, 'transportConstructorLine': transports[0].lineno,
            'firstRuntimeCallLine': min(node.lineno for node in runtime_calls),
            'absentMarkers': list(_PRETRANSPORT_ABSENT)},
        'modelCalls': 0, 'usageBasis': 'frozen_entrypoint_precedes_transport_and_runtime_initialization',
        'physicalLedgerCreated': False, 'nativeCheckpointCreated': False,
        'cleanupComplete': True, 'errorClass': trial['errorClass'],
        'durationSeconds': trial['processSeconds'],
        'durationMeaning': 'recorded processSeconds includes parent post-stop validation; not child-only elapsed time'}


def inherited_startup_failures(chain: list[Path]) -> list[dict]:
    records, visited, visiting = {}, set(), set()

    def visit(output: Path) -> None:
        output = output.resolve()
        if output in visiting:
            raise ValueError('cyclic startup failure lineage')
        if output in visited:
            return
        path = output / 'checkpoint.json'
        if not path.exists():
            return
        visiting.add(output)
        checkpoint = json.loads(path.read_bytes())
        sources = set()
        for receipt in checkpoint.get('startupFailureSegments', []):
            source = receipt.get('sourceDirectory')
            if not isinstance(source, str) or startup_failure_evidence(Path(source)) != receipt:
                raise ValueError('inherited startup failure evidence changed')
            if source in sources:
                raise ValueError('duplicate startup failure within one checkpoint')
            sources.add(source)
            if receipt['identity']['executorId'] != checkpoint['identity']['executorId']:
                raise ValueError('startup failure lineage changed logical owner')
            visit(Path(source))
            if source in records and records[source] != receipt:
                raise ValueError('conflicting startup failure lineage')
            records[source] = receipt
        visiting.remove(output)
        visited.add(output)

    for output in chain:
        visit(output)
    return list(records.values())


def bootstrap_terminal_evidence(predecessor: Path, document: dict, execution: dict,
                                messages: list[dict], spec, *, verify_candidate: bool = True) -> tuple[list, dict]:
    """A failed host bootstrap is not a product repair or a cleanup waiver."""
    root = predecessor / 'private' / TRIAL_ID
    # Authenticate the original typed reopen and its historical approved archive.
    prior, _, _ = load_checkpoint(predecessor, verify_candidate=verify_candidate)
    if saved_revalidation_request(predecessor) is None or prior.get('hostBootstrapRecovery') is not None:
        raise ValueError('bootstrap recovery requires an original authenticated approved revalidation')
    initial = recovered_host_document(predecessor)
    if (any(document[key] != initial[key] for key in ('executorId', 'candidateDigest', 'specDigest'))
            or document['attempt'] != initial['attempt'] + 1):
        raise ValueError('bootstrap failure changed the original checkpoint identity')
    old_state, new_state = initial['reviewState'], document['reviewState']
    excluded = {'evidenceDirectory', 'priorEvidenceDirectories'}
    if ({k: v for k, v in old_state.items() if k not in excluded}
            != {k: v for k, v in new_state.items() if k not in excluded}
            or new_state.get('evidenceDirectory') is not None
            or set(new_state.get('priorEvidenceDirectories', []))
                != set(old_state.get('priorEvidenceDirectories', [])) | {old_state['evidenceDirectory']}):
        raise ValueError('bootstrap failure must preserve historical review evidence')
    results = json.loads(safe_path(predecessor, 'results.json').read_bytes())
    rows = results.get('trials')
    cleanup = json.loads(safe_path(root, 'process-cleanup.json').read_bytes())
    if (execution.get('errorClass') is not None or results.get('manifestSha256') != sha_file(predecessor / 'manifest.json')
            or results.get('expectedTrials') != 1 or not isinstance(rows, list) or len(rows) != 1
            or rows[0].get('id') != TRIAL_ID or rows[0].get('success') is not False
            or rows[0].get('errorClass') is not None or rows[0].get('processCleanup') != cleanup
            or rows[0].get('judgement') is not None
            or rows[0].get('regressionTest', {}).get('status') != 'not_run_agent_cleanup_or_workspace'):
        raise ValueError('bootstrap terminal receipt is incomplete or involved terminal execution')
    if (cleanup.get('cleanupComplete') is not False
            or cleanup.get('processScanErrors') != ['freeze:PermissionError', 'kill-confirm:PermissionError']
            or any(cleanup.get(k) for k in ('evidenceErrors', 'identityMismatches', 'survivorPids',
                'lateDiscoveredPids', 'stoppedPids', 'killedPids', 'lastSeenProcesses', 'finalScanProcesses'))
            or cleanup.get('unconfirmedPids') != [cleanup.get('rootPid')]
            or any(item.get('sent') is not False for item in cleanup.get('signalEvents', []))
            or any(item.get('scanCount') != 0 for item in cleanup.get('phaseTimings', {}).values())):
        raise ValueError('only an unexecuted permission-denied process scan can be re-attested')
    feedback = document.get('completionFeedback')
    if (not isinstance(feedback, dict) or not feedback.get('_implementation_completion_feedback')
            or feedback not in messages):
        raise ValueError('bootstrap needs the real native public-check feedback')
    try:
        receipt = json.loads(feedback['content'].split('\nCompletion evidence:\n', 1)[1])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError('bootstrap public-check feedback is malformed') from error
    if (any(receipt.get(k) != document[v] for k, v in (
            ('executorId', 'executorId'), ('attempt', 'attempt'), ('patchDigest', 'candidateDigest')))
            or receipt.get('reason') != 'public_checks_not_current_and_passed'
            or receipt.get('status') != 'changes_required'
            or receipt.get('continuesSameExecutor') is not True):
        raise ValueError('bootstrap feedback is not the current host verification failure')
    checks = receipt.get('checks', {})
    evidence = checks.get('checks')
    if (checks.get('status') != 'inconclusive' or checks.get('patchDigest') != document['candidateDigest']
            or not isinstance(evidence, list) or len(evidence) != len(spec.checks)):
        raise ValueError('bootstrap public checks are missing')
    for actual, expected in zip(evidence, spec.checks):
        proof = actual.get('evidence', {})
        if (actual.get('checkId') != expected.check_id or actual.get('argv') != list(expected.argv)
                or actual.get('status') != 'inconclusive' or actual.get('patchDigest') != document['candidateDigest']
                or proof.get('checkId') != expected.check_id or proof.get('kind') != expected.kind
                or proof.get('argv') != list(expected.argv) or proof.get('status') != 'inconclusive'
                or proof.get('failureReason') != 'host_sandbox_unavailable'
                or proof.get('exitCode') is not None or proof.get('durationMs') != 0
                or any(proof.get(k, {}).get('byteCount') != 0 or proof.get(k, {}).get('preview') != ''
                    or proof.get(k, {}).get('previewBase64') != '' for k in ('stdout', 'stderr'))):
            raise ValueError('bootstrap public verification actually ran or has another failure')
    paths = sorted((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'))
    if len(paths) != 1:
        raise ValueError('bootstrap needs one actual parent journal')
    events = [json.loads(line) for line in paths[0].read_text().splitlines()]
    if (len({e.get('runId') for e in events}) != 1
            or len({e.get('sequence') for e in events}) != len(events)
            or any(e.get('type', '').startswith(('tool.', 'implementation.completion_review')) for e in events)):
        raise ValueError('bootstrap must not have executed tools or entered review')
    decisions = [e for e in events if e['type'] == 'implementation.completion_decided']
    models = [e for e in events if e['type'] in {'model.started', 'model.completed', 'model.failed'}]
    if (len(decisions) != 1 or any(decisions[0]['payload'].get(k) != receipt[k]
            for k in ('executorId', 'attempt', 'patchDigest', 'status'))
            or decisions[0]['payload'].get('publicChecksStatus') != 'inconclusive'
            or len(models) != 2 or [m['type'] for m in models] != ['model.started', 'model.failed']
            or not models[0]['payload'].get('operationId')
            or models[0]['payload']['operationId'] != models[1]['payload'].get('operationId')
            or models[1]['payload'].get('failureKind') != 'provider_error'):
        raise ValueError('bootstrap journal contains executed or ambiguous model work')
    with closing(sqlite3.connect((root / 'transport.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        physical = [json.loads(row[0]) for row in db.execute('SELECT data FROM requests ORDER BY call')]
    if len(physical) != 1:
        raise ValueError('bootstrap recovery requires exactly one pre-response failed request')
    request = physical[0]
    if (request.get('event') != 'failed' or request.get('errorClass') != 'URLError'
            or request.get('errorPhase') != 'headers' or request.get('streamDone') is not False
            or any(request.get(k) is not None for k in ('httpStatus', 'firstByteSeconds', 'firstContentSeconds',
                'lastByteSeconds', 'providerUsage'))
            or request.get('usage') not in (None, {'input_tokens': None, 'output_tokens': None,
                'cache_read_tokens': None, 'cache_creation_tokens': None, 'source': 'unavailable'})):
        raise ValueError('bootstrap model request received output or failed outside initial transport')
    if (len(messages) < 2 or messages[-1].get('role') != 'assistant' or messages[-1].get('_partial') is not True
            or messages[-2].get('role') != 'assistant'
            or not messages[-2].get('content', '').startswith('Model API error (URLError): ')):
        raise ValueError('bootstrap actual parent checkpoint lacks its original transport failure')
    from scripts.three_arm_processes import _recorded
    identities, errors = _recorded(safe_path(root, 'processes.jsonl'))
    if errors or not identities or identities.get(cleanup.get('rootPid')) != cleanup.get('rootPid'):
        raise ValueError('bootstrap controlled process identities are incomplete')
    bound_files = [predecessor / name for name in ('manifest.json', 'checkpoint.json', 'results.json')]
    bound_files += [root / name for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
        'implementation-checkpoint-receipt.json', 'execution.json', 'process-cleanup.json', 'processes.jsonl', 'transport.sqlite')]
    bound_files += paths
    return [], {'classification': 'host-bootstrap-blocked', 'reason': 'public_requirement_revalidation',
        'checkpointSource': 'production_parent_finally', 'hiddenJudgeUsed': False,
        'originalCleanupComplete': False, 'originalJudgementExecuted': False,
        'sourceSha256': {str(p.relative_to(predecessor)): sha_file(p) for p in bound_files},
        'controlledIdentities': [{'pid': pid, 'pgid': pgid} for pid, pgid in sorted(identities.items())],
        'historicalApprovalDigest': revalidation_request(document)['approvalDigest']}


def attest_bootstrap_cleanup(predecessor: Path, output: Path) -> None:
    """Run with host process visibility; append a receipt, never signal or rewrite."""
    from scripts.three_arm_processes import _process_table, _candidates
    predecessor, output = predecessor.resolve(), output.resolve()
    if output.exists():
        raise ValueError('cleanup attestation is append-only')
    inputs = predecessor_inputs(predecessor, host_bootstrap=True)
    identities = {row['pid']: row['pgid'] for row in inputs['terminalEvidence']['controlledIdentities']}
    table = _process_table()
    groups = set(identities.values())
    matches = _candidates(table, identities)
    # Even a reused recorded PID is ambiguous; never accept it by assuming exit.
    matches.update({p.pid: p.pgid for p in table.values() if p.pid in identities or p.pgid in groups})
    if not table or matches:
        raise ValueError('controlled processes are still present or full process scan is unavailable')
    private_json(output, {'attestationVersion': 1, 'kind': 'host_bootstrap_cleanup_absence',
        'predecessorDirectory': str(predecessor), 'observedEpoch': time.time(),
        'scanCommand': ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat='],
        'scannedProcessCount': len(table), 'controlledProcessesPresent': [],
        'signalsSent': False, 'originalCleanupComplete': False, 'cleanupConfirmedAbsent': True,
        'identity': inputs['identity'], 'terminalEvidence': inputs['terminalEvidence']})


def validate_bootstrap_attestation(path: Path, predecessor: Path, inputs: dict) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('bootstrap cleanup attestation must be a private regular host receipt')
    receipt = json.loads(path.read_bytes())
    if (set(receipt) != {'attestationVersion', 'kind', 'predecessorDirectory', 'observedEpoch', 'scanCommand',
            'scannedProcessCount', 'controlledProcessesPresent', 'signalsSent', 'originalCleanupComplete',
            'cleanupConfirmedAbsent', 'identity', 'terminalEvidence'}
            or receipt['attestationVersion'] != 1 or receipt['kind'] != 'host_bootstrap_cleanup_absence'
            or receipt['predecessorDirectory'] != str(predecessor.resolve())
            or type(receipt['observedEpoch']) not in (int, float) or not 0 < receipt['observedEpoch'] <= time.time()
            or receipt['scanCommand'] != ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat=']
            or type(receipt['scannedProcessCount']) is not int or receipt['scannedProcessCount'] < 1
            or receipt['controlledProcessesPresent'] != [] or receipt['signalsSent'] is not False
            or receipt['originalCleanupComplete'] is not False or receipt['cleanupConfirmedAbsent'] is not True
            or receipt['identity'] != inputs['identity'] or receipt['terminalEvidence'] != inputs['terminalEvidence']):
        raise ValueError('bootstrap cleanup attestation does not authenticate this terminal checkpoint')


def predecessor_inputs(predecessor: Path, *, verify_candidate: bool = True,
                       revalidate_approved: bool = False, host_bootstrap: bool = False,
                       resume_host_failure: bool = False, resume_model_failure: bool = False,
                       recover_rejected_coverage: bool = False) -> dict:
    root = predecessor / 'private' / TRIAL_ID
    manifest = json.loads((predecessor / 'manifest.json').read_bytes())
    if manifest['schedule'] != diagnostic.schedule() or transport_change_policy(manifest['limits']) != LIMITS:
        raise ValueError('different task schedule or unapproved transport change')
    execution = json.loads((root / 'execution.json').read_bytes())
    if not host_bootstrap and json.loads((root / 'process-cleanup.json').read_bytes()).get('cleanupComplete') is not True:
        raise ValueError('predecessor cleanup incomplete')
    if sha_file(predecessor / 'tasks.json') != manifest['taskSha256']:
        raise ValueError('frozen task bytes changed')
    if sha_file(predecessor / 'frozen-code.zip') != manifest['frozenCodeArchiveSha256']:
        raise ValueError('predecessor frozen source archive changed')
    cases = [x for x in json.loads((predecessor / 'tasks.json').read_bytes())['cases'] if x['id'] == diagnostic.CASE]
    if len(cases) != 1:
        raise ValueError('ambiguous public task identity')
    case = cases[0]
    workspace = Path(json.loads((root / 'workspace.json').read_bytes())['path'])
    document, messages, messages_path = host_checkpoint_inputs(root, implementation_spec(case), workspace,
        verify_candidate=verify_candidate)
    prior = json.loads((predecessor / 'checkpoint.json').read_bytes())
    if (document['executorId'] != prior['identity']['executorId']
            or document['attempt'] < prior['identity']['attempt']):
        raise ValueError('host checkpoint does not continue its original owner')
    if host_bootstrap:
        events, terminal = bootstrap_terminal_evidence(predecessor, document, execution, messages,
            implementation_spec(case), verify_candidate=verify_candidate)
    else:
        events, terminal = terminal_evidence(predecessor, document, execution, messages, prior['identity'],
            revalidate_approved=revalidate_approved, resume_host_failure=resume_host_failure,
            resume_model_failure=resume_model_failure, recover_rejected_coverage=recover_rejected_coverage,
            spec=implementation_spec(case))
    request = None
    if revalidate_approved or host_bootstrap:
        from minicode.implementation_continuation import HostImplementationContinuation
        request = revalidation_request(document)
        HostImplementationContinuation.for_revalidation(document,
            spec=implementation_spec(case), messages=messages, request=request)
    chain = predecessor_chain(predecessor)
    identity = {'executorId': document['executorId'], 'attempt': document['attempt'],
        'candidateDigest': document['candidateDigest'], 'completionEvents': events,
        'source': 'production_parent_finally'}
    return {'root': root, 'manifest': manifest, 'case': case, 'messages': messages,
        'parentContextPath': messages_path, 'hostDocument': document, 'workspace': workspace,
        'identity': identity, 'candidateFiles': candidate_manifest(workspace, case['authorized_paths']),
        'predecessorChain': chain,
        'predecessorClassification': terminal['classification'], 'terminalEvidence': terminal,
        'revalidationRequest': request}


def prepare(output: Path, predecessor: Path = DEFAULT_PREDECESSOR, *, revalidate_approved: bool = False,
            recover_host_bootstrap: bool = False, cleanup_attestation: Path | None = None,
            resume_host_failure: bool = False, resume_model_failure: bool = False,
            recover_coverage_audit_from: Path | None = None,
            startup_failure_from: Path | None = None) -> None:
    output, predecessor = output.resolve(), predecessor.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('continuation preparation requires a new empty directory')
    if sum((revalidate_approved, recover_host_bootstrap, resume_host_failure, resume_model_failure)) > 1:
        raise ValueError('choose only one explicit recovery mode')
    if (cleanup_attestation is not None) != recover_host_bootstrap:
        raise ValueError('host bootstrap recovery requires its separate cleanup attestation')
    if recover_coverage_audit_from is not None and (revalidate_approved or recover_host_bootstrap):
        raise ValueError('coverage recovery cannot reopen an approved audit')
    inputs = predecessor_inputs(predecessor, revalidate_approved=revalidate_approved,
        host_bootstrap=recover_host_bootstrap, resume_host_failure=resume_host_failure,
        resume_model_failure=resume_model_failure,
        recover_rejected_coverage=recover_coverage_audit_from is not None
            and recover_coverage_audit_from.resolve() == predecessor)
    if recover_host_bootstrap:
        validate_bootstrap_attestation(cleanup_attestation, predecessor, inputs)
    root = inputs['root']
    reopening = revalidate_approved or recover_host_bootstrap
    draft = (None if reopening or recover_coverage_audit_from is not None else
             recover_submission_draft(root, inputs['hostDocument'], implementation_spec(inputs['case'])))
    coverage = None if reopening or recover_coverage_audit_from is not None else recover_coverage_audit(root, inputs['hostDocument'])
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    for name in ('tasks.json', 'public-cases.json', 'source-provenance.json', 'public-preflight/receipt.json'):
        private_write(output / name, (predecessor / name).read_bytes())
    plan = REVALIDATION_PLAN if reopening else ANALYSIS_PLAN
    if recover_host_bootstrap:
        plan = plan.replace('The predecessor remains terminal, production-approved and immutable.',
            'The predecessor remains terminal, bootstrap-blocked and immutable; its earlier approval is historical.')
        plan += '\nThe predecessor bootstrap failed before public verification or model output. Its original cleanup failure is preserved; a separate host process-scan attestation certifies absence. The current native context is retried through typed public revalidation, with all predecessor usage retained.\n'
    if resume_model_failure:
        plan += '\nThe host explicitly resumes a normally returned but incomplete parent after an authenticated API failure. Native owner, candidate, partial receipt, main journal operation and physical provider interval are preserved. Legacy ledgers have no operation IDs: unique time-envelope association is recorded honestly, and later purpose-bound compaction calls do not replace the original parent failure.\n'
    if recover_coverage_audit_from is not None:
        plan += '\nThe host explicitly authenticates a selected ancestor archive to recover delivered error provenance in the same pending inspection epoch. A typed overlay preserves the original checkpoint and parent messages, verifies an exact child-dialogue prefix, and retains existing successful delivery credit. This grants no completion approval and adds no operator task instructions.\n'
    if startup_failure_from is not None:
        plan += '\nA separate failed startup attempt is retained with its original artifacts and recorded elapsed time. Its frozen entrypoint and absent start boundary establish that transport and the agent runtime were not initialized. Retry uses the same original native predecessor, parent context and authenticated overlay; no native checkpoint or empty physical ledger is invented for the failed attempt.\n'
    private_write(output / 'analysis-plan.md', plan.encode())
    private_write(output / 'private/parent-context.json', inputs['parentContextPath'].read_bytes())
    private_write(output / 'private/host-continuation.json', (root / 'implementation-checkpoint.json').read_bytes())
    coverage_recovery = (None if recover_coverage_audit_from is None else prepare_coverage_recovery(
        output, recover_coverage_audit_from, predecessor=predecessor, document=inputs['hostDocument'],
        spec=implementation_spec(inputs['case']), messages=inputs['messages']))
    draft_restoration = None
    if draft is not None:
        private_json(output / 'private/pending-submission-draft.json', draft)
        draft_restoration = {'kind': 'unaccepted_native_tool_pair_reference',
            'file': 'private/pending-submission-draft.json',
            'sha256': sha_file(output / 'private/pending-submission-draft.json')}
    coverage_restoration = None
    if coverage is not None:
        private_json(output / 'private/pending-coverage-audit.json', coverage)
        coverage_restoration = {'kind': 'actual_native_coverage_audit_context',
            'file': 'private/pending-coverage-audit.json',
            'sha256': sha_file(output / 'private/pending-coverage-audit.json')}
    approved_revalidation = None
    if reopening:
        private_json(output / 'private/approved-revalidation.json', inputs['revalidationRequest'])
        approved_revalidation = {'kind': 'explicit_public_requirement_revalidation',
            'file': 'private/approved-revalidation.json',
            'sha256': sha_file(output / 'private/approved-revalidation.json')}
    references = {str(p.relative_to(predecessor)): sha_file(p) for p in (
        predecessor / 'manifest.json', predecessor / 'tasks.json', predecessor / 'frozen-code.zip',
        root / 'implementation-checkpoint-receipt.json', root / 'implementation-checkpoint.json',
        root / 'implementation-checkpoint-messages.json', root / 'execution.json',
        root / 'workspace.json', root / 'baseline-files.json', root / 'process-cleanup.json',
        root / 'transport.sqlite', root / 'transport-config.json', predecessor / 'checkpoint.json',
        predecessor / 'results.json')}
    if (root / 'error.json').is_file():
        references[str((root / 'error.json').relative_to(predecessor))] = sha_file(root / 'error.json')
    if recover_host_bootstrap:
        references[str((root / 'processes.jsonl').relative_to(predecessor))] = sha_file(root / 'processes.jsonl')
    for path in (root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'):
        references[str(path.relative_to(predecessor))] = sha_file(path)
    if draft is not None or coverage is not None or revalidate_approved:
        archive = root / 'completion-review-archive.json'
        references[str(archive.relative_to(predecessor))] = sha_file(archive)
        for row in json.loads(archive.read_bytes())['files']:
            path = safe_path(root, row['file'])
            references[str(path.relative_to(predecessor))] = row['sha256']
    checkpoint = {'version': 1, 'kind': 'cross-process-logical-implementation-continuation',
        'createdAt': datetime.now(timezone.utc).isoformat(), 'predecessorDirectory': str(predecessor),
        'predecessorArtifactSha256': references, 'workspace': str(inputs['workspace']),
        'identity': inputs['identity'], 'parentContextSha256': sha_file(output / 'private/parent-context.json'),
        'parentMessageCount': len(inputs['messages']), 'candidateFiles': inputs['candidateFiles'],
        'hostContinuationSha256': sha_file(output / 'private/host-continuation.json'),
        'predecessorLedgerRoots': [str(p / 'private' / TRIAL_ID) for p in inputs['predecessorChain']],
        'ancestorLedgerContentSha256': {str(p / 'private' / TRIAL_ID / 'transport.sqlite'): ledger_content_sha256(p / 'private' / TRIAL_ID / 'transport.sqlite') for p in inputs['predecessorChain']},
        'ancestorLedgerSha256': {str(p / 'private' / TRIAL_ID / 'transport.sqlite'): sha_file(p / 'private' / TRIAL_ID / 'transport.sqlite') for p in inputs['predecessorChain']},
        'transportChange': transport_change_record(inputs['manifest']['limits']), 'predecessorClassification': inputs['predecessorClassification'],
        'terminalEvidence': inputs['terminalEvidence'],
        'migrationRestoration': ('native_host_state' if inputs['hostDocument'].get('migrationState') is not None
                                else 'fresh_migration_from_retained_review_state_no_child_context_saved'),
        'spec': asdict(implementation_spec(inputs['case'])),
        'originalModelPolicy': {key: inputs['manifest'].get(key) for key in ('model', 'temperature', 'reasoningEffortPolicy',
            'routeSha256', 'resolvedTransportRouteSha256', 'requestPolicy', 'limits', 'roleTurnLimits',
            'commonRequirementSha256', 'explicitRecipeSha256')},
        'unrestoredState': ['original Python stack', 'in-flight reviewer conversation', 'in-memory compactor controller',
                           'approved review cache', 'public-check cache'],
        'privateJudgeUsedForRecovery': False, 'freshBaselineHydration': False}
    if draft_restoration is not None:
        checkpoint['submissionDraftRestoration'] = draft_restoration
    if coverage_restoration is not None:
        checkpoint['coverageAuditRestoration'] = coverage_restoration
    if coverage_recovery is not None:
        checkpoint['coverageAuditRecovery'] = coverage_recovery
    if inputs['predecessorClassification'] == 'coverage-checkpoint-failed':
        checkpoint['coverageCheckpointFailureContinuation'] = {
            'version': 1, 'kind': 'explicit_truncated_rejection_checkpoint_resume'}
    startup_failures = inherited_startup_failures([*inputs['predecessorChain'],
        *([startup_failure_from] if startup_failure_from is not None else [])])
    if startup_failure_from is not None:
        failure = startup_failure_evidence(startup_failure_from)
        for key in ('predecessorDirectory', 'identity', 'hostContinuationSha256', 'parentContextSha256',
                    'workspace', 'spec', 'originalModelPolicy'):
            if canonical(failure[key]) != canonical(checkpoint[key]):
                raise ValueError('startup retry must retain the failed prepared inputs and native predecessor')
        identity = None
        if coverage_recovery is not None:
            descriptor = json.loads((output / coverage_recovery['file']).read_bytes())
            identity = {key: value for key, value in descriptor.items() if key != 'generatedRecords'}
        if failure['recoveryIdentity'] != identity:
            raise ValueError('startup retry must retain the original authenticated coverage overlay')
        if any(row['sourceDirectory'] == failure['sourceDirectory'] for row in startup_failures):
            raise ValueError('startup failure is already present in cumulative lineage')
        startup_failures.append(failure)
        checkpoint['startupFailureRetry'] = {'sourceDirectory': failure['sourceDirectory']}
    if startup_failures:
        checkpoint['startupFailureSegments'] = startup_failures
    if approved_revalidation is not None:
        checkpoint['approvedRevalidation'] = approved_revalidation
    if resume_host_failure:
        checkpoint['hostFailureContinuation'] = {'version': 1, 'kind': 'authenticated_zero_call_host_failure'}
    if resume_model_failure:
        checkpoint['modelFailureContinuation'] = {'version': 1, 'kind': 'authenticated_parent_model_failure'}
    if recover_host_bootstrap:
        private_write(output / 'private/bootstrap-cleanup-attestation.json', cleanup_attestation.read_bytes())
        checkpoint['hostBootstrapRecovery'] = {'kind': 'host_bootstrap_failure',
            'file': 'private/bootstrap-cleanup-attestation.json',
            'sha256': sha_file(output / 'private/bootstrap-cleanup-attestation.json')}
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
    request = saved_revalidation_request(output)
    bootstrap = checkpoint.get('hostBootstrapRecovery')
    host_failure = checkpoint.get('hostFailureContinuation')
    model_failure = checkpoint.get('modelFailureContinuation')
    coverage_failure = checkpoint.get('coverageCheckpointFailureContinuation')
    if coverage_failure is not None and (coverage_failure != {
            'version': 1, 'kind': 'explicit_truncated_rejection_checkpoint_resume'}
            or any(value is not None for value in (request, bootstrap, host_failure, model_failure))
            or checkpoint.get('coverageAuditRecovery') is None):
        raise ValueError('invalid explicit rejected coverage checkpoint continuation')
    if host_failure is not None and (host_failure != {'version': 1, 'kind': 'authenticated_zero_call_host_failure'}
            or request is not None or bootstrap is not None or model_failure is not None):
        raise ValueError('invalid explicit host failure continuation mode')
    if model_failure is not None and (model_failure != {'version': 1, 'kind': 'authenticated_parent_model_failure'}
            or request is not None or bootstrap is not None or host_failure is not None):
        raise ValueError('invalid explicit model failure continuation mode')
    if bootstrap is not None:
        if (not isinstance(bootstrap, dict) or set(bootstrap) != {'kind', 'file', 'sha256'}
                or bootstrap['kind'] != 'host_bootstrap_failure'
                or bootstrap['file'] != 'private/bootstrap-cleanup-attestation.json'
                or request is None):
            raise ValueError('invalid host bootstrap restoration contract')
        attestation = safe_path(output, bootstrap['file'])
        if sha_file(attestation) != bootstrap['sha256']:
            raise ValueError('bootstrap cleanup attestation changed')
    inputs = predecessor_inputs(predecessor, verify_candidate=verify_candidate,
        revalidate_approved=request is not None and bootstrap is None, host_bootstrap=bootstrap is not None,
        resume_host_failure=host_failure is not None, resume_model_failure=model_failure is not None,
        recover_rejected_coverage=coverage_failure is not None)
    if bootstrap is not None:
        validate_bootstrap_attestation(attestation, predecessor, inputs)
    if request != inputs['revalidationRequest']:
        raise ValueError('approved revalidation request differs from authenticated predecessor')
    if checkpoint['transportChange'] != transport_change_record(inputs['manifest']['limits']):
        raise ValueError('predecessor transport transition changed')
    if checkpoint['terminalEvidence'] != inputs['terminalEvidence']:
        raise ValueError('predecessor terminal evidence changed')
    if checkpoint['identity'] != inputs['identity'] or checkpoint['workspace'] != str(inputs['workspace']):
        raise ValueError('continuation owner or workspace changed')
    if asdict(implementation_spec(inputs['case'])) != checkpoint['spec']:
        # JSON changes tuple containers to arrays; compare canonical JSON.
        if canonical(asdict(implementation_spec(inputs['case']))) != canonical(checkpoint['spec']):
            raise ValueError('public ImplementationSpec changed')
    if sha_file(output / 'private/parent-context.json') != checkpoint['parentContextSha256']:
        raise ValueError('saved parent context changed')
    if (output / 'private/parent-context.json').read_bytes() != inputs['parentContextPath'].read_bytes():
        raise ValueError('recovery context is not the actual parent snapshot')
    if (sha_file(output / 'private/host-continuation.json') != checkpoint['hostContinuationSha256']
            or (output / 'private/host-continuation.json').read_bytes() != (inputs['root'] / 'implementation-checkpoint.json').read_bytes()):
        raise ValueError('native host continuation checkpoint changed')
    recovered = recovered_host_document(output)
    if checkpoint.get('submissionDraftRestoration') is not None:
        expected_draft = recover_submission_draft(inputs['root'], inputs['hostDocument'], implementation_spec(inputs['case']))
        if recovered['reviewState']['pendingSubmissionDraft'] != expected_draft:
            raise ValueError('recovery draft is not the exact authenticated native submission')
    if [str(p / 'private' / TRIAL_ID) for p in inputs['predecessorChain']] != checkpoint['predecessorLedgerRoots']:
        raise ValueError('physical ledger lineage changed')
    if any(sha_file(Path(path)) != digest for path, digest in checkpoint['ancestorLedgerSha256'].items()):
        raise ValueError('ancestor physical ledger changed')
    if any(ledger_content_sha256(Path(path)) != digest for path, digest in checkpoint['ancestorLedgerContentSha256'].items()):
        raise ValueError('ancestor logical ledger changed')
    if checkpoint.get('coverageAuditRecovery') is not None:
        saved_coverage_recovery(output, spec=implementation_spec(inputs['case']),
            messages=inputs['messages'], document=recovered)
    retry = checkpoint.get('startupFailureRetry')
    if retry is not None and (not isinstance(retry, dict) or set(retry) != {'sourceDirectory'}):
        raise ValueError('invalid explicit startup failure retry')
    failures = inherited_startup_failures([*inputs['predecessorChain'],
        *([Path(retry['sourceDirectory'])] if retry is not None else [])])
    if retry is not None:
        failure = startup_failure_evidence(Path(retry['sourceDirectory']))
        for key in ('predecessorDirectory', 'identity', 'hostContinuationSha256', 'parentContextSha256',
                    'workspace', 'spec', 'originalModelPolicy'):
            if canonical(failure[key]) != canonical(checkpoint[key]):
                raise ValueError('startup retry prepared identity changed')
        identity = None
        if checkpoint.get('coverageAuditRecovery') is not None:
            descriptor = json.loads((output / checkpoint['coverageAuditRecovery']['file']).read_bytes())
            identity = {key: value for key, value in descriptor.items() if key != 'generatedRecords'}
        if failure['recoveryIdentity'] != identity:
            raise ValueError('startup retry authenticated overlay changed')
        if any(row['sourceDirectory'] == failure['sourceDirectory'] for row in failures):
            raise ValueError('startup failure is duplicated in cumulative lineage')
        failures.append(failure)
    if checkpoint.get('startupFailureSegments', []) != failures:
        raise ValueError('startup failure cumulative lineage changed')
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
            or not callable(getattr(HostImplementationContinuation, 'for_revalidation', None))
            or not callable(getattr(HostImplementationContinuation, 'for_coverage_recovery', None))
            or not callable(getattr(ImplementationCompletionReview, 'from_state', None))
            or 'implementation_continuation' not in inspect.signature(create_agent_turn_runtime).parameters
            or 'implementation_continuation' not in inspect.signature(run_agent_turn).parameters
            or 'implementation_continuation=self.implementation_continuation' not in inspect.getsource(AgentTurnRuntime.execute)):
        raise RuntimeError('production continuation integration is unavailable')
    paths = ('minicode/implementation_continuation.py', 'minicode/agent_runtime.py',
             'minicode/completion_review_draft.py', 'minicode/coverage_audit_recovery.py',
             'minicode/agent_loop.py', 'minicode/implementation_completion_review.py')
    return {'version': 1, 'sourceSha256': {name: sha_file(ROOT / name) for name in paths},
            'entrypointSha256': sha_file(ENTRY), 'restoration': 'host capability; cross-process logical owner'}


def freeze(output: Path) -> None:
    checkpoint, inputs, messages = load_checkpoint(output)
    from minicode.implementation_completion_review import ImplementationCompletionReview
    restore = getattr(ImplementationCompletionReview, 'from_state', None)
    if not callable(restore):
        raise RuntimeError('production review restoration is not available')
    capabilities = continuation_capabilities()
    spec = implementation_spec(inputs['case'])
    continuation = restored_continuation(output, spec, messages)
    if checkpoint.get('coverageAuditRecovery') is not None:
        state = continuation.restore_review(spec).state
    else:
        state = recovered_host_document(output)['reviewState']
        restore(spec, state)
    private_json(output / 'private/review-state.json', state)
    manifest = {**inputs['manifest'], 'limits': LIMITS, 'transportChange': checkpoint['transportChange'], 'protocolVersion': PROTOCOL, 'comparison': 'cross-process continuation; not an independent trial',
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
    if (manifest['limits'] != LIMITS or manifest.get('transportChange') != checkpoint['transportChange']
            or any(manifest[key] != inputs['manifest'][key] for key in checkpoint['originalModelPolicy'] if key != 'limits')):
        raise ValueError('continuation model, scope, recipe or budget drift')
    return manifest


def services(output: Path):
    """Private module instances expose the unchanged scope, transport and judge."""
    module = diagnostic._library().runner(output)
    module.HARNESS_FILES = HARNESS_FILES
    return module


def restored_continuation(output: Path, spec, messages: list[dict]):
    from minicode.implementation_continuation import HostImplementationContinuation
    document = recovered_host_document(output)
    recovery = saved_coverage_recovery(output, spec=spec, messages=messages, document=document)
    if recovery is not None:
        return HostImplementationContinuation.for_coverage_recovery(
            document, spec=spec, messages=messages, recovery=recovery)
    request = saved_revalidation_request(output)
    if request is not None:
        return HostImplementationContinuation.for_revalidation(document, spec=spec, messages=messages, request=request)
    return HostImplementationContinuation.from_document(document, spec=spec, messages=messages)


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


def segment_transport_config(root: Path, runtime_config: dict, manifest: dict, execution_epoch: float) -> TransportConfig:
    return TransportConfig(
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
    transport_config = segment_transport_config(root, runtime_config, manifest, execution_epoch)
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
        stack.enter_context(MigrationEvidenceArchive(root))
        stack.enter_context(AdjudicationEvidenceArchive(root))
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


def combined_metering(predecessor_roots: list[Path], current_root: Path, *,
                     startup_failures: list[dict] | None = None,
                     current_startup_failure: dict | None = None) -> dict:
    """Read immutable physical ledgers; never initialize or mutate old databases."""
    records, segments, rejections = [], [], []
    failures = list(startup_failures or [])
    roots = [*predecessor_roots, current_root]
    if current_startup_failure is not None:
        if (current_root != Path(current_startup_failure['sourceDirectory']) / 'private' / TRIAL_ID
                or (current_root / 'transport.sqlite').exists()
                or startup_failure_evidence(Path(current_startup_failure['sourceDirectory'])) != current_startup_failure):
            raise ValueError('current missing ledger lacks authenticated pre-transport failure')
        roots = predecessor_roots
        failures.append(current_startup_failure)
    if len({row['sourceDirectory'] for row in failures}) != len(failures):
        raise ValueError('duplicate startup failure metering segment')
    for failure in failures:
        if startup_failure_evidence(Path(failure['sourceDirectory'])) != failure:
            raise ValueError('startup failure metering evidence changed')
        if Path(failure['sourceDirectory']) / 'private' / TRIAL_ID in roots:
            raise ValueError('startup failure cannot also count as a physical ledger segment')
    for index, root in enumerate(roots):
        path = root / 'transport.sqlite'
        if not path.is_file():
            raise ValueError('continuation segment is missing its physical ledger')
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT call,data,charge_tokens FROM requests ORDER BY call').fetchall()
            rejections.extend(json.loads(row[0]) for row in db.execute('SELECT data FROM admission_rejections ORDER BY rejection'))
        current = [{'call': call, **json.loads(data), 'chargedTokens': charge, 'continuationSegment': index}
                   for call, data, charge in rows]
        records.extend(current)
        execution = json.loads((root / 'execution.json').read_bytes())
        segments.append({'root': str(root), 'ledgerSha256': sha_file(path), 'ledgerContentSha256': ledger_content_sha256(path), 'modelCalls': len(current),
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
    startup_segments = [{key: row[key] for key in ('sourceDirectory', 'errorClass', 'durationSeconds',
        'durationMeaning', 'modelCalls', 'usageBasis', 'physicalLedgerCreated', 'nativeCheckpointCreated')}
        for row in failures]
    return {'segments': segments, 'startupFailureSegments': startup_segments,
            'startupFailureDurationSeconds': sum(row['durationSeconds'] for row in failures),
            'durationSeconds': sum(row['durationSeconds'] for row in [*segments, *failures]),
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
        code = service.wait_trial_process(
            proc, root, **({"setup_seconds": None} if LIMITS["wallSeconds"] is None else {})
        )
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
    startup_failure = None
    if not (root / 'transport.sqlite').exists():
        startup_failure = startup_failure_evidence(output)
    row.update(crossProcessContinuation=True, samePythonLoop=False,
        originalExecutorId=checkpoint['identity']['executorId'], resumedAfterAttempt=checkpoint['identity']['attempt'],
        predecessorDirectory=checkpoint['predecessorDirectory'], predecessorClassification=checkpoint['predecessorClassification'], transportChange=checkpoint['transportChange'],
        completionMechanism=summary, cumulativeMetering=combined_metering(
            [Path(p) for p in checkpoint['predecessorLedgerRoots']], root,
            startup_failures=checkpoint.get('startupFailureSegments', []), current_startup_failure=startup_failure))
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
    if startup_failure is not None:
        row['classification'] = 'startup-failed'
        row['transportInitialized'] = False
    shared.write_json(output / 'trials' / (TRIAL_ID + '.json'), row)
    service.checkpoint_results(output, manifest)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'freeze', 'validate', 'run', 'child', 'attest-bootstrap-cleanup'))
    parser.add_argument('--predecessor', type=Path, default=DEFAULT_PREDECESSOR)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--revalidate-approved', action='store_true',
        help='Explicitly reopen public requirement review from an authenticated approved checkpoint (prepare only).')
    parser.add_argument('--recover-host-bootstrap', action='store_true',
        help='Retry an unexecuted host bootstrap with a separately authenticated process-absence receipt (prepare only).')
    parser.add_argument('--resume-host-failure', action='store_true',
        help='Explicitly resume an authenticated zero-call host failure with a native pending audit (prepare only).')
    parser.add_argument('--resume-model-failure', action='store_true',
        help='Explicitly resume an unapproved parent whose API error became a native partial receipt (prepare only).')
    parser.add_argument('--recover-coverage-audit-from', type=Path,
        help='Explicitly recover same-audit delivered errors from this authenticated ancestor output (prepare only).')
    parser.add_argument('--startup-failure-from', type=Path,
        help='Explicitly retain a failed pre-transport attempt while retrying its original prepared inputs (prepare only).')
    parser.add_argument('--cleanup-attestation', type=Path,
        help='Append-only host receipt created by attest-bootstrap-cleanup with full process visibility.')
    args = parser.parse_args()
    if args.revalidate_approved and args.mode != 'prepare':
        parser.error('--revalidate-approved is an explicit prepare action; later modes use its frozen request')
    if (args.recover_host_bootstrap or args.cleanup_attestation is not None) and args.mode != 'prepare':
        parser.error('host bootstrap recovery flags are prepare-only')
    if args.resume_host_failure and args.mode != 'prepare':
        parser.error('host failure recovery is prepare-only; later modes use its frozen contract')
    if args.resume_model_failure and args.mode != 'prepare':
        parser.error('model failure recovery is prepare-only; later modes use its frozen contract')
    if args.recover_coverage_audit_from is not None and args.mode != 'prepare':
        parser.error('coverage recovery is prepare-only; later modes use its frozen descriptor')
    if args.startup_failure_from is not None and args.mode != 'prepare':
        parser.error('startup failure retry is prepare-only; later modes use its frozen receipt')
    output = args.output.resolve()
    if args.mode == 'prepare':
        prepare(output, args.predecessor, revalidate_approved=args.revalidate_approved,
            recover_host_bootstrap=args.recover_host_bootstrap, cleanup_attestation=args.cleanup_attestation,
            resume_host_failure=args.resume_host_failure, resume_model_failure=args.resume_model_failure,
            recover_coverage_audit_from=args.recover_coverage_audit_from,
            startup_failure_from=args.startup_failure_from)
    elif args.mode == 'attest-bootstrap-cleanup':
        attest_bootstrap_cleanup(args.predecessor, output)
        print(json.dumps({'cleanupAttestation': str(output), 'originalCleanupPreserved': True}))
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
