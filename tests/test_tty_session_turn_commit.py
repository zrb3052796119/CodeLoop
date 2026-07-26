from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

from minicode.permissions import PermissionManager
from minicode.session import AutosaveManager, create_new_session, load_session
from minicode.tooling import ToolRegistry
from minicode.tui.session_flow import consume_finished_tty_turn, finalize_tty_session
from minicode.tui.state import ScreenState, TtyAppArgs
from minicode.tui.types import TranscriptEntry
from minicode.web.read_model import DashboardReadModel


def test_successful_finished_turn_is_reloadable_without_exit_finalizer(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch("minicode.session.SESSIONS_DIR", sessions_dir),
        patch("minicode.session.MINI_CODE_DIR", tmp_path),
    ):
        session = create_new_session(str(workspace))
        final_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first real turn"},
            {"role": "assistant", "content": "first durable reply"},
        ]
        args = TtyAppArgs(
            runtime=None,
            tools=ToolRegistry([]),
            model=object(),
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "first real turn"},
            ],
            cwd=str(workspace),
            permissions=PermissionManager(str(workspace)),
        )
        state = ScreenState(
            session=session,
            autosave=AutosaveManager(session),
            transcript=[
                TranscriptEntry(id=1, kind="user", body="first real turn"),
                TranscriptEntry(
                    id=2,
                    kind="assistant",
                    body="first durable reply",
                ),
            ],
            history=["first real turn"],
            agent_result={"messages": final_messages, "done": True},
            agent_lock=threading.Lock(),
        )

        assert consume_finished_tty_turn(args, state) is True

        reloaded = load_session(session.session_id)
        assert reloaded is not None
        assert reloaded.messages == final_messages
        assert reloaded.history == ["first real turn"]
        assert state.agent_result["done"] is False


def test_two_finished_turns_are_idempotent_and_reload_latest_session_state(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch("minicode.session.SESSIONS_DIR", sessions_dir),
        patch("minicode.session.MINI_CODE_DIR", tmp_path),
    ):
        session = create_new_session(str(workspace))
        args = TtyAppArgs(
            runtime=None,
            tools=ToolRegistry([]),
            model=object(),
            messages=[{"role": "system", "content": "system"}],
            cwd=str(workspace),
            permissions=PermissionManager(str(workspace)),
        )
        state = ScreenState(
            session=session,
            autosave=AutosaveManager(session),
            agent_lock=threading.Lock(),
        )

        first_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply one"},
        ]
        state.history = ["first"]
        state.transcript = [
            TranscriptEntry(id=1, kind="user", body="first"),
            TranscriptEntry(id=2, kind="assistant", body="reply one"),
        ]
        state.agent_result = {"messages": first_messages, "done": True}
        assert consume_finished_tty_turn(args, state) is True
        assert consume_finished_tty_turn(args, state) is False

        second_messages = [
            *first_messages,
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply two"},
        ]
        state.history.append("second")
        state.transcript.extend(
            [
                TranscriptEntry(id=3, kind="user", body="second"),
                TranscriptEntry(id=4, kind="assistant", body="reply two"),
            ]
        )
        state.agent_result = {"messages": second_messages, "done": True}
        assert consume_finished_tty_turn(args, state) is True

        reloaded = load_session(session.session_id)
        assert reloaded is not None
        assert reloaded.messages == second_messages
        assert reloaded.history == ["first", "second"]
        assert reloaded.metadata.message_count == len(second_messages)
        assert reloaded.metadata.first_message == "first"
        assert reloaded.metadata.last_message == "reply two"
        assert len(reloaded.transcript_entries) == 4

        reloaded_again = load_session(session.session_id)
        assert reloaded_again is not None
        assert reloaded_again.messages == second_messages
        detail = DashboardReadModel(
            workspace=workspace,
            data_dir=tmp_path,
        ).session_detail(session.session_id, limit=50)
        assert [(item["role"], item["content"]) for item in detail["messages"]] == [
            ("user", "first"),
            ("assistant", "reply one"),
            ("user", "second"),
            ("assistant", "reply two"),
        ]


