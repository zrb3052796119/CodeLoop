from __future__ import annotations

from dataclasses import replace

import pytest

from minicode.web.cost_aggregation import (
    aggregate_run_cost,
    merge_cost_aggregates,
    project_cost_breakdown,
    project_cost_metric,
)


def _event(event_type: str, operation: str, **payload: object) -> dict[str, object]:
    return {
        "type": event_type,
        "payload": {"operationId": "modelop_" + operation * 32, **payload},
    }


def _completed(operation: str, source: str = "provider") -> dict[str, object]:
    return _event(
        "model.completed",
        operation,
        usage={
            "source": source,
            "inputTokens": 10 if source != "unavailable" else None,
            "outputTokens": 2 if source != "unavailable" else None,
            "cacheReadTokens": 0 if source != "unavailable" else None,
            "cacheCreationTokens": None,
        },
    )


def _priced(
    operation: str,
    amount: object,
    *,
    quality: str = "provider_usage_catalog_rate",
    model: str = "openai/gpt-4o",
) -> dict[str, object]:
    return _event(
        "model.costed",
        operation,
        costVersion=1,
        status="priced",
        quality=quality,
        currency="USD",
        catalogId="minicode-pricing-2026-07-17-v1",
        catalogModelKey=model,
        amountNanoUsd=amount,
        components={
            "inputNanoUsd": amount,
            "outputNanoUsd": 0,
            "cacheReadNanoUsd": 0,
            "cacheCreationNanoUsd": 0,
        },
    )


def test_one_provider_priced_operation_is_complete_and_exact() -> None:
    aggregate = aggregate_run_cost(
        [
            _event("model.started", "a"),
            _event(
                "model.completed",
                "a",
                usage={
                    "source": "provider",
                    "inputTokens": 120,
                    "outputTokens": 24,
                    "cacheReadTokens": 8,
                    "cacheCreationTokens": 0,
                },
            ),
            _event(
                "model.costed",
                "a",
                costVersion=1,
                status="priced",
                quality="provider_usage_catalog_rate",
                currency="USD",
                catalogId="minicode-pricing-2026-07-17-v1",
                catalogModelKey="openai/gpt-4o",
                amountNanoUsd=530_000,
                components={
                    "inputNanoUsd": 280_000,
                    "outputNanoUsd": 240_000,
                    "cacheReadNanoUsd": 10_000,
                    "cacheCreationNanoUsd": 0,
                },
            ),
        ],
        run_source="gateway",
    )

    assert project_cost_metric(aggregate) == {
        "status": "complete",
        "value": {
            "currency": "USD",
            "amountNanoUsd": "530000",
            "providerUsageNanoUsd": "530000",
            "estimatedUsageNanoUsd": "0",
            "components": {
                "inputNanoUsd": "280000",
                "outputNanoUsd": "240000",
                "cacheReadNanoUsd": "10000",
                "cacheCreationNanoUsd": "0",
            },
            "pricedCalls": 1,
            "quality": "provider",
            "catalogIds": ["minicode-pricing-2026-07-17-v1"],
        },
        "coverage": {
            "completedCalls": 1,
            "pricedCalls": 1,
            "unavailableCalls": 0,
            "missingCalls": 0,
            "failedAttempts": 0,
            "invalidEvents": 0,
            "duplicateEvents": 0,
            "conflictEvents": 0,
            "orphanEvents": 0,
            "historical": "partial",
            "scope": "retained-run-journal",
            "limited": False,
        },
    }
    assert aggregate.diagnostics == ()


def test_anomalies_never_reprice_or_double_count_a_partial_run() -> None:
    duplicate = _priced(
        "6",
        200,
        quality="estimated_usage_catalog_rate",
        model="openai/gpt-4o-mini",
    )
    aggregate = aggregate_run_cost(
        [
            _event("model.started", "a"),
            _completed("a"),
            _priced("a", 100),
            _event("model.started", "b"),
            _completed("b"),
            _event(
                "model.costed",
                "b",
                costVersion=1,
                status="unavailable",
                quality="unavailable",
                currency="USD",
                catalogId="minicode-pricing-2026-07-17-v1",
                reason="model_unpriced",
            ),
            _event("model.started", "c"),
            _completed("c"),
            _event("model.started", "d"),
            _event("model.failed", "d", failureKind="provider_error"),
            _priced("e", 999),
            _event("model.started", "f"),
            _priced("f", 999),
            _completed("f"),
            _event("model.started", "6"),
            _completed("6", "estimated"),
            duplicate,
            duplicate,
            _event("model.started", "7"),
            _completed("7"),
            _priced("7", 400),
            _priced("7", 401),
            _event("model.started", "8"),
            _completed("8", "estimated"),
            _priced("8", 500),
            _event("model.started", "9"),
            _event("model.failed", "9", failureKind="timeout"),
            _priced("9", 999),
            {"type": "model.costed", "payload": {"operationId": "bad-secret"}},
        ]
    )

    metric = project_cost_metric(aggregate)

    assert metric["status"] == "partial"
    assert metric["value"]["amountNanoUsd"] == "300"
    assert metric["value"]["providerUsageNanoUsd"] == "100"
    assert metric["value"]["estimatedUsageNanoUsd"] == "200"
    assert metric["value"]["pricedCalls"] == 2
    assert metric["value"]["quality"] == "mixed"
    assert metric["coverage"] == {
        "completedCalls": 7,
        "pricedCalls": 2,
        "unavailableCalls": 1,
        "missingCalls": 2,
        "failedAttempts": 2,
        "invalidEvents": 7,
        "duplicateEvents": 1,
        "conflictEvents": 1,
        "orphanEvents": 3,
        "historical": "partial",
        "scope": "retained-run-journal",
        "limited": False,
    }
    assert {item["code"] for item in aggregate.diagnostics} == {
        "cost_event_invalid",
        "cost_operation_unpaired",
        "cost_operation_duplicate",
        "cost_operation_conflict",
        "cost_operation_missing",
        "cost_quality_mismatch",
        "failed_attempt_unpriced",
    }
    assert "modelop_" not in str(aggregate.diagnostics)
    assert "secret" not in str(aggregate.diagnostics)


