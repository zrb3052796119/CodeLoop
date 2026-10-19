"""OpenAI-compatible API adapter for MiniCode.

Supports GPT-4o, GPT-4-turbo, GPT-4o-mini and any OpenAI-compatible endpoint
(e.g., Azure OpenAI, local LLMs with OpenAI-compatible API).
"""

from __future__ import annotations

import http.client
import inspect
import json
import math
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from minicode.api_retry import RETRYABLE_STATUS, calculate_backoff
from minicode.cost_tracker import calculate_cost
from minicode.model_call_control import (
    bounded_request_timeout,
    checkpoint_model_call,
    controlled_retry_sleep,
)
from minicode.model_registry import _is_official_glm53_flash_route
from minicode.state import (
    AppState,
    Store,
    add_cost,
    record_api_error,
    update_context_usage,
)
from minicode.tls import open_verified_url
from minicode.types import AgentStep, ModelUsage, StepDiagnostics

DEFAULT_MAX_RETRIES = 4
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_RESPONSE_READ_CHUNK_BYTES = 64 * 1024
_RESPONSE_CONTROL_POLL_SECONDS = 0.01
_MAX_BACKGROUND_IO_WORKERS = 4
_BACKGROUND_IO_WORKER_SLOTS = threading.BoundedSemaphore(_MAX_BACKGROUND_IO_WORKERS)
_MAX_ASYNC_RESPONSE_CLEANUPS = 4
_ASYNC_RESPONSE_CLEANUP_SLOTS = threading.BoundedSemaphore(
    _MAX_ASYNC_RESPONSE_CLEANUPS
)
_RESPONSE_SOCKET_PATHS = (
    ("fp", "raw", "_sock"),
    ("fp", "fp", "raw", "_sock"),
    ("file", "fp", "raw", "_sock"),
    ("raw", "_sock"),
    ("_sock",),
)


def _close_response_safely(response: object) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the real failure
            return


def _reader_accepts_amount(reader: object) -> bool:
    try:
        parameters = inspect.signature(reader).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }
        for parameter in parameters
    )


def _response_reader(response: object) -> tuple[Callable[..., object], bool]:
    read_one = getattr(response, "read1", None)
    if callable(read_one):
        return read_one, True
    read = getattr(response, "read", None)
    if not callable(read):
        try:
            iterator = iter(response)
        except TypeError as error:
            raise TypeError("model response does not provide a readable body") from error

        # Compatibility for lightweight streaming test shims. Production
        # urllib responses take the read1/read(amount) branches above.
        def read_from_iterator(_amount: int) -> object:
            return next(iterator, b"")

        return read_from_iterator, True
    accepts_amount = _reader_accepts_amount(read)
    if not accepts_amount and isinstance(
        response, (http.client.HTTPResponse, urllib.error.HTTPError)
    ):
        raise TypeError("model response does not provide a bounded reader")
    # Non-sized reads exist only on lightweight compatibility/test shims.
    # urllib production responses must take the bounded branches above.
    return read, accepts_amount


def _response_socket(response: object) -> object | None:
    for path in _RESPONSE_SOCKET_PATHS:
        candidate = response
        try:
            for attribute in path:
                candidate = getattr(candidate, attribute)
        except Exception:  # noqa: BLE001, S112 - optional wrapper path
            continue
        settimeout = getattr(candidate, "settimeout", None)
        if callable(settimeout):
            return candidate
    return None


def _set_response_socket_timeout(response: object, timeout: float) -> None:
    """Best-effort reset for the known urllib/http.client socket layouts."""

    candidate = _response_socket(response)
    settimeout = getattr(candidate, "settimeout", None)
    if not callable(settimeout):
        return
    try:
        settimeout(timeout)
    except (OSError, TypeError, ValueError):
        return


def _schedule_async_response_close(response: object) -> None:
    if not _ASYNC_RESPONSE_CLEANUP_SLOTS.acquire(blocking=False):
        return

    def cleanup() -> None:
        try:
            _close_response_safely(response)
        finally:
            _ASYNC_RESPONSE_CLEANUP_SLOTS.release()

    try:
        threading.Thread(
            target=cleanup,
            name="minicode-model-response-cleanup",
            daemon=True,
        ).start()
    except BaseException:  # noqa: BLE001 - best-effort cleanup only
        _ASYNC_RESPONSE_CLEANUP_SLOTS.release()
        return


