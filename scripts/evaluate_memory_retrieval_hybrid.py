"""CLI for explicit local-only Retrieval Phase 3B calibration and evaluation."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import time
from pathlib import Path

from experiments.memory_embedding_adapter import (
    LocalEmbeddingAdapter,
    download_pinned_local_model,
)
from scripts.memory_retrieval_hybrid_evaluator import (
    calibrate_configuration,
    payload_hash,
    run_final_evaluation,
    write_frozen_config,
    write_json_atomic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("download-model", "calibrate", "evaluate"))
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument(
        "--config-output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-hybrid-config.json",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-hybrid-offline.json",
    )
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/minicode-phase3b-work"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.mode == "download-model":
        manifest = download_pinned_local_model(
            args.model_path, allow_download=args.allow_model_download
        )
        print(json.dumps(manifest, indent=2))
        return
    loaded_started = time.perf_counter()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    adapter = LocalEmbeddingAdapter(args.model_path, batch_size=32)
    cold_load_ms = (time.perf_counter() - loaded_started) * 1000
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_multiplier = 1 if platform.system() == "Darwin" else 1024
    model_rss_delta_bytes = max(0, rss_after - rss_before) * rss_multiplier
    if args.mode == "calibrate":
        payload = calibrate_configuration(
            adapter=adapter,
            phase3a_dataset_root=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap",
            phase3a_baseline_path=PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline.json",
            work_root=args.work_root,
        )
        payload["calibration"]["model_cold_load_ms"] = round(cold_load_ms, 6)
        document = write_frozen_config(args.config_output, payload)
        print(
            json.dumps(
                {
                    "config_output": str(args.config_output),
                    "payload_sha256": document["payload_sha256"],
                    "selected_configuration": document["selected_configuration"],
                    "selected_analysis_metrics": document["calibration"]["selected_analysis_metrics"],
                    "attempt_count": document["calibration"]["attempt_count"],
                },
                indent=2,
            )
        )
        return
    report = run_final_evaluation(
        adapter=adapter,
        model_path=args.model_path,
        frozen_config_path=args.config_output,
        phase3a_dataset_root=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap",
        phase3a_baseline_path=PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline.json",
        holdout_root=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_phase3b_holdout",
        work_root=args.work_root,
    )
    report["performance"]["model_cold_load_ms"] = round(cold_load_ms, 6)
    report["performance"]["model_rss_delta_bytes"] = model_rss_delta_bytes
    report.pop("report_fingerprint", None)
    report["report_fingerprint"] = payload_hash(report)
    write_json_atomic(args.report_output, report)
    print(
        json.dumps(
            {
                "report_output": str(args.report_output),
                "report_fingerprint": report["report_fingerprint"],
                "acceptance_gate": report["acceptance_gate"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
