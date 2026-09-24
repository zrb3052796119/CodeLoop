"""Regression tests for recovery misassociation found in the 2026-09-09 review.

Each test is a counterexample the rule pipeline once got wrong: different
test targets merged into one confirmed recovery, same-basename files in
different directories treated as one target, a shell pipeline's zero exit
masking a fixture error, and an unrelated green check confirming a recovery
through the shared validator. The bounds are pinned both ways -- the honest
pairings that must keep working are asserted alongside the mispairings that
must stay rejected.
"""

from __future__ import annotations

from typing import Any

from minicode.reflection_evidence import (
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    TraceEvidenceExtractor,
    VerificationEvidence,
    _workspace_paths_equivalent,
)
from minicode.reflection_synthesis import (
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _call(
    index: int,
    call_id: str,
    tool: str,
    *,
    ok: bool,
    command: str | None = None,
    args: list[str] | None = None,
    cwd: str | None = None,
    path: str | None = None,
    changed: bool = False,
    message: str = "",
) -> list[dict[str, Any]]:
    tool_input: dict[str, Any] = {}
    if command is not None:
        tool_input["command"] = command
    if args is not None:
        tool_input["args"] = args
    if cwd is not None:
        tool_input["cwd"] = cwd
    if path is not None:
        tool_input["path"] = path
    file_fields: dict[str, Any] = {}
    if path is not None:
        file_fields["files"] = [path]
        file_fields["files_changed" if changed else "files_read"] = [path]
    events: list[dict[str, Any]] = [
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
                **file_fields,
            }
        )
    return events


def _done(index: int) -> dict[str, Any]:
    return {
        "event_id": f"e{index}",
        "type": "task_result",
        "status": "success",
        "had_errors": True,
        "errors_recovered": True,
        "tool_error_count": 1,
    }


def _evaluate(trace: list[dict[str, Any]], task: str = "Repair and verify"):
    evidence = TraceEvidenceExtractor().extract(task, trace)
    candidate = RuleReflectionSynthesizer().synthesize(task, evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return evidence, validation, decision


def _confirmed_recovery_claims(validation) -> list[ReflectionClaim]:
    return [
        claim
        for claim in validation.valid_claims
        if claim.claim_type == "recovery" and claim.epistemic_status == "confirmed"
    ]


def test_different_args_targets_are_not_merged_by_unrelated_edit() -> None:
    """pytest auth fails, a doc is edited, pytest billing passes: no recovery.

    The failing auth target was never retested, so no confirmed recovery may
    exist -- neither from the edit-then-success path (targets differ once
    ``args`` participates in the target identity) nor from the operational
    path.
    """
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "-q"],
            message="AssertionError: bad auth token",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="docs/README.md", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=["tests/test_billing.py", "-q"],
            message="3 passed",
        ),
        _done(30),
    ]
    evidence, validation, _ = _evaluate(trace)

    assert _confirmed_recovery_claims(validation) == []
    # Nothing persistable claims the auth failure was fixed: the value gate
    # must not accept a recovery built on the untested auth target.
    assert not [
        claim
        for claim in validation.valid_claims
        if claim.claim_type == "recovery" and claim.related_error_ids
    ]
    assert not [
        recovery
        for recovery in evidence.recoveries
        if "tests/test_billing.py" not in recovery.action
        and recovery.verification_call_ids
    ]


def test_same_basename_in_different_directories_is_not_one_target() -> None:
    """backend/tests/test_config.py and frontend/tests/test_config.py differ.

    A shared basename (or shared ``tests/`` parent) is resemblance, not
    identity; the operational path must not turn the frontend run's success
    into a "corrected invocation" for the backend failure.
    """
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["backend/tests/test_config.py", "-q"],
            message="AssertionError: bad backend config",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="python",
            args=["-m", "pytest", "frontend/tests/test_config.py", "-q"],
            message="3 passed",
        ),
        _done(20),
    ]
    evidence, validation, _ = _evaluate(trace)

    assert _confirmed_recovery_claims(validation) == []
    assert [
        recovery for recovery in evidence.recoveries if recovery.files_changed == ()
    ] == [] or all(
        recovery.epistemic_status != "confirmed" for recovery in evidence.recoveries
    )


