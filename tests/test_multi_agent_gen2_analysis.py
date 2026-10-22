from copy import deepcopy

import pytest

from scripts.analyze_multi_agent_gen2 import ARMS, CASES, assess


def fixture():
    manifest = {"phase": "gen2", "protocolVersion": "multi-agent-gen2-v1"}
    audit = {"valid": True, "mode": "final", "auditedTrials": 6, "expectedTrials": 6}
    rows = [{"case": case, "arm": arm, "repeat": 1, "success": True, "oracleSuccess": True,
             "regressionTestPassed": True, "scopePassed": True, "changedSourceFiles": ["minicode/target.py"],
             "processCleanup": {"cleanupComplete": True}, "usageComplete": True, "unknownUsageCalls": 0,
             "collaborationProtocolSatisfied": True, "implementationLeafRuns": 1}
            for case in CASES for arm in ARMS]
    return manifest, rows, audit


def test_four_repairs_with_each_arm_two_and_each_case_one_continue():
    manifest, rows, audit = fixture()
    rows[0]["success"] = rows[3]["success"] = False
    assert assess(manifest, rows, audit)["passed"]


def test_equal_perfect_scores_continue_without_superiority_claim():
    manifest, rows, audit = fixture()
    assert assess(manifest, rows, audit)["passed"]


@pytest.mark.parametrize("bad_field,bad_value", [
    ("usageComplete", False), ("unknownUsageCalls", 1),
    ("ledgerDamaged", True), ("scopePassed", False),
    ("collaborationProtocolSatisfied", False), ("implementationLeafRuns", 0),
])
def test_quality_or_protocol_failure_cannot_hide_behind_six_oracle_passes(bad_field, bad_value):
    manifest, rows, audit = fixture()
    rows[1][bad_field] = bad_value
    assert not assess(manifest, rows, audit)["passed"]


def test_four_repairs_concentrated_on_two_cases_do_not_continue():
    manifest, rows, audit = fixture()
    rows[0]["success"] = rows[1]["success"] = False
    assert not assess(manifest, rows, audit)["passed"]


def test_duplicate_or_missing_cell_and_invalid_audit_do_not_continue():
    manifest, rows, audit = fixture()
    duplicate = deepcopy(rows)
    duplicate[0] = duplicate[1]
    assert not assess(manifest, duplicate, audit)["passed"]
    assert not assess(manifest, rows[:-1], audit)["passed"]
    audit["valid"] = False
    assert not assess(manifest, rows, audit)["passed"]
