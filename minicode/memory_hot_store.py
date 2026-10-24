"""Optional, disposable Redis state; durable memory never depends on this module."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from minicode.logging_config import get_logger
from minicode.memory_path_identity import normalize_path_identity

logger = get_logger("memory_hot_store")
_T = TypeVar("_T")
# Leave a little scheduling headroom below the 200 ms turn limit. Socket
# timeouts alone do not bound DNS, reconnects, or a scan of many Redis keys.
_WAIT_BUDGET_SECONDS = 0.190


@dataclass
class _WaitBudget:
    remaining: float = _WAIT_BUDGET_SECONDS


_turn_budget: ContextVar[_WaitBudget | None] = ContextVar(
    "memory_hot_store_wait_budget", default=None
)
_stores: dict[tuple[str, str], MemoryHotStore] = {}
_stores_lock = threading.Lock()


def project_fingerprint(project_root: str | Path) -> str:
    """Reuse memory path normalization, including projects with spaces."""
    resolved = str(Path(project_root).expanduser().resolve())
    identity = normalize_path_identity(resolved, max_chars=max(256, len(resolved)))
    return hashlib.sha256((identity or resolved).encode("utf-8")).hexdigest()


def redis_draft_ttl_seconds(fallback: int = 14 * 24 * 60 * 60) -> int:
    try:
        ttl = int(os.environ.get("MINI_CODE_MEMORY_REDIS_DRAFT_TTL", fallback))
        return ttl if ttl > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def get_memory_hot_store(project_root: str | Path) -> MemoryHotStore:
    """Share the process fuse between retrieval and deposition in one project."""
    url = os.environ.get("MINI_CODE_MEMORY_REDIS_URL", "").strip()
    fingerprint = project_fingerprint(project_root)
    key = (url, fingerprint)
    with _stores_lock:
        if key not in _stores:
            _stores[key] = MemoryHotStore(url, project_fingerprint=fingerprint)
        return _stores[key]


class MemoryHotStore:
    """Redis-backed ephemeral layer. All failures degrade to no-op.

    Call ``begin_turn`` at injection to share one cumulative wait allowance
    with later writes. ``budget`` supplies the same bound to standalone callers
    and composes with nested retrieval calls without resetting that allowance.
    """

    def __init__(self, url: str | None, *, project_fingerprint: str = "") -> None:
        self._url = str(url or "").strip()
        self._project_fingerprint = project_fingerprint
        self._broken = False
        self._client: Any = None
        self._operation_lock = threading.Lock()
        self._breaker_lock = threading.Lock()

    @property
    def available(self) -> bool:
        # Deliberately avoid imports, DNS, and PING from a state inspection.
        return bool(self._url) and not self._broken

    def begin_turn(self) -> None:
        _turn_budget.set(_WaitBudget())

    @contextmanager
    def budget(self) -> Iterator[None]:
        if _turn_budget.get() is not None:
            yield
            return
        token = _turn_budget.set(_WaitBudget())
        try:
            yield
        finally:
            _turn_budget.reset(token)

    def _break(self, error: Exception) -> None:
        with self._breaker_lock:
            if self._broken:
                return
            self._broken = True
        # Exception messages and the URL can contain Redis credentials.
        logger.warning(
            "Redis memory hot store disabled for this process (%s)",
            type(error).__name__,
        )

    def _connect(self, deadline: float) -> Any:
        if self._client is None:
            import redis
            from redis.backoff import NoBackoff
            from redis.retry import Retry

            options = {
                "socket_connect_timeout": 0.2,
                "socket_timeout": 0.05,
                "retry": Retry(NoBackoff(), 0),
                "retry_on_timeout": False,
                "retry_on_error": [],
                "decode_responses": True,
            }
            # redis-py gives URL query options precedence over keyword args.
            # Do not let a URL silently override our timeout/retry contract.
            parsed = urlsplit(self._url)
            query = urlencode(
                [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if key not in options]
            )
            if not self.available or time.monotonic() >= deadline:
                raise TimeoutError("Redis connection budget exhausted")
            self._client = redis.Redis.from_url(
                urlunsplit(parsed._replace(query=query)), **options
            )
            # Imports/client setup may finish after the caller has fused. Never
            # issue a delayed PING in that case, even though setup itself has
            # completed successfully.
            if not self.available or time.monotonic() >= deadline:
                raise TimeoutError("Redis connection budget exhausted")
            self._client.ping()
        return self._client

    def _call(self, operation: Callable[[Any], _T], default: _T) -> _T:
        """Bound Redis I/O only; local validation/JSON work stays outside the fuse."""
        if not self.available:
            return default
        with self.budget():
            budget = _turn_budget.get()
            assert budget is not None
            started = time.monotonic()
            deadline = started + max(0.0, budget.remaining)
            if budget.remaining <= 0:
                self._break(TimeoutError())
                return default

            # One in-flight worker per instance; a stalled worker is daemonized
            # and permanently fused, so timeouts cannot accumulate new workers.
            if not self._operation_lock.acquire(timeout=budget.remaining):
                budget.remaining = 0.0
                self._break(TimeoutError())
                return default
            if not self.available:
                self._operation_lock.release()
                budget.remaining -= time.monotonic() - started
                return default

            done = threading.Event()
            result: list[_T] = []

            def run() -> None:
                try:
                    client = self._connect(deadline)
                    if self.available and time.monotonic() < deadline:
                        result.append(operation(client))
                    else:
                        self._break(TimeoutError())
                except Exception as exc:  # noqa: BLE001 - optional Redis must always degrade
                    self._break(exc)
                finally:
                    self._operation_lock.release()
                    done.set()

            worker = threading.Thread(target=run, name="memory-redis", daemon=True)
            try:
                worker.start()
            except RuntimeError as exc:
                self._operation_lock.release()
                self._break(exc)
                return default
            finished = done.wait(max(0.0, deadline - time.monotonic()))
            budget.remaining -= time.monotonic() - started
            if not finished:
                self._break(TimeoutError())
                return default
            return result[0] if self.available and result else default

    @staticmethod
    def _prefix(fingerprint: str) -> str:
        return f"minicode:mem:{quote(str(fingerprint), safe='')}:"

    @staticmethod
    def _local(operation: str, action: Callable[[], _T]) -> _T | None:
        """Skip bad caller/data input without declaring the connection broken."""
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - optional local work must degrade too
            # Never log payloads, keys, URLs or exception text containing secrets.
            logger.warning("Redis memory hot store skipped %s (%s)", operation, type(exc).__name__)
            return None

    @staticmethod
    def _key_part(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Key component must be a nonempty string")
        return quote(value, safe="")

    @staticmethod
    def _ttl(ttl_seconds: int) -> int:
        if type(ttl_seconds) is not int or not 0 < ttl_seconds <= (2**63 - 1) // 1000:
            raise ValueError("TTL must be a positive Redis-representable integer")
        return ttl_seconds

    @staticmethod
    def _encode(value: dict) -> str:
        if not isinstance(value, dict):
            raise TypeError("Payload must be a dictionary")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _decode(raw: str | bytes) -> dict:
        def reject_constant(_value: str) -> None:
            raise ValueError("Nonfinite JSON number")

        value = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise TypeError("Payload must be a dictionary")
        return value

    def put_draft(
        self,
        scope: str,
        project_fingerprint: str,
        entry: dict,
        ttl_seconds: int,
    ) -> bool:
        if not self.available:
            return False

        def prepare() -> tuple[str, str, int]:
            key = (
                f"minicode:mem:{self._key_part(project_fingerprint)}:draft:"
                f"{self._key_part(getattr(scope, 'value', scope))}:"
                f"{self._key_part(entry['id'])}"
            )
            return key, self._encode(entry), self._ttl(ttl_seconds)

        prepared = self._local("draft write", prepare)
        if prepared is None:
            return False
        key, value, ttl = prepared
        return self._call(lambda client: bool(client.set(key, value, ex=ttl)), False)

    def load_active_drafts(self, scope: str, project_fingerprint: str) -> list[dict]:
        if not self.available:
            return []
        prefix = self._local(
            "draft lookup",
            lambda: (
                f"minicode:mem:{self._key_part(project_fingerprint)}:draft:"
                f"{self._key_part(getattr(scope, 'value', scope))}:"
            ),
        )
        if prefix is None:
            return []

        def load(client: Any) -> list[str | bytes | None]:
            # Check the fuse between SCAN pages, so a timed-out worker never
            # continues issuing commands. SCAN may return duplicate keys.
            cursor = 0
            seen: set[str] = set()
            while self.available:
                cursor, batch = client.scan(cursor=cursor, match=prefix + "*", count=100)
                seen.update(batch)
                if cursor == 0:
                    break
            keys = sorted(seen)
            if not self.available or not keys:
                return []
            return client.mget(keys)

        entries: list[dict] = []
        for raw in self._call(load, []):
            if raw is None:  # A key can expire between SCAN and MGET.
                continue
            entry = self._local("draft decoding", lambda: self._decode(raw))
            if entry is not None:
                entries.append(entry)
        return entries

    def get_adjudication(self, cache_key: str) -> dict | None:
        if not self.available:
            return None
        key = self._local(
            "adjudication lookup",
            lambda: self._prefix(self._project_fingerprint) + f"adj:{self._key_part(cache_key)}",
        )
        if key is None:
            return None
        raw = self._call(lambda client: client.get(key), None)
        if raw is None:
            return None
        return self._local("adjudication decoding", lambda: self._decode(raw))

    def put_adjudication(
        self, cache_key: str, value: dict, ttl_seconds: int = 3600
    ) -> None:
        if not self.available:
            return
        prepared = self._local(
            "adjudication write",
            lambda: (
                self._prefix(self._project_fingerprint) + f"adj:{self._key_part(cache_key)}",
                self._encode(value),
                self._ttl(ttl_seconds),
            ),
        )
        if prepared is None:
            return
        key, payload, ttl = prepared
        self._call(lambda client: client.set(key, payload, ex=ttl), None)

    def acquire_lease(self, name: str, ttl_seconds: int) -> bool:
        """Acquire an expiry-only lease; curator does not use it yet.

        Before adding renewal/release, return and retain the ownership token.
        Release must atomically compare that token and DEL via Lua, never use
        an unconditional DEL that could delete a successor's lease.
        """
        if not self.available:
            return False
        prepared = self._local(
            "lease acquisition",
            lambda: (
                self._prefix(self._project_fingerprint) + f"lease:{self._key_part(name)}",
                uuid.uuid4().hex,
                self._ttl(ttl_seconds) * 1000,
            ),
        )
        if prepared is None:
            return False
        key, token, ttl_ms = prepared
        return self._call(
            lambda client: bool(client.set(key, token, nx=True, px=ttl_ms)),
            False,
        )
