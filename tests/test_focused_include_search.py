"""A named include stays a single permitted file at the actual tool boundary."""
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.permissions import PermissionDeniedError
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


def _execute(tmp_path, include, *, directory='tests', permissions=None, expect_found=False):
    folder = tmp_path / 'tests'
    folder.mkdir(exist_ok=True)
    (folder / 'target.py').write_text('def public_fixture():\n    return 123\n')
    nested = folder / 'nested'
    nested.mkdir(exist_ok=True)
    (nested / 'target.py').write_text('def private_duplicate():\n    return 999\n')
    captured = []
    class Model(ModelAdapter):
        calls = 0
        def next(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type='tool_calls', calls=[{'id':'search','toolName':'grep_files',
                    'input':{'path':directory,'include':include,'pattern':'fixture|duplicate','context_lines':1}}])
            if self.calls == 2:
                result = next(m for m in messages if m.get('role') == 'tool_result' and m.get('toolUseId') == 'search')
                captured.append(result)
                if expect_found and not result.get('isError'):
                    return AgentStep(type='tool_calls', calls=[{'id':'read','toolName':'read_file',
                        'input':{'path':'tests/target.py','start_line':1,'end_line':2}}])
                return AgentStep(type='assistant',kind='final',content='Search refused; no write was made.')
            result = next(m for m in messages if m.get('role') == 'tool_result' and m.get('toolUseId') == 'read')
            captured.append(result)
            assert not result.get('isError') and 'return 123' in result['content']
            return AgentStep(type='assistant',kind='final',content='Read the permitted fixture; no file changed.')
    model = Model()
    registry = ToolRegistry([grep_files_tool, read_file_tool, edit_file_tool])
    messages = run_agent_turn(model=model, tools=registry,
        messages=[{'role':'user','content':'Inspect the named public fixture in tests/target.py.'}],
        cwd=str(tmp_path), permissions=permissions, implementation_task=True,
        implementation_analysis_complete=True, enable_work_chain=False, max_steps=6)
    if expect_found:
        assert not captured[0].get('isError'), captured[0]['content']
        assert 'public_fixture' in captured[0]['content']
        assert 'private_duplicate' not in captured[0]['content']
        assert '1 file(s)' in captured[0]['content']
    assert model.calls == (3 if expect_found else 2)
    assert (folder / 'target.py').read_text() == 'def public_fixture():\n    return 123\n'
    return captured, messages


@pytest.mark.parametrize('include', ['target.py', ['target.py']])
def test_exact_include_in_directory_is_searchable_then_readable(tmp_path, include):
    assert Path(run_agent_turn.__code__.co_filename).resolve().parents[1] == Path(__file__).resolve().parents[1]
    results, messages = _execute(tmp_path, include, expect_found=True)
    assert len(results) == 2
    assert messages[-1]['content'] == 'Read the permitted fixture; no file changed.'


@pytest.mark.parametrize('include', [
    '*.py', '**/target.py', 'nested/target.py', ['target.py','other.py'],
    '../target.py', '/tmp/target.py', 'C:\\target.py', 'target?.py', '[t]arget.py', '.env', [],
])
def test_nonexact_include_cannot_unlock_directory_search(tmp_path, include):
    results, _ = _execute(tmp_path, include)
    assert results[0].get('isError')
    assert 'implementation_phase_limit' in results[0]['content']
    assert 'public_fixture' not in results[0]['content']


def test_exact_include_still_obeys_denied_file_permission(tmp_path):
    class Permissions:
        def ensure_path_access(self, target, intent):
            if Path(target).name == 'target.py':
                raise PermissionDeniedError('test target is outside read authority', code='test_scope_denied')
    results, _ = _execute(tmp_path, 'target.py', permissions=Permissions())
    assert results[0].get('isError')
    assert 'public_fixture' not in results[0]['content']


def test_exact_include_does_not_resolve_a_recursive_directory_target(tmp_path):
    results, _ = _execute(tmp_path, 'nested')
    assert results[0].get('isError')
    assert 'private_duplicate' not in results[0]['content']


def test_exact_include_symlink_cannot_escape_workspace(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '_outside.py')
    outside.write_text('def outside_secret(): return 999\n')
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests' / 'alias.py').symlink_to(outside)
    results, _ = _execute(tmp_path, 'alias.py')
    assert results[0].get('isError')
    assert 'outside_secret' not in results[0]['content']
