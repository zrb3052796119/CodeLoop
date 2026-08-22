"""Defects that only real tool output exposed.

Synthetic traces in the other reflection tests carry hand-written one-line
error messages. A real ``pytest`` run does not: it emits a coloured progress
bar, a banner, a source excerpt, a traceback and a summary, all as one
"message". Driving the actual agent against a failing suite surfaced four
problems that every synthetic fixture had hidden.
"""

from __future__ import annotations

import re

import pytest

from minicode.reflection_evidence import (
    EVIDENCE_MAX_TEXT_CHARS,
    TraceEvidenceExtractor,
    sanitize_evidence_text,
)
from minicode.reflection_synthesis import (
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


# Captured from a real run against a failing leasekit suite.
PYTEST_OUTPUT = (
    "\x1b[31mF\x1b[0m\x1b[31m                                     [100%]\x1b[0m\n"
    "=================================== FAILURES ===================================\n"
    "\x1b[31m\x1b[1m__________________________ test_renew_after_transfer ___________________________\x1b[0m\n"
    '    def test_renew_after_transfer():\n        lease = Lease("alice")\n'
    '        lease.transfer("bob")\n>       lease.renew()\n\n'
    "src/leasekit/lease.py:41: in renew\n    self._write()\n"
    "src/leasekit/lease.py:30: in _write\n    raise StaleTokenError(...)\n"
    "E   leasekit.lease.StaleTokenError: the fencing token was not refreshed\n"
    "src/leasekit/lease.py:41: StaleTokenError\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_renew.py::test_renew_after_transfer - "
    "leasekit.lease.StaleTokenError: the fencing token was not refreshed before the write\n"
    "1 failed in 0.01s"
)
COMMAND = "python -m pytest tests/ -q"


def _fix_trace(failure: str = PYTEST_OUTPUT) -> list[dict]:
    """A red-green-verified trace: suite fails, source changes, suite passes."""
    return [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "run_command", "input": {"command": COMMAND}},
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": "run_command", "status": "error", "is_error": True, "output_summary": failure},
        {"event_id": "e3", "type": "error", "call_id": "c1", "tool_name": "run_command", "error_type": "ToolError", "message": failure},
        {"event_id": "e4", "type": "tool_call", "call_id": "c2", "tool_name": "edit_file", "input": {"path": "src/leasekit/lease.py"}, "files": ["src/leasekit/lease.py"], "files_changed": ["src/leasekit/lease.py"]},
        {"event_id": "e5", "type": "tool_result", "call_id": "c2", "tool_name": "edit_file", "status": "success", "output_summary": "edited", "files_changed": ["src/leasekit/lease.py"]},
        {"event_id": "e6", "type": "tool_call", "call_id": "c3", "tool_name": "run_command", "input": {"command": COMMAND}},
        {"event_id": "e7", "type": "tool_result", "call_id": "c3", "tool_name": "run_command", "status": "success", "output_summary": "1 passed in 0.01s"},
        {"event_id": "e8", "type": "task_result", "status": "success", "had_errors": True, "errors_recovered": True, "tool_error_count": 1},
    ]


def _reflect(trace: list[dict]):
    evidence = TraceEvidenceExtractor().extract("Fix the failing test", trace)
    candidate = RuleReflectionSynthesizer().synthesize("Fix the failing test", evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return validation, decision


# ── A: terminal control sequences ───────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "\x1b[31mFAILED\x1b[0m tests/test_a.py::test_b",
        "\x1b[1;33mwarning\x1b[0m: unused import",
        "progress\x08\x08done",
        "bell\x07 and null\x00 bytes",
    ],
)
def test_terminal_control_sequences_never_reach_evidence(raw: str) -> None:
    """pytest, ruff and cargo all colour their output.

    Without stripping, a durable claim reads "When run_command fails with
    \\x1b[31mF\\x1b[0m ... [100%]", and any surface that renders a memory
    replays whatever escape codes a tool chose to emit.
    """
    cleaned = sanitize_evidence_text(raw)

    assert "\x1b" not in cleaned
    assert not any(ord(char) < 0x20 and char not in "\n\t\r" for char in cleaned)


def test_a_claim_built_from_coloured_output_is_clean() -> None:
    validation, _ = _reflect(_fix_trace())

    assert validation.valid_claims
    for claim in validation.valid_claims:
        assert "\x1b" not in claim.statement
        assert "\x1b" not in claim.applies_when
        assert "[100%]" not in claim.applies_when


