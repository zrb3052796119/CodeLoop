from __future__ import annotations

import json

import pytest

from minicode.run_events import project_model_duration_ms, project_model_usage
from minicode.types import ModelUsage


def test_project_model_usage_preserves_canonical_provider_buckets() -> None:
    assert project_model_usage(
        ModelUsage(
            input_tokens=1_200,
            output_tokens=180,
            cache_read_tokens=900,
            cache_creation_tokens=0,
            source="provider",
        )
    ) == {
        "source": "provider",
        "inputTokens": 1_200,
        "outputTokens": 180,
        "cacheReadTokens": 900,
        "cacheCreationTokens": 0,
    }


def test_project_model_duration_uses_bounded_monotonic_elapsed_milliseconds() -> None:
    assert project_model_duration_ms(10.0, 10.842) == 842


def test_project_model_usage_preserves_estimated_nullable_and_explicit_zero() -> None:
    assert project_model_usage(
        ModelUsage(
            input_tokens=0,
            output_tokens=42,
            cache_read_tokens=None,
            cache_creation_tokens=None,
            source="estimated",
        )
    ) == {
        "source": "estimated",
        "inputTokens": 0,
        "outputTokens": 42,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
    }


@pytest.mark.parametrize(
    "usage",
    [
        None,
        ModelUsage(source="unavailable"),
        ModelUsage(input_tokens=True, source="provider"),
        ModelUsage(output_tokens=-1, source="provider"),
        ModelUsage(cache_read_tokens=1_000_000_001, source="provider"),
        ModelUsage(source="invalid"),  # type: ignore[arg-type]
    ],
)
def test_project_model_usage_safely_degrades_missing_or_invalid_values(
    usage: object | None,
) -> None:
    assert project_model_usage(usage) == {
        "source": "unavailable",
        "inputTokens": None,
        "outputTokens": None,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
    }


def test_project_model_usage_does_not_leak_or_raise_from_malicious_properties() -> None:
    class HostileUsage:
        source = "provider"

        @property
        def input_tokens(self):
            raise RuntimeError("Bearer projection-secret")

    projected = project_model_usage(HostileUsage())

    assert projected["source"] == "unavailable"
    assert "projection-secret" not in json.dumps(projected)


@pytest.mark.parametrize(
    ("started_at", "finished_at"),
    [
        (True, 1.0),
        (0.0, float("nan")),
        (2.0, 1.0),
        (0.0, 86_400.001),
        ("0", 1.0),
    ],
)
def test_project_model_duration_rejects_invalid_or_unreasonable_values(
    started_at: object, finished_at: object
) -> None:
    assert project_model_duration_ms(started_at, finished_at) is None
