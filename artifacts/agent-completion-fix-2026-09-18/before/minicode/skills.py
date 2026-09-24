from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from minicode.tooling import SkillLoadBinding


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
    purposes: list[str] = field(default_factory=list)
    supported_operations: list[str] = field(default_factory=list)
    required_operations: list[str] = field(default_factory=list)
    activation_terms: list[str] = field(default_factory=list)
    content_digest: str = ""
    routing_identity_digest: str = ""


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


def _safe_skill_file(root: Path, path: Path) -> Path | None:
    """Resolve one regular Skill file without following catalog symlinks.

    Skill roots are partly workspace-controlled.  Lexical ``..`` checks do
    not stop ``skills/demo -> /private/path``; reject symlinks below the root
    and then verify resolved containment before reading anything.
    """
    try:
        # The catalog root itself is untrusted too.  Anchoring only at
        # ``root.resolve()`` would bless ``workspace/.mini-code -> /outside``
        # (or a symlinked ``skills`` root) and make the containment check
        # tautological.  The two components managed by MiniCode must be real
        # directories below the caller's project/user anchor.
        if root.name != "skills" or root.parent.name not in {
            ".mini-code",
            ".claude",
        }:
            return None
        anchor = root.parent.parent
        for managed in (root.parent, root):
            if managed.is_symlink() or not managed.is_dir():
                return None
        resolved_anchor = anchor.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        expected_root = (
            resolved_anchor / root.parent.name / root.name
        )
        if resolved_root != expected_root:
            return None

        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _directory_from_file(
    path: Path,
    source: str,
    *,
    root: Path,
) -> SkillDirectorySummary | None:
    safe_path = _safe_skill_file(root, path)
    if safe_path is None:
        return None
    try:
        content = safe_path.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata, _ = parse_frontmatter(content)
    name = _sanitize_skill_identifier(
        str(metadata.get("name") or ""), safe_path.parent.name
    )
    if not name:
        return None
    return SkillDirectorySummary(
        name=name,
        description=extract_description(content),
        path=str(safe_path),
        source=source,
        domains=_as_list(metadata.get("domains")),
        scopes=_as_list(metadata.get("scopes")),
        keywords=_as_list(metadata.get("keywords")),
    )


def _skill_from_file(
    path: Path,
    source: str,
    *,
    root: Path,
    fallback_name: str,
    directory: str = "",
    directory_summary: SkillDirectorySummary | None = None,
) -> LoadedSkill | None:
    safe_path = _safe_skill_file(root, path)
    if safe_path is None:
        return None
    try:
        content = safe_path.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata, _ = parse_frontmatter(content)
    name = _sanitize_skill_identifier(
        str(metadata.get("name") or ""), fallback_name
    )
    if not name:
        return None
    skill_directory = _sanitize_skill_identifier(
        str(metadata.get("directory") or ""), directory
    )
    qualified_name = f"{skill_directory}/{name}" if skill_directory else name
    directory_description = directory_summary.description if directory_summary else ""
    domains = _as_list(metadata.get("domains")) or (directory_summary.domains if directory_summary else [])
    scopes = _as_list(metadata.get("scopes")) or (directory_summary.scopes if directory_summary else [])
    keywords = _as_list(metadata.get("keywords")) or (directory_summary.keywords if directory_summary else [])

    loaded = LoadedSkill(
        name=name,
        qualified_name=qualified_name,
        description=extract_description(content),
        path=str(safe_path),
        source=source,
        directory=skill_directory,
        directory_description=directory_description,
        domains=list(domains),
        scopes=list(scopes),
        tools=_as_list(metadata.get("tools")),
        keywords=list(keywords),
        examples=_as_list(metadata.get("examples")),
        purposes=_as_list(metadata.get("purposes")),
        supported_operations=_as_list(metadata.get("supported_operations")),
        required_operations=_as_list(metadata.get("required_operations")),
        activation_terms=_as_list(metadata.get("activation_terms")),
        content=content,
    )
    loaded.content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    loaded.routing_identity_digest = _routing_identity_digest(
        loaded,
        directory_summary=directory_summary,
    )
    return loaded


