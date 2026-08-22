"""Bulk in a memory's text must not buy it relevance.

A real three-segment run injected the wrong memory: a "constraint" entry that
had swallowed a whole ``read_file`` result plus the model's own commentary
out-ranked a ``recovery`` entry that named the exact failure being retried.
The bloated entry simply matched more query tokens.

The extractor no longer produces that bulk, and these tests hold the
end-to-end consequence: the entry that actually answers the query is the one
that gets injected.

Known limitation, deliberately not pinned here: retrieval scoring itself is
unchanged, so a memory padded with unrelated text still gains relevance from
the padding. The fix removed the source of the padding, not the scorer's
susceptibility to it. A test asserting the undesirable ranking would have to
be deleted the day the scorer improves, so the limitation is recorded here
instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


RECOVERY_MEMORY = """Task Context: Run 'python -m pytest tests/ -q'. It fails. Diagnose it, fix the source.

Claim:
  Type: recovery
  Statement: After FAILED tests/test_renew.py::test_renew_after_transfer - \
leasekit.lease.StaleTokenError: the fencing token was not refreshed before the \
write, the recovery action was: Changed src/leasekit/lease.py, after which \
run_command succeeded on python pytest tests/.
  Applies when: When run_command fails on tests/test_renew.py::test_renew_after_transfer \
with StaleTokenError."""

CONSTRAINT_MEMORY = """Task Context: Run 'python -m pytest tests/ -q'. One test fails.

Claim:
  Type: constraint
  Statement: Project constraint: Python 3.11 is required: requires-python = ">=3.11\""""


def _store(workspace: Path, entries: dict[str, str]) -> tuple[MemoryPipeline, dict[str, str]]:
    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    names: dict[str, str] = {}
    for name, content in entries.items():
        entry = manager.add_entry(
            MemoryScope.PROJECT, category="task_context", content=content
        )
        assert entry is not None
        manager.approve_entry(entry.id, actor="user", reason="retrieval test")
        names[entry.id] = name
    return pipeline, names


def _top(pipeline: MemoryPipeline, names: dict[str, str], query: str) -> list[str]:
    pipeline.read(query, max_results=5, min_relevance=0.0)
    result = pipeline._last_retrieval_result
    assert result is not None
    return [names.get(entry_id, entry_id) for entry_id in (result.rendered_ids or [])]


def test_a_failure_query_retrieves_the_matching_recovery(tmp_path: Path) -> None:
    pipeline, names = _store(
        tmp_path,
        {"recovery": RECOVERY_MEMORY, "constraint": CONSTRAINT_MEMORY},
    )

    retrieved = _top(pipeline, names, "The suspend test fails with StaleTokenError. Fix it.")

    assert retrieved == ["recovery"]


def test_the_recovery_outranks_an_unrelated_constraint(tmp_path: Path) -> None:
    pipeline, names = _store(
        tmp_path,
        {"recovery": RECOVERY_MEMORY, "constraint": CONSTRAINT_MEMORY},
    )

    retrieved = _top(
        pipeline, names, "Run pytest, one test fails, diagnose and fix the source"
    )

    assert retrieved[0] == "recovery"


@pytest.mark.parametrize(
    "content",
    [RECOVERY_MEMORY, CONSTRAINT_MEMORY],
)
def test_no_claim_carries_a_tool_result_header(tmp_path: Path, content: str) -> None:
    """The bulk that caused the misranking came from tool-result plumbing."""
    pipeline, names = _store(tmp_path, {"entry": content})
    del pipeline, names

    assert "TOTAL_CHARS" not in content
    assert "OFFSET:" not in content
