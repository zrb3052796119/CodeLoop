from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import pytest

from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import RunJournal
from minicode.skill_versions import SkillVersionLedger
from minicode.skills import SkillSummary
from minicode.tools import create_default_tool_registry
from minicode.web.read_model import DashboardReadError, DashboardReadModel


def _record_shadow_skill_evidence(journal: RunJournal) -> None:
    skill = {
        "qualifiedName": "project/memory-audit",
        "source": "project",
        "directory": "project",
        "contentDigest": "a" * 64,
    }
    record = journal.create_run(
        title="password=private-task-title",
        source="headless",
    )
    journal.transition(record.id, "running")
    journal.append_event(
        record.id,
        "skill.routed",
        payload={
            "routingVersion": 2,
            "intentType": "review",
            "actionType": "analyze",
            "totalSkills": 1,
            "selectedCount": 1,
            "selected": [{**skill, "score": 4.25}],
            "selectedTruncated": False,
            "usedFallback": False,
        },
    )
    journal.append_event(
        record.id,
        "skill.loaded",
        payload={"loadVersion": 1, **skill},
    )
    journal.append_event(
        record.id,
        "task.outcome",
        payload={
            "outcomeVersion": 1,
            "outcomeStatus": "success",
            "goalAchieved": True,
            "learningSuccess": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
        },
    )
    journal.append_event(
        record.id,
        "skill.attributed",
        payload={
            "attributionVersion": 1,
            "attributionKind": "task_correlation",
            "outcomeStatus": "success",
            "goalAchieved": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
            "loadedSkillCount": 1,
            "loadedSkills": [skill],
            "loadedSkillsTruncated": False,
        },
    )
    journal.transition(record.id, "completed")


def test_skills_page_distinguishes_a_real_empty_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [],
    ).skills()

    assert payload["source"]["status"] == "live"
    assert payload["summary"]["total"] == 0
    assert payload["summary"]["directoryCount"] == 0
    assert payload["items"] == []
    assert payload["page"] == {
        "limit": 20,
        "hasMore": False,
        "nextCursor": None,
    }
    assert payload["evidence"] == {
        "status": "live",
        "scope": "retained-run-journal",
        "message": (
            "Shadow-only correlation; never grants routing or promotion authority."
        ),
        "ledger": {
            "ledgerVersion": 1,
            "mode": "shadow",
            "scannedRuns": 0,
            "runsTruncated": False,
            "eligibleTreatmentRuns": 0,
            "eligibleControlRuns": 0,
            "excludedRuns": {
                "nonCompleted": 0,
                "eventScanLimited": 0,
                "eventReadIncomplete": 0,
                    "missingOrInvalidOutcome": 0,
                    "nonBinaryOutcome": 0,
                    "unverifiedOutcome": 0,
                    "missingOrInvalidRouting": 0,
                "legacyRouting": 0,
                "ambiguousSkillUse": 0,
                "inconsistentSkillUse": 0,
            },
            "journalDiagnostics": 0,
            "evaluations": [],
            "evaluationsTruncated": False,
            "promotionEligible": False,
        },
    }


def test_skills_page_exposes_shadow_evidence_without_run_titles(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    _record_shadow_skill_evidence(journal)

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [],
        run_journal=journal,
    ).skills()

    evidence = payload["evidence"]
    assert evidence["status"] == "live"
    assert evidence["scope"] == "retained-run-journal"
    assert evidence["ledger"]["eligibleTreatmentRuns"] == 1
    assert evidence["ledger"]["eligibleControlRuns"] == 0
    assert evidence["ledger"]["promotionEligible"] is False
    evaluation = evidence["ledger"]["evaluations"][0]
    assert evaluation["shadowStatus"] == "insufficient_evidence"
    assert evaluation["goalAchievementDelta"] is None
    assert evaluation["skill"]["contentDigest"] == "a" * 64
    assert "private-task-title" not in json.dumps(payload)


def test_skills_page_exposes_read_only_version_lineage_and_locked_gates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = SkillSummary(
        name="memory-audit",
        qualified_name="auditing/memory-audit",
        description="Review persistent memory.",
        path="/private/password=skill-path/SKILL.md",
        source="project",
        directory="auditing",
        content_digest="a" * 64,
    )
    version_ledger = SkillVersionLedger(workspace)
    version_ledger.observe_catalog([skill])
    storage = workspace / ".mini-code" / "skill_versions.json"
    before = storage.read_bytes()

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [skill],
    ).skills()

    version_payload = payload["versionLedger"]
    assert version_payload["status"] == "live"
    assert version_payload["scope"] == "project-skill-version-ledger"
    assert version_payload["ledger"]["promotionLocked"] is True
    assert version_payload["ledger"]["evaluation"] == {
        "gatePolicyVersion": 2,
        "versionCount": 1,
        "promotionCandidateCount": 0,
    }
    version = version_payload["ledger"]["versions"][0]
    assert version["catalogCurrent"] is True
    assert version["skill"]["contentDigest"] == "a" * 64
    assert {
        gate["name"]: gate["status"]
        for gate in version["evaluation"]["gates"]
    } == {
        "outcome": "unavailable",
        "verification": "unavailable",
        "user": "unavailable",
        "cost": "unavailable",
        "latency": "unavailable",
    }
    assert storage.read_bytes() == before
    serialized = json.dumps(version_payload)
    assert "skill-path" not in serialized
    assert str(workspace) not in serialized


