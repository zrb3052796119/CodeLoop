"""Versioned, deterministic model-cost observations.

This module is deliberately independent from the Dashboard, RunJournal, and
the legacy TUI cost tracker.  A catalog quote is an observation computed from
canonical usage and an immutable rate card; it is not a provider invoice.

Production rates were retrieved from the linked first-party model pages on
2026-07-17.  Rates are USD per one million text tokens.  OpenAI Chat
Completions reports ``prompt_tokens`` inclusive of ``cached_tokens``, so the
uncached input component is ``input - cache_read``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from types import MappingProxyType
from typing import Literal


CATALOG_ID = "minicode-pricing-2026-07-17-v1"
CATALOG_VERSION = 1
CURRENCY = "USD"
COST_VERSION = 1

_NANO_USD_PER_USD = Decimal("1000000000")
_TOKENS_PER_MILLION = Decimal("1000000")
_NANO_USD_PER_RATE_TOKEN = _NANO_USD_PER_USD / _TOKENS_PER_MILLION
_WHOLE_NANO_USD = Decimal("1")
_MAX_TOKEN_COUNT = 1_000_000_000
_MAX_AMOUNT_NANO_USD = 1_000_000_000_000_000_000
_SAFE_MODEL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_MODEL_OPERATION_ID_RE = re.compile(r"^modelop_[0-9a-f]{32}$")

BucketSemantics = Literal[
    "input_includes_cache_read",
    "input_excludes_cache_buckets",
]
QuoteStatus = Literal["priced", "unavailable"]
QuoteQuality = Literal[
    "provider_usage_catalog_rate",
    "estimated_usage_catalog_rate",
    "unavailable",
]
UnavailableReason = Literal[
    "usage_unavailable",
    "model_unpriced",
    "pricing_incomplete",
    "token_semantics_unsupported",
    "invalid_usage",
    "pricing_failed",
]

_COMPONENT_KEYS = (
    "inputNanoUsd",
    "outputNanoUsd",
    "cacheReadNanoUsd",
    "cacheCreationNanoUsd",
)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """One immutable model rate card entry."""

    catalog_model_key: str
    aliases: tuple[str, ...]
    input_usd_per_million: Decimal | None
    output_usd_per_million: Decimal | None
    cache_read_usd_per_million: Decimal | None
    cache_creation_usd_per_million: Decimal | None
    token_bucket_semantics: BucketSemantics
    not_applicable_buckets: frozenset[str]
    provider: str
    source_url: str
    retrieved_on: str
    rate_unit: str
    cache_billing_note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(
            self,
            "not_applicable_buckets",
            frozenset(self.not_applicable_buckets),
        )
        if not _SAFE_MODEL_KEY_RE.fullmatch(self.catalog_model_key):
            raise ValueError("catalog model key is not a safe canonical key")
        for alias in self.aliases:
            if not _SAFE_MODEL_KEY_RE.fullmatch(alias):
                raise ValueError("catalog alias is not an exact safe key")
        if "default" in {self.catalog_model_key, *self.aliases}:
            raise ValueError("default pricing is forbidden")
        if not self.not_applicable_buckets <= {
            "cache_read",
            "cache_creation",
        }:
            raise ValueError("unknown not-applicable token bucket")


@dataclass(frozen=True, slots=True)
class PricingCatalog:
    """Immutable versioned catalog with an exact-match index."""

    catalog_id: str
    catalog_version: int
    currency: str
    entries: tuple[ModelPrice, ...]
    _index: Mapping[str, ModelPrice] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        index: dict[str, ModelPrice] = {}
        for entry in entries:
            for key in (entry.catalog_model_key, *entry.aliases):
                if key in index:
                    raise ValueError("duplicate catalog model key or alias")
                index[key] = entry
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "_index", MappingProxyType(index))

    def exact_entry(self, key: str) -> ModelPrice | None:
        """Return an entry only for an exact, case-sensitive key or alias."""
        return self._index.get(key)


@dataclass(frozen=True, slots=True)
class CostQuote:
    """A safe canonical quote ready to become a ``model.costed`` payload."""

    status: QuoteStatus
    quality: QuoteQuality
    currency: str
    catalog_id: str
    catalog_version: int
    catalog_model_key: str | None = None
    amount_nano_usd: int | None = None
    components: Mapping[str, int] | None = None
    reason: UnavailableReason | None = None

    def __post_init__(self) -> None:
        if self.components is not None:
            object.__setattr__(
                self,
                "components",
                MappingProxyType(dict(self.components)),
            )

    def to_event_payload(self, operation_id: str) -> dict[str, object]:
        """Project the quote to the strict persisted event contract."""
        if not _MODEL_OPERATION_ID_RE.fullmatch(operation_id):
            raise ValueError("invalid model operation id")
        payload: dict[str, object] = {
            "costVersion": COST_VERSION,
            "operationId": operation_id,
            "status": self.status,
            "quality": self.quality,
            "currency": self.currency,
            "catalogId": self.catalog_id,
        }
        if self.status == "priced":
            payload.update(
                {
                    "catalogModelKey": self.catalog_model_key,
                    "amountNanoUsd": self.amount_nano_usd,
                    "components": dict(self.components or {}),
                }
            )
        else:
            payload["reason"] = self.reason or "pricing_failed"
        return payload


PRODUCTION_CATALOG = PricingCatalog(
    catalog_id=CATALOG_ID,
    catalog_version=CATALOG_VERSION,
    currency=CURRENCY,
    entries=(
        ModelPrice(
            catalog_model_key="openai/gpt-4o",
            aliases=("gpt-4o", "gpt-4o-2024-08-06"),
            input_usd_per_million=Decimal("2.50"),
            output_usd_per_million=Decimal("10.00"),
            cache_read_usd_per_million=Decimal("1.25"),
            cache_creation_usd_per_million=None,
            token_bucket_semantics="input_includes_cache_read",
            not_applicable_buckets=frozenset({"cache_creation"}),
            provider="OpenAI",
            source_url="https://developers.openai.com/api/docs/models/gpt-4o",
            retrieved_on="2026-07-17",
            rate_unit="USD per 1M text tokens",
            cache_billing_note=(
                "prompt_tokens includes cached_tokens; cache creation is not "
                "a separately billed response bucket"
            ),
        ),
        ModelPrice(
            catalog_model_key="openai/gpt-4o-mini",
            aliases=("gpt-4o-mini", "gpt-4o-mini-2024-07-18"),
            input_usd_per_million=Decimal("0.15"),
            output_usd_per_million=Decimal("0.60"),
            cache_read_usd_per_million=Decimal("0.075"),
            cache_creation_usd_per_million=None,
            token_bucket_semantics="input_includes_cache_read",
            not_applicable_buckets=frozenset({"cache_creation"}),
            provider="OpenAI",
            source_url=(
                "https://developers.openai.com/api/docs/models/gpt-4o-mini"
            ),
            retrieved_on="2026-07-17",
            rate_unit="USD per 1M text tokens",
            cache_billing_note=(
                "prompt_tokens includes cached_tokens; cache creation is not "
                "a separately billed response bucket"
            ),
        ),
    ),
)


def _unavailable(
    catalog: PricingCatalog,
    reason: UnavailableReason,
) -> CostQuote:
    return CostQuote(
        status="unavailable",
        quality="unavailable",
        currency=catalog.currency,
        catalog_id=catalog.catalog_id,
        catalog_version=catalog.catalog_version,
        reason=reason,
    )


def _actual_model_identity(model: object) -> str | None:
    if isinstance(model, str):
        return model

    explicit = getattr(model, "catalog_model_key", None)
    if isinstance(explicit, str):
        return explicit

    model_type = type(model)
    if not (
        model_type.__module__ == "minicode.openai_adapter"
        and model_type.__name__ == "OpenAIModelAdapter"
    ):
        return None

    runtime = getattr(model, "runtime", None)
    if not isinstance(runtime, Mapping):
        return None
    if runtime.get("_openrouter_headers") or runtime.get("_openrouter_params"):
        return None
    base_url = (
        os.environ.get("OPENAI_BASE_URL", "")
        or os.environ.get("OPENAI_API_BASE", "")
        or runtime.get("openaiBaseUrl", "")
        or "https://api.openai.com"
    )
    if not isinstance(base_url, str):
        return None
    if base_url.rstrip("/") != "https://api.openai.com":
        return None
    runtime_model = runtime.get("model")
    return runtime_model if isinstance(runtime_model, str) else None


def _usage_value(usage: object, camel_name: str, snake_name: str) -> object:
    if isinstance(usage, Mapping):
        return usage.get(camel_name)
    return getattr(usage, snake_name, None)


def _token_value(value: object, *, optional: bool) -> int | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > _MAX_TOKEN_COUNT
    ):
        raise ValueError("invalid token bucket")
    return value


def _component_nano_usd(tokens: int, rate: Decimal) -> int:
    if not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0:
        raise ArithmeticError("invalid catalog rate")
    with localcontext() as context:
        context.prec = 50
        amount = (
            Decimal(tokens) * rate * _NANO_USD_PER_RATE_TOKEN
        ).quantize(_WHOLE_NANO_USD, rounding=ROUND_HALF_EVEN)
    value = int(amount)
    if value < 0 or value > _MAX_AMOUNT_NANO_USD:
        raise OverflowError("cost component outside canonical range")
    return value


def _priced_bucket(
    *,
    token_value: int | None,
    rate: Decimal | None,
    bucket_name: str,
    not_applicable: frozenset[str],
) -> tuple[int, Decimal] | UnavailableReason:
    if bucket_name in not_applicable:
        if token_value in {None, 0}:
            return 0, Decimal("0")
        return "token_semantics_unsupported"
    if rate is None:
        if token_value == 0:
            return 0, Decimal("0")
        return "pricing_incomplete"
    if token_value is None:
        return "pricing_incomplete"
    return token_value, rate


def _quote_model_cost(
    *,
    model: object,
    usage: object,
    catalog: PricingCatalog,
) -> CostQuote:
    identity = _actual_model_identity(model)
    entry = catalog.exact_entry(identity) if isinstance(identity, str) else None
    if entry is None:
        return _unavailable(catalog, "model_unpriced")

    source = _usage_value(usage, "source", "source")
    if source == "unavailable" or usage is None:
        return _unavailable(catalog, "usage_unavailable")
    if source not in {"provider", "estimated"}:
        return _unavailable(catalog, "invalid_usage")

    raw_input_tokens = _usage_value(usage, "inputTokens", "input_tokens")
    raw_output_tokens = _usage_value(usage, "outputTokens", "output_tokens")
    if raw_input_tokens is None or raw_output_tokens is None:
        return _unavailable(catalog, "usage_unavailable")
    try:
        input_tokens = _token_value(raw_input_tokens, optional=False)
        output_tokens = _token_value(raw_output_tokens, optional=False)
        cache_read_tokens = _token_value(
            _usage_value(usage, "cacheReadTokens", "cache_read_tokens"),
            optional=True,
        )
        cache_creation_tokens = _token_value(
            _usage_value(usage, "cacheCreationTokens", "cache_creation_tokens"),
            optional=True,
        )
    except ValueError:
        return _unavailable(catalog, "invalid_usage")
    if input_tokens is None or output_tokens is None:
        return _unavailable(catalog, "usage_unavailable")
    if entry.input_usd_per_million is None or entry.output_usd_per_million is None:
        return _unavailable(catalog, "pricing_incomplete")

    cache_read = _priced_bucket(
        token_value=cache_read_tokens,
        rate=entry.cache_read_usd_per_million,
        bucket_name="cache_read",
        not_applicable=entry.not_applicable_buckets,
    )
    cache_creation = _priced_bucket(
        token_value=cache_creation_tokens,
        rate=entry.cache_creation_usd_per_million,
        bucket_name="cache_creation",
        not_applicable=entry.not_applicable_buckets,
    )
    for bucket in (cache_read, cache_creation):
        if isinstance(bucket, str):
            return _unavailable(catalog, bucket)

    cache_read_count, cache_read_rate = cache_read
    cache_creation_count, cache_creation_rate = cache_creation
    if entry.token_bucket_semantics == "input_includes_cache_read":
        if cache_read_count > input_tokens:
            return _unavailable(catalog, "invalid_usage")
        uncached_input_tokens = input_tokens - cache_read_count
    elif entry.token_bucket_semantics == "input_excludes_cache_buckets":
        uncached_input_tokens = input_tokens
    else:
        return _unavailable(catalog, "token_semantics_unsupported")

    components = {
        "inputNanoUsd": _component_nano_usd(
            uncached_input_tokens, entry.input_usd_per_million
        ),
        "outputNanoUsd": _component_nano_usd(
            output_tokens, entry.output_usd_per_million
        ),
        "cacheReadNanoUsd": _component_nano_usd(
            cache_read_count, cache_read_rate
        ),
        "cacheCreationNanoUsd": _component_nano_usd(
            cache_creation_count, cache_creation_rate
        ),
    }
    if tuple(components) != _COMPONENT_KEYS:
        raise AssertionError("canonical component schema changed")
    total = sum(components.values())
    if total > _MAX_AMOUNT_NANO_USD:
        raise OverflowError("canonical total outside supported range")
    return CostQuote(
        status="priced",
        quality=(
            "provider_usage_catalog_rate"
            if source == "provider"
            else "estimated_usage_catalog_rate"
        ),
        currency=catalog.currency,
        catalog_id=catalog.catalog_id,
        catalog_version=catalog.catalog_version,
        catalog_model_key=entry.catalog_model_key,
        amount_nano_usd=total,
        components=components,
    )


def quote_model_cost(
    *,
    model: object,
    usage: object,
    catalog: PricingCatalog = PRODUCTION_CATALOG,
) -> CostQuote:
    """Quote one completed model call without leaking unresolved identity.

    All resolution, malformed-object, and arithmetic failures are converted to
    a fixed low-cardinality unavailable result.  Callers may therefore treat
    pricing as best-effort observation only.
    """
    try:
        return _quote_model_cost(model=model, usage=usage, catalog=catalog)
    except BaseException:  # noqa: BLE001 - hostile observation must be isolated
        try:
            return _unavailable(catalog, "pricing_failed")
        except BaseException:  # noqa: BLE001 - even a hostile catalog is isolated
            return CostQuote(
                status="unavailable",
                quality="unavailable",
                currency=CURRENCY,
                catalog_id=CATALOG_ID,
                catalog_version=CATALOG_VERSION,
                reason="pricing_failed",
            )


def pricing_failure_event_payload(operation_id: str) -> dict[str, object]:
    """Return the fixed fail-closed event when observation itself breaks."""
    return _unavailable(PRODUCTION_CATALOG, "pricing_failed").to_event_payload(
        operation_id
    )


def project_model_cost_event(
    *,
    model: object,
    usage: object,
    operation_id: str,
) -> dict[str, object]:
    """Resolve, quote, and project one successful model call."""
    return quote_model_cost(model=model, usage=usage).to_event_payload(operation_id)
