"""The continuation printed by read_file must be a directly executable call."""

import json
import sys

import pytest

from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool


def next_arguments(output):
    line = next(line for line in output.splitlines() if line.startswith("TRUNCATED:"))
    marker = "next read_file arguments: "
    assert marker in line, "a truncated line read must return unambiguous complete arguments"
    return json.loads(line.partition(marker)[2])


def execute(registry, context, arguments):
    result = registry.execute("read_file", arguments, context)
    assert result.ok, result.output
    header_lines = 7 if "start_line" in arguments else 5
    return result.output, result.output.split("\n", header_lines)[-1]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file names cannot contain a double quote, which this continuation fixture uses",
)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_follow_line_boundary_hints_reconstructs_only_requested_range(tmp_path, newline):
    path = 'quoted "源😀.py'
    (tmp_path / path).write_bytes(newline.join(["skip", "甲😀", "乙😀", "丙😀", "outside"]).encode())
    registry = ToolRegistry([read_file_tool])
    context = ToolContext(cwd=str(tmp_path))
    args = {"path": path, "start_line": 2, "end_line": 4, "limit": 3}
    bodies = []
    for expected_start in [2, 3, 4]:
        assert args == {"path": path, "start_line": expected_start, "end_line": 4, "limit": 3}
        output, body = execute(registry, context, args)
        bodies.append(body)
        if expected_start < 4:
            args = next_arguments(output)
        else:
            assert "TRUNCATED: no\n" in output
    assert "".join(bodies) == "甲😀\n乙😀\n丙😀\n"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_partial_line_hint_switches_modes_without_repeating_or_skipping_text(tmp_path, newline):
    path = "source.py"
    (tmp_path / path).write_bytes(("skip" + newline + "甲😀乙😀丙😀" + newline).encode())
    registry = ToolRegistry([read_file_tool])
    context = ToolContext(cwd=str(tmp_path))
    output, first = execute(registry, context, {"path": path, "start_line": 2, "end_line": 2, "limit": 4})
    args = next_arguments(output)
    assert args == {"path": path, "offset": 9, "limit": 4}
    assert "omit start_line and end_line" in output
    _, second = execute(registry, context, args)
    assert first + second == "甲😀乙😀丙😀\n"


def test_continuation_does_not_capture_read_authority(tmp_path):
    path = "source.py"
    (tmp_path / path).write_text("aa\nbb\n")
    registry = ToolRegistry([read_file_tool])
    context = ToolContext(cwd=str(tmp_path))
    output, _ = execute(registry, context, {"path": path, "start_line": 1, "end_line": 2, "limit": 3})
    arguments = next_arguments(output)
    (tmp_path / path).unlink()
    result = registry.execute("read_file", arguments, context)
    assert not result.ok
    assert "error[not_found]" in result.output
