from __future__ import annotations

import sys

import pytest

import minicode.main as main_module


def test_cli_configuration_error_exits_instead_of_silently_using_mock(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "argv", ["minicode"])
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    monkeypatch.setattr(main_module, "_setup_cli_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "maybe_handle_management_command", lambda *_args: False)
    monkeypatch.setattr(
        main_module,
        "load_runtime_config",
        lambda _cwd: (_ for _ in ()).throw(RuntimeError("invalid provider config")),
    )

    with pytest.raises(SystemExit) as captured:
        main_module.main()

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert "Failed to load runtime config" in stderr
    assert "mock model" not in stderr
