"""Preserve delegation text as bounded, independently accountable criteria."""

from __future__ import annotations

import re

from minicode.multi_agent_contracts import MAX_WORKFLOW_CRITERIA, _MAX_TEXT


_NUMBER = r"(?:[0-9]+|[一二三四五六七八九十百零〇]+)"
_CLAUSE_START = re.compile(
    rf"^(?P<indent>[ \t]*)(?:[-*+•][ \t]+|{_NUMBER}、[ \t]*|"
    rf"{_NUMBER}[.．)][ \t]+|[（(]{_NUMBER}[）)][ \t]*)"
)
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def criteria_from_prompt(prompt: object) -> list[dict[str, str]]:
    """Split formatting boundaries, without summarizing or inferring coverage.

    Paragraphs and peer list items become criteria. Wrapped lines, nested list
    details and fenced examples remain with their original clause. Only outer
    whitespace is removed, matching the WorkflowRequest text contract.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required to construct acceptance criteria")
    if "\x00" in prompt:
        raise ValueError("prompt must not contain NUL")

    clauses: list[str] = []
    current: list[str] = []
    list_indent: int | None = None
    fence: str | None = None

    def finish() -> None:
        nonlocal list_indent
        if current:
            clauses.append("".join(current).strip())
            current.clear()
        list_indent = None

    for line in prompt.splitlines(keepends=True):
        fence_match = _FENCE.match(line)
        if fence is not None:
            current.append(line)
            if (
                fence_match
                and fence_match[1][0] == fence[0]
                and len(fence_match[1]) >= len(fence)
            ):
                fence = None
            continue
        if fence_match:
            current.append(line)
            fence = fence_match[1]
            continue
        if not line.strip():
            finish()
            continue
        marker = _CLAUSE_START.match(line)
        if marker:
            indent = len(marker["indent"].expandtabs())
            if list_indent is None or indent <= list_indent:
                finish()
                list_indent = indent
        current.append(line)
    finish()

    if len(clauses) > MAX_WORKFLOW_CRITERIA:
        raise ValueError(
            f"{len(clauses)} criteria exceed the limit of {MAX_WORKFLOW_CRITERIA}; "
            "use a bounded delegation without dropping requirements"
        )
    criteria = []
    for index, clause in enumerate(clauses, start=1):
        if len(clause) > _MAX_TEXT:
            raise ValueError(
                f"criterion {index} exceeds {_MAX_TEXT} characters; "
                "split the original requirement into explicit paragraphs or list items"
            )
        criteria.append({"id": f"c{index}", "kind": "behavior", "text": clause})
    return criteria


__all__ = ["criteria_from_prompt"]
