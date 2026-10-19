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
import urllib.request
from collections.abc import Callable
from typing import Any

from minicode.api_retry import (
    RETRYABLE_STATUS,
    calculate_backoff,
)
from minicode.model_call_control import (
    bounded_request_timeout,
    checkpoint_model_call,
    controlled_retry_sleep,
)
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


def _get_retry_limit() -> int:
    try:
        value = int(float(os.environ.get("MINI_CODE_MAX_RETRIES", DEFAULT_MAX_RETRIES)))
    except ValueError:
        value = DEFAULT_MAX_RETRIES
    return max(0, value)


def _parse_retry_after_seconds(retry_after: str | None) -> float | None:
    """Parse Retry-After header into seconds."""
    if not retry_after:
        return None
    try:
        seconds = float(retry_after)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(retry_after)
        return max(0.0, target.timestamp() - time.time())
    except (ValueError, TypeError):
        pass
    return None


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


def _read_json_body(
    response: object,
    *,
    maximum_bytes: int,
    read_timeout_seconds: float,
    cancellation_token: Any = None,
    deadline_monotonic: float | None = None,
) -> Any:
    text = _read_response_bytes(
        response,
        maximum_bytes=maximum_bytes,
        read_timeout_seconds=read_timeout_seconds,
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
    ).decode("utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": {"message": text.strip()}}


def _extract_error_message(data: Any, status: int) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return f"Model request failed: {status}"


def _parse_assistant_text(content: str) -> tuple[str, str | None]:
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
            raw = trimmed[len(prefix) :].strip()
            if closing_tag:
                raw = raw.replace(closing_tag, "").strip()
            return raw, kind
    return trimmed, None


def _to_text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def _to_assistant_text(message: dict[str, Any]) -> str:
    if message["role"] == "assistant_progress":
        return f"<progress>\n{message['content']}\n</progress>"
    return message["content"]


def _push_anthropic_message(messages: list[dict[str, Any]], role: str, block: dict[str, Any]) -> None:
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].append(block)
    else:
        messages.append({"role": role, "content": [block]})


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
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
        if role == "system":
            continue
        if role == "user":
            _push_anthropic_message(converted, "user", _to_text_block(message["content"]))
            continue
        if role in {"assistant", "assistant_progress"}:
            _push_anthropic_message(converted, "assistant", _to_text_block(_to_assistant_text(message)))
            continue
        if role == "assistant_tool_call":
            turn_id = message.get("assistantTurnId")
            if isinstance(turn_id, str) and turn_id:
                if turn_id in emitted_tool_call_turns:
                    continue
                emitted_tool_call_turns.add(turn_id)
                grouped_messages = tool_call_groups[turn_id]
            else:
                grouped_messages = [message]
            tool_turn_text = next(
                (
                    grouped_message.get("content")
                    for grouped_message in grouped_messages
                    if isinstance(grouped_message.get("content"), str)
                    and grouped_message.get("content")
                ),
                None,
            )
            if isinstance(tool_turn_text, str) and tool_turn_text:
                _push_anthropic_message(
                    converted,
                    "assistant",
                    _to_text_block(tool_turn_text),
                )
            for grouped_message in grouped_messages:
                _push_anthropic_message(
                    converted,
                    "assistant",
                    {
                        "type": "tool_use",
                        "id": grouped_message["toolUseId"],
                        "name": grouped_message["toolName"],
                        "input": grouped_message["input"],
                    },
                )
            continue
        _push_anthropic_message(
            converted,
            "user",
            {
                "type": "tool_result",
                "tool_use_id": message["toolUseId"],
                "content": message["content"],
                "is_error": message["isError"],
            },
        )
    return system, converted


