"""Offline freeze must distinguish behavioral failures from broken fixtures."""
import json

import pytest

from scripts.prepare_multi_agent_gen2_tasks import target_failure_proven


REFERENCE = {"returncode": 0, "stdout": json.dumps({"passed": True, "errors": []})}


@pytest.mark.parametrize("baseline", [
    {"returncode": 1, "stdout": "", "stderr": "ImportError: missing module"},
    {"returncode": 2, "stdout": json.dumps({"passed": False, "errors": ["fixture unavailable"]})},
    {"returncode": 1, "stdout": json.dumps({"passed": False, "errors": []})},
    {"returncode": 0, "stdout": json.dumps({"passed": True, "errors": []})},
    {"returncode": 1, "stdout": "[]"},
])
def test_setup_error_or_green_baseline_is_not_a_real_counterexample(baseline):
    assert not target_failure_proven(baseline, REFERENCE)


def test_actual_behavior_failure_and_fixed_success_are_required():
    baseline = {"returncode": 1, "stdout": json.dumps({"ok": False, "errors": ["schema_does_not_fit: expected 0 requests, got 1"]})}
    assert target_failure_proven(baseline, REFERENCE)
    assert not target_failure_proven(baseline, {"returncode": 1, "stdout": REFERENCE["stdout"]})
