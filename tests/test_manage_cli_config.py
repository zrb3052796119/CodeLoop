from __future__ import annotations

import sys

import minicode.main as main_module
from minicode.config_migration import ModelEnvMigrationReport
from minicode.manage_cli import maybe_handle_management_command


def test_config_migrate_env_management_command_is_secret_free(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_migrate(**kwargs):
        captured.update(kwargs)
        return ModelEnvMigrationReport(
            env_path="/safe/user/.mini-code/.env",
            settings_path="/safe/user/.mini-code/settings.json",
            migrated_keys=("CUSTOM_API_KEY", "MINI_CODE_MODEL"),
            removed_legacy_fields=("env.CUSTOM_API_KEY", "model"),
            scrubbed_workspace_keys=("MINICODE_EMBEDDING_API_KEY",),
        )

    monkeypatch.setattr(
        "minicode.config_migration.migrate_model_config_to_user_env",
        fake_migrate,
    )

    handled = maybe_handle_management_command(
        str(tmp_path),
        [
            "config",
            "migrate-env",
            "--import-workspace-env",
            ".env",
            "--scrub-workspace",
        ],
    )

    assert handled is True
    assert captured["workspace_env_path"] == tmp_path / ".env"
    assert captured["scrub_workspace"] is True
    output = capsys.readouterr().out
    assert "Migrated keys: 2" in output
    assert "CUSTOM_API_KEY" not in output


def test_main_preserves_management_option_names_and_values(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "minicode",
            "config",
            "migrate-env",
            "--import-workspace-env",
            ".env",
            "--scrub-workspace",
        ],
    )
    monkeypatch.setattr(main_module, "_setup_cli_logging", lambda *_args, **_kwargs: None)

    def fake_management(cwd, argv):
        captured["cwd"] = cwd
        captured["argv"] = argv
        return True

    monkeypatch.setattr(main_module, "maybe_handle_management_command", fake_management)

    main_module.main()

    assert captured["argv"] == [
        "config",
        "migrate-env",
        "--import-workspace-env",
        ".env",
        "--scrub-workspace",
    ]
