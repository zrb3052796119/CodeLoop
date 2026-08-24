#!/usr/bin/env python3
"""Freeze V4 tasks with atomic tool-turn replay and tool-neutral verification."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V4_ROOT = ROOT / "artifacts" / "persistent-memory-repair-acceptance-v4"
V4_MANIFEST = V4_ROOT / "manifest.json"
V4_RESULT = V4_ROOT / "live-results.json"
V4_MANIFEST_SHA256 = (
    "beb99521e26b63408fdb075745f1fd04c1a11e8fa6cb8f2ba0ea6acc5cd6a788"
)
V4_RESULT_SHA256 = (
    "373d3e7b85e07e2a2781ba247db1fea72d7e10beb1935e31c2a5b51ad473c184"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "persistent-memory-repair-acceptance-v5" / "manifest.json"
)
SUITE_ID = "persistent-memory-repair-acceptance-2026-08-23-v5"
RUNTIME_PROFILE_IDENTITY = {
    "adapterType": "openai_compatible",
    "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-v4-pro",
    "provider": "custom",
}


def _read_frozen_snapshot(
    path: Path,
    expected_sha256: str,
    *,
    identity: str,
) -> bytes:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"frozen {identity} identity mismatch")
    return payload


def _contract_sha256(value: dict[str, str]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _make_verification_oracles_tool_neutral(
    cases: list[dict[str, Any]],
) -> int:
    changed = 0
    for case in cases:
        for index, oracle in enumerate(case.get("oracles", [])):
            if oracle.get("id") != "verification-ran":
                continue
            if oracle != {
                "everyTurn": True,
                "id": "verification-ran",
                "kind": "tool_succeeded",
                "min": 1,
                "toolName": "run_command",
            }:
                raise ValueError("V4 verification oracle identity mismatch")
            case["oracles"][index] = {
                "everyTurn": True,
                "id": "verification-ran",
                "kind": "verification_passed",
                "min": 1,
                "sources": ["run_command_exit", "test_runner"],
                "verificationKind": "tests",
            }
            changed += 1
    return changed


def build_manifest() -> dict[str, Any]:
    manifest_payload = _read_frozen_snapshot(
        V4_MANIFEST,
        V4_MANIFEST_SHA256,
        identity="V4 acceptance manifest",
    )
    _read_frozen_snapshot(
        V4_RESULT,
        V4_RESULT_SHA256,
        identity="V4 failure result",
    )
    source = json.loads(manifest_payload)
    if not isinstance(source, dict) or not isinstance(source.get("cases"), list):
        raise TypeError("V4 acceptance manifest is invalid")
    document = deepcopy(source)
    document["suiteId"] = SUITE_ID
    document["description"] = (
        "Synthetic post-repair live acceptance V5: the same ten V4 tasks, "
        "fixtures, behavioral checks and content-free Memory attribution; "
        "verification evidence is now provider-neutral across canonical "
        "run_command and test_runner observations."
    )
    document["runtimeProfileContract"] = {
        **RUNTIME_PROFILE_IDENTITY,
        "profileSha256": _contract_sha256(RUNTIME_PROFILE_IDENTITY),
        "version": 2,
    }
    changed = _make_verification_oracles_tool_neutral(document["cases"])
    if changed != 8:
        raise ValueError("unexpected V4 verification oracle count")
    document["supersedes"] = {
        "suiteId": source.get("suiteId"),
        "manifest": V4_MANIFEST.relative_to(ROOT).as_posix(),
        "manifestSha256": V4_MANIFEST_SHA256,
        "firstAttemptResult": V4_RESULT.relative_to(ROOT).as_posix(),
        "firstAttemptResultSha256": V4_RESULT_SHA256,
        "firstAttemptPassedCases": 7,
        "firstAttemptTotalCases": 10,
        "retainedAsFailureEvidence": True,
    }
    document["repairsUnderTest"] = [
        "deepseek_atomic_content_reasoning_tool_turn_replay_v3",
        "anthropic_atomic_multi_tool_turn_replay_v1",
        "compaction_atomic_multi_tool_turn_boundary_v1",
        "provider_neutral_verification_oracle_v1",
        "resume_manifest_case_and_source_identity_binding_v1",
        "private_evidence_public_projection_binding_v1",
        "credential_free_actual_adapter_route_binding_v2",
        "frozen_source_single_snapshot_builder_v1",
        "strict_turn_and_nonvacuous_oracle_contract_v1",
        "suite_completion_envelope_v1",
        "verified_recovery_red_green_semantic_anchors_v2",
        "behavior_level_recovery_projection_v1",
    ]
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(build_manifest(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
