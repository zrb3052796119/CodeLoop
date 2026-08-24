import minicode.tui.chrome as chrome_module
import minicode.tui.navigation as navigation_module
import minicode.logging_config as logging_config_module
import minicode.main as main_module

from minicode.cli_commands import SLASH_COMMANDS, format_slash_commands
from minicode.main import _render_startup_prelude
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tui import (
    render_banner,
    render_input_prompt,
    render_panel,
    render_permission_prompt,
    render_slash_menu,
    render_transcript,
)
from minicode.tui.chrome import string_display_width, strip_ansi
from minicode.tui.navigation import _get_transcript_body_lines
from minicode.tui.state import ScreenState, TtyAppArgs
from minicode.tui.types import TranscriptEntry


def test_render_panel_contains_title() -> None:
    rendered = render_panel("Demo", "body")
    assert "Demo" in rendered
    assert "body" in rendered


def test_render_banner_includes_model() -> None:
    rendered = render_banner(
        {"model": "claude-test", "baseUrl": "https://api.anthropic.com"},
        "/tmp/demo",
        ["cwd: /tmp/demo"],
        {"transcriptCount": 1, "messageCount": 2, "skillCount": 3, "mcpCount": 4},
    )
    assert "claude-test" in rendered
    assert "api.anthropic.com" in rendered


def test_render_banner_uses_selected_custom_provider_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(chrome_module, "_cached_terminal_size", lambda: (100, 24))

    rendered = render_banner(
        {
            "model": "deepseek-chat",
            "provider": "custom",
            "baseUrl": "https://api.anthropic.com",
            "customBaseUrl": "https://api.deepseek.com",
            "customApiKey": "test-only-key",
        },
        "/tmp/demo",
        [],
        {"messageCount": 1, "skillCount": 2, "mcpCount": 3},
    )

    plain = strip_ansi(rendered)
    assert "api.deepseek.com" in plain
    assert "api.anthropic.com" not in plain


def test_interactive_startup_has_no_legacy_prelude() -> None:
    rendered = _render_startup_prelude(
        {"model": "claude-test"},
        "/tmp/demo",
        ["cwd: /tmp/demo"],
        {"transcriptCount": 0, "messageCount": 1, "skillCount": 3, "mcpCount": 1},
        interactive=True,
    )

    assert rendered == ""


def test_interactive_tui_logging_stays_out_of_alternate_screen(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        logging_config_module,
        "setup_logging",
        lambda **kwargs: calls.append(kwargs),
    )

    main_module._setup_cli_logging("WARNING", interactive=True)

    assert calls == [{"level": "WARNING", "log_to_console": False}]


def test_narrow_header_is_bounded_and_uses_project_name(monkeypatch) -> None:
    monkeypatch.setattr(chrome_module, "_cached_terminal_size", lambda: (60, 24))
    cwd = "/Users/demo/code/coding agent/MiniCode-Python-main"

    rendered = render_banner(
        {"model": "deepseek-chat", "baseUrl": "https://server.max-tabs.com"},
        cwd,
        [f"cwd: {cwd}"],
        {"transcriptCount": 0, "messageCount": 1, "skillCount": 17, "mcpCount": 1},
    )

    plain = strip_ansi(rendered)
    assert "MiniCode-Python-main" in plain
    assert cwd not in plain
    assert "╭" not in plain
    assert all(string_display_width(line) <= 60 for line in rendered.splitlines())


def test_prompt_is_product_neutral_and_concise() -> None:
    rendered = strip_ansi(render_input_prompt("", 0))

    assert "codeloop>" not in rendered
    assert "Ask about this project" in rendered
    assert "Enter send" in rendered
    assert "Ctrl+J newline" in rendered
    assert "Esc" not in rendered


def test_slash_menu_is_bounded_to_a_small_viewport(monkeypatch) -> None:
    monkeypatch.setattr(chrome_module, "_cached_terminal_size", lambda: (80, 24))

    rendered = render_slash_menu(SLASH_COMMANDS, selected_index=0)

    assert len(rendered.splitlines()) <= 6
    assert "more" in strip_ansi(rendered)
    assert all(string_display_width(line) <= 80 for line in rendered.splitlines())


def test_slash_menu_height_is_reserved_from_transcript(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chrome_module, "_cached_terminal_size", lambda: (80, 24))
    monkeypatch.setattr(navigation_module, "_cached_terminal_size", lambda: (80, 24))
    state = ScreenState(input="/", cursor_offset=1)
    args = TtyAppArgs(
        runtime=None,
        tools=ToolRegistry([]),
        model=None,
        messages=[],
        cwd=str(tmp_path),
        permissions=PermissionManager(str(tmp_path)),
    )

    assert _get_transcript_body_lines(args, state) == 6


def test_help_output_uses_the_compact_terminal_design() -> None:
    rendered = format_slash_commands()

    assert "CodeLoop commands" in rendered
    assert "╔" not in rendered
    assert "📚" not in rendered
    assert len(rendered.splitlines()) <= 12
    assert all(string_display_width(line) <= 60 for line in rendered.splitlines())


def test_render_transcript_shows_tool_entry() -> None:
    transcript = [
        TranscriptEntry(id=1, kind="user", body="hi"),
        TranscriptEntry(id=2, kind="tool", body="done", toolName="read_file", status="success"),
    ]
    rendered = render_transcript(transcript, scroll_offset=0)
    assert "read_file" in rendered
    assert "ok" in rendered


def test_render_transcript_shows_intermediate_collapse_phase() -> None:
    transcript = [
        TranscriptEntry(
            id=1,
            kind="tool",
            body="full output here",
            toolName="run_command",
            status="success",
            collapsePhase=1,
        ),
    ]

    rendered = render_transcript(transcript, scroll_offset=0)

    assert "run_command" in rendered
    assert "collapsing" in rendered


def test_render_transcript_shows_collapsed_summary_when_fully_collapsed() -> None:
    transcript = [
        TranscriptEntry(
            id=1,
            kind="tool",
            body="full output here",
            toolName="run_command",
            status="success",
            collapsed=True,
            collapsedSummary="short summary",
            collapsePhase=3,
        ),
    ]

    rendered = render_transcript(transcript, scroll_offset=0)

    assert "run_command" in rendered
    assert "short summary" in rendered
    assert "full output here" not in rendered


def test_render_permission_prompt_lists_choices() -> None:
    rendered = render_permission_prompt(
        {
            "summary": "Need approval",
            "details": ["target: demo.txt"],
            "choices": [{"key": "1", "label": "allow once"}],
        }
    )
    assert "Need approval" in rendered
    assert "allow once" in rendered


def test_render_network_permission_prompt_contains_only_safe_summary() -> None:
    rendered = render_permission_prompt(
        {
            "summary": "mini-code wants to send a network request",
            "details": [
                "method: POST",
                "destination: https://api.public.example:443",
                "path: /v1/items",
            ],
            "choices": [
                {"key": "y", "label": "allow once"},
                {"key": "n", "label": "deny once"},
            ],
        },
        expanded=True,
    )

    assert "POST" in rendered
    assert "api.public.example" in rendered
    assert "/v1/items" in rendered
    assert "allow once" in rendered
    assert "deny once" in rendered
    assert "?" not in rendered
    assert "Authorization" not in rendered