def test_workspace_path_equivalence_bounds() -> None:
    assert _workspace_paths_equivalent(
        {"tests/test_auth.py"}, {"tests/test_auth.py"}
    )
    # Relative spellings resolve only against a cwd the call provides.
    assert _workspace_paths_equivalent(
        {"tests/test_auth.py"},
        {"tests/test_auth.py"},
        failed_cwd="/repo",
        successful_cwd="/repo",
    )
    # Absolute vs relative spellings meet at the anchor.
    assert _workspace_paths_equivalent(
        {"/repo/tests/test_auth.py"},
        {"tests/test_auth.py"},
        successful_cwd="/repo",
    )
    # A wrapper that carries its anchor inside the command string.
    assert _workspace_paths_equivalent(
        {"tests/test_auth.py"},
        {"/repo/tests/test_auth.py", "/repo"},
        failed_cwd="/repo",
    )
    # Same relative spelling under different cwds is a different file.
    assert not _workspace_paths_equivalent(
        {"tests/test_config.py"},
        {"tests/test_config.py"},
        failed_cwd="/repo/backend",
        successful_cwd="/repo/frontend",
    )
    # An unanchored relative spelling never matches an absolute one.
    assert not _workspace_paths_equivalent(
        {"/repo/tests/test_auth.py"}, {"tests/test_auth.py"}
    )
    # Shared suffix is not identity: both files can coexist.
    assert not _workspace_paths_equivalent(
        {"tests/test_auth.py"}, {"backend/tests/test_auth.py"}
    )
    # Same basename under different directories is a different target.
    assert not _workspace_paths_equivalent(
        {"backend/tests/test_config.py"}, {"frontend/tests/test_config.py"}
    )
    # Shared parent directory alone is not identity.
    assert not _workspace_paths_equivalent(
        {"tests/test_auth.py"}, {"tests/test_billing.py"}
    )


def test_mixed_passed_error_output_is_not_a_passed_verification() -> None:
    """A pipeline's zero exit must not mask "1 passed, 1 error"."""
    extractor = TraceEvidenceExtractor()
    verification = extractor._verification_result(
        "success", "1 passed, 1 error in 0.15s"
    )
    assert verification == "failed"

    assert extractor._verification_result("success", "2 errors in 0.10s") == "failed"
    assert extractor._verification_result("success", "FAILED (failures=1)") == "failed"
    assert extractor._verification_result("success", "3 passed in 0.2s") == "passed"
    assert extractor._verification_result("success", "no errors") == "passed"
    assert extractor._verification_result("success", "0 errors in 0.1s") == "passed"

    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "--bad-option"],
            message="UsageError: unrecognized arguments: --bad-option",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="bash",
            args=["-lc", "pytest tests/test_auth.py -q | tail -20"],
            message="1 passed, 1 error in 0.15s",
        ),
        _done(20),
    ]
    evidence, validation, _ = _evaluate(trace)

    assert all(
        item.result != "passed"
        for item in evidence.verification
        if item.call_id == "c2"
    )
    assert _confirmed_recovery_claims(validation) == []


def _auth_evidence(*, verification_summary: str, linked: bool = True) -> TaskEvidence:
    error = ErrorEvidence(
        "error-1", "call-error", "run_command", "AssertionError",
        "Expired token was accepted", ("event-error",),
    )
    recovery = RecoveryEvidence(
        "recovery-1", ("error-1",), "Moved the expiry check before lookup",
        ("event-recovery",), ("src/auth.py",), "confirmed",
        verification_call_ids=("call-verify",) if linked else (),
    )
    verification = VerificationEvidence(
        "verify-1", "run_command", "call-verify", "test", "targeted", "passed",
        ("event-verify",), verification_summary,
    )
    return TaskEvidence(
        errors=[error],
        recoveries=[recovery],
        verification=[verification],
        outcome="success",
        event_positions={"event-error": 0, "event-recovery": 1, "event-verify": 2},
    )


