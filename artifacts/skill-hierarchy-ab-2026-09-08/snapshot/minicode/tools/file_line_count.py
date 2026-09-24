from __future__ import annotations

from minicode.tooling import ToolDefinition, ToolResult
from minicode.workspace import resolve_tool_path


def _validate(input_data: dict) -> dict:
    path = input_data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("path is required")
    return {"path": path}


def _run(input_data: dict, context) -> ToolResult:
    target = resolve_tool_path(context, input_data["path"], "read")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(
            ok=False,
            output=f"File {input_data['path']} appears to be binary. Cannot count lines as text.",
        )

    lines = content.splitlines()
    total = len(lines)
    non_empty = sum(1 for line in lines if line.strip())
    empty = total - non_empty

    return ToolResult(
        ok=True,
        output=(
            f"FILE: {input_data['path']}\n"
            f"LINES: {total}\n"
            f"NON_EMPTY_LINES: {non_empty}\n"
            f"EMPTY_LINES: {empty}\n"
            f"CHARACTERS: {len(content)}"
        ),
    )


file_line_count_tool = ToolDefinition(
    name="file_line_count",
    description="Count total, non-empty, and empty lines in a UTF-8 text file relative to the workspace root.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace root"}
        },
        "required": ["path"],
    },
    validator=_validate,
    run=_run,
)
