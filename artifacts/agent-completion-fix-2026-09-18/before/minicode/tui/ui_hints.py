from __future__ import annotations

from minicode.tui.state import ScreenState, TtyAppArgs


def _get_contextual_help(state: ScreenState, args: TtyAppArgs) -> str | None:
    """Return a small context-sensitive hint for the footer area."""
    if not state.is_busy and not state.pending_approval:
        return None

    if state.is_busy and state.active_tool:
        return f"Running {state.active_tool} · Ctrl+C cancel"

    if state.pending_approval:
        return "Permission required · ↑↓ choose · Enter confirm · Esc deny"

    return None
