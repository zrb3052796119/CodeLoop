"""Live, synthetic-only evaluator for the Hybrid Memory v2 relevance decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from minicode.memory_hybrid import (
    HYBRID_ALLOWED_DECISIONS as ALLOWED_DECISIONS,
    HYBRID_ALLOWED_REASONS as ALLOWED_REASONS,
    HYBRID_PROMPT_VERSION as PROMPT_VERSION,
    HYBRID_SYSTEM_PROMPT as SYSTEM_PROMPT,
)


ROOT = Path(__file__).resolve().parents[1]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "report_fingerprint"}
    return hashlib.sha256(_stable_json(body).encode("utf-8")).hexdigest()


def _eligible(entry: dict[str, Any]) -> bool:
    return (
        entry.get("approval_status", "approved") == "approved"
        and entry.get("lifecycle_status", "active") == "active"
        and entry.get("safety_status", "safe") == "safe"
        and not bool(entry.get("curator_locked", False))
        and entry.get("tier", "long_term") != "archival"
    )


def load_analysis_pairs() -> list[dict[str, Any]]:
    from scripts.memory_retrieval_semantic_gap_evaluator import load_dataset

    dataset = load_dataset(ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap")
    pairs: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        if case["split"] != "analysis":
            continue
        labeled_ids = set(case["primary_entry_ids"]) | set(case["must_exclude_ids"])
        for entry in case["memories"]:
            if entry["id"] not in labeled_ids or not _eligible(entry):
                continue
            pairs.append(
                _pair(
                    pair_id=case["case_id"],
                    query=case["query"],
                    current_files=case["current_files"],
                    active_domains=case["active_domains"],
                    entry=entry,
                    label=entry["id"] in case["primary_entry_ids"],
                    relation=case["semantic_relation_type"],
                )
            )
    return pairs


def load_v2_holdout_pairs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = ROOT / "tests" / "fixtures" / "memory_retrieval_hybrid_v2_holdout"
    document = json.loads((root / "holdout.json").read_text(encoding="utf-8"))
    pairs: list[dict[str, Any]] = []
    for case in document["cases"]:
        entry = {"id": f"{case['case_id']}-memory", **case["entry"]}
        if not _eligible(entry):
            continue
        positive = case["polarity"] == "positive"
        pairs.append(
            _pair(
                pair_id=case["case_id"],
                query=case["query"],
                current_files=case["current_files"],
                active_domains=case["active_domains"],
                entry=entry,
                label=positive,
                relation=case["relation"],
            )
        )
    return pairs, document["promotion_thresholds"]


def _pair(
    *,
    pair_id: str,
    query: str,
    current_files: list[str],
    active_domains: list[str],
    entry: dict[str, Any],
    label: bool,
    relation: str,
) -> dict[str, Any]:
    return {
        "id": pair_id,
        "query": query,
        "currentFiles": list(current_files),
        "activeDomains": list(active_domains),
        "memory": {
            "content": str(entry.get("content", ""))[:2000],
            "scope": entry.get("scope", "project"),
            "category": entry.get("category", "semantic-rule"),
            "tags": list(entry.get("tags", []))[:16],
            "domains": list(entry.get("domains", []))[:16],
            "metadata": dict(entry.get("metadata", {})),
        },
        "_label": bool(label),
        "_relation": relation,
    }


def _parse_decisions(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", text.strip(), re.S)
    if match is None:
        raise ValueError("verifier returned no JSON object")
    payload = json.loads(match.group(0))
    rows = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        raise ValueError("verifier decision count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("verifier decision is not an object")
        pair_id = row.get("id")
        decision = row.get("decision")
        reason = row.get("reasonCode")
        confidence = row.get("confidence")
        object_match = row.get("objectMatch")
        relation_supported = row.get("relationSupported")
        if (
            not isinstance(pair_id, str)
            or decision not in ALLOWED_DECISIONS
            or reason not in ALLOWED_REASONS
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
            or not isinstance(object_match, bool)
            or not isinstance(relation_supported, bool)
        ):
            raise ValueError("verifier decision is malformed")
        result.append(
            {
                "id": pair_id,
                "decision": decision,
                "confidence": round(float(confidence), 6),
                "objectMatch": object_match,
                "relationSupported": relation_supported,
                "reasonCode": reason,
            }
        )
    if sorted(row["id"] for row in result) != sorted(expected_ids):
        raise ValueError("verifier decision IDs mismatch")
    return result


def evaluate_pairs(
    model: Any,
    pairs: list[dict[str, Any]],
    *,
    batch_size: int = 17,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    usage = {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0}
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        public = [{key: value for key, value in row.items() if not key.startswith("_")} for row in batch]
        step = model.next(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _stable_json({"pairs": public})},
            ]
        )
        decisions.extend(_parse_decisions(step.content, [row["id"] for row in batch]))
        usage["model_call_count"] += 1
        if step.usage is not None:
            usage["input_tokens"] += step.usage.input_tokens or 0
            usage["output_tokens"] += step.usage.output_tokens or 0
    by_id = {row["id"]: row for row in decisions}
    if len(by_id) != len(decisions) or len(by_id) != len(pairs):
        raise ValueError("verifier emitted duplicate decisions")
    tp = fp = tn = fn = 0
    failures: list[dict[str, Any]] = []
    for pair in pairs:
        decision = by_id[pair["id"]]
        predicted = (
            decision["decision"] == "relevant"
            and decision["confidence"] >= min_confidence
            and decision["objectMatch"] is True
            and decision["relationSupported"] is True
        )
        label = pair["_label"]
        if predicted and label:
            tp += 1
        elif predicted:
            fp += 1
        elif label:
            fn += 1
        else:
            tn += 1
        if predicted != label:
            failures.append(
                {
                    "id": pair["id"],
                    "label": label,
                    "relation": pair["_relation"],
                    "decision": decision,
                }
            )
    recall = tp / max(1, tp + fn)
    precision = tp / max(1, tp + fp)
    negative_rate = fp / max(1, fp + tn)
    return {
        "pair_count": len(pairs),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "metrics": {
            "positive_recall": recall,
            "precision": precision,
            "hard_negative_accept_rate": negative_rate,
        },
        "usage": usage,
        "failures": failures,
        "decisions": decisions,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("analysis", "v2-holdout"), required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.85)
    parser.add_argument("--acknowledge-one-shot", action="store_true")
    args = parser.parse_args()
    if not 0.0 <= args.min_confidence <= 1.0:
        raise SystemExit("--min-confidence must be in [0, 1]")
    default_output = ROOT / "artifacts" / (
        "memory-retrieval-hybrid-v2-analysis.json"
        if args.split == "analysis"
        else "memory-retrieval-hybrid-v2-holdout.json"
    )
    output = args.output or default_output
    if args.split == "v2-holdout":
        if not args.acknowledge_one_shot:
            raise SystemExit("v2 holdout requires --acknowledge-one-shot")
        if output.exists():
            raise SystemExit("v2 holdout evidence already exists; dataset is spent")

    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_model_adapter

    apply_env_file([args.env_file])
    runtime = load_runtime_config(ROOT)
    runtime = dict(
        runtime,
        maxOutputTokens=5000,
        temperature=0,
        modelMaxRetries=1,
        modelTimeoutSeconds=90,
    )
    model = create_model_adapter(runtime["model"], None, runtime)
    if args.split == "analysis":
        pairs = load_analysis_pairs()
        thresholds: dict[str, Any] = {}
    else:
        pairs, thresholds = load_v2_holdout_pairs()
    evaluation = evaluate_pairs(
        model,
        pairs,
        min_confidence=args.min_confidence,
    )
    report = {
        "schema_version": "2.0",
        "prompt_version": PROMPT_VERSION,
        "split": args.split,
        "synthetic_data": True,
        "model_id": runtime["model"],
        "min_confidence": args.min_confidence,
        "promotion_thresholds": thresholds,
        **evaluation,
    }
    report["report_fingerprint"] = _fingerprint(report)
    _write_json_atomic(output, report)
    print(
        json.dumps(
            {
                "output": str(output),
                "metrics": report["metrics"],
                "confusion": report["confusion"],
                "usage": report["usage"],
                "failure_ids": [item["id"] for item in report["failures"]],
                "report_fingerprint": report["report_fingerprint"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
