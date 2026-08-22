from __future__ import annotations

import time

import pytest

from minicode.agent_intelligence import (
    ClassifiedError,
    ErrorCategory,
    NudgeGenerator,
    RecoveryStrategy,
)
from minicode.state_observer import MeasurementVector, StateObserver


def test_command_permission_nudge_never_recommends_escalation() -> None:
    error = ClassifiedError(
        category=ErrorCategory.PERMISSION,
        strategy=RecoveryStrategy.REQUEST_PERMISSION,
        confidence=0.9,
        context={"tool_name": "run_command"},
    )

    nudge = NudgeGenerator.generate(error, retry_count=0)

    assert "sudo" in nudge.lower()
    assert "do not add sudo" in nudge.lower()
    assert "elevated permissions" not in nudge.lower()
    assert "retry attempt" not in nudge.lower()


def test_zero_bootstrap_sample_does_not_poison_latency_baseline() -> None:
    observer = StateObserver()
    observer.update(
        MeasurementVector(
            timestamp=time.time(),
            response_time=0.0,
            success_rate=1.0,
            error_count=0,
            tool_calls=0,
        )
    )

    for _ in range(20):
        state = observer.update(
            MeasurementVector(
                timestamp=time.time(),
                response_time=0.25,
                success_rate=1.0,
                error_count=0,
                tool_calls=1,
            )
        )

    assert observer._response_time_baseline == pytest.approx(0.25)
    assert state.internal_load < 0.45
    assert state.context_pressure < 0.1
    assert state.system_degradation < 0.1