def _validate_recovery_claim(evidence: TaskEvidence, statement: str):
    claim = ReflectionClaim(
        "llm-claim-1", "recovery", "expiry_before_lookup", statement,
        ["event-error", "event-recovery", "event-verify"], "confirmed",
        applies_when="When validating an authentication token before a lookup.",
        limitations=["Only targeted checks passed."],
        verification_ids=["verify-1"], related_error_ids=["error-1"],
        related_recovery_ids=["recovery-1"],
    )
    candidate = ReflectionCandidate("Repair auth", "success", [claim])
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return validation, decision


VERBATIM = (
    "After Expired token was accepted, the recovery was: "
    "Moved the expiry check before lookup."
)


def test_validator_accepts_related_passed_verification() -> None:
    evidence = _auth_evidence(verification_summary="Auth expiry regression passed")
    validation, decision = _validate_recovery_claim(evidence, VERBATIM)

    assert validation.valid_claims, [item.code for item in validation.issues]
    assert decision.accepted


def test_validator_rejects_unrelated_passed_verification() -> None:
    """A green logging check must not confirm an auth recovery.

    The rule synthesizer already refused this pairing; the shared validator
    used to accept it because any passed verification was enough. Both
    admission paths now share one relationship test.
    """
    evidence = _auth_evidence(
        verification_summary="logging format checks passed", linked=False
    )
    validation, decision = _validate_recovery_claim(evidence, VERBATIM)

    assert not validation.valid_claims
    assert "confirmed_recovery_verification_unrelated" in {
        issue.code for issue in validation.issues
    }
    assert not decision.accepted


def test_rule_and_validator_agree_on_unrelated_verification() -> None:
    """Rule output and a hand-built candidate get the same verdict."""
    evidence = _auth_evidence(
        verification_summary="logging format checks passed", linked=False
    )

    rule_candidate = RuleReflectionSynthesizer().synthesize("Repair auth", evidence)
    rule_recovery = [
        claim
        for claim in rule_candidate.claims
        if claim.claim_type == "recovery"
    ]
    assert all(claim.epistemic_status != "confirmed" for claim in rule_recovery)
    assert all(not claim.verification_ids for claim in rule_recovery)

    validation, decision = _validate_recovery_claim(evidence, VERBATIM)
    assert not decision.accepted


# --- Implementation-review regressions (2026-09-09 F1-F3) -------------------


def _identity_evaluate(steps):
    """Mirror of the review's identity probe: full engine over a trace."""
    trace: list[dict[str, Any]] = []
    for index, (tool, payload, ok, output) in enumerate(steps, start=1):
        tool_input = dict(payload)
        events = _call(
            index * 10,
            f"c{index}",
            tool,
            ok=ok,
            command=tool_input.get("command"),
            args=tool_input.get("args"),
            cwd=tool_input.get("cwd"),
            path=tool_input.get("path"),
            message=output,
        )
        trace.extend(events)
    trace.append(_done(90))
    return _evaluate(trace, task="Repair and verify")


def test_shared_cwd_is_not_a_common_resource() -> None:
    """auth fails, billing passes under the same cwd: no confirmed recovery."""
    _, validation, _ = _identity_evaluate(
        [
            (
                "run_command",
                {
                    "command": "pytest",
                    "args": ["tests/test_auth.py"],
                    "cwd": "/repo/workspace",
                },
                False,
                "AssertionError: expired auth token accepted",
            ),
            (
                "run_command",
                {
                    "command": "python",
                    "args": ["-m", "pytest", "tests/test_billing.py"],
                    "cwd": "/repo/workspace",
                },
                True,
                "3 passed",
            ),
        ]
    )
    assert _confirmed_recovery_claims(validation) == []


