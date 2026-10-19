"""Recovery uses real durable receipts and a killed interpreter, never HTTP."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

from scripts import run_three_arm_real as live


_LEDGER_WRITER = """
from dataclasses import asdict
import json, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.evaluation_transport import SharedLedger, TransportConfig

config = TransportConfig(**json.loads(sys.argv[2]))
ledger = SharedLedger(config)
completed = ledger.start({'subagentId': 'plan-1', 'role': 'plan'}, 200)
ledger.update(completed, {
    'event': 'completed',
    'usage': {'source': 'provider', 'input_tokens': 100, 'output_tokens': 10,
              'cache_read_tokens': 20},
})
received = ledger.start({'subagentId': 'executor-1', 'role': 'executor'}, 200)
ledger.update(received, {
    'usage': {'source': 'provider', 'input_tokens': 40, 'output_tokens': 5,
              'cache_read_tokens': 10},
    'providerRequestId': 'receipt-before-kill',
})
ledger.start({'role': 'parent'}, 300)
print('durable', flush=True)
signal.pause()
"""


def _fixture(tmp_path: Path) -> tuple[dict, Path, Path]:
    trial = {"id": "fixture-r1-auto", "case": "fixture", "repeat": 1,
             "arm": "auto"}
    root = tmp_path / "private" / trial["id"]
    root.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "value.py").write_text("VALUE = 1\n")
    live.write_json(root / "workspace.json", {"path": str(workspace)})
    live.write_json(root / "baseline-files.json", live.workspace_files(workspace))
    live.write_json(tmp_path / "tasks.json", {"cases": [{
        "id": "fixture", "category": "recovery", "authorized_paths": ["value.py"],
        "oracle_source": (
            "from pathlib import Path\nimport sys\n"
            "assert (Path(sys.argv[1]) / 'value.py').read_text() == 'VALUE = 2\\n'\n"
        ),
    }]})
    return trial, root, workspace


@pytest.mark.skipif(os.name != "posix", reason="guardian uses POSIX process groups")
def test_guardian_kill_recovers_receipts_inflight_scope_and_analysis_fields(
    tmp_path: Path,
) -> None:
    trial, root, workspace = _fixture(tmp_path)
    config = live.TransportConfig(
        ledger_path=str(root / "transport.sqlite"),
        expected_model="fixture-model",
        expected_route_sha256="a" * 64,
        deadline_epoch=time.time() + 60,
    )
    live.write_json(root / "transport-config.json", asdict(config))
    now = time.time()
    live.write_json(root / "collaboration.json", {
        "runs": [
            {"subagentId": "plan-1", "role": "plan", "startedEpoch": now - 2,
             "returnedEpoch": now - 1, "returned": True, "toolOk": True,
             "projected": {"outcome": "completed", "partial": False}},
            {"subagentId": "executor-1", "role": "executor",
             "startedEpoch": now - 1},
        ],
        "deliveredIds": ["plan-1"],
    })
    process = subprocess.Popen(
        [sys.executable, "-c", _LEDGER_WRITER, str(live.ROOT),
         json.dumps(asdict(config))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0], "writer did not start"
        assert process.stdout.readline().strip() == "durable"
        # Stop while one request has a receipt but no completion and another
        # admitted request has no provider usage at all.
        live.write_json(root / "execution-start.json", {
            "epoch": now - 3, "deadlineEpoch": time.time() - 0.1,
        })
        assert live.wait_trial_process(process, root) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)

    (workspace / "value.py").write_text("VALUE = 2\n")
    # Scope still needs auditing even though the agent produced no terminal
    # execution/transcript/subagents files and the behavioral oracle passes.
    (workspace / "forbidden.py").write_text("FORBIDDEN = True\n")
    row = live.finalize_trial(tmp_path, trial, process_code=process.returncode)
    assert row["errorClass"] == "HardTimeout"
    assert row["oracleSuccess"] and not row["scopePassed"] and not row["success"]
    assert row["unauthorizedChanges"] == ["forbidden.py"]
    assert row["changedFiles"] == ["forbidden.py", "value.py"]
    assert row["modelCalls"] == 3 and row["inFlightCalls"] == 2
    assert row["unknownUsageCalls"] == 1 and row["failedCalls"] == 0
    assert (row["inputTokens"], row["outputTokens"], row["totalTokens"]) == (
        140, 15, 155,
    )
    assert row["cacheReadTokens"] == 30
    assert not row["usageComplete"] and not row["tokenUsageComplete"]
    assert not row["cacheUsageComplete"] and row["estimatedCostCny"] is None
    assert row["knownCostLowerBoundCny"] == pytest.approx(
        (110 * 0.8 + 30 * 0.23 + 15 * 2.8) / 1_000_000,
    )
    assert row["leafModelCalls"] == 2 and row["actualLeafRuns"] == 1
    assert row["deliveredLeafResults"] == row["readAnalysisLeafRuns"] == 1
    assert row["successfulLeafRuns"] == 1 and row["collaborationExecuted"]
    assert not row["collaborationProtocolSatisfied"]
    assert row["implementationLeafRuns"] == 0 and row["delegationAttempts"] == 0
    assert row["durationSeconds"] >= 3 and row["judgeSeconds"] > 0
    assert row["budget"] is None and row["taskErrorCodes"] == []
    assert row["approvalDecisions"] == [] and not row["ledgerDamaged"]
    assert live.read_json(tmp_path / "trials" / f"{trial['id']}.json") == row

    recovered = live.read_json(root / "transport-summary.json")
    assert recovered["chargedTokens"] == 455
    assert recovered["providerUsageCalls"] == 2 and recovered["completed"] == 1
    received = recovered["requests"][1]
    assert received["event"] == "started"
    assert received["providerRequestId"] == "receipt-before-kill"
    assert received["usage"]["output_tokens"] == 5
    assert recovered["requests"][2]["chargedTokens"] == 300
    # JSONL intentionally preserves the start/terminal export shape. SQLite
    # and its recovered snapshot carry nonterminal receipts authoritatively.
    exported = [json.loads(line) for line in
                (root / "calls.jsonl").read_text().splitlines()]
    assert sum(event["event"] == "started" for event in exported) == 3
    assert sum(event["event"] == "completed" for event in exported) == 1


def test_truncated_collaboration_evidence_cannot_erase_trial_row(
    tmp_path: Path,
) -> None:
    trial, root, workspace = _fixture(tmp_path)
    (workspace / "value.py").write_text("VALUE = 2\n")
    # A worker killed inside a non-atomic write can leave this exact shape.
    (root / "collaboration.json").write_text('{"runs": [')
    row = live.finalize_trial(tmp_path, trial, process_code=-signal.SIGKILL)
    assert row["oracleSuccess"] and row["scopePassed"]
    assert not row["collaborationExecuted"]
    assert not row["usageComplete"] and row["estimatedCostCny"] is None
    assert (tmp_path / "trials" / f"{trial['id']}.json").is_file()
