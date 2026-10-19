"""Default maintenance guidance must preserve explicit project conventions."""

from pathlib import Path

import pytest

from minicode.prompt import build_system_prompt


@pytest.mark.parametrize("interactive", [True, False])
def test_default_bugfix_guidance_has_no_imposed_architecture(tmp_path, interactive):
    prompt = build_system_prompt(
        str(tmp_path), ["write scope: app.py"],
        {"user_interaction_available": interactive},
    )
    for obsolete in (
        "Theory first", "Six Areas", "exactly ONE sink", "1:1 path mirror",
        "Code file paths must be isomorphic", "No code without design",
    ):
        assert obsolete not in prompt
    assert "existing architecture" in prompt
    assert "smallest supported change" in prompt
    assert "Read-only investigations stay read-only" in prompt
    assert "write scope: app.py" in prompt
    assert "Verification, approval, review, and commit gates remain scheduler-owned" in prompt
    assert "Coding verification scope" in prompt
    assert "within 3800 characters" in prompt


def test_explicit_global_and_project_conventions_are_retained(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "workspace"
    project.mkdir()
    global_rules = "Global convention: document public API migrations."
    project_rules = "Project convention: Theory first; use the Six Areas layout."
    (home / ".claude" / "CLAUDE.md").write_text(global_rules, encoding="utf-8")
    (project / "CLAUDE.md").write_text(project_rules, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    prompt = build_system_prompt(str(project), [], {})

    assert global_rules in prompt
    assert project_rules in prompt
    assert "explicit user and project instructions" in prompt


def test_truncation_guidance_requests_only_relevant_source_windows(tmp_path):
    prompt = build_system_prompt(str(tmp_path), [], {})
    assert "TRUNCATED: yes" in prompt
    assert "does not require reading the whole file" in prompt
    assert "start_line/end_line" in prompt
    assert "continue reading with a larger offset" not in prompt