def test_failed_finished_turn_persists_real_user_message_without_assistant(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch("minicode.session.SESSIONS_DIR", sessions_dir),
        patch("minicode.session.MINI_CODE_DIR", tmp_path),
    ):
        session = create_new_session(str(workspace))
        user_only_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "real request before failure"},
        ]
        args = TtyAppArgs(
            runtime=None,
            tools=ToolRegistry([]),
            model=object(),
            messages=user_only_messages,
            cwd=str(workspace),
            permissions=PermissionManager(str(workspace)),
        )
        state = ScreenState(
            session=session,
            autosave=AutosaveManager(session),
            transcript=[
                TranscriptEntry(
                    id=1,
                    kind="user",
                    body="real request before failure",
                )
            ],
            history=["real request before failure"],
            agent_result={"messages": None, "done": True},
            agent_lock=threading.Lock(),
        )

        assert consume_finished_tty_turn(args, state) is True
        reloaded = load_session(session.session_id)
        assert reloaded is not None
        assert reloaded.messages == user_only_messages
        assert not any(message["role"] == "assistant" for message in reloaded.messages)
        assert state.agent_result["messages"] is None


def test_finished_turn_save_failure_preserves_result_and_dirty_retry(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch("minicode.session.SESSIONS_DIR", sessions_dir),
        patch("minicode.session.MINI_CODE_DIR", tmp_path),
    ):
        session = create_new_session(str(workspace))
        final_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "save this"},
            {"role": "assistant", "content": "real result"},
        ]
        args = TtyAppArgs(
            runtime=None,
            tools=ToolRegistry([]),
            model=object(),
            messages=final_messages[:-1],
            cwd=str(workspace),
            permissions=PermissionManager(str(workspace)),
        )
        state = ScreenState(
            session=session,
            autosave=AutosaveManager(session),
            transcript=[
                TranscriptEntry(id=1, kind="user", body="save this"),
                TranscriptEntry(id=2, kind="assistant", body="real result"),
            ],
            history=["save this"],
            agent_result={"messages": final_messages, "done": True},
            agent_lock=threading.Lock(),
        )

        with patch(
            "minicode.session.os.replace",
            side_effect=OSError("Bearer persistence-secret /private/path"),
        ):
            assert consume_finished_tty_turn(args, state) is True

        assert state.agent_result["messages"] == final_messages
        assert state.status == "Session save deferred; will retry."
        assert "secret" not in state.status.lower()
        assert load_session(session.session_id) is None
        assert state.autosave.save_now(force_full=False) is True
        assert state.autosave.save_now(force_full=False) is False
        reloaded = load_session(session.session_id)
        assert reloaded is not None
        assert reloaded.messages == final_messages


def test_exit_full_save_failure_keeps_exit_flow_and_can_retry(
    tmp_path: Path,
    capsys,
) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch("minicode.session.SESSIONS_DIR", sessions_dir),
        patch("minicode.session.MINI_CODE_DIR", tmp_path),
    ):
        session = create_new_session(str(workspace))
        args = TtyAppArgs(
            runtime=None,
            tools=ToolRegistry([]),
            model=object(),
            messages=[{"role": "user", "content": "exit-safe"}],
            cwd=str(workspace),
            permissions=PermissionManager(str(workspace)),
        )
        state = ScreenState(
            session=session,
            autosave=AutosaveManager(session),
        )

        with patch(
            "minicode.session.os.replace",
            side_effect=OSError("Authorization=exit-secret /private/path"),
        ):
            finalize_tty_session(args, state)

        output = capsys.readouterr().out
        assert "Session save deferred; will retry." in output
        assert "exit-secret" not in output
        assert state.autosave.save_now(force_full=True) is True
