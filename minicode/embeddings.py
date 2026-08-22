"""Shared embedding seam for Skill routing and Hybrid Memory.

Callers own retrieval or routing policy.  This module owns transport-neutral
query/document encoding, vector validation, normalization, and non-secret
provider identity used by promotion evidence.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from minicode.agent_budget import record_budgeted_model_call
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    bounded_request_timeout,
    checkpoint_model_call,
)
from minicode.pricing import (
    pricing_failure_event_payload,
    project_model_cost_event,
)
from minicode.run_events import (
    emit_event_safely,
    new_model_operation_id,
    project_model_duration_ms,
    project_model_usage,
)
from minicode.tls import create_verified_ssl_context
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.types import ModelUsage


DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"
EMBEDDING_API_KEY_ENV = "MINICODE_EMBEDDING_API_KEY"
EMBEDDING_BASE_URL_ENV = "MINICODE_EMBEDDING_BASE_URL"
EMBEDDING_MODEL_ENV = "MINICODE_EMBEDDING_MODEL"
EMBEDDING_TIMEOUT_ENV = "MINICODE_EMBEDDING_TIMEOUT_SECONDS"
_MAX_TEXT_CHARS = 4_000
_EMBED_BATCH_LIMIT = 10

REMOTE_EMBEDDING_CANARY_VERSION = "embedding-canary-v1"
MEMORY_REPRESENTATION_VERSION = "memory-structured-v1"
_REMOTE_EMBEDDING_CANARIES = (
    "Recover a stale fencing-token lease without repeating the committed write.",
    "提交成功后再发布事件，并拒绝相反的执行顺序。",
    "Keep services/billing/retry.py distinct from tools/billing/retry.py.",
)


class EmbeddingUnavailable(RuntimeError):
    """Raised when an embedding transport cannot or should not be used."""


def resolve_embedding_setting(
    workspace: str | Path | None,
    name: str,
    default: str = "",
) -> str:
    """Resolve process env, workspace env, user env, then a default."""
    from minicode.env_file import read_env_files

    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    paths = [Path.home() / ".mini-code" / ".env"]
    if workspace is not None:
        paths.append(Path(workspace) / ".env")
    return read_env_files(paths).get(name, "").strip() or default


class OpenAICompatibleEmbeddingClient:
    """Minimal synchronous client for an OpenAI-compatible /embeddings API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_EMBEDDING_BASE_URL,
        model: str = DEFAULT_EMBEDDING_MODEL,
        timeout: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingUnavailable("embedding api key is empty")
        self._api_key = api_key
        self._endpoint = f"{str(base_url).rstrip('/')}/embeddings"
        self._model = model
        self._timeout = timeout
        self._ssl_context = ssl_context

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def model(self) -> str:
        return self._model

    def embed(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_LIMIT):
            vectors.extend(
                self._embed_batch(
                    texts[start : start + _EMBED_BATCH_LIMIT],
                    call_context=call_context,
                )
            )
        return vectors

    def _embed_batch(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        context = call_context or {}
        agent_budget = context.get("agent_budget")
        event_sink = context.get("event_sink")
        cancellation_token = context.get("cancellation_token")
        deadline_monotonic = context.get("deadline_monotonic")
        purpose = str(context.get("purpose") or "embedding")[:80]
        operation_id = new_model_operation_id()
        started_at = time.monotonic()
        reservation = None
        request_started = False
        emit_event_safely(
            event_sink,
            "model.started",
            payload={"operationId": operation_id, "purpose": purpose},
        )
        estimated_tokens = max(
            1,
            sum(max(1, len(str(text)) // 4) for text in texts),
        )
        payload = json.dumps(
            {
                "model": self._model,
                "input": [str(text)[:_MAX_TEXT_CHARS] for text in texts],
                "encoding_format": "float",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            if agent_budget is not None:
                reservation = agent_budget.reserve_model_call(estimated_tokens)
            timeout = bounded_request_timeout(
                self._timeout,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            request_started = True
            if self._ssl_context is None:
                response_context = urllib.request.urlopen(
                    request, timeout=timeout
                )
            else:
                response_context = urllib.request.urlopen(
                    request,
                    timeout=timeout,
                    context=self._ssl_context,
                )
            with response_context as response:
                body = json.loads(response.read().decode("utf-8"))
            checkpoint_model_call(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
        except (TurnCancellationRequested, ModelCallDeadlineExceeded) as error:
            self._settle_failed_embedding(
                agent_budget,
                reservation,
                event_sink,
                operation_id,
                purpose,
                started_at,
                "interrupted"
                if isinstance(error, TurnCancellationRequested)
                else "timeout",
                charge_estimate=request_started,
            )
            raise
        except urllib.error.HTTPError as error:
            self._settle_failed_embedding(
                agent_budget,
                reservation,
                event_sink,
                operation_id,
                purpose,
                started_at,
                "provider_error",
                charge_estimate=request_started,
            )
            detail = ""
            try:
                detail = error.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - diagnostics only
                pass
            raise EmbeddingUnavailable(
                f"embedding endpoint returned {error.code}: {detail or error.reason}"
            ) from error
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            self._settle_failed_embedding(
                agent_budget,
                reservation,
                event_sink,
                operation_id,
                purpose,
                started_at,
                "timeout" if isinstance(error, TimeoutError) else "provider_error",
                charge_estimate=request_started,
            )
            raise EmbeddingUnavailable(f"embedding request failed: {error}") from error
        try:
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise EmbeddingUnavailable("embedding response shape unexpected")
            vectors: list[list[float]] = []
            for item in data:
                vector = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(vector, list) or not vector:
                    raise EmbeddingUnavailable("embedding vector missing")
                vectors.append([float(value) for value in vector])
        except (EmbeddingUnavailable, TypeError, ValueError) as error:
            self._settle_failed_embedding(
                agent_budget,
                reservation,
                event_sink,
                operation_id,
                purpose,
                started_at,
                "provider_error",
                charge_estimate=request_started,
            )
            if isinstance(error, EmbeddingUnavailable):
                raise
            raise EmbeddingUnavailable("embedding response vector invalid") from error
        provider_usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = None
        if isinstance(provider_usage, dict):
            raw_tokens = provider_usage.get("prompt_tokens")
            if raw_tokens is None:
                raw_tokens = provider_usage.get("total_tokens")
            if isinstance(raw_tokens, int) and not isinstance(raw_tokens, bool):
                input_tokens = max(0, raw_tokens)
        usage = ModelUsage(
            input_tokens=input_tokens if input_tokens is not None else estimated_tokens,
            output_tokens=0,
            source="provider" if input_tokens is not None else "estimated",
        )
        usage_payload = project_model_usage(usage)
        try:
            cost_payload = project_model_cost_event(
                model=self._model,
                usage=usage_payload,
                operation_id=operation_id,
            )
        except BaseException:  # noqa: BLE001 - observation stays optional
            cost_payload = pricing_failure_event_payload(operation_id)
        record_budgeted_model_call(
            agent_budget,
            model=self._model,
            usage=usage_payload,
            reservation=reservation,
            cost_payload=cost_payload,
        )
        completed: dict[str, object] = {
            "operationId": operation_id,
            "purpose": purpose,
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
            "usage": usage_payload,
        }
        duration = self._duration_ms(started_at)
        if duration is not None:
            completed["durationMs"] = duration
        emit_event_safely(event_sink, "model.completed", payload=completed)
        cost_payload["purpose"] = purpose
        emit_event_safely(event_sink, "model.costed", payload=cost_payload)
        return vectors

    @staticmethod
    def _duration_ms(started_at: float) -> int | None:
        try:
            return project_model_duration_ms(started_at, time.monotonic())
        except BaseException:  # noqa: BLE001 - observation stays optional
            return None

    @classmethod
    def _settle_failed_embedding(
        cls,
        agent_budget: Any,
        reservation: Any,
        event_sink: Any,
        operation_id: str,
        purpose: str,
        started_at: float,
        failure_kind: str,
        *,
        charge_estimate: bool,
    ) -> None:
        settle = getattr(agent_budget, "fail_model_call", None)
        if callable(settle):
            settle(reservation, charge_estimate=charge_estimate)
        payload: dict[str, object] = {
            "operationId": operation_id,
            "purpose": purpose,
            "failureKind": failure_kind,
        }
        duration = cls._duration_ms(started_at)
        if duration is not None:
            payload["durationMs"] = duration
        emit_event_safely(event_sink, "model.failed", payload=payload)

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def create_openai_compatible_embedding_client(
    workspace: str | Path | None,
) -> OpenAICompatibleEmbeddingClient | None:
    """Create the configured shared client without exposing its credential."""
    api_key = resolve_embedding_setting(workspace, EMBEDDING_API_KEY_ENV)
    if not api_key:
        return None
    try:
        timeout = float(
            resolve_embedding_setting(workspace, EMBEDDING_TIMEOUT_ENV, "10") or 10
        )
    except ValueError:
        timeout = 10.0
    return OpenAICompatibleEmbeddingClient(
        api_key,
        base_url=resolve_embedding_setting(
            workspace, EMBEDDING_BASE_URL_ENV, DEFAULT_EMBEDDING_BASE_URL
        ),
        model=resolve_embedding_setting(
            workspace, EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL
        ),
        timeout=timeout,
        ssl_context=create_verified_ssl_context(),
    )


@runtime_checkable
class EmbeddingEncoder(Protocol):
    """Small interface shared by local and remote embedding adapters."""

    @property
    def identity(self) -> dict[str, Any]: ...

    def encode_queries(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]: ...

    def encode_documents(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]: ...


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_valid_embedding_vector(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    )


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if (
        not left
        or not right
        or len(left) != len(right)
        or not is_valid_embedding_vector(left)
        or not is_valid_embedding_vector(right)
    ):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(float(value) ** 2 for value in left))
    norm_right = math.sqrt(sum(float(value) ** 2 for value in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


def _normalized_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
) -> tuple[tuple[float, ...], ...]:
    if len(vectors) != expected_count:
        raise ValueError("embedding response count mismatch")
    dimension: int | None = None
    result: list[tuple[float, ...]] = []
    for raw in vectors:
        if not raw:
            raise ValueError("embedding vector is empty")
        vector = tuple(float(value) for value in raw)
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding vector contains non-finite values")
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise ValueError("embedding vector dimensions differ")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 1e-12:
            raise ValueError("embedding vector has zero norm")
        result.append(tuple(value / norm for value in vector))
    return tuple(result)


def _canary_fingerprint(vectors: Sequence[Sequence[float]]) -> str:
    quantized = [[round(float(value), 8) for value in vector] for vector in vectors]
    return hashlib.sha256(_stable_json(quantized).encode("utf-8")).hexdigest()


class OpenAICompatibleEmbeddingEncoder:
    """Normalized encoder adapter over an OpenAI-compatible embedding client."""

    def __init__(
        self,
        client: Any,
        *,
        provider: str,
        representation_version: str = MEMORY_REPRESENTATION_VERSION,
    ) -> None:
        if not str(provider).strip():
            raise ValueError("embedding provider is required")
        self._client = client
        self._provider = str(provider).strip().lower()
        self._representation_version = str(representation_version).strip()
        self._identity: dict[str, Any] | None = None
        self._identity_lock = threading.Lock()

    @property
    def identity(self) -> dict[str, Any]:
        with self._identity_lock:
            if self._identity is None:
                vectors = self._encode(_REMOTE_EMBEDDING_CANARIES)
                model = str(
                    getattr(self._client, "model", "")
                    or getattr(self._client, "_model", "")
                )
                endpoint = str(
                    getattr(self._client, "endpoint", "")
                    or getattr(self._client, "_endpoint", "")
                )
                if not model or not endpoint:
                    raise ValueError("embedding client identity is unavailable")
                self._identity = {
                    "provider": self._provider,
                    "model_id": model,
                    "endpoint": endpoint,
                    "dimension": len(vectors[0]),
                    "representation_version": self._representation_version,
                    "canary_version": REMOTE_EMBEDDING_CANARY_VERSION,
                    "canary_fingerprint": _canary_fingerprint(vectors),
                }
            return dict(self._identity)

    def encode_queries(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        return self._encode(texts, call_context=call_context)

    def encode_documents(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        return self._encode(texts, call_context=call_context)

    def _encode(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        normalized_texts = tuple(str(text) for text in texts)
        if any(not text.strip() for text in normalized_texts):
            raise ValueError("embedding input must be non-empty text")
        if not normalized_texts:
            return ()
        embed = self._client.embed
        embed_kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(embed)
            if "call_context" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            ):
                embed_kwargs["call_context"] = call_context
        except (TypeError, ValueError):
            pass
        return _normalized_vectors(
            embed(normalized_texts, **embed_kwargs),
            expected_count=len(normalized_texts),
        )


__all__ = [
    "DEFAULT_EMBEDDING_BASE_URL",
    "DEFAULT_EMBEDDING_MODEL",
    "EmbeddingEncoder",
    "EmbeddingUnavailable",
    "EMBEDDING_API_KEY_ENV",
    "EMBEDDING_BASE_URL_ENV",
    "EMBEDDING_MODEL_ENV",
    "MEMORY_REPRESENTATION_VERSION",
    "OpenAICompatibleEmbeddingClient",
    "OpenAICompatibleEmbeddingEncoder",
    "REMOTE_EMBEDDING_CANARY_VERSION",
    "cosine_similarity",
    "create_openai_compatible_embedding_client",
    "create_verified_ssl_context",
    "is_valid_embedding_vector",
    "resolve_embedding_setting",
]
