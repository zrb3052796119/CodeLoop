"""Fixture corrections may not redirect an unchanged observed call via imports."""
# ruff: noqa: F811 - pytest fixture imported from the real execution fixture

import pytest

from minicode.review_probe_correction import validate_probe_correction_sources
from minicode.reviewer_verification import ReviewerProbe
from test_assertion_probe_fixture_correction import (
    FIXED, _correct, _failed_original, store_case,  # noqa: F401 - shared real pytest fixture
)


def _source(code):
    return ReviewerProbe.create(criterion_ids=['behavior'], scenario='Observe the public value.', code=code).to_dict()


@pytest.mark.parametrize(('old', 'new'), [
    ('from candidate import value', 'from fixture_helpers import value'),
    ('from candidate import value', 'from candidate import other as value'),
    ('import candidate as subject', 'import fixture_helpers as subject'),
    ('import candidate.api', 'import candidate.fake'),
    ('from .candidate import value', 'from ..candidate import value'),
    ('from candidate import EXPECTED', 'from fixture_helpers import EXPECTED'),
    ('from candidate import value', 'from candidate import value\nfrom fixture_helpers import *'),
])
def test_correction_preserves_original_observed_import_binding(old, new):
    if 'subject' in old:
        body = '    result = subject.value()\n    assert result == 1\n'
    elif old.startswith('import candidate.'):
        body = '    result = candidate.api.value()\n    assert result == 1\n'
    elif 'EXPECTED' in old:
        body = '    result = EXPECTED\n    assert result == 1\n'
    else:
        body = '    result = value()\n    assert result == 1\n'
    suffix = '\ndef test_value():\n' + body
    with pytest.raises(ValueError, match='import binding'):
        validate_probe_correction_sources(_source(old + suffix), _source(new + suffix))


def test_correction_cannot_shadow_observed_import_in_test_scope():
    old = 'from candidate import value\ndef test_value():\n    result = value()\n    assert result == 1\n'
    new = old.replace('    result', '    from fixture_helpers import value\n    result')
    with pytest.raises(ValueError, match='import binding'):
        validate_probe_correction_sources(_source(old), _source(new))


@pytest.mark.parametrize('extra', ['import pathlib\n', 'from candidate import unused_helper\n'])
def test_correction_allows_unrelated_setup_import(extra):
    old = 'from candidate import value\ndef test_value():\n    result = value(0)\n    assert result == 1\n'
    new = extra + old.replace('value(0)', 'value(1)')
    validate_probe_correction_sources(_source(old), _source(new))


def test_real_probe_correction_rejects_redirect_before_execution_or_retirement(store_case):
    case = store_case()
    old = _failed_original(case)
    # The module is deliberately absent: importing/running it is already too late.
    redirected = FIXED.replace('from candidate import Store, render', 'from fixture_helpers import Store, render')
    result = case.registry.execute('review_test', _correct(old, redirected), case.context)
    assert not result.ok and 'review_probe_correction_rejected' in result.output
    assert 'import binding' in result.output
    assert len(case.attempts) == 1 and not case.publications
    assert [probe.probe_id for probe in case.ledger.probes()] == [old['probeId']]
