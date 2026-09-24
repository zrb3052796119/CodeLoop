"""Repairs driven by the 6-family warm/cold benchmark (glm-5.3-flash).

A. Recovery lessons must serialize execution preconditions (cwd): a lesson
   that says ``python -m pytest ledgerlib/tests`` without the ``src`` working
   directory transfers a command whose hidden precondition no longer holds.
B. Command failures get their own circuit: an unrelated success in the same
   step must not reset a failing command streak, which previously let retry
   loops span dozens of model calls.
C. A trace with no groundable evidence skips the shadow LLM comparison call.
"""

from __future__ import annotations

from typing import Any

import pytest

from minicode.recovery_control import RecoveryGuard
from minicode.reflection_evidence import TraceEvidenceExtractor


def _attempt(
    index: int,
    call_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    ok: bool,
    output: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "event_id": f"e{index}",
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool_name,
            "input": tool_input,
        },
        {
            "event_id": f"e{index + 1}",
            "type": "tool_result",
            "call_id": call_id,
            "tool_name": tool_name,
            "status": "success" if ok else "error",
            "is_error": not ok,
            "output_summary": output,
        },
    ]
    if not ok:
        events.append(
            {
                "event_id": f"e{index + 2}",
                "type": "error",
                "call_id": call_id,
                "tool_name": tool_name,
                "error_type": "ToolError",
                "message": output,
            }
        )
    return events


# ---------------------------------------------------------------- fix A

def test_recovery_invocation_serializes_cwd_precondition() -> None:
    trace = [
        *_attempt(
            1,
            "cmd-failed",
            "run_command",
            {"command": "python3", "args": ["-m", "pytest", "ledgerlib/tests"], "cwd": "/ws"},
            ok=False,
            output="ERROR: No module named ledgerlib",
        ),
        *_attempt(
            10,
            "cmd-passed",
            "run_command",
            {
                "command": "python3",
                "args": ["-m", "pytest", "ledgerlib/tests", "-q"],
                "cwd": "/ws/src",
            },
            ok=True,
            output="3 passed",
        ),
        {
            "event_id": "e30",
            "type": "task_result",
            "status": "success",
            "had_errors": True,
            "errors_recovered": True,
            "tool_error_count": 1,
        },
    ]

    evidence = TraceEvidenceExtractor().extract("Run the test suite", trace)

    assert len(evidence.recoveries) == 1
    action = evidence.recoveries[0].action
    assert "cwd: /ws/src" in action
    # The failed attempt's context is kept for contrast.
    assert "cwd: /ws" in action


def test_recovery_invocation_without_cwd_stays_clean() -> None:
    trace = [
        *_attempt(
            1,
            "lint-failed",
            "run_command",
            {"command": "ruff", "args": ["check", "src"]},
            ok=False,
            output="error: unexpected argument",
        ),
        *_attempt(
            10,
            "lint-passed",
            "run_command",
            {"command": "ruff", "args": ["check", "--fix", "src"]},
            ok=True,
            output="All checks passed",
        ),
        {
            "event_id": "e30",
            "type": "task_result",
            "status": "success",
            "had_errors": True,
            "errors_recovered": True,
            "tool_error_count": 1,
        },
    ]

    evidence = TraceEvidenceExtractor().extract("Lint the repo", trace)

    assert len(evidence.recoveries) == 1
    assert "cwd" not in evidence.recoveries[0].action


# ---------------------------------------------------------------- fix B

def _cmd(ok: bool, output: str = "") -> dict[str, Any]:
    return {"toolName": "run_command", "input": {"command": "python3"}}


def _read(ok: bool = True) -> dict[str, Any]:
    return {"toolName": "read_file", "input": {"path": "README.md"}}


def test_command_failure_streak_survives_unrelated_successes() -> None:
    guard = RecoveryGuard()
    # 复现基准病理: 失败命令与成功的读文件交替,旧行为下步级成功不断清零计数。
    guard.observe(_cmd(ok=False), ok=False, output="No module named x")
    guard.observe(_read(ok=True), ok=True, output="README")
    guard.observe(_cmd(ok=False), ok=False, output="path does not exist")
    guard.observe(_read(ok=True), ok=True, output="README")
    guard.observe(_cmd(ok=False), ok=False, output="permission denied")

    nudge = guard.complete_step([False, True])  # 命令失败 + 读成功

    assert nudge is not None
    assert nudge.startswith("error[command_strategy_switch_required]")
    assert "working directory" in nudge


def test_command_failure_nudge_is_issued_once() -> None:
    guard = RecoveryGuard()
    for _ in range(3):
        guard.observe(_cmd(ok=False), ok=False, output="boom")
    assert guard.complete_step([False]) is not None
    guard.observe(_cmd(ok=False), ok=False, output="boom")

    assert guard.complete_step([False]) is None  # 指引只发一次


