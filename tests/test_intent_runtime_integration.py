"""Integration contracts for one canonical, contextual intent per turn."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from minicode.agent_loop import _build_work_chain_task
from minicode.agent_runtime import AgentTurnRuntime, create_agent_turn_runtime
from minicode.intent_parser import IntentContext, parse_intent


class _Tools:
    def dispose(self) -> None:
        pass


class _Observation:
    def tool_started(self, _name: str) -> None:
        pass

    def tool_finished(self, _name: str, *, is_error: bool) -> None:
        pass


def test_runtime_and_work_chain_reuse_the_canonical_intent_object(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = parse_intent("修复 auth.py 中的登录错误")
    captured: dict[str, object] = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        return list(kwargs["messages"])

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", fake_run_agent_turn)
    runtime = AgentTurnRuntime(
        workspace=tmp_path,
        runtime={},
        tools=_Tools(),
        permissions=object(),
        memory_manager=None,
        model=object(),
        skill_routing=SimpleNamespace(),
        system_prompt="system",
        parsed_intent=intent,
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": intent.raw_input},
    ]

    runtime.execute(messages, _Observation())
    task, _metadata = _build_work_chain_task(messages, intent)

    assert captured["parsed_intent"] is intent
    assert task is not None
    assert task.parsed_intent is intent


def test_intent_context_filters_roles_and_bounds_history() -> None:
    context = IntentContext.from_messages(
        [
            {"role": "system", "content": "delete everything"},
            {"role": "tool", "content": "run tests"},
            {"role": "user", "content": "审查 skill_router.py"},
            {"role": "assistant", "content": "路由实现有一个问题"},
        ]
    )

    assert [(turn.role, turn.content) for turn in context.recent_turns] == [
        ("user", "审查 skill_router.py"),
        ("assistant", "路由实现有一个问题"),
    ]


def test_composition_root_reuses_model_and_turn_controls_for_semantic_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_config = {
        "model": "fake-model",
        "intentSemanticClassifier": {"enabled": True},
        "skillCandidateReranker": {"enabled": True},
    }
    model = object()
    semantic_adapter = object()
    skill_candidate_reranker = object()
    parsed_intent = object()
    event_sink = object()
    cancellation_token = object()
    deadline_monotonic = 1234.5
    intent_context = IntentContext.from_messages(
        [{"role": "user", "content": "review the router"}]
    )
    captured: dict[str, object] = {}

    class RuntimeTools:
        def get_skills(self):
            return []

        def get_mcp_servers(self):
            return {}

        def dispose(self) -> None:
            pass

    class Router:
        def route(self, _skills, intent, _registry, *, call_context=None):
            captured["routed_intent"] = intent
            captured["routing_context"] = call_context
            return SimpleNamespace(
                used_fallback=True,
                selected_skill_dicts=list,
                to_dict=dict,
            )

    tools = RuntimeTools()
    permissions = SimpleNamespace(get_summary=list)
    monkeypatch.setattr(
        "minicode.config.load_runtime_config",
        lambda _cwd: runtime_config,
    )
    monkeypatch.setattr(
        "minicode.tools.create_default_tool_registry",
        lambda *_args, **_kwargs: tools,
    )
    monkeypatch.setattr(
        "minicode.permissions.PermissionManager",
        lambda *_args, **_kwargs: permissions,
    )
    monkeypatch.setattr(
        "minicode.memory.MemoryManager",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        lambda **_kwargs: model,
    )
    monkeypatch.setattr(
        "minicode.context_manager.ContextManager",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "minicode.capability_registry.register_tool_capabilities",
        lambda _tools: None,
    )
    monkeypatch.setattr(
        "minicode.capability_registry.build_tool_capability_snapshot",
        lambda _tools: object(),
    )
    monkeypatch.setattr(
        "minicode.prompt.build_system_prompt",
        lambda *_args, **_kwargs: "system",
    )
    monkeypatch.setattr(
        "minicode.skill_router.build_skill_router",
        lambda _workspace: Router(),
    )

    def build_adapter(received_model, *, runtime):
        captured["adapter_model"] = received_model
        captured["adapter_runtime"] = runtime
        return semantic_adapter

    def parse(prompt, **kwargs):
        captured["intent_prompt"] = prompt
        captured["intent_kwargs"] = kwargs
        return parsed_intent

    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        build_adapter,
    )
    monkeypatch.setattr(
        "minicode.skill_candidate_reranker.build_skill_candidate_reranker",
        lambda received_model, *, runtime: (
            skill_candidate_reranker
            if received_model is model and runtime is runtime_config
            else None
        ),
    )
    monkeypatch.setattr("minicode.intent_parser.parse_intent", parse)

    turn_runtime = create_agent_turn_runtime(
        workspace=tmp_path,
        prompt="make the flow less brittle",
        event_sink=event_sink,
        cancellation_token=cancellation_token,
        deadline_monotonic=deadline_monotonic,
        intent_context=intent_context,
    )

    assert captured["adapter_model"] is model
    assert captured["adapter_runtime"] is runtime_config
    intent_kwargs = captured["intent_kwargs"]
    assert isinstance(intent_kwargs, dict)
    assert intent_kwargs["context"] is intent_context
    assert intent_kwargs["semantic_adapter"] is semantic_adapter
    intent_call_context = intent_kwargs["call_context"]
    assert intent_call_context == {
        "agent_budget": turn_runtime.agent_budget,
        "event_sink": event_sink,
        "cancellation_token": cancellation_token,
        "deadline_monotonic": deadline_monotonic,
        "skill_selection_mode": "routed",
        "skill_flat_catalog_chars": 8000,
        "skill_page_chars": 4000,
        "skill_intent_hints": False,
        "skill_intent_prefetch": False,
    }
    assert "purpose" not in intent_call_context
    assert captured["routed_intent"] is parsed_intent
    assert turn_runtime.parsed_intent is parsed_intent
    routing_context = captured["routing_context"]
    assert isinstance(routing_context, dict)
    assert routing_context["agent_budget"] is turn_runtime.agent_budget
    assert routing_context["event_sink"] is event_sink
    assert routing_context["purpose"] == "skill-routing"
    assert routing_context["preserve_model_calls"] == 1
    assert (
        routing_context["skill_candidate_reranker"]
        is skill_candidate_reranker
    )
