"""Fit and evaluate semantic-intent confidence from content-free labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from minicode.intent_semantic_calibration import (
    INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    CalibrationReport,
    evaluate_confidence_calibration,
    fit_confidence_calibration,
    load_calibration_observations,
    write_calibration_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "observations",
        type=Path,
        help=(
            "JSON array/object containing whole-response rawConfidence, "
            "correct, and split labels"
        ),
    )
    parser.add_argument("artifact_output", type=Path)
    parser.add_argument(
        "--version",
        required=True,
        help=(
            "Calibration artifact version (for example 2026-09-experiment-holdout-v1)"
        ),
    )
    parser.add_argument(
        "--model-key",
        required=True,
        help="Exact canonical model identifier used by the classifier runtime",
    )
    parser.add_argument(
        "--classifier-contract-version",
        default=INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    )
    parser.add_argument("--fit-split", required=True)
    parser.add_argument("--evaluation-split", required=True)
    parser.add_argument("--bin-count", type=int, default=10)
    parser.add_argument("--prior-alpha", type=float, default=1.0)
    parser.add_argument("--prior-beta", type=float, default=1.0)
    parser.add_argument("--acceptance-threshold", type=float, default=0.72)
    parser.add_argument("--report-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fit_split == args.evaluation_split:
        parser.error("--fit-split and --evaluation-split must be distinct")
    observations = load_calibration_observations(args.observations)
    calibration = fit_confidence_calibration(
        observations,
        version=args.version,
        model_key=args.model_key,
        classifier_contract_version=args.classifier_contract_version,
        bin_count=args.bin_count,
        prior_alpha=args.prior_alpha,
        prior_beta=args.prior_beta,
        split=args.fit_split,
    )
    fit_metrics = evaluate_confidence_calibration(
        calibration,
        observations,
        acceptance_threshold=args.acceptance_threshold,
        split=args.fit_split,
    )
    evaluation_metrics = evaluate_confidence_calibration(
        calibration,
        observations,
        acceptance_threshold=args.acceptance_threshold,
        split=args.evaluation_split,
    )
    calibration.write(args.artifact_output)
    report = CalibrationReport(
        calibration_version=calibration.version,
        classifier_contract_version=calibration.classifier_contract_version,
        model_key=calibration.model_key,
        production_fit=calibration.production_fit,
        fit_split=args.fit_split,
        evaluation_split=args.evaluation_split,
        fit_metrics=fit_metrics,
        evaluation_metrics=evaluation_metrics,
    )
    if args.report_output is not None:
        write_calibration_report(args.report_output, report)
    print(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
