"""Tests for the permission mode wiring: env var, shared checker, /mode command."""

from __future__ import annotations

import warnings

import pytest

import minicode.auto_mode as auto_mode
from minicode.auto_mode import (
    PERMISSION_MODE_ENV_VAR,
    PermissionMode,
    get_checker,
    get_permission_mode,
    set_permission_mode,
)
from minicode.cli_commands import try_handle_local_command
from minicode.permissions import PermissionManager


@pytest.fixture(autouse=True)
def reset_mode_state(monkeypatch):
    """Restore the module-level singletons around every test."""
    monkeypatch.delenv(PERMISSION_MODE_ENV_VAR, raising=False)
    original_mode = PermissionMode.DEFAULT
    auto_mode._env_initialized = False
    auto_mode._checker.set_mode(original_mode)
    auto_mode._mode_state.mode = original_mode
    yield
    auto_mode._env_initialized = False
    auto_mode._checker.set_mode(original_mode)
    auto_mode._mode_state.mode = original_mode


def test_env_var_selects_startup_mode(monkeypatch):
    monkeypatch.setenv(PERMISSION_MODE_ENV_VAR, "auto")
    assert get_permission_mode() is PermissionMode.AUTO


def test_env_var_invalid_value_warns_and_keeps_default(monkeypatch):
    monkeypatch.setenv(PERMISSION_MODE_ENV_VAR, "yolo")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_permission_mode() is PermissionMode.DEFAULT
    assert any(PERMISSION_MODE_ENV_VAR in str(w.message) for w in caught)


def test_env_var_unset_keeps_default():
    assert get_permission_mode() is PermissionMode.DEFAULT


def test_explicit_set_outranks_later_env_init(monkeypatch):
    set_permission_mode(PermissionMode.PLAN)
    monkeypatch.setenv(PERMISSION_MODE_ENV_VAR, "bypass")
    # A later get_checker() must not let the env var clobber the user choice.
    assert get_permission_mode() is PermissionMode.PLAN


def test_permission_manager_shares_global_checker(tmp_path):
    set_permission_mode(PermissionMode.AUTO)
    manager = PermissionManager(str(tmp_path), prompt=None)
    assert manager.auto_checker is get_checker()
    assert manager.auto_checker.mode is PermissionMode.AUTO


def test_permission_manager_explicit_mode_gets_dedicated_checker(tmp_path):
    manager = PermissionManager(
        str(tmp_path), prompt=None, auto_mode=PermissionMode.PLAN
    )
    assert manager.auto_checker is not get_checker()
    assert manager.auto_checker.mode is PermissionMode.PLAN


def test_mode_command_shows_status():
    result = try_handle_local_command("/mode")
    assert result is not None
    assert "Current mode: default" in result


def test_mode_command_switches_mode():
    result = try_handle_local_command("/mode auto")
    assert result is not None
    assert "Auto mode" in result
    assert get_permission_mode() is PermissionMode.AUTO


def test_mode_command_rejects_unknown_mode():
    result = try_handle_local_command("/mode nope")
    assert result is not None
    assert "Unknown permission mode" in result
    assert get_permission_mode() is PermissionMode.DEFAULT
