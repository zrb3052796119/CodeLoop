from __future__ import annotations

import re
from pathlib import Path

import pytest

from minicode.agent_loop import (
    _reflection_memory_operation_id,
    run_agent_turn,
)
from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory import MemoryManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class _FinalModel(ModelAdapter):
    def next(
        self,
        _messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        return AgentStep(type="assistant", content="done")


def test_reflection_operation_identity_is_stable_and_context_bound() -> None:
    provenance = {
        "run_id": "run_" + "a" * 32,
        "subagent_id": "sub_" + "b" * 32,
        "turn_id": "turn_" + "c" * 32,
        "agent_depth": 2,
    }

    first = _reflection_memory_operation_id(provenance)
    reordered = _reflection_memory_operation_id(
        {
            "agent_depth": 2,
            "turn_id": provenance["turn_id"],
            "subagent_id": provenance["subagent_id"],
            "run_id": provenance["run_id"],
            "ignored_content": "must not affect an opaque write identity",
        }
    )

    assert first == reordered
    assert first is not None
    assert re.fullmatch(r"memoryop_[0-9a-f]{32}", first)
    assert _reflection_memory_operation_id(
        {**provenance, "run_id": "run_" + "d" * 32}
    ) != first
    assert _reflection_memory_operation_id(
        {**provenance, "turn_id": "turn_" + "e" * 32}
    ) != first
    assert _reflection_memory_operation_id(
        {**provenance, "subagent_id": "sub_" + "f" * 32}
    ) != first
    assert _reflection_memory_operation_id(
        {**provenance, "agent_depth": 3}
    ) != first


@pytest.mark.parametrize(
    "provenance",
    [
        {},
        {"agent_depth": 1},
        {"run_id": None, "subagent_id": None, "turn_id": None},
        {"run_id": 7, "subagent_id": False, "turn_id": []},
    ],
)
def test_reflection_operation_identity_requires_stable_provenance(
    provenance: dict[str, object],
) -> None:
    assert _reflection_memory_operation_id(provenance) is None  # type: ignore[arg-type]


def test_run_agent_turn_binds_reflection_operation_to_real_run_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(
        project_root=workspace,
        data_root=tmp_path / "memory-home" / ".mini-code",
    )
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )
    turn_id = "turn_" + "1" * 32
    captured: list[dict[str, object]] = []

    def capture_reflection(self, **kwargs):
        captured.append(dict(kwargs))

    monkeypatch.setattr(
        CyberneticOrchestrator,
        "reflect_on_task",
        capture_reflection,
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Bind reflection write operation",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        run_agent_turn(
            model=_FinalModel(),
            tools=ToolRegistry([]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {
                    "role": "user",
                    "content": "implement and verify the parser fix",
                    "_conversation_turn_id": turn_id,
                },
            ],
            cwd=str(workspace),
            memory_manager=manager,
            event_sink=observation,
            agent_depth=2,
            max_steps=2,
        )

    assert run_id is not None
    assert len(captured) == 1
    provenance = captured[0]["provenance"]
    operation_id = captured[0]["operation_id"]
    assert provenance == {
        "run_id": run_id,
        "subagent_id": None,
        "turn_id": turn_id,
        "agent_depth": 2,
    }
    assert isinstance(operation_id, str)
    assert re.fullmatch(r"memoryop_[0-9a-f]{32}", operation_id)
    assert operation_id == _reflection_memory_operation_id(provenance)
