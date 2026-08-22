from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.permissions as permissions_module
from minicode.permission_approval import (
    MAX_COMMAND_PREVIEW_BYTES,
    PermissionApprovalBroker,
    _truncate_utf8,
)
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.write_file import write_file_tool
from minicode.tools.run_command import run_command_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


@pytest.fixture(autouse=True)
def isolated_permission_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_path = tmp_path / "home" / "permissions.json"
    monkeypatch.setattr(permissions_module, "MINI_CODE_PERMISSIONS_PATH", store_path)
    permissions_module._normalize_path_cached.cache_clear()
    yield store_path
    permissions_module._normalize_path_cached.cache_clear()


def _start_write(
    *,
    workspace: Path,
    session,
    manager: PermissionManager,
    content: str = "approved\n",
) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = write_file_tool.run(
                {"path": "demo.txt", "content": content},
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - assert control-flow result
            outcome["error"] = error
        finally:
            session.tool_finished("write_file")

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _pending_item(broker: PermissionApprovalBroker) -> dict[str, object]:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        items = broker.snapshot()["items"]
        if items:
            return items[0]
        time.sleep(0.005)
    raise AssertionError("permission request did not become pending")


def _start_command_check(
    *,
    workspace: Path,
    session,
    manager: PermissionManager,
    command: str,
    args: list[str],
    reason: str = "Command review requested.",
) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def check() -> None:
        session.tool_started("run_command")
        try:
            manager.ensure_command(
                command,
                args,
                str(workspace),
                force_prompt_reason=reason,
            )
            outcome["result"] = "allowed"
        except BaseException as error:  # noqa: BLE001 - assert control-flow result
            outcome["error"] = error
        finally:
            session.tool_finished("run_command")

    thread = threading.Thread(target=check)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize(
    ("command", "args", "sensitive_marker"),
    [
        ("tool", ["--password", "split-password-marker"], "split-password-marker"),
        ("tool", ["--TOKEN=equals-token-marker"], "equals-token-marker"),
        ("tool", ["--Api_Key", "mixed-api-marker"], "mixed-api-marker"),
        ("tool", ["--access-token", "access-token-marker"], "access-token-marker"),
        ("tool", ["--auth_token=auth-token-marker"], "auth-token-marker"),
        ("tool", ["--secret", "secret-marker"], "secret-marker"),
        ("tool", ["--credential", "credential-marker"], "credential-marker"),
        ("tool", ["--user", "alice:user-marker"], "user-marker"),
        ("tool", ["-pcompact-marker"], "compact-marker"),
        ("tool", ["-H", "Authorization: Bearer authorization-marker"], "authorization-marker"),
        ("tool", ["--header", "Cookie: session=cookie-marker"], "cookie-marker"),
        ("tool", ["--header=X-API-Key: header-api-marker"], "header-api-marker"),
        ("env", ["PASSWORD=environment-marker", "tool"], "environment-marker"),
        (
            "tool",
            ["https://alice:url-userinfo-marker@example.invalid/resource"],
            "url-userinfo-marker",
        ),
    ],
)
def test_command_review_redacts_structured_sensitive_argv_and_is_deny_only(
    tmp_path: Path,
    command: str,
    args: list[str],
    sensitive_marker: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "a" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command=command,
        args=args,
    )
    try:
        item = _pending_item(broker)
        serialized = json.dumps(broker.snapshot(), ensure_ascii=False)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"]["commandPreview"] == "[REDACTED SENSITIVE REVIEW]"
        assert item["review"]["redacted"] is True
        assert sensitive_marker not in serialized
        with pytest.raises(Exception) as blocked:
            broker.decide(
                permission_id=item["permissionId"],
                turn_id=turn_id,
                decision="allow_once",
            )
        assert getattr(blocked.value, "code", None) == "permission_not_reviewable"
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)
    assert isinstance(outcome.get("error"), RuntimeError)


