"""Context window management for LLM conversations.

Tracks token usage, estimates context window consumption, and provides
auto-compaction to prevent context overflow in long conversations.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from minicode.config import MINI_CODE_DIR


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default context window sizes (tokens)
DEFAULT_CONTEXT_WINDOWS = {
    # Anthropic
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-haiku-3-20240307": 100_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # OpenRouter popular models
    "openrouter/auto": 200_000,
    "anthropic/claude-sonnet-4": 200_000,
    "anthropic/claude-opus-4": 200_000,
    "openai/gpt-4o": 128_000,
    "openai/gpt-4o-mini": 128_000,
    "google/gemini-2.5-pro": 1_000_000,
    "google/gemini-2.5-flash": 1_000_000,
    "meta-llama/llama-4-maverick": 1_000_000,
    "deepseek/deepseek-r1": 128_000,
    "deepseek/deepseek-chat": 128_000,
    "qwen/qwen3-235b-a22b": 128_000,
    "minimax/minimax-m1": 1_000_000,
    "default": 128_000,  # Fallback
}

# Canonical auto-compaction high-water mark. ContextCompactor owns the
# compression implementation; ContextManager uses the same threshold solely
# for accounting/status compatibility.
AUTOCOMPACT_THRESHOLD = 0.85

# Estimated tokens per character (rough average for English/Code)
CHARS_PER_TOKEN = 4.0

# Minimum messages to keep after compaction
MIN_MESSAGES_TO_KEEP = 10

# System prompt is always kept (counts as 1 message)
SYSTEM_PROMPT_RESERVED = 1


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

# 预编译的正则表达式用于快速 CJK 字符检测
_CJK_PATTERN = re.compile(r'[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF]')

# LRU 缓存：token 估算被频繁调用（每条消息、每次上下文检查），
# 相同文本的 token 数是确定性的，缓存可避免重复计算。
_token_cache: dict[str, int] = {}
_TOKEN_CACHE_MAX = 1024


def estimate_tokens(text: str) -> int:
    """改进的 token 估算，支持中英文
    
    - 英文/代码：约 4 字符/token
    - 中文/日文：约 1.5 字符/token
    - 混合文本：使用启发式估算
    
    性能优化：使用正则表达式替代逐字符 ord() 检查，速度快 10-50 倍。
    带 LRU 缓存避免重复计算相同文本。
    """
    if not text:
        return 0
    
    # 缓存查找（短文本优先缓存）
    cache_key = text if len(text) < 256 else hash(text)  # 长文本用 hash 作为 key
    cached = _token_cache.get(cache_key)
    if cached is not None:
        return cached
    
    # 使用正则表达式快速统计 CJK 字符数量
    cjk_count = len(_CJK_PATTERN.findall(text))
    
    # CJK 字符约 1.5 字符/token，英文约 4 字符/token
    ascii_chars = len(text) - cjk_count
    
    result = max(1, int(cjk_count / 1.5 + ascii_chars / 4.0))
    
    # 缓存结果（防止无限增长）
    if len(_token_cache) < _TOKEN_CACHE_MAX:
        _token_cache[cache_key] = result
    
    return result


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate tokens for a single message."""
    tokens = 0
    
    # Role overhead
    role = message.get("role", "")
    if role == "system":
        tokens += 3  # System prompt overhead
    elif role == "user":
        tokens += 4  # User message overhead
    elif role == "assistant":
        tokens += 3  # Assistant overhead
    elif role == "assistant_tool_call":
        tokens += 7  # Tool call overhead
    elif role == "tool_result":
        tokens += 6  # Tool result overhead
    elif role == "assistant_progress":
        tokens += 3
    
    # Content tokens
    content = message.get("content", "")
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    
    # Tool call input/output
    if "input" in message:
        input_str = json.dumps(message["input"]) if isinstance(message["input"], dict) else str(message["input"])
        tokens += estimate_tokens(input_str)
    
    return tokens


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_message_tokens(msg) for msg in messages)




# ---------------------------------------------------------------------------
# Context tracking
# ---------------------------------------------------------------------------

@dataclass
class ContextStats:
    """Current context window statistics."""
    total_tokens: int = 0
    context_window: int = 0
    usage_percentage: float = 0.0
    messages_count: int = 0
    system_tokens: int = 0
    conversation_tokens: int = 0
    tool_calls_count: int = 0
    is_near_limit: bool = False
    should_compact: bool = False


