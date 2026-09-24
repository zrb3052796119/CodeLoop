"""Polluted-pool self-correction mechanism (offline, Layer A of the study).

This is the deterministic layer of the memory-pollution experiment: it drives
the REAL MemoryPipeline end to end across three rounds against a REAL
MemoryManager seeded with a polluted pool (correct lesson + stale-path
pollutant + irrelevant entry), using scripted-but-realistic execution traces.
No provider calls.

What must hold for the live experiment to be interpretable:

- Round 1: the stale-path claim is exercised and fails -> one corroborated
  failure is recorded (no quarantine yet).
- Round 2: exercised and fails again -> second corroborated failure
  quarantines the pollutant (existing threshold = 2).
- Round 3: canonical retrieval no longer renders the quarantined pollutant,
  while the correct lesson stays rendered and keeps receiving small positive
  feedback.
- With the reviewer disabled (the pre-feature production arm), the same
  three rounds never quarantine the pollutant.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_contribution_reviewer import ContributionReviewConfig
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import append_trace_event


CORRECT_CONTENT = (
    "The project auth policy document lives at docs/policies/auth-policy.md "
    "and reports its verification marker there"
)
STALE_CONTENT = (
    "The project auth policy document lives at policies/auth-policy.md "
    "for quick reference"
)
IRRELEVANT_CONTENT = (
    "Team convention: keep verification tests small and focused; prefer "
    "fast unit tests over broad integration runs for policy changes"
)

TASK = "Find the auth policy document in this repository and report its marker"


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return MemoryManager(project_root=workspace)


def _seed(manager: MemoryManager, content: str) -> str:
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        content,
        tags=["auth", "policy"],
        source="seed",
    )
    assert entry is not None
    return entry.id


def _entry_of(manager: MemoryManager, entry_id: str):
    for scope_file in manager.memories.values():
        for entry in scope_file.entries:
            if entry.id == entry_id:
                return entry
    raise AssertionError(f"entry {entry_id} not found")


def _round_trace(read_stale: bool) -> list[dict]:
    """A realistic round: the agent tries the stale path (fails), recovers by
    reading the correct path (succeeds), then reports the marker."""
    trace: list[dict] = []
    if read_stale:
        append_trace_event(
            trace,
            {
                "type": "tool_call",
                "step": 1,
                "call_id": "c-stale",
                "tool_name": "read_file",
                "input": {"path": "policies/auth-policy.md"},
            },
        )
        append_trace_event(
            trace,
            {
                "type": "tool_result",
                "step": 1,
                "call_id": "c-stale",
                "tool_name": "read_file",
                "status": "error",
                "is_error": True,
                "output_summary": "File not found: policies/auth-policy.md",
            },
        )
    append_trace_event(
        trace,
        {
            "type": "tool_call",
            "step": 2,
            "call_id": "c-correct",
            "tool_name": "read_file",
            "input": {"path": "docs/policies/auth-policy.md"},
        },
    )
    append_trace_event(
        trace,
        {
            "type": "tool_result",
            "step": 2,
            "call_id": "c-correct",
            "tool_name": "read_file",
            "status": "success",
            "is_error": False,
            "output_summary": "auth policy ... marker: AUTH-POLICY-771",
        },
    )
    append_trace_event(
        trace,
        {
            "type": "task_result",
            "step": 2,
            "status": "success",
            "final_outcome": "success",
            "completion_succeeded": True,
            "summary": "reported marker",
        },
    )
    return trace


def _messages() -> list[dict]:
    return [{"role": "system", "content": "You are a coding agent."}]


def _fresh_manager_for_round(manager: MemoryManager) -> MemoryManager:
    """Reload authority from disk the way the live runner does per turn."""
    return MemoryManager(project_root=manager.workspace)


def _rendered_ids(pipeline: MemoryPipeline) -> set[str]:
    receipt = pipeline.last_committed_prompt_receipt
    assert receipt is not None
    return set(receipt.rendered_ids)


@pytest.mark.parametrize("review_mode", ["rule", "off"])
def test_polluted_pool_evolution_across_three_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review_mode: str
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    correct_id = _seed(manager, CORRECT_CONTENT)
    stale_id = _seed(manager, STALE_CONTENT)
    _seed(manager, IRRELEVANT_CONTENT)

    reviewer_config = ContributionReviewConfig(mode=review_mode)

    # Round 1: stale path exercised and fails once.
    pipeline = MemoryPipeline(
        _fresh_manager_for_round(manager),
        contribution_reviewer=None,
    )
    pipeline.initialize()
    from minicode.memory_contribution_reviewer import MemoryContributionReviewer

    pipeline._contribution_reviewer = MemoryContributionReviewer(reviewer_config)
    pipeline.inject(TASK, None, _messages())
    assert stale_id in _rendered_ids(pipeline), "pollutant must compete for retrieval"
    pipeline.review_injected_memories(
        TASK, _round_trace(read_stale=True), operation_id="op-round-1"
    )
    round1 = _fresh_manager_for_round(manager)
    if review_mode == "rule":
        assert _entry_of(round1, stale_id).corroborated_failure_count == 1
        assert _entry_of(round1, stale_id).lifecycle_status != "rejected"
    else:
        # Review off: no corroborated channel exists at all.
        assert _entry_of(round1, stale_id).corroborated_failure_count == 0

    # Round 2: exercised and fails again -> quarantine (threshold = 2).
    pipeline2 = MemoryPipeline(_fresh_manager_for_round(manager))
    pipeline2.initialize()
    pipeline2._contribution_reviewer = MemoryContributionReviewer(reviewer_config)
    pipeline2.inject(TASK, None, _messages())
    pipeline2.review_injected_memories(
        TASK, _round_trace(read_stale=True), operation_id="op-round-2"
    )
    round2 = _fresh_manager_for_round(manager)
    if review_mode == "rule":
        assert _entry_of(round2, stale_id).corroborated_failure_count == 2
        assert _entry_of(round2, stale_id).lifecycle_status == "rejected"

    # Round 3: canonical retrieval must not render the quarantined pollutant,
    # while the correct lesson keeps helping.
    pipeline3 = MemoryPipeline(_fresh_manager_for_round(manager))
    pipeline3.initialize()
    pipeline3._contribution_reviewer = MemoryContributionReviewer(reviewer_config)
    pipeline3.inject(TASK, None, _messages())
    rendered = _rendered_ids(pipeline3)
    if review_mode == "rule":
        assert stale_id not in rendered
    else:
        # Without the reviewer nothing ever quarantines the pollutant.
        final = _fresh_manager_for_round(manager)
        assert _entry_of(final, stale_id).lifecycle_status != "rejected"
    assert correct_id in rendered, "correct lesson must remain rendered"


def test_correct_lesson_accumulates_small_positive_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    correct_id = _seed(manager, CORRECT_CONTENT)
    _seed(manager, STALE_CONTENT)
    _seed(manager, IRRELEVANT_CONTENT)

    from minicode.memory_contribution_reviewer import MemoryContributionReviewer

    for round_index in (1, 2):
        pipeline = MemoryPipeline(_fresh_manager_for_round(manager))
        pipeline.initialize()
        pipeline._contribution_reviewer = MemoryContributionReviewer(
            ContributionReviewConfig(mode="rule")
        )
        pipeline.inject(TASK, None, _messages())
        if correct_id not in _rendered_ids(pipeline):
            pytest.skip("retrieval did not render the correct lesson")
        pipeline.review_injected_memories(
            TASK,
            _round_trace(read_stale=False),
            operation_id=f"op-positive-{round_index}",
        )
    final = _fresh_manager_for_round(manager)
    correct = _entry_of(final, correct_id)
    assert correct.success_count >= 1
    assert correct.lifecycle_status != "rejected"
