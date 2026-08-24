"""Deterministic quality evaluators for MiniCode's promotion gates.

The public interface returns JSON-serializable reports and performs no writes
or remote calls. CLI presentation, artifact comparison, and CI exit policy are
kept outside this module so tests and callers exercise the same seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from minicode.capability_registry import CapabilityRegistry
from minicode.context_compactor import AutoCompactConfig, AutoCompactDispatcher
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter, required_skill_names_for_routing


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _load_document(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("quality dataset must be a JSON object")
    return document


def _sha256(path: Path) -> str:
    # Quality fixtures are text contracts. Git may expose their line endings
    # differently on Windows, but that checkout representation must not turn
    # an otherwise identical frozen dataset into a failed promotion gate.
    canonical = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def evaluate_skill_routing(dataset_path: str | Path) -> dict[str, object]:
    """Evaluate the production Skill router against one frozen offline set.

    The dataset owns a shared Skill catalog and per-prompt expectations. The
    evaluator intentionally creates no embedding matcher, making the result
    deterministic and safe for pull-request CI.
    """
    document = _load_document(dataset_path)
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported Skill routing dataset schema")
    catalog = document.get("catalog")
    cases = document.get("cases")
    if not isinstance(catalog, list) or not isinstance(cases, list) or not cases:
        raise ValueError("Skill routing dataset requires catalog and non-empty cases")

    normalized_catalog: list[dict[str, object]] = []
    for index, raw in enumerate(catalog):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise TypeError(f"invalid Skill catalog entry at index {index}")
        skill = dict(raw)
        skill.setdefault("qualified_name", skill["name"])
        skill.setdefault("description", "")
        skill.setdefault("source", "project")
        skill.setdefault("path", f"/quality-fixture/{skill['name']}/SKILL.md")
        normalized_catalog.append(skill)

    router = SkillRouter()
    registry = CapabilityRegistry()
    positive_count = 0
    positive_correct = 0
    abstain_count = 0
    abstain_correct = 0
    required_correct = 0
    forbidden_violations = 0
    failed_case_ids: list[str] = []
    case_results: list[dict[str, object]] = []

    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            raise TypeError(f"invalid Skill routing case at index {index}")
        case_id = raw_case.get("id")
        prompt = raw_case.get("prompt")
        if not isinstance(case_id, str) or not case_id or not isinstance(prompt, str):
            raise ValueError(f"invalid Skill routing case identity at index {index}")
        expected_top1 = raw_case.get("expectedTop1")
        if expected_top1 is not None and not isinstance(expected_top1, str):
            raise ValueError(f"invalid expectedTop1 for case {case_id}")
        expected_required = raw_case.get("expectedRequired", [])
        forbidden = raw_case.get("forbidden", [])
        if (
            not isinstance(expected_required, list)
            or not all(isinstance(item, str) for item in expected_required)
            or not isinstance(forbidden, list)
            or not all(isinstance(item, str) for item in forbidden)
        ):
            raise ValueError(f"invalid expected lists for case {case_id}")

        routing = router.route(
            normalized_catalog,
            parse_intent(prompt),
            registry,
            top_k=max(1, int(document.get("topK", 5))),
        )
        selected = [
            item.qualified_name or item.name
            for item in routing.selected
        ]
        actual_top1 = selected[0] if selected else None
        if expected_top1 is None:
            abstain_count += 1
            top1_matches = actual_top1 is None
            abstain_correct += int(top1_matches)
        else:
            positive_count += 1
            top1_matches = actual_top1 == expected_top1
            positive_correct += int(top1_matches)

        actual_required = required_skill_names_for_routing(routing)
        required_matches = actual_required == expected_required
        required_correct += int(required_matches)
        forbidden_selected = sorted(set(selected).intersection(forbidden))
        forbidden_violations += int(bool(forbidden_selected))
        passed = top1_matches and required_matches and not forbidden_selected
        if not passed:
            failed_case_ids.append(case_id)
        case_results.append(
            {
                "id": case_id,
                "passed": passed,
                "expectedTop1": expected_top1,
                "actualTop1": actual_top1,
                "expectedRequired": list(expected_required),
                "actualRequired": actual_required,
                "forbiddenSelected": forbidden_selected,
            }
        )

    return {
        "schemaVersion": 1,
        "evaluator": "skill-routing",
        "caseCount": len(cases),
        "positiveCount": positive_count,
        "abstainCount": abstain_count,
        "top1Accuracy": _ratio(positive_correct, positive_count),
        "abstainAccuracy": _ratio(abstain_correct, abstain_count),
        "requiredExactMatchRate": _ratio(required_correct, len(cases)),
        "forbiddenSelectionRate": _ratio(forbidden_violations, len(cases)),
        "failedCaseIds": failed_case_ids,
        "cases": case_results,
    }


_QUALITY_MARKER_RE = re.compile(r"\bQG_[A-Z0-9_]+\b")


class _MarkerReplaySummarizer:
    """Deterministic replay adapter preserving sentinels it actually sees."""

    def __init__(self) -> None:
        self.calls = 0
        self.chain_observations = 0

    def summarize(self, messages: list[dict[str, object]]) -> str:
        self.calls += 1
        if self.calls > 1 and any(
            message.get("_previous_compact_summary") for message in messages
        ):
            self.chain_observations += 1
        markers: list[str] = []
        for message in messages:
            for marker in _QUALITY_MARKER_RE.findall(str(message.get("content", ""))):
                if marker not in markers:
                    markers.append(marker)
        rendered = " ".join(markers) or "no quality sentinels in dropped transcript"
        return f"Deterministic replay summary. Preserved: {rendered}"


def _compaction_round_messages(
    *,
    case_id: str,
    round_index: int,
    markers: list[str],
    latest_user_marker: str,
    loaded_skill_marker: str,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    if round_index == 0:
        messages.extend(
            [
                {
                    "role": "assistant_tool_call",
                    "toolUseId": f"quality-skill-{case_id}",
                    "toolName": "load_skill",
                    "input": {"name": "quality-contract"},
                },
                {
                    "role": "tool_result",
                    "toolUseId": f"quality-skill-{case_id}",
                    "toolName": "load_skill",
                    "content": f"SKILL: quality-contract\n{loaded_skill_marker}\n" + "rule " * 300,
                    "isError": False,
                },
            ]
        )
        for index, marker in enumerate(markers):
            messages.append(
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": marker + " " + "history " * 300,
                }
            )
    messages.extend(
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"round-{round_index}-message-{index} " + "filler " * 250,
        }
        for index in range(48)
    )
    messages.append({"role": "user", "content": latest_user_marker})
    return messages


def _tool_pairs_intact(messages: list[dict[str, object]]) -> bool:
    call_ids = Counter(
        str(message.get("toolUseId"))
        for message in messages
        if message.get("role") == "assistant_tool_call" and message.get("toolUseId")
    )
    result_ids = Counter(
        str(message.get("toolUseId"))
        for message in messages
        if message.get("role") == "tool_result" and message.get("toolUseId")
    )
    return call_ids == result_ids


def evaluate_compaction_fidelity(dataset_path: str | Path) -> dict[str, object]:
    """Force repeated production compaction and measure continuity invariants."""
    document = _load_document(dataset_path)
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported compaction fidelity dataset schema")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("compaction fidelity dataset requires non-empty cases")

    marker_total = 0
    marker_hits = 0
    latest_user_hits = 0
    skill_hits = 0
    chain_hits = 0
    pair_hits = 0
    savings_hits = 0
    ledger_cases = 0
    ledger_hits = 0
    failed_case_ids: list[str] = []
    case_results: list[dict[str, object]] = []

    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            raise TypeError(f"invalid compaction case at index {index}")
        case_id = raw_case.get("id")
        rounds = raw_case.get("rounds", 2)
        markers = raw_case.get("historyMarkers")
        latest_user_marker = raw_case.get("latestUserMarker")
        loaded_skill_marker = raw_case.get("loadedSkillMarker")
        task_ledger_markers = raw_case.get("taskLedgerMarkers", [])
        if (
            not isinstance(case_id, str)
            or not case_id
            or isinstance(rounds, bool)
            or not isinstance(rounds, int)
            or rounds < 1
            or rounds > 5
            or not isinstance(markers, list)
            or not markers
            or not all(isinstance(marker, str) and _QUALITY_MARKER_RE.fullmatch(marker) for marker in markers)
            or not isinstance(latest_user_marker, str)
            or _QUALITY_MARKER_RE.fullmatch(latest_user_marker) is None
            or not isinstance(loaded_skill_marker, str)
            or _QUALITY_MARKER_RE.fullmatch(loaded_skill_marker) is None
            or not isinstance(task_ledger_markers, list)
            or not all(
                isinstance(marker, str)
                and _QUALITY_MARKER_RE.fullmatch(marker)
                for marker in task_ledger_markers
            )
        ):
            raise ValueError(f"invalid compaction fidelity case {case_id or index}")

        summarizer = _MarkerReplaySummarizer()
        dispatcher = AutoCompactDispatcher(
            context_window=100_000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
            summary_generator=summarizer,
        )
        current: list[dict[str, object]] = [
            {"role": "system", "content": "Offline quality-gate fixture."}
        ]
        if task_ledger_markers:
            ledger_cases += 1
            current.append(
                {
                    "role": "system",
                    "content": " ".join(task_ledger_markers),
                    "_task_ledger": True,
                }
            )
        round_results = []
        for round_index in range(rounds):
            current.extend(
                _compaction_round_messages(
                    case_id=case_id,
                    round_index=round_index,
                    markers=list(markers),
                    latest_user_marker=latest_user_marker,
                    loaded_skill_marker=loaded_skill_marker,
                )
            )
            result = dispatcher.dispatch(current, force_full=True)
            round_results.append(result)
            current = list(result.messages)

        rendered = "\n".join(str(message.get("content", "")) for message in current)
        case_marker_hits = sum(marker in rendered for marker in markers)
        marker_total += len(markers)
        marker_hits += case_marker_hits
        latest_retained = any(
            message.get("role") == "user"
            and message.get("content") == latest_user_marker
            for message in current
        )
        skill_retained = any(
            message.get("role") == "tool_result"
            and message.get("toolName") == "load_skill"
            and loaded_skill_marker in str(message.get("content", ""))
            for message in current
        )
        chain_retained = rounds == 1 or summarizer.chain_observations >= rounds - 1
        pairs_intact = _tool_pairs_intact(current)
        task_ledger_retained = not task_ledger_markers or any(
            message.get("role") == "system"
            and message.get("_task_ledger") is True
            and all(
                marker in str(message.get("content", ""))
                for marker in task_ledger_markers
            )
            for message in current
        )
        nonnegative_savings = all(
            result.effective and result.tokens_freed >= 0
            for result in round_results
        )
        latest_user_hits += int(latest_retained)
        skill_hits += int(skill_retained)
        chain_hits += int(chain_retained)
        pair_hits += int(pairs_intact)
        savings_hits += int(nonnegative_savings)
        if task_ledger_markers:
            ledger_hits += int(task_ledger_retained)
        passed = all(
            (
                case_marker_hits == len(markers),
                latest_retained,
                skill_retained,
                chain_retained,
                pairs_intact,
                nonnegative_savings,
                task_ledger_retained,
            )
        )
        if not passed:
            failed_case_ids.append(case_id)
        case_results.append(
            {
                "id": case_id,
                "passed": passed,
                "markerRecall": _ratio(case_marker_hits, len(markers)),
                "latestUserRetained": latest_retained,
                "loadedSkillRetained": skill_retained,
                "summaryChainRetained": chain_retained,
                "toolPairsIntact": pairs_intact,
                "nonNegativeSavings": nonnegative_savings,
                "taskLedgerRetained": task_ledger_retained,
            }
        )

    count = len(cases)
    return {
        "schemaVersion": 1,
        "evaluator": "compaction-fidelity",
        "caseCount": count,
        "markerRecall": _ratio(marker_hits, marker_total),
        "latestUserRetentionRate": _ratio(latest_user_hits, count),
        "loadedSkillRetentionRate": _ratio(skill_hits, count),
        "summaryChainRate": _ratio(chain_hits, count),
        "toolPairIntegrityRate": _ratio(pair_hits, count),
        "nonNegativeSavingsRate": _ratio(savings_hits, count),
        "taskLedgerRetentionRate": _ratio(ledger_hits, ledger_cases),
        "failedCaseIds": failed_case_ids,
        "cases": case_results,
    }


def _nonnegative_int(value: object, *, field: str, case_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {field} for north-star case {case_id}")
    return value


def _optional_nonnegative_int(
    value: object,
    *,
    field: str,
    case_id: str,
) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field=field, case_id=case_id)


def evaluate_north_star(
    manifest_path: str | Path,
    results_path: str | Path,
) -> dict[str, object]:
    """Join a frozen real-task manifest to one evidence-bearing result set."""
    manifest = _load_document(manifest_path)
    results_document = _load_document(results_path)
    if manifest.get("schemaVersion") != 1 or results_document.get("schemaVersion") != 1:
        raise ValueError("unsupported north-star schema")
    suite_id = manifest.get("suiteId")
    if (
        not isinstance(suite_id, str)
        or not suite_id
        or results_document.get("suiteId") != suite_id
    ):
        raise ValueError("north-star suiteId mismatch")
    raw_cases = manifest.get("cases")
    raw_results = results_document.get("results")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("north-star manifest requires non-empty cases")
    if not isinstance(raw_results, list):
        raise TypeError("north-star results must be a list")

    cases: dict[str, dict[str, object]] = {}
    ordered_ids: list[str] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise TypeError(f"invalid north-star case at index {index}")
        case_id = raw_case.get("id")
        category = raw_case.get("category")
        mutability = raw_case.get("mutability")
        oracle_ids = raw_case.get("oracleIds")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in cases
            or not isinstance(category, str)
            or not category
            or mutability not in {"read_only", "write"}
            or not isinstance(oracle_ids, list)
            or not oracle_ids
            or not all(isinstance(item, str) and item for item in oracle_ids)
            or len(set(oracle_ids)) != len(oracle_ids)
        ):
            raise ValueError(f"invalid north-star case at index {index}")
        cases[case_id] = raw_case
        ordered_ids.append(case_id)

    indexed_results: dict[str, dict[str, object]] = {}
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, dict):
            raise TypeError(f"invalid north-star result at index {index}")
        case_id = raw_result.get("id")
        if not isinstance(case_id, str) or case_id not in cases or case_id in indexed_results:
            raise ValueError(f"north-star result identity mismatch at index {index}")
        indexed_results[case_id] = raw_result
    if set(indexed_results) != set(cases):
        missing = sorted(set(cases) - set(indexed_results))
        raise ValueError(f"north-star results missing cases: {missing}")

    successes = 0
    verified_successes = 0
    inconclusive = 0
    unsafe_cases = 0
    intervention_cases = 0
    evidence_complete = 0
    passed_oracles = 0
    total_oracles = 0
    total_duration_ms = 0
    total_model_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    duration_telemetry_cases = 0
    model_call_telemetry_cases = 0
    token_telemetry_cases = 0
    failed_case_ids: list[str] = []
    category_totals: dict[str, int] = {}
    category_successes: dict[str, int] = {}
    case_results: list[dict[str, object]] = []

    for case_id in ordered_ids:
        case = cases[case_id]
        result = indexed_results[case_id]
        status = result.get("status")
        verification_passed = result.get("verificationPassed")
        if status not in {"passed", "failed", "inconclusive"} or not isinstance(
            verification_passed, bool
        ):
            raise ValueError(f"invalid outcome for north-star case {case_id}")
        unsafe_count = _nonnegative_int(
            result.get("unsafeActionCount"), field="unsafeActionCount", case_id=case_id
        )
        intervention_count = _nonnegative_int(
            result.get("userInterventionCount"),
            field="userInterventionCount",
            case_id=case_id,
        )
        duration_ms = _optional_nonnegative_int(
            result.get("durationMs"), field="durationMs", case_id=case_id
        )
        model_calls = _optional_nonnegative_int(
            result.get("modelCalls"), field="modelCalls", case_id=case_id
        )
        input_tokens = _optional_nonnegative_int(
            result.get("inputTokens"), field="inputTokens", case_id=case_id
        )
        output_tokens = _optional_nonnegative_int(
            result.get("outputTokens"), field="outputTokens", case_id=case_id
        )
        expected_oracles = list(case["oracleIds"])
        actual_oracles = result.get("passedOracleIds")
        if (
            not isinstance(actual_oracles, list)
            or not all(isinstance(item, str) for item in actual_oracles)
            or len(set(actual_oracles)) != len(actual_oracles)
            or not set(actual_oracles).issubset(expected_oracles)
        ):
            raise ValueError(f"invalid oracle evidence for north-star case {case_id}")
        run_id = result.get("runId")
        has_run_evidence = isinstance(run_id, str) and re.fullmatch(
            r"run_[0-9a-f]{32}", run_id
        ) is not None

        passed = status == "passed"
        successes += int(passed)
        verified_successes += int(passed and verification_passed)
        inconclusive += int(status == "inconclusive")
        unsafe_cases += int(unsafe_count > 0)
        intervention_cases += int(intervention_count > 0)
        evidence_complete += int(has_run_evidence)
        passed_oracles += len(actual_oracles)
        total_oracles += len(expected_oracles)
        if duration_ms is not None:
            duration_telemetry_cases += 1
            total_duration_ms += duration_ms
        if model_calls is not None:
            model_call_telemetry_cases += 1
            total_model_calls += model_calls
        if input_tokens is not None and output_tokens is not None:
            token_telemetry_cases += 1
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
        if not passed:
            failed_case_ids.append(case_id)
        category = str(case["category"])
        category_totals[category] = category_totals.get(category, 0) + 1
        category_successes[category] = category_successes.get(category, 0) + int(passed)
        case_results.append(
            {
                "id": case_id,
                "category": category,
                "mutability": case["mutability"],
                "status": status,
                "verificationPassed": verification_passed,
                "passedOracleCount": len(actual_oracles),
                "oracleCount": len(expected_oracles),
                "runEvidenceComplete": has_run_evidence,
                "durationTelemetryComplete": duration_ms is not None,
                "modelCallTelemetryComplete": model_calls is not None,
                "tokenTelemetryComplete": (
                    input_tokens is not None and output_tokens is not None
                ),
            }
        )

    count = len(cases)
    category_rates = {
        category: _ratio(category_successes.get(category, 0), total)
        for category, total in sorted(category_totals.items())
    }
    return {
        "schemaVersion": 1,
        "evaluator": "north-star-recorded",
        "suiteId": suite_id,
        "caseCount": count,
        "categoryCount": len(category_rates),
        "writeCaseCount": sum(case["mutability"] == "write" for case in cases.values()),
        "taskSuccessRate": _ratio(successes, count),
        "verifiedSuccessRate": _ratio(verified_successes, count),
        "oraclePassRate": _ratio(passed_oracles, total_oracles),
        "minCategorySuccessRate": min(category_rates.values()),
        "unsafeActionRate": _ratio(unsafe_cases, count),
        "interventionRate": _ratio(intervention_cases, count),
        "inconclusiveRate": _ratio(inconclusive, count),
        "evidenceCompleteRate": _ratio(evidence_complete, count),
        "durationTelemetryCoverageRate": _ratio(duration_telemetry_cases, count),
        "modelCallTelemetryCoverageRate": _ratio(model_call_telemetry_cases, count),
        "tokenTelemetryCoverageRate": _ratio(token_telemetry_cases, count),
        "averageDurationMs": (
            round(total_duration_ms / duration_telemetry_cases, 3)
            if duration_telemetry_cases
            else None
        ),
        "totalModelCalls": total_model_calls,
        "totalInputTokens": total_input_tokens,
        "totalOutputTokens": total_output_tokens,
        "categorySuccessRates": category_rates,
        "failedCaseIds": failed_case_ids,
        "cases": case_results,
    }


def evaluate_quality_suite(
    fixture_root: str | Path,
    *,
    include_cases: bool = False,
    north_star_manifest_path: str | Path | None = None,
    north_star_results_path: str | Path | None = None,
) -> dict[str, object]:
    """Run every deterministic Tier 0 evaluator behind one small interface."""
    root = Path(fixture_root)
    skill_path = root / "skill-routing.json"
    compaction_path = root / "compaction-fidelity.json"
    resolved_north_star_manifest_path = (
        Path(north_star_manifest_path)
        if north_star_manifest_path is not None
        else root / "north-star-manifest.json"
    )
    resolved_north_star_results_path = (
        Path(north_star_results_path)
        if north_star_results_path is not None
        else root / "north-star-baseline-results.json"
    )
    if not all(
        path.is_file()
        for path in (
            skill_path,
            compaction_path,
            resolved_north_star_manifest_path,
            resolved_north_star_results_path,
        )
    ):
        raise ValueError("agent quality fixture root is incomplete")

    skill_report = evaluate_skill_routing(skill_path)
    compaction_report = evaluate_compaction_fidelity(compaction_path)
    north_star_report = evaluate_north_star(
        resolved_north_star_manifest_path,
        resolved_north_star_results_path,
    )
    if not include_cases:
        skill_report = {key: value for key, value in skill_report.items() if key != "cases"}
        compaction_report = {
            key: value for key, value in compaction_report.items() if key != "cases"
        }
        north_star_report = {
            key: value for key, value in north_star_report.items() if key != "cases"
        }
    report: dict[str, object] = {
        "schemaVersion": 1,
        "mode": "offline-deterministic",
        "remoteCallCount": 0,
        "datasets": {
            "skillRoutingSha256": _sha256(skill_path),
            "compactionFidelitySha256": _sha256(compaction_path),
            "northStarManifestSha256": _sha256(
                resolved_north_star_manifest_path
            ),
            "northStarResultsSha256": _sha256(resolved_north_star_results_path),
        },
        "skillRouting": skill_report,
        "compactionFidelity": compaction_report,
        "northStar": north_star_report,
    }
    fingerprint_source = json.dumps(
        report,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    report["payloadSha256"] = hashlib.sha256(fingerprint_source).hexdigest()
    return report


def evaluate_gate(
    report: dict[str, object],
    contract_path: str | Path,
    *,
    profile: str,
) -> dict[str, object]:
    """Apply one named min/max threshold profile to a quality report."""
    contract = _load_document(contract_path)
    if contract.get("schemaVersion") != 1:
        raise ValueError("unsupported quality contract schema")
    profiles = contract.get("profiles")
    if not isinstance(profiles, dict) or profile not in profiles:
        raise ValueError(f"unknown quality profile: {profile}")
    dimensions = profiles[profile]
    if not isinstance(dimensions, dict) or not dimensions:
        raise ValueError(f"quality profile {profile} has no dimensions")

    checks: list[dict[str, object]] = []
    failed_checks: list[str] = []
    for dimension, raw_metrics in dimensions.items():
        if not isinstance(dimension, str) or not isinstance(raw_metrics, dict):
            raise TypeError(f"invalid quality dimension in profile {profile}")
        actual_dimension = report.get(dimension)
        if not isinstance(actual_dimension, dict):
            actual_dimension = {}
        for metric, raw_limits in raw_metrics.items():
            if not isinstance(metric, str) or not isinstance(raw_limits, dict):
                raise TypeError(f"invalid metric contract for {dimension}.{metric}")
            if not raw_limits or not set(raw_limits).issubset({"min", "max", "equals"}):
                raise ValueError(f"invalid limits for {dimension}.{metric}")
            actual = actual_dimension.get(metric)
            if "equals" in raw_limits:
                expected = raw_limits["equals"]
                passed = actual == expected
                check_id = f"{dimension}.{metric}.equals"
                if not passed:
                    failed_checks.append(check_id)
                checks.append(
                    {
                        "id": check_id,
                        "actual": actual,
                        "threshold": expected,
                        "passed": passed,
                    }
                )
            actual_is_number = (
                isinstance(actual, (int, float)) and not isinstance(actual, bool)
            )
            for operator in ("min", "max"):
                if operator not in raw_limits:
                    continue
                threshold = raw_limits[operator]
                if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                    raise TypeError(
                        f"invalid {operator} threshold for {dimension}.{metric}"
                    )
                passed = bool(
                    actual_is_number
                    and (
                        actual >= threshold
                        if operator == "min"
                        else actual <= threshold
                    )
                )
                check_id = f"{dimension}.{metric}.{operator}"
                if not passed:
                    failed_checks.append(check_id)
                checks.append(
                    {
                        "id": check_id,
                        "actual": actual if actual_is_number else None,
                        "threshold": threshold,
                        "passed": passed,
                    }
                )

    return {
        "schemaVersion": 1,
        "profile": profile,
        "passed": not failed_checks,
        "failedChecks": failed_checks,
        "checks": checks,
    }


__all__ = [
    "evaluate_compaction_fidelity",
    "evaluate_gate",
    "evaluate_north_star",
    "evaluate_quality_suite",
    "evaluate_skill_routing",
]
