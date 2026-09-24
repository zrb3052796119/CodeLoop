"""USER 作用域个人画像:常驻渲染块 + 证据制观察通道。

设计契约(与讨论定稿一致):
- 已批准的 USER user_profile 条目以有界"背景参考"块常驻注入,
  不走查询检索(python project_facts 的镜像);
- 个人陈述经确定性 facet 分类进入 USER 观察通道:首条观察创建
  pending 候选,重复仅累计计数,永不自动批准(跨项目生效必须人审);
- 已拒绝的 facet 不再被重新观察(负记忆);
- `/memory add user: role: ...` 语法直达画像类别。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import (
    MemoryManager,
    MemoryScope,
    USER_PROFILE_CATEGORY,
)
from minicode.memory_pipeline import MemoryPipeline, _classify_user_facet


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


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _pipeline(manager: MemoryManager, workspace: Path) -> MemoryPipeline:
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    return pipeline


# ---------------------------------------------------------------- 渲染块

def test_approved_user_profile_renders_ambient_block(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.add_entry(
        MemoryScope.USER, USER_PROFILE_CATEGORY, "role: 资深后端开发",
        tags=["user-facet"], source="test",
    )
    pipeline = _pipeline(manager, workspace)
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject("随便一个不相干的任务", [], messages)

    assert "User Profile" in injected[0]["content"]
    assert "role: 资深后端开发" in injected[0]["content"]
    assert "background reference" in injected[0]["content"]


def test_pending_facet_not_rendered_until_approved(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.record_user_facet_observation("role", "资深后端开发")
    pipeline = _pipeline(manager, workspace)
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject("任意任务", [], messages)

    assert "User Profile" not in injected[0]["content"]

    entry_id = manager.memories[MemoryScope.USER].entries[0].id
    manager.decide_pending_entry(entry_id, "approve", actor="user", reason="t")
    injected2 = pipeline.inject("任意任务", [], messages)
    assert "role: 资深后端开发" in injected2[0]["content"]


def test_profile_block_bounded(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    for index in range(20):
        manager.add_entry(
            MemoryScope.USER, USER_PROFILE_CATEGORY,
            f"concern: 关注点编号{index}以及一些填充文字用于测试长度边界行为",
            tags=["user-facet"], source="test",
        )
    pipeline = _pipeline(manager, workspace)
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject("任意任务", [], messages)
    lines = [
        line for line in injected[0]["content"].splitlines()
        if line.startswith("- ")
    ]

    assert len(lines) <= pipeline._USER_PROFILE_MAX_ENTRIES


def test_project_scope_profile_entries_do_not_render(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.add_entry(
        MemoryScope.PROJECT, USER_PROFILE_CATEGORY, "role: 项目内不应渲染",
        source="test",
    )
    pipeline = _pipeline(manager, workspace)
    messages = [{"role": "system", "content": "SYSTEM"}]

    injected = pipeline.inject("任意任务", [], messages)

    assert "项目内不应渲染" not in injected[0]["content"]


# ---------------------------------------------------------------- 观察通道

def test_observation_counts_and_stays_pending(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)

    status1, entry_id, count1 = manager.record_user_facet_observation(
        "skill", "擅长 Python 和 Go"
    )
    assert status1 == "created" and count1 == 1

    status2, _entry2, count2 = manager.record_user_facet_observation(
        "skill", "擅长 Python 和 Go"
    )
    status3, _entry3, count3 = manager.record_user_facet_observation(
        "skill", "擅长 Python 和 Go"
    )

    assert (status2, count2) == ("observed", 2)
    assert (status3, count3) == ("observed", 3)
    entries = manager.memories[MemoryScope.USER].entries
    assert len(entries) == 1  # 去重: 同 facet 同值合并计数
    entry = entries[0]
    # 达到阈值仍然 pending —— 跨项目生效必须人审一次。
    assert entry.approval_status == "pending"
    assert "awaiting" in entry.approval_reason
    assert entry.approval_content_hash == memory_module._approval_hash_for_entry(
        entry
    )


def test_rejected_facet_is_negative_memory(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.record_user_facet_observation("domain", "负责电商交易系统")
    entry = manager.memories[MemoryScope.USER].entries[0]
    manager.decide_pending_entry(entry.id, "reject", actor="user", reason="错")

    status, _entry, count = manager.record_user_facet_observation(
        "domain", "负责电商交易系统"
    )

    assert status == "suppressed" and count == 0
    assert len(manager.memories[MemoryScope.USER].entries) == 1


def test_approved_facet_observations_are_noops(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.add_entry(
        MemoryScope.USER, USER_PROFILE_CATEGORY, "role: 资深后端开发",
        tags=["user-facet"], source="test",
    )

    status, _entry, count = manager.record_user_facet_observation(
        "role", "资深后端开发"
    )

    assert status == "known"
    assert len(manager.memories[MemoryScope.USER].entries) == 1


# ---------------------------------------------------------------- 分类器

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我是资深后端开发,主要写服务端", "role"),
        ("我擅长 Python 和 Go", "skill"),
        ("我负责电商交易系统的交易模块", "domain"),
        ("我对 QPS 很敏感,经常排查性能问题", "concern"),
        # 复合句取句首主语:主语是行业 → domain(等保陈述单独成句时才是 compliance)
        ("我们行业是金融科技,需遵守等保2.0规范", "domain"),
        ("需遵守等保2.0规范", "compliance"),
        ("我喜欢用 pytest 而不是 unittest", "preference"),
        ("帮我修复这个 bug", None),
        ("运行测试并报告结果", None),
    ],
)
def test_facet_classifier(text: str, expected: str | None) -> None:
    assert _classify_user_facet(text) == expected


# ---------------------------------------------------------------- 手工通道

def test_manual_facet_syntax_routes_to_profile_category(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)

    message = manager.handle_user_memory_input(
        "/memory add user: role: 资深后端开发"
    )

    assert message is not None and "Saved" in message
    entry = manager.memories[MemoryScope.USER].entries[0]
    assert entry.category == USER_PROFILE_CATEGORY
    assert entry.scope == MemoryScope.USER
    assert entry.approval_status == "approved"

    pipeline = _pipeline(manager, workspace)
    messages = [{"role": "system", "content": "SYSTEM"}]
    injected = pipeline.inject("任意任务", [], messages)
    assert "role: 资深后端开发" in injected[0]["content"]


def test_plain_user_memory_keeps_note_category(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)

    manager.handle_user_memory_input("/memory add user: 小红是我的好朋友")

    entry = manager.memories[MemoryScope.USER].entries[0]
    assert entry.category == "note"
    assert entry.category != USER_PROFILE_CATEGORY


# ---------------------------------------------------------------- 端到端

def test_end_to_end_chat_statement_to_ambient_injection(
    isolated_home: Path, workspace: Path
) -> None:
    """聊天陈述(含技术词) → USER 观察候选 → 一次人审 → 常驻注入。"""

    manager = MemoryManager(project_root=workspace)
    pipeline = _pipeline(manager, workspace)

    pipeline.write(
        "我是资深后端开发,擅长Python和Go",
        [{"event_id": "e1", "type": "task_result", "status": "success"}],
    )

    user_entries = manager.memories[MemoryScope.USER].entries
    assert len(user_entries) == 1
    candidate = user_entries[0]
    assert candidate.category == USER_PROFILE_CATEGORY
    assert candidate.approval_status == "pending"
    assert candidate.scope == MemoryScope.USER

    # 未审批前不渲染。
    pre = pipeline.inject(
        "帮我修复一个bug", [], [{"role": "system", "content": "SYSTEM"}]
    )
    assert "User Profile" not in pre[0]["content"]

    manager.decide_pending_entry(
        candidate.id, "approve", actor="user", reason="profile confirmed"
    )

    post = pipeline.inject(
        "帮我修复一个bug", [], [{"role": "system", "content": "SYSTEM"}]
    )
    assert "User Profile" in post[0]["content"]
    assert "background reference" in post[0]["content"]
    assert "role: 我是资深后端开发" in post[0]["content"]


def test_task_turns_do_not_create_facet_observations(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    pipeline = _pipeline(manager, workspace)

    pipeline.write(
        "帮我修复登录页面的bug",
        [
            {
                "event_id": "e1", "type": "tool_call", "call_id": "c1",
                "tool_name": "read_file", "input": {"path": "a.py"},
            },
            {
                "event_id": "e2", "type": "tool_result", "call_id": "c1",
                "tool_name": "read_file", "status": "success",
                "output_summary": "ok",
            },
            {"event_id": "e3", "type": "task_result", "status": "success"},
        ],
    )

    assert manager.memories[MemoryScope.USER].entries == []
