"""Shared turn-level budget for the top-level Agent and its sub-agents.

A single :class:`AgentTurnBudget` object is created for one user Turn and is
passed through ``ToolContext`` into every nested ``task`` sub-agent. Parent and
children reserve and record against the same thread-safe counters, so parallel
read-only sub-agents cannot each spend as if they were the only consumer.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

_NANO_USD_PER_USD = Decimal(1_000_000_000)
DEFAULT_MAX_TOTAL_TOKENS = 1_000_000
DEFAULT_MAX_MODEL_CALLS = 80
DEFAULT_MAX_COST_USD = Decimal("5.00")


def _optional_int(value: object, *, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _optional_decimal(value: object, *, name: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


@dataclass(frozen=True, slots=True)
class AgentBudgetSnapshot:
    """Content-free counters for journals and parent-run observations."""

    limit_total_tokens: int | None
    limit_model_calls: int | None
    limit_cost_usd: str | None
    used_total_tokens: int
    reserved_total_tokens: int
    used_model_calls: int
    used_cost_usd: str

    def to_dict(self) -> dict[str, object]:
        return {
            "limitTotalTokens": self.limit_total_tokens,
            "limitModelCalls": self.limit_model_calls,
            "limitCostUsd": self.limit_cost_usd,
            "usedTotalTokens": self.used_total_tokens,
            "reservedTotalTokens": self.reserved_total_tokens,
            "usedModelCalls": self.used_model_calls,
            "usedCostUsd": self.used_cost_usd,
        }


class AgentBudgetExceeded(RuntimeError):
    """Raised when the shared turn budget cannot admit another model call."""

    def __init__(self, reason: str, snapshot: AgentBudgetSnapshot) -> None:
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot


@dataclass(frozen=True, slots=True)
class AgentBudgetReservation:
    """Lease for one admitted model call's estimated input tokens."""

    reservation_id: int
    estimated_input_tokens: int


class AgentTurnBudget:
    """Thread-safe shared token/model-call/cost budget for one Agent Turn."""

    def __init__(
        self,
        *,
        max_total_tokens: int | None = None,
        max_model_calls: int | None = None,
        max_cost_usd: Decimal | str | None = None,
    ) -> None:
        self.max_total_tokens = _optional_int(
            max_total_tokens, name="max_total_tokens"
        )
        self.max_model_calls = _optional_int(
            max_model_calls, name="max_model_calls"
        )
        self.max_cost_usd = _optional_decimal(
            max_cost_usd, name="max_cost_usd"
        )
        self._lock = threading.Lock()
        self._used_total_tokens = 0
        self._used_model_calls = 0
        self._used_cost_usd = Decimal("0.0")
        self._reserved_total_tokens = 0
        self._next_reservation_id = 1
        self._reservations: dict[int, int] = {}

    @classmethod
    def from_runtime(cls, runtime: Mapping[str, Any] | None) -> "AgentTurnBudget":
        values = runtime or {}
        settings = values.get("agentTurnBudget")
        if not isinstance(settings, Mapping):
            settings = {}
        env = os.environ
        return cls(
            max_total_tokens=_optional_int(
                env.get("MINI_CODE_TURN_BUDGET_TOKENS")
                or values.get("agentTurnBudgetTokens")
                or settings.get("maxTokens")
                or DEFAULT_MAX_TOTAL_TOKENS,
                name="max_total_tokens",
            ),
            max_model_calls=_optional_int(
                env.get("MINI_CODE_TURN_BUDGET_MODEL_CALLS")
                or values.get("agentTurnBudgetModelCalls")
                or settings.get("maxModelCalls")
                or DEFAULT_MAX_MODEL_CALLS,
                name="max_model_calls",
            ),
            max_cost_usd=_optional_decimal(
                env.get("MINI_CODE_TURN_BUDGET_COST_USD")
                or values.get("agentTurnBudgetCostUsd")
                or settings.get("maxCostUsd")
                or DEFAULT_MAX_COST_USD,
                name="max_cost_usd",
            ),
        )

    @property
    def has_limits(self) -> bool:
        return any(
            limit is not None
            for limit in (
                self.max_total_tokens,
                self.max_model_calls,
                self.max_cost_usd,
            )
        )

    def snapshot(self) -> AgentBudgetSnapshot:
        with self._lock:
            return AgentBudgetSnapshot(
                limit_total_tokens=self.max_total_tokens,
                limit_model_calls=self.max_model_calls,
                limit_cost_usd=(
                    format(self.max_cost_usd, "f")
                    if self.max_cost_usd is not None
                    else None
                ),
                used_total_tokens=self._used_total_tokens,
                reserved_total_tokens=self._reserved_total_tokens,
                used_model_calls=self._used_model_calls,
                used_cost_usd=format(self._used_cost_usd, "f"),
            )

    def reserve_model_call(
        self, estimated_input_tokens: int = 0
    ) -> AgentBudgetReservation:
        """Admit one model call against the shared budget, or raise.

        The estimate is a guard against sending an already-oversized context;
        actual token usage is recorded after the provider responds.
        """
        estimate = max(0, int(estimated_input_tokens or 0))
        with self._lock:
            if (
                self.max_model_calls is not None
                and self._used_model_calls >= self.max_model_calls
            ):
                raise AgentBudgetExceeded(
                    "model call budget exhausted", self._locked_snapshot()
                )
            if (
                self.max_cost_usd is not None
                and self._used_cost_usd >= self.max_cost_usd
            ):
                raise AgentBudgetExceeded(
                    "cost budget exhausted", self._locked_snapshot()
                )
            if (
                self.max_total_tokens is not None
                and self._used_total_tokens + self._reserved_total_tokens + estimate
                > self.max_total_tokens
            ):
                raise AgentBudgetExceeded(
                    "token budget exhausted", self._locked_snapshot()
                )
            self._used_model_calls += 1
            reservation = AgentBudgetReservation(
                reservation_id=self._next_reservation_id,
                estimated_input_tokens=estimate,
            )
            self._next_reservation_id += 1
            self._reservations[reservation.reservation_id] = estimate
            self._reserved_total_tokens += estimate
            return reservation

    def record_model_call(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal | str | float | None = None,
        reservation: AgentBudgetReservation | None = None,
    ) -> None:
        """Record actual usage from one completed model response."""
        safe_input = max(0, int(input_tokens or 0))
        safe_output = max(0, int(output_tokens or 0))
        with self._lock:
            reservation_id = (
                reservation.reservation_id
                if isinstance(reservation, AgentBudgetReservation)
                else next(iter(self._reservations), None)
            )
            if reservation_id is not None:
                reserved = self._reservations.pop(reservation_id, 0)
                self._reserved_total_tokens = max(
                    0, self._reserved_total_tokens - reserved
                )
            self._used_total_tokens += safe_input + safe_output
            if cost_usd is not None:
                try:
                    parsed_cost = Decimal(str(cost_usd))
                except Exception:
                    parsed_cost = Decimal("0.0")
                if parsed_cost.is_finite() and parsed_cost > 0:
                    self._used_cost_usd += parsed_cost

    def fail_model_call(
        self,
        reservation: AgentBudgetReservation | None,
        *,
        charge_estimate: bool = False,
    ) -> bool:
        """Settle one failed/abandoned model-call reservation.

        Admission already increments ``used_model_calls`` and that count is
        intentionally preserved: a provider failure still consumed one call
        attempt.  Only the pending token lease is released.  Callers that know
        the request was transmitted but received no usage may conservatively
        charge the estimate instead.
        """
        if not isinstance(reservation, AgentBudgetReservation):
            return False
        with self._lock:
            reserved = self._reservations.pop(reservation.reservation_id, None)
            if reserved is None:
                return False
            self._reserved_total_tokens = max(
                0,
                self._reserved_total_tokens - reserved,
            )
            if charge_estimate:
                self._used_total_tokens += reserved
            return True

    def record_cost_nano_usd(self, amount_nano_usd: int | None) -> None:
        if (
            isinstance(amount_nano_usd, int)
            and not isinstance(amount_nano_usd, bool)
            and amount_nano_usd > 0
        ):
            with self._lock:
                self._used_cost_usd += (
                    Decimal(amount_nano_usd) / _NANO_USD_PER_USD
                )

    def _locked_snapshot(self) -> AgentBudgetSnapshot:
        # Caller must hold self._lock.
        return AgentBudgetSnapshot(
            limit_total_tokens=self.max_total_tokens,
            limit_model_calls=self.max_model_calls,
            limit_cost_usd=(
                format(self.max_cost_usd, "f")
                if self.max_cost_usd is not None
                else None
            ),
            used_total_tokens=self._used_total_tokens,
            reserved_total_tokens=self._reserved_total_tokens,
            used_model_calls=self._used_model_calls,
            used_cost_usd=format(self._used_cost_usd, "f"),
                )


