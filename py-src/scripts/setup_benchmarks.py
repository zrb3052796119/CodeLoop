"""Setup script for downloading and preparing benchmark data.

Downloads SWE-bench Verified, NL2Repo-Bench, MLE-bench, and PaperBench
data into the expected directory structure.

Usage:
    python scripts/setup_benchmarks.py --all
    python scripts/setup_benchmarks.py --benchmark swe_bench
    python scripts/setup_benchmarks.py --benchmark swe_bench --output-dir ./benchmarks
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py-src"))

BENCHMARK_URLS = {
    "swe_bench": {
        "repo": "https://github.com/princeton-nlp/SWE-bench",
        "data_file": "swe_bench_verified.jsonl",
        "description": "SWE-bench Verified: 500 real-world GitHub issues",
        "setup_instructions": [
            "git clone https://github.com/princeton-nlp/SWE-bench.git benchmarks/swe_bench_repo",
            "cp benchmarks/swe_bench_repo/swebench/harness/constants.py benchmarks/swe_bench_repo/",
            "pip install swebench",
        ],
    },
    "nl2repo": {
        "repo": "https://github.com/bytecode-systems/NL2Repo-Bench",
        "data_file": "tasks.jsonl",
        "description": "NL2Repo-Bench: 104 repository generation tasks",
        "setup_instructions": [
            "git clone https://github.com/bytecode-systems/NL2Repo-Bench.git benchmarks/nl2repo_repo",
        ],
    },
    "mle_bench": {
        "repo": "https://github.com/openai/mle-bench",
        "data_file": "tasks.jsonl",
        "description": "MLE-bench: 75 Kaggle ML competition tasks",
        "setup_instructions": [
            "git clone https://github.com/openai/mle-bench.git benchmarks/mle_bench_repo",
            "pip install mle-bench",
        ],
    },
    "paper_bench": {
        "repo": "https://github.com/openai/preparedness",
        "data_file": "tasks.jsonl",
        "description": "PaperBench Code-Dev: 20 ICML 2024 paper reproduction tasks",
        "setup_instructions": [
            "git clone https://github.com/openai/preparedness.git benchmarks/paper_bench_repo",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Setup benchmark data for MAS empirical study",
    )
    parser.add_argument(
        "--benchmark", type=str, default=None,
        help="Setup a specific benchmark: swe_bench, nl2repo, mle_bench, paper_bench",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Setup all benchmarks",
    )
    parser.add_argument(
        "--output-dir", type=str, default="benchmarks",
        help="Base output directory for benchmark data",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available benchmarks and their status",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print setup steps without executing",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list:
        _list_benchmarks(args.output_dir)
        return

    if args.all:
        benchmarks = list(BENCHMARK_URLS.keys())
    elif args.benchmark:
        if args.benchmark not in BENCHMARK_URLS:
            print(f"Unknown benchmark: {args.benchmark}")
            print(f"Available: {list(BENCHMARK_URLS.keys())}")
            sys.exit(1)
        benchmarks = [args.benchmark]
    else:
        print("Use --benchmark <name>, --all, or --list")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for bench_name in benchmarks:
        info = BENCHMARK_URLS[bench_name]
        bench_dir = output_dir / bench_name
        bench_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"Setting up: {bench_name}")
        print(f"  {info['description']}")
        print(f"  Target: {bench_dir}")
        print(f"{'='*60}")

        if args.dry_run:
            print("\n  Setup steps (dry run):")
            for step in info["setup_instructions"]:
                print(f"    $ {step}")
            continue

        for step in info["setup_instructions"]:
            print(f"\n  Running: {step}")
            try:
                result = subprocess.run(
                    step, shell=True, cwd=str(output_dir.parent),
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    print(f"    Warning: {result.stderr[:200]}")
                else:
                    print(f"    OK: {result.stdout[:200] if result.stdout else 'no output'}")
            except Exception as e:
                print(f"    Error: {e}")

        _create_data_symlink_or_copy(bench_name, bench_dir, output_dir)

    print(f"\n{'='*60}")
    print("Setup complete!")
    print(f"Benchmark data directory: {output_dir.absolute()}")
    print()
    print("Next steps:")
    print("  1. Verify data: python scripts/setup_benchmarks.py --list")
    print("  2. Run pilot:  python scripts/run_experiment.py --pilot --mock-model")
    print("  3. Run real:   python scripts/run_experiment.py --benchmark swe_bench \\")
    print("                   --architectures single,sequential --num-runs 1")


def _list_benchmarks(output_dir: str) -> None:
    """List benchmark status."""
    print("\n=== Benchmark Status ===\n")
    base = Path(output_dir)

    for name, info in BENCHMARK_URLS.items():
        bench_dir = base / name
        data_file = bench_dir / info["data_file"]
        status = "READY" if data_file.exists() else "NOT FOUND"
        icon = "✅" if data_file.exists() else "❌"

        print(f"  {icon} {name}")
        print(f"     Description: {info['description']}")
        print(f"     Data file: {data_file}")
        print(f"     Status: {status}")
        print()


def _create_data_symlink_or_copy(
    bench_name: str,
    bench_dir: Path,
    output_dir: Path,
) -> None:
    """Create expected data files if the repo was cloned successfully.

    For SWE-bench, extracts the verified split from the cloned repo.
    For other benchmarks, creates a placeholder structure.
    """
    data_file = bench_dir / BENCHMARK_URLS[bench_name]["data_file"]

    if data_file.exists():
        print(f"  Data file already exists: {data_file}")
        return

    repo_dir = output_dir / f"{bench_name}_repo"
    if repo_dir.exists():
        print(f"  Found cloned repo at: {repo_dir}")

        if bench_name == "swe_bench":
            _extract_swe_bench_data(repo_dir, bench_dir)
        else:
            _create_placeholder_tasks(bench_name, bench_dir)
    else:
        print(f"  Repo not found. Creating placeholder data file.")
        _create_placeholder_tasks(bench_name, bench_dir)


def _extract_swe_bench_data(repo_dir: Path, bench_dir: Path) -> None:
    """Extract SWE-bench Verified data from the cloned repo."""
    data_file = bench_dir / "swe_bench_verified.jsonl"

    verified_dir = repo_dir / "swebench" / "harness" / "data"
    if verified_dir.exists():
        for split_file in verified_dir.glob("*.json"):
            try:
                with open(split_file, encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, list):
                    with open(data_file, "w", encoding="utf-8") as out:
                        for item in data:
                            out.write(json.dumps(item) + "\n")
                    print(f"  Extracted {len(data)} tasks from {split_file.name}")
                    return
            except (json.JSONDecodeError, KeyError):
                continue

    print(f"  Could not extract SWE-bench data automatically.")
    print(f"  Please download from: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified")
    _create_placeholder_tasks("swe_bench", bench_dir)


def _create_placeholder_tasks(bench_name: str, bench_dir: Path) -> None:
    """Create a placeholder tasks file for a benchmark.

    Used when the actual data hasn't been downloaded yet, to allow
    testing the experiment pipeline structure.
    """
    data_file = bench_dir / BENCHMARK_URLS[bench_name]["data_file"]

    placeholders = {
        "swe_bench": [
            {"instance_id": "swe_bench_verified_001", "repo": "example/repo",
             "problem_statement": "Fix the bug in the authentication module",
             "base_commit": "abc123", "test_patch": "diff --git placeholder"},
        ],
        "nl2repo": [
            {"task_id": "nl2r_001",
             "requirement_doc": "Build a simple calculator library with add/subtract/multiply/divide",
             "test_dir": "tests", "expected_files": ["calculator.py"]},
        ],
        "mle_bench": [
            {"competition_id": "titanic",
             "description": "Predict survival on the Titanic using passenger data",
             "dataset_path": "data/titanic"},
        ],
        "paper_bench": [
            {"paper_id": "pp_001",
             "paper_text": "Paper: Attention Is All You Need...",
             "rubric": {"total_points": 100}},
        ],
    }

    tasks = placeholders.get(bench_name, [{"task_id": f"{bench_name}_001", "instruction": "Placeholder task"}])

    with open(data_file, "w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task) + "\n")

    print(f"  Created placeholder: {data_file} ({len(tasks)} tasks)")
    print(f"  Replace with real data before running experiments!")


if __name__ == "__main__":
    main()