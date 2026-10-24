"""Shared turn-level budget for the top-level Agent and its sub-agents.

A single :class:`AgentTurnBudget` object is created for one user Turn and is
passed through ``ToolContext`` into every nested ``task`` sub-agent. Parent and
children reserve and record against the same thread-safe counters, so parallel
read-only sub-agents cannot each spend as if they were the only consumer.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, DecimalException
from typing import Any

_NANO_USD_PER_USD = Decimal(1_000_000_000)
_LEASE_NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
# Legacy finite subprocess-lease defaults. These are not implicit cumulative
# limits on a user Turn: from_runtime leaves unconfigured dimensions unlimited.
# Keep them available to callers explicitly constructing a finite lease.
DEFAULT_MAX_TOTAL_TOKENS = 3_000_000
DEFAULT_MAX_MODEL_CALLS = 150
DEFAULT_MAX_COST_USD = Decimal("5.00")


def _optional_int(value: object, *, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"\+?[0-9]+", value.strip()):
        parsed = int(value)
    elif type(value) is int:
        parsed = value
    else:
        raise ValueError(f"{name} must be a non-negative integer or None")
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer or None")
    return parsed or None


def _optional_decimal(value: object, *, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative amount or None")
    try:
        parsed = Decimal(str(value))
    except (DecimalException, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite non-negative amount or None") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative amount or None")
    return parsed or None


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
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    usage_uncertain: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "limitTotalTokens": self.limit_total_tokens,
            "limitModelCalls": self.limit_model_calls,
            "limitCostUsd": self.limit_cost_usd,
            "usedTotalTokens": self.used_total_tokens,
            "reservedTotalTokens": self.reserved_total_tokens,
            "usedModelCalls": self.used_model_calls,
            "usedCostUsd": self.used_cost_usd,
            "usedInputTokens": self.used_input_tokens,
            "usedOutputTokens": self.used_output_tokens,
            "usageUncertain": self.usage_uncertain,
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


def _strict_protocol_int(
    value: object,
    *,
    name: str,
    minimum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _strict_protocol_decimal(
    value: object,
    *,
    name: str,
    allow_zero: bool,
) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is invalid")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise ValueError(f"{name} is invalid") from exc
    if (
        not parsed.is_finite()
        or parsed < 0
        or (parsed == 0 and parsed.is_signed())
        or (not allow_zero and parsed == 0)
        or format(parsed, "f") != value
    ):
        raise ValueError(f"{name} is invalid")
    return parsed


def _strict_lease_nonce(value: object) -> str:
    if not isinstance(value, str) or _LEASE_NONCE_RE.fullmatch(value) is None:
        raise ValueError("leaseNonce is invalid")
    return value


@dataclass(frozen=True, slots=True)
class SubprocessBudgetLease:
    """Parent-owned allowance; null dimensions have no cumulative ceiling."""

    lease_id: int
    lease_nonce: str
    max_total_tokens: int | None
    max_model_calls: int | None
    max_cost_usd: Decimal | None

    VERSION = 2
    _FIELDS = frozenset(
        {
            "budgetLeaseVersion",
            "leaseId",
            "leaseNonce",
            "maxTotalTokens",
            "maxModelCalls",
            "maxCostUsd",
        }
    )

    def __post_init__(self) -> None:
        _strict_protocol_int(self.lease_id, name="lease_id", minimum=1)
        if (
            not isinstance(self.lease_nonce, str)
            or _LEASE_NONCE_RE.fullmatch(self.lease_nonce) is None
        ):
            raise ValueError("lease_nonce is invalid")
        if self.max_total_tokens is not None:
            _strict_protocol_int(self.max_total_tokens, name="max_total_tokens", minimum=1)
        if self.max_model_calls is not None:
            _strict_protocol_int(self.max_model_calls, name="max_model_calls", minimum=1)
        if self.max_cost_usd is not None and (
            not isinstance(self.max_cost_usd, Decimal)
            or not self.max_cost_usd.is_finite()
            or self.max_cost_usd <= 0
        ):
            raise ValueError("max_cost_usd is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "budgetLeaseVersion": self.VERSION,
            "leaseId": self.lease_id,
            "leaseNonce": self.lease_nonce,
            "maxTotalTokens": self.max_total_tokens,
            "maxModelCalls": self.max_model_calls,
            "maxCostUsd": format(self.max_cost_usd, "f") if self.max_cost_usd is not None else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> SubprocessBudgetLease:
        if not isinstance(value, Mapping) or set(value) != cls._FIELDS:
            raise ValueError("subprocess budget lease fields are invalid")
        version = _strict_protocol_int(
            value.get("budgetLeaseVersion"),
            name="budgetLeaseVersion",
            minimum=1,
        )
        if version != cls.VERSION:
            raise ValueError("subprocess budget lease version is unsupported")
        return cls(
            lease_id=_strict_protocol_int(
                value.get("leaseId"), name="leaseId", minimum=1
            ),
            lease_nonce=_strict_lease_nonce(value.get("leaseNonce")),
            max_total_tokens=None if value["maxTotalTokens"] is None else _strict_protocol_int(
                value.get("maxTotalTokens"),
                name="maxTotalTokens",
                minimum=1,
            ),
            max_model_calls=None if value["maxModelCalls"] is None else _strict_protocol_int(
                value.get("maxModelCalls"),
                name="maxModelCalls",
                minimum=1,
            ),
            max_cost_usd=None if value["maxCostUsd"] is None else _strict_protocol_decimal(
                value.get("maxCostUsd"),
                name="maxCostUsd",
                allow_zero=False,
            ),
        )

    def accepts(self, usage: SubprocessBudgetUsage | None) -> bool:
        return (
            isinstance(usage, SubprocessBudgetUsage)
            and usage.lease_id == self.lease_id
            and secrets.compare_digest(usage.lease_nonce, self.lease_nonce)
            and usage.used_model_calls >= 0
            and (self.max_model_calls is None or usage.used_model_calls <= self.max_model_calls)
            and usage.input_tokens >= 0
            and usage.output_tokens >= 0
            and (self.max_total_tokens is None
                 or usage.input_tokens + usage.output_tokens <= self.max_total_tokens)
            and usage.used_cost_usd.is_finite()
            and usage.used_cost_usd >= 0
            and (self.max_cost_usd is None or usage.used_cost_usd <= self.max_cost_usd)
        )


@dataclass(frozen=True, slots=True)
class SubprocessBudgetUsage:
    """Closed, content-free child accounting returned for lease settlement."""

    lease_id: int
    lease_nonce: str
    used_model_calls: int
    input_tokens: int
    output_tokens: int
    used_cost_usd: Decimal

    VERSION = 2
    _FIELDS = frozenset(
        {
            "budgetUsageVersion",
            "leaseId",
            "leaseNonce",
            "usedModelCalls",
            "inputTokens",
            "outputTokens",
            "usedCostUsd",
        }
    )

    def __post_init__(self) -> None:
        _strict_protocol_int(self.lease_id, name="lease_id", minimum=1)
        if (
            not isinstance(self.lease_nonce, str)
            or _LEASE_NONCE_RE.fullmatch(self.lease_nonce) is None
        ):
            raise ValueError("lease_nonce is invalid")
        _strict_protocol_int(
            self.used_model_calls,
            name="used_model_calls",
            minimum=0,
        )
        _strict_protocol_int(self.input_tokens, name="input_tokens", minimum=0)
        _strict_protocol_int(self.output_tokens, name="output_tokens", minimum=0)
        if (
            not isinstance(self.used_cost_usd, Decimal)
            or not self.used_cost_usd.is_finite()
            or self.used_cost_usd < 0
            or (self.used_cost_usd == 0 and self.used_cost_usd.is_signed())
        ):
            raise ValueError("used_cost_usd is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "budgetUsageVersion": self.VERSION,
            "leaseId": self.lease_id,
            "leaseNonce": self.lease_nonce,
            "usedModelCalls": self.used_model_calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "usedCostUsd": format(self.used_cost_usd, "f"),
        }

    @classmethod
    def from_dict(cls, value: object) -> SubprocessBudgetUsage:
        if not isinstance(value, Mapping) or set(value) != cls._FIELDS:
            raise ValueError("subprocess budget usage fields are invalid")
        version = _strict_protocol_int(
            value.get("budgetUsageVersion"),
            name="budgetUsageVersion",
            minimum=1,
        )
        if version != cls.VERSION:
            raise ValueError("subprocess budget usage version is unsupported")
        return cls(
            lease_id=_strict_protocol_int(
                value.get("leaseId"), name="leaseId", minimum=1
            ),
            lease_nonce=_strict_lease_nonce(value.get("leaseNonce")),
            used_model_calls=_strict_protocol_int(
                value.get("usedModelCalls"),
                name="usedModelCalls",
                minimum=0,
            ),
            input_tokens=_strict_protocol_int(
                value.get("inputTokens"), name="inputTokens", minimum=0
            ),
            output_tokens=_strict_protocol_int(
                value.get("outputTokens"), name="outputTokens", minimum=0
            ),
            used_cost_usd=_strict_protocol_decimal(
                value.get("usedCostUsd"),
                name="usedCostUsd",
                allow_zero=True,
            ),
        )

    @classmethod
    def from_snapshot(
        cls,
        lease: SubprocessBudgetLease,
        snapshot: AgentBudgetSnapshot,
    ) -> SubprocessBudgetUsage:
        if snapshot.reserved_total_tokens != 0:
            raise ValueError("subprocess budget still has an active reservation")
        if snapshot.usage_uncertain:
            raise ValueError("subprocess budget has uncertain provider usage")
        return cls(
            lease_id=lease.lease_id,
            lease_nonce=lease.lease_nonce,
            used_model_calls=snapshot.used_model_calls,
            input_tokens=snapshot.used_input_tokens,
            output_tokens=snapshot.used_output_tokens,
            used_cost_usd=Decimal(snapshot.used_cost_usd),
        )


class AgentTurnBudget:
    """Shared usage ledger with optional token/model-call/cost ceilings.

    Unset, None, or zero limits are unlimited; accounting and reservations
    remain active. Only explicit positive limits restrict cumulative usage.
    """

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
        self._used_input_tokens = 0
        self._used_output_tokens = 0
        self._used_model_calls = 0
        self._used_cost_usd = Decimal("0.0")
        self._reserved_total_tokens = 0
        self._reserved_model_calls = 0
        self._reserved_cost_usd = Decimal("0.0")
        self._next_reservation_id = 1
        self._reservations: dict[int, int] = {}
        self._has_uncertain_model_usage = False
        self._next_subprocess_lease_id = 1
        self._subprocess_leases: dict[int, SubprocessBudgetLease] = {}

    @classmethod
    def from_runtime(cls, runtime: Mapping[str, Any] | None) -> AgentTurnBudget:
        values = runtime or {}
        settings = values.get("agentTurnBudget")
        if not isinstance(settings, Mapping):
            settings = {}
        env = os.environ

        def configured(env_name: str, flat_name: str, nested_name: str) -> object:
            # Presence, not truthiness: explicit 0/None cancels a lower-priority
            # setting. An empty environment variable retains the usual unset
            # convention, while malformed non-empty values fail validation.
            if env.get(env_name) not in (None, ""):
                return env[env_name]
            if flat_name in values:
                return values[flat_name]
            return settings.get(nested_name)

        return cls(
            max_total_tokens=configured(
                "MINI_CODE_TURN_BUDGET_TOKENS", "agentTurnBudgetTokens", "maxTokens",
            ),
            max_model_calls=configured(
                "MINI_CODE_TURN_BUDGET_MODEL_CALLS", "agentTurnBudgetModelCalls", "maxModelCalls",
            ),
            max_cost_usd=configured(
                "MINI_CODE_TURN_BUDGET_COST_USD", "agentTurnBudgetCostUsd", "maxCostUsd",
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
                used_input_tokens=self._used_input_tokens,
                used_output_tokens=self._used_output_tokens,
                usage_uncertain=self._has_uncertain_model_usage,
            )

    @property
    def reserved_model_calls(self) -> int:
        """Host-only live subprocess occupancy; never a new reservation."""
        with self._lock:
            return self._reserved_model_calls

    def reserve_model_call(
        self,
        estimated_input_tokens: int = 0,
        *,
        preserve_model_calls: int = 0,
        preserve_total_tokens: int = 0,
    ) -> AgentBudgetReservation:
        """Admit one model call against the shared budget, or raise.

        The estimate is a guard against sending an already-oversized context;
        actual token usage is recorded after the provider responds. Optional
        ``preserve_model_calls`` atomically leaves that many calls available
        for higher-priority work after this reservation, avoiding a check/use
        race between parallel advisory classifiers.
        ``preserve_total_tokens`` keeps estimated space free at this admission
        boundary for the response and later requests. It is not a separate
        token grant or a guarantee about provider-reported usage.
        """
        estimate = max(0, int(estimated_input_tokens or 0))
        if type(preserve_total_tokens) is not int or preserve_total_tokens < 0:
            raise ValueError("preserve_total_tokens must be a non-negative integer")
        if (
            isinstance(preserve_model_calls, bool)
            or not isinstance(preserve_model_calls, int)
            or preserve_model_calls < 0
        ):
            raise ValueError("preserve_model_calls must be a non-negative integer")
        with self._lock:
            if (
                self.max_model_calls is not None
                and self._used_model_calls
                + self._reserved_model_calls
                + 1
                + preserve_model_calls
                > self.max_model_calls
            ):
                raise AgentBudgetExceeded(
                    "model call budget exhausted", self._locked_snapshot()
                )
            if (
                self.max_cost_usd is not None
                and self._used_cost_usd + self._reserved_cost_usd
                >= self.max_cost_usd
            ):
                raise AgentBudgetExceeded(
                    "cost budget exhausted", self._locked_snapshot()
                )
            if (
                self.max_total_tokens is not None
                and self._used_total_tokens + self._reserved_total_tokens + estimate + preserve_total_tokens
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

    def reserve_subprocess_lease(
        self,
        *,
        requested_total_tokens: int | None,
        requested_model_calls: int | None,
        requested_cost_usd: Decimal | str | None,
        preserve_model_calls: int = 1,
    ) -> SubprocessBudgetLease:
        """Atomically reserve a child allowance, bounded by finite parents.

        Independent processes cannot share this object's lock. The parent
        therefore reserves every finite dimension before spawning a worker.
        A null request takes the finite parent's remaining allowance, or stays
        null under an unlimited parent. It never creates a larger allowance.
        """
        requested_tokens = None if requested_total_tokens is None else _strict_protocol_int(
            requested_total_tokens,
            name="requested_total_tokens",
            minimum=1,
        )
        requested_calls = None if requested_model_calls is None else _strict_protocol_int(
            requested_model_calls,
            name="requested_model_calls",
            minimum=1,
        )
        requested_cost = _optional_decimal(
            requested_cost_usd,
            name="requested_cost_usd",
        )
        if requested_cost is None and requested_cost_usd is not None:
            raise ValueError("requested_cost_usd is invalid")
        if (
            isinstance(preserve_model_calls, bool)
            or not isinstance(preserve_model_calls, int)
            or preserve_model_calls < 0
        ):
            raise ValueError("preserve_model_calls must be a non-negative integer")

        with self._lock:
            available_calls = requested_calls
            if self.max_model_calls is not None:
                parent_calls = (self.max_model_calls
                    - self._used_model_calls
                    - self._reserved_model_calls
                    - preserve_model_calls)
                available_calls = parent_calls if available_calls is None else min(available_calls, parent_calls)
            available_tokens = requested_tokens
            if self.max_total_tokens is not None:
                parent_tokens = (self.max_total_tokens
                    - self._used_total_tokens
                    - self._reserved_total_tokens)
                available_tokens = parent_tokens if available_tokens is None else min(available_tokens, parent_tokens)
            available_cost = requested_cost
            if self.max_cost_usd is not None:
                parent_cost = (self.max_cost_usd
                    - self._used_cost_usd
                    - self._reserved_cost_usd)
                available_cost = parent_cost if available_cost is None else min(available_cost, parent_cost)

            if available_calls is not None and available_calls < 1:
                raise AgentBudgetExceeded(
                    "model call budget cannot admit a subprocess lease",
                    self._locked_snapshot(),
                )
            if available_tokens is not None and available_tokens < 1:
                raise AgentBudgetExceeded(
                    "token budget cannot admit a subprocess lease",
                    self._locked_snapshot(),
                )
            if available_cost is not None and available_cost <= 0:
                raise AgentBudgetExceeded(
                    "cost budget cannot admit a subprocess lease",
                    self._locked_snapshot(),
                )

            lease = SubprocessBudgetLease(
                lease_id=self._next_subprocess_lease_id,
                lease_nonce=secrets.token_hex(32),
                max_total_tokens=available_tokens,
                max_model_calls=available_calls,
                max_cost_usd=available_cost,
            )
            self._next_subprocess_lease_id += 1
            self._subprocess_leases[lease.lease_id] = lease
            self._reserved_total_tokens += lease.max_total_tokens or 0
            self._reserved_model_calls += lease.max_model_calls or 0
            self._reserved_cost_usd += lease.max_cost_usd or Decimal(0)
            return lease

    def settle_subprocess_lease(
        self,
        lease: SubprocessBudgetLease,
        usage: SubprocessBudgetUsage | None,
    ) -> SubprocessBudgetUsage | None:
        """Settle once; missing usage charges finite dimensions conservatively.

        Returning ``None`` means this exact lease was not active (normally an
        idempotent repeated settlement). A mismatched object can never settle
        another reservation merely by presenting its numeric lease ID.
        Unlimited dimensions have no fictitious chargeable maximum. Missing
        or invalid receipts mark the shared accounting explicitly uncertain;
        zero in such a dimension is a known lower bound, never exact usage.
        """
        if not isinstance(lease, SubprocessBudgetLease):
            return None
        with self._lock:
            active = self._subprocess_leases.get(lease.lease_id)
            if active is None or active != lease:
                return None
            self._subprocess_leases.pop(lease.lease_id)
            self._reserved_total_tokens = max(
                0,
                self._reserved_total_tokens - (lease.max_total_tokens or 0),
            )
            self._reserved_model_calls = max(
                0,
                self._reserved_model_calls - (lease.max_model_calls or 0),
            )
            self._reserved_cost_usd = max(
                Decimal("0.0"),
                self._reserved_cost_usd - (lease.max_cost_usd or Decimal(0)),
            )

            accepted = lease.accepts(usage)
            if not accepted:
                self._has_uncertain_model_usage = True
            charged = usage if accepted else SubprocessBudgetUsage(
                lease_id=lease.lease_id,
                lease_nonce=lease.lease_nonce,
                used_model_calls=lease.max_model_calls or 0,
                input_tokens=lease.max_total_tokens or 0,
                output_tokens=0,
                used_cost_usd=lease.max_cost_usd or Decimal(0),
            )
            self._used_model_calls += charged.used_model_calls
            self._used_input_tokens += charged.input_tokens
            self._used_output_tokens += charged.output_tokens
            self._used_total_tokens += charged.input_tokens + charged.output_tokens
            self._used_cost_usd += charged.used_cost_usd
            return charged

    def subprocess_usage(
        self,
        lease: SubprocessBudgetLease,
    ) -> SubprocessBudgetUsage:
        """Create a receipt only when every child model call is fully settled.

        A pending reservation means accounting failed before its usage landed;
        an estimated failure means the request may have reached the provider but
        no authoritative cost receipt came back. Either state forces the parent
        to charge the full lease instead of accepting a deceptively exact child
        receipt.
        """
        if not isinstance(lease, SubprocessBudgetLease):
            raise TypeError("subprocess budget lease is invalid")
        with self._lock:
            if self._reservations or self._subprocess_leases:
                raise ValueError("subprocess budget has an active reservation")
            if self._has_uncertain_model_usage:
                raise ValueError("subprocess budget has uncertain provider usage")
            snapshot = self._locked_snapshot()
        return SubprocessBudgetUsage.from_snapshot(lease, snapshot)

    def record_model_call(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal | str | float | None = None,
        reservation: AgentBudgetReservation | None = None,
        settlement_authoritative: bool = True,
    ) -> None:
        """Record usage from one completed model response.

        ``settlement_authoritative`` is false when either token usage or cost
        had to be estimated. Such usage remains useful for local admission and
        telemetry, but it cannot produce an exact subprocess receipt: the
        parent must conservatively charge the full finite lease instead.
        """
        if not isinstance(settlement_authoritative, bool):
            raise TypeError("settlement_authoritative must be a bool")
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
            self._used_input_tokens += safe_input
            self._used_output_tokens += safe_output
            if not settlement_authoritative:
                self._has_uncertain_model_usage = True
            if cost_usd is not None:
                try:
                    parsed_cost = Decimal(str(cost_usd))
                except (DecimalException, TypeError, ValueError):
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
            # Admission happens immediately before the provider call. Once an
            # admitted call fails without an authoritative usage receipt, the
            # child cannot prove its exact token/cost consumption — even when a
            # caller elects not to charge the local input estimate.
            self._has_uncertain_model_usage = True
            if charge_estimate:
                self._used_total_tokens += reserved
                self._used_input_tokens += reserved
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
            used_input_tokens=self._used_input_tokens,
            used_output_tokens=self._used_output_tokens,
            usage_uncertain=self._has_uncertain_model_usage,
        )

    def attribution_view(self, key: str) -> AttributedBudgetView:
        """Return a per-consumer view that tags this budget's accounting.

        Parallel sub-agents share one budget object, so a global
        start/finish diff cannot attribute token deltas to one consumer.
        Routing every model call through a per-sub-agent view tags the
        delta at the accounting point instead, which stays correct under
        parallel interleaving.
        """
        return AttributedBudgetView(self, key)


class AttributedBudgetView:
    """Per-consumer facade over an :class:`AgentTurnBudget` (or another view).

    The view delegates every budget operation unchanged — limits,
    reservations and cost accounting stay centralized — while keeping its
    own input/output token counters for everything recorded through it.
    Nested views (e.g. a workflow wrapping its phase sub-agents) therefore
    report subtree totals, and a leaf view reports exactly its own calls.
    """

    def __init__(self, budget: AgentTurnBudget | AttributedBudgetView, key: str) -> None:
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("attribution key is invalid")
        self._budget = budget
        self._key = key
        self._lock = threading.Lock()
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def attribution_key(self) -> str:
        return self._key

    @property
    def max_total_tokens(self) -> int | None:
        return self._budget.max_total_tokens

    @property
    def max_model_calls(self) -> int | None:
        return self._budget.max_model_calls

    @property
    def max_cost_usd(self) -> Decimal | None:
        return self._budget.max_cost_usd

    @property
    def has_limits(self) -> bool:
        return self._budget.has_limits

    def snapshot(self) -> AgentBudgetSnapshot:
        return self._budget.snapshot()

    @property
    def reserved_model_calls(self) -> int:
        return self._budget.reserved_model_calls

    def reserve_model_call(
        self,
        estimated_input_tokens: int = 0,
        *,
        preserve_model_calls: int = 0,
        preserve_total_tokens: int = 0,
    ) -> AgentBudgetReservation:
        return self._budget.reserve_model_call(
            estimated_input_tokens,
            preserve_model_calls=preserve_model_calls,
            **({"preserve_total_tokens": preserve_total_tokens} if preserve_total_tokens else {}),
        )

    def reserve_subprocess_lease(
        self,
        *,
        requested_total_tokens: int | None,
        requested_model_calls: int | None,
        requested_cost_usd: Decimal | str | None,
        preserve_model_calls: int = 1,
    ) -> SubprocessBudgetLease:
        return self._budget.reserve_subprocess_lease(
            requested_total_tokens=requested_total_tokens,
            requested_model_calls=requested_model_calls,
            requested_cost_usd=requested_cost_usd,
            preserve_model_calls=preserve_model_calls,
        )

    def settle_subprocess_lease(
        self,
        lease: SubprocessBudgetLease,
        usage: SubprocessBudgetUsage | None,
    ) -> SubprocessBudgetUsage | None:
        charged = self._budget.settle_subprocess_lease(lease, usage)
        if charged is not None:
            with self._lock:
                self._input_tokens += charged.input_tokens
                self._output_tokens += charged.output_tokens
        return charged

    def record_model_call(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal | str | float | None = None,
        reservation: AgentBudgetReservation | None = None,
        settlement_authoritative: bool = True,
    ) -> None:
        safe_input = max(0, int(input_tokens or 0))
        safe_output = max(0, int(output_tokens or 0))
        with self._lock:
            self._input_tokens += safe_input
            self._output_tokens += safe_output
        self._budget.record_model_call(
            input_tokens=safe_input,
            output_tokens=safe_output,
            cost_usd=cost_usd,
            reservation=reservation,
            settlement_authoritative=settlement_authoritative,
        )

    def fail_model_call(
        self,
        reservation: AgentBudgetReservation | None,
        *,
        charge_estimate: bool = False,
    ) -> bool:
        return self._budget.fail_model_call(
            reservation, charge_estimate=charge_estimate
        )

    def record_cost_nano_usd(self, amount_nano_usd: int | None) -> None:
        self._budget.record_cost_nano_usd(amount_nano_usd)

    def attribution_view(self, key: str) -> AttributedBudgetView:
        """Nest one more attribution level (e.g. a phase inside a workflow)."""
        return AttributedBudgetView(self, key)

    def attributed_tokens(self) -> tuple[int, int]:
        """Cumulative (input, output) tokens recorded through this view."""
        with self._lock:
            return self._input_tokens, self._output_tokens


class LimitedBudgetView(AttributedBudgetView):
    """Bound one Assignment on the existing ledger, including retry/closing calls.

    This is not a new grant: every admission still uses the shared atomic
    reservation. The floor prevents this Worker from consuming required later
    work; other concurrent consumers can still exhaust the shared budget, in
    which case this Worker fails closed rather than borrowing their allowance.
    """

    def __init__(self, budget, key: str, *, max_calls: int | None, preserve_calls: int,
                 max_tokens: int | None = None, preserve_tokens: int = 0):
        super().__init__(budget, key)
        self._max_calls = max_calls
        self._preserve_calls = preserve_calls
        self._admitted_calls = 0
        self._max_tokens = max_tokens
        self._preserve_tokens = preserve_tokens
        initial = super().snapshot()
        self._initial_used_tokens = initial.used_total_tokens
        self._initial_used_calls = initial.used_model_calls
        self._call_lock = threading.Lock()

    def snapshot(self):
        # The worker's planning view must exclude downstream floors that its
        # admission path protects. Keep usage counters and the shared budget
        # untouched; the same snapshot also crosses the existing worker RPC.
        shared = super().snapshot()
        tokens = shared.limit_total_tokens
        if tokens is not None:
            tokens = max(0, tokens - self._preserve_tokens)
        if self._max_tokens is not None:
            local = self._initial_used_tokens + self._max_tokens
            tokens = local if tokens is None else min(tokens, local)
        calls = shared.limit_model_calls
        if calls is not None:
            calls = max(0, calls - self._preserve_calls)
        if self._max_calls is not None:
            local_calls = shared.used_model_calls + max(0, self._max_calls - self._admitted_calls)
            calls = local_calls if calls is None else min(calls, local_calls)
        return replace(shared, limit_total_tokens=tokens, limit_model_calls=calls)

    @property
    def admitted_model_calls(self) -> int:
        with self._call_lock:
            return self._admitted_calls

    def reserve_model_call(
        self, estimated_input_tokens=0, *, preserve_model_calls=0,
        preserve_total_tokens=0,
    ):
        if type(preserve_model_calls) is not int or preserve_model_calls < 0:
            raise ValueError("preserve_model_calls must be a non-negative integer")
        with self._call_lock:
            snapshot = self.snapshot()
            if self._max_tokens is not None and (
                snapshot.used_total_tokens - self._initial_used_tokens
                + snapshot.reserved_total_tokens + max(0, estimated_input_tokens)
                + preserve_total_tokens > self._max_tokens
            ):
                raise AgentBudgetExceeded("worker token allowance exhausted", snapshot)
            if (self._max_calls is not None
                    and self._admitted_calls + 1 + preserve_model_calls > self._max_calls):
                raise AgentBudgetExceeded(
                    "worker model call budget exhausted", self.snapshot()
                )
            reservation = super().reserve_model_call(
                estimated_input_tokens,
                preserve_model_calls=self._preserve_calls + preserve_model_calls,
                preserve_total_tokens=self._preserve_tokens + preserve_total_tokens,
            )
            self._admitted_calls += 1
            return reservation


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

    def safe_token(value: object) -> int:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            else 0
        )

    safe_input = safe_token(input_tokens)
    safe_output = safe_token(output_tokens)
    safe_cache_read = safe_token(cache_read_tokens)
    safe_cache_creation = (
        safe_token(cache_creation_tokens)
    )
    cost_usd: Decimal | float | None = None
    authoritative_cost = (
        isinstance(cost_payload, Mapping)
        and cost_payload.get("status") == "priced"
        and cost_payload.get("quality") == "provider_usage_catalog_rate"
        and isinstance(cost_payload.get("amountNanoUsd"), int)
        and not isinstance(cost_payload.get("amountNanoUsd"), bool)
        and cost_payload["amountNanoUsd"] >= 0
    )
    if authoritative_cost:
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
        settlement_authoritative=(
            usage.get("source") == "provider"
            and isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
            and authoritative_cost
        ),
    )


__all__ = [
    "DEFAULT_MAX_COST_USD",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_TOTAL_TOKENS",
    "AgentBudgetExceeded",
    "AgentBudgetReservation",
    "AgentBudgetSnapshot",
    "AgentTurnBudget",
    "AttributedBudgetView",
    "LimitedBudgetView",
    "SubprocessBudgetLease",
    "SubprocessBudgetUsage",
    "record_budgeted_model_call",
]