# ── B: the whole tool run as one "message" ──────────────────────────────


def test_the_condition_names_the_failing_test_not_the_banner() -> None:
    """The banner used to win simply by coming first in the output."""
    validation, _ = _reflect(_fix_trace())

    recovery = next(c for c in validation.valid_claims if c.claim_type == "recovery")
    assert recovery.applies_when == (
        "When run_command fails on tests/test_renew.py::test_renew_after_transfer "
        "with StaleTokenError."
    )
    assert "FAILURES" not in recovery.applies_when


def test_the_statement_quotes_the_lesson_not_the_whole_run() -> None:
    validation, _ = _reflect(_fix_trace())

    recovery = next(c for c in validation.valid_claims if c.claim_type == "recovery")
    assert recovery.statement.startswith(
        "After FAILED tests/test_renew.py::test_renew_after_transfer"
    )
    assert "def test_renew_after_transfer" not in recovery.statement
    assert len(recovery.statement) < 300


def test_a_statement_still_has_to_quote_its_evidence() -> None:
    """Relaxing grounding to an excerpt must not let a claim invent text.

    The anti-fabrication property is what the containment check exists for,
    so it is checked directly rather than assumed from the excerpt rule.
    """
    from minicode.reflection_synthesis import ReflectionCandidate, ReflectionClaim

    evidence = TraceEvidenceExtractor().extract("Fix the failing test", _fix_trace())
    invented = ReflectionClaim(
        "claim-1",
        "recovery",
        "invented",
        "After the database deadlocked, the recovery action was: restarted postgres.",
        ["e2", "e3", "e5"],
        "confirmed",
        applies_when="When postgres deadlocks.",
        related_error_ids=["error-000001"],
    )

    validation = ReflectionClaimValidator().validate(
        ReflectionCandidate("Fix the failing test", "success", [invented]), evidence
    )

    assert not validation.valid_claims
    assert any(issue.code == "claim_statement_not_grounded" for issue in validation.issues)


# ── C: verification linkage must not hinge on truncation ────────────────


def test_a_verbose_failure_still_counts_as_verified() -> None:
    """The defect this pins down.

    Verification used to be linked to a recovery by token overlap against the
    verification summary. For a whole-suite run that summary is "1 passed in
    0.01s", whose only shared token was the duration -- "01s". This output is
    640 characters, the evidence limit is 600, and the trailing "1 failed in
    0.01s" fell off the end. The fix was demoted from confirmed to inferred
    because pytest had been wordy.
    """
    assert len(PYTEST_OUTPUT) > EVIDENCE_MAX_TEXT_CHARS
    assert sanitize_evidence_text(PYTEST_OUTPUT).endswith("...[truncated]")

    validation, decision = _reflect(_fix_trace())

    recovery = next(c for c in validation.valid_claims if c.claim_type == "recovery")
    assert recovery.epistemic_status == "confirmed"
    assert decision.accepted is True
    assert "verified_solution" in decision.durable_signals


def test_a_short_and_a_verbose_failure_reach_the_same_verdict() -> None:
    """Same event, different verbosity, same conclusion."""
    terse = "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError\n1 failed in 0.01s"

    _, verbose_decision = _reflect(_fix_trace())
    _, terse_decision = _reflect(_fix_trace(terse))

    assert verbose_decision.durable_signals == terse_decision.durable_signals


def test_verification_links_by_red_green_not_by_shared_wording() -> None:
    """Isolate the linkage rule from the token-overlap fallback.

    The fallback only ever connected these two by accident: a failure ending
    "1 failed in 0.01s" and a pass reading "1 passed in 0.01s" share the token
    "01s", the duration. Here the runs took different amounts of time and the
    failure does not print one at all, so nothing is shared and only the
    red-green pattern -- this same check failed before the change -- can
    establish that the fix was verified.
    """
    failure = (
        "=================================== FAILURES ===================================\n"
        "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError"
    )
    trace = _fix_trace(failure)
    trace[6]["output_summary"] = "1 passed in 2.34s"

    from minicode.reflection_synthesis import _text_tokens

    assert not (
        _text_tokens(failure) & _text_tokens("1 passed in 2.34s")
    ), "fixture must not share wording, or it would not isolate the rule"

    validation, decision = _reflect(trace)

    recovery = next(c for c in validation.valid_claims if c.claim_type == "recovery")
    assert recovery.epistemic_status == "confirmed"
    assert "verified_solution" in decision.durable_signals


