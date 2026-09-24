"""Subprocess isolation for write-capable ``general`` sub-agents.

Covers the hard boundary the in-process backend could not provide: a hung
model call inside a general sub-agent is killed with its whole process group
on deadline or cancellation, and the parent projects exactly one typed
terminal state.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

import minicode.subagent_worker as worker_module
import minicode.tools.task as task_module
from minicode.agent_budget import (
    AgentTurnBudget,
    SubprocessBudgetUsage,
)
from minicode.memory_provenance import (
    MAX_MEMORY_PROVENANCE_BATCHES,
    MemoryProvenanceBatch,
)
from minicode.run_journal import RunJournal
from minicode.subagent_journal import new_subagent_id
from minicode.subagent_result import extract_subagent_result
from minicode.subagent_supervisor import (
    SubagentProcessOutcome,
    run_subagent_process,
)
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import (
    SkillLoadBinding,
    SkillLoadPolicy,
    ToolContext,
    serialize_skill_load_policy,
)
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)

TURN_ID = "turn_" + "c" * 32
LEASE_NONCE = "a" * 64

ALPHA_SKILL_BINDING = SkillLoadBinding(
    qualified_name="project/alpha",
    source="project",
    content_digest="a" * 64,
    routing_identity_digest="b" * 64,
)
BETA_SKILL_BINDING = SkillLoadBinding(
    qualified_name="project/beta",
    source="project",
    content_digest="c" * 64,
    routing_identity_digest="d" * 64,
)
ALPHA_REPLACEMENT_BINDING = SkillLoadBinding(
    qualified_name=ALPHA_SKILL_BINDING.qualified_name,
    source=ALPHA_SKILL_BINDING.source,
    content_digest="e" * 64,
    routing_identity_digest="f" * 64,
)

_SLEEP_CHILD = "import time; time.sleep(60)"

# Fake child: publish a well-formed worker result, then exit.
_RESULT_CHILD = (
    "import json, sys; "
    "request_path = sys.argv[sys.argv.index('--request') + 1]; "
    "request = json.load(open(request_path)); "
    "path = sys.argv[sys.argv.index('--result') + 1]; "
    "open(path, 'w').write(json.dumps({"
    "'version': 1, 'status': 'completed', "
    "'final_message': 'subprocess done', 'report_content': None, "
    "'model_turns': 1, 'tool_calls': 0, 'result_truncated': False, "
    "'error': None, 'budget_usage': {"
    "'budgetUsageVersion': 2, "
    "'leaseId': request['budget_lease']['leaseId'], "
    "'leaseNonce': request['budget_lease']['leaseNonce'], "
    "'usedModelCalls': 1, 'inputTokens': 0, 'outputTokens': 0, "
    "'usedCostUsd': '0'}}))"
)

# Fake child: publish a completed result and then hang, modelling a late
# write racing the parent's kill.
_LATE_RESULT_CHILD = _RESULT_CHILD + "; import time; time.sleep(60)"

_TYPED_REPORT = {
    "resultVersion": 1,
    "summary": "Applied and verified the bounded change.",
    "files": [{"path": "minicode/tools/task.py", "action": "modified"}],
    "risks": ["Legacy embedders keep the thread backend semantics."],
    "verification": {
        "status": "passed",
        "checks": ["pytest tests/test_subagent_subprocess.py"],
    },
}

_SEMANTIC_OPERATION_ID = "modelop_" + "a" * 32


def _semantic_success_events(secret: str = "") -> list[dict[str, object]]:
    return [
        {
            "type": "model.started",
            "step": None,
            "payload": {
                "operationId": _SEMANTIC_OPERATION_ID,
                "purpose": "intent_classification",
                "prompt": secret,
            },
        },
        {
            "type": "model.completed",
            "step": None,
            "payload": {
                "operationId": _SEMANTIC_OPERATION_ID,
                "purpose": "intent_classification",
                "resultType": "assistant",
                "contentPresent": True,
                "toolCallCount": 0,
                "usage": {
                    "source": "estimated",
                    "inputTokens": 31,
                    "outputTokens": 7,
                    "cacheReadTokens": 0,
                    "cacheCreationTokens": 0,
                },
                "semanticStatus": "accepted",
                "semanticOperationCount": 1,
                "calibrationVersion": "bootstrap-conservative-v1",
                "calibrationProductionFit": False,
                "rawConfidenceMinimum": 0.9,
                "calibratedConfidenceMinimum": 0.82,
                "durationMs": 12,
                "output": secret,
            },
        },
        {
            "type": "model.costed",
            "step": None,
            "payload": {
                "costVersion": 1,
                "operationId": _SEMANTIC_OPERATION_ID,
                "status": "unavailable",
                "quality": "unavailable",
                "currency": "USD",
                "catalogId": "minicode-pricing-2026-07-17-v1",
                "reason": "model_unpriced",
                "purpose": "intent_classification",
                "exception": secret,
            },
        },
    ]


def _memory_batch(
    *,
    effect: str = "rendered",
    origin: str = "task_start",
    coverage: str = "complete",
) -> MemoryProvenanceBatch:
    versions = (
        (("project-1785082406796413000-b6ecf281", "a" * 64),)
        if coverage != "unknown"
        else ()
    )
    return MemoryProvenanceBatch(
        effect=effect,
        origin=origin,
        authority_versions=versions,
        receipt_id="b" * 32 if coverage != "unknown" else None,
        coverage=coverage,
    )


def _relay_envelope(
    *batches: MemoryProvenanceBatch,
) -> dict[str, object]:
    return {
        "relayVersion": 1,
        "semanticIntentEvents": [],
        "memoryProvenance": [batch.to_dict() for batch in batches],
    }


# Fake child carrying a typed hand-back report in its result payload.
_REPORT_CHILD = (
    "import json, sys; "
    f"report = json.dumps({_TYPED_REPORT!r}); "
    "request_path = sys.argv[sys.argv.index('--request') + 1]; "
    "request = json.load(open(request_path)); "
    "path = sys.argv[sys.argv.index('--result') + 1]; "
    "open(path, 'w').write(json.dumps({"
    "'version': 1, 'status': 'completed', "
    "'final_message': 'subprocess done', 'report_content': report, "
    "'model_turns': 2, 'tool_calls': 1, 'result_truncated': False, "
    "'error': None, 'budget_usage': {"
    "'budgetUsageVersion': 2, "
    "'leaseId': request['budget_lease']['leaseId'], "
    "'leaseNonce': request['budget_lease']['leaseNonce'], "
    "'usedModelCalls': 2, 'inputTokens': 0, 'outputTokens': 0, "
    "'usedCostUsd': '0'}}))"
)


def _request(tmp_path: Path, **overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "version": 2,
        "cwd": str(tmp_path),
        "prompt": "Do bounded work.",
        "subagent_id": "sub_" + "0" * 32,
        "result_key": "subagent_result_" + "0" * 32,
        "result_instruction": "",
        "max_turns": 3,
        "timeout_seconds": 30.0,
        "runtime": {"model": "fake"},
        "inject_memory": False,
        "semantic_observation_active": True,
        "parent_skill_load_policy": None,
        "budget_lease": {
            "budgetLeaseVersion": 2,
            "leaseId": 1,
            "leaseNonce": LEASE_NONCE,
            "maxTotalTokens": 3_000_000,
            "maxModelCalls": 6,
            "maxCostUsd": "5.00",
        },
    }
    request.update(overrides)
    return request


def _assert_process_group_gone(pid: int | None) -> None:
    assert pid is not None
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)


def test_supervisor_parses_a_completed_worker_result(tmp_path: Path) -> None:
    outcome = run_subagent_process(
        _request(tmp_path),
        command=[sys.executable, "-c", _RESULT_CHILD],
    )

    assert outcome.status == "completed"
    assert outcome.result is not None
    assert outcome.result["final_message"] == "subprocess done"
    assert outcome.result["model_turns"] == 1


def test_deadline_hard_kills_the_process_group(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = run_subagent_process(
        _request(tmp_path),
        deadline_monotonic=time.monotonic() + 0.3,
        command=[sys.executable, "-c", _SLEEP_CHILD],
    )

    assert outcome.status == "timed_out"
    # The kill is hard: a 60s sleeper returns in seconds, not minutes.
    assert time.monotonic() - started < 30
    _assert_process_group_gone(outcome.pid)


def test_cancellation_hard_kills_and_reports_cancelled(tmp_path: Path) -> None:
    token = TurnCancellationToken(TURN_ID)
    token.request()

    outcome = run_subagent_process(
        _request(tmp_path),
        cancellation_token=token,
        command=[sys.executable, "-c", _SLEEP_CHILD],
    )

    assert outcome.status == "cancelled"
    _assert_process_group_gone(outcome.pid)


def test_a_killed_workers_late_result_never_overrides_terminal_state(
    tmp_path: Path,
) -> None:
    outcome = run_subagent_process(
        _request(tmp_path),
        deadline_monotonic=time.monotonic() + 0.5,
        command=[sys.executable, "-c", _LATE_RESULT_CHILD],
    )

    # The child wrote a valid 'completed' result before the kill landed; the
    # terminal decision still wins exactly once.
    assert outcome.status == "timed_out"
    _assert_process_group_gone(outcome.pid)


def test_task_tool_projects_the_subprocess_typed_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        functools.partial(
            run_subagent_process,
            command=[sys.executable, "-c", _REPORT_CHILD],
        ),
    )

    result = task_module.task_tool.run(
        {
            "description": "subprocess contract",
            "prompt": "Apply the bounded change.",
            "agent_type": "general",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    structured = extract_subagent_result(result.output)
    assert result.ok is True
    assert structured is not None
    assert structured["agentType"] == "general"
    assert structured["outcome"] == "completed"
    assert structured["contractStatus"] == "reported"
    assert structured["summary"] == "Applied and verified the bounded change."
    assert structured["verification"]["status"] == "passed"


def test_task_tool_deadline_returns_a_timed_out_typed_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        functools.partial(
            run_subagent_process,
            command=[sys.executable, "-c", _SLEEP_CHILD],
        ),
    )
    payloads: list[dict[str, object]] = []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                payloads.append(payload)

    result = task_module.task_tool.run(
        {
            "description": "hung model call",
            "prompt": "Wait inside a stuck provider call.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=Sink(),
        ),
    )

    assert result.ok is False
    assert "sub_agent_timed_out" in result.output
    assert len(payloads) == 1
    assert payloads[0]["outcome"] == "timed_out"


def test_task_tool_cancellation_kills_and_records_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)

    def cancelling_supervisor(request, **kwargs) -> SubagentProcessOutcome:
        # Cancel after dispatch has begun: a token already requested at
        # validation time unwinds before any sub-agent exists, which is the
        # pre-existing early-raise path and records nothing.
        token.request()
        return run_subagent_process(
            request,
            command=[sys.executable, "-c", _SLEEP_CHILD],
            **kwargs,
        )

    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        cancelling_supervisor,
    )
    payloads: list[dict[str, object]] = []

    class Sink:
        def __init__(self) -> None:
            self.memory_records: list[MemoryProvenanceBatch] = []

        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                payloads.append(payload)

        def record_subagent_memory_provenance(
            self,
            _subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> None:
            self.memory_records.append(batch)

    sink = Sink()
    with pytest.raises(TurnCancellationRequested):
        task_module.task_tool.run(
            {
                "description": "cancelled hung call",
                "prompt": "Wait inside a stuck provider call.",
                "agent_type": "general",
            },
            ToolContext(
                cwd=str(tmp_path),
                _runtime={"model": "fake"},
                _event_sink=sink,
                _cancellation_token=token,
            ),
        )

    # The cancellation is no longer mis-recorded as a failure.
    assert len(payloads) == 1
    assert payloads[0]["outcome"] == "cancelled"
    assert sink.memory_records == list(
        task_module._unknown_subagent_memory_provenance()
    )


def test_sidecar_journal_accepts_the_new_terminal_outcomes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    sidecar = journal.open_subagent_journal(record.id)
    budget = AgentTurnBudget(max_model_calls=3)

    for outcome in ("timed_out", "cancelled"):
        subagent_id = new_subagent_id()
        sidecar.start(
            subagent_id=subagent_id,
            agent_type="general",
            max_turns=15,
            budget=budget.snapshot(),
        )
        sidecar.finish(
            subagent_id,
            outcome=outcome,
            model_turns=0,
            tool_calls=0,
            duration_ms=1,
            max_turns=15,
            result_truncated=False,
            budget=budget.snapshot(),
        )

    summaries = {
        summary.subagent_id: summary.outcome for summary in sidecar.list_runs()
    }
    assert sorted(summaries.values()) == ["cancelled", "timed_out"]


def _run_worker_in_process(
    tmp_path: Path,
    **request_overrides: object,
) -> tuple[int, dict[str, object]]:
    request_path = tmp_path / "worker-request.json"
    result_path = tmp_path / "worker-result.json"
    request_path.write_text(
        json.dumps(_request(tmp_path, **request_overrides)),
        encoding="utf-8",
    )
    original_cwd = os.getcwd()
    try:
        code = worker_module.run_worker(str(request_path), str(result_path))
    finally:
        os.chdir(original_cwd)
    return code, json.loads(result_path.read_text(encoding="utf-8"))


def test_worker_executes_a_turn_and_reports_the_typed_handback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        prompt = "\n".join(str(m.get("content", "")) for m in kwargs["messages"])
        match = re.search(r"result mailbox key `([^`]+)`", prompt)
        assert match is not None
        kwargs["subagent_mailbox"].write(
            match.group(1),
            json.dumps(_TYPED_REPORT),
            author="1",
        )
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "human hand-back"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, payload = _run_worker_in_process(tmp_path)

    assert code == 0
    assert payload["status"] == "completed"
    assert payload["final_message"] == "human hand-back"
    assert json.loads(payload["report_content"])["summary"] == (
        "Applied and verified the bounded change."
    )
    assert payload["model_turns"] == 1
    # Fail closed: the worker is non-interactive, so no prompt handler and no
    # user-interaction tools cross into the loop.
    assert captured["permissions"].prompt is None
    assert captured["allow_user_interaction"] is False
    assert captured["cancellation_token"] is None
    tool_names = {tool.name for tool in captured["tools"].list()}
    assert "task" not in tool_names
    assert "ask_user" not in tool_names
    assert "write_file" in tool_names


def test_worker_intersects_parent_policy_with_child_rerouting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    child_routing = {
        "selected": [
            {
                "name": "alpha",
                "qualified_name": ALPHA_REPLACEMENT_BINDING.qualified_name,
                "source": ALPHA_REPLACEMENT_BINDING.source,
                "content_digest": ALPHA_REPLACEMENT_BINDING.content_digest,
                "routing_identity_digest": (
                    ALPHA_REPLACEMENT_BINDING.routing_identity_digest
                ),
            },
        ],
        "selected_skills": [
            {
                "name": "alpha",
                "qualified_name": ALPHA_REPLACEMENT_BINDING.qualified_name,
                "source": ALPHA_REPLACEMENT_BINDING.source,
                "content_digest": ALPHA_REPLACEMENT_BINDING.content_digest,
                "routing_identity_digest": (
                    ALPHA_REPLACEMENT_BINDING.routing_identity_digest
                ),
            },
        ],
        "used_fallback": False,
        "prompt_mode": "routed",
        "authority_resolution": {},
    }
    monkeypatch.setattr(
        task_module,
        "_build_sub_agent_system_prompt",
        lambda **_kwargs: ("system", child_routing),
    )

    def fake_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA_SKILL_BINDING}),
        reason_code="routed-selection",
    )

    code, payload = _run_worker_in_process(
        tmp_path,
        parent_skill_load_policy=serialize_skill_load_policy(parent),
    )

    assert code == 0
    assert payload["status"] == "completed"
    effective = captured["skill_load_policy"]
    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allowed_bindings == frozenset()
    assert effective.permits(ALPHA_REPLACEMENT_BINDING) is False


def test_worker_routing_failure_preserves_parent_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        task_module,
        "_build_sub_agent_system_prompt",
        lambda **_kwargs: ("system", None),
    )

    def fake_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset(
            {ALPHA_SKILL_BINDING, BETA_SKILL_BINDING}
        ),
        denied_qualified_names=frozenset({"project/beta"}),
        reason_code="inventory-fallback",
    )

    code, payload = _run_worker_in_process(
        tmp_path,
        parent_skill_load_policy=serialize_skill_load_policy(parent),
    )

    assert code == 0
    assert payload["status"] == "completed"
    effective = captured["skill_load_policy"]
    assert effective != parent
    assert effective.reason_code == "child-routing-unavailable"
    assert effective.permits(BETA_SKILL_BINDING) is False


def test_worker_rejects_unbounded_parent_skill_policy(tmp_path: Path) -> None:
    malformed = {
        "version": 2,
        "allowed_bindings": [
            {
                "qualified_name": f"skill-{index}",
                "source": "project",
                "content_digest": f"{index:064x}"[-64:],
                "routing_identity_digest": f"{index + 1:064x}"[-64:],
            }
            for index in range(129)
        ],
        "denied_qualified_names": [],
        "reason_code": "routed-selection",
    }

    code, payload = _run_worker_in_process(
        tmp_path,
        parent_skill_load_policy=malformed,
    )

    assert code == 2
    assert payload["status"] == "error"
    assert "parent_skill_load_policy is invalid" in payload["error"]


def test_worker_missing_parent_skill_policy_field_degrades_to_deny_all(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    request["version"] = 1
    request.pop("parent_skill_load_policy")

    validated = worker_module._validate_request(request)

    policy = validated["parent_skill_load_policy"]
    assert isinstance(policy, SkillLoadPolicy)
    assert policy.allowed_bindings == frozenset()
    assert policy.reason_code == "parent-policy-unavailable"


def test_worker_v2_requires_parent_skill_policy_field(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.pop("parent_skill_load_policy")

    with pytest.raises(ValueError, match="parent_skill_load_policy"):
        worker_module._validate_request(request)


def test_worker_relays_only_a_bounded_content_free_semantic_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child result may carry semantic telemetry, never request content."""
    from minicode import intent_parser

    original_parse_intent = intent_parser.parse_intent
    secret = "customer-secret-must-not-cross-the-process-boundary"

    def fake_parse_intent(_text, *, call_context=None, **_kwargs):
        assert isinstance(call_context, dict)
        sink = call_context["event_sink"]
        for event in _semantic_success_events(secret):
            sink.emit(
                event["type"],
                step=event["step"],
                payload=event["payload"],
            )
        return original_parse_intent("Refactor this module")

    def fake_turn(**kwargs):
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(intent_parser, "parse_intent", fake_parse_intent)
    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, payload = _run_worker_in_process(tmp_path)

    assert code == 0
    relayed = payload["semantic_intent_events"]
    assert set(relayed) == {
        "relayVersion",
        "semanticIntentEvents",
        "memoryProvenance",
    }
    assert relayed["relayVersion"] == 1
    assert relayed["memoryProvenance"] == [
        batch.to_dict()
        for batch in task_module._unknown_subagent_memory_provenance()
    ]
    semantic_events = relayed["semanticIntentEvents"]
    assert [event["type"] for event in semantic_events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert len(semantic_events) == 3
    assert secret not in json.dumps(relayed, sort_keys=True)
    assert set(semantic_events[0]["payload"]) == {"operationId", "purpose"}
    assert semantic_events[1]["payload"]["semanticStatus"] == "accepted"
    assert len(json.dumps(relayed)) < 8_192


def test_worker_without_parent_sidecar_does_not_call_semantic_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport capture is not evidence unless the parent can persist it."""
    import minicode.intent_semantic_classifier as classifier_module

    class SemanticProvider:
        def __init__(self) -> None:
            self.runtime = {"model": "fake"}
            self.calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            raise AssertionError("unobserved semantic provider call")

    provider = SemanticProvider()
    classifier = classifier_module.BudgetedSemanticIntentClassifier(
        provider,
        config=classifier_module.IntentSemanticClassifierConfig(enabled=True),
    )
    monkeypatch.setattr(
        classifier_module,
        "build_semantic_intent_adapter",
        lambda _model, *, runtime: classifier,
    )

    def fake_turn(**kwargs):
        assert kwargs["event_sink"].observation_active is False
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, payload = _run_worker_in_process(
        tmp_path,
        prompt="Could you make this flow less brittle?",
        semantic_observation_active=False,
    )

    assert code == 0
    assert provider.calls == 0
    assert payload["semantic_intent_events"] == {
        "relayVersion": 1,
        "semanticIntentEvents": [],
        "memoryProvenance": [
            batch.to_dict()
            for batch in task_module._unknown_subagent_memory_provenance()
        ],
    }


def test_worker_rejects_non_boolean_semantic_observation_claim(
    tmp_path: Path,
) -> None:
    code, payload = _run_worker_in_process(
        tmp_path,
        semantic_observation_active="yes",
    )

    assert code == 2
    assert payload["status"] == "error"
    assert "semantic_observation_active is invalid" in payload["error"]


@pytest.mark.parametrize(
    "budget_lease",
    [
        None,
        {
            "budgetLeaseVersion": True,
            "leaseId": 1,
            "leaseNonce": LEASE_NONCE,
            "maxTotalTokens": 100,
            "maxModelCalls": 2,
            "maxCostUsd": "1",
        },
        {
            "budgetLeaseVersion": 2,
            "leaseId": 1,
            "leaseNonce": LEASE_NONCE,
            "maxTotalTokens": 100,
            "maxModelCalls": 2,
            "maxCostUsd": "1",
            "unexpected": "field",
        },
    ],
)
def test_worker_rejects_a_missing_or_malformed_parent_budget_lease(
    tmp_path: Path,
    budget_lease: object,
) -> None:
    code, payload = _run_worker_in_process(
        tmp_path,
        budget_lease=budget_lease,
    )

    assert code == 2
    assert payload["status"] == "error"
    assert "budget lease" in payload["error"]
    assert payload["budget_usage"] is None


def test_worker_semantic_classifier_and_agent_share_the_issued_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic routing is accounted before the Agent's own model call."""
    import minicode.intent_semantic_classifier as classifier_module
    from minicode.types import AgentStep, ModelUsage

    class SemanticProvider:
        def __init__(self) -> None:
            self.runtime = {"model": "gpt-4o"}
            self.catalog_model_key = "gpt-4o"

        def next(self, _messages, **_kwargs):
            return AgentStep(
                type="assistant",
                content='{"schemaVersion":1,"operations":[]}',
                usage=ModelUsage(
                    input_tokens=9,
                    output_tokens=3,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    source="provider",
                ),
            )

    classifier = classifier_module.BudgetedSemanticIntentClassifier(
        SemanticProvider(),
        config=classifier_module.IntentSemanticClassifierConfig(enabled=True),
    )
    monkeypatch.setattr(
        classifier_module,
        "build_semantic_intent_adapter",
        lambda _model, *, runtime: classifier,
    )
    monkeypatch.setattr(
        task_module,
        "_build_sub_agent_system_prompt",
        lambda **_kwargs: ("system", None),
    )

    def fake_turn(**kwargs):
        budget = kwargs["agent_budget"]
        reservation = budget.reserve_model_call(3)
        budget.record_model_call(
            input_tokens=3,
            output_tokens=1,
            reservation=reservation,
        )
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)
    code, payload = _run_worker_in_process(
        tmp_path,
        prompt="Could you make this flow less brittle?",
        semantic_observation_active=True,
        budget_lease={
            "budgetLeaseVersion": 2,
            "leaseId": 73,
            "leaseNonce": LEASE_NONCE,
            "maxTotalTokens": 1_000,
            "maxModelCalls": 2,
            "maxCostUsd": "1.00",
        },
    )

    assert code == 0
    usage = SubprocessBudgetUsage.from_dict(payload["budget_usage"])
    assert usage.lease_id == 73
    assert usage.used_model_calls == 2
    assert usage.input_tokens >= 3
    assert usage.output_tokens >= 1


