"""Explicit host recovery of delivered errors after a pending checkpoint lagged.

The caller authenticates the archive and its owner/ancestor chain before passing
records here. A digest is an identity check, not trust in a model-supplied path.
No public tool accepts these records. Original checkpoints are never rewritten.
"""
from copy import deepcopy
from pathlib import Path, PurePosixPath
import json
import re

from minicode.coverage_audit_state import (
    DELIVERY_TOOLS, build_coverage_audit_state, coverage_delivery_pairs,
    delivered_tool_pairs, host_record, record_document, validate_coverage_audit_state,
)
from minicode.completion_coverage_inspection import validate_inspection_record
from minicode.implementation_continuation import continuation_digest, implementation_spec_digest
from minicode.reviewer_verification import ReviewerProbe

_BINDINGS = ('auditId', 'candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')
_RECORD_KEYS = ('authorityRecord', 'documentsRecord', 'messagesRecord', 'inspectionRecord',
                'deliveryRecord', 'reviewMaterialsRecord', 'legacyAuditRecord')


def _identity(record):
    return {'ref': record['ref'], 'sha256': record['sha256'],
            'bytes': len(record['rawJson'].encode('utf-8'))}


def _pair(call, result):
    return {'call': call, 'result': result}


def _challenge_result_matches(pair, probe, wrapped, candidate_digest):
    """Bind the actually displayed result to the owner-validated receipt.

    Host notes may follow the original JSON. Their bytes remain in the retained
    pair; parsing them must not replace or trim the archived dialogue.
    """
    from minicode.completion_evidence_view import EvidenceViews
    try:
        output, _end = json.JSONDecoder().raw_decode(pair['result']['content'].lstrip())
        if (not isinstance(output, dict)
                or any(output.get(key) != wrapped.get(key) for key in ('probeId', 'candidateDigest', 'receiptRef'))
                or wrapped.get('probeId') != probe.probe_id
                or wrapped.get('candidateDigest') != candidate_digest
                or pair['result']['isError'] is not (wrapped['receipt']['status'] != 'passed')):
            return False
        return output == wrapped or output == EvidenceViews(candidate_digest=candidate_digest).view(wrapped)
    except (ValueError, TypeError, KeyError):
        return False


