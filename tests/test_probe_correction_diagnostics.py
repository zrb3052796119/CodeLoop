"""Rejected probe edits identify their actual structural difference without relaxing admission."""

import json

import pytest

from minicode.review_probe_correction import validate_probe_correction_sources
from minicode.reviewer_verification import ReviewerProbe
from test_review_probe_correction import correction_case, probe_case, _input, _correction  # noqa: F401


def source(code):
    return ReviewerProbe.create(criterion_ids=['ac_behavior'], scenario='generic numeric contract', code=code).to_dict()


BASE = "def test_number():\n    value = int('bad fixture')\n    assert value == 3\n"


@pytest.mark.parametrize(('old', 'new', 'category'), [
    (BASE, BASE.replace('test_number', 'test_other'), 'testIdentities'),
    (BASE, BASE.replace('value == 3', 'value == 4'), 'assertions'),
    (BASE, BASE.replace("int('bad fixture')", "float('bad fixture')"), 'subjectCalls'),
    (BASE, BASE.replace("int('bad fixture')", "int('bad fixture') + 1"), 'observedBindings'),
    ("def test_number():\n    if True:\n        assert 1 == 2\n",
     "def test_number():\n    if False:\n        assert 1 == 2\n", 'controlFlow'),
    ("import pytest\ndef test_number():\n    with pytest.raises(ValueError):\n        int('bad')\n",
     "import pytest\ndef test_number():\n    with pytest.raises(TypeError):\n        int('bad')\n", 'pytestControls'),
    ("from builtins import int as number\ndef test_number():\n    value = number('3')\n    assert value == 3\n",
     "from builtins import float as number\ndef test_number():\n    value = number('3')\n    assert value == 3\n", 'importBindings'),
])
def test_rejected_correction_identifies_first_actual_difference(old, new, category):
    original = source(old)
    with pytest.raises(ValueError) as caught:
        validate_probe_correction_sources(original, source(new))
    diagnostics = getattr(caught.value, 'diagnostics', None)
    assert isinstance(diagnostics, dict), 'A generic rejection cannot identify which preserved component changed.'
    assert diagnostics['originalProbeId'] == original['probeId']
    difference = next(row for row in diagnostics['differences'] if row['category'] == category)
    assert difference['original']['present']
    assert difference['original']['value'] != difference['replacement'].get('value')
    assert diagnostics['sourceRecovery'] == {'tool': 'review_probe_read', 'action': 'source',
                                             'probeId': original['probeId']}
    if category == 'assertions':
        assert difference['originalLocation'] == {'line': 3, 'scope': 'test_number'}
        assert difference['replacementLocation'] == {'line': 3, 'scope': 'test_number'}
    diagnostics['differences'].clear()
    assert caught.value.diagnostics['differences'], 'Diagnostics must not expose mutable host state.'


def test_fixture_argument_change_and_extra_setup_call_remain_allowed():
    fixed = BASE.replace("int('bad fixture')", "int('3')").replace('    value =', '    print("setup")\n    value =')
    validate_probe_correction_sources(source(BASE), source(fixed))


def test_large_assertion_feedback_is_bounded_and_identifies_source():
    code = 'def test_number():\n    assert "' + 'x' * 12000 + '" == "expected"\n'
    with pytest.raises(ValueError) as caught:
        validate_probe_correction_sources(source(code), source(code.replace('expected', 'different')))
    diagnostics = getattr(caught.value, 'diagnostics', None)
    assert diagnostics, 'Rejected large probes still need recoverable, specific diagnostics.'
    row = diagnostics['differences'][0]
    assert row['category'] == 'assertions' and row['original']['truncated']
    assert 'expected' in row['original']['value'] and 'different' in row['replacement']['value']
    assert row['firstDifferingChar'] > 10000
    assert len(str(caught.value).encode()) < 8192


def test_tool_feedback_carries_difference_without_running_or_retiring_replacement(correction_case):  # noqa: F811
    _cap, ledger, registry, context, _store, attempts, publications, _calls = correction_case
    original = json.loads(registry.execute('review_test', _input(), context).output)
    response = registry.execute('review_test', _correction(original, code=BASE.replace('value == 3', 'value == 4')), context)
    assert not response.ok
    assert 'diagnostics=' in response.output, 'The model must receive the host mismatch, not only a generic policy error.'
    diagnostics, _ = json.JSONDecoder().raw_decode(response.output.split('diagnostics=', 1)[1])
    assert 'assertions' in {row['category'] for row in diagnostics['differences']}
    assert len(attempts) == 1 and publications == []
    assert [probe.probe_id for probe in ledger.probes()] == [original['probeId']]
