"""Workflow-private continuity for the single scoped writing Executor.

This object is a host capability, not a public continuation ID. It retains
conversation and context governance only; each Assignment supplies fresh
permissions, tool registry, submission channel, and budget attribution.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from minicode.context_manager import ContextManager, estimate_messages_tokens
from minicode.implementation_recovery import InputTokenCalibration


class ExecutorRequestViewState:
    """Host observations used only to project requests for one bound model.

    No permissions, budget, phase counters or submission capability live here.
    The owning session validates the workspace and scope before every use.
    """

    def __init__(self, *, model: object) -> None:
        self._model = model
        self._changed_paths: frozenset[str] = frozenset()
        self._observer: Any | None = None
        self.calibration = InputTokenCalibration()
        self.recovery_view_active = False

    def bind_observer(self, *, model: object, observer: Any) -> None:
        if model is not self._model:
            raise ValueError("request projection belongs to another Executor model")
        self.finish_turn()
        self._observer = observer

    @property
    def changed_paths(self) -> frozenset[str]:
        observed = (self._observer.observed_changed_paths
                    if self._observer is not None else frozenset())
        return self._changed_paths | observed

    def finish_turn(self) -> None:
        # Capture the last host-observed edit even if no next request ran.
        # An edit interrupted before observation is not claimed here. Never
        # reconstruct a host byte observation from model text.
        self._changed_paths = self.changed_paths
        self._observer = None


class ExecutorSession:
    def __init__(self, *, owner: object, workspace_root: str) -> None:
        if owner is None:
            raise ValueError("executor_session requires a host owner")
        self._owner: object | None = owner
        # Diagnostic identity only: possession never grants continuation.
        self.session_id = "execsession_" + uuid4().hex
        self._workspace_root = Path(workspace_root).resolve()
        self._scope: tuple[tuple[str, ...], ...] | None = None
        self._lock = threading.Lock()
        self._active = False
        self._closed = False
        self.model: Any | None = None
        self.context_manager: ContextManager | None = None
        self._request_view: ExecutorRequestViewState | None = None
        self.last_outcome: str | None = None

    def validate(self, *, owner: object, context: object) -> tuple[tuple[str, ...], ...]:
        if self._closed or owner is not self._owner:
            raise ValueError("executor_session is closed or belongs to another workflow")
        if getattr(context, "_logical_agent_role", None) != "executor":
            raise ValueError("executor_session requires the scheduler Executor role")
        for root in (getattr(context, "cwd", None),
                     getattr(context, "_logical_agent_workspace_root", None)):
            if not isinstance(root, str) or Path(root).resolve() != self._workspace_root:
                raise ValueError("executor_session belongs to another workspace")
        scope = getattr(context, "_logical_agent_scope", None)
        values = []
        for field in ("read_roots", "write_paths", "forbidden_paths"):
            value = scope.get(field) if isinstance(scope, dict) else getattr(scope, field, None)
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise ValueError("executor_session requires a frozen scheduler scope")
            values.append(value)
        binding = tuple(values)
        if self._scope is not None and binding != self._scope:
            raise ValueError("executor_session scope cannot change between Assignments")
        return binding

    def begin(self, *, owner: object, context: object) -> list[dict] | None:
        with self._lock:
            binding = self.validate(owner=owner, context=context)
            if self._active:
                raise ValueError("executor_session is already executing an Assignment")
            self._scope = binding
            self._active = True
            self.last_outcome = None
            return (deepcopy(self.context_manager.messages)
                    if self.context_manager is not None else None)

    def bind_model(self, factory: Callable[[], Any], *, tools: object) -> Any:
        if self.model is None:
            self.model = factory()
        # Provider adapters hold a registry for tool schema generation. Never
        # keep a previous Assignment's now-closed submission/check capability.
        if hasattr(self.model, "tools"):
            self.model.tools = tools
        return self.model

    def request_view(self) -> ExecutorRequestViewState:
        with self._lock:
            if not self._active or self._closed or self.model is None:
                raise ValueError("request projection requires an active Executor Assignment")
            if self._request_view is None:
                self._request_view = ExecutorRequestViewState(model=self.model)
            return self._request_view

    def finish(self, *, messages: list[dict] | None = None, outcome: str) -> None:
        with self._lock:
            if not self._active:
                return
            if messages is not None and self.context_manager is not None:
                self.context_manager.messages = deepcopy(messages)
            self.last_outcome = outcome
            if self._request_view is not None:
                self._request_view.finish_turn()
            if self.model is not None and hasattr(self.model, "tools"):
                self.model.tools = None
            self._active = False

    def continuation_input_tokens(self, *, owner: object) -> int | None:
        """Price the retained request view without changing its transcript.

        This is a feasibility quote, not a model-call reservation. The renewed
        Assignment and feedback are priced again by the actual loop against
        the same ledger before any provider request is sent.
        """
        from minicode.implementation_recovery import project_recovery_messages

        with self._lock:
            if (self._closed or self._active or owner is not self._owner
                    or self.model is None or self.context_manager is None):
                return None
            view = self._request_view
            messages = project_recovery_messages(
                self.context_manager.messages,
                changed_paths=view.changed_paths if view is not None else frozenset(),
            )
            local = estimate_messages_tokens(messages)
            return view.calibration.estimate(local) if view is not None else local

    def close(self) -> None:
        """Discard continuity when its owning workflow has ended."""
        with self._lock:
            if self._active:
                raise ValueError("executor_session cannot close while an Assignment is running")
            self._closed = True
            self._owner = None
            self.model = None
            self.context_manager = None
            self._request_view = None