def test_a_check_that_never_failed_before_the_change_is_not_a_link() -> None:
    """Red-green needs the red: a suite that only ever passed proves nothing."""
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "read_file", "input": {"path": "src/leasekit/lease.py"}, "files": ["src/leasekit/lease.py"], "files_read": ["src/leasekit/lease.py"]},
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": "read_file", "status": "error", "is_error": True, "output_summary": "error[not_found]: File does not exist."},
        {"event_id": "e3", "type": "error", "call_id": "c1", "tool_name": "read_file", "error_type": "ToolError", "message": "error[not_found]: File does not exist."},
        {"event_id": "e4", "type": "tool_call", "call_id": "c2", "tool_name": "write_file", "input": {"path": "src/leasekit/lease.py"}, "files": ["src/leasekit/lease.py"], "files_changed": ["src/leasekit/lease.py"]},
        {"event_id": "e5", "type": "tool_result", "call_id": "c2", "tool_name": "write_file", "status": "success", "output_summary": "written", "files_changed": ["src/leasekit/lease.py"]},
        {"event_id": "e6", "type": "tool_call", "call_id": "c3", "tool_name": "run_command", "input": {"command": "ruff check ."}},
        {"event_id": "e7", "type": "tool_result", "call_id": "c3", "tool_name": "run_command", "status": "success", "output_summary": "All checks passed!"},
        {"event_id": "e8", "type": "task_result", "status": "success", "had_errors": True, "errors_recovered": True, "tool_error_count": 1},
    ]

    validation, _ = _reflect(trace)

    recoveries = [c for c in validation.valid_claims if c.claim_type == "recovery"]
    assert all(claim.epistemic_status != "confirmed" for claim in recoveries)


# ── E: bounding markers are not signals ─────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lease.transfer(...[truncated]", "lease.transfer"),
        ('        lease.transfer("bob")\n...[truncated]', 'lease.transfer("bob")'),
        ("...[truncated middle]...", ""),
        (
            "noise\n...[truncated middle]...\n"
            "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError",
            "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError",
        ),
    ],
)
def test_a_bounding_marker_never_becomes_the_failure_signal(
    raw: str, expected: str
) -> None:
    """Output is bounded in more than one place before reflection sees it.

    A real run produced "When run_command fails with lease.transfer(...
    [truncated]" -- the marker had become the signal, and the dangling "("
    came from a source line cut mid-expression.
    """
    from minicode.reflection_synthesis import _salient_line

    salient = _salient_line(raw)

    assert "truncated" not in salient
    if expected:
        assert salient == expected


def test_a_condition_built_from_a_truncated_line_stays_readable() -> None:
    from minicode.reflection_evidence import ErrorEvidence
    from minicode.reflection_synthesis import RuleReflectionSynthesizer

    error = ErrorEvidence(
        "error-1",
        "call-1",
        "run_command",
        "ToolError",
        'F [100%]\n=== FAILURES ===\n    def test_x():\n        lease.transfer(...[truncated]',
        ("event-1",),
    )

    condition = RuleReflectionSynthesizer()._error_applies_when(error)

    assert condition == "When run_command fails with lease.transfer."
    assert "truncated" not in condition


# ── F: a constraint is the declaration, not the file ────────────────────


READ_FILE_OUTPUT = '''FILE: pyproject.toml
OFFSET: 0
END: 152
TOTAL_CHARS: 152
TRUNCATED: no
[project]
name = "leasekit"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pytest>=8.0"]

[tool.pytest.ini_options]
pythonpath = ["src"] This is very revealing. There was a memory entry describing a root cause.'''


def test_a_constraint_claim_quotes_the_declaration_only() -> None:
    """A real run stored the read_file header and the model's musings as a
    "constraint", because the extractor pasted the whole tool result whenever
    "requires-python" appeared anywhere in it."""
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "read_file", "input": {"path": "pyproject.toml"}, "files": ["pyproject.toml"], "files_read": ["pyproject.toml"]},
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": "read_file", "status": "success", "output_summary": READ_FILE_OUTPUT, "files_read": ["pyproject.toml"]},
        {"event_id": "e3", "type": "task_result", "status": "success", "had_errors": False, "tool_error_count": 0},
    ]

    validation, decision = _reflect(trace)

    constraint = next(c for c in validation.valid_claims if c.claim_type == "constraint")
    assert constraint.statement == (
        'Project constraint: Python 3.11 is required: requires-python = ">=3.11"'
    )
    assert "TOTAL_CHARS" not in constraint.statement
    assert "very revealing" not in constraint.statement
    assert "stable_project_constraint" in decision.durable_signals


