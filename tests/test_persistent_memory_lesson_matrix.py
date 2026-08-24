"""Deterministic cross-session acceptance for every durable lesson family."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest

from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


FIXTURE = Path(__file__).parent / "fixtures" / "persistent_memory_lesson_matrix.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_matrix_has_three_distinct_cases_for_each_supported_lesson_family() -> None:
    counts = Counter(case["family"] for case in CASES)

    assert counts == {
        "path_resource_recovery": 3,
        "command_recovery": 3,
        "code_fix_recovery": 3,
        "stable_verification_rule": 3,
        "project_constraint_decision": 3,
    }
    assert len({case["id"] for case in CASES}) == 15
    assert len({case["content"] for case in CASES}) == 15


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[case["id"] for case in CASES],
)
def test_lesson_persists_across_manager_restart_and_is_injected(
    tmp_path: Path,
    case: dict[str, object],
) -> None:
    writer = MemoryManager(project_root=tmp_path)
    entry = writer.add_entry(
        MemoryScope.PROJECT,
        str(case["category"]),
        str(case["content"]),
        tags=[str(case["tag"])],
        source="deterministic_lesson_matrix",
        provenance={"case_id": str(case["id"]), "family": str(case["family"])},
    )
    assert entry is not None
    assert entry.is_active

    # A fresh manager is the cross-conversation boundary: only durable state
    # written by the first instance is available to this retrieval.
    reader = MemoryManager(project_root=tmp_path)
    pipeline = MemoryPipeline(reader)
    pipeline.initialize(
        workspace_path=str(tmp_path),
        enable_reranker=False,
        enable_vector=False,
    )
    messages = pipeline.inject(
        str(case["query"]),
        [str(value) for value in case["currentFiles"]],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )

    assert pipeline.last_retrieval_result is not None
    assert pipeline.last_retrieval_result.rendered_ids == (entry.id,)
    assert str(case["content"]) in messages[0]["content"]
    persisted = reader.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert persisted.retrieval_count == 1
    assert persisted.injection_count == 1
