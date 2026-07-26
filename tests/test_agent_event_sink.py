from __future__ import annotations

import logging
import re

import pytest

from minicode.run_events import emit_event_safely, new_model_operation_id


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, int | None, object]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, step, payload))


def test_none_is_noop_and_normal_sink_receives_unchanged_payload() -> None:
    payload = {"operationId": "modelop_" + "a" * 32}
    sink = RecordingSink()

    emit_event_safely(None, "model.started", step=1, payload=payload)
    emit_event_safely(sink, "model.started", step=1, payload=payload)

    assert sink.events == [("model.started", 1, payload)]
    assert sink.events[0][2] is payload


def test_sink_exception_is_isolated_and_log_is_generic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingSink:
        def emit(self, *_args, **_kwargs) -> None:
            raise RuntimeError("Bearer sink-secret payload-secret")

    caplog.set_level(logging.WARNING, logger="minicode.run_events")

    emit_event_safely(
        FailingSink(),
        "model.failed",
        step=7,
        payload={"password": "payload-secret"},
    )

    assert "Agent event sink unavailable" in caplog.text
    assert "sink-secret" not in caplog.text
    assert "payload-secret" not in caplog.text
    assert "model.failed" not in caplog.text


def test_sink_keyboard_interrupt_is_not_swallowed() -> None:
    class InterruptingSink:
        def emit(self, *_args, **_kwargs) -> None:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        emit_event_safely(InterruptingSink(), "model.started", step=1)


def test_model_operation_ids_are_local_bounded_and_unique() -> None:
    first = new_model_operation_id()
    second = new_model_operation_id()

    assert re.fullmatch(r"modelop_[0-9a-f]{32}", first)
    assert re.fullmatch(r"modelop_[0-9a-f]{32}", second)
    assert first != second
