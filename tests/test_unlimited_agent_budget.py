"""Unlimited means absent cumulative ceilings, not an oversized allowance."""

from decimal import Decimal

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget, AttributedBudgetView
from minicode.config import load_runtime_config, safe_runtime_summary


_ENV = (
    "MINI_CODE_TURN_BUDGET_TOKENS",
    "MINI_CODE_TURN_BUDGET_MODEL_CALLS",
    "MINI_CODE_TURN_BUDGET_COST_USD",
)


@pytest.fixture(autouse=True)
def isolated_budget_environment(monkeypatch):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)


def test_default_budget_has_no_cumulative_ceiling_and_still_accounts_usage():
    budget = AgentTurnBudget.from_runtime({})
    child = AttributedBudgetView(budget, "executor")
    assert not budget.has_limits and not child.has_limits
    for _ in range(151):
        lease = child.reserve_model_call(30_000, preserve_model_calls=1000,
                                         preserve_total_tokens=10_000_000)
        child.record_model_call(input_tokens=30_000, output_tokens=100,
                                cost_usd="0.10", reservation=lease)
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls,
            snapshot.limit_cost_usd) == (None, None, None)
    assert snapshot.used_model_calls == 151
    assert snapshot.used_input_tokens == 4_530_000
    assert snapshot.used_output_tokens == 15_100
    assert snapshot.used_total_tokens == 4_545_100
    assert Decimal(snapshot.used_cost_usd) == Decimal("15.10")
    assert snapshot.reserved_total_tokens == 0
    assert child.snapshot() == snapshot


@pytest.mark.parametrize("disabled", [None, 0, "0"])
def test_explicit_disabled_fields_do_not_restore_product_defaults(disabled):
    budget = AgentTurnBudget.from_runtime({"agentTurnBudget": {
        "maxTokens": disabled, "maxModelCalls": disabled, "maxCostUsd": disabled,
    }})
    assert not budget.has_limits
    assert budget.snapshot().to_dict()["limitTotalTokens"] is None


@pytest.mark.parametrize("disabled", [None, 0, "0"])
def test_flat_disabled_fields_override_nested_finite_settings(disabled):
    budget = AgentTurnBudget.from_runtime({
        "agentTurnBudgetTokens": disabled,
        "agentTurnBudgetModelCalls": disabled,
        "agentTurnBudgetCostUsd": disabled,
        "agentTurnBudget": {"maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5"},
    })
    assert not budget.has_limits


def test_environment_zero_overrides_runtime_finite_values(monkeypatch):
    for key in _ENV:
        monkeypatch.setenv(key, "0")
    budget = AgentTurnBudget.from_runtime({"agentTurnBudget": {
        "maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5",
    }})
    assert not budget.has_limits


@pytest.mark.parametrize("limits,reason", [
    ({"maxTokens": 100}, "token budget"),
    ({"maxModelCalls": 1}, "model call budget"),
    ({"maxCostUsd": "0.10"}, "cost budget"),
])
def test_explicit_finite_limits_remain_enforced_without_adding_other_ceilings(limits, reason):
    budget = AgentTurnBudget.from_runtime({"agentTurnBudget": limits})
    assert budget.has_limits
    first = budget.reserve_model_call(50)
    budget.record_model_call(input_tokens=80, output_tokens=20, cost_usd="0.10",
                             reservation=first)
    with pytest.raises(AgentBudgetExceeded, match=reason):
        budget.reserve_model_call(1)
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.used_total_tokens == 100
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.limit_total_tokens == limits.get("maxTokens")
    assert snapshot.limit_model_calls == limits.get("maxModelCalls")
    assert snapshot.limit_cost_usd == limits.get("maxCostUsd")


@pytest.mark.parametrize("key", ["maxTokens", "maxModelCalls"])
@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "1.5", "invalid", float("inf")])
def test_invalid_integer_limit_never_silently_disables_accounting(key, invalid):
    with pytest.raises(ValueError, match="max_"):
        AgentTurnBudget.from_runtime({"agentTurnBudget": {key: invalid}})


@pytest.mark.parametrize("invalid", [True, False, -1, "NaN", "Infinity", "invalid"])
def test_invalid_cost_limit_is_rejected(invalid):
    with pytest.raises(ValueError, match="max_cost_usd"):
        AgentTurnBudget.from_runtime({"agentTurnBudget": {"maxCostUsd": invalid}})


def test_unlimited_budget_still_releases_abandoned_reservations():
    budget = AgentTurnBudget.from_runtime({})
    lease = budget.reserve_model_call(4_000_000)
    assert budget.snapshot().reserved_total_tokens == 4_000_000
    assert budget.fail_model_call(lease, charge_estimate=True)
    assert not budget.fail_model_call(lease, charge_estimate=True)
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.used_total_tokens == 4_000_000
    assert snapshot.reserved_total_tokens == 0


def test_migrated_config_keeps_explicit_budget_settings(monkeypatch):
    settings = {"modelEnvMigrationVersion": 1,
                "agentTurnBudget": {"maxTokens": 123, "maxModelCalls": 4, "maxCostUsd": "0.25"}}
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda cwd=None: settings)
    monkeypatch.setattr("minicode.config.read_settings_file", lambda path: settings)
    monkeypatch.setattr("minicode.env_file.read_private_env_file", lambda *args, **kwargs: {})
    runtime = load_runtime_config()
    assert runtime["agentTurnBudget"] == settings["agentTurnBudget"]
    assert safe_runtime_summary(runtime)["effectiveTurnBudget"] == settings["agentTurnBudget"]


def test_config_preserves_zero_override_and_reports_unlimited(monkeypatch):
    settings = {"env": {key: 0 for key in _ENV}, "agentTurnBudget": {
        "maxTokens": 123, "maxModelCalls": 4, "maxCostUsd": "0.25",
    }}
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda cwd=None: settings)
    monkeypatch.setattr("minicode.config.read_settings_file", lambda path: {})
    monkeypatch.setattr("minicode.env_file.read_private_env_file", lambda *args, **kwargs: {})
    runtime = load_runtime_config()
    assert runtime["agentTurnBudget"] == {"maxTokens": 0, "maxModelCalls": 0, "maxCostUsd": 0}
    assert safe_runtime_summary(runtime)["effectiveTurnBudget"] == {
        "maxTokens": None, "maxModelCalls": None, "maxCostUsd": None,
    }