def test_same_relative_path_under_different_cwd_is_not_one_target() -> None:
    _, validation, _ = _identity_evaluate(
        [
            (
                "run_command",
                {
                    "command": "pytest",
                    "args": ["tests/test_config.py"],
                    "cwd": "/repo/backend",
                },
                False,
                "AssertionError: backend config broken",
            ),
            (
                "run_command",
                {
                    "command": "python",
                    "args": ["-m", "pytest", "tests/test_config.py"],
                    "cwd": "/repo/frontend",
                },
                True,
                "3 passed",
            ),
        ]
    )
    assert _confirmed_recovery_claims(validation) == []


def test_relative_suffix_is_not_file_identity() -> None:
    """``tests/test_auth.py`` and ``backend/tests/test_auth.py`` can coexist."""
    _, validation, _ = _identity_evaluate(
        [
            (
                "run_command",
                {"command": "pytest", "args": ["tests/test_auth.py"]},
                False,
                "AssertionError: root auth token accepted",
            ),
            (
                "run_command",
                {
                    "command": "python",
                    "args": ["-m", "pytest", "backend/tests/test_auth.py"],
                },
                True,
                "3 passed",
            ),
        ]
    )
    assert _confirmed_recovery_claims(validation) == []


def test_changed_selection_filter_is_not_a_fix() -> None:
    """``-k=expired`` failing then ``-k=valid`` passing deselected the failure."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "-k=expired"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="docs/README.md", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=["tests/test_auth.py", "-k=valid"],
            message="1 passed, 1 deselected",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_selection_filter_kept_constant_still_pairs() -> None:
    """Same ``-k`` value with presentation-flag churn is still one target."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "-k=expired"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="src/auth.py", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=["tests/test_auth.py", "-k=expired", "-q"],
            message="1 passed",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation), "same-selection rerun after an edit is a fix-verified loop"


def test_real_quote_cannot_promote_inferred_source_to_confirmed() -> None:
    """F1: quote channel must inherit status from referenced records."""
    trace = [
        {
            "event_id": "decision-event",
            "type": "assistant",
            "message": "The root cause is checking token expiry after account lookup.",
        },
        {"event_id": "done", "type": "task_result", "status": "success"},
    ]
    evidence = TraceEvidenceExtractor().extract("Repair auth", trace)
    source = evidence.decisions[0]
    assert source.epistemic_status == "inferred"

    base = {
        "claim_type": "decision",
        "semantic_key": "expiry_order",
        "statement": (
            "Expiry validation after looking up an account is the identified defect."
        ),
        "epistemic_status": "confirmed",
        "applies_when": "When checking authentication tokens.",
        "limitations": [],
        "evidence_ids": ["decision-event"],
        "verification_ids": [],
        "related_error_ids": [],
        "related_recovery_ids": [],
    }
    quoted = {**base, "evidence_quotes": [source.statement]}

    from minicode.reflection_llm import (
        ReflectionLLMConfig,
        parse_llm_candidate,
    )
    import json as _json

    candidate = parse_llm_candidate(
        _json.dumps(
            {
                "task_summary": "Repair auth",
                "outcome": evidence.outcome,
                "claims": [quoted],
            }
        ),
        "Repair auth",
        evidence,
        ReflectionLLMConfig(),
    )
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    assert not validation.valid_claims
    assert "epistemic_status_overclaim" in {
        issue.code for issue in validation.issues
    }


