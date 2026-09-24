"""Offline contracts for the memory-pollution study builder and analyzer.

The manifest must satisfy the production runner contract and the study's
own invariants (arms, env overrides, seeded roles, stale claims that fail in
the fixture).  The analyzer is exercised end to end on synthetic journals and
pool snapshots whose numbers encode a known mechanism pattern, so every
statistic and pool endpoint has an expected value.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.analyze_memory_pollution_study import analyze
from scripts.build_memory_pollution_study_manifest import build_manifest
from scripts.run_north_star_live import _validate_manifest


# ── manifest contract ──────────────────────────────────────────────


def test_manifest_satisfies_runner_contract_and_study_invariants() -> None:
    manifest = build_manifest()
    cases = _validate_manifest(manifest)
    assert len(cases) == 18
    assert sum(len(case["turns"]) for case in cases) == 54
    family_arms: dict[str, set[str]] = {}
    for case in cases:
        study = case["study"]
        family_arms.setdefault(study["familyId"], set()).add(study["arm"])
        turns = case["turns"]
        assert len(turns) == 3
        prompts = [turn["prompt"] for turn in turns]
        assert len(set(prompts)) == 3, "rounds must be distinct sessions"
        env = case["env"]
        assert env["MINI_CODE_MEMORY_CONTRIBUTION_REVIEW"] in {"off", "rule"}
        if study["arm"] == "polluted-off":
            assert env["MINI_CODE_MEMORY_CONTRIBUTION_REVIEW"] == "off"
            assert len(case["memoryEntries"]) == 3
        elif study["arm"] == "polluted-rule":
            assert env["MINI_CODE_MEMORY_CONTRIBUTION_REVIEW"] == "rule"
            assert len(case["memoryEntries"]) == 3
        else:
            assert study["arm"] == "clean"
            assert len(case["memoryEntries"]) == 1
        # Stale claims must fail in this fixture.
        for role, entry in zip(study["seedRoles"], case["memoryEntries"]):
            if role == "stale" and study["stratum"] == "path-recovery":
                stale_path = _path_inside(entry["content"])
                assert stale_path not in case["files"]
    assert len(family_arms) == 6
    assert all(arms == {"clean", "polluted-off", "polluted-rule"} for arms in family_arms.values())


def _path_inside(text: str) -> str | None:
    for token in text.replace(".", " ").split():
        if "/" in token:
            return token.rstrip(".,;:")
    return None


def test_command_families_ship_runnable_suites_matching_verifiers() -> None:
    manifest = build_manifest()
    for case in manifest["cases"]:
        if case["study"]["stratum"] != "command-recovery":
            continue
        verifier = next(
            oracle
            for oracle in case["oracles"]
            if oracle["id"] == "target-verifier"
        )
        module = verifier["argv"][-1]
        test_path = module.replace(".", "/") + ".py"
        assert test_path in case["files"], test_path
        marker = case["study"]["targetMarker"]
        assert marker in case["files"][test_path]


# ── analyzer end to end on synthetic evidence ──────────────────────

_ARM_PROFILE = {
    # arm -> {round -> (tool_calls, input_tokens)}
    "clean": {1: (2, 1000), 2: (2, 1000), 3: (2, 1000)},
    "polluted-off": {1: (4, 1800), 2: (4, 1800), 3: (4, 1800)},
    "polluted-rule": {1: (4, 1800), 2: (3, 1500), 3: (2, 1100)},
}


def _synthetic_run_events(tool_calls: int, input_tokens: int) -> list[dict]:
    events = [
        {"type": "run.started", "timestamp": "2026-09-09T10:00:00+00:00", "payload": {}}
    ]
    for index in range(tool_calls):
        operation = f"op-{index}"
        events.append(
            {
                "type": "tool.started",
                "payload": {"operationId": operation, "toolName": "read_file"},
            }
        )
        events.append(
            {
                "type": "tool.finished",
                "payload": {
                    "operationId": operation,
                    "outcome": "success",
                    "paired": True,
                },
            }
        )
    events.append(
        {
            "type": "model.completed",
            "payload": {
                "usage": {"inputTokens": input_tokens, "outputTokens": 50}
            },
        }
    )
    events.append(
        {"type": "task.outcome", "payload": {"outcomeStatus": "success"}}
    )
    events.append(
        {
            "type": "run.completed",
            "timestamp": "2026-09-09T10:00:30+00:00",
            "payload": {},
        }
    )
    return events


def _synthetic_pool(case: dict) -> dict:
    study = case["study"]
    arm = study["arm"]
    entries = []
    role_ids: dict[str, str] = {}
    for index, (role, memory) in enumerate(
        zip(study["seedRoles"], case["memoryEntries"])
    ):
        entry_id = f"seed-{index}"
        role_ids[role] = entry_id
        quarantined = role == "stale" and arm == "polluted-rule"
        entries.append(
            {
                "id": entry_id,
                "category": "lesson",
                "tier": "short_term",
                "approval_status": "rejected" if quarantined else "approved",
                "lifecycle_status": "rejected" if quarantined else "active",
                "safety_status": "safe",
                "success_count": 0,
                "failure_count": 0,
                "corroborated_success_count": 0,
                "corroborated_failure_count": 2 if quarantined else 0,
                "injection_count": 3,
                "usage_count": 3,
                "content_sha256": hashlib.sha256(
                    memory["content"].encode()
                ).hexdigest(),
            }
        )
    # The correct lesson renders every round. The stale lesson renders in
    # rounds 1-2 under review (quarantined before round 3) and in every round
    # without review.
    stale_rounds = (1, 2) if arm == "polluted-rule" else (1, 2, 3)
    rendered_by_turn: dict[str, list[str]] = {
        "0": [role_ids["correct"]],
        "1": [role_ids["correct"]],
        "2": [role_ids["correct"]],
    }
    if "stale" in role_ids:
        for round_index in stale_rounds:
            rendered_by_turn[str(round_index - 1)].append(role_ids["stale"])
    return {"entries": entries, "renderedByTurn": rendered_by_turn}


def test_analyzer_recovers_mechanism_pattern_from_synthetic_evidence(
    tmp_path: Path,
) -> None:
    manifest = build_manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence_root = tmp_path / "full-results-evidence"
    results = []
    for case in manifest["cases"]:
        arm = case["study"]["arm"]
        run_ids = []
        for round_index in (1, 2, 3):
            tool_calls, input_tokens = _ARM_PROFILE[arm][round_index]
            run_id = (
                "run_"
                + hashlib.sha256(
                    f"{case['id']}#{round_index}".encode()
                ).hexdigest()[:32]
            )
            run_ids.append(run_id)
            events_path = (
                evidence_root
                / "cases"
                / case["id"]
                / "journal"
                / "runs"
                / run_id
                / "events.ndjson"
            )
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "\n".join(
                    json.dumps(event) for event in _synthetic_run_events(
                        tool_calls, input_tokens
                    )
                )
                + "\n",
                encoding="utf-8",
            )
        evidence_path = evidence_root / "cases" / case["id"] / "evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"memoryPool": _synthetic_pool(case)}), encoding="utf-8"
        )
        results.append(
            {
                "id": case["id"],
                "status": "passed",
                "runId": run_ids[-1],
                "relatedRunIds": run_ids[:-1],
            }
        )
    result_path = tmp_path / "full-results.json"
    result_path.write_text(
        json.dumps({"schemaVersion": 1, "suiteId": manifest["suiteId"], "results": results}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "analysis-output"
    payload = analyze(
        manifest_path=manifest_path, result_path=result_path, output_dir=output_dir
    )

    turn_rows = (output_dir / "turn-level.csv").read_text().splitlines()
    assert len(turn_rows) == 55  # header + 54

    summary = payload["roundSummary"]
    assert (output_dir / "round-summary.csv").exists()
    clean_r1 = next(
        cell
        for cell in summary
        if cell["arm"] == "clean" and cell["round"] == 1
    )
    assert clean_r1["families"] == 6
    assert clean_r1["tool_calls_mean"] == 2.0
    assert clean_r1["task_input_tokens_mean"] == 1000.0
    assert clean_r1["duration_ms_mean"] == 30000.0
    rule_r3 = next(
        cell
        for cell in summary
        if cell["arm"] == "polluted-rule" and cell["round"] == 3
    )
    assert rule_r3["tool_calls_mean"] == 2.0
    assert rule_r3["duration_ms_mean"] == 30000.0

    tests = payload["primaryTests"]
    assert tests["tool_calls:polluted-rule-minus-clean:round1"]["meanDifference"] == 2.0
    assert tests["tool_calls:polluted-rule-minus-clean:round3"]["meanDifference"] == 0.0
    assert tests["tool_calls:polluted-off-minus-clean:round3"]["meanDifference"] == 2.0
    assert (
        tests["tool_calls:polluted-rule-minus-polluted-off:round3"]["meanDifference"]
        == -2.0
    )

    pool = payload["poolEndpoints"]
    assert pool["staleQuarantinedRate"]["polluted-rule"] == 1.0
    assert pool["staleQuarantinedRate"]["polluted-off"] == 0.0
    assert pool["staleRenderedRound3Rate"]["polluted-rule"] == 0.0
    assert pool["staleRenderedRound3Rate"]["polluted-off"] == 1.0
    assert pool["correctRenderedAnyRoundRate"]["clean"] == 1.0

    report = (output_dir / "analysis-report.md").read_text(encoding="utf-8")
    assert "Pool self-correction endpoints" in report
    assert "Interpretation boundary" in report
