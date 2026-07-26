from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from minicode.permission_event_contract import normalize_permission_event_payload
from minicode.permission_approval import PermissionApprovalBroker
from minicode.run_journal import RunJournal, RunJournalValidationError
from minicode.turn_cancellation import TurnCancellationToken
from minicode.web.read_model import DashboardReadModel


REQUESTED = {
    "permissionVersion": 1,
    "permissionId": "permission_" + "1" * 32,
    "kind": "edit",
    "toolName": "write_file",
    "toolOperationId": "permissiontool_" + "2" * 32,
    "reviewable": True,
}
DECIDED = {
    "permissionVersion": 1,
    "permissionId": "permission_" + "1" * 32,
    "decisionKind": "allowed",
}
NETWORK_REQUESTED = {
    "permissionVersion": 1,
    "permissionId": "permission_" + "3" * 32,
    "kind": "network",
    "toolName": "http_request",
    "toolOperationId": "permissiontool_" + "4" * 32,
    "reviewable": True,
}


def test_permission_event_contract_is_exact_and_content_free() -> None:
    assert normalize_permission_event_payload("permission.requested", REQUESTED) == REQUESTED
    assert normalize_permission_event_payload("permission.decided", DECIDED) == DECIDED
    assert normalize_permission_event_payload(
        "permission.requested", {**REQUESTED, "path": "/private/secret"}
    ) is None
    assert normalize_permission_event_payload(
        "permission.requested", {**REQUESTED, "permissionVersion": True}
    ) is None
    assert normalize_permission_event_payload(
        "permission.decided", {**DECIDED, "decisionKind": "allow_once"}
    ) is None
    assert (
        normalize_permission_event_payload(
            "permission.requested", NETWORK_REQUESTED
        )
        == NETWORK_REQUESTED
    )
    assert (
        normalize_permission_event_payload(
            "permission.requested",
            {**NETWORK_REQUESTED, "url": "https://fixture.invalid/?secret=hidden"},
        )
        is None
    )


def test_real_network_permission_events_and_journal_are_content_free(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "state"
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="network permission trace", source="gateway")
    journal.transition(run.id, "running")
    observed: list[tuple[str, dict[str, object]]] = []

    def emit(event_type: str, payload: dict[str, object]) -> None:
        observed.append((event_type, payload))
        journal.append_event(run.id, event_type, payload=payload)

    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    turn_id = "turn_" + "5" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=run.id,
        cancellation_token=TurnCancellationToken(turn_id),
        event_sink=emit,
    )
    outcome: dict[str, object] = {}
    sensitive_marker = "event-fixture-secret"

    def prompt() -> None:
        session.tool_started("http_request")
        try:
            outcome["result"] = session.prompt(
                {
                    "schemaVersion": 1,
                    "kind": "network",
                    "summary": "Review a network request.",
                    "details": [f"query: {sensitive_marker}"],
                    "review": {
                        "reviewVersion": 1,
                        "method": "POST",
                        "scheme": "https",
                        "hostname": "api.public.example",
                        "port": 443,
                        "pathSummary": "/v1/items",
                        "hasBody": True,
                        "hasSensitiveHeaders": True,
                        "requestFingerprint": "networkreq_" + "6" * 64,
                    },
                }
            )
        finally:
            session.tool_finished("http_request")

    worker = threading.Thread(target=prompt)
    worker.start()
    deadline = time.monotonic() + 1
    items: list[dict[str, object]] = []
    while time.monotonic() < deadline and not items:
        items = broker.snapshot()["items"]
        if not items:
            time.sleep(0.002)
    item = items[0]
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    worker.join(timeout=1)
    journal.transition(run.id, "completed")

    assert not worker.is_alive()
    assert outcome["result"]["decision"] == "deny_operation"
    assert [event_type for event_type, _payload in observed] == [
        "permission.requested",
        "permission.decided",
    ]
    assert observed[0][1] == {
        "permissionVersion": 1,
        "permissionId": item["permissionId"],
        "kind": "network",
        "toolName": "http_request",
        "toolOperationId": item["toolOperationId"],
        "reviewable": True,
    }
    assert observed[1][1] == {
        "permissionVersion": 1,
        "permissionId": item["permissionId"],
        "decisionKind": "denied",
    }
    serialized = json.dumps(
        [event.to_dict() for event in journal.list_events(run.id).items]
    )
    assert sensitive_marker not in serialized
    assert str(workspace) not in serialized
    session.close()
    broker.close()


def test_run_journal_and_read_model_project_only_safe_permission_fields(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "state"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="permission trace", source="gateway")
    journal.transition(record.id, "running")
    journal.append_event(record.id, "permission.requested", payload=REQUESTED)
    journal.append_event(record.id, "permission.decided", payload=DECIDED)
    sensitive_marker = "journal-sensitive-marker"
    with pytest.raises(RunJournalValidationError):
        journal.append_event(
            record.id,
            "permission.requested",
            payload={
                **REQUESTED,
                "commandPreview": f"tool --password {sensitive_marker}",
            },
        )
    journal.transition(record.id, "completed")

    detail = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=RunJournal(workspace, data_dir=data_dir),
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    ).run_detail(record.id)
    permission_items = [
        item for item in detail["events"] if item["type"].startswith("permission.")
    ]
    assert [item["details"] for item in permission_items] == [REQUESTED, DECIDED]
    serialized = json.dumps(detail)
    assert sensitive_marker not in serialized
    assert str(workspace) not in serialized
