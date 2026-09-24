"""Language-bridge admission: a Chinese query must be able to reach an
English lesson (and vice versa) through the deterministic relevance gate.

The seed case reproduces the live packaging benchmark incident: a valid,
approved English lesson was rejected with ``relevance_gate_rejected_all``
because the Chinese task query and the lesson shared zero lexical tokens.
The curated CJK<->English bridge translates query intent across scripts.
Ranking stays honest: a lesson whose only tie to the task is generic
command vocabulary clears the gate but keeps a moderate relevance score.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    _informative_terms,
    _language_bridge_terms,
)

# 逐字取自真实试点 run 的产物(packaging-learn)。
PACKAGING_LESSON = (
    "Observed error pattern for run_command / PermissionError: "
    "error[permission_denied]: Tool run_command request was denied by "
    "workspace policy (command_denied). Use a materially different direct "
    "command request with explicit args and an in-workspace cwd; do not "
    "add shell wrappers, pipes, redirections, sudo, or other escalation."
)
# 真实试点中"本应学到"的那类教训:与本任务真正相关的英文恢复知识。
TEST_RUN_LESSON = (
    "Verified recovery: the test suite only runs with cwd=src using "
    "`python -m pytest ../tests`; running pytest from the repo root fails "
    "with ImportError because the installed packaging shadows the local "
    "src package. Do not reuse the failed root invocation."
)
PACKAGING_QUERY = (
    "请运行这个仓库的完整测试套件,然后报告:(1) 你使用的确切命令和工作目录;"
    "(2) 通过/失败/跳过的测试数量。不要修改任何文件,不要安装任何依赖。"
)
UNRELATED_QUERY = "介绍一下团队的季度规划和文化活动安排"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", home)
    monkeypatch.setattr(
        memory_module.MemoryPaths,
        "for_workspace",
        classmethod(
            lambda cls, workspace: cls(
                user_memory=home / "memory",
                project_memory=Path(workspace) / ".mini-code-memory",
                local_memory=Path(workspace) / ".mini-code-memory-local",
            )
        ),
    )
    return home


def _pipeline_with_lesson(
    tmp_path: Path, content: str
) -> tuple[MemoryPipeline, MemoryManager, str]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "task_context",
        content,
        tags=["self-reflection", "failure", "run_command"],
        source="test",
    )
    assert entry is not None and entry.approval_status == "approved"
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    return pipeline, manager, entry.id


def test_bridge_translates_query_intent_across_scripts() -> None:
    informative = _informative_terms(PACKAGING_QUERY, ())
    bridged = _language_bridge_terms(informative)

    assert "命令" in informative  # 原始意图存在
    lowered = set(bridged) | set(informative)
    # 中文任务词汇桥接到英文技术 token。注:"test" 是通用词,桥接输出
    # 与原始词同样受通用词过滤——这是有意的保守行为。
    assert {"command", "run", "install"} <= lowered


def test_bridge_keeps_untranslated_queries_untouched() -> None:
    informative = _informative_terms(UNRELATED_QUERY, ())
    bridged = _language_bridge_terms(informative)

    assert bridged == ()


def test_relevant_english_lesson_reaches_injection_from_chinese_query(
    isolated_home: Path, tmp_path: Path
) -> None:
    pipeline, manager, entry_id = _pipeline_with_lesson(
        tmp_path, TEST_RUN_LESSON
    )
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject(PACKAGING_QUERY, [], messages)

    assert entry_id in pipeline._last_injected_ids
    assert "pytest" in injected[0]["content"]


def test_packaging_incident_lesson_clears_gate_with_honest_ranking(
    isolated_home: Path, tmp_path: Path
) -> None:
    """事故原案例:桥接让它越过相关性门;分数诚实地停留在中等。

    修复前:relevance_gate_rejected_all(候选被门消灭,分数无从谈起)。
    修复后:门放行(multiple_informative_terms),相关性 ~0.29——
    这条元教训与"跑测试"任务本就只有中等相关,排序层不应放行注入
    (pipeline 阈值 0.3),修复的是召回与门,不是硬塞弱相关内容。
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "task_context",
        PACKAGING_LESSON,
        tags=["self-reflection", "failure", "run_command"],
        source="test",
    )
    assert entry is not None

    retriever = CanonicalMemoryRetriever(manager)
    result = retriever.retrieve(
        MemoryRetrievalRequest(
            query=PACKAGING_QUERY,
            current_files=[],
            active_domains=[],
            min_relevance=0.2,
        )
    )

    assert not result.no_match
    assert len(result.selected or []) == 1
    selected = result.selected[0]
    assert {"command", "run", "cwd"} <= set(selected.score.matched_terms)
    assert "multiple_informative_terms" in selected.reason_codes


def test_unrelated_query_still_fails_closed(
    isolated_home: Path, tmp_path: Path
) -> None:
    pipeline, manager, entry_id = _pipeline_with_lesson(
        tmp_path, TEST_RUN_LESSON
    )
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject(UNRELATED_QUERY, [], messages)

    assert pipeline._last_injected_ids == []
    assert "pytest" not in injected[0]["content"]


def test_reverse_bridge_english_query_reaches_chinese_lesson(
    isolated_home: Path, tmp_path: Path
) -> None:
    chinese_lesson = (
        "已验证的恢复:运行测试必须使用 python -m pytest tests,并从仓库根目录执行;"
        "不要使用 bash 包装。失败的调用形态是 PYTHONPATH 前缀写法,已被权限层拒绝。"
    )
    pipeline, manager, entry_id = _pipeline_with_lesson(
        tmp_path, chinese_lesson
    )
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject(
        "Run the test suite and report the command used", [], messages
    )

    assert entry_id in pipeline._last_injected_ids