def _routing_identity_digest(
    skill: SkillSummary | LoadedSkill | Mapping[str, Any],
    *,
    directory_summary: SkillDirectorySummary | Mapping[str, Any] | None = None,
) -> str:
    """Return the stable identity of all inputs that can affect routing.

    ``content_digest`` remains the exact body binding used by ``load_skill``.
    This second digest additionally covers the final effective metadata after
    ``SKILL_DIR.md`` inheritance, so routing caches, version lineage, and
    feedback do not survive a semantic catalog change.
    """

    def value(name: str, default: Any = "") -> Any:
        if isinstance(skill, Mapping):
            return skill.get(name, default)
        return getattr(skill, name, default)

    def directory_value(name: str, default: Any = "") -> Any:
        if isinstance(directory_summary, Mapping):
            return directory_summary.get(name, default)
        return getattr(directory_summary, name, default)

    content_digest = str(value("content_digest"))
    if (
        directory_summary is None
        and re.fullmatch(r"[0-9a-f]{64}", content_digest) is not None
    ):
        # A direct Skill's frontmatter and body are already fully bound by its
        # content digest. Preserve that legacy identity; only inherited
        # directory metadata requires the composite projection below.
        return content_digest

    payload = {
        "schemaVersion": 1,
        "contentDigest": content_digest,
        "name": str(value("name")),
        "qualifiedName": str(value("qualified_name") or value("name")),
        "description": str(value("description")),
        "directory": str(value("directory")),
        "directoryDescription": str(value("directory_description")),
        "domains": [str(item) for item in (value("domains", []) or [])],
        "scopes": [str(item) for item in (value("scopes", []) or [])],
        "tools": [str(item) for item in (value("tools", []) or [])],
        "keywords": [str(item) for item in (value("keywords", []) or [])],
        "examples": [str(item) for item in (value("examples", []) or [])],
        "purposes": [str(item) for item in (value("purposes", []) or [])],
        "supportedOperations": [
            str(item)
            for item in (value("supported_operations", []) or [])
        ],
        "requiredOperations": [
            str(item)
            for item in (value("required_operations", []) or [])
        ],
        "activationTerms": [
            str(item) for item in (value("activation_terms", []) or [])
        ],
        "directoryMetadata": (
            {
                "name": str(directory_value("name")),
                "description": str(directory_value("description")),
                "domains": [
                    str(item)
                    for item in (directory_value("domains", []) or [])
                ],
                "scopes": [
                    str(item)
                    for item in (directory_value("scopes", []) or [])
                ],
                "keywords": [
                    str(item)
                    for item in (directory_value("keywords", []) or [])
                ],
            }
            if directory_summary is not None
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _list_skill_dirs(root: Path, source: str) -> list[LoadedSkill]:
    if not root.exists():
        return []
    results: list[LoadedSkill] = []
    for entry in root.iterdir():
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            # Windows: untrusted mount points, broken symlinks, etc.
            continue

        directory_summary = _directory_from_file(
            entry / "SKILL_DIR.md", source, root=root
        )
        direct_skill = entry / "SKILL.md"
        if _safe_skill_file(root, direct_skill) is not None:
            skill = _skill_from_file(
                direct_skill,
                source,
                root=root,
                fallback_name=entry.name,
            )
            if skill is not None:
                results.append(skill)

        if directory_summary is None:
            continue
        for nested in entry.iterdir():
            try:
                if nested.is_symlink() or not nested.is_dir():
                    continue
            except OSError:
                continue
            skill_path = nested / "SKILL.md"
            if _safe_skill_file(root, skill_path) is None:
                continue
            skill = _skill_from_file(
                skill_path,
                source,
                root=root,
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
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue
            directory = _directory_from_file(
                entry / "SKILL_DIR.md", source, root=root
            )
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
            purposes=list(skill.purposes),
            supported_operations=list(skill.supported_operations),
            required_operations=list(skill.required_operations),
            activation_terms=list(skill.activation_terms),
            content_digest=skill.content_digest,
            routing_identity_digest=skill.routing_identity_digest,
        )
        for skill in ordered
    ]


_SKILL_NAME_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Identifiers the SkillVersionLedger can represent (ASCII, no spaces). One
# non-conforming catalog name aborts the whole version observation, so
# untrusted frontmatter values are degraded instead of passed through.
_LEDGER_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def _sanitize_skill_identifier(value: str, fallback: str) -> str:
    candidate = value.strip() or fallback.strip()
    if not candidate:
        return ""
    if _LEDGER_SAFE_SEGMENT_RE.fullmatch(candidate):
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:12]
    return f"skill_{digest}"


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


def _summary_value(summary: SkillSummary | dict[str, Any], key: str) -> Any:
    if isinstance(summary, dict):
        return summary.get(key)
    return getattr(summary, key, None)


