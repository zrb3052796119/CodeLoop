"""Bounded synthetic response capture and deterministic reflection replay."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from minicode.memory import assess_memory_safety
from minicode.reflection_evidence import sanitize_evidence_text
from minicode.reflection_llm import (
    LLMSynthesisAttempt,
    StructuredGenerationClient,
    StructuredGenerationResponse,
    reflection_output_schema_version,
    reflection_prompt_hash,
)
from minicode.reflection_shadow_metrics import reflection_task_identifier


CAPTURE_SCHEMA_VERSION = 1
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{8,}|bearer\s+(?!\[redacted\])\S+|"
    r"(?:api[_-]?key|password|secret|token|authorization)"
    r"\s*[:=]\s*(?!\[redacted\])\S+)"
)
_SAFE_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9._:/-]+")
_FORMAL_MEMORY_NAMES = {
    "local.json",
    "local.md",
    "memory.json",
    "memory.md",
    "project.json",
    "project.md",
    "user.json",
    "user.md",
}
_MEMORY_DIRECTORY_NAMES = {
    ".mini-code",
    ".mini-code-memory",
    ".mini-code-memory-local",
    ".mini-code-session-memory",
}
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class SyntheticCaptureError(ValueError):
    """The requested capture target or fixture is not approved for capture."""


class ReplayResponseUnavailable(RuntimeError):
    """No integrity-checked synthetic response exists for the requested task."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _safe_identifier(value: Any, *, limit: int = 120) -> str:
    text = str(value or "unknown")[:160]
    if _SECRET_VALUE_RE.search(text):
        return "redacted"
    return _SAFE_IDENTIFIER_RE.sub("_", text).strip("_")[:limit] or "unknown"


def _safe_task_identifier(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"[0-9a-fA-F]{16}", text):
        return text.lower()
    return reflection_task_identifier(text)


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_nonnegative_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed) if math.isfinite(parsed) else None


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


def _validate_capture_path(path: Path) -> None:
    if path.suffix.lower() != ".jsonl":
        raise SyntheticCaptureError("synthetic response capture must use .jsonl")
    lowered_parts = {
        part.lower() for part in path.expanduser().resolve(strict=False).parts
    }
    if lowered_parts & _MEMORY_DIRECTORY_NAMES:
        raise SyntheticCaptureError("capture path cannot be inside memory storage")
    if path.name.lower() in _FORMAL_MEMORY_NAMES:
        raise SyntheticCaptureError("capture path cannot use a memory filename")


def _load_capture_manifest(dataset_root: Path) -> dict[str, Any]:
    manifest_path = dataset_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticCaptureError("synthetic fixture manifest is unavailable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") not in {1, 2}
    ):
        raise SyntheticCaptureError("synthetic fixture manifest schema is invalid")
    if manifest.get("synthetic_data") is not True:
        raise SyntheticCaptureError("response capture requires synthetic fixture data")
    if manifest.get("response_capture_allowed") is not True:
        raise SyntheticCaptureError("synthetic response capture is not approved")
    return manifest


