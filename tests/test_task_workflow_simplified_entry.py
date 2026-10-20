"""Tests for the simplified workflow entry and the coached gate rejections.

The production Agent Loop disables direct state-changing sub-agents, so the
task tool must make central scheduling reachable: rejections coach the model
with a minimal WorkflowRequest.v1 example that itself passes
``parse_workflow_request``, and the simplified entry (``write_paths`` /
``verification_commands`` convenience fields) constructs the request through
the same contract channel instead of bypassing it.
"""

from __future__ import annotations

import json

import pytest

from minicode.multi_agent_contracts import parse_workflow_request
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task as task_module

_ENFORCED = ToolContext(_central_workflow_v2_enforced=True)


def _capture_sync_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _recording_run(input_data, context):
        captured.update(input_data)
        return ToolResult(ok=True, output="recorded")

    monkeypatch.setattr(task_module, "_SYNC_RUN", _recording_run)
    return captured


def _validated(payload: dict[str, object]) -> dict[str, object]:
    return task_module.task_tool.validator(payload)


def test_coaching_example_passes_contract_validation() -> None:
    """The example embedded in rejection errors must never teach an invalid
    request — parse it directly so drift fails loudly."""
    request = parse_workflow_request(
        task_module._WORKFLOW_REQUEST_EXAMPLE,
        legacy_task_id="task_test",
    )

    assert request.structured is True
    assert request.kind == "change"
    assert request.scope.write_paths == ("src/example.py",)


def _assert_example_embedded(output: str) -> None:
    example = json.dumps(task_module._WORKFLOW_REQUEST_EXAMPLE, indent=2)
    assert example in output


