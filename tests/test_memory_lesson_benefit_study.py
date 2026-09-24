"""Offline contracts for the memory-lesson-benefit study builder and analyzer.

The manifest must satisfy the runner contract plus the pairing invariants
(identical warm/cold target prompts and fixtures, llm reviewer env
everywhere, misdirection absent from fixtures).  The analyzer is exercised
end to end on synthetic journals encoding a known benefit pattern (warm
target cheaper than cold) plus fabricated reviewer events.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.analyze_memory_lesson_benefit_study import analyze
from scripts.build_memory_lesson_benefit_study_manifest import build_manifest
from scripts.run_north_star_live import _validate_manifest


# ── manifest contract ──────────────────────────────────────────────


def test_manifest_satisfies_runner_contract_and_pairing_invariants() -> None:
    manifest = build_manifest()
    cases = _validate_manifest(manifest)
    assert len(cases) == 24
    assert sum(len(case["turns"]) for case in cases) == 36
    warm = [case for case in cases if case["study"]["condition"] == "warm"]
    cold = [case for case in cases if case["study"]["condition"] == "cold"]
    assert len(warm) == 12 and len(cold) == 12
    for case in cases:
        assert case["env"] == {"MINI_CODE_MEMORY_CONTRIBUTION_REVIEW": "llm"}
        study = case["study"]
        # Command-recovery design: every fixture ships the correct suite and
        # the misdirected module must not exist anywhere in the fixture.
        verifier = next(
            oracle for oracle in case["oracles"] if oracle["id"] == "target-verifier"
        )
        module = verifier["argv"][-1]
        assert module in {
            str(path).replace("/", ".")[:-3]
            for path in case["files"]
            if str(path).endswith(".py") and "/tests/" in str(path)
        }
        assert study["wrongTarget"] not in {
            str(path).replace("/", ".")[:-3] for path in case["files"]
        }
    for warm_case in warm:
        partner = next(
            case
            for case in cold
            if case["study"]["familyId"] == warm_case["study"]["familyId"]
            and case["study"]["block"] == warm_case["study"]["block"]
        )
        assert warm_case["turns"][1]["prompt"] == partner["turns"][0]["prompt"]
        assert warm_case["files"] == partner["files"]
        oracle_ids = warm_case["oracleIds"]
        for required in (
            "source-failure",
            "lesson-written",
            "lesson-covers-target",
            "lesson-attributed",
        ):
            assert required in oracle_ids
    assert len({case["study"]["familyId"] for case in cases}) == 6
    assert {case["study"]["block"] for case in cases} == {1, 2}


# ── analyzer end to end on synthetic evidence ──────────────────────

_WARM_TARGET_PROFILE = (1, 8000)      # (tool_calls, input_tokens)
_COLD_TARGET_PROFILE = (4, 25000)
_LEARNING_PROFILE = (4, 20000)


def _run_events(
    tool_calls: int,
    input_tokens: int,
    *,
    tool_failures: int = 0,
    review: bool = False,
) -> list[dict]:
    events: list[dict] = [
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
                    "outcome": "error" if index < tool_failures else "success",
                    "paired": True,
                },
            }
        )
    events.append(
        {
            "type": "model.completed",
            "payload": {
                "usage": {"inputTokens": input_tokens, "outputTokens": 60}
            },
        }
    )
    if review:
        events.extend(
            [
                {
                    "type": "model.started",
                    "payload": {"purpose": "memory_contribution_review"},
                },
                {
                    "type": "model.completed",
                    "payload": {
                        "purpose": "memory_contribution_review",
                        "usage": {"inputTokens": 1500, "outputTokens": 200},
                    },
                },
                {
                    "type": "memory.contribution_reviewed",
                    "payload": {
                        "reviewSource": "llm",
                        "failureCode": None,
                        "verdicts": [
                            {
                                "memoryId": "m1",
                                "outcome": "helped",
                                "confidence": 0.9,
                                "basis": "llm",
                                "downgradeReason": None,
                            }
                        ],
                    },
                },
            ]
        )
    events.append(
        {"type": "task.outcome", "payload": {"outcomeStatus": "success"}}
    )
    events.append(
        {
            "type": "run.completed",
            "timestamp": "2026-09-09T10:00:20+00:00",
            "payload": {},
        }
    )
    return events


def test_analyzer_recovers_benefit_pattern_from_synthetic_evidence(
    tmp_path: Path,
) -> None:
    manifest = build_manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence_root = tmp_path / "full-results-evidence"
    results = []
    warm_index = 0
    for case in manifest["cases"]:
        study = case["study"]
        condition = study["condition"]
        run_ids = []
        turn_profiles = []
        if condition == "warm":
            turn_profiles.append(_LEARNING_PROFILE + (1,))
            turn_profiles.append(_WARM_TARGET_PROFILE + (0,))
        else:
            turn_profiles.append(_COLD_TARGET_PROFILE + (0,))
        for turn_index, (tool_calls, tokens, failures) in enumerate(turn_profiles):
            run_id = "run_" + hashlib.sha256(
                f"{case['id']}#{turn_index}".encode()
            ).hexdigest()[:32]
            run_ids.append(run_id)
            events = _run_events(
                tool_calls,
                tokens,
                tool_failures=failures,
                review=(condition == "warm" and turn_index == 1),
            )
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
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
        passed = list(case["oracleIds"])
        if condition == "warm":
            warm_index += 1
            if warm_index in (3, 7):  # two warm cases fail content precipitation
                passed.remove("lesson-covers-target")
                passed.remove("lesson-attributed")
        results.append(
            {
                "id": case["id"],
                "status": "passed" if len(passed) == len(case["oracleIds"]) else "failed",
                "runId": run_ids[-1],
                "relatedRunIds": run_ids[:-1],
                "passedOracleIds": passed,
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

    summary = {cell["condition"]: cell for cell in payload["conditionSummary"]}
    assert summary["warm"]["tool_calls_mean"] == 1.0
    assert summary["cold"]["tool_calls_mean"] == 4.0
    assert summary["warm"]["task_input_tokens_mean"] == 8000.0
    assert summary["cold"]["task_input_tokens_mean"] == 25000.0
    assert summary["warm"]["side_model_calls_mean"] == 1.0
    assert summary["cold"]["side_model_calls_mean"] == 0.0

    tests = payload["tests"]
    assert tests["tool_calls:cold-minus-warm"]["meanSaving"] == 3.0
    assert tests["task_input_tokens:cold-minus-warm"]["meanSaving"] == 17000.0
    assert "holmP" in tests["tool_calls:cold-minus-warm"]

    learning = payload["learningChain"]
    assert learning["lesson-covers-target"] == {"passed": 10, "total": 12}
    assert learning["lesson-written"] == {"passed": 12, "total": 12}

    reviewer = payload["reviewer"]
    assert reviewer["reviewModelCalls"] == 12
    assert reviewer["reviewInputTokens"] == 12 * 1500
    assert reviewer["verdicts"] == {"helped": 12}
    assert reviewer["reviewSources"] == {"llm": 12}

    report = (output_dir / "analysis-report.md").read_text(encoding="utf-8")
    assert "Raw per-condition target-turn metrics" in report
    assert "Learning-chain validity" in report
    assert "LLM contribution reviewer behaviour" in report
