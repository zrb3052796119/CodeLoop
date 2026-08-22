"""Restrained slate-indigo color theme for the MiniCode TUI.

A low-saturation palette inspired by the Rust version's ColorTheme.
All colors are expressed as ANSI 256-color or 24-bit (RGB) escape codes.
"""

from __future__ import annotations

from dataclasses import dataclass


def _rgb(r: int, g: int, b: int) -> str:
    """24-bit foreground color escape code."""
    return f"\x1b[38;2;{r};{g};{b}m"


def _rgb_bg(r: int, g: int, b: int) -> str:
    """24-bit background color escape code."""
    return f"\x1b[48;2;{r};{g};{b}m"


@dataclass(frozen=True)
class ColorTheme:
    """High-contrast terminal palette with one primary accent family."""

    # Section borders / frames
    header: str        # Workspace header border
    session: str       # Session feed border
    input: str         # Input box border
    approval: str      # Approval dialog border

    # Message kinds
    user: str          # User messages
    assistant: str     # Assistant messages
    progress: str      # Progress messages
    tool: str          # Tool messages
    tool_error: str    # Tool error messages

    # UI chrome
    command_highlight_bg: str   # Slash command highlight background
    expandable: str             # [展开]/[收起] toggle text

    # Header label colors
    header_label_info: str       # project / provider / model / auth labels
    header_label_session: str    # session label
    header_label_permissions: str  # permissions / cwd labels
    header_label_recent: str     # recent tools label

    # Text utilities
    reset: str = "\x1b[0m"
    bold: str = "\x1b[1m"
    dim: str = "\x1b[2m"
    italic: str = "\x1b[3m"
    underline: str = "\x1b[4m"
    reverse: str = "\x1b[7m"

    # Semantic aliases
    subtle: str = "\x1b[38;5;243m"    # gray for subtle/secondary text
    border: str = "\x1b[38;5;39m"     # bright blue (legacy panel borders)
    border_dim: str = "\x1b[38;5;24m" # secondary border
    accent: str = "\x1b[38;5;214m"    # warm orange accent
    accent2: str = "\x1b[38;5;141m"   # soft purple accent
    highlight_bg: str = "\x1b[48;5;236m"  # dark selection background


def _default_theme() -> ColorTheme:
    """Build the default slate-indigo terminal theme."""
    return ColorTheme(
        # Structural chrome uses one quiet accent family.
        header=_rgb(95, 115, 165),
        session=_rgb(95, 115, 165),
        input=_rgb(125, 145, 210),
        approval=_rgb(205, 110, 110),

        # Color remains semantic inside the conversation.
        user=_rgb(130, 170, 230),
        assistant=_rgb(165, 190, 215),
        progress=_rgb(210, 170, 90),
        tool=_rgb(145, 165, 210),
        tool_error=_rgb(220, 100, 100),

        # UI chrome
        command_highlight_bg=_rgb_bg(75, 90, 130),
        expandable=_rgb(145, 165, 210),

        # Header labels
        header_label_info=_rgb(125, 145, 210),
        header_label_session=_rgb(145, 165, 210),
        header_label_permissions=_rgb(205, 110, 110),
        header_label_recent=_rgb(145, 165, 210),
    )


# Module-level singleton
_THEME: ColorTheme | None = None


def theme() -> ColorTheme:
    """Return the global ColorTheme instance (created once)."""
    global _THEME
    if _THEME is None:
        _THEME = _default_theme()
    return _THEME
