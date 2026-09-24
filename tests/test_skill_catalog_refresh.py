from __future__ import annotations

from pathlib import Path

import pytest

import minicode.main as main_module
import minicode.tui.input_handler as input_handler
from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.skill_router import skill_load_policy_for_routing
from minicode.tooling import ToolContext, ToolDefinition, ToolResult
from minicode.tools import create_default_tool_registry
from minicode.tui.state import ScreenState, TtyAppArgs


def _write_skill(root: Path, name: str, version: str) -> Path:
    path = root / '.mini-code' / 'skills' / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nname: {name}\ndescription: {name} {version} workflow\n---\n'
        f'Instructions-{name}-{version}\n',
        encoding='utf-8',
    )
    return path


def _route(root, tools):
    return main_module._route_skills_for_prompt(
        str(root), tools, 'Inspect the project',
        call_context={'skill_selection_mode': 'model'},
    )


def _execute(root, tools, routing, name, data):
    return tools.execute(
        name, data, ToolContext(
            cwd=str(root),
            _skill_load_policy=skill_load_policy_for_routing(routing),
        ),
    )


def test_cli_refreshes_add_modify_delete_together(tmp_path):
    root = tmp_path.resolve()
    alpha = _write_skill(root, 'alpha', 'v1')
    tools = create_default_tool_registry(str(root), include_mcp=False)
    old_catalog, old_route = _route(root, tools)
    captured_loader = tools.find('load_skill')
    frozen = tools.filtered(lambda tool: True)
    ordinary_tool = tools.find('read_file')

    _write_skill(root, 'alpha', 'v2')
    _write_skill(root, 'beta', 'v1')
    # Files cannot silently expand an already routed turn's tool snapshot.
    assert tools.get_skills() == old_catalog
    assert not _execute(root, tools, old_route, 'load_skill', {'name': 'beta'}).ok
    assert not _execute(root, tools, old_route, 'load_skill', {'name': 'alpha'}).ok

    catalog, routing = _route(root, tools)
    assert {s['name'] for s in catalog} == {'alpha', 'beta'}
    assert next(s for s in catalog if s['name'] == 'alpha')['description'] == 'alpha v2 workflow'
    assert tools.find('read_file') is ordinary_tool
    assert tools.find('load_skill') is not captured_loader
    assert frozen.get_skills() == old_catalog
    assert not _execute(root, frozen, routing, 'load_skill', {'name': 'beta'}).ok
    for name, arguments in [
        ('load_skill', {'name': 'alpha'}),
        ('browse_skill_directory', {'directory_names': ['']}),
        ('search_skills', {'query': 'alpha'}),
    ]:
        result = _execute(root, tools, routing, name, arguments)
        assert result.ok, result.output
        assert 'v2' in result.output
    assert _execute(root, tools, routing, 'load_skill', {'name': 'beta'}).ok

    alpha.unlink()
    catalog, routing = _route(root, tools)
    assert [s['name'] for s in catalog] == ['beta']
    assert not _execute(root, tools, routing, 'load_skill', {'name': 'alpha'}).ok
    for name, arguments in [
        ('browse_skill_directory', {'directory_names': ['']}),
        ('search_skills', {'query': 'alpha'}),
    ]:
        result = _execute(root, tools, routing, name, arguments)
        assert 'alpha v2 workflow' not in result.output


