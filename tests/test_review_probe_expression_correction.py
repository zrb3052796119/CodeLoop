"""Generic stockroom examples exercise independent expression admission.

Actual pytest establishes the fixture failures and corrected executions; AST
admission alone is never treated as objective/source semantic authority.
"""

import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.review_probe_correction import validate_probe_correction_sources
from minicode.review_probe_expression_correction import validate_adjudicated_expression_correction
from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction
from minicode.reviewer_verification import ReviewerProbe


SUBJECT = '''class Stockroom:
    rows = (3, 4)

    def lookup(self, request):
        if request == {"sku": "pear", "variants": ["loose"]}:
            return {"stock": 3}
        return None
'''
INLINE = '''from stockroom import Stockroom

def test_stock():
    room = Stockroom()
    assert room.lookup({"sku": "pear:loose", "variants": []})["stock"] == 3
    assert room.rows == (3, 4)
'''
INLINE_FIXED = INLINE.replace('{"sku": "pear:loose", "variants": []}', '{"sku": "pear", "variants": ["loose"]}')
ACCESSOR = '''from stockroom import Stockroom

def test_stock():
    room = Stockroom()
    for quantity in room.list_rows():
        assert quantity > 0
    assert room.rows == (3, 4)
'''
ACCESSOR_FIXED = ACCESSOR.replace("room.list_rows()", "room.rows")


def source(code, *, criteria=("stock_count",), scenario="verify stock quantities"):
    return ReviewerProbe.create(criterion_ids=criteria, scenario=scenario, code=code).to_dict()


def _pytest(directory: Path, code: str):
    directory.mkdir()
    (directory / "stockroom.py").write_text(SUBJECT)
    (directory / "test_stock.py").write_text(code)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_stock.py"],
        cwd=directory, capture_output=True, text=True, timeout=15,
        env={**{key: value for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}},
             "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return result


@pytest.mark.parametrize("before,after,mode,error", [
    (INLINE, INLINE_FIXED, "fixture_inline_inputs", "TypeError"),
    (ACCESSOR, ACCESSOR_FIXED, "fixture_accessor", "AttributeError"),
])
def test_real_fixture_failure_then_pass_still_requires_independent_admission(tmp_path, before, after, mode, error):
    old_run, new_run = _pytest(tmp_path / "old", before), _pytest(tmp_path / "corrected", after)
    assert old_run.returncode == 1 and error in old_run.stdout, old_run.stdout + old_run.stderr
    assert new_run.returncode == 0 and "1 passed" in new_run.stdout, new_run.stdout + new_run.stderr
    old, new = source(before), source(after)
    assert validate_adjudicated_expression_correction(old, new) == {"mode": mode}
    # This is the production independently-adjudicated entry point. Running
    # these exact tests against the frozen old wrapper must fail this assertion.
    assert validate_adjudicated_probe_correction(old, new) == {"mode": mode}
    with pytest.raises(ValueError):
        validate_probe_correction_sources(old, new)


@pytest.mark.parametrize("after", [
    INLINE_FIXED.replace("room.lookup", "room.other"),
    INLINE_FIXED.replace('["stock"]', '["other"]'),
    INLINE_FIXED.replace("== 3", "== 4"),
    INLINE_FIXED.replace("== 3", "!= 3"),
    INLINE_FIXED.replace('{"sku": "pear", "variants": ["loose"]}', "request"),
    INLINE_FIXED.replace('["loose"]', "make_variants()"),
    INLINE_FIXED.replace('["loose"]', "[name for name in variants]"),
    INLINE_FIXED.replace("room.lookup(", "room.lookup(*"),
    INLINE_FIXED.replace('})["stock"]', '}, override=True)["stock"]'),
    INLINE_FIXED.replace('assert room.lookup({"sku": "pear", "variants": ["loose"]})["stock"] == 3', 'assert 3 == 3'),
    INLINE_FIXED.replace("    assert room.rows == (3, 4)\n", ""),
    INLINE_FIXED.replace("room = Stockroom()", "room = Replacement()"),
    INLINE_FIXED.replace("from stockroom import Stockroom", "from unrelated import Stockroom"),
    INLINE.replace("== 3", '== expected({"value": 3})'),
])
def test_inline_inputs_cannot_change_subject_predicate_expectation_or_other_ast(after):
    assert validate_adjudicated_expression_correction(source(INLINE), source(after)) is None


