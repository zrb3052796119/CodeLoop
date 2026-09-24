"""Read-only, offline counterexamples for the second recovery-lesson review.

Run from the repository root with:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/second-fix-review/episode_probe.py
No provider or durable-memory APIs are called.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "recovery_fixture", ROOT / "tests/test_recovery_lesson.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

from minicode.recovery_lesson import (
    episode_to_task_evidence,
    extract_recovery_episodes,
)
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from minicode.reflection_synthesis import ReflectionClaimValidator


def followup_slots_consumed_by_same_interpreter():
    trace = fixture._timeout_retry_trace()
    trace[3:3] = fixture._call(
        4, "noise1", "run_command", ok=True,
        tool_input={"command": "python", "args": ["scripts/cleanup.py"]},
    ) + fixture._call(
        6, "noise2", "run_command", ok=True,
        tool_input={"command": "python", "args": ["scripts/lint.py"]},
    )
    evidence = TraceEvidenceExtractor().extract(fixture.TASK, trace)
    episode = extract_recovery_episodes(evidence)[0]
    selected = [call.invocation for call in episode.followup_calls]
    actual_retry = evidence.tool_calls[-1].invocation
    return {
        "failure": episode.failed_calls[0].invocation,
        "selected_followups": selected,
        "actual_retry": actual_retry,
        "actual_retry_retained": actual_retry in selected,
    }


def matching_red_check_lost_after_four_unrelated_failures():
    trace = fixture._fix_verified_trace()
    trace.insert(5, {
        "event_id": "e12", "type": "recovery",
        "action": "Insert expiry validation before token lookup",
        "related_error_call_ids": ["c1"],
    })
    trace[-2]["output_summary"] = "1 passed"
    noise = []
    for index in range(4):
        noise += fixture._call(
            -40 + index * 4, f"noise{index}", "run_command", ok=False,
            tool_input={
                "command": "pytest", "args": [f"tests/test_noise{index}.py"]
            },
            message="AssertionError: unrelated failure",
        )
    evidence = TraceEvidenceExtractor().extract(fixture.TASK, noise + trace)
    episode = next(
        item for item in extract_recovery_episodes(evidence)
        if item.recovery is not None
    )
    sub = episode_to_task_evidence(episode, evidence)
    claim = fixture._abstract_recovery_claim(sub)
    claim["evidence_quotes"] = ["Expired token was accepted"]
    candidate = parse_llm_candidate(
        fixture._scripted_response(evidence.outcome, [claim]), fixture.TASK,
        evidence, ReflectionLLMConfig(mode="llm_shadow"),
    )
    parent_validation = ReflectionClaimValidator().validate(candidate, evidence)
    sub_validation = ReflectionClaimValidator().validate(candidate, sub)
    return {
        "episode_status": episode.causal_status,
        "parent_checks": [
            [item.call_id, item.result] for item in evidence.verification
        ],
        "episode_checks": [
            [item.call_id, item.result] for item in sub.verification
        ],
        "parent_valid": len(parent_validation.valid_claims),
        "episode_valid": len(sub_validation.valid_claims),
        "episode_issues": [issue.code for issue in sub_validation.issues],
    }


if __name__ == "__main__":
    print(json.dumps({
        "F4_same_interpreter_displaces_retry": (
            followup_slots_consumed_by_same_interpreter()
        ),
        "F5_old_unrelated_checks_displace_red_side": (
            matching_red_check_lost_after_four_unrelated_failures()
        ),
    }, indent=2, ensure_ascii=False))