def _derive(document, spec, late, source_archive_digest, delivery_record=None):
    from minicode.implementation_completion_review import ImplementationCompletionReview

    if (document.get('specDigest') != implementation_spec_digest(spec)
            or not isinstance(source_archive_digest, str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', source_archive_digest)):
        raise ValueError('recovery requires the original specification and authenticated archive identity')
    state = document.get('reviewState')
    if not isinstance(state, dict) or not isinstance(state.get('pendingCoverageAudit'), dict):
        raise ValueError('recovery requires an existing native pending audit')
    # Validate the complete host probe/receipt ledger before consulting challenges.
    owner = ImplementationCompletionReview.from_state(spec, state)
    old = validate_coverage_audit_state(state['pendingCoverageAudit'], objective=spec.objective,
        candidate_digest=document['candidateDigest'])
    if old['coverageAuditStateVersion'] != 3:
        raise ValueError('delivery recovery requires an existing inspection epoch')
    prior = validate_inspection_record(old['inspectionRecord'], old)
    if prior['phase'] != 'inspection' or prior['result'] is not None:
        raise ValueError('delivery recovery cannot reopen or advance a committed audit phase')
    if set(late) != {'messagesRecord', 'inspectionRecord', 'deliveryRecord'}:
        raise ValueError('recovery requires exactly the three original native sidecars')
    directory = PurePosixPath(old['authorityRecord']['ref']).parent
    for record in late.values():
        value = record_document(record)
        if (PurePosixPath(record['ref']).parent != directory
                or any(value.get(key) != old[key] for key in _BINDINGS)):
            raise ValueError('late audit record changed owner directory or audit bindings')
    messages_value = record_document(late['messagesRecord'])
    if set(messages_value) != {*_BINDINGS, 'messages'}:
        raise ValueError('late audit messages must be an exact native record')
    messages = messages_value['messages']
    old_messages = record_document(old['messagesRecord'])['messages']
    if (not isinstance(messages, list) or len(messages) <= len(old_messages)
            or messages[:len(old_messages)] != old_messages):
        raise ValueError('recovery requires an exact forward extension of the saved dialogue')
    current = list(delivered_tool_pairs(messages))
    by_id = {call['toolUseId']: _pair(call, result) for call, result in current}
    inspection = validate_inspection_record(late['inspectionRecord'], old)
    if (any(inspection[key] != prior[key] for key in inspection if key not in {'reads', 'challenges'})
            or inspection['reads'][:len(prior['reads'])] != prior['reads']
            or inspection['challenges'][:len(prior['challenges'])] != prior['challenges']):
        raise ValueError('recovery must preserve the inspection phase, original task and prior evidence')
    for row in inspection['reads'][len(prior['reads']):]:
        if by_id.get(row['call']['toolUseId']) != _pair(row['call'], row['result']):
            raise ValueError('new inspection read lacks its original delivered pair')
    late_delivery = record_document(late['deliveryRecord'])
    if set(late_delivery) != {*_BINDINGS, 'pairs'} or not isinstance(late_delivery['pairs'], list):
        raise ValueError('late delivery must be an exact native record')
    # Check pairing/uniqueness without requiring the known missing errors yet.
    flat = [message for pair in late_delivery['pairs'] for message in (pair['call'], pair['result'])]
    delivered = [_pair(call, result) for call, result in delivered_tool_pairs(flat)]
    old_pairs = [_pair(call, result) for call, result in coverage_delivery_pairs(old)]
    if delivered[:len(old_pairs)] != old_pairs:
        raise ValueError('late delivery must preserve the original provenance prefix')
    delivered_by_id = {pair['call']['toolUseId']: pair for pair in delivered}
    old_ids = {pair['call']['toolUseId'] for pair in old_pairs}
    for pair in delivered:
        identity = pair['call']['toolUseId']
        if pair['call']['toolName'] not in DELIVERY_TOOLS:
            raise ValueError('late delivery contains an untracked tool')
        if identity in by_id and by_id[identity] != pair:
            raise ValueError('late delivery changed the actual native call/result bytes')
        if identity not in old_ids and identity not in by_id:
            raise ValueError('new successful provenance lacks actual delivered messages')
    missing, positions = [], []
    for call, result in current:
        if call['toolName'] not in DELIVERY_TOOLS or call['toolUseId'] in delivered_by_id:
            continue
        if result['isError'] is not True:
            raise ValueError('recovery cannot synthesize missing successful delivery')
        missing.append(_pair(call, result))
        positions.append({'toolUseId': call['toolUseId'], 'toolName': call['toolName'],
            'callIndex': messages.index(call), 'resultIndex': messages.index(result),
            'callDigest': continuation_digest(call), 'resultDigest': continuation_digest(result)})
    if not missing:
        raise ValueError('recovery requires an actual delivered-error provenance gap')
    # New challenge IDs must already exist in the host-validated source/receipt
    # ledger and in the original late delivery; a recovered error grants none.
    for probe_id in inspection['challenges'][len(prior['challenges']):]:
        probe = owner._probes.get(probe_id)
        if probe is None or (old['candidateDigest'], probe_id) not in owner._receipts:
            raise ValueError('inspection challenge lacks the original owner receipt ledger')
        matched = False
        for pair in delivered:
            call = pair['call']
            if call['toolName'] != 'review_test':
                continue
            value = call['input']
            try:
                source = ReviewerProbe.create(criterion_ids=value.get('criterionIds'),
                    scenario=value.get('scenario'), code=value.get('code'))
            except (ValueError, TypeError):
                continue
            wrapped = owner._receipts[(old['candidateDigest'], probe_id)]
            if (source.to_dict() == probe.to_dict()
                    and _challenge_result_matches(pair, probe, wrapped, old['candidateDigest'])):
                matched = True
        if not matched:
            raise ValueError('inspection challenge lacks an original tracked execution pair')
    # Preserve historical compacted pairs and the exact current result order.
    historical = [pair for pair in delivered if pair['call']['toolUseId'] not in by_id]
    ordered = historical + [_pair(call, result) for call, result in current if call['toolName'] in DELIVERY_TOOLS]
    if [pair for pair in ordered if pair['call']['toolUseId'] in delivered_by_id] != delivered:
        raise ValueError('recovery would reorder original delivery provenance')
    delivery = {**{key: old[key] for key in _BINDINGS}, 'pairs': ordered}
    receipt = {'coverageRecoveryVersion': 1, 'reason': 'delivered_error_checkpoint_lag',
        'checkpointDigest': continuation_digest(document), 'oldPendingDigest': continuation_digest(old),
        **{key: document[key] for key in ('executorId', 'attempt', 'candidateDigest', 'specDigest', 'messagesDigest')},
        'auditId': old['auditId'], 'sourceArchiveDigest': source_archive_digest,
        'oldRecords': {key: _identity(old[key]) for key in _RECORD_KEYS if key in old},
        'lateRecords': {key: _identity(record) for key, record in late.items()},
        'addedErrorDeliveries': positions}
    if delivery_record is None:
        return delivery, receipt, None
    if record_document(delivery_record) != delivery:
        raise ValueError('recovered delivery differs from exact original messages')
    pending = build_coverage_audit_state(authority_record=old['authorityRecord'], documents_record=old['documentsRecord'],
        messages_record=late['messagesRecord'], delivery_record=delivery_record,
        review_materials_record=old.get('reviewMaterialsRecord'), inspection_record=late['inspectionRecord'],
        legacy_audit_record=old.get('legacyAuditRecord'))
    receipt['recoveredDelivery'] = _identity(delivery_record)
    receipt['recoveredPendingDigest'] = continuation_digest(pending)
    return delivery, receipt, pending


class HostCoverageAuditRecovery:
    """Typed host overlay; neither a model verdict nor a rewritten checkpoint."""

    @classmethod
    def from_records(cls, *, continuation, spec, messages_record, inspection_record,
                     delivery_record, source_archive_digest, persist, rejection_records=None, interruption_records=None):
        from minicode.implementation_continuation import HostImplementationContinuation
        if not isinstance(continuation, HostImplementationContinuation):
            raise TypeError('coverage recovery requires the original typed host continuation')
        document = continuation.document
        late = deepcopy({'messagesRecord': messages_record, 'inspectionRecord': inspection_record,
                         'deliveryRecord': delivery_record})
        if interruption_records is not None:
            from minicode.coverage_interruption_recovery import derive_interruption_recovery
            if rejection_records is not None:
                raise ValueError('choose exactly one explicit audit recovery mode')
            originals = deepcopy(interruption_records)
            receipt, pending = derive_interruption_recovery(document, spec, late, originals, source_archive_digest)
            reference = persist('coverage-audit-recovery-receipt', receipt)
            record = host_record(reference, Path(reference).read_bytes())
            if record_document(record) != receipt:
                raise ValueError('recovery receipt persistence changed the host derivation')
            result = object.__new__(cls)
            result._interruption_records = originals
            result._late, result._source_archive_digest = late, source_archive_digest
            result._receipt_record, result._pending = record, pending
            return result
        if rejection_records is not None:
            from minicode.coverage_rejection_recovery import derive_rejection_recovery

            originals = deepcopy(rejection_records)
            rejection, _, _, _ = derive_rejection_recovery(document, spec, late, originals, source_archive_digest)
            reference = persist('coverage-audit-recovery-rejection', rejection)
            generated = {'rejectionRecord': host_record(reference, Path(reference).read_bytes()),
                         'messagesRecord': None, 'deliveryRecord': None}
            _, _, derived, _ = derive_rejection_recovery(document, spec, late, originals,
                source_archive_digest, generated)
            for key, kind in (('messagesRecord', 'coverage-audit-recovery-messages'),
                              ('deliveryRecord', 'coverage-audit-recovery-delivery')):
                reference = persist(kind, derived[key])
                generated[key] = host_record(reference, Path(reference).read_bytes())
            _, receipt, _, pending = derive_rejection_recovery(document, spec, late, originals,
                source_archive_digest, generated)
            reference = persist('coverage-audit-recovery-receipt', receipt)
            record = host_record(reference, Path(reference).read_bytes())
            if record_document(record) != receipt:
                raise ValueError('recovery receipt persistence changed the host derivation')
            result = object.__new__(cls)
            result._late, result._source_archive_digest = late, source_archive_digest
            result._rejection_records, result._generated_records = originals, generated
            result._receipt_record, result._pending = record, pending
            return result
        delivery, _, _ = _derive(document, spec, late, source_archive_digest)
        reference = persist('coverage-audit-recovery-delivery', delivery)
        recovered_delivery = host_record(reference, Path(reference).read_bytes())
        _, receipt, pending = _derive(document, spec, late, source_archive_digest, recovered_delivery)
        reference = persist('coverage-audit-recovery-receipt', receipt)
        record = host_record(reference, Path(reference).read_bytes())
        if record_document(record) != receipt:
            raise ValueError('recovery receipt persistence changed the host derivation')
        result = object.__new__(cls)
        result._rejection_records = None
        result._late, result._source_archive_digest = late, source_archive_digest
        result._delivery_record, result._receipt_record = recovered_delivery, record
        result._pending = pending
        return result

    def validate_for(self, document, *, spec):
        if getattr(self, '_interruption_records', None) is not None:
            from minicode.coverage_interruption_recovery import derive_interruption_recovery
            expected, pending = derive_interruption_recovery(document, spec, self._late,
                self._interruption_records, self._source_archive_digest)
        elif self._rejection_records is not None:
            from minicode.coverage_rejection_recovery import derive_rejection_recovery
            _, expected, _, pending = derive_rejection_recovery(document, spec, self._late,
                self._rejection_records, self._source_archive_digest, self._generated_records)
        else:
            _, expected, pending = _derive(document, spec, self._late,
                self._source_archive_digest, self._delivery_record)
        if record_document(self._receipt_record) != expected or pending != self._pending:
            raise ValueError('coverage recovery no longer binds its original host checkpoint')

    @property
    def pending_state(self):
        return deepcopy(self._pending)

    @property
    def receipt_record(self):
        return deepcopy(self._receipt_record)
