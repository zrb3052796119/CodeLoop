from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "minicode/web/static/index.html"
STYLES = ROOT / "minicode/web/static/assets/styles.css"
APP = ROOT / "minicode/web/static/assets/app.js"


class _ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _styles() -> str:
    return STYLES.read_text(encoding="utf-8")


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def _shell_tags() -> list[tuple[str, dict[str, str]]]:
    parser = _ShellParser()
    parser.feed(_html())
    return parser.tags


def _root_block(stylesheet: str) -> str:
    start = stylesheet.index(":root {")
    return stylesheet[start : stylesheet.index("\n}", start) + 2]


def test_visual_system_defines_semantic_surface_tokens() -> None:
    root = _root_block(_styles())
    for token in (
        "--surface-page",
        "--surface-nav",
        "--surface-main",
        "--surface-dock",
        "--surface-elevated",
        "--surface-overlay",
        "--surface-hover",
        "--surface-selected",
    ):
        assert token in root


def test_visual_system_defines_semantic_text_and_border_tokens() -> None:
    root = _root_block(_styles())
    for token in (
        "--text-primary",
        "--text-secondary",
        "--text-muted",
        "--text-disabled",
        "--text-inverse",
        "--border-subtle",
        "--border-standard",
        "--border-strong",
        "--border-focus",
    ):
        assert token in root


def test_visual_system_defines_complete_semantic_state_tokens() -> None:
    root = _root_block(_styles())
    for state in ("live", "warning", "unavailable", "danger", "info"):
        assert f"--state-{state}:" in root
        assert f"--state-{state}-surface:" in root


def test_visual_system_defines_typography_spacing_shape_motion_and_layout_scales() -> None:
    root = _root_block(_styles())
    for token in (
        "--font-display",
        "--font-body",
        "--font-data",
        "--text-xs",
        "--text-sm",
        "--text-md",
        "--space-1",
        "--space-2",
        "--space-3",
        "--space-4",
        "--radius-xs",
        "--radius-sm",
        "--radius-md",
        "--shadow-overlay",
        "--duration-fast",
        "--ease-standard",
        "--layout-nav-width",
        "--layout-dock-width",
        "--layout-header-height",
        "--layout-resizer-width",
    ):
        assert token in root


def test_single_dark_theme_declares_the_full_semantic_contract() -> None:
    """The dashboard ships one deliberate dark theme: the full semantic token
    contract lives in :root, `color-scheme: dark` is declared, and no
    prefers-color-scheme fork can drift out of sync."""
    stylesheet = _styles()
    root = _root_block(stylesheet)
    text_colors = {
        "--text-primary",
        "--text-secondary",
        "--text-muted",
        "--text-disabled",
        "--text-inverse",
    }
    semantic_color_tokens = {
        name
        for name in re.findall(r"(--(?:surface|text|border|state)-[\w-]+)\s*:", root)
        if not name.startswith("--text-") or name in text_colors
    }
    assert len(semantic_color_tokens) >= 24
    assert "color-scheme: dark" in root
    assert "@media (prefers-color-scheme:" not in stylesheet


def test_components_do_not_reintroduce_raw_status_colors_or_generic_live_layout() -> None:
    stylesheet = _styles()
    assert not re.search(r"#[0-9a-fA-F]{3,8}", stylesheet[stylesheet.index("* {") :])
    assert not re.search(r"(?m)^\.live(?:\s|,|\{)", stylesheet)


def test_formal_assets_do_not_depend_on_network_fonts_or_images() -> None:
    combined = _html() + _styles() + _app()
    assert "fonts.googleapis.com" not in combined
    assert "fonts.gstatic.com" not in combined
    assert not re.search(r"url\(\s*['\"]?https?://", combined)
    assert not re.search(r"<(?:img|source)\b[^>]+https?://", combined)


def test_navigation_keeps_all_public_routes_and_count_hooks() -> None:
    tags = _shell_tags()
    nav_links = [
        attrs
        for tag, attrs in tags
        if tag == "a" and "data-view" in attrs
    ]
    assert [attrs["data-view"] for attrs in nav_links] == [
        "overview",
        "runs",
        "sessions",
        "memory",
        "skills",
        "connections",
        "ops",
        "system",
    ]
    assert [attrs["href"] for attrs in nav_links] == [
        "#overview",
        "#runs",
        "#sessions",
        "#memory",
        "#skills",
        "#connections",
        "#ops",
        "#system",
    ]
    html = _html()
    for count in ("runs", "sessions", "memory", "skills", "connections", "usage"):
        assert f'data-count="{count}"' in html


