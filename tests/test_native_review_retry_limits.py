"""Caller retry ceilings bound physical adapter sends without changing defaults."""

import inspect
import io
import json
import urllib.error

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep
from test_model_adapter_call_controls import _adapter_for_provider, _provider_text_body


def _caller_override(value):
    # Real before-source has no loop argument. Let it make its original
    # provider sends so the counterexample is the request count, not TypeError.
    return ({'max_retries_override': value}
        if 'max_retries_override' in inspect.signature(run_agent_turn).parameters else {})


def _http_error(request, status, message):
    return urllib.error.HTTPError(request.full_url, status, message, {},
        io.BytesIO(json.dumps({'error': {'message': message}}).encode()))


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize(('override', 'expected'), [(None, 3), (0, 1), (1, 2)])
def test_actual_loop_passes_retry_ceiling_to_physical_adapter_sends(
        tmp_path, monkeypatch, provider, override, expected):
    adapter = _adapter_for_provider(provider)
    adapter.runtime['modelMaxRetries'] = 2
    requests = []

    def reject(request, **_kwargs):
        requests.append(json.loads(request.data))
        raise _http_error(request, 503, 'local controlled service unavailable')

    monkeypatch.setattr(f'minicode.{provider}_adapter.open_verified_url', reject)
    monkeypatch.setattr(f'minicode.{provider}_adapter.controlled_retry_sleep', lambda *_a, **_k: None)
    messages = run_agent_turn(model=adapter, tools=ToolRegistry([]), cwd=str(tmp_path),
        messages=[{'role': 'user', 'content': 'Describe the current state.'}],
        enable_work_chain=False, implementation_task=False, enable_implementation_phases=False,
        max_steps=1, **_caller_override(override))
    assert len(requests) == expected
    assert 'Model API error' in messages[-1]['content']


@pytest.mark.parametrize(('override', 'transient_first', 'expected', 'success'), [
    (None, False, 2, True),  # runtime=0 alone keeps the existing compatibility fallback
    (0, False, 1, False),
    (1, False, 2, True),
    (1, True, 2, False),   # the normal retry consumed the explicit allowance
    (2, True, 3, True),
    (None, True, 3, True),
])
def test_anthropic_cache_fallback_shares_explicit_physical_retry_allowance(
        monkeypatch, override, transient_first, expected, success):
    adapter = _adapter_for_provider('anthropic')
    adapter.runtime['modelMaxRetries'] = int(transient_first)
    requests = []
    monkeypatch.setenv('MINI_CODE_PROMPT_CACHE', '1')

    def respond(request, **_kwargs):
        requests.append(json.loads(request.data))
        if transient_first and len(requests) == 1:
            raise _http_error(request, 429, 'local controlled rate limit')
        if len(requests) == 1 + int(transient_first):
            raise _http_error(request, 400, 'cache_control is not supported')
        return io.BytesIO(_provider_text_body('anthropic'))

    monkeypatch.setattr('minicode.anthropic_adapter.open_verified_url', respond)
    monkeypatch.setattr('minicode.anthropic_adapter.controlled_retry_sleep', lambda *_a, **_k: None)
    messages = [{'role': 'system', 'content': 'Read-only native review.'},
        {'role': 'user', 'content': 'Inspect the supplied evidence.'}]
    if success:
        assert adapter.next(messages, max_retries_override=override).content == 'done'
        assert 'cache_control' not in json.dumps(requests[-1])
    else:
        with pytest.raises(RuntimeError, match='cache_control'):
            adapter.next(messages, max_retries_override=override)
    assert len(requests) == expected
    assert 'cache_control' in json.dumps(requests[0])


@pytest.mark.parametrize('override', [0, 3])
def test_phase_and_reserved_closing_keep_the_stricter_zero(tmp_path, override):
    seen = []

    class Model:
        def next(self, _messages, **options):
            seen.append(options)
            if len(seen) == 1:
                return AgentStep(type='tool_calls', calls=[{'id': 'bookkeeping',
                    'toolName': 'todo_write', 'input': {'todos': []}}])
            return AgentStep(type='assistant', content='Independent verification remains pending.')

    todo = ToolDefinition(name='todo_write', description='Record progress',
        input_schema={'type': 'object'}, validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output='recorded'))
    run_agent_turn(model=Model(), tools=ToolRegistry([todo, edit_file_tool]),
        messages=[{'role': 'user', 'content': 'Fix app.py.'}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, bounded_implementation_phases=True,
        max_steps=2, enable_bounded_completion=True,
        agent_budget=AgentTurnBudget(max_model_calls=2), **_caller_override(override))
    assert len(seen) == 2 and seen[-1]['disable_tools'] is True
    assert all(options['max_retries_override'] == 0 for options in seen)


def test_new_loop_control_preserves_models_without_optional_kwargs(tmp_path):
    class LegacyModel:
        def next(self, _messages):
            return AgentStep(type='assistant', content='No changes requested.')

    messages = run_agent_turn(model=LegacyModel(), tools=ToolRegistry([]), cwd=str(tmp_path),
        messages=[{'role': 'user', 'content': 'Describe the current state.'}],
        enable_work_chain=False, implementation_task=False, enable_implementation_phases=False,
        max_steps=1, **_caller_override(0))
    assert messages[-1]['content'] == 'No changes requested.'
