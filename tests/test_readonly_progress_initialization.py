"""Local actual-loop regressions; synthetic evidence and no provider network."""

from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

import minicode.agent_loop as loop
from minicode.auto_mode import PermissionMode
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.tooling import ToolCapability, ToolDefinition, ToolMetadata, ToolRegistry, ToolResult
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool


def long_authority():
    return json.dumps({'receipt': {'output': 'x' * 10_000}, 'verdict': 'pending'})


def readonly_tool(name, run):
    return ToolDefinition(name=name, description='Generic local read-only fixture',
        input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
        validator=lambda value: value, run=run,
        metadata=ToolMetadata(name=name, description='Read-only fixture',
            capabilities={ToolCapability.READ_ONLY}))


@pytest.mark.parametrize('explicit', [False, None, True])
def test_readonly_actual_adapter_preserves_authority_and_native_receipt_without_parsing(
        tmp_path, monkeypatch, explicit):
    parsed = []

    def forbidden_parse(text, *args, **kwargs):
        parsed.append(text)
        pytest.fail('Read-only evidence must not enter unused progress intent parsing')

    monkeypatch.setattr(loop, 'parse_intent', forbidden_parse)
    authority = long_authority()
    receipt = json.dumps({'receiptId': 'generic_receipt', 'output': 'RESULT ' * 1000})
    emitted, submitted, requests = [], [], []

    def read_receipt(value, context):
        emitted.append((value, context.cwd))
        return ToolResult(ok=True, output=receipt)

    def submit(value, context):
        submitted.append((value, context.cwd))
        return ToolResult(ok=True, output='{"verdict":"inconclusive"}', _implementation_submitted=True)

    reader = readonly_tool('review_probe_read', read_receipt)
    terminal = readonly_tool('completion_probe_adjudication_submit', submit)
    child_tools = ToolRegistry([reader, terminal])
    parent_tools = ToolRegistry([read_file_tool, write_file_tool])
    runtime = {'model': 'gpt-test', 'openaiBaseUrl': 'https://openai.invalid/v1',
        'openaiApiKey': 'test', 'modelMaxRetries': 0, 'maxOutputTokens': 4096,
        'reflectionMode': 'rule'}
    adapter = OpenAIModelAdapter(runtime, parent_tools)
    parent_schema = deepcopy(adapter.request_tool_schemas())
    messages = [{'role': 'system', 'content': 'Read the supplied local evidence.'},
                {'role': 'user', 'content': authority}]
    original = deepcopy(messages)

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        del timeout
        body = json.loads(request.data)
        requests.append(body)
        schemas = {tool['function']['name']: tool['function']['parameters'] for tool in body['tools']}
        assert schemas == {reader.name: reader.input_schema, terminal.name: terminal.input_schema}
        assert any(message.get('role') == 'user' and message.get('content') == authority
                   for message in body['messages'])
        if len(requests) == 1:
            name, identity = reader.name, 'read-actual-receipt'
        else:
            assert len(requests) == 2
            actual = next(message for message in body['messages']
                if message.get('role') == 'tool' and message.get('tool_call_id') == 'read-actual-receipt')
            assert actual['content'] == receipt
            name, identity = terminal.name, 'submit-native'
        return Response(json.dumps({'choices': [{'index': 0, 'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': identity,
                'type': 'function', 'function': {'name': name, 'arguments': '{}'}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}).encode())

    monkeypatch.setattr('minicode.openai_adapter.open_verified_url', transport)
    result = loop.run_agent_turn(model=adapter, tools=child_tools, messages=messages,
        cwd=str(tmp_path), runtime=runtime, implementation_task=explicit,
        enable_work_chain=False, enable_implementation_phases=False,
        defer_curator_maintenance=True, max_steps=3)
    assert parsed == [] and len(requests) == 2 and len(emitted) == len(submitted) == 1
    assert messages == original
    assert any(message.get('content') == authority for message in result)
    assert any(message.get('role') == 'tool_result' and message.get('content') == receipt for message in result)
    assert not any(message.get('_coding_progress') for message in result)
    assert adapter.tools is parent_tools and adapter.request_tool_schemas() == parent_schema


@pytest.mark.parametrize('explicit,writable,plan,prompt,preparsed,expected_calls,enabled', [
    (False, True, False, 'Fix app.py.', False, 0, False),
    (True, True, False, 'Perform the locked assignment.', False, 0, True),
    (None, True, False, 'Fix app.py.', False, 1, True),
    (None, True, False, 'Review app.py only; do not edit.', False, 1, False),
    (None, True, False, 'Fix app.py.', True, 0, True),
    (None, True, True, 'Fix app.py.', False, 0, False),
    (True, True, True, 'Fix app.py.', False, 0, False),
    (None, False, False, 'Fix app.py.', False, 0, False),
    (True, False, False, 'Fix app.py.', False, 0, False),
])
def test_explicit_classification_registry_and_plan_boundaries(tmp_path, monkeypatch,
        explicit, writable, plan, prompt, preparsed, expected_calls, enabled):
    original_parse, tracker_class = loop.parse_intent, loop.CodingProgressTracker
    supplied = original_parse(prompt) if preparsed else None
    parses, trackers, requests = [], [], []

    def parse(text, *args, **kwargs):
        parses.append(text)
        assert expected_calls, 'An explicit classification or read-only context needs no inference'
        return original_parse(text, *args, **kwargs)

    def tracker(*args, **kwargs):
        value = tracker_class(*args, **kwargs)
        trackers.append(value)
        return value

    monkeypatch.setattr(loop, 'parse_intent', parse)
    monkeypatch.setattr(loop, 'CodingProgressTracker', tracker)
    stop = KeyboardInterrupt('inspect first real model request')

    class Model:
        def next(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            raise stop

    tools = ToolRegistry([read_file_tool, write_file_tool] if writable else [read_file_tool])
    permissions = PermissionManager(str(tmp_path),
        auto_mode=PermissionMode.PLAN if plan else PermissionMode.DEFAULT)
    with pytest.raises(KeyboardInterrupt) as caught:
        loop.run_agent_turn(model=Model(), tools=tools, messages=[{'role': 'user', 'content': prompt}],
            cwd=str(tmp_path), permissions=permissions, parsed_intent=supplied,
            implementation_task=explicit, enable_work_chain=False, enable_implementation_phases=False,
            max_steps=1, defer_curator_maintenance=True)
    assert caught.value is stop and len(requests) == 1
    assert len(parses) == expected_calls and all(text == prompt for text in parses)
    assert len(trackers) == 1 and trackers[0].enabled is enabled
    assert not any(message.get('_coding_progress') for message in requests[0])


def test_real_long_json_parser_path_reaches_readonly_model_within_local_deadline(tmp_path):
    # Run the actual parser/loop in its own bounded process: a regression cannot
    # stall the suite. The child inherits pytest's disposable HOME; no network
    # adapter or source/candidate from the real evaluation is involved.
    source = textwrap.dedent('''
        import json
        from minicode.agent_loop import run_agent_turn
        from minicode.tooling import ToolRegistry
        from minicode.tools.read_file import read_file_tool
        from minicode.types import AgentStep
        import sys
        authority = json.dumps({'receipt': {'output': 'x' * 10000}, 'verdict': 'pending'})
        seen = []
        class Model:
            def next(self, messages, **kwargs):
                seen.append(messages)
                assert any(m.get('content') == authority for m in messages)
                assert set(kwargs['tools_override'].list_all()) == {'read_file'}
                return AgentStep(type='assistant', kind='final', content='Local readonly evidence observed.')
        result = run_agent_turn(model=Model(), tools=ToolRegistry([read_file_tool]),
            messages=[{'role': 'user', 'content': authority}], cwd=sys.argv[1],
            implementation_task=False, enable_work_chain=False, enable_implementation_phases=False,
            max_steps=1, defer_curator_maintenance=True)
        assert len(seen) == 1 and result[-1]['content'] == 'Local readonly evidence observed.'
        print('REAL_READONLY_REQUEST_WITH_EXACT_AUTHORITY')
    ''')
    repository = next(parent for parent in Path(__file__).resolve().parents if (parent / 'minicode').is_dir())
    try:
        result = subprocess.run([sys.executable, '-c', source, str(tmp_path)], cwd=repository,
            capture_output=True, text=True, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail('Unneeded progress classification blocked a readonly model request for over 5 seconds')
    assert result.returncode == 0, result.stderr
    assert 'REAL_READONLY_REQUEST_WITH_EXACT_AUTHORITY' in result.stdout
