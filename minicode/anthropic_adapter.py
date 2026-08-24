from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from minicode.api_retry import (
    RETRYABLE_STATUS,
    calculate_backoff,
)
from minicode.model_call_control import (
    bounded_request_timeout,
    checkpoint_model_call,
    controlled_retry_sleep,
)
from minicode.state import AppState, Store, add_cost, record_api_error, update_context_usage
from minicode.tls import open_verified_url
from minicode.types import AgentStep, ModelUsage, StepDiagnostics

DEFAULT_MAX_RETRIES = 4


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


def _read_json_body(response) -> Any:
    text = response.read().decode("utf-8")
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


class AnthropicModelAdapter:
    def __init__(self, runtime: dict[str, Any], tools) -> None:
        self.runtime = runtime
        self.tools = tools
        # Cache the serialized tool list — tools rarely change within a session
        self._cached_tools_json: list[dict[str, Any]] | None = None
        self._tools_cache_key: int = 0  # hash of tool list for invalidation
        self._thinking_blocks: list[dict[str, Any]] = []  # Preserve thinking blocks for round-trip

    def _get_serialized_tools(self) -> list[dict[str, Any]]:
        """Get serialized tool list with caching."""
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

    def next(
        self,
        messages: list[dict[str, Any]],
        on_stream_chunk: Callable[[str], None] | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
        store: Store[AppState] | None = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> AgentStep:
        system_message, converted_messages = _to_anthropic_messages(messages)

        # Replay stored thinking blocks into the first assistant message
        # with text content (DeepSeek extended thinking round-trip)
        if self._thinking_blocks:
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
        serialized_tools = self._get_serialized_tools()
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
        if on_stream_chunk:
            request_body["stream"] = True

        request = urllib.request.Request(
            url=self.runtime["baseUrl"].rstrip("/") + "/v1/messages",
            data=json.dumps(request_body).encode("utf-8"),
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

        max_retries = int(
            self.runtime.get("modelMaxRetries", _get_retry_limit())
        )
        response = None
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
                response = open_verified_url(request, timeout=timeout)
                checkpoint_model_call(
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                break
            except urllib.error.HTTPError as error:
                response = error
                if error.code not in RETRYABLE_STATUS or attempt >= max_retries:
                    break
                # Use semantic error classification for adaptive backoff
                from minicode.api_retry import classify_error
                category = classify_error(error)
                retry_after = _parse_retry_after_seconds(error.headers.get("retry-after"))
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
            data = _read_json_body(response)
            checkpoint_model_call(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            status = getattr(response, "status", getattr(response, "code", 200))
            if status >= 400:
                if store:
                    store.set_state(record_api_error())
                raise RuntimeError(_extract_error_message(data, status))
    
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
                elif block_type == "thinking":
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
        
        for line in response:
            checkpoint_model_call(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
                
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
                    except Exception:
                        parsed_input = {}
                    tool_calls.append({
                        "id": active_tool_call["id"],
                        "toolName": active_tool_call["name"],
                        "input": parsed_input
                    })
                    active_tool_call = None
                if active_thinking_block:
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
            elif etype == "error":
                err = event.get("error", {})
                raise RuntimeError(f"Streaming error: {err.get('message', 'Unknown')}")
        
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
