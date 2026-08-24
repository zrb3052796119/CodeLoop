"""One-shot canonical end-to-end gate for Hybrid Memory v2.

The evaluator uses the already frozen synthetic v2 holdout as one global
memory corpus. It measures dense top-k recall and the public MemoryPipeline
read path without changing labels, thresholds, prompts, or model identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from minicode.memory import (
    MemoryEntry,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)
from minicode.memory_hybrid import (
    HYBRID_CHALLENGER_PROMPT_VERSION,
    HYBRID_CHALLENGER_SYSTEM_PROMPT,
    HYBRID_CHALLENGER_MODE,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_EVIDENCE_SCHEMA_VERSION,
    HYBRID_PROMPT_VERSION,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_SYSTEM_PROMPT,
    build_hybrid_promotion_verifier_runtime,
    evidence_fingerprint,
)
from minicode.memory_hybrid_runtime import (
    DEFAULT_DENSE_TOP_K,
    MAX_CANDIDATES,
    LocalE5Encoder,
    _entry_document,
    _entry_is_dense_eligible,
    _query_document,
)
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import MemoryRetrievalRequest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "memory_retrieval_hybrid_v2_holdout"
REFERENCE_TIME = 1_800_000_000.0
EXPECTED_FREEZE_MANIFEST_SHA256 = (
    "ed85536b03fbdd5920a7261308f7ab335807c93495d2a74b61150afb7033c859"
)
HOLDOUTS = {
    "v2": (FIXTURE_ROOT, EXPECTED_FREEZE_MANIFEST_SHA256),
    "v3": (
        ROOT / "tests" / "fixtures" / "memory_retrieval_hybrid_v3_holdout",
        "19ad40a591aa66e33ba0242d8b638652d1997b4aaaecda67211bdb0599efddb9",
    ),
    "v4": (
        ROOT / "tests" / "fixtures" / "memory_retrieval_hybrid_v4_holdout",
        "b6812eb64a0e1aa0d5b3ccba90b276fbe358284ef77ae7747b7ad4065d3389bf",
    ),
    "v5": (
        ROOT / "tests" / "fixtures" / "memory_retrieval_hybrid_v5_holdout",
        "189fdfe799f7dcc04ec8c48a993bbf4d0586fa0f898bb90b595fde0994e37447",
    ),
    "v6-qwen": (
        ROOT
        / "tests"
        / "fixtures"
        / "memory_retrieval_hybrid_v6_qwen_holdout",
        "30b037063d6be3a477415067982980a5ee9322cba678707a7f872d11029731ba",
    ),
    "v7-qwen": (
        ROOT
        / "tests"
        / "fixtures"
        / "memory_retrieval_hybrid_v7_qwen_holdout",
        "d3bfde05c1652f6498cb529dd69148165da1eaea44cca4e5f1e48a64a454d97e",
    ),
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_holdout(
    fixture_root: Path = FIXTURE_ROOT,
    expected_manifest_sha256: str = EXPECTED_FREEZE_MANIFEST_SHA256,
) -> dict[str, Any]:
    manifest_path = fixture_root / "frozen.sha256"
    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        expected[filename] = digest
    actual = {
        filename: _sha256_file(fixture_root / filename)
        if (fixture_root / filename).is_file()
        else "missing"
        for filename in expected
    }
    manifest_sha = _sha256_file(manifest_path)
    return {
        "matches": actual == expected
        and manifest_sha == expected_manifest_sha256,
        "manifest_sha256": manifest_sha,
        "expected_manifest_sha256": expected_manifest_sha256,
        "mismatches": {
            name: {"expected": expected[name], "actual": actual[name]}
            for name in expected
            if expected[name] != actual[name]
        },
    }


def _entry(entry_id: str, source: dict[str, Any], *, background: bool) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=MemoryScope(source.get("scope", "project")),
        category=str(source.get("category", "background" if background else "semantic-rule")),
        content=str(source.get("content", "")),
        tags=list(source.get("tags", [])),
        domains=list(source.get("domains", [])),
        metadata=dict(source.get("metadata", {})),
        approval_status=str(source.get("approval_status", "approved")),
        lifecycle_status=str(source.get("lifecycle_status", "active")),
        safety_status=str(source.get("safety_status", "safe")),
        curator_locked=bool(source.get("curator_locked", False)),
        tier=MemoryTier(source.get("tier", "long_term")),
        created_at=REFERENCE_TIME,
        updated_at=REFERENCE_TIME,
        last_accessed=REFERENCE_TIME,
    )


def load_corpus(
    fixture_root: Path = FIXTURE_ROOT,
) -> tuple[dict[str, Any], tuple[MemoryEntry, ...]]:
    dataset = json.loads((fixture_root / "holdout.json").read_text(encoding="utf-8"))
    entries = [
        _entry(item["id"], item, background=True)
        for item in dataset["background"]
    ]
    entries.extend(
        _entry(f"{case['case_id']}-memory", case["entry"], background=False)
        for case in dataset["cases"]
    )
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("v2 canonical corpus has duplicate memory IDs")
    return dataset, tuple(entries)


def _manager(root: Path, entries: tuple[MemoryEntry, ...]) -> MemoryManager:
    manager = MemoryManager(project_root=root, data_root=root / "user-memory")
    for entry in entries:
        manager.memories[entry.scope].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def case_local_corpus(
    entries: tuple[MemoryEntry, ...], target_id: str
) -> tuple[MemoryEntry, ...]:
    by_id = {entry.id: entry for entry in entries}
    target = by_id.get(target_id)
    if target is None:
        raise ValueError("canonical case target is missing from the frozen corpus")
    background = tuple(entry for entry in entries if "-bg-" in entry.id)
    expected_background = sum("-bg-" in entry.id for entry in entries)
    if not background or len(background) != expected_background:
        raise ValueError("canonical case requires every frozen background memory")
    return (*background, target)


def _model_identity(model_path: Path) -> dict[str, Any]:
    manifest = json.loads(
        (model_path / "model_manifest.json").read_text(encoding="utf-8")
    )
    return {
        field: manifest[field]
        for field in ("model_id", "model_revision", "model_fingerprint")
    }


def evaluation_evidence(model_path: Path, verifier_model_id: str) -> dict[str, Any]:
    payload = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "evaluation_only": True,
        "model": _model_identity(model_path),
        "verifier": {
            "model_id": verifier_model_id,
            "prompt_version": HYBRID_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "minimum_confidence": 0.85,
        },
        "challenger": {
            "model_id": verifier_model_id,
            "prompt_version": HYBRID_CHALLENGER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "minimum_confidence": 0.8,
            "mode": HYBRID_CHALLENGER_MODE,
            "veto_reason_codes": sorted(HYBRID_CHALLENGER_VETO_REASONS),
        },
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "max_model_calls_per_task": 8,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "acceptance_gate": {"passed": True, "evaluation_bootstrap": True},
        "production_enablement_allowed": True,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    return payload


def evaluate_dense_candidates(
    dataset: dict[str, Any],
    entries: tuple[MemoryEntry, ...],
    model_path: Path | None = None,
    *,
    encoder: Any | None = None,
) -> dict[str, Any]:
    if encoder is None:
        if model_path is None:
            raise ValueError("dense evaluation requires an encoder or local model")
        encoder = LocalE5Encoder(model_path, _model_identity(model_path))
    eligible = tuple(entry for entry in entries if _entry_is_dense_eligible(entry))
    vectors = encoder.encode_documents([_entry_document(entry) for entry in eligible])
    by_id = {entry.id: vector for entry, vector in zip(eligible, vectors, strict=True)}
    rows: list[dict[str, Any]] = []
    positive_hits = 0
    positive_total = 0
    for case in dataset["cases"]:
        request = MemoryRetrievalRequest(
            query=case["query"],
            current_files=tuple(case["current_files"]),
            active_domains=tuple(case["active_domains"]),
        )
        query_vector = encoder.encode_queries([_query_document(request)])[0]
        ranked = sorted(
            (
                (
                    entry_id,
                    sum(
                        left * right
                        for left, right in zip(query_vector, vector, strict=True)
                    ),
                )
                for entry_id, vector in by_id.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )
        top_ids = [entry_id for entry_id, _score in ranked[:DEFAULT_DENSE_TOP_K]]
        target_id = f"{case['case_id']}-memory"
        if case["polarity"] == "positive":
            positive_total += 1
            positive_hits += int(target_id in top_ids)
        rows.append(
            {
                "case_id": case["case_id"],
                "polarity": case["polarity"],
                "target_rank": next(
                    (index for index, (entry_id, _score) in enumerate(ranked, 1) if entry_id == target_id),
                    None,
                ),
                "target_in_top_20": target_id in top_ids,
                "top_20_ids_sha256": hashlib.sha256(
                    "\n".join(top_ids).encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "eligible_corpus_size": len(eligible),
        "positive_hits": positive_hits,
        "positive_total": positive_total,
        "positive_recall_at_20": positive_hits / max(1, positive_total),
        "cases": rows,
    }


def evaluate_canonical(
    dataset: dict[str, Any],
    entries: tuple[MemoryEntry, ...],
    *,
    model: Any,
    model_path: Path | None,
    evidence_path: Path,
    workspace: Path,
    hybrid_provider_factory: Any | None = None,
    embedding_provider: str = "local-e5",
    allow_remote_embedding: bool = False,
) -> dict[str, Any]:
    background_entries = tuple(
        entry for entry in entries if "-bg-" in entry.id
    )
    manager = _manager(workspace, background_entries)
    pipeline = MemoryPipeline(
        manager,
        hybrid_provider_factory=hybrid_provider_factory,
    )
    evaluation_fingerprint = json.loads(
        evidence_path.read_text(encoding="utf-8")
    )["report_fingerprint"]
    promotion_target = (
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT"
        if embedding_provider == "qwen"
        else "minicode.memory_hybrid.HYBRID_ACCEPTED_PROMOTION_FINGERPRINT"
    )
    with patch(promotion_target, evaluation_fingerprint):
        pipeline.initialize(
            model_adapter=model,
            workspace_path=str(workspace),
            enable_vector=True,
            hybrid_model_path=model_path,
            hybrid_evidence_path=evidence_path,
            hybrid_embedding_provider=embedding_provider,
            allow_remote_memory_embedding=allow_remote_embedding,
        )
    if not pipeline.stats["hybrid_active"]:
        raise RuntimeError(
            f"canonical hybrid did not activate: {pipeline.stats['hybrid_inactive_reason']}"
        )
    rows: list[dict[str, Any]] = []
    tp = fp = fn = 0
    hard_negative_target_renders = 0
    lifecycle_safety_leakage = 0
    provider_fallbacks = 0
    selected_positive_hits = 0
    positive_total = 0
    ineligible_ids = {
        entry.id for entry in entries if not _entry_is_dense_eligible(entry)
    }
    unsafe_indexed_ids: set[str] = set()
    with patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME):
        for case in dataset["cases"]:
            target_id = f"{case['case_id']}-memory"
            case_entries = case_local_corpus(entries, target_id)
            provider = pipeline._retriever._hybrid_provider
            provider._model_calls = 0
            for scope, memory_file in manager.memories.items():
                memory_file.entries[:] = [
                    entry for entry in case_entries if entry.scope == scope
                ]
                memory_file._rebuild_indices()
            rendered = pipeline.read(
                case["query"],
                current_files=list(case["current_files"]),
                active_domains=list(case["active_domains"]),
                max_results=32,
                max_total_tokens=12_000,
                max_tokens_per_memory=400,
                context_usage=0.0,
                _record_retrieval=False,
            )
            result = pipeline._last_retrieval_result
            rendered_ids = [item["id"] for item in rendered]
            selected_ids = list(result.selected_ids)
            target_candidate = next(
                (
                    memory
                    for memory in result.candidates
                    if memory.entry_id == target_id
                ),
                None,
            )
            fallback = bool(result.diagnostics["hybrid"]["fallback"])
            provider_fallbacks += int(fallback)
            provider = pipeline._retriever._hybrid_provider
            unsafe_indexed_ids.update(
                set(getattr(provider, "_records", {})) & ineligible_ids
            )
            if case["polarity"] == "positive":
                positive_total += 1
                selected_positive_hits += int(target_id in selected_ids)
                if target_id in rendered_ids:
                    tp += 1
                else:
                    fn += 1
                fp += sum(entry_id != target_id for entry_id in rendered_ids)
            else:
                target_rendered = target_id in rendered_ids
                hard_negative_target_renders += int(target_rendered)
                fp += len(rendered_ids)
            leaked = sorted(set(rendered_ids) & ineligible_ids)
            lifecycle_safety_leakage += len(leaked)
            rows.append(
                {
                    "case_id": case["case_id"],
                    "polarity": case["polarity"],
                    "target_id": target_id,
                    "selected_ids": selected_ids,
                    "rendered_ids": rendered_ids,
                    "ineligible_rendered_ids": leaked,
                    "hybrid_fallback": fallback,
                    "target_candidate": (
                        {
                            "dense_score": target_candidate.score.dense_score,
                            "semantic_score": target_candidate.score.semantic_score,
                            "reason_codes": list(target_candidate.reason_codes),
                        }
                        if target_candidate is not None
                        else None
                    ),
                    "hybrid_diagnostics": result.diagnostics["hybrid"],
                }
            )
    sorted_unsafe_indexed_ids = sorted(unsafe_indexed_ids)
    precision = tp / max(1, tp + fp)
    return {
        "confusion": {"tp": tp, "fp": fp, "fn": fn},
        "metrics": {
            "post_decision_positive_recall": selected_positive_hits
            / max(1, positive_total),
            "rendered_positive_recall": tp / max(1, positive_total),
            "rendered_precision": precision,
            "hard_negative_render_rate": hard_negative_target_renders
            / max(1, sum(case["polarity"] != "positive" for case in dataset["cases"])),
            "lifecycle_safety_leakage": lifecycle_safety_leakage,
            "provider_fallback_count": provider_fallbacks,
            "unsafe_indexed_count": len(sorted_unsafe_indexed_ids),
        },
        "unsafe_indexed_ids": sorted_unsafe_indexed_ids,
        "cases": rows,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--holdout-version", choices=tuple(HOLDOUTS), default="v5")
    parser.add_argument(
        "--output",
        type=Path,
    )
    parser.add_argument("--acknowledge-one-shot", action="store_true")
    args = parser.parse_args()
    output = args.output or (
        ROOT
        / "artifacts"
        / f"memory-retrieval-hybrid-{args.holdout_version}-canonical.json"
    )
    if not args.acknowledge_one_shot:
        raise SystemExit("canonical holdout requires --acknowledge-one-shot")
    if output.exists():
        raise SystemExit("canonical evidence already exists; evaluation is one-shot")
    fixture_root, expected_manifest = HOLDOUTS[args.holdout_version]
    frozen = verify_frozen_holdout(fixture_root, expected_manifest)
    if not frozen["matches"]:
        raise SystemExit("frozen v2 holdout verification failed")

    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_model_adapter

    apply_env_file([args.env_file])
    runtime = build_hybrid_promotion_verifier_runtime(load_runtime_config(ROOT))
    model = create_model_adapter(runtime["model"], None, runtime)
    dataset, entries = load_corpus(fixture_root)
    evidence = evaluation_evidence(args.model_path, runtime["model"])
    with tempfile.TemporaryDirectory(prefix="minicode-hybrid-v2-canonical-") as temporary:
        temporary_root = Path(temporary)
        evidence_path = temporary_root / "evaluation-evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        dense = evaluate_dense_candidates(dataset, entries, args.model_path)
        canonical = evaluate_canonical(
            dataset,
            entries,
            model=model,
            model_path=args.model_path,
            evidence_path=evidence_path,
            workspace=temporary_root / "workspace",
        )

    thresholds = dataset["promotion_thresholds"]
    metrics = canonical["metrics"]
    checks = {
        "frozen_holdout": frozen["matches"],
        "candidate_recall_at_20": dense["positive_recall_at_20"]
        >= thresholds["candidate_recall_at_20_min"],
        "post_decision_positive_recall": metrics["post_decision_positive_recall"]
        >= thresholds["post_decision_positive_recall_min"],
        "rendered_positive_recall": metrics["rendered_positive_recall"]
        >= thresholds["rendered_positive_recall_min"],
        "rendered_precision": metrics["rendered_precision"]
        >= thresholds["rendered_precision_min"],
        "hard_negative_render_rate": metrics["hard_negative_render_rate"]
        <= thresholds["hard_negative_render_rate_max"],
        "lifecycle_safety_leakage": metrics["lifecycle_safety_leakage"]
        <= thresholds["lifecycle_safety_leakage_max"],
        "unsafe_indexed_count": metrics["unsafe_indexed_count"] == 0,
        "provider_fallback_count": metrics["provider_fallback_count"] == 0,
    }
    report = {
        "schema_version": "2.0",
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "holdout_version": args.holdout_version,
        "dataset": frozen,
        "model": _model_identity(args.model_path),
        "verifier": evidence["verifier"],
        "challenger": evidence["challenger"],
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "candidate_corpus_protocol": "global_45_eligible_memories",
        "canonical_corpus_protocol": "case_target_plus_16_shared_background",
        "thresholds": thresholds,
        "dense": dense,
        "canonical": canonical,
        "acceptance_gate": {"passed": all(checks.values()), "checks": checks},
    }
    report["report_fingerprint"] = evidence_fingerprint(report)
    _write_json_atomic(output, report)
    print(
        json.dumps(
            {
                "output": str(output),
                "candidate_recall_at_20": dense["positive_recall_at_20"],
                **metrics,
                "acceptance_gate": report["acceptance_gate"],
                "report_fingerprint": report["report_fingerprint"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report["acceptance_gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
