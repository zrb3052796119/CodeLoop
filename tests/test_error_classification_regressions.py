"""Recovery must follow diagnostics, not words in displayed test source/paths."""

from __future__ import annotations

import pytest

from minicode.agent_intelligence import (
    ErrorClassifier,
    NudgeGenerator,
    RecoveryStrategy,
)


@pytest.mark.parametrize("tool_name", ["run_command", "test_runner"])
def test_pytest_assertion_does_not_trigger_network_retry(tool_name: str) -> None:
    output = """============================= FAILURES =============================
________________________ test_connection_recovery ________________________
    def test_connection_recovery():
        connection = FakeConnection(timeout=1)
        expected = "network refused unreachable reset closed dns ssl certificate"
>       assert connection.state == expected
E       AssertionError: assert 'closed' == 'network refused unreachable'
tests/test_connection_pool.py:42: AssertionError
==================== 1 failed, 10 passed in 0.15s ====================
"""

    error = ErrorClassifier.classify(output, tool_name)

    assert error.category.value == "test_failure"
    assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE
    nudge = NudgeGenerator.generate(error)
    assert "assertion" in nudge
    assert "rerun the affected tests" in nudge
    assert "retry the same operation" not in nudge.lower()


@pytest.mark.parametrize(
    "diagnostic",
    [
        "ModuleNotFoundError: No module named 'jsonschema'",
        "ImportError: cannot import name 'Draft202012Validator' from 'jsonschema'",
    ],
)
def test_missing_dependency_under_memory_path_requires_environment_repair(diagnostic: str) -> None:
    output = f"""ImportError while loading conftest '/repo/tests/conftest.py'.
tests/conftest.py:12: in <module>
    from minicode.memory import MemoryManager
minicode/memory.py:39: in <module>
    import jsonschema
E   {diagnostic}
"""

    error = ErrorClassifier.classify(output, "run_command")

    assert error.category.value == "dependency"
    assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE
    nudge = NudgeGenerator.generate(error)
    assert "interpreter" in nudge
    assert "dependencies" in nudge
    assert "freeing up resources" not in nudge


@pytest.mark.parametrize(
    "output",
    [
        "\x1b[31mE   AssertionError: connection timeout\x1b[0m",
        "E       assert connection.is_open\n1 failed in 0.2s",
        "FAILED tests/test_network.py::test_timeout - AssertionError: connection closed",
        "==================== 3 failed, 2 passed in 0.2s ====================",
        "==================== 2 passed, 3 failed in 0.2s ====================",
        "🧪 Test Runner\n📊 Results:\n  ✓ Passed:  4\n  ✗ Failed:  1\n",
    ],
)
def test_test_failure_receipts_are_actionable_without_keyword_voting(output: str) -> None:
    error = ErrorClassifier.classify(output, "test_runner")

    assert error.category.value == "test_failure"
    assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE


@pytest.mark.parametrize(
    ("diagnostic", "category", "strategy"),
    [
        ("requests.exceptions.ConnectionError: peer disappeared", "network", "retry_exponential_backoff"),
        ("ConnectionRefusedError: [Errno 61] Connection refused", "network", "retry_exponential_backoff"),
        ("TimeoutError: connection attempt exceeded its deadline", "timeout", "wait_and_retry"),
        ("MemoryError", "resource", "wait_and_retry"),
        ("OSError: [Errno 28] No space left on device", "resource", "wait_and_retry"),
        ("PermissionError: [Errno 13] Permission denied", "permission", "request_permission"),
    ],
)
def test_real_terminal_exception_outweighs_unrelated_traceback_source(
    diagnostic: str, category: str, strategy: str
) -> None:
    output = f"""Traceback (most recent call last):
  File "/repo/memory/connection/timeout/permissions.py", line 42, in execute
    raise ValueError("invalid input syntax format resource quota limit exceeded")
{diagnostic}
"""

    error = ErrorClassifier.classify(output, "run_command")

    assert error.category.value == category
    assert error.strategy.value == strategy


def test_chained_exception_uses_final_assertion_diagnostic() -> None:
    output = """ConnectionError: Connection refused

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/repo/tests/test_connection.py", line 12, in test_error_message
    assert "expected" in str(error)
AssertionError: unexpected connection refused message
"""

    error = ErrorClassifier.classify(output, "run_command")

    assert error.category.value == "test_failure"


@pytest.mark.parametrize(
    "output",
    [
        "/repo/memory/connection/resource_limit.py:42: something unexpected happened",
        "    raise ConnectionError('connection refused')",
        "connection_pool.py\nmemory.py\ntimeout.py",
    ],
)
def test_paths_and_source_excerpts_do_not_diagnose_runtime_failures(output: str) -> None:
    assert ErrorClassifier.classify(output, "run_command").category.value == "unknown"


def test_missing_module_reported_by_interpreter_is_dependency_failure() -> None:
    error = ErrorClassifier.classify("/repo/memory/.venv/bin/python: No module named jsonschema", "run_command")

    assert error.category.value == "dependency"


def test_read_file_missing_path_keeps_existing_recovery_strategy() -> None:
    error = ErrorClassifier.classify("File not found: /repo/memory/connection.py", "read_file")

    assert error.category.value == "logic"
    assert error.strategy is RecoveryStrategy.SKIP_AND_CONTINUE
