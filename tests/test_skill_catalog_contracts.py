from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from minicode.capability_registry import CapabilityRegistry
from minicode.skill_authoring import propose_skill
from minicode.skill_feedback import SkillRoutingFeedback
from minicode.skill_versions import SkillVersionLedger
from minicode.skills import (
    _routing_identity_digest,
    discover_skills,
    install_skill,
    load_skill_from_catalog,
    remove_managed_skill,
)

_CONTENT_DIGEST = "a" * 64
_ROUTING_DIGEST_A = "b" * 64
_ROUTING_DIGEST_B = "c" * 64


def _write_directory_marker(
    path: Path,
    *,
    name: str = "analysis",
    description: str = "Analyze code structure.",
    keywords: str = "review, architecture",
) -> None:
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "domains: [code, analysis]\n"
        "scopes: [readonly]\n"
        f"keywords: [{keywords}]\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def _write_skill(path: Path, *, name: str = "explain") -> None:
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Explain a codebase.\n"
        "tools: [read_file]\n"
        "---\n\n"
        "# Explain\n",
        encoding="utf-8",
    )


def test_directory_metadata_changes_routing_identity_not_content_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / ".mini-code" / "skills" / "analysis"
    skill_directory = directory / "explain"
    skill_directory.mkdir(parents=True)
    marker = directory / "SKILL_DIR.md"
    _write_directory_marker(marker)
    _write_skill(skill_directory / "SKILL.md")

    original_catalog = discover_skills(tmp_path)
    original = original_catalog[0]
    assert original.activation_terms == []
    assert original.routing_identity_digest == _routing_identity_digest(
        original,
        directory_summary={
            "name": "analysis",
            "description": "Analyze code structure.",
            "domains": ["code", "analysis"],
            "scopes": ["readonly"],
            "keywords": ["review", "architecture"],
        },
    )
    _write_directory_marker(
        marker,
        description="Analyze architecture and security boundaries.",
        keywords="review, architecture, security",
    )
    changed = discover_skills(tmp_path)[0]

    assert original.content_digest == changed.content_digest
    assert original.routing_identity_digest != changed.routing_identity_digest
    assert len(original.routing_identity_digest) == 64
    # Loading remains bound to the exact SKILL.md body. Directory routing
    # metadata has its own identity and does not masquerade as body drift.
    loaded = load_skill_from_catalog(
        tmp_path,
        "analysis/explain",
        original_catalog,
    )
    assert loaded is not None
    assert loaded.activation_terms == []
    assert loaded.routing_identity_digest == original.routing_identity_digest


def test_skill_operation_contract_round_trips_and_changes_routing_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill_directory = tmp_path / ".mini-code" / "skills" / "reviewer"
    skill_directory.mkdir(parents=True)
    skill_file = skill_directory / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review existing code without changing it.\n"
        "purposes: [audit, inspect]\n"
        "supported_operations: [review.analyze, review.compare]\n"
        "required_operations: [search.read]\n"
        "activation_terms: [teach, learn, 教我]\n"
        "---\n\n"
        "# Reviewer\n",
        encoding="utf-8",
    )

    original = discover_skills(tmp_path)[0]
    projected_catalog = [asdict(original)]
    loaded = load_skill_from_catalog(
        tmp_path,
        "reviewer",
        projected_catalog,
    )

    assert original.purposes == ["audit", "inspect"]
    assert original.supported_operations == [
        "review.analyze",
        "review.compare",
    ]
    assert original.required_operations == ["search.read"]
    assert original.activation_terms == ["teach", "learn", "教我"]
    assert projected_catalog[0]["purposes"] == ["audit", "inspect"]
    assert projected_catalog[0]["supported_operations"] == [
        "review.analyze",
        "review.compare",
    ]
    assert projected_catalog[0]["required_operations"] == ["search.read"]
    assert projected_catalog[0]["activation_terms"] == [
        "teach",
        "learn",
        "教我",
    ]
    assert loaded is not None
    assert loaded.purposes == ["audit", "inspect"]
    assert loaded.supported_operations == [
        "review.analyze",
        "review.compare",
    ]
    assert loaded.required_operations == ["search.read"]
    assert loaded.activation_terms == ["teach", "learn", "教我"]
    bound_identity = _routing_identity_digest(
        projected_catalog[0],
        directory_summary={},
    )
    changed_purposes = {
        **projected_catalog[0],
        "purposes": ["explain"],
    }
    changed_operations = {
        **projected_catalog[0],
        "supported_operations": ["review.analyze"],
    }
    changed_required_operations = {
        **projected_catalog[0],
        "required_operations": ["review.analyze"],
    }
    changed_activation_terms = {
        **projected_catalog[0],
        "activation_terms": ["audit"],
    }
    assert _routing_identity_digest(
        changed_purposes,
        directory_summary={},
    ) != bound_identity
    assert _routing_identity_digest(
        changed_operations,
        directory_summary={},
    ) != bound_identity
    assert _routing_identity_digest(
        changed_required_operations,
        directory_summary={},
    ) != bound_identity
    assert _routing_identity_digest(
        changed_activation_terms,
        directory_summary={},
    ) != bound_identity
    original_routing_identity = original.routing_identity_digest

    skill_file.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review existing code without changing it.\n"
        "purposes: [audit, inspect]\n"
        "supported_operations: [review.analyze]\n"
        "required_operations: [search.read]\n"
        "activation_terms: [teach, learn, 教我]\n"
        "---\n\n"
        "# Reviewer\n",
        encoding="utf-8",
    )

    changed = discover_skills(tmp_path)[0]
    assert changed.routing_identity_digest != original_routing_identity


