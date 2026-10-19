"""Vision calls share their caller's admission, usage, and lifecycle controls."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolExecutionAbandoned
from minicode.tools.inspect_image import inspect_image_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken

_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "/z8AAwMBAAJDAXQAAAAASUVORK5CYII="
)
_RUNTIME = {
    "visionModel": "gpt-4o",
    "subagentBaseUrl": "https://private-vision.example.invalid/v1",
    "subagentApiKey": "private-api-key",
}


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, dict(payload or {})))


class Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture(autouse=True)
def workspace_image_and_no_live_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "private-file.png").write_bytes(base64.b64decode(_PNG_BASE64))

    def unexpected_http(*_args, **_kwargs):
        raise AssertionError("unexpected vision HTTP request")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_http)


def _context(tmp_path: Path, budget=None, **kwargs) -> ToolContext:
    return ToolContext(
        cwd=str(tmp_path), _runtime=_RUNTIME, _agent_budget=budget, **kwargs
    )


def _run(context: ToolContext):
    return inspect_image_tool.run(
        {"path": "private-file.png", "question": "private-question-marker"}, context
    )


def _response(usage: object = None) -> dict:
    return {
        "choices": [{"message": {"content": "private-answer-marker"}}],
        "usage": usage,
    }


@pytest.mark.parametrize("dimension", ["model_calls", "tokens", "cost"])
def test_exhausted_shared_budget_prevents_vision_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dimension: str,
) -> None:
    calls = []
    budget = AgentTurnBudget(
        max_model_calls=1 if dimension == "model_calls" else 10,
        max_total_tokens=10 if dimension == "tokens" else 100_000,
        max_cost_usd="0.001" if dimension == "cost" else "1",
    )
    reservation = budget.reserve_model_call()
    budget.record_model_call(input_tokens=10, cost_usd="0.001", reservation=reservation)
    previous = budget.snapshot().to_dict()

    def http(*_args, **_kwargs):
        calls.append(1)
        return Response(_response({"prompt_tokens": 100, "completion_tokens": 42}))

    monkeypatch.setattr("urllib.request.urlopen", http)
    result = _run(_context(tmp_path, budget))
    assert not result.ok
    assert "budget" in result.output
    assert calls == []
    assert budget.snapshot().to_dict() == previous


def test_actual_usage_is_settled_once_and_attributed_with_private_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = AgentTurnBudget(
        max_model_calls=3, max_total_tokens=100_000, max_cost_usd="1"
    )
    workflow = shared.attribution_view("workflow")
    visual = workflow.attribution_view("visual")
    sink = RecordingSink()
    calls = []

    def http(*_args, **_kwargs):
        calls.append(1)
        assert shared.snapshot().used_model_calls == 1
        assert shared.snapshot().reserved_total_tokens > 0
        return Response(_response({"prompt_tokens": 100, "completion_tokens": 42}))

    monkeypatch.setattr("urllib.request.urlopen", http)
    result = _run(_context(tmp_path, visual, _event_sink=sink, _step=3))
    assert result.ok
    assert calls == [1]
    after = shared.snapshot()
    assert (
        after.used_model_calls,
        after.used_input_tokens,
        after.used_output_tokens,
    ) == (1, 100, 42)
    assert after.reserved_total_tokens == 0
    assert visual.attributed_tokens() == workflow.attributed_tokens() == (100, 42)
    assert [kind for kind, _ in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert len({event["operationId"] for _, event in sink.events}) == 1
    usage = sink.events[1][1]["usage"]
    assert usage["source"] == "provider"
    assert (usage["inputTokens"], usage["outputTokens"]) == (100, 42)
    price = sink.events[2][1]
    assert Decimal(after.used_cost_usd) == Decimal(price["amountNanoUsd"]) / Decimal(
        1_000_000_000
    )
    serialized = json.dumps(sink.events)
    for private in (
        _PNG_BASE64,
        "private-file",
        "private-question",
        "private-answer",
        "private-api-key",
        "private-vision",
    ):
        assert private not in serialized


@pytest.mark.parametrize(
    "usage",
    [None, {"total_tokens": 42}, {"prompt_tokens": True, "completion_tokens": -1}],
)
def test_missing_or_invalid_usage_is_conservatively_charged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: object,
) -> None:
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=100_000)
    sink = RecordingSink()
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: Response(_response(usage))
    )
    result = _run(_context(tmp_path, budget, _event_sink=sink))
    assert result.ok
    observed = sink.events[1][1]["usage"]
    assert observed["source"] == "estimated"
    assert observed["inputTokens"] > 0
    assert observed["outputTokens"] >= 1024
    assert (
        budget.snapshot().used_total_tokens
        == observed["inputTokens"] + observed["outputTokens"]
    )
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().reserved_total_tokens == 0
    assert Decimal(budget.snapshot().used_cost_usd) > 0


@pytest.mark.parametrize("failure", ["network", "http", "timeout"])
def test_transmitted_failures_release_reservations_and_record_uncertain_spend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=100_000)
    sink = RecordingSink()

    def http(*_args, **_kwargs):
        if failure == "http":
            raise urllib.error.HTTPError(
                "https://private-vision", 500, "private-error", {}, None
            )
        if failure == "timeout":
            raise TimeoutError("private-timeout-error")
        raise urllib.error.URLError("private-network-error")

    monkeypatch.setattr("urllib.request.urlopen", http)
    result = _run(_context(tmp_path, budget, _event_sink=sink))
    assert not result.ok
    after = budget.snapshot()
    assert after.used_model_calls == 1
    assert after.reserved_total_tokens == 0
    assert after.used_total_tokens > 0
    assert Decimal(after.used_cost_usd) > 0
    assert [kind for kind, _ in sink.events] == [
        "model.started",
        "model.failed",
        "model.costed",
    ]
    assert len({event["operationId"] for _, event in sink.events}) == 1
    assert "private-error" not in json.dumps(sink.events)
    assert "private-network-error" not in json.dumps(sink.events)
    if failure == "timeout":
        assert sink.events[1][1]["failureKind"] == "timeout"


@pytest.mark.parametrize("control", ["deadline", "cancel", "abandon"])
def test_stopped_owner_cannot_send_or_reserve_a_vision_call(
    tmp_path: Path,
    control: str,
) -> None:
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=100_000)
    token = TurnCancellationToken("turn_" + "1" * 32)
    abandoned = threading.Event()
    context = _context(
        tmp_path, budget, _cancellation_token=token, _tool_abandoned=abandoned
    )
    if control == "deadline":
        context._task_deadline_monotonic = time.monotonic() - 1
    elif control == "cancel":
        token.request()
    else:
        abandoned.set()
    with pytest.raises(
        (
            TaskDeadlineExceeded,
            ModelCallDeadlineExceeded,
            TurnCancellationRequested,
            ToolExecutionAbandoned,
        )
    ):
        _run(context)
    assert budget.snapshot().used_model_calls == 0
    assert budget.snapshot().reserved_total_tokens == 0


@pytest.mark.parametrize("control", ["cancel", "abandon", "deadline"])
def test_late_response_is_charged_but_cannot_continue_a_stopped_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: str,
) -> None:
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=100_000)
    token = TurnCancellationToken("turn_" + "2" * 32)
    abandoned = threading.Event()
    context = _context(
        tmp_path, budget, _cancellation_token=token, _tool_abandoned=abandoned
    )

    def http(*_args, **_kwargs):
        if control == "cancel":
            token.request()
        elif control == "abandon":
            abandoned.set()
        else:
            context._task_deadline_monotonic = time.monotonic() - 1
        return Response(_response({"prompt_tokens": 100, "completion_tokens": 42}))

    monkeypatch.setattr("urllib.request.urlopen", http)
    with pytest.raises(
        (TurnCancellationRequested, ToolExecutionAbandoned, TaskDeadlineExceeded)
    ):
        _run(context)
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 142
    assert budget.snapshot().reserved_total_tokens == 0


def test_abandonment_between_admission_and_http_releases_the_pending_reservation(
    tmp_path: Path,
) -> None:
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=100_000)
    abandoned = threading.Event()

    class AbandoningSink(RecordingSink):
        def emit(self, event_type: str, *, step=None, payload=None) -> None:
            super().emit(event_type, step=step, payload=payload)
            if event_type == "model.started":
                abandoned.set()

    sink = AbandoningSink()
    with pytest.raises(ToolExecutionAbandoned):
        _run(_context(tmp_path, budget, _event_sink=sink, _tool_abandoned=abandoned))
    assert budget.snapshot().reserved_total_tokens == 0
    assert budget.snapshot().used_total_tokens == 0
    assert [kind for kind, _ in sink.events] == ["model.started", "model.failed"]


def test_parent_deadline_bounds_vision_socket_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received = []

    def http(_request, *, timeout):
        received.append(timeout)
        return Response(_response({"prompt_tokens": 100, "completion_tokens": 42}))

    monkeypatch.setenv("MINICODE_MODEL_TIMEOUT", "60")
    monkeypatch.setattr("urllib.request.urlopen", http)
    context = _context(tmp_path, _task_deadline_monotonic=time.monotonic() + 0.5)
    assert _run(context).ok
    assert 0 < received[0] <= 0.5
