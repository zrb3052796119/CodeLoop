"""Hand an unfinished workflow candidate back to the parent without approving it."""

from __future__ import annotations

import json
from typing import Mapping


_DIFF_CHARS = 24_000
_REVIEWED_VERDICTS = {"approved", "changes_required", "inconclusive"}


def _bounded_diff(diff: str) -> tuple[str, bool]:
    if len(diff) <= _DIFF_CHARS:
        return diff, False
    head = _DIFF_CHARS // 2
    return diff[:head] + "\n...<truncated>...\n" + diff[-head:], True


def render_unapproved_workflow_diff(
    *,
    diff_document: Mapping[str, object] | None,
    review: Mapping[str, object] | None = None,
    patch_digest: str | None = None,
) -> str:
    """Return a parent-visible section, or empty when no diff was captured."""

    if not isinstance(diff_document, Mapping):
        return ""
    diff = diff_document.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        return ""
    verdict = review.get("verdict") if isinstance(review, Mapping) else None
    source = "reviewed" if verdict in _REVIEWED_VERDICTS else "executor"
    shown, text_truncated = _bounded_diff(diff)
    header = {
        "approval": "unapproved",
        "source": source,
        "promoted": False,
        "patchDigest": diff_document.get("patchDigest") or patch_digest,
        "diffComplete": diff_document.get("complete") is True and not text_truncated,
        "encoding": diff_document.get("encoding") or "utf-8",
        "reviewVerdict": verdict if isinstance(verdict, str) else None,
    }
    return (
        "=== UNAPPROVED WORKFLOW DIFF v1 ===\n"
        "This candidate was not approved and was not promoted. "
        "Continue from it solo; do not treat it as a committed workflow result.\n"
        + json.dumps(header, ensure_ascii=False, sort_keys=True)
        + "\n"
        + shown
    )


def render_unapproved_candidate(candidate: Mapping[str, object] | None) -> str:
    """Render a failure-candidate capture when the frozen diff was not sealed."""

    if not isinstance(candidate, Mapping):
        return ""
    files = candidate.get("files")
    if not isinstance(files, list):
        return ""
    parts: list[str] = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        diff = item.get("diff")
        if not isinstance(diff, str) or not diff.strip():
            continue
        path = item.get("path") if isinstance(item.get("path"), str) else "unknown"
        parts.append(f"diff -- candidate {path}\n{diff}")
    if not parts:
        return ""
    digest = candidate.get("baseDigest")
    return render_unapproved_workflow_diff(
        diff_document={
            "diff": "\n".join(parts),
            "complete": False,
            "encoding": "utf-8",
            "patchDigest": digest if isinstance(digest, str) else None,
        },
        review=None,
    )


__all__ = ["render_unapproved_candidate", "render_unapproved_workflow_diff"]