def test_default_dashboard_discovery_matches_runtime_observed_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill_path = (
        workspace
        / ".mini-code"
        / "skills"
        / "memory-audit"
        / "SKILL.md"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: memory-audit\n"
        "description: Review persistent memory.\n"
        "---\n"
        "# Memory Audit\n",
        encoding="utf-8",
    )
    create_default_tool_registry(str(workspace), runtime={})

    version_payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    ).skills()["versionLedger"]

    assert version_payload["status"] == "live"
    versions = version_payload["ledger"]["versions"]
    assert len(versions) == 1
    assert versions[0]["catalogCurrent"] is True


def test_version_history_failure_does_not_hide_catalog_or_shadow_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    storage = workspace / ".mini-code" / "skill_versions.json"
    storage.parent.mkdir(parents=True)
    storage.write_text(
        '{"schemaVersion":1,"password":"version-secret"}',
        encoding="utf-8",
    )
    skill = SkillSummary(
        name="memory-audit",
        qualified_name="memory-audit",
        description="Review memory.",
        path="/private/SKILL.md",
        source="project",
        content_digest="a" * 64,
    )

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [skill],
    ).skills()

    assert payload["source"]["status"] == "live"
    assert payload["items"][0]["qualifiedName"] == "memory-audit"
    assert payload["evidence"]["status"] == "live"
    assert payload["versionLedger"] == {
        "status": "unavailable",
        "scope": "project-skill-version-ledger",
        "message": "Skill version history could not be read.",
        "ledger": None,
    }
    assert "version-secret" not in json.dumps(payload)


def test_skill_evidence_marks_incomplete_event_scan_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    snapshot = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [],
        run_journal=journal,
    ).skills()["evidence"]["ledger"]
    assert snapshot is not None
    snapshot["excludedRuns"]["eventScanLimited"] = 1
    monkeypatch.setattr(
        "minicode.web.read_model.SkillEvidenceLedger.snapshot",
        lambda _self: snapshot,
    )

    evidence = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [],
        run_journal=journal,
    ).skills()["evidence"]

    assert evidence["status"] == "partial"


def test_skill_evidence_failure_does_not_hide_the_skill_catalog(
    tmp_path: Path,
) -> None:
    class BrokenJournal:
        def list_runs(self, **_kwargs):
            raise OSError("password=journal-secret")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = SkillSummary(
        name="daily-coding",
        qualified_name="daily-coding",
        description="Daily coding.",
        path="/private/SKILL.md",
        source="project",
    )

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: [skill],
        run_journal=BrokenJournal(),  # type: ignore[arg-type]
    ).skills()

    assert payload["source"]["status"] == "live"
    assert [item["qualifiedName"] for item in payload["items"]] == [
        "daily-coding"
    ]
    assert payload["evidence"] == {
        "status": "unavailable",
        "scope": "retained-run-journal",
        "message": "Skill evidence could not be read.",
        "ledger": None,
    }
    assert "journal-secret" not in json.dumps(payload)


def test_skills_page_projects_safe_summary_without_paths_or_bodies(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = [
        SkillSummary(
            name="daily-coding",
            qualified_name="engineering/daily-coding",
            description="Use password=hidden-value safely.",
            path=str(tmp_path / "sk-test-secret" / "SKILL.md"),
            source="project",
            directory="engineering",
            domains=["coding"],
            scopes=["project"],
            tools=["read_file", "edit_file"],
            keywords=["implementation"],
            examples=["full example must not leave the model"],
        ),
        SkillSummary(
            name="verification-loop",
            qualified_name="verification-loop",
            description="Bounded verification.",
            path=str(tmp_path / "other" / "SKILL.md"),
            source="user",
        ),
    ]

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: skills,
    ).skills()

    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["source"]["status"] == "live"
    assert payload["summary"] == {
        "total": 2,
        "bySource": {
            "project": 1,
            "user": 1,
            "compat_project": 0,
            "compat_user": 0,
        },
        "directoryCount": 1,
        "directories": ["engineering"],
    }
    assert payload["page"] == {
        "limit": 20,
        "hasMore": False,
        "nextCursor": None,
    }
    assert payload["filters"] == {"source": None, "directory": None}
    assert payload["items"][0] == {
        "name": "daily-coding",
        "qualifiedName": "engineering/daily-coding",
        "description": "Use password=[REDACTED] safely.",
        "descriptionTruncated": False,
        "source": "project",
        "directory": "engineering",
        "domains": ["coding"],
        "scopes": ["project"],
        "tools": ["read_file", "edit_file"],
        "keywords": ["implementation"],
        "exampleCount": 1,
    }
    serialized = json.dumps(payload)
    for forbidden in (
        "hidden-value",
        "sk-test-secret",
        "full example must not leave the model",
        '"path"',
        '"content"',
    ):
        assert forbidden not in serialized