def test_project_skills_declare_narrow_operation_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = Path(__file__).resolve().parents[1]
    expected = {
        "code-skills/minicode-study": (
            ["codebase explanation", "architecture walkthrough"],
            ["explain.read"],
        ),
        "code-understanding/codebase-explanation": (
            ["codebase explanation", "runtime trace"],
            ["explain.read", "search.read"],
        ),
        "code-understanding/design-review": (
            ["design review", "code audit"],
            ["review.analyze", "review.compare"],
        ),
        "code-understanding/minicode-learning-coach-demo": (
            ["guided codebase explanation", "architecture walkthrough"],
            ["explain.read", "question.create"],
        ),
        "debugging/concurrency-debugging": (
            ["concurrency diagnosis", "race and deadlock analysis"],
            ["debug.analyze"],
        ),
        "debugging/pytest-debugging": (
            ["pytest failure diagnosis", "pytest failure repair"],
            ["debug.analyze", "debug.update", "test.execute"],
        ),
        "dependencies/dependency-hygiene": (
            ["dependency addition", "dependency upgrade or removal"],
            ["configure.update", "review.analyze", "test.execute"],
        ),
        "documentation/readme-authoring": (
            ["README authoring", "README revision"],
            ["document.create", "document.update"],
        ),
            "git/commit-hygiene": (
                ["change review before commit", "record code changes"],
                ["review.analyze", "code.update", "document.create"],
            ),
        "performance/hotpath-profiling": (
            ["performance diagnosis", "measured optimization"],
            [
                "debug.analyze",
                "review.analyze",
                "refactor.update",
                "test.execute",
            ],
        ),
        "quality/lint-and-static-cleanup": (
            ["lint warning repair", "static analysis cleanup"],
            ["code.update", "debug.update"],
        ),
        "refactoring/safe-refactor": (
            ["behavior-preserving refactor"],
            ["refactor.update"],
        ),
        "refactoring/structural-rename": (
            ["structural rename", "module move"],
            ["code.rename", "code.update"],
        ),
        "security/secret-and-injection-review": (
            ["security audit", "secret and injection review"],
            ["review.analyze"],
        ),
        "testing/regression-from-bug-report": (
            ["bug reproduction", "regression test and repair"],
            [
                "debug.analyze",
                "debug.update",
                "test.create",
                "test.execute",
            ],
        ),
        "testing/test-driven-development": (
            ["test-first implementation", "test-first code change"],
            ["test.create", "test.execute", "code.create", "code.update"],
        ),
    }

    actual = {
        skill.qualified_name: (
            skill.purposes,
            skill.supported_operations,
        )
        for skill in discover_skills(workspace)
        if skill.source == "project"
    }

    assert actual == expected
    required_operations = {
        skill.qualified_name: skill.required_operations
        for skill in discover_skills(workspace)
        if skill.source == "project" and skill.required_operations
    }
    assert required_operations == {
        "testing/regression-from-bug-report": [
            "test.create",
            "test.execute",
        ]
    }
    activation_terms = {
        skill.qualified_name: skill.activation_terms
        for skill in discover_skills(workspace)
        if skill.source == "project"
    }
    assert set(activation_terms) == set(expected)
    assert {
        name: terms for name, terms in activation_terms.items() if terms
    } == {
        "code-skills/minicode-study": [
            "minicode",
            "memory.py",
            "agent_loop.py",
            "main.py",
            "cybernetic",
            "记忆系统",
            "控制论",
        ],
        "code-understanding/codebase-explanation": [
            "code",
            "codebase",
            "module",
            "file",
            "function",
            "class",
            "architecture",
                "auth",
                "query",
                "config",
                "configuration",
                "script",
            "docs",
            "agent_loop",
            "tool call",
            "call flow",
            "prompt",
            "route_skill_turn",
            "cli",
                "import",
                "imports",
                "reference",
                "references",
                "caller",
            "callers",
            "代码",
            "代码库",
            "模块",
            "文件",
            "函数",
            "类",
                "架构",
                "配置",
                "调用",
            "调用方",
            "调用链",
                "流程",
                "引用",
                "拼出",
        ],
        "code-understanding/minicode-learning-coach-demo": [
            "teach",
            "teaching",
            "learn",
            "learning",
            "study",
            "coach",
            "quiz",
            "interview",
            "教我",
            "教学",
            "学习",
            "带我学",
            "带我走一遍",
            "提问我",
            "面试",
        ],
        "performance/hotpath-profiling": [
            "slow",
            "performance",
            "profile",
            "profiling",
            "latency",
            "throughput",
            "bottleneck",
            "timing",
            "seconds",
            "milliseconds",
            "慢",
            "性能",
            "剖析",
            "延迟",
            "吞吐",
            "瓶颈",
            "耗时",
            "秒",
            "毫秒",
            "热点",
            "开销",
            "时间",
            "分钟",
            "测速",
        ],
        "git/commit-hygiene": [
            "commit",
            "git",
            "staging",
            "diff",
            "branch",
            "提交",
            "暂存",
            "分支",
        ],
        "testing/regression-from-bug-report": [
            "bug",
            "report",
            "reported",
            "reproduce",
            "repro",
            "regression",
            "failing test",
            "用户反馈",
            "用户报告",
            "回归测试",
            "复现",
        ],
    }


