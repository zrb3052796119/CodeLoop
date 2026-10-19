"""Progress events must survive the production child sink and disk reload."""

from pathlib import Path

import pytest

from minicode.run_journal import RunJournal
from minicode.subagent_journal import SubagentRunJournal, new_subagent_id
from minicode.tools.task import _SubagentJournalSink


def _start_child(tmp_path: Path) -> tuple[Path, Path, str, str, SubagentRunJournal]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "journal"
    journal = RunJournal(workspace, data_dir=data_dir)
    parent = journal.create_run(title="Child progress fixture", source="headless")
    journal.transition(parent.id, "running")
    subjournal = journal.open_subagent_journal(parent.id)
    child_id = new_subagent_id()
    subjournal.start(
        subagent_id=child_id, agent_type="general", max_turns=37, budget=None,
    )
    return workspace, data_dir, parent.id, child_id, subjournal


@pytest.mark.parametrize("event_type,payload", [
    ("model.retry_scheduled", {
        "operationId": "modelop_transport_retry", "failureKind": "network",
        "attempt": 1, "delaySeconds": 0.5, "usage": "unknown",
    }),
    ("execution.progress_checkpoint", {
        "reasonCode": "implementation_deadline_pressure",
        "inspectionBatches": 8,
        "remainingTokens": 32000,
        "estimatedRequestsLeft": 3,
        "remainingSeconds": 89.5,
    }),
    ("execution.progress_checkpoint", {
        "reasonCode": "inspection_without_delivery",
        "inspectionBatches": 4,
        "remainingTokens": None,
        "estimatedRequestsLeft": None,
        "remainingSeconds": None,
    }),
    ("context.read_history_bounded", {
        "charactersSaved": 24680, "protectedResultCount": 2,
    }),
    ("execution.request_admission_rejected", {
        "reason": "total_tokens",
        "estimatedInputTokens": 27663,
        "preserveTotalTokens": 73984,
        "outputLimit": 4096,
        "preserveModelCalls": 1,
        "usedTotalTokens": 415616,
        "limitTotalTokens": 426138,
        "reservedTotalTokens": 0,
        "usedModelCalls": 21,
        "limitModelCalls": 29,
    }),
])
def test_child_progress_event_round_trips_through_real_sink(
    tmp_path: Path, event_type: str, payload: dict[str, object],
) -> None:
    workspace, data_dir, parent_id, child_id, subjournal = _start_child(tmp_path)
    sink = _SubagentJournalSink(subjournal, child_id)

    sink.emit(event_type, step=12, payload=payload)

    reloaded = RunJournal(workspace, data_dir=data_dir)
    events = reloaded.list_subagent_events(parent_id, child_id)
    assert len(events) == 1
    assert events[0].type == event_type
    assert events[0].sequence == 1
    assert events[0].step == 12
    assert events[0].payload == payload
    # Observation must not inflate the model or tool execution counters.
    assert sink.model_turns == 0
    assert sink.tool_calls == 0
    assert not any(event.type == event_type for event in reloaded.list_events(parent_id).items)


@pytest.mark.parametrize("event_type", [
    "execution.progress_checkpoint", "context.read_history_bounded",
    "execution.request_admission_rejected",
    "model.retry_scheduled",
])
def test_child_progress_retains_existing_content_redaction(
    tmp_path: Path, event_type: str,
) -> None:
    workspace, data_dir, parent_id, child_id, subjournal = _start_child(tmp_path)
    sink = _SubagentJournalSink(subjournal, child_id)
    marker = "PRIVATE_TASK_BODY_MUST_NOT_PERSIST"
    sink.emit(event_type, step=1, payload={
        "charactersSaved": 100,
        "content": marker,
        "prompt": marker,
        "path": marker,
        "input": {"arbitrary": marker},
        "details": {"summary": marker, "command": marker, "count": 2},
    })

    reloaded = RunJournal(workspace, data_dir=data_dir)
    events = reloaded.list_subagent_events(parent_id, child_id)
    assert len(events) == 1
    assert events[0].payload == {"charactersSaved": 100, "details": {"count": 2}}
    sidecars = list(data_dir.rglob(f"{child_id}.jsonl"))
    assert len(sidecars) == 1
    assert marker not in sidecars[0].read_text(encoding="utf-8")


def test_parent_retry_observation_survives_disk_reload(tmp_path: Path) -> None:
    workspace, data_dir = tmp_path / "workspace", tmp_path / "journal"
    workspace.mkdir()
    payload = {"operationId": "modelop_transport_retry", "failureKind": "network",
               "attempt": 1, "delaySeconds": 0.5, "usage": "unknown"}
    journal = RunJournal(workspace, data_dir=data_dir)
    parent_id = journal.create_run(title="Network retry", source="headless").id
    journal.transition(parent_id, "running")
    journal.append_event(parent_id, "model.retry_scheduled", payload=payload)
    reloaded = RunJournal(workspace, data_dir=data_dir)
    assert reloaded.list_events(parent_id).items[-1].payload == payload


@pytest.mark.parametrize("event_type", ["assistant.completed", "execution.unregistered"])
def test_unregistered_child_events_remain_rejected_without_leaking(
    tmp_path: Path, event_type: str,
) -> None:
    workspace, data_dir, parent_id, child_id, subjournal = _start_child(tmp_path)
    sink = _SubagentJournalSink(subjournal, child_id)
    marker = "UNREGISTERED_EVENT_SECRET_MUST_NOT_PERSIST"
    sidecar = next(data_dir.rglob(f"{child_id}.jsonl"))
    before = sidecar.read_bytes()

    # The optional sink swallows storage rejection so telemetry cannot stop work.
    sink.emit(event_type, step=1, payload={"value": marker})
    assert sidecar.read_bytes() == before
    with pytest.raises(ValueError, match="event type is not allowed"):
        subjournal.append_event(
            child_id, sequence=1, event_type=event_type,
            step=1, payload={"value": marker},
        )
    assert sidecar.read_bytes() == before

    sink.emit("model.started", step=2, payload={"operationId": "modelop_fixture"})
    events = RunJournal(workspace, data_dir=data_dir).list_subagent_events(parent_id, child_id)
    assert [(event.sequence, event.type) for event in events] == [(1, "model.started")]
    assert sink.model_turns == 1
    assert marker not in sidecar.read_text(encoding="utf-8")
