"""Host-only recovery of a cleared audit after an unsubmitted transport stop.

Original terminal records remain history. This overlay restores only the latest
actually delivered pending dialogue; it neither creates a submission nor credit.
The caller authenticates every record against the frozen input/native archives.
"""
from pathlib import PurePosixPath
import re

from minicode.coverage_audit_state import (
    build_coverage_audit_state, coverage_delivery_pairs, delivered_tool_pairs,
    record_document, validate_coverage_audit_state,
)
from minicode.implementation_continuation import continuation_digest, implementation_spec_digest

_BINDINGS = ('auditId', 'candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')


def _identity(record):
    return {key: record[key] for key in ('ref', 'sha256')} | {'bytes': len(record['rawJson'].encode())}


def derive_interruption_recovery(document, spec, late, originals, archive_digest):
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.completion_coverage_audit import _digest as audit_digest

    if (document.get('specDigest') != implementation_spec_digest(spec)
            or not isinstance(archive_digest, str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', archive_digest)):
        raise ValueError('interrupted audit recovery requires original specification and authenticated archive')
    state = document['reviewState']
    if state.get('pendingCoverageAudit') is not None:
        raise ValueError('interrupted audit recovery cannot replace a native pending audit')
    ImplementationCompletionReview.from_state(spec, state)
    if set(originals) != {'initialState', 'decisionRecord', 'decisionAuthorityRecord'}:
        raise ValueError('interrupted audit recovery requires exact inherited and terminal records')
    old = validate_coverage_audit_state(originals['initialState'], objective=spec.objective,
        candidate_digest=document['candidateDigest'])
    if (old['coverageAuditStateVersion'] != 3
            or record_document(old['inspectionRecord'])['phase'] != 'reconciliation'
            or set(late) != {'messagesRecord', 'inspectionRecord', 'deliveryRecord'}
            or late['inspectionRecord'] != old['inspectionRecord']):
        raise ValueError('interrupted audit recovery cannot change inspection or reopen its phase')
    messages_value, delivery_value = (record_document(late[k]) for k in ('messagesRecord', 'deliveryRecord'))
    if (set(messages_value) != {*_BINDINGS, 'messages'}
            or set(delivery_value) != {*_BINDINGS, 'pairs'}
            or any(v.get(k) != old[k] for v in (messages_value, delivery_value) for k in _BINDINGS)):
        raise ValueError('interrupted audit recovery changed native audit bindings')
    directory = PurePosixPath(late['messagesRecord']['ref']).parent
    if (str(directory) != state.get('evidenceDirectory')
            or any(PurePosixPath(record['ref']).parent != directory for record in
                   (late['deliveryRecord'], originals['decisionRecord'], originals['decisionAuthorityRecord']))):
        raise ValueError('interrupted audit records changed current native owner directory')
    old_messages = record_document(old['messagesRecord'])['messages']
    messages, pairs = messages_value['messages'], delivery_value['pairs']
    old_pairs = [{'call': c, 'result': r} for c, r in coverage_delivery_pairs(old)]
    if (not isinstance(messages, list) or len(messages) <= len(old_messages)
            or messages[:len(old_messages)] != old_messages
            or not isinstance(pairs, list) or pairs[:len(old_pairs)] != old_pairs):
        raise ValueError('interrupted audit recovery requires exact initial dialogue and delivery prefixes')
    extension = messages[len(old_messages):]
    if (messages[-1].get('role') != 'assistant' or messages[-1].get('_partial') is not True
            or not any(m.get('role') == 'system' and m.get('_model_transport_failure') is True for m in extension)):
        raise ValueError('interrupted audit lacks its actual transport stop and partial response')
    actual_pairs = {c['toolUseId']: {'call': c, 'result': r} for c, r in delivered_tool_pairs(messages)}
    for pair in pairs[len(old_pairs):]:
        if actual_pairs.get(pair['call']['toolUseId']) != pair:
            raise ValueError('interrupted audit cannot invent delivered evidence')
        if pair['call']['toolName'] not in {'review_evidence_read', 'review_probe_read', 'completion_coverage_audit_submit'}:
            raise ValueError('interrupted audit recovery does not import new probe executions')
    # Native validation authenticates every read response and rejected draft,
    # and rejects any successful coverage submit, including an inconclusive one.
    pending = build_coverage_audit_state(authority_record=old['authorityRecord'],
        documents_record=old['documentsRecord'], messages_record=late['messagesRecord'],
        delivery_record=late['deliveryRecord'], inspection_record=old['inspectionRecord'],
        review_materials_record=old.get('reviewMaterialsRecord'), legacy_audit_record=old.get('legacyAuditRecord'))
    decision = record_document(originals['decisionRecord'])
    provenance = {'originalAuthorityRef': old['authorityRecord']['ref'],
        'originalAuthorityDigest': old['authorityRecord']['sha256'], 'auditId': old['auditId']}
    if (decision.get('status') != 'inconclusive' or decision.get('requirements') != []
            or decision.get('inspectionRecord') != old['inspectionRecord']
            or decision.get('authorityProvenance') != provenance
            or decision.get('authorityRef') != originals['decisionAuthorityRecord']['ref']
            or originals['decisionAuthorityRecord']['rawJson'] != old['authorityRecord']['rawJson']
            or any(decision.get(k) != old[k] for k in _BINDINGS if k != 'auditId')):
        raise ValueError('interrupted audit terminal record changed inherited authority or contains a verdict')
    reviews = state.get('reviews', [])
    latest = reviews[-1] if reviews else {}
    expected_audit = {**decision, 'evidenceRef': originals['decisionRecord']['ref'],
        'evidenceDigest': audit_digest(decision)}
    if (latest.get('status') != 'inconclusive' or latest.get('patchDigest') != document['candidateDigest']
            or latest.get('coverageAudit') != expected_audit):
        raise ValueError('interrupted audit is not the current native unapproved review')
    receipt = {'coverageRecoveryVersion': 3, 'reason': 'unsubmitted_transport_stop_cleared_pending',
        'checkpointDigest': continuation_digest(document),
        **{key: document[key] for key in ('executorId', 'attempt', 'candidateDigest', 'specDigest', 'messagesDigest')},
        'auditId': old['auditId'], 'sourceArchiveDigest': archive_digest,
        'oldPendingDigest': continuation_digest(old),
        'oldRecords': {k: _identity(v) for k, v in old.items() if k.endswith('Record')},
        'lateRecords': {k: _identity(v) for k, v in late.items()},
        'terminalRecords': {k: _identity(originals[k]) for k in ('decisionRecord', 'decisionAuthorityRecord')},
        'initialMessages': len(old_messages), 'recoveredMessages': len(messages),
        'initialDeliveries': len(old_pairs), 'recoveredDeliveries': len(pairs),
        'recoveredPendingDigest': continuation_digest(pending), 'historyRewritten': False,
        'newSourceReadCredit': False, 'newChallengeCredit': False, 'approvalGranted': False}
    return receipt, pending
