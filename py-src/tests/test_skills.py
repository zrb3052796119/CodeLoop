from pathlib import Path

from minicode.skills import discover_skill_directories, discover_skills, load_skill


def test_discover_skills_prefers_project_root(tmp_path: Path, monkeypatch) -> None:
    project_skill = tmp_path / ".mini-code" / "skills" / "demo" / "SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text("# Demo\n\nProject description\n", encoding="utf-8")

    user_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    user_skill = user_home / ".mini-code" / "skills" / "demo" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("# Demo\n\nUser description\n", encoding="utf-8")

    skills = discover_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0].description == "Project description"
    loaded = load_skill(tmp_path, "demo")
    assert loaded is not None
    assert loaded.content.startswith("# Demo")


def test_discover_nested_skill_directories_and_frontmatter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / ".mini-code" / "skills" / "code-understanding"
    root.mkdir(parents=True)
    (root / "SKILL_DIR.md").write_text(
        """---
name: code-understanding
description: Skills for reading and explaining code architecture.
domains: [code, file, search, analysis]
scopes: [readonly]
keywords: [explain, architecture, trace]
---

# Code Understanding
""",
        encoding="utf-8",
    )
    skill_dir = root / "codebase-explanation"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: codebase-explanation
description: Explain codebase architecture and tool calling flow.
directory: code-understanding
domains: [code, file, search, analysis]
scopes: [readonly]
tools: [read_file, grep_files, load_skill]
keywords: [agent_loop, architecture, tool calling]
examples:
  - "Explain how agent_loop.py handles tool calls"
---

# Codebase Explanation
""",
        encoding="utf-8",
    )

    directories = discover_skill_directories(tmp_path)
    skills = discover_skills(tmp_path)
    loaded = load_skill(tmp_path, "code-understanding/codebase-explanation")

    assert directories[0].name == "code-understanding"
    assert directories[0].domains == ["code", "file", "search", "analysis"]
    assert skills[0].qualified_name == "code-understanding/codebase-explanation"
    assert skills[0].directory == "code-understanding"
    assert skills[0].tools == ["read_file", "grep_files", "load_skill"]
    assert loaded is not None
    assert loaded.qualified_name == "code-understanding/codebase-explanation"
    assert "Codebase Explanation" in loaded.content
