import os

import minicode.cli_commands as cli_commands_module
from minicode.cli_commands import (
    find_matching_slash_commands,
    format_slash_commands,
    try_handle_local_command,
)
from minicode.config import MINI_CODE_ENV_PATH, MINI_CODE_SETTINGS_PATH
from minicode.env_file import read_private_env_file, update_private_env_file
from minicode.local_tool_shortcuts import parse_local_tool_shortcut


def test_find_matching_slash_commands_returns_help_variants() -> None:
    matches = find_matching_slash_commands("/mo")
    assert "/model" in matches
    assert "/model <model-name>" in matches


def test_find_matching_slash_commands_returns_cybernetics() -> None:
    matches = find_matching_slash_commands("/cy")
    assert "/cybernetics" in matches


def test_parse_local_tool_shortcut_parses_cmd() -> None:
    shortcut = parse_local_tool_shortcut("/cmd src::git status")
    assert shortcut == {
        "toolName": "run_command",
        "input": {"command": "git status", "cwd": "src"},
    }


def test_parse_local_tool_shortcut_parses_patch_pairs() -> None:
    shortcut = parse_local_tool_shortcut("/patch demo.txt::hello::hi::world::earth")
    assert shortcut == {
        "toolName": "patch_file",
        "input": {
            "path": "demo.txt",
            "replacements": [
                {"search": "hello", "replace": "hi"},
                {"search": "world", "replace": "earth"},
            ],
        },
    }


def test_format_slash_commands_includes_permissions() -> None:
    assert "/permissions" in format_slash_commands()


def test_format_slash_commands_describes_patch_replacements() -> None:
    commands = format_slash_commands()
    # 检查格式化后的帮助信息包含关键命令
    assert "/patch" in commands
    assert "replacements" in commands or "multiple" in commands


def test_format_slash_commands_includes_history_and_retry() -> None:
    commands = format_slash_commands()
    assert "/history" in commands
    assert "/retry" in commands
    assert "/cybernetics" in commands


def test_config_paths_shows_global_model_env_and_legacy_fallback() -> None:
    result = try_handle_local_command("/config-paths")

    assert result is not None
    assert f"primary model env: {MINI_CODE_ENV_PATH}" in result
    assert f"mini-code settings fallback: {MINI_CODE_SETTINGS_PATH}" in result
    assert "workspace .env" not in result


def test_model_command_writes_private_global_env(tmp_path, monkeypatch) -> None:
    mini_dir = tmp_path / ".mini-code"
    monkeypatch.setattr(cli_commands_module, "MINI_CODE_ENV_PATH", mini_dir / ".env")

    result = try_handle_local_command("/model gpt-4o")

    assert result is not None
    assert f"to {mini_dir / '.env'}" in result
    assert "MINI_CODE_MODEL=\"gpt-4o\"" in (mini_dir / ".env").read_text(
        encoding="utf-8"
    )
    if os.name == "posix":
        assert (mini_dir.stat().st_mode & 0o777) == 0o700
        assert ((mini_dir / ".env").stat().st_mode & 0o777) == 0o600


def test_model_command_updates_provider_for_unambiguous_cross_provider_switch(
    tmp_path,
    monkeypatch,
) -> None:
    env_path = tmp_path / ".mini-code" / ".env"
    update_private_env_file(
        env_path,
        {
            "MINI_CODE_MODEL": "claude-sonnet-4-20250514",
            "MINI_CODE_PROVIDER": "anthropic",
        },
    )
    monkeypatch.setattr(cli_commands_module, "MINI_CODE_ENV_PATH", env_path)

    result = try_handle_local_command("/model gpt-4o")

    assert result is not None
    values = read_private_env_file(env_path)
    assert values["MINI_CODE_MODEL"] == "gpt-4o"
    assert values["MINI_CODE_PROVIDER"] == "openai"


def test_model_command_preserves_provider_for_unknown_custom_model(
    tmp_path,
    monkeypatch,
) -> None:
    env_path = tmp_path / ".mini-code" / ".env"
    update_private_env_file(
        env_path,
        {
            "MINI_CODE_MODEL": "old-private-model",
            "MINI_CODE_PROVIDER": "custom",
        },
    )
    monkeypatch.setattr(cli_commands_module, "MINI_CODE_ENV_PATH", env_path)

    result = try_handle_local_command("/model new-private-model")

    assert result is not None
    values = read_private_env_file(env_path)
    assert values["MINI_CODE_MODEL"] == "new-private-model"
    assert values["MINI_CODE_PROVIDER"] == "custom"


def test_model_command_returns_redacted_error_when_private_env_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    env_path = tmp_path / ".mini-code" / ".env"
    monkeypatch.setattr(cli_commands_module, "MINI_CODE_ENV_PATH", env_path)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("write-failure-canary-secret")

    monkeypatch.setattr(cli_commands_module, "update_private_env_file", fail_write)

    result = try_handle_local_command("/model gpt-4o")

    assert result is not None
    assert "model update failed" in result
    assert str(env_path) in result
    assert "write-failure-canary-secret" not in result


def test_status_shows_only_selected_provider_transport(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_commands_module,
        "load_runtime_config",
        lambda: {
            "model": "deepseek-chat",
            "provider": "custom",
            "baseUrl": "https://api.anthropic.com",
            "customBaseUrl": "https://api.deepseek.com",
            "customApiKey": "status-canary-secret",
            "agentTurnBudget": {},
            "mcpServers": {},
            "sourceSummary": "model config: user_env",
        },
    )

    result = try_handle_local_command("/status")

    assert result is not None
    assert "provider: custom" in result
    assert "baseUrl: https://api.deepseek.com" in result
    assert "api.anthropic.com" not in result
    assert "status-canary-secret" not in result


def test_memory_command_uses_current_workspace(tmp_path) -> None:
    result = try_handle_local_command("/memory", cwd=str(tmp_path))

    assert result is not None
    assert "Memory System Status" in result


def test_cybernetics_command_shows_controller_inventory() -> None:
    result = try_handle_local_command("/cybernetics")

    assert result is not None
    assert "Cybernetic Control System" in result
    assert "CyberneticSupervisor" in result
    assert "ProgressController" in result


def test_cybernetics_command_uses_persisted_report(tmp_path, monkeypatch) -> None:
    import minicode.cybernetic_supervisor as supervisor_module
    from minicode.cybernetic_supervisor import ControlSnapshot, CyberneticSupervisor, save_supervisor_report

    monkeypatch.setattr(
        supervisor_module,
        "SUPERVISOR_STATE_PATH",
        tmp_path / "cybernetic_supervisor.json",
    )
    report = CyberneticSupervisor().report([
        ControlSnapshot(name="context", health=0.2, risk=0.9, action="compact")
    ])
    save_supervisor_report(report)

    result = try_handle_local_command("/cybernetics")

    assert result is not None
    assert "source: latest agent-loop report" in result
    assert "context: compact" in result
