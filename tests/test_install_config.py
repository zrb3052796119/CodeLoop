from __future__ import annotations

import os

import minicode.install as install_module
from minicode.env_file import read_private_env_file


def test_installer_writes_provider_profile_to_private_user_env(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    env_path = mini_dir / ".env"
    settings_path = mini_dir / "settings.json"
    answers = {
        "Model name": "deepseek-chat",
        "Provider (anthropic/openai/openrouter/custom)": "custom",
        "CUSTOM_API_BASE_URL": "https://api.deepseek.com",
    }
    monkeypatch.setattr(install_module, "MINI_CODE_DIR", mini_dir)
    monkeypatch.setattr(install_module, "MINI_CODE_ENV_PATH", env_path)
    monkeypatch.setattr(install_module, "MINI_CODE_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(install_module, "load_effective_settings", lambda: {})
    monkeypatch.setattr(
        install_module,
        "_read_input",
        lambda prompt, default=None: answers.get(prompt, default or ""),
    )
    monkeypatch.setattr(
        install_module,
        "_read_secret",
        lambda _prompt: "installer-canary-secret",
    )
    monkeypatch.setattr(install_module, "_install_launcher_script", lambda: None)

    install_module.main()

    values = read_private_env_file(env_path)
    assert values["MINI_CODE_MODEL"] == "deepseek-chat"
    assert values["MINI_CODE_PROVIDER"] == "custom"
    assert values["CUSTOM_API_BASE_URL"] == "https://api.deepseek.com"
    assert values["CUSTOM_API_KEY"] == "installer-canary-secret"
    assert not settings_path.exists()
    assert "installer-canary-secret" not in capsys.readouterr().out
    if os.name == "posix":
        assert (mini_dir.stat().st_mode & 0o777) == 0o700
        assert (env_path.stat().st_mode & 0o777) == 0o600


def test_secret_input_uses_non_echoing_getpass(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        install_module.getpass,
        "getpass",
        lambda prompt: captured.append(prompt) or "hidden-value",
    )

    assert install_module._read_secret("API key") == "hidden-value"
    assert captured == ["API key: "]
