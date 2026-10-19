"""A semantic authority may fix an expected scalar, never rewrite the tested behavior."""

import pytest

from minicode.reviewer_verification import ReviewerProbe


CODE = "from stock import release\ndef test_release():\n    quantity = release(7, 3)\n    quantity = release(quantity, 4)\n    assert quantity == 10\n"


def source(code=CODE, criteria=("req_inventory",), scenario="Two releases accumulate"):
    return ReviewerProbe.create(criterion_ids=criteria, scenario=scenario, code=code).to_dict()


def test_expected_literal_only_preserves_the_complete_experiment():
    from minicode.review_probe_semantic_correction import validate_semantic_probe_correction
    result = validate_semantic_probe_correction(source(), source(CODE.replace("== 10", "== 14")))
    assert result["changedAssertions"] == [{"line": 5, "oldExpected": 10, "newExpected": 14}]


@pytest.mark.parametrize("new", [
    CODE.replace("== 10", ">= 10"),
    CODE.replace("assert quantity == 10", "assert True"),
    CODE.replace("release(quantity, 4)", "release(quantity, 0)").replace("== 10", "== 14"),
    CODE.replace("    quantity = release(quantity, 4)\n", "").replace("== 10", "== 14"),
    CODE.replace("== 10", "== quantity"),
    CODE.replace("== 10", "== True"),
    CODE.replace("== 10", "== 14").replace("def test_release", "def test_weakened"),
    CODE.replace("assert quantity == 10", "if False:\n        assert quantity == 14"),
    CODE.replace("from stock import release", "from fake_stock import release").replace("== 10", "== 14"),
    CODE,
])
def test_no_semantic_authority_can_use_literal_correction_to_rewrite_the_experiment(new):
    from minicode.review_probe_semantic_correction import validate_semantic_probe_correction
    with pytest.raises(ValueError):
        validate_semantic_probe_correction(source(), source(new))


def test_criteria_and_scenario_cannot_be_laundered():
    from minicode.review_probe_semantic_correction import validate_semantic_probe_correction
    for new in (source(CODE.replace("== 10", "== 14"), criteria=("req_other",)),
                source(CODE.replace("== 10", "== 14"), scenario="No longer two releases")):
        with pytest.raises(ValueError):
            validate_semantic_probe_correction(source(), new)


def test_numeric_expectation_family_allows_fractional_average_without_boolean_or_infinity():
    from minicode.review_probe_semantic_correction import validate_semantic_probe_correction
    code = "from statistics import mean\ndef test_average():\n    assert mean([0, 1]) == 0\n"
    result = validate_semantic_probe_correction(source(code), source(code.replace("== 0", "== 0.5")))
    assert result["changedAssertions"] == [{"line": 3, "oldExpected": 0, "newExpected": 0.5}]
    for expected in ("False", "1e999", "float('nan')", "float('inf')"):
        with pytest.raises(ValueError):
            validate_semantic_probe_correction(source(code), source(code.replace("== 0", "== " + expected)))


def test_independent_fixture_admission_preserves_assertions_and_is_host_classified():
    from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction
    fixture = "from stock import release\ndef test_release():\n    observed = release(7, 3)\n    assert observed == 14\n"
    fixed = fixture.replace("release(7, 3)", "release(7, 7)")
    assert validate_adjudicated_probe_correction(source(fixture), source(fixed)) == {"mode": "fixture_inputs"}
    literal = validate_adjudicated_probe_correction(source(fixture), source(fixture.replace("== 14", "== 10")))
    assert literal["mode"] == "expected_literal"
    # A replacement cannot combine relaxed expectations with altered inputs.
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(fixture), source(fixed.replace("== 14", "== 7")))
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(fixture), source(fixed, scenario="Another requirement"))
