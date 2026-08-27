"""Architectural boundary tests for the centrally controlled workflow runner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


def _imported_modules(source_path: Path) -> set[str | None]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return imported


def test_workflow_runner_exposes_one_complete_run_seam() -> None:
    from minicode import workflow_runner

    assert workflow_runner.__all__ == ("run_workflow",)
    parameters = inspect.signature(workflow_runner.run_workflow).parameters
    assert tuple(parameters) == (
        "input_data",
        "context",
        "depth",
        "subagent_id",
        "run_subagent",
    )
    assert parameters["depth"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["subagent_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["run_subagent"].kind is inspect.Parameter.KEYWORD_ONLY


def test_workflow_runner_does_not_reverse_import_task_tool() -> None:
    from minicode import workflow_runner

    source_path = Path(workflow_runner.__file__)
    assert "minicode.tools.task" not in _imported_modules(source_path)


def test_task_tool_does_not_own_workflow_runtime_internals() -> None:
    import minicode.tools.task as task_module

    forbidden = {
        "minicode.central_scheduler",
        "minicode.deterministic_verifier",
        "minicode.scheduler_artifacts",
        "minicode.scheduler_audit",
        "minicode.scheduler_trace",
        "minicode.workflow_workspace",
    }
    assert _imported_modules(Path(task_module.__file__)).isdisjoint(forbidden)


def test_task_binds_the_current_child_runner_at_workflow_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.tools.task as task_module
    from minicode.tooling import ToolContext, ToolResult

    captured: dict[str, object] = {}

    def child_runner(_input_data: dict, _context: object) -> ToolResult:
        raise AssertionError("the capture runner must not dispatch a child")

    def capture_workflow(
        input_data: dict,
        context: object,
        *,
        depth: int,
        subagent_id: str,
        run_subagent: object,
    ) -> ToolResult:
        captured.update(
            input_data=input_data,
            context=context,
            depth=depth,
            subagent_id=subagent_id,
            run_subagent=run_subagent,
        )
        return ToolResult(ok=True, output="captured")

    monkeypatch.setattr(task_module, "_run", child_runner)
    monkeypatch.setattr(task_module, "run_workflow", capture_workflow)

    result = task_module.task_tool.run(
        {
            "description": "capture workflow",
            "prompt": "Capture the runner handoff.",
            "agent_type": "workflow",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    assert result.ok is True
    assert captured["run_subagent"] is child_runner
    assert captured["depth"] == 0
    assert str(captured["subagent_id"]).startswith("sub_")
