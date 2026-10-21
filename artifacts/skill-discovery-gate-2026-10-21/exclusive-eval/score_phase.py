"""Offline scoring for the directory-presel verification batch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scoring import score

HERE = Path(__file__).resolve().parent
TRIALS = HERE / "trials"
POSITIVE = ["R01", "R02", "R03", "R04", "X01", "X02", "X03", "X04", "C01", "C02", "C03", "C04"]
NEGATIVE = ["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08"]
DISCOVERY_TOOLS = {"browse_skill_directory", "search_skills"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def corpus_proper(run, run_dir):
    catalog = read_json(run_dir / "catalog.json")
    manifest = read_json(run_dir / "fixture-manifest.json")
    bodies = {}
    for entry in catalog:
        name = entry.get("qualified_name") or entry["name"]
        body = Path(entry["path"]).read_text(encoding="utf-8")
        if hashlib.sha256(body.encode()).hexdigest() != entry["content_digest"]:
            raise ValueError("body_hash_mismatch")
        bodies[name] = body
    if len(bodies) != 1000 or manifest["skill_count"] != 1000:
        raise ValueError("catalog_count_mismatch")
    return bodies


def token_totals(run: dict[str, Any]) -> dict[str, Any]:
    chats = [r for r in run.get("provider_requests", []) if r.get("kind") == "chat"]
    embeddings = [r for r in run.get("provider_requests", []) if r.get("kind") == "embedding"]

    def usage_of(request: dict[str, Any]) -> dict[str, int]:
        usage = request.get("usage") or {}
        return {
            "input": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "output": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
            "cached": int(usage.get("input_tokens_details", {}).get("cached_tokens", 0) if isinstance(usage.get("input_tokens_details"), dict) else 0),
        }

    first = chats[0] if chats else None
    total_in = sum(usage_of(r)["input"] for r in chats)
    total_out = sum(usage_of(r)["output"] for r in chats)
    total_cached = sum(usage_of(r)["cached"] for r in chats)
    return {
        "chat_requests": len(chats),
        "first_chat_input_tokens": usage_of(first)["input"] if first else None,
        "chat_input_total": total_in,
        "chat_output_total": total_out,
        "chat_cached_total": total_cached,
        "embedding_requests": len(embeddings),
        "embedding_input_total": sum(usage_of(r)["input"] for r in embeddings),
    }


def score_one(case: dict[str, Any], repeat: int) -> dict[str, Any] | None:
    case_id = case["id"]
    run_dir = TRIALS / f"{case_id}-P-r{repeat}"
    run_path = run_dir / "run.json"
    if not run_path.is_file():
        return None
    run = read_json(run_path)
    try:
        bodies, corpus_ok = corpus_proper(run, run_dir), True
    except Exception:  # noqa: BLE001
        bodies, corpus_ok = {}, False
    expected = case["expected"]
    result = score(run, expected, run_dir=run_dir, bodies=bodies, corpus_complete=corpus_ok)
    discovery = [
        e for e in run.get("executions", [])
        if e.get("name") in DISCOVERY_TOOLS and e.get("ok")
    ]
    telemetry = run.get("presel_telemetry") or {}
    suggested = list(telemetry.get("suggested_directories") or [])
    sources = dict(telemetry.get("suggested_directory_sources") or {})
    target_dirs = sorted({
        str(name).split("/", 1)[0]
        for group in expected.get("required_skill_groups", ())
        for name in group
    })
    hint_hits = [d for d in target_dirs if d in suggested]
    semantic_suggested = [d for d, s in sources.items() if "semantic" in s]
    return {
        "case_id": case_id, "repeat": repeat, "negative": bool(expected.get("negative")),
        "completed": result["completed"], "stop_reason": result["stop_reason"],
        "primary_success": result["primary_success"],
        "strict_load": result["strict_load"], "retrieval_and_handoff": result["retrieval_and_handoff"],
        "strict_chain": result["strict_chain"],
        "negative_false_trigger": result["negative_false_trigger"],
        "negative_clean": result["negative_clean"],
        "task": result.get("task") or {},
        "loaded": result["successful_loader_names"],
        "discovery_initiated": bool(discovery),
        "discovery_calls": [e.get("name") for e in discovery],
        "hints_present": bool(suggested),
        "suggested_directories": suggested,
        "suggested_sources": sources,
        "semantic_suggested": semantic_suggested,
        "target_directories": target_dirs,
        "hint_target_hit": bool(hint_hits),
        "hint_target_dirs": hint_hits,
        "presel_vectors": (run.get("presel_telemetry") or {}).get("directory_vectors_cached"),
        "tokens": token_totals(run),
        "wall_seconds": run.get("wall_seconds"),
    }


def main() -> None:
    cases = read_json(HERE / "cases.json")["cases"]
    rows = []
    for case in cases:
        for repeat in (1, 2, 3):
            row = score_one(case, repeat)
            if row is not None:
                rows.append(row)
    positives = [r for r in rows if not r["negative"]]
    negatives = [r for r in rows if r["negative"]]

    def count(rows_, key: str) -> int:
        return sum(1 for r in rows_ if r[key])

    summary = {
        "schema": "skill-presel-verify-summary/v1",
        "trials_scored": len(rows),
        "positives": {
            "n": len(positives),
            "discovery_initiated": count(positives, "discovery_initiated"),
            "strict_load": count(positives, "strict_load"),
            "retrieval_and_handoff": count(positives, "retrieval_and_handoff"),
            "strict_chain": count(positives, "strict_chain"),
            "primary_success": count(positives, "primary_success"),
            "hint_target_hit": count(positives, "hint_target_hit"),
            "hints_present": count(positives, "hints_present"),
            "task_passed": sum(1 for r in positives if (r.get("task") or {}).get("passed")),
        },
        "negatives": {
            "n": len(negatives),
            "false_triggers": sum(1 for r in negatives if r["negative_false_trigger"]),
            "task_passed": sum(1 for r in negatives if (r.get("task") or {}).get("passed")),
        },
        "tokens": {
            "first_chat_input_mean": (
                sum(r["tokens"]["first_chat_input_tokens"] or 0 for r in rows) / len(rows)
                if rows else None
            ),
            "chat_input_total": sum(r["tokens"]["chat_input_total"] for r in rows),
            "chat_output_total": sum(r["tokens"]["chat_output_total"] for r in rows),
            "embedding_requests_total": sum(r["tokens"]["embedding_requests"] for r in rows),
        },
        "per_case": {},
    }
    for case_id in POSITIVE + NEGATIVE:
        case_rows = [r for r in rows if r["case_id"] == case_id]
        if case_rows:
            summary["per_case"][case_id] = {
                r["repeat"]: {
                    "discovery": r["discovery_initiated"],
                    "strict_load": r["strict_load"],
                    "handoff": r["retrieval_and_handoff"],
                    "hint_hit": r["hint_target_hit"],
                    "suggested": r["suggested_directories"],
                    "sources": r["suggested_sources"],
                    "semantic": r["semantic_suggested"],
                    "task": (r.get("task") or {}).get("passed"),
                    "loaded": r["loaded"],
                } for r in case_rows
            }
    out = HERE / "analysis-presel.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