def test_shell_retains_semantic_landmarks_and_primary_focus_target() -> None:
    tags = _shell_tags()
    assert any(tag == "nav" and attrs.get("id") == "nav" for tag, attrs in tags)
    assert any(tag == "main" and attrs.get("id") == "main-content" for tag, attrs in tags)
    assert any(tag == "aside" and attrs.get("id") == "dock" for tag, attrs in tags)
    assert any(
        tag == "a" and attrs.get("class") == "skip-link" and attrs.get("href") == "#view"
        for tag, attrs in tags
    )
    assert any(
        attrs.get("id") == "view" and attrs.get("tabindex") == "-1"
        for _, attrs in tags
    )


def test_chat_dock_retains_all_business_hooks() -> None:
    html = _html()
    for element_id in (
        "dock",
        "dock-status",
        "dock-close",
        "dock-new",
        "dock-refresh",
        "history-toggle",
        "session-menu",
        "permission-panel",
        "chat-log",
        "chat-form",
        "message",
        "chat-cancel",
        "chat-submit",
        "dock-reopen",
    ):
        assert f'id="{element_id}"' in html


def test_dock_authority_copy_is_progressively_disclosed() -> None:
    html = _html()
    assert 'class="dock-authority"' in html
    assert "<details" in html
    assert "SSE live refresh / invalidation" in html
    assert "final Session authority" in html
    assert "loopback permission approval" in html
    assert "<em>synchronous request" not in html
    assert "<p>synchronous request · SSE live refresh" not in html


def test_shell_panels_expose_accessible_toggle_state() -> None:
    html = _html()
    app = _app()
    for element_id in ("nav-toggle", "nav-reopen"):
        assert re.search(
            rf'id="{element_id}"[^>]+aria-controls="nav"',
            html,
        )
    for element_id in ("dock-close", "dock-reopen"):
        assert re.search(
            rf'id="{element_id}"[^>]+aria-controls="dock"',
            html,
        )
    assert "function setShellPanelState" in app
    assert "aria-expanded" in app


def test_shell_layout_changes_do_not_mutate_or_clear_the_chat_draft() -> None:
    javascript = _app()
    shell = javascript[
        javascript.index("function setShellPanelState") : javascript.index(
            "\nfunction wireResize"
        )
    ]
    assert "chatStore.draft" not in shell
    assert ".value" not in shell
    assert "submitChatTurn" not in shell
    assert "fetch(" not in shell


def test_resizers_have_mouse_and_keyboard_contracts() -> None:
    html = _html()
    app = _app()
    for element_id, controls in (
        ("nav-resizer", "nav"),
        ("dock-resizer", "dock"),
    ):
        assert re.search(
            rf'id="{element_id}"[^>]+role="separator"[^>]+tabindex="0"'
            rf'[^>]+aria-controls="{controls}"',
            html,
        )
    resize = app[app.index("function wireResize") : app.index("\nfunction wireShell")]
    assert "'mousedown'" in resize
    assert "'keydown'" in resize
    assert "ArrowLeft" in resize
    assert "ArrowRight" in resize


def test_shell_uses_explicit_medium_and_narrow_layout_states() -> None:
    app = _app()
    stylesheet = _styles()
    assert "const SHELL_BREAKPOINTS" in app
    assert "dockOverlay: 1100" in app
    assert "navOverlay: 640" in app
    assert "@media (max-width: 1100px)" in stylesheet
    assert "@media (max-width: 700px)" in stylesheet
    assert "#dock-resizer { display: none; }" in stylesheet
    assert "max-width: 100vw" in stylesheet


def test_dialog_toast_dock_and_skip_link_have_a_tokenized_z_order() -> None:
    root = _root_block(_styles())
    values = {}
    for name in ("dock", "reopen", "toast", "dialog", "skip"):
        match = re.search(rf"--z-{name}:\s*(\d+)", root)
        assert match
        values[name] = int(match.group(1))
    assert values["dock"] < values["reopen"] < values["toast"] < values["dialog"] < values["skip"]
    stylesheet = _styles()
    assert "z-index: var(--z-dock)" in stylesheet
    assert "z-index: var(--z-toast)" in stylesheet
    assert "z-index: var(--z-dialog)" in stylesheet