def resolve_skill_catalog_binding(
    name: str,
    catalog: list[SkillSummary | dict[str, Any]],
) -> SkillSummary | dict[str, Any] | None:
    """Resolve one canonical or unambiguous bare catalog identity."""
    normalized_name = name.strip().strip("/")
    if not normalized_name or not _is_safe_skill_name(normalized_name):
        return None
    exact = [
        item
        for item in catalog
        if str(_summary_value(item, "qualified_name") or "") == normalized_name
    ]
    if "/" in normalized_name:
        matches = exact
    else:
        # Direct ``foo`` and nested ``bundle/foo`` share a public bare name.
        # In that catalog, ``foo`` is ambiguous even though it is also the
        # direct Skill's qualified name; callers must remove the collision or
        # choose the nested binding explicitly as ``bundle/foo``.
        matches = [
            item
            for item in catalog
            if str(_summary_value(item, "name") or "") == normalized_name
        ]
    return matches[0] if len(matches) == 1 else None


def load_skill_from_catalog(
    cwd: str | Path,
    name: str,
    catalog: list[SkillSummary | dict[str, Any]],
    *,
    expected_binding: SkillLoadBinding | None = None,
) -> LoadedSkill | None:
    """Load the exact file/version advertised by one discovery snapshot."""
    binding = resolve_skill_catalog_binding(name, catalog)
    if binding is None:
        return None
    if expected_binding is not None:
        from minicode.tooling import skill_load_binding_from_value

        # Compare the complete route-time identity before opening SKILL.md.
        # A freshly assembled loader must not turn an old routing decision
        # into authority over a replacement body with the same public name.
        if skill_load_binding_from_value(binding) != expected_binding:
            return None
    source = str(_summary_value(binding, "source") or "")
    roots = {root_source: root for root, root_source in _skill_roots(cwd)}
    root = roots.get(source)
    if root is None:
        return None
    path = Path(str(_summary_value(binding, "path") or ""))
    safe_path = _safe_skill_file(root, path)
    if safe_path is None or str(safe_path) != str(path):
        return None
    try:
        content = safe_path.read_text(encoding="utf-8")
    except OSError:
        return None
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    expected_content_digest = (
        expected_binding.content_digest
        if expected_binding is not None
        else str(_summary_value(binding, "content_digest") or "")
    )
    if digest != expected_content_digest:
        return None
    loaded = LoadedSkill(
        name=str(_summary_value(binding, "name") or ""),
        qualified_name=str(_summary_value(binding, "qualified_name") or ""),
        description=str(_summary_value(binding, "description") or ""),
        path=str(safe_path),
        source=source,
        directory=str(_summary_value(binding, "directory") or ""),
        directory_description=str(
            _summary_value(binding, "directory_description") or ""
        ),
        domains=list(_summary_value(binding, "domains") or []),
        scopes=list(_summary_value(binding, "scopes") or []),
        tools=list(_summary_value(binding, "tools") or []),
        keywords=list(_summary_value(binding, "keywords") or []),
        examples=list(_summary_value(binding, "examples") or []),
        purposes=list(_summary_value(binding, "purposes") or []),
        supported_operations=list(
            _summary_value(binding, "supported_operations") or []
        ),
        required_operations=list(
            _summary_value(binding, "required_operations") or []
        ),
        activation_terms=list(
            _summary_value(binding, "activation_terms") or []
        ),
        content_digest=digest,
        routing_identity_digest=str(
            _summary_value(binding, "routing_identity_digest") or ""
        ),
        content=content,
    )
    if not loaded.routing_identity_digest:
        loaded.routing_identity_digest = _routing_identity_digest(loaded)
    return loaded


def load_skill(cwd: str | Path, name: str) -> LoadedSkill | None:
    return load_skill_from_catalog(cwd, name, discover_skills(cwd))


def _managed_skill_root(scope: str, cwd: str | Path) -> Path:
    if scope not in {"project", "user"}:
        raise RuntimeError("Invalid Skill scope: expected 'project' or 'user'.")
    anchor = Path(cwd) if scope == "project" else _home_dir()
    return anchor / ".mini-code" / "skills"


def _ensure_managed_skill_root(scope: str, cwd: str | Path) -> Path:
    root = _managed_skill_root(scope, cwd)
    anchor = root.parent.parent
    try:
        if anchor.is_symlink() or not anchor.is_dir():
            raise RuntimeError("Skill install anchor is not a safe directory.")
        for directory in (root.parent, root):
            if directory.is_symlink():
                raise RuntimeError(
                    "Skill install root cannot contain managed symlinks."
                )
            if directory.exists():
                if not directory.is_dir():
                    raise RuntimeError("Skill install root is not a directory.")
            else:
                directory.mkdir(mode=0o700)
    except OSError as error:
        raise RuntimeError("Skill install root is unavailable.") from error
    return root


