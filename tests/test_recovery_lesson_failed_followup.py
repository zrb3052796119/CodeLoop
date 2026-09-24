"""A bounded candidate projection must not erase an observed failed retry."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from minicode.recovery_lesson import (
    RecoveryLessonAgent, episode_to_task_evidence, extract_recovery_episodes,
)
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import ReflectionLLMConfig, StructuredGenerationResponse


def _case(case_id="retry-still-failed"):
    data = json.loads((Path(__file__).parent / "fixtures/recovery_lesson_shadow/cases.json").read_text())
    return next(case for case in data["cases"] if case["case_id"] == case_id)


def _base():
    case = _case("unverified-failed")
    trace = copy.deepcopy(case["trace"][:-1])
    trace[0]["input"]["cwd"] = "/workspace/app"
    return case["task_description"], trace


def _failed(call_id, *, path="tests/test_pending.py", cwd="/workspace/app", args=()):
    return [
        {"event_id": f"{call_id}-call", "type": "tool_call", "call_id": call_id,
         "tool_name": "run_command", "input": {"command": "python",
             "args": ["-m", "pytest", path, *args], "cwd": cwd}},
        {"event_id": f"{call_id}-result", "type": "tool_result", "call_id": call_id,
         "tool_name": "run_command", "status": "error", "is_error": True,
         "output_summary": "Operation incomplete; cause unknown."},
        {"event_id": f"{call_id}-error", "type": "error", "call_id": call_id,
         "tool_name": "run_command", "error_type": "ToolError",
         "message": "Operation incomplete; cause unknown."},
    ]


def _projection(task, trace):
    parent = TraceEvidenceExtractor().extract(task, trace)
    episode = extract_recovery_episodes(parent)[0]
    return parent, episode, episode_to_task_evidence(episode, parent)


def test_live_failed_retry_fixture_keeps_failed_check_invocation_and_positions():
    case = _case()
    parent, episode, sub = _projection(case["task_description"], case["trace"])

    assert episode.causal_status == "candidate"
    assert episode.verifications == ()
    assert episode.followup_calls == ()
    check = next(item for item in sub.verification if item.call_id == "call-3")
    assert check.result == "failed"
    assert check.summary == "Operation incomplete; cause unknown."
    assert next(item for item in sub.tool_calls if item.call_id == "call-3").invocation == "python -m pytest tests/test_pending.py"
    for event_id in ("event-000007", "event-000008", "event-000009"):
        assert sub.event_positions[event_id] == parent.event_positions[event_id]
    assert not any(item.result == "passed" for item in sub.verification)
    assert [item.error_id for item in sub.errors] == list(episode.related_error_ids)


def test_failed_retry_reaches_prompt_without_becoming_success_context():
    case = _case()
    parent = TraceEvidenceExtractor().extract(case["task_description"], case["trace"])

    class Capture:
        payload = None

        def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
            self.payload = json.loads(messages[-1]["content"])
            return StructuredGenerationResponse(text=json.dumps({
                "task_summary": case["task_description"], "outcome": "failed", "claims": [],
            }))

    client = Capture()
    result = RecoveryLessonAgent(client, ReflectionLLMConfig()).attempt_lessons(case["task_description"], parent)
    assert result.outcomes[0].failure_code is None
    context = client.payload["recovery_episode_context"]
    assert context["failed_call_ids"] == ["call-1"]
    assert context["followup_call_ids"] == []
    assert context["verification_ids"] == []
    assert "event-000008" in context["ordered_event_ids"]
    task = client.payload["task_evidence"]["task"]
    assert any(item["call_id"] == "call-3" and item["result"] == "failed" for item in task["verification"])
    assert any(item["call_id"] == "call-3" and item["status"] == "failed" for item in task["tools"])
    assert result.lessons == []


def test_relationship_filter_precedes_cap_and_rejects_path_cwd_and_selection_noise():
    task, trace = _base()
    for index in range(6):
        trace.extend(_failed(f"noise-{index}", path=f"tests/test_other_{index}.py"))
    trace.extend(_failed("other-cwd", cwd="/workspace/other"))
    trace.extend(_failed("other-selection", args=("-k", "subset")))
    trace.extend(_failed("related-retry"))
    _, _, sub = _projection(task, trace)

    assert [item.call_id for item in sub.verification] == ["related-retry"]
    assert {item.call_id for item in sub.tool_calls} == {"call-1", "related-retry"}


def test_related_failed_context_is_bounded_and_selected_in_time_order():
    task, trace = _base()
    for index in range(6):
        trace.extend(_failed(f"retry-{index}"))
    parent, episode, _ = _projection(task, trace)
    parent.verification.reverse()
    sub = episode_to_task_evidence(episode, parent)

    assert [item.call_id for item in sub.verification] == [f"retry-{index}" for index in range(4)]
    assert "retry-4-call" not in sub.event_positions


def test_later_independent_recovery_chain_does_not_supply_candidate_context():
    task, trace = _base()
    trace.extend(_failed("own-retry"))
    trace.extend(_failed("next-chain-root"))
    trace.append({"event_id": "next-recovery", "type": "recovery",
                  "action": "Attempted another change without successful verification.",
                  "related_error_call_ids": ["next-chain-root"]})
    trace.extend(_failed("next-chain-retry"))
    _, _, sub = _projection(task, trace)

    assert [item.call_id for item in sub.verification] == ["own-retry"]
    assert not any(event.startswith("next-") for event in sub.event_positions)


def test_same_target_before_recovery_is_not_a_followup():
    task, trace = _base()
    recovery = trace.pop()
    trace.extend(_failed("before-recovery"))
    trace.append(recovery)
    trace.extend(_failed("after-recovery"))
    _, _, sub = _projection(task, trace)
    assert [item.call_id for item in sub.verification] == ["after-recovery"]


@pytest.mark.parametrize("missing", ["retry-call", "retry-result"])
def test_missing_followup_timing_is_not_guessed(missing):
    task, trace = _base()
    trace.extend(_failed("retry"))
    parent, episode, _ = _projection(task, trace)
    parent.event_positions.pop(missing)
    sub = episode_to_task_evidence(episode, parent)
    assert sub.verification == []


def test_verified_episode_keeps_original_proof_without_later_failed_noise():
    case = _case("auth-expiry")
    baseline_parent, baseline_episode, baseline = _projection(case["task_description"], case["trace"])
    assert baseline_episode.causal_status == "verified"
    trace = copy.deepcopy(case["trace"])
    trace.extend(_failed("late-failure", path="tests/test_auth.py", cwd=None))
    _, episode, sub = _projection(case["task_description"], trace)
    assert episode.causal_status == "verified"
    assert sub.verification == baseline.verification
    assert sub.tool_calls == baseline.tool_calls
    assert sub.event_positions == baseline.event_positions