def test_worker_cannot_exceed_the_parent_issued_model_call_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_module,
        "_build_sub_agent_system_prompt",
        lambda **_kwargs: ("system", None),
    )

    def over_budget_turn(**kwargs):
        budget = kwargs["agent_budget"]
        first = budget.reserve_model_call()
        budget.record_model_call(reservation=first)
        budget.reserve_model_call()
        raise AssertionError("the second reservation must not be admitted")

    monkeypatch.setattr(worker_module, "run_agent_turn", over_budget_turn)
    code, payload = _run_worker_in_process(
        tmp_path,
        semantic_observation_active=False,
        budget_lease={
            "budgetLeaseVersion": 2,
            "leaseId": 19,
            "leaseNonce": LEASE_NONCE,
            "maxTotalTokens": 100,
            "maxModelCalls": 1,
            "maxCostUsd": "1.00",
        },
    )

    assert code == 1
    assert payload["status"] == "budget_exceeded"
    usage = SubprocessBudgetUsage.from_dict(payload["budget_usage"])
    assert usage.lease_id == 19
    assert usage.used_model_calls == 1


def test_actual_worker_abstention_reaches_the_parent_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise classifier -> worker payload -> parent journal end to end."""
    import minicode.intent_semantic_classifier as classifier_module
    from minicode.types import AgentStep

    class AbstainingProvider:
        def __init__(self) -> None:
            self.runtime = {"model": "fake"}
            self.calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            return AgentStep(
                type="assistant",
                content='{"schemaVersion":1,"operations":[]}',
            )

    provider = AbstainingProvider()
    classifier = classifier_module.BudgetedSemanticIntentClassifier(
        provider,
        config=classifier_module.IntentSemanticClassifierConfig(enabled=True),
    )

    def build_classifier(_model, *, runtime):
        assert isinstance(runtime, dict)
        return classifier

    def fake_turn(**kwargs):
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(
        classifier_module,
        "build_semantic_intent_adapter",
        build_classifier,
    )
    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, worker_payload = _run_worker_in_process(
        tmp_path,
        prompt="Could you make this flow less brittle?",
        semantic_observation_active=True,
    )

    assert code == 0
    assert provider.calls == 1
    assert (
        worker_payload["semantic_intent_events"]["semanticIntentEvents"][1]["payload"][
            "semanticStatus"
        ]
        == "abstained"
    )

    workspace = tmp_path / "parent-workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "parent-home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    sidecar = journal.open_subagent_journal(record.id)

    class ParentSink:
        def emit(self, _event_type, *, step=None, payload=None) -> None:
            return None

        def open_subagent_journal(self):
            return sidecar

        def record_written_memory_ids(self, _entry_ids) -> None:
            return None

    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        lambda _request, **_kwargs: SubagentProcessOutcome(
            status="completed",
            result=worker_payload,
            error=None,
            duration_ms=1,
            pid=None,
        ),
    )

    result = task_module.task_tool.run(
        {
            "description": "abstention telemetry",
            "prompt": "Apply a bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _event_sink=ParentSink(),
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is True
    summaries = journal.list_subagent_runs(record.id)
    events = journal.list_subagent_events(record.id, summaries[0].subagent_id)
    assert [event.type for event in events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert events[1].payload["semanticStatus"] == "abstained"


def test_parent_marks_semantic_observation_inactive_without_a_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, object] = {}
    parent_policy = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA_SKILL_BINDING}),
        reason_code="routed-selection",
    )

    def fake_supervisor(request, **_kwargs) -> SubagentProcessOutcome:
        captured_request.update(request)
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "done",
                "report_content": None,
                "model_turns": 1,
                "tool_calls": 0,
                "result_truncated": False,
                "error": None,
            },
            error=None,
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)

    result = task_module.task_tool.run(
        {
            "description": "unobserved semantic boundary",
            "prompt": "Apply a bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _skill_load_policy=parent_policy,
        ),
    )

    assert result.ok is True
    assert captured_request["semantic_observation_active"] is False
    assert captured_request["parent_skill_load_policy"] == (
        serialize_skill_load_policy(parent_policy)
    )
    assert captured_request["parent_skill_load_policy"]["version"] == 3
    assert "path" not in json.dumps(
        captured_request["parent_skill_load_policy"],
        sort_keys=True,
    )


def test_unbounded_parent_issues_finite_defaults_and_exactly_settles_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = AgentTurnBudget()
    captured_lease: dict[str, object] = {}

    def fake_supervisor(request, **_kwargs) -> SubagentProcessOutcome:
        captured_lease.update(request["budget_lease"])
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "done",
                "report_content": None,
                "model_turns": 2,
                "tool_calls": 0,
                "result_truncated": False,
                "error": None,
                "budget_usage": {
                    "budgetUsageVersion": 2,
                    "leaseId": request["budget_lease"]["leaseId"],
                    "leaseNonce": request["budget_lease"]["leaseNonce"],
                    "usedModelCalls": 2,
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "usedCostUsd": "0.25",
                },
            },
            error=None,
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)
    result = task_module.task_tool.run(
        {
            "description": "leased subprocess",
            "prompt": "Apply a bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_budget=budget,
        ),
    )

    assert result.ok is True
    assert captured_lease["budgetLeaseVersion"] == 2
    assert captured_lease["leaseId"] == 1
    assert isinstance(captured_lease["leaseNonce"], str)
    assert len(captured_lease["leaseNonce"]) == 64
    assert captured_lease["maxTotalTokens"] == 3_000_000
    assert captured_lease["maxModelCalls"] == 18
    assert captured_lease["maxCostUsd"] == "5.00"
    snapshot = budget.snapshot()
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_model_calls == 2
    assert snapshot.used_input_tokens == 100
    assert snapshot.used_output_tokens == 20
    assert snapshot.used_cost_usd == "0.25"


def test_completed_zero_usage_receipt_cannot_release_a_nonzero_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=3,
        max_cost_usd="0.50",
    )

    def fake_supervisor(request, **_kwargs) -> SubagentProcessOutcome:
        lease = request["budget_lease"]
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "done",
                "report_content": None,
                "model_turns": 1,
                "tool_calls": 0,
                "result_truncated": False,
                "error": None,
                "budget_usage": {
                    "budgetUsageVersion": 2,
                    "leaseId": lease["leaseId"],
                    "leaseNonce": lease["leaseNonce"],
                    "usedModelCalls": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "usedCostUsd": "0",
                },
            },
            error=None,
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)
    result = task_module.task_tool.run(
        {
            "description": "zero receipt",
            "prompt": "Apply a bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_budget=budget,
        ),
    )

    assert result.ok is True
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 2
    assert snapshot.used_total_tokens == 100
    assert snapshot.used_cost_usd == "0.50"


@pytest.mark.parametrize("terminal_status", ["timed_out", "result_missing"])
def test_hard_kill_or_missing_result_conservatively_charges_the_full_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    budget = AgentTurnBudget(
        max_total_tokens=100,
        max_model_calls=5,
        max_cost_usd="0.50",
    )

    def fake_supervisor(request, **_kwargs) -> SubagentProcessOutcome:
        forged_result = None
        if terminal_status == "timed_out":
            forged_result = {
                "status": "completed",
                "budget_usage": {
                    "budgetUsageVersion": 2,
                    "leaseId": request["budget_lease"]["leaseId"],
                    "leaseNonce": request["budget_lease"]["leaseNonce"],
                    "usedModelCalls": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "usedCostUsd": "0",
                },
            }
        return SubagentProcessOutcome(
            status=terminal_status,
            result=forged_result,
            error="child produced no trustworthy receipt",
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)
    result = task_module.task_tool.run(
        {
            "description": "unreported child usage",
            "prompt": "Apply a bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_budget=budget,
        ),
    )

    assert result.ok is False
    snapshot = budget.snapshot()
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_model_calls == 4
    assert snapshot.used_total_tokens == 100
    assert snapshot.used_input_tokens == 100
    assert snapshot.used_output_tokens == 0
    assert Decimal(snapshot.used_cost_usd) == Decimal("0.50")


def test_parent_revalidates_and_persists_semantic_events_in_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent owns sidecar sequence and strips every non-contract field."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    sidecar = journal.open_subagent_journal(record.id)
    secret = "provider-output-and-exception-must-not-persist"

    class ParentSink:
        def emit(self, _event_type, *, step=None, payload=None) -> None:
            return None

        def open_subagent_journal(self):
            return sidecar

        def record_written_memory_ids(self, _entry_ids) -> None:
            return None

    def fake_supervisor(child_request, **_kwargs) -> SubagentProcessOutcome:
        assert child_request["semantic_observation_active"] is True
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "done",
                "report_content": None,
                "model_turns": 2,
                "tool_calls": 0,
                "result_truncated": False,
                "error": None,
                "semantic_intent_events": _semantic_success_events(secret),
            },
            error=None,
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)

    result = task_module.task_tool.run(
        {
            "description": "semantic telemetry relay",
            "prompt": "Apply a bounded refactor.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _event_sink=ParentSink(),
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is True
    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    events = journal.list_subagent_events(record.id, summaries[0].subagent_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.type for event in events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    serialized = json.dumps([event.to_dict() for event in events], sort_keys=True)
    assert secret not in serialized
    persisted_keys = {key for event in events for key in event.payload}
    assert "prompt" not in persisted_keys
    assert "output" not in persisted_keys
    assert "exception" not in persisted_keys


def test_parent_drops_oversized_or_partial_semantic_event_batches() -> None:
    success = _semantic_success_events()

    assert task_module._normalize_semantic_intent_relay_events(success[:1]) == ()
    assert (
        task_module._normalize_semantic_intent_relay_events([*success, success[0]])
        == ()
    )

    mismatched = json.loads(json.dumps(success))
    mismatched[-1]["payload"]["operationId"] = "modelop_" + "b" * 32
    assert task_module._normalize_semantic_intent_relay_events(mismatched) == ()

    failure = [
        success[0],
        {
            "type": "model.failed",
            "step": None,
            "payload": {
                "operationId": _SEMANTIC_OPERATION_ID,
                "purpose": "intent_classification",
                "failureKind": "timeout",
                "durationMs": 10,
                "exception": "provider exception content",
            },
        },
    ]
    normalized_failure = task_module._normalize_semantic_intent_relay_events(failure)
    assert [event["type"] for event in normalized_failure] == [
        "model.started",
        "model.failed",
    ]
    assert "exception" not in normalized_failure[1]["payload"]

    child_sink = task_module._SubagentJournalSink(
        None,
        "sub_" + "0" * 32,
        capture_semantic_intent_events=True,
        semantic_observation_active=True,
    )
    for event in [*success, success[0]]:
        child_sink.emit(
            event["type"],
            step=event["step"],
            payload=event["payload"],
        )
    assert child_sink.semantic_intent_events == {
        "relayVersion": 1,
        "semanticIntentEvents": [],
        "memoryProvenance": [
            batch.to_dict()
            for batch in task_module._unknown_subagent_memory_provenance()
        ],
    }


def test_subagent_sink_binds_memory_provenance_to_trusted_parent_identity() -> None:
    subagent_id = "sub_" + "1" * 32

    class ParentSink:
        run_id = "run_" + "2" * 32

        def __init__(self) -> None:
            self.records: list[tuple[str, MemoryProvenanceBatch]] = []

        def record_subagent_memory_provenance(
            self,
            observed_subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> None:
            self.records.append((observed_subagent_id, batch))

    parent = ParentSink()
    sink = task_module._SubagentJournalSink(
        None,
        subagent_id,
        parent_memory_provenance_sink=parent,
    )
    batch = _memory_batch()

    sink.record_memory_provenance(batch)
    sink.record_memory_provenance(batch)

    assert sink.subagent_id == subagent_id
    assert sink.parent_run_id == parent.run_id
    assert sink.feedback_observation_id == f"subagent:{subagent_id}"
    assert not hasattr(sink, "run_id")
    assert parent.records == [(subagent_id, batch)]
    assert sink.semantic_intent_events["memoryProvenance"] == [
        batch.to_dict(),
        task_module._unknown_subagent_memory_provenance()[1].to_dict(),
    ]
    assert "subagentId" not in json.dumps(sink.semantic_intent_events)
    with pytest.raises(TypeError, match="MemoryProvenanceBatch"):
        sink.record_memory_provenance(batch.to_dict())  # type: ignore[arg-type]


def test_subagent_sink_retries_until_parent_acknowledges_provenance() -> None:
    subagent_id = "sub_" + "9" * 32

    class FlakyParent:
        def __init__(self) -> None:
            self.attempts = 0
            self.records: list[MemoryProvenanceBatch] = []

        def record_subagent_memory_provenance(
            self,
            observed_subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> bool:
            assert observed_subagent_id == subagent_id
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("transient parent journal failure")
            self.records.append(batch)
            return True

    parent = FlakyParent()
    sink = task_module._SubagentJournalSink(
        None,
        subagent_id,
        parent_memory_provenance_sink=parent,
    )
    batch = _memory_batch()

    assert sink.record_memory_provenance(batch) is False
    assert sink.record_memory_provenance(batch) is True
    assert parent.attempts == 2
    assert parent.records == [batch]


def test_valid_one_effect_relay_closes_the_other_effect_as_unknown() -> None:
    subagent_id = "sub_" + "8" * 32

    class Parent:
        def __init__(self) -> None:
            self.records: list[MemoryProvenanceBatch] = []

        def record_subagent_memory_provenance(
            self,
            observed_subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> bool:
            assert observed_subagent_id == subagent_id
            self.records.append(batch)
            return True

    parent = Parent()
    sink = task_module._SubagentJournalSink(
        None,
        subagent_id,
        parent_memory_provenance_sink=parent,
    )
    rendered = _memory_batch(effect="rendered", origin="task_start")

    covered = task_module._replay_semantic_intent_events(
        _relay_envelope(rendered),
        sink,
    )
    assert covered == frozenset({"rendered"})
    task_module._record_unknown_subagent_memory_provenance(
        sink,
        effects=frozenset({"written"}),
    )
    sink.flush_memory_provenance(final=True)

    assert parent.records == [
        rendered,
        task_module._unknown_subagent_memory_provenance()[1],
    ]


def test_parent_replays_valid_memory_batches_before_failed_child_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _memory_batch(effect="written", origin="reflection")

    class ParentSink:
        def __init__(self) -> None:
            self.records: list[tuple[str, MemoryProvenanceBatch]] = []

        def emit(self, _event_type, *, step=None, payload=None) -> None:
            return None

        def record_subagent_memory_provenance(
            self,
            subagent_id: str,
            observed: MemoryProvenanceBatch,
        ) -> None:
            self.records.append((subagent_id, observed))

    parent = ParentSink()
    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        lambda _request, **_kwargs: SubagentProcessOutcome(
            status="failed",
            result={
                "version": 1,
                "status": "failed",
                "typed_status": "failed",
                "final_message": "child failed after reflection",
                "report_content": None,
                "model_turns": 1,
                "tool_calls": 0,
                "result_truncated": False,
                "error": "failed",
                "semantic_intent_events": _relay_envelope(batch),
            },
            error="failed",
            duration_ms=1,
            pid=None,
        ),
    )

    result = task_module.task_tool.run(
        {
            "description": "failed child provenance",
            "prompt": "Inspect then fail.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=parent,
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is False
    assert [record[1] for record in parent.records] == [
        batch,
        task_module._unknown_subagent_memory_provenance()[0],
    ]


@pytest.mark.parametrize("status", ["timed_out", "result_missing"])
def test_missing_subprocess_relay_records_unknown_memory_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    class ParentSink:
        def __init__(self) -> None:
            self.records: list[MemoryProvenanceBatch] = []

        def emit(self, _event_type, *, step=None, payload=None) -> None:
            return None

        def record_subagent_memory_provenance(
            self,
            _subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> None:
            self.records.append(batch)

    parent = ParentSink()
    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        lambda _request, **_kwargs: SubagentProcessOutcome(
            status=status,
            result=None,
            error="worker result unavailable",
            duration_ms=1,
            pid=None,
        ),
    )

    result = task_module.task_tool.run(
        {
            "description": "missing child provenance",
            "prompt": "Inspect in an isolated child.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=parent,
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is False
    assert parent.records == list(task_module._unknown_subagent_memory_provenance())


def test_malformed_or_oversized_child_relay_degrades_to_unknown() -> None:
    subagent_id = "sub_" + "3" * 32

    class ParentSink:
        def __init__(self) -> None:
            self.records: list[MemoryProvenanceBatch] = []

        def record_subagent_memory_provenance(
            self,
            observed_subagent_id: str,
            batch: MemoryProvenanceBatch,
        ) -> None:
            assert observed_subagent_id == subagent_id
            self.records.append(batch)

    for hostile in (
        {
            **_relay_envelope(_memory_batch()),
            "subagentId": "sub_" + "4" * 32,
        },
        {
            "relayVersion": 1,
            "semanticIntentEvents": [],
            "memoryProvenance": [_memory_batch().to_dict()]
            * (MAX_MEMORY_PROVENANCE_BATCHES + 1),
        },
    ):
        parent = ParentSink()
        sink = task_module._SubagentJournalSink(
            None,
            subagent_id,
            parent_memory_provenance_sink=parent,
        )

        known = task_module._replay_semantic_intent_events(hostile, sink)
        if not known:
            task_module._record_unknown_subagent_memory_provenance(sink)

        assert known is None
        assert parent.records == list(task_module._unknown_subagent_memory_provenance())


def test_cooperative_deadline_can_relay_observed_memory_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _memory_batch()

    class ParentSink:
        def __init__(self) -> None:
            self.records: list[MemoryProvenanceBatch] = []

        def emit(self, _event_type, *, step=None, payload=None) -> None:
            return None

        def record_subagent_memory_provenance(
            self,
            _subagent_id: str,
            observed: MemoryProvenanceBatch,
        ) -> None:
            self.records.append(observed)

    parent = ParentSink()
    monkeypatch.setattr(
        task_module,
        "run_subagent_process",
        lambda _request, **_kwargs: SubagentProcessOutcome(
            status="deadline_exceeded",
            result={
                "version": 1,
                "status": "deadline_exceeded",
                "final_message": "",
                "report_content": None,
                "model_turns": 1,
                "tool_calls": 0,
                "result_truncated": False,
                "error": "deadline",
                "semantic_intent_events": _relay_envelope(batch),
            },
            error="deadline",
            duration_ms=1,
            pid=None,
        ),
    )

    result = task_module.task_tool.run(
        {
            "description": "cooperative deadline provenance",
            "prompt": "Inspect until the inner deadline.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=parent,
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is False
    assert parent.records == [
        batch,
        task_module._unknown_subagent_memory_provenance()[1],
    ]


def test_worker_maps_its_inner_deadline_to_a_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deadline_turn(**kwargs):
        raise TaskDeadlineExceeded(0.001)

    monkeypatch.setattr(worker_module, "run_agent_turn", deadline_turn)

    code, payload = _run_worker_in_process(tmp_path)

    assert code == 1
    assert payload["status"] == "deadline_exceeded"
    assert "deadline" in payload["error"]
    assert payload["semantic_intent_events"] == {
        "relayVersion": 1,
        "semanticIntentEvents": [],
        "memoryProvenance": [
            batch.to_dict()
            for batch in task_module._unknown_subagent_memory_provenance()
        ],
    }


def test_worker_preserves_validated_parent_run_identity_for_memory_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_run_id = "run_" + "7" * 32

    def fake_turn(**kwargs):
        assert kwargs["event_sink"].parent_run_id == parent_run_id
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, payload = _run_worker_in_process(
        tmp_path,
        parent_run_id=parent_run_id,
    )

    assert code == 0
    assert payload["status"] == "completed"


def test_worker_rejects_malformed_parent_run_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parent_run_id"):
        worker_module._validate_request(
            _request(tmp_path, parent_run_id="run_not-trusted")
        )


def test_worker_module_writes_a_typed_error_without_model_config(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        json.dumps(_request(tmp_path, runtime={"model": ""})),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "minicode.subagent_worker",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ],
        capture_output=True,
        timeout=60,
    )

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert completed.returncode == 1
    assert payload["status"] == "error"
    assert "parent_model_missing" in payload["error"]


def test_supervisor_reports_a_missing_result_with_the_stderr_tail(
    tmp_path: Path,
) -> None:
    outcome = run_subagent_process(
        _request(tmp_path),
        command=[
            sys.executable,
            "-c",
            "import sys; print('boom-detail', file=sys.stderr); sys.exit(3)",
        ],
    )

    assert outcome.status == "result_missing"
    assert outcome.error is not None
    assert "boom-detail" in outcome.error


def test_task_tool_surfaces_worker_failures_without_success_wrapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_supervisor(request, **_kwargs) -> SubagentProcessOutcome:
        return SubagentProcessOutcome(
            status="error",
            result={"error": "SubagentModelRoutingError: parent_model_missing"},
            error="SubagentModelRoutingError: parent_model_missing",
            duration_ms=1,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)

    result = task_module.task_tool.run(
        {
            "description": "worker failure",
            "prompt": "Attempt the bounded change.",
            "agent_type": "general",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    assert result.ok is False
    assert "parent_model_missing" in result.output
    assert extract_subagent_result(result.output) is None


def test_worker_reports_its_attributed_token_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child's process-local budget split rides the result payload so the
    parent journal can attribute tokens to this sub-agent."""

    def fake_turn(**kwargs):
        budget = kwargs["agent_budget"]
        reservation = budget.reserve_model_call(100)
        budget.record_model_call(
            input_tokens=111, output_tokens=22, reservation=reservation
        )
        budget.record_model_call(input_tokens=333, output_tokens=44)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_turn)

    code, payload = _run_worker_in_process(tmp_path)

    assert code == 0
    assert payload["status"] == "completed"
    assert payload["input_tokens"] == 444
    assert payload["output_tokens"] == 66
    assert payload["budget_usage"] == {
        "budgetUsageVersion": 2,
        "leaseId": 1,
        "leaseNonce": LEASE_NONCE,
        "usedModelCalls": 1,
        "inputTokens": 444,
        "outputTokens": 66,
        "usedCostUsd": "0.0",
    }