def test_command_review_redacts_split_sensitive_reason_without_echo(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "1" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    marker = "reason-sensitive-marker"
    thread, _outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command="tool",
        args=["inspect", "relative.txt"],
        reason=f"Command includes password {marker}",
    )
    try:
        item = _pending_item(broker)
        serialized = json.dumps(broker.snapshot(), ensure_ascii=False)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"]["reason"] == "Command review is unavailable."
        assert marker not in serialized
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    "command_factory",
    [
        lambda workspace, outside, home: (str(outside / "tool"), ["inspect"]),
        lambda workspace, outside, home: ("tool", [str(outside / "input.txt")]),
        lambda workspace, outside, home: ("tool", [str(home / "profile.txt")]),
        lambda workspace, outside, home: (
            "tool",
            [f"--output={outside / 'output.txt'}"],
        ),
        lambda workspace, outside, home: (
            f"tool < {outside / 'input.txt'} | helper",
            [],
        ),
    ],
)
def test_command_review_never_serializes_local_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command_factory,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    home = tmp_path / "home"
    workspace.mkdir()
    outside.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    command, args = command_factory(workspace, outside, home)
    turn_id = "turn_" + "b" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, _outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command=command,
        args=args,
    )
    try:
        item = _pending_item(broker)
        serialized = json.dumps(broker.snapshot(), ensure_ascii=False)
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert item["review"]["commandPreview"] == "[REDACTED SENSITIVE REVIEW]"
        assert item["review"]["redacted"] is True
        for local_path in (workspace, outside, home):
            assert str(local_path) not in serialized
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)