def test_red_green_requires_the_same_verification_target() -> None:
    """F3: an auth failure plus a billing pass is not a red-green pair."""
    error = ErrorEvidence(
        "error-1", "auth-failure", "run_command", "AssertionError",
        "Expired token was accepted", ("auth-error",),
    )
    recovery = RecoveryEvidence(
        "recovery-1", ("error-1",), "Moved expiry validation before lookup",
        ("recovery-event",), ("src/auth.py",), "confirmed",
    )
    failed = VerificationEvidence(
        "verify-auth", "run_command", "auth-failure", "test", "targeted",
        "failed", ("auth-failed-check",), "Auth expiry check failed",
    )
    passed = VerificationEvidence(
        "verify-billing", "run_command", "billing-success", "test", "targeted",
        "passed", ("billing-passed-check",), "Billing invoice check passed",
    )
    evidence = TaskEvidence(
        errors=[error],
        recoveries=[recovery],
        verification=[failed, passed],
        outcome="success",
        event_positions={
            "auth-error": 0,
            "auth-failed-check": 1,
            "recovery-event": 2,
            "billing-passed-check": 3,
        },
    )
    claim = ReflectionClaim(
        "llm-claim-1", "recovery", "expiry_order",
        "After Expired token was accepted, Moved expiry validation before lookup.",
        ["auth-error", "recovery-event", "billing-passed-check"], "confirmed",
        applies_when="When checking authentication tokens.",
        limitations=["Only the targeted billing check passed."],
        verification_ids=["verify-billing"], related_error_ids=["error-1"],
        related_recovery_ids=["recovery-1"],
    )
    candidate = ReflectionCandidate("Repair auth", "success", [claim])
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    assert not validation.valid_claims
    assert "confirmed_recovery_verification_unrelated" in {
        issue.code for issue in validation.issues
    }

    rule_candidate = RuleReflectionSynthesizer().synthesize("Repair auth", evidence)
    assert all(
        item.epistemic_status != "confirmed"
        for item in rule_candidate.claims
        if item.claim_type == "recovery"
    )


# --- Second-fix-review regressions (unified CallObjective contract) ---------


def test_changed_marker_selection_is_not_a_fix() -> None:
    """``-m=expired`` failing then ``-m=valid`` passing deselected the work."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "-m=expired"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="docs/README.md", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=["tests/test_auth.py", "-m=valid"],
            message="1 passed, 1 deselected",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_inline_cd_is_an_anchor_not_a_shared_target() -> None:
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="bash",
            args=["-lc", "cd /repo/workspace && pytest tests/test_auth.py"],
            message="AssertionError: expired auth token accepted",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="bash",
            args=["-lc", "cd /repo/workspace && pytest tests/test_billing.py"],
            message="3 passed",
        ),
        _done(20),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_shared_output_artifact_is_not_a_shared_target() -> None:
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "--junitxml=/tmp/results.xml"],
            message="AssertionError: expired auth token accepted",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="python",
            args=[
                "-m",
                "pytest",
                "tests/test_billing.py",
                "--junitxml=/tmp/results.xml",
            ],
            message="3 passed",
        ),
        _done(20),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_missing_fixture_is_not_a_missing_test_target() -> None:
    """The absent object must be the requested target, not what it reads."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py"],
            message=(
                "FileNotFoundError: No such file or directory: "
                "'fixtures/credentials.json'"
            ),
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="python",
            args=["-m", "pytest", "backend/tests/test_auth.py"],
            message="3 passed",
        ),
        _done(20),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def _grounding_evidence(
    failed_invocation: str, passed_invocation: str
) -> TaskEvidence:
    from minicode.reflection_evidence import ToolEvidence

    return TaskEvidence(
        errors=[
            ErrorEvidence(
                "error-auth",
                "failed-call",
                "run_command",
                "AssertionError",
                "Expired token was accepted",
                ("auth-error",),
            )
        ],
        recoveries=[
            RecoveryEvidence(
                "recovery-auth",
                ("error-auth",),
                "Moved expiry validation before lookup",
                ("recovery-event",),
                ("src/auth.py",),
                "confirmed",
            )
        ],
        verification=[
            VerificationEvidence(
                "verify-failed",
                "run_command",
                "failed-call",
                "test",
                "targeted",
                "failed",
                ("failed-check",),
                "1 failed",
            ),
            VerificationEvidence(
                "verify-passed",
                "run_command",
                "passed-call",
                "test",
                "targeted",
                "passed",
                ("passed-check",),
                "1 passed",
            ),
        ],
        tool_calls=[
            ToolEvidence(
                "run_command",
                "failed-call",
                "failed-invocation",
                ("auth-error", "failed-check"),
                "failed",
                failed_invocation,
            ),
            ToolEvidence(
                "run_command",
                "passed-call",
                "passed-invocation",
                ("passed-check",),
                "success",
                passed_invocation,
            ),
        ],
        outcome="success",
        event_positions={
            "failed-invocation": 0,
            "auth-error": 1,
            "failed-check": 2,
            "recovery-event": 3,
            "passed-invocation": 4,
            "passed-check": 5,
        },
    )


