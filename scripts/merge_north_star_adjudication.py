#!/usr/bin/env python3
"""Merge one bounded north-star adjudication run without hiding first-pass evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _by_id(items: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise TypeError(f"{label} must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise TypeError(f"{label} item is invalid")
        item_id = str(item["id"])
        if item_id in indexed:
            raise ValueError(f"duplicate {label} id: {item_id}")
        indexed[item_id] = dict(item)
    return indexed


def merge_results(
    *,
    original_manifest: Mapping[str, object],
    revised_manifest: Mapping[str, object],
    original_results: Mapping[str, object],
    rerun_results: Mapping[str, object],
    replacements: Mapping[str, str],
) -> dict[str, object]:
    old_cases = _by_id(original_manifest.get("cases"), "original cases")
    new_cases = _by_id(revised_manifest.get("cases"), "revised cases")
    old_results = _by_id(original_results.get("results"), "original results")
    new_results = _by_id(rerun_results.get("results"), "rerun results")
    if set(old_cases) != set(new_cases) or set(old_results) != set(new_cases):
        raise ValueError("original manifest/results do not cover the revised suite")
    changed = {case_id for case_id in old_cases if old_cases[case_id] != new_cases[case_id]}
    replacement_ids = set(replacements)
    if not changed <= replacement_ids:
        raise ValueError("every revised case must have fresh rerun evidence")
    if set(new_results) != replacement_ids:
        raise ValueError("rerun results must exactly match replacement ids")

    ordered_results: list[dict[str, Any]] = []
    inherited = original_results.get("adjudications", [])
    if not isinstance(inherited, list) or not all(
        isinstance(item, dict) for item in inherited
    ):
        raise TypeError("original adjudications must be a list of objects")
    adjudications: list[dict[str, object]] = [dict(item) for item in inherited]
    for case in revised_manifest.get("cases", []):
        case_id = str(case["id"])
        if case_id not in replacement_ids:
            ordered_results.append(old_results[case_id])
            continue
        prior = old_results[case_id]
        replacement = new_results[case_id]
        ordered_results.append(replacement)
        adjudications.append(
            {
                "id": case_id,
                "reason": replacements[case_id],
                "priorRunId": prior.get("runId"),
                "priorStatus": prior.get("status"),
                "replacementRunId": replacement.get("runId"),
                "replacementStatus": replacement.get("status"),
            }
        )
    return {
        "schemaVersion": 1,
        "suiteId": revised_manifest.get("suiteId"),
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": ordered_results,
        "adjudications": adjudications,
        "reusedOriginalEvidenceCount": len(ordered_results) - len(replacements),
    }


def _replacement(value: str) -> tuple[str, str]:
    case_id, separator, reason = value.partition("=")
    if not separator or not case_id.strip() or not reason.strip():
        raise argparse.ArgumentTypeError("replacement must be CASE_ID=REASON")
    return case_id.strip(), reason.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-manifest", required=True, type=Path)
    parser.add_argument("--revised-manifest", required=True, type=Path)
    parser.add_argument("--original-results", required=True, type=Path)
    parser.add_argument("--rerun-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replacement", action="append", type=_replacement, default=[])
    args = parser.parse_args()
    replacements = dict(args.replacement)
    if len(replacements) != len(args.replacement):
        raise ValueError("replacement ids must be unique")
    revised_bytes = args.revised_manifest.read_bytes()
    merged = merge_results(
        original_manifest=_load(args.original_manifest),
        revised_manifest=_load(args.revised_manifest),
        original_results=_load(args.original_results),
        rerun_results=_load(args.rerun_results),
        replacements=replacements,
    )
    merged["manifestSha256"] = hashlib.sha256(revised_bytes).hexdigest()
    merged["sourceReports"] = {
        "original": str(args.original_results),
        "rerun": str(args.rerun_results),
        "revisedManifest": str(args.revised_manifest),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