def _apply_prompt_cache_markers(body: dict[str, Any]) -> bool:
    """Mark the stable system block, the last tool, and the conversation
    tip as cache breakpoints.

    Returns whether anything was marked. The serialized-tool cache and the
    converted messages are never mutated: marked blocks are copies. The
    message marker rides the LAST content block of the LAST message, so
    each step of a growing conversation caches its full prefix for the
    next step to read at cache price.
    """
    marked = False
    system = body.get("system")
    if isinstance(system, str) and system:
        body["system"] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        marked = True
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        body["tools"] = [
            *tools[:-1],
            {**tools[-1], "cache_control": {"type": "ephemeral"}},
        ]
        marked = True
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        blocks = last.get("content")
        if isinstance(blocks, list) and blocks:
            messages[-1] = {
                **last,
                "content": [
                    *blocks[:-1],
                    {**blocks[-1], "cache_control": {"type": "ephemeral"}},
                ],
            }
            marked = True
    return marked


def _strip_prompt_cache_markers(body: dict[str, Any]) -> None:
    """Revert to the plain pre-marker body shape (fallback retry)."""
    system = body.get("system")
    if isinstance(system, list):
        body["system"] = "\n\n".join(
            block.get("text", "")
            for block in system
            if isinstance(block, dict)
        )
    tools = body.get("tools")
    if isinstance(tools, list):
        body["tools"] = [
            {key: value for key, value in tool.items() if key != "cache_control"}
            for tool in tools
        ]
    messages = body.get("messages")
    if isinstance(messages, list):
        plain_messages: list[dict[str, Any]] = []
        for message in messages:
            blocks = message.get("content")
            if isinstance(blocks, list):
                plain_messages.append(
                    {
                        **message,
                        "content": [
                            {
                                block_key: block_value
                                for block_key, block_value in block.items()
                                if block_key != "cache_control"
                            }
                            if isinstance(block, dict)
                            else block
                            for block in blocks
                        ],
                    }
                )
            else:
                plain_messages.append(message)
        body["messages"] = plain_messages


