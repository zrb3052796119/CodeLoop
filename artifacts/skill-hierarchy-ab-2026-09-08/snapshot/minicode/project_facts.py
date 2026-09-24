"""Deterministic project facts observed from execution traces.

Project facts (confirmed dependencies, build commands) are not lessons: they
are stable, re-derivable truths about the workspace. Persisting them as
Memory entries mixed lessons with inventory and pushed low-information
claims through the approval queue. This store keeps them separate:

- written as plain JSON under ``.mini-code-memory/project_facts.json``;
- guarded by the same cooperative store lock as the Memory store;
- injected into prompts as a short static block, no approval needed.

Only facts whose truth is deterministic (read from manifests/imports) belong
here. Anything requiring interpretation stays in the reflection pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from minicode.memory import assess_memory_safety, sanitize_for_persistence
from minicode.memory_store import MemoryStoreConflict, MemoryStoreCoordinator
from minicode.memory_write_contract import ProjectFactsEffect

logger = logging.getLogger(__name__)

MAX_FACTS = 200
MAX_NAME_CHARS = 120
RENDER_MAX_BYTES = 2_000
MAX_WRITE_RECEIPTS = 128

_DEPENDENCY_NAME_RE = re.compile(
    r"^@?[A-Za-z0-9][A-Za-z0-9._+\-]*"
    r"(?:(?:[/@:])[A-Za-z0-9][A-Za-z0-9._+\-]*){0,7}$"
)
_MEMORY_OPERATION_ID_RE = re.compile(r"^memoryop_[0-9a-f]{32}$")


def project_facts_payload_digest(
    names: list[str],
    provenance: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "schema": "project_facts_write_v1",
            "dependencies": names,
            "provenance": provenance,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectFactsWritePlan:
    """Sanitized deterministic input to one Project Facts observation."""

    names: tuple[str, ...]
    provenance: dict[str, Any]
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ProjectFactsOperationReceipt:
    """Content-free proof retained for one idempotent facts write."""

    operation_id: str
    payload_digest: str


def prepare_project_facts_write(
    names: list[str],
    provenance: dict[str, Any] | None = None,
) -> ProjectFactsWritePlan | None:
    """Validate, sanitize and bind one dependency observation."""
    validated: list[str] = []
    for name in names:
        if not str(name or "").strip():
            continue
        safe_name = _safe_dependency_name(name)
        if not safe_name:
            return None
        validated.append(safe_name)
    clean = sorted(set(validated))
    if not clean:
        return None
    sanitized_provenance = sanitize_for_persistence(dict(provenance or {}))
    safe_provenance = (
        sanitized_provenance
        if isinstance(sanitized_provenance, dict)
        else {}
    )
    return ProjectFactsWritePlan(
        names=tuple(clean),
        provenance=safe_provenance,
        payload_digest=project_facts_payload_digest(clean, safe_provenance),
    )


def _validated_write_receipts(data: dict[str, Any]) -> list[dict[str, str]]:
    raw_receipts = data.get("write_receipts", [])
    if not isinstance(raw_receipts, list) or len(raw_receipts) > MAX_WRITE_RECEIPTS:
        raise MemoryStoreConflict("Project Facts write receipts are invalid")
    receipts: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_receipts:
        if not isinstance(raw, dict) or set(raw) != {
            "operation_id",
            "payload_digest",
        }:
            raise MemoryStoreConflict("Project Facts write receipt is invalid")
        operation_id = raw.get("operation_id")
        payload_digest = raw.get("payload_digest")
        if (
            not isinstance(operation_id, str)
            or _MEMORY_OPERATION_ID_RE.fullmatch(operation_id) is None
            or not isinstance(payload_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", payload_digest) is None
            or operation_id in seen
        ):
            raise MemoryStoreConflict("Project Facts write receipt is invalid")
        seen.add(operation_id)
        receipts.append(
            {
                "operation_id": operation_id,
                "payload_digest": payload_digest,
            }
        )
    return receipts


def _safe_dependency_name(value: Any) -> str:
    """Return one prompt-safe dependency identifier or fail closed."""
    name = str(sanitize_for_persistence(str(value or ""))).strip()[:MAX_NAME_CHARS]
    if (
        not name
        or "[REDACTED" in name
        or _DEPENDENCY_NAME_RE.fullmatch(name) is None
        or not assess_memory_safety(name, source="project_fact").allowed
    ):
        return ""
    return name


@dataclass
class ProjectFact:
    kind: str  # "dependency" | "build_command" (extensible)
    name: str
    first_seen: float
    last_seen: float
    occurrences: int = 1
    status: str = "active"
    provenance: list[dict[str, Any]] = field(default_factory=list)
    retracted_at: float | None = None
    retraction_reason: str = ""


class ProjectFactsStore:
    """Small append/merge store for deterministic project facts."""

    def __init__(self, workspace: str | Path, *, timeout: float = 5.0) -> None:
        self._root = Path(workspace) / ".mini-code-memory"
        self._path = self._root / "project_facts.json"
        self._coordinator = MemoryStoreCoordinator(self._root, timeout=timeout)

    # ── Read ───────────────────────────────────────────────────────

    def snapshot(self, *, strict: bool = False) -> dict[str, ProjectFact]:
        """Return the current facts keyed by ``kind:name`` (best effort)."""
        data = self._load(strict=strict)
        facts: dict[str, ProjectFact] = {}
        for raw in data.get("facts", []):
            if not isinstance(raw, dict):
                continue
            sanitized_raw = sanitize_for_persistence(raw)
            if not isinstance(sanitized_raw, dict):
                continue
            raw = sanitized_raw
            kind = str(raw.get("kind", "")).strip()[:40]
            name = (
                _safe_dependency_name(raw.get("name", ""))
                if kind == "dependency"
                else str(raw.get("name", "")).strip()[:MAX_NAME_CHARS]
            )
            if not kind or not name:
                continue
            try:
                fact = ProjectFact(
                    kind=kind,
                    name=name,
                    first_seen=float(raw.get("first_seen", 0.0) or 0.0),
                    last_seen=float(raw.get("last_seen", 0.0) or 0.0),
                    occurrences=max(1, int(raw.get("occurrences", 1) or 1)),
                    status=(
                        str(raw.get("status", "active"))
                        if str(raw.get("status", "active")) in {"active", "retracted"}
                        else "retracted"
                    ),
                    provenance=[
                        dict(item)
                        for item in raw.get("provenance", [])[:16]
                        if isinstance(item, dict)
                    ] if isinstance(raw.get("provenance", []), list) else [],
                    retracted_at=(
                        float(raw["retracted_at"])
                        if raw.get("retracted_at") is not None
                        else None
                    ),
                    retraction_reason=str(raw.get("retraction_reason", ""))[:240],
                )
            except (TypeError, ValueError):
                continue
            facts[f"{kind}:{name}"] = fact
        return facts

    def render_markdown(self, max_bytes: int = RENDER_MAX_BYTES) -> str:
        """Render a bounded, stable-ordered prompt block."""
        facts = self.snapshot()
        if not facts:
            return ""
        dependencies = sorted(
            (
                fact.name
                for fact in facts.values()
                if fact.kind == "dependency" and fact.status == "active"
            )
        )
        lines: list[str] = []
        if dependencies:
            shown = ", ".join(dependencies[:60])
            if len(dependencies) > 60:
                shown += f", (+{len(dependencies) - 60} more)"
            lines.append(f"- Confirmed dependencies: {shown}")
        if not lines:
            return ""
        block = "## Project Facts\n\n" + "\n".join(lines)
        if len(block.encode("utf-8")) <= max_bytes:
            return block
        return (
            "## Project Facts\n\n"
            f"- Confirmed dependencies: {len(dependencies)} tracked in "
            f"{self._path.name}"
        )

    # ── Write ──────────────────────────────────────────────────────

    @contextmanager
    def operation_receipt_guard(
        self,
        operation_id: str,
    ) -> Iterator[ProjectFactsOperationReceipt | None]:
        """Hold Project Facts authority while exposing one strict receipt.

        The caller must acquire the canonical Memory lock before entering this
        guard.  Keeping both locks until a legacy Pipeline envelope is written
        removes the inspect-then-bind race without pretending the two authority
        files form one transaction: this context never writes Project Facts.
        """
        if (
            not isinstance(operation_id, str)
            or _MEMORY_OPERATION_ID_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        with self._coordinator.transaction():
            receipts = _validated_write_receipts(self._load(strict=True))
            prior = next(
                (
                    receipt
                    for receipt in receipts
                    if receipt["operation_id"] == operation_id
                ),
                None,
            )
            yield (
                None
                if prior is None
                else ProjectFactsOperationReceipt(
                    operation_id=prior["operation_id"],
                    payload_digest=prior["payload_digest"],
                )
            )

    def observe_dependencies(
        self,
        names: list[str],
        *,
        provenance: dict[str, Any] | None = None,
    ) -> int:
        """Merge newly confirmed dependency names; return added count."""
        prepared = prepare_project_facts_write(names, provenance)
        if prepared is None:
            return 0
        clean = list(prepared.names)
        safe_provenance = prepared.provenance
        try:
            with self._coordinator.transaction():
                receipts = _validated_write_receipts(self._load(strict=True))
                facts = self.snapshot(strict=True)
                self._require_capacity(facts, clean)
                added, _updated = self._merge_dependencies(
                    facts,
                    clean,
                    safe_provenance,
                )
                self._save(facts, write_receipts=receipts)
        except Exception as exc:  # noqa: BLE001 - facts are advisory
            logger.warning("ProjectFactsStore: observe failed safely: %s", exc)
            return 0
        return added

    def observe_dependencies_once(
        self,
        names: list[str],
        *,
        operation_id: str,
        provenance: dict[str, Any] | None = None,
    ) -> ProjectFactsEffect:
        """Merge once within the retained 128-operation replay window."""
        if (
            not isinstance(operation_id, str)
            or _MEMORY_OPERATION_ID_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        prepared = prepare_project_facts_write(names, provenance)
        if prepared is None:
            return ProjectFactsEffect(status="skipped")
        clean = list(prepared.names)
        safe_provenance = prepared.provenance
        payload_digest = prepared.payload_digest

        with self._coordinator.transaction():
            receipts = _validated_write_receipts(self._load(strict=True))
            prior = next(
                (
                    receipt
                    for receipt in receipts
                    if receipt["operation_id"] == operation_id
                ),
                None,
            )
            if prior is not None:
                if prior["payload_digest"] != payload_digest:
                    raise MemoryStoreConflict(
                        "Project Facts operation payload changed"
                    )
                return ProjectFactsEffect(status="replayed")

            facts = self.snapshot(strict=True)
            self._require_capacity(facts, clean)
            added, updated = self._merge_dependencies(
                facts,
                clean,
                safe_provenance,
            )
            receipts.append(
                {
                    "operation_id": operation_id,
                    "payload_digest": payload_digest,
                }
            )
            self._save(
                facts,
                write_receipts=receipts[-MAX_WRITE_RECEIPTS:],
            )
        return ProjectFactsEffect(
            status="committed",
            added_count=added,
            updated_count=updated,
        )

    @staticmethod
    def _merge_dependencies(
        facts: dict[str, ProjectFact],
        names: list[str],
        provenance: dict[str, Any],
    ) -> tuple[int, int]:
        now = time.time()
        added = 0
        updated = 0
        for name in names:
            key = f"dependency:{name}"
            existing = facts.get(key)
            if existing is None:
                facts[key] = ProjectFact(
                    kind="dependency",
                    name=name,
                    first_seen=now,
                    last_seen=now,
                    provenance=[provenance] if provenance else [],
                )
                added += 1
                continue
            existing.last_seen = now
            existing.occurrences += 1
            existing.status = "active"
            existing.retracted_at = None
            existing.retraction_reason = ""
            if provenance:
                existing.provenance = (
                    existing.provenance + [provenance]
                )[-16:]
            updated += 1
        return added, updated

    @staticmethod
    def _require_capacity(
        facts: dict[str, ProjectFact],
        names: list[str],
    ) -> None:
        resulting_keys = set(facts)
        resulting_keys.update(f"dependency:{name}" for name in names)
        if len(facts) > MAX_FACTS or len(resulting_keys) > MAX_FACTS:
            raise MemoryStoreConflict("Project Facts capacity exceeded")

    def retract_dependency(
        self,
        name: str,
        *,
        reason: str,
        provenance: dict[str, Any] | None = None,
    ) -> bool:
        """Retract a fact while retaining an auditable tombstone."""
        normalized = str(name or "").strip()[:MAX_NAME_CHARS]
        if not normalized:
            return False
        sanitized_provenance = sanitize_for_persistence(dict(provenance or {}))
        safe_provenance = (
            sanitized_provenance
            if isinstance(sanitized_provenance, dict)
            else {}
        )
        with self._coordinator.transaction():
            facts = self.snapshot(strict=True)
            fact = facts.get(f"dependency:{normalized}")
            if fact is None:
                return False
            fact.status = "retracted"
            fact.retracted_at = time.time()
            fact.retraction_reason = str(reason or "retracted")[:240]
            if safe_provenance:
                fact.provenance = (fact.provenance + [safe_provenance])[-16:]
            self._save(facts)
        return True

    # ── Storage ────────────────────────────────────────────────────

    def _load(self, *, strict: bool = False) -> dict[str, Any]:
        if self._root.is_symlink() or self._path.is_symlink():
            if strict:
                raise MemoryStoreConflict("Project Facts path is unsafe")
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
            if strict:
                raise MemoryStoreConflict("Project Facts authority is invalid")
            return {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            if strict:
                raise MemoryStoreConflict(
                    "Project Facts authority is unreadable"
                ) from error
            return {}

    def _save(
        self,
        facts: dict[str, ProjectFact],
        *,
        write_receipts: list[dict[str, str]] | None = None,
    ) -> None:
        if self._root.is_symlink() or self._path.is_symlink():
            raise OSError("project facts path is a symbolic link")
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._root.is_symlink() or self._path.is_symlink():
            raise OSError("project facts path is a symbolic link")
        ordered = sorted(facts.values(), key=lambda fact: (fact.kind, fact.name))
        if len(facts) > MAX_FACTS:
            raise MemoryStoreConflict("Project Facts capacity exceeded")
        receipts = (
            _validated_write_receipts(self._load(strict=True))
            if write_receipts is None
            else write_receipts
        )
        sanitized_payload = sanitize_for_persistence(
            {
                "last_updated": time.time(),
                "facts": [asdict(fact) for fact in ordered],
                "write_receipts": receipts[-MAX_WRITE_RECEIPTS:],
            }
        )
        payload = json.dumps(
            sanitized_payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        descriptor, temporary = tempfile.mkstemp(
            dir=str(self._root), prefix=".project-facts-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        except OSError:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


__all__ = [
    "ProjectFact",
    "ProjectFactsOperationReceipt",
    "ProjectFactsStore",
    "ProjectFactsWritePlan",
    "prepare_project_facts_write",
    "project_facts_payload_digest",
]
