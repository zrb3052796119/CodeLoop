"""Independent fixture-name repairs retain the full acceptance experiment."""

import pytest

from minicode.reviewer_verification import ReviewerProbe
from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction


SOURCE = ("from stock import hold\n"
          "def test_inventory():\n"
          "    _remaining = hold(5, 2)\n"
          "    assert remaining == 3\n")


def probe(code):
    return ReviewerProbe.create(criterion_ids=["stock"], scenario="reserve inventory", code=code).to_dict()


@pytest.mark.parametrize("fixed", [
    SOURCE.replace("_remaining =", "remaining ="),
    SOURCE.replace("    assert", "    remaining = _remaining\n    assert"),
])
def test_binding_repair_preserves_the_call_and_assertion(fixed):
    assert validate_adjudicated_probe_correction(probe(SOURCE), probe(fixed))["mode"] == "fixture_bindings"


def test_tuple_fixture_binding_can_recover_an_unbound_observed_name():
    old = SOURCE.replace("_remaining = hold(5, 2)", "unused, _remaining = inventory_fixture()")
    new = old.replace("unused, _remaining", "unused, remaining")
    assert validate_adjudicated_probe_correction(probe(old), probe(new))["mode"] == "fixture_bindings"


@pytest.mark.parametrize("fixed", [
    SOURCE.replace("_remaining =", "remaining =").replace("== 3", "== 4"),
    SOURCE.replace("_remaining =", "remaining =").replace("hold(5, 2)", "hold(8, 5)"),
    SOURCE.replace("    assert", "    remaining = 3\n    assert"),
    SOURCE.replace("    assert", "    remaining = fake_result\n    assert"),
    SOURCE.replace("    assert", "    remaining = hold(5, 2)\n    assert"),
    SOURCE.replace("    assert remaining == 3", "    remaining = _remaining\n    return"),
    SOURCE.replace("    assert", "    remaining = _remaining\n    if False:\n        assert"),
])
def test_binding_repair_cannot_change_or_bypass_the_experiment(fixed):
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(probe(SOURCE), probe(fixed))


@pytest.mark.parametrize("prefix", ["remaining = 3\n", "from stock import remaining\n"])
def test_fixture_binding_cannot_shadow_an_existing_module_binding(prefix):
    old = prefix + SOURCE
    new = old.replace("_remaining =", "remaining =")
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(probe(old), probe(new))


def test_used_assignment_destination_cannot_be_renamed():
    old = SOURCE.replace("    assert remaining", "    assert _remaining >= 0\n    assert remaining")
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(probe(old), probe(old.replace("_remaining =", "remaining =")))


def test_alias_must_use_an_already_bound_local():
    new = SOURCE.replace("    _remaining =", "    remaining = _remaining\n    _remaining =")
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(probe(SOURCE), probe(new))


def test_name_repair_does_not_authorize_rewriting_the_scenario():
    fixed = probe(SOURCE.replace("_remaining =", "remaining ="))
    altered = ReviewerProbe.create(criterion_ids=["stock"], scenario="different requirement", code=fixed["code"])
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(probe(SOURCE), altered.to_dict())