class AnthropicModelAdapter:
    def __init__(self, runtime: dict[str, Any], tools) -> None:
        self.runtime = runtime
        self.tools = tools
        # Cache the serialized tool list — tools rarely change within a session
        self._cached_tools_json: list[dict[str, Any]] | None = None
        self._tools_cache_key: int = 0  # hash of tool list for invalidation
        self._thinking_blocks: list[dict[str, Any]] = []  # Preserve thinking blocks for round-trip

    def request_tool_schemas(self, *, tools_override=None, disable_tools=False):
        """Return request schemas without mutating the cached tool catalog."""
        if disable_tools:
            return []
        schemas = (self._get_serialized_tools(tools_override) if tools_override is not None
                   else self._get_serialized_tools())
        body = {"tools": schemas}
        if self._prompt_cache_enabled():
            _apply_prompt_cache_markers(body)
        return body["tools"]

    def _get_serialized_tools(self, tools_override=None) -> list[dict[str, Any]]:
        """Get serialized tool list with caching."""
        if tools_override is not None:
            return [{"name": tool.name, "description": tool.description,
                     "input_schema": tool.input_schema} for tool in tools_override.list()]
        if self.tools is None:
            return []
        current_tools = self.tools.list()
        current_key = hash(tuple((t.name, t.description) for t in current_tools))
        if self._cached_tools_json is None or current_key != self._tools_cache_key:
            self._cached_tools_json = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in current_tools
            ]
            self._tools_cache_key = current_key
        return self._cached_tools_json

    def _prompt_cache_enabled(self) -> bool:
        """Prompt-cache markers are on unless explicitly disabled.

        The runtime key ``promptCache`` (default true) carries the host
        decision; the ``MINI_CODE_PROMPT_CACHE=0`` environment variable is
        an operator kill-switch that wins over the runtime. A provider that
        rejects the markers still gets one marker-free retry below, so an
        unknown gateway cannot break the model call outright.
        """
        kill = os.environ.get("MINI_CODE_PROMPT_CACHE", "1").strip().lower()
        if kill in {"0", "false", "no", "off"}:
            return False
        return bool(self.runtime.get("promptCache", True))

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
    ) -> AgentStep:
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

        system_message, converted_messages = _to_anthropic_messages(messages)

        # Replay stored thinking blocks into the first assistant message
        # with text content (DeepSeek extended thinking round-trip)
        if self._thinking_blocks and not isolated_reasoning_state:
            for i in range(len(converted_messages)):
                msg = converted_messages[i]
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        converted_messages[i] = dict(msg)
                        converted_messages[i]["content"] = list(self._thinking_blocks) + content
                        break
            self._thinking_blocks = []

        request_body = {
            "model": self.runtime["model"],
            "system": system_message,
            "messages": converted_messages,
        }
        if not disable_tools:
            serialized_tools = self.request_tool_schemas(tools_override=tools_override,
                disable_tools=disable_tools)
            if serialized_tools:
                request_body["tools"] = serialized_tools
        # Disable extended thinking for non-Anthropic models that support it
        # but require round-trip preservation our message format can't provide
        if self.runtime.get("disableThinking"):
            request_body["thinking"] = {"type": "disabled"}
        if self.runtime.get("maxOutputTokens") is not None:
            request_body["max_tokens"] = self.runtime["maxOutputTokens"]
        if self.runtime.get("temperature") is not None:
            request_body["temperature"] = self.runtime["temperature"]
        if max_output_tokens_override is not None:
            request_body["max_tokens"] = max_output_tokens_override
        if temperature_override is not None:
            request_body["temperature"] = float(temperature_override)
        if on_stream_chunk:
            request_body["stream"] = True

        # Prompt-cache breakpoints on the two stable, resent-every-step
        # blocks: the system prompt and the tool catalog. Bigmodel's
        # Anthropic-compatible endpoint honors them (verified 2026-09-24);
        # a gateway that rejects them gets one marker-free retry.
        prompt_cache_active = (
            self._prompt_cache_enabled()
            and _apply_prompt_cache_markers(request_body)
        )

        def _build_request(body: dict[str, Any]) -> urllib.request.Request:
            return urllib.request.Request(
                url=self.runtime["baseUrl"].rstrip("/") + "/v1/messages",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                    **(
                        {"x-api-key": self.runtime["apiKey"]}
                        if self.runtime.get("apiKey")
                        else {"Authorization": f"Bearer {self.runtime['authToken']}"}
                    ),
                },
                method="POST",
            )

        request = _build_request(request_body)

        max_retries = (
            max_retries_override
            if max_retries_override is not None
            else int(self.runtime.get("modelMaxRetries", _get_retry_limit()))
        )
        response = None
        response_read_timeout = 0.0
        for attempt in range(max_retries + 1):
            try:
                timeout = bounded_request_timeout(
                    float(
                        self.runtime.get("modelTimeoutSeconds")
                        or os.environ.get("MINICODE_MODEL_TIMEOUT", "60")
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
                # Use semantic error classification for adaptive backoff
                from minicode.api_retry import classify_error
                try:
                    retry_after_value = None
                    headers = getattr(error, "headers", None)
                    get_header = getattr(headers, "get", None)
                    if callable(get_header):
                        try:
                            retry_after_value = get_header("retry-after")
                        except Exception:  # noqa: BLE001 - malformed header shim
                            retry_after_value = None
                finally:
                    _close_response_safely(error)
                    response = None
                category = classify_error(error)
                retry_after = _parse_retry_after_seconds(retry_after_value)
                wait = calculate_backoff(attempt, retry_after=retry_after,
                                        category=category)
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
            raise RuntimeError("Model request failed before receiving a response")

        if not on_stream_chunk:
            data = _read_json_body(
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
            status = getattr(response, "status", getattr(response, "code", 200))
            if status >= 400:
                error_message = _extract_error_message(data, status)
                if (
                    status == 400
                    and prompt_cache_active
                    and "cache_control" in error_message.lower()
                ):
                    # The gateway rejected the prompt-cache markers, not the
                    # request itself. Degrade once to a marker-free body
                    # instead of failing the model call.
                    _strip_prompt_cache_markers(request_body)
                    prompt_cache_active = False
                    _close_response_safely(response)
                    response = _open_verified_url_controlled(
                        _build_request(request_body),
                        timeout=response_read_timeout,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                    data = _read_json_body(
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
                    status = getattr(
                        response, "status", getattr(response, "code", 200)
                    )
                    error_message = _extract_error_message(data, status)
                if status >= 400:
                    if store:
                        store.set_state(record_api_error())
                    raise RuntimeError(error_message)
    
            provider_usage = data.get("usage") if isinstance(data, dict) else None
            if isinstance(provider_usage, dict) and (
                "input_tokens" in provider_usage
                or "output_tokens" in provider_usage
            ):
                model_usage = ModelUsage(
                    input_tokens=int(provider_usage.get("input_tokens", 0) or 0),
                    output_tokens=int(provider_usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(
                        provider_usage.get("cache_read_input_tokens", 0) or 0
                    ),
                    cache_creation_tokens=int(
                        provider_usage.get("cache_creation_input_tokens", 0) or 0
                    ),
                    source="provider",
                )
            else:
                from minicode.context_manager import estimate_messages_tokens

                estimated_text = "\n".join(
                    str(block.get("text", ""))
                    for block in (data.get("content", []) if isinstance(data, dict) else [])
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                model_usage = ModelUsage(
                    input_tokens=estimate_messages_tokens(messages),
                    output_tokens=max(0, len(estimated_text) // 4),
                    source="estimated",
                )

            # Update store with API call success and cost tracking
            if store:
                from minicode.cost_tracker import calculate_cost
                input_tokens = model_usage.input_tokens or 0
                output_tokens = model_usage.output_tokens or 0
                cache_read_tokens = model_usage.cache_read_tokens or 0
                cache_creation_tokens = model_usage.cache_creation_tokens or 0
                
                cost_usd = calculate_cost(
                    model=self.runtime["model"],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                )
                if cost_usd > 0:
                    store.set_state(add_cost(cost_usd))
                
                # Update context usage
                total_tokens = input_tokens + output_tokens
                store.set_state(update_context_usage(total_tokens))
    
            tool_calls: list[dict[str, Any]] = []
            text_parts: list[str] = []
            block_types: list[str] = []
            ignored_block_types: list[str] = []
    
            for block in data.get("content", []) if isinstance(data, dict) else []:
                block_type = block.get("type")
                block_types.append(block_type)
                if block_type == "text" and isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
                elif block_type == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str):
                    tool_calls.append({"id": block["id"], "toolName": block["name"], "input": block.get("input")})
                elif block_type == "thinking" and not isolated_reasoning_state:
                    self._thinking_blocks.append(block)  # Preserve for round-trip
                else:
                    ignored_block_types.append(str(block_type))
    
            parsed_text, kind = _parse_assistant_text("\n".join(text_parts).strip())
            diagnostics = StepDiagnostics(
                stopReason=data.get("stop_reason") if isinstance(data, dict) else None,
                blockTypes=block_types,
                ignoredBlockTypes=ignored_block_types,
            )
    
            if tool_calls:
                return AgentStep(
                    type="tool_calls",
                    calls=tool_calls,
                    content=parsed_text,
                    contentKind="progress" if kind == "progress" else None,
                    diagnostics=diagnostics,
                    usage=model_usage,
                )
            return AgentStep(
                type="assistant",
                content=parsed_text,
                kind=kind,
                diagnostics=diagnostics,
                usage=model_usage,
            )

        # STREAMING PARSER
        tool_calls = []
        text_parts = []
        block_types = []
        ignored_block_types = []
        active_tool_call = None
        active_thinking_block = None
        stop_reason = None
        
        # Streaming cost tracking
        stream_input_tokens = 0
        stream_output_tokens = 0
        stream_cache_read_tokens = 0
        stream_cache_creation_tokens = 0
        stream_usage_seen = False
        
        def consume_stream_line(line: bytes) -> bool | None:
            nonlocal active_thinking_block
            nonlocal active_tool_call
            nonlocal stop_reason
            nonlocal stream_cache_creation_tokens
            nonlocal stream_cache_read_tokens
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
                
            etype = event.get("type")
            if etype == "message_start":
                # Initial usage from message_start
                msg = event.get("message", {})
                usage = msg.get("usage", {})
                stream_usage_seen = bool(usage)
                stream_input_tokens = usage.get("input_tokens", 0)
                stream_cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                stream_cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            elif etype == "content_block_start":
                cb = event.get("content_block", {})
                c_type = cb.get("type")
                block_types.append(c_type)
                if c_type == "tool_use":
                    active_tool_call = {
                        "id": cb.get("id"),
                        "name": cb.get("name"),
                        "input_json": ""
                    }
                elif c_type == "thinking":
                    active_thinking_block = {"type": "thinking", "thinking": ""}
            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                d_type = delta.get("type")
                if d_type == "text_delta":
                    chunk = delta.get("text", "")
                    text_parts.append(chunk)
                    on_stream_chunk(chunk)
                elif d_type == "input_json_delta":
                    if active_tool_call:
                        active_tool_call["input_json"] += delta.get("partial_json", "")
                elif d_type == "thinking_delta":
                    if active_thinking_block:
                        chunk = delta.get("thinking", "")
                        active_thinking_block["thinking"] += chunk
                        if on_thinking_delta:
                            on_thinking_delta(chunk)
                elif d_type == "signature_delta":
                    if active_thinking_block:
                        active_thinking_block["signature"] = active_thinking_block.get("signature", "") + delta.get("signature", "")
            elif etype == "content_block_stop":
                if active_tool_call:
                    try:
                        parsed_input = json.loads(active_tool_call["input_json"])
                    except (json.JSONDecodeError, TypeError):
                        parsed_input = {}
                    tool_calls.append({
                        "id": active_tool_call["id"],
                        "toolName": active_tool_call["name"],
                        "input": parsed_input
                    })
                    active_tool_call = None
                if active_thinking_block and not isolated_reasoning_state:
                    self._thinking_blocks.append(active_thinking_block)
                active_thinking_block = None
            elif etype == "message_delta":
                delta = event.get("delta", {})
                if "stop_reason" in delta:
                    stop_reason = delta["stop_reason"]
                # Final output tokens from message_delta
                usage = event.get("usage", {})
                if usage.get("output_tokens"):
                    stream_usage_seen = True
                    stream_output_tokens = usage["output_tokens"]
            elif etype == "message_stop":
                return False
            elif etype == "error":
                err = event.get("error", {})
                raise RuntimeError(f"Streaming error: {err.get('message', 'Unknown')}")

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
        
        if stream_usage_seen:
            model_usage = ModelUsage(
                input_tokens=int(stream_input_tokens or 0),
                output_tokens=int(stream_output_tokens or 0),
                cache_read_tokens=int(stream_cache_read_tokens or 0),
                cache_creation_tokens=int(stream_cache_creation_tokens or 0),
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

        # Update store with streaming cost tracking
        if store:
            from minicode.cost_tracker import calculate_cost
            cost_usd = calculate_cost(
                model=self.runtime["model"],
                input_tokens=stream_input_tokens,
                output_tokens=stream_output_tokens,
                cache_read_tokens=stream_cache_read_tokens,
                cache_creation_tokens=stream_cache_creation_tokens,
            )
            if cost_usd > 0:
                store.set_state(add_cost(cost_usd))
            total_tokens = stream_input_tokens + stream_output_tokens
            store.set_state(update_context_usage(total_tokens))
                
        parsed_text, kind = _parse_assistant_text("".join(text_parts).strip())
        diagnostics = StepDiagnostics(
            stopReason=stop_reason,
            blockTypes=block_types,
            ignoredBlockTypes=ignored_block_types,
        )
        if tool_calls:
            return AgentStep(
                type="tool_calls",
                calls=tool_calls,
                content=parsed_text,
                contentKind="progress" if kind == "progress" else None,
                diagnostics=diagnostics,
                usage=model_usage,
            )
        return AgentStep(
            type="assistant",
            content=parsed_text,
            kind=kind,
            diagnostics=diagnostics,
            usage=model_usage,
        )
