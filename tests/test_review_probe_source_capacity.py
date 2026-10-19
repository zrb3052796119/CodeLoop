"""Full host probe source uses data storage, never model contract authority."""

import hashlib
import json

import pytest

from minicode.scheduler_artifacts import (
    AssignmentArtifactChannel, MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifactError, SchedulerArtifactStore,
)
from minicode.subagent_mailbox import SubagentMailbox


def _source(code=None):
    code = code or ('# 完整反例 "\\\\"\n' * 2400 + 'def test_requirement():\n    assert True\n')
    digest = 'sha256:' + hashlib.sha256(code.encode()).hexdigest()
    identity = json.dumps({'criterionIds': ['behavior'], 'scenario': 'public requirement boundary',
                           'sourceDigest': digest}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    probe_id = 'review_probe_' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return {'probeVersion': 1, 'resultType': 'ReviewProbeSource.v1', 'probeId': probe_id,
            'criterionIds': ['behavior'], 'scenario': 'public requirement boundary',
            'code': code, 'sourceDigest': digest}


def _key(document):
    return 'artifact_review_probe_source_' + hashlib.sha256(document['probeId'].encode()).hexdigest()[:32]


def test_complete_probe_source_above_control_limit_is_preserved_and_readable():
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    source = _source()
    artifact = store.publish(_key(source), source, authority=authority)
    assert len(artifact.content.encode()) > 20_000
    assert json.loads(artifact.content) == source
    reader = store.create_assignment_channel(readable_keys=(_key(source),), write_key='artifact_review_result')
    assert reader.read(_key(source)).content == artifact.content
    assert json.loads(reader.read(_key(source)).content)['code'] == source['code']


def test_probe_source_namespace_and_type_are_host_only_even_below_twenty_kb():
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    source = _source('def test_requirement(): assert True\n')
    with pytest.raises(SchedulerArtifactError, match='host|scheduler'):
        store.create_assignment_channel(readable_keys=(), write_key=_key(source))
    with pytest.raises(ValueError, match='host'):
        AssignmentArtifactChannel(SubagentMailbox(), readable_keys=(), result_key=_key(source), author='reviewer')
    channel = store.create_assignment_channel(readable_keys=(), write_key='artifact_model_result')
    with pytest.raises(SchedulerArtifactError, match='host|scheduler'):
        channel.write(channel.write_key, json.dumps(source), author='reviewer')
    assert store.read(channel.write_key) is None
    with pytest.raises(SchedulerArtifactError, match='authority'):
        store.publish(_key(source), source, authority=object())


@pytest.mark.parametrize('change', [
    {'sourceDigest': 'sha256:' + '0' * 64}, {'probeVersion': True},
    {'probeId': 'review_probe_' + '0' * 24}, {'criterionIds': ['other']},
    {'criterionIds': ['behavior', 'behavior']}, {'scenario': 'another scenario'},
    {'scenario': '\ud800'}, {'code': '\ud800'}, {'unknown': 'extra'},
])
def test_host_source_still_requires_exact_shape_and_immutable_identity(change):
    authority = object()
    source = _source('def test_requirement(): assert True\n')
    key = _key(source)
    store = SchedulerArtifactStore(scheduler_authority=authority)
    with pytest.raises(SchedulerArtifactError):
        store.publish(key, {**source, **change}, authority=authority)
    assert store.read(key) is None


def test_probe_source_must_use_its_bound_artifact_namespace():
    authority = object()
    source = _source('def test_requirement(): assert True\n')
    store = SchedulerArtifactStore(scheduler_authority=authority)
    with pytest.raises(SchedulerArtifactError):
        store.publish('artifact_arbitrary_source', source, authority=authority)


def test_source_data_keeps_original_total_budget_and_model_contract_limit():
    authority = object()
    source = _source('#' + 'x' * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES // 2) + '\ndef test_one(): pass\n')
    store = SchedulerArtifactStore(scheduler_authority=authority)
    store.publish(_key(source), source, authority=authority)
    second = _source(source['code'] + '# second\n')
    with pytest.raises(SchedulerArtifactError, match='4 MiB'):
        store.publish(_key(second), second, authority=authority)
    assert store.read(_key(second)) is None
    oversized = _source('#' + 'x' * MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES + '\ndef test_one(): pass\n')
    with pytest.raises(SchedulerArtifactError, match='4 MiB'):
        SchedulerArtifactStore(scheduler_authority=authority).publish(_key(oversized), oversized, authority=authority)
    channel = store.create_assignment_channel(readable_keys=(), write_key='artifact_model_contract')
    with pytest.raises(SchedulerArtifactError, match='20,000'):
        channel.write(channel.write_key, json.dumps(source), author='reviewer')
    with pytest.raises(SchedulerArtifactError, match='20,000'):
        store.publish('artifact_regular_contract', {'code': 'x' * 24_000}, authority=authority)


def test_scenario_rejected_before_execution_cannot_become_an_unusable_active_probe():
    from test_reviewer_verification import INPUT, _case

    _cap, ledger, registry, context, calls, *_ = _case()
    result = registry.execute('review_test', {**INPUT, 'scenario': 'x' * 1201}, context)
    assert not result.ok and '1200' in result.output
    assert calls == [] and ledger.probes() == ()


@pytest.mark.parametrize('ids', [['x' * 81], [f'criterion{i}' for i in range(101)]])
def test_probe_metadata_stays_inside_existing_assignment_identifier_bounds(ids):
    from minicode.reviewer_verification import ReviewerProbe

    with pytest.raises(ValueError):
        ReviewerProbe.create(criterion_ids=ids, scenario='public requirement', code='def test_ok(): pass\n')
