"""Environment failures must not become durable project memory.

The store this guards against had three entries, all of the same shape:

    Type: error_pattern
    Statement: Observed error pattern for web_search / ToolError:
               error[search_unavailable]: ... bing=redirect_blocked ...
    Applies when: When web_search reports ToolError.
    Limitations: Observed in one task trace; broader recurrence is not
                 yet established.

Every one described the host's network egress (a fake-ip proxy resolving
search engines into a range the SSRF guard blocks), carried no remedy, and
stated a trigger condition that merely restated the observation.
"""

from __future__ import annotations

import pytest

from minicode.reflection_evidence import ErrorEvidence, TaskEvidence
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    RuleReflectionSynthesizer,
    ReflectionClaim,
    ReflectionValueGate,
)


SEARCH_UNAVAILABLE = (
    "error[search_unavailable]: Web search is unavailable. Providers: "
    "bing=redirect_blocked, baidu=redirect_blocked, duckduckgo=redirect_blocked."
)
DESTINATION_BLOCKED = "error[destination_blocked]: The request destination is not allowed."


def _error(
    index: int,
    tool: str,
    message: str,
    *,
    error_type: str = "ToolError",
) -> ErrorEvidence:
    return ErrorEvidence(
        f"error-{index}",
        f"call-{index}",
        tool,
        error_type,
        message,
        (f"event-{index}",),
    )


def _error_claim(index: int, statement: str) -> ReflectionClaim:
    return ReflectionClaim(
        f"claim-{index}",
        "error_pattern",
        f"observed_{index}",
        statement,
        [f"event-{index}"],
        "confirmed",
        applies_when="When web_search reports ToolError.",
        limitations=["Observed in one task trace."],
        related_error_ids=[f"error-{index}"],
    )


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        ("web_search", SEARCH_UNAVAILABLE),
        ("web_fetch", DESTINATION_BLOCKED),
        ("web_fetch", "error[http_error]: The server returned 503."),
        ("http_request", "error[resolver_busy]: The DNS resolver is saturated."),
        ("run_command", "getaddrinfo failed for pypi.org"),
        ("run_command", "connection refused while contacting the registry"),
        ("run_command", "certificate verify failed: unable to get local issuer"),
    ],
)
def test_environment_failures_produce_no_error_pattern_claim(
    tool: str, message: str
) -> None:
    evidence = TaskEvidence(
        errors=[_error(1, tool, message)],
        outcome="failed",
        had_errors=True,
    )

    candidate = RuleReflectionSynthesizer().synthesize("Search for a name", evidence)

    assert [claim.claim_type for claim in candidate.claims] == []


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        (
            "run_command",
            "pytest failed: tests/test_lease.py::test_renew raised StaleToken.",
        ),
        # A project that itself implements DNS, TLS or proxying owns these
        # failures; the transport vocabulary must not annex them.
        ("run_command", "pytest failed: src/dns/parser.py rejected a valid A record."),
        ("run_command", "mypy: src/proxy/pool.py:31 incompatible return type."),
        ("run_command", "pytest failed: tests/test_ssl.py::test_cipher_order."),
        # Codes are environment-scoped only from a network-egress tool.
        ("run_command", "error[server_error]: the fixture server returned 500."),
    ],
)
def test_project_failures_still_produce_an_error_pattern_claim(
    tool: str, message: str
) -> None:
    """The guard must not swallow real project failures."""
    evidence = TaskEvidence(
        errors=[_error(1, tool, message, error_type="CommandError")],
        outcome="failed",
        had_errors=True,
    )

    candidate = RuleReflectionSynthesizer().synthesize("Run the suite", evidence)

    assert [claim.claim_type for claim in candidate.claims] == ["error_pattern"]


def test_retrying_an_unreachable_endpoint_does_not_buy_durability() -> None:
    """The exact loophole the shipped store fell through.

    Two byte-identical web_search failures in one trace satisfy
    ``_has_recurrent_error``, which would otherwise lift
    ``single_observation_error_pattern`` and let the entry persist.
    """
    claims = [_error_claim(index, f"Observed error pattern: {SEARCH_UNAVAILABLE}") for index in (1, 2)]
    evidence = TaskEvidence(
        errors=[_error(index, "web_search", SEARCH_UNAVAILABLE) for index in (1, 2)],
        outcome="failed",
        had_errors=True,
    )

    decision = ReflectionValueGate().evaluate(
        ReflectionCandidate("Search twice", "failed", claims),
        ClaimValidationResult(valid_claims=claims),
        evidence,
    )

    assert decision.accepted is False
    assert decision.durable_signals == []
    assert "environment_scoped_error_pattern" in decision.reason_codes


def test_a_real_failure_alongside_an_environment_one_still_persists() -> None:
    """Scoping is per-claim, so one unreachable host cannot mute a real finding."""
    claims = [
        _error_claim(1, f"Observed error pattern: {SEARCH_UNAVAILABLE}"),
        _error_claim(2, "Observed error pattern: pytest failed with StaleToken twice."),
    ]
    evidence = TaskEvidence(
        errors=[
            _error(1, "web_search", SEARCH_UNAVAILABLE),
            _error(2, "run_command", "pytest failed with StaleToken twice.", error_type="CommandError"),
            _error(3, "run_command", "pytest failed with StaleToken twice.", error_type="CommandError"),
        ],
        outcome="failed",
        had_errors=True,
    )

    decision = ReflectionValueGate().evaluate(
        ReflectionCandidate("Repair the lease", "failed", claims),
        ClaimValidationResult(valid_claims=claims),
        evidence,
    )

    assert decision.accepted is True
    assert decision.durable_signals == ["reusable_error_pattern"]
