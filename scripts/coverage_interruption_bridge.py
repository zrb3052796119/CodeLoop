"""Narrow archive bridge for a host-cleared, unsubmitted resumed audit."""
import hashlib
import json
from pathlib import Path

from minicode.coverage_audit_state import build_coverage_audit_state, host_record, record_document


def interrupted_coverage_source(runner, source, predecessor, document, chain):
    if source != predecessor:
        raise ValueError('cleared audit recovery must use the actual terminal predecessor')
    root = source / 'private' / runner.TRIAL_ID
    receipt_path = root / 'implementation-checkpoint-receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    if (receipt.get('available') is not True or receipt.get('source') != 'production_parent_finally'
            or any(receipt.get(k) != document[k] for k in ('executorId', 'candidateDigest', 'attempt'))
            or runner.sha_file(root / 'implementation-checkpoint.json') != receipt.get('documentSha256')
            or runner.sha_file(root / 'implementation-checkpoint-messages.json') != receipt.get('messagesSha256')):
        raise ValueError('cleared audit recovery changed original terminal owner or checkpoint')
    # Reuse the already loaded native document; do not parse the large terminal
    # or input checkpoints again merely to obtain immutable record identities.
    manifest = json.loads((source / 'manifest.json').read_bytes())
    if manifest.get('continuationCheckpointSha256') != runner.sha_file(source / 'checkpoint.json'):
        raise ValueError('cleared audit frozen input checkpoint changed')
    prepared = json.loads((source / 'checkpoint.json').read_bytes())
    cap = prepared.get('coverageAuditRecovery')
    if (not isinstance(cap, dict) or cap.get('kind') != 'typed_same_audit_archive_recovery'
            or cap.get('file') != 'private/coverage-recovery.json'):
        raise ValueError('cleared audit recovery requires an authenticated frozen input recovery receipt')
    descriptor_path = runner.safe_path(source, cap['file'])
    if runner.sha_file(descriptor_path) != cap['sha256']:
        raise ValueError('frozen input recovery descriptor changed')
    descriptor = json.loads(descriptor_path.read_bytes())
    generated = descriptor['generatedRecords']['coverage-audit-recovery-receipt']
    generated_path = runner.safe_path(source, generated['file'])
    if generated['file'] != 'private/coverage-recovery-receipt.json' or runner.sha_file(generated_path) != generated['sha256']:
        raise ValueError('frozen input recovery receipt changed')
    inherited = json.loads(generated_path.read_bytes())
    if (inherited.get('coverageRecoveryVersion') != 2
            or inherited.get('reason') != 'truncated_rejection_checkpoint_lag'
            or any(inherited.get(k) != document[k] for k in ('executorId', 'candidateDigest', 'specDigest'))
            or inherited.get('attempt') != prepared['identity']['attempt']
            or prepared['identity']['executorId'] != document['executorId']
            or prepared['identity']['candidateDigest'] != document['candidateDigest']
            or inherited['attempt'] >= document['attempt']):
        raise ValueError('cleared audit recovery changed frozen input identity')
    manifests = []
    for ancestor in chain:
        origin = ancestor / 'private' / runner.TRIAL_ID
        path = origin / 'completion-review-archive.json'
        if path.is_file():
            if path.is_symlink():
                raise ValueError('inherited audit archive cannot be a symlink')
            manifest = json.loads(path.read_bytes())
            if manifest.get('archiveComplete') is not True or manifest.get('errors'):
                raise ValueError('inherited audit archive is incomplete')
            manifests.append((origin, manifest))
    identities = {}

    def inherited_record(key):
        identity = inherited['oldRecords'][key]
        reference = Path(identity['ref'])
        folder = 'review_' + hashlib.sha256(str(reference.parent.resolve()).encode()).hexdigest()[:24]
        relative = 'completion-review/' + folder + '/' + reference.name
        matches = []
        for origin, manifest in manifests:
            for row in manifest['files']:
                if row['file'] == relative and 'sha256:' + row['sha256'] == identity['sha256'] and row['bytes'] == identity['bytes']:
                    matches.append((origin, row))
        if not matches:
            raise ValueError('inherited audit original is absent from authenticated lineage archives')
        origin, row = matches[-1]
        path = runner.safe_path(origin, row['file'])
        raw = path.read_bytes()
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('inherited audit original bytes changed')
        identities[key] = {'file': str(path), **identity}
        return host_record(str(reference), raw)

    inherited_records = {key: inherited_record(key) for key in inherited['oldRecords']
        if key not in {'messagesRecord', 'deliveryRecord'}}
    directory = runner.verify_archive(root)
    original = Path(document['reviewState']['evidenceDirectory'])
    if directory.name != 'review_' + hashlib.sha256(str(original.resolve()).encode()).hexdigest()[:24]:
        raise ValueError('cleared audit archive changed native owner directory')
    audit_id = inherited['auditId']
    selected = {}

    def native_record(path, key):
        raw = path.read_bytes()
        record = host_record(str(original / path.name), raw)
        selected[key] = {'file': str(path.relative_to(source)), 'ref': record['ref'],
                         'sha256': record['sha256'], 'bytes': len(raw)}
        return record

    def native_series(suffix):
        rows = []
        for path in sorted(directory.glob('*' + suffix)):
            value = json.loads(path.read_bytes())
            if value.get('auditId') == audit_id:
                rows.append(path)
        if not rows:
            raise ValueError('cleared audit archive lacks actual native dialogue or delivery')
        return rows

    messages = native_series('-coverage-audit-messages.json')
    deliveries = native_series('-coverage-audit-delivery.json')
    initial = build_coverage_audit_state(authority_record=inherited_records['authorityRecord'],
        documents_record=inherited_records['documentsRecord'],
        messages_record=native_record(messages[0], 'initialMessagesRecord'),
        delivery_record=native_record(deliveries[0], 'initialDeliveryRecord'),
        inspection_record=inherited_records['inspectionRecord'],
        review_materials_record=inherited_records.get('reviewMaterialsRecord'),
        legacy_audit_record=inherited_records.get('legacyAuditRecord'))
    decisions = []
    for path in directory.glob('*-coverage-audit-decision.json'):
        value = json.loads(path.read_bytes())
        if (value.get('auditId') == audit_id or value.get('authorityRef') == initial['authorityRecord']['ref']
                or value.get('authorityProvenance', {}).get('auditId') == audit_id):
            decisions.append(path)
    if len(decisions) != 1:
        raise ValueError('cleared audit has missing or ambiguous terminal records')
    decision_record = native_record(decisions[0], 'decisionRecord')
    decision = record_document(decision_record)
    authority_ref = Path(decision['authorityRef'])
    if authority_ref.parent != original:
        raise ValueError('cleared audit terminal authority points outside original native directory')
    authority_record = native_record(directory / authority_ref.name, 'decisionAuthorityRecord')
    records = {'messagesRecord': native_record(messages[-1], 'messagesRecord'),
        'deliveryRecord': native_record(deliveries[-1], 'deliveryRecord'),
        'inspectionRecord': inherited_records['inspectionRecord'],
        'interruptionRecords': {'initialState': initial, 'decisionRecord': decision_record,
                               'decisionAuthorityRecord': authority_record}}
    descriptor = {'version': 1, 'kind': 'explicit_same_audit_archive_recovery',
        'recoveryReason': 'unsubmitted_transport_stop_cleared_pending',
        'sourceDirectory': str(source), 'sourceArchiveSha256': runner.sha_file(root / 'completion-review-archive.json'),
        'sourceNativeReceiptSha256': runner.sha_file(receipt_path),
        'sourceNativeCheckpointSha256': receipt['documentSha256'],
        'sourceNativeMessagesSha256': receipt['messagesSha256'],
        'executorId': document['executorId'], 'candidateDigest': document['candidateDigest'], 'auditId': audit_id,
        'frozenInputDescriptorSha256': cap['sha256'], 'frozenInputRecoveryReceiptSha256': generated['sha256'],
        'inheritedRecords': identities, 'records': selected}
    return descriptor, records
