"""Explicit long-request policy, using real adapters and only local fake HTTP."""

import json
import threading
import time
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_evaluation_transport import (
    DONE,
    MESSAGES,
    RUNTIME,
    TEXT,
    USAGE,
    ScriptedResponse,
    config,
    event,
)
from test_reviewer_result_submission import _bound

from minicode import openai_adapter as adapter
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from scripts import run_continuous_agent_diagnostic as diagnostic
from scripts.evaluation_transport import (
    EvaluationIdleTimeout,
    EvaluationTransport,
    TransportConfig,
)


def _unlimited(tmp_path, **kwargs):
    return replace(
        config(tmp_path),
        deadline_epoch=None,
        max_model_calls=None,
        max_total_tokens=None,
        request_timeout_seconds=None,
        max_output_tokens=131072,
        **kwargs,
    )


def test_none_request_deadline_survives_real_adapter_and_stream(tmp_path, monkeypatch):
    observed = []

    def http(request, *, timeout):
        observed.append((json.loads(request.data), timeout,
                         request._minicode_connect_timeout_seconds))
        return ScriptedResponse(
            [b": heartbeat\n\n"] * 5 + [event({**TEXT, "usage": USAGE}), DONE],
            delay=0.02,
        )

    monkeypatch.setattr(adapter, "open_verified_url", http)
    cfg = _unlimited(tmp_path)
    assert TransportConfig(**json.loads(json.dumps(asdict(cfg)))) == cfg
    with EvaluationTransport(cfg) as transport:
        result = adapter.OpenAIModelAdapter(
            {**RUNTIME, "modelTimeoutSeconds": None, "maxOutputTokens": 131072}, None
        ).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert result.content == "hello" and snapshot["usageComplete"]
    assert snapshot["requests"][0]["requestDeadlineEpoch"] is None
    assert snapshot["requests"][0]["seconds"] > 0.1
    assert len(observed) == 1 and observed[0][0]["max_tokens"] == 131072
    assert observed[0][1] == cfg.idle_timeout_seconds == 60
    assert observed[0][2] == cfg.connect_timeout_seconds == 30


@pytest.mark.parametrize("guard", ["idle", "caller", "cancel"])
def test_none_total_deadline_keeps_actual_idle_owner_and_cancellation(
    tmp_path, monkeypatch, guard
):
    from minicode import model_call_control
    from scripts import evaluation_transport

    # Model elapsed receive time explicitly: setup/SQLite/runner scheduling must
    # not decide which guard wins. Keep the real adapter, reader, queue and ledger.
    clock = SimpleNamespace(now=time.monotonic())
    local_time = SimpleNamespace(monotonic=lambda: clock.now, time=time.time)
    for module in (adapter, model_call_control, evaluation_transport):
        monkeypatch.setattr(module, "time", local_time)
    first_consumed = threading.Event()
    received_at = []
    intervals = iter([0.03, 0.031] if guard == "caller" else [0.051])

    class TimedResponse(ScriptedResponse):
        def read1(self, _amount):
            if guard == "caller" and len(received_at) == 1:
                assert first_consumed.wait(2), "first heartbeat was not consumed"
            gap = next(intervals, None)
            if gap is None:
                self.closed.wait(2)
                return b""
            clock.now += gap
            received_at.append(clock.now)
            return b": heartbeat\n\n"

    original_update = evaluation_transport.SharedLedger.update

    def update(ledger, call, fields):
        result = original_update(ledger, call, fields)
        if fields.get("lastByteSeconds") is not None:
            first_consumed.set()
        return result

    monkeypatch.setattr(evaluation_transport.SharedLedger, "update", update)
    stream = TimedResponse([])
    token = TurnCancellationToken("turn_" + "a" * 32)
    opened = []

    def http(*args, **kwargs):
        opened.append(True)
        if guard == "cancel":
            token.request()
        return stream

    monkeypatch.setattr(adapter, "open_verified_url", http)
    expected = {
        "idle": EvaluationIdleTimeout,
        "caller": ModelCallDeadlineExceeded,
        "cancel": TurnCancellationRequested,
    }[guard]
    with EvaluationTransport(
        _unlimited(tmp_path, idle_timeout_seconds=0.05)
    ) as transport:
        started = clock.now
        kwargs = {"cancellation_token": token}
        if guard == "caller":
            kwargs["deadline_monotonic"] = started + 0.06
        with pytest.raises(expected):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES, **kwargs)
        snap = transport.ledger.snapshot()
    assert len(opened) == snap["started"] == snap["failed"] == snap["unknownUsageCalls"] == 1
    assert snap["requests"][0]["errorClass"] == expected.__name__
    assert stream.closed.wait(1)
    if guard == "caller":
        # Both actual receive gaps are below the unchanged 50ms idle bound;
        # only the 60ms caller deadline has elapsed on the second heartbeat.
        assert first_consumed.is_set() and len(received_at) == 2
        assert received_at[0] - started < 0.05
        assert received_at[1] - received_at[0] < 0.05
        assert received_at[1] >= kwargs["deadline_monotonic"]
    elif guard == "idle":
        assert len(received_at) == 1 and received_at[0] - started > 0.05
    else:
        assert received_at == []


