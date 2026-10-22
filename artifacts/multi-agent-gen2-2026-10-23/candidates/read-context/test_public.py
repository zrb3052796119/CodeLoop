"""Ordinary source inspection remains compatible while context handling is repaired."""
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.read_file import read_file_tool


def test_read_and_search_ordinary_source(tmp_path):
    (tmp_path / "sample.py").write_text("first = 1\ntarget = 2\nlast = 3\n", encoding="utf-8")
    registry = ToolRegistry([read_file_tool, grep_files_tool])
    context = ToolContext(cwd=str(tmp_path))
    read = registry.execute("read_file", {"path": "sample.py", "offset": 0, "limit": 8}, context)
    assert read.ok, read.output
    assert "OFFSET: 0\nEND: 8\n" in read.output
    assert read.output.endswith("first = ")
    search = registry.execute("grep_files", {"path": ".", "pattern": "target", "context_lines": 0}, context)
    assert search.ok, search.output
    assert "sample.py:2: target = 2" in search.output
    assert "1 match(es) in 1 file(s)" in search.output


def test_complete_read_and_search_no_match(tmp_path):
    (tmp_path / "sample.py").write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry([read_file_tool, grep_files_tool])
    context = ToolContext(cwd=str(tmp_path))
    read = registry.execute("read_file", {"path": "sample.py", "limit": 100}, context)
    assert read.ok and "TRUNCATED: no\n" in read.output
    assert read.output.endswith("value = 1\n")
    search = registry.execute("grep_files", {"path": ".", "pattern": "absent"}, context)
    assert search.ok, search.output
    assert "No matches found" in search.output