@pytest.mark.parametrize(
    ("events", "limited", "expected_status", "expected_value"),
    [
        (
            [
                _event("model.started", "a"),
                _completed("a"),
                _event(
                    "model.costed",
                    "a",
                    costVersion=1,
                    status="unavailable",
                    quality="unavailable",
                    currency="USD",
                    catalogId="minicode-pricing-2026-07-17-v1",
                    reason="model_unpriced",
                ),
            ],
            False,
            "unavailable",
            None,
        ),
        ([_event("model.started", "a"), _completed("a")], False, "unavailable", None),
        (
            [
                _event("model.started", "a"),
                _event("model.failed", "a", failureKind="provider_error"),
            ],
            False,
            "unavailable",
            None,
        ),
        ([], False, "unavailable", None),
        (
            [_event("model.started", "a"), _completed("a"), _priced("a", 0)],
            False,
            "complete",
            "0",
        ),
        (
            [_event("model.started", "a"), _completed("a"), _priced("a", 0)],
            True,
            "partial",
            "0",
        ),
    ],
)
def test_status_never_confuses_zero_with_unavailable(
    events: list[dict[str, object]],
    limited: bool,
    expected_status: str,
    expected_value: str | None,
) -> None:
    metric = project_cost_metric(
        aggregate_run_cost(events, limited=limited)
    )

    assert metric["status"] == expected_status
    if expected_value is None:
        assert metric["value"] is None
    else:
        assert metric["value"]["amountNanoUsd"] == expected_value


@pytest.mark.parametrize(
    "cost",
    [
        _priced("a", True),
        _priced("a", -1),
        _priced("a", 1.5),
        _priced("a", 1_000_000_000_000_000_001),
        {
            **_priced("a", 1),
            "payload": {**_priced("a", 1)["payload"], "currency": "EUR"},
        },
        {
            **_priced("a", 1),
            "payload": {
                **_priced("a", 1)["payload"],
                "components": {
                    "inputNanoUsd": 2,
                    "outputNanoUsd": 0,
                    "cacheReadNanoUsd": 0,
                    "cacheCreationNanoUsd": 0,
                },
            },
        },
    ],
)
def test_invalid_money_never_enters_the_observed_amount(
    cost: dict[str, object],
) -> None:
    aggregate = aggregate_run_cost(
        [_event("model.started", "a"), _completed("a"), cost]
    )
    metric = project_cost_metric(aggregate)

    assert metric["status"] == "unavailable"
    assert metric["value"] is None
    assert metric["coverage"]["invalidEvents"] == 1
    assert {item["code"] for item in aggregate.diagnostics} == {
        "cost_event_invalid",
        "cost_operation_missing",
    }


