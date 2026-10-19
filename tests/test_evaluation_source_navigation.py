"""Source navigation must not turn an explicit file search into a false miss."""

from pathlib import Path

import pytest

from minicode.tooling import ToolContext
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.read_file import read_file_tool


def _grep(workspace: Path, path: str, **options):
    data = grep_files_tool.validator({
        "path": path, "pattern": "import", **options,
    })
    return grep_files_tool.run(data, ToolContext(cwd=str(workspace)))


def _source(workspace: Path) -> Path:
    directory = workspace / "minicode"
    directory.mkdir()
    source = directory / "agent_loop.py"
    source.write_text(
        '"""Agent loop."""\nfrom minicode.tools import task\n\n'
        "def run():\n    return task\n",
        encoding="utf-8",
    )
    return source


def test_explicit_file_and_its_directory_return_same_matches_and_context(tmp_path):
    _source(tmp_path)
    file_result = _grep(tmp_path, "minicode/agent_loop.py", context_lines=1)
    directory_result = _grep(tmp_path, "minicode", context_lines=1)
    assert file_result.ok and directory_result.ok
    assert file_result.output == directory_result.output
    assert "agent_loop.py:1:  " in file_result.output
    assert "agent_loop.py:2: from minicode.tools import task" in file_result.output
    assert "agent_loop.py:3:  " in file_result.output
    assert "1 match(es) in 1 file(s)" in file_result.output


@pytest.mark.parametrize("options, matches", [
    ({"include": "*.py"}, True),
    ({"include": ["agent_*.py"], "exclude": "*_test.py"}, True),
    ({"include": "*.ts"}, False),
    ({"exclude": ["agent_loop.py"]}, False),
])
def test_explicit_file_keeps_include_and_exclude_filters(tmp_path, options, matches):
    _source(tmp_path)
    file_result = _grep(tmp_path, "minicode/agent_loop.py", **options)
    directory_result = _grep(tmp_path, "minicode", **options)
    assert file_result.ok and file_result.output == directory_result.output
    assert ("from minicode.tools import task" in file_result.output) is matches


@pytest.mark.parametrize("path", ["absent.py", "absent", "absent/agent_loop.py"])
def test_missing_search_path_is_an_error_not_a_successful_empty_search(tmp_path, path):
    result = _grep(tmp_path, path)
    assert not result.ok
    assert "error[not_found]" in result.output
    assert "No matches" not in result.output


def test_existing_file_and_directory_without_matches_remain_successful(tmp_path):
    _source(tmp_path)
    for path in ("minicode", "minicode/agent_loop.py"):
        result = _grep(tmp_path, path, pattern="not_present_anywhere")
        assert result.ok and result.output == "No matches found."


def test_directory_search_still_recurses_and_skips_hidden_directories(tmp_path):
    _source(tmp_path)
    for name in ("nested", ".hidden", "__pycache__"):
        directory = tmp_path / "minicode" / name
        directory.mkdir()
        (directory / "probe.py").write_text("import marker\n", encoding="utf-8")
    result = _grep(tmp_path, "minicode", pattern="marker")
    assert result.ok
    assert "nested/probe.py:1: import marker" in result.output
    assert ".hidden" not in result.output and "__pycache__" not in result.output
    assert "1 match(es) in 1 file(s)" in result.output


def test_read_file_pagination_uses_characters_and_returned_end_offset(tmp_path):
    (tmp_path / "source.py").write_text("A中\n文BC", encoding="utf-8")
    context = ToolContext(cwd=str(tmp_path))
    first = read_file_tool.run(
        read_file_tool.validator({"path": "source.py", "limit": 3}), context,
    )
    assert first.ok and first.output.split("\n", 5)[-1] == "A中\n"
    assert "END: 3\n" in first.output
    assert "TRUNCATED: yes - call read_file again with offset 3\n" in first.output
    second = read_file_tool.run(
        read_file_tool.validator({"path": "source.py", "offset": 3, "limit": 3}),
        context,
    )
    assert second.ok and second.output.split("\n", 5)[-1] == "文BC"
    assert "END: 6\n" in second.output and "TRUNCATED: no\n" in second.output
