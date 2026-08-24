#!/usr/bin/env python3
"""Build V3 live acceptance with exact Memory lesson attribution.

V1 remains immutable failure evidence. V2 retains the behavioral-oracle
correction. V3 changes no task fixture or behavioral oracle; it adds one
content-free attribution oracle to each case so success is credited only when
the intended seeded or learned lesson is rendered in the target Turn.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V2_MANIFEST = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v2" / "manifest.json"
)
V2_MANIFEST_SHA256 = (
    "ab21f4010adeb192bd9a4fae2f25dfb93912fbd31b0430ececbe674a88a82bc0"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v3" / "manifest.json"
)
SUITE_ID = "persistent-memory-repair-acceptance-2026-08-23-v3"

ATTRIBUTION_BY_CASE: dict[str, dict[str, Any]] = {
    "pmem-b1-schema-contract-warm": {
        "source": "seeded",
        "seedIndexes": [0],
        "renderedTurn": 0,
        "min": 1,
    },
    "pmem-b1-auth-policy-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "recovery",
        "min": 1,
    },
    "npmem-b1-payload-command-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "recovery",
        "min": 1,
    },
    "npmem-b1-release-command-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "recovery",
        "min": 1,
    },
    "npmem-b1-expired-session-repair-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "recovery",
        "min": 1,
    },
    "npmem-b1-token-normalization-repair-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "recovery",
        "min": 1,
    },
    "npmem-b1-ledger-verification-rule-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "verification_rule",
        "min": 1,
    },
    "npmem-b1-parser-verification-rule-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "verification_rule",
        "min": 1,
    },
    "npmem-b1-registry-order-constraint-warm": {
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "constraint",
        "min": 1,
    },
    "npmem-b1-error-envelope-constraint-warm": {
        "source": "seeded",
        "seedIndexes": [0],
        "renderedTurn": 0,
        "min": 1,
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    if _sha256(V2_MANIFEST) != V2_MANIFEST_SHA256:
        raise ValueError("frozen V2 acceptance manifest identity mismatch")
    source = json.loads(V2_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("cases"), list):
        raise TypeError("V2 acceptance manifest is invalid")
    document = deepcopy(source)
    case_ids = {
        str(case.get("id")) for case in document["cases"] if isinstance(case, dict)
    }
    if case_ids != set(ATTRIBUTION_BY_CASE):
        raise ValueError("V2 acceptance case identity mismatch")

    document["suiteId"] = SUITE_ID
    document["description"] = (
        "Synthetic post-repair live acceptance V3: two cases per durable "
        "lesson family with exact source-Turn/seed to target-Turn Memory "
        "attribution, in addition to the V2 behavioral oracles."
    )
    document["supersedes"] = {
        "suiteId": source.get("suiteId"),
        "manifest": str(V2_MANIFEST.relative_to(ROOT)),
        "sha256": V2_MANIFEST_SHA256,
        "retainedAsEvidence": True,
    }
    document["attributionContract"] = {
        "version": 1,
        "contentFree": True,
        "requiresExactEntryIdIntersection": True,
        "learnedSourceTurnMustPrecedeRenderedTurn": True,
    }

    for case in document["cases"]:
        case_id = str(case["id"])
        if any(oracle.get("id") == "lesson-attributed" for oracle in case["oracles"]):
            raise ValueError(f"duplicate attribution oracle: {case_id}")
        case["oracles"].append(
            {
                "id": "lesson-attributed",
                "kind": "memory_attributed",
                **deepcopy(ATTRIBUTION_BY_CASE[case_id]),
            }
        )
        case["oracleIds"].append("lesson-attributed")
    return document


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