def test_focus_visible_is_shared_and_visibly_tokenized() -> None:
    stylesheet = _styles()
    assert ":focus-visible" in stylesheet
    assert "var(--border-focus)" in stylesheet
    assert "outline-offset:" in stylesheet
    assert ".resizer:focus-visible" in stylesheet


def test_statuses_include_text_and_a_non_color_marker() -> None:
    html = _html()
    stylesheet = _styles()
    assert 'class="status-marker"' in html
    assert 'aria-hidden="true"' in html
    assert ".source-state::before" in stylesheet
    assert ".live-refresh-status::before" in stylesheet
    assert "content:" in stylesheet


def test_reduced_motion_disables_all_shell_motion() -> None:
    stylesheet = _styles()
    reduced = stylesheet[stylesheet.index("@media (prefers-reduced-motion: reduce)") :]
    assert "animation: none !important" in reduced
    assert "transition: none !important" in reduced
    assert "scroll-behavior: auto !important" in reduced


def test_formal_frontend_keeps_exactly_one_event_source_and_no_new_pollers() -> None:
    javascript = _app()
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    intervals = re.findall(r"setInterval\(([^,\n]+)", javascript)
    assert intervals == ["tickMeta"]
    assert "new WebSocket" not in javascript


def test_permission_allow_and_deny_hooks_remain_explicit() -> None:
    javascript = _app()
    assert 'data-permission-decision="allow_once"' in javascript
    assert 'data-permission-decision="deny_once"' in javascript
    assert "decidePermission" in javascript
    assert "canAllowPermission" in javascript


def test_chat_cancel_send_and_authority_hooks_remain_explicit() -> None:
    html = _html()
    javascript = _app()
    assert 'id="chat-cancel"' in html
    assert 'id="chat-submit"' in html
    assert "cancelActiveTurn" in javascript
    assert "submitChatTurn" in javascript
    assert "loadSessionDetail" in javascript
    assert "reconcileActiveTurnOnce" in javascript


def test_session_and_project_memory_deletion_hooks_remain_explicit() -> None:
    javascript = _app()
    for hook in (
        "openConversationDeletion",
        "openProjectMemoryDeletion",
        "loadDeletionPreview",
        "submitDeletion",
        "data-deletion-submit",
    ):
        assert hook in javascript


def test_rest_and_action_requests_keep_no_store_authority() -> None:
    javascript = _app()
    assert javascript.count("cache: 'no-store'") >= 15
    snapshot_fetch = javascript[
        javascript.index("fetch('/api/v1/snapshot'") :
        javascript.index("fetch('/api/v1/snapshot'") + 180
    ]
    assert "cache: 'no-store'" in snapshot_fetch
    assert "wireResize('nav-resizer', '--nav-w', 'miniNavW'" in javascript
    assert "wireResize('dock-resizer', '--dock-w', 'miniDockW'" in javascript
    assert "localStorage.setItem(storageKey" in javascript
    assert "localStorage.setItem('message'" not in javascript


def test_disabled_destructive_and_primary_controls_are_unambiguous() -> None:
    stylesheet = _styles()
    assert "button:disabled" in stylesheet
    assert "cursor: not-allowed" in stylesheet
    assert ".permission-allow" in stylesheet
    assert ".permission-deny" in stylesheet
    assert ".deletion-destructive" in stylesheet
    assert "var(--state-danger)" in stylesheet


def test_global_primitives_use_semantic_surfaces_and_borders() -> None:
    stylesheet = _styles()
    shell = stylesheet[: stylesheet.index(".tiles {")]
    for selector in ("body", "#nav", "main", ".pagehead"):
        assert selector in shell
    assert "background: var(--surface-page)" in shell
    assert "background: var(--surface-nav)" in shell
    assert "background: var(--surface-main)" in shell
    assert "var(--border-standard)" in shell


def test_visual_helpers_include_visually_hidden_content() -> None:
    stylesheet = _styles()
    html = _html()
    assert ".visually-hidden" in stylesheet
    assert 'class="visually-hidden"' in html


def test_shell_theme_metadata_matches_the_dark_surface() -> None:
    html = _html()
    match = re.search(r'<meta name="theme-color" content="(#[0-9a-fA-F]{6})"', html)
    assert match
    assert match.group(1).lower() == "#171d2b"
    assert '<meta name="color-scheme" content="dark"' in html
