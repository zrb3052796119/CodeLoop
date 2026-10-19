"""One auditor first inspects the candidate, then sees another reviewer's claims.

Inspection records observations, not coverage authority. The containing native
checkpoint authenticates this state; references/hashes alone confer no trust.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

from minicode.coverage_audit_state import host_record, record_document
from minicode.review_evidence_documents import canonical
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult

_BINDINGS = ('auditId', 'candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')
_READ_TOOLS = {'read_file', 'grep_files', 'list_files', 'file_tree', 'find_symbols', 'file_line_count'}
_HISTORICAL_PROBE_FIELDS = ('correctsProbeId', 'correctionReason', 'sourceEvidence')

_SUBMIT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'status': {'type': 'string', 'enum': ['ready_for_evidence', 'changes_required', 'inconclusive']},
    'observations': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
        'requirementId': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['checked', 'uncertain', 'missing']},
        'sourceEvidence': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
            'path': {'type': 'string'}, 'startLine': {'type': 'integer'}, 'endLine': {'type': 'integer'}}, 'required': ['path', 'startLine', 'endLine']}},
        'expectedBehavior': {'type': 'string'}, 'observedBehavior': {'type': 'string'}, 'probeIds': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['requirementId', 'status', 'sourceEvidence', 'expectedBehavior', 'observedBehavior', 'probeIds']}},
    'findings': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['status', 'observations', 'findings']}


class _InspectionInputError(ValueError):
    def __init__(self, message: str, diagnostics: list[dict]):
        super().__init__(message)
        self.diagnostics = diagnostics


def _schema_diagnostics(value: object, schema: dict, path: str = '$') -> list[dict]:
    """Report the inspection schema's small JSON subset without normalizing input."""
    expected = schema.get('type')
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'string': isinstance(value, str), 'integer': type(value) is int}
    if expected in matches and not matches[expected]:
        return [{'path': path, 'code': 'type', 'expectedType': expected,
                 'message': f'{path} must be {expected}'}]
    diagnostics = []
    if 'enum' in schema and value not in schema['enum']:
        diagnostics.append({'path': path, 'code': 'enum', 'allowedValues': schema['enum'],
                            'message': f'{path} must use one of the advertised values'})
    if isinstance(value, dict) and expected == 'object':
        for key in schema.get('required', []):
            if key not in value:
                diagnostics.append({'path': f'{path}.{key}', 'code': 'required',
                                    'missingFields': [key], 'message': f'{path} requires {key}'})
        properties = schema.get('properties', {})
        for key, item in value.items():
            if key in properties:
                diagnostics.extend(_schema_diagnostics(item, properties[key], f'{path}.{key}'))
            elif schema.get('additionalProperties') is False:
                diagnostics.append({'path': f'{path}.{key}', 'code': 'additionalProperties',
                                    'message': f'{path} does not accept {key}'})
    elif isinstance(value, list) and expected == 'array':
        for index, item in enumerate(value):
            diagnostics.extend(_schema_diagnostics(item, schema.get('items', {}), f'{path}[{index}]'))
    return diagnostics


def _submission_diagnostics(value: object) -> list[dict]:
    diagnostics = _schema_diagnostics(value, _SUBMIT_SCHEMA)
    if (isinstance(value, dict) and value.get('status') == 'ready_for_evidence'
            and isinstance(value.get('findings'), list) and value['findings']):
        diagnostics.append({'path': '$.findings', 'code': 'ready_requires_no_findings',
            'message': 'ready_for_evidence requires an empty findings array; unresolved findings cannot enter evidence reconciliation'})
    return diagnostics


def _source_diagnostic(field_path: str, index: int, anchor: dict, message: str, code: str) -> dict:
    return {'path': f'{field_path}[{index}]', 'code': code, 'message': message,
            'sourcePath': anchor['path'], 'startLine': anchor['startLine'], 'endLine': anchor['endLine']}


