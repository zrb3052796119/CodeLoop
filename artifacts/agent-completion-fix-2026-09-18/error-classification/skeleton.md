# Repository Test Skeleton

Container style: `function`
Common fixtures: `category, diagnostic, output, strategy, tool_name`

## Candidate scaffold

```python
# Select only imports required by the target behavior from the examples below.

def test_changed_behavior(category, diagnostic, output):
    # Arrange repository-native inputs and state.
    # Act through a supported public entry point.
    # Assert one externally visible behavioral difference.
    raise NotImplementedError
```

## Ranked examples

### `tests/test_error_classification_regressions.py`

Fixtures used: `category, diagnostic, output, strategy, tool_name`
Test names: `test_pytest_assertion_does_not_trigger_network_retry, test_missing_dependency_under_memory_path_requires_environment_repair, test_test_failure_receipts_are_actionable_without_keyword_voting, test_real_terminal_exception_outweighs_unrelated_traceback_source, test_chained_exception_uses_final_assertion_diagnostic, test_paths_and_source_excerpts_do_not_diagnose_runtime_failures, test_missing_module_reported_by_interpreter_is_dependency_failure, test_read_file_missing_path_keeps_existing_recovery_strategy`

Imports:
```python
from __future__ import annotations
import pytest
from minicode.agent_intelligence import (
    ErrorClassifier,
    NudgeGenerator,
    RecoveryStrategy,
)
```
Assertion examples:
```python
assert error.category.value == "test_failure"
assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE
assert "assertion" in nudge
assert "rerun the affected tests" in nudge
assert "retry the same operation" not in nudge.lower()
assert error.category.value == "dependency"
assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE
assert "interpreter" in nudge
assert "dependencies" in nudge
assert "freeing up resources" not in nudge
assert error.category.value == "test_failure"
assert error.strategy is RecoveryStrategy.FALLBACK_ALTERNATIVE
assert error.category.value == category
assert error.strategy.value == strategy
assert error.category.value == "test_failure"
```
