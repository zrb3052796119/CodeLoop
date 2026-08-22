from __future__ import annotations

import math

from scripts.analyze_persistent_memory_large_study import (
    _run_metrics,
    build_family_rows,
    build_pair_rows,
    cluster_bootstrap,
    exact_sign_test,
    exact_wilcoxon,
    hodges_lehmann,
    holm_adjust,
)


def test_exact_paired_statistics_have_known_small_sample_values() -> None:
    differences = [1.0, 2.0, 3.0, 4.0]

    sign = exact_sign_test(differences)
    wilcoxon = exact_wilcoxon(differences)

    assert sign == {
        "positive": 4,
        "negative": 0,
        "zero": 0,
        "n_nonzero": 4,
        "p_two_sided": 0.125,
    }
    assert wilcoxon["w_plus"] == 10
    assert wilcoxon["w_minus"] == 0
    assert wilcoxon["p_two_sided"] == 0.125
    assert wilcoxon["rank_biserial"] == 1
    assert hodges_lehmann(differences) == 2.5


def test_zero_differences_are_removed_from_exact_tests() -> None:
    assert exact_sign_test([0, 0]) == {
        "positive": 0,
        "negative": 0,
        "zero": 2,
        "n_nonzero": 0,
        "p_two_sided": 1.0,
    }
    assert exact_wilcoxon([0, 0])["p_two_sided"] == 1.0


def test_holm_adjustment_is_monotone_in_sorted_probability_order() -> None:
    adjusted = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})

    assert adjusted == {"a": 0.03, "c": 0.06, "b": 0.06}


def test_cluster_bootstrap_is_seeded_and_reports_expected_point_estimate() -> None:
    first = cluster_bootstrap([4, 6, 8], [2, 3, 4], samples=200, seed=17)
    second = cluster_bootstrap([4, 6, 8], [2, 3, 4], samples=200, seed=17)

    assert first == second
    assert first["absolute"]["estimate"] == 3
    assert first["relative_percent"]["estimate"] == 50


def test_run_metrics_separates_interactive_work_from_memory_reflection() -> None:
    events = [
        {
            "type": "run.started",
            "timestamp": "2026-08-21T00:00:00.000Z",
            "payload": {},
        },
        {
            "type": "memory.rendered",
            "timestamp": "2026-08-21T00:00:00.010Z",
            "payload": {"injected": True, "renderedCount": 1},
        },
        {
            "type": "model.completed",
            "timestamp": "2026-08-21T00:00:00.020Z",
            "payload": {
                "usage": {"inputTokens": 100, "outputTokens": 10},
            },
        },
        {
            "type": "tool.started",
            "timestamp": "2026-08-21T00:00:00.030Z",
            "payload": {"operationId": "op1", "toolName": "read_file"},
        },
        {
            "type": "tool.finished",
            "timestamp": "2026-08-21T00:00:00.040Z",
            "payload": {
                "operationId": "op1",
                "toolName": "read_file",
                "outcome": "success",
                "paired": True,
            },
        },
        {
            "type": "task.outcome",
            "timestamp": "2026-08-21T00:00:00.050Z",
            "payload": {"outcomeStatus": "success"},
        },
        {
            "type": "model.completed",
            "timestamp": "2026-08-21T00:00:00.060Z",
            "payload": {
                "purpose": "memory_reflection",
                "usage": {"inputTokens": 25, "outputTokens": 5},
            },
        },
        {
            "type": "run.completed",
            "timestamp": "2026-08-21T00:00:00.100Z",
            "payload": {},
        },
    ]

    metrics = _run_metrics(events)

    assert metrics["task_model_calls"] == 1
    assert metrics["total_model_calls"] == 2
    assert metrics["reflection_model_calls"] == 1
    assert metrics["task_input_tokens"] == 100
    assert metrics["total_input_tokens"] == 125
    assert metrics["direct_first"] is True
    assert metrics["memory_injected"] is True
    assert metrics["duration_ms"] == 100


def _turn_row(
    block: int,
    condition: str,
    *,
    tool_calls: int,
) -> dict[str, object]:
    return {
        "block": block,
        "family_id": "family-a",
        "stratum": "operations",
        "lesson_mode": "learned",
        "condition": condition,
        "condition_order": 1 if condition == "warm" else 2,
        "target_success": True,
        "direct_first": condition == "warm",
        "memory_injected": condition == "warm",
        "tool_calls": tool_calls,
        "tool_failures": 0,
        "task_model_calls": tool_calls + 1,
        "task_input_tokens": tool_calls * 100,
        "task_output_tokens": tool_calls * 10,
        "total_model_calls": tool_calls + 1,
        "total_input_tokens": tool_calls * 100,
        "total_output_tokens": tool_calls * 10,
        "duration_ms": tool_calls * 1000,
    }


def test_pair_and_family_rows_keep_blocks_nested() -> None:
    turns = [
        _turn_row(block, condition, tool_calls=1 if condition == "warm" else block + 1)
        for block in range(1, 4)
        for condition in ("warm", "cold")
    ]

    pairs = build_pair_rows(turns)
    families = build_family_rows(pairs)

    assert len(pairs) == 3
    assert len(families) == 1
    assert families[0]["blocks"] == 3
    assert families[0]["warm_tool_calls"] == 1
    assert families[0]["cold_tool_calls"] == 3
    assert families[0]["saving_tool_calls"] == 2
    assert math.isclose(
        float(families[0]["reduction_percent_tool_calls"]),
        200 / 3,
    )