def _grounding_claim_ids(evidence: TaskEvidence) -> list[str]:
    from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
    import json as _json

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
        "evidence_ids": ["auth-error", "recovery-event", "passed-check"],
        "verification_ids": ["verify-passed"],
        "related_error_ids": ["error-auth"],
        "related_recovery_ids": ["recovery-auth"],
    }
    candidate = parse_llm_candidate(
        _json.dumps(
            {
                "task_summary": "Repair auth",
                "outcome": evidence.outcome,
                "claims": [claim],
            }
        ),
        "Repair auth",
        evidence,
        ReflectionLLMConfig(),
    )
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    return [
        claim.claim_id for claim in validation.valid_claims
    ], [issue.code for issue in validation.issues]


def test_red_green_survives_absolute_relative_pair_across_cwd() -> None:
    valid, _ = _grounding_claim_ids(
        _grounding_evidence(
            "pytest /repo/backend/tests/test_auth.py",
            "pytest tests/test_auth.py (cwd: /repo/backend)",
        )
    )
    assert valid


def test_red_green_rejects_cwd_and_scope_drift() -> None:
    for failed, passed in [
        # Same spelling under different working directories.
        (
            "pytest tests/test_auth.py (cwd: /repo/backend)",
            "pytest tests/test_auth.py (cwd: /repo/frontend)",
        ),
        # The passing run dropped the file that failed.
        (
            "pytest tests/test_auth.py tests/test_smoke.py",
            "pytest tests/test_smoke.py",
        ),
        # A different node selector is a different check.
        (
            "pytest tests/test_auth.py::test_expired",
            "pytest tests/test_auth.py::test_valid",
        ),
        # Pathless passing side proves no coverage.
        ("pytest tests/test_auth.py", "pytest smoke"),
    ]:
        valid, _ = _grounding_claim_ids(
            _grounding_evidence(failed, passed)
        )
        assert valid == [], (failed, passed)


def test_recovery_cannot_borrow_an_unrelated_errors_red_green() -> None:
    from dataclasses import replace
    from minicode.reflection_evidence import ToolEvidence

    evidence = _grounding_evidence(
        "pytest tests/test_billing.py", "pytest tests/test_billing.py"
    )
    auth_error = replace(evidence.errors[0], call_id="auth-failure")
    billing_error = ErrorEvidence(
        "error-billing",
        "failed-call",
        "run_command",
        "AssertionError",
        "Invoice rounding was incorrect",
        ("billing-error",),
    )
    evidence = replace(
        evidence,
        errors=[auth_error, billing_error],
        tool_calls=[
            ToolEvidence(
                "run_command",
                "auth-failure",
                "auth-invocation",
                ("auth-error",),
                "failed",
                "pytest tests/test_auth.py",
            ),
            replace(
                evidence.tool_calls[0],
                result_event_ids=("billing-error", "failed-check"),
            ),
            evidence.tool_calls[1],
        ],
        event_positions={
            **evidence.event_positions,
            "auth-invocation": -1,
            "billing-error": 1,
        },
    )
    valid, issues = _grounding_claim_ids(evidence)
    assert valid == []
    assert "confirmed_recovery_verification_unrelated" in issues

    rule_candidate = RuleReflectionSynthesizer().synthesize(
        "Repair auth", evidence
    )
    assert all(
        claim.epistemic_status != "confirmed"
        for claim in rule_candidate.claims
        if claim.claim_type == "recovery"
    )