def test_a_normative_project_policy_line_becomes_a_stable_constraint() -> None:
    trace = [
        {
            "event_id": "e1",
            "type": "tool_call",
            "call_id": "c1",
            "tool_name": "read_file",
            "input": {"path": "POLICY.md"},
            "files": ["POLICY.md"],
            "files_read": ["POLICY.md"],
        },
        {
            "event_id": "e2",
            "type": "tool_result",
            "call_id": "c1",
            "tool_name": "read_file",
            "status": "success",
            "output_summary": (
                "FILE: POLICY.md\nOFFSET: 0\nEND: 70\nTOTAL_CHARS: 70\n"
                "TRUNCATED: no\n\n# Public API Policy\n"
                "- All public dates must use YYYY-MM-DD.\n"
            ),
            "files_read": ["POLICY.md"],
        },
        {
            "event_id": "e3",
            "type": "task_result",
            "status": "success",
            "had_errors": False,
            "tool_error_count": 0,
        },
    ]

    validation, decision = _reflect(trace)

    constraint = next(
        claim for claim in validation.valid_claims if claim.claim_type == "constraint"
    )
    assert constraint.statement == (
        "Project constraint: All public dates must use YYYY-MM-DD."
    )
    assert constraint.evidence_ids == ["e2"]
    assert "stable_project_constraint" in decision.durable_signals


def test_wrapped_policy_constraint_keeps_continuation_and_scope() -> None:
    trace = [
        {
            "event_id": "e1",
            "type": "tool_call",
            "call_id": "c1",
            "tool_name": "read_file",
            "input": {"path": "POLICY.md"},
            "files": ["POLICY.md"],
            "files_read": ["POLICY.md"],
        },
        {
            "event_id": "e2",
            "type": "tool_result",
            "call_id": "c1",
            "tool_name": "read_file",
            "status": "success",
            "output_summary": (
                "FILE: POLICY.md\nOFFSET: 0\nEND: 300\nTOTAL_CHARS: 300\n"
                "TRUNCATED: no\n\n# Audit Correlation Policy\n\n"
                "Every outbound audit-event correlation token must use `ZETA-` "
                "followed by\nexactly four uppercase hexadecimal characters "
                "(`0-9` or `A-F`).\n\n"
                "This rule applies only to outbound audit-event correlation "
                "tokens. It does not\napply to database IDs, user IDs, or internal "
                "trace IDs.\n\nContact the platform team with policy questions.\n"
            ),
            "files_read": ["POLICY.md"],
        },
        {
            "event_id": "e3",
            "type": "task_result",
            "status": "success",
            "had_errors": False,
            "tool_error_count": 0,
        },
    ]

    validation, decision = _reflect(trace)

    constraint = next(
        claim for claim in validation.valid_claims if claim.claim_type == "constraint"
    )
    assert constraint.statement == (
        "Project constraint: Every outbound audit-event correlation token must use "
        "`ZETA-` followed by exactly four uppercase hexadecimal characters "
        "(`0-9` or `A-F`). This rule applies only to outbound audit-event "
        "correlation tokens. It does not apply to database IDs, user IDs, or "
        "internal trace IDs."
    )
    assert constraint.evidence_ids == ["e2"]
    assert "stable_project_constraint" in decision.durable_signals


# ── G: one crash is one failure ─────────────────────────────────────────


CRASH = (
    "error[tool_crashed]: Tool run_command failed with FileNotFoundError. "
    "Details were written to the local log."
)


def test_a_crash_reported_twice_is_one_error() -> None:
    """A crashing tool emits a tool_result carrying the wrapper type and an
    error carrying the underlying one, over an identical message. That keyed
    twice and produced "run_command / FileNotFoundError" and
    "run_command / ToolError" as separate error_pattern claims."""
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "run_command", "input": {"command": "pytest"}},
        # The wrapper type is carried explicitly, exactly as the real trace
        # did. Letting the regex infer it from the message instead would give
        # both events the same type and merge them for the wrong reason.
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": "run_command", "status": "error", "is_error": True, "error_type": "ToolError", "output_summary": CRASH},
        {"event_id": "e3", "type": "error", "call_id": "c1", "tool_name": "run_command", "error_type": "FileNotFoundError", "message": CRASH},
        {"event_id": "e4", "type": "task_result", "status": "failed", "had_errors": True, "tool_error_count": 1},
    ]

    evidence = TraceEvidenceExtractor().extract("Run the suite", trace)

    assert len(evidence.errors) == 1
    error = evidence.errors[0]
    # The underlying exception is kept over the wrapper that reported it.
    assert error.error_type == "FileNotFoundError"
    assert set(error.source_event_ids) == {"e2", "e3"}