def test_refresh_preserves_mcp_connection_and_warm_adapter_schemas(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    _write_skill(root, 'alpha', 'v1')
    events = []
    external = ToolDefinition(
        name='mcp__fixture__read', description='Test external tool',
        input_schema={'type': 'object'}, validator=lambda value: value,
        run=lambda data, context: ToolResult(ok=True, output='unused'),
    )

    def create_mcp(**kwargs):
        events.append('connect')
        return {'tools': [external], 'servers': [], 'dispose': lambda: events.append('dispose')}

    monkeypatch.setattr('minicode.tools.create_mcp_backed_tools', create_mcp)
    tools = create_default_tool_registry(str(root))
    runtime = {'model': 'fixture', 'apiKey': 'offline-fixture'}
    adapters = [OpenAIModelAdapter(runtime, tools), AnthropicModelAdapter(runtime, tools)]
    for adapter in adapters:
        adapter._get_serialized_tools()
    _write_skill(root, 'beta', 'v1')
    catalog, routing = _route(root, tools)
    assert {s['name'] for s in catalog} == {'alpha', 'beta'}
    assert events == ['connect']
    assert tools.find(external.name) is external
    for adapter in adapters:
        # These tool schemas are catalog-independent. Keeping a warm cache
        # must serialize exactly the same surface as a fresh adapter.
        assert adapter._get_serialized_tools() == type(adapter)(runtime, tools)._get_serialized_tools()
    assert _execute(root, tools, routing, 'load_skill', {'name': 'beta'}).ok
    tools.dispose()
    assert events == ['connect', 'dispose']


def _tui_turn(root, tools, history, monkeypatch):
    captured = {}

    def run_turn(**kwargs):
        captured.update(kwargs)
        return [*kwargs['messages'], {'role': 'assistant', 'content': 'done'}]

    monkeypatch.setattr(input_handler, 'run_agent_turn', run_turn)
    args = TtyAppArgs(
        runtime={'model': 'fixture', 'skillSelection': {'mode': 'model'}},
        tools=tools, model=object(), messages=history, cwd=str(root),
        permissions=PermissionManager(str(root)), run_observation_enabled=False,
    )
    state = ScreenState(input='Inspect the project')
    assert input_handler._handle_input(args, state, lambda: None) is False
    state.agent_thread.join(timeout=5)
    assert not state.agent_thread.is_alive()
    assert captured, state.agent_result
    return captured, state.agent_result['messages']


@pytest.mark.parametrize('change', ['update', 'delete', 'resume'])
def test_tui_refresh_retires_old_body_preserving_tool_batch(tmp_path, monkeypatch, change):
    root = tmp_path.resolve()
    alpha = _write_skill(root, 'alpha', 'v1')
    tools = create_default_tool_registry(str(root), include_mcp=False)
    first, _ = _tui_turn(root, tools, [{'role': 'system', 'content': 'sys'}], monkeypatch)
    result = tools.execute('load_skill', {'name': 'alpha'}, ToolContext(
        cwd=str(root), _skill_load_policy=first['skill_load_policy'],
    ))
    assert result.ok
    history = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'Load the alpha skill'},
        {'role': 'assistant_tool_call', 'toolUseId': 'load', 'toolName': 'load_skill',
         'input': {'name': 'alpha'}, 'assistantTurnId': 'batch', 'content': 'Loading',
         'reasoningContent': 'Existing provider reasoning'},
        {'role': 'assistant_tool_call', 'toolUseId': 'other', 'toolName': 'read_file',
         'input': {'path': 'README.md'}, 'assistantTurnId': 'batch'},
        {'role': 'tool_result', 'toolUseId': 'load', 'toolName': 'load_skill',
         'content': result.output, 'isError': False},
        {'role': 'tool_result', 'toolUseId': 'other', 'toolName': 'read_file',
         'content': 'Read completed', 'isError': False},
    ]
    if change == 'delete':
        alpha.unlink()
    else:
        _write_skill(root, 'alpha', 'v2')
    _write_skill(root, 'beta', 'v1')
    if change == 'resume':
        tools = create_default_tool_registry(str(root), include_mcp=False)
    captured, _ = _tui_turn(root, tools, history, monkeypatch)
    messages = captured['messages']
    assert 'Instructions-alpha-v1' not in str(messages)
    assert 'beta v1 workflow' in messages[0]['content']
    loaded = next(m for m in messages if m.get('toolUseId') == 'load' and m['role'] == 'tool_result')
    assert loaded['isError'] is True
    assert 'skill_context_invalidated' in loaded['content']
    assert messages[2:4] == history[2:4]
    assert messages[5] == history[5]
    assert 'Instructions-alpha-v1' in history[4]['content']
    # The compactor must no longer pin the obsolete body as active authority.
    from minicode.context_compactor import _loaded_skill_context_indices
    assert not _loaded_skill_context_indices(messages)
    fresh = tools.execute('load_skill', {'name': 'beta'}, ToolContext(
        cwd=str(root), _skill_load_policy=captured['skill_load_policy'],
    ))
    assert fresh.ok, fresh.output
    if change != 'delete':
        current = tools.execute('load_skill', {'name': 'alpha'}, ToolContext(
            cwd=str(root), _skill_load_policy=captured['skill_load_policy'],
        ))
        assert current.ok and 'Instructions-alpha-v2' in current.output


@pytest.mark.parametrize('suffix', ['', '\n\n[System note: Keep going.]'])
def test_tui_keeps_unchanged_loaded_body(tmp_path, monkeypatch, suffix):
    root = tmp_path.resolve()
    _write_skill(root, 'alpha', 'v1')
    tools = create_default_tool_registry(str(root), include_mcp=False)
    result = tools.execute('load_skill', {'name': 'alpha'}, ToolContext(cwd=str(root)))
    assert result.ok
    historical_result = {
        'role': 'tool_result', 'toolUseId': 'load', 'toolName': 'load_skill',
        'content': result.output + suffix, 'isError': False,
    }
    history = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'assistant_tool_call', 'toolUseId': 'load', 'toolName': 'load_skill',
         'input': {'name': 'alpha'}}, historical_result,
    ]
    captured, _ = _tui_turn(root, tools, history, monkeypatch)
    assert captured['messages'][2] == historical_result