@dataclass
class ContextManager:
    """Manages context window tracking and auto-compaction."""
    model: str = "default"
    context_window: int = 0
    _messages: list[dict[str, Any]] = field(default_factory=list, repr=False)
    compaction_history: list[dict[str, Any]] = field(default_factory=list)
    _token_cache: dict[int, int] = field(default_factory=dict, repr=False)  # id(msg) -> tokens

    # EMA ratio between estimated and provider-reported tokens (1.0 = the
    # estimator is unbiased). Applied inside get_stats().
    _token_calibration: float = field(default=1.0, repr=False)

    # Turn-to-turn controller reuse. ``run_agent_turn`` normally rebuilds its
    # compactor and cybernetic orchestrator from scratch, which silently reset
    # PID/predictor/dedup/microcompact state every user turn. The owning
    # ContextManager lives for the whole TUI/dashboard session, so it is the
    # natural place to carry those objects across turns.
    _context_compactor: Any = field(default=None, repr=False, compare=False)
    _context_cybernetics: Any = field(default=None, repr=False, compare=False)
    _controller_model: str = field(default="", repr=False, compare=False)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        # External code replaces the whole list after compaction. The token
        # cache is keyed by id(); CPython reuses freed addresses, so a stale
        # cache would silently serve wrong counts for the new list.
        self._messages = value
        self._token_cache.clear()

    def __post_init__(self):
        if self.context_window == 0:
            self.context_window = DEFAULT_CONTEXT_WINDOWS.get(
                self.model, DEFAULT_CONTEXT_WINDOWS["default"]
            )
    
    def update_model(self, model: str) -> None:
        """Update model and adjust context window."""
        self.model = model
        self.context_window = DEFAULT_CONTEXT_WINDOWS.get(
            model, DEFAULT_CONTEXT_WINDOWS["default"]
        )

    def record_observed_tokens(self, observed_tokens: int) -> None:
        """Calibrate the estimator against provider-reported input tokens.

        Character-based estimation is systematically wrong for CJK-heavy or
        tool-heavy contexts; a slow-moving ratio learned from real API usage
        keeps the compaction thresholds honest.
        """
        if observed_tokens <= 0 or not self._messages:
            return
        estimated = sum(
            self._token_cache.get(id(m)) or estimate_message_tokens(m)
            for m in self._messages
        )
        if estimated <= 0:
            return
        ratio = min(3.0, max(0.25, observed_tokens / estimated))
        # EMA: one odd reading moves the ratio a little, a consistent bias
        # moves it all the way.
        self._token_calibration = 0.8 * self._token_calibration + 0.2 * ratio
    
    def add_message(self, message: dict[str, Any]) -> None:
        """Add a message and update tracking."""
        self.messages.append(message)
        # Cache token count immediately to avoid re-estimation in get_stats()
        self._token_cache[id(message)] = estimate_message_tokens(message)
    
    def get_stats(self) -> ContextStats:
        """Calculate current context statistics.
        
        Uses cached token counts when available (O(1) amortized for
        messages added via add_message).
        """
        if not self.messages:
            return ContextStats(
                context_window=self.context_window,
            )
        
        # Count tokens using cache when available
        system_tokens = 0
        conversation_tokens = 0
        tool_calls = 0
        
        for msg in self.messages:
            msg_tokens = self._token_cache.get(id(msg))
            if msg_tokens is None:
                msg_tokens = estimate_message_tokens(msg)
                self._token_cache[id(msg)] = msg_tokens
            if msg.get("role") == "system":
                system_tokens += msg_tokens
            else:
                conversation_tokens += msg_tokens
            
            if msg.get("role") == "assistant_tool_call":
                tool_calls += 1
        
        total_tokens = int(
            (system_tokens + conversation_tokens) * self._token_calibration
        )
        usage_pct = (total_tokens / self.context_window * 100) if self.context_window > 0 else 0

        is_near_limit = usage_pct >= 80  # Warning at 80%
        should_compact = usage_pct >= (AUTOCOMPACT_THRESHOLD * 100)

        return ContextStats(
            total_tokens=total_tokens,
            context_window=self.context_window,
            usage_percentage=usage_pct,
            messages_count=len(self.messages),
            system_tokens=system_tokens,
            conversation_tokens=conversation_tokens,
            tool_calls_count=tool_calls,
            is_near_limit=is_near_limit,
            should_compact=should_compact,
        )
    
    def should_auto_compact(self) -> bool:
        """Report the canonical ContextCompactor high-water condition."""
        return self.get_stats().should_compact
    
    def compact_messages(self) -> list[dict[str, Any]]:
        """Compact through the sole canonical ContextCompactor implementation.

        This compatibility interface retains ContextManager's accounting and
        history surface while delegating every compression decision and
        transformation to ContextCompactor.
        """
        stats = self.get_stats()
        if not stats.should_compact:
            return self.messages

        if self._context_compactor is None:
            from minicode.context_compactor import AutoCompactConfig, ContextCompactor

            self._context_compactor = ContextCompactor(
                context_window=self.context_window,
                estimate_fn=estimate_message_tokens,
                config=AutoCompactConfig(
                    threshold_ratio=AUTOCOMPACT_THRESHOLD,
                    circuit_breaker_limit=3,
                    session_memory_enabled=False,
                ),
            )
        else:
            self._context_compactor._context_window = self.context_window

        compacted = self._context_compactor.compact_messages(
            self.messages,
            force=True,
        )
        result = self._context_compactor.last_result
        if compacted == self.messages or result is None or not result.effective:
            return self.messages

        self.messages = list(compacted)
        boundary = result.boundary
        self.compaction_history.append(
            {
                "timestamp": time.time(),
                "before_tokens": (
                    boundary.tokens_before if boundary is not None else stats.total_tokens
                ),
                "after_tokens": (
                    boundary.tokens_after
                    if boundary is not None
                    else estimate_messages_tokens(compacted)
                ),
                "messages_removed": (
                    boundary.messages_removed
                    if boundary is not None
                    else stats.messages_count - len(compacted)
                ),
                "backend": "context_compactor",
                "strategy": result.strategy.value,
            }
        )
        return self.messages
    
    def get_context_summary(self) -> str:
        """Get a human-readable context usage summary."""
        stats = self.get_stats()
        
        if stats.messages_count == 0:
            return "Context: empty"
        
        status = "✓"
        if stats.is_near_limit:
            status = "⚠"
        if stats.should_compact:
            status = "🔴"
        
        return (
            f"Context: {status} {stats.usage_percentage:.0f}% "
            f"({stats.total_tokens:,}/{stats.context_window:,} tokens, "
            f"{stats.messages_count} msgs, {stats.tool_calls_count} tools)"
        )
    
    def format_context_details(self) -> str:
        """Get detailed context information for /context command."""
        stats = self.get_stats()
        
        lines = [
            "Context Window Usage",
            "=" * 50,
            f"Model: {self.model}",
            f"Context window: {stats.context_window:,} tokens",
            "",
            f"Total tokens: {stats.total_tokens:,}",
            f"Usage: {stats.usage_percentage:.1f}%",
            f"Messages: {stats.messages_count}",
            f"Tool calls: {stats.tool_calls_count}",
            "",
        ]
        
        if stats.should_compact:
            lines.append("⚠️  WARNING: Context is near capacity!")
            lines.append("Auto-compaction will trigger soon.")
            lines.append("")
        
        if self.compaction_history:
            lines.append("Compaction History:")
            for comp in self.compaction_history[-3:]:  # Last 3
                ts = time.strftime("%H:%M:%S", time.localtime(comp["timestamp"]))
                lines.append(
                    f"  {ts}: {comp['messages_removed']} messages removed, "
                    f"{comp['before_tokens']:,} → {comp['after_tokens']:,} tokens"
                )
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_context_state(manager: ContextManager) -> None:
    """Save context manager state to disk."""
    state_path = MINI_CODE_DIR / "context_state.json"
    MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    
    state = {
        "model": manager.model,
        "context_window": manager.context_window,
        "messages": manager.messages,
        "compaction_history": manager.compaction_history[-10:],  # Keep last 10
        "_token_calibration": manager._token_calibration,
    }
    
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_context_state() -> ContextManager | None:
    """Load context manager state from disk."""
    state_path = MINI_CODE_DIR / "context_state.json"
    if not state_path.exists():
        return None
    
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        manager = ContextManager(
            model=state.get("model", "default"),
            context_window=state.get("context_window", 0),
            _messages=state.get("messages", []),
            compaction_history=state.get("compaction_history", []),
        )
        if "_token_calibration" in state:
            manager._token_calibration = float(state["_token_calibration"] or 1.0)
        return manager
    except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError):
        return None


def clear_context_state() -> None:
    """Clear saved context state."""
    state_path = MINI_CODE_DIR / "context_state.json"
    if state_path.exists():
        state_path.unlink()