@pytest.mark.parametrize("after", [
    ACCESSOR_FIXED.replace("for quantity in room.rows:", "for quantity in other.rows:"),
    ACCESSOR_FIXED.replace("for quantity in room.rows:", "for quantity in []:"),
    ACCESSOR_FIXED.replace("for quantity in room.rows:", "for quantity in getattr(room, 'rows', []):"),
    ACCESSOR_FIXED.replace("for quantity in room.rows:", "for quantity in filter(None, room.rows):"),
    ACCESSOR_FIXED.replace("for quantity in room.rows:", "for quantity in room.__dict__:"),
    ACCESSOR_FIXED.replace("assert quantity > 0", "assert quantity >= 0"),
    ACCESSOR_FIXED.replace("room = Stockroom()", "room = Substitute()"),
    ACCESSOR.replace("room.list_rows()", "room.get_rows()"),
])
def test_accessor_cannot_swap_receiver_stub_filter_or_change_predicates(after):
    assert validate_adjudicated_expression_correction(source(ACCESSOR), source(after)) is None


def test_accessor_requires_zero_arguments_and_preserves_read_shape():
    assert validate_adjudicated_expression_correction(
        source(ACCESSOR.replace("room.list_rows()", "room.list_rows('all')")), source(ACCESSOR_FIXED),
    ) is None
    assert validate_adjudicated_expression_correction(
        source(ACCESSOR.replace("room.list_rows()", "room.list_rows(all=True)")), source(ACCESSOR_FIXED),
    ) is None


@pytest.mark.parametrize("change", ["identity", "criteria", "scenario", "identical"])
def test_expression_admission_requires_bound_distinct_sources(change):
    old, new = source(INLINE), source(INLINE_FIXED)
    if change == "identity":
        new = copy.deepcopy(new)
        new["sourceDigest"] = "sha256:" + "f" * 64
    elif change == "criteria":
        new = source(INLINE_FIXED, criteria=("different_requirement",))
    elif change == "scenario":
        new = source(INLINE_FIXED, scenario="unrelated scenario")
    else:
        new = copy.deepcopy(old)
    assert validate_adjudicated_expression_correction(old, new) is None


def test_inline_keyword_values_are_literal_inputs_but_expected_side_is_not():
    old = source("def test_stock():\n    assert stock(sku='pear:loose') == 3\n")
    new = source("def test_stock():\n    assert stock(sku='pear') == 3\n")
    assert validate_adjudicated_expression_correction(old, new) == {"mode": "fixture_inline_inputs"}
    old = source("def test_stock():\n    assert stock() == expected(sku='pear:loose')\n")
    new = source("def test_stock():\n    assert stock() == expected(sku='pear')\n")
    assert validate_adjudicated_expression_correction(old, new) is None


@pytest.mark.parametrize("replacement", ["set()", "[*(1, 2)]", "{**{'sku': 'pear'}}"])
def test_literal_inputs_cannot_add_evaluation_or_unpacking(replacement):
    old = source("def test_stock():\n    assert stock([]) == 3\n")
    new = source(f"def test_stock():\n    assert stock({replacement}) == 3\n")
    assert validate_adjudicated_expression_correction(old, new) is None


@pytest.mark.parametrize("before,after", [
    ("def test_stock():\n    assert stock() == expected.rows()\n",
     "def test_stock():\n    assert stock() == expected.rows\n"),
    ("def test_stock():\n    assert room.rows() == (3, 4)\n",
     "def test_stock():\n    assert room.rows == (3, 4)\n"),
    ("def test_stock():\n    assert valid(), room.description()\n",
     "def test_stock():\n    assert valid(), room.description\n"),
    ("import pytest\ndef test_stock():\n    with pytest.raises(errors.missing()):\n        assert stock() == 3\n",
     "import pytest\ndef test_stock():\n    with pytest.raises(errors.missing):\n        assert stock() == 3\n"),
    ("import pytest\ndef test_stock():\n    assert pytest.fail('a') == 3\n",
     "import pytest\ndef test_stock():\n    assert pytest.fail('b') == 3\n"),
    ("def test_stock():\n    for quantity in room.list_rows():\n        pass\n",
     "def test_stock():\n    for quantity in room.rows:\n        pass\n"),
    ("def test_stock():\n    stock(1)\n", "def test_stock():\n    stock(2)\n"),
])
def test_expression_correction_keeps_observations_and_pytest_controls(before, after):
    assert validate_adjudicated_expression_correction(source(before), source(after)) is None


@pytest.mark.parametrize("before,after", [(INLINE, INLINE_FIXED), (ACCESSOR, ACCESSOR_FIXED)])
def test_expression_correction_keeps_test_identity(before, after):
    assert validate_adjudicated_expression_correction(
        source(before), source(after.replace("def test_stock():", "def test_other():")),
    ) is None
