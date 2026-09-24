"""Process-local admission registry for concurrent workflow write scopes.

Inside a single turn the tool scheduler already serializes workflow
sub-agents, but a threaded host (the gateway's ``ThreadingHTTPServer``) can
serve overlapping turns, so two ``run_workflow`` executions may race in one
process.  Without admission control both would burn their full stage budget
and only collide at promotion.  This registry refuses admission up front when
a new workflow declares write paths that overlap an active workflow.

Layering: this is only the cheapest in-process preflight.  Promotion remains
the final authority across processes (the advisory parent lock, the complete
parent-baseline digest, and ``git apply --check``), so cross-process conflicts
are intentionally out of scope here. Disjoint-write admission allows concurrent
work but does not prove that two candidates can both safely promote.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from minicode.scheduler_path_policy import normalized_forbidden_paths_overlap


class WorkflowWriteScopeConflict(RuntimeError):
    """A workflow declared write paths that overlap an active workflow."""

    def __init__(
        self,
        *,
        workflow_id: str,
        conflicting_workflow_id: str,
        overlapping_paths: tuple[tuple[str, str], ...],
    ) -> None:
        super().__init__(
            f"workflow write scope overlaps active workflow "
            f"{conflicting_workflow_id}"
        )
        self.workflow_id = workflow_id
        self.conflicting_workflow_id = conflicting_workflow_id
        self.overlapping_paths = overlapping_paths


@dataclass(frozen=True, slots=True)
class WorkflowScopeRegistration:
    """One active workflow's resolved write-scope claim."""

    workflow_id: str
    write_paths: tuple[str, ...]
    registered_at: float


class WorkflowScopeRegistry:
    """Thread-safe set of active workflow write scopes for one process.

    Overlap semantics are write-write only and deliberately conservative:
    two scope paths conflict when either lexically contains the other (for
    example ``src/`` and ``src/a.py``), using the same case-insensitive
    lexical identity rules as the Scheduler capability boundary.  Read scopes
    are never registered, and an empty write scope (the legacy fallback)
    therefore never conflicts.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._lock = threading.Lock()
        self._registrations: dict[str, WorkflowScopeRegistration] = {}

    @staticmethod
    def _normalize_write_paths(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple):
            raise ValueError("workflow write scope must be a tuple")
        normalized: list[str] = []
        for path in value:
            if not isinstance(path, str) or not path:
                raise ValueError("workflow write scope contains an unsafe path")
            portable = path.replace("\\", "/")
            parts = [part for part in portable.split("/") if part not in {"", "."}]
            if ".." in parts:
                raise ValueError("workflow write scope contains an unsafe path")
            prefix = "/" if portable.startswith("/") else ""
            joined = prefix + "/".join(parts)
            normalized.append(joined or ".")
        return tuple(normalized)

    def register(
        self,
        workflow_id: str,
        *,
        write_paths: tuple[str, ...],
    ) -> WorkflowScopeRegistration:
        """Claim ``write_paths`` or raise when they overlap an active claim."""

        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")
        normalized = self._normalize_write_paths(write_paths)
        with self._lock:
            if workflow_id in self._registrations:
                raise ValueError(
                    f"workflow scope is already registered: {workflow_id}"
                )
            for active in self._registrations.values():
                overlapping = tuple(
                    (incoming, active_path)
                    for incoming in normalized
                    for active_path in active.write_paths
                    if normalized_forbidden_paths_overlap(incoming, active_path)
                )
                if overlapping:
                    raise WorkflowWriteScopeConflict(
                        workflow_id=workflow_id,
                        conflicting_workflow_id=active.workflow_id,
                        overlapping_paths=overlapping,
                    )
            registration = WorkflowScopeRegistration(
                workflow_id=workflow_id,
                write_paths=normalized,
                registered_at=float(self._clock()),
            )
            self._registrations[workflow_id] = registration
            return registration

    def unregister(self, workflow_id: str) -> bool:
        """Release one claim; safe to call from any terminal cleanup path."""

        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")
        with self._lock:
            return self._registrations.pop(workflow_id, None) is not None

    def snapshot(self) -> tuple[WorkflowScopeRegistration, ...]:
        """Return a stable view of the currently active claims."""

        with self._lock:
            return tuple(self._registrations.values())


_PROCESS_REGISTRY = WorkflowScopeRegistry()


def process_workflow_scope_registry() -> WorkflowScopeRegistry:
    """Return the process-wide registry consulted at workflow admission."""

    return _PROCESS_REGISTRY


__all__ = [
    "WorkflowScopeRegistration",
    "WorkflowScopeRegistry",
    "WorkflowWriteScopeConflict",
    "process_workflow_scope_registry",
]
