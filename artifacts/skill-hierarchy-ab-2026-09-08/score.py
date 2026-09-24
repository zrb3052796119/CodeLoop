"""Scoring for the hierarchical skill-discovery A/B experiment.

Reads raw per-run ``run.json`` files written by runner.py, joins them with
tasks.json and the per-variant catalog manifests (which carry the
variant-adjusted target locations), and emits per-arm metrics plus a per-task
A/B/B2 diff table.

Metrics per (scale, variant, arm):
- target directory discoverability: the target's directory appeared in a
  successful browse call, or a skill from it appeared in a browse/search
  result page;
- final correct load rate: a digest-verified load of the target (or an
  acceptable alternative);
- cross-directory rescue rate: correct load where the target directory was
  NOT part of the first discovery step (found via search or later browsing);
- direct-load rate: correct load with no discovery step at all (typical for
  explicit qualified-name tasks);
- negative false-trigger rate: any successful load on a negative task;
- body-delivery confirmation: loaded bodies whose sha256 matches the catalog
  digest and were appended to the conversation (always true in-window), plus
  confirmed handoff to a subsequent model call when the window allows it;
- mean steps / browse calls / search calls / load calls, mean latency, tokens.

Timeouts (and child errors) are reported separately and never counted as
correct or as clean non-triggers.

Usage:
    python score.py --live live [--catalogs catalogs] [--tasks tasks.json] [--json-out live/score-summary.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

WORK = Path(__file__).resolve().parent
ARMS = ("A", "B", "B2")

ERROR_STOP_REASONS = {"child_crashed", "child_error", "model_error"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score_run(run: dict, task: dict, manifest: dict) -> dict:
    """Reduce one raw run to scored facts."""
    timeout = bool(run.get("timeout")) or run.get("stop_reason") == "run_timeout"
    errored = run.get("stop_reason") in ERROR_STOP_REASONS
    tool_calls = run.get("tool_calls", [])
    loads = run.get("load_calls", [])
    ok_loads = [l for l in loads if l.get("ok")]
    verified_loads = [l for l in ok_loads if l.get("body_integrity_ok")]

    browsed_dirs: list[str] = []
    seen_dirs: list[str] = []
    seen_qnames: list[str] = []
    browse_calls = search_calls = 0
    first_discovery_dirs: list[str] | None = None
    for call in tool_calls:
        if not call.get("ok"):
            continue
        args = call.get("args") or {}
        if call["name"] == "browse_skill_directory":
            browse_calls += 1
            dirs = [str(d) for d in args.get("directory_names", [])]
            browsed_dirs.extend(dirs)
            if first_discovery_dirs is None:
                first_discovery_dirs = list(dirs)
        elif call["name"] == "search_skills":
            search_calls += 1
            preferred = [str(d) for d in (args.get("directory_names") or [])]
            if first_discovery_dirs is None:
                first_discovery_dirs = list(preferred)
        seen_qnames.extend(call.get("seen_qualified_names") or [])
        seen_dirs.extend(call.get("seen_directories") or [])

    record = {
        "task_id": run["task_id"],
        "scale": run["scale"],
        "variant": run["variant"],
        "arm": run["arm"],
        "negative": bool(task.get("negative")),
        "stop_reason": run.get("stop_reason"),
        "timeout": timeout,
        "errored": errored,
        "valid": not timeout and not errored,
        "steps": run.get("steps", 0),
        "browse_calls": browse_calls,
        "search_calls": search_calls,
        "load_calls": len(loads),
        "elapsed_ms": run.get("elapsed_ms", 0),
        "input_tokens": run.get("input_tokens", 0),
        "output_tokens": run.get("output_tokens", 0),
        "loaded": [l.get("qualified_name") for l in ok_loads],
        "verified_loaded": [l.get("qualified_name") for l in verified_loads],
    }

    if task.get("negative"):
        record["false_trigger"] = bool(ok_loads) if record["valid"] else None
        record["clean_abstain"] = (not ok_loads) if record["valid"] else None
        return record

    target = (manifest.get("task_targets") or {}).get(task["id"])
    if target is None:
        record["errored"] = True
        record["valid"] = False
        record["stop_reason"] = "missing_target_mapping"
        return record
    target_qname = target["qualified_name"]
    target_dir = target["directory"]
    acceptable = {target_qname, *task.get("acceptable_alternatives", [])}

    record["target"] = target_qname
    record["target_directory"] = target_dir
    record["dir_discovered"] = (
        target_dir in browsed_dirs
        or target_dir in seen_dirs
        or any(q.startswith(target_dir + "/") for q in seen_qnames)
    )
    correct = [q for q in record["verified_loaded"] if q in acceptable]
    record["correct_load"] = bool(correct)
    record["wrong_loads"] = [
        q for q in record["verified_loaded"] if q not in acceptable
    ]
    record["direct_load"] = bool(
        correct and not browsed_dirs and not search_calls
    )
    record["rescue"] = bool(
        correct
        and first_discovery_dirs is not None
        and target_dir not in first_discovery_dirs
    )
    record["body_delivered"] = len(verified_loads)
    record["body_handoff_confirmed"] = sum(
        1 for l in verified_loads if l.get("confirmed_handoff")
    )
    return record


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rate(flags: list[bool]) -> str:
    return f"{sum(flags)}/{len(flags)}"


def aggregate(records: list[dict]) -> dict:
    """Per (scale, variant, arm) aggregate."""
    out: dict[str, dict] = {}
    by_cell: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        by_cell[(record["scale"], record["variant"], record["arm"])].append(record)
    for (scale, variant, arm), cell in sorted(by_cell.items()):
        positives = [r for r in cell if not r["negative"] and r["valid"]]
        negatives = [r for r in cell if r["negative"] and r["valid"]]
        timeouts = [r for r in cell if r["timeout"]]
        errors = [r for r in cell if r["errored"] and not r["timeout"]]
        correct = [r for r in positives if r.get("correct_load")]
        key = f"{scale}/{variant}/{arm}"
        out[key] = {
            "runs": len(cell),
            "timeouts": len(timeouts),
            "errors": len(errors),
            "valid_positives": len(positives),
            "valid_negatives": len(negatives),
            "dir_discovery": _rate([bool(r.get("dir_discovered")) for r in positives]) if positives else "n/a",
            "correct_load": _rate([bool(r.get("correct_load")) for r in positives]) if positives else "n/a",
            "rescue_of_correct": _rate([bool(r.get("rescue")) for r in correct]) if correct else "n/a",
            "direct_load_of_correct": _rate([bool(r.get("direct_load")) for r in correct]) if correct else "n/a",
            "negative_false_triggers": _rate([bool(r.get("false_trigger")) for r in negatives]) if negatives else "n/a",
            "negative_clean_abstains": _rate([bool(r.get("clean_abstain")) for r in negatives]) if negatives else "n/a",
            "bodies_delivered": sum(int(r.get("body_delivered", 0)) for r in positives),
            "bodies_handoff_confirmed": sum(int(r.get("body_handoff_confirmed", 0)) for r in positives),
            "mean_steps": round(_mean([r["steps"] for r in cell if r["valid"]]), 2),
            "mean_browse_calls": round(_mean([r["browse_calls"] for r in cell if r["valid"]]), 2),
            "mean_search_calls": round(_mean([r["search_calls"] for r in cell if r["valid"]]), 2),
            "mean_load_calls": round(_mean([r["load_calls"] for r in cell if r["valid"]]), 2),
            "mean_elapsed_ms": round(_mean([r["elapsed_ms"] for r in cell if r["valid"]]), 1),
            "input_tokens": sum(r["input_tokens"] for r in cell),
            "output_tokens": sum(r["output_tokens"] for r in cell),
        }
    return out


def _mark(record: dict | None) -> str:
    if record is None:
        return "-"
    if record["timeout"]:
        return "T"
    if record["errored"]:
        return "E"
    if record["negative"]:
        return "ok" if record.get("clean_abstain") else "FALSE-TRIGGER"
    if record.get("correct_load"):
        return "ok"
    if record.get("dir_discovered"):
        return "~"  # found the directory but no correct load
    return "x"


def diff_table(records: list[dict]) -> str:
    by_task: dict[tuple[str, str, str], dict[str, dict]] = defaultdict(dict)
    for record in records:
        by_task[(record["scale"], record["variant"], record["task_id"])][record["arm"]] = record
    lines = [
        "| Scale | Variant | Task | A | B | B2 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (scale, variant, task_id) in sorted(by_task):
        row = by_task[(scale, variant, task_id)]
        marks = [_mark(row.get(arm)) for arm in ARMS]
        lines.append(f"| {scale} | {variant} | {task_id} | " + " | ".join(marks) + " |")
    lines.append("")
    lines.append(
        "Marks: ok = correct load (positive) / clean abstain (negative); "
        "~ = target directory discovered but no correct load; x = not discovered; "
        "T = timeout (excluded from rates); E = error; - = no run."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", type=Path, required=True,
                        help="run output dir containing <scale>-<variant>/<task>-<arm>/run.json")
    parser.add_argument("--catalogs", type=Path, default=WORK / "catalogs")
    parser.add_argument("--tasks", type=Path, default=WORK / "tasks.json")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="defaults to <live>/score-summary.json")
    args = parser.parse_args(argv)

    tasks = {t["id"]: t for t in _load_json(args.tasks)["tasks"]}
    run_paths = sorted(args.live.glob("*/*/run.json"))
    if not run_paths:
        print(f"no run.json files under {args.live}", file=sys.stderr)
        return 1

    manifests: dict[str, dict] = {}

    def manifest_for(scale: str, variant: str) -> dict:
        key = f"{scale}-{variant}"
        if key not in manifests:
            manifests[key] = _load_json(
                args.catalogs / key / "catalog-manifest.json"
            )
        return manifests[key]

    records = []
    skipped = []
    for path in run_paths:
        run = _load_json(path)
        task = tasks.get(run.get("task_id"))
        if task is None:
            skipped.append((str(path), "unknown task"))
            continue
        try:
            manifest = manifest_for(run["scale"], run["variant"])
        except FileNotFoundError:
            skipped.append((str(path), "missing manifest"))
            continue
        records.append(score_run(run, task, manifest))
    for path, reason in skipped:
        print(f"skipped {path}: {reason}", file=sys.stderr)

    summary = aggregate(records)
    table = diff_table(records)
    out_path = args.json_out or (args.live / "score-summary.json")
    out_path.write_text(
        json.dumps({
            "runs_scored": len(records),
            "per_cell": summary,
            "runs": records,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(table)
    print()
    for key, cell in summary.items():
        print(
            f"{key}: discovery {cell['dir_discovery']}, "
            f"correct {cell['correct_load']}, rescue {cell['rescue_of_correct']}, "
            f"false-triggers {cell['negative_false_triggers']}, "
            f"timeouts {cell['timeouts']}, errors {cell['errors']}, "
            f"mean steps {cell['mean_steps']}, "
            f"in-tokens {cell['input_tokens']}"
        )
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
