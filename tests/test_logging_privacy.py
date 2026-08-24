from __future__ import annotations

import logging
import os

import pytest

import minicode.logging_config as logging_module


def _close_minicode_handlers() -> None:
    logger = logging.getLogger("minicode")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def test_file_logging_is_private_and_redacts_messages_and_exceptions(
    tmp_path,
    monkeypatch,
) -> None:
    mini_dir = tmp_path / ".mini-code"
    log_file = mini_dir / "minicode.log"
    monkeypatch.setattr(logging_module, "MINI_CODE_DIR", mini_dir)
    monkeypatch.setattr(logging_module, "LOG_FILE", log_file)
    _close_minicode_handlers()

    logger = logging_module.setup_logging(
        level="DEBUG",
        log_to_file=True,
        log_to_console=False,
    )
    logger.error(
        "CUSTOM_API_KEY=canary-assignment Authorization: Bearer canary-bearer "
        "sk-ant-canarytoken"
    )
    logger.error(
        'json={"api_key":"canary-json"} '
        "python={'Authorization': 'Bearer canary-python'} "
        'headers={"X-Api-Key": "canary-header"}'
    )
    try:
        raise RuntimeError("password=canary-exception")
    except RuntimeError:
        logger.exception("request failed")
    _close_minicode_handlers()

    content = log_file.read_text(encoding="utf-8")
    for secret in (
        "canary-assignment",
        "canary-bearer",
        "canarytoken",
        "canary-exception",
        "canary-json",
        "canary-python",
        "canary-header",
    ):
        assert secret not in content
    assert "<redacted>" in content
    if os.name == "posix":
        assert (mini_dir.stat().st_mode & 0o777) == 0o700
        assert (log_file.stat().st_mode & 0o777) == 0o600


def test_logging_refuses_a_symlink_destination(tmp_path, monkeypatch) -> None:
    mini_dir = tmp_path / ".mini-code"
    mini_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.log"
    outside.write_text("safe", encoding="utf-8")
    log_file = mini_dir / "minicode.log"
    log_file.symlink_to(outside)
    monkeypatch.setattr(logging_module, "MINI_CODE_DIR", mini_dir)
    monkeypatch.setattr(logging_module, "LOG_FILE", log_file)
    _close_minicode_handlers()

    with pytest.raises(RuntimeError, match="log_file_symlink"):
        logging_module.setup_logging(log_to_file=True, log_to_console=False)

    assert outside.read_text(encoding="utf-8") == "safe"
