"""Opt-in streaming transport and cross-process receipts for live evaluations.

This is an experiment adapter, not a production transport replacement. Install
before constructing agents, and supervise the entire trial process separately
for a hard wall-clock boundary. The SQLite file contains private model outputs.
No URL, credentials, request headers, or exception messages are persisted.

Calls are admitted atomically across processes. Token admission reserves an
estimated input plus bounded output; it is NOT an exact tokenizer-based cap.
Unknown provider usage retains that reservation and remains unknown in reports.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from functools import wraps
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import sys
import threading
import time
from typing import Any
import urllib.error
import uuid


class EvaluationBudgetExceeded(RuntimeError):
    pass


class EvaluationIdleTimeout(TimeoutError):
    pass


class EvaluationStreamError(RuntimeError):
    pass


def route_fingerprint(runtime: dict) -> str:
    """Fingerprint the *resolved adapter* route, never a credential."""
    from minicode.openai_adapter import _chat_completions_url, _get_openai_base_url

    url = _chat_completions_url(_get_openai_base_url(runtime))
    return hashlib.sha256(url.encode()).hexdigest()


@dataclass(frozen=True)
class TransportConfig:
    ledger_path: str
    expected_model: str
    expected_route_sha256: str
    deadline_epoch: float | None
    max_model_calls: int | None = 32
    max_total_tokens: int | None = 200_000
    max_output_tokens: int = 4096
    connect_timeout_seconds: float = 30.0
    idle_timeout_seconds: float = 60.0
    request_timeout_seconds: float | None = 180.0
    worker_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ledger_path", str(self.ledger_path))
        if not Path(self.ledger_path).is_absolute():
            raise ValueError("ledger_path must be absolute")
        if not self.expected_model or not re.fullmatch(
            r"[a-f0-9]{64}", self.expected_route_sha256
        ):
            raise ValueError("expected model/route is invalid")
        for name in ("max_model_calls", "max_total_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if value is None and name != "max_output_tokens":
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "deadline_epoch", "connect_timeout_seconds", "idle_timeout_seconds",
            "request_timeout_seconds",
        ):
            value = getattr(self, name)
            if value is None and name in {"deadline_epoch", "request_timeout_seconds"}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")


def _token(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(raw: object) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    inputs = _token(raw.get("prompt_tokens"))
    outputs = _token(raw.get("completion_tokens"))
    cache = _token(details.get("cached_tokens"))
    if cache is None:
        cache = _token(raw.get("cached_tokens"))
    if cache is not None and (inputs is None or cache > inputs):
        cache = None
    created = _token(raw.get("cache_creation_input_tokens"))
    return {
        "input_tokens": inputs, "output_tokens": outputs,
        "cache_read_tokens": cache, "cache_creation_tokens": created,
        "source": "provider" if inputs is not None and outputs is not None else "unavailable",
    }


def _safe_provider_usage(raw: object) -> dict[str, Any] | None:
    """Persist only known numeric receipt fields, never arbitrary provider text."""
    if not isinstance(raw, dict):
        return None
    result = {
        key: value for key in (
            "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens",
            "cache_creation_input_tokens", "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        ) if (value := _token(raw.get(key))) is not None
    }
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = raw.get(key)
        if isinstance(details, dict):
            result[key] = {
                name: value for name in (
                    "cached_tokens", "reasoning_tokens", "audio_tokens",
                    "accepted_prediction_tokens", "rejected_prediction_tokens",
                ) if (value := _token(details.get(name))) is not None
            }
    return result


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", value) else None


class SharedLedger:
    """One durable row per admitted HTTP attempt, shared by all interpreters."""

    def __init__(self, config: TransportConfig):
        self.config = config
        path = Path(config.ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, config TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                call INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL,
                charge_tokens INTEGER NOT NULL, data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admission_rejections (
                rejection INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)""")
            frozen = asdict(config)
            frozen.pop("worker_id")
            encoded = json.dumps(frozen, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (encoded,))
            if db.execute("SELECT config FROM settings WHERE id=1").fetchone()[0] != encoded:
                raise ValueError("evaluation ledger configuration drift")
        os.chmod(path, 0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.config.ledger_path, timeout=1.0)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def start(self, metadata: dict[str, Any], reserve_tokens: int) -> int:
        if type(reserve_tokens) is not int or reserve_tokens < 0:
            raise ValueError("reserve_tokens must be a nonnegative integer")
        rejection_message = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            calls, tokens = db.execute("SELECT COUNT(*), COALESCE(SUM(charge_tokens),0) FROM requests").fetchone()
            now = time.time()
            reason = None
            if self.config.deadline_epoch is not None and now >= self.config.deadline_epoch:
                reason, rejection_message = "deadline", "evaluation deadline exceeded before admission"
            elif self.config.max_model_calls is not None and calls >= self.config.max_model_calls:
                reason, rejection_message = "request_budget", "evaluation request budget exhausted"
            elif self.config.max_total_tokens is not None and tokens + reserve_tokens > self.config.max_total_tokens:
                reason, rejection_message = "token_budget", "evaluation token admission budget exhausted"
            if reason is not None:
                # A production reservation can precede this stricter HTTP
                # admission. Keep that rejection observable without inventing
                # a physical request, token charge, usage receipt, or call ID.
                row = {
                    key: _safe_id(metadata.get(key)) for key in (
                        "actorId", "subagentId", "role", "agentType", "model",
                        "parentToolOperationId", "routeSha256",
                    )
                }
                row.update({
                    "event": "admission_rejected", "reason": reason,
                    "rejectedEpoch": now, "reservedTokens": reserve_tokens,
                    "currentRequests": calls, "currentChargedTokens": tokens,
                    "maxModelCalls": self.config.max_model_calls,
                    "maxTotalTokens": self.config.max_total_tokens,
                    "deadlineEpoch": self.config.deadline_epoch,
                    "estimatedInputTokens": _token(metadata.get("estimatedInputTokens")),
                    "maxOutputTokens": _token(metadata.get("maxOutputTokens")),
                    "pid": os.getpid(), "thread": threading.current_thread().name,
                })
                db.execute("INSERT INTO admission_rejections(data) VALUES (?)", (json.dumps(row),))
            else:
                row = {
                    **metadata, "event": "started", "usage": _usage(None),
                    "providerUsage": None, "reservedTokens": reserve_tokens,
                    "startedEpoch": now, "pid": os.getpid(),
                    "thread": threading.current_thread().name,
                    "providerRequestId": None, "providerResponseId": None,
                    "firstByteSeconds": None, "ttftSeconds": None,
                    "firstContentSeconds": None, "lastEventSeconds": None,
                    "lastByteSeconds": None, "streamDone": False,
                }
                cursor = db.execute(
                    "INSERT INTO requests(status,charge_tokens,data) VALUES ('started',?,?)",
                    (reserve_tokens, json.dumps(row, ensure_ascii=False)),
                )
                call = int(cursor.lastrowid)
        # Raising inside the transaction would roll back the rejection itself.
        if rejection_message is not None:
            raise EvaluationBudgetExceeded(rejection_message)
        return call

    def update(self, call: int, fields: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data, charge_tokens FROM requests WHERE call=?", (call,)).fetchone()
            if row is None:
                raise ValueError("unknown evaluation request")
            data, charge = json.loads(row[0]), row[1]
            data.update(fields)
            usage = data.get("usage", {})
            # Incomplete and killed requests retain conservative reservation.
            if usage.get("source") == "provider":
                charge = usage["input_tokens"] + usage["output_tokens"]
            db.execute(
                "UPDATE requests SET status=?, charge_tokens=?, data=? WHERE call=?",
                (data["event"], charge, json.dumps(data, ensure_ascii=False), call),
            )

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT call,data,charge_tokens FROM requests ORDER BY call").fetchall()
            rejections = db.execute("SELECT rejection,data FROM admission_rejections ORDER BY rejection").fetchall()
        records = [{"call": call, **json.loads(data), "chargedTokens": charge} for call, data, charge in rows]
        rejected = [{"rejection": identity, **json.loads(data)} for identity, data in rejections]
        known = [r for r in records if r["usage"]["source"] == "provider"]
        cache_known = [r for r in known if r["usage"]["cache_read_tokens"] is not None]
        return {
            "requests": records, "started": len(records),
            "admissionRejections": rejected, "admissionRejectedCount": len(rejected),
            "admissionRejectedByReason": {
                reason: sum(r["reason"] == reason for r in rejected)
                for reason in ("deadline", "request_budget", "token_budget")
            },
            "completed": sum(r["event"] == "completed" for r in records),
            "failed": sum(r["event"] == "failed" for r in records),
            "inFlight": sum(r["event"] == "started" for r in records),
            "providerUsageCalls": len(known), "unknownUsageCalls": len(records) - len(known),
            "usageComplete": bool(records) and len(known) == len(records),
            "inputTokensKnown": sum(r["usage"]["input_tokens"] for r in known),
            "outputTokensKnown": sum(r["usage"]["output_tokens"] for r in known),
            "cacheReadTokensKnown": sum(r["usage"]["cache_read_tokens"] for r in cache_known),
            "cacheUsageComplete": bool(records) and len(cache_known) == len(records),
            "chargedTokens": sum(r["chargedTokens"] for r in records),
            "tokenBudgetSemantics": "estimated input plus maximum output reserved at admission; unknown usage retains reservation; actual usage may exceed estimate",
        }

    def export_jsonl(self, path: str | Path) -> None:
        """Export old-harness-compatible start/terminal pairs without recounting.

        The SQLite database, including in-flight usage receipts, is authoritative;
        this is a derived convenience export, safe even after a worker was killed.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for row in self.snapshot()["requests"]:
                start = {key: row.get(key) for key in (
                    "call", "model", "pid", "thread", "role", "actorId", "subagentId", "startedEpoch",
                )}
                stream.write(json.dumps({**start, "event": "started"}, ensure_ascii=False) + "\n")
                if row["event"] != "started":
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


_CURRENT_CALL: ContextVar[dict | None] = ContextVar("evaluation_transport_call", default=None)
_CURRENT_ACTOR: ContextVar[dict | None] = ContextVar("evaluation_transport_actor", default=None)
_ACTIVE_TRANSPORT: EvaluationTransport | None = None


@contextmanager
def evaluation_context(*, subagent_id: str | None = None, role: str | None = None, **metadata):
    """Optional explicit attribution binding for evaluator-owned worker seams."""
    actor = {**(_CURRENT_ACTOR.get() or {}), **metadata}
    if subagent_id is not None:
        actor.update({"subagentId": subagent_id, "actorId": subagent_id})
    if role is not None:
        actor["role"] = role
    token = _CURRENT_ACTOR.set(actor)
    try:
        yield
    finally:
        _CURRENT_ACTOR.reset(token)


class EvaluationTransport:
    """Process-local installation; subprocesses explicitly bootstrap this too."""

    def __init__(self, config: TransportConfig):
        self.config = config
        self.ledger = SharedLedger(config)
        self.deadline = (time.monotonic() + max(0, config.deadline_epoch - time.time())
                         if config.deadline_epoch is not None else None)
        self._patches: list[tuple[object, str, object]] = []

    def _patch(self, owner, name, value):
        self._patches.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self):
        global _ACTIVE_TRANSPORT
        if _ACTIVE_TRANSPORT is not None:
            raise RuntimeError("evaluation transport already installed")
        from minicode import openai_adapter as adapter
        from minicode import subagent_model_routing, subagent_supervisor
        from minicode.tools import task

        self._original_open = adapter._open_verified_url_controlled
        self._original_consume = adapter._consume_response_lines
        original_next = adapter.OpenAIModelAdapter.next
        signature = inspect.signature(original_next)

        @wraps(original_next)
        def measured(model, *args, **kwargs):
            bound = signature.bind(model, *args, **kwargs)
            bound.apply_defaults()
            if model.runtime.get("model") != self.config.expected_model or route_fingerprint(model.runtime) != self.config.expected_route_sha256:
                raise RuntimeError("evaluation model route drift")
            started = time.monotonic()
            deadlines = ([] if self.config.request_timeout_seconds is None
                         else [started + self.config.request_timeout_seconds])
            for candidate in (self.deadline, bound.arguments.get("deadline_monotonic")):
                if candidate is not None:
                    deadlines.append(candidate)
            deadline = min(deadlines) if deadlines else None
            from minicode.model_call_control import checkpoint_model_call
            checkpoint_model_call(deadline_monotonic=deadline, cancellation_token=bound.arguments.get("cancellation_token"))
            bound.arguments["deadline_monotonic"] = deadline
            bound.arguments["max_retries_override"] = 0
            bound.arguments["max_output_tokens_override"] = min(
                bound.arguments.get("max_output_tokens_override") or self.config.max_output_tokens,
                self.config.max_output_tokens,
            )
            bound.arguments["on_stream_chunk"] = bound.arguments.get("on_stream_chunk") or (lambda _text: None)
            # Production's streaming store cost path silently estimates absent
            # usage. The experiment ledger is the only monetary authority.
            bound.arguments["store"] = None
            actor = _CURRENT_ACTOR.get() or getattr(model, "_evaluation_actor", None) or {
                "agentType": "general" if self.config.worker_id else "main",
                "role": "general" if self.config.worker_id else "main",
                "actorId": self.config.worker_id or "main",
                "subagentId": self.config.worker_id,
            }
            state = {"started": started, "deadline": deadline, "call": None,
                     "usage": _usage(None), "actor": actor, "phase": "prepare"}
            token = _CURRENT_CALL.set(state)
            try:
                step = original_next(*bound.args, **bound.kwargs)
                from minicode.types import ModelUsage
                step.usage = ModelUsage(**state["usage"])
                if state["call"] is None:
                    raise EvaluationStreamError("adapter did not enter evaluation transport")
                self.ledger.update(state["call"], {
                    "event": "completed", "seconds": time.monotonic() - started,
                    "usage": state["usage"], "response": step.content,
                    "reasoning": step.reasoningContent,
                    "toolCalls": [{"id": c["id"], "name": c["toolName"], "arguments": c["input"]} for c in step.calls],
                    "stopReason": getattr(step.diagnostics, "stopReason", None),
                })
                return step
            except BaseException as error:
                if state["call"] is not None:
                    self.ledger.update(state["call"], {
                        "event": "failed", "seconds": time.monotonic() - started,
                        "errorClass": type(error).__name__, "usage": state["usage"],
                        "errorPhase": state["phase"],
                    })
                raise
            finally:
                _CURRENT_CALL.reset(token)

        original_factory = subagent_model_routing.create_subagent_model_adapter

        @wraps(original_factory)
        def factory(runtime, agent_type, *, tools):
            model = original_factory(runtime, agent_type, tools=tools)
            model._evaluation_actor = _CURRENT_ACTOR.get() or {
                "agentType": agent_type, "role": agent_type,
                "actorId": self.config.worker_id or f"eval_{uuid.uuid4().hex}",
                "subagentId": self.config.worker_id,
            }
            return model

        def with_actor(original):
            @wraps(original)
            def run(input_data, context):
                reserved_id = input_data.get("_subagent_id")
                logical_role = getattr(context, "_logical_agent_role", None)
                actor = {
                    "agentType": input_data.get("agent_type"),
                    "role": str(getattr(logical_role, "value", logical_role)
                                or input_data.get("agent_type")),
                    "actorId": reserved_id or f"eval_{uuid.uuid4().hex}",
                    "subagentId": reserved_id,
                }
                token = _CURRENT_ACTOR.set(actor)
                try:
                    return original(input_data, context)
                finally:
                    _CURRENT_ACTOR.reset(token)
            return run

        original_new_id = task.new_subagent_id

        def new_id():
            identity = original_new_id()
            actor = _CURRENT_ACTOR.get()
            if actor is not None:
                actor.update({"subagentId": identity, "actorId": identity})
            return identity

        config_path = Path(self.config.ledger_path).with_suffix(".transport.json")
        # The shared worker config deliberately contains no runtime credentials.
        if not config_path.exists():
            descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(asdict(self.config), stream)
                stream.flush()
                os.fsync(stream.fileno())
        original_spawn = subagent_supervisor.run_subagent_process

        @wraps(original_spawn)
        def spawn(request, **kwargs):
            if kwargs.get("command") is not None:
                raise RuntimeError("custom worker command bypasses evaluation bootstrap")
            kwargs["command"] = [sys.executable, str(Path(__file__).resolve()), "--worker-bootstrap", str(config_path)]
            return original_spawn(request, **kwargs)

        self._patch(adapter.OpenAIModelAdapter, "next", measured)
        self._patch(adapter, "_open_verified_url_controlled", self._open)
        self._patch(adapter, "_consume_response_lines", self._consume)
        self._patch(subagent_model_routing, "create_subagent_model_adapter", factory)
        self._patch(task, "_run", with_actor(task._run))
        self._patch(task, "_SYNC_RUN", with_actor(task._SYNC_RUN))
        self._patch(task, "new_subagent_id", new_id)
        self._patch(subagent_supervisor, "run_subagent_process", spawn)
        self._patch(task, "run_subagent_process", spawn)
        _ACTIVE_TRANSPORT = self
        return self

    def __exit__(self, *_exc):
        global _ACTIVE_TRANSPORT
        for owner, name, original in reversed(self._patches):
            setattr(owner, name, original)
        self._patches.clear()
        _ACTIVE_TRANSPORT = None

    def _open(self, request, *, timeout, cancellation_token, deadline_monotonic):
        from minicode import openai_adapter as adapter
        from minicode.context_manager import estimate_messages_tokens, estimate_tokens

        state = _CURRENT_CALL.get()
        if state is None:
            raise RuntimeError("unattributed evaluation request")
        if state["call"] is not None:
            raise RuntimeError("unmetered adapter retry denied")
        body = json.loads(request.data)
        if body.get("model") != self.config.expected_model or hashlib.sha256(request.full_url.encode()).hexdigest() != self.config.expected_route_sha256:
            raise RuntimeError("evaluation HTTP route drift")
        body["stream"] = True
        body["stream_options"] = {**body.get("stream_options", {}), "include_usage": True}
        request.data = json.dumps(body).encode()
        estimated = estimate_messages_tokens(body["messages"]) + estimate_tokens(json.dumps(body.get("tools", [])))
        metadata = {
            **state["actor"], "model": body["model"],
            "routeSha256": self.config.expected_route_sha256,
            "estimatedInputTokens": estimated, "maxOutputTokens": body["max_tokens"],
            # Persist the provider-visible projection, never tool schemas,
            # messages, or private runtime settings. This lets diagnosis
            # distinguish unavailable authority from a model's own claim.
            "toolNames": [name for item in body.get("tools", [])[:256]
                          if isinstance(item, dict)
                          and isinstance(item.get("function"), dict)
                          and (name := _safe_id(item["function"].get("name")))],
            "reasoningEffort": (body.get("reasoning_effort")
                                if body.get("reasoning_effort") in {"low", "high", "max"} else None),
            "requestDeadlineEpoch": (None if state["deadline"] is None else
                                     time.time() + max(0, state["deadline"] - time.monotonic())),
        }
        from minicode.permission_approval import _CURRENT_TOOL
        binding = _CURRENT_TOOL.get()
        metadata["parentToolOperationId"] = getattr(binding, "operation_id", None)
        state["call"] = self.ledger.start(metadata, estimated + body["max_tokens"])
        state["phase"] = "headers"
        # urllib's timeout covers response headers as well as connection setup.
        # Keep the physical connect/TLS cap as host-only request metadata; the
        # open watchdog and connected socket use the existing idle allowance.
        request._minicode_connect_timeout_seconds = self.config.connect_timeout_seconds
        try:
            response = self._original_open(
                request, timeout=(self.config.idle_timeout_seconds if state["deadline"] is None
                                  else min(self.config.idle_timeout_seconds,
                                           max(0.001, state["deadline"] - time.monotonic()))),
                cancellation_token=cancellation_token, deadline_monotonic=deadline_monotonic,
            )
        except urllib.error.HTTPError as error:
            self.ledger.update(state["call"], {"httpStatus": error.code, "providerRequestId": self._request_id(error)})
            adapter._schedule_async_response_close(error)
            raise EvaluationStreamError(f"provider HTTP status {error.code}") from None
        self.ledger.update(state["call"], {
            "headersSeconds": time.monotonic() - state["started"],
            "providerRequestId": self._request_id(response),
            "httpStatus": getattr(response, "status", getattr(response, "code", None)),
        })
        status = getattr(response, "status", getattr(response, "code", 200))
        if isinstance(status, int) and status >= 400:
            adapter._schedule_async_response_close(response)
            raise EvaluationStreamError(f"provider HTTP status {status}")
        return response

    @staticmethod
    def _request_id(response):
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        return next((safe for key in ("x-request-id", "request-id", "x-trace-id") if (safe := _safe_id(headers.get(key)))), None)

    def _consume(self, response, *, maximum_bytes, read_timeout_seconds, line_consumer, cancellation_token, deadline_monotonic):
        """Reset idle time per received byte; never extend total/caller deadline."""
        from minicode import openai_adapter as adapter
        from minicode.model_call_control import checkpoint_model_call

        state = _CURRENT_CALL.get()
        if state is None:
            raise RuntimeError("unattributed evaluation stream")
        state["phase"] = "stream"
        del read_timeout_seconds  # Production's fixed body timer is replaced.
        received: queue.Queue = queue.Queue(maxsize=8)
        stop = threading.Event()
        reader, sized = adapter._response_reader(response)

        def publish(item):
            while not stop.is_set():
                try:
                    received.put(item, timeout=0.02)
                    return
                except queue.Full:
                    pass

        def read():
            try:
                while not stop.is_set():
                    timeout = self.config.idle_timeout_seconds
                    if deadline_monotonic is not None:
                        timeout = min(timeout, max(0.001, deadline_monotonic - time.monotonic()))
                    adapter._set_response_socket_timeout(response, timeout)
                    chunk = reader(64 * 1024) if sized else reader()
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    publish((chunk, time.monotonic(), None))
                    if not chunk:
                        return
            except BaseException as error:
                publish((None, time.monotonic(), error))

        worker = threading.Thread(target=read, daemon=True, name="evaluation-stream-reader")
        worker.start()
        last_byte = time.monotonic()
        buffer = b""
        data_lines: list[bytes] = []
        total = 0
        done = False
        telemetry: dict[str, Any] = {}

        def dispatch():
            nonlocal done
            if not data_lines:
                return
            payload = b"\n".join(data_lines)
            data_lines.clear()
            now = time.monotonic() - state["started"]
            telemetry["lastEventSeconds"] = now
            if payload.strip() == b"[DONE]":
                done = True
                telemetry["streamDone"] = True
                self.ledger.update(state["call"], telemetry)
                line_consumer(b"data: [DONE]")
                return
            try:
                event = json.loads(payload)
            except (ValueError, UnicodeError):
                raise EvaluationStreamError("invalid provider SSE JSON") from None
            if not isinstance(event, dict) or event.get("error"):
                raise EvaluationStreamError("provider SSE error")
            if response_id := _safe_id(event.get("id")):
                telemetry["providerResponseId"] = response_id
            for choice in event.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                    telemetry.setdefault("ttftSeconds", now)
                if delta.get("content"):
                    telemetry.setdefault("firstContentSeconds", now)
                if choice.get("finish_reason"):
                    telemetry["streamFinishReason"] = choice["finish_reason"]
            if isinstance(event.get("usage"), dict):
                provider_usage = {
                    **(telemetry.get("providerUsage") or {}),
                    **(_safe_provider_usage(event["usage"]) or {}),
                }
                state["usage"] = _usage(provider_usage)
                telemetry.update({"providerUsage": provider_usage, "usage": state["usage"]})
            # Each parsed event is durable before its contents reach the agent.
            self.ledger.update(state["call"], telemetry)
            line_consumer(b"data: " + json.dumps(event).encode())

        try:
            while not done:
                checkpoint_model_call(cancellation_token=cancellation_token, deadline_monotonic=deadline_monotonic)
                remaining = self.config.idle_timeout_seconds - (time.monotonic() - last_byte)
                if deadline_monotonic is not None:
                    remaining = min(remaining, deadline_monotonic - time.monotonic())
                if remaining <= 0:
                    checkpoint_model_call(deadline_monotonic=deadline_monotonic)
                    raise EvaluationIdleTimeout("evaluation stream idle timeout")
                try:
                    chunk, observed_at, error = received.get(timeout=min(0.02, remaining))
                except queue.Empty:
                    continue
                if error:
                    raise error
                if not chunk:
                    if buffer:
                        if buffer.startswith(b"data:"):
                            data_lines.append(buffer[5:].lstrip(b" "))
                    dispatch()
                    if not done:
                        raise EvaluationStreamError("provider stream ended without DONE")
                    break
                last_byte = observed_at
                telemetry.setdefault("firstByteSeconds", observed_at - state["started"])
                telemetry["lastByteSeconds"] = observed_at - state["started"]
                total += len(chunk)
                if total > maximum_bytes:
                    raise EvaluationStreamError("provider response byte limit exceeded")
                buffer += chunk
                while b"\n" in buffer and not done:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if not line:
                        dispatch()
                    elif line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip(b" "))
                # Heartbeats reset idle timeout and persist last activity too.
                self.ledger.update(state["call"], telemetry)
            checkpoint_model_call(cancellation_token=cancellation_token, deadline_monotonic=deadline_monotonic)
            state["phase"] = "parse"
        finally:
            stop.set()
            adapter._interrupt_response_read(response)


def _worker_bootstrap(argv: list[str]) -> int:
    """Original production worker protocol, with evaluation hooks installed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    config_path, *worker_args = argv
    raw = json.loads(Path(config_path).read_text())
    request_path = worker_args[worker_args.index("--request") + 1]
    request = json.loads(Path(request_path).read_text())
    raw["worker_id"] = request.get("subagent_id")
    from minicode.subagent_worker import main

    with EvaluationTransport(TransportConfig(**raw)):
        return main(worker_args)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker-bootstrap":
        raise SystemExit(_worker_bootstrap(sys.argv[2:]))
    raise SystemExit("evaluation_transport is an importable experiment adapter")
