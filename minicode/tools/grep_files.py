"""Grep tool — search file contents with regex, glob filtering, and context lines.

Inspired by Claude Code's Grep tool which uses ripgrep-level search
with AST-aware filtering, glob patterns, and context lines.
"""
from __future__ import annotations

import fnmatch
import os
import re
import stat
from pathlib import Path
from typing import Any

from minicode.permissions import PermissionDeniedError
from minicode.tooling import ToolDefinition, ToolResult
from minicode.workspace import resolve_tool_path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox',
    'dist', 'build', '.hg', '.svn', '.next', '.nuxt', 'target',
    'vendor', 'Pods', '.dart_tool', '.gradle', '.idea', '.vscode',
    'coverage', '.coverage', 'htmlcov', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', '.pytype',
})

# Binary file extensions to skip
BINARY_EXTENSIONS = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
    '.mp3', '.mp4', '.wav', '.avi', '.mov', '.mkv', '.flv',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.so', '.dll', '.dylib', '.exe', '.bin', '.dat',
    '.pyc', '.pyo', '.class', '.o', '.obj',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.sqlite', '.db', '.lock',
})

MAX_FILES = 5000
MAX_RESULTS = 200
MAX_RESULT_SIZE = 50_000  # chars
MAX_CONTEXT_LINES = 20


# ---------------------------------------------------------------------------
# Glob matching
# ---------------------------------------------------------------------------

def _matches_glob(path: Path, include_globs: list[str] | None, exclude_globs: list[str] | None) -> bool:
    """Check if a path matches include/exclude glob patterns.
    
    Args:
        path: File path relative to search root
        include_globs: If provided, file must match at least one
        exclude_globs: If provided, file must NOT match any
    """
    posix_path = path.as_posix()
    name = path.name
    
    if exclude_globs:
        for pattern in exclude_globs:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(posix_path, pattern):
                return False
    
    if include_globs:
        for pattern in include_globs:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(posix_path, pattern):
                return True
        return False  # Didn't match any include pattern
    
    return True


# ---------------------------------------------------------------------------
# Search logic
# ---------------------------------------------------------------------------

def _search_file(
    file_path: Path,
    regex: re.Pattern,
    context_lines: int,
    root: Path,
) -> list[dict[str, Any]] | None:
    """Search a single file for regex matches with context.
    
    Returns list of match dicts, or None if file can't be read.
    """
    # Skip binary files
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return None
    
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None
    
    # Text I/O already normalizes CRLF/CR. Use the same LF line boundaries as
    # read_file: splitlines() also splits form-feed and Unicode string data.
    lines = content.split("\n")
    if lines[-1] == "":
        lines.pop()
    matches = []
    
    for line_num, line in enumerate(lines, start=1):
        if regex.search(line):
            # Build context
            context_before = []
            context_after = []
            
            if context_lines > 0:
                for i in range(max(0, line_num - 1 - context_lines), line_num - 1):
                    context_before.append({
                        "line": i + 1,
                        "text": lines[i],
                        "is_match": False,
                    })
                for i in range(line_num, min(len(lines), line_num + context_lines)):
                    context_after.append({
                        "line": i + 1,
                        "text": lines[i],
                        "is_match": False,
                    })
            
            matches.append({
                "line": line_num,
                "text": line,
                "is_match": True,
                "context_before": context_before,
                "context_after": context_after,
            })
    
    return matches if matches else None


def _format_results(
    results: list[tuple[Path, list[dict[str, Any]]]],
    root: Path,
    max_results: int = MAX_RESULTS,
) -> str:
    """Format search results for output.
    
    Format: path:line:content (with optional context lines)
    """
    output_parts = []
    total_matches = 0
    
    for file_path, matches in results:
        if total_matches >= max_results:
            break
        
        rel_path = file_path.relative_to(root).as_posix()
        
        selected = matches[:max_results - total_matches]
        total_matches += len(selected)
        if not any(m.get("context_before") or m.get("context_after") for m in selected):
            # Preserve the established no-context layout exactly.
            for match in selected:
                output_parts.append(f"{rel_path}:{match['line']}: {match['text']}")
                if len(matches) > 1:
                    output_parts.append("")
            continue
        # Merge overlapping/adjacent windows while retaining every actual match
        # marker. A match that first appeared as another match's context must
        # not be downgraded to a context-only line.
        visible: dict[int, dict[str, Any]] = {}
        for match in selected:
            for item in [*match.get("context_before", []), match, *match.get("context_after", [])]:
                previous = visible.get(item["line"])
                if previous is None or item.get("is_match"):
                    visible[item["line"]] = item
        previous_line = None
        for number, item in sorted(visible.items()):
            if previous_line is not None and number > previous_line + 1:
                output_parts.append("")
            gap = "" if item.get("is_match") else " "
            output_parts.append(f"{rel_path}:{number}: {gap}{item['text']}")
            previous_line = number
        output_parts.append("")
    
    return "\n".join(output_parts)


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

def _validate(input_data: dict) -> dict:
    pattern = input_data.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern is required")
    
    # Validate regex
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")
    
    # Parse include/exclude globs
    include = input_data.get("include")
    if isinstance(include, str):
        include = [include]
    elif include is None:
        include = None
    elif not isinstance(include, list):
        include = None
    
    exclude = input_data.get("exclude")
    if isinstance(exclude, str):
        exclude = [exclude]
    elif exclude is None:
        # Default excludes
        exclude = []
    elif not isinstance(exclude, list):
        exclude = []

    context_lines = input_data.get("context_lines", 0)
    if type(context_lines) is not int or not 0 <= context_lines <= MAX_CONTEXT_LINES:
        raise ValueError(f"context_lines must be an integer between 0 and {MAX_CONTEXT_LINES}")
    
    return {
        "pattern": pattern,
        "path": input_data.get("path", "."),
        "include": include,
        "exclude": exclude,
        "context_lines": context_lines,
        "case_sensitive": bool(input_data.get("case_sensitive", False)),
    }


