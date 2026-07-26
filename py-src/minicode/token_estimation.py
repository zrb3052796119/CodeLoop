"""Token estimation utilities with thread-safe LRU cache.

Extracted from context_manager.py to avoid circular imports with working_memory.py.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict


# Thread-safe LRU cache for token estimation
class _TokenCache:
    """Thread-safe LRU cache for token estimation with periodic cleanup."""

    def __init__(self, max_size: int = 10000, ttl: float = 300.0):
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: dict = {}
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key) -> int | None:
        with self._lock:
            if key in self._cache:
                # Check TTL
                if time.time() - self._timestamps[key] > self._ttl:
                    del self._cache[key]
                    del self._timestamps[key]
                    return None
                # Move to end (LRU)
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key, value: int) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._max_size:
                    # Remove oldest
                    oldest = next(iter(self._cache))
                    del self._cache[oldest]
                    del self._timestamps[oldest]
                self._cache[key] = value
            self._timestamps[key] = time.time()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()


_token_cache = _TokenCache(max_size=10000, ttl=300.0)

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    """Fast token estimation with thread-safe LRU cache.

    Heuristic token counting based on character distribution:
    - CJK characters: ~1.5 chars per token
    - ASCII characters: ~4 chars per token

    Performance: Uses regex for fast CJK detection + LRU cache with TTL.
    """
    if not text:
        return 0

    # Cache lookup (short strings as key, long strings by hash)
    cache_key = text if len(text) < 256 else hash(text)
    cached = _token_cache.get(cache_key)
    if cached is not None:
        return cached

    # Use regex for fast CJK detection
    cjk_count = len(_CJK_PATTERN.findall(text))
    ascii_chars = len(text) - cjk_count

    result = max(1, int(cjk_count / 1.5 + ascii_chars / 4.0))

    _token_cache.put(cache_key, result)
    return result


def estimate_message_tokens(message: dict[str, any]) -> int:
    """Estimate tokens for a single message."""
    tokens = 0

    # Role overhead
    role = message.get("role", "")
    if role == "system":
        tokens += 3
    elif role == "user":
        tokens += 4
    elif role == "assistant":
        tokens += 4
    elif role == "tool":
        tokens += 4
    else:
        tokens += 3

    # Content tokens
    content = message.get("content", "")
    if content:
        tokens += estimate_tokens(content)

    # Tool call tokens
    if "tool_calls" in message:
        for call in message["tool_calls"]:
            tokens += 10  # Base overhead per tool call
            if "function" in call:
                func = call["function"]
                name = func.get("name", "")
                if name:
                    tokens += estimate_tokens(name)
                args = func.get("arguments", "")
                if args:
                    tokens += estimate_tokens(str(args))

    # Tool result tokens
    if "tool_results" in message:
        for result in message["tool_results"]:
            tokens += 5  # Base overhead per result
            result_content = result.get("content", "")
            if result_content:
                tokens += estimate_tokens(str(result_content))

    # Input tokens (for tool calls)
    input_data = message.get("input", "")
    if input_data:
        input_str = input_data if isinstance(input_data, str) else str(input_data)
        tokens += estimate_tokens(input_str)

    return tokens


def estimate_messages_tokens(messages: list[dict[str, any]]) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_message_tokens(msg) for msg in messages)


def clear_token_cache() -> None:
    """Clear the token estimation cache."""
    _token_cache.clear()