def test_genuinely_different_failures_of_one_call_stay_separate() -> None:
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "run_command", "input": {"command": "ruff"}},
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": "run_command", "status": "error", "is_error": True, "output_summary": "E501 line too long"},
        {"event_id": "e3", "type": "error", "call_id": "c1", "tool_name": "run_command", "error_type": "ValueError", "message": "totally unrelated parse failure"},
        {"event_id": "e4", "type": "task_result", "status": "failed", "had_errors": True, "tool_error_count": 2},
    ]

    evidence = TraceEvidenceExtractor().extract("Lint", trace)

    assert len(evidence.errors) == 2


# ── H: redaction must not corrupt ordinary code prose ───────────────────


@pytest.mark.parametrize(
    "text",
    [
        "api_key=sk-abcdefghijklmnopqrstuvwxyz",
        "password=hunter2",
        "CUSTOM_API_KEY=e5f8a1b2c3d4",
        "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27u",
        "secret_key=aGVsbG93b3JsZDEyMzQ1",
        "credential=abcdef123456",
        "token=1234567890123",
    ],
)
def test_real_secrets_are_still_redacted(text: str) -> None:
    assert "[REDACTED" in sanitize_evidence_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "set token = 1 first",
        "the field _token=1 is stale",
        "_token = self._store_token",
        "password = None means unset",
        "secret = False by default",
        "token=0",
        "auth_token = self.session.token",
    ],
)
def test_ordinary_code_prose_survives_redaction(text: str) -> None:
    """A real run stored a correct root-cause explanation as
    "sets `_token=[REDACTED] BEFORE the increment"."""
    assert sanitize_evidence_text(text) == text


# ── I: the trace layer bounds output before reflection sees it ──────────


COLOURED_PYTEST = (
    "\x1b[31mF\x1b[0m\x1b[31m" + " " * 72 + "[100%]\x1b[0m\n"
    "=================================== FAILURES ===================================\n"
    "\x1b[31m\x1b[1m________ test_renew_after_transfer ________\x1b[0m\n"
    "\n    \x1b[94mdef\x1b[39;49;00m \x1b[92mtest_renew_after_transfer\x1b[39;49;00m():\n"
    '        lease = Lease(\x1b[33m"\x1b[39;49;00m\x1b[33malice\x1b[39;49;00m\x1b[33m"\x1b[39;49;00m)\n'
    '        lease.transfer(\x1b[33m"\x1b[39;49;00m\x1b[33mbob\x1b[39;49;00m\x1b[33m"\x1b[39;49;00m)\n'
    ">       lease.renew()\n"
)


@pytest.mark.parametrize("limit", range(120, 700, 11))
def test_a_sequence_cut_in_half_upstream_never_corrupts_a_claim(limit: int) -> None:
    """The subtlest defect real data produced.

    agent_loop bounds each trace field long before the evidence layer runs, and
    the cut lands wherever it lands -- including the middle of an SGR sequence.
    ECMA-48 puts "." in the intermediate-byte class, so a trailing "\\x1b[33"
    followed by the appended "...[truncated]" was consumed as one sequence,
    "\\x1b[33...[", and a real memory recorded
    "When run_command fails with lease.transfer(truncated]".

    Every cut position has to survive it, not just the convenient ones.
    """
    from minicode.agent_loop import _redact_trace_text
    from minicode.reflection_evidence import _bound_error_message
    from minicode.reflection_synthesis import _salient_line

    bounded = _bound_error_message(_redact_trace_text(COLOURED_PYTEST, limit))
    salient = _salient_line(sanitize_evidence_text(bounded, 2_000))

    assert "\x1b" not in salient
    assert "truncated" not in salient
    # A sequence whose final byte was cut must not leave its parameters
    # behind as ordinary text ("lease.transfer([3"). pytest's own "[100%]"
    # closes its bracket, so requiring an unclosed one separates the two.
    assert not re.search(r"\[[0-9;?]+(?![0-9;?%\]])", salient), salient


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ("it sets `_token=1` BEFORE the increment", False),
        ("set token = 1 first", False),
        ("_token = self._store_token", False),
        ("api_key=sk-abcdefghijk", True),
        ("password=hunter2", True),
        ("Authorization: Bearer abc123xyz", True),
    ],
)
def test_the_trace_layer_shares_the_evidence_layer_redaction(
    text: str, redacted: bool
) -> None:
    """The trace layer decides what memory can ever contain, so it has to hold
    the same rule rather than a second, blunter copy of it."""
    from minicode.agent_loop import _redact_trace_text

    assert ("[REDACTED" in _redact_trace_text(text)) is redacted


