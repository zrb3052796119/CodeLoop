from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

from minicode.tooling import ToolDefinition, ToolResult
from minicode.workspace import resolve_tool_path

DEFAULT_READ_LIMIT = 8000
MAX_READ_LIMIT = 20000

# 文件内容缓存，避免重复读取同一文件
# 缓存键：(文件路径，修改时间) -> (内容, 缓存时间)
_file_cache: dict[tuple[str, float], tuple[str, float]] = {}
_FILE_CACHE_TTL = 2.0  # 缓存有效期 2 秒
# read_file 是并发批量执行的只读工具；缓存是进程级共享状态，必须加锁，
# 否则一个线程在清理时迭代 dict、另一个线程插入会抛
# "dictionary changed size during iteration" 并被误报为 tool_crashed。
_file_cache_lock = threading.Lock()
# Only whole-file reads of small files go through the cache. Windowed reads
# of large files cache nothing, so a multi-gigabyte file never lands in
# process memory whole.
_MAX_CACHED_FILE_BYTES = 262_144
# Char-skip granularity for windowed reads: bounded transient memory while
# seeking to the requested offset.
_SKIP_CHUNK_CHARS = 65_536


class _ReadFailure(Exception):
    """A read that must be reported, not silently rendered as empty."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _read_failure_for(error: OSError) -> _ReadFailure:
    if isinstance(error, FileNotFoundError):
        return _ReadFailure("not_found", "File does not exist.")
    if isinstance(error, IsADirectoryError):
        return _ReadFailure("not_a_file", "Path is a directory, not a file.")
    if isinstance(error, PermissionError):
        return _ReadFailure("permission_denied", "File is not readable.")
    return _ReadFailure("unreadable", "File could not be read.")


def _get_cached_file_content(target: Path) -> str:
    """获取小文件整体内容，使用缓存避免重复读取

    Read failures are raised, never returned as "". Collapsing a missing
    file, a directory, or a permission error into an empty string made
    read_file answer ok=True with TOTAL_CHARS: 0 — byte-identical to a
    genuinely empty file — so the model could not tell "this file does not
    exist" from "this file is empty" and would act on the wrong one.
    """
    try:
        file_stat = target.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise _ReadFailure("not_a_file", "Path is not a regular file.")
        mtime = file_stat.st_mtime
        cache_key = (str(target), mtime)

        with _file_cache_lock:
            cached = _file_cache.get(cache_key)
            if cached is not None:
                content, cache_time = cached
                if time.monotonic() - cache_time <= _FILE_CACHE_TTL:
                    return content

        # 读取（锁外执行 I/O），成功后再入缓存
        content = target.read_text(encoding="utf-8")
        with _file_cache_lock:
            now = time.monotonic()
            expired_keys = [
                key
                for key, (_, cached_at) in _file_cache.items()
                if now - cached_at > _FILE_CACHE_TTL
            ]
            for key in expired_keys:
                del _file_cache[key]
            _file_cache[cache_key] = (content, now)
        return content
    except _ReadFailure:
        raise
    except UnicodeDecodeError:
        raise
    except OSError as error:
        raise _read_failure_for(error) from error


def _read_text_window(
    target: Path, start: int, count: int
) -> tuple[str, bool, int | None]:
    """Read at most ``count`` characters starting at ``start``.

    Streams instead of loading the whole file, so ``read_file`` with an
    offset/limit window on a multi-gigabyte file costs only the window plus
    one skip chunk of memory. The third value is the exact normalized character
    count when EOF was observed, including when ``start`` is beyond EOF.
    """
    try:
        with target.open("r", encoding="utf-8") as handle:
            remaining = start
            while remaining > 0:
                skipped = handle.read(min(remaining, _SKIP_CHUNK_CHARS))
                if not skipped:
                    return "", False, start - remaining
                remaining -= len(skipped)
            window = handle.read(count + 1)
    except UnicodeDecodeError:
        raise
    except OSError as error:
        raise _read_failure_for(error) from error
    more = len(window) > count
    return window[:count], more, None if more else start + len(window)


def _read_line_window(
    target: Path,
    start_line: int,
    end_line: int,
    count: int,
) -> tuple[str, int, bool, int | None]:
    """Read an inclusive one-based range without materializing long lines.

    Character offsets and CRLF normalization match character mode. Truncation
    describes the requested range, not unrelated lines after ``end_line``.
    """
    try:
        with target.open("r", encoding="utf-8") as handle:
            line, offset = 1, 0
            while line < start_line:
                skipped = handle.readline(_SKIP_CHUNK_CHARS)
                if not skipped:
                    return "", offset, False, offset
                offset += len(skipped)
                if skipped.endswith("\n"):
                    line += 1
            chunks, remaining = [], count
            while line <= end_line and remaining:
                chunk = handle.readline(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
                if chunk.endswith("\n"):
                    line += 1
            content = "".join(chunks)
            more = bool(handle.read(1))
            truncated = not remaining and line <= end_line and more
            total = None if more else offset + len(content)
            return content, offset, truncated, total
    except UnicodeDecodeError:
        raise
    except OSError as error:
        raise _read_failure_for(error) from error


def _validate(input_data: dict) -> dict:
    path = input_data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("path is required")
    offset = int(input_data.get("offset", 0))
    limit = int(input_data.get("limit", DEFAULT_READ_LIMIT))
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit < 1 or limit > MAX_READ_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_READ_LIMIT}")
    if "start_line" in input_data or "end_line" in input_data:
        if "offset" in input_data:
            raise ValueError("start_line/end_line cannot be combined with offset")
        start_line, end_line = input_data.get("start_line"), input_data.get("end_line")
        if type(start_line) is not int or type(end_line) is not int:
            raise ValueError("start_line and end_line are both required integers")
        if not 1 <= start_line <= end_line:
            raise ValueError("line range must satisfy 1 <= start_line <= end_line")
        return {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "limit": limit,
        }
    return {"path": path, "offset": offset, "limit": limit}


def _run(input_data: dict, context) -> ToolResult:
    try:
        target = resolve_tool_path(context, input_data["path"], "read")
    except PermissionError as error:
        return ToolResult(ok=False, output=f"error[permission_denied]: {error}")

    line_mode = "start_line" in input_data
    offset = input_data.get("offset", 0)
    limit = input_data["limit"]

    try:
        try:
            file_stat = target.stat()
            file_size = file_stat.st_size
        except OSError as error:
            raise _read_failure_for(error) from error

        if not stat.S_ISREG(file_stat.st_mode):
            raise _ReadFailure("not_a_file", "Path is not a regular file.")
        if line_mode:
            chunk, offset, truncated, eof_total = _read_line_window(
                target,
                input_data["start_line"],
                input_data["end_line"],
                limit,
            )
            end = offset + len(chunk)
            total_chars = str(eof_total) if eof_total is not None else f">{end}"
        elif offset == 0 and file_size <= _MAX_CACHED_FILE_BYTES:
            content = _get_cached_file_content(target)
            end = min(len(content), limit)
            chunk = content[:end]
            total_chars: str = str(len(content))
            truncated = end < len(content)
        else:
            chunk, more, eof_total = _read_text_window(target, offset, limit)
            end = offset + len(chunk)
            # EOF reached inside the window: the total is exact; otherwise
            # only a lower bound is known without reading the rest.
            total_chars = str(eof_total) if eof_total is not None else f">{end}"
            truncated = more
    except _ReadFailure as failure:
        # Echo the caller's own path, never the resolved absolute one.
        return ToolResult(
            ok=False,
            output=f"error[{failure.code}]: {failure.message} ({input_data['path']})",
        )
    except UnicodeDecodeError:
        return ToolResult(
            ok=False,
            output=(
                f"error[binary_file]: File appears to be binary and cannot be "
                f"read as text. ({input_data['path']})"
            ),
        )

    continuation = "no"
    if truncated:
        continuation = f"yes - call read_file again with offset {end}"
        if line_mode:
            if chunk.endswith("\n"):
                arguments = {
                    "path": input_data["path"],
                    "start_line": input_data["start_line"] + chunk.count("\n"),
                    "end_line": input_data["end_line"],
                    "limit": limit,
                }
                continuation = "yes - next read_file arguments: "
            else:
                arguments = {"path": input_data["path"], "offset": end, "limit": limit}
                continuation = (
                    "yes - switch to character paging; omit start_line and end_line; "
                    "next read_file arguments: "
                )
            continuation += json.dumps(arguments, ensure_ascii=False)
    header_fields = [
        f"FILE: {input_data['path']}",
        f"OFFSET: {offset}",
        f"END: {end}",
        f"TOTAL_CHARS: {total_chars}",
        f"TRUNCATED: {continuation}",
    ]
    if line_mode:
        first = input_data["start_line"] if chunk else "none"
        last = (
            input_data["start_line"] + chunk.count("\n") - int(chunk.endswith("\n"))
            if chunk
            else "none"
        )
        header_fields.extend(
            [
                f"REQUESTED_LINES: {input_data['start_line']}-{input_data['end_line']} (inclusive)",
                (f"RETURNED_LINES: {first}-{last}" if chunk else "RETURNED_LINES: none")
                + (
                    " (last line may be partial)"
                    if truncated and not chunk.endswith("\n")
                    else ""
                ),
            ]
        )
    header = "\n".join([*header_fields, ""])
    return ToolResult(ok=True, output=header + chunk)


read_file_tool = ToolDefinition(
    name="read_file",
    description=(
        "Read a UTF-8 text file relative to the workspace root. "
        "Use offset/limit for character paging, or start_line/end_line for an "
        "inclusive one-based line range (for example a grep match); do not mix "
        "line bounds with offset. limit is a character safety cap in either mode. "
        "Returns at most 20000 body characters with unnumbered exact text. "
        "CRLF is normalized to LF. When TRUNCATED is yes, follow its continuation "
        "arguments. Line reads retain the requested end_line at complete line "
        "boundaries; a partial line requires character paging without line bounds."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the UTF-8 text file.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": (
                    "Zero-based character offset, not a line number or byte "
                    "offset. Start at 0; use the previous END to read the next page."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_READ_LIMIT,
                "default": DEFAULT_READ_LIMIT,
                "description": (
                    "Maximum body characters in either reading mode, not lines. "
                    "Default 8000; allowed range 1 to 20000."
                ),
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line, one-based and inclusive. Requires end_line; cannot be combined with offset.",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last line, one-based and inclusive; must be >= start_line. Requires start_line; cannot be combined with offset.",
            },
        },
        "required": ["path"],
        "oneOf": [
            {"required": ["start_line", "end_line"], "not": {"required": ["offset"]}},
            {
                "not": {
                    "anyOf": [{"required": ["start_line"]}, {"required": ["end_line"]}]
                }
            },
        ],
    },
    validator=_validate,
    run=_run,
)