def _interrupt_response_read(response: object) -> None:
    """Interrupt a reader without synchronously taking HTTPResponse's lock."""

    candidate = _response_socket(response)
    if candidate is None:
        _schedule_async_response_close(response)
        return
    shutdown = getattr(candidate, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown(socket.SHUT_RDWR)
        except (OSError, TypeError, ValueError):
            pass
    close = getattr(candidate, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def _check_io_controls(
    *,
    cancellation_token: Any,
    deadline_monotonic: float | None,
    local_deadline_monotonic: float,
    timeout_message: str,
) -> None:
    checkpoint_model_call(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    )
    if time.monotonic() >= local_deadline_monotonic:
        checkpoint_model_call(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        raise TimeoutError(timeout_message)


def _join_io_worker_controlled(
    worker: threading.Thread,
    *,
    cancellation_token: Any,
    deadline_monotonic: float | None,
    local_deadline_monotonic: float,
) -> None:
    """Wait for reader cleanup without weakening the response deadline."""

    while worker.is_alive():
        _check_io_controls(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            local_deadline_monotonic=local_deadline_monotonic,
            timeout_message="model response read timed out",
        )
        remaining = max(0.0, local_deadline_monotonic - time.monotonic())
        worker.join(timeout=min(_RESPONSE_CONTROL_POLL_SECONDS, remaining))


def _acquire_io_worker_slot(
    *,
    cancellation_token: Any,
    deadline_monotonic: float | None,
    local_deadline_monotonic: float,
    timeout_message: str,
) -> None:
    while True:
        _check_io_controls(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            local_deadline_monotonic=local_deadline_monotonic,
            timeout_message=timeout_message,
        )
        remaining = max(0.0, local_deadline_monotonic - time.monotonic())
        if _BACKGROUND_IO_WORKER_SLOTS.acquire(
            timeout=min(_RESPONSE_CONTROL_POLL_SECONDS, remaining)
        ):
            return


def _open_verified_url_controlled(
    request: urllib.request.Request,
    *,
    timeout: float,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> object:
    """Open transport behind a bounded worker so wall-clock controls stay hard."""

    # ``timeout`` was already bounded against the caller's absolute deadline
    # by ``bounded_request_timeout``. Deriving the local watchdog from that
    # duration also keeps this transport clock independent from callers that
    # provide a test/facade monotonic clock to model-call control.
    local_deadline = time.monotonic() + timeout
    _acquire_io_worker_slot(
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
        local_deadline_monotonic=local_deadline,
        timeout_message="model transport open timed out",
    )
    outcomes: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
    state_lock = threading.Lock()
    state = {"abandoned": False}

    def open_worker() -> None:
        try:
            try:
                outcome: object = open_verified_url(request, timeout=timeout)
                succeeded = True
            except BaseException as error:  # noqa: BLE001 - propagate across worker
                outcome = error
                succeeded = False
            with state_lock:
                if state["abandoned"]:
                    if succeeded or isinstance(outcome, urllib.error.HTTPError):
                        _close_response_safely(outcome)
                    return
                outcomes.put_nowait((succeeded, outcome))
        finally:
            _BACKGROUND_IO_WORKER_SLOTS.release()

    try:
        worker = threading.Thread(
            target=open_worker,
            name="minicode-model-transport-open",
            daemon=True,
        )
        worker.start()
    except BaseException:
        _BACKGROUND_IO_WORKER_SLOTS.release()
        raise

    received_outcome: tuple[bool, object] | None = None
    try:
        while True:
            _check_io_controls(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
                local_deadline_monotonic=local_deadline,
                timeout_message="model transport open timed out",
            )
            remaining = max(0.0, local_deadline - time.monotonic())
            try:
                received_outcome = outcomes.get(
                    timeout=min(_RESPONSE_CONTROL_POLL_SECONDS, remaining)
                )
            except queue.Empty:
                continue
            _check_io_controls(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
                local_deadline_monotonic=local_deadline,
                timeout_message="model transport open timed out",
            )
            break
    except BaseException:
        late_outcome = received_outcome
        with state_lock:
            state["abandoned"] = True
            if late_outcome is None:
                try:
                    late_outcome = outcomes.get_nowait()
                except queue.Empty:
                    pass
        if late_outcome is not None and (
            late_outcome[0]
            or isinstance(late_outcome[1], urllib.error.HTTPError)
        ):
            _close_response_safely(late_outcome[1])
        raise
    worker.join()
    if received_outcome is None:
        raise RuntimeError("model transport open completed without an outcome")
    succeeded, outcome = received_outcome
    if succeeded:
        return outcome
    if isinstance(outcome, BaseException):
        raise outcome.with_traceback(None)
    raise RuntimeError("model transport open failed")


def _read_response_bytes(
    response: object,
    *,
    maximum_bytes: int,
    read_timeout_seconds: float,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
    chunk_consumer: Callable[[bytes], bool | None] | None = None,
    retain_body: bool = True,
) -> bytes:
    """Read a bounded body while keeping cancellation/deadlines interruptible."""

    chunks: list[bytes] = []
    chunk = b""
    total = 0
    stop_reader = threading.Event()
    read_requests: queue.Queue[int | None] = queue.Queue(maxsize=1)
    read_results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
    reader_started = False
    try:
        checkpoint_model_call(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        read_deadline = time.monotonic() + read_timeout_seconds
        reader, accepts_amount = _response_reader(response)
        _acquire_io_worker_slot(
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            local_deadline_monotonic=read_deadline,
            timeout_message="model response read timed out",
        )

        def read_worker() -> None:
            try:
                while not stop_reader.is_set():
                    requested = read_requests.get()
                    if requested is None or stop_reader.is_set():
                        return
                    remaining = read_deadline - time.monotonic()
                    if remaining <= 0:
                        read_results.put((False, TimeoutError("response read timeout")))
                        return
                    _set_response_socket_timeout(response, remaining)
                    try:
                        value = reader(requested) if accepts_amount else reader()
                    except BaseException as error:  # noqa: BLE001 - worker boundary
                        read_results.put((False, error))
                        return
                    read_results.put((True, value))
                    if not value or not accepts_amount:
                        return
            finally:
                try:
                    _close_response_safely(response)
                finally:
                    _BACKGROUND_IO_WORKER_SLOTS.release()

        worker: threading.Thread | None = None
        try:
            worker = threading.Thread(
                target=read_worker,
                name="minicode-model-response-reader",
                daemon=True,
            )
            reader_started = True
            worker.start()
        except BaseException:
            if worker is None or worker.ident is None:
                reader_started = False
                _BACKGROUND_IO_WORKER_SLOTS.release()
            raise
        assert worker is not None

        while True:
            _check_io_controls(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
                local_deadline_monotonic=read_deadline,
                timeout_message="model response read timed out",
            )
            requested = min(_RESPONSE_READ_CHUNK_BYTES, maximum_bytes - total + 1)
            read_requests.put(requested)
            while True:
                _check_io_controls(
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    local_deadline_monotonic=read_deadline,
                    timeout_message="model response read timed out",
                )
                remaining = max(0.0, read_deadline - time.monotonic())
                try:
                    succeeded, outcome = read_results.get(
                        timeout=min(_RESPONSE_CONTROL_POLL_SECONDS, remaining)
                    )
                except queue.Empty:
                    continue
                break
            _check_io_controls(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
                local_deadline_monotonic=read_deadline,
                timeout_message="model response read timed out",
            )
            if not succeeded:
                if isinstance(outcome, BaseException):
                    raise outcome.with_traceback(None)
                raise RuntimeError("model response read failed")
            chunk = outcome
            if not isinstance(chunk, bytes):
                raise TypeError("model response body must be bytes")
            if not chunk:
                stop_reader.set()
                _join_io_worker_controlled(
                    worker,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    local_deadline_monotonic=read_deadline,
                )
                return b"".join(chunks)
            total += len(chunk)
            if total > maximum_bytes:
                chunks.clear()
                chunk = b""
                raise RuntimeError("Model response exceeded the configured byte limit")
            keep_reading = chunk_consumer(chunk) if chunk_consumer is not None else None
            if retain_body:
                chunks.append(chunk)
            if keep_reading is False:
                stop_reader.set()
                try:
                    read_requests.put_nowait(None)
                except queue.Full:
                    pass
                _join_io_worker_controlled(
                    worker,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    local_deadline_monotonic=read_deadline,
                )
                return b"".join(chunks)
            if not accepts_amount:
                stop_reader.set()
                _join_io_worker_controlled(
                    worker,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    local_deadline_monotonic=read_deadline,
                )
                return b"".join(chunks)
    except BaseException:
        chunks.clear()
        chunk = b""
        stop_reader.set()
        try:
            read_requests.put_nowait(None)
        except queue.Full:
            pass
        if reader_started:
            _interrupt_response_read(response)
        else:
            _close_response_safely(response)
        raise


def _consume_response_lines(
    response: object,
    *,
    maximum_bytes: int,
    read_timeout_seconds: float,
    line_consumer: Callable[[bytes], bool | None],
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> None:
    pending = bytearray()

    stopped = False

    def consume_chunk(chunk: bytes) -> bool | None:
        nonlocal stopped
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                return
            line = bytes(pending[:newline]).rstrip(b"\r")
            del pending[: newline + 1]
            if line_consumer(line) is False:
                stopped = True
                return False

    _read_response_bytes(
        response,
        maximum_bytes=maximum_bytes,
        read_timeout_seconds=read_timeout_seconds,
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
        chunk_consumer=consume_chunk,
        retain_body=False,
    )
    if pending and not stopped:
        line_consumer(bytes(pending).rstrip(b"\r"))


def _get_openai_base_url(runtime: dict) -> str:
    """Get the base URL from the frozen provider runtime."""
    return str(runtime.get("openaiBaseUrl") or "https://api.openai.com").rstrip("/")


def _get_openai_api_key(runtime: dict) -> str:
    """Get the credential from the frozen provider runtime."""
    return str(runtime.get("openaiApiKey") or "")


def _chat_completions_url(base_url: str) -> str:
    """Join root or already-versioned OpenAI-compatible base URLs safely."""
    normalized = str(base_url).rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _to_openai_messages(
    messages: list[dict[str, Any]],
    *,
    require_tool_reasoning_content: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Convert internal message format to OpenAI Chat Completion format.
    
    Returns (system_message, chat_messages)
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    tool_call_groups: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        if message.get("role") != "assistant_tool_call":
            continue
        turn_id = message.get("assistantTurnId")
        if isinstance(turn_id, str) and turn_id:
            tool_call_groups.setdefault(turn_id, []).append(message)
    emitted_tool_call_turns: set[str] = set()
    
    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        
        if role == "system":
            system_parts.append(content)
            continue
        
        if role == "user":
            converted.append({"role": "user", "content": content})
            continue
        
        if role in ("assistant", "assistant_progress"):
            text = content
            if role == "assistant_progress":
                text = f"<progress>\n{content}\n</progress>"
            converted.append({"role": "assistant", "content": text})
            continue
        
        if role == "assistant_tool_call":
            turn_id = message.get("assistantTurnId")
            if isinstance(turn_id, str) and turn_id:
                if turn_id in emitted_tool_call_turns:
                    continue
                emitted_tool_call_turns.add(turn_id)
                grouped_messages = tool_call_groups[turn_id]
            else:
                # Preserve compatibility with histories written before turn
                # identities were introduced.
                grouped_messages = [message]

            # One model assistant turn can contain multiple tool calls.  The
            # agent loop stores results per call, so rebuild the original
            # assistant turn before sending it back to OpenAI-compatible APIs.
            grouped_content = next(
                (
                    grouped_message.get("content")
                    for grouped_message in grouped_messages
                    if isinstance(grouped_message.get("content"), str)
                    and grouped_message.get("content")
                ),
                None,
            )
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": (
                    grouped_content
                    if isinstance(grouped_content, str) and grouped_content
                    else None
                ),
                "tool_calls": [
                    {
                        "id": grouped_message["toolUseId"],
                        "type": "function",
                        "function": {
                            "name": grouped_message["toolName"],
                            "arguments": (
                                json.dumps(grouped_message["input"])
                                if isinstance(grouped_message["input"], dict)
                                else "{}"
                            ),
                        },
                    }
                    for grouped_message in grouped_messages
                ],
            }
            reasoning_values = [
                grouped_message.get("reasoningContent")
                for grouped_message in grouped_messages
                if isinstance(grouped_message.get("reasoningContent"), str)
            ]
            reasoning_content = next(
                (value for value in reasoning_values if value),
                reasoning_values[0] if reasoning_values else None,
            )
            if isinstance(reasoning_content, str):
                assistant_message["reasoning_content"] = reasoning_content
            elif require_tool_reasoning_content:
                # DeepSeek thinking-mode requests require the field on every
                # replayed assistant tool turn.  An empty value is the honest
                # representation when an older/history turn did not preserve
                # any reasoning tokens.
                assistant_message["reasoning_content"] = ""
            converted.append(assistant_message)
            continue
        
        if role == "tool_result":
            converted.append({
                "role": "tool",
                "tool_call_id": message["toolUseId"],
                "content": message.get("content", ""),
            })
            continue
    
    system_message = "\n\n".join(system_parts)
    return system_message, converted


def _parse_assistant_text(content: str) -> tuple[str, str | None]:
    """Parse progress/final markers from assistant text."""
    trimmed = content.strip()
    if not trimmed:
        return "", None
    markers = [
        ("<final>", "final", "</final>"),
        ("[FINAL]", "final", None),
        ("<progress>", "progress", "</progress>"),
        ("[PROGRESS]", "progress", None),
    ]
    for prefix, kind, closing_tag in markers:
        if trimmed.startswith(prefix):
            raw = trimmed[len(prefix):].strip()
            if closing_tag:
                raw = raw.replace(closing_tag, "").strip()
            return raw, kind
    return trimmed, None


def serialize_function_tools(tool_definitions) -> list[dict[str, Any]]:
    """Project a registry to the function schemas sent by this adapter."""
    return [{"type": "function", "function": {
        "name": tool.name, "description": tool.description, "parameters": tool.input_schema,
    }} for tool in tool_definitions]


class OpenAIModelAdapter:
    """Model adapter for OpenAI-compatible APIs."""
    
    def __init__(self, runtime: dict[str, Any], tools) -> None:
        self.runtime = runtime
        self.tools = tools
        # OpenAI-compatible syntax alone does not prove native strict-schema
        # support.  The provider factory writes this private capability from
        # the resolved provider route; direct/custom adapters default closed.
        self.supports_native_json_schema = (
            runtime.get("_supportsNativeJsonSchema") is True
        )
        supports_glm_controls = self._has_glm_control_route()
        self.supports_json_object = (
            supports_glm_controls and runtime.get("_supportsJsonObject") is True
        )
        # The provider factory owns this exact route capability. Direct
        # adapters and other providers remain closed unless explicitly routed.
        self.supported_reasoning_efforts: tuple[str, ...] = (
            ("low", "high", "max")
            if supports_glm_controls
            and runtime.get("_supportedReasoningEfforts") == ("low", "high", "max")
            else ()
        )
        self._cached_tools_json: list[dict[str, Any]] | None = None
        self._tools_cache_key: int = 0

    def _has_glm_control_route(self) -> bool:
        return (
            self.runtime.get("_resolvedOpenAIProvider") in {"openai", "custom"}
            and _is_official_glm53_flash_route(
                self.runtime.get("model"), _get_openai_base_url(self.runtime)
            )
        )
    
    def request_tool_schemas(self, *, tools_override=None, disable_tools=False,
                             json_object_override=False, response_schema_override=None):
        """Return the final tool payload shared by admission and provider calls."""
        if disable_tools or json_object_override or response_schema_override is not None:
            return []
        if tools_override is not None:
            return self._get_serialized_tools(tools_override)
        supplied = self.runtime.get("_openrouter_params", {}).get("tools")
        if supplied is not None:
            return supplied
        return self._get_serialized_tools()

    def _get_serialized_tools(self, tools_override=None) -> list[dict[str, Any]]:
        """Get serialized tool list in OpenAI function format with caching."""
        if tools_override is not None:
            return serialize_function_tools(tools_override.list())
        if self.tools is None:
            return []
        current_tools = self.tools.list()
        current_key = hash(tuple((t.name, t.description) for t in current_tools))
        if self._cached_tools_json is None or current_key != self._tools_cache_key:
            self._cached_tools_json = serialize_function_tools(current_tools)
            self._tools_cache_key = current_key
        return self._cached_tools_json
    
    def next(
        self,
        messages: list[dict[str, Any]],
        on_stream_chunk: Callable[[str], None] | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
        store: Store[AppState] | None = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        disable_tools: bool = False,
        tools_override=None,
        max_output_tokens_override: int | None = None,
        max_response_bytes_override: int | None = None,
        temperature_override: float | None = None,
        max_retries_override: int | None = None,
        isolated_reasoning_state: bool = False,
        response_schema_override: dict[str, Any] | None = None,
        json_object_override: bool = False,
        reasoning_effort_override: str | None = None,
    ) -> AgentStep:
        del isolated_reasoning_state  # This adapter has no mutable reasoning state.
        if max_output_tokens_override is not None and (
            isinstance(max_output_tokens_override, bool)
            or not isinstance(max_output_tokens_override, int)
            or max_output_tokens_override <= 0
        ):
            raise ValueError("max_output_tokens_override must be a positive integer")
        if max_response_bytes_override is not None and (
            isinstance(max_response_bytes_override, bool)
            or not isinstance(max_response_bytes_override, int)
            or max_response_bytes_override <= 0
            or max_response_bytes_override > DEFAULT_MAX_RESPONSE_BYTES
        ):
            raise ValueError(
                "max_response_bytes_override must be between 1 and the global limit"
            )
        if temperature_override is not None and (
            isinstance(temperature_override, bool)
            or not isinstance(temperature_override, (int, float))
            or not math.isfinite(float(temperature_override))
        ):
            raise ValueError("temperature_override must be finite")
        if max_retries_override is not None and (
            isinstance(max_retries_override, bool)
            or not isinstance(max_retries_override, int)
            or max_retries_override < 0
        ):
            raise ValueError("max_retries_override must be a non-negative integer")
        if not isinstance(json_object_override, bool):
            raise ValueError("json_object_override must be boolean")
        if json_object_override and response_schema_override is not None:
            raise ValueError("JSON object and JSON schema overrides are mutually exclusive")
        if json_object_override:
            if not self.supports_json_object or not self._has_glm_control_route():
                raise RuntimeError("json_object_unsupported")
            disable_tools = True
        if reasoning_effort_override is not None:
            if (
                not isinstance(reasoning_effort_override, str)
                or reasoning_effort_override not in {"low", "high", "max"}
            ):
                raise ValueError("reasoning_effort_override must be low, high, or max")
            if (
                reasoning_effort_override not in self.supported_reasoning_efforts
                or not self._has_glm_control_route()
            ):
                raise RuntimeError("reasoning_effort_unsupported")
        if response_schema_override is not None:
            if not self.supports_native_json_schema:
                raise RuntimeError("native_json_schema_unsupported")
            if (
                not isinstance(response_schema_override, dict)
                or set(response_schema_override) != {"name", "strict", "schema"}
                or not isinstance(response_schema_override.get("name"), str)
                or not response_schema_override["name"]
                or len(response_schema_override["name"]) > 64
                or response_schema_override.get("strict") is not True
                or not isinstance(response_schema_override.get("schema"), dict)
            ):
                raise ValueError("response_schema_override is invalid")
            try:
                encoded_schema = json.dumps(
                    response_schema_override,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError) as error:
                raise ValueError("response_schema_override is invalid") from error
            if len(encoded_schema) > 64 * 1024:
                raise ValueError("response_schema_override is too large")
            # Native structured output and Agent tool invocation are mutually
            # exclusive for this narrow call shape.
            disable_tools = True

        base_url = _get_openai_base_url(self.runtime)
        explicit_reasoning_replay = self.runtime.get(
            "requireReasoningContentReplay"
        )
        if isinstance(explicit_reasoning_replay, bool):
            require_tool_reasoning_content = explicit_reasoning_replay
        else:
            hostname = (
                urllib.parse.urlsplit(base_url).hostname or ""
            ).casefold()
            require_tool_reasoning_content = (
                hostname == "deepseek.com" or hostname.endswith(".deepseek.com")
            )
        system_message, converted_messages = _to_openai_messages(
            messages,
            require_tool_reasoning_content=require_tool_reasoning_content,
        )
        
        request_body: dict[str, Any] = {
            "model": self.runtime["model"],
            "messages": converted_messages,
        }
        if not disable_tools:
            serialized_tools = self.request_tool_schemas(tools_override=tools_override,
                disable_tools=disable_tools, json_object_override=json_object_override,
                response_schema_override=response_schema_override)
            if serialized_tools:
                request_body["tools"] = serialized_tools
        
        if system_message:
            request_body["messages"].insert(0, {"role": "system", "content": system_message})
        
        if self.runtime.get("maxOutputTokens") is not None:
            request_body["max_tokens"] = self.runtime["maxOutputTokens"]
        if self.runtime.get("temperature") is not None:
            request_body["temperature"] = self.runtime["temperature"]
        enable_thinking = self.runtime.get("enableThinking")
        if enable_thinking is not None:
            if not isinstance(enable_thinking, bool):
                raise ValueError("enableThinking must be boolean")
            request_body["enable_thinking"] = enable_thinking
        
        if on_stream_chunk:
            request_body["stream"] = True
        
        api_key = _get_openai_api_key(self.runtime)
        
        # Build headers — support OpenRouter and custom endpoints
        headers = {
            "content-type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # OpenRouter extra headers (HTTP-Referer, X-Title)
        openrouter_headers = self.runtime.get("_openrouter_headers", {})
        headers.update(openrouter_headers)
        # Custom endpoint extra headers
        custom_headers = self.runtime.get("_custom_headers", {})
        headers.update(custom_headers)

        # OpenRouter extra params (transforms, etc.)
        openrouter_params = self.runtime.get("_openrouter_params", {})
        for k, v in openrouter_params.items():
            if v is not None:
                request_body[k] = v

        # Per-call controls intentionally win over provider/runtime defaults.
        # Structured side calls can therefore remain tool-free and tightly
        # bounded while normal Agent calls retain their existing configuration.
        if tools_override is not None:
            effective_tools = self.request_tool_schemas(
                tools_override=tools_override, disable_tools=disable_tools,
                json_object_override=json_object_override,
                response_schema_override=response_schema_override,
            )
            if effective_tools:
                request_body["tools"] = effective_tools
                choice = request_body.get("tool_choice")
                if isinstance(choice, dict) and choice.get("type") == "function":
                    function = choice.get("function")
                    chosen = function.get("name") if isinstance(function, dict) else None
                    available = {tool["function"]["name"] for tool in effective_tools}
                    if chosen not in available:
                        request_body.pop("tool_choice", None)
            else:
                request_body.pop("tools", None)
                request_body.pop("tool_choice", None)
                request_body.pop("parallel_tool_calls", None)
        if disable_tools:
            request_body.pop("tools", None)
            request_body.pop("tool_choice", None)
            request_body.pop("parallel_tool_calls", None)
        if max_output_tokens_override is not None:
            request_body["max_tokens"] = max_output_tokens_override
        if temperature_override is not None:
            request_body["temperature"] = float(temperature_override)
        if response_schema_override is not None:
            request_body["response_format"] = {
                "type": "json_schema",
                "json_schema": response_schema_override,
            }
        elif json_object_override:
            request_body["response_format"] = {"type": "json_object"}
        if reasoning_effort_override is not None:
            request_body["reasoning_effort"] = reasoning_effort_override

        request = urllib.request.Request(
            url=_chat_completions_url(base_url),
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        
        # Retry logic
        max_retries = (
            max_retries_override
            if max_retries_override is not None
            else int(self.runtime.get("modelMaxRetries", 4))
        )
        response = None
        response_read_timeout = 0.0
        for attempt in range(max_retries + 1):
            try:
                timeout = bounded_request_timeout(
                    float(
                        self.runtime.get("modelTimeoutSeconds")
                        or os.environ.get("MINICODE_MODEL_TIMEOUT", "120")
                    ),
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                response_read_timeout = timeout
                response = _open_verified_url_controlled(
                    request,
                    timeout=timeout,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                try:
                    checkpoint_model_call(
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                except BaseException:
                    _close_response_safely(response)
                    response = None
                    raise
                break
            except urllib.error.HTTPError as error:
                response = error
                if error.code not in RETRYABLE_STATUS or attempt >= max_retries:
                    break
                from minicode.api_retry import classify_error
                _close_response_safely(error)
                response = None
                category = classify_error(error)
                wait = calculate_backoff(attempt, category=category)
                controlled_retry_sleep(
                    wait,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
            except urllib.error.URLError:
                if attempt >= max_retries:
                    raise
                wait = calculate_backoff(attempt)
                controlled_retry_sleep(
                    wait,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
        
        if response is None:
            raise RuntimeError("OpenAI request failed before receiving a response")
        
        if not on_stream_chunk:
            # Non-streaming response
            response_bytes = _read_response_bytes(
                response,
                maximum_bytes=(
                    max_response_bytes_override
                    if max_response_bytes_override is not None
                    else DEFAULT_MAX_RESPONSE_BYTES
                ),
                read_timeout_seconds=response_read_timeout,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            data = json.loads(response_bytes.decode("utf-8"))
            status = getattr(response, "status", getattr(response, "code", 200))
            
            if status >= 400:
                if store:
                    store.set_state(record_api_error())
                error_msg = data.get("error", {}).get("message", f"OpenAI API error: {status}")
                raise RuntimeError(error_msg)
            
            # Parse response
            choices = data.get("choices", [])
            provider_usage = data.get("usage")
            if isinstance(provider_usage, dict) and (
                "prompt_tokens" in provider_usage
                or "completion_tokens" in provider_usage
            ):
                details = provider_usage.get("prompt_tokens_details", {})
                model_usage = ModelUsage(
                    input_tokens=int(provider_usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(provider_usage.get("completion_tokens", 0) or 0),
                    cache_read_tokens=int(
                        details.get("cached_tokens", 0) or 0
                    ) if isinstance(details, dict) else 0,
                    source="provider",
                )
            else:
                from minicode.context_manager import estimate_messages_tokens

                estimated_text = "".join(
                    str(choice.get("message", {}).get("content", "") or "")
                    for choice in choices[:1]
                )
                model_usage = ModelUsage(
                    input_tokens=estimate_messages_tokens(messages),
                    output_tokens=max(0, len(estimated_text) // 4),
                    source="estimated",
                )

            if store:
                input_tokens = model_usage.input_tokens or 0
                output_tokens = model_usage.output_tokens or 0
                cost_usd = calculate_cost(
                    model=self.runtime["model"],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                if cost_usd > 0:
                    store.set_state(add_cost(cost_usd))
                store.set_state(update_context_usage(input_tokens + output_tokens))

            if not choices:
                return AgentStep(type="assistant", content="", usage=model_usage)
            
            choice = choices[0]
            message = choice.get("message", {})
            text_content = message.get("content", "") or ""
            reasoning_content_raw = message.get("reasoning_content")
            reasoning_content = (
                reasoning_content_raw
                if isinstance(reasoning_content_raw, str)
                else None
            )
            tool_calls_raw = message.get("tool_calls", [])
            
            stop_reason = choice.get("finish_reason")
            
            tool_calls = []
            if tool_calls_raw:
                for tc in tool_calls_raw:
                    func = tc.get("function", {})
                    try:
                        parsed_input = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        parsed_input = {}
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "toolName": func.get("name", ""),
                        "input": parsed_input,
                    })
            
            parsed_text, kind = _parse_assistant_text(text_content.strip())
            diagnostics = StepDiagnostics(
                stopReason=stop_reason,
                blockTypes=["tool_calls"] if tool_calls else (["text"] if text_content else []),
                ignoredBlockTypes=[],
            )
            
            if tool_calls:
                return AgentStep(
                    type="tool_calls",
                    calls=tool_calls,
                    content=parsed_text,
                    contentKind="progress" if kind == "progress" else None,
                    reasoningContent=reasoning_content,
                    diagnostics=diagnostics,
                    usage=model_usage,
                )
            return AgentStep(
                type="assistant",
                content=parsed_text,
                kind=kind,
                reasoningContent=reasoning_content,
                diagnostics=diagnostics,
                usage=model_usage,
            )
        
        # Streaming response
        tool_calls = []
        text_parts = []
        reasoning_parts = []
        reasoning_content_seen = False
        active_tool_calls: dict[int, dict] = {}
        stop_reason = None
        stream_input_tokens = 0
        stream_output_tokens = 0
        stream_usage_seen = False
        
        def consume_stream_line(line: bytes) -> bool | None:
            nonlocal reasoning_content_seen
            nonlocal stop_reason
            nonlocal stream_input_tokens
            nonlocal stream_output_tokens
            nonlocal stream_usage_seen

            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data: "):
                return
            data_str = line_str[6:]
            if data_str == "[DONE]":
                return False
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                return
            
            choices = event.get("choices", [])
            if not choices:
                # Maybe usage info
                usage = event.get("usage", {})
                if usage:
                    stream_usage_seen = True
                    stream_input_tokens = usage.get("prompt_tokens", 0)
                    stream_output_tokens = usage.get("completion_tokens", 0)
                return
            
            delta = choices[0].get("delta", {})
            finish_reason = choices[0].get("finish_reason")
            if finish_reason:
                stop_reason = finish_reason
            
            # Text content
            content = delta.get("content", "")
            if content:
                text_parts.append(content)
                on_stream_chunk(content)

            reasoning_content = delta.get("reasoning_content")
            if "reasoning_content" in delta and isinstance(reasoning_content, str):
                reasoning_content_seen = True
                reasoning_parts.append(reasoning_content)
                if reasoning_content and on_thinking_delta:
                    on_thinking_delta(reasoning_content)
            
            # Tool calls (incremental)
            tc_deltas = delta.get("tool_calls", [])
            for tc_delta in tc_deltas:
                idx = tc_delta.get("index", 0)
                if idx not in active_tool_calls:
                    active_tool_calls[idx] = {
                        "id": tc_delta.get("id", ""),
                        "name": "",
                        "arguments": "",
                    }
                func = tc_delta.get("function", {})
                if func.get("name"):
                    active_tool_calls[idx]["name"] = func["name"]
                if func.get("arguments"):
                    active_tool_calls[idx]["arguments"] += func["arguments"]
                if tc_delta.get("id"):
                    active_tool_calls[idx]["id"] = tc_delta["id"]

        _consume_response_lines(
            response,
            maximum_bytes=(
                max_response_bytes_override
                if max_response_bytes_override is not None
                else DEFAULT_MAX_RESPONSE_BYTES
            ),
            read_timeout_seconds=response_read_timeout,
            line_consumer=consume_stream_line,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        
        # Finalize tool calls
        for idx in sorted(active_tool_calls.keys()):
            tc = active_tool_calls[idx]
            try:
                parsed_input = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                parsed_input = {}
            tool_calls.append({
                "id": tc["id"],
                "toolName": tc["name"],
                "input": parsed_input,
            })
        
        if stream_usage_seen:
            model_usage = ModelUsage(
                input_tokens=int(stream_input_tokens or 0),
                output_tokens=int(stream_output_tokens or 0),
                source="provider",
            )
        else:
            from minicode.context_manager import estimate_messages_tokens

            stream_input_tokens = estimate_messages_tokens(messages)
            stream_output_tokens = max(0, len("".join(text_parts)) // 4)
            model_usage = ModelUsage(
                input_tokens=stream_input_tokens,
                output_tokens=stream_output_tokens,
                source="estimated",
            )

        # Streaming cost tracking
        if store:
            cost_usd = calculate_cost(
                model=self.runtime["model"],
                input_tokens=stream_input_tokens,
                output_tokens=stream_output_tokens,
            )
            if cost_usd > 0:
                store.set_state(add_cost(cost_usd))
            store.set_state(update_context_usage(stream_input_tokens + stream_output_tokens))
        
        parsed_text, kind = _parse_assistant_text("".join(text_parts).strip())
        reasoning_content = (
            "".join(reasoning_parts) if reasoning_content_seen else None
        )
        diagnostics = StepDiagnostics(
            stopReason=stop_reason,
            blockTypes=["tool_calls"] if tool_calls else (["text"] if text_parts else []),
            ignoredBlockTypes=[],
        )
        
        if tool_calls:
            return AgentStep(
                type="tool_calls",
                calls=tool_calls,
                content=parsed_text,
                contentKind="progress" if kind == "progress" else None,
                reasoningContent=reasoning_content,
                diagnostics=diagnostics,
                usage=model_usage,
            )
        return AgentStep(
            type="assistant",
            content=parsed_text,
            kind=kind,
            reasoningContent=reasoning_content,
            diagnostics=diagnostics,
            usage=model_usage,
        )
