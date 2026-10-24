"""Required utility inputs and scheduling metadata agree with real behavior."""

import hashlib

import pytest

from minicode.tooling import ToolContext
from minicode.tools.crypto_utils import hash_tool
from minicode.tools.encoding_utils import base64_encode_tool, url_encode_tool
from minicode.tools.file_line_count import file_line_count_tool
from minicode.tools.text_utils import (
    line_count_tool, text_dedupe_tool, text_join_tool, text_sort_tool,
)

CASES = (
    (base64_encode_tool, "text"), (url_encode_tool, "text"),
    (hash_tool, "text"), (text_sort_tool, "content"),
    (text_dedupe_tool, "content"), (text_join_tool, "items"),
    (line_count_tool, "content"),
)


@pytest.mark.parametrize("tool,field", CASES, ids=[tool.name for tool, _ in CASES])
@pytest.mark.parametrize("input_kind", ["missing", "null", "number", "boolean", "list", "object"])
def test_required_utility_input_rejects_omission_and_wrong_type(tool, field, input_kind):
    assert tool.input_schema["required"] == [field]
    invalid = {"null": None, "number": 7, "boolean": True, "list": [], "object": {}}
    values = {} if input_kind == "missing" else {field: invalid[input_kind]}
    with pytest.raises(ValueError, match=field):
        tool.validator(values)


@pytest.mark.parametrize("tool,field", CASES, ids=[tool.name for tool, _ in CASES])
def test_explicit_empty_utility_string_remains_valid(tool, field, tmp_path):
    values = tool.validator({field: ""})
    assert values[field] == ""
    result = tool.run(values, ToolContext(cwd=str(tmp_path)))
    assert result.ok, result.output
    if tool is hash_tool:
        assert result.output == hashlib.sha256(b"").hexdigest()
    elif tool is line_count_tool:
        assert "Characters: 0" in result.output
    else:
        assert result.output == ""


def test_file_line_count_is_read_only_and_safe_to_schedule_concurrently(tmp_path):
    target = tmp_path / "example.txt"
    target.write_bytes(b"one\n\ntwo\n")
    before = target.read_bytes()
    assert file_line_count_tool.is_read_only
    assert file_line_count_tool.call_is_concurrency_safe({"path": target.name})
    result = file_line_count_tool.run(
        file_line_count_tool.validator({"path": target.name}), ToolContext(cwd=str(tmp_path)))
    assert result.ok and "LINES: 3" in result.output
    assert target.read_bytes() == before
