"""Native audit phases obey their actual shared request admission views."""

from copy import deepcopy

import pytest

from minicode.agent_budget import AgentBudgetExceeded
from minicode.completion_review_limits import create_review_limits
from minicode.coverage_audit_state import record_document
import test_completion_coverage_audit as native
import test_independent_coverage_inspection as inspection


@pytest.mark.parametrize("phase, expected", [("inspection", 8), ("reconciliation", 6)])
def test_real_audit_stops_at_phase_cap_and_retains_same_context(tmp_path, monkeypatch, phase, expected):
    case = native._case(tmp_path, monkeypatch)
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "bounded")
    host = case["host"]
    parent = host._agent_budget
    host._completion_review_limits = create_review_limits(host)
    actions = ([inspection.read_source()] * 8 if phase == "inspection" else
        [inspection.read_source(), ("completion_coverage_inspection_submit", inspection.inspection(case)),
         *[native._read(case)] * 4])
    requests = inspection.scripted(monkeypatch, case, actions)
    states = []
    with pytest.raises(AgentBudgetExceeded):
        inspection.assess(case, sink=lambda state: states.append(deepcopy(state)))
    assert len(requests) == expected
    assert parent.snapshot().used_model_calls == expected
    limits = host._completion_review_limits
    assert limits["stages"][phase]["budget"].admitted_model_calls == (8 if phase == "inspection" else 4)
    saved = states[-1]
    assert saved["candidateDigest"] == case["candidate_digest"]
    phase_state = record_document(saved["inspectionRecord"])
    assert phase_state["phase"] == phase
    assert phase_state["reads"]
    if phase == "reconciliation":
        assert phase_state["result"]["status"] == "ready_for_evidence"
    # Same-turn reentry may not grant another local cap or replace the actual
    # auditor context. A later parent turn can explicitly resume that evidence.
    with pytest.raises(AgentBudgetExceeded):
        inspection.assess(case, state=saved)
    assert len(requests) == expected
    assert parent.snapshot().used_model_calls == expected
