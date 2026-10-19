"""Explicit restoration of one host-retained rejection after display truncation.

Only the trusted host archive bridge calls this helper. It retains the actual
visible bytes and adds an authenticated unaccepted-draft binding, never reading
credit, an accepted decision, or model-authored repair instructions.
"""
from copy import deepcopy
import json
from pathlib import PurePosixPath
import re

from minicode.coverage_audit_state import (build_coverage_audit_state,
    coverage_delivery_pairs, delivered_tool_pairs, record_document,
    validate_coverage_audit_state)
from minicode.coverage_audit_submission import CoverageDrafts, rejection_delivery_document
from minicode.implementation_continuation import continuation_digest, implementation_spec_digest
from minicode.review_evidence_documents import canonical
from minicode.tooling import _smart_truncate_output

_BINDINGS = ('auditId', 'candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')
_METADATA = '_coverage_rejection_record'


def _identity(record):
    return {key: record[key] for key in ('ref', 'sha256')} | {'bytes': len(record['rawJson'].encode())}


def derive_rejection_recovery(document, spec, late, originals, archive_digest, generated=None):
    """Validate exact old prefixes and one original failed submit, then bind it."""
    from minicode.implementation_completion_review import ImplementationCompletionReview

    if (document.get('specDigest') != implementation_spec_digest(spec)
            or not isinstance(archive_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', archive_digest)):
        raise ValueError('rejected submission recovery requires original specification and authenticated archive')
    ImplementationCompletionReview.from_state(spec, document['reviewState'])
    old = validate_coverage_audit_state(document['reviewState']['pendingCoverageAudit'],
        objective=spec.objective, candidate_digest=document['candidateDigest'])
    if (old['coverageAuditStateVersion'] != 3
            or record_document(old['inspectionRecord'])['phase'] != 'reconciliation'
            or set(late) != {'messagesRecord', 'inspectionRecord', 'deliveryRecord'}
            or late['inspectionRecord'] != old['inspectionRecord']
            or set(originals) != {'draftRecord', 'startedRecord', 'finishedRecord'}):
        raise ValueError('rejected submission recovery must retain a committed unchanged inspection')
    directory = PurePosixPath(old['messagesRecord']['ref']).parent
    for key, record in {**late, **originals}.items():
        value = record_document(record)
        if key != 'inspectionRecord' and PurePosixPath(record['ref']).parent != directory:
            raise ValueError('rejected submission original record changed native owner directory')
        if key in {'messagesRecord', 'deliveryRecord', 'draftRecord'} and any(value.get(k) != old[k] for k in _BINDINGS):
            raise ValueError('rejected submission original audit bindings changed')
    messages_value = record_document(late['messagesRecord'])
    delivery_value = record_document(late['deliveryRecord'])
    if (set(messages_value) != {*_BINDINGS, 'messages'}
            or set(delivery_value) != {*_BINDINGS, 'pairs'}):
        raise ValueError('rejected submission requires exact native message and delivery records')
    old_messages = record_document(old['messagesRecord'])['messages']
    messages, pairs = messages_value['messages'], delivery_value['pairs']
    old_pairs = [{'call': call, 'result': result} for call, result in coverage_delivery_pairs(old)]
    if (not isinstance(messages, list) or len(messages) != len(old_messages) + 2
            or messages[:-2] != old_messages or not isinstance(pairs, list)
            or len(pairs) != len(old_pairs) + 1 or pairs[:-1] != old_pairs):
        raise ValueError('rejected submission recovery requires exactly one forward call/result extension')
    native_pairs = list(delivered_tool_pairs(messages))
    call, result = messages[-2:]
    if (not native_pairs or native_pairs[-1] != (call, result)
            or pairs[-1] != {'call': call, 'result': result}
            or call['toolName'] != 'completion_coverage_audit_submit'
            or result['isError'] is not True or _METADATA in result):
        raise ValueError('rejected submission is not the exact original delivered error pair')
    started, finished, draft = (record_document(originals[k])
        for k in ('startedRecord', 'finishedRecord', 'draftRecord'))
    expected_keys = {'auditId', 'input', 'invocationId', 'isError', 'output', 'phase', 'toolName'}
    if (set(started) != expected_keys or set(finished) != expected_keys
            or started['phase'] != 'started' or started['isError'] is not False
            or started['input'] != call['input'] or started['invocationId'] is not None
            or finished['phase'] != 'finished' or finished['isError'] is not True
            or finished['input'] is not None or finished['invocationId'] != originals['startedRecord']['ref']
            or any(row['auditId'] != old['auditId'] or row['toolName'] != call['toolName'] for row in (started, finished))):
        raise ValueError('rejected submission lacks the original started/finished invocation binding')
    sequence = [PurePosixPath(originals[k]['ref']).name.split('-', 1)[0]
        for k in ('startedRecord', 'draftRecord', 'finishedRecord')]
    if not all(x.isdigit() for x in sequence) or not int(sequence[0]) < int(sequence[1]) < int(sequence[2]):
        raise ValueError('rejected submission draft is outside the original invocation interval')
    drafts = CoverageDrafts({k: old[k] for k in _BINDINGS if k != 'auditId'}, old['auditId'])
    for prior_call, prior_result in coverage_delivery_pairs(old):
        if prior_call['toolName'] == 'completion_coverage_audit_submit' and prior_result['isError']:
            drafts.restore_rejection(prior_call['input'], prior_result['content'],
                rejection_record=prior_result.get(_METADATA), tool_use_id=prior_call['toolUseId'])
    expected = drafts.retain(call['input'], drafts.resolve(call['input']))
    if (set(draft) != {*expected, 'rejection'}
            or {k: draft[k] for k in expected} != expected
            or not isinstance(draft['rejection'], dict)
            or draft['rejection'].get('errorCode') != 'coverage_audit_invalid'
            or draft['rejection'].get('draftId') != expected['draftId']
            or draft['rejection'].get('draftStatus') != 'unaccepted'
            or 'draftRef' in draft['rejection']):
        raise ValueError('rejected submission does not reconstruct its original unaccepted native draft')
    full_output = canonical({**draft['rejection'], 'draftRef': originals['draftRecord']['ref']})
    display = _smart_truncate_output(full_output, call['toolName'])
    if display == full_output or finished['output'] != display:
        raise ValueError('rejected submission does not match the actual legacy truncation')
    try:
        json.JSONDecoder().raw_decode(display)
    except json.JSONDecodeError:
        pass
    else:
        raise ValueError('legacy rejection recovery requires the evidenced malformed JSON display')
    rejection = rejection_delivery_document(bindings={k: old[k] for k in _BINDINGS if k != 'auditId'},
        audit_id=old['auditId'], original_input=call['input'], tool_use_id=call['toolUseId'],
        native_output=full_output, display_output=display, delivered_output=result['content'],
        projection='legacy_smart_truncation_v1')
    receipt = {'coverageRecoveryVersion': 2, 'reason': 'truncated_rejection_checkpoint_lag',
        'checkpointDigest': continuation_digest(document), 'oldPendingDigest': continuation_digest(old),
        **{key: document[key] for key in ('executorId', 'attempt', 'candidateDigest', 'specDigest', 'messagesDigest')},
        'auditId': old['auditId'], 'sourceArchiveDigest': archive_digest,
        'oldRecords': {key: _identity(value) for key, value in old.items() if key.endswith('Record')},
        'lateRecords': {key: _identity(value) for key, value in late.items()},
        'rejectionOriginals': {key: _identity(value) for key, value in originals.items()},
        'addedErrorDeliveries': [], 'recoveredRejectedCallId': call['toolUseId'],
        'originalVisibleResultDigest': continuation_digest(result),
        'fullRejectionPreviouslyDelivered': False, 'newSourceReadCredit': False,
        'newChallengeCredit': False, 'approvalGranted': False}
    if generated is None:
        return rejection, receipt, None, None
    if set(generated) != {'rejectionRecord', 'messagesRecord', 'deliveryRecord'}:
        raise ValueError('rejection recovery requires exactly three derived host records')
    if record_document(generated['rejectionRecord']) != rejection:
        raise ValueError('recovered rejection differs from authenticated native originals')
    derived_messages, derived_delivery = deepcopy(messages_value), deepcopy(delivery_value)
    derived_messages['messages'][-1][_METADATA] = deepcopy(generated['rejectionRecord'])
    derived_delivery['pairs'][-1]['result'][_METADATA] = deepcopy(generated['rejectionRecord'])
    expected_records = {'messagesRecord': derived_messages, 'deliveryRecord': derived_delivery}
    for key, value in expected_records.items():
        if generated[key] is not None and record_document(generated[key]) != value:
            raise ValueError('rejection recovery changed original visible call/result history')
    if any(generated[key] is None for key in expected_records):
        return rejection, receipt, expected_records, None
    pending = build_coverage_audit_state(authority_record=old['authorityRecord'], documents_record=old['documentsRecord'],
        messages_record=generated['messagesRecord'], delivery_record=generated['deliveryRecord'],
        inspection_record=old['inspectionRecord'], review_materials_record=old.get('reviewMaterialsRecord'),
        legacy_audit_record=old.get('legacyAuditRecord'))
    receipt['recoveredRecords'] = {key: _identity(value) for key, value in generated.items()}
    receipt['recoveredPendingDigest'] = continuation_digest(pending)
    return rejection, receipt, expected_records, pending
