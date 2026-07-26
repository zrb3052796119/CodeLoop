"""Privacy-bounded metrics for the optional reflection LLM shadow branch."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import tempfile
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
_SAFE_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9._:/-]+")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+\S+|"
    r"(?:api[_-]?key|password|secret|token|authorization)\s*[:=]\s*\S+)"
)
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def reflection_task_identifier(task_description: str) -> str:
    from minicode.reflection_evidence import sanitize_evidence_text

    bounded = sanitize_evidence_text(task_description, 200)
    return hashlib.sha256(bounded.encode("utf-8")).hexdigest()[:16]


def deterministic_shadow_sample(task_identifier: str, sample_rate: float) -> bool:
    """Select a stable fraction without process-global randomness."""
    rate = max(0.0, min(1.0, float(sample_rate)))
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    bucket = int(hashlib.sha256(task_identifier.encode("utf-8")).hexdigest()[:16], 16)
    return bucket < int(rate * (1 << 64))


def _safe_identifier(value: Any) -> str:
    text = str(value or "unknown")[:160]
    if _SECRET_VALUE_RE.search(text):
        return "redacted"
    return _SAFE_IDENTIFIER_RE.sub("_", text).strip("_")[:120] or "unknown"


def _safe_task_hash(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"[0-9a-fA-F]{16}", text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


class ReflectionShadowMetricsRecorder:
    """Write an allowlisted, bounded JSONL stream outside memory storage."""

    def __init__(
        self,
        path: str | Path,
        *,
        model: str,
        provider: str,
        max_records: int = 5_000,
        max_file_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.path = Path(path).expanduser()
        if self.path.suffix.lower() != ".jsonl":
            raise ValueError("reflection shadow metrics path must end in .jsonl")
        self.model = _safe_identifier(model)
        self.provider = _safe_identifier(provider)
        self.max_records = max(1, min(100_000, int(max_records)))
        self.max_file_bytes = max(4_096, min(100 * 1024 * 1024, int(max_file_bytes)))

    def record(self, comparison: Any) -> bool:
        """Record one comparison; all I/O and validation failures are isolated."""
        try:
            record = self._build_record(comparison)
            encoded = json.dumps(
                record,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            if len(encoded) > self.max_file_bytes or _SECRET_VALUE_RE.search(
                encoded.decode("utf-8", errors="ignore")
            ):
                return False
            self._append_bounded(encoded)
            return True
        except Exception:
            return False

    def _build_record(self, comparison: Any) -> dict[str, Any]:
        eligibility = comparison.eligibility_decision
        issue_counts = dict(getattr(comparison, "validator_issue_code_counts", {}))
        suppression_reasons = Counter(
            dict(getattr(comparison, "suppression_reason_codes", {}) or {}).values()
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_identifier": _safe_task_hash(comparison.task_identifier),
            "model": self.model,
            "provider": self.provider,
            "mode": "llm_shadow",
            "eligible": bool(eligibility.eligible),
            "eligibility_reason_codes": [
                _safe_identifier(code) for code in eligibility.reason_codes[:16]
            ],
            "estimated_value": _safe_identifier(eligibility.estimated_value),
            "sampled": bool(getattr(comparison, "sampled", False)),
            "sampled_out": bool(getattr(comparison, "sampled_out", False)),
            "llm_called": bool(comparison.llm_called),
            "rule_claim_count": int(comparison.rule_claim_count),
            "llm_claim_count": int(comparison.llm_claim_count),
            "rule_valid_claim_count": int(comparison.rule_valid_claim_count),
            "llm_valid_claim_count": int(comparison.llm_valid_claim_count),
            "rule_persistable_claim_count": len(
                getattr(comparison, "rule_persistable_claim_ids", ())
            ),
            "llm_persistable_claim_count": len(
                getattr(comparison, "llm_persistable_claim_ids", ())
            ),
            "gap_fill_selection_source": _safe_identifier(
                getattr(comparison, "gap_fill_selection_source", "rule")
            ),
            "replace_selection_source": _safe_identifier(
                getattr(comparison, "replace_selection_source", "rule")
            ),
            "gap_fill_attempted": bool(
                getattr(comparison, "gap_fill_attempted", False)
            ),
            "replace_regression": bool(
                getattr(comparison, "replace_regression", False)
            ),
            "suppressed_claim_count": len(
                getattr(comparison, "suppressed_claim_ids", ())
            ),
            "suppression_reason_code_counts": {
                _safe_identifier(code): int(value)
                for code, value in sorted(suppression_reasons.items())
            },
            "rule_value_accepted": bool(comparison.rule_value_decision.get("accepted")),
            "llm_value_accepted": (
                bool(comparison.llm_value_decision.get("accepted"))
                if comparison.llm_value_decision is not None
                else None
            ),
            "rule_durable_signal_codes": [
                _safe_identifier(code) for code in comparison.rule_durable_signals[:16]
            ],
            "llm_durable_signal_codes": [
                _safe_identifier(code) for code in comparison.llm_durable_signals[:16]
            ],
            "validator_issue_code_counts": {
                _safe_identifier(code): max(0, int(count))
                for code, count in sorted(issue_counts.items())
            },
            "fallback_reason": (
                _safe_identifier(comparison.fallback_reason)
                if comparison.fallback_reason
                else None
            ),
            "parser_failure_detail_code": (
                _safe_identifier(comparison.parser_failure_detail_code)
                if getattr(comparison, "parser_failure_detail_code", None)
                else None
            ),
            "parse_schema_failure": bool(comparison.parse_schema_failure),
            "timeout_failure": bool(comparison.timeout_failure),
            "provider_failure": bool(comparison.provider_failure),
            "tool_call_failure": comparison.fallback_reason == "tool_call_rejected",
            "input_safety_status": _safe_identifier(comparison.input_safety_status),
            "output_safety_status": _safe_identifier(comparison.output_safety_status),
            "input_truncated": bool(comparison.input_truncated),
            "latency_ms": max(0.0, float(comparison.latency_ms)),
            "input_tokens": _optional_nonnegative(comparison.input_tokens),
            "output_tokens": _optional_nonnegative(comparison.output_tokens),
            "cache_read_tokens": _optional_nonnegative(
                getattr(comparison, "cache_read_tokens", None)
            ),
            "cache_creation_tokens": _optional_nonnegative(
                getattr(comparison, "cache_creation_tokens", None)
            ),
            "usage_source": _safe_identifier(
                getattr(comparison, "usage_source", "unavailable")
            ),
            "estimated_cost_usd": _optional_nonnegative_float(
                comparison.estimated_cost_usd
            ),
            "semantic_key_overlap_count": len(comparison.semantic_key_overlap),
        }

    def _append_bounded(self, encoded: bytes) -> None:
        lock_path = self.path.with_name(self.path.name + ".lock")
        with _path_lock(self.path), _process_lock(lock_path):
            existing: list[bytes] = []
            if self.path.exists():
                for line in self.path.read_bytes().splitlines():
                    if _SECRET_VALUE_RE.search(
                        line.decode("utf-8", errors="ignore")
                    ):
                        continue
                    try:
                        item = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(item, dict) or item.get("schema_version") != SCHEMA_VERSION:
                        continue
                    existing.append(line + b"\n")
            lines = (existing + [encoded])[-self.max_records :]
            kept: list[bytes] = []
            used = 0
            for line in reversed(lines):
                if used + len(line) > self.max_file_bytes:
                    break
                kept.append(line)
                used += len(line)
            payload = b"".join(reversed(kept))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


def _optional_nonnegative(value: Any) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


def _optional_nonnegative_float(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return max(0.0, parsed) if math.isfinite(parsed) else None


def load_shadow_metric_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    source = Path(path)
    if not source.exists():
        return records
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("schema_version") == SCHEMA_VERSION:
            records.append(item)
    return records


def summarize_shadow_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    eligible = sum(bool(item.get("eligible")) for item in records)
    sampled = sum(bool(item.get("sampled")) for item in records)
    called = sum(bool(item.get("llm_called")) for item in records)
    fallbacks = Counter(
        str(item["fallback_reason"])
        for item in records
        if item.get("fallback_reason")
    )
    called_records = [item for item in records if item.get("llm_called")]
    latencies = sorted(float(item.get("latency_ms") or 0.0) for item in called_records)
    usage_sources = Counter(str(item.get("usage_source") or "unavailable") for item in called_records)
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in called_records)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in called_records)
    total_cost = sum(float(item.get("estimated_cost_usd") or 0.0) for item in called_records)
    validator_issues: Counter[str] = Counter()
    suppression_reasons: Counter[str] = Counter()
    for item in records:
        for code, issue_count in dict(
            item.get("validator_issue_code_counts") or {}
        ).items():
            validator_issues[str(code)] += int(issue_count)
        for code, reason_count in dict(
            item.get("suppression_reason_code_counts") or {}
        ).items():
            suppression_reasons[str(code)] += int(reason_count)
    return {
        "record_count": count,
        "eligible_count": eligible,
        "sampled_count": sampled,
        "call_count": called,
        "eligibility_rate": eligible / count if count else 0.0,
        "sample_rate": sampled / eligible if eligible else 0.0,
        "call_rate": called / sampled if sampled else 0.0,
        "fallback_rate": sum(fallbacks.values()) / count if count else 0.0,
        "fallback_reasons": dict(sorted(fallbacks.items())),
        "validator_issue_code_counts": dict(sorted(validator_issues.items())),
        "suppression_reason_code_counts": dict(
            sorted(suppression_reasons.items())
        ),
        "gap_fill_attempt_count": sum(
            bool(item.get("gap_fill_attempted")) for item in records
        ),
        "replace_regression_count": sum(
            bool(item.get("replace_regression")) for item in records
        ),
        "llm_value_accept_rate": _boolean_rate(called_records, "llm_value_accepted"),
        "rule_value_accept_rate": _boolean_rate(records, "rule_value_accepted"),
        "parse_failure_rate": _boolean_rate(called_records, "parse_schema_failure"),
        "timeout_rate": _boolean_rate(called_records, "timeout_failure"),
        "provider_failure_rate": _boolean_rate(called_records, "provider_failure"),
        "latency_ms": {
            "average": statistics.fmean(latencies) if latencies else 0.0,
            "median": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile_nearest_rank(latencies, 0.95),
        },
        "tokens": {"input": input_tokens, "output": output_tokens},
        "usage_sources": dict(sorted(usage_sources.items())),
        "estimated_cost_usd": total_cost,
    }


def _boolean_rate(records: list[dict[str, Any]], key: str) -> float:
    present = [item.get(key) for item in records if item.get(key) is not None]
    return sum(bool(value) for value in present) / len(present) if present else 0.0


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


__all__ = [
    "ReflectionShadowMetricsRecorder",
    "deterministic_shadow_sample",
    "load_shadow_metric_records",
    "reflection_task_identifier",
    "summarize_shadow_metrics",
]