# --- Objective-contract-review regressions (all confirmations through
# --- the full contract; conflicts cannot be overridden) ---------------------


def test_main_deriver_uses_the_full_contract_not_the_truncated_target() -> None:
    """Six named files: the sixth (auth) cannot hide inside the token cut."""
    targets = [f"tests/test_{name}.py" for name in ("one", "two", "three", "four", "five")]
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=targets + ["tests/test_auth.py"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="docs/README.md", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=targets,
            message="5 passed",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_main_deriver_truncation_cannot_hide_a_marker_change() -> None:
    targets = [f"tests/test_{name}.py" for name in ("one", "two", "three", "four", "five")]
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=targets + ["-m=expired"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="docs/README.md", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=targets + ["-m=valid"],
            message="5 passed, 1 deselected",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_generic_fallback_cannot_override_a_scope_conflict() -> None:
    for first, second in [
        # Bare directory operands: no file identity, but an explicit
        # marker conflict that overlap must not overrule.
        (
            ("pytest", ["tests", "-m=expired"], "AssertionError: x"),
            ("pytest", ["tests", "-m=valid"], "1 passed, 1 deselected"),
        ),
        # Unclassifiable ``-m`` on an unknown tool stays ambiguous, and a
        # changed value stays a candidate.
        (
            ("dbtool", ["migrate", "auth", "-m=expired"], "UsageError: invalid mode"),
            ("dbtool", ["migrate", "auth", "-m=valid"], "Migration completed"),
        ),
    ]:
        trace = [
            *_call(
                1, "c1", "run_command", ok=False,
                command=first[0], args=first[1], message=first[2],
            ),
            *_call(
                10, "c2", "run_command", ok=True,
                command=second[0], args=second[1], message=second[2],
            ),
            _done(20),
        ]
        _, validation, _ = _evaluate(trace)
        assert _confirmed_recovery_claims(validation) == []


def test_missing_target_exceptions_do_not_relax_scope() -> None:
    missing = "FileNotFoundError: No such file or directory: 'tests/test_auth.py'"
    cases = [
        # A default-scope retry under a different executable is not a fix.
        [
            *_call(1, "c1", "run_command", ok=False, command="pytest",
                   args=["tests/test_auth.py"], message=missing),
            *_call(10, "c2", "run_command", ok=True, command="echo",
                   args=["done"], message="done"),
            _done(20),
        ],
        # Relocation does not license selecting different tests.
        [
            *_call(1, "c1", "run_command", ok=False, command="pytest",
                   args=["tests/test_auth.py", "-m=expired"], message=missing),
            *_call(10, "c2", "run_command", ok=True, command="pytest",
                   args=["backend/tests/test_auth.py", "-m=valid"], message="3 passed"),
            _done(20),
        ],
    ]
    for trace in cases:
        _, validation, _ = _evaluate(trace)
        assert _confirmed_recovery_claims(validation) == []


def test_unrecognized_flag_difference_stays_a_candidate() -> None:
    """``--ignore-glob`` on the passing side deselected the failing test."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/", "-q", "--tb=short", "-p", "no:cacheprovider"],
            message="1 failed, 1 passed",
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="pytest",
            args=[
                "tests/",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "--ignore-glob=*/test_auth.py",
            ],
            message="1 passed",
        ),
        _done(20),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_identical_unknown_flags_are_harmless() -> None:
    """The same unclassified argument on both sides proves nothing changed."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="pytest",
            args=["tests/test_auth.py", "-p", "no:cacheprovider"],
            message="AssertionError: expired token accepted",
        ),
        *_call(10, "c2", "edit_file", ok=True, path="src/auth.py", changed=True),
        *_call(
            20,
            "c3",
            "run_command",
            ok=True,
            command="pytest",
            args=["tests/test_auth.py", "-p", "no:cacheprovider"],
            message="1 passed",
        ),
        _done(30),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation)


