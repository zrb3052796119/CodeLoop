"""Replay frozen intent cases against either real source snapshot or candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
OLD = ROOT / "artifacts/intent-evaluation-2026-09-07"
from score_loader import load_original_score
score = load_original_score()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def validate_module_sources(source):
    imported = {}
    for name, module in sys.modules.items():
        if name == "minicode" or name.startswith("minicode."):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(source), (name, str(path), str(source))
            imported[name] = str(path.relative_to(source))
    return imported


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=ROOT)
    ap.add_argument("--dataset", choices=("old50", "prior40", "priorfresh40", "priorfresh50", "fresh50"), default="old50")
    ap.add_argument("--output", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--max-output", type=int)
    ap.add_argument("--ids")
    args = ap.parse_args()
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    import minicode.intent_parser as parser_module
    from minicode.intent_parser import IntentContext, parse_intent
    assert Path(parser_module.__file__).resolve().is_relative_to(source)
    validate_module_sources(source)
    scorer = score
    fresh_module = None
    scoring_dependency = OLD / "run_evaluation.py"
    if args.dataset == "prior40":
        import importlib.util
        scoring_dependency = ROOT / ".repair-work/intent-phase1/holdout_scoring.py"
        spec = importlib.util.spec_from_file_location("phase1_holdout_score", scoring_dependency)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert Path(module.__file__).resolve() == scoring_dependency.resolve()
        scorer = module.score
    elif args.dataset == "priorfresh40":
        import importlib.util
        scoring_dependency = ROOT / ".repair-work/intent-phase2/score_holdout.py"
        spec = importlib.util.spec_from_file_location("phase2_holdout_score", scoring_dependency)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert Path(module.__file__).resolve() == scoring_dependency.resolve()
        scorer = module.score
        assert sha(ROOT / ".repair-work/intent-phase2/holdout.json") == module.HOLDOUT_SHA256
        assert sha(ROOT / ".repair-work/intent-phase2/holdout-authorship.md") == module.AUTHORSHIP_SHA256
    elif args.dataset == "priorfresh50":
        import importlib.util
        scoring_dependency = ROOT / ".repair-work/intent-phase3/score_holdout.py"
        spec = importlib.util.spec_from_file_location("phase3_holdout_score", scoring_dependency)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert Path(module.__file__).resolve() == scoring_dependency.resolve()
        scorer = module.score
        assert sha(ROOT / ".repair-work/intent-phase3/holdout.json") == module.HOLDOUT_SHA256
        assert sha(ROOT / ".repair-work/intent-phase3/holdout-authorship.md") == module.AUTHORSHIP_SHA256
    elif args.dataset == "fresh50":
        import importlib.util
        scoring_dependency = WORK / "score_holdout.py"
        spec = importlib.util.spec_from_file_location("phase4_holdout_score", scoring_dependency)
        fresh_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = fresh_module
        spec.loader.exec_module(fresh_module)
        assert Path(fresh_module.__file__).resolve() == scoring_dependency.resolve()
        scorer = fresh_module.score
    cases_files = {
        "old50": [OLD / "routine_cases.json", OLD / "independent_cases.json"],
        "prior40": [ROOT / ".repair-work/intent-phase1/holdout.json"],
        "priorfresh40": [ROOT / ".repair-work/intent-phase2/holdout.json"],
        "priorfresh50": [ROOT / ".repair-work/intent-phase3/holdout.json"],
        "fresh50": [WORK / "holdout.json"],
    }[args.dataset]
    if args.dataset == "fresh50":
        assert sha(WORK / "holdout.json") == fresh_module.HOLDOUT_SHA256
        assert sha(WORK / "holdout-authorship.md") == fresh_module.AUTHORSHIP_SHA256
    cases = [c for p in cases_files for c in json.loads(p.read_text())]
    assert len(cases) == (50 if args.dataset in {"old50", "priorfresh50", "fresh50"} else 40)
    assert len({case["id"] for case in cases}) == len(cases)
    if args.dataset == "fresh50":
        for case in cases:
            fresh_module.validate_case(case)
    if args.ids:
        ids = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in ids]
        assert len(cases) == len(ids)
    out = WORK / args.output
    out.mkdir(parents=True, exist_ok=False)
    source_hashes = {str(p.relative_to(source)): sha(p) for p in (source / "minicode").rglob("*.py")}
    freeze = {"source_root": str(source), "source_hashes": source_hashes,
              "case_hashes": {str(p): sha(p) for p in cases_files}, "scorer_sha256": sha(OLD / "run_evaluation.py"),
              "runner_sha256": sha(Path(__file__)), "dataset": args.dataset, "count": len(cases),
              "live": args.live, "timeout_override": args.timeout, "max_output_override": args.max_output,
              "python": sys.version}
    freeze["score_loader_sha256"] = sha(WORK / "score_loader.py")
    freeze["scoring_dependency"] = str(scoring_dependency)
    freeze["scoring_dependency_sha256"] = sha(scoring_dependency)
    adapter = None
    calibration_path = None
    calibration_hash = None
    if args.live:
        from minicode.config import load_runtime_config
        from minicode.agent_budget import AgentTurnBudget
        from minicode.intent_semantic_classifier import build_semantic_intent_adapter
        from minicode.model_registry import create_model_adapter
        from minicode.run_journal import RunJournal
        from minicode.run_lifecycle import observe_run
        from minicode.turn_cancellation import TurnCancellationToken
        from minicode.tooling import ToolRegistry
        runtime = load_runtime_config(ROOT)
        config = dict(runtime["intentSemanticClassifier"])
        if config.get("calibrationPath"):
            calibration_path = Path(config["calibrationPath"]).expanduser().resolve()
            calibration_hash = sha(calibration_path) if calibration_path.is_file() else None
        config["enabled"] = True
        if args.timeout is not None:
            config["timeoutSeconds"] = args.timeout
        if args.max_output is not None:
            config["maxOutputTokens"] = args.max_output
        runtime["intentSemanticClassifier"] = config
        model = create_model_adapter(runtime["model"], ToolRegistry([]), runtime=runtime)
        assert type(model).__name__ != "MockModelAdapter"
        adapter = build_semantic_intent_adapter(model, runtime=runtime)
        assert adapter is not None
        journal = RunJournal(ROOT, data_dir=out / "journal")
        freeze["runtime"] = {"model": runtime["model"], "provider": runtime["provider"],
            "intentSemanticClassifier": {k: v for k, v in config.items() if k != "calibrationPath"},
            "supports_json_object": getattr(model, "supports_json_object", False),
            "supported_reasoning_efforts": getattr(model, "supported_reasoning_efforts", ())}
        freeze["runtime"]["calibration_artifact"] = {
            "configured": calibration_path is not None,
            "path_sha256": hashlib.sha256(str(calibration_path).encode()).hexdigest() if calibration_path else None,
            "content_sha256": calibration_hash,
        }
    freeze["imported_modules"] = validate_module_sources(source)
    write(out / "freeze.json", freeze)
    results = []
    wall_started = time.perf_counter()
    for case in cases:
        ctx = case.get("context", {})
        context = IntentContext.from_messages(ctx.get("recent_turns", []), active_task=ctx.get("active_task", ""))
        events = []
        call_context = None
        run_id = None
        if args.live:
            observation_cm = observe_run(workspace=ROOT, source="unknown", title="intent phase4 " + case["id"], journal_factory=lambda workspace: journal)
            observation = observation_cm.__enter__()
            assert observation.observation_active
            budget = AgentTurnBudget.from_runtime(runtime)
            call_context = {"agent_budget": budget, "event_sink": observation,
                            "cancellation_token": TurnCancellationToken("turn_" + uuid.uuid4().hex),
                            "deadline_monotonic": time.monotonic() + 60}
            run_id = observation.run_id
        started = time.perf_counter()
        try:
            intent = parse_intent(case["text"], context=context, semantic_adapter=adapter, call_context=call_context)
            elapsed = (time.perf_counter() - started) * 1000
            primary_ok, issues = scorer(case, intent)
            result = {"case": case, "primary_ok": primary_ok, "all_labeled_checks_ok": not issues,
                      "issues": issues, "actual": intent.to_detailed_dict(), "read_only": intent.is_read_only(), "elapsed_ms": elapsed}
            if args.dataset != "old50":
                raw_primary, raw_issues = score(case, intent)
                result["original_conjunctive_scoring"] = {"primary_ok": raw_primary, "all_labeled_checks_ok": not raw_issues, "issues": raw_issues}
        except Exception as error:
            result = {"case": case, "primary_ok": False, "all_labeled_checks_ok": False,
                      "issues": [type(error).__name__], "actual": None, "elapsed_ms": (time.perf_counter() - started) * 1000}
        finally:
            if args.live:
                observation_cm.__exit__(None, None, None)
        if args.live:
            events = [{"type": e.type, "payload": e.payload} for e in journal.list_events(run_id, limit=100).items if e.type.startswith("model.")]
            result.update({"run_id": run_id, "budget": budget.snapshot().to_dict()})
        result["events"] = events
        results.append(result)
        write(out / "results.json", results)
        a = result["actual"] or {}
        print(case["id"], "PASS" if result["all_labeled_checks_ok"] else "FAIL", a.get("intent_type"), a.get("diagnostics", {}).get("semantic_status"), f"{result['elapsed_ms']:.1f}ms", flush=True)
    durations = sorted(r["elapsed_ms"] for r in results)
    all_events = [e for r in results for e in r["events"]]
    called = [r for r in results if any(e["type"] == "model.started" for e in r["events"])]
    summary = {"total": len(results), "primary_correct": sum(r["primary_ok"] for r in results),
        "all_labeled_checks_correct": sum(r["all_labeled_checks_ok"] for r in results),
        "failed_ids": [r["case"]["id"] for r in results if not r["all_labeled_checks_ok"]],
        "semantic_status": dict(Counter((r["actual"] or {}).get("diagnostics", {}).get("semantic_status", "exception") for r in results)),
        "event_counts": dict(Counter(e["type"] for e in all_events)),
        "called_ids": [r["case"]["id"] for r in called],
        "called_median_ms": statistics.median(r["elapsed_ms"] for r in called) if called else None,
        "latency_ms": {"median": statistics.median(durations), "p95": durations[math.ceil(len(durations) * .95) - 1], "max": max(durations)},
        "wall_seconds": time.perf_counter() - wall_started,
        "source_unchanged": set(source_hashes) == {str(p.relative_to(source)) for p in (source / "minicode").rglob("*.py")} and all(sha(source / p) == h for p, h in source_hashes.items()),
        "runner_unchanged": sha(Path(__file__)) == freeze["runner_sha256"],
        "scorer_unchanged": sha(OLD / "run_evaluation.py") == freeze["scorer_sha256"] and (
            sha(scoring_dependency) == freeze["scoring_dependency_sha256"] and sha(WORK / "score_loader.py") == freeze["score_loader_sha256"]
        ),
        "module_sources_valid": bool(validate_module_sources(source)),
        "calibration_unchanged": calibration_path is None or (sha(calibration_path) if calibration_path.is_file() else None) == calibration_hash,
        "cases_unchanged": all(sha(Path(p)) == h for p, h in freeze["case_hashes"].items())}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
