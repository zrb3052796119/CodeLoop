from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import minicode.config as config_module
import minicode.config_migration as migration_module
from minicode.config_migration import migrate_model_config_to_user_env
from minicode.env_file import read_private_env_file


def _write_settings(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(path, 0o600)


def test_migration_moves_all_model_domains_and_scrubs_legacy_sources(
    tmp_path,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    workspace_env = tmp_path / "workspace.env"
    _write_settings(
        settings_path,
        {
            "model": "deepseek-chat",
            "env": {
                "CUSTOM_API_BASE_URL": "https://api.deepseek.com",
                "CUSTOM_API_KEY": "primary-secret",
                "UNRELATED_SETTING": "preserved",
            },
            "reflectionModel": "deepseek-chat",
            "allowRemoteReflectionModel": True,
            "memoryHybrid": {
                "enabled": True,
                "embeddingProvider": "qwen",
                "allowRemoteEmbedding": True,
                "verifierModel": "deepseek-chat",
            },
            "mcpServers": {
                "demo": {"command": "demo", "env": {"MCP_SECRET": "keep-me"}}
            },
        },
    )
    workspace_env.write_text(
        "MINI_CODE_SUBAGENT_API_KEY=child-secret\n"
        "MINI_CODE_SUBAGENT_MODEL=qwen3.6-flash\n"
        "MINICODE_EMBEDDING_API_KEY=embedding-secret\n"
        "MINICODE_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
        "PROJECT_ONLY=value\n",
        encoding="utf-8",
    )
    os.chmod(workspace_env, 0o600)

    report = migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
        workspace_env_path=workspace_env,
        scrub_workspace=True,
    )

    values = read_private_env_file(env_path)
    assert values["MINI_CODE_MODEL"] == "deepseek-chat"
    assert values["MINI_CODE_PROVIDER"] == "custom"
    assert values["CUSTOM_API_KEY"] == "primary-secret"
    assert values["MINI_CODE_SUBAGENT_API_KEY"] == "child-secret"
    assert values["MINICODE_EMBEDDING_API_KEY"] == "embedding-secret"
    assert values["MINI_CODE_MEMORY_HYBRID_ENABLED"] == "true"
    assert values["MINI_CODE_REFLECTION_MODEL"] == "deepseek-chat"

    remaining = json.loads(settings_path.read_text(encoding="utf-8"))
    assert remaining["modelEnvMigrationVersion"] == 1
    assert remaining["env"] == {"UNRELATED_SETTING": "preserved"}
    assert remaining["mcpServers"]["demo"]["env"]["MCP_SECRET"] == "keep-me"
    assert "model" not in remaining
    assert "memoryHybrid" not in remaining
    assert "reflectionModel" not in remaining

    workspace_text = workspace_env.read_text(encoding="utf-8")
    assert "PROJECT_ONLY=value" in workspace_text
    assert "child-secret" not in workspace_text
    assert "embedding-secret" not in workspace_text
    assert "primary-secret" not in repr(report)
    if os.name == "posix":
        assert (mini_dir.stat().st_mode & 0o777) == 0o700
        assert (env_path.stat().st_mode & 0o777) == 0o600
        assert (settings_path.stat().st_mode & 0o777) == 0o600


def test_migration_conflict_reports_only_key_names_and_performs_no_config_write(
    tmp_path,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "deepseek-chat",
            "env": {"CUSTOM_API_KEY": "legacy-secret"},
        },
    )
    from minicode.env_file import update_private_env_file

    update_private_env_file(env_path, {"CUSTOM_API_KEY": "existing-secret"})
    settings_before = settings_path.read_bytes()
    env_before = env_path.read_bytes()

    with pytest.raises(RuntimeError) as captured:
        migrate_model_config_to_user_env(
            settings_path=settings_path,
            env_path=env_path,
        )

    message = str(captured.value)
    assert "CUSTOM_API_KEY" in message
    assert "legacy-secret" not in message
    assert "existing-secret" not in message
    assert settings_path.read_bytes() == settings_before
    assert env_path.read_bytes() == env_before


def test_failure_after_env_commit_keeps_legacy_source_and_retry_succeeds(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "gpt-4o",
            "provider": "openai",
            "env": {"OPENAI_API_KEY": "retry-secret"},
        },
    )
    original_write = migration_module._atomic_write_json
    monkeypatch.setattr(
        migration_module,
        "_atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(RuntimeError, match="injected"):
        migrate_model_config_to_user_env(
            settings_path=settings_path,
            env_path=env_path,
        )

    assert read_private_env_file(env_path)["OPENAI_API_KEY"] == "retry-secret"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["env"][
        "OPENAI_API_KEY"
    ] == "retry-secret"

    monkeypatch.setattr(migration_module, "_atomic_write_json", original_write)
    migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )
    assert "env" not in json.loads(settings_path.read_text(encoding="utf-8"))


