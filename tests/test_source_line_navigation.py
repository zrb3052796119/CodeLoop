"""Behavioral navigation regressions; no model calls or production workspace writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import MAX_READ_LIMIT, read_file_tool
from minicode.tools.grep_files import MAX_RESULT_SIZE, grep_files_tool, _format_results


def read(workspace, **values):
    return read_file_tool.run(
        read_file_tool.validator({"path": "source.py", **values}),
        ToolContext(cwd=str(workspace)),
    )


def test_line_read_uses_inclusive_grep_numbers_and_exact_unnumbered_text(tmp_path):
    (tmp_path / "source.py").write_text(
        "before\n目标😀\n    return value\nafter\n", encoding="utf-8"
    )
    result = read(tmp_path, start_line=2, end_line=3)
    assert result.ok
    assert result.output.split("\n", 7)[-1] == "目标😀\n    return value\n"
    assert "OFFSET: 7\n" in result.output
    assert "REQUESTED_LINES: 2-3 (inclusive)\nRETURNED_LINES: 2-3\n" in result.output
    assert (
        "TRUNCATED: no\n" in result.output
    )  # Later unrelated lines do not truncate the requested range.


def test_line_mode_and_character_mode_share_crlf_unicode_offsets(tmp_path):
    (tmp_path / "source.py").write_bytes("one\r\n二😀\r\nlast\r\n".encode())
    result = read(tmp_path, start_line=2, end_line=2)
    assert result.output.split("\n", 7)[-1] == "二😀\n"
    assert "OFFSET: 4\nEND: 7\n" in result.output
    following = read(tmp_path, offset=7, limit=100)
    assert following.output.split("\n", 5)[-1] == "last\n"
    assert "TOTAL_CHARS: 12\n" in following.output


@pytest.mark.parametrize("content", ["", "a", "a\n", "a\nb", "a\nb\n", "甲😀\r\n乙"])
def test_line_bounds_beyond_eof_report_real_normalized_size(tmp_path, content):
    (tmp_path / "source.py").write_bytes(content.encode())
    normalized = content.replace("\r\n", "\n")
    result = read(tmp_path, start_line=50, end_line=80)
    assert result.ok and result.output.split("\n", 7)[-1] == ""
    assert (
        f"OFFSET: {len(normalized)}\nEND: {len(normalized)}\nTOTAL_CHARS: {len(normalized)}\n"
        in result.output
    )
    assert "RETURNED_LINES: none\n" in result.output


@pytest.mark.parametrize("offset", [8, 20000, 999999])
def test_character_offset_past_eof_never_invents_total_length(tmp_path, offset):
    (tmp_path / "source.py").write_bytes("A中\r\n文😀".encode())
    result = read(tmp_path, offset=offset, limit=7)
    assert result.ok and result.output.split("\n", 5)[-1] == ""
    assert "TOTAL_CHARS: 5\n" in result.output
    assert "TRUNCATED: no\n" in result.output


def test_partial_long_line_uses_cap_and_character_continuation(tmp_path):
    (tmp_path / "source.py").write_text(
        "skip\n" + "😀" * 25000 + "\nlast", encoding="utf-8"
    )
    first = read(tmp_path, start_line=2, end_line=2, limit=MAX_READ_LIMIT)
    assert first.ok and first.output.split("\n", 7)[-1] == "😀" * 20000
    assert "END: 20005\n" in first.output
    assert '"offset": 20005' in first.output
    assert "omit start_line and end_line" in first.output
    assert "RETURNED_LINES: 2-2 (last line may be partial)\n" in first.output
    second = read(tmp_path, offset=20005, limit=5001)
    assert second.output.split("\n", 5)[-1] == "😀" * 5000 + "\n"


def test_cap_at_line_boundary_does_not_claim_partial_line(tmp_path):
    (tmp_path / "source.py").write_text("aaa\nbbb\nccc\n")
    selected = read(tmp_path, start_line=1, end_line=3, limit=4)
    assert selected.output.split("\n", 7)[-1] == "aaa\n"
    assert "RETURNED_LINES: 1-1\n" in selected.output
    assert "TRUNCATED: yes" in selected.output
    exact = read(tmp_path, start_line=1, end_line=1, limit=4)
    assert "TRUNCATED: no\n" in exact.output


def test_last_unterminated_line_and_shorter_file_are_not_truncation(tmp_path):
    (tmp_path / "source.py").write_text("one\ntwo")
    result = read(tmp_path, start_line=2, end_line=100, limit=3)
    assert result.output.split("\n", 7)[-1] == "two"
    assert "TOTAL_CHARS: 7\nTRUNCATED: no\n" in result.output
    assert "RETURNED_LINES: 2-2\n" in result.output


def test_line_seeking_never_reads_a_whole_giant_line(tmp_path, monkeypatch):
    target = tmp_path / "source.py"
    target.write_text("x" * 200000 + "\nneedle\nend")
    original_open = Path.open
    sizes = []

    class Bounded:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def read(self, size):
            assert 0 < size <= 65536
            sizes.append(size)
            return self.handle.read(size)

        def readline(self, size):
            assert 0 < size <= 65536
            sizes.append(size)
            return self.handle.readline(size)

    def controlled(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return Bounded(handle) if path == target else handle

    monkeypatch.setattr(Path, "open", controlled)
    result = read(tmp_path, start_line=2, end_line=2)
    assert result.ok and result.output.split("\n", 7)[-1] == "needle\n"
    assert len(sizes) >= 5


@pytest.mark.parametrize(
    "values",
    [
        {"start_line": 1},
        {"end_line": 2},
        {"start_line": 0, "end_line": 1},
        {"start_line": 2, "end_line": 1},
        {"start_line": True, "end_line": 2},
        {"start_line": 1.2, "end_line": 2},
        {"start_line": "1", "end_line": 2},
        {"start_line": 1, "end_line": 2, "offset": 0},
        {"start_line": 1, "end_line": 2, "limit": 20001},
    ],
)
def test_ambiguous_or_invalid_ranges_are_visible_validation_failures(tmp_path, values):
    registry = ToolRegistry([read_file_tool])
    result = registry.execute(
        "read_file", {"path": "source.py", **values}, ToolContext(cwd=str(tmp_path))
    )
    assert not result.ok and "Input validation error in read_file:" in result.output


def test_line_read_preserves_scope_permission_and_internal_store_checks(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("outside-secret")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link.py").symlink_to(outside)
    (workspace / ".mini-code-memory").mkdir()
    (workspace / ".mini-code-memory/memory.json").write_text("private-store")
    permissions = PermissionManager(
        str(workspace), prompt=lambda _: {"decision": "deny_once"}
    )
    for path in (str(outside), "link.py", ".mini-code-memory/memory.json"):
        result = ToolRegistry([read_file_tool]).execute(
            "read_file",
            {"path": path, "start_line": 1, "end_line": 1},
            ToolContext(cwd=str(workspace), permissions=permissions),
        )
        assert (
            not result.ok
            and "outside-secret" not in result.output
            and "private-store" not in result.output
        )


def test_line_read_missing_directory_binary_and_io_errors_are_truthful(
    tmp_path, monkeypatch
):
    (tmp_path / "dir").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff\x00")
    blocked = tmp_path / "blocked"
    blocked.write_text("hidden")
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("hidden absolute marker")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    for path, code in (
        ("absent", "not_found"),
        ("dir", "not_a_file"),
        ("binary", "binary_file"),
        ("blocked", "permission_denied"),
    ):
        result = read_file_tool.run(
            read_file_tool.validator({"path": path, "start_line": 1, "end_line": 3}),
            ToolContext(cwd=str(tmp_path)),
        )
        assert not result.ok and f"error[{code}]" in result.output
        assert "hidden absolute marker" not in result.output


@pytest.mark.parametrize("context", [0, 5, 6, 18, 20])
def test_grep_returns_requested_context_instead_of_silently_clamping(tmp_path, context):
    (tmp_path / "source.py").write_text("\n".join(f"line{i}" for i in range(1, 62)))
    data = grep_files_tool.validator(
        {"path": "source.py", "pattern": "^line31$", "context_lines": context}
    )
    assert data["context_lines"] == context
    result = grep_files_tool.run(data, ToolContext(cwd=str(tmp_path)))
    assert result.ok
    assert (
        f"source.py:{31 - context}:" in result.output
        and f"source.py:{31 + context}:" in result.output
    )
    if context < 20:
        assert f"source.py:{30 - context}:" not in result.output
    assert "source.py:31: line31" in result.output


@pytest.mark.parametrize("context", [-1, 21, 100, True, 2.5, "18", None])
def test_grep_out_of_range_or_noninteger_context_is_visible_error(tmp_path, context):
    result = ToolRegistry([grep_files_tool]).execute(
        "grep_files",
        {"path": ".", "pattern": "x", "context_lines": context},
        ToolContext(cwd=str(tmp_path)),
    )
    assert not result.ok and "context_lines" in result.output and "20" in result.output


def test_grep_overlap_keeps_each_line_once_and_retains_both_match_markers(tmp_path):
    (tmp_path / "source.py").write_text(
        "a\nb\nMATCH one\nd\nMATCH two\nf\ng\nh\ni\nj\nk\nl\nMATCH three\nn\no\n"
    )
    result = grep_files_tool.run(
        grep_files_tool.validator(
            {"path": "source.py", "pattern": "MATCH", "context_lines": 2}
        ),
        ToolContext(cwd=str(tmp_path)),
    )
    assert result.ok
    lines = [
        line for line in result.output.splitlines() if line.startswith("source.py:")
    ]
    numbers = [int(line.split(":")[1]) for line in lines]
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 11, 12, 13, 14, 15]
    assert result.output.count("source.py:3: MATCH one") == 1
    assert result.output.count("source.py:5: MATCH two") == 1
    assert "source.py:7:  g\n\nsource.py:11:  k" in result.output
    assert "3 match(es)" in result.output


def test_grep_single_match_legacy_context_layout_is_unchanged(tmp_path):
    (tmp_path / "source.py").write_text("before\nmatch\nafter\n")
    result = grep_files_tool.run(
        grep_files_tool.validator(
            {"path": "source.py", "pattern": "match", "context_lines": 1}
        ),
        ToolContext(cwd=str(tmp_path)),
    )
    assert (
        result.output
        == "source.py:1:  before\nsource.py:2: match\nsource.py:3:  after\n\n\n1 match(es) in 1 file(s)"
    )


def test_grep_total_output_cap_includes_notice_and_summary(tmp_path):
    (tmp_path / "source.py").write_text("match " + "中" * 60000)
    result = grep_files_tool.run(
        grep_files_tool.validator(
            {"path": "source.py", "pattern": "match", "context_lines": 20}
        ),
        ToolContext(cwd=str(tmp_path)),
    )
    assert result.ok and len(result.output) == MAX_RESULT_SIZE
    assert "truncated" in result.output and result.output.endswith(
        "1 match(es) in 1 file(s)"
    )


def test_grep_match_limit_still_applies_when_merging_context(tmp_path):
    matches = [
        {
            "line": n,
            "text": "match",
            "is_match": True,
            "context_before": [],
            "context_after": [],
        }
        for n in range(1, 6)
    ]
    output = _format_results(
        [(tmp_path / "source.py", matches)], tmp_path, max_results=2
    )
    assert output == "source.py:1: match\n\nsource.py:2: match\n"


def test_tool_schemas_advertise_line_mode_and_the_real_context_range():
    properties = read_file_tool.input_schema["properties"]
    assert properties["start_line"]["minimum"] == properties["end_line"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 20000
    assert "offset" in properties["start_line"]["description"]
    assert grep_files_tool.input_schema["properties"]["context_lines"]["maximum"] == 20
