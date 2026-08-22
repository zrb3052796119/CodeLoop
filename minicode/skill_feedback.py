"""Conservative live-ranking adapter for Skill evidence.

The evidence ledger remains a correlation-only shadow report.  This module
grants a much narrower authority: exceptionally strong, independently
verified, user-confirmed evidence may adjust the ordering of an already
admitted Skill by at most 0.25 points.  It cannot create query relevance,
break router abstention, cross a Skill digest, or override an explicit request.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from minicode.logging_config import get_logger
from minicode.run_journal import RunJournal
from minicode.skill_evidence import SkillEvidenceLedger


_MIN_COHORT_RUNS = 20
_MIN_USER_SIGNALS = 3
_MIN_ABSOLUTE_DELTA = 0.15
_RANK_ADJUSTMENT = 0.25
_MAX_EVALUATIONS = 100
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$")
_SKILL_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCES = frozenset({"project", "user", "compat_project", "compat_user"})
_DEFAULT_CACHE_TTL_SECONDS = 60.0
_FAILURE_CACHE_TTL_SECONDS = 10.0
_MAX_CACHE_ENTRIES = 64

logger = get_logger("skill_feedback")

FeedbackKey = tuple[str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class SkillFeedbackDecision:
    adjustment: float
    status: str
    treatment_runs: int
    control_runs: int


class SkillRoutingFeedback:
    """Immutable, bounded decisions derived from one validated snapshot."""

    def __init__(
        self,
        decisions: Mapping[FeedbackKey, SkillFeedbackDecision] | None = None,
    ) -> None:
        self._decisions = dict(decisions or {})

    @property
    def decision_count(self) -> int:
        return len(self._decisions)

    def decision(
        self,
        *,
        qualified_name: str,
        source: str,
        directory: str,
        content_digest: str,
        intent_type: str,
        action_type: str,
    ) -> SkillFeedbackDecision | None:
        return self._decisions.get(
            (
                qualified_name,
                source,
                directory,
                content_digest,
                intent_type,
                action_type,
            )
        )

    @classmethod
    def from_snapshot(cls, snapshot: object) -> "SkillRoutingFeedback":
        if not _valid_snapshot_header(snapshot):
            return cls()
        assert isinstance(snapshot, Mapping)
        evaluations = snapshot.get("evaluations")
        scanned_runs = snapshot.get("scannedRuns")
        assert isinstance(evaluations, list)
        assert isinstance(scanned_runs, int) and not isinstance(scanned_runs, bool)
        decisions: dict[FeedbackKey, SkillFeedbackDecision] = {}
        for evaluation in evaluations:
            parsed = _parse_evaluation(evaluation, scanned_runs=scanned_runs)
            if parsed is None:
                continue
            key, decision = parsed
            if key in decisions:
                # Conflicting duplicate evidence is an invalid snapshot, not a
                # reason to let iteration order choose live routing behavior.
                return cls()
            decisions[key] = decision
        return cls(decisions)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    feedback: SkillRoutingFeedback


_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[str, _CacheEntry] = OrderedDict()


def build_skill_routing_feedback(
    workspace: str | Path,
    *,
    cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
) -> SkillRoutingFeedback:
    """Build the production adapter, cached and fail-closed.

    The environment switch is an operator rollback lever. Evidence ranking is
    enabled by default because the adapter itself enforces the strict gate and
    has no admission authority.
    """
    enabled = os.environ.get("MINICODE_SKILL_FEEDBACK", "1").strip().lower()
    if enabled in {"0", "false", "off", "disabled"}:
        return SkillRoutingFeedback()
    try:
        ttl = float(cache_ttl_seconds)
    except (TypeError, ValueError):
        ttl = _DEFAULT_CACHE_TTL_SECONDS
    if not math.isfinite(ttl) or ttl < 0:
        ttl = _DEFAULT_CACHE_TTL_SECONDS
    try:
        key = str(Path(workspace).expanduser().resolve())
    except Exception:  # noqa: BLE001 - invalid workspace disables feedback
        return SkillRoutingFeedback()

    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now < cached.expires_at:
            _CACHE.move_to_end(key)
            return cached.feedback

    failure = False
    try:
        snapshot = SkillEvidenceLedger(RunJournal(key)).snapshot()
        feedback = SkillRoutingFeedback.from_snapshot(snapshot)
    except Exception as exc:  # noqa: BLE001 - telemetry never breaks routing
        failure = True
        feedback = SkillRoutingFeedback()
        logger.warning(
            "Skill feedback disabled for this refresh (%s)",
            type(exc).__name__,
        )
    cache_for = min(ttl, _FAILURE_CACHE_TTL_SECONDS) if failure else ttl
    entry = _CacheEntry(expires_at=now + cache_for, feedback=feedback)
    with _CACHE_LOCK:
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return feedback


def clear_skill_feedback_cache() -> None:
    """Clear process-local projections (primarily for deterministic tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _valid_snapshot_header(snapshot: object) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    evaluations = snapshot.get("evaluations")
    diagnostics = snapshot.get("journalDiagnostics")
    scanned_runs = snapshot.get("scannedRuns")
    return (
        snapshot.get("ledgerVersion") == 1
        and snapshot.get("mode") == "shadow"
        and snapshot.get("runsTruncated") is False
        and snapshot.get("evaluationsTruncated") is False
        and snapshot.get("promotionEligible") is False
        and isinstance(diagnostics, int)
        and not isinstance(diagnostics, bool)
        and diagnostics == 0
        and isinstance(scanned_runs, int)
        and not isinstance(scanned_runs, bool)
        and 0 <= scanned_runs <= 200
        and isinstance(evaluations, list)
        and len(evaluations) <= _MAX_EVALUATIONS
    )


def _parse_evaluation(
    evaluation: object,
    *,
    scanned_runs: int,
) -> tuple[FeedbackKey, SkillFeedbackDecision] | None:
    if not isinstance(evaluation, Mapping):
        return None
    status = evaluation.get("shadowStatus")
    if status not in {"positive_signal", "negative_signal"}:
        return None
    if (
        evaluation.get("sampleGatePassed") is not True
        or evaluation.get("promotionEligible") is not False
    ):
        return None

    identity = _identity(evaluation.get("skill"))
    profile = _profile(evaluation.get("profile"))
    treatment = _cohort(evaluation.get("treatment"))
    control = _cohort(evaluation.get("control"))
    reported_delta = evaluation.get("goalAchievementDelta")
    if (
        identity is None
        or profile is None
        or treatment is None
        or control is None
        or isinstance(reported_delta, bool)
        or not isinstance(reported_delta, (int, float))
        or not math.isfinite(float(reported_delta))
        or treatment["runs"] < _MIN_COHORT_RUNS
        or control["runs"] < _MIN_COHORT_RUNS
        or treatment["runs"] + control["runs"] > scanned_runs
    ):
        return None

    delta = (
        treatment["achievements"] / treatment["runs"]
        - control["achievements"] / control["runs"]
    )
    if (
        round(delta, 4) != float(reported_delta)
        or abs(delta) < _MIN_ABSOLUTE_DELTA
    ):
        return None
    treatment_interval = treatment["interval"]
    control_interval = control["interval"]
    treatment_verification = treatment["verification"]
    treatment_signal = treatment["user_signal"]
    if status == "positive_signal":
        authorized = (
            delta > 0
            and treatment_interval[0] > control_interval[1]
            and treatment_verification["passed"] == treatment["runs"]
            and treatment_verification["failed"] == 0
            and treatment_signal["accepted"] >= _MIN_USER_SIGNALS
            and treatment_signal["corrected"] == 0
            and treatment_signal["rejected"] == 0
        )
        adjustment = _RANK_ADJUSTMENT
    else:
        authorized = (
            delta < 0
            and treatment_interval[1] < control_interval[0]
            and treatment_verification["failed"]
            >= max(1, math.ceil(treatment["runs"] * 0.25))
            and treatment_signal["accepted"] == 0
            and (
                treatment_signal["corrected"]
                + treatment_signal["rejected"]
                >= _MIN_USER_SIGNALS
            )
        )
        adjustment = -_RANK_ADJUSTMENT
    if not authorized:
        return None

    key: FeedbackKey = (*identity, *profile)
    return key, SkillFeedbackDecision(
        adjustment=adjustment,
        status=str(status),
        treatment_runs=treatment["runs"],
        control_runs=control["runs"],
    )


def _identity(value: object) -> tuple[str, str, str, str] | None:
    if not isinstance(value, Mapping):
        return None
    qualified_name = value.get("qualifiedName")
    source = value.get("source")
    directory = value.get("directory")
    digest = value.get("contentDigest")
    if (
        not isinstance(qualified_name, str)
        or _SKILL_NAME_RE.fullmatch(qualified_name) is None
        or source not in _SOURCES
        or not isinstance(directory, str)
        or (
            directory
            and _SKILL_DIRECTORY_RE.fullmatch(directory) is None
        )
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
    ):
        return None
    return qualified_name, str(source), directory, digest


def _profile(value: object) -> tuple[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    intent_type = value.get("intentType")
    action_type = value.get("actionType")
    if (
        not isinstance(intent_type, str)
        or not intent_type
        or not isinstance(action_type, str)
        or not action_type
    ):
        return None
    return intent_type, action_type


def _cohort(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    runs = _bounded_int(value.get("runs"), upper=200)
    achievements = _bounded_int(value.get("goalAchievements"), upper=200)
    interval = value.get("goalAchievementInterval")
    reported_rate = value.get("goalAchievementRate")
    verification = value.get("verification")
    user_signal = value.get("userSignal")
    if (
        runs is None
        or achievements is None
        or achievements > runs
        or not isinstance(interval, Mapping)
        or isinstance(reported_rate, bool)
        or not isinstance(reported_rate, (int, float))
        or not math.isfinite(float(reported_rate))
        or not isinstance(verification, Mapping)
        or not isinstance(user_signal, Mapping)
    ):
        return None
    lower = _probability(interval.get("lower"))
    upper = _probability(interval.get("upper"))
    expected_lower, expected_upper = _wilson_interval(achievements, runs)
    expected_rate = round(achievements / runs, 4) if runs else 0.0
    if (
        lower is None
        or upper is None
        or lower > upper
        or float(reported_rate) != expected_rate
        or lower != round(expected_lower, 4)
        or upper != round(expected_upper, 4)
    ):
        return None

    observed = _bounded_int(verification.get("observedRuns"), upper=runs)
    passed = _bounded_int(verification.get("passedRuns"), upper=runs)
    failed = _bounded_int(verification.get("failedRuns"), upper=runs)
    if (
        verification.get("coverageComplete") is not True
        or observed != runs
        or passed is None
        or failed is None
        or passed + failed != observed
    ):
        return None

    signal_observed = _bounded_int(user_signal.get("observedRuns"), upper=runs)
    accepted = _bounded_int(user_signal.get("acceptedRuns"), upper=runs)
    corrected = _bounded_int(user_signal.get("correctedRuns"), upper=runs)
    rejected = _bounded_int(user_signal.get("rejectedRuns"), upper=runs)
    if (
        signal_observed is None
        or accepted is None
        or corrected is None
        or rejected is None
        or accepted + corrected + rejected != signal_observed
    ):
        return None
    return {
        "runs": runs,
        "achievements": achievements,
        "interval": (expected_lower, expected_upper),
        "verification": {"passed": passed, "failed": failed},
        "user_signal": {
            "accepted": accepted,
            "corrected": corrected,
            "rejected": rejected,
        },
    }


def _bounded_int(value: object, *, upper: int) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > upper
    ):
        return None
    return value


def _probability(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        return None
    return float(value)


def _wilson_interval(successes: int, runs: int) -> tuple[float, float]:
    if runs == 0:
        return 0.0, 1.0
    z = 1.96
    rate = successes / runs
    denominator = 1 + (z * z / runs)
    center = (rate + z * z / (2 * runs)) / denominator
    margin = (
        z
        * math.sqrt(
            rate * (1 - rate) / runs + z * z / (4 * runs * runs)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


__all__ = [
    "SkillFeedbackDecision",
    "SkillRoutingFeedback",
    "build_skill_routing_feedback",
    "clear_skill_feedback_cache",
]