# ── D: module paths are not files ───────────────────────────────────────


def test_a_dotted_module_path_is_not_mistaken_for_a_file() -> None:
    """"leasekit.lease" came from "leasekit.lease.StaleTokenError"."""
    validation, _ = _reflect(_fix_trace())

    recovery = next(c for c in validation.valid_claims if c.claim_type == "recovery")
    assert "leasekit.lease " not in recovery.applies_when
    assert "or leasekit.lease" not in recovery.applies_when


# ── J: a crashing tool is not project knowledge ─────────────────────────


@pytest.mark.parametrize(
    "tool",
    ["edit_file", "write_file", "patch_file"],
)
def test_a_tooling_crash_does_not_become_a_lesson(tool: str) -> None:
    """One real run recorded five of these -- edit_file, write_file and
    patch_file each crashing the same way -- which crowded out the single
    claim describing the actual code fix. A fault in the agent's own tooling
    says nothing about the project being edited."""
    message = (
        f"error[tool_crashed]: Tool {tool} failed with RuntimeError. "
        "Details were written to the local log"
    )
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": tool, "input": {"path": "src/lease.py"}},
        {"event_id": "e2", "type": "tool_result", "call_id": "c1", "tool_name": tool, "status": "error", "is_error": True, "error_type": "ToolError", "output_summary": message},
        {"event_id": "e3", "type": "error", "call_id": "c1", "tool_name": tool, "error_type": "RuntimeError", "message": message},
        {"event_id": "e4", "type": "tool_call", "call_id": "c2", "tool_name": tool, "input": {"path": "src/lease.py"}},
        {"event_id": "e5", "type": "tool_result", "call_id": "c2", "tool_name": tool, "status": "error", "is_error": True, "error_type": "ToolError", "output_summary": message},
        {"event_id": "e6", "type": "error", "call_id": "c2", "tool_name": tool, "error_type": "RuntimeError", "message": message},
        {"event_id": "e7", "type": "task_result", "status": "failed", "had_errors": True, "tool_error_count": 2},
    ]

    validation, decision = _reflect(trace)

    assert [c.claim_type for c in validation.valid_claims] == []
    assert decision.accepted is False


def test_a_real_project_failure_is_still_kept_alongside_a_crash() -> None:
    crash = "error[tool_crashed]: Tool edit_file failed with RuntimeError."
    failure = "FAILED tests/test_renew.py::test_renew_after_transfer - StaleTokenError"
    trace = [
        {"event_id": "e1", "type": "tool_call", "call_id": "c1", "tool_name": "edit_file", "input": {"path": "src/lease.py"}},
        {"event_id": "e2", "type": "error", "call_id": "c1", "tool_name": "edit_file", "error_type": "RuntimeError", "message": crash},
        {"event_id": "e3", "type": "tool_call", "call_id": "c2", "tool_name": "run_command", "input": {"command": COMMAND}},
        {"event_id": "e4", "type": "error", "call_id": "c2", "tool_name": "run_command", "error_type": "CommandError", "message": failure},
        {"event_id": "e5", "type": "tool_call", "call_id": "c3", "tool_name": "run_command", "input": {"command": COMMAND}},
        {"event_id": "e6", "type": "error", "call_id": "c3", "tool_name": "run_command", "error_type": "CommandError", "message": failure},
        {"event_id": "e7", "type": "task_result", "status": "failed", "had_errors": True, "tool_error_count": 3},
    ]

    validation, _ = _reflect(trace)

    statements = " ".join(c.statement for c in validation.valid_claims)
    assert "StaleTokenError" in statements
    assert "tool_crashed" not in statements