def test_skills_page_filters_and_cursor_paginates_stably(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = [
        SkillSummary(
            name=name,
            qualified_name=f"{directory}/{name}" if directory else name,
            description=f"Description {name}",
            path=f"/not-returned/{name}/SKILL.md",
            source=source,
            directory=directory,
        )
        for name, source, directory in (
            ("zulu", "user", "engineering"),
            ("alpha", "project", "engineering"),
            ("bravo", "project", "engineering"),
            ("charlie", "compat_project", "research"),
            ("delta", "compat_user", "research"),
        )
    ]
    model = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: skills,
    )

    first = model.skills(directory="engineering", limit=2)
    second = model.skills(
        directory="engineering", limit=2, cursor=first["page"]["nextCursor"]
    )
    project = model.skills(source="project", directory="engineering")

    assert [item["name"] for item in first["items"] + second["items"]] == [
        "alpha",
        "bravo",
        "zulu",
    ]
    assert first["page"]["hasMore"] is True
    assert second["page"]["hasMore"] is False
    assert [item["name"] for item in project["items"]] == ["alpha", "bravo"]
    assert first["summary"]["total"] == 5


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"source": "compat"}, "invalid_source"),
        ({"directory": "../secret"}, "invalid_directory"),
        ({"directory": "x" * 65}, "invalid_directory"),
        ({"limit": 0}, "invalid_limit"),
        ({"limit": 101}, "invalid_limit"),
        ({"cursor": "../secret"}, "invalid_cursor"),
        ({"cursor": "x" * 513}, "invalid_cursor"),
    ],
)
def test_skills_page_rejects_invalid_filters_and_paging(
    tmp_path: Path, kwargs: dict[str, object], code: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(DashboardReadError) as error:
        DashboardReadModel(
            workspace,
            data_dir=tmp_path / "home" / ".mini-code",
            skill_loader=lambda _workspace: [],
        ).skills(**kwargs)  # type: ignore[arg-type]

    assert error.value.status == 400
    assert error.value.code == code


def test_skills_page_ignores_ordinary_root_files_without_reading_or_writing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    roots = {
        "project": workspace / ".mini-code" / "skills",
        "user": data_dir / "skills",
        "compat_project": workspace / ".claude" / "skills",
        "compat_user": data_dir.parent / ".claude" / "skills",
    }
    ordinary_names = (".DS_Store", "README.md", "desktop.ini", "metadata.json")
    tracked: list[Path] = []
    ordinary_files: set[Path] = set()
    for index, (source, root) in enumerate(roots.items()):
        ordinary = root / ordinary_names[index]
        ordinary.parent.mkdir(parents=True, exist_ok=True)
        ordinary.write_text(
            f"ordinary-file-secret-{source}", encoding="utf-8"
        )
        ordinary_files.add(ordinary)
        skill_file = root / f"{source}-skill" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text(
            f"---\nname: {source}-skill\ndescription: Safe {source} Skill.\n---\n",
            encoding="utf-8",
        )
        tracked.extend((ordinary, skill_file))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked
    }
    before_tree = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    original_read_text = Path.read_text

    def reject_ordinary_body_read(path: Path, *args, **kwargs):
        if path in ordinary_files:
            pytest.fail("ordinary root files must not be read as Skill content")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_ordinary_body_read)

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills(limit=100)

    assert payload["source"]["status"] == "live"
    assert payload["diagnostics"] == []
    assert payload["summary"]["total"] == 4
    assert payload["summary"]["bySource"] == {
        "project": 1,
        "user": 1,
        "compat_project": 1,
        "compat_user": 1,
    }
    assert {item["name"] for item in payload["items"]} == {
        "project-skill",
        "user-skill",
        "compat_project-skill",
        "compat_user-skill",
    }
    serialized = json.dumps(payload)
    for name in ordinary_names:
        assert name not in serialized
    assert "ordinary-file-secret" not in serialized
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_tree


