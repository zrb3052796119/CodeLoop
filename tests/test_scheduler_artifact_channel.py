"""Per-Assignment mailbox views cannot become a free collaboration bus."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

import minicode.scheduler_artifacts as scheduler_artifacts_module
from minicode.central_scheduler import DispatchStage, StageAssignment
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_BYTES,
    MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW,
    AssignmentArtifactChannel,
    SchedulerArtifactError,
    SchedulerArtifactStore,
)
from minicode.subagent_mailbox import SubagentMailbox, SubagentMailboxError
from minicode.tooling import ToolContext
from minicode.tools.subagent_notes import subagent_note_write_tool
from minicode.workflow_runner import _assignment_result_key


@pytest.mark.parametrize("forgeable", [None, "scheduler", 1, True, ()])
def test_store_requires_an_identity_capability(forgeable: object) -> None:
    with pytest.raises(ValueError, match="capability"):
        SchedulerArtifactStore(scheduler_authority=forgeable)


def test_scheduler_store_publishes_canonical_immutable_artifacts() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)
    source = {"z": [3, {"状态": True}], "a": 1}

    artifact = store.publish(
        "artifact_plan",
        source,
        authority=scheduler_authority,
    )
    source["a"] = 99

    assert artifact.artifact_id == "artifact_plan"
    assert artifact.content == '{"a":1,"z":[3,{"状态":true}]}'
    assert store.read("artifact_plan") is artifact
    assert store.list_artifact_ids() == ("artifact_plan",)
    with pytest.raises(FrozenInstanceError):
        artifact.content = "{}"


def test_scheduler_publish_is_capability_guarded_and_write_once() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)

    with pytest.raises(SchedulerArtifactError, match="authority"):
        store.publish(
            "artifact_result",
            {"status": "forged"},
            authority=object(),
        )

    original = store.publish(
        "artifact_result",
        {"status": "accepted"},
        authority=scheduler_authority,
    )
    with pytest.raises(SchedulerArtifactError, match="write-once"):
        store.publish(
            "artifact_result",
            {"status": "overwritten"},
            authority=scheduler_authority,
        )

    assert store.read("artifact_result") is original
    assert original.content == '{"status":"accepted"}'


def test_scheduler_artifacts_are_strict_json_bounded_to_20k_utf8_bytes() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)

    exact = store.publish(
        "artifact_exact_limit",
        {"data": "x" * 19_989},
        authority=scheduler_authority,
    )

    assert MAX_SCHEDULER_ARTIFACT_BYTES == 20_000
    assert len(exact.content.encode("utf-8")) == MAX_SCHEDULER_ARTIFACT_BYTES

    with pytest.raises(SchedulerArtifactError, match="20,000"):
        store.publish(
            "artifact_over_limit",
            {"data": "x" * 19_990},
            authority=scheduler_authority,
        )
    with pytest.raises(SchedulerArtifactError, match="20,000"):
        store.publish(
            "artifact_unicode_over_limit",
            {"data": "界" * 6_664},
            authority=scheduler_authority,
        )
    with pytest.raises(SchedulerArtifactError, match="JSON-safe"):
        store.publish(
            "artifact_non_finite",
            {"score": float("nan")},
            authority=scheduler_authority,
        )
    with pytest.raises(SchedulerArtifactError, match="string keys"):
        store.publish(
            "artifact_numeric_key",
            {1: "not canonical"},
            authority=scheduler_authority,
        )


def test_store_creates_an_exact_assignment_read_and_write_channel() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)
    store.publish(
        "artifact_plan",
        {"plan": ["inspect", "change"]},
        authority=scheduler_authority,
    )
    store.publish(
        "verification_baseline",
        {"status": "passed"},
        authority=scheduler_authority,
    )
    store.publish(
        "artifact_private_ballot",
        {"choice": "sealed"},
        authority=scheduler_authority,
    )

    channel = store.create_assignment_channel(
        readable_keys=("artifact_plan", "verification_baseline"),
        write_key="artifact_execution_result",
    )

    assert channel.readable_keys == (
        "artifact_plan",
        "verification_baseline",
    )
    assert channel.write_key == "artifact_execution_result"
    assert channel.list_keys() == channel.readable_keys
    assert channel.read("artifact_plan").content == (
        '{"plan":["inspect","change"]}'
    )
    with pytest.raises(SchedulerArtifactError, match="not readable"):
        channel.read("artifact_private_ballot")
    with pytest.raises(SchedulerArtifactError, match="not readable"):
        channel.read("artifact_execution_result")

    written = channel.write(
        "artifact_execution_result",
        '{ "z": 2, "a": 1 }',
        author="spoofed-worker",
    )

    assert written.content == '{"a":1,"z":2}'
    assert store.read("artifact_execution_result") is written
    assert channel.list_keys() == channel.readable_keys
    assert store.list_artifact_ids() == (
        "artifact_plan",
        "verification_baseline",
        "artifact_private_ballot",
        "artifact_execution_result",
    )
    with pytest.raises(SchedulerArtifactError, match="write-once"):
        channel.write(
            "artifact_execution_result",
            "{}",
            author="spoofed-worker",
        )
    with pytest.raises(SchedulerArtifactError, match="write key"):
        channel.write("artifact_escape", "{}", author="spoofed-worker")


def test_assignment_write_key_is_reserved_and_malformed_json_does_not_spend_it() -> (
    None
):
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)
    store.publish(
        "artifact_input",
        {"ready": True},
        authority=scheduler_authority,
    )
    channel = store.create_assignment_channel(
        readable_keys=("artifact_input",),
        write_key="artifact_reserved_result",
    )

    with pytest.raises(SchedulerArtifactError, match="reserved"):
        store.publish(
            "artifact_reserved_result",
            {"forged": True},
            authority=scheduler_authority,
        )
    with pytest.raises(SchedulerArtifactError, match="duplicate keys"):
        channel.write(
            "artifact_reserved_result",
            '{"status":"first","status":"second"}',
            author="worker",
        )

    written = channel.write(
        "artifact_reserved_result",
        '{"status":"completed"}',
        author="worker",
    )
    assert written.content == '{"status":"completed"}'


def test_scheduler_failure_receipt_seals_an_unwritten_assignment_key() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)
    channel = store.create_assignment_channel(
        readable_keys=(),
        write_key="artifact_failed_result",
    )

    receipt = store.seal_stage_result(
        "artifact_failed_result",
        {
            "resultType": "SchedulerStageReceipt.v1",
            "outcome": "failed",
        },
        authority=scheduler_authority,
    )

    assert receipt.content == (
        '{"outcome":"failed","resultType":"SchedulerStageReceipt.v1"}'
    )
    with pytest.raises(SchedulerArtifactError, match="invalid"):
        channel.write(
            "artifact_failed_result",
            '{"status":"late"}',
            author="worker",
        )


def test_assignment_close_revokes_a_write_that_has_not_linearized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    channel = store.create_assignment_channel(
        readable_keys=(),
        write_key="artifact_parsing_result",
    )
    parsing = threading.Event()
    release = threading.Event()
    close_done = threading.Event()
    errors: list[BaseException] = []
    original_parse = scheduler_artifacts_module._parse_json_document

    def blocked_parse(content):
        parsing.set()
        assert release.wait(5)
        return original_parse(content)

    monkeypatch.setattr(
        scheduler_artifacts_module,
        "_parse_json_document",
        blocked_parse,
    )

    def late_write() -> None:
        try:
            channel.write(
                "artifact_parsing_result",
                '{"status":"late"}',
                author="worker",
            )
        except BaseException as exc:  # test thread reports exact failure
            errors.append(exc)

    writer = threading.Thread(target=late_write)
    writer.start()
    assert parsing.wait(5)
    closer = threading.Thread(
        target=lambda: (channel.close(), close_done.set())
    )
    closer.start()
    try:
        assert close_done.wait(1), "close waited for the Worker channel lock"
    finally:
        release.set()
    closer.join(5)
    writer.join(5)

    assert not closer.is_alive()
    assert not writer.is_alive()
    assert store.read("artifact_parsing_result") is None
    assert len(errors) == 1
    assert isinstance(errors[0], SchedulerArtifactError)
    assert "spent" in str(errors[0])


def test_artifact_capacity_reserves_and_atomically_consumes_result_slots() -> None:
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    for index in range(MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW - 1):
        store.publish(
            f"artifact_capacity_{index}",
            {"index": index},
            authority=authority,
        )
    worker_channel = store.create_assignment_channel(
        readable_keys=(),
        write_key="artifact_capacity_worker_result",
    )

    with pytest.raises(SchedulerArtifactError, match="128-artifact"):
        store.publish(
            "artifact_capacity_stolen_slot",
            {"forbidden": True},
            authority=authority,
        )

    written = worker_channel.write(
        "artifact_capacity_worker_result",
        '{"status":"completed"}',
        author="worker",
    )
    assert written.artifact_id == "artifact_capacity_worker_result"
    assert len(store.list_artifact_ids()) == MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW

    receipt_store = SchedulerArtifactStore(scheduler_authority=authority)
    for index in range(MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW - 1):
        receipt_store.publish(
            f"artifact_receipt_capacity_{index}",
            {"index": index},
            authority=authority,
        )
    late_channel = receipt_store.create_assignment_channel(
        readable_keys=(),
        write_key="artifact_capacity_receipt",
    )
    receipt_store.publish_stage_receipt(
        "artifact_capacity_receipt",
        {"resultType": "SchedulerStageReceipt.v1"},
        authority=authority,
    )
    with pytest.raises(SchedulerArtifactError, match="invalid"):
        late_channel.write(
            "artifact_capacity_receipt",
            '{"status":"late"}',
            author="worker",
        )


def test_assignment_channel_configuration_fails_closed() -> None:
    scheduler_authority = object()
    store = SchedulerArtifactStore(scheduler_authority=scheduler_authority)
    store.publish(
        "artifact_input",
        {"ready": True},
        authority=scheduler_authority,
    )
    store.publish(
        "artifact_occupied",
        {"status": "sealed"},
        authority=scheduler_authority,
    )

    with pytest.raises(SchedulerArtifactError, match="unique tuple"):
        store.create_assignment_channel(
            readable_keys=(["artifact_input"],),
            write_key="artifact_result",
        )
    with pytest.raises(SchedulerArtifactError, match="not published"):
        store.create_assignment_channel(
            readable_keys=("artifact_missing",),
            write_key="artifact_result",
        )
    with pytest.raises(SchedulerArtifactError, match="also be readable"):
        store.create_assignment_channel(
            readable_keys=("artifact_input",),
            write_key="artifact_input",
        )
    with pytest.raises(SchedulerArtifactError, match="reserved"):
        store.create_assignment_channel(
            readable_keys=("artifact_input",),
            write_key="artifact_occupied",
        )


def test_assignment_channel_reads_only_inputs_and_writes_one_result_once() -> None:
    mailbox = SubagentMailbox()
    mailbox.write("artifact_input", "accepted input", author="scheduler")
    mailbox.write("private_ballot", "secret", author="other voter")
    channel = AssignmentArtifactChannel(
        mailbox,
        readable_keys=("artifact_input",),
        result_key="artifact_result",
        author="asg_12345678",
    )

    assert channel.read("artifact_input").content == "accepted input"
    assert channel.list_keys() == ("artifact_input",)
    with pytest.raises(SubagentMailboxError, match="not readable"):
        channel.read("private_ballot")

    written = channel.write("artifact_result", "sealed", author="spoofed")

    assert written.author == "asg_12345678"
    assert mailbox.read("artifact_result").content == "sealed"
    with pytest.raises(SubagentMailboxError, match="immutable"):
        channel.write("artifact_result", "overwrite", author="spoofed")
    with pytest.raises(SubagentMailboxError, match="result key"):
        channel.write("another_key", "escape", author="spoofed")


def test_real_executor_result_key_passes_the_note_tool_boundary() -> None:
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    assignment = StageAssignment(
        workflow_id="wf_" + "f" * 120,
        task_id="task_executor_result",
        assignment_id="asg_" + "a" * 120,
        attempt_id="attempt_" + "b" * 120,
        attempt=1,
        agent_run_id="sub_" + "c" * 32,
        stage=DispatchStage.EXECUTOR,
        rule_id="route.reviewed_change",
    )
    result_key = _assignment_result_key(assignment, "exec")
    channel = store.create_assignment_channel(
        readable_keys=(),
        write_key=result_key,
    )
    validated = subagent_note_write_tool.validator(
        {"key": result_key, "content": '{"status":"completed"}'}
    )

    result = subagent_note_write_tool.run(
        validated,
        ToolContext(_subagent_mailbox=channel),
    )

    assert len(result_key) <= 80
    assert result.ok is True
    assert store.read(result_key).content == '{"status":"completed"}'