def test_an_in_workspace_absolute_path_stays_reviewable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path under the workspace is rewritten, not hidden.

    Hiding it satisfied "no absolute path is serialized" by blanking the whole
    review, which left the reviewer a Reject button and nothing to read -- and
    an agent writes in-workspace absolute paths routinely, so ordinary
    commands became unapprovable. Rewriting to a workspace-relative form keeps
    the serialization guarantee below while restoring the decision.
    """
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    turn_id = "turn_" + "c" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, _outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command="tool",
        args=[str(workspace / "inside.txt")],
    )
    try:
        item = _pending_item(broker)
        serialized = json.dumps(broker.snapshot(), ensure_ascii=False)

        assert item["reviewable"] is True
        assert item["review"]["redacted"] is False
        assert item["review"]["commandPreview"] == "tool inside.txt"
        # The guarantee the blanket rule existed to provide, kept intact.
        for local_path in (workspace, home):
            assert str(local_path) not in serialized

        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    ("value", "max_bytes", "expected_truncated"),
    [
        ("exact", 5, False),
        ("abcdef", 5, True),
        ("中文测试", 7, True),
        ("😀😀", 5, True),
        ("a", 0, True),
        ("ab", 1, True),
        ("ab", 2, False),
        ("中", 2, True),
    ],
)
def test_truncate_utf8_final_value_never_exceeds_declared_byte_budget(
    value: str,
    max_bytes: int,
    expected_truncated: bool,
) -> None:
    result, truncated = _truncate_utf8(value, max_bytes)
    assert len(result.encode("utf-8")) <= max_bytes
    assert truncated is expected_truncated
    if not truncated:
        assert result == value


def test_command_preview_public_projection_respects_strict_utf8_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "c" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, _outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command="tool",
        args=["😀" * (MAX_COMMAND_PREVIEW_BYTES // 2)],
    )
    try:
        item = _pending_item(broker)
        preview = item["review"]["commandPreview"]
        assert len(preview.encode("utf-8")) <= MAX_COMMAND_PREVIEW_BYTES
        assert item["review"]["truncated"] is True
        assert item["reviewable"] is False
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("tool", ["inspect", "relative/data.json"]),
        ("tool", ["--endpoint", "https://example.invalid/api/v1/items"]),
        ("pytest", ["-q", "tests/test_example.py"]),
    ],
)
def test_safe_structured_command_review_remains_allowable(
    tmp_path: Path,
    command: str,
    args: list[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "d" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, outcome = _start_command_check(
        workspace=workspace,
        session=session,
        manager=manager,
        command=command,
        args=args,
        reason="Ordinary development command review.",
    )
    try:
        item = _pending_item(broker)
        assert item["reviewable"] is True
        assert item["choices"] == ["allow_once", "deny_once"]
        assert item["review"]["cwd"] == "."
        assert item["review"]["redacted"] is False
        assert item["review"]["truncated"] is False
        assert str(workspace) not in json.dumps(item)
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="allow_once",
        )
    finally:
        broker.close()
        thread.join(timeout=1)
    assert outcome == {"result": "allowed"}
    assert manager.session_allowed_commands == set()


@pytest.mark.parametrize("terminal", ["deny", "timeout", "cancel"])
def test_sensitive_real_command_never_starts_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + {"deny": "d", "timeout": "e", "cancel": "f"}[terminal] * 32
    token = TurnCancellationToken(turn_id)
    broker = PermissionApprovalBroker(
        workspace,
        timeout_seconds=0.05 if terminal == "timeout" else 2,
        poll_interval=0.005,
    )
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=token,
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    calls: list[object] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr("minicode.tools.run_command.sys.platform", "win32")
    monkeypatch.setattr("minicode.tools.run_command.subprocess.run", fake_run)
    marker = f"sensitive-{terminal}-marker"
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("run_command")
        try:
            outcome["result"] = run_command_tool.run(
                {
                    "command": "unknown-tool",
                    "args": ["--password", marker],
                    "timeout": 1,
                },
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - terminal state is asserted
            outcome["error"] = error
        finally:
            session.tool_finished("run_command")

    thread = threading.Thread(target=run)
    thread.start()
    item = _pending_item(broker)
    serialized = json.dumps(broker.snapshot(), ensure_ascii=False)
    assert marker not in serialized
    assert item["reviewable"] is False
    assert item["choices"] == ["deny_once"]
    assert calls == []
    if terminal == "deny":
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    elif terminal == "cancel":
        token.request()
    thread.join(timeout=1)
    broker.close()
    assert not thread.is_alive()
    assert calls == []
    assert "error" in outcome


@pytest.mark.parametrize(
    "review_request",
    [
        {
            "schemaVersion": True,
            "kind": "command",
            "review": {
                "command": "tool",
                "args": [],
                "cwd": ".",
                "reason": "review",
            },
        },
        {
            "schemaVersion": 1,
            "kind": "command",
            "review": {
                "command": "tool",
                "args": [],
                "cwd": ".",
                "reason": "review",
                "unexpected": "invalid-schema-marker",
            },
        },
        {
            "schemaVersion": 1,
            "kind": "command",
            "review": {
                "command": "tool",
                "args": "not-a-list",
                "cwd": ".",
                "reason": "review",
            },
        },
    ],
)
def test_unknown_or_invalid_command_review_fails_closed_without_echo(
    tmp_path: Path,
    review_request: dict[str, object],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "0" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("run_command")
        try:
            outcome["result"] = session.prompt(review_request)
        finally:
            session.tool_finished("run_command")

    thread = threading.Thread(target=prompt)
    thread.start()
    item = _pending_item(broker)
    serialized = json.dumps(broker.snapshot(), ensure_ascii=False)
    assert item["reviewable"] is False
    assert item["choices"] == ["deny_once"]
    assert item["review"] == {}
    assert "invalid-schema-marker" not in serialized
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    thread.join(timeout=1)
    broker.close()
    assert outcome == {"result": {"decision": "deny_operation"}}


def test_real_write_file_waits_for_allow_and_allow_is_operation_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    turn_id = "turn_" + "1" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id="run_" + "2" * 32,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)

    first, first_outcome = _start_write(
        workspace=workspace,
        session=session,
        manager=manager,
        content="first\n",
    )
    item = _pending_item(broker)
    assert item["kind"] == "edit"
    assert item["review"]["targetPath"] == "demo.txt"
    assert not (workspace / "demo.txt").exists()

    decision = broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    assert decision.decision_accepted is True
    first.join(timeout=2)
    assert not first.is_alive()
    assert "error" not in first_outcome
    assert (workspace / "demo.txt").read_text(encoding="utf-8") == "first\n"
    assert manager.session_allowed_edits == set()

    second, _ = _start_write(
        workspace=workspace,
        session=session,
        manager=manager,
        content="second\n",
    )
    second_item = _pending_item(broker)
    assert second_item["permissionId"] != item["permissionId"]
    broker.decide(
        permission_id=second_item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    second.join(timeout=2)
    assert (workspace / "demo.txt").read_text(encoding="utf-8") == "first\n"


def test_cancel_wakes_real_permission_prompt_and_prevents_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "3" * 32
    token = TurnCancellationToken(turn_id)
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2, poll_interval=0.01)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=token,
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, outcome = _start_write(
        workspace=workspace,
        session=session,
        manager=manager,
    )
    permission_id = _pending_item(broker)["permissionId"]

    token.request()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert not (workspace / "demo.txt").exists()
    with pytest.raises(Exception) as late:
        broker.decide(
            permission_id=permission_id,
            turn_id=turn_id,
            decision="allow_once",
        )
    assert getattr(late.value, "code", None) == "permission_cancelled"


def test_permission_manager_emits_structured_review_and_supports_internal_decisions(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def prompt(request: dict[str, object]) -> dict[str, str]:
        requests.append(request)
        return {"decision": "allow_operation"}

    manager = PermissionManager(str(tmp_path), prompt=prompt)
    target = tmp_path / "demo.py"
    manager.ensure_edit(str(target), "--- a/demo.py\n+++ b/demo.py")
    manager.ensure_edit(str(target), "--- a/demo.py\n+++ b/demo.py")

    assert len(requests) == 2
    assert requests[0]["schemaVersion"] == 1
    assert requests[0]["review"] == {
        "targetPath": str(target.resolve()),
        "diffPreview": "--- a/demo.py\n+++ b/demo.py",
    }
    assert manager.session_allowed_edits == set()


def test_decision_retry_is_idempotent_and_opposite_decision_conflicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "6" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, _ = _start_write(workspace=workspace, session=session, manager=manager)
    item = _pending_item(broker)

    first = broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    retry = broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    assert first.decision_accepted is True
    assert retry.decision_accepted is False
    assert retry.status == "allowed"
    with pytest.raises(Exception) as conflict:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
    assert getattr(conflict.value, "code", None) == "permission_already_decided"
    thread.join(timeout=2)
    tombstone = broker._records[item["permissionId"]]
    assert tombstone.review == {}
    assert tombstone.summary == ""
    assert tombstone._session is None


def test_unreviewable_secret_command_cannot_be_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "7" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def check() -> None:
        session.tool_started("run_command")
        try:
            manager.ensure_command(
                "python",
                ["-c", "password=do-not-leak"],
                str(workspace),
            )
        except BaseException as error:  # noqa: BLE001
            outcome["error"] = error
        finally:
            session.tool_finished("run_command")

    thread = threading.Thread(target=check)
    thread.start()
    item = _pending_item(broker)
    serialized = json.dumps(item)
    assert item["reviewable"] is False
    assert item["choices"] == ["deny_once"]
    assert "do-not-leak" not in serialized
    assert str(workspace) not in serialized
    with pytest.raises(Exception) as blocked:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="allow_once",
        )
    assert getattr(blocked.value, "code", None) == "permission_not_reviewable"
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    thread.join(timeout=2)
    assert isinstance(outcome.get("error"), RuntimeError)


@pytest.mark.parametrize("decision", ["allow_once", "deny_once"])
def test_real_dangerous_command_starts_only_after_allow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + ("8" if decision == "allow_once" else "9") * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    calls: list[object] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr("minicode.tools.run_command.sys.platform", "win32")
    monkeypatch.setattr("minicode.tools.run_command.subprocess.run", fake_run)
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("run_command")
        try:
            outcome["result"] = run_command_tool.run(
                {"command": "python -c pass", "timeout": 1},
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001
            outcome["error"] = error
        finally:
            session.tool_finished("run_command")

    thread = threading.Thread(target=run)
    thread.start()
    item = _pending_item(broker)
    assert item["reviewable"] is True
    assert item["choices"] == ["allow_once", "deny_once"]
    assert item["review"]["cwd"] == "."
    assert str(workspace) not in json.dumps(item)
    assert calls == []
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision=decision,
    )
    thread.join(timeout=2)
    assert len(calls) == (1 if decision == "allow_once" else 0)
    assert manager.session_allowed_commands == set()


def test_capacity_timeout_and_close_all_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broker = PermissionApprovalBroker(
        workspace,
        timeout_seconds=0.05,
        max_pending=1,
        poll_interval=0.005,
    )
    first_turn = "turn_" + "a" * 32
    second_turn = "turn_" + "b" * 32
    first_session = broker.begin_turn(
        turn_id=first_turn,
        run_id=None,
        cancellation_token=TurnCancellationToken(first_turn),
    )
    second_session = broker.begin_turn(
        turn_id=second_turn,
        run_id=None,
        cancellation_token=TurnCancellationToken(second_turn),
    )
    first_manager = PermissionManager(str(workspace), prompt=first_session.prompt)
    second_manager = PermissionManager(str(workspace), prompt=second_session.prompt)
    first, first_outcome = _start_write(
        workspace=workspace,
        session=first_session,
        manager=first_manager,
        content="first",
    )
    item = _pending_item(broker)
    second, second_outcome = _start_write(
        workspace=workspace,
        session=second_session,
        manager=second_manager,
        content="second",
    )
    second.join(timeout=1)
    assert not second.is_alive()
    assert isinstance(second_outcome.get("error"), RuntimeError)
    assert not (workspace / "demo.txt").exists()

    first.join(timeout=1)
    assert not first.is_alive()
    assert isinstance(first_outcome.get("error"), RuntimeError)
    with pytest.raises(Exception) as expired:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=first_turn,
            decision="allow_once",
        )
    assert getattr(expired.value, "code", None) == "permission_expired"

    close_turn = "turn_" + "c" * 32
    close_session = broker.begin_turn(
        turn_id=close_turn,
        run_id=None,
        cancellation_token=TurnCancellationToken(close_turn),
    )
    close_manager = PermissionManager(str(workspace), prompt=close_session.prompt)
    closing, closing_outcome = _start_write(
        workspace=workspace,
        session=close_session,
        manager=close_manager,
    )
    _pending_item(broker)
    broker.close()
    closing.join(timeout=1)
    assert not closing.is_alive()
    assert isinstance(closing_outcome.get("error"), RuntimeError)
    assert not (workspace / "demo.txt").exists()


def test_external_path_projection_never_exposes_or_allows_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "outside.txt"
    turn_id = "turn_" + "d" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def check() -> None:
        session.tool_started("read_file")
        try:
            manager.ensure_path_access(str(external), "read")
        except BaseException as error:  # noqa: BLE001
            outcome["error"] = error
        finally:
            session.tool_finished("read_file")

    thread = threading.Thread(target=check)
    thread.start()
    item = _pending_item(broker)
    assert item["review"] == {"intent": "read", "outsideWorkspace": True}
    assert str(external) not in json.dumps(item)
    with pytest.raises(Exception) as blocked:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="allow_once",
        )
    assert getattr(blocked.value, "code", None) == "permission_not_reviewable"
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    thread.join(timeout=1)
    assert isinstance(outcome.get("error"), RuntimeError)


def test_allow_then_cancel_at_final_checkpoint_prevents_real_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "e" * 32
    token = TurnCancellationToken(turn_id)
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=token,
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    at_final_checkpoint = threading.Event()
    continue_checkpoint = threading.Event()
    checks = 0

    def checkpoint() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            at_final_checkpoint.set()
            assert continue_checkpoint.wait(1)
        session.check_operation()

    manager.operation_checkpoint = checkpoint
    thread, outcome = _start_write(
        workspace=workspace,
        session=session,
        manager=manager,
    )
    item = _pending_item(broker)
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    assert at_final_checkpoint.wait(1)
    token.request()
    continue_checkpoint.set()
    thread.join(timeout=1)

    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert not (workspace / "demo.txt").exists()


def test_tool_side_effect_after_final_checkpoint_is_not_rolled_back(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "f" * 32
    token = TurnCancellationToken(turn_id)
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=token,
    )
    manager = PermissionManager(
        str(workspace),
        prompt=session.prompt,
        operation_checkpoint=session.check_operation,
    )
    thread, outcome = _start_write(
        workspace=workspace,
        session=session,
        manager=manager,
    )
    item = _pending_item(broker)
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    thread.join(timeout=1)
    token.request()

    assert "error" not in outcome
    assert (workspace / "demo.txt").read_text() == "approved\n"


def test_close_is_process_local_and_new_broker_restores_no_pending(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    turn_id = "turn_" + "0" * 32
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    thread, _outcome = _start_write(
        workspace=workspace, session=session, manager=manager
    )
    item = _pending_item(broker)
    broker.close()
    thread.join(timeout=1)
    with pytest.raises(Exception) as late:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="allow_once",
        )
    assert getattr(late.value, "code", None) == "permission_unavailable"

    restarted = PermissionApprovalBroker(workspace)
    assert restarted.snapshot()["items"] == []
    assert restarted.revision() != broker.revision()


def test_tombstones_are_bounded_and_expire_without_review_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    now = [10.0]
    broker = PermissionApprovalBroker(
        workspace,
        timeout_seconds=2,
        tombstone_limit=2,
        tombstone_ttl_seconds=5,
        monotonic=lambda: now[0],
    )
    for index, digit in enumerate(("1", "2", "3")):
        turn_id = "turn_" + digit * 32
        session = broker.begin_turn(
            turn_id=turn_id,
            run_id=None,
            cancellation_token=TurnCancellationToken(turn_id),
        )
        manager = PermissionManager(str(workspace), prompt=session.prompt)
        thread, _outcome = _start_write(
            workspace=workspace,
            session=session,
            manager=manager,
            content=str(index),
        )
        item = _pending_item(broker)
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=turn_id,
            decision="deny_once",
        )
        thread.join(timeout=1)
        now[0] += 0.1

    assert len(broker._records) == 2
    assert all(record.review == {} for record in broker._records.values())
    now[0] += 6
    broker.snapshot()
    assert broker._records == {}


def test_same_name_concurrent_tool_contexts_do_not_cross_turns(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    running: list[tuple[threading.Thread, dict[str, object]]] = []
    for digit, content in (("5", "five"), ("6", "six")):
        turn_id = "turn_" + digit * 32
        session = broker.begin_turn(
            turn_id=turn_id,
            run_id=None,
            cancellation_token=TurnCancellationToken(turn_id),
        )
        manager = PermissionManager(str(workspace), prompt=session.prompt)
        outcome: dict[str, object] = {}

        def run(
            current_session=session,
            current_manager=manager,
            current_content=content,
            current_outcome=outcome,
        ) -> None:
            current_session.tool_started("write_file")
            try:
                current_outcome["result"] = write_file_tool.run(
                    {
                        "path": f"{current_content}.txt",
                        "content": current_content,
                    },
                    ToolContext(cwd=str(workspace), permissions=current_manager),
                )
            except BaseException as error:  # noqa: BLE001 - expected deny result
                current_outcome["error"] = error
            finally:
                current_session.tool_finished("write_file")

        thread = threading.Thread(target=run)
        thread.start()
        running.append((thread, outcome))

    deadline = time.monotonic() + 1
    items: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        items = broker.snapshot()["items"]
        if len(items) == 2:
            break
        time.sleep(0.005)
    assert len(items) == 2
    assert len({item["turnId"] for item in items}) == 2
    assert len({item["toolOperationId"] for item in items}) == 2
    for item in items:
        broker.decide(
            permission_id=item["permissionId"],
            turn_id=item["turnId"],
            decision="deny_once",
        )
    for thread, _outcome in running:
        thread.join(timeout=1)