def test_repeated_migration_is_content_idempotent(tmp_path) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "gpt-4o",
            "provider": "openai",
            "env": {"OPENAI_API_KEY": "idempotent-secret"},
        },
    )
    migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )
    first_env = env_path.read_bytes()
    first_settings = settings_path.read_bytes()

    report = migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )

    assert env_path.read_bytes() == first_env
    assert settings_path.read_bytes() == first_settings
    assert report.migrated_keys == ()


def test_bare_deepseek_key_migrates_to_startable_custom_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "deepseek-chat",
            "env": {"DEEPSEEK_API_KEY": "direct-deepseek-key"},
        },
    )

    migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )

    migrated = read_private_env_file(env_path)
    assert migrated["MINI_CODE_PROVIDER"] == "custom"

    monkeypatch.setattr(config_module, "MINI_CODE_DIR", mini_dir)
    monkeypatch.setattr(config_module, "MINI_CODE_ENV_PATH", env_path)
    monkeypatch.setattr(config_module, "MINI_CODE_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(config_module, "MINI_CODE_MCP_PATH", mini_dir / "mcp.json")
    monkeypatch.setattr(
        config_module,
        "CLAUDE_SETTINGS_PATH",
        tmp_path / ".claude" / "settings.json",
    )
    for name in config_module.USER_MODEL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)

    runtime = config_module.load_runtime_config(tmp_path / "workspace")

    assert runtime["provider"] == "custom"
    assert runtime["customBaseUrl"] == "https://api.deepseek.com"
    assert runtime["customApiKey"] == "direct-deepseek-key"


def test_migration_preserves_env_precedence_over_structured_fields(tmp_path) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "structured-model",
            "provider": "custom",
            "customBaseUrl": "https://structured.invalid/v1",
            "customApiKey": "structured-primary-secret",
            "authToken": "structured-token-must-not-revive",
            "agentTurnBudget": {"maxModelCalls": 99},
            "subagentRouting": {
                "apiKey": "structured-child-secret",
                "models": {"plan": "structured-plan-model"},
            },
            "memoryHybrid": {"embeddingProvider": "local-e5"},
            "env": {
                "MINI_CODE_MODEL": "env-model",
                "CUSTOM_API_BASE_URL": "https://env.example/v1",
                "CUSTOM_API_KEY": "env-primary-secret",
                "ANTHROPIC_AUTH_TOKEN": "",
                "MINI_CODE_TURN_BUDGET_MODEL_CALLS": "7",
                "MINI_CODE_SUBAGENT_API_KEY": "env-child-secret",
                "MINI_CODE_SUBAGENT_PLAN_MODEL": "env-plan-model",
                "MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER": "qwen",
            },
        },
    )

    migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )

    values = read_private_env_file(env_path)
    assert values["MINI_CODE_MODEL"] == "env-model"
    assert values["CUSTOM_API_BASE_URL"] == "https://env.example/v1"
    assert values["CUSTOM_API_KEY"] == "env-primary-secret"
    assert values["ANTHROPIC_AUTH_TOKEN"] == ""
    assert values["MINI_CODE_TURN_BUDGET_MODEL_CALLS"] == "7"
    assert values["MINI_CODE_SUBAGENT_API_KEY"] == "env-child-secret"
    assert values["MINI_CODE_SUBAGENT_PLAN_MODEL"] == "env-plan-model"
    assert values["MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER"] == "qwen"


def test_migration_preserves_legacy_env_alias_precedence(tmp_path) -> None:
    mini_dir = tmp_path / ".mini-code"
    settings_path = mini_dir / "settings.json"
    env_path = mini_dir / ".env"
    _write_settings(
        settings_path,
        {
            "model": "structured-model",
            "provider": "openai",
            "openaiBaseUrl": "https://structured.invalid/v1",
            "env": {
                "ANTHROPIC_MODEL": "gpt-4o",
                "OPENAI_API_BASE": "https://env.example/v1",
                "OPENAI_API_KEY": "env-openai-secret",
            },
        },
    )

    migrate_model_config_to_user_env(
        settings_path=settings_path,
        env_path=env_path,
    )

    values = read_private_env_file(env_path)
    assert values["ANTHROPIC_MODEL"] == "gpt-4o"
    assert "MINI_CODE_MODEL" not in values
    assert values["OPENAI_API_BASE"] == "https://env.example/v1"
    assert "OPENAI_BASE_URL" not in values
