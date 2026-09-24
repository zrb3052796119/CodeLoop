"""Per-episode recovery lesson generation, from the 2026-09-09 review.

The episode layer keeps what the whole-task projection dropped (changed
invocation inputs such as ``timeout``), hands one bounded segment at a time
to the existing no-tools synthesizer, and funnels every candidate through
the same shared admission gates the rule path uses. These tests pin the
extraction shapes and the admission outcomes on both sides of each gate.
"""

from __future__ import annotations

import json
from typing import Any

from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import (
    ReflectionLLMConfig,
    StructuredGenerationResponse,
    parse_llm_candidate,
)
from minicode.recovery_lesson import (
    RecoveryLessonAgent,
    episode_to_task_evidence,
    extract_recovery_episodes,
)


class _QueueClient:
    """Deterministic no-tools client: one scripted response per episode."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> StructuredGenerationResponse:
        del messages, timeout_seconds, max_output_tokens
        self.calls += 1
        text = self._responses.pop(0) if self._responses else ""
        return StructuredGenerationResponse(
            text=text,
            input_tokens=16,
            output_tokens=max(1, len(text) // 4),
        )


def _call(
    index: int,
    call_id: str,
    tool: str,
    *,
    ok: bool,
    tool_input: dict[str, Any],
    message: str = "",
    path: str | None = None,
    changed: bool = False,
) -> list[dict[str, Any]]:
    file_fields: dict[str, Any] = {}
    if path is not None:
        file_fields["files"] = [path]
        file_fields["files_changed" if changed else "files_read"] = [path]
    events = [
        {
            "event_id": f"e{index}",
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool,
            "input": tool_input,
            **file_fields,
        },
        {
            "event_id": f"e{index + 1}",
            "type": "tool_result",
            "call_id": call_id,
            "tool_name": tool,
            "status": "success" if ok else "error",
            "is_error": not ok,
            "output_summary": message or ("ok" if ok else "failed"),
            **file_fields,
        },
    ]
    if not ok:
        events.append(
            {
                "event_id": f"e{index + 2}",
                "type": "error",
                "call_id": call_id,
                "tool_name": tool,
                "error_type": "CommandError",
                "message": message or "failed",
            }
        )
    return events


def _timeout_retry_trace() -> list[dict[str, Any]]:
    return [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            tool_input={
                "command": "python",
                "args": ["scripts/report.py"],
                "timeout": 1,
            },
            message="Command timed out after 1 seconds (process group terminated).",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            tool_input={
                "command": "python",
                "args": ["scripts/report.py"],
                "timeout": 120,
            },
            message="report created",
        ),
        {
            "event_id": "e20",
            "type": "task_result",
            "status": "success",
            "had_errors": True,
            "errors_recovered": True,
            "tool_error_count": 1,
        },
    ]


def _fix_verified_trace() -> list[dict[str, Any]]:
    return [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            tool_input={"command": "pytest", "args": ["tests/test_auth.py", "-q"]},
            message="AssertionError: Expired token was accepted",
        ),
        *_call(
            10,
            "c2",
            "edit_file",
            ok=True,
            tool_input={
                "path": "src/auth.py",
                "old": "token = lookup(token)",
                "new": "check_expiry(token); token = lookup(token)",
            },
            path="src/auth.py",
            changed=True,
        ),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            tool_input={"command": "pytest", "args": ["tests/test_auth.py", "-q"]},
            message="auth expiry regression: 1 passed",
        ),
        {
            "event_id": "e30",
            "type": "task_result",
            "status": "success",
            "had_errors": True,
            "errors_recovered": True,
            "tool_error_count": 1,
        },
    ]


TASK = "Repair the auth expiry regression"


def test_timeout_budget_change_survives_as_a_candidate_episode() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())

    # The rule projection still refuses a causal recovery for a transient
    # failure -- correctly. What must not happen is the input change
    # disappearing entirely.
    assert evidence.recoveries == []
    episodes = extract_recovery_episodes(evidence)
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.causal_status == "candidate"
    assert episode.recovery is None
    assert episode.failed_calls[0].invocation == (
        "python scripts/report.py (timeout: 1)"
    )
    assert episode.followup_calls[0].invocation == (
        "python scripts/report.py (timeout: 120)"
    )


def test_verified_recovery_episode_keeps_its_linked_check() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())
    episodes = extract_recovery_episodes(evidence)

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.causal_status == "verified"
    assert episode.recovery is not None
    assert episode.recovery.epistemic_status == "confirmed"
    assert [item.result for item in episode.verifications] == ["passed"]
    assert episode.event_ids


def _scripted_response(evidence_outcome: str, claims: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "task_summary": TASK,
            "outcome": evidence_outcome,
            "claims": claims,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _abstract_recovery_claim(sub_evidence) -> dict[str, Any]:
    error = sub_evidence.errors[0]
    recovery = sub_evidence.recoveries[0]
    verification = sub_evidence.verification[0]
    return {
        "claim_type": "recovery",
        "semantic_key": "expiry_check_before_lookup",
        "statement": (
            "Accepting an expired token during lookup was repaired by validating "
            "expiry before the lookup; the targeted auth check passed afterwards."
        ),
        "evidence_ids": list(
            dict.fromkeys(
                [
                    *error.source_event_ids,
                    *recovery.event_ids,
                    *verification.event_ids,
                ]
            )
        ),
        "epistemic_status": "confirmed",
        "applies_when": "When validating an authentication token before a lookup.",
        "limitations": ["Only the targeted auth check passed."],
        "verification_ids": [verification.verification_id],
        "related_error_ids": [error.error_id],
        "related_recovery_ids": [recovery.recovery_id],
        "evidence_quotes": [
            "Expired token was accepted",
            "Changed src/auth.py",
        ],
    }


def test_episode_lesson_accepts_abstract_statement_with_real_quotes() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)

    # The abstract statement contains neither the verbatim error line nor
    # the rule action text; grounding rides on the quotes.
    candidate = parse_llm_candidate(
        _scripted_response(
            sub_evidence.outcome, [_abstract_recovery_claim(sub_evidence)]
        ),
        TASK,
        sub_evidence,
        ReflectionLLMConfig(mode="llm_shadow"),
    )
    from minicode.reflection_synthesis import (
        ReflectionClaimValidator,
        ReflectionValueGate,
    )

    validation = ReflectionClaimValidator().validate(candidate, sub_evidence)
    assert validation.valid_claims, [item.code for item in validation.issues]
    decision = ReflectionValueGate().evaluate(
        candidate, validation, sub_evidence
    )
    assert decision.accepted
    assert candidate.claims[0].evidence_quotes


def test_fabricated_quote_is_refused_at_parse_time() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)

    fabricated = _abstract_recovery_claim(sub_evidence)
    fabricated["evidence_quotes"] = ["the trace also mentions kubernetes"]
    try:
        parse_llm_candidate(
            _scripted_response(sub_evidence.outcome, [fabricated]),
            TASK,
            sub_evidence,
            ReflectionLLMConfig(mode="llm_shadow"),
        )
    except Exception as exc:  # LLMCandidateParseError
        assert "evidence_quote_not_grounded" in str(exc)
    else:
        raise AssertionError("fabricated quote must be refused")


def test_candidate_episode_cannot_be_promoted_to_confirmed() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)

    overreach = {
        "claim_type": "recovery",
        "semantic_key": "raise_timeout_budget",
        "statement": (
            "Raising the command timeout budget to 120 seconds was the "
            "verified fix for the report run."
        ),
        "evidence_ids": list(sub_evidence.errors[0].source_event_ids),
        "epistemic_status": "confirmed",
        "applies_when": "When the report run times out after one second.",
        "limitations": ["Observed once."],
        "verification_ids": [],
        "related_error_ids": [sub_evidence.errors[0].error_id],
        "related_recovery_ids": [],
        "evidence_quotes": ["Command timed out after 1 seconds"],
    }
    candidate = parse_llm_candidate(
        _scripted_response(sub_evidence.outcome, [overreach]),
        TASK,
        sub_evidence,
        ReflectionLLMConfig(mode="llm_shadow"),
    )
    from minicode.reflection_synthesis import ReflectionClaimValidator

    validation = ReflectionClaimValidator().validate(candidate, sub_evidence)
    assert not validation.valid_claims
    assert {
        issue.code for issue in validation.issues
    } & {
        "confirmed_recovery_without_verification",
        "confirmed_recovery_missing_recovery_reference",
    }


def test_agent_runs_per_episode_and_records_abstention() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())
    episodes = extract_recovery_episodes(evidence)

    # The candidate episode yields an abstention: one episode, one call,
    # no lesson, and the failure is recorded rather than guessed around.
    abstain = _scripted_response(evidence.outcome, [])
    agent = RecoveryLessonAgent(
        _QueueClient([abstain]),
        ReflectionLLMConfig(mode="llm_shadow"),
    )
    result = agent.attempt_lessons(TASK, evidence)

    assert len(result.episodes) == len(episodes)
    assert [outcome.attempted for outcome in result.outcomes] == [True]
    assert result.lessons == []
    assert result.to_dict()["llm_called"] == 1


def test_agent_collects_verified_lesson_per_episode() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)

    agent = RecoveryLessonAgent(
        _QueueClient(
            [
                _scripted_response(
                    evidence.outcome, [_abstract_recovery_claim(sub_evidence)]
                )
            ]
        ),
        ReflectionLLMConfig(mode="llm_shadow"),
    )
    result = agent.attempt_lessons(TASK, evidence)

    assert len(result.lessons) == 1
    lesson = result.lessons[0]
    assert lesson.claim_type == "recovery"
    assert lesson.epistemic_status == "confirmed"
    assert lesson.evidence_quotes
    assert result.outcomes[0].causal_status == "verified"


# --- Implementation-review regressions (2026-09-09 F4-F7) -------------------


def test_unrelated_success_does_not_displace_the_real_retry() -> None:
    """git status between two report runs must not become the follow-up."""
    trace = _timeout_retry_trace()
    trace[3:3] = _call(
        5, "unrelated", "run_command", ok=True,
        tool_input={"command": "git", "args": ["status"]},
    )
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    episodes = extract_recovery_episodes(evidence)

    assert len(episodes) == 1
    episode = episodes[0]
    assert [
        call.invocation for call in episode.followup_calls
    ] == ["python scripts/report.py (timeout: 120)"]


def test_projection_admission_matches_parent_evidence() -> None:
    """A recovery the parent confirms must survive episode projection."""
    trace = _fix_verified_trace()
    trace.insert(
        5,
        {
            "event_id": "e12",
            "type": "recovery",
            "action": "Insert expiry validation before token lookup",
            "related_error_call_ids": ["c1"],
        },
    )
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)

    claim = _abstract_recovery_claim(sub_evidence)
    claim["evidence_quotes"] = ["Expired token was accepted"]
    from minicode.reflection_synthesis import ReflectionClaimValidator

    for target in (evidence, sub_evidence):
        candidate = parse_llm_candidate(
            _scripted_response(target.outcome, [claim]),
            TASK,
            target,
            ReflectionLLMConfig(mode="llm_shadow"),
        )
        validation = ReflectionClaimValidator().validate(candidate, target)
        assert len(validation.valid_claims) == 1, [
            (target is sub_evidence, issue.code) for issue in validation.issues
        ]


def test_lesson_ids_are_episode_namespaced() -> None:
    import copy

    first = _fix_verified_trace()[:-1]
    second = copy.deepcopy(_fix_verified_trace())
    for event in second:
        event["event_id"] = "second-" + event["event_id"]
        if "call_id" in event:
            event["call_id"] = "second-" + event["call_id"]
        if "input" in event:
            event["input"] = json.loads(
                json.dumps(event["input"]).replace("auth.py", "billing.py")
            )
        for key in ("files", "files_changed", "files_read"):
            if key in event:
                event[key] = [
                    item.replace("auth.py", "billing.py") for item in event[key]
                ]
    evidence = TraceEvidenceExtractor().extract(TASK, first + second)
    episodes = extract_recovery_episodes(evidence)
    assert len(episodes) == 2

    responses = []
    for index, episode in enumerate(episodes):
        sub = episode_to_task_evidence(episode, evidence)
        claim = _abstract_recovery_claim(sub)
        claim["semantic_key"] += f"_case_{index}"
        claim["evidence_quotes"] = ["Expired token was accepted"]
        responses.append(_scripted_response(evidence.outcome, [claim]))

    result = RecoveryLessonAgent(
        _QueueClient(responses), ReflectionLLMConfig(mode="llm_shadow")
    ).attempt_lessons(TASK, evidence)

    assert len(result.lessons) == 2
    assert len({claim.claim_id for claim in result.lessons}) == 2
    assert result.lessons[0].claim_id.startswith("episode-")
    # The per-episode outcome mirrors what was accepted for that episode.
    assert [
        [claim.claim_id for claim in outcome.persistable_claims]
        for outcome in result.outcomes
    ] == [[claim.claim_id] for claim in result.lessons]


def test_invocation_is_quotable_evidence() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)
    error = sub_evidence.errors[0]

    claim = {
        "claim_type": "warning",
        "semantic_key": "timeout_invocation_quote",
        "statement": (
            "The report invocation used a 120-second timeout after an "
            "earlier timeout."
        ),
        "evidence_ids": list(
            dict.fromkeys(
                [
                    *error.source_event_ids,
                    sub_evidence.tool_calls[-1].call_event_id,
                    *sub_evidence.tool_calls[-1].result_event_ids,
                ]
            )
        ),
        "epistemic_status": "inferred",
        "applies_when": "When executing this report command under a short timeout.",
        "limitations": ["One observation."],
        "verification_ids": [],
        "related_error_ids": [error.error_id],
        "related_recovery_ids": [],
        "evidence_quotes": [sub_evidence.tool_calls[-1].invocation],
    }
    candidate = parse_llm_candidate(
        _scripted_response(sub_evidence.outcome, [claim]),
        TASK,
        sub_evidence,
        ReflectionLLMConfig(mode="llm_shadow"),
    )
    assert candidate.claims[0].evidence_quotes == [
        "python scripts/report.py (timeout: 120)"
    ]

    # And the validator independently accepts the quote against its own
    # event-keyed invocation index.
    from minicode.reflection_synthesis import ReflectionClaimValidator

    validation = ReflectionClaimValidator().validate(candidate, sub_evidence)
    assert validation.valid_claims, [
        issue.code for issue in validation.issues
    ]


def test_validated_unpersisted_claim_is_kept_as_observation() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())
    episodes = extract_recovery_episodes(evidence)
    sub_evidence = episode_to_task_evidence(episodes[0], evidence)
    error = sub_evidence.errors[0]

    warning = {
        "claim_type": "warning",
        "semantic_key": "report_timeout_observation",
        "statement": (
            "The report command timed out with a one-second budget; its "
            "later invocation with a 120-second budget succeeded, without "
            "establishing the cause."
        ),
        "evidence_ids": list(error.source_event_ids),
        "epistemic_status": "inferred",
        "applies_when": "When executing this report command under a short timeout.",
        "limitations": [
            "Only one observation; the timeout increase is not a verified causal fix."
        ],
        "verification_ids": [],
        "related_error_ids": [error.error_id],
        "related_recovery_ids": [],
        "evidence_quotes": ["Command timed out after 1 seconds"],
    }
    result = RecoveryLessonAgent(
        _QueueClient([_scripted_response(evidence.outcome, [warning])]),
        ReflectionLLMConfig(mode="llm_shadow"),
    ).attempt_lessons(TASK, evidence)

    assert result.lessons == []
    assert len(result.observations) == 1
    assert result.observations[0].claim_id.startswith("episode-")
    assert result.outcomes[0].model_called is True


def test_budget_decline_is_not_reported_as_a_model_call() -> None:
    from minicode.agent_budget import AgentTurnBudget

    evidence = TraceEvidenceExtractor().extract(TASK, _timeout_retry_trace())
    client = _QueueClient([])
    result = RecoveryLessonAgent(
        client, ReflectionLLMConfig(mode="llm_shadow")
    ).attempt_lessons(
        TASK, evidence, agent_budget=AgentTurnBudget(max_model_calls=1)
    )

    assert client.calls == 0
    assert result.llm_called == 0
    assert result.outcomes[0].failure_code == "agent_budget_exhausted"
    assert result.outcomes[0].model_called is False


def test_same_interpreter_noise_cannot_displace_the_real_retry() -> None:
    """``python scripts/cleanup.py`` shares only the interpreter."""
    trace = _timeout_retry_trace()
    trace[3:3] = _call(
        4, "noise1", "run_command", ok=True,
        tool_input={"command": "python", "args": ["scripts/cleanup.py"]},
    ) + _call(
        6, "noise2", "run_command", ok=True,
        tool_input={"command": "python", "args": ["scripts/lint.py"]},
    )
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    episodes = extract_recovery_episodes(evidence)

    assert len(episodes) == 1
    selected = [call.invocation for call in episodes[0].followup_calls]
    assert selected == ["python scripts/report.py (timeout: 120)"]


def test_red_side_survives_a_flood_of_unrelated_failures() -> None:
    trace = _fix_verified_trace()
    trace.insert(
        5,
        {
            "event_id": "e12",
            "type": "recovery",
            "action": "Insert expiry validation before token lookup",
            "related_error_call_ids": ["c1"],
        },
    )
    trace[-2]["output_summary"] = "1 passed"
    noise: list[dict[str, Any]] = []
    for index in range(4):
        noise += _call(
            -40 + index * 4,
            f"noise{index}",
            "run_command",
            ok=False,
            tool_input={
                "command": "pytest",
                "args": [f"tests/test_noise{index}.py"],
            },
            message="AssertionError: unrelated failure",
        )
    evidence = TraceEvidenceExtractor().extract(TASK, noise + trace)
    episode = next(
        item
        for item in extract_recovery_episodes(evidence)
        if item.recovery is not None
    )
    sub_evidence = episode_to_task_evidence(episode, evidence)

    claim = _abstract_recovery_claim(sub_evidence)
    claim["evidence_quotes"] = ["Expired token was accepted"]
    from minicode.reflection_synthesis import ReflectionClaimValidator

    for target in (evidence, sub_evidence):
        candidate = parse_llm_candidate(
            _scripted_response(target.outcome, [claim]),
            TASK,
            target,
            ReflectionLLMConfig(mode="llm_shadow"),
        )
        validation = ReflectionClaimValidator().validate(candidate, target)
        assert len(validation.valid_claims) == 1, [
            (target is sub_evidence, issue.code)
            for issue in validation.issues
        ]


def test_rendered_cwd_restored_for_followup_selection() -> None:
    """Same relative path under another cwd is not the same retry."""
    trace = _timeout_retry_trace()
    trace[0]["input"]["cwd"] = "/repo/backend"
    trace[3]["input"]["cwd"] = "/repo/backend"
    noise: list[dict[str, Any]] = []
    for index in range(2):
        noise.extend(
            _call(
                30 + index * 3,
                f"noise{index}",
                "run_command",
                ok=True,
                tool_input={
                    "command": "python",
                    "args": ["scripts/report.py"],
                    "cwd": f"/repo/other{index}",
                    "timeout": 120,
                },
                message="report created",
            )
        )
    trace[3:3] = noise
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    episodes = extract_recovery_episodes(evidence)

    assert len(episodes) == 1
    selected = [call.invocation for call in episodes[0].followup_calls]
    assert selected == [
        "python scripts/report.py (cwd: /repo/backend; timeout: 120)"
    ]


def test_projection_keeps_every_retained_checks_invocation() -> None:
    """The fifth green check stays provable inside the episode."""
    import importlib.util as _ilu
    from dataclasses import replace as _replace
    from minicode.reflection_synthesis import ReflectionClaimValidator, ReflectionValueGate

    spec = _ilu.spec_from_file_location(
        "gp", "artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py"
    )
    prior = _ilu.module_from_spec(spec)
    spec.loader.exec_module(prior)
    evidence = prior.fixture(
        "pytest tests/test_auth.py", "pytest tests/test_auth.py"
    )
    checks = [evidence.verification[0]]
    calls = [evidence.tool_calls[0]]
    positions = dict(evidence.event_positions)
    for index in range(1, 6):
        checks.append(
            _replace(
                evidence.verification[1],
                verification_id=f"verify-{index}",
                call_id=f"passed-{index}",
                event_ids=(f"check-{index}",),
            )
        )
        calls.append(
            _replace(
                evidence.tool_calls[1],
                call_id=f"passed-{index}",
                call_event_id=f"invoke-{index}",
                result_event_ids=(f"check-{index}",),
            )
        )
        positions[f"invoke-{index}"] = 4 + 2 * index
        positions[f"check-{index}"] = 5 + 2 * index
    evidence = _replace(
        evidence, verification=checks, tool_calls=calls, event_positions=positions
    )
    episode = extract_recovery_episodes(evidence)[0]
    sub_evidence = episode_to_task_evidence(episode, evidence)

    claim = {
        "claim_type": "recovery",
        "semantic_key": "expiry_order",
        "statement": (
            "After Expired token was accepted, Moved expiry validation "
            "before lookup."
        ),
        "epistemic_status": "confirmed",
        "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": ["auth-error", "recovery-event", "check-5"],
        "verification_ids": ["verify-5"],
        "related_error_ids": ["error-auth"],
        "related_recovery_ids": ["recovery-auth"],
    }
    import json as _json

    for target in (evidence, sub_evidence):
        response = _json.dumps(
            {
                "task_summary": "Repair auth",
                "outcome": target.outcome,
                "claims": [claim],
            }
        )
        candidate = parse_llm_candidate(
            response,
            "Repair auth",
            target,
            ReflectionLLMConfig(mode="llm_shadow"),
        )
        validation = ReflectionClaimValidator().validate(candidate, target)
        gate = ReflectionValueGate().evaluate(candidate, validation, target)
        assert len(validation.valid_claims) == 1
        assert gate.accepted


def _independent_recovery_evidence(count: int):
    import copy

    trace = []
    for index in range(count):
        segment = copy.deepcopy(_fix_verified_trace()[:-1])
        for event in segment:
            event["event_id"] = f"segment-{index}-" + event["event_id"]
            if "call_id" in event:
                event["call_id"] = f"segment-{index}-" + event["call_id"]
        trace.extend(json.loads(json.dumps(segment).replace("auth.py", f"auth{index}.py")))
    trace.append(_fix_verified_trace()[-1])
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    assert len(extract_recovery_episodes(evidence)) == count
    return evidence


def test_episode_call_ceiling_bounds_work_without_a_parent_budget() -> None:
    evidence = _independent_recovery_evidence(3)
    client = _QueueClient([_scripted_response(evidence.outcome, [])] * 3)
    result = RecoveryLessonAgent(
        client, ReflectionLLMConfig(mode="llm_shadow"), max_model_calls=2,
    ).attempt_lessons(TASK, evidence)

    assert client.calls == result.llm_called == 2
    assert len(result.outcomes) == 3
    assert [item.attempted for item in result.outcomes] == [True, True, False]
    assert result.outcomes[-1].failure_code == "episode_call_limit"
    assert result.outcomes[-1].input_tokens is None
    assert result.outcomes[-1].latency_ms is None


def test_episode_provider_failure_consumes_the_call_ceiling() -> None:
    class BrokenClient:
        calls = 0

        def generate_json(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("synthetic provider timeout")

    evidence = _independent_recovery_evidence(2)
    client = BrokenClient()
    result = RecoveryLessonAgent(
        client, ReflectionLLMConfig(mode="llm_shadow"), max_model_calls=1,
    ).attempt_lessons(TASK, evidence)

    assert client.calls == result.llm_called == 1
    assert [item.failure_code for item in result.outcomes] == [
        "provider_timeout", "episode_call_limit",
    ]
    assert result.outcomes[0].estimated_cost_usd is None


def test_parent_budget_is_shared_across_episodes_and_preserves_final_call() -> None:
    from minicode.agent_budget import AgentTurnBudget

    evidence = _independent_recovery_evidence(3)
    budget = AgentTurnBudget(max_model_calls=2)
    client = _QueueClient([_scripted_response(evidence.outcome, [])] * 3)
    result = RecoveryLessonAgent(
        client, ReflectionLLMConfig(mode="llm_shadow"), max_model_calls=3,
    ).attempt_lessons(TASK, evidence, agent_budget=budget)

    assert client.calls == result.llm_called == 1
    assert [item.failure_code for item in result.outcomes] == [
        None, "agent_budget_exhausted", "agent_budget_exhausted",
    ]
    assert budget.snapshot().used_model_calls == 1


def test_episode_review_retains_rejected_claim_and_provider_usage() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())
    sub = episode_to_task_evidence(extract_recovery_episodes(evidence)[0], evidence)
    claim = _abstract_recovery_claim(sub)
    claim["claim_type"] = "root_cause"
    text = _scripted_response(evidence.outcome, [claim])

    class MeteredClient:
        def generate_json(self, *args, **kwargs):
            return StructuredGenerationResponse(
                text=text, input_tokens=91, output_tokens=31,
                cache_read_tokens=12, cache_creation_tokens=0,
                estimated_cost_usd=0.003, latency_ms=45, usage_source="provider",
            )

    result = RecoveryLessonAgent(
        MeteredClient(), ReflectionLLMConfig(mode="llm_shadow"),
    ).attempt_lessons(TASK, evidence)

    assert not result.lessons
    outcome = result.outcomes[0]
    assert outcome.validation_issues
    assert outcome.generated_claims[0].statement == claim["statement"]
    review = result.to_dict()["outcomes"][0]
    assert review["input_tokens"] == 91
    assert review["output_tokens"] == 31
    assert review["cache_read_tokens"] == 12
    assert review["cache_creation_tokens"] == 0
    assert review["usage_source"] == "provider"
    assert review["estimated_cost_usd"] == 0.003
    assert review["latency_ms"] == 45
    assert review["generated_claims"][0]["statement"] == claim["statement"]


def test_episode_review_keeps_missing_usage_unknown() -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _fix_verified_trace())

    class UnmeteredClient:
        def generate_json(self, *args, **kwargs):
            return StructuredGenerationResponse(
                text=_scripted_response(evidence.outcome, []),
            )

    result = RecoveryLessonAgent(
        UnmeteredClient(), ReflectionLLMConfig(mode="llm_shadow"),
    ).attempt_lessons(TASK, evidence)
    outcome = result.to_dict()["outcomes"][0]
    assert outcome["input_tokens"] is None
    assert outcome["output_tokens"] is None
    assert outcome["estimated_cost_usd"] is None
    assert outcome["usage_source"] == "unavailable"


def test_episode_cancel_and_deadline_stop_before_provider() -> None:
    import time
    import pytest

    from minicode.model_call_control import ModelCallDeadlineExceeded
    from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken

    evidence = _independent_recovery_evidence(2)
    client = _QueueClient([])
    agent = RecoveryLessonAgent(client, ReflectionLLMConfig(mode="llm_shadow"))
    token = TurnCancellationToken("turn_" + "1" * 32)
    token.request()
    with pytest.raises(TurnCancellationRequested):
        agent.attempt_lessons(TASK, evidence, cancellation_token=token)
    with pytest.raises(ModelCallDeadlineExceeded):
        agent.attempt_lessons(TASK, evidence, deadline_monotonic=time.monotonic() - 1)
    assert client.calls == 0
