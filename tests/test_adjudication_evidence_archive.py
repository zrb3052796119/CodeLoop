"""Retain real independent-review persistence without reading its semantics."""
import json
from pathlib import Path
import stat

import pytest

from minicode.completion_probe_migration import CompletionProbeMigration
from minicode.probe_failure_adjudication import ProbeFailureAdjudicator
from scripts import run_requirement_completion_checkpoint as runner


def spec():
    return runner.implementation_spec({'authorized_paths': ['stock.py'],
        'prompt': 'Implement stock holds and releases.',
        'verification_argv': ['python3', '-m', 'pytest', '-q']})


def manifest(root):
    return json.loads((root / 'adjudication-evidence-archive.json').read_bytes())


def test_distinct_real_adjudicators_are_archived_without_mixing_migration(tmp_path):
    owners = [ProbeFailureAdjudicator(spec()), ProbeFailureAdjudicator(spec())]
    migration = CompletionProbeMigration(spec())
    root = tmp_path / 'trial'
    with runner.MigrationEvidenceArchive(root), runner.AdjudicationEvidenceArchive(root):
        references = [owner._persist('decision', {'opaque': f'PRIVATE_EVIDENCE_{i}'})
                      for i, owner in enumerate(owners)]
        migration._persist('completed-unit', {'opaque': 'PRIVATE_OLD_MIGRATION'})
    document = manifest(root)
    assert document['archiveComplete'] and document['errors'] == []
    assert document['adjudicationDirectories'] == 2
    assert len({row['adjudicationId'] for row in document['files']}) == 2
    assert {runner.safe_path(root, row['file']).read_bytes() for row in document['files']} == {
        Path(reference).read_bytes() for reference in references}
    for row in document['files']:
        path = runner.safe_path(root, row['file'])
        assert row['sha256'] == runner.sha_file(path)
        assert row['bytes'] == path.stat().st_size
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    old = json.loads((root / 'migration-evidence-archive.json').read_bytes())
    assert len(old['files']) == 1 and old['migrationDirectories'] == 1
    assert all('adjudication-evidence' not in row['file'] for row in old['files'])
    assert 'PRIVATE_' not in json.dumps(document)


def test_actual_finally_context_is_retained_without_replacing_control_exception(tmp_path):
    owner = ProbeFailureAdjudicator(spec())
    original = ProbeFailureAdjudicator._persist
    failure = KeyboardInterrupt('stop this local review')
    root = tmp_path / 'trial'
    with pytest.raises(KeyboardInterrupt) as caught:
        with runner.AdjudicationEvidenceArchive(root):
            try:
                owner._persist('authority', {'objective': 'original stock requirement'})
                raise failure
            finally:
                reference = owner._persist('messages', {'messages': [{'role': 'tool', 'content': 'real receipt'}]})
    assert caught.value is failure and ProbeFailureAdjudicator._persist is original
    document = manifest(root)
    assert document['archiveComplete'] and len(document['files']) == 2
    row = next(row for row in document['files'] if row['file'].endswith('-messages.json'))
    assert runner.safe_path(root, row['file']).read_bytes() == Path(reference).read_bytes()


def test_archive_failure_is_reported_without_changing_real_persist_result(tmp_path, monkeypatch):
    owner = ProbeFailureAdjudicator(spec())
    archive = runner.AdjudicationEvidenceArchive(tmp_path / 'trial')

    def fail(*args):
        raise OSError('local archive unavailable')

    monkeypatch.setattr(archive, '_copy', fail)
    with archive:
        reference = owner._persist('decision', {'verdict': 'inconclusive'})
    assert json.loads(Path(reference).read_bytes()) == {'verdict': 'inconclusive'}
    document = manifest(archive.root)
    assert document['archiveComplete'] is False
    assert document['errors'] == [{'operation': 'copy', 'errorClass': 'OSError'}]


def test_prior_adjudication_archive_cannot_be_reused_or_overwritten(tmp_path):
    root = tmp_path / 'trial'
    with runner.AdjudicationEvidenceArchive(root):
        ProbeFailureAdjudicator(spec())._persist('decision', {'opaque': 'original'})
    before = (root / 'adjudication-evidence-archive.json').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        with runner.AdjudicationEvidenceArchive(root):
            pytest.fail('old evidence was reopened')
    assert (root / 'adjudication-evidence-archive.json').read_bytes() == before
