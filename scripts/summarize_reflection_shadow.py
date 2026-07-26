#!/usr/bin/env python3
"""Summarize privacy-bounded reflection shadow metrics JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(summary: dict[str, Any]) -> str:
    latency = summary["latency_ms"]
    tokens = summary["tokens"]
    lines = [
        "# Reflection Shadow Metrics",
        "",
        f"- Records: **{summary['record_count']}**",
        f"- Eligible / sampled / called: **{summary['eligible_count']} / {summary['sampled_count']} / {summary['call_count']}**",
        f"- Eligibility / sampling / call: **{_percent(summary['eligibility_rate'])} / {_percent(summary['sample_rate'])} / {_percent(summary['call_rate'])}**",
        f"- Fallback / LLM value accept / rule value accept: **{_percent(summary['fallback_rate'])} / {_percent(summary['llm_value_accept_rate'])} / {_percent(summary['rule_value_accept_rate'])}**",
        f"- Parse / timeout / provider failure: **{_percent(summary['parse_failure_rate'])} / {_percent(summary['timeout_rate'])} / {_percent(summary['provider_failure_rate'])}**",
        f"- Latency average / median / P95: **{latency['average']:.1f} / {latency['median']:.1f} / {latency['p95']:.1f} ms**",
        f"- Tokens input / output: **{tokens['input']} / {tokens['output']}**",
        f"- Usage sources: **{json.dumps(summary['usage_sources'], sort_keys=True)}**",
        f"- Estimated cost: **${summary['estimated_cost_usd']:.6f}**",
        f"- Fallback reasons: **{json.dumps(summary['fallback_reasons'], sort_keys=True)}**",
        f"- Validator issues: **{json.dumps(summary['validator_issue_code_counts'], sort_keys=True)}**",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    from minicode.reflection_shadow_metrics import (
        load_shadow_metric_records,
        summarize_shadow_metrics,
    )

    summary = summarize_shadow_metrics(load_shadow_metric_records(args.input))
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(summary), encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
