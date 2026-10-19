#!/usr/bin/env python3
"""Descriptive post-hoc diagnostics; never change preregistered comparisons.

Reads content-free trial metrics and projects only usage/error metadata from
private request ledgers. No prompt or response text is copied into outputs.
Partial snapshots require --allow-partial and are visibly marked incomplete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ARMS = ("single", "multi")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_token(value: object) -> bool:
    return type(value) is int and value >= 0


def request_projection(records: list[dict]) -> dict:
    """First completion means first completion event, not first known usage."""
    started = {row["call"] for row in records if row.get("event") == "started"}
    completed = [row for row in records if row.get("event") == "completed"]
    failed = [row for row in records if row.get("event") == "failed"]
    completed_ids = {row["call"] for row in completed}
    failed_ids = {row["call"] for row in failed}
    provider_usage_ids = {
        row["call"]
        for row in completed
        if isinstance(row.get("usage"), dict)
        and row["usage"].get("source") == "provider"
        and is_token(row["usage"].get("input_tokens"))
        and is_token(row["usage"].get("output_tokens"))
    }
    first = completed[0] if completed else None
    first_usage = first.get("usage") if first else None
    first_input = None
    if (
        isinstance(first_usage, dict)
        and first_usage.get("source") == "provider"
        and is_token(first_usage.get("input_tokens"))
    ):
        first_input = first_usage["input_tokens"]
    first_started = next(
        (row["call"] for row in records if row.get("event") == "started"), None
    )
    error_counts = Counter(
        str(row.get("errorClass") or "UnknownError") for row in failed
    )
    timeout_ids = {
        row["call"]
        for row in failed
        if "timeout" in str(row.get("errorClass", "")).casefold()
    }
    return {
        "startedRequests": len(started),
        "completedRequests": len(completed_ids),
        "failedRequests": len(failed_ids),
        "requestErrorClasses": dict(error_counts),
        "timeoutRequests": len(timeout_ids),
        "requestsWithProviderUsage": len(started & provider_usage_ids),
        "requestsMissingProviderUsage": len(started - provider_usage_ids),
        "completedRequestsMissingProviderUsage": len(
            completed_ids - provider_usage_ids
        ),
        "startedRequestsWithoutTerminalEvent": len(
            started - completed_ids - failed_ids
        ),
        "firstCompletedCall": first["call"] if first else None,
        "firstStartedCall": first_started,
        "firstCompletionWasFirstStartedRequest": first is not None
        and first["call"] == first_started,
        "firstCompletedInputTokens": first_input,
    }


def read_trial_projection(root: Path, assignment: dict, row: dict) -> dict:
    trial_id = assignment["id"]
    if (
        not isinstance(trial_id, str)
        or Path(trial_id).name != trial_id
        or trial_id in (".", "..")
    ):
        raise ValueError("Unsafe trial id")
    for field in ("id", "case", "repeat", "arm"):
        if row.get(field) != assignment.get(field):
            raise ValueError(f"Trial assignment mismatch: {trial_id}/{field}")
    path = root / "private" / trial_id / "calls.jsonl"
    records = []
    malformed_lines = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                if isinstance(record, dict) and is_token(record.get("call")):
                    records.append(record)
                else:
                    malformed_lines += 1
            except json.JSONDecodeError:
                malformed_lines += 1
    projection = request_projection(records)
    return {
        **assignment,
        "category": row.get("category", "unknown"),
        "success": row.get("success"),
        "usageComplete": row.get("usageComplete"),
        "trialErrorClass": row.get("errorClass"),
        "trialTimedOut": "timeout" in str(row.get("errorClass") or "").casefold(),
        "delegationAttempts": row.get("delegationAttempts"),
        "ledgerMissing": not path.exists(),
        "ledgerMalformedLines": malformed_lines,
        "ledgerSha256": sha(path) if path.exists() else None,
        **projection,
    }


def numeric_summary(values: list[int]) -> dict:
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "positive": sum(x > 0 for x in values),
        "zero": sum(x == 0 for x in values),
        "negative": sum(x < 0 for x in values),
    }


def distribution(rows: list[dict]) -> dict:
    keys = (
        "startedRequests",
        "completedRequests",
        "failedRequests",
        "timeoutRequests",
        "requestsWithProviderUsage",
        "requestsMissingProviderUsage",
        "completedRequestsMissingProviderUsage",
        "startedRequestsWithoutTerminalEvent",
    )
    errors: Counter = Counter()
    for row in rows:
        errors.update(row["requestErrorClasses"])
    return {
        "trials": len(rows),
        "usageCompleteTrials": sum(row["usageComplete"] is True for row in rows),
        "usageIncompleteTrials": sum(row["usageComplete"] is False for row in rows),
        "usageStatusMissingTrials": sum(row["usageComplete"] is None for row in rows),
        "trialsWithTimeoutRequest": sum(row["timeoutRequests"] > 0 for row in rows),
        "trialsWithTimeoutErrorClass": sum(row["trialTimedOut"] for row in rows),
        "trialsWithMissingProviderUsage": sum(
            row["requestsMissingProviderUsage"] > 0 for row in rows
        ),
        "ledgerMissingTrials": sum(row["ledgerMissing"] for row in rows),
        "ledgerMalformedLines": sum(row["ledgerMalformedLines"] for row in rows),
        "trialErrorClasses": dict(
            Counter(row["trialErrorClass"] or "none" for row in rows)
        ),
        "requestErrorClasses": dict(errors),
        **{key: sum(row[key] for row in rows) for key in keys},
    }


def mechanism_summary(projections: list[dict], expected_schedule: list[dict]) -> dict:
    lookup = {(row["case"], row["repeat"], row["arm"]): row for row in projections}
    pair_ids = sorted({(row["case"], row["repeat"]) for row in expected_schedule})
    pairs = []
    for case, repeat in pair_ids:
        a, b = (lookup.get((case, repeat, arm)) for arm in ARMS)
        av = a["firstCompletedInputTokens"] if a else None
        bv = b["firstCompletedInputTokens"] if b else None
        pairs.append(
            {
                "case": case,
                "repeat": repeat,
                "singleTrialPresent": a is not None,
                "multiTrialPresent": b is not None,
                "singleFirstCompletedInputTokens": av,
                "multiFirstCompletedInputTokens": bv,
                "deltaMultiMinusSingle": bv - av
                if av is not None and bv is not None
                else None,
                "bothCompletionsAreFirstStartedRequest": bool(
                    a
                    and b
                    and a["firstCompletionWasFirstStartedRequest"]
                    and b["firstCompletionWasFirstStartedRequest"]
                ),
            }
        )
    observed = [row for row in pairs if row["deltaMultiMinusSingle"] is not None]
    first_attempt = [
        row for row in observed if row["bothCompletionsAreFirstStartedRequest"]
    ]
    task_means = {}
    for case in sorted({row["case"] for row in pairs}):
        values = [
            row["deltaMultiMinusSingle"] for row in observed if row["case"] == case
        ]
        task_means[case] = {
            "observedPairs": len(values),
            "meanDelta": statistics.mean(values) if values else None,
        }
    return {
        "analysisType": "post-hoc exploratory descriptive mechanism diagnostics; no hypothesis tests",
        "complete": len(projections) == len(expected_schedule),
        "observedTrials": len(projections),
        "expectedTrials": len(expected_schedule),
        "firstCompletionDefinition": "First completed event in ledger order; provider-reported input_tokens only. Never skip missing usage to choose a later completion.",
        "pairedUnit": "same task and repeat; repeated pairs are not independent tasks",
        "firstCompletedInputTokenPairs": pairs,
        "pairedInputDelta": numeric_summary(
            [row["deltaMultiMinusSingle"] for row in observed]
        ),
        "pairedInputDeltaWhenBothAreFirstStartedRequest": numeric_summary(
            [row["deltaMultiMinusSingle"] for row in first_attempt]
        ),
        "perTaskInputDelta": task_means,
        "arms": {
            arm: distribution([row for row in projections if row["arm"] == arm])
            for arm in ARMS
        },
        "byRepeat": {
            str(repeat): {
                arm: distribution(
                    [
                        row
                        for row in projections
                        if row["arm"] == arm and row["repeat"] == repeat
                    ]
                )
                for arm in ARMS
            }
            for repeat in sorted({row["repeat"] for row in expected_schedule})
        },
        "byTask": {
            case: {
                arm: distribution(
                    [
                        row
                        for row in projections
                        if row["arm"] == arm and row["case"] == case
                    ]
                )
                for arm in ARMS
            }
            for case in sorted({row["case"] for row in expected_schedule})
        },
        "trials": projections,
        "interpretationLimits": [
            "Both arms use the same bounded host approval callback. This tests model-chosen delegation for preauthorized write scope, not no-approval headless forced workflow escalation.",
            "Post-hoc mechanism hypotheses do not replace or add to preregistered significance tests.",
            "A first-request input difference is compatible with prompt/tool-schema context overhead, but does not prove why latency or success changed.",
            "Unavailable first-completion usage remains null; reported pairs are the observed subset, not all 24 unless complete.",
            "Timeouts are classified from errorClass containing timeout; non-timeout errors and missing terminal records remain separate.",
            "Any missing usage blocks complete cost comparisons; known input/output totals are lower bounds, not savings estimates.",
            "No observed delegation means outcomes cannot be attributed to multiple agents collaborating.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("artifacts/single-vs-multi-2026-10-13")
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="Defaults to exploratory-mechanisms.json inside input.",
    )
    args = parser.parse_args()
    try:
        manifest_path = args.input / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        projections = []
        for assignment in manifest["schedule"]:
            trial_id = assignment["id"]
            if (
                not isinstance(trial_id, str)
                or Path(trial_id).name != trial_id
                or trial_id in (".", "..")
            ):
                raise ValueError("Unsafe trial id")
            row_path = args.input / "trials" / f"{trial_id}.json"
            if row_path.exists():
                row = json.loads(row_path.read_text(encoding="utf-8"))
                projections.append(read_trial_projection(args.input, assignment, row))
        summary = mechanism_summary(projections, manifest["schedule"])
        if not summary["complete"] and not args.allow_partial:
            raise ValueError(
                "Incomplete trials; use --allow-partial only for a visibly partial exploratory snapshot."
            )
        summary["manifestSha256"] = sha(manifest_path)
        summary["analysisScriptSha256"] = sha(Path(__file__))
        target = args.output or args.input / "exploratory-mechanisms.json"
        if target.name in {
            "manifest.json",
            "results.json",
            "tasks.json",
            "summary.json",
        }:
            raise ValueError(
                "Exploratory output cannot replace frozen or preregistered artifacts."
            )
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "complete": summary["complete"],
                    "observedTrials": summary["observedTrials"],
                    "pairedInputDelta": summary["pairedInputDelta"],
                    "output": str(target),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(
            json.dumps({"error": str(error), "analysisType": "post-hoc exploratory"}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
