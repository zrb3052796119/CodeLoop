"""No provider calls: exercise real adapter against scripted HTTP streams."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error

import pytest

from minicode import openai_adapter as adapter
from minicode.model_call_control import ModelCallDeadlineExceeded
from scripts.evaluation_transport import (
    EvaluationBudgetExceeded,
    EvaluationIdleTimeout,
    EvaluationStreamError,
    EvaluationTransport,
    SharedLedger,
    TransportConfig,
    evaluation_context,
    route_fingerprint,
)


RUNTIME = {
    "model": "glm-evaluation-test", "openaiBaseUrl": "https://example.invalid/v1",
    "openaiApiKey": "test-not-a-real-key", "modelTimeoutSeconds": 0.01,
    "modelMaxRetries": 4, "maxOutputTokens": 4096,
}
MESSAGES = [{"role": "user", "content": "Respond briefly."}]


def config(tmp_path, **overrides):
    return TransportConfig(
        ledger_path=str(tmp_path / "requests.sqlite"),
        expected_model=RUNTIME["model"],
        expected_route_sha256=route_fingerprint(RUNTIME),
        deadline_epoch=time.time() + 30,
        **overrides,
    )


def event(data):
    return b"data:" + json.dumps(data).encode() + b"\n\n"


DONE = b"data: [DONE]\n\n"
TEXT = {"id": "response-123", "choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]}
USAGE = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28, "prompt_tokens_details": {"cached_tokens": 10}, "completion_tokens_details": {"reasoning_tokens": 3}}


class ScriptedResponse:
    status = 200
    headers = {"x-request-id": "request-123", "authorization": "must-not-be-logged"}

    def __init__(self, chunks, delay=0):
        self.chunks = iter(chunks)
        self.delay = delay
        self.closed = threading.Event()

    def read1(self, _amount):
        self.closed.wait(self.delay)
        chunk = next(self.chunks, b"")
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def close(self):
        self.closed.set()


def install_open(monkeypatch, response, captured=None):
    def open_http(request, *, timeout):
        if captured is not None:
            captured.append((json.loads(request.data), timeout))
        return response
    monkeypatch.setattr(adapter, "open_verified_url", open_http)


def test_forces_stream_with_usage_and_preserves_tools_reasoning_and_cache(tmp_path, monkeypatch):
    chunks = [
        event({"choices": [{"delta": {"reasoning_content": "think"}}]}),
        event({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {"name": "read_file", "arguments": '{"path":'}}]}}]}),
        event({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a.py"}'}}]}, "finish_reason": "tool_calls"}], "usage": USAGE}),
        DONE,
    ]
    captured = []
    install_open(monkeypatch, ScriptedResponse(chunks), captured)
    original = adapter.OpenAIModelAdapter.next
    cfg = config(tmp_path)
    with EvaluationTransport(cfg) as transport:
        with evaluation_context(subagent_id="sub_" + "1" * 32, role="analyst"):
            step = adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
        transport.ledger.export_jsonl(tmp_path / "calls.jsonl")
    assert adapter.OpenAIModelAdapter.next is original
    assert captured[0][0]["stream"] is True
    assert captured[0][0]["stream_options"] == {"include_usage": True}
    # Configured production 10ms timer must not remain the body timer.
    assert captured[0][1] > 1
    assert step.reasoningContent == "think"
    assert step.calls == [{"id": "t1", "toolName": "read_file", "input": {"path": "a.py"}}]
    assert asdict(step.usage) == {"input_tokens": 20, "output_tokens": 8, "cache_read_tokens": 10, "cache_creation_tokens": None, "source": "provider"}
    row = snapshot["requests"][0]
    assert row["role"] == "analyst" and row["subagentId"] == "sub_" + "1" * 32
    assert row["providerRequestId"] == "request-123"
    assert row["providerUsage"]["completion_tokens_details"]["reasoning_tokens"] == 3
    assert row["ttftSeconds"] >= 0 and row["lastEventSeconds"] >= row["ttftSeconds"]
    assert row["streamDone"] is True
    assert snapshot["usageComplete"] and snapshot["chargedTokens"] == 28
    exported = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["event"] for r in exported] == ["started", "completed"]
    assert "test-not-a-real-key" not in json.dumps(snapshot)
    assert "must-not-be-logged" not in json.dumps(snapshot)
    assert "https://" not in json.dumps(snapshot)


def test_missing_partial_usage_stays_unknown_and_reservation_remains(tmp_path, monkeypatch):
    install_open(monkeypatch, ScriptedResponse([event({**TEXT, "usage": {"prompt_tokens": 20}}), DONE]))
    with EvaluationTransport(config(tmp_path)) as transport:
        step = adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert step.usage.source == "unavailable"
    assert step.usage.output_tokens is None
    assert snapshot["unknownUsageCalls"] == 1 and not snapshot["usageComplete"]
    assert snapshot["requests"][0]["chargedTokens"] == snapshot["requests"][0]["reservedTokens"]


def queued_stream_during_ledger_update(monkeypatch, schedule):
    """Use real queue/thread/SQLite, but control receive time and consumer lag."""
    from minicode import model_call_control
    from scripts import evaluation_transport as transport_module

    clock = SimpleNamespace(now=time.monotonic())
    clock.started = clock.now
    local_time = SimpleNamespace(monotonic=lambda: clock.now, time=time.time, sleep=time.sleep)
    for module in (transport_module, adapter, model_call_control):
        monkeypatch.setattr(module, "time", local_time)
    consumer_held = threading.Event()
    eof_queued = threading.Event()
    observed = []
    original_queue = transport_module.queue

    class ObservedQueue(original_queue.Queue):
        def put(self, item, *args, **kwargs):
            super().put(item, *args, **kwargs)
            observed.append(item)
            if item[0] == b"" or item[2] is not None:
                eof_queued.set()

    monkeypatch.setattr(transport_module, "queue", SimpleNamespace(
        Queue=ObservedQueue, Empty=original_queue.Empty, Full=original_queue.Full,
    ))

    class ScheduledResponse(ScriptedResponse):
        def read1(self, _amount):
            if observed:
                assert consumer_held.wait(3), "consumer did not enter the first durable event"
            advance, chunk = next(self.chunks)
            clock.now += advance
            if isinstance(chunk, BaseException):
                raise chunk
            return chunk

    response = ScheduledResponse(schedule)
    captured = []
    install_open(monkeypatch, response, captured)
    original_update = SharedLedger.update

    def update(ledger, call, values):
        result = original_update(ledger, call, values)
        if "firstContentSeconds" in values and not consumer_held.is_set():
            consumer_held.set()
            assert eof_queued.wait(3), "reader did not queue the complete scripted stream"
        return result

    monkeypatch.setattr(SharedLedger, "update", update)
    return clock, response, captured, observed


@pytest.mark.parametrize("done_bytes, eof_gap", [(DONE, 0.03), (DONE, 0.2), (b"data: [DONE]", 0.03)])
def test_active_stream_exceeds_old_fixed_timer_but_respects_idle(tmp_path, monkeypatch, done_bytes, eof_gap):
    clock, response, captured, observed = queued_stream_during_ledger_update(monkeypatch, [
        (0.03, event(TEXT)), (0.03, b": heartbeat\n\n"),
        (0.03, event({"usage": USAGE})), (0.03, done_bytes), (eof_gap, b""),
    ])
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.1, request_timeout_seconds=1)) as transport:
        result = adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert result.content == "hello"
    assert snapshot["completed"] == len(captured) == 1
    assert clock.now - clock.started == pytest.approx(0.12 + eof_gap)
    assert clock.now - clock.started > RUNTIME["modelTimeoutSeconds"]
    assert [item[0] for item in observed] == [event(TEXT), b": heartbeat\n\n", event({"usage": USAGE}), done_bytes, b""]
    row = snapshot["requests"][0]
    assert row["firstByteSeconds"] == pytest.approx(0.03)
    assert row["lastByteSeconds"] == pytest.approx(0.12)
    assert row["streamDone"] and snapshot["chargedTokens"] == 28
    assert response.closed.wait(1)


@pytest.mark.parametrize("terminal, gap, error", [
    (DONE, 0.11, EvaluationIdleTimeout),
    (b"", 0.11, EvaluationIdleTimeout),
    (b"", 0.03, EvaluationStreamError),
    (RuntimeError("reader failed"), 0.03, RuntimeError),
    (RuntimeError("reader failed"), 0.11, EvaluationIdleTimeout),
])
def test_queued_terminal_preserves_real_receive_gap_and_eof_contract(tmp_path, monkeypatch, terminal, gap, error):
    schedule = [(0.03, event({**TEXT, "usage": USAGE})), (gap, terminal)]
    if terminal and not isinstance(terminal, BaseException):
        schedule.append((0.01, b""))
    _clock, response, captured, observed = queued_stream_during_ledger_update(monkeypatch, schedule)
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.1, request_timeout_seconds=1)) as transport:
        with pytest.raises(error) as raised:
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    if error is RuntimeError:
        assert raised.value is terminal
    row = snapshot["requests"][0]
    assert snapshot["failed"] == len(captured) == 1
    assert row["errorClass"] == error.__name__ and row["errorPhase"] == "stream"
    assert row["usage"]["output_tokens"] == 8 and not row.get("streamDone")
    assert [item[0] for item in observed] == [None if isinstance(chunk, BaseException) else chunk for _delay, chunk in schedule]
    assert response.closed.wait(1)


def test_unterminated_done_cannot_use_late_eof_to_bypass_idle(tmp_path, monkeypatch):
    _clock, response, captured, _observed = queued_stream_during_ledger_update(monkeypatch, [
        (0.03, event({**TEXT, "usage": USAGE})), (0.03, b"data: [DONE]"), (0.11, b""),
    ])
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.1, request_timeout_seconds=1)) as transport:
        with pytest.raises(EvaluationIdleTimeout):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert snapshot["failed"] == len(captured) == 1
    assert not snapshot["requests"][0].get("streamDone")
    assert response.closed.wait(1)


def test_absolute_deadline_precedes_already_queued_done_and_idle(tmp_path, monkeypatch):
    clock, response, captured, observed = queued_stream_during_ledger_update(monkeypatch, [
        (0.03, event({**TEXT, "usage": USAGE})), (0.03, b": heartbeat\n\n"),
        (0.03, DONE), (0.03, b""),
    ])
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.05, request_timeout_seconds=1)) as transport:
        with pytest.raises(ModelCallDeadlineExceeded):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES, deadline_monotonic=clock.started + 0.1)
        snapshot = transport.ledger.snapshot()
    row = snapshot["requests"][0]
    assert snapshot["failed"] == len(captured) == 1
    assert row["errorClass"] == "ModelCallDeadlineExceeded" and row["errorPhase"] == "stream"
    assert row["usage"]["output_tokens"] == 8 and not row.get("streamDone")
    assert len(observed) == 4 and response.closed.wait(1)


def test_idle_timeout_keeps_received_usage_and_never_retries(tmp_path, monkeypatch):
    class StallingResponse(ScriptedResponse):
        def read1(self, amount):
            if getattr(self, "sent", False):
                self.closed.wait(3)
                return b""
            self.sent = True
            return event({**TEXT, "usage": USAGE})

    captured = []
    response = StallingResponse([])
    install_open(monkeypatch, response, captured)
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.05)) as transport:
        started = time.monotonic()
        with pytest.raises(EvaluationIdleTimeout):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert time.monotonic() - started < 1
    assert len(captured) == 1
    assert snapshot["failed"] == 1 and snapshot["providerUsageCalls"] == 1
    assert snapshot["requests"][0]["usage"]["output_tokens"] == 8
    assert response.closed.wait(1)


def test_total_deadline_and_caller_deadline_win_over_active_heartbeats(tmp_path, monkeypatch):
    response = ScriptedResponse([b": heartbeat\n\n"] * 100, delay=0.015)
    install_open(monkeypatch, response)
    with EvaluationTransport(config(tmp_path, idle_timeout_seconds=0.2, request_timeout_seconds=0.3)) as transport:
        started = time.monotonic()
        with pytest.raises(ModelCallDeadlineExceeded):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES, deadline_monotonic=started + 0.09)
        snapshot = transport.ledger.snapshot()
    assert time.monotonic() - started < 0.5
    assert snapshot["unknownUsageCalls"] == 1
    assert snapshot["requests"][0]["errorClass"] == "ModelCallDeadlineExceeded"


@pytest.mark.parametrize("chunks", [
    [event(TEXT)],  # Missing DONE is not an apparently completed answer.
    [event({"error": {"message": "provider secret diagnostic"}})],
    [b"data: broken\n\n"],
])
def test_bad_or_truncated_stream_is_failed_with_unknown_usage(tmp_path, monkeypatch, chunks):
    install_open(monkeypatch, ScriptedResponse(chunks))
    with EvaluationTransport(config(tmp_path)) as transport:
        with pytest.raises(EvaluationStreamError):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert snapshot["failed"] == snapshot["unknownUsageCalls"] == 1
    assert "provider secret diagnostic" not in json.dumps(snapshot)


def test_model_route_mismatch_is_rejected_before_http_admission(tmp_path, monkeypatch):
    captured = []
    install_open(monkeypatch, ScriptedResponse([]), captured)
    with EvaluationTransport(config(tmp_path)) as transport:
        wrong = {**RUNTIME, "model": "other"}
        with pytest.raises(RuntimeError, match="route drift"):
            adapter.OpenAIModelAdapter(wrong, None).next(MESSAGES)
        assert transport.ledger.snapshot()["started"] == 0
    assert captured == []


def test_shared_ledger_calls_tokens_unknown_and_config_drift(tmp_path):
    cfg = config(tmp_path, max_model_calls=3, max_total_tokens=100)
    ledger = SharedLedger(cfg)
    call = ledger.start({}, 60)
    ledger.update(call, {"event": "failed"})
    with pytest.raises(EvaluationBudgetExceeded, match="token"):
        SharedLedger(cfg).start({}, 50)
    # Receipt can arrive before completion and survives failure/process exit.
    ledger.update(call, {"usage": {"input_tokens": 10, "output_tokens": 5, "source": "provider", "cache_read_tokens": None}})
    SharedLedger(cfg).start({}, 50)
    SharedLedger(cfg).start({}, 10)
    with pytest.raises(EvaluationBudgetExceeded, match="request"):
        ledger.start({}, 0)
    with pytest.raises(ValueError, match="configuration drift"):
        SharedLedger(replace(cfg, max_model_calls=5))
    assert ledger.snapshot()["chargedTokens"] == 75


def test_request_admission_is_atomic_across_independent_python_processes(tmp_path):
    cfg = config(tmp_path, max_model_calls=3)
    ledger = SharedLedger(cfg)
    root = str(Path(__file__).resolve().parents[1])
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.evaluation_transport import SharedLedger, TransportConfig, EvaluationBudgetExceeded
ledger = SharedLedger(TransportConfig(**json.loads(sys.argv[2])))
try:
    call = ledger.start({'actorId': 'child'}, 10)
except EvaluationBudgetExceeded:
    pass
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, root, json.dumps(asdict(cfg))], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(7)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
    snapshot = ledger.snapshot()
    assert snapshot["started"] == snapshot["inFlight"] == snapshot["unknownUsageCalls"] == 3
    assert len({r["pid"] for r in snapshot["requests"]}) == 3
    assert snapshot["chargedTokens"] == 30
    assert snapshot["admissionRejectedCount"] == 4
    assert snapshot["admissionRejectedByReason"] == {"deadline": 0, "request_budget": 4, "token_budget": 0}
    assert [r["rejection"] for r in snapshot["admissionRejections"]] == [1, 2, 3, 4]
    assert len({r["pid"] for r in snapshot["admissionRejections"]}) == 4


@pytest.mark.parametrize("reason", ["deadline", "request_budget", "token_budget"])
def test_admission_rejections_commit_separately_without_usage_or_private_content(tmp_path, reason):
    cfg = config(tmp_path, max_model_calls=2 if reason == "token_budget" else 1, max_total_tokens=100)
    if reason == "deadline":
        cfg = replace(cfg, deadline_epoch=time.time() - 1)
    ledger = SharedLedger(cfg)
    if reason != "deadline":
        ledger.start({}, 90)
    before = ledger.snapshot()
    metadata = {
        "actorId": "sub_" + "3" * 32, "subagentId": "sub_" + "3" * 32,
        "role": "executor", "agentType": "general", "model": RUNTIME["model"],
        "routeSha256": cfg.expected_route_sha256,
        "parentToolOperationId": "toolop_test", "estimatedInputTokens": 15,
        "maxOutputTokens": 5, "prompt": "PRIVATE_PROMPT", "headers": {"Authorization": "PRIVATE_KEY"},
        "body": "PRIVATE_BODY", "credential": "PRIVATE_CREDENTIAL",
    }
    with pytest.raises(EvaluationBudgetExceeded):
        ledger.start(metadata, 20)
    # Read using a new SQLite connection after the exception: the rejection
    # must have committed, rather than rolling back with the raised error.
    with sqlite3.connect(cfg.ledger_path) as db:
        raw = json.loads(db.execute("SELECT data FROM admission_rejections").fetchone()[0])
    assert raw["reason"] == reason and raw["event"] == "admission_rejected"
    assert raw["reservedTokens"] == 20 and raw["currentRequests"] == before["started"]
    assert raw["currentChargedTokens"] == before["chargedTokens"]
    assert raw["actorId"] == metadata["actorId"] and raw["role"] == "executor"
    assert raw["pid"] == os.getpid() and raw["rejectedEpoch"] > 0
    assert not any(key in raw for key in ("usage", "providerUsage", "call", "prompt", "headers", "body", "credential"))
    assert "PRIVATE_" not in json.dumps(raw)
    after = SharedLedger(cfg).snapshot()
    assert after["admissionRejectedCount"] == 1
    assert after["admissionRejectedByReason"][reason] == 1
    for key in ("requests", "started", "completed", "failed", "inFlight", "chargedTokens",
                "providerUsageCalls", "unknownUsageCalls", "usageComplete", "cacheUsageComplete",
                "inputTokensKnown", "outputTokensKnown", "cacheReadTokensKnown"):
        assert after[key] == before[key]
    ledger.export_jsonl(tmp_path / "calls.jsonl")
    assert "admission_rejected" not in (tmp_path / "calls.jsonl").read_text()


def test_real_agent_budget_can_admit_a_call_that_transport_rejects_before_http(tmp_path, monkeypatch):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn
    from minicode.tooling import ToolRegistry

    captured = []
    receipt = {**USAGE, "prompt_tokens": 4000, "total_tokens": 4008}
    install_open(monkeypatch, ScriptedResponse([event({**TEXT, "usage": receipt}), DONE]), captured)
    budget = AgentTurnBudget(max_total_tokens=5000, max_model_calls=3)
    cfg = config(tmp_path, max_total_tokens=5000, max_model_calls=3)
    tools = ToolRegistry([])
    with EvaluationTransport(cfg) as transport:
        model = adapter.OpenAIModelAdapter(dict(RUNTIME), tools)
        def turn():
            return run_agent_turn(
                model=model, tools=tools, messages=MESSAGES, cwd=str(tmp_path),
                agent_budget=budget, enable_work_chain=False, max_steps=1,
            )
        turn()
        assert budget.snapshot().used_model_calls == 1
        assert budget.snapshot().used_total_tokens == 4008
        messages = turn()
        snapshot = transport.ledger.snapshot()
    assert any("EvaluationBudgetExceeded" in str(m.get("content")) for m in messages)
    assert len(captured) == snapshot["started"] == snapshot["completed"] == 1
    assert snapshot["admissionRejectedCount"] == 1
    rejection = snapshot["admissionRejections"][0]
    assert rejection["reason"] == "token_budget" and rejection["role"] == "main"
    assert rejection["currentRequests"] == 1 and rejection["currentChargedTokens"] == 4008
    assert rejection["currentChargedTokens"] + rejection["reservedTokens"] > cfg.max_total_tokens
    final = budget.snapshot()
    assert final.used_model_calls == snapshot["started"] + snapshot["admissionRejectedCount"] == 2
    assert final.used_total_tokens == snapshot["chargedTokens"] == 4008
    assert final.used_input_tokens == snapshot["inputTokensKnown"] == 4000
    assert final.used_output_tokens == snapshot["outputTokensKnown"] == 8
    assert final.reserved_total_tokens == 0
    assert snapshot["usageComplete"] and snapshot["cacheUsageComplete"]
    assert snapshot["unknownUsageCalls"] == 0


def test_task_binding_uses_actual_leaf_id_and_logical_role(tmp_path, monkeypatch):
    from minicode.tools import task
    from minicode import subagent_model_routing
    from types import SimpleNamespace

    install_open(monkeypatch, ScriptedResponse([event({**TEXT, "usage": USAGE}), DONE]))
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda runtime, agent_type, *, tools: adapter.OpenAIModelAdapter(runtime, tools))

    def run_leaf(input_data, context):
        identity = input_data.get("_subagent_id") or task.new_subagent_id()
        model = subagent_model_routing.create_subagent_model_adapter(RUNTIME, "plan", tools=None)
        return identity, model.next(MESSAGES)

    monkeypatch.setattr(task, "_run", run_leaf)
    with EvaluationTransport(config(tmp_path)) as transport:
        identity, _ = task._run({"agent_type": "plan"}, SimpleNamespace(_logical_agent_role="analyst"))
        row = transport.ledger.snapshot()["requests"][0]
    assert row["subagentId"] == row["actorId"] == identity
    assert row["role"] == "analyst" and row["agentType"] == "plan"


def test_subprocess_launcher_uses_explicit_bootstrap_without_changing_environment(tmp_path, monkeypatch):
    from minicode import subagent_supervisor
    from minicode.tools import task
    calls = []
    monkeypatch.setattr(subagent_supervisor, "run_subagent_process", lambda request, **kwargs: calls.append((request, kwargs)))
    with EvaluationTransport(config(tmp_path)):
        task.run_subagent_process({"subagent_id": "sub_" + "2" * 32}, deadline_monotonic=time.monotonic() + 2)
    command = calls[0][1]["command"]
    assert command[0] == sys.executable and command[2] == "--worker-bootstrap"
    raw = json.loads(Path(command[3]).read_text())
    assert raw["expected_model"] == RUNTIME["model"]
    assert "test-not-a-real-key" not in json.dumps(raw)


def test_open_failure_is_counted_once_without_internal_retry(tmp_path, monkeypatch):
    attempts = []

    def fail_open(request, *, timeout):
        attempts.append(timeout)
        raise urllib.error.URLError("must not copy provider exception text")

    monkeypatch.setattr(adapter, "open_verified_url", fail_open)
    with EvaluationTransport(config(tmp_path)) as transport:
        with pytest.raises(urllib.error.URLError):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        row = transport.ledger.snapshot()["requests"][0]
    assert len(attempts) == 1
    assert row["errorPhase"] == "headers" and row["errorClass"] == "URLError"
    assert row["usage"]["source"] == "unavailable"
    assert "must not copy" not in json.dumps(row)


def test_worker_bootstrap_installs_transport_before_real_worker_entry(tmp_path, monkeypatch):
    from scripts.evaluation_transport import _worker_bootstrap
    from minicode import subagent_worker

    cfg = config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(asdict(cfg)))
    request_path = tmp_path / "request.json"
    actor = "sub_" + "9" * 32
    request_path.write_text(json.dumps({"subagent_id": actor}))
    install_open(monkeypatch, ScriptedResponse([event({**TEXT, "usage": USAGE}), DONE]))

    def worker_main(argv):
        adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        assert argv == ["--request", str(request_path), "--result", "unused"]
        return 7

    monkeypatch.setattr(subagent_worker, "main", worker_main)
    assert _worker_bootstrap([str(config_path), "--request", str(request_path), "--result", "unused"]) == 7
    row = SharedLedger(cfg).snapshot()["requests"][0]
    assert row["subagentId"] == row["actorId"] == actor
    assert row["role"] == "general"


def test_stream_byte_fragments_preserve_unicode_and_cancellation(tmp_path, monkeypatch):
    text = {"choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}], "usage": USAGE}
    payload = b"data: " + json.dumps(text, ensure_ascii=False).encode() + b"\n\n" + DONE
    install_open(monkeypatch, ScriptedResponse([bytes([byte]) for byte in payload]))
    with EvaluationTransport(config(tmp_path)):
        assert adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES).content == "你好"

    class Cancelled(Exception):
        pass

    class Cancellation:
        def raise_if_requested(self):
            if flag.is_set():
                raise Cancelled()

    flag = threading.Event()
    response = ScriptedResponse([b": heartbeat\n\n"] * 100, delay=0.02)
    install_open(monkeypatch, response)
    timer = threading.Timer(0.08, flag.set)
    timer.start()
    try:
        with EvaluationTransport(config(tmp_path / "cancel")) as transport:
            with pytest.raises(Cancelled):
                adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES, cancellation_token=Cancellation())
            row = transport.ledger.snapshot()["requests"][0]
        assert row["errorClass"] == "Cancelled" and row["errorPhase"] == "stream"
    finally:
        timer.cancel()


def test_persists_wire_tool_names_without_private_schema_or_headers(tmp_path, monkeypatch):
    from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
    secret = "schema-description-must-not-be-retained"
    tool = ToolDefinition(name="edit_file", description=secret,
        input_schema={"type": "object", "description": secret},
        validator=lambda v: v, run=lambda v,c: ToolResult(ok=True, output="unused"))
    install_open(monkeypatch, ScriptedResponse([event({**TEXT,"usage":USAGE}),DONE]))
    with EvaluationTransport(config(tmp_path)) as transport:
        adapter.OpenAIModelAdapter(dict(RUNTIME), ToolRegistry([tool])).next(MESSAGES)
        row=transport.ledger.snapshot()["requests"][0]
    assert row["toolNames"] == ["edit_file"]
    assert row["reasoningEffort"] is None
    assert secret not in json.dumps(row)
    assert RUNTIME["openaiApiKey"] not in json.dumps(row)


def test_ledger_records_effort_from_actual_wire_body(tmp_path, monkeypatch):
    runtime={**RUNTIME,"model":"glm-5.3-flash", "openaiBaseUrl":"https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        "_isolatedOpenAIConfig":True, "_resolvedOpenAIProvider":"custom", "_supportedReasoningEfforts":("low","high","max")}
    cfg=config(tmp_path);cfg=replace(cfg,expected_model=runtime["model"],expected_route_sha256=route_fingerprint(runtime))
    install_open(monkeypatch, ScriptedResponse([event({**TEXT,"usage":USAGE}),DONE]))
    with EvaluationTransport(cfg) as transport:
        adapter.OpenAIModelAdapter(runtime,None).next(MESSAGES,reasoning_effort_override="high")
        row=transport.ledger.snapshot()["requests"][0]
    assert row["reasoningEffort"] == "high"
    assert row["toolNames"] == []
