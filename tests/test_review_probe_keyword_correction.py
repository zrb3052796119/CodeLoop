"""Independent authority can repair missing setup keywords without rewriting tests."""

import pytest

from minicode.review_probe_correction import validate_probe_correction_sources
from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction
from minicode.reviewer_verification import ReviewerProbe


CODE = (
    "from stock import release\n"
    "def test_inventory(tmp_path):\n"
    "    folder = tmp_path / 'inventory'\n"
    "    folder.mkdir()\n"
    "    folder.mkdir()\n"
    "    observed = release(7, 3)\n"
    "    assert observed == 10\n"
    "    assert folder.is_dir()\n"
)
FIXED = CODE.replace("folder.mkdir()\n    observed", "folder.mkdir(exist_ok=True)\n    observed")


def source(code=CODE, *, criteria=("inventory",), scenario="Release changes quantity"):
    return ReviewerProbe.create(criterion_ids=criteria, scenario=scenario, code=code).to_dict()


def test_independent_admission_allows_only_the_added_static_fixture_keyword():
    assert validate_adjudicated_probe_correction(source(), source(FIXED)) == {"mode": "fixture_keywords"}
    # This helper supplies structural admission, never its own semantic authority.
    with pytest.raises(ValueError):
        validate_probe_correction_sources(source(), source(FIXED))


def test_keyword_addition_is_not_an_api_allowlist_and_preserves_original_keyword_values():
    old = CODE.replace("folder.mkdir()", "prepare_workspace(folder, mode=0o700)")
    new = old.replace("prepare_workspace(folder, mode=0o700)\n    observed",
                      "prepare_workspace(folder, mode=0o700, reusable=True, labels=('stock',))\n    observed")
    assert validate_adjudicated_probe_correction(source(old), source(new))["mode"] == "fixture_keywords"
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(old), source(new.replace("mode=0o700, reusable", "mode=0o777, reusable")))


@pytest.mark.parametrize("new", [
    FIXED.replace("== 10", "== 999"),
    FIXED.replace("assert observed == 10", "assert True"),
    FIXED.replace("    assert folder.is_dir()\n", ""),
    FIXED.replace("release(7, 3)", "release(7, 0)"),
    FIXED.replace("observed = release", "observed = fake_release"),
    FIXED.replace("    observed = release(7, 3)\n", "    observed = 10\n"),
    FIXED.replace("from stock import release", "from fake_stock import release"),
    FIXED.replace("def test_inventory", "def test_another"),
    FIXED.replace("    assert observed", "    if False:\n        assert observed"),
    FIXED.replace("    folder.mkdir()\n", ""),
    FIXED.replace("    observed = release", "    disable_validation()\n    observed = release"),
    FIXED.replace("mkdir(exist_ok=True)", "create(exist_ok=True)"),
    FIXED.replace("mkdir(exist_ok=True)", "mkdir(0o777, exist_ok=True)"),
    FIXED.replace("mkdir(exist_ok=True)", "mkdir(exist_ok=True, exist_ok=False)"),
])
def test_keyword_admission_cannot_mix_changes_to_business_calls_assertions_or_flow(new):
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(), source(new))


@pytest.mark.parametrize("expression", [
    "allow_existing", "options.allow_existing", "authorize()", "set()",
    "[item for item in values]", "(lambda: True)", "(observed := True)",
    "{'x': execute()}", "[*values]",
])
def test_new_keywords_cannot_add_dynamic_evaluation_or_side_effects(expression):
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(), source(FIXED.replace("exist_ok=True", "exist_ok=" + expression)))


@pytest.mark.parametrize("old_call,new_call", [
    ("folder.mkdir(mode=0o700)", "folder.mkdir(exist_ok=True)"),
    ("folder.mkdir(mode=0o700)", "folder.mkdir(permissions=0o700)"),
    ("folder.mkdir(*arguments)", "folder.mkdir(*arguments, exist_ok=True)"),
    ("folder.mkdir(**options)", "folder.mkdir(**options, exist_ok=True)"),
    ("folder.mkdir()", "folder.mkdir(**{'exist_ok': True})"),
])
def test_no_keyword_removal_rename_or_argument_unpacking(old_call, new_call):
    old = CODE.replace("folder.mkdir()", old_call)
    new = old.replace(old_call + "\n    observed", new_call + "\n    observed")
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(old), source(new))


@pytest.mark.parametrize("old,new", [
    ("def test_x():\n    assert check()\n", "def test_x():\n    assert check(ignore=True)\n"),
    ("@wrapper()\ndef test_x():\n    assert True\n", "@wrapper(skip=True)\ndef test_x():\n    assert True\n"),
    ("import pytest\ndef test_x():\n    with pytest.raises(ValueError):\n        run()\n",
     "import pytest\ndef test_x():\n    with pytest.raises(ValueError, match='anything'):\n        run()\n"),
])
def test_keyword_admission_preserves_assertion_calls_decorators_and_pytest_controls(old, new):
    with pytest.raises(ValueError):
        validate_adjudicated_probe_correction(source(old), source(new))


def test_keyword_admission_keeps_criteria_and_scenario_bound():
    for replacement in (source(FIXED, criteria=("other",)), source(FIXED, scenario="Different goal")):
        with pytest.raises(ValueError):
            validate_adjudicated_probe_correction(source(), replacement)
