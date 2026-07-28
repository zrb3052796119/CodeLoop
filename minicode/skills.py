from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SkillDirectorySummary:
    name: str
    description: str
    path: str
    source: str
    domains: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillSummary:
    name: str
    description: str
    path: str
    source: str
    qualified_name: str = ""
    directory: str = ""
    directory_description: str = ""
    domains: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    content_digest: str = ""


@dataclass(slots=True)
class LoadedSkill(SkillSummary):
    content: str = ""


def extract_description(markdown: str) -> str:
    metadata, body = parse_frontmatter(markdown)
    description = str(metadata.get("description", "")).strip()
    if description:
        return description.replace("`", "")

    normalized = body.replace("\r\n", "\n")
    paragraphs = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    for block in paragraphs:
        if block.startswith("#"):
            continue
        for line in [part.strip() for part in block.split("\n")]:
            if line and not line.startswith("#"):
                return line.replace("`", "")
    return "No description provided."


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    normalized = markdown.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, markdown

    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, markdown

    raw = normalized[4:end]
    body = normalized[end + len("\n---\n"):]
    return _parse_simple_yaml(raw), body


def _parse_simple_yaml(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            existing = result.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(_unquote(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if not value:
            result[current_key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[current_key] = [
                _unquote(part.strip())
                for part in value[1:-1].split(",")
                if part.strip()
            ]
        else:
            result[current_key] = _unquote(value)
    return result


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _home_dir() -> Path:
    return Path.home()


def _skill_roots(cwd: str | Path) -> list[tuple[Path, str]]:
    base = Path(cwd)
    home = _home_dir()
    return [
        (base / ".mini-code" / "skills", "project"),
        (home / ".mini-code" / "skills", "user"),
        (base / ".claude" / "skills", "compat_project"),
        (home / ".claude" / "skills", "compat_user"),
    ]


def _directory_from_file(path: Path, source: str) -> SkillDirectorySummary | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata, _ = parse_frontmatter(content)
    name = str(metadata.get("name") or path.parent.name).strip()
    if not name:
        return None
    return SkillDirectorySummary(
        name=name,
        description=extract_description(content),
        path=str(path),
        source=source,
        domains=_as_list(metadata.get("domains")),
        scopes=_as_list(metadata.get("scopes")),
        keywords=_as_list(metadata.get("keywords")),
    )


def _skill_from_file(
    path: Path,
    source: str,
    *,
    fallback_name: str,
    directory: str = "",
    directory_summary: SkillDirectorySummary | None = None,
) -> LoadedSkill | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata, _ = parse_frontmatter(content)
    name = str(metadata.get("name") or fallback_name).strip()
    if not name:
        return None
    skill_directory = str(metadata.get("directory") or directory).strip()
    qualified_name = f"{skill_directory}/{name}" if skill_directory else name
    directory_description = directory_summary.description if directory_summary else ""
    domains = _as_list(metadata.get("domains")) or (directory_summary.domains if directory_summary else [])
    scopes = _as_list(metadata.get("scopes")) or (directory_summary.scopes if directory_summary else [])
    keywords = _as_list(metadata.get("keywords")) or (directory_summary.keywords if directory_summary else [])

    return LoadedSkill(
        name=name,
        qualified_name=qualified_name,
        description=extract_description(content),
        path=str(path),
        source=source,
        directory=skill_directory,
        directory_description=directory_description,
        domains=list(domains),
        scopes=list(scopes),
        tools=_as_list(metadata.get("tools")),
        keywords=list(keywords),
        examples=_as_list(metadata.get("examples")),
        content=content,
    )


def _list_skill_dirs(root: Path, source: str) -> list[LoadedSkill]:
    if not root.exists():
        return []
    results: list[LoadedSkill] = []
    for entry in root.iterdir():
        try:
            if not entry.is_dir():
                continue
        except OSError:
            # Windows: untrusted mount points, broken symlinks, etc.
            continue

        directory_summary = _directory_from_file(entry / "SKILL_DIR.md", source)
        direct_skill = entry / "SKILL.md"
        if direct_skill.exists():
            skill = _skill_from_file(direct_skill, source, fallback_name=entry.name)
            if skill is not None:
                results.append(skill)

        if directory_summary is None:
            continue
        for nested in entry.iterdir():
            try:
                if not nested.is_dir():
                    continue
            except OSError:
                continue
            skill_path = nested / "SKILL.md"
            if not skill_path.exists():
                continue
            skill = _skill_from_file(
                skill_path,
                source,
                fallback_name=nested.name,
                directory=directory_summary.name,
                directory_summary=directory_summary,
            )
            if skill is not None:
                results.append(skill)
    return results


def discover_skill_directories(cwd: str | Path) -> list[SkillDirectorySummary]:
    by_key: dict[str, SkillDirectorySummary] = {}
    for root, source in _skill_roots(cwd):
        if not root.exists():
            continue
        for entry in root.iterdir():
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            directory = _directory_from_file(entry / "SKILL_DIR.md", source)
            if directory is not None:
                by_key.setdefault(directory.name, directory)
    return list(by_key.values())


def discover_skills(cwd: str | Path) -> list[SkillSummary]:
    by_name: dict[str, LoadedSkill] = {}
    by_qualified_name: dict[str, LoadedSkill] = {}
    for root, source in _skill_roots(cwd):
        for skill in _list_skill_dirs(root, source):
            by_qualified_name.setdefault(skill.qualified_name or skill.name, skill)
            by_name.setdefault(skill.name, skill)

    ordered: list[LoadedSkill] = []
    seen: set[str] = set()
    for skill in [*by_qualified_name.values(), *by_name.values()]:
        key = skill.qualified_name or skill.name
        if key in seen:
            continue
        seen.add(key)
        ordered.append(skill)

    return [
        SkillSummary(
            name=skill.name,
            qualified_name=skill.qualified_name or skill.name,
            description=skill.description,
            path=skill.path,
            source=skill.source,
            directory=skill.directory,
            directory_description=skill.directory_description,
            domains=list(skill.domains),
            scopes=list(skill.scopes),
            tools=list(skill.tools),
            keywords=list(skill.keywords),
            examples=list(skill.examples),
            content_digest=hashlib.sha256(
                skill.content.encode("utf-8")
            ).hexdigest(),
        )
        for skill in ordered
    ]


_SKILL_NAME_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _is_safe_skill_name(name: str) -> bool:
    """Skill names are at most ``directory/skill`` with conservative segment
    characters — anything else (``..``, absolute paths, backslashes) could
    escape the skill roots when joined onto a path."""
    segments = name.split("/")
    if not 1 <= len(segments) <= 2:
        return False
    return all(
        _SKILL_NAME_SEGMENT_RE.fullmatch(segment) is not None
        and ".." not in segment
        for segment in segments
    )


def load_skill(cwd: str | Path, name: str) -> LoadedSkill | None:
    normalized_name = name.strip().strip("/")
    if not normalized_name or not _is_safe_skill_name(normalized_name):
        return None

    for root, source in _skill_roots(cwd):
        if "/" in normalized_name:
            directory, skill_name = normalized_name.split("/", 1)
            skill_path = root / directory / skill_name / "SKILL.md"
            directory_summary = _directory_from_file(root / directory / "SKILL_DIR.md", source)
            if skill_path.exists():
                return _skill_from_file(
                    skill_path,
                    source,
                    fallback_name=skill_name,
                    directory=directory,
                    directory_summary=directory_summary,
                )

        skill_path = root / normalized_name / "SKILL.md"
        if skill_path.exists():
            return _skill_from_file(skill_path, source, fallback_name=normalized_name)

        if root.exists():
            for entry in root.iterdir():
                try:
                    if not entry.is_dir() or not (entry / "SKILL_DIR.md").exists():
                        continue
                except OSError:
                    continue
                nested_path = entry / normalized_name / "SKILL.md"
                if nested_path.exists():
                    return _skill_from_file(
                        nested_path,
                        source,
                        fallback_name=normalized_name,
                        directory=entry.name,
                        directory_summary=_directory_from_file(entry / "SKILL_DIR.md", source),
                    )
    return None


def _managed_skill_root(scope: str, cwd: str | Path) -> Path:
    return (Path(cwd) / ".mini-code" / "skills") if scope == "project" else (_home_dir() / ".mini-code" / "skills")


def install_skill(cwd: str | Path, source_path: str, name: str | None = None, scope: str = "user") -> dict[str, str]:
    source = Path(source_path)
    if not source.is_absolute():
        source = Path(cwd) / source
    if source.is_dir():
        skill_file = source / "SKILL.md"
        inferred_name = source.name
    else:
        skill_file = source if source.name == "SKILL.md" else source / "SKILL.md"
        inferred_name = skill_file.parent.name
    if not skill_file.exists():
        raise RuntimeError(f"No SKILL.md found in {source}")

    skill_name = (name or inferred_name).strip()
    if not skill_name:
        raise RuntimeError("Skill name cannot be empty.")

    target_dir = _managed_skill_root(scope, cwd) / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_file, target_dir / "SKILL.md")
    return {"name": skill_name, "targetPath": str(target_dir / "SKILL.md")}


def remove_managed_skill(cwd: str | Path, name: str, scope: str = "user") -> dict[str, object]:
    target_path = _managed_skill_root(scope, cwd) / name
    if not target_path.exists():
        return {"removed": False, "targetPath": str(target_path)}
    shutil.rmtree(target_path)
    return {"removed": True, "targetPath": str(target_path)}
