"""Replay sealed synthetic prompts against the current production intent parser.

Run from any directory with Python 3.11+. No Agent, tool execution, or provider
is invoked. Expected labels are never used as input to the parser.
"""

from __future__ import annotations

import hashlib
import json
import signal
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timeout_handler(signum, frame):
    raise TimeoutError("Parser exceeded 10 seconds for one prompt")


def score(case, intent):
    issues = []
    primary_ok = intent.intent_type.value in case["expected"]
    if not primary_ok:
        issues.append(f"primary: expected {case['expected']}, got {intent.intent_type.value}")
    affirmed = intent.affirmed_operations
    present = {op.intent_type.value for op in affirmed}
    negated = {op.intent_type.value for op in intent.negated_operations}
    for label in case.get("required_affirmed", []):
        if label not in present:
            issues.append(f"missing affirmative intent: {label}")
    for label in case.get("forbidden_affirmed", []):
        if label in present:
            issues.append(f"unexpected affirmative intent: {label}")
    for label in case.get("required_negated", []):
        if label not in negated:
            issues.append(f"missing negated intent: {label}")
    for label, actions in case.get("expected_actions", {}).items():
        matching = [op for op in affirmed if op.intent_type.value == label]
        if not any(op.action_type.value in actions for op in matching):
            issues.append(f"action for {label}: expected one of {actions}")
    for op in affirmed:
        if op.action_type.value in case.get("forbidden_actions", []):
            issues.append(f"forbidden affirmative action: {op.intent_type.value}/{op.action_type.value}")
    for kind, values in case.get("expected_entities", {}).items():
        for value in values:
            if value not in intent.entities.get(kind, []):
                issues.append(f"missing entity {kind}: {value}")
    if "expected_read_only" in case and intent.is_read_only() != case["expected_read_only"]:
        issues.append(f"read_only: expected {case['expected_read_only']}, got {intent.is_read_only()}")
    if "expected_order" in case:
        cursor = 0
        for op in affirmed:
            if cursor < len(case["expected_order"]) and op.intent_type.value == case["expected_order"][cursor]:
                cursor += 1
        if cursor != len(case["expected_order"]):
            issues.append(f"missing requested operation order: {case['expected_order']}")
    return primary_ok, issues


def main():
    files = [HERE / "routine_cases.json", HERE / "independent_cases.json"]
    cases = [case for path in files for case in read_json(path)]
    assert len(cases) == 50 and len({c["id"] for c in cases}) == 50
    # Freeze authored questions, expectations and source before the first parse.
    source_files = [ROOT / "minicode" / name for name in (
        "intent_parser.py", "intent_constraints.py", "intent_negation.py",
        "intent_semantic_classifier.py", "skill_routing_turn.py",
    )]
    freeze = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "semantic_mode": "disabled; confirmed current runtime configuration",
        "files": {str(p.relative_to(ROOT)): digest(p) for p in files + source_files + [Path(__file__)]},
        "case_count": len(cases),
        "scoring": "primary accepted labels; all explicitly pre-labeled operation/polarity/action/entity/read-only/order requirements",
        "scope": "synthetic intent parsing; no downstream Skill ranking, real tool execution or model call",
    }
    freeze_path = HERE / "freeze.json"
    if freeze_path.exists():
        raise RuntimeError("freeze.json exists; preserve the first run and use a new output directory for replays")
    write_json(freeze_path, freeze)
    from minicode.intent_parser import IntentContext, IntentParser

    init_start = time.perf_counter()
    parser = IntentParser()
    init_ms = (time.perf_counter() - init_start) * 1000
    results = []
    signal.signal(signal.SIGALRM, timeout_handler)
    for case in cases:
        context_spec = case.get("context", {})
        context = IntentContext.from_messages(
            context_spec.get("recent_turns", []),
            active_task=context_spec.get("active_task", ""),
        )
        started = time.perf_counter()
        try:
            signal.setitimer(signal.ITIMER_REAL, 10)
            intent = parser.parse(case["text"], context=context)
            elapsed_ms = (time.perf_counter() - started) * 1000
            signal.setitimer(signal.ITIMER_REAL, 0)
            primary_ok, issues = score(case, intent)
            result = {
                "case": case, "primary_ok": primary_ok, "all_labeled_checks_ok": not issues,
                "issues": issues, "elapsed_ms": elapsed_ms,
                "actual": intent.to_detailed_dict(), "read_only": intent.is_read_only(),
            }
        except Exception as exc:
            result = {
                "case": case, "primary_ok": False, "all_labeled_checks_ok": False,
                "issues": [f"parser error: {type(exc).__name__}: {exc}"],
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "actual": None,
            }
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        results.append(result)
        # Retain partial results even if a later case fails.
        write_json(HERE / "results.json", results)
        ops = ", ".join(f"{o['polarity']}:{o['intent_type']}/{o['action_type']}" for o in (result["actual"] or {}).get("operations", []))
        print(f"{case['id']} {'PASS' if result['all_labeled_checks_ok'] else 'FAIL'} {ops} | {'; '.join(result['issues'])}", flush=True)
    durations = sorted(r["elapsed_ms"] for r in results)
    grouped = defaultdict(list)
    for result in results:
        grouped[result["case"]["category"]].append(result)
    summary = {
        "total": len(results),
        "primary_correct": sum(r["primary_ok"] for r in results),
        "all_labeled_checks_correct": sum(r["all_labeled_checks_ok"] for r in results),
        "parser_exceptions": sum(r["actual"] is None for r in results),
        "groups": {k: {"count": len(v), "primary_correct": sum(r["primary_ok"] for r in v), "all_labeled_checks_correct": sum(r["all_labeled_checks_ok"] for r in v)} for k, v in grouped.items()},
        "parser_init_ms": init_ms,
        "latency_ms": {"median": statistics.median(durations), "p95_nearest_rank": durations[47], "max": max(durations), "total": sum(durations)},
        "semantic_status": dict(Counter((r["actual"] or {}).get("diagnostics", {}).get("semantic_status", "exception") for r in results)),
        "sources_unchanged": all(digest(ROOT / name) == old_hash for name, old_hash in freeze["files"].items()),
    }
    write_json(HERE / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
