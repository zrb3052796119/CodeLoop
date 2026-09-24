"""Production-chain retry contract for cross-scope prompt accounting."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


def test_prompt_receipt_retry_completes_partial_durable_accounting_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    project = manager.add_entry(
        MemoryScope.PROJECT,
        "failure-recovery",
        (
            "CROSS_SCOPE_PROMPT_RECEIPT checkout retry requires project "
            "build verification"
        ),
        tags=["checkout", "retry", "verification"],
        source="test",
    )
    user = manager.add_entry(
        MemoryScope.USER,
        "failure-recovery",
        (
            "CROSS_SCOPE_PROMPT_RECEIPT checkout retry requires user durable "
            "accounting verification"
        ),
        tags=["checkout", "retry", "accounting"],
        source="test",
    )
    assert project is not None
    assert user is not None

    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        enable_reranker=False,
        enable_vector=False,
    )

    accounting_observation_ids: list[str | None] = []
    original_accounting = manager.record_retrievals_and_injections

    def track_accounting(
        retrieved_entry_ids: list[str],
        injected_entry_ids: list[str],
        **kwargs: object,
    ) -> None:
        raw_observation_id = kwargs.get("observation_id")
        accounting_observation_ids.append(
            raw_observation_id if isinstance(raw_observation_id, str) else None
        )
        original_accounting(
            retrieved_entry_ids,
            injected_entry_ids,
            **kwargs,
        )

    monkeypatch.setattr(
        manager,
        "record_retrievals_and_injections",
        track_accounting,
    )

    original_atomic_write = manager._atomic_write
    attempted_authority_roots: list[Path] = []

    def fail_second_scope_once(target: Path, content: str) -> None:
        if target.name == "memory.json":
            attempted_authority_roots.append(target)
            if len(attempted_authority_roots) == 2:
                raise OSError("simulated second-scope authority failure")
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", fail_second_scope_once)

    messages = pipeline.inject(
        "CROSS_SCOPE_PROMPT_RECEIPT checkout retry verification accounting",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        max_memories=5,
        max_total_tokens=2_000,
        max_tokens_per_memory=500,
        min_relevance=0.0,
    )
    result = pipeline.last_retrieval_result
    receipt = pipeline.last_committed_prompt_receipt
    expected_ids = {project.id, user.id}

    assert result is not None
    assert set(result.rendered_ids) == expected_ids
    assert receipt is not None
    assert set(receipt.rendered_ids) == expected_ids
    assert receipt.receipt_id
    assert len(attempted_authority_roots) == 2
    assert receipt.prompt_text in str(messages[0]["content"])
    assert set(pipeline.injected_memory_ids) == expected_ids
    assert len(pipeline.injected_memory_ids) == 2

    # The prompt was already materialized, but durable accounting committed
    # only the first scope. Replaying the same receipt must retry persistence
    # without appending to the volatile injected ledger again.
    pipeline.commit_prompt_receipt(receipt, record_retrieval=True)

    assert set(pipeline.injected_memory_ids) == expected_ids
    assert len(pipeline.injected_memory_ids) == 2

    persisted = MemoryManager(project_root=workspace, data_root=data_root)
    durable_counts: dict[str, tuple[int, int]] = {}
    for scope, entry_id in (
        (MemoryScope.PROJECT, project.id),
        (MemoryScope.USER, user.id),
    ):
        entry = persisted.memories[scope]._id_index[entry_id]
        durable_counts[scope.value] = (
            entry.retrieval_count,
            entry.injection_count,
        )

    assert {
        "manager_observation_ids": accounting_observation_ids,
        "durable_counts": durable_counts,
        "volatile_injected_ids": pipeline.injected_memory_ids,
    } == {
        "manager_observation_ids": [
            receipt.receipt_id,
            receipt.receipt_id,
        ],
        "durable_counts": {
            MemoryScope.PROJECT.value: (1, 1),
            MemoryScope.USER.value: (1, 1),
        },
        "volatile_injected_ids": receipt.rendered_ids,
    }
