"""Tool failures must be distinguishable and must not leak local paths.

Two audit findings covered here:

- TOOL-001: `read_file` swallowed every OSError and returned "", so a missing
  file produced `ok=True` with `TOTAL_CHARS: 0` — byte-identical to a
  genuinely empty file. A coding agent cannot act correctly on that: "the
  file is empty, I'll write content" and "the file isn't there, I have the
  wrong path" call for opposite next steps.
- SEC-005: the ToolRegistry crash safety net returned the raw exception plus a
  traceback excerpt straight to the model, carrying absolute local paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry
from minicode.tools.read_file import read_file_tool


def _read(workspace: Path, path: str):
    return read_file_tool.run(
        read_file_tool.validator({"path": path}),
        ToolContext(cwd=str(workspace), permissions=None),
    )


def test_missing_file_is_not_reported_as_an_empty_file(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_text("", encoding="utf-8")

    missing = _read(tmp_path, "missing.py")
    empty = _read(tmp_path, "empty.py")

    assert missing.ok is False
    assert "error[not_found]" in missing.output
    # The genuinely empty file still succeeds...
    assert empty.ok is True
    assert "TOTAL_CHARS: 0" in empty.output
    # ...and the two are no longer indistinguishable.
    assert missing.output != empty.output


def test_directory_and_unreadable_paths_get_distinct_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "adir").mkdir()
    blocked = tmp_path / "blocked.py"
    blocked.write_text("secret", encoding="utf-8")
    original_read_text = Path.read_text

    def permission_denied(path: Path, *args, **kwargs) -> str:
        if path == blocked:
            raise PermissionError("synthetic unreadable file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", permission_denied)

    assert "error[not_a_file]" in _read(tmp_path, "adir").output
    result = _read(tmp_path, "blocked.py")
    assert result.ok is False
    assert "error[permission_denied]" in result.output


def test_binary_file_is_refused_with_a_stable_code(tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"\x00\xff\xfe\x01binary")

    result = _read(tmp_path, "bin.dat")

    assert result.ok is False
    assert "error[binary_file]" in result.output


def test_read_failure_echoes_the_caller_path_not_the_resolved_one(
    tmp_path: Path,
) -> None:
    result = _read(tmp_path, "missing.py")

    assert "missing.py" in result.output
    assert str(tmp_path) not in result.output


def test_ordinary_read_is_unaffected(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("print('hi')\n", encoding="utf-8")

    result = _read(tmp_path, "real.py")

    assert result.ok is True
    assert "print('hi')" in result.output


def test_tool_crash_does_not_leak_absolute_paths_or_traceback(
    tmp_path: Path,
) -> None:
    marker = str(tmp_path / "SECRET_ABSOLUTE_DIR")

    def crash(_input, _context):
        raise RuntimeError(f"failed while reading {marker}/inner.py")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="boomtool",
                description="d",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=crash,
            )
        ]
    )

    result = registry.execute(
        "boomtool", {}, ToolContext(cwd=str(tmp_path), permissions=None)
    )

    assert result.ok is False
    assert "SECRET_ABSOLUTE_DIR" not in result.output
    assert str(tmp_path) not in result.output
    assert "Traceback" not in result.output
    assert 'File "' not in result.output
    # Still actionable: which tool, and what kind of failure.
    assert "error[tool_crashed]" in result.output
    assert "boomtool" in result.output
    assert "RuntimeError" in result.output


def test_permission_denial_keeps_typed_semantics_without_leaking_reason(
    tmp_path: Path,
    caplog,
) -> None:
    from minicode.permissions import PermissionDeniedError

    marker = str(tmp_path / "SECRET_COMMAND")

    def deny(_input, _context):
        raise PermissionDeniedError(marker, code="command_denied")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="policy_tool",
                description="d",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=deny,
            )
        ]
    )

    result = registry.execute(
        "policy_tool", {}, ToolContext(cwd=str(tmp_path), permissions=None)
    )

    assert result.ok is False
    assert result.output.startswith("error[permission_denied]:")
    assert marker not in result.output
    assert "direct command" in result.output
    assert marker not in caplog.text
    assert "crashed" not in caplog.text.lower()


def test_arbitrary_exception_cannot_claim_a_model_safe_projection(
    tmp_path: Path,
) -> None:
    marker = str(tmp_path / "SECRET_PROJECTED_PATH")

    class UntrustedProjection(RuntimeError):
        def tool_output(self, _tool_name: str) -> str:
            return f"error[permission_denied]: leaked {marker}"

    def crash(_input, _context):
        raise UntrustedProjection("boom")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="untrusted_tool",
                description="d",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=crash,
            )
        ]
    )

    result = registry.execute(
        "untrusted_tool", {}, ToolContext(cwd=str(tmp_path), permissions=None)
    )

    assert result.output.startswith("error[tool_crashed]:")
    assert marker not in result.output
