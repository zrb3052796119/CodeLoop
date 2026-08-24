"""Pytest collection controls for repository-local legacy smoke scripts."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

from tests.global_state_isolation import (
    FormalStateChanged,
    GlobalStateGuard,
    IsolatedTestHome,
    assert_minicode_not_preloaded,
)


_REAL_HOME = Path(
    os.environ.get("MINICODE_PYTEST_REAL_HOME")
    or os.environ.get("HOME")
    or Path.home()
).expanduser().resolve()
assert_minicode_not_preloaded(sys.modules, real_home=_REAL_HOME)
_TEST_HOME = IsolatedTestHome.create(real_home=_REAL_HOME, environ=os.environ)


_REAL_MINICODE_DIR = _REAL_HOME / ".mini-code"
_PROTECTED_REAL_PATHS = (
    _REAL_MINICODE_DIR / "memory" / "memory.json",
    _REAL_MINICODE_DIR / "memory" / "MEMORY.md",
    _REAL_MINICODE_DIR / "memory" / "approval_audit.json",
    _REAL_MINICODE_DIR / "sessions_index.json",
    _REAL_MINICODE_DIR / ".env",
    _REAL_MINICODE_DIR / "settings.json",
    _REAL_MINICODE_DIR / "history.json",
    _REAL_MINICODE_DIR / "context_state.json",
    _REAL_MINICODE_DIR / "USER.md",
    _REAL_MINICODE_DIR / "permissions.json",
    _REAL_MINICODE_DIR / "mcp.json",
    _REAL_MINICODE_DIR / "minicode.log",
    _REAL_MINICODE_DIR / "cybernetic_supervisor.json",
)
_REAL_HOME_GUARD = GlobalStateGuard(
    _PROTECTED_REAL_PATHS,
    protected_roots=(_REAL_MINICODE_DIR,),
)
_REAL_HOME_GUARD_FAILURE: str | None = None


# These root-level scripts are manual smoke/integration utilities from earlier
# development rounds. Normal pytest coverage lives under tests/.
collect_ignore = [
    "smoke_test.py",
    "test_chinese_input.py",
    "test_integration.py",
    "test_optim.py",
    "test_run.py",
    "test_state_integration.py",
    "visual_test.py",
]

collect_ignore_glob = [
    "benchmarks/*.py",
]


def _reset_imported_process_state() -> None:
    logger = logging.getLogger("minicode")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    history_module = sys.modules.get("minicode.history")
    if history_module is not None:
        setattr(history_module, "_history_cache", None)
    context_module = sys.modules.get("minicode.context_manager")
    token_cache = getattr(context_module, "_token_cache", None) if context_module else None
    if isinstance(token_cache, dict):
        token_cache.clear()


def pytest_sessionstart(session: pytest.Session) -> None:
    _REAL_HOME_GUARD.capture_start()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _REAL_HOME_GUARD_FAILURE
    try:
        _REAL_HOME_GUARD.assert_unchanged()
    except FormalStateChanged as exc:
        _REAL_HOME_GUARD_FAILURE = str(exc)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if _REAL_HOME_GUARD_FAILURE:
        terminalreporter.write_sep("=", "REAL HOME POLLUTION DETECTED")
        terminalreporter.write_line(_REAL_HOME_GUARD_FAILURE)


def pytest_unconfigure(config: pytest.Config) -> None:
    _reset_imported_process_state()
    _TEST_HOME.cleanup()


@pytest.fixture(autouse=True)
def _isolate_minicode_state_per_test():
    _reset_imported_process_state()
    _TEST_HOME.reset()
    yield
    _reset_imported_process_state()
    _TEST_HOME.reset()


@pytest.fixture
def minicode_test_home() -> Path:
    return _TEST_HOME.home


@pytest.fixture
def minicode_real_home() -> Path:
    return _REAL_HOME


@pytest.fixture
def memory_manager(tmp_path):
    """Create a MemoryManager with temporary paths."""
    from minicode.memory import MemoryManager
    return MemoryManager(project_root=tmp_path)


@pytest.fixture
def memory_with_entries(memory_manager):
    """Create a MemoryManager pre-populated with test entries."""
    from minicode.memory import MemoryScope
    entries = [
        ("project", "architecture", "Uses FastAPI for REST API backend", ["api", "fastapi"]),
        ("project", "code-pattern", "All functions use snake_case naming", ["convention", "naming"]),
        ("project", "testing", "Tests use pytest with fixtures", ["test", "pytest"]),
        ("user", "preference", "Always respond in Chinese", ["language", "chinese"]),
        ("local", "decision", "Use SQLite for development database", ["database", "sqlite"]),
    ]
    for scope, category, content, tags in entries:
        memory_manager.add_entry(
            MemoryScope(scope), category, content, tags
        )
    return memory_manager


@pytest.fixture
def mock_memory_search():
    """Mock search function for testing prompt injection."""
    def mock_search(query, scope=None, limit=20, min_relevance=0.1):
        from minicode.memory import MemoryEntry, MemoryScope
        return [
            MemoryEntry(id="test-1", scope=MemoryScope.PROJECT, category="test", content=f"Mock result for: {query}"),
        ]
    return mock_search


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with basic structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "src" / "main.py").write_text("# Main file\n")
    return str(workspace)
