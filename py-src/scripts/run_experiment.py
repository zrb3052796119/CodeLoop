"""Experiment runner script for MAS empirical study.

Integrates with MiniCode's model and tool infrastructure to run
controlled experiments comparing single-agent vs multi-agent architectures.

Usage:
    python scripts/run_experiment.py --pilot
    python scripts/run_experiment.py --benchmark swe_bench --architectures single,sequential
    python scripts/run_experiment.py --all --num-runs 3
    python scripts/run_experiment.py --pilot --dry-run

Environment variables:
    ANTHROPIC_API_KEY: Anthropic API key (required for non-mock mode)
    EXPERIMENT_OUTPUT_DIR: Output directory (default: experiment_results)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py-src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MAS empirical study experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--pilot", action="store_true",
        help="Run pilot experiment (SWE-bench, 3 architectures, 5 tasks)",
    )
    mode.add_argument(
        "--benchmark", type=str,
        help="Run a specific benchmark: swe_bench, nl2repo, mle_bench, paper_bench",
    )
    mode.add_argument(
        "--all", action="store_true",
        help="Run all benchmarks with all architectures",
    )
    mode.add_argument(
        "--config", type=str,
        help="Run from a JSON experiment config file",
    )

    parser.add_argument(
        "--architectures", type=str,
        default="single,sequential,parallel,hierarchical,consensus,tool_mediated,adaptive",
        help="Comma-separated architecture names",
    )
    parser.add_argument(
        "--model", type=str, default="claude-sonnet-4-20250514",
        help="Model identifier",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=os.environ.get("EXPERIMENT_OUTPUT_DIR", "experiment_results"),
        help="Output directory for experiment results",
    )
    parser.add_argument(
        "--num-runs", type=int, default=3,
        help="Number of runs per configuration",
    )
    parser.add_argument(
        "--max-tasks", type=int, default=None,
        help="Override max tasks per benchmark",
    )
    parser.add_argument(
        "--mock-model", action="store_true",
        help="Use mock model adapter (no API calls, for pipeline testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print experiment configs without executing",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from minicode.experiments.configs import (
        BenchmarkConfig,
        ExperimentConfig,
        ALL_ARCHITECTURE_CONFIGS,
        ALL_BENCHMARK_CONFIGS,
        get_pilot_configs,
    )

    if args.dry_run:
        _print_dry_run(args)
        return

    if args.pilot:
        configs = get_pilot_configs(model_name=args.model)
        configs = [_apply_args_overrides(c, args) for c in configs]
        _run_configs(configs, args)

    elif args.benchmark:
        bench_configs = [b for b in ALL_BENCHMARK_CONFIGS if b.name == args.benchmark]
        if not bench_configs:
            print(f"Unknown benchmark: {args.benchmark}")
            print(f"Available: {[b.name for b in ALL_BENCHMARK_CONFIGS]}")
            sys.exit(1)

        arch_names = [a.strip() for a in args.architectures.split(",")]
        arch_configs = [a for a in ALL_ARCHITECTURE_CONFIGS if a.name in arch_names]
        if not arch_configs:
            print(f"No valid architectures in: {args.architectures}")
            sys.exit(1)

        bench = bench_configs[0]
        if args.max_tasks is not None:
            bench = _override_benchmark_tasks(bench, args)

        configs = []
        for arch in arch_configs:
            exp_id = f"{bench.name}_{arch.name}"
            configs.append(ExperimentConfig(
                experiment_id=exp_id,
                architecture=arch,
                benchmark=bench,
                model_name=args.model,
                seed=args.seed,
                output_dir=args.output_dir,
            ))

        _run_configs(configs, args)

    elif args.all:
        configs = []
        for bench in ALL_BENCHMARK_CONFIGS:
            bench_cfg = _override_benchmark_tasks(bench, args)
            for arch in ALL_ARCHITECTURE_CONFIGS:
                exp_id = f"{bench.name}_{arch.name}"
                configs.append(ExperimentConfig(
                    experiment_id=exp_id,
                    architecture=arch,
                    benchmark=bench_cfg,
                    model_name=args.model,
                    seed=args.seed,
                    output_dir=args.output_dir,
                ))

        _run_configs(configs, args)

    elif args.config:
        import json
        with open(args.config, encoding="utf-8") as f:
            data = json.load(f)
        configs = [ExperimentConfig(**c) for c in data]
        configs = [_apply_args_overrides(c, args) for c in configs]
        _run_configs(configs, args)


def _apply_args_overrides(config: ExperimentConfig, args: argparse.Namespace) -> ExperimentConfig:
    """Apply CLI argument overrides to experiment config."""
    if args.max_tasks is not None:
        config.benchmark.max_tasks = args.max_tasks
    if args.num_runs != 3:
        config.benchmark.num_runs = args.num_runs
    config.seed = args.seed
    config.output_dir = args.output_dir
    return config


def _override_benchmark_tasks(
    bench: BenchmarkConfig,
    args: argparse.Namespace,
) -> BenchmarkConfig:
    """Create a benchmark config with overridden task count and runs."""
    return BenchmarkConfig(
        name=bench.name,
        task_type=bench.task_type,
        description=bench.description,
        data_dir=bench.data_dir,
        max_tasks=args.max_tasks or bench.max_tasks,
        timeout_per_task_seconds=bench.timeout_per_task_seconds,
        num_runs=args.num_runs,
        eval_script=bench.eval_script,
        extra_params=bench.extra_params,
    )


def _print_dry_run(args: argparse.Namespace) -> None:
    """Print experiment plan without executing."""
    from minicode.experiments.configs import (
        ALL_ARCHITECTURE_CONFIGS,
        ALL_BENCHMARK_CONFIGS,
        get_pilot_configs,
    )

    print("=== Dry Run: Experiment Plan ===\n")

    if args.pilot:
        configs = get_pilot_configs(model_name=args.model)
        title = "Pilot (SWE-bench, 5 tasks, 1 run)"
    elif args.benchmark:
        arch_names = [a.strip() for a in args.architectures.split(",")]
        arch_configs = [a for a in ALL_ARCHITECTURE_CONFIGS if a.name in arch_names]
        bench = next(b for b in ALL_BENCHMARK_CONFIGS if b.name == args.benchmark)
        configs = [
            ExperimentConfig(
                experiment_id=f"{bench.name}_{a.name}",
                architecture=a,
                benchmark=bench,
                model_name=args.model,
                seed=args.seed,
                output_dir=args.output_dir,
            )
            for a in arch_configs
        ]
        title = f"Benchmark: {args.benchmark}"
    else:
        print("Use --pilot, --benchmark <name>, or --all")
        return

    print(f"Plan: {title}")
    print(f"Model: {args.model}")
    print(f"Seed: {args.seed}")
    print(f"Output: {args.output_dir}")
    print()

    total_tasks = 0
    total_runs = 0
    for c in configs:
        tasks = c.benchmark.max_tasks
        runs = c.benchmark.num_runs
        total_tasks += tasks * runs
        total_runs += runs
        print(f"  [{c.experiment_id}]")
        print(f"    Architecture: {c.architecture.name} ({c.architecture.description[:60]}...)")
        print(f"    Agents: {c.architecture.agent_count}, Adaptive: {c.architecture.adaptive}")
        print(f"    Tasks: {tasks} x {runs} runs")
        print()

    print(f"Summary: {len(configs)} configs, ~{total_tasks} total task executions")
    print(f"Estimated total time: ~{total_tasks * 5}min (varies by benchmark)")


def _run_configs(configs: list[ExperimentConfig], args: argparse.Namespace) -> None:
    """Execute experiment configurations with real MiniCode infrastructure."""
    from minicode.experiments.benchmark_runner import BenchmarkRunner
    from minicode.multi_agent.orchestrator import create_minicode_orchestrator

    print(f"Running {len(configs)} experiment configurations...")
    print(f"Model: {args.model}, Mock: {args.mock_model}, Output: {args.output_dir}")
    print()

    for i, config in enumerate(configs):
        print(f"[{i+1}/{len(configs)}] {config.experiment_id}")
        print(f"  Architecture: {config.architecture.name}")
        print(f"  Pattern: {config.architecture.pattern}, Agents: {config.architecture.agent_count}")
        print(f"  Benchmark: {config.benchmark.name} ({config.benchmark.max_tasks} tasks x {config.benchmark.num_runs} runs)")

        try:
            model = _create_model(config, args)
            tools = _create_tools(args)

            orchestrator = create_minicode_orchestrator(
                model=model,
                tools=tools,
                cwd=".",
            )
            orchestrator.experiment_mode = True
            orchestrator.seed = config.seed

            runner = BenchmarkRunner(
                orchestrator=orchestrator,
                output_dir=config.output_dir,
            )

            runs = runner.run_experiment(config)

            for run in runs:
                print(f"    Run {run.run_index}: pass_rate={run.pass_rate:.2%}, "
                      f"tasks={run.task_count}, duration={run.duration_seconds:.1f}s")

        except Exception as e:
            print(f"    ERROR: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()


def _create_model(config: ExperimentConfig, args: argparse.Namespace):
    """Create a ModelAdapter using MiniCode's model registry.

    Supports both real API models and mock mode for pipeline testing.
    """
    from minicode.model_registry import create_model_adapter

    if args.mock_model:
        return _create_mock_model(config)

    return create_model_adapter(
        model=config.model_name,
        tools=None,
        runtime=None,
        force_mock=False,
    )


def _create_tools(args: argparse.Namespace):
    """Create a ToolRegistry with MiniCode's standard tools."""
    if args.mock_model:
        return _create_mock_tools()

    from minicode.tools import create_default_tool_registry
    return create_default_tool_registry(cwd=".", runtime=None)


def _create_mock_model(config: ExperimentConfig):
    """Create a mock ModelAdapter for pipeline testing without API calls."""

    class MockModelAdapter:
        def next(self, messages, on_stream_chunk=None, store=None):
            from minicode.types import AgentStep
            return AgentStep(
                type="assistant",
                content=f"[MOCK] Response for experiment {config.experiment_id}",
                kind="final",
            )

    return MockModelAdapter()


def _create_mock_tools():
    """Create a minimal mock ToolRegistry."""
    from minicode.tooling import ToolRegistry
    return ToolRegistry(tools=[], skills=[], mcp_servers=[])


if __name__ == "__main__":
    main()