def test_general_rejection_coaches_the_workflow_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_module,
        "_SYNC_RUN",
        lambda *_args, **_kwargs: pytest.fail("legacy runner was called"),
    )
    validated = _validated(
        {
            "description": "direct change",
            "prompt": "Change files directly.",
            "agent_type": "general",
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is False
    assert "error[central_scheduler_required]" in result.output
    _assert_example_embedded(result.output)


def test_simplified_entry_constructs_structured_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_sync_run(monkeypatch)
    validated = _validated(
        {
            "description": "fix login redirect",
            "prompt": "Fix the login redirect bug and keep tests green.",
            "agent_type": "workflow",
            "write_paths": ["src/login.py", "tests/test_login.py"],
            "verification_commands": [["python3", "-m", "pytest", "-q"]],
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is True
    assert captured["_workflow_auto_constructed"] is True
    assert captured["_workflow_auto_constructed_mode"] == "structured"
    constructed = captured["workflow_request"]
    assert constructed is not None
    # The constructed request goes through the same contract channel.
    request = parse_workflow_request(constructed, legacy_task_id="task_test")
    assert request.structured is True
    assert request.kind == "change"
    assert request.scope.write_paths == ("src/login.py", "tests/test_login.py")
    assert len(request.verification_checks) == 1
    assert request.verification_checks[0].argv == ("python3", "-m", "pytest", "-q")
    # Existing clamping rules still apply: public input cannot certify the
    # parent's context.  A narrow, fully covered, non-sensitive scope now
    # certifies executable acceptance and takes the scoped route; the
    # deterministic Verifier gate still runs before commit.
    profile = request.task_profile
    assert profile.parent_has_sufficient_context is False
    assert profile.acceptance_executable is True


def test_simplified_entry_without_write_paths_keeps_fail_closed_legacy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty write scope must not be a construction failure: the dispatch
    keeps the legacy request (empty declared write set) and marks it."""
    captured = _capture_sync_run(monkeypatch)
    validated = _validated(
        {
            "description": "fix unknown bug",
            "prompt": "Diagnose the failing test, then fix the cause.",
            "agent_type": "workflow",
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is True
    assert captured["_workflow_auto_constructed"] is True
    assert captured["_workflow_auto_constructed_mode"] == "legacy"
    assert captured.get("workflow_request") is None


def test_write_paths_without_verification_commands_is_coached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_module,
        "_SYNC_RUN",
        lambda *_args, **_kwargs: pytest.fail("legacy runner was called"),
    )
    validated = _validated(
        {
            "description": "fix login redirect",
            "prompt": "Fix the login redirect bug.",
            "agent_type": "workflow",
            "write_paths": ["src/login.py"],
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is False
    assert "error[workflow_request_invalid]" in result.output
    assert "verification_commands" in result.output
    _assert_example_embedded(result.output)


def test_unrecognized_verification_command_is_coached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_module,
        "_SYNC_RUN",
        lambda *_args, **_kwargs: pytest.fail("legacy runner was called"),
    )
    validated = _validated(
        {
            "description": "fix login redirect",
            "prompt": "Fix the login redirect bug.",
            "agent_type": "workflow",
            "write_paths": ["src/login.py"],
            "verification_commands": [["curl", "http://example.com"]],
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is False
    assert "error[workflow_request_invalid]" in result.output
    assert "verifier grammar" in result.output
    _assert_example_embedded(result.output)


def test_string_verification_commands_are_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_sync_run(monkeypatch)
    validated = _validated(
        {
            "description": "fix login redirect",
            "prompt": "Fix the login redirect bug.",
            "agent_type": "workflow",
            "write_paths": ["src/login.py"],
            "verification_commands": ["python3 -m pytest tests/ -q"],
        }
    )

    result = task_module.task_tool.run(validated, _ENFORCED)

    assert result.ok is True
    request = parse_workflow_request(
        captured["workflow_request"], legacy_task_id="task_test"
    )
    assert request.verification_checks[0].argv == (
        "python3",
        "-m",
        "pytest",
        "tests/",
        "-q",
    )


def test_missing_description_is_rejected() -> None:
    with pytest.raises(ValueError, match="description is required"):
        _validated(
            {
                "prompt": "Fix the bug.",
                "agent_type": "workflow",
                "write_paths": ["src/login.py"],
                "verification_commands": [["python3", "-m", "pytest", "-q"]],
            }
        )


def test_convenience_fields_are_rejected_for_non_workflow_types() -> None:
    with pytest.raises(ValueError, match="only valid for agent_type=workflow"):
        _validated(
            {
                "description": "explore with scope",
                "prompt": "Look around.",
                "agent_type": "explore",
                "write_paths": ["src/login.py"],
            }
        )


def test_convenience_fields_cannot_mix_with_explicit_request() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        _validated(
            {
                "description": "fix login redirect",
                "prompt": "Fix the login redirect bug.",
                "agent_type": "workflow",
                "workflow_request": task_module._WORKFLOW_REQUEST_EXAMPLE,
                "write_paths": ["src/login.py"],
            }
        )


def test_non_enforced_context_does_not_auto_construct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy embedders keep the historical passthrough: no construction, no
    marker, no interference with the legacy compatibility route."""
    captured = _capture_sync_run(monkeypatch)
    validated = _validated(
        {
            "description": "fix unknown bug",
            "prompt": "Diagnose and fix.",
            "agent_type": "workflow",
        }
    )

    result = task_module.task_tool.run(validated, ToolContext())

    assert result.ok is True
    assert "_workflow_auto_constructed" not in captured
    assert captured.get("workflow_request") is None


def test_workflow_prompt_over_the_contract_limit_is_not_rejected() -> None:
    from minicode.workflow_prompt_criteria import fit_contract_text

    oversized = "Implement the store.\n" + ("x" * 4401)
    validated = _validated(
        {
            "description": "add tag filtering",
            "prompt": oversized,
            "agent_type": "workflow",
            "write_paths": ["notes/store.py"],
            "verification_commands": [["python3", "-m", "pytest", "-q"]],
        }
    )
    assert validated["prompt"] == oversized.strip()
    fitted = fit_contract_text(validated["prompt"])
    assert len(fitted) <= 4000
    assert fitted.startswith("Implement the store.")
    assert fitted.endswith("x")
    assert "[...]" in fitted
    request, error = task_module._auto_construct_workflow_request(validated)
    assert error is None
    assert "".join(item["text"] for item in request["criteria"]).count("x") == 4401


def test_workflow_prompt_at_limit_passes() -> None:
    validated = _validated(
        {
            "description": "add tag filtering",
            "prompt": "x" * task_module._WORKFLOW_PROMPT_MAX_CHARS,
            "agent_type": "workflow",
            "write_paths": ["notes/store.py"],
            "verification_commands": [["python3", "-m", "pytest", "-q"]],
        }
    )
    assert validated["prompt"] == "x" * task_module._WORKFLOW_PROMPT_MAX_CHARS


def test_prompt_gate_is_scoped_to_workflow_and_preserves_explicit_request() -> None:
    # Non-workflow agents keep the historical passthrough for long prompts.
    oversized = "x" * (task_module._WORKFLOW_PROMPT_MAX_CHARS + 1)
    validated = _validated(
        {
            "description": "long read-only brief",
            "prompt": oversized,
            "agent_type": "explore",
        }
    )
    assert validated["prompt"] == oversized
    # The explicit workflow_request path is unchanged: a normal-length prompt
    # with a valid request validates exactly as before.
    validated = _validated(
        {
            "description": "fix login redirect",
            "prompt": "Fix the login redirect bug.",
            "agent_type": "workflow",
            "workflow_request": task_module._WORKFLOW_REQUEST_EXAMPLE,
        }
    )
    assert validated["workflow_request"]["requestVersion"] == 1
