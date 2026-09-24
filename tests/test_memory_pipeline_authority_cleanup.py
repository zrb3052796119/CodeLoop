from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import minicode.memory as memory_module
from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_injector import MemoryInjector
from minicode.memory_pipeline import MemoryPipeline


def _pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MemoryPipeline, MemoryManager]:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use isolated pytest fixtures for checkout tests.",
        tags=["pytest", "checkout"],
    ) is not None
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace))
    return pipeline, manager


def test_pipeline_owns_the_canonical_controller_and_retriever(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)

    assert not hasattr(pipeline, "_injector")
    assert pipeline.injection_controller is not None
    assert pipeline._retriever._controller is pipeline.injection_controller

    pipeline.read("pytest checkout fixtures")

    assert pipeline.last_injection_decision is not None
    assert pipeline.last_injection_decision.mode.value == "standard"


def test_orchestrator_and_pipeline_share_one_memory_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = MagicMock()
    pipeline_type = MagicMock(return_value=pipeline)
    monkeypatch.setattr("minicode.memory_pipeline.MemoryPipeline", pipeline_type)
    orchestrator = CyberneticOrchestrator()
    orchestrator.initialize(MagicMock(model_id="test-model"), MagicMock())

    orchestrator.wire_memory(MagicMock())

    assert pipeline_type.call_args.kwargs["injection_controller"] is (
        orchestrator.memory_ctrl
    )


def test_compatibility_injector_does_not_retain_dead_candidate_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipeline_instance, manager = _pipeline(tmp_path, monkeypatch)
    injector = MemoryInjector(
        manager,
        injection_cooldown=0.0,
        reranker=object(),
    )

    assert injector.inject_for_task("pytest checkout fixtures")

    assert not {
        "_reranker",
        "_last_rerank_result",
        "_last_result",
    } & vars(injector).keys()


@pytest.mark.parametrize(
    "poisoned_state",
    [
        {"read_count": "poisoned", "write_count": 0, "maintain_count": 0},
        {"read_count": True, "write_count": 0, "maintain_count": 0},
        {"read_count": -1, "write_count": 0, "maintain_count": 0},
        {"read_count": 10**30, "write_count": 0, "maintain_count": 0},
        {
            "schema_version": 2,
            "read_count": 99,
            "write_count": 99,
            "maintain_count": 99,
        },
    ],
)
def test_invalid_pipeline_state_cannot_poison_runtime_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    poisoned_state: dict[str, object],
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)
    state_path = Path(pipeline._state_path() or "")
    state_path.write_text(json.dumps(poisoned_state), encoding="utf-8")

    pipeline.initialize(workspace_path=str(tmp_path / "workspace"))
    pipeline.read("pytest checkout fixtures")

    assert pipeline.stats["read_count"] == 1
    assert pipeline.stats["write_count"] == 0
    assert pipeline.stats["maintain_count"] == 0


def test_pipeline_state_reader_rejects_symlink_and_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)
    state_path = Path(pipeline._state_path() or "")
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "read_count": 99,
                "write_count": 99,
                "maintain_count": 99,
            }
        ),
        encoding="utf-8",
    )
    state_path.unlink(missing_ok=True)
    state_path.symlink_to(outside)

    pipeline.initialize(workspace_path=str(tmp_path / "workspace"))
    assert pipeline.stats["read_count"] == 0

    state_path.unlink()
    state_path.write_text("{" + " " * 70_000 + "}", encoding="utf-8")
    pipeline.initialize(workspace_path=str(tmp_path / "workspace"))
    assert pipeline.stats["read_count"] == 0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_pipeline_state_reader_rejects_fifo_without_opening_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)
    state_path = Path(pipeline._state_path() or "")
    state_path.unlink(missing_ok=True)
    os.mkfifo(state_path)

    pipeline.initialize(workspace_path=str(tmp_path / "workspace"))

    assert pipeline.stats["read_count"] == 0


def test_valid_pipeline_state_restores_bounded_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)
    state_path = Path(pipeline._state_path() or "")
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "read_count": 7,
                "write_count": 5,
                "maintain_count": 3,
            }
        ),
        encoding="utf-8",
    )

    pipeline.initialize(workspace_path=str(tmp_path / "workspace"))

    assert pipeline.stats["read_count"] == 7
    assert pipeline.stats["write_count"] == 5
    assert pipeline.stats["maintain_count"] == 3


def test_pipeline_state_persists_only_supported_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager = _pipeline(tmp_path, monkeypatch)
    pipeline.save_state()

    payload = json.loads(Path(pipeline._state_path() or "").read_text())

    assert payload["schema_version"] == 1
    assert set(payload) == {
        "schema_version",
        "read_count",
        "write_count",
        "maintain_count",
        "timestamp",
    }
