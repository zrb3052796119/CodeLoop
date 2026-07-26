"""Analyze experiment results and generate reports.

Usage:
    python scripts/analyze_results.py --results-dir experiment_results
    python scripts/analyze_results.py --results-dir experiment_results --format csv
    python scripts/analyze_results.py --results-dir experiment_results --compare swe_bench
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py-src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze MAS experiment results")
    parser.add_argument("--results-dir", type=str, default="experiment_results",
                        help="Directory containing experiment results")
    parser.add_argument("--format", type=str, default="table", choices=["table", "csv", "json"],
                        help="Output format")
    parser.add_argument("--compare", type=str, default=None,
                        help="Compare architectures on a specific benchmark (e.g., swe_bench)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from minicode.experiments.experiment_logger import ExperimentLogger
    from minicode.experiments.metrics import (
        compute_metrics,
        aggregate_runs,
        compute_improvement_over_baseline,
    )
    from minicode.experiments.configs import ALL_ARCHITECTURE_CONFIGS

    logger = ExperimentLogger(args.results_dir)

    index_path = Path(args.results_dir) / "experiment_index.json"
    if not index_path.exists():
        print(f"No experiment index found at {index_path}")
        print("Run experiments first with: python scripts/run_experiment.py --pilot")
        return

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    if not index.get("runs"):
        print("No runs found in experiment index.")
        return

    runs_data = index["runs"]

    if args.compare:
        _print_comparison(runs_data, args.compare, args.results_dir)
        return

    if args.format == "csv":
        _export_csv(runs_data, args.results_dir, args.output)
    elif args.format == "json":
        _export_json(runs_data, args.results_dir, args.output)
    else:
        _print_summary_table(runs_data)


def _print_summary_table(runs_data: list[dict]) -> None:
    """Print a summary table of all experiment runs."""
    print("\n" + "=" * 100)
    print("MAS Empirical Study - Experiment Results Summary")
    print("=" * 100)

    benchmarks = sorted(set(r["benchmark"] for r in runs_data))
    architectures = sorted(set(r["architecture"] for r in runs_data))

    for bench in benchmarks:
        bench_runs = [r for r in runs_data if r["benchmark"] == bench]
        print(f"\n--- Benchmark: {bench} ---")
        print(f"{'Architecture':<20} {'Status':<10} {'Pass Rate':<12} {'Duration':<12}")
        print("-" * 54)

        for arch in architectures:
            arch_runs = [r for r in bench_runs if r["architecture"] == arch]
            if not arch_runs:
                continue
            for run in arch_runs:
                print(f"{arch:<20} {run['status']:<10} {run['pass_rate']:.2%}{'':>6} {run['duration_seconds']:.1f}s")


def _print_comparison(runs_data: list[dict], benchmark: str, results_dir: str) -> None:
    """Print a detailed comparison of architectures on a benchmark."""
    from minicode.experiments.metrics import compute_improvement_over_baseline, aggregate_runs
    from minicode.experiments.configs import ALL_ARCHITECTURE_CONFIGS

    bench_runs = [r for r in runs_data if r["benchmark"] == benchmark]
    if not bench_runs:
        print(f"No runs found for benchmark: {benchmark}")
        return

    print(f"\n{'=' * 80}")
    print(f"Architecture Comparison on {benchmark}")
    print(f"{'=' * 80}")

    print(f"\n{'Architecture':<20} {'Pass Rate':<12} {'Duration(s)':<12} {'vs Baseline':<15}")
    print("-" * 60)

    baseline_rate = 0.0
    results: dict[str, dict] = {}

    for run in bench_runs:
        arch = run["architecture"]
        if arch not in results:
            results[arch] = {"pass_rates": [], "durations": []}
        results[arch]["pass_rates"].append(run["pass_rate"])
        results[arch]["durations"].append(run["duration_seconds"])

        if arch == "single":
            baseline_rate = run["pass_rate"]

    for arch_name, data in results.items():
        mean_pass = sum(data["pass_rates"]) / len(data["pass_rates"])
        mean_duration = sum(data["durations"]) / len(data["durations"])

        if arch_name == "single" or baseline_rate == 0:
            improvement = "baseline"
        else:
            delta = (mean_pass - baseline_rate) / baseline_rate * 100
            improvement = f"{delta:+.1f}%"

        print(f"{arch_name:<20} {mean_pass:.2%}{'':>6} {mean_duration:.1f}s{'':>5} {improvement:<15}")

    print(f"\nBaseline (single-agent) pass rate: {baseline_rate:.2%}")
    print(f"Positive improvement = MAS beats single-agent")


def _export_csv(runs_data: list[dict], results_dir: str, output_path: str | None = None) -> None:
    """Export results to CSV format."""
    output = output_path or os.path.join(results_dir, "results.csv")

    fieldnames = [
        "experiment_id", "architecture", "benchmark", "status",
        "pass_rate", "duration_seconds",
    ]

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs_data:
            writer.writerow({
                "experiment_id": run.get("experiment_id", ""),
                "architecture": run.get("architecture", ""),
                "benchmark": run.get("benchmark", ""),
                "status": run.get("status", ""),
                "pass_rate": f"{run.get('pass_rate', 0):.4f}",
                "duration_seconds": f"{run.get('duration_seconds', 0):.1f}",
            })

    print(f"Results exported to: {output}")


def _export_json(runs_data: list[dict], results_dir: str, output_path: str | None = None) -> None:
    """Export results to JSON format."""
    output = output_path or os.path.join(results_dir, "results_summary.json")

    with open(output, "w", encoding="utf-8") as f:
        json.dump(runs_data, f, indent=2, ensure_ascii=False)

    print(f"Results exported to: {output}")


if __name__ == "__main__":
    main()