def test_fifth_command_failure_opens_the_circuit() -> None:
    guard = RecoveryGuard()
    for _ in range(4):
        guard.observe(_cmd(ok=False), ok=False, output="boom")
        guard.complete_step([False])
    assert guard.stop_decision() is None

    guard.observe(_cmd(ok=False), ok=False, output="boom")
    guard.complete_step([False])

    stop = guard.stop_decision()
    assert stop is not None
    assert stop.reason_code == "consecutive_command_failures"


def test_successful_command_resets_the_streak() -> None:
    guard = RecoveryGuard()
    # 混合步形态(基准病理的真实形状): 命令失败但读文件成功,
    # 步级电路视为进度,命令电路独立累计。
    for index in range(3):
        guard.observe(_cmd(ok=False), ok=False, output="boom")
        guard.observe(_read(ok=True), ok=True, output="README")
        nudge = guard.complete_step([False, True])
        if index < 2:
            assert nudge is None
        else:
            assert nudge is not None
            assert nudge.startswith("error[command_strategy_switch_required]")

    # 成功命令复位计数与指引标志。
    guard.observe(_cmd(ok=True), ok=True, output="3 passed")
    guard.observe(_read(ok=True), ok=True, output="README")
    assert guard.complete_step([True]) is None

    for _ in range(2):
        guard.observe(_cmd(ok=False), ok=False, output="boom")
        guard.observe(_read(ok=True), ok=True, output="README")
        assert guard.complete_step([False, True]) is None

    guard.observe(_cmd(ok=False), ok=False, output="boom")
    guard.observe(_read(ok=True), ok=True, output="README")
    nudge = guard.complete_step([False, True])

    # 新的 3 连败再次触发命令指引。
    assert nudge is not None
    assert nudge.startswith("error[command_strategy_switch_required]")


def test_step_level_strategy_switch_still_fires() -> None:
    guard = RecoveryGuard()
    for _ in range(3):
        guard.complete_step([False, False])

    # 第四次调用时已发过;第三次调用应返回原步级指引
    guard2 = RecoveryGuard()
    nudge = None
    for _ in range(3):
        nudge = guard2.complete_step([False, False])
    assert nudge is not None
    assert nudge.startswith("error[strategy_switch_required]")


# ---------------------------------------------------------------- fix C

def test_shadow_skipped_when_evidence_has_nothing_groundable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.memory as memory_module
    from minicode.memory import MemoryManager, MemoryScope
    from minicode.memory_pipeline import MemoryPipeline

    monkeypatch.setattr(
        memory_module, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)

    calls: list[int] = []

    class CountingShadowEngine:
        mode = "llm_shadow"

        def reflect(self, *_args: Any, **_kwargs: Any):
            calls.append("reflect")
            return _rule_result_with_empty_evidence()

        def complete_shadow(self, *_args: Any, **_kwargs: Any):
            calls.append("shadow")
            return None

    def _rule_result_with_empty_evidence():
        from minicode.reflection_synthesis import TaskEvidence

        class _Result:
            task_evidence = TaskEvidence()
            claim_validation = type("V", (), {"valid_claims": []})()

        return _Result()

    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        enable_reranker=False,
        reflection_engine=CountingShadowEngine(),
    )

    # 零证据 trace: 只读了两个文件,一切成功。
    trace = [
        *_attempt(1, "r1", "read_file", {"path": "README.md"}, ok=True, output="ok"),
        *_attempt(4, "r2", "read_file", {"path": "docs/a.md"}, ok=True, output="ok"),
        {"event_id": "e9", "type": "task_result", "status": "success"},
    ]
    pipeline.write("Read the docs", trace)

    assert "reflect" in calls
    assert "shadow" not in calls


def test_shadow_runs_when_evidence_exists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.memory as memory_module
    from minicode.memory import MemoryManager
    from minicode.memory_pipeline import MemoryPipeline

    monkeypatch.setattr(
        memory_module, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)

    calls: list[str] = []

    class CountingShadowEngine:
        mode = "llm_shadow"

        def reflect(self, *_args: Any, **_kwargs: Any):
            calls.append("reflect")
            from minicode.reflection_synthesis import TaskEvidence

            class _Result:
                task_evidence = TaskEvidence(
                    errors=[object()],  # 任意一类证据存在
                )
                claim_validation = type("V", (), {"valid_claims": []})()

            return _Result()

        def complete_shadow(self, *_args: Any, **_kwargs: Any):
            calls.append("shadow")
            return None

    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        enable_reranker=False,
        reflection_engine=CountingShadowEngine(),
    )
    trace = [
        *_attempt(
            1, "c1", "run_command", {"command": "make"},
            ok=False, output="boom",
        ),
        {"event_id": "e4", "type": "task_result", "status": "failed"},
    ]
    pipeline.write("Build the repo", trace)

    assert "shadow" in calls
