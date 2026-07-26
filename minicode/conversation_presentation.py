"""Optional no-throw presentation seam for one synchronous conversation turn."""

from __future__ import annotations

from typing import Protocol


class ConversationPresentation(Protocol):
    """Observe temporary connection-local UI facts without becoming authority."""

    def assistant_delta(self, text: str) -> None: ...

    def tool_started(self, tool_name: str) -> None: ...

    def tool_finished(self, tool_name: str, *, is_error: bool) -> None: ...


def emit_assistant_delta_safely(
    presentation: ConversationPresentation | None,
    text: str,
) -> None:
    if presentation is None:
        return
    try:
        presentation.assistant_delta(text)
    except BaseException:  # noqa: BLE001 - UI presentation never alters execution
        pass


def emit_tool_started_safely(
    presentation: ConversationPresentation | None,
    tool_name: str,
) -> None:
    if presentation is None:
        return
    try:
        presentation.tool_started(tool_name)
    except BaseException:  # noqa: BLE001 - UI presentation never alters execution
        pass


def emit_tool_finished_safely(
    presentation: ConversationPresentation | None,
    tool_name: str,
    *,
    is_error: bool,
) -> None:
    if presentation is None:
        return
    try:
        presentation.tool_finished(tool_name, is_error=is_error)
    except BaseException:  # noqa: BLE001 - UI presentation never alters execution
        pass


__all__ = [
    "ConversationPresentation",
    "emit_assistant_delta_safely",
    "emit_tool_finished_safely",
    "emit_tool_started_safely",
]
