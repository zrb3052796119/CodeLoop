"""Conversation runtime must retire stale Skill bodies before the next model call."""
from types import SimpleNamespace

from minicode.agent_runtime import AgentTurnRuntime
from minicode.tooling import ToolContext
from minicode.tools import create_default_tool_registry


def test_runtime_retires_old_body_using_the_already_routed_catalog(tmp_path, monkeypatch):
    skill = tmp_path / ".mini-code" / "skills" / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: alpha\ndescription: alpha workflow\n---\nOLD BODY\n")
    old_tools = create_default_tool_registry(str(tmp_path), include_mcp=False)
    old_load = old_tools.execute("load_skill", {"name": "alpha"}, ToolContext(cwd=str(tmp_path)))
    assert old_load.ok
    old_tools.dispose()
    skill.write_text("---\nname: alpha\ndescription: alpha workflow\n---\nCURRENT BODY\n")
    tools = create_default_tool_registry(str(tmp_path), include_mcp=False)
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return kwargs["messages"]

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", execute)
    # The runtime was assembled/routed already. Do not silently rescan here.
    monkeypatch.setattr(tools, "refresh_skill_catalog", lambda: (_ for _ in ()).throw(AssertionError("rescan")))
    runtime = AgentTurnRuntime(
        workspace=tmp_path, runtime={}, tools=tools, permissions=object(),
        memory_manager=None, model=object(), skill_routing=SimpleNamespace(),
        system_prompt="system",
    )
    history = [
        {"role": "assistant_tool_call", "toolUseId": "load-1", "toolName": "load_skill", "input": {"name": "alpha"}},
        {"role": "tool_result", "toolUseId": "load-1", "toolName": "load_skill", "content": old_load.output, "isError": False},
        {"role": "user", "content": "Continue with the updated workflow"},
    ]
    try:
        result = runtime.execute(history, object())
        assert result[0] == history[0]
        assert result[1]["toolUseId"] == "load-1"
        assert result[1]["isError"] is True
        assert "OLD BODY" not in result[1]["content"]
        assert "skill_context_invalidated" in result[1]["content"]
        assert history[1]["isError"] is False
        assert captured["tools"] is tools
    finally:
        runtime.dispose()