REAL_TRACEBACK = """tests/test_auth.py:2: in test_auth
    open("fixtures/credentials.json").read()
E   FileNotFoundError: [Errno 2] No such file or directory: 'fixtures/credentials.json'

=========================== 1 error in 0.02s ==========================="""


def test_real_traceback_does_not_bind_the_missing_object() -> None:
    """The requested test path appears in the traceback; the fixture was missing."""
    trace = [
        *_call(
            1,
            "c1",
            "run_command",
            ok=False,
            command="python",
            args=["-m", "pytest", "-q", "--tb=short", "tests/test_auth.py"],
            message=REAL_TRACEBACK,
        ),
        *_call(
            10,
            "c2",
            "run_command",
            ok=True,
            command="python",
            args=["-m", "pytest", "-q", "--tb=short", "backend/tests/test_auth.py"],
            message="1 passed",
        ),
        _done(20),
    ]
    _, validation, _ = _evaluate(trace)
    assert _confirmed_recovery_claims(validation) == []


def test_red_green_scope_comes_from_the_recovery_not_the_claim() -> None:
    """Swapping/widening/emptying claim error references borrows nothing."""
    import importlib.util as _ilu
    import json as _json
    from dataclasses import replace as _replace
    from minicode.reflection_evidence import ToolEvidence
    from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate

    spec = _ilu.spec_from_file_location(
        "gp", "artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py"
    )
    prior = _ilu.module_from_spec(spec)
    spec.loader.exec_module(prior)
    evidence = prior.fixture(
        "pytest tests/test_billing.py", "pytest tests/test_billing.py"
    )
    auth = _replace(evidence.errors[0], call_id="auth-failure")
    billing = ErrorEvidence(
        "error-billing",
        "failed-call",
        "run_command",
        "AssertionError",
        "Invoice rounding was incorrect",
        ("billing-error",),
    )
    evidence = _replace(
        evidence,
        errors=[auth, billing],
        tool_calls=[
            ToolEvidence(
                "run_command", "auth-failure", "auth-invocation",
                ("auth-error",), "failed", "pytest tests/test_auth.py",
            ),
            _replace(
                evidence.tool_calls[0],
                result_event_ids=("billing-error", "failed-check"),
            ),
            evidence.tool_calls[1],
        ],
        event_positions={
            **evidence.event_positions,
            "auth-invocation": 0,
            "billing-error": 1,
        },
    )
    for related in (["error-auth"], ["error-auth", "error-billing"], ["error-billing"], []):
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
            "evidence_ids": ["auth-error", "recovery-event", "passed-check"],
            "verification_ids": ["verify-passed"],
            "related_error_ids": related,
            "related_recovery_ids": ["recovery-auth"],
        }
        candidate = parse_llm_candidate(
            _json.dumps(
                {
                    "task_summary": "Repair auth",
                    "outcome": evidence.outcome,
                    "claims": [claim],
                }
            ),
            "Repair auth",
            evidence,
            ReflectionLLMConfig(),
        )
        validation = ReflectionClaimValidator().validate(candidate, evidence)
        assert validation.valid_claims == [], related
        assert "confirmed_recovery_verification_unrelated" in {
            issue.code for issue in validation.issues
        }

    # Unbindable error (call_id None) must not widen into a task-wide search.
    unbound = _replace(evidence, errors=[_replace(evidence.errors[0], call_id=None), billing])
    rule_candidate = RuleReflectionSynthesizer().synthesize("Repair auth", unbound)
    assert all(
        claim.epistemic_status != "confirmed"
        for claim in rule_candidate.claims
        if claim.claim_type == "recovery"
    )