def test_expired_caller_deadline_does_not_count_an_unstarted_request(tmp_path, monkeypatch):
    opened = []

    def http(*args, **kwargs):
        opened.append(True)
        raise AssertionError("an expired caller must not open HTTP")

    monkeypatch.setattr(adapter, "open_verified_url", http)
    with EvaluationTransport(_unlimited(tmp_path)) as transport:
        with pytest.raises(ModelCallDeadlineExceeded):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(
                MESSAGES, deadline_monotonic=time.monotonic() - 1,
            )
        snap = transport.ledger.snapshot()
    assert opened == [] and snap["requests"] == []
    assert snap["started"] == snap["failed"] == snap["unknownUsageCalls"] == 0


@pytest.mark.parametrize(
    "surface", ["parent", "executor", "reviewer", "reviewer-native"]
)
def test_all_loop_surfaces_pass_none_to_adapter_despite_external_environment(
    tmp_path, monkeypatch, surface
):
    import minicode.agent_loop as loop

    monkeypatch.setenv("MINICODE_MODEL_TIMEOUT", "300")
    observed = []
    original = loop._model_next

    def next_step(*args, **kwargs):
        observed.append((kwargs["deadline_monotonic"], kwargs.get("request_options")))
        return original(*args, **kwargs)

    monkeypatch.setattr(loop, "_model_next", next_step)
    body = []

    def http(request, *, timeout):
        body.append(json.loads(request.data))
        # Real adapter error path stops this parameter-boundary fixture. No
        # unbounded fake continuation or real network call is performed.
        return ScriptedResponse([event({"error": {"message": "offline fixture stop"}})])

    monkeypatch.setattr(adapter, "open_verified_url", http)
    runtime = {**RUNTIME, "maxOutputTokens": 131072, "modelTimeoutSeconds": None}
    registry = ToolRegistry([])
    options = {}
    if surface == "executor":
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name="edit_file",
                    description="authorized fixture editor",
                    input_schema={"type": "object"},
                    validator=lambda value: value,
                    run=lambda *_: ToolResult(ok=True, output="unused fixture tool"),
                )
            ]
        )
        options["implementation_task"] = True
    elif surface.startswith("reviewer"):
        _assignment, _store, channel, tool, _ = _bound()
        registry = ToolRegistry([tool])
        options.update(host_native_result_tool=tool, subagent_mailbox=channel)
    with diagnostic.role_overrides({"roleTurnLimits": diagnostic.ROLE_OVERRIDES}):
        assert "MINICODE_MODEL_TIMEOUT" not in __import__("os").environ
        with EvaluationTransport(_unlimited(tmp_path)) as transport:
            run_agent_turn(
                model=adapter.OpenAIModelAdapter(runtime, registry),
                tools=registry,
                messages=list(MESSAGES),
                cwd=str(tmp_path),
                runtime=runtime,
                agent_budget=AgentTurnBudget(
                    max_total_tokens=None, max_model_calls=None
                ),
                max_steps=0 if surface == "reviewer-native" else 1,
                max_steps_soft_landing=surface == "reviewer-native",
                deadline_monotonic=None,
                **options,
            )
            rows = transport.ledger.snapshot()["requests"]
    assert observed and all(value[0] is None for value in observed)
    if surface in {"executor", "reviewer-native"}:
        assert observed[0][1]["max_output_tokens_override"] == 131072
    assert rows and all(row["requestDeadlineEpoch"] is None for row in rows)
    assert all(value["max_tokens"] == 131072 for value in body)
    assert __import__("os").environ["MINICODE_MODEL_TIMEOUT"] == "300"


def test_new_diagnostic_policy_is_versioned_and_preserves_non_time_controls():
    assert diagnostic.PROTOCOL == "continuous-retained-task-diagnostic-v3"
    assert diagnostic.LIMITS == {
        "maxTokens": None,
        "maxModelCalls": None,
        "maxCostUsd": None,
        "wallSeconds": None,
        "maxOutputTokens": 131072,
        "requestTimeoutSeconds": None,
        "idleTimeoutSeconds": 120,
        "connectTimeoutSeconds": 30,
    }
    text = diagnostic.prompt_for_arm("unchanged original task", "explicit")
    assert text.startswith("unchanged original task")
    assert "8192" not in text and "300 秒" not in text and "131072" in text


@pytest.mark.parametrize("value", [False, 0, -1, float("inf"), float("nan")])
def test_optional_request_deadline_still_rejects_invalid_values(tmp_path, value):
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        replace(config(tmp_path), request_timeout_seconds=value)