def test_skills_page_ordinary_root_files_do_not_consume_discovery_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    root = workspace / ".mini-code" / "skills"
    root.mkdir(parents=True)
    for index in range(10_050):
        (root / f"ordinary-{index:05d}.metadata").touch()
    skill_file = root / "zz-valid" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(
        "---\nname: valid-after-ordinary-files\n"
        "description: This real Skill must remain discoverable.\n---\n",
        encoding="utf-8",
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills(limit=1)

    assert payload["source"]["status"] == "live"
    assert payload["diagnostics"] == []
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["bySource"]["project"] == 1
    assert [item["name"] for item in payload["items"]] == [
        "valid-after-ordinary-files"
    ]
    assert payload["page"] == {
        "limit": 1,
        "hasMore": False,
        "nextCursor": None,
    }


def test_skills_page_ignores_ds_store_in_project_and_compat_project_roots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    roots = (
        (workspace / ".mini-code" / "skills", "project-skill"),
        (workspace / ".claude" / "skills", "compat-project-skill"),
    )
    for root, name in roots:
        root.mkdir(parents=True)
        (root / ".DS_Store").write_bytes(b"desktop-metadata-secret")
        skill_file = root / name / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text(
            f"---\nname: {name}\ndescription: Safe Skill.\n---\n",
            encoding="utf-8",
        )

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills()

    assert payload["source"]["status"] == "live"
    assert payload["diagnostics"] == []
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["bySource"] == {
        "project": 1,
        "user": 0,
        "compat_project": 1,
        "compat_user": 0,
    }
    assert "desktop-metadata-secret" not in json.dumps(payload)


def test_skills_page_uses_bounded_root_anchored_discovery_and_isolates_bad_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    roots = {
        "project": workspace / ".mini-code" / "skills",
        "user": data_dir / "skills",
        "compat_project": workspace / ".claude" / "skills",
        "compat_user": data_dir.parent / ".claude" / "skills",
    }
    tracked: list[Path] = []
    for source, root in roots.items():
        skill_file = root / f"{source}-skill" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text(
            f"---\nname: {source}-skill\ndescription: Safe {source} summary.\n"
            "tools: [read_file]\n---\n\n# Hidden full body\n",
            encoding="utf-8",
        )
        tracked.append(skill_file)

    directory_root = roots["project"] / "engineering"
    (directory_root / "nested").mkdir(parents=True)
    directory_file = directory_root / "SKILL_DIR.md"
    directory_file.write_text(
        "---\nname: engineering\ndescription: Engineering skills.\n"
        "domains: [coding]\n---\n",
        encoding="utf-8",
    )
    nested_file = directory_root / "nested" / "SKILL.md"
    nested_file.write_text(
        "---\nname: nested\ndescription: Nested skill.\n---\n",
        encoding="utf-8",
    )
    tracked.extend([directory_file, nested_file])

    invalid = roots["project"] / "invalid" / "SKILL.md"
    invalid.parent.mkdir()
    invalid.write_bytes(b"\xff\xfe")
    tracked.append(invalid)
    outside = tmp_path / "outside-skill.md"
    outside.write_text(
        "---\nname: escaped\ndescription: outside-skill-secret\n---\n",
        encoding="utf-8",
    )
    escaped = roots["project"] / "escaped" / "SKILL.md"
    escaped.parent.mkdir()
    escaped.symlink_to(outside)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked}

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills(limit=100)

    assert payload["summary"]["bySource"] == {
        "project": 2,
        "user": 1,
        "compat_project": 1,
        "compat_user": 1,
    }
    assert payload["summary"]["directoryCount"] == 1
    assert {item["name"] for item in payload["items"]} == {
        "project-skill",
        "user-skill",
        "compat_project-skill",
        "compat_user-skill",
        "nested",
    }
    nested = next(item for item in payload["items"] if item["name"] == "nested")
    assert nested["qualifiedName"] == "engineering/nested"
    assert nested["domains"] == ["coding"]
    assert payload["source"]["status"] == "error"
    assert {item["code"] for item in payload["diagnostics"]} == {
        "skill_read_failed"
    }
    assert "outside-skill-secret" not in json.dumps(payload)
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