def validate_inspection_record(record, state):
    value = record_document(record)
    if (set(value) != {'inspectionVersion', *_BINDINGS, 'phase', 'origin', 'reads', 'challenges', 'result', 'taskPacket'}
            or type(value['inspectionVersion']) is not int or value['inspectionVersion'] != 1
            or any(value[key] != state[key] for key in _BINDINGS)
            or value['phase'] not in {'inspection', 'reconciliation'}
            or value['origin'] not in {'fresh', 'legacy_new_context'}
            or not isinstance(value['reads'], list) or not isinstance(value['challenges'], list)
            or not isinstance(value['taskPacket'], dict) or set(value['taskPacket']) != {'role', 'content'}
            or value['taskPacket']['role'] != 'user' or not isinstance(value['taskPacket']['content'], str)):
        raise ValueError('independent inspection binding or phase changed')
    for row in value['reads']:
        if (not isinstance(row, dict) or set(row) != {'path', 'sourceDigest', 'offset', 'end', 'call', 'result'}
                or row['call'].get('toolName') != 'read_file'
                or row['result'].get('toolUseId') != row['call'].get('toolUseId')
                or row['result'].get('toolName') != 'read_file' or row['result'].get('isError') is not False
                or type(row['offset']) is not int or type(row['end']) is not int
                or row['offset'] < 0 or row['end'] < row['offset']):
            raise ValueError('inspection requires actual delivered source read evidence')
    result = value['result']
    if result is not None and (not isinstance(result, dict)
            or set(result) != {'status', 'observations', 'findings'}
            or result['status'] != 'ready_for_evidence' or not result['observations']):
        raise ValueError('inspection result is not an independent observation record')
    if value['phase'] == 'reconciliation' and result is None:
        raise ValueError('evidence cannot be disclosed before inspection commits')
    return deepcopy(value)


