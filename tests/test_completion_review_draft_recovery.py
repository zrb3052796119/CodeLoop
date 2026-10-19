"""The continuation bridge retains exact rejected input without rewriting history."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import run_requirement_completion_checkpoint as runner
from test_completion_review_draft import records, raw
from test_requirement_completion_checkpoint import terminal, write, cloned_script_tree


def archived_terminal(tmp_path):
    source, root, workspace, host = terminal(tmp_path)
    document = host.document
    original = tmp_path / 'original-evidence'
    document['reviewState']['evidenceDirectory'] = str(original)
    write(root / 'implementation-checkpoint.json', document)
    receipt = json.loads((root / 'implementation-checkpoint-receipt.json').read_bytes())
    receipt['documentSha256'] = runner.sha_file(root / 'implementation-checkpoint.json')
    write(root / 'implementation-checkpoint-receipt.json', receipt)
    directory = root / 'completion-review/review_a'
    started, finished = records(candidate=document['candidateDigest'])
    finished['invocationId'] = str(original / '00003-tool-evidence.json')
    (directory / '00003-tool-evidence.json').write_bytes(raw(started))
    (directory / '00005-tool-evidence.json').write_bytes(raw(finished))
    reseal(root)
    case = json.loads((source / 'tasks.json').read_bytes())['cases'][0]
    return source, root, workspace, document, runner.implementation_spec(case)


def reseal(root):
    paths = sorted((root / 'completion-review/review_a').iterdir())
    write(root / 'completion-review-archive.json', {'archiveComplete': True, 'errors': [], 'files': [
        {'file': str(path.relative_to(root)), 'sha256': runner.sha_file(path), 'bytes': path.stat().st_size}
        for path in paths]})


def test_prepare_restores_only_authenticated_draft_and_keeps_native_parent_unchanged(tmp_path, monkeypatch):
    source, root, workspace, document, spec = archived_terminal(tmp_path)
    before = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    candidate = {path: path.read_bytes() for path in workspace.iterdir() if path.is_file()}
    output = tmp_path / 'followup'
    cloned_script_tree(tmp_path, monkeypatch)
    runner.prepare(output, source)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert checkpoint['submissionDraftRestoration']['kind'] == 'unaccepted_native_tool_pair_reference'
    assert (output / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    restored = runner.restored_continuation(output, spec, messages).document
    draft = restored['reviewState']['pendingSubmissionDraft']
    assert restored == {**document, 'reviewState': {**document['reviewState'], 'pendingSubmissionDraft': draft}}
    assert draft['status'] == 'unaccepted'
    assert draft['started']['rawJson'].encode() == (root / 'completion-review/review_a/00003-tool-evidence.json').read_bytes()
    assert draft['finished']['rawJson'].encode() == (root / 'completion-review/review_a/00005-tool-evidence.json').read_bytes()
    assert (output / 'private/pending-submission-draft.json').stat().st_mode & 0o777 == 0o600
    runner.freeze(output)
    runner.validate(output)
    assert json.loads((output / 'private/review-state.json').read_bytes())['pendingSubmissionDraft'] == draft
    assert all(path.read_bytes() == content for path, content in before.items())
    assert all(path.read_bytes() == content for path, content in candidate.items())


@pytest.mark.parametrize('tamper', ['raw', 'pair', 'duplicate', 'sidecar', 'candidate'])
def test_archive_and_recovery_binding_tampering_fail_closed(tmp_path, tamper):
    source, root, workspace, _document, _spec = archived_terminal(tmp_path)
    output = tmp_path / 'followup'
    if tamper in {'sidecar', 'candidate'}:
        runner.prepare(output, source)
        if tamper == 'sidecar':
            with (output / 'private/pending-submission-draft.json').open('ab') as stream:
                stream.write(b' ')
        else:
            (workspace / 'example.py').write_text('CHANGED = True\n')
        with pytest.raises(ValueError):
            runner.load_checkpoint(output)
        return
    path = root / 'completion-review/review_a/00005-tool-evidence.json'
    finished = json.loads(path.read_bytes())
    if tamper == 'pair':
        finished['invocationId'] = '/tmp/forged/00003-tool-evidence.json'
    elif tamper == 'duplicate':
        (path.parent / '00006-tool-evidence.json').write_bytes(path.read_bytes())
    else:
        finished['output'] += ' overwritten'
    path.write_bytes(raw(finished))
    if tamper != 'raw':
        reseal(root)  # Even an authenticated record cannot forge the original pair.
    with pytest.raises(ValueError):
        runner.prepare(output, source)
    assert not output.exists()


def test_accepted_submission_is_not_recovered_as_a_draft(tmp_path):
    _, root, _, document, spec = archived_terminal(tmp_path)
    path = root / 'completion-review/review_a/00005-tool-evidence.json'
    value = json.loads(path.read_bytes())
    value['isError'] = False
    path.write_bytes(raw(value))
    reseal(root)
    assert runner.recover_submission_draft(root, document, spec) is None


def test_existing_native_draft_wins_without_replacing_any_state(tmp_path):
    _, root, _, document, spec = archived_terminal(tmp_path)
    draft = runner.recover_submission_draft(root, document, spec)
    document['reviewState']['pendingSubmissionDraft'] = draft
    before = deepcopy(document)
    assert runner.recover_submission_draft(root, document, spec) is None
    assert document == before
    assert draft['objectiveDigest'] == 'sha256:' + hashlib.sha256(spec.objective.encode()).hexdigest()


def test_old_state_without_submission_records_remains_byte_identical(tmp_path):
    source, root, _, host = terminal(tmp_path)
    output = tmp_path / 'followup'
    runner.prepare(output, source)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert 'submissionDraftRestoration' not in checkpoint
    assert runner.restored_continuation(output, runner.implementation_spec(
        json.loads((source / 'tasks.json').read_bytes())['cases'][0]), messages).document == host.document
    assert not (output / 'private/pending-submission-draft.json').exists()
    assert (root / 'implementation-checkpoint.json').read_bytes() == (output / 'private/host-continuation.json').read_bytes()
