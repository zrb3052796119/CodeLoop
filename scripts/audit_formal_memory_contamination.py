#!/usr/bin/env python3
"""Read-only formal-memory contamination inventory and recovery-plan generator."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "formal-memory-contamination-inventory-v1"
MAX_INPUT_BYTES = 64 * 1024 * 1024
TEST_WINDOW_RADIUS_SECONDS = 120.0
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|authorization|secret|token)\b\s*[:=]\s*[\"']?(?!\[REDACTED\])[A-Za-z0-9_./+\-=]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_./+\-=]{8,}"),
    re.compile(r"\bsk-(?:live|prod|ant|or)-[A-Za-z0-9_-]{8,}"),
)


@dataclass(frozen=True)
class StaticMemoryFixture:
    content: str
    category: str
    tags: tuple[str, ...]
    source_file: str
    source_line: int


@dataclass(frozen=True)
class AuditInputs:
    real_home: Path
    project_root: Path
    phase1_artifact: Path
    output_path: Path
    markdown_path: Path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"audit input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse audit input: {path}") from exc


def _snapshot(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        absolute = path.expanduser().absolute()
        if not absolute.exists():
            result[str(absolute)] = {"exists": False, "sha256": None, "size": None, "mtime_ns": None}
            continue
        metadata = absolute.stat()
        result[str(absolute)] = {
            "exists": True,
            "sha256": _sha256_file(absolute),
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
    return result


def output_contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _literal(node: ast.AST | None, default: Any = None) -> Any:
    if node is None:
        return default
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return default


def _is_user_scope(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr.upper() == "USER"
    value = _literal(node)
    return isinstance(value, str) and value.lower() == "user"


def extract_static_memory_fixtures(project_root: Path) -> list[StaticMemoryFixture]:
    fixtures: dict[tuple[str, str, tuple[str, ...]], StaticMemoryFixture] = {}
    tests_root = project_root / "tests"
    if not tests_root.exists():
        return []
    for path in sorted(tests_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        relative = path.relative_to(project_root).as_posix()
        for node in ast.walk(tree):
            category: Any = None
            content: Any = None
            tags: Any = []
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "add_entry" and len(node.args) >= 3 and _is_user_scope(node.args[0]):
                    category = _literal(node.args[1])
                    content = _literal(node.args[2])
                    tags = _literal(node.args[3], []) if len(node.args) >= 4 else []
                elif node.func.attr == "MemoryEntry" or node.func.attr == "memory_entry":
                    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
                    if _is_user_scope(keywords.get("scope")):
                        category = _literal(keywords.get("category"))
                        content = _literal(keywords.get("content"))
                        tags = _literal(keywords.get("tags"), [])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "MemoryEntry":
                keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
                if _is_user_scope(keywords.get("scope")):
                    category = _literal(keywords.get("category"))
                    content = _literal(keywords.get("content"))
                    tags = _literal(keywords.get("tags"), [])
            elif isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 3 and _is_user_scope(node.elts[0]):
                category = _literal(node.elts[1])
                content = _literal(node.elts[2])
                tags = _literal(node.elts[3], []) if len(node.elts) >= 4 else []
            if not isinstance(category, str) or not isinstance(content, str):
                continue
            normalized_tags = tuple(str(tag) for tag in tags) if isinstance(tags, (list, tuple)) else ()
            key = (content, category, normalized_tags)
            fixtures.setdefault(
                key,
                StaticMemoryFixture(
                    content=content,
                    category=category,
                    tags=normalized_tags,
                    source_file=relative,
                    source_line=getattr(node, "lineno", 0),
                ),
            )
    return sorted(fixtures.values(), key=lambda item: (item.content, item.category, item.source_file))


def _known_window(phase1_artifact: dict[str, Any]) -> tuple[float, float] | None:
    metadata = (
        phase1_artifact.get("formal_memory_snapshot_after", {})
        .get("user/memory.json", {})
    )
    mtime_ns = metadata.get("mtime_ns")
    if not isinstance(mtime_ns, (int, float)):
        return None
    center = float(mtime_ns) / 1_000_000_000
    return center - TEST_WINDOW_RADIUS_SECONDS, center + TEST_WINDOW_RADIUS_SECONDS


def _contains_test_marker(value: Any, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if isinstance(value, str):
        lowered = value.lower()
        return "pytest" in lowered or lowered.startswith("test-") or "test_fixture" in lowered
    if isinstance(value, dict):
        return any(_contains_test_marker(item, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_test_marker(item, depth + 1) for item in value)
    return False


def classify_memory_entry(
    entry: dict[str, Any],
    *,
    fixtures: list[StaticMemoryFixture],
    approval_records: list[dict[str, Any]],
    known_window: tuple[float, float] | None,
) -> dict[str, Any]:
    content = str(entry.get("content", ""))
    content_hash = _sha256_text(content)
    tags = tuple(str(tag) for tag in entry.get("tags", []) if isinstance(tag, (str, int, float)))
    matching = [fixture for fixture in fixtures if fixture.content == content]
    evidence: set[str] = set()
    groups: set[str] = set()
    if matching:
        evidence.add("fixture_content_exact")
        groups.add("static_fixture")
        if any(fixture.category == str(entry.get("category", "")) and fixture.tags == tags for fixture in matching):
            evidence.add("fixture_shape_exact")
    created_at = entry.get("created_at")
    updated_at = entry.get("updated_at")
    if known_window and any(
        isinstance(value, (int, float)) and known_window[0] <= float(value) <= known_window[1]
        for value in (created_at, updated_at)
    ):
        evidence.add("phase1_test_window")
        groups.add("test_window")
    for record in approval_records:
        if record.get("entry_id") != entry.get("id"):
            continue
        evidence.add("approval_entry_id_match")
        groups.add("approval_audit")
        if record.get("content_hash") == content_hash:
            evidence.add("approval_entry_hash_match")
    if _contains_test_marker(entry.get("source")):
        evidence.add("source_test_marker")
        groups.add("source_provenance")
    if _contains_test_marker(entry.get("provenance")):
        evidence.add("provenance_test_marker")
        groups.add("source_provenance")

    if len(groups) >= 2:
        classification = "confirmed_test_artifact"
        confidence = 0.99 if len(groups) >= 3 else 0.95
        proposed_action = "remove_memory_entry"
    elif groups and groups != {"static_fixture"}:
        classification = "probable_test_artifact"
        confidence = 0.7
        proposed_action = "manual_review"
    elif groups == {"static_fixture"}:
        classification = "ambiguous"
        confidence = 0.4
        proposed_action = "manual_review"
    else:
        classification = "protected_user_data"
        confidence = 0.0
        proposed_action = "no_action"

    result: dict[str, Any] = {
        "record_type": "memory_entry",
        "entry_id": str(entry.get("id", "")),
        "scope": str(entry.get("scope", "user")),
        "category": str(entry.get("category", "")),
        "content_sha256": content_hash,
        "content_length": len(content),
        "classification": classification,
        "confidence": confidence,
        "evidence_codes": sorted(evidence),
        "created_at": created_at,
        "updated_at": updated_at,
        "proposed_action": proposed_action,
        "requires_user_approval": True,
    }
    if matching:
        result["preview"] = content[:80]
    return result


def _extract_static_session_ids(project_root: Path) -> set[str]:
    result: set[str] = set()
    tests_root = project_root / "tests"
    if not tests_root.exists():
        return result
    for path in tests_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value.startswith("test-") and len(value) <= 128:
                    result.add(value)
    return result


def _workspace_type(value: Any) -> str:
    workspace = str(value or "").lower().replace("\\", "/")
    if "pytest-" in workspace or "/pytest/" in workspace or "pytest-of-" in workspace:
        return "pytest_tmp"
    if workspace.startswith("/tmp/test") or "/tmp/test" in workspace:
        return "temporary_test"
    if workspace:
        return "non_test_path"
    return "missing"


def _classify_session(
    session_id: str,
    metadata: dict[str, Any],
    *,
    static_session_ids: set[str],
    known_window: tuple[float, float] | None,
) -> dict[str, Any]:
    evidence: set[str] = set()
    groups: set[str] = set()
    if session_id in static_session_ids:
        evidence.add("fixture_session_id_exact")
        groups.add("test_identity")
    if re.match(r"^(?:test|pytest)[-_]", session_id, re.IGNORECASE):
        evidence.add("test_session_id_pattern")
        groups.add("test_identity")
    workspace_type = _workspace_type(metadata.get("workspace"))
    if workspace_type in {"pytest_tmp", "temporary_test"}:
        evidence.add("pytest_workspace")
        groups.add("test_workspace")
    if known_window and any(
        isinstance(value, (int, float)) and known_window[0] <= float(value) <= known_window[1]
        for value in (metadata.get("created_at"), metadata.get("updated_at"))
    ):
        evidence.add("phase1_test_window")
        groups.add("test_window")

    if len(groups) >= 2:
        classification = "confirmed_test_artifact"
        confidence = 0.99 if len(groups) >= 3 else 0.95
        action = "remove_confirmed_test_session_index_record"
    elif groups:
        classification = "probable_test_artifact"
        confidence = 0.7
        action = "manual_review"
    else:
        classification = "protected_user_data"
        confidence = 0.0
        action = "no_action"
    return {
        "record_type": "session",
        "session_id_sha256": _sha256_text(session_id),
        "workspace_type": workspace_type,
        "classification": classification,
        "confidence": confidence,
        "evidence_codes": sorted(evidence),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "proposed_action": action,
        "requires_user_approval": True,
    }


def _build_recovery_plan(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    confirmed_memory = False
    for index, item in enumerate(inventory, start=1):
        action = str(item["proposed_action"])
        if action == "remove_memory_entry":
            confirmed_memory = True
        target_ref = item.get("entry_id") or item.get("session_id_sha256")
        actions.append(
            {
                "action_id": f"proposal-{index:04d}",
                "target_type": item["record_type"],
                "target_ref": target_ref,
                "proposed_action": action,
                "approved": False,
                "requires_user_approval": True,
            }
        )
    if confirmed_memory:
        for action in (
            "regenerate_derived_markdown_after_approved_removal",
            "retain_audit_history_and_append_cleanup_event",
        ):
            actions.append(
                {
                    "action_id": f"proposal-{len(actions) + 1:04d}",
                    "target_type": "memory_store",
                    "target_ref": "user-memory",
                    "proposed_action": action,
                    "approved": False,
                    "requires_user_approval": True,
                }
            )
    return actions


def _render_markdown(report: dict[str, Any]) -> str:
    counts = report["classification_counts"]
    action_counts = Counter(
        item["proposed_action"] for item in report["proposed_recovery_plan"]
    )
    lines = [
        "# Formal Memory Contamination Audit",
        "",
        "> Read-only inventory. No deletion, restore, approval change, or Markdown regeneration was executed.",
        "",
        "## Safety",
        "",
        f"- Dry run: `{str(report['dry_run']).lower()}`",
        f"- Formal files unchanged: `{str(report['formal_files_unchanged']).lower()}`",
        f"- Remote calls: {report['remote_call_count']}",
        "- Full memory content, conversation text, provenance values, and credentials are excluded.",
        "",
        "## Classification",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]
    for classification in (
        "confirmed_test_artifact",
        "probable_test_artifact",
        "ambiguous",
        "protected_user_data",
    ):
        lines.append(f"| {classification} | {counts.get(classification, 0)} |")
    lines.extend(["", "## Proposed Recovery Plan", "", "All actions remain `approved=false`.", ""])
    for action, count in sorted(action_counts.items()):
        lines.append(f"- `{action}`: {count}")
    lines.extend(
        [
            "",
            "## Recovery Boundary",
            "",
            "Because no pre-contamination byte copy exists, a future approved operation can only perform auditable logical cleanup; it cannot guarantee byte-for-byte restoration.",
            "Approval audit history must be retained and extended with cleanup events. Derived Markdown may be regenerated only after approved JSON entry removal.",
            "",
        ]
    )
    return "\n".join(lines)


def audit_formal_state(inputs: AuditInputs) -> dict[str, Any]:
    home = inputs.real_home.expanduser().resolve()
    project_root = inputs.project_root.resolve()
    mini_code = home / ".mini-code"
    memory_path = mini_code / "memory" / "memory.json"
    markdown_path = mini_code / "memory" / "MEMORY.md"
    approval_path = mini_code / "memory" / "approval_audit.json"
    sessions_path = mini_code / "sessions_index.json"
    formal_paths = [memory_path, markdown_path, approval_path, sessions_path]
    before = _snapshot(formal_paths)

    memory_data = _read_json(memory_path, {"entries": []})
    approval_data = _read_json(approval_path, {"records": []})
    sessions_data = _read_json(sessions_path, {})
    phase1_data = _read_json(inputs.phase1_artifact, {})
    fixtures = extract_static_memory_fixtures(project_root)
    window = _known_window(phase1_data)
    approval_records = [
        record for record in approval_data.get("records", []) if isinstance(record, dict)
    ] if isinstance(approval_data, dict) else []
    entries = memory_data.get("entries", []) if isinstance(memory_data, dict) else []
    inventory = [
        classify_memory_entry(
            entry,
            fixtures=fixtures,
            approval_records=approval_records,
            known_window=window,
        )
        for entry in entries
        if isinstance(entry, dict)
    ]
    static_session_ids = _extract_static_session_ids(project_root)
    if isinstance(sessions_data, dict):
        for session_id, metadata in sorted(sessions_data.items()):
            if isinstance(metadata, dict):
                inventory.append(
                    _classify_session(
                        str(session_id),
                        metadata,
                        static_session_ids=static_session_ids,
                        known_window=window,
                    )
                )
    inventory.sort(
        key=lambda item: (
            str(item["record_type"]),
            str(item.get("entry_id") or item.get("session_id_sha256")),
        )
    )
    after = _snapshot(formal_paths)
    counts = Counter(item["classification"] for item in inventory)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "remote_call_count": 0,
        "formal_files_before": before,
        "formal_files_after": after,
        "formal_files_unchanged": before == after,
        "source_counts": {
            "memory_entries": sum(item["record_type"] == "memory_entry" for item in inventory),
            "session_records": sum(item["record_type"] == "session" for item in inventory),
            "approval_records_used_as_evidence": len(approval_records),
            "static_memory_fixtures": len(fixtures),
        },
        "classification_counts": {
            name: counts.get(name, 0)
            for name in (
                "confirmed_test_artifact",
                "probable_test_artifact",
                "ambiguous",
                "protected_user_data",
            )
        },
        "inventory": inventory,
        "proposed_recovery_plan": _build_recovery_plan(inventory),
        "limitations": [
            "This is a read-only evidence inventory and performs no cleanup.",
            "No pre-contamination byte copy exists, so byte-for-byte recovery is impossible.",
            "Probable and ambiguous records require manual review.",
            "Approval audit history must be retained; future cleanup appends an event.",
        ],
    }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown = _render_markdown(report)
    if output_contains_secret(serialized) or output_contains_secret(markdown):
        raise ValueError("privacy scan rejected generated audit output")
    inputs.output_path.parent.mkdir(parents=True, exist_ok=True)
    inputs.markdown_path.parent.mkdir(parents=True, exist_ok=True)
    inputs.output_path.write_text(serialized, encoding="utf-8")
    inputs.markdown_path.write_text(markdown, encoding="utf-8")
    if _snapshot(formal_paths) != before:
        raise RuntimeError("formal files changed while generating read-only audit")
    return report


def build_argument_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-home", type=Path, default=Path.home())
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument(
        "--phase1-artifact",
        type=Path,
        default=project_root / "artifacts" / "memory-retrieval-baseline.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "artifacts" / "formal-memory-contamination-inventory.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=project_root / "docs" / "formal-memory-contamination-audit.md",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    report = audit_formal_state(
        AuditInputs(
            real_home=args.real_home,
            project_root=args.project_root,
            phase1_artifact=args.phase1_artifact,
            output_path=args.output,
            markdown_path=args.markdown,
        )
    )
    print(
        json.dumps(
            {
                "dry_run": report["dry_run"],
                "formal_files_unchanged": report["formal_files_unchanged"],
                "classification_counts": report["classification_counts"],
                "proposed_actions": len(report["proposed_recovery_plan"]),
            },
            sort_keys=True,
        )
    )
    return 0 if report["formal_files_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
