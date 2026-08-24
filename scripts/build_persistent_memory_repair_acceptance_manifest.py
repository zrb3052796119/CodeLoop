#!/usr/bin/env python3
"""Build the bounded post-repair live Memory acceptance manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PATH_SOURCE = ROOT / "artifacts" / "persistent-memory-large-study-v3" / "manifest.json"
NON_PATH_SOURCE = ROOT / "artifacts" / "non-path-memory-study-v2" / "manifest.json"
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v1" / "manifest.json"
)
SUITE_ID = "persistent-memory-repair-acceptance-2026-08-23-v1"

SELECTIONS: dict[str, tuple[str, ...]] = {
    "path_resource_recovery": (
        "pmem-b1-schema-contract-warm",
        "pmem-b1-auth-policy-warm",
    ),
    "command_recovery": (
        "npmem-b1-payload-command-warm",
        "npmem-b1-release-command-warm",
    ),
    "code_fix_recovery": (
        "npmem-b1-expired-session-repair-warm",
        "npmem-b1-token-normalization-repair-warm",
    ),
    "stable_verification_rule": (
        "npmem-b1-ledger-verification-rule-warm",
        "npmem-b1-parser-verification-rule-warm",
    ),
    "project_constraint_decision": (
        "npmem-b1-registry-order-constraint-warm",
        "npmem-b1-error-envelope-constraint-warm",
    ),
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise ValueError(f"invalid source manifest: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    sources = (_load(PATH_SOURCE), _load(NON_PATH_SOURCE))
    available = {
        str(case["id"]): case
        for source in sources
        for case in source["cases"]
        if isinstance(case, dict) and isinstance(case.get("id"), str)
    }
    cases: list[dict[str, Any]] = []
    missing: list[str] = []
    for family, case_ids in SELECTIONS.items():
        for case_id in case_ids:
            source_case = available.get(case_id)
            if source_case is None:
                missing.append(case_id)
                continue
            case = deepcopy(source_case)
            study = dict(case.get("study") or {})
            study["acceptanceFamily"] = family
            study["sourceCaseId"] = case_id
            case["study"] = study
            cases.append(case)
    if missing:
        raise ValueError(f"source cases missing: {missing}")
    return {
        "schemaVersion": 1,
        "suiteId": SUITE_ID,
        "description": (
            "Synthetic post-repair live acceptance: two independent warm cases "
            "for each durable Memory lesson family."
        ),
        "caseCount": len(cases),
        "familyCount": len(SELECTIONS),
        "casesPerFamily": 2,
        "sourceManifests": [
            {"path": str(PATH_SOURCE.relative_to(ROOT)), "sha256": _sha256(PATH_SOURCE)},
            {
                "path": str(NON_PATH_SOURCE.relative_to(ROOT)),
                "sha256": _sha256(NON_PATH_SOURCE),
            },
        ],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
