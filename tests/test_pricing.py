from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest

from minicode.pricing import (
    CATALOG_ID,
    CATALOG_VERSION,
    ModelPrice,
    PRODUCTION_CATALOG,
    PricingCatalog,
    quote_model_cost,
)
from minicode.openai_adapter import OpenAIModelAdapter


def _usage(
    *,
    source: str = "provider",
    input_tokens: object = 100,
    output_tokens: object = 20,
    cache_read_tokens: object = 0,
    cache_creation_tokens: object = None,
) -> dict[str, object]:
    return {
        "source": source,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheCreationTokens": cache_creation_tokens,
    }


def _catalog(
    *,
    semantics: str = "input_includes_cache_read",
    input_rate: Decimal | None = Decimal("2.5"),
    output_rate: Decimal | None = Decimal("10"),
    cache_read_rate: Decimal | None = Decimal("1.25"),
    cache_creation_rate: Decimal | None = None,
    not_applicable: frozenset[str] = frozenset({"cache_creation"}),
) -> PricingCatalog:
    return PricingCatalog(
        catalog_id="test-only-pricing-v1",
        catalog_version=1,
        currency="USD",
        entries=(
            ModelPrice(
                catalog_model_key="test/model",
                aliases=("test-alias",),
                input_usd_per_million=input_rate,
                output_usd_per_million=output_rate,
                cache_read_usd_per_million=cache_read_rate,
                cache_creation_usd_per_million=cache_creation_rate,
                token_bucket_semantics=semantics,
                not_applicable_buckets=not_applicable,
                provider="test-only",
                source_url="https://example.invalid/test-only",
                retrieved_on="2026-07-17",
                rate_unit="USD per 1M tokens",
                cache_billing_note="test-only fixed rates",
            ),
        ),
    )


def test_production_catalog_is_fixed_exact_and_immutable() -> None:
    priced = quote_model_cost(model="openai/gpt-4o", usage=_usage())
    alias = quote_model_cost(model="gpt-4o", usage=_usage())

    assert CATALOG_ID == "minicode-pricing-2026-07-17-v1"
    assert CATALOG_VERSION == 1
    assert priced == alias
    assert priced.catalog_model_key == "openai/gpt-4o"
    assert priced.catalog_id == CATALOG_ID
    assert isinstance(priced.components, MappingProxyType)
    with pytest.raises(TypeError):
        priced.components["inputNanoUsd"] = 0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        priced.catalog_id = "replacement"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        PRODUCTION_CATALOG.entries = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        PRODUCTION_CATALOG._index["default"] = PRODUCTION_CATALOG.entries[0]  # type: ignore[index]


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4",
        "gpt-4o-extra",
        "GPT-4O",
        " gpt-4o",
        "gpt-4o\n",
        "default",
        "sk-secret-model-name",
    ],
)
def test_model_matching_is_exact_and_unknown_identity_is_not_returned(model: str) -> None:
    quote = quote_model_cost(model=model, usage=_usage())

    assert quote.status == "unavailable"
    assert quote.reason == "model_unpriced"
    assert quote.catalog_model_key is None
    assert model not in str(quote.to_event_payload("modelop_" + "a" * 32))


def test_arbitrary_custom_runtime_is_unpriced_even_when_raw_name_matches() -> None:
    custom = SimpleNamespace(runtime={"model": "gpt-4o"})

    assert quote_model_cost(model=custom, usage=_usage()).reason == "model_unpriced"


def test_explicit_safe_identity_can_use_exact_catalog_key() -> None:
    adapter = SimpleNamespace(catalog_model_key="openai/gpt-4o")

    assert quote_model_cost(model=adapter, usage=_usage()).status == "priced"


def test_actual_openai_adapter_requires_official_direct_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    official = OpenAIModelAdapter({"model": "gpt-4o"}, tools=None)
    custom = OpenAIModelAdapter(
        {
            "model": "gpt-4o",
            "openaiBaseUrl": "https://custom.example.invalid",
        },
        tools=None,
    )
    routed = OpenAIModelAdapter(
        {
            "model": "gpt-4o",
            "_openrouter_headers": {"X-Title": "MiniCode"},
        },
        tools=None,
    )

    assert quote_model_cost(model=official, usage=_usage()).status == "priced"
    assert quote_model_cost(model=custom, usage=_usage()).reason == "model_unpriced"
    assert quote_model_cost(model=routed, usage=_usage()).reason == "model_unpriced"


def test_hostile_identity_property_is_isolated_without_exception_text() -> None:
    class HostileModel:
        @property
        def catalog_model_key(self):
            raise SystemExit("sk-hostile-identity-secret")

    quote = quote_model_cost(model=HostileModel(), usage=_usage())

    assert quote.status == "unavailable"
    assert quote.reason == "pricing_failed"
    assert "hostile" not in str(quote)