def test_merge_preserves_run_isolation_exact_strings_and_stable_breakdowns() -> None:
    def priced_run(
        source: str,
        operation: str,
        amount: int,
        *,
        quality: str,
        catalog: str,
        model: str,
    ):
        cost = _priced(operation, amount, quality=quality, model=model)
        cost["payload"]["catalogId"] = catalog
        usage_source = "provider" if quality.startswith("provider") else "estimated"
        return aggregate_run_cost(
            [
                _event("model.started", operation),
                _completed(operation, usage_source),
                cost,
            ],
            run_source=source,
        )

    first = priced_run(
        "gateway",
        "a",
        100,
        quality="provider_usage_catalog_rate",
        catalog="minicode-pricing-a-v1",
        model="openai/gpt-4o",
    )
    second = priced_run(
        "headless",
        "a",
        300,
        quality="estimated_usage_catalog_rate",
        catalog="minicode-pricing-b-v1",
        model="openai/gpt-4o-mini",
    )
    third = priced_run(
        "tui",
        "b",
        200,
        quality="provider_usage_catalog_rate",
        catalog="minicode-pricing-a-v1",
        model="openai/gpt-4o",
    )
    unavailable = aggregate_run_cost(
        [
            _event("model.started", "c"),
            _completed("c"),
            _event(
                "model.costed",
                "c",
                costVersion=1,
                status="unavailable",
                quality="unavailable",
                currency="USD",
                catalogId="minicode-pricing-a-v1",
                reason="model_unpriced",
            ),
            _event("model.started", "d"),
            _completed("d"),
            _event("model.started", "e"),
            _event("model.failed", "e", failureKind="provider_error"),
        ],
        run_source="unknown",
    )

    merged = merge_cost_aggregates([first, second, third, unavailable])
    metric = project_cost_metric(merged)

    assert metric["status"] == "partial"
    assert metric["value"]["amountNanoUsd"] == "600"
    assert metric["value"]["providerUsageNanoUsd"] == "300"
    assert metric["value"]["estimatedUsageNanoUsd"] == "300"
    assert project_cost_breakdown(merged) == {
        "quality": [
            {
                "quality": "estimated_usage_catalog_rate",
                "pricedCalls": 1,
                "amountNanoUsd": "300",
            },
            {
                "quality": "provider_usage_catalog_rate",
                "pricedCalls": 2,
                "amountNanoUsd": "300",
            },
        ],
        "catalogs": [
            {
                "catalogId": "minicode-pricing-a-v1",
                "pricedCalls": 2,
                "amountNanoUsd": "300",
            },
            {
                "catalogId": "minicode-pricing-b-v1",
                "pricedCalls": 1,
                "amountNanoUsd": "300",
            },
        ],
        "models": [
            {
                "catalogModelKey": "openai/gpt-4o",
                "pricedCalls": 2,
                "amountNanoUsd": "300",
            },
            {
                "catalogModelKey": "openai/gpt-4o-mini",
                "pricedCalls": 1,
                "amountNanoUsd": "300",
            },
        ],
        "sources": [
            {"source": "headless", "pricedCalls": 1, "amountNanoUsd": "300"},
            {"source": "tui", "pricedCalls": 1, "amountNanoUsd": "200"},
            {"source": "gateway", "pricedCalls": 1, "amountNanoUsd": "100"},
        ],
        "unavailableReasons": [
            {"reason": "failed_attempt_unpriced", "calls": 1},
            {"reason": "missing_cost_event", "calls": 1},
            {"reason": "model_unpriced", "calls": 1},
        ],
    }


def test_amount_above_javascript_safe_integer_remains_an_exact_string() -> None:
    amount = 9_007_199_254_740_993
    aggregate = aggregate_run_cost(
        [_event("model.started", "a"), _completed("a"), _priced("a", amount)]
    )

    assert project_cost_metric(aggregate)["value"]["amountNanoUsd"] == str(amount)


def test_same_bounded_input_is_fully_deterministic() -> None:
    events = [
        _event("model.started", "a"),
        _completed("a"),
        _priced("a", 530_000),
    ]

    first = aggregate_run_cost(events, run_source="gateway")
    second = aggregate_run_cost(events, run_source="gateway")

    assert first == second
    assert project_cost_metric(first) == project_cost_metric(second)
    assert project_cost_breakdown(first) == project_cost_breakdown(second)


def test_event_limit_and_journal_failure_retain_observed_amount_as_partial() -> None:
    events = [
        _event("model.started", "a"),
        _completed("a"),
        _priced("a", 530_000),
        *({"type": "tool.started", "payload": {}} for _ in range(998)),
    ]

    limited = project_cost_metric(
        aggregate_run_cost(events, max_events=10_000)
    )
    read_failed = project_cost_metric(
        aggregate_run_cost(events[:3], journal_read_failed=True)
    )

    assert limited["status"] == "partial"
    assert limited["value"]["amountNanoUsd"] == "530000"
    assert limited["coverage"]["limited"] is True
    assert read_failed["status"] == "partial"
    assert read_failed["value"]["amountNanoUsd"] == "530000"
    assert read_failed["coverage"]["limited"] is True


def test_merge_total_overflow_is_rejected_instead_of_wrapping_or_rounding() -> None:
    one = aggregate_run_cost(
        [
            _event("model.started", "a"),
            _completed("a"),
            _priced("a", 1_000_000_000_000_000_000),
        ]
    )
    over_limit = replace(
        one,
        completed_calls=100_001,
        priced=one.priced * 100_001,
    )

    merged = merge_cost_aggregates([over_limit])
    metric = project_cost_metric(merged)

    assert metric["status"] == "unavailable"
    assert metric["value"] is None
    assert metric["coverage"]["limited"] is True
    assert metric["coverage"]["invalidEvents"] == 1
    assert {item["code"] for item in merged.diagnostics} == {
        "cost_event_invalid"
    }
