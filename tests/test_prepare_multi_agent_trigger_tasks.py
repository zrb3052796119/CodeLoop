"""Offline counterexamples and source separation for the synthetic task pack."""
from copy import deepcopy
import json
import sys

import pytest

from scripts import prepare_multi_agent_trigger_tasks as prepare


def receipt(*, passed: bool, actual: int) -> dict:
    return {"returncode": 0 if passed else 1,
            "stdout": json.dumps({"passed": passed, "errors": [] if passed else ["target"],
                                  "checks": [{"label": "target", "actual": actual,
                                              "expected": 1, "passed": passed}]})}


@pytest.mark.parametrize("bad", [
    {"returncode": 1, "stdout": "", "stderr": "ImportError: absent"},
    {"returncode": 2, "stdout": receipt(passed=False, actual=0)["stdout"]},
    {"returncode": 1, "stdout": json.dumps({"passed": False, "errors": ["setup"]})},
    {"returncode": 1, "stdout": "[]"},
    receipt(passed=True, actual=1),
    receipt(passed=False, actual=1),
])
def test_invalid_or_inconsistent_baseline_evidence_is_rejected(bad):
    assert not prepare.target_failure_proven(bad, receipt(passed=True, actual=1))


def test_behavior_evidence_requires_matching_check_identity_and_green_reference():
    before, after = receipt(passed=False, actual=0), receipt(passed=True, actual=1)
    assert prepare.target_failure_proven(before, after)
    changed = json.loads(after["stdout"])
    changed["checks"][0]["label"] = "different target"
    assert not prepare.target_failure_proven(before, {**after, "stdout": json.dumps(changed)})
    assert not prepare.target_failure_proven(before, receipt(passed=False, actual=0))


def test_check_receipts_capture_values_before_later_mutation(tmp_path):
    prepare.materialize(tmp_path, prepare.SHARED_FILES)
    path = tmp_path / "oracle.py"
    path.write_text(prepare.oracle('''
        actual = {"items": [1]}
        expected = {"items": [1]}
        check("before_mutation", actual, expected)
        actual["items"].append(2)
        expected["items"].append(3)
    '''))
    result = prepare.run([sys.executable, "-B", str(path), str(tmp_path)], tmp_path)
    assert result["returncode"] == 0
    check = json.loads(result["stdout"])["checks"][0]
    assert check == {"label": "before_mutation", "actual": {"items": [1]},
                     "expected": {"items": [1]}, "passed": True}


def test_real_import_failure_cannot_pass_the_behavior_proof(tmp_path):
    value, _ = prepare.limit_clamp()
    prepare.materialize(tmp_path, {**prepare.SHARED_FILES, **value["files"],
                                   "minicode/limits.py": "raise ImportError('fixture missing')\n"})
    path = tmp_path / "oracle.py"
    path.write_text(value["oracle_source"])
    result = prepare.run([sys.executable, "-B", str(path), str(tmp_path)], tmp_path)
    assert result["returncode"] != 0
    assert "ImportError" in result["stderr"]
    assert not prepare.target_failure_proven(result, receipt(passed=True, actual=1))


def test_all_eight_real_public_and_hidden_checks_and_material_separation(tmp_path):
    output = tmp_path / "pack"
    prepare.prepare(output)
    tasks = json.loads((output / "tasks.json").read_text())
    references = json.loads((output / "reference-fixes.json").read_text())
    proof = json.loads((output / "offline-proof.json").read_text())
    manifest = json.loads((output / "source-manifest.json").read_text())
    assert tuple(case["id"] for case in tasks["cases"]) == prepare.CASES
    assert [case["triggerExpectation"] for case in tasks["cases"]] == ["positive"] * 6 + ["negative"] * 2
    assert len(proof) == 8
    for value, fixed, evidence in zip(tasks["cases"], references["cases"], proof):
        assert value["id"] == fixed["id"] == evidence["case"]
        assert evidence["baselinePublic"]["returncode"] == 0
        assert evidence["referencePublic"]["returncode"] == 0
        assert prepare.target_failure_proven(evidence["baselineOracle"], evidence["referenceOracle"])
        public_files = {**tasks["shared_files"], **value["files"]}
        assert "tests/test_eval_regression.py" not in public_files
        assert set(fixed["files"]) == set(value["sourcePaths"])
        assert set(value["authorized_paths"]) == {*value["sourcePaths"], "tests/test_eval_regression.py"}
        assert set(value["files"]) == {*value["sourcePaths"], "tests/test_public.py"}
        assert all(value["oracle_source"] != content for content in public_files.values())
        assert any(value["files"][path] != content for path, content in fixed["files"].items())
    assert manifest["taskSha256"] == prepare.sha((output / "tasks.json").read_bytes())
    assert manifest["referenceFixesSha256"] == prepare.sha((output / "reference-fixes.json").read_bytes())
    original = deepcopy(tasks)
    with pytest.raises(ValueError, match="already exists"):
        prepare.prepare(output)
    assert json.loads((output / "tasks.json").read_text()) == original
