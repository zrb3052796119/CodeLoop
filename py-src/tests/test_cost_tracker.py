from minicode.cost_tracker import calculate_cost
import pytest


def test_calculate_cost_accepts_deepseek_direct_model_name() -> None:
    cost = calculate_cost(
        "deepseek-chat",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )

    assert cost == pytest.approx(0.63)


def test_calculate_cost_returns_float_for_unknown_model() -> None:
    cost = calculate_cost("unknown-openai-compatible-model", input_tokens=1000, output_tokens=500)

    assert isinstance(cost, float)
    assert cost > 0
