"""Deterministic claim synthesis, validation, and durable-value selection.

The module consumes only ``TaskEvidence``.  It does not re-read execution
traces, call a model, persist memory, or reinterpret raw tool payloads.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from minicode.reflection_evidence import (
    DecisionEvidence,
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    VerificationEvidence,
    sanitize_evidence_text,
)


ClaimType = Literal[
    "constraint",
    "dependency",
    "error_pattern",
    "root_cause",
    "recovery",
    "decision",
    "correction",
    "verification_rule",
    "warning",
    "approach",
]
ClaimSeverity = Literal["info", "warning", "error"]

CLAIM_MAX_TEXT_CHARS = 1_200
CLAIM_MAX_AUX_TEXT_CHARS = 600
CLAIM_MAX_ITEMS = 256


@runtime_checkable
class ReflectionSynthesizer(Protocol):
    """Common candidate-only synthesis contract."""

    def synthesize(
        self,
        task_description: str,
        evidence: TaskEvidence,
    ) -> ReflectionCandidate: ...

_GENERIC_CLAIMS = (
    "task completed successfully",
    "used unique tool",
    "used tools",
    "errors occurred with tool",
    "review error patterns",
    "consider breaking the task into smaller steps",
    "this approach worked",
    "follow best practices",
    "test after making changes",
)
_GENERIC_ERROR_MESSAGES = {
    "error",
    "failed",
    "failure",
    "operation failed",
    "operation failed before completion",
    "tool error",
    "unknown error",
    "an error occurred",
}
# Tools whose failures describe the host's network egress rather than the
# project being worked on.
_NETWORK_EGRESS_TOOLS = {"web_search", "web_fetch", "http_request"}
# Transport/reachability codes emitted by those tools. A failure carrying one
# of these describes the machine the agent happens to be running on -- a proxy,
# a DNS policy, an unreachable remote host -- so it is state, not knowledge:
# it does not generalise to another machine and it carries no remedy the agent
# could apply. Persisting it produces memories whose "applies when" merely
# restates the error.
_ENVIRONMENT_ERROR_CODES = {
    "destination_blocked",
    "http_error",
    "network_unavailable",
    "rate_limited",
    "redirect_blocked",
    "resolver_busy",
    "search_unavailable",
    "server_error",
}
# Transport failures are environment-scoped whichever tool surfaces them.
# Kept to phrases that only a transport layer emits: a project that itself
# works on DNS, TLS or proxying must keep its own failures, so bare "dns",
# "ssl" and "proxy" are deliberately not enough on their own.
_ENVIRONMENT_ERROR_PATTERN = re.compile(
    r"\b(?:dns (?:resolution|lookup|resolver)|getaddrinfo|"
    r"name or service not known|temporary failure in name resolution|"
    r"connection (?:refused|reset|aborted|timed out)|network is unreachable|"
    r"no route to host|sslerror|ssl handshake fail|"
    r"certificate verify failed|proxy (?:error|connection))\b"
)
_READ_TOOLS = {"read_file"}
_SEARCH_TOOLS = {"grep_files", "search_files", "find_symbols", "find_references"}
_LIST_TOOLS = {"list_directory", "list_files", "directory_tree"}
_FORMAT_TOOLS = {"format_file", "formatter"}


def _ordered_unique(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))[:CLAIM_MAX_ITEMS]


def _normalize_text(value: Any) -> str:
    return " ".join(sanitize_evidence_text(value, CLAIM_MAX_TEXT_CHARS).strip().lower().split())


# A pytest node id is the most precise handle a later run can match on, so it
# is tried before a plain path.
_TEST_NODE_RE = re.compile(r"((?:[\w.-]+/)*[\w.-]+\.py::[\w.\[\]-]+)")
# The leading lookbehind rejects anything preceded by "/", "~" or a word
# character, so an absolute path contributes nothing: only workspace-relative
# names travel to another machine, and only they are safe to persist.
# A slash is strong evidence of a path, so that form stays permissive. A bare
# "name.ext" is not: "leasekit.lease" and "self._token" look identical to it,
# so it is restricted to extensions a file actually carries.
_FILE_EXTENSIONS = (
    "py|pyi|ts|tsx|js|jsx|mjs|cjs|json|toml|yaml|yml|md|rst|txt|ini|cfg|conf|"
    "lock|rs|go|java|kt|rb|php|cs|swift|c|h|cc|cpp|hpp|sh|bash|zsh|sql|"
    "html|css|scss|xml|csv|env"
)
_RELATIVE_PATH_RE = re.compile(
    rf"(?<![\w/~.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z][A-Za-z0-9]{{0,5}}"
    rf"|[\w-]+\.(?:{_FILE_EXTENSIONS}))(?![\w/])"
)
# The artifact regexes already refuse absolute paths, but the residue
# fallback quotes what is left of the message, so it needs its own scrub:
# a home directory or a drive letter is this machine's layout, useless to a
# later run and not something a condition should carry.
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|~[\\/]|(?<![\w.-])/)[^\s'\"()\[\]]*"
)
_FAILURE_CODE_RE = re.compile(r"error\[([a-z_]+)\]")
_EXCEPTION_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Failure|Timeout|Denied|NotFound))\b"
)
_CAMEL_SIGNAL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b")
# Generic wrappers say nothing about what actually went wrong; the message
# almost always carries the real signal underneath one of these.
_GENERIC_ERROR_TYPES = {"toolerror", "unknownerror", "commanderror", "exception", "error"}
_APPLIES_WHEN_MAX_ARTIFACTS = 2
_CONNECTIVE = r"\b(?:and|or|in|on|at|for|from|of|the|a)\b"
_TRIM_PUNCT = r"[\s:,;.\-—、，。；：]"


# A failing command's message is its whole output: a pytest run contributes a
# progress bar, a banner, a source excerpt and a summary. Only one line names
# what actually failed.
_SALIENT_LINE_RE = re.compile(
    r"^(?:FAILED|ERROR|E\s)|(?:\b\w*(?:Error|Exception|Failure)\b\s*:)|^error\["
    # Tools that render their own report mark failures with a glyph rather
    # than a keyword; without these, a failing test_runner run matched nothing
    # and fell through to the last-line fallback.
    r"|^[✗❌⚠]",
)
# Lines that report success or are pure decoration. They are never the reason
# something failed, but the fallback used to reach for them: a failing
# test_runner run was recorded as "error pattern ... ⊘ Skipped: 0" and
# "error pattern ... ✓ [::unknow" -- a counter and a passing test.
_NON_FAILURE_LINE_RE = re.compile(r"^[✓⊘✅ℹ📊📈🧪🔍]|^\s*(?:✓|⊘)\s")
_BANNER_RE = re.compile(r"^[=\-_~*#\s]*$|^[=\-_]{3,}.*[=\-_]{3,}$")
# Output is bounded in more than one place before reflection sees it, so a
# message can arrive already carrying a marker. Left in, the marker becomes
# the "signal": a real run produced
# ``When run_command fails with lease.transfer(...[truncated]``.
_TRUNCATION_MARKER_RE = re.compile(r"\.*\[[a-z ]*truncated[a-z ]*\]\.*")
# A line cut mid-expression ends on an opening delimiter or a connector; those
# tails read as noise once quoted into a condition.
_DANGLING_TAIL_RE = re.compile(r"[\s(\[{,=+\-/*.:]+$")



def _salient_line(message: str) -> str:
    """Reduce multi-line tool output to the line that names the failure.

    Feeding the whole dump to the condition produced
    ``When run_command fails with F [100%] ==== FAILURES ====`` -- the banner
    won the race simply by coming first.
    """
    message = _TRUNCATION_MARKER_RE.sub(" ", message)
    lines = [line.strip() for line in message.splitlines()]
    lines = [
        line
        for line in lines
        if line and not _BANNER_RE.match(line) and not _NON_FAILURE_LINE_RE.match(line)
    ]
    if not lines:
        # Everything was a banner or a success counter. There is no failure
        # line to quote, and quoting "⊘ Skipped: 0" instead invents one, so
        # the caller is told there is no signal and the claim degrades or
        # drops rather than asserting something the output never said.
        return ""
    for line in lines:
        if _SALIENT_LINE_RE.search(line):
            return _DANGLING_TAIL_RE.sub("", line) or line
    # Falling back to the last line keeps it adjacent to the failure. Skipping
    # over a truncated one to an earlier line traded a readable fragment for
    # an unrelated header ("F [100%]"), which is worse; trimming the dangling
    # tail is enough. Fragments that carry no failure information at all --
    # "✓ [::unknow" -- are already gone, filtered above as success lines.
    return _DANGLING_TAIL_RE.sub("", lines[-1]) or lines[-1]


def _named_artifacts(message: str) -> tuple[list[str], list[str]]:
    """Name the concrete things a failure was about.

    An applicability condition is only useful if a later run can check it.
    "When run_command reports CommandError" cannot be checked -- it restates
    the observation. "When run_command fails on tests/test_lease.py::test_renew"
    can.

    Returns (shown, all_found). Only `shown` reaches the condition, but the
    full list is needed to strip a message down to its residue: leaving the
    artifacts past the cap in place produces "fails on a.py or b.py with
    ... and and c.py".
    """
    found: list[str] = []
    for match in _TEST_NODE_RE.finditer(message):
        found.append(match.group(1))
    for match in _RELATIVE_PATH_RE.finditer(message):
        path = match.group(1)
        if not any(path in item for item in found):
            found.append(path)
    found = list(dict.fromkeys(found))
    return found[:_APPLIES_WHEN_MAX_ARTIFACTS], found


def _failure_signal(error: ErrorEvidence, artifacts: list[str]) -> str:
    """Name what went wrong, preferring the message's own code over the wrapper.

    Returns "" when the message adds nothing beyond the artifacts already
    named, so the condition stays free of "fails on X with ... X ...".
    """
    message = _salient_line(sanitize_evidence_text(error.message, 2_000))
    code = _FAILURE_CODE_RE.search(message)
    if code:
        return code.group(1)
    for pattern in (_EXCEPTION_NAME_RE, _CAMEL_SIGNAL_RE):
        match = pattern.search(message)
        if match:
            return match.group(1)
    error_type = sanitize_evidence_text(error.error_type or "", 80).strip()
    if error_type and _normalize_text(error_type) not in _GENERIC_ERROR_TYPES:
        return error_type
    residue = _ABSOLUTE_PATH_RE.sub(" ", message)
    for artifact in artifacts:
        residue = residue.replace(artifact, " ")
    # Removing the artifacts leaves line/column suffixes and connectives
    # dangling; on their own they carry no meaning.
    residue = re.sub(r":\d+(?::\d+)?", " ", residue)
    residue = " ".join(residue.split())
    residue = re.sub(rf"{_CONNECTIVE}(?:\s+{_CONNECTIVE})+", " ", residue)
    residue = re.sub(rf"\s+{_CONNECTIVE}$", "", " ".join(residue.split()))
    residue = re.sub(rf"^{_TRIM_PUNCT}+|{_TRIM_PUNCT}+$", "", residue).strip()
    return residue[:80] if len(residue) >= 3 and re.search(r"[A-Za-z一-鿿]", residue) else ""


# A tool crashing is a fault in the agent's own tooling, not a fact about the
# project it is editing. One real run recorded five of these -- edit_file,
# write_file and patch_file each crashing the same way -- and they crowded out
# the single claim that described the actual code fix.
_TOOLING_FAULT_RE = re.compile(
    r"\berror\[(?:tool_crashed|sub_agent_depth_exceeded)\]"
    # A tool hitting its own wall-clock limit describes how long this machine
    # took, not anything about the project: real runs stored "Tool
    # 'run_command' timed out after 120s" and "Tool 'task' timed out after
    # 120s" as durable lessons.
    r"|\btool '[^']+' timed out after \d+"
    r"|\btools? timed out after \d+"
)


def _is_environment_scoped_error(error: ErrorEvidence) -> bool:
    """Report whether a failure describes the host environment, not the project.

    An unreachable search provider, an SSRF guard refusing a private
    destination, or a saturated DNS resolver all say something about the
    machine the run happened on. None of them generalise to the next machine
    and none of them imply an action the agent could take differently, so a
    durable memory built from one reads "when web_search reports ToolError,
    web_search reported ToolError" -- a restatement, not a prediction.
    """
    haystack = _normalize_text(f"{error.error_type or ''} {error.message}")
    if not haystack:
        return False
    if _TOOLING_FAULT_RE.search(haystack):
        return True
    if _ENVIRONMENT_ERROR_PATTERN.search(haystack):
        return True
    if _normalize_text(error.tool_name) not in _NETWORK_EGRESS_TOOLS:
        return False
    return any(
        re.search(rf"\b{re.escape(code)}\b", haystack)
        for code in _ENVIRONMENT_ERROR_CODES
    )


def _normalize_semantic_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9_\u4e00-\u9fff]+",
        "_",
        sanitize_evidence_text(value, 160).lower(),
    ).strip("_") or "claim"


def _semantic_slug(value: str, prefix: str) -> str:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower())
    body = "_".join(tokens[:12]).strip("_") or "fact"
    return f"{prefix}_{body}"[:160].rstrip("_")


def _config_constraint_semantic_key(statement: str) -> str:
    """Bind a policy claim to its stable subject, not its current value."""
    lowered = statement.lower()
    if "requires-python" in lowered or re.match(
        r"(?i)^python\s+\d+(?:\.\d+)+\s+is\s+required\b",
        statement.strip(),
    ):
        return "project_constraint_python_runtime_version"
    operator = re.search(
        r"(?i)\b(?:must|shall|required|requires|always|never|do\s+not|don't|"
        r"cannot|may\s+not)\b|(?:必须|需要|不得|禁止|始终|一律)",
        statement,
    )
    if operator is None or operator.start() < 4:
        return _semantic_slug(statement, "project_constraint")
    subject = statement[: operator.start()].strip(" :-")
    subject = re.sub(r"(?i)^(?:every|all|the)\s+", "", subject).strip()
    return _semantic_slug(subject or statement, "project_constraint")


def _text_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3
        and token
        not in {
            "after",
            "before",
            "changed",
            "current",
            "error",
            "failed",
            "passed",
            "project",
            "result",
            "test",
            "tests",
            "this",
            "tool",
            "using",
            "with",
        }
    }


def _cjk_bigrams(value: str) -> set[str]:
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    return {chars[index : index + 2] for index in range(max(0, len(chars) - 1))}


def _event_position(evidence: TaskEvidence, event_ids: list[str] | tuple[str, ...]) -> int:
    positions = [evidence.event_positions[event_id] for event_id in event_ids if event_id in evidence.event_positions]
    return max(positions, default=-1)


@dataclass
class ReflectionClaim:
    """One reusable proposition linked to exact evidence records."""

    claim_id: str
    claim_type: ClaimType
    semantic_key: str
    statement: str
    evidence_ids: list[str]
    epistemic_status: Literal["confirmed", "inferred", "unknown"]
    applies_when: str = ""
    limitations: list[str] = field(default_factory=list)
    verification_ids: list[str] = field(default_factory=list)
    related_error_ids: list[str] = field(default_factory=list)
    related_recovery_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "semantic_key": self.semantic_key,
            "statement": self.statement,
            "evidence_ids": list(self.evidence_ids),
            "epistemic_status": self.epistemic_status,
            "applies_when": self.applies_when,
            "limitations": list(self.limitations),
            "verification_ids": list(self.verification_ids),
            "related_error_ids": list(self.related_error_ids),
            "related_recovery_ids": list(self.related_recovery_ids),
        }


@dataclass
class ReflectionCandidate:
    """Untrusted claim proposal produced before deterministic validation."""

    task_summary: str
    outcome: Literal["success", "failed", "unknown"]
    claims: list[ReflectionClaim] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    synthesis_diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_summary": self.task_summary,
            "outcome": self.outcome,
            "claims": [claim.to_dict() for claim in self.claims],
            "source_event_ids": list(self.source_event_ids),
            "synthesis_diagnostics": list(self.synthesis_diagnostics),
        }


@dataclass(frozen=True)
class ClaimValidationIssue:
    code: str
    message: str
    claim_id: str | None = None
    severity: ClaimSeverity = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "claim_id": self.claim_id,
            "severity": self.severity,
        }


@dataclass
class ClaimValidationResult:
    valid_claims: list[ReflectionClaim] = field(default_factory=list)
    rejected_claims: list[ReflectionClaim] = field(default_factory=list)
    issues: list[ClaimValidationIssue] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return bool(self.valid_claims)

    def to_dict(self, *, include_rejected_text: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accepted": self.accepted,
            "valid_claims": [claim.to_dict() for claim in self.valid_claims],
            "rejected_claim_ids": [claim.claim_id for claim in self.rejected_claims],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if include_rejected_text:
            result["rejected_claims"] = [claim.to_dict() for claim in self.rejected_claims]
        return result


@dataclass
class ReflectionValueDecision:
    accepted: bool = False
    reason_codes: list[str] = field(default_factory=lambda: ["missing_value_decision"])
    durable_signals: list[str] = field(default_factory=list)
    accepted_claim_ids: list[str] = field(default_factory=list)
    rejected_claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason_codes": list(self.reason_codes),
            "durable_signals": list(self.durable_signals),
            "accepted_claim_ids": list(self.accepted_claim_ids),
            "rejected_claim_ids": list(self.rejected_claim_ids),
        }


class RuleReflectionSynthesizer:
    """Build conservative claim candidates from normalized TaskEvidence."""

    def synthesize(self, task_description: str, evidence: TaskEvidence) -> ReflectionCandidate:
        claims: list[ReflectionClaim] = []
        source_event_ids: list[str] = []

        def add_claim(
            claim_type: ClaimType,
            semantic_key: str,
            statement: str,
            evidence_ids: list[str] | tuple[str, ...],
            epistemic_status: Literal["confirmed", "inferred", "unknown"],
            *,
            applies_when: str = "",
            limitations: list[str] | None = None,
            verification_ids: list[str] | None = None,
            related_error_ids: list[str] | None = None,
            related_recovery_ids: list[str] | None = None,
        ) -> None:
            bounded_ids = _ordered_unique(list(evidence_ids))
            claims.append(
                ReflectionClaim(
                    claim_id=f"claim-{len(claims) + 1:06d}",
                    claim_type=claim_type,
                    semantic_key=semantic_key,
                    statement=sanitize_evidence_text(statement, CLAIM_MAX_TEXT_CHARS),
                    evidence_ids=bounded_ids,
                    epistemic_status=epistemic_status,
                    applies_when=sanitize_evidence_text(applies_when, CLAIM_MAX_AUX_TEXT_CHARS),
                    limitations=[
                        sanitize_evidence_text(item, CLAIM_MAX_AUX_TEXT_CHARS)
                        for item in (limitations or [])[:CLAIM_MAX_ITEMS]
                    ],
                    verification_ids=_ordered_unique(verification_ids or []),
                    related_error_ids=_ordered_unique(related_error_ids or []),
                    related_recovery_ids=_ordered_unique(related_recovery_ids or []),
                )
            )
            source_event_ids.extend(bounded_ids)

        config_constraints = [
            item for item in evidence.decisions if item.source_kind == "config_constraint"
        ]
        for decision in evidence.decisions:
            self._synthesize_decision_claim(
                decision,
                task_description,
                evidence,
                add_claim,
            )

        confirmed_libraries = [
            item for item in evidence.libraries if item.status == "confirmed"
        ]
        routine_ruff_format = (
            {item.name for item in confirmed_libraries} == {"ruff"}
            and bool(evidence.files_changed)
            and not any(
                (
                    evidence.files_read,
                    evidence.errors,
                    evidence.recoveries,
                    evidence.decisions,
                )
            )
        )
        if confirmed_libraries and not config_constraints and not routine_ruff_format:
            names = sorted(dict.fromkeys(item.name for item in confirmed_libraries))
            library_ids = _ordered_unique(
                [event_id for item in confirmed_libraries for event_id in item.event_ids]
            )
            add_claim(
                "dependency",
                _semantic_slug("_".join(names), "project_dependencies"),
                f"Project confirmed dependencies: {', '.join(names)}.",
                library_ids,
                "confirmed",
            )

        recovery_error_ids = {
            error_id for recovery in evidence.recoveries for error_id in recovery.related_error_ids
        }
        for recovery in evidence.recoveries:
            self._synthesize_recovery_claim(recovery, evidence, add_claim)

        root_cause_error_ids = {
            error_id
            for claim in claims
            if claim.claim_type == "root_cause"
            for error_id in claim.related_error_ids
        }
        for error in evidence.errors:
            if error.error_id in recovery_error_ids or error.error_id in root_cause_error_ids:
                continue
            if not self._specific_error(error):
                continue
            if self._environment_scoped_error(error):
                continue
            add_claim(
                "error_pattern",
                _semantic_slug(
                    f"{error.tool_name or 'tool'} {error.error_type or ''} {error.message}",
                    "error",
                ),
                self._error_statement(error),
                error.source_event_ids,
                error.epistemic_status,
                applies_when=self._error_applies_when(error),
                limitations=["Observed in one task trace; broader recurrence is not yet established."],
                related_error_ids=[error.error_id],
            )

        self._synthesize_approach_claim(task_description, evidence, add_claim)

        return ReflectionCandidate(
            task_summary=sanitize_evidence_text(task_description, 200),
            outcome=evidence.outcome,
            claims=claims,
            source_event_ids=_ordered_unique(source_event_ids),
            synthesis_diagnostics=[],
        )

    def _synthesize_approach_claim(
        self,
        task_description: str,
        evidence: TaskEvidence,
        add_claim: Any,
    ) -> None:
        """Record how a cleanly successful, verified task was approached.

        The other claim types all fire on failure signals (errors, user
        corrections, dependency reads), so a flawless run used to leave
        nothing durable behind -- the better the agent performed, the less it
        "learned". This claim closes that gap, but only under the strict
        shape that made the other types trustworthy: real file changes, a
        passed verification, and the agent's own bounded causal summary.
        """
        if evidence.outcome != "success" or evidence.errors:
            return
        if not evidence.files_changed:
            return
        passed = [item for item in evidence.verification if item.result == "passed"]
        if not passed:
            return

        files = sorted({item.path for item in evidence.files_changed})
        files_shown = ", ".join(files[:5]) + (
            f" (+{len(files) - 5} more)" if len(files) > 5 else ""
        )
        verification = passed[-1]
        verify_desc = (
            (verification.summary or "").strip()
            or (verification.command_kind or "").strip()
            or (verification.tool_name or "").strip()
            or "verification"
        )

        # The agent's own one-line "why it worked"; dropped when it is too
        # thin to add anything over the deterministic facts.
        summary_line = ""
        if evidence.final_summary:
            candidate = _salient_line(evidence.final_summary)
            if len(_normalize_text(candidate)) >= 16:
                summary_line = candidate

        task_summary = sanitize_evidence_text(task_description, 120)
        statement = f"For '{task_summary}': "
        if summary_line:
            statement += f"{summary_line} "
        statement += f"Changed {files_shown}; verified by {verify_desc} (passed)."

        limitations: list[str] = []
        if any(item.scope == "targeted" for item in passed):
            limitations.append("Verified only by targeted checks.")

        file_event_ids = [
            event_id
            for item in evidence.files_changed
            for event_id in item.event_ids
        ]
        verification_event_ids = [
            event_id for item in passed for event_id in item.event_ids
        ]
        add_claim(
            "approach",
            _semantic_slug(f"{task_summary} {files_shown}", "approach"),
            statement,
            _ordered_unique(
                [*file_event_ids, *verification_event_ids, *evidence.final_summary_event_ids]
            ),
            "confirmed",
            applies_when=(
                f"When working on {files[0]} or similar tasks in this project."
            ),
            limitations=limitations or None,
            verification_ids=[item.verification_id for item in passed],
        )

    def _synthesize_decision_claim(
        self,
        decision: DecisionEvidence,
        task_description: str,
        evidence: TaskEvidence,
        add_claim: Any,
    ) -> None:
        statement = decision.statement.strip()
        if not statement or decision.source_kind == "inferred_rationale":
            return
        lowered = statement.lower()
        if decision.source_kind == "user_correction":
            add_claim(
                "correction",
                _semantic_slug(statement, "correction"),
                f"User correction: {statement}",
                decision.event_ids,
                decision.epistemic_status,
            )
            return
        if decision.source_kind in {"user_constraint", "config_constraint"}:
            if re.search(r"\b(?:verify|verification|test|check)\b|(?:验证|测试|检查)", lowered):
                passed = [item for item in evidence.verification if item.result == "passed"]
                if passed:
                    verification_ids = [item.verification_id for item in passed]
                    event_ids = list(decision.event_ids) + [
                        event_id for item in passed for event_id in item.event_ids
                    ]
                    add_claim(
                        "verification_rule",
                        _semantic_slug(statement, "verification_rule"),
                        f"Project verification rule: {statement}",
                        event_ids,
                        decision.epistemic_status,
                        applies_when=f"When {sanitize_evidence_text(task_description, 180)}.",
                        verification_ids=verification_ids,
                    )
                    return
            add_claim(
                "constraint",
                (
                    _config_constraint_semantic_key(statement)
                    if decision.source_kind == "config_constraint"
                    else _semantic_slug(statement, "project_constraint")
                ),
                f"Project constraint: {statement}",
                decision.event_ids,
                decision.epistemic_status,
            )
            return

        if re.search(r"\b(?:root cause|caused|causes)\b|(?:根因|导致)", lowered):
            related_errors = list(evidence.errors)
            related_recoveries = [
                recovery
                for recovery in evidence.recoveries
                if set(recovery.related_error_ids)
                & {error.error_id for error in related_errors}
            ]
            last_recovery = related_recoveries[-1] if related_recoveries else None
            passed = self._passed_verifications_after(last_recovery, evidence)
            linked_passed = [
                item
                for item in passed
                if last_recovery is not None
                and self._verification_matches_recovery(
                    item, last_recovery, related_errors, evidence
                )
            ]
            confirmed = bool(related_errors and related_recoveries and linked_passed)
            status: Literal["confirmed", "inferred", "unknown"] = (
                "confirmed"
                if confirmed and decision.epistemic_status == "confirmed"
                else "inferred"
            )
            limitations = []
            if status != "confirmed":
                limitations.append("The causal explanation is incomplete or lacks linked recovery verification.")
            if any(item.scope == "targeted" for item in linked_passed):
                limitations.append("The recovery was checked only by targeted verification.")
            event_ids = list(decision.event_ids)
            event_ids.extend(event_id for error in related_errors for event_id in error.source_event_ids)
            event_ids.extend(event_id for recovery in related_recoveries for event_id in recovery.event_ids)
            event_ids.extend(event_id for item in linked_passed for event_id in item.event_ids)
            add_claim(
                "root_cause",
                _semantic_slug(statement, "root_cause"),
                statement,
                event_ids,
                status,
                applies_when=self._error_applies_when(related_errors[0]) if related_errors else f"When {task_description}.",
                limitations=limitations,
                verification_ids=[item.verification_id for item in linked_passed],
                related_error_ids=[error.error_id for error in related_errors],
                related_recovery_ids=[item.recovery_id for item in related_recoveries],
            )
            return

        limitations: list[str] = []
        rationale = decision.rationale or ""
        if decision.epistemic_status == "inferred" or re.search(
            r"\b(?:may|might|probably|likely|older|compatib)\w*\b", rationale.lower()
        ):
            limitations.append("The decision is limited to the stated compatibility context.")
        add_claim(
            "decision",
            _semantic_slug(statement, "decision"),
            statement,
            decision.event_ids,
            decision.epistemic_status,
            applies_when=f"When {sanitize_evidence_text(task_description, 180)}.",
            limitations=limitations,
        )

    def _synthesize_recovery_claim(
        self,
        recovery: RecoveryEvidence,
        evidence: TaskEvidence,
        add_claim: Any,
    ) -> None:
        related_errors = [
            item for item in evidence.errors if item.error_id in recovery.related_error_ids
        ]
        if not related_errors:
            return
        passed = self._passed_verifications_after(recovery, evidence)
        linked_passed = [
            item for item in passed if self._verification_matches_recovery(item, recovery, related_errors, evidence)
        ]
        status: Literal["confirmed", "inferred", "unknown"] = (
            "confirmed"
            if linked_passed and recovery.epistemic_status == "confirmed"
            else "inferred"
        )
        limitations: list[str] = []
        if not linked_passed:
            limitations.append("The recovery has no successful verification linked to this failure.")
        elif any(item.scope == "targeted" for item in linked_passed):
            limitations.append("The recovery was checked only by targeted verification.")
        event_ids = [
            event_id for error in related_errors for event_id in error.source_event_ids
        ] + list(recovery.event_ids) + [
            event_id for item in linked_passed for event_id in item.event_ids
        ]
        error = related_errors[0]
        statement = (
            f"After {_salient_line(sanitize_evidence_text(error.message, 2_000))}, "
            f"the recovery action was: {recovery.action}."
        )
        if recovery.change_summary:
            # The old->new excerpt is what turns "a file was edited" into an
            # executable fix; the action text stays verbatim above so
            # statement-alignment validation still holds.
            statement += f" Change: {recovery.change_summary}."
        add_claim(
            "recovery",
            _semantic_slug(f"{recovery.action} {error.message}", "recovery"),
            statement,
            event_ids,
            status,
            applies_when=self._error_applies_when(error),
            limitations=limitations,
            verification_ids=[item.verification_id for item in linked_passed],
            related_error_ids=[item.error_id for item in related_errors],
            related_recovery_ids=[recovery.recovery_id],
        )

    def _passed_verifications_after(
        self,
        recovery: RecoveryEvidence | None,
        evidence: TaskEvidence,
    ) -> list[VerificationEvidence]:
        recovery_position = _event_position(evidence, recovery.event_ids) if recovery else -1
        return [
            item
            for item in evidence.verification
            if item.result == "passed"
            and (
                recovery_position < 0
                or _event_position(evidence, item.event_ids) < 0
                or _event_position(evidence, item.event_ids) > recovery_position
            )
        ]

    def _verification_matches_recovery(
        self,
        verification: VerificationEvidence,
        recovery: RecoveryEvidence,
        errors: list[ErrorEvidence],
        evidence: TaskEvidence,
    ) -> bool:
        if (
            verification.call_id
            and verification.call_id in recovery.verification_call_ids
        ):
            return True
        if verification.scope == "full":
            return True
        if self._reproduces_an_earlier_failure(verification, recovery, evidence):
            return True
        recovery_text = " ".join(
            [recovery.action, *recovery.files_changed, *(item.message for item in errors)]
        )
        verification_files = [
            item.path
            for item in evidence.referenced_files
            if verification.call_id and item.call_id == verification.call_id
        ]
        verification_text = " ".join([verification.summary, *verification_files])
        if _text_tokens(recovery_text) & _text_tokens(verification_text):
            return True
        return bool(_cjk_bigrams(recovery_text) & _cjk_bigrams(verification_text))

    def _reproduces_an_earlier_failure(
        self,
        verification: VerificationEvidence,
        recovery: RecoveryEvidence,
        evidence: TaskEvidence,
    ) -> bool:
        """Report the red-green pattern: this check failed before the change.

        The token-overlap fallback below compares a recovery against a
        verification summary, but a whole-suite summary is "1 passed in
        0.01s" -- it shares nothing with the failure except, by coincidence,
        the duration string. A real run showed the consequence: 640 characters
        of coloured pytest output were truncated at the 600-character evidence
        limit, the trailing "1 failed in 0.01s" was lost with it, and the only
        overlapping token went with it, silently demoting a genuinely verified
        fix to "inferred". Whether a fix counts as verified must not depend on
        how verbose the tool was.
        """
        if not verification.command_kind:
            return False
        recovery_position = _event_position(evidence, recovery.event_ids)
        if recovery_position < 0:
            return False
        return any(
            item.result == "failed"
            and item.command_kind == verification.command_kind
            and 0 <= _event_position(evidence, item.event_ids) < recovery_position
            for item in evidence.verification
        )

    def _specific_error(self, error: ErrorEvidence) -> bool:
        message = _normalize_text(error.message)
        if not message or message in _GENERIC_ERROR_MESSAGES:
            return False
        if error.error_type and _normalize_text(error.error_type) not in {"toolerror", "unknownerror"}:
            return True
        return bool(
            re.search(r"[/\\._:]|\b(?:denied|missing|mismatch|timeout|timed out|unavailable|accepted)\b", message)
            or len(message) >= 32
            or re.search(r"[\u4e00-\u9fff]{4,}", message)
        )

    def _environment_scoped_error(self, error: ErrorEvidence) -> bool:
        return _is_environment_scoped_error(error)

    def _error_statement(self, error: ErrorEvidence) -> str:
        prefix = " / ".join(
            item for item in (error.tool_name, error.error_type) if item
        )
        message = _salient_line(sanitize_evidence_text(error.message, 2_000))
        return (
            f"Observed error pattern for {prefix}: {message}"
            if prefix
            else f"Observed error pattern: {message}"
        )

    def _error_applies_when(self, error: ErrorEvidence) -> str:
        """State a condition a later run can actually test itself against.

        The previous form -- "When {tool} reports {error_type}." -- restated
        the observation instead of predicting anything, so every memory built
        on it answered "does this apply to me?" with "yes, if it applies".
        Naming the artifact and the underlying signal makes the question
        answerable.
        """
        source = sanitize_evidence_text(error.tool_name or "", 80).strip() or "the operation"
        artifacts, every_artifact = _named_artifacts(
            _salient_line(sanitize_evidence_text(error.message, 2_000))
        )
        signal = _failure_signal(error, every_artifact)
        if artifacts and signal:
            return f"When {source} fails on {' or '.join(artifacts)} with {signal}."
        if artifacts:
            return f"When {source} fails on {' or '.join(artifacts)}."
        if signal:
            return f"When {source} fails with {signal}."
        return f"When {source} fails the same way."


class ReflectionClaimValidator:
    """Validate candidate claims through indexed evidence provenance."""

    _APPLICABILITY_REQUIRED = {
        "error_pattern",
        "root_cause",
        "recovery",
        "decision",
        "verification_rule",
        "warning",
        "approach",
    }

    def validate(
        self,
        candidate: ReflectionCandidate,
        evidence: TaskEvidence,
    ) -> ClaimValidationResult:
        indexes = self._build_indexes(evidence)
        valid: list[ReflectionClaim] = []
        rejected: list[ReflectionClaim] = []
        issues: list[ClaimValidationIssue] = []

        for group in self._claim_groups(candidate.claims):
            merged, conflict_issue = self._merge_group(group)
            if conflict_issue is not None and conflict_issue.severity == "error":
                rejected.extend(group)
                issues.append(conflict_issue)
                continue
            if conflict_issue is not None:
                issues.append(conflict_issue)
            claim, redaction_issue = self._sanitize_claim(merged)
            if redaction_issue is not None:
                issues.append(redaction_issue)
            claim_issues = self._validate_claim(claim, indexes)
            if any(issue.severity == "error" for issue in claim_issues):
                rejected.append(claim)
            else:
                valid.append(claim)
            issues.extend(claim_issues)

        return ClaimValidationResult(valid_claims=valid, rejected_claims=rejected, issues=issues)

    def _claim_groups(self, claims: list[ReflectionClaim]) -> list[list[ReflectionClaim]]:
        groups: dict[str, list[ReflectionClaim]] = {}
        for claim in claims[:CLAIM_MAX_ITEMS]:
            groups.setdefault(_normalize_semantic_key(claim.semantic_key), []).append(claim)
        return list(groups.values())

    def _merge_group(
        self, group: list[ReflectionClaim]
    ) -> tuple[ReflectionClaim, ClaimValidationIssue | None]:
        first = group[0]
        signatures = {
            (
                claim.claim_type,
                _normalize_text(claim.statement),
                claim.epistemic_status,
                _normalize_text(claim.applies_when),
                tuple(_normalize_text(item) for item in claim.limitations),
            )
            for claim in group
        }
        if len(signatures) > 1:
            return first, ClaimValidationIssue(
                code="conflicting_semantic_key",
                message=f"Conflicting claims use semantic_key {first.semantic_key}.",
                claim_id=first.claim_id,
            )
        if len(group) == 1:
            return first, None
        return replace(
            first,
            evidence_ids=_ordered_unique(
                [event_id for claim in group for event_id in claim.evidence_ids]
            ),
            verification_ids=_ordered_unique(
                [item for claim in group for item in claim.verification_ids]
            ),
            related_error_ids=_ordered_unique(
                [item for claim in group for item in claim.related_error_ids]
            ),
            related_recovery_ids=_ordered_unique(
                [item for claim in group for item in claim.related_recovery_ids]
            ),
        ), ClaimValidationIssue(
            code="duplicate_semantic_key_merged",
            message=f"Merged duplicate semantic_key {first.semantic_key}.",
            claim_id=first.claim_id,
            severity="info",
        )

    def _sanitize_claim(
        self, claim: ReflectionClaim
    ) -> tuple[ReflectionClaim, ClaimValidationIssue | None]:
        sanitized_statement = sanitize_evidence_text(claim.statement, CLAIM_MAX_TEXT_CHARS)
        sanitized_applies = sanitize_evidence_text(claim.applies_when, CLAIM_MAX_AUX_TEXT_CHARS)
        sanitized_key = _normalize_semantic_key(claim.semantic_key)
        sanitized_limitations = [
            sanitize_evidence_text(item, CLAIM_MAX_AUX_TEXT_CHARS)
            for item in claim.limitations[:CLAIM_MAX_ITEMS]
        ]
        sanitized = replace(
            claim,
            semantic_key=sanitized_key,
            statement=sanitized_statement,
            evidence_ids=_ordered_unique(claim.evidence_ids),
            applies_when=sanitized_applies,
            limitations=sanitized_limitations,
            verification_ids=_ordered_unique(claim.verification_ids),
            related_error_ids=_ordered_unique(claim.related_error_ids),
            related_recovery_ids=_ordered_unique(claim.related_recovery_ids),
        )
        changed = (
            sanitized_statement != claim.statement
            or sanitized_applies != claim.applies_when
            or sanitized_limitations != claim.limitations
        )
        if not changed:
            return sanitized, None
        return sanitized, ClaimValidationIssue(
            code="claim_text_redacted_or_bounded",
            message="Claim text was redacted or length-bounded before validation.",
            claim_id=claim.claim_id,
            severity="warning",
        )

    def _build_indexes(self, evidence: TaskEvidence) -> dict[str, Any]:
        event_types: dict[str, set[str]] = defaultdict(set)
        decisions_by_event: dict[str, list[DecisionEvidence]] = defaultdict(list)
        libraries_by_event: dict[str, list[Any]] = defaultdict(list)
        errors_by_event: dict[str, list[ErrorEvidence]] = defaultdict(list)
        recoveries_by_event: dict[str, list[RecoveryEvidence]] = defaultdict(list)
        errors_by_id = {item.error_id: item for item in evidence.errors}
        recoveries_by_id = {item.recovery_id: item for item in evidence.recoveries}
        verification_by_id = {item.verification_id: item for item in evidence.verification}

        for item in evidence.files_read + evidence.files_changed + evidence.referenced_files:
            for event_id in item.event_ids:
                event_types[event_id].add("file")
        for item in evidence.libraries:
            for event_id in item.event_ids:
                event_types[event_id].add("library")
                libraries_by_event[event_id].append(item)
        for item in evidence.errors:
            for event_id in item.source_event_ids:
                event_types[event_id].add("error")
                errors_by_event[event_id].append(item)
        for item in evidence.recoveries:
            for event_id in item.event_ids:
                event_types[event_id].add("recovery")
                recoveries_by_event[event_id].append(item)
        for item in evidence.decisions:
            for event_id in item.event_ids:
                event_types[event_id].add("decision")
                decisions_by_event[event_id].append(item)
        for item in evidence.verification:
            for event_id in item.event_ids:
                event_types[event_id].add("verification")
        for event_id in evidence.final_summary_event_ids:
            event_types[event_id].add("assistant")
        return {
            "event_types": event_types,
            "decisions_by_event": decisions_by_event,
            "libraries_by_event": libraries_by_event,
            "errors_by_event": errors_by_event,
            "recoveries_by_event": recoveries_by_event,
            "errors_by_id": errors_by_id,
            "recoveries_by_id": recoveries_by_id,
            "verification_by_id": verification_by_id,
        }

    def _validate_claim(
        self, claim: ReflectionClaim, indexes: dict[str, Any]
    ) -> list[ClaimValidationIssue]:
        issues: list[ClaimValidationIssue] = []

        def reject(code: str, message: str) -> None:
            issues.append(ClaimValidationIssue(code, message, claim.claim_id))

        if not claim.evidence_ids:
            reject("missing_evidence_reference", "Claim has no evidence_ids.")
        missing_events = [
            event_id for event_id in claim.evidence_ids if event_id not in indexes["event_types"]
        ]
        if missing_events:
            reject("invalid_evidence_reference", f"Unknown evidence_ids: {missing_events}.")
        missing_verification = [
            item for item in claim.verification_ids if item not in indexes["verification_by_id"]
        ]
        if missing_verification:
            reject("invalid_verification_reference", f"Unknown verification_ids: {missing_verification}.")
        missing_errors = [
            item for item in claim.related_error_ids if item not in indexes["errors_by_id"]
        ]
        if missing_errors:
            reject("invalid_error_reference", f"Unknown related_error_ids: {missing_errors}.")
        missing_recoveries = [
            item for item in claim.related_recovery_ids if item not in indexes["recoveries_by_id"]
        ]
        if missing_recoveries:
            reject("invalid_recovery_reference", f"Unknown related_recovery_ids: {missing_recoveries}.")

        referenced_types = set().union(
            *(indexes["event_types"].get(event_id, set()) for event_id in claim.evidence_ids)
        ) if claim.evidence_ids else set()
        self._validate_groundedness(claim, referenced_types, indexes, reject)
        self._validate_statement_alignment(claim, indexes, reject)

        if claim.epistemic_status == "unknown":
            reject("unknown_epistemic_status", "Unknown claims cannot be persisted as facts.")
        source_statuses = self._source_statuses(claim, indexes)
        if claim.epistemic_status == "confirmed" and any(
            status != "confirmed" for status in source_statuses
        ):
            reject("epistemic_status_overclaim", "Claim certainty exceeds its source evidence.")

        normalized = _normalize_text(claim.statement)
        if not normalized or any(phrase in normalized for phrase in _GENERIC_CLAIMS):
            reject("generic_claim", "Claim is a generic execution summary, not reusable knowledge.")
        elif len(normalized) < 16:
            reject("insufficient_specificity", "Claim does not identify a specific object or action.")

        if claim.claim_type in self._APPLICABILITY_REQUIRED and not claim.applies_when.strip():
            reject("missing_applies_when", f"{claim.claim_type} requires applies_when.")
        if claim.epistemic_status == "inferred" and not claim.limitations:
            reject("missing_limitations", "Inferred claims require limitations.")
        if claim.claim_type in {"recovery", "root_cause"}:
            targeted = any(
                indexes["verification_by_id"].get(item)
                and indexes["verification_by_id"][item].scope == "targeted"
                for item in claim.verification_ids
            )
            if targeted and not claim.limitations:
                reject("missing_limitations", "Targeted verification requires limitations.")

        from minicode.memory import assess_memory_safety

        safety = assess_memory_safety(claim.statement, source="reflection_claim")
        if not safety.allowed:
            reject("unsafe_claim_text", "Claim text contains an unsafe future instruction.")
        return issues

    def _validate_groundedness(
        self,
        claim: ReflectionClaim,
        referenced_types: set[str],
        indexes: dict[str, Any],
        reject: Any,
    ) -> None:
        required: dict[str, set[str]] = {
            "constraint": {"decision"},
            "dependency": {"library"},
            "error_pattern": {"error"},
            "root_cause": {"error", "decision"},
            "recovery": {"error", "recovery"},
            "decision": {"decision"},
            "correction": {"decision"},
            "verification_rule": {"verification", "decision"},
            "warning": {"error"},
            "approach": {"verification", "file"},
        }
        missing = required[claim.claim_type] - referenced_types
        if missing:
            reject("claim_type_evidence_mismatch", f"{claim.claim_type} lacks evidence types {sorted(missing)}.")

        decisions = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["decisions_by_event"].get(event_id, [])
        ]
        if claim.claim_type == "constraint" and not any(
            item.source_kind in {"user_constraint", "config_constraint"}
            for item in decisions
        ):
            reject("constraint_not_stable", "Constraint lacks user or config provenance.")
        if claim.claim_type == "dependency":
            libraries = [
                item
                for event_id in claim.evidence_ids
                for item in indexes["libraries_by_event"].get(event_id, [])
            ]
            if not libraries or any(item.status != "confirmed" for item in libraries):
                reject("dependency_not_confirmed", "Dependency lacks confirmed LibraryEvidence.")
        if claim.claim_type == "correction" and not any(
            item.source_kind in {"user_correction", "old_memory_disproved"}
            for item in decisions
        ):
            reject("correction_not_explicit", "Correction lacks explicit correction provenance.")
        if claim.claim_type == "verification_rule" and not any(
            item.source_kind in {"user_constraint", "config_constraint"}
            for item in decisions
        ):
            reject("verification_rule_not_stable", "Verification rule lacks a stable rule source.")

        passed = [
            indexes["verification_by_id"].get(item) for item in claim.verification_ids
        ]
        has_passed = any(item and item.result == "passed" for item in passed)
        if claim.claim_type == "recovery" and claim.epistemic_status == "confirmed" and not has_passed:
            reject("confirmed_recovery_without_verification", "Confirmed recovery requires passed verification.")
        if claim.claim_type == "root_cause" and claim.epistemic_status == "confirmed":
            if not claim.related_error_ids or not claim.related_recovery_ids or not has_passed:
                reject("confirmed_root_cause_without_full_chain", "Confirmed root cause requires error, recovery, and passed verification.")

    def _validate_statement_alignment(
        self,
        claim: ReflectionClaim,
        indexes: dict[str, Any],
        reject: Any,
    ) -> None:
        """Prevent a valid evidence ID from endorsing unrelated claim text."""
        statement = _normalize_text(claim.statement)

        def source_text(value: str) -> str:
            return _normalize_text(sanitize_evidence_text(value, CLAIM_MAX_TEXT_CHARS))

        def quotes_source(value: str) -> bool:
            """Require the statement to quote the evidence, not all of it.

            Verbatim containment is what stops a claim asserting something the
            trace never said, and that property is kept: both accepted forms
            are contiguous excerpts of the message itself, derived from it
            deterministically. Demanding the *entire* message forced every
            statement to paste a whole tool run -- a progress bar, a banner
            and a source excerpt -- around the one line that carried the
            lesson.
            """
            full = source_text(value)
            if full and full in statement:
                return True
            salient = source_text(_salient_line(sanitize_evidence_text(value, 2_000)))
            return bool(salient) and salient in statement

        decisions = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["decisions_by_event"].get(event_id, [])
        ]
        libraries = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["libraries_by_event"].get(event_id, [])
        ]
        errors = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["errors_by_event"].get(event_id, [])
        ]
        recoveries = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["recoveries_by_event"].get(event_id, [])
        ]

        aligned = True
        if claim.claim_type in {"constraint", "decision", "correction", "verification_rule", "root_cause"}:
            aligned = any(
                source_text(item.statement) in statement for item in decisions
            )
        elif claim.claim_type == "dependency":
            names = {source_text(item.name) for item in libraries if item.status == "confirmed"}
            aligned = bool(names) and all(name in statement for name in names)
        elif claim.claim_type in {"error_pattern", "warning"}:
            aligned = any(quotes_source(item.message) for item in errors)
        elif claim.claim_type == "recovery":
            aligned = (
                any(source_text(item.action) in statement for item in recoveries)
                and any(quotes_source(item.message) for item in errors)
            )
        if not aligned:
            reject(
                "claim_statement_not_grounded",
                "Claim statement is not aligned with its referenced structured evidence.",
            )

    def _source_statuses(self, claim: ReflectionClaim, indexes: dict[str, Any]) -> list[str]:
        statuses: list[str] = []
        claim_statement = _normalize_text(claim.statement)
        for event_id in claim.evidence_ids:
            statuses.extend(
                item.epistemic_status
                for item in indexes["decisions_by_event"].get(event_id, [])
                if _normalize_text(
                    sanitize_evidence_text(item.statement, CLAIM_MAX_TEXT_CHARS)
                )
                in claim_statement
                or claim_statement
                in _normalize_text(
                    sanitize_evidence_text(item.statement, CLAIM_MAX_TEXT_CHARS)
                )
            )
            statuses.extend(
                item.epistemic_status
                for item in indexes["libraries_by_event"].get(event_id, [])
            )
        statuses.extend(
            indexes["errors_by_id"][item].epistemic_status
            for item in claim.related_error_ids
            if item in indexes["errors_by_id"]
        )
        statuses.extend(
            indexes["recoveries_by_id"][item].epistemic_status
            for item in claim.related_recovery_ids
            if item in indexes["recoveries_by_id"]
        )
        return statuses


class ReflectionValueGate:
    """Select validated reflections that contain durable reusable signals."""

    def evaluate(
        self,
        candidate: ReflectionCandidate,
        validation: ClaimValidationResult,
        evidence: TaskEvidence,
    ) -> ReflectionValueDecision:
        rejected_ids = [claim.claim_id for claim in validation.rejected_claims]
        if not validation.valid_claims:
            reasons = self._low_value_reasons(evidence)
            if candidate.claims and any(
                claim.claim_type == "root_cause" for claim in candidate.claims
            ):
                reasons.append("unsupported_root_cause")
            reasons.append("no_valid_claim")
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=_ordered_unique(reasons),
                rejected_claim_ids=rejected_ids,
            )

        global_errors = [
            issue
            for issue in validation.issues
            if issue.severity == "error" and issue.claim_id is None
        ]
        if global_errors:
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["global_validation_error"],
                rejected_claim_ids=rejected_ids,
            )

        signals: list[str] = []
        accepted_claim_ids: list[str] = []
        for claim in validation.valid_claims:
            claim_signals = self._signals_for_claim(claim)
            if claim_signals:
                signals.extend(claim_signals)
                accepted_claim_ids.append(claim.claim_id)

        if not signals:
            reasons = self._low_value_reasons(evidence)
            if any(
                claim.claim_type == "root_cause"
                and claim.epistemic_status != "confirmed"
                for claim in validation.valid_claims
            ):
                reasons.append("unsupported_root_cause")
            if candidate.outcome == "unknown":
                reasons.append("unknown_outcome_without_durable_fact")
            reasons.append("no_durable_signal")
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=_ordered_unique(reasons),
                rejected_claim_ids=rejected_ids,
            )

        accepted_types = {
            claim.claim_type
            for claim in validation.valid_claims
            if claim.claim_id in accepted_claim_ids
        }
        has_unverified_recovery = bool(evidence.recoveries) and any(
            recovery.epistemic_status != "confirmed"
            for recovery in evidence.recoveries
        )
        has_passed_verification = any(
            verification.result == "passed"
            for verification in evidence.verification
        )
        if (
            evidence.outcome in {"failed", "unknown"}
            and has_unverified_recovery
            and not has_passed_verification
            and accepted_types
            and accepted_types <= {"error_pattern", "warning"}
        ):
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["unverified_recovery_context"],
                rejected_claim_ids=rejected_ids,
            )

        has_verified_recovery = (
            has_passed_verification
            and any(
                recovery.epistemic_status == "confirmed"
                for recovery in evidence.recoveries
            )
        )

        # Checked before recurrence on purpose: retrying an unreachable
        # endpoint inside one trace satisfies _has_recurrent_error, so an
        # environment failure would otherwise ride a retry past the gate.
        if (
            accepted_types
            and accepted_types <= {"error_pattern", "warning"}
            and not has_verified_recovery
            and self._only_environment_scoped_errors(validation, accepted_claim_ids, evidence)
        ):
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["environment_scoped_error_pattern"],
                rejected_claim_ids=rejected_ids,
            )

        has_recurrent_error = self._has_recurrent_error(evidence)
        if (
            accepted_types
            and accepted_types <= {"error_pattern", "warning"}
            and not has_verified_recovery
            and not has_recurrent_error
        ):
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["single_observation_error_pattern"],
                rejected_claim_ids=rejected_ids,
            )

        return ReflectionValueDecision(
            accepted=True,
            reason_codes=["accepted_durable_reflection"],
            durable_signals=_ordered_unique(signals),
            accepted_claim_ids=accepted_claim_ids,
            rejected_claim_ids=rejected_ids,
        )

    def _only_environment_scoped_errors(
        self,
        validation: ClaimValidationResult,
        accepted_claim_ids: list[str],
        evidence: TaskEvidence,
    ) -> bool:
        """Report whether every failure behind the accepted claims is host state.

        Scoped to the errors the accepted claims actually cite, so a run that
        also hit a genuine project failure keeps its claim.
        """
        accepted = set(accepted_claim_ids)
        cited = {
            error_id
            for claim in validation.valid_claims
            if claim.claim_id in accepted
            for error_id in claim.related_error_ids
        }
        errors = [error for error in evidence.errors if error.error_id in cited] or list(
            evidence.errors
        )
        if not errors:
            return False
        return all(_is_environment_scoped_error(error) for error in errors)

    def _has_recurrent_error(self, evidence: TaskEvidence) -> bool:
        signatures: dict[tuple[str, str, str], set[str]] = {}
        for error in evidence.errors:
            signature = (
                _normalize_text(error.tool_name),
                _normalize_text(error.error_type),
                _normalize_text(error.message),
            )
            if not any(signature):
                continue
            occurrence_id = error.call_id or error.error_id
            signatures.setdefault(signature, set()).add(occurrence_id)
        return any(len(occurrences) >= 2 for occurrences in signatures.values())

    def _signals_for_claim(self, claim: ReflectionClaim) -> list[str]:
        if claim.claim_type == "constraint":
            return ["stable_project_constraint"]
        if claim.claim_type == "dependency" and claim.epistemic_status == "confirmed":
            return ["confirmed_dependency"]
        if claim.claim_type in {"error_pattern", "warning"}:
            return ["reusable_error_pattern"]
        if claim.claim_type == "root_cause" and claim.epistemic_status == "confirmed":
            return ["confirmed_error_recovery_verified"]
        if claim.claim_type == "recovery" and claim.epistemic_status == "confirmed":
            return ["confirmed_error_recovery_verified", "verified_solution"]
        if claim.claim_type == "decision":
            return ["key_technical_decision"]
        if claim.claim_type == "correction":
            signals = ["user_correction"]
            normalized = _normalize_text(f"{claim.semantic_key} {claim.statement}")
            if "memory" in normalized and any(word in normalized for word in ("wrong", "invalid", "stale")):
                signals.append("old_memory_disproved")
            return signals
        if claim.claim_type == "verification_rule":
            return ["stable_verification_rule"]
        if claim.claim_type == "approach" and claim.epistemic_status == "confirmed":
            return ["verified_approach"]
        return []

    def _low_value_reasons(self, evidence: TaskEvidence) -> list[str]:
        tools = {item.tool_name for item in evidence.tool_calls}
        reasons: list[str] = []
        if tools and tools <= _READ_TOOLS:
            reasons.append("routine_read_only")
        if tools and tools <= _SEARCH_TOOLS:
            reasons.append("routine_search_only")
        if tools and tools <= _LIST_TOOLS:
            reasons.append("routine_directory_listing")
        if tools and tools <= _FORMAT_TOOLS:
            reasons.append("routine_format_only")
        if evidence.verification and not evidence.errors and not evidence.recoveries:
            reasons.append("routine_verification_only")
        if (
            {item.name for item in evidence.libraries if item.status == "confirmed"}
            == {"ruff"}
            and evidence.files_changed
            and not any(
                (
                    evidence.files_read,
                    evidence.errors,
                    evidence.recoveries,
                    evidence.decisions,
                )
            )
        ):
            reasons.append("routine_format_only")
        if evidence.recovery_suggestions and not evidence.recoveries:
            reasons.append("recovery_suggestion_only")
        if evidence.libraries and not any(
            item.status == "confirmed" for item in evidence.libraries
        ):
            reasons.append("weak_dependency_mention")
        if evidence.outcome == "unknown":
            reasons.append("unknown_outcome_without_durable_fact")
        if evidence.outcome == "success" and not any(
            (
                evidence.errors,
                evidence.recoveries,
                evidence.decisions,
                evidence.libraries,
            )
        ):
            reasons.append("task_success_only")
        if tools and not any((evidence.errors, evidence.recoveries, evidence.decisions, evidence.libraries)):
            reasons.append("tool_count_only")
        if evidence.errors and not reasons:
            reasons.append("generic_error_summary")
        return reasons or ["no_durable_signal"]


__all__ = [
    "ClaimType",
    "ClaimValidationIssue",
    "ClaimValidationResult",
    "ReflectionCandidate",
    "ReflectionClaim",
    "ReflectionClaimValidator",
    "ReflectionSynthesizer",
    "ReflectionValueDecision",
    "ReflectionValueGate",
    "RuleReflectionSynthesizer",
]
