"""Runner support for the memory-lesson-benefit study.

New oracle kind ``memory_lesson_covers``: proves that a lesson precipitated
from a real failure episode actually covers the correct target (the exact
path or unittest module), by content inspection of the workspace PROJECT
memory authority at evaluation time.  Only the boolean outcome is public;
lesson text never leaves the private evaluation.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_north_star_live as runner
import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope


def _case(**extra: object) -> dict:
    case = {
        "id": "lb-x1-support",
        "category": "memory-lesson-benefit",
        "mutability": "read_only",
        "files": {"README.md": "synthetic\n"},
        "turns": [{"prompt": "learn"}],
        "oracleIds": ["covers"],
        "oracles": [
            {
                "id": "covers",
                "kind": "memory_lesson_covers",
                "text": "docs/policies/auth-policy.md",
            }
        ],
    }
    case.update(extra)
    return case


def _manifest(**extra: object) -> dict:
    return {"schemaVersion": 1, "suiteId": "suite-lb", "cases": [_case(**extra)]}


# ── manifest contract ──────────────────────────────────────────────


def test_manifest_accepts_lesson_covers_contract() -> None:
    cases = runner._validate_manifest(_manifest())
    assert cases[0]["oracles"][0]["kind"] == "memory_lesson_covers"


def test_manifest_rejects_invalid_lesson_covers_contract() -> None:
    with pytest.raises(ValueError, match="lesson"):
        runner._validate_manifest(
            _manifest(
                oracles=[
                    {
                        "id": "covers",
                        "kind": "memory_lesson_covers",
                        "text": "",
                    }
                ]
            )
        )
    with pytest.raises(ValueError, match="lesson"):
        runner._validate_manifest(
            _manifest(
                oracles=[
                    {
                        "id": "covers",
                        "kind": "memory_lesson_covers",
                        # missing text entirely
                    }
                ]
            )
        )
    with pytest.raises(ValueError, match="lesson"):
        runner._validate_manifest(
            _manifest(
                oracles=[
                    {
                        "id": "covers",
                        "kind": "memory_lesson_covers",
                        "text": "docs/policies/auth-policy.md",
                        "claimType": "not-a-claim",
                    }
                ]
            )
        )


# ── oracle evaluation ──────────────────────────────────────────────


def _workspace_with_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> Path:
    monkeypatch.setattr(
        memory_module, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        content,
        tags=["auth"],
        source="reflection",
    )
    return workspace


def _evaluate(oracle: dict, workspace: Path) -> bool:
    return runner._evaluate_oracle(
        oracle,
        workspace=workspace,
        turns=[],
        before_digest=None,
        journal=SimpleNamespace(),
    )


def test_lesson_covers_passes_when_content_contains_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace_with_lesson(
        tmp_path,
        monkeypatch,
        "Recovery: the auth policy lives at docs/policies/auth-policy.md, "
        "not at the stale location.",
    )
    assert _evaluate(_case()["oracles"][0], workspace) is True


def test_lesson_covers_fails_without_covering_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace_with_lesson(
        tmp_path, monkeypatch, "Prefer focused unit tests."
    )
    assert _evaluate(_case()["oracles"][0], workspace) is False


def test_lesson_covers_fails_on_empty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    assert _evaluate(_case()["oracles"][0], workspace) is False