def test_skills_page_preserves_corruption_and_child_symlink_diagnostics(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    root = workspace / ".mini-code" / "skills"
    valid = root / "valid" / "SKILL.md"
    valid.parent.mkdir(parents=True)
    valid.write_text(
        "---\nname: valid\ndescription: Valid Skill remains visible.\n---\n",
        encoding="utf-8",
    )
    invalid_utf = root / "invalid-utf" / "SKILL.md"
    invalid_utf.parent.mkdir()
    invalid_utf.write_bytes(b"\xff\xfe")
    invalid_frontmatter = root / "invalid-frontmatter" / "SKILL.md"
    invalid_frontmatter.parent.mkdir()
    invalid_frontmatter.write_text(
        "---\nname: invalid-frontmatter\ndescription: unterminated-secret",
        encoding="utf-8",
    )
    invalid_name = root / "invalid-name" / "SKILL.md"
    invalid_name.parent.mkdir()
    invalid_name.write_text(
        "---\nname: ../invalid-name\ndescription: invalid-name-secret\n---\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside-skills"
    outside_skill = outside / "escaped" / "SKILL.md"
    outside_skill.parent.mkdir(parents=True)
    outside_skill.write_text(
        "---\nname: escaped\ndescription: outside-directory-secret\n---\n",
        encoding="utf-8",
    )
    (root / "escaped-directory").symlink_to(
        outside / "escaped", target_is_directory=True
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills()

    assert payload["source"]["status"] == "error"
    assert payload["summary"]["total"] == 1
    assert [item["name"] for item in payload["items"]] == ["valid"]
    assert [item["code"] for item in payload["diagnostics"]] == [
        "skill_read_failed",
        "skill_read_failed",
        "skill_read_failed",
        "skill_read_failed",
    ]
    serialized = json.dumps(payload)
    for secret in (
        "unterminated-secret",
        "invalid-name-secret",
        "outside-directory-secret",
    ):
        assert secret not in serialized


def test_skills_page_localizes_root_entry_metadata_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    root = workspace / ".mini-code" / "skills"
    unreadable = root / "unreadable"
    unreadable.mkdir(parents=True)
    valid = root / "valid" / "SKILL.md"
    valid.parent.mkdir()
    valid.write_text(
        "---\nname: valid\ndescription: Valid Skill.\n---\n",
        encoding="utf-8",
    )
    original_lstat = Path.lstat

    def fail_one_entry(path: Path):
        if path == unreadable:
            raise OSError("private filesystem failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_one_entry)

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills()

    assert payload["source"]["status"] == "error"
    assert [item["name"] for item in payload["items"]] == ["valid"]
    assert payload["diagnostics"] == [
        {
            "source": "skills",
            "code": "skill_read_failed",
            "message": "A Skill directory could not be read.",
        }
    ]
    assert "private filesystem failure" not in json.dumps(payload)


def test_skills_page_localizes_skill_directory_enumeration_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    root = workspace / ".mini-code" / "skills"
    grouped = root / "grouped"
    grouped.mkdir(parents=True)
    (grouped / "SKILL_DIR.md").write_text(
        "---\nname: grouped\ndescription: Grouped Skills.\n---\n",
        encoding="utf-8",
    )
    valid = root / "valid" / "SKILL.md"
    valid.parent.mkdir()
    valid.write_text(
        "---\nname: valid\ndescription: Valid Skill.\n---\n",
        encoding="utf-8",
    )
    original_iterdir = Path.iterdir

    def fail_one_directory(path: Path):
        if path == grouped:
            raise OSError("private enumeration failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_one_directory)

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills()

    assert payload["source"]["status"] == "error"
    assert [item["name"] for item in payload["items"]] == ["valid"]
    assert payload["diagnostics"] == [
        {
            "source": "skills",
            "code": "skill_read_failed",
            "message": "A Skill directory could not be read.",
        }
    ]
    assert "private enumeration failure" not in json.dumps(payload)


def test_skills_page_bounds_descriptions_metadata_lists_and_response_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    long_values = [f"value-{index}-" + "x" * 100 for index in range(30)]
    skills = [
        SkillSummary(
            name=f"skill-{index:03d}",
            qualified_name=f"engineering/skill-{index:03d}",
            description="credential=very-secret-value " + "d" * 1_000,
            path=f"/not-returned/{index}/SKILL.md",
            source="project",
            directory="engineering",
            domains=long_values,
            scopes=long_values,
            tools=long_values,
            keywords=long_values,
            examples=["hidden example"] * 3,
        )
        for index in range(100)
    ]

    payload = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        skill_loader=lambda _workspace: skills,
    ).skills(limit=100)

    assert payload["page"]["hasMore"] is True
    assert payload["page"]["nextCursor"] is not None
    assert len(payload["items"]) < 100
    assert any(
        item["code"] == "response_budget_applied"
        for item in payload["diagnostics"]
    )
    for item in payload["items"]:
        assert item["descriptionTruncated"] is True
        assert len(item["description"]) <= 401
        assert item["exampleCount"] == 3
        for field in ("domains", "scopes", "tools", "keywords"):
            assert len(item[field]) == 20
            assert all(len(value) <= 65 for value in item[field])
    serialized = json.dumps(payload)
    assert "very-secret-value" not in serialized
    assert "hidden example" not in serialized


@pytest.mark.parametrize("failure", ["oversized", "root_symlink"])
def test_skills_page_localizes_oversized_files_and_root_symlink_escapes(
    tmp_path: Path, failure: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    root = workspace / ".mini-code" / "skills"
    if failure == "oversized":
        path = root / "oversized" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    else:
        outside = tmp_path / "outside-skills"
        path = outside / "escaped" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: escaped\ndescription: escaped-root-secret\n---\n",
            encoding="utf-8",
        )
        root.parent.mkdir(parents=True)
        root.symlink_to(outside, target_is_directory=True)

    payload = DashboardReadModel(workspace, data_dir=data_dir).skills()

    assert payload["source"]["status"] == "error"
    assert payload["items"] == []
    assert payload["diagnostics"][0]["code"] == "skill_read_failed"
    assert "escaped-root-secret" not in json.dumps(payload)


def test_connections_page_distinguishes_live_gateway_empty_config_and_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    payload = DashboardReadModel(
        workspace, data_dir=tmp_path / "home" / ".mini-code"
    ).connections()

    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["source"] == {
        "status": "stale",
        "updatedAt": payload["generatedAt"],
        "message": "MCP runtime facts are retained and historical; current MCP status is unavailable.",
    }
    assert payload["summary"] == {
        "gatewayStatus": "live",
        "configuredMcpCount": 0,
        "registeredConfiguredMcpCount": None,
        "activeMcpInstanceCount": None,
        "liveMcpCount": None,
        "complete": True,
        "observedConfiguredCount": 0,
        "unobservedConfiguredCount": 0,
        "unmatchedObservedServerCount": 0,
    }
    assert payload["gateway"] == {
        "status": "live",
        "transport": "http",
        "scope": "local",
    }
    assert payload["mcpRuntime"] == {
        "status": "unavailable",
        "current": "unavailable",
        "historical": "partial",
        "lastObservedAt": None,
        "retainedObservationCount": 0,
        "liveCount": None,
        "message": "No retained MCP observation is available in the scanned window; current MCP status is unavailable.",
    }
    assert payload["coverage"] == {
        "scope": "retained-run-scoped-mcp-observations",
        "historical": "partial",
        "current": "unavailable",
        "runScanLimit": 100,
        "eventScanLimitPerRun": 1000,
        "retainedRuns": 0,
        "scannedRuns": 0,
        "limited": False,
    }
    assert payload["mcpServers"] == []
    assert payload["configSources"] == {
        "user": {"status": "live", "updatedAt": None, "count": 0},
        "project": {"status": "live", "updatedAt": None, "count": 0},
    }
    assert payload["diagnostics"] == []
    assert not (tmp_path / "home" / ".mini-code").exists()


def test_connections_page_associates_effective_server_with_retained_runtime_fact(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"command": "python", "protocol": "newline-json"}
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"protocol": "content-length"}}}
        ),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Observed MCP", source="gateway")
    journal.transition(run.id, "running")
    event = journal.append_event(
        run.id,
        "mcp.runtime.observed",
        step=2,
        payload={
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "shared"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": True,
            "protocol": "newline-json",
        },
    )
    journal.transition(run.id, "completed")
    tracked = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in data_dir.rglob("*")
        if path.is_file()
    }

    payload = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).connections()

    assert payload["summary"]["liveMcpCount"] is None
    assert payload["summary"]["observedConfiguredCount"] == 1
    assert payload["summary"]["unobservedConfiguredCount"] == 0
    assert payload["summary"]["unmatchedObservedServerCount"] == 0
    assert payload["mcpServers"][0]["scope"] == "project"
    assert payload["mcpServers"][0]["protocol"] == "content-length"
    assert payload["mcpRuntime"] == {
        "status": "stale",
        "current": "unavailable",
        "historical": "partial",
        "lastObservedAt": event.timestamp,
        "retainedObservationCount": 1,
        "liveCount": None,
        "message": "Retained Run observations are historical; current MCP status is unavailable.",
    }
    assert payload["coverage"] == {
        "scope": "retained-run-scoped-mcp-observations",
        "historical": "partial",
        "current": "unavailable",
        "runScanLimit": 100,
        "eventScanLimitPerRun": 1000,
        "retainedRuns": 1,
        "scannedRuns": 1,
        "limited": False,
    }
    assert payload["mcpServers"][0]["runtime"] == {
        "status": "stale",
        "current": "unavailable",
        "observed": True,
        "lastObservedAt": event.timestamp,
        "lastOutcome": "request_succeeded",
        "connectionAttempted": True,
        "observedProtocol": "newline-json",
        "retainedObservationCount": 1,
    }
    assert mcp_server_key(workspace, "shared") not in json.dumps(payload)
    for path, (content, mtime) in tracked.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