def record_budgeted_model_call(
    agent_budget: AgentTurnBudget | None,
    *,
    model: Any,
    usage: Mapping[str, object],
    reservation: AgentBudgetReservation | None = None,
    cost_payload: Mapping[str, object] | None = None,
) -> None:
    """Settle tokens and a conservative cost estimate for one provider call.

    Canonical priced events remain the primary monetary authority. Providers
    outside that small audited catalog still need an enforceable turn ceiling,
    so the existing advisory cost catalog supplies a conservative fallback.
    """
    if agent_budget is None:
        return
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    cache_read_tokens = usage.get("cacheReadTokens")
    cache_creation_tokens = usage.get("cacheCreationTokens")
    safe_input = input_tokens if isinstance(input_tokens, int) else 0
    safe_output = output_tokens if isinstance(output_tokens, int) else 0
    safe_cache_read = cache_read_tokens if isinstance(cache_read_tokens, int) else 0
    safe_cache_creation = (
        cache_creation_tokens if isinstance(cache_creation_tokens, int) else 0
    )
    cost_usd: Decimal | float | None = None
    if (
        isinstance(cost_payload, Mapping)
        and cost_payload.get("status") == "priced"
        and isinstance(cost_payload.get("amountNanoUsd"), int)
    ):
        cost_usd = Decimal(cost_payload["amountNanoUsd"]) / _NANO_USD_PER_USD
    else:
        model_name = model if isinstance(model, str) else getattr(model, "model_id", None)
        if not isinstance(model_name, str) or not model_name:
            runtime = getattr(model, "runtime", None)
            model_name = runtime.get("model") if isinstance(runtime, Mapping) else ""
        from minicode.cost_tracker import calculate_cost

        cost_usd = calculate_cost(
            str(model_name or "default"),
            input_tokens=safe_input,
            output_tokens=safe_output,
            cache_read_tokens=safe_cache_read,
            cache_creation_tokens=safe_cache_creation,
        )
    agent_budget.record_model_call(
        input_tokens=safe_input,
        output_tokens=safe_output,
        cost_usd=cost_usd,
        reservation=reservation,
    )


__all__ = [
    "AgentBudgetExceeded",
    "AgentBudgetReservation",
    "AgentBudgetSnapshot",
    "AgentTurnBudget",
    "DEFAULT_MAX_COST_USD",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_TOTAL_TOKENS",
    "record_budgeted_model_call",
]
