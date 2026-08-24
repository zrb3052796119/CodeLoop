#!/usr/bin/env python3
"""Freeze the unchanged V3 tasks for final post-root-cause acceptance."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3_ROOT = ROOT / "artifacts" / "persistent-memory-repair-acceptance-v3"
V3_MANIFEST = V3_ROOT / "manifest.json"
V3_RESULT = V3_ROOT / "live-results.json"
V3_MANIFEST_SHA256 = (
    "a4644b34673cba48b21d9114c86f5948809c345f6446ca0e0a2444bfa7cc3cbf"
)
V3_RESULT_SHA256 = (
    "b64954ef63b8c99f3230927c5bef508f59b1c3d0fc4a6f786e279b5714525f5f"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v4" / "manifest.json"
)
SUITE_ID = "persistent-memory-repair-acceptance-2026-08-23-v4"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    if _sha256(V3_MANIFEST) != V3_MANIFEST_SHA256:
        raise ValueError("frozen V3 acceptance manifest identity mismatch")
    if _sha256(V3_RESULT) != V3_RESULT_SHA256:
        raise ValueError("frozen V3 failure result identity mismatch")
    source = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("cases"), list):
        raise TypeError("V3 acceptance manifest is invalid")
    document = deepcopy(source)
    document["suiteId"] = SUITE_ID
    document["description"] = (
        "Synthetic post-repair live acceptance V4: the exact V3 tasks, "
        "fixtures, behavioral oracles and content-free attribution oracles, "
        "rerun after the observed reasoning replay and recovery projection "
        "root causes were repaired."
    )
    document["supersedes"] = {
        "suiteId": source.get("suiteId"),
        "manifest": str(V3_MANIFEST.relative_to(ROOT)),
        "manifestSha256": V3_MANIFEST_SHA256,
        "firstAttemptResult": str(V3_RESULT.relative_to(ROOT)),
        "firstAttemptResultSha256": V3_RESULT_SHA256,
        "firstAttemptPassedCases": 7,
        "firstAttemptTotalCases": 10,
        "retainedAsFailureEvidence": True,
    }
    document["repairsUnderTest"] = [
        "deepseek_tool_reasoning_replay_v2",
        "verified_recovery_red_green_semantic_anchors_v2",
        "edit_file_old_new_change_projection_v1",
        "behavior_level_recovery_projection_v1",
    ]
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
