#!/usr/bin/env python3
"""Bounded, synthetic-only RecoveryLessonAgent shadow evaluation.

Default execution is scripted and tests the pipeline contract, not model
quality. --execute uses the configured reflection model and preserves its
remote-model policy. Neither mode writes memory or reads historical tasks.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import inspect
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minicode.agent_budget import AgentTurnBudget
from minicode.config import load_runtime_config
from minicode.model_registry import build_provider_config
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.recovery_lesson import (
    RecoveryLessonAgent, episode_to_task_evidence, extract_recovery_episodes,
)
from minicode.reflection_evidence import TaskEvidence, TraceEvidenceExtractor, sanitize_evidence_text
from minicode.reflection_llm import (
    ReflectionLLMConfig, StructuredGenerationResponse,
    create_structured_generation_client,
)
from minicode.reflection_synthesis import (
    ReflectionClaimValidator, ReflectionValueGate, RuleReflectionSynthesizer,
)

DEFAULT_DATASET = PROJECT_ROOT / "tests/fixtures/recovery_lesson_shadow"
ABSOLUTE_MAX_CALLS = 30
REVIEW_RUBRIC = [
    "Every factual clause is supported by the cited events, including changed inputs and later results.",
    "Causality and confidence do not exceed the observed recovery relationship.",
    "Applicability and limitations preserve the verified target and scope.",
    "The lesson adds an accurate usable abstraction beyond the same-episode rule baseline.",
    "Abstention is appropriate when the evidence cannot establish a reusable lesson.",
]


def load_dataset(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("synthetic_data") is not True
        or manifest.get("response_capture_allowed") is not True
        or document.get("schema_version") != 1
    ):
        raise ValueError("Only the versioned, explicitly synthetic shadow dataset is supported")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset must contain cases")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id")
        annotations = case.get("episode_annotations")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("Case IDs must be unique nonempty strings")
        seen.add(case_id)
        if (
            not isinstance(case.get("trace"), list)
            or not isinstance(case.get("task_description"), str)
            or not isinstance(annotations, list)
            or len(annotations) > 2
            or case.get("expected_episode_count") != len(annotations)
        ):
            raise ValueError(f"Invalid case schema: {case_id}")
        for item in annotations:
            if (
                item.get("expected_causal_status") not in {"verified", "candidate"}
                or not isinstance(item.get("expected_recovery_lesson"), bool)
                or not isinstance(item.get("allowed_scope"), str)
                or not isinstance(item.get("forbidden_conclusions"), list)
                or item.get("manual_semantic_review_required") is not True
                or item.get("offline_response", {}).get("kind") not in {"lesson", "abstain"}
            ):
                raise ValueError(f"Invalid episode annotation: {case_id}")
    return manifest, cases


def _rule_baseline(task: str, evidence: TaskEvidence) -> dict[str, Any]:
    candidate = RuleReflectionSynthesizer().synthesize(task, evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    value = ReflectionValueGate().evaluate(candidate, validation, evidence)
    accepted = set(value.accepted_claim_ids) if value.accepted else set()
    return {
        "generated_claims": [claim.to_dict() for claim in candidate.claims],
        "lessons": [claim.to_dict() for claim in validation.valid_claims if claim.claim_id in accepted],
        "validation": validation.to_dict(include_rejected_text=True),
        "value": value.to_dict(),
    }


def _scripted_response(task: str, evidence: TaskEvidence, annotation: dict[str, Any]) -> str:
    """Hand-authored propositions, with IDs resolved against this fixed trace."""
    claims = []
    script = annotation["offline_response"]
    if script["kind"] == "lesson":
        error = evidence.errors[0]
        recovery = evidence.recoveries[0]
        checks = [item for item in evidence.verification if item.result == "passed"]
        ids = list(dict.fromkeys([
            *error.source_event_ids, *recovery.event_ids,
            *(event_id for check in checks for event_id in check.event_ids),
        ]))
        claims.append({
            "claim_type": "recovery", "semantic_key": "observed_recovery_method",
            "statement": script["statement"], "epistemic_status": "confirmed",
            "evidence_ids": ids, "evidence_quotes": [error.message, recovery.action],
            "verification_ids": [item.verification_id for item in checks],
            "related_error_ids": [error.error_id],
            "related_recovery_ids": [recovery.recovery_id],
            "applies_when": annotation["allowed_scope"],
            "limitations": ["Only the cited targeted checks passed."],
        })
    return json.dumps({"task_summary": task, "outcome": evidence.outcome, "claims": claims}, ensure_ascii=False)


class ScriptedContractClient:
    """Offline canned outputs; no reported provider token or cost estimates."""
    model_id = "scripted-contract"

    def __init__(self) -> None:
        self.responses: list[str] = []

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens, **kwargs):
        del messages, timeout_seconds, max_output_tokens, kwargs
        if not self.responses:
            raise RuntimeError("scripted_response_exhausted")
        return StructuredGenerationResponse(text=self.responses.pop(0))


class BatchCallLimitReached(RuntimeError):
    pass


class MeasuredBoundedClient:
    """Authoritative count at the actual generate_json boundary."""
    def __init__(self, delegate: Any, max_calls: int, *, max_capture_bytes: int = 32_768) -> None:
        self.delegate = delegate
        self.max_calls = max_calls
        self.max_capture_bytes = max_capture_bytes
        self.records: list[dict[str, Any]] = []
        self.limit_rejections = 0
        self.case_id = ""
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return str(getattr(self.delegate, "model_id", "") or "")

    def generate_json(
        self, messages, *, timeout_seconds, max_output_tokens,
        cancellation_token=None, deadline_monotonic=None,
    ):
        with self._lock:
            if len(self.records) >= self.max_calls:
                self.limit_rejections += 1
                raise BatchCallLimitReached("batch_model_call_limit")
            record: dict[str, Any] = {
                "case_id": self.case_id, "call_index": len(self.records) + 1,
                "latency_ms": None, "input_tokens": None, "output_tokens": None,
                "estimated_cost_usd": None, "usage_source": "unavailable",
                "raw_response_text": None, "provider_failure": None,
                "stop_reason": None, "reasoning_present": None,
                "messages_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                "request_parameters": {"timeout_seconds": timeout_seconds, "max_output_tokens": max_output_tokens},
            }
            try:
                request = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "user"))
                record["episode_id"] = request.get("recovery_episode_context", {}).get("episode_id")
            except (StopIteration, ValueError, TypeError):
                record["episode_id"] = None
            self.records.append(record)
        started = time.perf_counter()
        kwargs = {"timeout_seconds": timeout_seconds, "max_output_tokens": max_output_tokens}
        parameters = inspect.signature(self.delegate.generate_json).parameters
        variadic = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
        for key, value in {
            "cancellation_token": cancellation_token, "deadline_monotonic": deadline_monotonic,
        }.items():
            if variadic or key in parameters:
                kwargs[key] = value
        try:
            response = self.delegate.generate_json(messages, **kwargs)
            sanitized_response = sanitize_evidence_text(response.text, self.max_capture_bytes)
            captured_bytes = sanitized_response.encode("utf-8")[:self.max_capture_bytes]
            record.update({
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost_usd": response.estimated_cost_usd,
                "usage_source": response.usage_source,
                "stop_reason": response.stop_reason,
                "reasoning_present": response.reasoning_present,
                "raw_response_text": captured_bytes.decode("utf-8", errors="ignore"),
                "raw_response_truncated": len(sanitized_response.encode("utf-8")) > self.max_capture_bytes,
                "raw_response_sanitized": True,
            })
            return response
        except (Exception, KeyboardInterrupt) as exc:
            # Never serialize arbitrary provider exceptions (URLs/credentials).
            record["provider_failure"] = type(exc).__name__
            raise
        finally:
            record["latency_ms"] = (time.perf_counter() - started) * 1_000


def _usage_metric(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    known = [
        item[field] for item in records
        if isinstance(item.get(field), (int, float))
        and not isinstance(item.get(field), bool)
        and math.isfinite(item[field]) and item[field] >= 0
    ]
    unknown_count = len(records) - len(known)
    return {
        "total": sum(known) if records and unknown_count == 0 else None,
        "known_subtotal": sum(known) if known else None,
        "known_count": len(known), "unknown_count": unknown_count,
    }


def evaluate_cases(
    cases: list[dict[str, Any]], *, manifest: dict[str, Any],
    config: ReflectionLLMConfig, execute: bool = False, client: Any | None = None,
    max_calls: int = 10, runtime_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or not 0 <= max_calls <= ABSOLUTE_MAX_CALLS:
        raise ValueError("max_calls must be between 0 and 30")
    if manifest.get("synthetic_data") is not True:
        raise ValueError("Only synthetic datasets may be evaluated")
    if execute and client is None:
        raise ValueError("execute requires a configured structured client")
    scripted = None if execute else ScriptedContractClient()
    measured = MeasuredBoundedClient(client if execute else scripted, max_calls, max_capture_bytes=config.max_output_bytes)
    # The reflection synthesizer reserves one call for its parent. This
    # standalone batch has no parent generation, so add exactly that sentinel
    # allowance; the actual-call wrapper independently enforces max_calls.
    budget = AgentTurnBudget(max_model_calls=max_calls + 1)
    agent = RecoveryLessonAgent(measured, replace(config, mode="llm_shadow"), max_model_calls=2)
    report: dict[str, Any] = {
        "schema_version": 1, "dataset": manifest["dataset_id"],
        "dataset_content_sha256": hashlib.sha256(json.dumps({"manifest": manifest, "cases": cases}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "mode": "real_provider" if execute else "scripted_contract",
        "synthetic_data": True, "memory_writes": False,
        "runtime": runtime_summary or {"model": config.model, "prompt_version": config.prompt_version},
        "limits": {"batch_max_calls": max_calls, "per_case_max_calls": 2, "timeout_seconds": config.timeout_seconds},
        "cases": [], "review_rubric": REVIEW_RUBRIC,
        "semantic_quality": {"verdict": "not_established", "manual_review_required": True},
        "limitations": [
            "Scripted mode validates pipeline contracts only; it is not evidence of model quality.",
            "Validator admission establishes structural gates, not semantic support or generalization quality.",
            "This fixed smoke sample is too small to establish production readiness.",
            "No changes are made to runtime configuration or durable memory.",
        ],
    }
    extractor = TraceEvidenceExtractor()
    for case in cases:
        task = case["task_description"]
        evidence = extractor.extract(task, case["trace"])
        episodes = extract_recovery_episodes(evidence)
        annotations = case["episode_annotations"]
        matches = (
            len(episodes) == case["expected_episode_count"]
            and [item.causal_status for item in episodes] == [item["expected_causal_status"] for item in annotations]
        )
        case_record: dict[str, Any] = {
            "case_id": case["case_id"], "category": case["category"],
            "precheck": {"matched": matches, "expected_episode_count": case["expected_episode_count"], "actual_episode_count": len(episodes)},
            "whole_task_rule": _rule_baseline(task, evidence), "episodes": [],
        }
        report["cases"].append(case_record)
        if not matches:
            case_record["skip_reason"] = "dataset_evidence_contract_mismatch"
            continue
        sub_evidence = [episode_to_task_evidence(episode, evidence) for episode in episodes]
        if scripted is not None:
            scripted.responses = [_scripted_response(task, sub, annotation) for sub, annotation in zip(sub_evidence, annotations)]
        measured.case_id = case["case_id"]
        start = len(measured.records)
        result = None
        interrupted = False
        case_failure = None
        try:
            result = agent.attempt_lessons(task, evidence, agent_budget=budget)
        except ModelCallDeadlineExceeded:
            # A provider-local deadline is not a deadline for this batch.
            # Retain physical-call evidence and continue with the next case.
            case_failure = "provider_deadline_exceeded"
        except (TurnCancellationRequested, KeyboardInterrupt):
            case_failure = "batch_interrupted"
            interrupted = True
            report["interrupted"] = True
        if case_failure:
            case_record["execution_failure"] = case_failure
        outcomes = {item.episode_id: item for item in result.outcomes} if result is not None else {}
        case_physical = measured.records[start:]
        physical_by_episode = {item["episode_id"]: item for item in case_physical if item.get("episode_id")}
        physical = iter(measured.records[start:])
        for episode, sub, annotation in zip(episodes, sub_evidence, annotations):
            outcome = outcomes.get(episode.episode_id)
            if outcome is not None:
                payload = asdict(outcome)
                payload["lessons"] = payload.pop("persistable_claims")
                payload["observations"] = [
                    claim.to_dict() for claim in result.observations
                    if claim.claim_id.startswith(episode.episode_id + ":")
                ]
                payload["outcome_available"] = True
                call_record = next(physical, None) if outcome.model_called else None
            else:
                call_record = physical_by_episode.get(episode.episode_id)
                unavailable_reason = (
                    case_failure if call_record and call_record.get("provider_failure")
                    else "outcome_unavailable_case_interrupted" if call_record
                    else "case_aborted_before_episode"
                )
                # The agent never returned a result for this case. A prior
                # response may be reviewable, but reconstructing accepted
                # claims would invent a result the evaluator did not receive.
                payload = {
                    "episode_id": episode.episode_id, "causal_status": episode.causal_status,
                    "attempted": call_record is not None, "model_called": call_record is not None,
                    "failure_code": unavailable_reason, "outcome_available": False,
                    "stop_reason": call_record.get("stop_reason") if call_record else None,
                    "reasoning_present": call_record.get("reasoning_present") if call_record else None,
                    "generated_claims": [], "lessons": [], "observations": [],
                    "validation_issues": [], "value_reason_codes": [],
                }
            # A defensive wrapper refusal never reached the delegate. Report
            # it as a budget skip, not a provider failure or physical call.
            if payload["model_called"] and call_record is None:
                payload["model_called"] = False
                payload["failure_code"] = "batch_model_call_limit"
            payload["physical_call"] = call_record
            case_record["episodes"].append({
                "episode_id": episode.episode_id, "annotation": annotation,
                "projected_evidence": asdict(sub),
                "rule_baseline": _rule_baseline(task, sub), "outcome": payload,
                "semantic_review": {
                    "status": "pending" if execute else "not_model_quality_evidence",
                    "verdict": None, "notes": [],
                },
            })
        if interrupted:
            report["unprocessed_case_ids"] = [item["case_id"] for item in cases[len(report["cases"]):]]
            break
    all_episodes = [item for case in report["cases"] for item in case["episodes"]]
    called = [item for item in all_episodes if item["outcome"].get("physical_call") is not None]
    candidate_overclaims = [
        item for item in all_episodes
        if item["annotation"]["expected_causal_status"] == "candidate"
        and any(claim["epistemic_status"] == "confirmed" and claim["claim_type"] in {"recovery", "root_cause"}
                for claim in item["outcome"]["lessons"])
    ]
    report["summary"] = {
        "case_count": len(cases), "processed_case_count": len(report["cases"]),
        "unprocessed_case_count": len(cases) - len(report["cases"]),
        "episode_count": len(all_episodes),
        "provider_calls": len(measured.records) if execute else 0,
        "scripted_calls": 0 if execute else len(measured.records),
        "precheck_mismatch_count": sum(not item["precheck"]["matched"] for item in report["cases"]),
        "zero_episode_precheck_count": sum(item["precheck"]["matched"] and item["precheck"]["actual_episode_count"] == 0 for item in report["cases"]),
        "budget_skipped_episode_count": sum(item["outcome"]["failure_code"] in {"agent_budget_exhausted", "batch_model_call_limit", "episode_call_limit"} for item in all_episodes),
        "provider_failure_count": sum(item["provider_failure"] is not None for item in measured.records),
        "output_limit_reached_count": sum(item["outcome"]["failure_code"] == "output_limit_reached" for item in all_episodes),
        "empty_response_count": sum(item["outcome"]["failure_code"] == "empty_response" for item in all_episodes),
        "unavailable_outcome_count": sum(not item["outcome"]["outcome_available"] for item in all_episodes),
        "case_aborted_unprocessed_episode_count": sum(item["outcome"]["failure_code"] == "case_aborted_before_episode" for item in all_episodes),
        "parsed_response_count": sum(item["outcome"]["failure_code"] is None for item in called),
        "generated_claim_count": sum(len(item["outcome"]["generated_claims"]) for item in all_episodes),
        "structurally_admitted_lesson_count": sum(len(item["outcome"]["lessons"]) for item in all_episodes),
        "candidate_confirmed_recovery_count": len(candidate_overclaims),
        "same_episode_rule_lesson_count": sum(len(item["rule_baseline"]["lessons"]) for item in all_episodes),
        "manual_review_pending_episode_count": len(called) if execute else 0,
        "usage": {field: _usage_metric(measured.records, field) for field in ("input_tokens", "output_tokens", "estimated_cost_usd")},
        "latency_ms": {
            "total": sum(item["latency_ms"] for item in measured.records) if measured.records else None,
            "median": statistics.median(item["latency_ms"] for item in measured.records) if measured.records else None,
        },
        "extraction_and_candidate_safety_passed": not candidate_overclaims and all(item["precheck"]["matched"] for item in report["cases"]),
        "pipeline_completed": (
            len(called) == len(all_episodes)
            and all(item["outcome"]["failure_code"] is None for item in called)
            and all(item["precheck"]["matched"] for item in report["cases"])
            and not report.get("interrupted", False)
        ),
        "production_readiness": "not_established",
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/recovery-lesson-shadow.json")
    args = parser.parse_args(argv)
    if not 0 <= args.max_calls <= ABSOLUTE_MAX_CALLS:
        parser.error("--max-calls must be between 0 and 30")
    if args.timeout is not None and (not math.isfinite(args.timeout) or not 1 <= args.timeout <= 120):
        parser.error("--timeout must be between 1 and 120 seconds")
    manifest, cases = load_dataset(args.dataset)
    if args.case_id:
        requested = set(args.case_id)
        if requested - {case["case_id"] for case in cases}:
            parser.error("Unknown --case-id")
        cases = [case for case in cases if case["case_id"] in requested]
    client = None
    if args.execute:
        runtime = load_runtime_config(PROJECT_ROOT)
        config = replace(ReflectionLLMConfig.from_runtime(runtime), mode="llm_shadow")
        if args.timeout is not None:
            config = replace(config, timeout_seconds=args.timeout)
        factory = create_structured_generation_client(runtime, config)
        if factory.client is None:
            parser.error(factory.unavailable_reason or "reflection_client_unavailable")
        client = factory.client
        model = config.model or str(runtime.get("model") or "")
        provider = build_provider_config(model, runtime)
        runtime_summary = {
            "model": model, "provider": provider.provider.value,
            "remote": factory.is_remote, "allow_remote_reflection": config.allow_remote_model,
            "credentials_present": bool(provider.api_key), "prompt_version": config.prompt_version,
        }
    else:
        config = ReflectionLLMConfig(mode="llm_shadow", model="scripted-contract", timeout_seconds=args.timeout or 15.0)
        runtime_summary = {"model": "scripted-contract", "provider": "none", "remote": False, "prompt_version": config.prompt_version}
    report = evaluate_cases(cases, manifest=manifest, config=config, execute=args.execute,
                            client=client, max_calls=args.max_calls, runtime_summary=runtime_summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": report["mode"], "summary": report["summary"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