def _ensure_skill_subdirectory(parent: Path, name: str) -> tuple[Path, bool]:
    directory = parent / name
    if directory.is_symlink():
        raise RuntimeError("Skill install path cannot contain symlinks.")
    if directory.exists():
        if not directory.is_dir():
            raise RuntimeError("Skill install path is not a directory.")
        return directory, False
    try:
        directory.mkdir(mode=0o700)
    except OSError as error:
        raise RuntimeError("Skill install directory is unavailable.") from error
    return directory, True


def _default_skill_directory_marker(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Skills in the {name} directory.\n"
        "---\n\n"
        f"# {name}\n"
    )


def _validate_skill_directory_marker(
    marker: Path,
    *,
    root: Path,
    source: str,
    expected_name: str,
) -> None:
    directory = _directory_from_file(marker, source, root=root)
    if directory is None or directory.name != expected_name:
        raise RuntimeError(
            "Invalid SKILL_DIR.md: its discovered name must match the "
            f"'{expected_name}' directory."
        )


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
    if not _is_safe_skill_name(skill_name):
        raise RuntimeError(
            "Invalid skill name: expected 'name' or 'directory/name' using "
            "letters, digits, '.', '_', '-' only."
        )

    root = _ensure_managed_skill_root(scope, cwd)
    source_name = "project" if scope == "project" else "user"
    segments = skill_name.split("/")
    created_directories: list[Path] = []
    parent = root
    marker: Path | None = None
    marker_created = False
    target_file: Path | None = None
    target_preexisted = False
    target_touched = False
    previous_content: bytes | None = None
    try:
        if len(segments) == 2:
            parent, created = _ensure_skill_subdirectory(parent, segments[0])
            if created:
                created_directories.append(parent)
            marker = parent / "SKILL_DIR.md"
            if marker.is_symlink():
                raise RuntimeError("SKILL_DIR.md cannot be a symlink.")
            if marker.exists():
                if not marker.is_file():
                    raise RuntimeError("SKILL_DIR.md is not a regular file.")
            else:
                marker.write_text(
                    _default_skill_directory_marker(segments[0]),
                    encoding="utf-8",
                )
                marker_created = True
            _validate_skill_directory_marker(
                marker,
                root=root,
                source=source_name,
                expected_name=segments[0],
            )

        target_dir, created = _ensure_skill_subdirectory(parent, segments[-1])
        if created:
            created_directories.append(target_dir)
        target_file = target_dir / "SKILL.md"
        if target_file.is_symlink():
            raise RuntimeError("SKILL.md cannot be a symlink.")
        if target_file.exists() and not target_file.is_file():
            raise RuntimeError("SKILL.md is not a regular file.")
        target_preexisted = target_file.exists()
        previous_content = target_file.read_bytes() if target_preexisted else None
        target_touched = True
        shutil.copyfile(skill_file, target_file)

        target_resolved = target_file.resolve(strict=True)
        discovered = [
            item
            for item in discover_skills(cwd)
            if item.source == source_name
            and item.qualified_name == skill_name
            and Path(item.path) == target_resolved
        ]
        if len(discovered) != 1:
            raise RuntimeError(
                "Installed Skill did not resolve to the requested catalog "
                f"identity '{skill_name}'."
            )
    except Exception:
        if target_touched and target_file is not None:
            try:
                if target_preexisted and previous_content is not None:
                    target_file.write_bytes(previous_content)
                else:
                    target_file.unlink(missing_ok=True)
            except OSError:
                pass
        if marker_created and marker is not None:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return {"name": skill_name, "targetPath": str(target_file)}


def remove_managed_skill(cwd: str | Path, name: str, scope: str = "user") -> dict[str, object]:
    normalized_name = name.strip()
    if not normalized_name or not _is_safe_skill_name(normalized_name):
        raise RuntimeError(
            "Invalid skill name: expected 'name' or 'directory/name' using "
            "letters, digits, '.', '_', '-' only."
        )
    root = _managed_skill_root(scope, cwd)
    target_path = root / normalized_name
    if not root.exists():
        return {"removed": False, "targetPath": str(target_path)}

    # Deletion must enforce the same managed-root boundary as installation.
    # Otherwise ``workspace/.mini-code -> /outside`` would turn a project Skill
    # removal into an out-of-workspace recursive delete.
    root = _ensure_managed_skill_root(scope, cwd)
    target_path = root
    for segment in normalized_name.split("/"):
        target_path = target_path / segment
        if target_path.is_symlink():
            raise RuntimeError("Skill removal path cannot contain symlinks.")
        if not target_path.exists():
            return {"removed": False, "targetPath": str(target_path)}
        if not target_path.is_dir():
            raise RuntimeError("Skill removal target is not a directory.")
    shutil.rmtree(target_path)
    return {"removed": True, "targetPath": str(target_path)}
