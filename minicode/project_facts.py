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

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from minicode.memory_store import MemoryStoreCoordinator

logger = logging.getLogger(__name__)

MAX_FACTS = 200
MAX_NAME_CHARS = 120
RENDER_MAX_BYTES = 2_000


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

    def snapshot(self) -> dict[str, ProjectFact]:
        """Return the current facts keyed by ``kind:name`` (best effort)."""
        data = self._load()
        facts: dict[str, ProjectFact] = {}
        for raw in data.get("facts", []):
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).strip()[:40]
            name = str(raw.get("name", "")).strip()[:MAX_NAME_CHARS]
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

    def observe_dependencies(
        self,
        names: list[str],
        *,
        provenance: dict[str, Any] | None = None,
    ) -> int:
        """Merge newly confirmed dependency names; return added count."""
        clean = sorted(
            {
                str(name).strip()[:MAX_NAME_CHARS]
                for name in names
                if str(name or "").strip()
            }
        )
        if not clean:
            return 0
        now = time.time()
        added = 0
        try:
            with self._coordinator.transaction():
                facts = self.snapshot()
                for name in clean:
                    key = f"dependency:{name}"
                    existing = facts.get(key)
                    if existing is None:
                        facts[key] = ProjectFact(
                            kind="dependency", name=name,
                            first_seen=now, last_seen=now,
                            provenance=[dict(provenance or {})] if provenance else [],
                        )
                        added += 1
                    else:
                        existing.last_seen = now
                        existing.occurrences += 1
                        existing.status = "active"
                        existing.retracted_at = None
                        existing.retraction_reason = ""
                        if provenance:
                            existing.provenance = (
                                existing.provenance + [dict(provenance)]
                            )[-16:]
                self._save(facts)
        except Exception as exc:  # noqa: BLE001 - facts are advisory
            logger.warning("ProjectFactsStore: observe failed safely: %s", exc)
            return 0
        return added

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
        with self._coordinator.transaction():
            facts = self.snapshot()
            fact = facts.get(f"dependency:{normalized}")
            if fact is None:
                return False
            fact.status = "retracted"
            fact.retracted_at = time.time()
            fact.retraction_reason = str(reason or "retracted")[:240]
            if provenance:
                fact.provenance = (fact.provenance + [dict(provenance)])[-16:]
            self._save(facts)
        return True

    # ── Storage ────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._root.is_symlink() or self._path.is_symlink():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}

    def _save(self, facts: dict[str, ProjectFact]) -> None:
        if self._root.is_symlink() or self._path.is_symlink():
            raise OSError("project facts path is a symbolic link")
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._root.is_symlink() or self._path.is_symlink():
            raise OSError("project facts path is a symbolic link")
        ordered = sorted(facts.values(), key=lambda fact: (fact.kind, fact.name))
        payload = json.dumps(
            {
                "last_updated": time.time(),
                "facts": [asdict(fact) for fact in ordered[:MAX_FACTS]],
            },
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


__all__ = ["ProjectFact", "ProjectFactsStore"]
