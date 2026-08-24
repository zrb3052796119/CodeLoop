#!/usr/bin/env python3
"""Build the corrected V2 post-repair live Memory acceptance manifest.

V1 is retained as immutable failure evidence.  V2 changes only an oracle that
incorrectly required one implementation spelling (`lower`) even though the
frozen behavioral test also accepts the stronger Unicode-aware `casefold`.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V1_MANIFEST = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v1" / "manifest.json"
)
V1_MANIFEST_SHA256 = (
    "b73b47102aac8eabfc2595555ca6aafca2000af0573c4e0bdec8d788796b1a93"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v2" / "manifest.json"
)
SUITE_ID = "persistent-memory-repair-acceptance-2026-08-23-v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    if _sha256(V1_MANIFEST) != V1_MANIFEST_SHA256:
        raise ValueError("frozen V1 acceptance manifest identity mismatch")
    source = json.loads(V1_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise TypeError("V1 acceptance manifest root must be an object")
    document = deepcopy(source)
    document["suiteId"] = SUITE_ID
    document["description"] = (
        "Synthetic post-repair live acceptance V2: V1 tasks with the token "
        "normalization oracle corrected to verify behavior rather than a "
        "specific lower-versus-casefold implementation."
    )
    document["supersedes"] = {
        "suiteId": source.get("suiteId"),
        "manifest": str(V1_MANIFEST.relative_to(ROOT)),
        "sha256": V1_MANIFEST_SHA256,
        "retainedAsFailureEvidence": True,
    }
    document["oracleCorrections"] = [
        {
            "caseId": "npmem-b1-token-normalization-repair-warm",
            "oracleId": "target-content",
            "reason": "casefold_is_behaviorally_valid_and_passes_the_frozen_test",
            "from": {
                "kind": "file_contains",
                "text": "normalized = value.strip().lower()",
            },
            "to": {
                "kind": "file_not_contains",
                "text": "normalized = value.strip()\n",
            },
        }
    ]

    target_case = next(
        case
        for case in document["cases"]
        if case.get("id") == "npmem-b1-token-normalization-repair-warm"
    )
    target_oracle = next(
        oracle
        for oracle in target_case["oracles"]
        if oracle.get("id") == "target-content"
    )
    target_oracle["kind"] = "file_not_contains"
    target_oracle["text"] = "normalized = value.strip()\n"
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