def _run(input_data: dict, context) -> ToolResult:
    try:
        root = resolve_tool_path(context, input_data["path"], "search")
        mode = root.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return ToolResult(
            ok=False,
            output=(
                "error[not_found]: Search path does not exist. "
                f"({input_data['path']})"
            ),
        )
    except (PermissionError, PermissionDeniedError):
        return ToolResult(
            ok=False,
            output="error[permission_denied]: Search path is not accessible.",
        )
    except OSError:
        return ToolResult(
            ok=False,
            output=(
                "error[unreadable]: Search path could not be inspected. "
                f"({input_data['path']})"
            ),
        )
    single_file = root if stat.S_ISREG(mode) else None
    if single_file is not None:
        # Use the containing directory as the relative root, so glob matching,
        # line numbers and formatting use the same path as a directory search.
        root = single_file.parent
    elif not stat.S_ISDIR(mode):
        return ToolResult(
            ok=False,
            output=(
                "error[not_searchable]: Path is not a file or directory. "
                f"({input_data['path']})"
            ),
        )
    
    # Compile regex
    flags = 0 if input_data.get("case_sensitive", False) else re.IGNORECASE
    try:
        regex = re.compile(input_data["pattern"], flags)
    except re.error as e:
        return ToolResult(ok=False, output=f"Invalid regex: {e}")
    
    context_lines = input_data.get("context_lines", 0)
    include_globs = input_data.get("include", [])
    exclude_globs = input_data.get("exclude", [])
    
    # Collect files, pruning skip-listed and hidden directories during the
    # walk. rglob("*") materializes the entire tree — node_modules and .git
    # included — before any filter runs, so a large repo paid full traversal
    # cost regardless of MAX_FILES.
    def permitted_path(path: Path) -> Path | None:
        # Root permission does not authorize every descendant: Assignment
        # exclusions and symlinks must be checked before traversal or reading.
        # Cancellation/deadline exceptions deliberately propagate.
        try:
            return resolve_tool_path(context, str(path), "search")
        except (PermissionError, PermissionDeniedError, OSError):
            return None

    all_files: list[Path] = []
    if single_file is not None:
        all_files.append(single_file)
    else:
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if name not in SKIP_DIRS
                    and not name.startswith(".")
                    and permitted_path(Path(dirpath) / name) is not None
                ]
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    all_files.append(Path(dirpath) / filename)
                if len(all_files) >= MAX_FILES:
                    break
        except PermissionError:
            return ToolResult(ok=False, output=f"Permission denied: {root}")
        except OSError as e:
            return ToolResult(ok=False, output=f"Cannot read directory: {e}")
    all_files.sort()

    # Search
    results: list[tuple[Path, list[dict[str, Any]]]] = []
    file_count = 0
    skipped = 0
    total_matches = 0

    for file_path in all_files:
        # File limit
        if file_count >= MAX_FILES:
            break
        
        # Glob filtering
        rel_path = file_path.relative_to(root)
        if not _matches_glob(rel_path, include_globs, exclude_globs if exclude_globs else None):
            skipped += 1
            continue

        resolved_file = permitted_path(file_path)
        if resolved_file is None:
            skipped += 1
            continue
        if not resolved_file.is_file():
            continue
        
        file_count += 1
        
        # Search file
        matches = _search_file(resolved_file, regex, context_lines, root)
        if matches:
            results.append((file_path, matches))
            total_matches += len(matches)
            if total_matches >= MAX_RESULTS:
                break
    
    # Format output
    if not results:
        return ToolResult(ok=True, output="No matches found.")
    
    output = _format_results(results, root)
    
    # Add summary
    summary = f"\n\n{total_matches} match(es) in {len(results)} file(s)"
    if file_count >= MAX_FILES:
        summary += f" (search stopped at {MAX_FILES} files)"
    if skipped > 0:
        summary += f" ({skipped} file(s) skipped)"
    if len(output) + len(summary) > MAX_RESULT_SIZE:
        notice = f"\n\n... (truncated, maximum output {MAX_RESULT_SIZE} chars)"
        output = output[:MAX_RESULT_SIZE - len(notice) - len(summary)] + notice
    output += summary
    
    return ToolResult(ok=True, output=output)


grep_files_tool = ToolDefinition(
    name="grep_files",
    description=(
        "Search a UTF-8 text file or files under a directory using a regex pattern. "
        "Supports glob-based include/exclude filtering and context lines. "
        "Results are formatted as path:line:content; overlapping context is shown once. "
        "Use read_file start_line/end_line to inspect a matched line range."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": (
                    "File or directory to search (default: current directory). "
                    "For a file, glob filters and result paths are relative "
                    "to its parent directory."
                ),
            },
            "include": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "Glob pattern(s) to include (e.g. '*.py', ['*.ts', '*.tsx']). Only matching files are searched.",
            },
            "exclude": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "Glob pattern(s) to exclude (e.g. '*.test.ts', ['*.min.js', '*.generated.*'])",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of context lines before and after each match (0-20, default: 0). Out-of-range values are rejected, not silently shortened.",
                "minimum": 0,
                "maximum": MAX_CONTEXT_LINES,
                "default": 0,
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive search (default: false)",
            },
        },
        "required": ["pattern"],
    },
    validator=_validate,
    run=_run,
)
