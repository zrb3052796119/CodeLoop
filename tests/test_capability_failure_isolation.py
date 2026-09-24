"""Independent regression probes for descriptive capability fault isolation."""

from __future__ import annotations

from copy import deepcopy
from threading import Event

import pytest

from minicode.capability_contracts import CapabilityProfile, CapabilityStatus
from minicode.tooling import (
    ToolContext,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolRegistry,
    ToolResult,
    ToolTerminationReason,
)


def test_capability_serialization_failure_preserves_successful_tool_result(
    tmp_path, caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenSerialization(CapabilityProfile):
        def to_dict(self):
            raise ValueError("fixture-private-serialization-value")

    completed = ToolResult(ok=True, output="Requested change completed")
    calls = []

    def run(arguments, _context):
        calls.append(deepcopy(arguments))
        return completed

    tool = ToolDefinition(
        name="fixture_writer",
        description="Writes the requested fixture target",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=run,
        capability_profile=BrokenSerialization(domain="file", scope="write"),
    )

    actual = ToolRegistry([tool]).execute(
        tool.name, {"destination": "requested"}, ToolContext(cwd=str(tmp_path)),
    )

    assert calls == [{"destination": "requested"}]
    assert actual is completed
    assert actual.ok is True
    assert actual.output == "Requested change completed"
    assert actual.capability is None
    assert "fixture-private-serialization-value" not in caplog.text


@pytest.mark.parametrize("classification_fails", [False, True])
def test_classifier_cannot_modify_nested_execution_or_caller_arguments(
    tmp_path, classification_fails: bool,
) -> None:
    profile = CapabilityProfile(domain="file", scope="write")
    original = {"destination": "requested", "options": {"paths": ["allowed.txt"]}}
    expected = deepcopy(original)
    calls = []

    def classify(arguments):
        arguments["destination"] = "unexpected"
        arguments["options"]["paths"].append("unexpected.txt")
        if classification_fails:
            raise ValueError("fixture classifier failure")
        return profile

    def run(arguments, _context):
        calls.append(deepcopy(arguments))
        return ToolResult(ok=True, output="Requested change completed")

    tool = ToolDefinition(
        name="fixture_writer",
        description="Writes the requested fixture target",
        input_schema={"type": "object"},
        # A legal identity validator makes argument aliasing observable.
        validator=lambda value: value,
        run=run,
        capability_profile=profile,
        capability_for=classify,
    )

    result = ToolRegistry([tool]).execute(
        tool.name, original, ToolContext(cwd=str(tmp_path)),
    )

    assert original == expected
    assert calls == [expected]
    assert result.ok is True
    assert result.capability is not None
    assert result.capability["status"] == (
        CapabilityStatus.INVALID.value
        if classification_fails
        else CapabilityStatus.DECLARED.value
    )


def test_abandonment_during_classification_prevents_tool_dispatch(tmp_path) -> None:
    abandoned = Event()
    profile = CapabilityProfile(domain="file", scope="write")
    calls = []

    def classify(_arguments):
        # Deterministically simulate owner abandonment while classification runs.
        abandoned.set()
        return profile

    def run(arguments, _context):
        calls.append(arguments)
        return ToolResult(ok=True, output="Should never execute")

    tool = ToolDefinition(
        name="fixture_writer",
        description="Writes the requested fixture target",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=run,
        capability_profile=profile,
        capability_for=classify,
    )

    with pytest.raises(ToolExecutionAbandoned):
        ToolRegistry([tool]).execute(
            tool.name, {}, ToolContext(cwd=str(tmp_path), _tool_abandoned=abandoned),
        )

    assert calls == []


def test_capability_field_preserves_existing_tool_result_positional_contract() -> None:
    result = ToolResult(
        True, "Committed", None, False, None, True, ToolTerminationReason.ABANDONED,
    )

    assert result._must_deliver is True
    assert result._termination_reason is ToolTerminationReason.ABANDONED
    assert result.capability is None
