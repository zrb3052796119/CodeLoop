from __future__ import annotations

import json

from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)


def _report() -> str:
    return json.dumps(
        {
            "resultVersion": 1,
            "summary": "Mapped the auth flow and its tests.",
            "files": [
                {"path": "minicode/auth.py", "action": "read"},
                {"path": "tests/test_auth.py", "action": "modified"},
            ],
            "risks": ["Legacy callers may omit the token."],
            "verification": {
                "status": "passed",
                "checks": ["pytest tests/test_auth.py"],
            },
        }
    )


def test_subagent_result_projects_report_under_parent_owned_identity() -> None:
    result = project_subagent_result(
        _report(),
        subagent_id="sub_" + "a" * 32,
        agent_type="general",
        outcome="completed",
        fallback_summary="unused",
    )

    assert result == {
        "resultVersion": 1,
        "subagentId": "sub_" + "a" * 32,
        "agentType": "general",
        "outcome": "completed",
        "contractStatus": "reported",
        "summary": "Mapped the auth flow and its tests.",
        "files": [
            {"path": "minicode/auth.py", "action": "read"},
            {"path": "tests/test_auth.py", "action": "modified"},
        ],
        "risks": ["Legacy callers may omit the token."],
        "verification": {
            "status": "passed",
            "checks": ["pytest tests/test_auth.py"],
        },
    }


def test_subagent_result_falls_back_without_inventing_evidence() -> None:
    result = project_subagent_result(
        None,
        subagent_id="sub_" + "b" * 32,
        agent_type="explore",
        outcome="completed",
        fallback_summary="plain final answer",
    )

    assert result["contractStatus"] == "fallback"
    assert result["summary"] == "plain final answer"
    assert result["files"] == []
    assert result["risks"] == []
    assert result["verification"] == {
        "status": "inconclusive",
        "checks": [],
    }


def test_subagent_result_rejects_extra_fields_and_oversized_values() -> None:
    raw = json.loads(_report())
    raw["prompt"] = "must not cross the result boundary"
    extra = project_subagent_result(
        json.dumps(raw),
        subagent_id="sub_" + "c" * 32,
        agent_type="plan",
        outcome="completed",
        fallback_summary="safe fallback",
    )
    raw.pop("prompt")
    raw["summary"] = "x" * 4001
    oversized = project_subagent_result(
        json.dumps(raw),
        subagent_id="sub_" + "d" * 32,
        agent_type="plan",
        outcome="completed",
        fallback_summary="safe fallback",
    )

    assert extra["contractStatus"] == "fallback"
    assert oversized["contractStatus"] == "fallback"


def test_rendered_subagent_result_has_a_round_trip_boundary() -> None:
    result = project_subagent_result(
        _report(),
        subagent_id="sub_" + "e" * 32,
        agent_type="general",
        outcome="completed",
        fallback_summary="unused",
    )
    output = "human narrative\n\n" + render_subagent_result(result)

    assert extract_subagent_result(output) == result
    assert extract_subagent_result("human narrative only") is None
