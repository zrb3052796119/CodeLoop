"""Bounded, read-only cross-Run Skill evidence derived from RunJournal."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from minicode.intent_parser import ActionType, IntentType
from minicode.run_journal import RunEvent, RunJournal, RunRecord
from minicode.task_outcome_event import normalize_task_outcome_payload
from minicode.verification_observation import normalize_verification_payload


_MAX_RUNS = 200
_RUN_PAGE_SIZE = 100
_MAX_EVENTS_PER_RUN = 500
_EVENT_PAGE_SIZE = 100
_MAX_EVALUATIONS = 100
_MIN_COMPARABLE_RUNS = 5
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$")
_SKILL_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_OPERATION_ID_RE = re.compile(r"^modelop_[0-9a-f]{32}$")
_CATALOG_ID_RE = re.compile(
    r"^minicode-pricing-[a-z0-9][a-z0-9._-]{0,63}$"
)
_MODEL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_SKILL_SOURCES = frozenset(
    {"project", "user", "compat_project", "compat_user"}
)
_INTENT_TYPES = frozenset(item.value for item in IntentType)
_ACTION_TYPES = frozenset(item.value for item in ActionType)

SkillIdentity = tuple[str, str, str, str]
TaskProfile = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _Experience:
    profile: TaskProfile
    routed_skills: tuple[SkillIdentity, ...]
    loaded_skill: SkillIdentity | None
    outcome: dict[str, object]
    cost_nano_usd: int | None
    duration_ms: int | None
    verification: str | None
    user_signal: str | None


class SkillEvidenceLedger:
    """Hide Run paging, validation, joining, and statistics behind one snapshot."""

    def __init__(self, journal: RunJournal) -> None:
        self._journal = journal

    def snapshot(self) -> dict[str, object]:
        """Return a bounded aggregate that has no live-routing authority."""
        records, runs_truncated, journal_diagnostics = self._scan_runs()
        exclusions = {
            "nonCompleted": 0,
            "eventScanLimited": 0,
            "eventReadIncomplete": 0,
            "missingOrInvalidOutcome": 0,
            "nonBinaryOutcome": 0,
            "missingOrInvalidRouting": 0,
            "legacyRouting": 0,
            "ambiguousSkillUse": 0,
            "inconsistentSkillUse": 0,
        }
        treatment_runs = 0
        control_runs = 0
        grouped: dict[
            tuple[SkillIdentity, TaskProfile],
            dict[str, list[_Experience]],
        ] = {}

        for record in records:
            experience, exclusion, diagnostics = self._experience(record)
            journal_diagnostics += diagnostics
            if exclusion is not None:
                exclusions[exclusion] += 1
                continue
            if experience is None:
                continue
            if experience.loaded_skill is not None:
                treatment_runs += 1
                keys = (experience.loaded_skill,)
                cohort = "treatment"
            else:
                control_runs += 1
                keys = experience.routed_skills
                cohort = "control"
            for skill in keys:
                group = grouped.setdefault(
                    (skill, experience.profile),
                    {"treatment": [], "control": []},
                )
                group[cohort].append(experience)

        evaluations = [
            self._evaluation(skill, profile, cohorts)
            for (skill, profile), cohorts in sorted(grouped.items())
            if cohorts["treatment"]
        ]
        evaluations_truncated = len(evaluations) > _MAX_EVALUATIONS
        return {
            "ledgerVersion": 1,
            "mode": "shadow",
            "scannedRuns": len(records),
            "runsTruncated": runs_truncated,
            "eligibleTreatmentRuns": treatment_runs,
            "eligibleControlRuns": control_runs,
            "excludedRuns": exclusions,
            "journalDiagnostics": journal_diagnostics,
            "evaluations": evaluations[:_MAX_EVALUATIONS],
            "evaluationsTruncated": evaluations_truncated,
            "promotionEligible": False,
        }

    def _scan_runs(self) -> tuple[list[RunRecord], bool, int]:
        records: list[RunRecord] = []
        cursor: str | None = None
        diagnostics = 0
        has_more = False
        while len(records) < _MAX_RUNS:
            page = self._journal.list_runs(
                limit=min(_RUN_PAGE_SIZE, _MAX_RUNS - len(records)),
                cursor=cursor,
            )
            records.extend(page.items)
            diagnostics += len(page.diagnostics)
            has_more = page.has_more
            if not page.has_more or page.next_cursor is None:
                break
            cursor = page.next_cursor
        return records, has_more, diagnostics

    def _read_events(
        self,
        record: RunRecord,
    ) -> tuple[list[RunEvent] | None, int]:
        if record.event_count > _MAX_EVENTS_PER_RUN:
            return None, 0
        events: list[RunEvent] = []
        cursor: str | None = None
        diagnostics = 0
        while len(events) < _MAX_EVENTS_PER_RUN:
            page = self._journal.list_events(
                record.id,
                limit=min(
                    _EVENT_PAGE_SIZE,
                    _MAX_EVENTS_PER_RUN - len(events),
                ),
                cursor=cursor,
            )
            events.extend(page.items)
            diagnostics += len(page.diagnostics)
            if not page.has_more or page.next_cursor is None:
                return events, diagnostics
            cursor = page.next_cursor
        return None, diagnostics

    def _experience(
        self,
        record: RunRecord,
    ) -> tuple[_Experience | None, str | None, int]:
        if record.status != "completed":
            return None, "nonCompleted", 0
        try:
            events, diagnostics = self._read_events(record)
        except Exception:  # noqa: BLE001 - one Run cannot erase the ledger
            return None, "eventReadIncomplete", 0
        if events is None:
            return None, "eventScanLimited", diagnostics
        if diagnostics:
            return None, "eventReadIncomplete", diagnostics

        outcome_events = [
            event for event in events if event.type == "task.outcome"
        ]
        if len(outcome_events) != 1:
            return None, "missingOrInvalidOutcome", diagnostics
        outcome_event = outcome_events[0]
        outcome = normalize_task_outcome_payload(outcome_event.payload)
        if outcome is None:
            return None, "missingOrInvalidOutcome", diagnostics
        if outcome["outcomeStatus"] not in {"success", "failed"}:
            return None, "nonBinaryOutcome", diagnostics

        routing_events = [
            event for event in events if event.type == "skill.routed"
        ]
        if len(routing_events) != 1:
            return None, "missingOrInvalidRouting", diagnostics
        routing_event = routing_events[0]
        routing, legacy = _normalize_routing(routing_event.payload)
        if legacy:
            return None, "legacyRouting", diagnostics
        if routing is None:
            return None, "missingOrInvalidRouting", diagnostics
        profile, routed_skills = routing
        if not routed_skills:
            return None, "missingOrInvalidRouting", diagnostics
        if routing_event.sequence >= outcome_event.sequence:
            return None, "inconsistentSkillUse", diagnostics

        verification = _run_verification(
            events,
            after_sequence=routing_event.sequence,
            before_sequence=outcome_event.sequence,
        )
        try:
            stored_user_signal = self._journal.get_user_signal(record.id)
        except Exception:  # noqa: BLE001 - one sidecar cannot erase Run evidence
            stored_user_signal = None
        user_signal = (
            stored_user_signal.signal
            if stored_user_signal is not None
            and stored_user_signal.source == "explicit_user_action"
            and stored_user_signal.signal in {"accept", "correct", "reject"}
            else None
        )
        loaded_events = [
            event for event in events if event.type == "skill.loaded"
        ]
        cost_nano_usd, duration_ms = _run_economics(events)
        loaded_identities: set[SkillIdentity] = set()
        for event in loaded_events:
            identity = _normalize_loaded(event.payload)
            if identity is None:
                return None, "inconsistentSkillUse", diagnostics
            loaded_identities.add(identity)

        attribution_events = [
            event for event in events if event.type == "skill.attributed"
        ]
        load_attempted = any(
            event.type == "tool.started"
            and event.payload.get("toolName") == "load_skill"
            for event in events
        )
        if not loaded_identities:
            if attribution_events or load_attempted:
                return None, "inconsistentSkillUse", diagnostics
            return (
                _Experience(
                    profile=profile,
                    routed_skills=routed_skills,
                    loaded_skill=None,
                    outcome=outcome,
                    cost_nano_usd=cost_nano_usd,
                    duration_ms=duration_ms,
                    verification=verification,
                    user_signal=user_signal,
                ),
                None,
                diagnostics,
            )
        if len(loaded_identities) != 1:
            return None, "ambiguousSkillUse", diagnostics
        if len(attribution_events) != 1:
            return None, "inconsistentSkillUse", diagnostics
        attribution_event = attribution_events[0]
        if (
            any(
                not (
                    routing_event.sequence
                    < event.sequence
                    < outcome_event.sequence
                )
                for event in loaded_events
            )
            or attribution_event.sequence <= outcome_event.sequence
        ):
            return None, "inconsistentSkillUse", diagnostics
        attributed = _normalize_attribution(
            attribution_event.payload,
            outcome,
        )
        if attributed is None or set(attributed) != loaded_identities:
            return None, "inconsistentSkillUse", diagnostics
        loaded_skill = next(iter(loaded_identities))
        if loaded_skill not in routed_skills:
            return None, "inconsistentSkillUse", diagnostics
        return (
            _Experience(
                profile=profile,
                routed_skills=routed_skills,
                loaded_skill=loaded_skill,
                outcome=outcome,
                cost_nano_usd=cost_nano_usd,
                duration_ms=duration_ms,
                verification=verification,
                user_signal=user_signal,
            ),
            None,
            diagnostics,
        )

    @staticmethod
    def _evaluation(
        skill: SkillIdentity,
        profile: TaskProfile,
        cohorts: dict[str, list[_Experience]],
    ) -> dict[str, object]:
        treatment = _cohort(cohorts["treatment"])
        control = _cohort(cohorts["control"])
        sample_gate = (
            treatment["runs"] >= _MIN_COMPARABLE_RUNS
            and control["runs"] >= _MIN_COMPARABLE_RUNS
        )
        if not sample_gate:
            shadow_status = "insufficient_evidence"
        elif (
            treatment["goalAchievementInterval"]["lower"]
            > control["goalAchievementInterval"]["upper"]
        ):
            shadow_status = "positive_signal"
        elif (
            treatment["goalAchievementInterval"]["upper"]
            < control["goalAchievementInterval"]["lower"]
        ):
            shadow_status = "negative_signal"
        else:
            shadow_status = "inconclusive"
        delta = (
            round(
                treatment["goalAchievementRate"]
                - control["goalAchievementRate"],
                4,
            )
            if treatment["runs"] > 0 and control["runs"] > 0
            else None
        )
        return {
            "skill": _skill_dict(skill),
            "profile": {
                "intentType": profile[0],
                "actionType": profile[1],
            },
            "treatment": treatment,
            "control": control,
            "goalAchievementDelta": delta,
            "sampleGatePassed": sample_gate,
            "shadowStatus": shadow_status,
            "promotionEligible": False,
        }


def _normalize_routing(
    payload: dict[str, object],
) -> tuple[tuple[TaskProfile, tuple[SkillIdentity, ...]] | None, bool]:
    version = payload.get("routingVersion")
    if version == 1:
        return None, True
    intent_type = payload.get("intentType")
    action_type = payload.get("actionType")
    selected_count = payload.get("selectedCount")
    selected = payload.get("selected")
    selected_truncated = payload.get("selectedTruncated")
    if (
        version != 2
        or intent_type not in _INTENT_TYPES
        or action_type not in _ACTION_TYPES
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or not 1 <= selected_count <= 20
        or not isinstance(selected, list)
        or len(selected) != selected_count
        or selected_truncated is not False
    ):
        return None, False
    identities: list[SkillIdentity] = []
    for item in selected:
        if not isinstance(item, dict):
            return None, False
        identity = _skill_identity(item)
        score = item.get("score")
        if (
            identity is None
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            return None, False
        identities.append(identity)
    if len(set(identities)) != len(identities):
        return None, False
    return ((intent_type, action_type), tuple(identities)), False


def _normalize_loaded(payload: dict[str, object]) -> SkillIdentity | None:
    if payload.get("loadVersion") != 1:
        return None
    return _skill_identity(payload)


def _normalize_attribution(
    payload: dict[str, object],
    outcome: dict[str, object],
) -> tuple[SkillIdentity, ...] | None:
    loaded_count = payload.get("loadedSkillCount")
    loaded_skills = payload.get("loadedSkills")
    if (
        payload.get("attributionVersion") != 1
        or payload.get("attributionKind") != "task_correlation"
        or payload.get("outcomeStatus") != outcome["outcomeStatus"]
        or payload.get("goalAchieved") is not outcome["goalAchieved"]
        or payload.get("hadToolErrors") is not outcome["hadToolErrors"]
        or payload.get("errorsRecovered") is not outcome["errorsRecovered"]
        or payload.get("toolErrorCount") != outcome["toolErrorCount"]
        or isinstance(loaded_count, bool)
        or not isinstance(loaded_count, int)
        or loaded_count != 1
        or not isinstance(loaded_skills, list)
        or len(loaded_skills) != loaded_count
        or payload.get("loadedSkillsTruncated") is not False
    ):
        return None
    identities = tuple(
        identity
        for item in loaded_skills
        if isinstance(item, dict)
        for identity in [_skill_identity(item)]
        if identity is not None
    )
    return identities if len(identities) == loaded_count else None


def _skill_identity(payload: dict[str, object]) -> SkillIdentity | None:
    qualified_name = payload.get("qualifiedName")
    source = payload.get("source")
    directory = payload.get("directory")
    content_digest = payload.get("contentDigest")
    if (
        not isinstance(qualified_name, str)
        or _SKILL_NAME_RE.fullmatch(qualified_name) is None
        or source not in _SKILL_SOURCES
        or not isinstance(directory, str)
        or (
            directory
            and _SKILL_DIRECTORY_RE.fullmatch(directory) is None
        )
        or not isinstance(content_digest, str)
        or _SHA256_RE.fullmatch(content_digest) is None
    ):
        return None
    return qualified_name, source, directory, content_digest


def _skill_dict(identity: SkillIdentity) -> dict[str, str]:
    return {
        "qualifiedName": identity[0],
        "source": identity[1],
        "directory": identity[2],
        "contentDigest": identity[3],
    }


def _run_economics(events: list[RunEvent]) -> tuple[int | None, int | None]:
    """Return exact per-Run priced cost and observed Model duration."""
    started: set[str] = set()
    terminal: dict[str, tuple[str, int | None, str | None]] = {}
    costs: dict[str, int] = {}
    cost_seen: set[str] = set()
    model_invalid = False
    cost_invalid = False
    for event in events:
        if event.type not in {
            "model.started",
            "model.completed",
            "model.failed",
            "model.costed",
        }:
            continue
        payload = event.payload
        operation_id = payload.get("operationId")
        if (
            not isinstance(operation_id, str)
            or _MODEL_OPERATION_ID_RE.fullmatch(operation_id) is None
        ):
            if event.type == "model.costed":
                cost_invalid = True
            else:
                model_invalid = True
            continue
        if event.type == "model.started":
            if operation_id in started or operation_id in terminal:
                model_invalid = True
            else:
                started.add(operation_id)
            continue
        if event.type in {"model.completed", "model.failed"}:
            if operation_id not in started or operation_id in terminal:
                model_invalid = True
                continue
            duration = payload.get("durationMs")
            safe_duration = (
                duration
                if isinstance(duration, int)
                and not isinstance(duration, bool)
                and 0 <= duration <= 86_400_000
                else None
            )
            usage_source = None
            if event.type == "model.completed":
                usage = payload.get("usage")
                if isinstance(usage, Mapping) and usage.get("source") in {
                    "provider",
                    "estimated",
                    "unavailable",
                }:
                    usage_source = str(usage["source"])
            terminal[operation_id] = (
                event.type,
                safe_duration,
                usage_source,
            )
            continue

        terminal_fact = terminal.get(operation_id)
        if operation_id in cost_seen:
            cost_invalid = True
            continue
        cost_seen.add(operation_id)
        amount = _priced_cost(payload, terminal_fact)
        if amount is None:
            if not _unavailable_cost(payload, terminal_fact):
                cost_invalid = True
            continue
        costs[operation_id] = amount

    if not started or model_invalid or set(terminal) != started:
        return None, None
    durations = [fact[1] for fact in terminal.values()]
    duration_total = (
        sum(value for value in durations if value is not None)
        if all(value is not None for value in durations)
        else None
    )
    completed_ids = {
        operation_id
        for operation_id, fact in terminal.items()
        if fact[0] == "model.completed"
    }
    failed = any(fact[0] == "model.failed" for fact in terminal.values())
    cost_total = (
        sum(costs.values())
        if (
            completed_ids
            and not failed
            and not cost_invalid
            and cost_seen == completed_ids
            and set(costs) == completed_ids
        )
        else None
    )
    return cost_total, duration_total


def _run_verification(
    events: list[RunEvent],
    *,
    after_sequence: int,
    before_sequence: int,
) -> str | None:
    observations = [
        event for event in events if event.type == "task.verified"
    ]
    if not observations:
        return None
    normalized = [
        normalize_verification_payload(event.payload)
        for event in observations
        if after_sequence < event.sequence < before_sequence
    ]
    if len(normalized) != len(observations) or any(
        item is None for item in normalized
    ):
        return None
    return (
        "failed"
        if any(item["outcome"] == "failed" for item in normalized if item)
        else "passed"
    )


def _priced_cost(
    payload: Mapping[str, object],
    terminal_fact: tuple[str, int | None, str | None] | None,
) -> int | None:
    components = payload.get("components")
    amount = payload.get("amountNanoUsd")
    quality = payload.get("quality")
    expected_quality = (
        "provider_usage_catalog_rate"
        if terminal_fact is not None and terminal_fact[2] == "provider"
        else "estimated_usage_catalog_rate"
        if terminal_fact is not None and terminal_fact[2] == "estimated"
        else None
    )
    component_names = {
        "inputNanoUsd",
        "outputNanoUsd",
        "cacheReadNanoUsd",
        "cacheCreationNanoUsd",
    }
    if (
        terminal_fact is None
        or terminal_fact[0] != "model.completed"
        or set(payload)
        != {
            "costVersion",
            "operationId",
            "status",
            "quality",
            "currency",
            "catalogId",
            "catalogModelKey",
            "amountNanoUsd",
            "components",
        }
        or payload.get("costVersion") != 1
        or payload.get("status") != "priced"
        or quality != expected_quality
        or payload.get("currency") != "USD"
        or not isinstance(payload.get("catalogId"), str)
        or _CATALOG_ID_RE.fullmatch(str(payload["catalogId"])) is None
        or not isinstance(payload.get("catalogModelKey"), str)
        or _MODEL_KEY_RE.fullmatch(str(payload["catalogModelKey"])) is None
        or isinstance(amount, bool)
        or not isinstance(amount, int)
        or not 0 <= amount <= 1_000_000_000_000_000_000
        or not isinstance(components, Mapping)
        or set(components) != component_names
    ):
        return None
    values = list(components.values())
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 1_000_000_000_000_000_000
        for value in values
    ):
        return None
    return amount if sum(values) == amount else None


def _unavailable_cost(
    payload: Mapping[str, object],
    terminal_fact: tuple[str, int | None, str | None] | None,
) -> bool:
    return (
        terminal_fact is not None
        and terminal_fact[0] == "model.completed"
        and set(payload)
        == {
            "costVersion",
            "operationId",
            "status",
            "quality",
            "currency",
            "catalogId",
            "reason",
        }
        and payload.get("costVersion") == 1
        and payload.get("status") == "unavailable"
        and payload.get("quality") == "unavailable"
        and payload.get("currency") == "USD"
        and isinstance(payload.get("catalogId"), str)
        and _CATALOG_ID_RE.fullmatch(str(payload["catalogId"])) is not None
        and payload.get("reason")
        in {
            "usage_unavailable",
            "model_unpriced",
            "pricing_incomplete",
            "token_semantics_unsupported",
            "invalid_usage",
            "pricing_failed",
        }
    )


def _cohort(experiences: list[_Experience]) -> dict[str, object]:
    runs = len(experiences)
    outcomes = [item.outcome for item in experiences]
    successes = sum(item["goalAchieved"] is True for item in outcomes)
    lower, upper = _wilson_interval(successes, runs)
    observed_costs = [
        item.cost_nano_usd
        for item in experiences
        if item.cost_nano_usd is not None
    ]
    observed_durations = [
        item.duration_ms
        for item in experiences
        if item.duration_ms is not None
    ]
    observed_verifications = [
        item.verification
        for item in experiences
        if item.verification is not None
    ]
    observed_user_signals = [
        item.user_signal
        for item in experiences
        if item.user_signal is not None
    ]
    return {
        "runs": runs,
        "goalAchievements": successes,
        "goalAchievementRate": round(successes / runs, 4) if runs else 0.0,
        "goalAchievementInterval": {
            "lower": round(lower, 4),
            "upper": round(upper, 4),
        },
        "toolErrorRuns": sum(
            item["hadToolErrors"] is True for item in outcomes
        ),
        "recoveredErrorRuns": sum(
            item["errorsRecovered"] is True for item in outcomes
        ),
        "cost": {
            "observedRuns": len(observed_costs),
            "totalNanoUsd": (
                str(sum(observed_costs)) if observed_costs else None
            ),
            "coverageComplete": bool(runs)
            and len(observed_costs) == runs,
        },
        "latency": {
            "observedRuns": len(observed_durations),
            "totalDurationMs": (
                sum(observed_durations) if observed_durations else None
            ),
            "coverageComplete": bool(runs)
            and len(observed_durations) == runs,
        },
        "verification": {
            "observedRuns": len(observed_verifications),
            "passedRuns": observed_verifications.count("passed"),
            "failedRuns": observed_verifications.count("failed"),
            "coverageComplete": bool(runs)
            and len(observed_verifications) == runs,
        },
        "userSignal": {
            "observedRuns": len(observed_user_signals),
            "acceptedRuns": observed_user_signals.count("accept"),
            "correctedRuns": observed_user_signals.count("correct"),
            "rejectedRuns": observed_user_signals.count("reject"),
            "coverageComplete": bool(runs)
            and len(observed_user_signals) == runs,
        },
    }


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


__all__ = ["SkillEvidenceLedger"]
