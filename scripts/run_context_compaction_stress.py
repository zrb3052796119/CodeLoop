#!/usr/bin/env python3
"""Run a deterministic scale/round stress matrix through production compaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "context-compaction-paired-v1"
    / "deterministic-stress.json"
)
SCALES = (12, 24, 48, 96)
ROUNDS = (1, 2, 3, 4, 5)
LAYOUTS = ("head", "middle", "boundary")


from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
)
from minicode.context_manager import estimate_message_tokens
from scripts.agent_quality_evaluator import (
    _MarkerReplaySummarizer,
    _tool_pairs_intact,
    evaluate_compaction_fidelity,
)


def _marker(case_id: str, round_index: int, suffix: str) -> str:
    normalized = case_id.replace("-", "_").upper()
    return f"QG_STRESS_{normalized}_R{round_index}_{suffix}"


def _round_messages(
    *,
    case_id: str,
    round_index: int,
    scale: int,
    layout: str,
    markers: list[str],
) -> list[dict[str, object]]:
    positions = {
        "head": 0,
        "middle": scale // 2,
        "boundary": max(0, scale - 5),
    }
    marker_value = _marker(case_id, round_index, "STATE")
    markers.append(marker_value)
    messages: list[dict[str, object]] = []
    if round_index == 0:
        messages.extend(
            [
                {
                    "role": "assistant_tool_call",
                    "toolUseId": f"skill-{case_id}",
                    "toolName": "load_skill",
                    "input": {"name": "stress-contract"},
                },
                {
                    "role": "tool_result",
                    "toolUseId": f"skill-{case_id}",
                    "toolName": "load_skill",
                    "content": (
                        f"SKILL: stress-contract\n{_marker(case_id, 0, 'SKILL')}\n"
                        + "protected instruction " * 220
                    ),
                    "isError": False,
                },
            ]
        )
    for index in range(scale):
        content = (
            marker_value if index == positions[layout] else f"generated-{round_index}-{index}"
        )
        messages.append(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": content + " bounded filler" * 120,
            }
        )
    call_id = f"pair-{case_id}-{round_index}"
    messages.extend(
        [
            {
                "role": "assistant_tool_call",
                "toolUseId": call_id,
                "toolName": "read_file",
                "input": {"path": "generated.txt"},
            },
            {
                "role": "tool_result",
                "toolUseId": call_id,
                "toolName": "read_file",
                "content": "generated tool evidence " * 160,
                "isError": False,
            },
        ]
    )
    messages.append(
        {
            "role": "user",
            "content": _marker(case_id, round_index, "LATEST"),
        }
    )
    return messages


def _run_case(scale: int, rounds: int, layout: str) -> dict[str, object]:
    case_id = f"s{scale}-r{rounds}-{layout}"
    ledger_marker = _marker(case_id, 0, "LEDGER")
    skill_marker = _marker(case_id, 0, "SKILL")
    current: list[dict[str, Any]] = [
        {"role": "system", "content": "Deterministic stress fixture."},
        {
            "role": "system",
            "content": ledger_marker,
            "_task_ledger": True,
        },
    ]
    uncompacted = list(current)
    markers: list[str] = []
    summarizer = _MarkerReplaySummarizer()
    dispatcher = AutoCompactDispatcher(
        context_window=100_000,
        config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
        estimate_fn=estimate_message_tokens,
        summary_generator=summarizer,
    )
    round_savings: list[int] = []
    effective_rounds = 0
    for round_index in range(rounds):
        additions = _round_messages(
            case_id=case_id,
            round_index=round_index,
            scale=scale,
            layout=layout,
            markers=markers,
        )
        current.extend(additions)
        uncompacted.extend(additions)
        result = dispatcher.dispatch(current, force_full=True)
        effective_rounds += int(result.effective)
        round_savings.append(result.tokens_freed)
        current = list(result.messages)

    rendered = "\n".join(str(message.get("content", "")) for message in current)
    compacted_tokens = sum(estimate_message_tokens(message) for message in current)
    uncompacted_tokens = sum(
        estimate_message_tokens(message) for message in uncompacted
    )
    latest_marker = _marker(case_id, rounds - 1, "LATEST")
    marker_recall = sum(marker in rendered for marker in markers) / len(markers)
    latest_retained = any(
        message.get("role") == "user" and message.get("content") == latest_marker
        for message in current
    )
    skill_retained = any(
        message.get("role") == "tool_result"
        and message.get("toolName") == "load_skill"
        and skill_marker in str(message.get("content", ""))
        for message in current
    )
    ledger_retained = any(
        message.get("_task_ledger") is True
        and ledger_marker in str(message.get("content", ""))
        for message in current
    )
    chain_retained = rounds == 1 or summarizer.chain_observations >= rounds - 1
    tool_pairs_intact = _tool_pairs_intact(current)
    passed = all(
        (
            effective_rounds == rounds,
            marker_recall == 1.0,
            latest_retained,
            skill_retained,
            ledger_retained,
            chain_retained,
            tool_pairs_intact,
            compacted_tokens < uncompacted_tokens,
        )
    )
    return {
        "id": case_id,
        "scaleMessagesPerRound": scale,
        "rounds": rounds,
        "layout": layout,
        "passed": passed,
        "markerRecall": marker_recall,
        "latestUserRetained": latest_retained,
        "loadedSkillRetained": skill_retained,
        "taskLedgerRetained": ledger_retained,
        "summaryChainRetained": chain_retained,
        "toolPairsIntact": tool_pairs_intact,
        "effectiveRounds": effective_rounds,
        "uncompactedEstimatedTokens": uncompacted_tokens,
        "compactedEstimatedTokens": compacted_tokens,
        "estimatedTokenReduction": uncompacted_tokens - compacted_tokens,
        "estimatedPercentReduction": (
            100.0 * (uncompacted_tokens - compacted_tokens) / uncompacted_tokens
        ),
        "roundEstimatedTokensFreed": round_savings,
    }


def build_report() -> dict[str, object]:
    fixture = ROOT / "tests" / "fixtures" / "agent_quality" / "compaction-fidelity.json"
    frozen_gate = evaluate_compaction_fidelity(fixture)
    cases = [
        _run_case(scale, rounds, layout)
        for scale in SCALES
        for rounds in ROUNDS
        for layout in LAYOUTS
    ]
    reductions = [float(case["estimatedPercentReduction"]) for case in cases]
    return {
        "schemaVersion": 1,
        "suiteId": "minicode-context-compaction-deterministic-stress-2026-08-25-v1",
        "productionSourceSha256": hashlib.sha256(
            (ROOT / "minicode" / "context_compactor.py").read_bytes()
        ).hexdigest(),
        "frozenFidelityFixture": fixture.relative_to(ROOT).as_posix(),
        "frozenFidelityGate": frozen_gate,
        "stressMatrix": {
            "caseCount": len(cases),
            "scales": list(SCALES),
            "rounds": list(ROUNDS),
            "layouts": list(LAYOUTS),
            "passedCases": sum(case["passed"] is True for case in cases),
            "markerRecall": statistics.fmean(
                float(case["markerRecall"]) for case in cases
            ),
            "latestUserRetentionRate": statistics.fmean(
                case["latestUserRetained"] is True for case in cases
            ),
            "loadedSkillRetentionRate": statistics.fmean(
                case["loadedSkillRetained"] is True for case in cases
            ),
            "taskLedgerRetentionRate": statistics.fmean(
                case["taskLedgerRetained"] is True for case in cases
            ),
            "summaryChainRetentionRate": statistics.fmean(
                case["summaryChainRetained"] is True for case in cases
            ),
            "toolPairIntegrityRate": statistics.fmean(
                case["toolPairsIntact"] is True for case in cases
            ),
            "estimatedPercentReduction": {
                "mean": statistics.fmean(reductions),
                "median": statistics.median(reductions),
                "min": min(reductions),
                "max": max(reductions),
            },
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0 if report["stressMatrix"]["passedCases"] == len(report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
