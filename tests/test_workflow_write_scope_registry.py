"""Static write-conflict preflight and scoped promotion-baseline checks."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import minicode.workflow_runner as workflow_runner_module
from minicode.tooling import ToolContext, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.workflow_runner import run_workflow
from minicode.workflow_scope_registry import (
    WorkflowScopeRegistry,
    WorkflowWriteScopeConflict,
)
from minicode.workflow_workspace import (
    WorkflowIsolationError as _WorkflowIsolationError,
)
from minicode.workflow_workspace import (
    WorkflowWorkspaceTransaction as _WorkflowWorkspaceTransaction,
)


def _structured_request(*write_paths: str) -> dict:
    return {
        "requestVersion": 1,
        "profile": {
            "taskId": "task_scope_registry",
            "kind": "change",
            "risk": "low",
            "complex": False,
            "parentHasSufficientContext": True,
        },
        "scope": {
            "readRoots": ["."],
            "writePaths": list(write_paths),
            "forbiddenPaths": [".env", ".git"],
        },
        "criteria": [
            {"id": "ac1", "kind": "behavior", "text": "The target is updated."}
        ],
        "verificationChecks": [
            {
                "checkId": "check_tests",
                "kind": "tests",
                "argv": ["pytest", "-q"],
                "criterionIds": ["ac1"],
            }
        ],
    }


def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> WorkflowScopeRegistry:
    registry = WorkflowScopeRegistry()
    monkeypatch.setattr(
        workflow_runner_module,
        "process_workflow_scope_registry",
        lambda: registry,
    )
    return registry


# --- Registry unit semantics -------------------------------------------------


def test_overlapping_write_scope_is_rejected_with_conflict_details() -> None:
    registry = WorkflowScopeRegistry()
    registry.register("wf_active", write_paths=("src/app.py",))

    with pytest.raises(WorkflowWriteScopeConflict) as excinfo:
        registry.register("wf_new", write_paths=("src/app.py",))

    conflict = excinfo.value
    assert conflict.workflow_id == "wf_new"
    assert conflict.conflicting_workflow_id == "wf_active"
    assert conflict.overlapping_paths == (("src/app.py", "src/app.py"),)


def test_prefix_containment_counts_as_overlap() -> None:
    registry = WorkflowScopeRegistry()
    registry.register("wf_active", write_paths=("src",))

    with pytest.raises(WorkflowWriteScopeConflict) as excinfo:
        registry.register("wf_new", write_paths=("src/a.py",))
    assert excinfo.value.conflicting_workflow_id == "wf_active"

    with pytest.raises(WorkflowWriteScopeConflict):
        registry.register("wf_reverse", write_paths=(".",))


def test_disjoint_and_empty_write_scopes_are_admitted() -> None:
    registry = WorkflowScopeRegistry()
    registry.register("wf_a", write_paths=("src/a.py",))
    registry.register("wf_b", write_paths=("src/b.py", "docs"))
    # The legacy fallback declares an empty write set and never conflicts.
    registry.register("wf_legacy", write_paths=())

    snapshot = registry.snapshot()
    assert [item.workflow_id for item in snapshot] == [
        "wf_a",
        "wf_b",
        "wf_legacy",
    ]


def test_unregister_releases_paths_for_later_workflows() -> None:
    registry = WorkflowScopeRegistry()
    registry.register("wf_active", write_paths=("src",))

    assert registry.unregister("wf_active") is True
    # Cleanup paths are idempotent so a crash-path retry cannot leak or fail.
    assert registry.unregister("wf_active") is False
    assert registry.snapshot() == ()

    registry.register("wf_next", write_paths=("src/a.py",))
    assert [item.workflow_id for item in registry.snapshot()] == ["wf_next"]


def test_duplicate_workflow_id_fails_closed() -> None:
    registry = WorkflowScopeRegistry()
    registry.register("wf_active", write_paths=("src",))

    with pytest.raises(ValueError, match="already registered"):
        registry.register("wf_active", write_paths=("docs",))


def test_concurrent_registration_is_thread_safe() -> None:
    registry = WorkflowScopeRegistry()
    errors: list[BaseException] = []

    def claim(index: int) -> None:
        try:
            registry.register(f"wf_{index}", write_paths=(f"scope_{index}",))
            registry.unregister(f"wf_{index}")
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert registry.snapshot() == ()


# --- run_workflow admission --------------------------------------------------


def test_overlapping_workflow_is_rejected_before_any_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _fresh_registry(monkeypatch)
    resolved_target = (tmp_path.resolve() / "allowed.txt").as_posix()
    registry.register("wf_active", write_paths=(resolved_target,))
    phase_calls: list[str] = []

    def fake_phases(*_args, **_kwargs) -> ToolResult:
        phase_calls.append("ran")
        return ToolResult(ok=True, output="unexpected")

    monkeypatch.setattr(workflow_runner_module, "_run_workflow_phases", fake_phases)

    result = run_workflow(
        {
            "description": "conflicting workflow",
            "prompt": "Update allowed.txt",
            "workflow_request": _structured_request("allowed.txt"),
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
        depth=0,
        subagent_id="sub_" + "1" * 32,
        run_subagent=lambda _input, _context: ToolResult(ok=True, output=""),
    )

    assert result.ok is False
    assert "error[workflow_write_conflict]" in result.output
    assert "wf_active" in result.output
    assert resolved_target in result.output
    # Admission refused before the snapshot, so no stage ever ran and the
    # conflicting claim is untouched.
    assert phase_calls == []
    assert [item.workflow_id for item in registry.snapshot()] == ["wf_active"]


def test_disjoint_concurrent_workflows_are_both_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _fresh_registry(monkeypatch)
    barrier = threading.Barrier(2, timeout=30)
    finished: list[str] = []

    def fake_phases(input_data, _context, _depth, **_kwargs) -> ToolResult:
        write_path = input_data["workflow_request"]["scope"]["writePaths"][0]
        barrier.wait()
        finished.append(write_path)
        return ToolResult(ok=True, output=f"done:{write_path}")

    monkeypatch.setattr(workflow_runner_module, "_run_workflow_phases", fake_phases)

    def run_one(index: int, write_path: str, results: dict[int, ToolResult]) -> None:
        results[index] = run_workflow(
            {
                "description": f"workflow {index}",
                "prompt": f"Update {write_path}",
                "workflow_request": _structured_request(write_path),
            },
            ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
            depth=0,
            subagent_id=f"sub_{index + 1:032d}",
            run_subagent=lambda _input, _context: ToolResult(ok=True, output=""),
        )

    results: dict[int, ToolResult] = {}
    threads = [
        threading.Thread(target=run_one, args=(0, "a/one.txt", results)),
        threading.Thread(target=run_one, args=(1, "b/two.txt", results)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results[0].ok is True
    assert results[1].ok is True
    assert sorted(finished) == ["a/one.txt", "b/two.txt"]
    assert registry.snapshot() == ()


def test_terminal_paths_release_the_scope_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _fresh_registry(monkeypatch)

    def failing_phases(*_args, **_kwargs) -> ToolResult:
        raise RuntimeError("phase boom")

    monkeypatch.setattr(
        workflow_runner_module,
        "_run_workflow_phases",
        failing_phases,
    )
    with pytest.raises(RuntimeError, match="phase boom"):
        run_workflow(
            {
                "description": "failing workflow",
                "prompt": "Update allowed.txt",
                "workflow_request": _structured_request("allowed.txt"),
            },
            ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
            depth=0,
            subagent_id="sub_" + "2" * 32,
            run_subagent=lambda _input, _context: ToolResult(ok=True, output=""),
        )
    assert registry.snapshot() == ()

    def cancelled_phases(*_args, **_kwargs) -> ToolResult:
        raise TurnCancellationRequested("turn_test")

    monkeypatch.setattr(
        workflow_runner_module,
        "_run_workflow_phases",
        cancelled_phases,
    )
    with pytest.raises(TurnCancellationRequested):
        run_workflow(
            {
                "description": "cancelled workflow",
                "prompt": "Update allowed.txt",
                "workflow_request": _structured_request("allowed.txt"),
            },
            ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
            depth=0,
            subagent_id="sub_" + "3" * 32,
            run_subagent=lambda _input, _context: ToolResult(ok=True, output=""),
        )
    assert registry.snapshot() == ()

    # After every terminal path the same write paths can be claimed again.
    monkeypatch.setattr(
        workflow_runner_module,
        "_run_workflow_phases",
        lambda *_args, **_kwargs: ToolResult(ok=True, output="done"),
    )
    result = run_workflow(
        {
            "description": "reused workflow",
            "prompt": "Update allowed.txt",
            "workflow_request": _structured_request("allowed.txt"),
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
        depth=0,
        subagent_id="sub_" + "4" * 32,
        run_subagent=lambda _input, _context: ToolResult(ok=True, output=""),
    )
    assert result.ok is True
    assert registry.snapshot() == ()


# --- Write bounds and complete promotion baseline ----------------------------


def test_write_scope_does_not_exclude_other_files_from_dependency_validation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    unrelated = tmp_path / "other.txt"
    target.write_text("before", encoding="utf-8")
    unrelated.write_text("baseline", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(
        str(tmp_path),
        baseline_scope_paths=("allowed.txt",),
    ) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))
        unrelated.write_text("concurrent update", encoding="utf-8")
        with pytest.raises(_WorkflowIsolationError, match="parent workspace changed"):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

    assert target.read_text(encoding="utf-8") == "before"
    assert unrelated.read_text(encoding="utf-8") == "concurrent update"


def test_disjoint_scoped_transactions_require_a_fresh_snapshot_after_promotion(
    tmp_path: Path,
) -> None:
    """Write-set independence does not establish independent read dependencies."""
    first = tmp_path / "a" / "one.txt"
    second = tmp_path / "b" / "two.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("a-before", encoding="utf-8")
    second.write_text("b-before", encoding="utf-8")

    transaction_a = _WorkflowWorkspaceTransaction(
        str(tmp_path),
        baseline_scope_paths=("a",),
    )
    transaction_b = _WorkflowWorkspaceTransaction(
        str(tmp_path),
        baseline_scope_paths=("b",),
    )
    try:
        Path(transaction_a.cwd, "a", "one.txt").write_text(
            "a-after",
            encoding="utf-8",
        )
        Path(transaction_b.cwd, "b", "two.txt").write_text(
            "b-after",
            encoding="utf-8",
        )
        frozen_a = transaction_a.freeze(allowed_paths=("a",))
        frozen_b = transaction_b.freeze(allowed_paths=("b",))
        # b promotes first, invalidating a's unproven read dependencies.
        frozen_b.promote(approved_patch_digest=frozen_b.patch_digest)
        with pytest.raises(_WorkflowIsolationError, match="parent workspace changed"):
            frozen_a.promote(approved_patch_digest=frozen_a.patch_digest)
    finally:
        transaction_a.close()
        transaction_b.close()

    assert first.read_text(encoding="utf-8") == "a-before"
    assert second.read_text(encoding="utf-8") == "b-after"


def test_scoped_baseline_still_rejects_in_scope_parent_drift(tmp_path: Path) -> None:
    target = tmp_path / "a" / "one.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(
        str(tmp_path),
        baseline_scope_paths=("a",),
    ) as transaction:
        Path(transaction.cwd, "a", "one.txt").write_text("after", encoding="utf-8")
        frozen = transaction.freeze(allowed_paths=("a",))
        target.write_text("concurrent in-scope update", encoding="utf-8")

        with pytest.raises(_WorkflowIsolationError, match="parent workspace changed"):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

    assert target.read_text(encoding="utf-8") == "concurrent in-scope update"


def test_scoped_baseline_rejects_paths_changed_beyond_the_scope(
    tmp_path: Path,
) -> None:
    with _WorkflowWorkspaceTransaction(
        str(tmp_path),
        baseline_scope_paths=("a",),
    ) as transaction:
        Path(transaction.cwd, "escaped.txt").write_text("escaped", encoding="utf-8")

        with pytest.raises(
            _WorkflowIsolationError,
            match="exceed the promotion baseline scope",
        ):
            transaction.freeze(allowed_paths=("escaped.txt",))

    assert not (tmp_path / "escaped.txt").exists()


def test_baseline_scope_requires_a_non_empty_tuple(tmp_path: Path) -> None:
    with pytest.raises(_WorkflowIsolationError, match="baseline scope"):
        _WorkflowWorkspaceTransaction(str(tmp_path), baseline_scope_paths=())
