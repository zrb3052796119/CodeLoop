"""Paired live evaluation using production semantic fallback and telemetry."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent
ROOT = BASELINE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BASELINE))
from run_evaluation import score

from minicode.agent_budget import AgentTurnBudget
from minicode.config import load_runtime_config
from minicode.intent_parser import IntentContext, parse_intent
from minicode.intent_semantic_classifier import build_semantic_intent_adapter
from minicode.model_registry import create_model_adapter
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.turn_cancellation import TurnCancellationToken


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RecordedObservation:
    """Forward every event to the actual isolated durable RunObservation."""

    def __init__(self, observation):
        self.observation = observation
        self.events = []

    @property
    def observation_active(self):
        return self.observation.observation_active

    def emit(self, event_type, *, step=None, payload=None):
        self.observation.emit(event_type, step=step, payload=payload)
        self.events.append({"event_type": event_type, "payload": dict(payload or {})})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--preflight", action="store_true")
    args = ap.parse_args()
    assert Path(args.output).name == args.output
    run_dir = HERE / args.output
    frozen = json.loads((BASELINE / "freeze.json").read_text())
    assert all(digest(ROOT / name) == old for name, old in frozen["files"].items()), "Baseline/source changed"
    baseline_results = json.loads((BASELINE / "results.json").read_text())
    cases = [r["case"] for r in baseline_results]
    assert len(cases) == 50
    runtime = load_runtime_config(str(ROOT))
    semantic = dict(runtime["intentSemanticClassifier"])
    original_enabled = semantic["enabled"]
    semantic["enabled"] = True
    if args.timeout is not None:
        semantic["timeoutSeconds"] = args.timeout
    runtime["intentSemanticClassifier"] = semantic
    tools = ToolRegistry([])
    model = create_model_adapter(model=runtime["model"], tools=tools, runtime=runtime)
    assert type(model).__name__ != "MockModelAdapter"
    adapter = build_semantic_intent_adapter(model, runtime=runtime)
    assert adapter is not None
    transport_field = {"custom": "customBaseUrl", "openai": "openaiBaseUrl", "openrouter": "openrouterBaseUrl"}.get(runtime.get("provider"), "baseUrl")
    safe_config = {
        "provider": runtime.get("provider"), "model": runtime.get("model"),
        "endpoint_host": urlsplit(runtime.get(transport_field, "")).hostname,
        "adapter_class": type(model).__name__, "configured_enabled_before_run": original_enabled,
        "intentSemanticClassifier": {k: v for k, v in semantic.items() if k != "calibrationPath"},
        "calibration_path_present": bool(semantic.get("calibrationPath")),
        "enableThinking": runtime.get("enableThinking"),
        "sample_count": 50, "budget_per_case": AgentTurnBudget.from_runtime(runtime).snapshot().to_dict(),
        "effective_changes": {"enabled": True, **({"timeoutSeconds": args.timeout} if args.timeout is not None else {})},
    }
    print(json.dumps(safe_config, ensure_ascii=False), flush=True)
    if args.preflight:
        return
    run_dir.mkdir(exist_ok=True)
    claim = run_dir / "freeze.json"
    with claim.open("x", encoding="utf-8") as stream:
        json.dump({
            "started_at": datetime.now(timezone.utc).isoformat(),
            "config": safe_config, "baseline_freeze_sha256": digest(BASELINE / "freeze.json"),
            "baseline_results_sha256": digest(BASELINE / "results.json"),
            "runner_sha256": digest(Path(__file__)), "source_hashes": frozen["files"],
            "python": sys.version,
        }, stream, ensure_ascii=False, indent=2)
    results = []
    run_start = time.perf_counter()
    journal = RunJournal(ROOT, data_dir=run_dir / "journal")
    for baseline, case in zip(baseline_results, cases):
        budget = AgentTurnBudget.from_runtime(runtime)
        cancellation = TurnCancellationToken("turn_" + uuid.uuid4().hex)
        context_spec = case.get("context", {})
        context = IntentContext.from_messages(context_spec.get("recent_turns", []), active_task=context_spec.get("active_task", ""))
        with observe_run(workspace=ROOT, source="unknown", title="intent semantic evaluation " + case["id"], journal_factory=lambda workspace: journal) as observation:
            assert observation.observation_active, "Durable observation failed; no provider call allowed"
            sink = RecordedObservation(observation)
            started = time.perf_counter()
            try:
                intent = parse_intent(case["text"], context=context, semantic_adapter=adapter, call_context={
                    "agent_budget": budget, "event_sink": sink, "cancellation_token": cancellation,
                    "deadline_monotonic": time.monotonic() + 60,
                })
                elapsed_ms = (time.perf_counter() - started) * 1000
                primary_ok, issues = score(case, intent)
                result = {"case": case, "primary_ok": primary_ok, "all_labeled_checks_ok": not issues,
                          "issues": issues, "actual": intent.to_detailed_dict(), "read_only": intent.is_read_only(), "elapsed_ms": elapsed_ms}
            except Exception as error:
                result = {"case": case, "primary_ok": False, "all_labeled_checks_ok": False,
                          "issues": ["parser exception: " + type(error).__name__], "actual": None,
                          "elapsed_ms": (time.perf_counter() - started) * 1000}
            result.update({"events": sink.events, "budget": budget.snapshot().to_dict(), "run_id": observation.run_id,
                           "baseline_primary_ok": baseline["primary_ok"], "baseline_all_labeled_checks_ok": baseline["all_labeled_checks_ok"]})
        results.append(result)
        write_json(run_dir / "results.json", results)
        status = (result["actual"] or {}).get("diagnostics", {}).get("semantic_status", "exception")
        print(f"{case['id']} {'PASS' if result['all_labeled_checks_ok'] else 'FAIL'} semantic={status} ms={result['elapsed_ms']:.1f} calls={sum(e['event_type']=='model.started' for e in sink.events)}", flush=True)
    events = [e for r in results for e in r["events"]]
    durations = sorted(r["elapsed_ms"] for r in results)
    called = [r for r in results if any(e["event_type"] == "model.started" for e in r["events"])]
    summary = {
        "total": len(results), "primary_correct": sum(r["primary_ok"] for r in results),
        "all_labeled_checks_correct": sum(r["all_labeled_checks_ok"] for r in results),
        "primary_improvements": [r["case"]["id"] for r in results if r["primary_ok"] and not r["baseline_primary_ok"]],
        "primary_regressions": [r["case"]["id"] for r in results if not r["primary_ok"] and r["baseline_primary_ok"]],
        "improvements": [r["case"]["id"] for r in results if r["all_labeled_checks_ok"] and not r["baseline_all_labeled_checks_ok"]],
        "regressions": [r["case"]["id"] for r in results if not r["all_labeled_checks_ok"] and r["baseline_all_labeled_checks_ok"]],
        "semantic_status": dict(Counter((r["actual"] or {}).get("diagnostics", {}).get("semantic_status", "exception") for r in results)),
        "event_counts": dict(Counter(e["event_type"] for e in events)),
        "called_case_ids": [r["case"]["id"] for r in called],
        "token_totals": {key: sum(r["budget"][key] for r in results) for key in ("usedInputTokens", "usedOutputTokens", "usedTotalTokens")},
        "usage_sources": dict(Counter(e["payload"].get("usage", {}).get("source", "missing") for e in events if e["event_type"] == "model.completed")),
        "cost_status": dict(Counter(e["payload"].get("status", "missing") for e in events if e["event_type"] == "model.costed")),
        "latency_ms": {"median": statistics.median(durations), "p95_nearest_rank": durations[47], "max": max(durations), "total": sum(durations)},
        "called_latency_ms": {"median": statistics.median(r["elapsed_ms"] for r in called) if called else None},
        "wall_seconds": time.perf_counter() - run_start,
        "source_and_labels_unchanged": all(digest(ROOT / name) == old for name, old in frozen["files"].items()),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