def test_connections_page_keeps_disabled_config_while_showing_historical_fact(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "disabled-server": {"command": "python", "enabled": False}
                }
            }
        ),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Earlier enabled", source="tui")
    journal.transition(run.id, "running")
    journal.append_event(
        run.id,
        "mcp.runtime.observed",
        payload={
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "disabled-server"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "connection_failed",
            "connectionAttempted": True,
            "failureKind": "timeout",
        },
    )
    journal.transition(run.id, "failed")

    payload = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).connections()

    server = payload["mcpServers"][0]
    assert server["status"] == "disabled"
    assert server["liveStatus"] == "unavailable"
    assert server["runtime"]["status"] == "stale"
    assert server["runtime"]["lastOutcome"] == "connection_failed"
    assert payload["summary"]["observedConfiguredCount"] == 1
    assert payload["summary"]["liveMcpCount"] is None


def test_connections_page_counts_unmatched_history_without_returning_server_key(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"current": {"command": "python"}}}),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Removed config", source="headless")
    journal.transition(run.id, "running")
    removed_key = mcp_server_key(workspace, "removed")
    journal.append_event(
        run.id,
        "mcp.runtime.observed",
        payload={
            "mcpVersion": 1,
            "serverKey": removed_key,
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": False,
        },
    )
    journal.transition(run.id, "completed")

    payload = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).connections()

    assert payload["mcpRuntime"]["status"] == "stale"
    assert payload["summary"]["observedConfiguredCount"] == 0
    assert payload["summary"]["unobservedConfiguredCount"] == 1
    assert payload["summary"]["unmatchedObservedServerCount"] == 1
    assert payload["mcpServers"][0]["runtime"]["status"] == "unavailable"
    assert removed_key not in json.dumps(payload)


def test_connections_page_isolates_config_and_runtime_source_failures(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        '{"mcpServers": Authorization=user-secret', encoding="utf-8"
    )
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"project": {"command": "python"}}}),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Project MCP", source="gateway")
    journal.transition(run.id, "running")
    journal.append_event(
        run.id,
        "mcp.runtime.observed",
        payload={
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "project"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_failed",
            "connectionAttempted": False,
            "failureKind": "request_error",
        },
    )
    journal.transition(run.id, "completed")

    config_failed = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    ).connections()

    assert config_failed["source"]["status"] == "error"
    assert config_failed["configSources"]["user"]["status"] == "error"
    assert config_failed["mcpRuntime"]["status"] == "stale"
    assert config_failed["mcpServers"][0]["runtime"]["observed"] is True
    assert "user-secret" not in json.dumps(config_failed)

    class FailingJournal:
        def list_runs(self, *, limit: int):
            raise OSError("Bearer runtime-secret")

    runtime_failed = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=FailingJournal()
    ).connections()

    assert runtime_failed["mcpServers"][0]["status"] == "configured"
    assert runtime_failed["mcpServers"][0]["runtime"]["status"] == "error"
    assert runtime_failed["mcpRuntime"]["status"] == "error"
    assert runtime_failed["coverage"]["retainedRuns"] is None
    assert runtime_failed["source"]["status"] == "error"
    assert "runtime-secret" not in json.dumps(runtime_failed)