class CoverageInspection:
    def __init__(self, *, bindings, objective, requirements, manifest, cwd, permissions,
                 source_anchors, probe_evidence, persist, manager, finish_negative,
                 capability_profile, record=None, legacy=False):
        self.bindings, self.objective = bindings, objective
        self.requirements, self.manifest = requirements, manifest
        self.root, self.permissions = Path(cwd).resolve(), permissions
        self.source_anchors, self.probe_evidence = source_anchors, probe_evidence
        self.persist, self.manager, self.finish_negative = persist, manager, finish_negative
        self.profile = capability_profile
        self.value = (validate_inspection_record(record, bindings) if record is not None else {
            'inspectionVersion': 1, **bindings, 'phase': 'inspection',
            'origin': 'legacy_new_context' if legacy else 'fresh', 'reads': [], 'challenges': [], 'result': None,
            'taskPacket': {'role': 'user', 'content': canonical({'coverageInspection': {
                'auditId': bindings['auditId'], 'candidateDigest': bindings['candidateDigest'],
                'objectiveDigest': bindings['objectiveDigest'], 'requirements': requirements,
                'behaviorContract': manifest}})}})
        self.record = record
        if record is None:
            self._commit(self.value)

    def _commit(self, value):
        reference = self.persist('coverage-inspection', value)
        record = host_record(reference, Path(reference).read_bytes())
        validate_inspection_record(record, self.bindings)
        self.value, self.record = deepcopy(value), record

    def initial_messages(self):
        return [{'role': 'system', 'content':
            'Independently inspect the public objective against the read-only candidate before seeing the ordinary reviewer proposal. '
            'Read actual candidate source; record only risks, checked behavior, necessary challenges and uncertainty you actually investigated. '
            'There is no requirement to rewrite every requirement or to create a fixed number of tests. '
            'Use read_file to read the exact source anchors you cite. A concrete independently source-backed missing behavior '
            'must immediately return changes_required to the existing implementer. An unattributed failing probe is uncertainty, not product repair authority. '
            'ready_for_evidence records observations only and cannot approve completion. After it commits, this same auditor context '
            'receives the ordinary proposal and reconciles evidence with those independent observations. '
            + ('An earlier audit context already saw reviewer claims. Its original history is preserved; this is an explicit new independent epoch, not a claim that the old context became blind. ' if self.value['origin'] == 'legacy_new_context' else '')
            + '\n=== ORIGINAL REQUIREMENT AUTHORITY ===\n' + self.objective},
            deepcopy(self.value['taskPacket'])]

    def _delivered(self, name, value, result, validator):
        previous = result._delivery_ack
        def acknowledge():
            if previous is not None:
                previous()
            call, output = self.manager.messages[-2:]
            if (call.get('role') != 'assistant_tool_call' or output.get('role') != 'tool_result'
                    or call.get('toolName') != name or validator(call.get('input')) != value
                    or output.get('toolUseId') != call.get('toolUseId')):
                raise ValueError('inspection source was not actually delivered')
            if output.get('isError') and name != 'review_test':
                return
            next_value = deepcopy(self.value)
            if name == 'read_file':
                path = (self.root / value['path']).resolve()
                if not path.is_relative_to(self.root):
                    raise ValueError('inspection read escaped candidate')
                self.permissions.ensure_path_access(str(path), 'read')
                raw = path.read_bytes()
                content = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
                text = output['content']
                match = re.match(r'FILE: [^\n]*\nOFFSET: (\d+)\nEND: (\d+)\nTOTAL_CHARS: [^\n]*\nTRUNCATED: [^\n]*\n', text)
                if match is None:
                    raise ValueError('inspection read lacks actual read_file window')
                start, end = map(int, match.groups())
                body = text[match.end():]
                if 'start_line' in value:
                    body = body.split('\n', 2)[-1]
                if body != content[start:end] or end - start != len(body):
                    raise ValueError('inspection source delivery differs from current candidate')
                next_value['reads'].append({'path': path.relative_to(self.root).as_posix(),
                    'sourceDigest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'offset': start, 'end': end,
                    'call': deepcopy(call), 'result': deepcopy(output)})
            elif name == 'review_test':
                from minicode.reviewer_verification import ReviewerProbe
                try:
                    document = json.JSONDecoder().raw_decode(output['content'].lstrip())[0]
                    probe = ReviewerProbe.create(criterion_ids=value.get('criterionIds'), scenario=value.get('scenario'), code=value.get('code'))
                    current = self.probe_evidence(probe.probe_id) or {}
                    wrapped = current.get('receipt') or {}
                    if (isinstance(document, dict) and document.get('probeId') == probe.probe_id
                            and document.get('candidateDigest') == self.bindings['candidateDigest']
                            and isinstance(wrapped.get('receiptRef'), str)
                            and document.get('receiptRef') == wrapped['receiptRef']
                            and current.get('source') == probe.to_dict()
                            and wrapped.get('candidateDigest') == self.bindings['candidateDigest']
                            and probe.probe_id not in next_value['challenges']):
                        next_value['challenges'].append(probe.probe_id)
                except (ValueError, TypeError, KeyError):
                    # Rejected inputs still have delivery provenance, but no
                    # executed challenge or source-reading authority.
                    return
            self._commit(next_value)
        return replace(result, _delivery_ack=acknowledge)

    def _anchors(self, rows, *, field_path='$.sourceEvidence'):
        try:
            anchors = self.source_anchors(rows) if rows else []
        except (ValueError, TypeError, KeyError, OSError) as error:
            # Use the same source validator only to locate an already-rejected
            # submitted range. This creates no source-read or challenge credit.
            diagnostics = []
            for index, anchor in enumerate(rows):
                try:
                    self.source_anchors([anchor])
                except (ValueError, TypeError, KeyError, OSError) as source_error:
                    diagnostics.append(_source_diagnostic(field_path, index, anchor,
                        str(source_error), 'source_validation'))
            raise _InspectionInputError(str(error), diagnostics or [{
                'path': field_path, 'code': 'source_validation', 'message': str(error)}]) from error
        diagnostics = []
        for index, anchor in enumerate(anchors):
            raw = (self.root / anchor['path']).read_bytes()
            text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            lines = text.splitlines(keepends=True)
            start = len(''.join(lines[:anchor['startLine'] - 1]))
            end = len(''.join(lines[:anchor['endLine']]))
            intervals = sorted((row['offset'], row['end']) for row in self.value['reads']
                if row['path'] == anchor['path'] and row['sourceDigest'] == anchor['sourceDigest'])
            reached = start
            for first, last in intervals:
                if first <= reached:
                    reached = max(reached, last)
            if reached < end:
                diagnostics.append(_source_diagnostic(field_path, index, anchor,
                    'read this current candidate source range before submitting inspection', 'source_not_read'))
        if diagnostics:
            raise _InspectionInputError('read these current candidate source anchors before submitting inspection', diagnostics)
        return anchors

    def submit(self, value, _context):
        field_path = '$'
        try:
            if self.value['result'] is not None:
                raise ValueError('inspection is already committed')
            diagnostics = _submission_diagnostics(value)
            if diagnostics:
                raise _InspectionInputError('inspection submission has invalid fields', diagnostics)
            if (not isinstance(value, dict) or set(value) != {'status', 'observations', 'findings'}
                    or value['status'] not in {'ready_for_evidence', 'changes_required', 'inconclusive'}
                    or not isinstance(value['observations'], list) or not isinstance(value['findings'], list)
                    or any(not isinstance(text, str) or not text.strip() for text in value['findings'])):
                raise ValueError('inspection requires status/observations/findings')
            rows, ids = [], {row['id'] for row in self.requirements}
            for row_index, row in enumerate(value['observations']):
                field_path = f'$.observations[{row_index}]'
                if (not isinstance(row, dict) or set(row) != {'requirementId', 'status', 'sourceEvidence', 'expectedBehavior', 'observedBehavior', 'probeIds'}
                        or row['requirementId'] not in ids or row['status'] not in {'checked', 'uncertain', 'missing'}
                        or any(not isinstance(row[key], str) or not row[key].strip() for key in ('expectedBehavior', 'observedBehavior'))
                        or not isinstance(row['probeIds'], list) or len(set(row['probeIds'])) != len(row['probeIds'])
                        or any(pid not in self.value['challenges'] for pid in row['probeIds'])):
                    raise ValueError('inspection observation must bind an original requirement and its own evidence')
                field_path = f'$.observations[{row_index}].sourceEvidence'
                anchors = self._anchors(row['sourceEvidence'], field_path=field_path)
                if row['status'] in {'checked', 'missing'} and not anchors:
                    raise ValueError('checked/missing observations require independently read candidate anchors')
                admitted = [pid for pid in row['probeIds'] if (self.probe_evidence(pid) or {}).get('receipt', {}).get('receipt', {}).get('status') == 'passed']
                rows.append({**row, 'sourceEvidence': anchors,
                    'probeIds': admitted if row['status'] == 'missing' else row['probeIds'],
                    **({'unresolvedProbeIds': [pid for pid in row['probeIds'] if pid not in admitted]} if row['status'] == 'missing' and admitted != row['probeIds'] else {})})
            field_path = '$'
            missing = [row for row in rows if row['status'] == 'missing']
            if value['status'] == 'ready_for_evidence':
                if missing or value['findings'] or not any(row['sourceEvidence'] for row in rows):
                    raise ValueError('ready requires actual independent source observations and no known missing behavior')
                next_value = {**self.value, 'result': {**value, 'observations': rows}}
                self._commit(next_value)
                return ToolResult(ok=True, output=canonical({'status': 'ready_for_evidence', 'inspectionRef': self.record['ref'], 'grantsCoverage': False}), _implementation_submitted=True)
            if not value['findings'] or (value['status'] == 'changes_required' and not missing):
                raise ValueError('changes_required needs a concrete independently source-backed missing behavior; uncertainty is inconclusive')
            result = self.finish_negative('changes_required' if missing else value['status'], rows, value['findings'], value)
            return ToolResult(ok=True, output=canonical(result), _implementation_submitted=True)
        except (ValueError, TypeError, KeyError, OSError) as error:
            diagnostics = (error.diagnostics if isinstance(error, _InspectionInputError) else [
                {'path': field_path, 'code': 'validation', 'message': str(error)}])
            return ToolResult(ok=False, output=canonical({'errorCode': 'coverage_inspection_invalid',
                'message': str(error), 'diagnostics': diagnostics}))

    def registry(self, tools):
        available = []
        for definition in tools.list():
            if definition.name not in _READ_TOOLS | {'review_test'}:
                continue
            if definition.name != 'review_test' and not definition.is_read_only:
                continue
            def run(value, context, definition=definition):
                disallowed = [key for key in _HISTORICAL_PROBE_FIELDS if key in value] if definition.name == 'review_test' else []
                if disallowed:
                    return ToolResult(ok=False, output=canonical({
                        'errorCode': 'coverage_inspection_invalid_probe_fields',
                        'message': 'Historical probe correction is unavailable during independent inspection.',
                        'disallowedFields': disallowed}))
                result = definition.run(value, context)
                return self._delivered(definition.name, value, result, definition.validator) if definition.name in {'read_file', 'review_test'} else result
            schema = definition.input_schema
            if definition.name == 'review_test':
                # Advertise the phase's actual capabilities without mutating the
                # shared later-phase definition or relaxing its runtime guard.
                schema = deepcopy(schema)
                for key in _HISTORICAL_PROBE_FIELDS:
                    schema.get('properties', {}).pop(key, None)
            available.append(replace(definition, run=run, input_schema=schema))
        schema = deepcopy(_SUBMIT_SCHEMA)
        available.append(ToolDefinition(name='completion_coverage_inspection_submit', description='Commit independent source observations or immediately report a concrete omission. No coverage approval is available in this phase.',
            input_schema=schema, validator=lambda value: value, run=self.submit, capability_profile=self.profile))
        return ToolRegistry(available, skills=[], mcp_servers=[])

    def disclose(self, messages, publish):
        if self.value['result'] is None:
            raise ValueError('cannot disclose reviewer proposal before independent inspection commits')
        previous_value, previous_record, previous_messages = self.value, self.record, self.manager.messages
        try:
            self._commit({**self.value, 'phase': 'reconciliation'})
            self.manager.messages = [*previous_messages, *messages]
            publish()
        except BaseException:
            # A persisted record alone cannot publish a phase without its actual
            # context. Keep the accepted inspection so retry discloses once.
            self.value, self.record, self.manager.messages = previous_value, previous_record, previous_messages
            raise