def test_input_including_cache_read_is_not_double_counted_and_reconciles() -> None:
    quote = quote_model_cost(
        model="test/model",
        usage=_usage(input_tokens=100, output_tokens=10, cache_read_tokens=40),
        catalog=_catalog(),
    )

    assert quote.status == "priced"
    assert dict(quote.components) == {
        "inputNanoUsd": 150_000,
        "outputNanoUsd": 100_000,
        "cacheReadNanoUsd": 50_000,
        "cacheCreationNanoUsd": 0,
    }
    assert quote.amount_nano_usd == sum(quote.components.values()) == 300_000


def test_separate_cache_buckets_include_cache_creation() -> None:
    catalog = _catalog(
        semantics="input_excludes_cache_buckets",
        cache_creation_rate=Decimal("3.75"),
        not_applicable=frozenset(),
    )
    quote = quote_model_cost(
        model="test-alias",
        usage=_usage(
            input_tokens=60,
            output_tokens=10,
            cache_read_tokens=40,
            cache_creation_tokens=20,
        ),
        catalog=catalog,
    )

    assert quote.status == "priced"
    assert dict(quote.components) == {
        "inputNanoUsd": 150_000,
        "outputNanoUsd": 100_000,
        "cacheReadNanoUsd": 50_000,
        "cacheCreationNanoUsd": 75_000,
    }
    assert quote.amount_nano_usd == 375_000


def test_rounding_is_half_even_per_component_and_deterministic() -> None:
    catalog = _catalog(
        semantics="input_excludes_cache_buckets",
        input_rate=Decimal("0.0005"),
        output_rate=Decimal("0.0005"),
        cache_read_rate=None,
        not_applicable=frozenset({"cache_read", "cache_creation"}),
    )
    usage = _usage(
        input_tokens=1,
        output_tokens=3,
        cache_read_tokens=None,
    )

    first = quote_model_cost(model="test/model", usage=usage, catalog=catalog)
    second = quote_model_cost(model="test/model", usage=usage, catalog=catalog)

    assert first == second
    assert dict(first.components) == {
        "inputNanoUsd": 0,
        "outputNanoUsd": 2,
        "cacheReadNanoUsd": 0,
        "cacheCreationNanoUsd": 0,
    }
    assert first.amount_nano_usd == 2


@pytest.mark.parametrize("tokens", [0, 1_000_000_000])
def test_zero_and_large_valid_token_counts_reconcile(tokens: int) -> None:
    catalog = _catalog(
        semantics="input_excludes_cache_buckets",
        cache_read_rate=None,
        not_applicable=frozenset({"cache_read", "cache_creation"}),
    )

    quote = quote_model_cost(
        model="test/model",
        usage=_usage(
            input_tokens=tokens,
            output_tokens=tokens,
            cache_read_tokens=None,
        ),
        catalog=catalog,
    )

    assert quote.status == "priced"
    assert quote.amount_nano_usd == sum(quote.components.values())  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("usage", "reason"),
    [
        (_usage(input_tokens=-1), "invalid_usage"),
        (_usage(input_tokens=True), "invalid_usage"),
        (_usage(input_tokens=1_000_000_001), "invalid_usage"),
        (_usage(input_tokens=1, cache_read_tokens=2), "invalid_usage"),
        (_usage(input_tokens=None), "usage_unavailable"),
        (_usage(source="unavailable"), "usage_unavailable"),
        (_usage(source="invented"), "invalid_usage"),
    ],
)
def test_invalid_or_missing_usage_is_safely_unavailable(
    usage: dict[str, object], reason: str
) -> None:
    quote = quote_model_cost(model="test/model", usage=usage, catalog=_catalog())

    assert quote.status == "unavailable"
    assert quote.reason == reason
    assert quote.amount_nano_usd is None
    assert quote.components is None


def test_nonzero_bucket_without_rate_makes_whole_quote_unavailable() -> None:
    catalog = _catalog(
        semantics="input_excludes_cache_buckets",
        cache_creation_rate=None,
        not_applicable=frozenset({"cache_read"}),
    )

    quote = quote_model_cost(
        model="test/model",
        usage=_usage(cache_read_tokens=None, cache_creation_tokens=4),
        catalog=catalog,
    )

    assert quote.status == "unavailable"
    assert quote.reason == "pricing_incomplete"
    assert quote.amount_nano_usd is None


def test_non_decimal_catalog_rate_fails_closed() -> None:
    catalog = _catalog(input_rate=0.1)  # type: ignore[arg-type]

    quote = quote_model_cost(
        model="test/model",
        usage=_usage(),
        catalog=catalog,
    )

    assert quote.status == "unavailable"
    assert quote.reason == "pricing_failed"
    assert quote.amount_nano_usd is None


def test_estimated_usage_has_explicit_quality_when_all_buckets_are_known() -> None:
    catalog = _catalog(
        semantics="input_excludes_cache_buckets",
        cache_read_rate=None,
        not_applicable=frozenset({"cache_read", "cache_creation"}),
    )
    quote = quote_model_cost(
        model="test/model",
        usage=_usage(
            source="estimated",
            cache_read_tokens=None,
            cache_creation_tokens=None,
        ),
        catalog=catalog,
    )

    assert quote.status == "priced"
    assert quote.quality == "estimated_usage_catalog_rate"
    assert "exact" not in quote.quality
    assert "invoice" not in quote.quality