def test_connections_page_merges_project_over_user_without_starting_mcp_or_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    user_path = data_dir / "mcp.json"
    project_path = workspace / ".mcp.json"
    user_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {
                        "command": "python",
                        "args": ["password=hidden-value"],
                        "env": {"TOKEN": "sk-test-secret"},
                        "url": "https://url-secret.invalid/mcp",
                        "headers": {"Authorization": "credential-secret"},
                        "protocol": "newline-json",
                    },
                    "user-disabled": {
                        "command": "npx",
                        "enabled": False,
                    },
                    "bad-user": "Bearer very-secret-token",
                }
            }
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {
                        "args": ["Authorization=project-secret"],
                        "env": {"COOKIE": "cookie-secret"},
                        "protocol": "content-length",
                    },
                    "project-missing-command": {
                        "env": {"API_KEY": "provider-secret"}
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (user_path, project_path)
    }

    def fail_popen(*_args, **_kwargs):
        pytest.fail("Connections read must not create a subprocess")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    payload = DashboardReadModel(workspace, data_dir=data_dir).connections()

    assert payload["summary"] == {
        "gatewayStatus": "live",
        "configuredMcpCount": 3,
        "registeredConfiguredMcpCount": None,
        "activeMcpInstanceCount": None,
        "liveMcpCount": None,
        "complete": False,
        "observedConfiguredCount": 0,
        "unobservedConfiguredCount": 3,
        "unmatchedObservedServerCount": 0,
    }
    by_name = {item["name"]: item for item in payload["mcpServers"]}
    assert by_name["shared"] == {
        "name": "shared",
        "scope": "project",
        "status": "configured",
        "liveStatus": "unavailable",
        "protocol": "content-length",
        "current": {
            "status": "unavailable",
            "state": None,
            "activeInstanceCount": None,
            "protocol": None,
            "failureKind": None,
            "updatedAt": None,
            "reason": "source_unavailable",
        },
        "runtime": {
            "status": "unavailable",
            "current": "unavailable",
            "observed": False,
            "lastObservedAt": None,
            "lastOutcome": None,
            "connectionAttempted": None,
            "observedProtocol": None,
            "retainedObservationCount": 0,
        },
    }
    assert by_name["user-disabled"]["status"] == "disabled"
    assert by_name["project-missing-command"]["status"] == "error"
    assert payload["configSources"]["user"] == {
        "status": "error",
        "updatedAt": payload["configSources"]["user"]["updatedAt"],
        "count": 2,
    }
    assert payload["configSources"]["project"]["status"] == "live"
    assert payload["source"]["status"] == "error"
    assert {item["code"] for item in payload["diagnostics"]} == {
        "mcp_entry_invalid"
    }
    serialized = json.dumps(payload)
    for forbidden in (
        "hidden-value",
        "sk-test-secret",
        "very-secret-token",
        "project-secret",
        "cookie-secret",
        "provider-secret",
        "url-secret",
        "credential-secret",
        '"command"',
        '"args"',
        '"env"',
        '"url"',
        '"headers"',
    ):
        assert forbidden not in serialized
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("failure", ["corrupt", "oversized", "symlink"])
def test_connections_page_localizes_one_bad_config_source(
    tmp_path: Path, failure: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    user_path = data_dir / "mcp.json"
    if failure == "corrupt":
        user_path.write_text('{"mcpServers": password=hidden-value', encoding="utf-8")
    elif failure == "oversized":
        user_path.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    else:
        outside = tmp_path / "outside-mcp.json"
        outside.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "outside": {
                            "command": "python",
                            "env": {"TOKEN": "outside-secret"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        user_path.symlink_to(outside)
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "password=hidden-value": {
                        "command": "python",
                        "args": ["Bearer very-secret-token"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).connections()

    assert payload["source"]["status"] == "error"
    assert payload["summary"]["configuredMcpCount"] == 1
    assert payload["summary"]["liveMcpCount"] is None
    assert payload["configSources"]["user"] == {
        "status": "error",
        "updatedAt": None,
        "count": None,
    }
    assert payload["configSources"]["project"]["status"] == "live"
    assert payload["mcpServers"][0]["scope"] == "project"
    assert payload["mcpServers"][0]["name"] == "password=[REDACTED]"
    serialized = json.dumps(payload)
    for secret in (
        "hidden-value",
        "very-secret-token",
        "outside-secret",
    ):
        assert secret not in serialized


def test_connections_page_bounds_large_configuration_summaries(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    f"server-{index:03d}": {"command": "python"}
                    for index in range(150)
                }
            }
        ),
        encoding="utf-8",
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).connections()

    assert payload["summary"]["configuredMcpCount"] == 150
    assert len(payload["mcpServers"]) == 100
    assert payload["source"]["status"] == "stale"
    assert any(
        item["code"] == "response_budget_applied"
        for item in payload["diagnostics"]
    )


def test_connections_page_isolates_malformed_nested_env_during_override(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {
                        "command": "python",
                        "env": "password=hidden-value",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {
                        "protocol": "newline-json",
                        "env": {"TOKEN": "sk-test-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).connections()

    assert payload["summary"]["configuredMcpCount"] == 1
    assert payload["mcpServers"][0]["name"] == "shared"
    assert payload["mcpServers"][0]["status"] == "configured"
    assert payload["source"]["status"] == "error"
    assert any(
        item["code"] == "mcp_entry_invalid" for item in payload["diagnostics"]
    )
    assert "hidden-value" not in json.dumps(payload)
    assert "sk-test-secret" not in json.dumps(payload)


def test_system_page_returns_only_safe_runtime_workspace_and_feature_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    monkeypatch.setattr(
        "minicode.web.read_model.importlib_metadata.version",
        lambda package: "9.8.7" if package == "minicode-py" else "unexpected",
    )

    payload = DashboardReadModel(workspace, data_dir=data_dir).system()

    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["source"] == {
        "status": "live",
        "updatedAt": payload["generatedAt"],
        "message": None,
    }
    assert payload["application"] == {
        "name": "minicode-py",
        "version": "9.8.7",
        "dashboardSchemaVersion": 1,
    }
    assert payload["runtime"] == {
        "pythonVersion": platform.python_version(),
        "platform": "macOS" if platform.system() == "Darwin" else platform.system(),
        "architecture": platform.machine() or "unknown",
        "processMode": "gateway",
    }
    assert payload["workspace"] == {
        "id": payload["workspace"]["id"],
        "name": "workspace",
        "status": "live",
    }
    assert payload["workspace"]["id"].startswith("ws_")
    assert payload["features"] == {
        "dashboard": "read-only",
        "sessions": "live",
        "memory": "live",
        "skills": "live",
        "mcpConfig": "stale",
        "mcpRuntime": "unavailable",
        "runs": "lifecycle-model-usage-cost-tool-assistant-skill-memory-context",
        "usage": "live",
        "sse": "unavailable",
        "writes": "unavailable",
    }
    assert payload["storage"] == {
        "sessions": {"status": "live", "writable": None},
        "memoryUser": {"status": "live", "writable": None},
        "memoryProject": {"status": "live", "writable": None},
        "memoryLocal": {"status": "live", "writable": None},
        "skills": {"status": "live", "writable": None},
        "mcpConfig": {"status": "stale", "writable": None},
    }
    assert payload["diagnostics"] == []
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths
    serialized = json.dumps(payload)
    for forbidden in (
        str(data_dir.parent),
        str(workspace),
        "executable",
        "sys.path",
        "argv",
        '"env"',
        "provider",
    ):
        assert forbidden not in serialized


def test_system_page_uses_source_checkout_version_fallback_and_missing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "password=hidden-value-missing"
    monkeypatch.setattr(
        "minicode.web.read_model.importlib_metadata.version",
        lambda _package: (_ for _ in ()).throw(RuntimeError("sk-test-secret")),
    )

    payload = DashboardReadModel(
        workspace, data_dir=tmp_path / "home" / ".mini-code"
    ).system()

    assert payload["application"]["version"] == "0.1.0"
    assert payload["workspace"]["status"] == "error"
    assert payload["source"]["status"] == "error"
    assert payload["storage"]["sessions"]["status"] == "live"
    assert payload["features"]["runs"] == "lifecycle-model-usage-cost-tool-assistant-skill-memory-context"
    serialized = json.dumps(payload)
    assert "hidden-value-missing" not in serialized
    assert "sk-test-secret" not in serialized


def test_system_page_localizes_storage_failures_without_exposing_source_text(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    session_index = data_dir / "sessions_index.json"
    session_index.write_text(
        '{"broken": Bearer very-secret-token', encoding="utf-8"
    )
    user_memory = data_dir / "memory" / "memory.json"
    user_memory.parent.mkdir()
    user_memory.write_text('{"entries": password=hidden-value', encoding="utf-8")
    skill_file = workspace / ".mini-code" / "skills" / "bad" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_bytes(b"\xff\xfe")
    mcp_file = workspace / ".mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "invalid-runtime": {"env": {"TOKEN": "sk-test-secret"}}
                }
            }
        ),
        encoding="utf-8",
    )
    tracked = (session_index, user_memory, skill_file, mcp_file)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked}

    payload = DashboardReadModel(workspace, data_dir=data_dir).system()

    assert payload["source"]["status"] == "error"
    assert payload["features"]["sessions"] == "error"
    assert payload["features"]["memory"] == "error"
    assert payload["features"]["skills"] == "error"
    assert payload["features"]["mcpConfig"] == "error"
    assert payload["features"]["mcpRuntime"] == "unavailable"
    assert payload["storage"]["memoryUser"]["status"] == "error"
    assert payload["storage"]["memoryProject"]["status"] == "live"
    assert payload["storage"]["memoryLocal"]["status"] == "live"
    serialized = json.dumps(payload)
    for secret in ("very-secret-token", "hidden-value", "sk-test-secret"):
        assert secret not in serialized
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime
