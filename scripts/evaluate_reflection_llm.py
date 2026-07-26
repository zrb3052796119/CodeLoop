#!/usr/bin/env python3
"""Generate deterministic offline reports for reflection LLM shadow mode."""

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


def _render_accuracy(report: dict[str, Any]) -> str:
    lines = [
        "# Reflection LLM Shadow Accuracy",
        "",
        f"Independent holdout: **{report['case_count']} cases**. The provider is a deterministic offline scripted fixture; no network model was called.",
        "",
        "| Mode / branch | Validator claim P | R | F1 | Value P | R | F1 | Low-value false-write |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "rule": "rule",
        "llm_shadow_rule": "llm_shadow / rule",
        "llm_shadow_llm": "llm_shadow / LLM",
        "llm": "llm with fallback",
    }
    for key, label in labels.items():
        mode = report["modes"][key]
        claims = mode["claim_quality"]["validator"]
        value = mode["value_quality"]
        lines.append(
            f"| {label} | {_percent(claims['precision'])} | {_percent(claims['recall'])} | {_percent(claims['f1'])} | "
            f"{_percent(value['precision'])} | {_percent(value['recall'])} | {_percent(value['f1'])} | {_percent(value['low_value_false_write_rate'])} |"
        )
    shadow = report["modes"]["llm_shadow_llm"]
    runtime = report["llm_runtime"]
    lines.extend(
        [
            "",
            "## Safety And Validity",
            "",
            f"- Validator unsupported accepted claims: **{shadow['unsupported_accepted_claims']}**",
            f"- Invalid evidence references after parsing/validation: **{shadow['invalid_evidence_references']}**",
            f"- Epistemic mismatches accepted: **{shadow['epistemic_mismatches']}**",
            f"- Forbidden accepted claims: **{shadow['forbidden_accepted_claims']}**",
            f"- Candidate root-cause overclaims: **{shadow['root_cause_candidate_overclaim']}**; persistable root-cause overclaims: **{shadow['root_cause_overclaim']}**",
            f"- Eligibility/call/fallback rates: **{_percent(runtime['eligibility_rate'])} / {_percent(runtime['call_rate'])} / {_percent(runtime['fallback_rate'])}**",
            f"- Scripted latency average/median/P95: **{runtime['latency_ms']['average']:.1f} / {runtime['latency_ms']['median']:.1f} / {runtime['latency_ms']['p95']:.1f} ms**",
            f"- Average input/output tokens: **{runtime['average_input_tokens']:.1f} / {runtime['average_output_tokens']:.1f}**; estimated scripted total cost: **${runtime['estimated_cost_usd']:.4f}**",
            "",
            "## Comparative Cases",
            "",
            "LLM-only correct cases after Validator: " + ", ".join(f"`{case}`" for case in report["comparative_cases"]["llm_only_correct_claims"]),
            "",
            "Rule-only correct cases after Validator: " + ", ".join(f"`{case}`" for case in report["comparative_cases"]["rule_only_correct_claims"]),
            "",
            "## Interpretation Boundary",
            "",
            "Scripted candidates demonstrate that the architecture can improve cross-event claim recall while retaining deterministic safety gates. They do not establish that any real provider will produce these candidates reliably.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python3 scripts/evaluate_reflection_llm.py --dataset tests/fixtures/reflection_llm_holdout --output artifacts/reflection-accuracy-llm-shadow.json --markdown docs/reflection-accuracy-llm-shadow.md --comparison docs/reflection-llm-shadow-comparison.md",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _render_comparison(report: dict[str, Any]) -> str:
    rule = report["modes"]["rule"]
    shadow = report["modes"]["llm_shadow_llm"]
    production = report["modes"]["llm"]
    runtime = report["llm_runtime"]
    return "\n".join(
        [
            "# Reflection LLM Shadow Comparison",
            "",
            "## What Improved",
            "",
            f"The scripted LLM branch raised Validator-stage claim recall from **{_percent(rule['claim_quality']['validator']['recall'])}** to **{_percent(shadow['claim_quality']['validator']['recall'])}** and precision from **{_percent(rule['claim_quality']['validator']['precision'])}** to **{_percent(shadow['claim_quality']['validator']['precision'])}**. The gains are concentrated in bilingual and multi-event policies where one reusable claim combines two or three explicit DecisionEvidence records.",
            "",
            f"Experimental `llm` with deterministic fallback reached claim F1 **{_percent(production['claim_quality']['validator']['f1'])}** and value F1 **{_percent(production['value_quality']['f1'])}**, while low-value false-write remained **{_percent(production['value_quality']['low_value_false_write_rate'])}**.",
            "",
            "## What Failed",
            "",
            "The causal-trap case generated one unsupported confirmed root-cause candidate from an error followed by a passing test. `ReflectionClaimValidator` rejected it for missing decision/recovery evidence, failed statement grounding, missing full-chain references, and missing targeted-test limitations. No root-cause overclaim became persistable.",
            "",
            "The shadow LLM branch also loses recall whenever eligibility declines a call or a scripted provider/parser failure occurs. The production `llm` branch recovers those cases through unchanged rule fallback; shadow reporting deliberately does not hide them.",
            "",
            "## Configuration",
            "",
            "The default remains `reflectionSynthesizerMode: \"rule\"`. `llm_shadow` evaluates a non-production candidate; `llm` is experimental. `allowRemoteReflectionModel` defaults to `false`, so a remote reflection model is not contacted without an explicit opt-in.",
            "",
            "```json",
            "{",
            "  \"reflectionSynthesizerMode\": \"llm_shadow\",",
            "  \"reflectionModel\": \"local-model-name\",",
            "  \"reflectionLLMTimeoutSeconds\": 15,",
            "  \"reflectionLLMMaxOutputTokens\": 1200,",
            "  \"reflectionLLMMaxInputBytes\": 24576,",
            "  \"reflectionLLMMaxOutputBytes\": 32768,",
            "  \"reflectionLLMMaxClaims\": 8,",
            "  \"allowRemoteReflectionModel\": false",
            "}",
            "```",
            "",
            "## Failure Accounting",
            "",
            f"- Timeout rate: **{_percent(runtime['timeout_rate'])}**",
            f"- Parse failure rate: **{_percent(runtime['parse_failure_rate'])}**",
            f"- Provider failure rate: **{_percent(runtime['provider_failure_rate'])}**",
            f"- Tool-call rejection rate: **{_percent(runtime['tool_call_rejection_rate'])}**",
            "- Fallback reasons: " + ", ".join(f"`{key}`={value}" for key, value in report["fallback_reasons"].items()),
            "",
            "## Recommendation",
            "",
            "Keep `rule` as the default and continue `llm_shadow` evaluation. The offline holdout proves isolation, validation, and potential semantic benefit, but it is not sufficient evidence for default or broad production `llm` activation because no real model distribution was measured.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    args = parser.parse_args()

    from scripts.reflection_llm_evaluator import evaluate_holdout, write_report

    report = evaluate_holdout(args.dataset)
    for path in (args.output, args.markdown, args.comparison):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, args.output)
    args.markdown.write_text(_render_accuracy(report), encoding="utf-8")
    args.comparison.write_text(_render_comparison(report), encoding="utf-8")
    print(json.dumps({"cases": report["case_count"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
