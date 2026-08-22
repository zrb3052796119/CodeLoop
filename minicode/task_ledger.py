"""Parent-owned task state that is projected outside compaction summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from minicode.verification_observation import normalize_verification_payload


TASK_LEDGER_VERSION = 1
_MAX_GOAL_CHARS = 4_000
_MAX_CONSTRAINTS = 8
_MAX_CONSTRAINT_CHARS = 500
_MAX_FACTS = 20
_MAX_ATTEMPTS = 20
_ERROR_CODE_RE = re.compile(r"error\[([a-z_]{1,64})\]")
_CONSTRAINT_RE = re.compile(
    r"\b(?:must|must not|do not|don't|never|required|only)\b|"
    r"必须|不要|不得|不能|只能|仅限",
    re.IGNORECASE,
)


def _ordered_append(items: list[str], value: str, limit: int) -> bool:
    if not value or value in items:
        return False
    items.append(value)
    if len(items) > limit:
        del items[: len(items) - limit]
    return True


def _explicit_constraints(goal: str) -> list[str]:
    constraints: list[str] = []
    for sentence in re.split(r"(?<=[.!?。！？])\s*|\n+", goal):
        text = sentence.strip()
        if text and _CONSTRAINT_RE.search(text):
            _ordered_append(
                constraints,
                text[:_MAX_CONSTRAINT_CHARS],
                _MAX_CONSTRAINTS,
            )
    return constraints


@dataclass(slots=True)
class TaskLedger:
    """A small runtime ledger whose facts come from typed observations only."""

    goal: str
    constraints: list[str] = field(default_factory=list)
    verified_facts: list[str] = field(default_factory=list)
    rejected_attempts: list[str] = field(default_factory=list)
    revision: int = 1

    @classmethod
    def from_messages(
        cls,
        messages: list[dict[str, Any]],
    ) -> "TaskLedger | None":
        goal = next(
            (
                str(message.get("content", "")).strip()
                for message in reversed(messages)
                if message.get("role") == "user"
                and str(message.get("content", "")).strip()
            ),
            "",
        )
        if not goal:
            return None
        bounded_goal = goal[:_MAX_GOAL_CHARS]
        return cls(
            goal=bounded_goal,
            constraints=_explicit_constraints(bounded_goal),
        )

    def record_verification(self, payload: object) -> bool:
        normalized = normalize_verification_payload(payload)
        if normalized is None:
            return False
        fact = (
            f"{normalized['kind']} {normalized['outcome']} "
            f"via {normalized['source']}"
        )
        changed = _ordered_append(self.verified_facts, fact, _MAX_FACTS)
        if changed:
            self.revision += 1
        return changed

    def record_failed_attempt(self, tool_name: object, output: object) -> bool:
        name = str(tool_name or "").strip()
        match = _ERROR_CODE_RE.search(str(output or ""))
        if not name or match is None:
            return False
        attempt = f"{name[:80]}: error[{match.group(1)}]"
        changed = _ordered_append(self.rejected_attempts, attempt, _MAX_ATTEMPTS)
        if changed:
            self.revision += 1
        return changed

    def to_message(self) -> dict[str, object]:
        lines = [
            f"[Task Ledger v{TASK_LEDGER_VERSION} — parent-owned, compaction-immune]",
            f"Revision: {self.revision}",
            "",
            "## Goal (verbatim, bounded)",
            self.goal,
        ]
        if self.constraints:
            lines.extend(["", "## Explicit constraints"])
            lines.extend(f"- {item}" for item in self.constraints)
        if self.verified_facts:
            lines.extend(["", "## Typed verification facts"])
            lines.extend(f"- {item}" for item in self.verified_facts)
        if self.rejected_attempts:
            lines.extend(["", "## Failed or rejected attempts"])
            lines.extend(f"- {item}" for item in self.rejected_attempts)
        return {
            "role": "system",
            "content": "\n".join(lines),
            "_task_ledger": True,
            "_task_ledger_version": TASK_LEDGER_VERSION,
            "_task_ledger_revision": self.revision,
        }

    def reconcile(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace any stale ledger projection with exactly one current copy."""
        current = [message for message in messages if not message.get("_task_ledger")]
        insert_at = 0
        while insert_at < len(current) and current[insert_at].get("role") == "system":
            insert_at += 1
        current.insert(insert_at, self.to_message())
        return current


__all__ = ["TASK_LEDGER_VERSION", "TaskLedger"]
