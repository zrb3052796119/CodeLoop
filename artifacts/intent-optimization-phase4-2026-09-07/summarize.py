"""Verify sealed before/after runs and summarize each dataset separately."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
PHASE1 = ROOT / ".repair-work/intent-phase1"
PHASE3 = ROOT / ".repair-work/intent-phase3"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runs():
    for dataset, prefix in (("old50", "old50"), ("prior40", "prior40"), ("priorfresh40", "priorfresh40"), ("priorfresh50", "fresh50"), ("fresh50", "fresh50")):
        for mode in ("disabled", "live"):
            before = PHASE3 / f"{prefix}-final-{mode}" if dataset != "fresh50" else WORK / f"fresh50-baseline-{mode}"
            after = WORK / f"{dataset}-final-{mode}"
            yield dataset, mode, before, after


def verify(path, rows, source):
    summary = read(path / "summary.json")
    freeze = read(path / "freeze.json")
    flags = [v for k, v in summary.items() if "unchanged" in k or k == "module_sources_valid"]
    assert flags and all(v is True for v in flags), path
    assert summary["total"] == len(rows) == freeze["count"], path
    assert summary["all_labeled_checks_correct"] == sum(r["all_labeled_checks_ok"] for r in rows), path
    assert set(freeze["source_hashes"]) == {str(p.relative_to(source)) for p in (source / "minicode").rglob("*.py")}, path
    for name, expected in freeze["source_hashes"].items():
        assert sha(source / name) == expected, (path, name)
    for name, expected in freeze["case_hashes"].items():
        assert sha(Path(name)) == expected, (path, name)
    original = ROOT / "artifacts/intent-evaluation-2026-09-07/run_evaluation.py"
    assert sha(original) == freeze["scorer_sha256"], path
    if "holdout_scorer_sha256" in freeze:
        assert sha(PHASE1 / "holdout_scoring.py") == freeze["holdout_scorer_sha256"], path
    if "scoring_dependency" in freeze:
        assert sha(Path(freeze["scoring_dependency"])) == freeze["scoring_dependency_sha256"], path
        assert sha(WORK / "score_loader.py") == freeze["score_loader_sha256"], path
    assert freeze["runner_sha256"] in {sha(WORK / "evaluate.py"), sha(PHASE3 / "evaluate.py")}, path


def metrics(rows):
    called = [r for r in rows if any(e["type"] == "model.started" for e in r.get("events", []))]
    completed = [e["payload"] for r in rows for e in r.get("events", []) if e["type"] == "model.completed"]
    readonly = [r for r in rows if r["case"].get("expected_read_only") is True]
    effects = {"create", "update", "delete", "execute", "move", "rename", "merge", "split"}
    return {
        "total": len(rows), "primary_correct": sum(r["primary_ok"] for r in rows),
        "strict_correct": sum(r["all_labeled_checks_ok"] for r in rows),
        "failed_ids": [r["case"]["id"] for r in rows if not r["all_labeled_checks_ok"]],
        "readonly_count": len(readonly),
        "readonly_effectful_ids": [r["case"]["id"] for r in readonly if any(
            o["polarity"] == "affirmed" and o["action_type"] in effects
            for o in (r["actual"] or {}).get("operations", [])
        )],
        "readonly_check_failed_ids": [r["case"]["id"] for r in readonly if r.get("read_only") is False],
        "semantic_status": dict(Counter((r["actual"] or {}).get("diagnostics", {}).get("semantic_status", "exception") for r in rows)),
        "model_call_attempts": len(called),
        "call_median_ms": statistics.median(r["elapsed_ms"] for r in called) if called else None,
        "event_counts": dict(Counter(e["type"] for r in rows for e in r.get("events", []))),
        "usage_sources": dict(Counter(e.get("usage", {}).get("source", "unavailable") for e in completed)),
    }


def main():
    output = {}
    for dataset, mode, before_path, after_path in runs():
        before, after = read(before_path / "results.json"), read(after_path / "results.json")
        verify(before_path, before, WORK / "baseline")
        verify(after_path, after, WORK / "evaluated-candidate")
        assert [r["case"] for r in before] == [r["case"] for r in after]
        if mode == "live":
            assert read(before_path / "freeze.json")["runtime"] == read(after_path / "freeze.json")["runtime"]
        output[f"{dataset}_{mode}"] = {
            "before": metrics(before), "after": metrics(after),
            "improved_ids": [a["case"]["id"] for b, a in zip(before, after) if not b["all_labeled_checks_ok"] and a["all_labeled_checks_ok"]],
            "regressed_ids": [a["case"]["id"] for b, a in zip(before, after) if b["all_labeled_checks_ok"] and not a["all_labeled_checks_ok"]],
        }
    (WORK / "comparison.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
