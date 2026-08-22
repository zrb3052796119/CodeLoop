"""Synthetic smoke test for allowlisted production Hybrid Memory evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedding-provider",
        choices=("local-e5", "qwen"),
        default="local-e5",
    )
    parser.add_argument("--model-path", type=Path)
    parser.add_argument(
        "--evidence-path",
        type=Path,
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if args.embedding_provider == "local-e5" and args.model_path is None:
        parser.error("--model-path is required for local-e5")
    evidence_path = args.evidence_path or (
        ROOT
        / "artifacts"
        / (
            "memory-retrieval-hybrid-qwen-v1-production-evidence.json"
            if args.embedding_provider == "qwen"
            else "memory-retrieval-hybrid-v4-production-evidence.json"
        )
    )
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_model_adapter

    apply_env_file([args.env_file])
    runtime = dict(
        load_runtime_config(ROOT),
        maxOutputTokens=2000,
        temperature=0,
        modelMaxRetries=1,
        modelTimeoutSeconds=90,
    )
    model = create_model_adapter("deepseek-chat", None, dict(runtime, model="deepseek-chat"))
    with tempfile.TemporaryDirectory(prefix="minicode-hybrid-production-smoke-") as temp:
        root = Path(temp)
        manager = MemoryManager(project_root=root, data_root=root / "user-memory")
        for entry in (
            MemoryEntry(
                id="correct-after-commit",
                scope=MemoryScope.PROJECT,
                category="architecture",
                content="The handler notifies the caller only after the durable update commits.",
                tags=["caller-notify", "durable-update"],
                approval_status="approved",
                lifecycle_status="active",
                safety_status="safe",
            ),
            MemoryEntry(
                id="wrong-before-commit",
                scope=MemoryScope.PROJECT,
                category="architecture",
                content="The handler notifies the caller before the durable update commits.",
                tags=["caller-notify", "durable-update"],
                approval_status="approved",
                lifecycle_status="active",
                safety_status="safe",
            ),
        ):
            manager.memories[entry.scope].entries.append(entry)
        manager.memories[MemoryScope.PROJECT]._rebuild_indices()
        pipeline = MemoryPipeline(manager)
        pipeline.initialize(
            model_adapter=model,
            workspace_path=str(root),
            enable_vector=True,
            hybrid_model_path=args.model_path,
            hybrid_evidence_path=evidence_path,
            hybrid_embedding_provider=args.embedding_provider,
            allow_remote_memory_embedding=args.embedding_provider == "qwen",
        )
        rendered = pipeline.read(
            "Notify the caller only after the durable update commits.",
            max_results=8,
            context_usage=0.0,
            _record_retrieval=False,
        )
        ids = [item["id"] for item in rendered]
        result = {
            "hybrid_active": pipeline.stats["hybrid_active"],
            "hybrid_inactive_reason": pipeline.stats["hybrid_inactive_reason"],
            "rendered_ids": ids,
            "hybrid_diagnostics": pipeline._last_retrieval_result.diagnostics["hybrid"],
            "passed": ids == ["correct-after-commit"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["passed"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