def test_worker_reports_partial_tokens_on_its_inner_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consumption before the cooperative deadline is still attributed."""

    def deadline_turn(**kwargs):
        kwargs["agent_budget"].record_model_call(input_tokens=50, output_tokens=5)
        raise TaskDeadlineExceeded(0.001)

    monkeypatch.setattr(worker_module, "run_agent_turn", deadline_turn)

    code, payload = _run_worker_in_process(tmp_path)

    assert code == 1
    assert payload["status"] == "deadline_exceeded"
    assert payload["input_tokens"] == 50
    assert payload["output_tokens"] == 5
    assert payload["budget_usage"]["leaseId"] == 1


@pytest.mark.parametrize("uncertain", [False, True])
def test_worker_omits_exact_receipt_for_unsettled_or_uncertain_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uncertain: bool,
) -> None:
    def incomplete_turn(**kwargs):
        budget = kwargs["agent_budget"]
        reservation = budget.reserve_model_call(0 if not uncertain else 10)
        if uncertain:
            budget.fail_model_call(reservation, charge_estimate=True)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(worker_module, "run_agent_turn", incomplete_turn)
    code, payload = _run_worker_in_process(tmp_path)

    assert code == 0
    assert payload["status"] == "completed"
    assert payload["budget_usage"] is None
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None


def test_worker_error_payload_keeps_tokens_absent(
    tmp_path: Path,
) -> None:
    """A request that never reaches the loop has no attributable tokens."""
    request_path = tmp_path / "worker-request.json"
    result_path = tmp_path / "worker-result.json"
    request = _request(tmp_path)
    request["version"] = 99
    request_path.write_text(json.dumps(request), encoding="utf-8")

    code = worker_module.run_worker(str(request_path), str(result_path))

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 2
    assert payload["status"] == "error"
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None
