"""Rejected native inputs remain exact reference material, never approval."""
from copy import deepcopy
import hashlib
import json

import pytest

from minicode.completion_review_draft import create_submission_draft, validate_submission_draft


CANDIDATE = 'sha256:' + 'a' * 64
OBJECTIVE = 'sha256:' + 'b' * 64
START = '/tmp/generic-review/00003-tool-evidence.json'
FINISH = '/tmp/generic-review/00005-tool-evidence.json'


def raw(value):
    return (json.dumps(value, ensure_ascii=False, indent=3) + '\n').encode()


def records(*, candidate=CANDIDATE, index=0, bound=False):
    identity = 'completion_review_' + hashlib.sha256(f'{candidate}:{index}'.encode()).hexdigest()[:24]
    started = {'role': 'completion_reviewer', 'reviewId': identity,
        'toolName': 'completion_review_submit', 'phase': 'started',
        'input': {'status': 'approved', 'requirements': [{'id': 'req_generic',
            'status': 'covered', 'rationale': '原始文字\n  spacing', 'probeIds': ['probe_generic']}],
            'findings': ['An unaccepted proposal is not approval.']},
        'output': '', 'isError': False, 'invocationId': None}
    finished = {**started, 'phase': 'finished', 'input': None, 'isError': True,
        'output': '{"errorCode":"completion_review_invalid","message":"coverage missing"}',
        'invocationId': START}
    if bound:
        started.update(candidateDigest=candidate, objectiveDigest=OBJECTIVE)
        finished.update(candidateDigest=candidate, objectiveDigest=OBJECTIVE)
    return started, finished


def capture(started=None, finished=None, **kwargs):
    old, new = records()
    return create_submission_draft(raw(old if started is None else started),
        raw(new if finished is None else finished), started_ref=START, finished_ref=FINISH,
        candidate_digest=CANDIDATE, objective_digest=OBJECTIVE, review_index=0, **kwargs)


@pytest.mark.parametrize('bound', [False, True])
def test_native_pair_preserves_exact_bytes_and_only_unaccepted_projection(bound):
    started, finished = records(bound=bound)
    draft = capture(started, finished)
    projection = validate_submission_draft(draft, objective_digest=OBJECTIVE, candidate_digest=CANDIDATE)
    assert draft['started']['rawJson'].encode() == raw(started)
    assert draft['finished']['rawJson'].encode() == raw(finished)
    assert draft['started']['sha256'] == 'sha256:' + hashlib.sha256(raw(started)).hexdigest()
    assert projection['status'] == 'unaccepted' and projection['input'] == started['input']
    assert projection['output'] == finished['output']
    assert projection['startedRef'] == START and projection['finishedRef'] == FINISH
    assert 'rawJson' not in json.dumps(projection)
    projection['input']['requirements'].clear()
    assert validate_submission_draft(draft, objective_digest=OBJECTIVE)['input'] == started['input']


@pytest.mark.parametrize('part,key,value', [
    ('start', 'phase', 'finished'), ('start', 'isError', True),
    ('start', 'invocationId', START), ('start', 'input', None),
    ('finish', 'isError', False), ('finish', 'phase', 'started'),
    ('finish', 'input', {}), ('finish', 'invocationId', '/tmp/forged/00003-tool-evidence.json'),
    ('finish', 'reviewId', 'completion_review_' + '0' * 24),
    ('finish', 'toolName', 'review_test'), ('finish', 'role', 'executor'),
])
def test_forged_or_accepted_pairs_are_not_drafts(part, key, value):
    started, finished = records()
    (started if part == 'start' else finished)[key] = value
    with pytest.raises(ValueError):
        capture(started, finished)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(status='approved'),
    lambda d: d.update(reviewIndex=1),
    lambda d: d.update(reviewIndex=True),
    lambda d: d['started'].update(rawJson=d['started']['rawJson'] + ' '),
    lambda d: d['finished'].update(ref='/tmp/other/00005-tool-evidence.json'),
    lambda d: d['finished'].update(ref='/tmp/generic-review/00001-tool-evidence.json'),
    lambda d: d.update(accepted=True),
])
def test_saved_draft_tampering_is_rejected(mutate):
    draft = capture()
    mutate(draft)
    with pytest.raises(ValueError):
        validate_submission_draft(draft, objective_digest=OBJECTIVE, candidate_digest=CANDIDATE)


def test_current_candidate_and_original_objective_cannot_drift():
    draft = capture()
    with pytest.raises(ValueError, match='candidate changed'):
        validate_submission_draft(draft, objective_digest=OBJECTIVE, candidate_digest='sha256:' + 'c' * 64)
    with pytest.raises(ValueError, match='objective changed'):
        validate_submission_draft(draft, objective_digest='sha256:' + 'c' * 64)
    started, finished = records(bound=True)
    finished['candidateDigest'] = 'sha256:' + 'c' * 64
    with pytest.raises(ValueError, match='binding changed'):
        capture(started, finished)


def test_duplicate_json_keys_cannot_ambiguously_preserve_an_input():
    draft = capture()
    record = draft['started']
    record['rawJson'] = record['rawJson'].replace('"isError": false', '"isError": true, "isError": false')
    record['sha256'] = 'sha256:' + hashlib.sha256(record['rawJson'].encode()).hexdigest()
    with pytest.raises(ValueError, match='duplicate'):
        validate_submission_draft(draft, objective_digest=OBJECTIVE)


def test_validator_does_not_mutate_saved_state():
    draft = capture()
    before = deepcopy(draft)
    validate_submission_draft(draft, objective_digest=OBJECTIVE)
    assert draft == before
