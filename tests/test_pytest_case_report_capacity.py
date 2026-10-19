"""Complete pytest identities share the managed report's byte envelope."""

import copy
import hashlib
import json

from minicode.review_probe_correction import validate_pytest_case_report
from minicode.review_probe_execution import run_isolated_review_probe
from test_review_probe_execution import _probe, probe_case  # noqa: F401


def _seal(report):
    encoded = json.dumps({"junitDigest": report["reportDigest"],
                          "cases": report["caseReport"]["cases"]},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    report["caseReport"]["digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return report


def test_real_pytest_retains_distinct_long_parameter_ids(probe_case):  # noqa: F811
    _transaction, frozen, _target, _initial, _calls, options = probe_case
    source = ("import pytest\n"
              "@pytest.mark.parametrize('value', [1, 2], ids=['x' * 9000 + 'a', 'x' * 9000 + 'b'])\n"
              "def test_long_parameter(value): assert value in (1, 2)\n")
    result = run_isolated_review_probe(probe=_probe(source), frozen=frozen, **options)
    assert result["status"] == "passed"
    report = result["testReport"]
    assert validate_pytest_case_report(report)
    cases = report["caseReport"]["cases"]
    probe_target = result["executionBinding"]["probeTarget"]
    assert [case["nodeId"] for case in cases] == [
        probe_target + "::test_long_parameter[" + "x" * 9000 + suffix + "]"
        for suffix in ("a", "b")
    ]
    tampered = copy.deepcopy(report)
    tampered["caseReport"]["cases"][1]["nodeId"] += "changed"
    assert not validate_pytest_case_report(tampered)
    duplicate = copy.deepcopy(report)
    duplicate["caseReport"]["cases"][1]["nodeId"] = cases[0]["nodeId"]
    assert not validate_pytest_case_report(_seal(duplicate))


def test_report_capacity_counts_utf8_bytes_across_all_cases():
    report = {"collected": 300, "passed": 300, "failed": 0, "errors": 0, "skipped": 0,
              "reportDigest": "sha256:" + "a" * 64,
              "caseReport": {"cases": [
                  {"nodeId": str(index) + "界" * 2000, "outcome": "passed", "failureKind": None}
                  for index in range(300)]}}
    assert not validate_pytest_case_report(_seal(report))
