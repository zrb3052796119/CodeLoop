#!/usr/bin/env python3
"""Offline adaptive-delegation demo. No model call and no network.

Print one decision per canned request. Exit 1 if a decision drifts from the
expected mode. The parent-loop gate that enforces workflow is covered by
tests/test_adaptive_delegation.py; this script only shows the policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minicode.delegation_policy import decide_delegation  # noqa: E402

# (label, request, expected mode) with routing on and no host-owned spec.
CASES: tuple[tuple[str, str, str], ...] = (
    ("trivial-edit", "Fix the off-by-one in hold() in stock.py.", "solo"),
    ("typo-two-files", "Fix the typo in stock.py and test_public.py.", "solo"),
    ("readonly-two-files", "What do stock.py and test_public.py do?", "solo"),
    (
        "hypothetical-edit",
        "If I edit stock.py, do I also need to edit test_public.py?",
        "solo",
    ),
    (
        "explain-only",
        "Don't change stock.py or test_public.py; just explain the difference.",
        "solo",
    ),
    (
        "broad-survey",
        "How does authentication work across the repository?",
        "explore",
    ),
    (
        "multi-file",
        "Implement hold and release in stock.py and test_public.py.",
        "workflow",
    ),
    (
        "howto-multi-file",
        "How do I implement hold and release in stock.py and test_public.py?",
        "workflow",
    ),
    (
        "single-file-redesign",
        "Redesign hold and release in stock.py: reserve then commit, "
        "and keep the public invariants.",
        "workflow",
    ),
    (
        "explicit-subagent",
        "Use a subagent to implement hold in stock.py.",
        "workflow",
    ),
)


def evaluate() -> list[str]:
    """Return labels whose decision does not match the expected mode."""

    failures: list[str] = []
    for label, text, expected in CASES:
        decision = decide_delegation(
            text, routing_enabled=True, implementation_owned=False,
        )
        mark = "ok" if decision.mode == expected else "FAIL"
        print(f"{mark}\t{expected}\t{decision.mode}\t{decision.reason}\t{label}")
        if decision.mode != expected:
            failures.append(label)
    return failures


def main() -> int:
    print("adaptive delegation demo (offline, no model call)")
    failures = evaluate()
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    print("solo stays with the parent; workflow is the multi-file or redesign handoff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