@pytest.mark.skipif(
    os.name == "nt",
    reason="symlink semantics are POSIX-only",
)
@pytest.mark.parametrize("symlink_component", [".mini-code", "skills"])
def test_discovery_rejects_symlinked_managed_catalog_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_component: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / f"external-{symlink_component.lstrip('.')}"

    if symlink_component == ".mini-code":
        skill_directory = external / "skills" / "escaped"
        skill_directory.mkdir(parents=True)
        (workspace / ".mini-code").symlink_to(
            external,
            target_is_directory=True,
        )
    else:
        skill_directory = external / "escaped"
        skill_directory.mkdir(parents=True)
        manager = workspace / ".mini-code"
        manager.mkdir()
        (manager / "skills").symlink_to(
            external,
            target_is_directory=True,
        )
    _write_skill(skill_directory / "SKILL.md", name="escaped")

    assert discover_skills(workspace) == []


@pytest.mark.skipif(
    os.name == "nt",
    reason="symlink semantics are POSIX-only",
)
@pytest.mark.parametrize("symlink_component", [".mini-code", "skills"])
def test_removal_rejects_symlinked_managed_catalog_ancestors(
    tmp_path: Path,
    symlink_component: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / f"external-remove-{symlink_component.lstrip('.')}"
    escaped = external / "escaped"
    escaped.mkdir(parents=True)
    sentinel = escaped / "keep.txt"
    sentinel.write_text("outside", encoding="utf-8")

    if symlink_component == ".mini-code":
        (external / "skills").mkdir()
        escaped.rename(external / "skills" / "escaped")
        sentinel = external / "skills" / "escaped" / "keep.txt"
        (workspace / ".mini-code").symlink_to(
            external,
            target_is_directory=True,
        )
    else:
        manager = workspace / ".mini-code"
        manager.mkdir()
        (manager / "skills").symlink_to(
            external,
            target_is_directory=True,
        )

    with pytest.raises(RuntimeError, match="managed symlinks"):
        remove_managed_skill(workspace, "escaped", scope="project")

    assert sentinel.read_text(encoding="utf-8") == "outside"


def test_nested_install_creates_marker_and_verifies_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "source"
    source.mkdir()
    _write_skill(source / "SKILL.md", name="demo")

    result = install_skill(
        tmp_path,
        str(source),
        name="analysis/demo",
        scope="project",
    )

    marker = tmp_path / ".mini-code" / "skills" / "analysis" / "SKILL_DIR.md"
    assert marker.is_file()
    assert result["name"] == "analysis/demo"
    discovered = discover_skills(tmp_path)
    assert [skill.qualified_name for skill in discovered] == ["analysis/demo"]
    assert Path(discovered[0].path) == Path(result["targetPath"]).resolve()


def test_nested_install_rejects_invalid_existing_marker_without_copying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / ".mini-code" / "skills" / "analysis"
    directory.mkdir(parents=True)
    marker = directory / "SKILL_DIR.md"
    _write_directory_marker(marker, name="different")
    source = tmp_path / "source"
    source.mkdir()
    _write_skill(source / "SKILL.md", name="demo")

    with pytest.raises(RuntimeError, match="Invalid SKILL_DIR.md"):
        install_skill(
            tmp_path,
            str(source),
            name="analysis/demo",
            scope="project",
        )

    assert not (directory / "demo" / "SKILL.md").exists()
    assert "name: different" in marker.read_text(encoding="utf-8")


def test_install_rolls_back_when_requested_identity_is_not_discoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "source"
    source.mkdir()
    _write_skill(source / "SKILL.md", name="different")

    with pytest.raises(RuntimeError, match="requested catalog identity"):
        install_skill(
            tmp_path,
            str(source),
            name="analysis/demo",
            scope="project",
        )

    assert not (
        tmp_path / ".mini-code" / "skills" / "analysis" / "demo" / "SKILL.md"
    ).exists()
    assert discover_skills(tmp_path) == []


def test_new_directory_proposal_requires_marker_and_skill_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proposal = propose_skill(
        tmp_path,
        {
            "name": "invoice-reconciliation",
            "description": "Reconcile invoice and payment records.",
            "domains": ["finance"],
            "keywords": ["invoice", "payment"],
        },
        CapabilityRegistry(),
    )

    assert proposal["recommendation_type"] == "new_directory"
    assert proposal["new_directory_suggestion"]["path"] in proposal["next_step"]
    assert proposal["target_path"] in proposal["next_step"]
    assert "both" in proposal["next_step"]
    assert "not discoverable until both files exist" in proposal["next_step"]


def _version_skill(routing_digest: str) -> dict[str, object]:
    return {
        "name": "explain",
        "qualified_name": "analysis/explain",
        "source": "project",
        "directory": "analysis",
        "content_digest": _CONTENT_DIGEST,
        "routing_identity_digest": routing_digest,
    }


def test_version_lineage_prefers_routing_identity_with_same_body(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = SkillVersionLedger(workspace)

    ledger.observe_catalog([_version_skill(_ROUTING_DIGEST_A)])
    ledger.observe_catalog([_version_skill(_ROUTING_DIGEST_B)])
    snapshot = ledger.snapshot(
        [_version_skill(_ROUTING_DIGEST_B)],
        {
            "ledgerVersion": 1,
            "mode": "shadow",
            "evaluations": [],
            "promotionEligible": False,
        },
    )

    first, second = snapshot["versions"]
    assert first["versionId"] != second["versionId"]
    assert second["parentVersionId"] == first["versionId"]
    assert first["skill"]["contentDigest"] == _CONTENT_DIGEST
    assert second["skill"]["contentDigest"] == _CONTENT_DIGEST
    assert first["skill"]["routingIdentityDigest"] == _ROUTING_DIGEST_A
    assert second["skill"]["routingIdentityDigest"] == _ROUTING_DIGEST_B
    assert first["catalogCurrent"] is False
    assert second["catalogCurrent"] is True


def test_version_ledger_migrates_the_pre_split_routing_identity_id(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    storage = workspace / ".mini-code"
    storage.mkdir(parents=True)
    legacy_skill = {
        "qualifiedName": "analysis/explain",
        "source": "project",
        "directory": "analysis",
        "contentDigest": _CONTENT_DIGEST,
        "routingIdentityDigest": _ROUTING_DIGEST_A,
    }
    encoded = json.dumps(
        legacy_skill,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_id = "skillv_" + hashlib.sha256(encoded).hexdigest()[:32]
    (storage / "skill_versions.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "ledgerVersion": 1,
                "versions": [
                    {
                        "versionId": legacy_id,
                        "skill": legacy_skill,
                        "parentVersionId": None,
                        "status": "observed",
                        "firstObservedAt": "2026-08-30T16:31:12.241Z",
                        "createdFromRuns": [],
                    }
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    ledger = SkillVersionLedger(workspace)

    ledger.observe_catalog([_version_skill(_ROUTING_DIGEST_A)])
    snapshot = ledger.snapshot(
        [_version_skill(_ROUTING_DIGEST_A)],
        {
            "ledgerVersion": 1,
            "mode": "shadow",
            "evaluations": [],
            "promotionEligible": False,
        },
    )

    legacy, canonical = snapshot["versions"]
    assert legacy["versionId"] == legacy_id
    assert canonical["versionId"] != legacy_id
    assert canonical["parentVersionId"] == legacy_id
    assert canonical["catalogCurrent"] is True


def _cohort(*, positive: bool) -> dict[str, object]:
    successes = 20 if positive else 0
    return {
        "runs": 20,
        "goalAchievements": successes,
        "goalAchievementRate": successes / 20,
        "goalAchievementInterval": {
            "lower": 0.8389 if positive else 0.0,
            "upper": 1.0 if positive else 0.1611,
        },
        "verification": {
            "observedRuns": 20,
            "passedRuns": 20 if positive else 0,
            "failedRuns": 0 if positive else 20,
            "coverageComplete": True,
        },
        "userSignal": {
            "observedRuns": 3,
            "acceptedRuns": 3 if positive else 0,
            "correctedRuns": 0 if positive else 2,
            "rejectedRuns": 0 if positive else 1,
            "coverageComplete": False,
        },
    }


def _feedback_snapshot(*, routing_digest: str | None) -> dict[str, object]:
    skill = {
        "qualifiedName": "analysis/explain",
        "source": "project",
        "directory": "analysis",
        "contentDigest": _CONTENT_DIGEST,
    }
    if routing_digest is not None:
        skill["routingIdentityDigest"] = routing_digest
    return {
        "ledgerVersion": 1,
        "mode": "shadow",
        "scannedRuns": 40,
        "runsTruncated": False,
        "journalDiagnostics": 0,
        "evaluationsTruncated": False,
        "evaluations": [
            {
                "skill": skill,
                "profile": {
                    "intentType": "review",
                    "actionType": "analyze",
                },
                "treatment": _cohort(positive=True),
                "control": _cohort(positive=False),
                "goalAchievementDelta": 1.0,
                "sampleGatePassed": True,
                "shadowStatus": "positive_signal",
                "promotionEligible": False,
            }
        ],
        "promotionEligible": False,
    }


def _feedback_decision(
    feedback: SkillRoutingFeedback,
    *,
    routing_digest: str = "",
):
    return feedback.decision(
        qualified_name="analysis/explain",
        source="project",
        directory="analysis",
        content_digest=_CONTENT_DIGEST,
        routing_identity_digest=routing_digest,
        intent_type="review",
        action_type="analyze",
    )


def test_feedback_prefers_routing_identity_and_accepts_legacy_snapshot() -> None:
    current = SkillRoutingFeedback.from_snapshot(
        _feedback_snapshot(routing_digest=_ROUTING_DIGEST_A)
    )
    assert _feedback_decision(
        current,
        routing_digest=_ROUTING_DIGEST_A,
    ) is not None
    assert _feedback_decision(
        current,
        routing_digest=_ROUTING_DIGEST_B,
    ) is None

    legacy = SkillRoutingFeedback.from_snapshot(
        _feedback_snapshot(routing_digest=None)
    )
    assert _feedback_decision(
        legacy,
        routing_digest=_ROUTING_DIGEST_A,
    ) is None
    # Legacy callers and snapshots remain compatible when neither side has a
    # routing identity; they cannot cross into a newly identified catalog.
    assert _feedback_decision(legacy) is not None
