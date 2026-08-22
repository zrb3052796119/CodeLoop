from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from minicode.cost_tracker import CostTracker
from minicode.history import load_history_entries
from minicode.permissions import PermissionManager
from minicode.session import (
    AutosaveManager,
    SessionData,
    create_new_session,
    format_session_list,
    get_latest_session,
    list_sessions,
    load_session,
    save_session,
)
from minicode.state import create_app_store
from minicode.tui.state import PendingApproval, ScreenState, TtyAppArgs
from minicode.tui.tool_lifecycle import _bump_transcript_revision
from minicode.tui.types import TranscriptEntry


def handle_session_listing(cwd: str, list_sessions_only: bool) -> bool:
    if not list_sessions_only:
        return False
    sessions = list_sessions()
    print(format_session_list(sessions))
    return True


def load_or_create_session(cwd: str, resume_session: str | None) -> SessionData:
    workspace = str(Path(cwd).resolve())
    if resume_session:
        if resume_session == "latest":
            session = get_latest_session(workspace=workspace)
            if session:
                return session
            return create_new_session(workspace=workspace)

        session = load_session(resume_session)
        if not session:
            raise FileNotFoundError(f"Session '{resume_session}' not found.")
        return session

    session = get_latest_session(workspace=workspace)
    if session:
        return create_new_session(workspace=workspace)

    return create_new_session(workspace=workspace)


def build_tty_runtime_state(
    runtime: dict | None,
    tools: Any,
    model: Any,
    messages: list[Any],
    cwd: str,
    permissions: PermissionManager,
    session: SessionData,
    memory_manager: Any | None = None,
    context_manager: Any | None = None,
) -> tuple[TtyAppArgs, ScreenState]:
    args = TtyAppArgs(
        runtime=runtime,
        tools=tools,
        model=model,
        messages=messages,
        cwd=cwd,
        permissions=permissions,
        memory_manager=memory_manager,
        context_manager=context_manager,
    )

    state = ScreenState(
        history=load_history_entries(),
        session=session,
        autosave=AutosaveManager(session),
        app_state=create_app_store({
            "session_id": session.session_id,
            "workspace": cwd,
            "model": runtime.get("model", "unknown") if runtime else "unknown",
        }),
        cost_tracker=CostTracker(),
    )
    state.history_index = len(state.history)

    if session.messages:
        args.messages.clear()
        args.messages.extend(session.messages)
        for entry_data in session.transcript_entries:
            state.transcript.append(TranscriptEntry(**entry_data))
        _bump_transcript_revision(state)
        state.status = f"Resumed {len(session.messages)} messages"

    return args, state


def install_permission_prompt(
    args: TtyAppArgs,
    state: ScreenState,
    rerender: Any,
) -> tuple[threading.Event, dict[str, Any], Any]:
    approval_event = threading.Event()
    approval_result: dict[str, Any] = {}

    def _permission_prompt_handler(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal approval_result
        # Reset the reusable channel before making the request observable.
        # A renderer or input driver may resolve immediately after pending is
        # published; clearing afterwards would erase that decision and wait
        # forever.
        approval_result.clear()
        approval_event.clear()
        state.pending_approval = PendingApproval(
            request=request,
            resolve=lambda r: None,
        )
        rerender()
        approval_event.wait()
        result = approval_result.copy()
        state.pending_approval = None
        return result

    args.permissions.prompt = _permission_prompt_handler
    return approval_event, approval_result, _permission_prompt_handler


def _sync_tty_session(args: TtyAppArgs, state: ScreenState) -> None:
    """Copy one coherent completed-turn view into the owned SessionData."""
    if state.session is None:
        return
    state.session.messages = list(args.messages)
    state.session.transcript_entries = [
        {
            "id": entry.id,
            "kind": entry.kind,
            "toolName": entry.toolName,
            "status": entry.status,
            "body": entry.body,
            "collapsed": entry.collapsed,
            "collapsedSummary": entry.collapsedSummary,
            "collapsePhase": entry.collapsePhase,
        }
        for entry in state.transcript
    ]
    state.session.history = list(state.history)
    state.session.permissions_summary = args.permissions.get_summary()
    state.session.skills = args.tools.get_skills()
    state.session.mcp_servers = args.tools.get_mcp_servers()


def commit_finished_tty_turn(args: TtyAppArgs, state: ScreenState) -> bool:
    """Synchronize and immediately persist one consumed finished turn."""
    if state.session is None:
        return True
    _sync_tty_session(args, state)
    if state.autosave is not None:
        state.autosave.mark_dirty()
        saved = state.autosave.save_now(force_full=False)
    else:
        try:
            save_session(state.session)
        except Exception:  # keep the TUI result/control flow unchanged
            saved = False
        else:
            saved = True
    if not saved:
        state.status = "Session save deferred; will retry."
    return saved


def consume_finished_tty_turn(args: TtyAppArgs, state: ScreenState) -> bool:
    """Consume one background ``done`` result and commit it at most once."""
    result = state.agent_result
    lock = state.agent_lock
    if result is None or lock is None or not result.get("done"):
        return False
    with lock:
        if not result.get("done"):
            return False
        final_messages = result.get("messages")
        if isinstance(final_messages, list):
            args.messages = list(final_messages)
        result["done"] = False
    commit_finished_tty_turn(args, state)
    return True


def finalize_tty_session(args: TtyAppArgs, state: ScreenState) -> None:
    if not state.session:
        return

    _sync_tty_session(args, state)

    if state.autosave:
        saved = state.autosave.force_save()
    else:
        try:
            save_session(state.session, force_full=True)
        except Exception:
            saved = False
        else:
            saved = True

    if saved:
        print(f"\nSession saved: {state.session.session_id[:8]}")
    else:
        state.status = "Session save deferred; will retry."
        print("\nSession save deferred; will retry.")
