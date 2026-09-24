"""Production entrypoints must opt the top-level loop into bounded completion."""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import minicode.main as main_module
import minicode.tui.input_handler as input_handler_module
from minicode.agent_runtime import AgentTurnRuntime
from minicode.permissions import PermissionManager
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.tui.state import ScreenState, TtyAppArgs


def _tools() -> SimpleNamespace:
    return SimpleNamespace(
        get_skills=lambda: [],
        get_mcp_servers=lambda: [],
        list=lambda: [],
        dispose=lambda: None,
    )


@pytest.mark.parametrize("allow_user_interaction", [True, False])
def test_prepared_runtime_enables_bounded_completion_when_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_user_interaction: bool,
) -> None:
    calls: list[dict[str, Any]] = []

    def capture_turn(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [*kwargs["messages"], {"role": "assistant", "content": "completed"}]

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", capture_turn)
    runtime = AgentTurnRuntime(
        workspace=tmp_path,
        runtime={},
        tools=_tools(),
        permissions=PermissionManager(str(tmp_path)),
        memory_manager=None,
        model=object(),
        skill_routing=None,
        system_prompt="system",
        allow_user_interaction=allow_user_interaction,
    )
    messages = [{"role": "user", "content": "Fix the regression"}]

    with observe_run(
        workspace=tmp_path, source="tui", title="bounded runtime", enabled=False
    ) as observation:
        result = runtime.execute(messages, observation)

    assert len(calls) == 1
    assert calls[0].get("enable_bounded_completion") is True
    assert calls[0]["allow_user_interaction"] is allow_user_interaction
    assert calls[0]["messages"] == messages
    assert result[-1] == {"role": "assistant", "content": "completed"}


def test_classic_cli_enables_bounded_completion_for_a_user_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise argument parsing and the real non-TTY main dispatch."""
    calls: list[dict[str, Any]] = []
    tools = _tools()
    permissions = PermissionManager(str(tmp_path))
    profile = SimpleNamespace(
        load_merged=lambda: None,
        global_path=SimpleNamespace(exists=lambda: False),
        project_path=SimpleNamespace(exists=lambda: False),
    )
    routing = SimpleNamespace(
        used_fallback=True, selected=[], selected_skills=[], total_skills=0
    )

    def capture_turn(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [*kwargs["messages"], {"role": "assistant", "content": "completed"}]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["minicode"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("Fix the regression\n"))
    monkeypatch.setattr(main_module, "_configure_stdio_for_unicode", lambda: None)
    monkeypatch.setattr(main_module, "_setup_cli_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "maybe_handle_management_command", lambda *_args: False)
    monkeypatch.setattr(main_module, "load_runtime_config", lambda _cwd: {})
    monkeypatch.setattr(main_module, "create_default_tool_registry", lambda *_args, **_kwargs: tools)
    monkeypatch.setattr(main_module, "PermissionManager", lambda *_args, **_kwargs: permissions)
    monkeypatch.setattr(main_module, "create_model_adapter", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "build_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(main_module, "load_history_entries", lambda: [])
    monkeypatch.setattr(main_module, "save_history_entries", lambda _history: None)
    monkeypatch.setattr(main_module, "_route_skills_for_prompt", lambda *_args, **_kwargs: ([], routing))
    monkeypatch.setattr(main_module, "run_agent_turn", capture_turn)
    monkeypatch.setattr(main_module, "observe_run", lambda **kwargs: observe_run(**kwargs, enabled=False))
    monkeypatch.setattr("minicode.logging_config.get_logger", logging.getLogger)
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "minicode.skill_candidate_reranker.build_skill_candidate_reranker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "minicode.memory.MemoryManager",
        lambda **_kwargs: SimpleNamespace(handle_user_memory_input=lambda _value: None),
    )
    monkeypatch.setattr("minicode.user_profile.UserProfileManager", lambda **_kwargs: profile)
    monkeypatch.setattr(
        "minicode.state.create_app_store",
        lambda **_kwargs: SimpleNamespace(get_state=lambda: SimpleNamespace(session_id="new")),
    )

    main_module.main()

    assert len(calls) == 1
    assert calls[0].get("enable_bounded_completion") is True
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[0]["messages"][-1] == {"role": "user", "content": "Fix the regression"}
    assert "completed" in capsys.readouterr().out


def test_interactive_tui_enables_bounded_completion_in_background_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def capture_turn(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [*kwargs["messages"], {"role": "assistant", "content": "completed"}]

    monkeypatch.setattr(input_handler_module, "run_agent_turn", capture_turn)
    monkeypatch.setattr(input_handler_module, "save_history_entries", lambda _history: None)
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "minicode.skill_candidate_reranker.build_skill_candidate_reranker",
        lambda *_args, **_kwargs: None,
    )
    state = ScreenState(
        input="Fix the regression",
        session=SimpleNamespace(session_id="bounded_completion_session"),
    )
    args = TtyAppArgs(
        runtime={"model": "fake"},
        tools=ToolRegistry([]),
        model=object(),
        messages=[{"role": "system", "content": "system"}],
        cwd=str(tmp_path),
        permissions=PermissionManager(str(tmp_path)),
        run_observation_enabled=False,
    )

    assert input_handler_module._handle_input(args, state, lambda: None) is False
    assert state.agent_thread is not None
    state.agent_thread.join(timeout=5)

    assert not state.agent_thread.is_alive()
    assert len(calls) == 1
    assert calls[0].get("enable_bounded_completion") is True
    assert calls[0]["cwd"] == str(tmp_path)
    assert state.agent_result["messages"][-1] == {
        "role": "assistant", "content": "completed"
    }
    assert state.is_busy is False