class ObservedStructuredGenerationClient:
    """Expose the last provider response without changing generation behavior."""

    def __init__(self, delegate: StructuredGenerationClient) -> None:
        self._delegate = delegate
        self.last_response: StructuredGenerationResponse | None = None

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> StructuredGenerationResponse:
        self.last_response = None
        response = self._delegate.generate_json(
            messages,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
        self.last_response = response
        return response


class SyntheticResponseCaptureWriter:
    """Persist only approved, bounded, sanitized synthetic model responses."""

    def __init__(
        self,
        path: str | Path,
        *,
        dataset_root: str | Path,
        max_records: int = 100,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_response_bytes: int = 32_768,
    ) -> None:
        self.path = Path(path).expanduser()
        _validate_capture_path(self.path)
        self.dataset_root = Path(dataset_root).expanduser().resolve()
        self.manifest = _load_capture_manifest(self.dataset_root)
        self.max_records = max(1, min(1_000, int(max_records)))
        self.max_file_bytes = max(4_096, min(50 * 1024 * 1024, int(max_file_bytes)))
        self.max_response_bytes = max(
            1_024, min(262_144, int(max_response_bytes))
        )

    def record(
        self,
        *,
        case_id: str,
        task_identifier: str,
        model: str,
        provider: str,
        prompt_version: str,
        response: StructuredGenerationResponse | None,
        attempt: LLMSynthesisAttempt,
    ) -> bool:
        """Record one response; malformed or secret-bearing records fail closed."""
        try:
            record = self._build_record(
                case_id=case_id,
                task_identifier=task_identifier,
                model=model,
                provider=provider,
                prompt_version=prompt_version,
                response=response,
                attempt=attempt,
            )
            encoded = json.dumps(
                record,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            if len(encoded) > self.max_file_bytes:
                return False
            if _SECRET_VALUE_RE.search(encoded.decode("utf-8", errors="ignore")):
                return False
            self._append_bounded(encoded)
            return True
        except Exception:
            return False

    def _build_record(
        self,
        *,
        case_id: str,
        task_identifier: str,
        model: str,
        provider: str,
        prompt_version: str,
        response: StructuredGenerationResponse | None,
        attempt: LLMSynthesisAttempt,
    ) -> dict[str, Any]:
        raw_response = response.text if response is not None else ""
        response_hash = _sha256_text(raw_response) if response is not None else None
        sanitized_response: str | None = None
        capture_safety_status = "not_available"
        capture_safety_reason_code: str | None = None
        replay_response_hash: str | None = None

        if response is not None:
            raw_size = len(raw_response.encode("utf-8", errors="replace"))
            if raw_size > self.max_response_bytes:
                capture_safety_status = "rejected"
                capture_safety_reason_code = "response_too_large"
            else:
                sanitized = sanitize_evidence_text(
                    raw_response,
                    self.max_response_bytes,
                )
                safety = assess_memory_safety(
                    sanitized,
                    source="synthetic_reflection_response_capture",
                )
                capture_safety_status = _safe_identifier(safety.status)
                if safety.allowed and not _SECRET_VALUE_RE.search(sanitized):
                    sanitized_response = sanitized
                    replay_response_hash = _sha256_text(sanitized)
                else:
                    capture_safety_reason_code = "capture_content_not_safe"

        version = (
            prompt_version
            if prompt_version
            in {
                "baseline",
                "calibrated",
                "calibrated_verbose",
                "calibrated_compact",
            }
            else "calibrated_compact"
        )
        response_usage = response or StructuredGenerationResponse(text="")
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dataset_id": _safe_identifier(self.manifest.get("dataset_id")),
            "case_id": _safe_identifier(case_id),
            "task_identifier": _safe_task_identifier(task_identifier),
            "model": _safe_identifier(model),
            "provider": _safe_identifier(provider),
            "prompt_version": version,
            "prompt_version_hash": reflection_prompt_hash(version),
            "output_schema_version": reflection_output_schema_version(version),
            "sanitized_response": sanitized_response,
            "response_hash": response_hash,
            "replay_response_hash": replay_response_hash,
            "capture_safety_status": capture_safety_status,
            "capture_safety_reason_code": capture_safety_reason_code,
            "parser_result": "success" if attempt.success else "failure",
            "parser_failure_code": (
                _safe_identifier(attempt.failure_code)
                if attempt.failure_code
                else None
            ),
            "parser_failure_detail_code": (
                _safe_identifier(attempt.failure_detail_code)
                if attempt.failure_detail_code
                else None
            ),
            "input_safety_status": _safe_identifier(attempt.input_safety_status),
            "output_safety_status": _safe_identifier(attempt.output_safety_status),
            "input_tokens": _optional_nonnegative_int(response_usage.input_tokens),
            "output_tokens": _optional_nonnegative_int(response_usage.output_tokens),
            "cache_read_tokens": _optional_nonnegative_int(
                response_usage.cache_read_tokens
            ),
            "cache_creation_tokens": _optional_nonnegative_int(
                response_usage.cache_creation_tokens
            ),
            "usage_source": _safe_identifier(response_usage.usage_source),
            "estimated_cost_usd": _optional_nonnegative_float(
                response_usage.estimated_cost_usd
            ),
            "latency_ms": _optional_nonnegative_float(response_usage.latency_ms),
        }

    def _append_bounded(self, encoded: bytes) -> None:
        lock_path = self.path.with_name(self.path.name + ".lock")
        with _path_lock(self.path), _process_lock(lock_path):
            existing: list[bytes] = []
            if self.path.exists():
                for line in self.path.read_bytes().splitlines():
                    try:
                        item = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if (
                        not isinstance(item, dict)
                        or item.get("schema_version") != CAPTURE_SCHEMA_VERSION
                    ):
                        continue
                    if _SECRET_VALUE_RE.search(
                        line.decode("utf-8", errors="ignore")
                    ):
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
                prefix=self.path.name + ".",
                suffix=".tmp",
                dir=self.path.parent,
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


def load_synthetic_response_capture(path: str | Path) -> list[dict[str, Any]]:
    """Load structurally valid capture records without interpreting responses."""
    source = Path(path).expanduser()
    if not source.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("schema_version") == CAPTURE_SCHEMA_VERSION:
            records.append(item)
    return records


class ReplayStructuredGenerationClient:
    """Replay exact synthetic responses; never owns a network fallback."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.call_count = 0
        self._records: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for record in records:
            task_identifier = str(record.get("task_identifier") or "")
            response = record.get("sanitized_response")
            expected_hash = str(record.get("replay_response_hash") or "")
            if (
                re.fullmatch(r"[0-9a-f]{16}", task_identifier)
                and isinstance(response, str)
                and expected_hash == _sha256_text(response)
            ):
                self._records[task_identifier].append(dict(record))

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> StructuredGenerationResponse:
        del timeout_seconds, max_output_tokens
        self.call_count += 1
        task_identifier = self._task_identifier_from_messages(messages)
        queue = self._records.get(task_identifier)
        if not queue:
            raise ReplayResponseUnavailable(
                f"no synthetic replay response for task {task_identifier}"
            )
        record = queue.popleft()
        response = str(record["sanitized_response"])
        if str(record.get("replay_response_hash")) != _sha256_text(response):
            raise ReplayResponseUnavailable("synthetic replay response hash mismatch")
        usage_source = str(record.get("usage_source") or "unavailable")
        if usage_source not in {"provider", "estimated", "unavailable"}:
            usage_source = "unavailable"
        return StructuredGenerationResponse(
            text=response,
            input_tokens=_optional_nonnegative_int(record.get("input_tokens")),
            output_tokens=_optional_nonnegative_int(record.get("output_tokens")),
            cache_read_tokens=_optional_nonnegative_int(
                record.get("cache_read_tokens")
            ),
            cache_creation_tokens=_optional_nonnegative_int(
                record.get("cache_creation_tokens")
            ),
            usage_source=usage_source,  # type: ignore[arg-type]
            estimated_cost_usd=_optional_nonnegative_float(
                record.get("estimated_cost_usd")
            ),
            latency_ms=_optional_nonnegative_float(record.get("latency_ms")),
        )

    @staticmethod
    def _task_identifier_from_messages(messages: list[dict[str, str]]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message.get("content", ""))
                task_description = payload["task_evidence"]["task_description"]
            except (KeyError, TypeError, json.JSONDecodeError):
                break
            if isinstance(task_description, str):
                return reflection_task_identifier(task_description)
        raise ReplayResponseUnavailable("replay request has no task identifier")


__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "ObservedStructuredGenerationClient",
    "ReplayResponseUnavailable",
    "ReplayStructuredGenerationClient",
    "SyntheticCaptureError",
    "SyntheticResponseCaptureWriter",
    "load_synthetic_response_capture",
]
