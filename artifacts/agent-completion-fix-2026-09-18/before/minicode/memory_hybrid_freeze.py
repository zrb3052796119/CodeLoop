"""Reproducible source closure for Hybrid Memory production semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

HYBRID_RUNTIME_FREEZE_VERSION = "hybrid-runtime-freeze-v1"

# The release trust-anchor module is deliberately absent. Updating an accepted
# evidence fingerprint must not mutate the implementation that evidence froze.
HYBRID_RUNTIME_BEHAVIOR_FILES = (
    "minicode/advisory_lock.py",
    "minicode/agent_budget.py",
    "minicode/api_retry.py",
    "minicode/agent_loop.py",
    "minicode/conversation.py",
    "minicode/permissions.py",
    "minicode/session.py",
    "minicode/tooling.py",
    "minicode/tools/run_command.py",
    "minicode/config.py",
    "minicode/context_manager.py",
    "minicode/cost_tracker.py",
    "minicode/cybernetic_orchestrator.py",
    "minicode/domain_classifier.py",
    "minicode/embeddings.py",
    "minicode/env_file.py",
    "minicode/evaluation_one_shot.py",
    "minicode/memory.py",
    "minicode/memory_approval_interpretation.py",
    "minicode/memory_audit_contract.py",
    "minicode/memory_authority_io.py",
    "minicode/memory_authority_publisher.py",
    "minicode/memory_blob_store.py",
    "minicode/memory_candidate_consolidation.py",
    "minicode/memory_hybrid.py",
    "minicode/memory_hybrid_freeze.py",
    "minicode/memory_hybrid_promotion_contract.py",
    "minicode/memory_hybrid_runtime.py",
    "minicode/memory_injector.py",
    "minicode/memory_lexical_policy.py",
    "minicode/memory_path_identity.py",
    "minicode/memory_pipeline.py",
    "minicode/memory_provenance.py",
    "minicode/memory_retrieval.py",
    "minicode/memory_store.py",
    "minicode/memory_write_contract.py",
    "minicode/model_call_control.py",
    "minicode/model_registry.py",
    "minicode/openai_adapter.py",
    "minicode/pricing.py",
    "minicode/reflection_evidence.py",
    "minicode/reflection_synthesis.py",
    "minicode/run_events.py",
    "minicode/tls.py",
    "minicode/turn_cancellation.py",
    "minicode/types.py",
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_hybrid_runtime_freeze(root: Path | None = None) -> dict[str, Any]:
    """Hash the closed production source set used by retrieval and admission."""

    repository_root = (
        Path(root).resolve(strict=True)
        if root is not None
        else Path(__file__).resolve().parents[1]
    )
    rows: list[dict[str, str]] = []
    for relative in HYBRID_RUNTIME_BEHAVIOR_FILES:
        candidate = repository_root / relative
        resolved = candidate.resolve(strict=True)
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or repository_root not in resolved.parents
        ):
            raise ValueError(f"hybrid runtime source is missing or unsafe: {relative}")
        rows.append({"path": relative, "sha256": _sha256_file(candidate)})
    body: dict[str, Any] = {
        "schema_version": HYBRID_RUNTIME_FREEZE_VERSION,
        "file_count": len(rows),
        "files": rows,
    }
    return {
        **body,
        "aggregate_sha256": hashlib.sha256(
            _stable_json(body).encode("utf-8")
        ).hexdigest(),
    }


def hybrid_runtime_freeze_is_current(value: Any) -> bool:
    """Fail closed when promoted evidence no longer matches installed code."""

    if not isinstance(value, dict):
        return False
    try:
        return value == build_hybrid_runtime_freeze()
    except (OSError, RuntimeError, ValueError):
        return False
