"""Lossless model views of already-frozen Executor contracts.

Only repeated display text is replaced with references. Assignment authority,
artifact bytes, validation, permissions, and budgets remain outside this view.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from minicode.multi_agent_contracts import Assignment, ImplementationOrder, WorkerRole

_LIST_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?:[-*+•][ \t]+|"
    r"(?:[0-9]+|[一二三四五六七八九十百零〇]+)(?:、[ \t]*|[.．)][ \t]+)|"
    r"[（(](?:[0-9]+|[一二三四五六七八九十百零〇]+)[）)][ \t]*)"
)
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def _safe_label_boundary(task: str, start: int, end: int) -> bool:
    """Labels may precede whole prose blocks, never inline or fenced examples."""
    if start != task.rfind("\n", 0, start) + 1:
        return False
    line_end = task.find("\n", end)
    if task[end:line_end if line_end >= 0 else len(task)].strip():
        return False
    lines = task.split("\n")
    first = task.count("\n", 0, start)
    last = task.count("\n", 0, end)
    first_item = _LIST_ITEM.match(lines[first])
    if first and lines[first - 1].strip() and first_item is None:
        return False
    if last + 1 < len(lines) and lines[last + 1].strip():
        next_item = _LIST_ITEM.match(lines[last + 1])
        # A wrapped line or nested child means this item is incomplete.
        if next_item is None or next_item["indent"]:
            return False
    fence: str | None = None
    for index, line in enumerate(lines[:last + 1]):
        marker = _FENCE.match(line)
        if index == first and (fence is not None or marker is not None):
            return False
        if fence is None:
            if marker is not None:
                fence = marker[1]
        elif (marker is not None and marker[1][0] == fence[0]
              and len(marker[1]) >= len(fence) and not line[marker.end():].strip()):
            fence = None
    return fence is None


@dataclass(frozen=True)
class ExecutorInputProjection:
    original_task: str
    assignment: dict[str, object]
    order: dict[str, object]

    def render_contracts(self, *, reviewer: bool = False) -> str:
        return (
            ("\n\nREVIEWER INPUT VIEW:\n" if reviewer else "\n\nEXECUTOR INPUT VIEW:\n")
            + "These are display projections of the frozen contracts, not new authority. "
            "The original task appears once above; inserted [criterion ID] labels identify "
            "the exact original clauses referenced by textRef below. originalTaskRange "
            "uses zero-based Unicode character offsets [start,end) before labels were "
            "inserted. originalTaskRef refers to the complete original task. "
            "Unmatched or ambiguous clauses and distinct step actions remain verbatim. "
            "Full contracts remain available through the listed artifact references."
            + ("\n\nASSIGNMENT:\n" if reviewer else "\n\nEXECUTOR ASSIGNMENT:\n")
            + json.dumps(self.assignment, ensure_ascii=False, sort_keys=True)
            + "\n\nLOCKED IMPLEMENTATION ORDER:\n"
            + json.dumps(self.order, ensure_ascii=False, sort_keys=True)
        )


def project_executor_input(
    original_task: str,
    assignment: Assignment,
    order: ImplementationOrder,
    *,
    assignment_artifact_ref: str,
) -> ExecutorInputProjection:
    """Reference only uniquely matched, nonoverlapping original text.

    No summarization, whitespace normalization, fuzzy matching, or semantic
    inference occurs. Ambiguous matches and spans without safe paragraph/list
    boundaries stay fully written in the view. The model never needs offsets:
    each referenced clause receives its readable criterion ID at the source.
    """
    if (order.assignment_id != assignment.assignment_id and not (
        assignment.role is WorkerRole.REVIEWER and order.order_id in assignment.input_refs
    )):
        raise ValueError("Executor input order belongs to another Assignment")
    assignment_view = assignment.to_dict()
    order_view = order.to_dict()
    criteria = assignment_view["acceptanceCriteria"]
    candidates: dict[str, tuple[int, int]] = {}
    for criterion in criteria:
        text = criterion["text"]
        start = original_task.find(text)
        if start >= 0 and original_task.find(text, start + 1) < 0:
            candidates[criterion["id"]] = (start, start + len(text))
    overlaps = {
        left_id
        for left_id, (start, end) in candidates.items()
        for right_id, (other_start, other_end) in candidates.items()
        if left_id != right_id and start < other_end and other_start < end
    }
    spans = {
        key: span for key, span in candidates.items()
        if key not in overlaps and _safe_label_boundary(original_task, *span)
    }
    pieces: list[str] = []
    previous = 0
    for criterion_id, (start, _end) in sorted(spans.items(), key=lambda item: item[1]):
        pieces.extend((original_task[previous:start], f"[criterion {criterion_id}]\n"))
        previous = start
    pieces.append(original_task[previous:])
    for criterion in criteria:
        span = spans.get(criterion["id"])
        if span is not None:
            del criterion["text"]
            criterion["textRef"] = f"criterion {criterion['id']} in ORIGINAL TASK"
            criterion["originalTaskRange"] = {"start": span[0], "end": span[1]}
    if assignment_view["objective"] == original_task:
        del assignment_view["objective"]
        assignment_view["originalTaskRef"] = "ORIGINAL TASK"
    for step in order_view["steps"]:
        if step["action"] == original_task:
            del step["action"]
            step["originalTaskRef"] = "ORIGINAL TASK"
    assignment_view.update(
        inputProjectionVersion=1,
        fullAssignmentArtifactRef=assignment_artifact_ref,
    )
    order_view.update(inputProjectionVersion=1, fullOrderArtifactRef=order.order_id)
    return ExecutorInputProjection("".join(pieces), assignment_view, order_view)
