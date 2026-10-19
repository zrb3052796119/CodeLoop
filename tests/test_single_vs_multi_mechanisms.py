"""Synthetic request receipts for post-hoc diagnostics; no live API calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import analyze_single_vs_multi_mechanisms as mechanism


def receipt(call, *, tokens=100, source="provider"):
    return {
        "event": "completed",
        "call": call,
        "usage": {"source": source, "input_tokens": tokens, "output_tokens": 10},
        "response": "PRIVATE_PAYLOAD_MUST_NOT_ESCAPE",
        "toolCalls": [],
    }


def test_first_completion_missing_usage_is_not_replaced_by_later_usable_call():
    records = [
        {"event": "started", "call": 1},
        receipt(1, source="estimated"),
        {"event": "started", "call": 2},
        receipt(2),
    ]
    projection = mechanism.request_projection(records)
    assert projection["firstCompletedInputTokens"] is None
    assert projection["firstCompletedCall"] == 1
    assert projection["requestsMissingProviderUsage"] == 1
    assert projection["completedRequestsMissingProviderUsage"] == 1
    assert "PRIVATE_PAYLOAD" not in json.dumps(projection)


def test_first_completion_is_labeled_when_first_started_request_failed():
    records = [
        {"event": "started", "call": 1},
        {"event": "failed", "call": 1, "errorClass": "ReadTimeout"},
        {"event": "started", "call": 2},
        receipt(2, tokens=200),
        {"event": "started", "call": 3},
    ]
    projection = mechanism.request_projection(records)
    assert projection["firstCompletedInputTokens"] == 200
    assert projection["firstCompletionWasFirstStartedRequest"] is False
    assert projection["timeoutRequests"] == 1
    assert projection["failedRequests"] == 1
    assert projection["requestsMissingProviderUsage"] == 2
    assert projection["startedRequestsWithoutTerminalEvent"] == 1


def test_paired_diagnostics_keep_missing_members_and_task_repeat_identity(tmp_path):
    schedule, projections = [], []
    for case, repeat, arm, tokens in [
        ("case-a", 1, "single", 100),
        ("case-a", 1, "multi", 300),
        ("case-a", 2, "single", 200),
        ("case-a", 2, "multi", None),
    ]:
        assignment = {
            "id": f"{case}-{repeat}-{arm}",
            "case": case,
            "repeat": repeat,
            "arm": arm,
        }
        schedule.append(assignment)
        if tokens is None:
            continue
        ledger = tmp_path / "private" / assignment["id"] / "calls.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text(
            "\n".join(
                json.dumps(r)
                for r in [{"event": "started", "call": 1}, receipt(1, tokens=tokens)]
            )
        )
        row = {
            **assignment,
            "success": True,
            "usageComplete": True,
            "errorClass": None,
            "delegationAttempts": 0,
            "category": "synthetic",
        }
        projections.append(mechanism.read_trial_projection(tmp_path, assignment, row))
    summary = mechanism.mechanism_summary(projections, schedule)
    assert summary["complete"] is False
    assert summary["observedTrials"] == 3
    assert summary["pairedInputDelta"]["n"] == 1
    assert summary["pairedInputDelta"]["mean"] == 200
    assert summary["firstCompletedInputTokenPairs"][1]["deltaMultiMinusSingle"] is None
    assert summary["firstCompletedInputTokenPairs"][1]["multiTrialPresent"] is False
    assert "PRIVATE_PAYLOAD" not in json.dumps(summary)
    assert "post-hoc" in summary["analysisType"]


def test_cli_incomplete_requires_explicit_partial_and_does_not_touch_main_artifacts(
    tmp_path,
):
    manifest = {
        "schedule": [
            {
                "id": "synthetic-1-single",
                "case": "synthetic",
                "repeat": 1,
                "arm": "single",
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    before = manifest_path.read_bytes()
    command = [
        sys.executable,
        str(Path(mechanism.__file__).resolve()),
        "--input",
        str(tmp_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert not (tmp_path / "exploratory-mechanisms.json").exists()
    result = subprocess.run(
        [*command, "--allow-partial"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    output = json.loads((tmp_path / "exploratory-mechanisms.json").read_text())
    assert output["complete"] is False
    assert output["observedTrials"] == 0
    assert output["pairedInputDelta"]["mean"] is None
    assert manifest_path.read_bytes() == before
