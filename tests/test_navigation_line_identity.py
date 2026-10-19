"""Search, line count and exact line reads must name the same source lines."""

from __future__ import annotations

import pytest

from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.file_line_count import file_line_count_tool
from minicode.tools.grep_files import grep_files_tool
from minicode.tools.read_file import read_file_tool


def registry():
    return ToolRegistry([read_file_tool, grep_files_tool, file_line_count_tool])


@pytest.mark.parametrize(
    "content, expected_line, expected_count",
    [
        ("a = 1\n\f\ndef needle():\n    return 2\n", 3, 4),
        ('text = "left\u2028right"\ndef needle():\n    return text\n', 2, 3),
        ('text = "left\u2029right"\ndef needle():\n    return text\n', 2, 3),
        ('text = "left\x85right"\ndef needle():\n    return text\n', 2, 3),
        ('text = "left\vright"\ndef needle():\n    return text\n', 2, 3),
        ("a = 1\r\n\f\r\ndef needle():\r\n    return 2\r\n", 3, 4),
    ],
)
def test_search_line_numbers_address_the_same_valid_python_source(
    tmp_path, content, expected_line, expected_count
):
    compile(content, "source.py", "exec")
    (tmp_path / "source.py").write_bytes(content.encode("utf-8"))
    tools = registry()
    context = ToolContext(cwd=str(tmp_path))
    search = tools.execute(
        "grep_files", {"path": "source.py", "pattern": "^def needle"}, context
    )
    assert search.ok
    assert f"source.py:{expected_line}: def needle():" in search.output
    window = tools.execute(
        "read_file",
        {"path": "source.py", "start_line": expected_line, "end_line": expected_line},
        context,
    )
    assert window.ok and window.output.split("\n", 7)[-1] == "def needle():\n"
    counted = tools.execute("file_line_count", {"path": "source.py"}, context)
    assert counted.ok and f"LINES: {expected_count}" in counted.output.split("\n")


@pytest.mark.parametrize(
    "content, expected_count, empty, matches",
    [
        ("", 0, 0, []),
        ("\n", 1, 1, [1]),
        ("\n\n", 2, 2, [1, 2]),
        ("value", 1, 0, [1]),
        ("value\n", 1, 0, [1]),
        ("value\n\n", 2, 1, [1, 2]),
        ("value\r\n\r\n", 2, 1, [1, 2]),
        ("value\rnext", 2, 0, [1, 2]),
    ],
)
def test_empty_file_and_trailing_blank_lines_keep_existing_meaning(
    tmp_path, content, expected_count, empty, matches
):
    (tmp_path / "source.py").write_bytes(content.encode("utf-8"))
    tools = registry()
    context = ToolContext(cwd=str(tmp_path))
    counted = tools.execute("file_line_count", {"path": "source.py"}, context)
    assert counted.ok
    assert f"LINES: {expected_count}" in counted.output.split("\n")
    assert f"EMPTY_LINES: {empty}" in counted.output.split("\n")
    search = tools.execute("grep_files", {"path": "source.py", "pattern": "^"}, context)
    assert search.ok
    if not matches:
        assert search.output == "No matches found."
    else:
        actual = [
            int(line.split(":", 2)[1])
            for line in search.output.split("\n")
            if line.startswith("source.py:")
        ]
        assert actual == matches
    past = tools.execute(
        "read_file",
        {
            "path": "source.py",
            "start_line": expected_count + 1,
            "end_line": expected_count + 1,
        },
        context,
    )
    assert past.ok and past.output.split("\n", 7)[-1] == ""
