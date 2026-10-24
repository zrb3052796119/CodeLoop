from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

from minicode.capability_contracts import CapabilityClassifier, CapabilityProfile
from minicode.logging_config import get_logger

_logger = get_logger("tooling")
_CAPABILITY_ARGUMENTS_UNSET = object()


class ToolExecutionAbandoned(RuntimeError):
    """Raised when an owning tool timeout has abandoned this worker.

    The worker thread cannot be killed from Python, but a nested Agent loop
    can check the shared abandonment event at its next checkpoint and unwind
    instead of continuing to spend model calls and money.
    """


class ToolTerminationReason(str, Enum):
    """Closed internal control outcomes carried independently of display text."""

    ABANDONED = "abandoned"
    TURN_CANCELLED = "turn_cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"


if TYPE_CHECKING:
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.run_events import AgentEventSink, SkillUsageTracker


# ---------------------------------------------------------------------------
# Constants for smart truncation
# ---------------------------------------------------------------------------

# Default max output size (characters) — per tool type
_DEFAULT_MAX_OUTPUT = 30_000       # ~8K tokens, safe for context
_LARGE_OUTPUT_THRESHOLD = 50_000   # Trigger smart truncation above this

# Tool-specific output limits (characters)
_TOOL_OUTPUT_LIMITS: dict[str, int] = {
    "read_file": 40_000,
    "grep_files": 20_000,
    "run_command": 30_000,
    "run_with_debug": 30_000,
    "web_fetch": 20_000,
    "web_search": 15_000,
    "list_files": 15_000,
    "file_tree": 15_000,
    "code_review": 20_000,
    "diff_viewer": 20_000,
    "test_runner": 25_000,
    # SKILL.md is executable instruction context, not diagnostic output. Keep
    # it whole up to a deliberately bounded ceiling instead of generic
    # head/tail truncation that can remove the operative middle section.
    "load_skill": 200_000,
}


def _smart_truncate_output(output: str, tool_name: str, max_chars: int | None = None) -> str:
    """Intelligently truncate large tool output to preserve context window.
    
    Strategy:
    1. If output fits within limit, return as-is
    2. For file reads: keep head + tail (beginning and end of file)
    3. For command output: keep head + tail + error lines
    4. For grep/search: keep first N matches + summary
    5. Generic: keep head + tail with line count summary
    """
    if not output:
        return output
    
    limit = max_chars or _TOOL_OUTPUT_LIMITS.get(tool_name, _DEFAULT_MAX_OUTPUT)
    
    if len(output) <= limit:
        return output
    
    lines = output.split("\n")
    total_lines = len(lines)
    total_chars = len(output)
    
    # Calculate how many lines we can keep (rough estimate)
    avg_line_len = total_chars / max(1, total_lines)
    max_lines = int(limit / max(40, avg_line_len))

    if max_lines < 2:
        # One large JSON/log line cannot be selected as both head and tail.
        # Reserve marker space and take disjoint character ranges instead.
        marker = f"\n... [characters omitted (output too large: {total_chars:,} chars)] ...\n"
        if len(marker) >= limit:
            return output[:max(0, limit - 1)] + "…"
        remaining = limit - len(marker)
        head_chars = (remaining + 1) // 2
        tail_chars = remaining - head_chars
        return output[:head_chars] + marker + (output[-tail_chars:] if tail_chars else "")
    
    if tool_name == "read_file":
        # Keep head + tail — most important for understanding file structure
        head_lines = max(1, int(max_lines * 0.6))
        tail_lines = max(1, max_lines - head_lines)
        head = "\n".join(lines[:head_lines])
        tail = "\n".join(lines[-tail_lines:])
        omitted = total_lines - head_lines - tail_lines
        return (
            f"{head}\n"
            f"\n... [{omitted} lines omitted (output too large: {total_chars:,} chars)] ...\n\n"
            f"{tail}"
        )
    
    if tool_name in ("run_command", "run_with_debug"):
        # Keep head + error lines + tail
        head_lines = max(1, int(max_lines * 0.4))
        tail_lines = max(1, int(max_lines * 0.4))
        
        # Also extract error/warning lines
        error_pattern = re.compile(r'(?i)(error|fail|exception|traceback|warning)', re.IGNORECASE)
        error_lines = [
            (i, line) for i, line in enumerate(lines)
            if error_pattern.search(line) and head_lines <= i < total_lines - tail_lines
        ]
        error_text = ""
        if error_lines:
            error_text = "\n\n[Key errors/warnings from omitted section:]\n" + "\n".join(
                f"L{i+1}: {line[:200]}" for i, line in error_lines[:20]
            )
        
        head = "\n".join(lines[:head_lines])
        tail = "\n".join(lines[-tail_lines:])
        omitted = total_lines - head_lines - tail_lines
        return (
            f"{head}\n"
            f"\n... [{omitted} lines omitted (output too large: {total_chars:,} chars)] ...{error_text}\n\n"
            f"{tail}"
        )
    
    if tool_name in ("grep_files", "web_search"):
        # Keep first N matches + summary
        head = "\n".join(lines[:max_lines])
        omitted = total_lines - max_lines
        return (
            f"{head}\n"
            f"\n... [{omitted} more lines omitted (output too large: {total_chars:,} chars, {total_lines} total lines)] ..."
        )
    
    # Generic: head + tail
    head_lines = max(1, int(max_lines * 0.5))
    tail_lines = max(1, max_lines - head_lines)
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    omitted = total_lines - head_lines - tail_lines
    return (
        f"{head}\n"
        f"\n... [{omitted} lines omitted (output too large: {total_chars:,} chars)] ...\n\n"
        f"{tail}"
    )


# ---------------------------------------------------------------------------
# Tool metadata (inspired by Claude Code's Tool type)
# ---------------------------------------------------------------------------

class ToolCapability(str, Enum):
    """Tool capability flags."""
    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"
    CONCURRENCY_SAFE = "concurrency_safe"
    REQUIRES_PERMISSION = "requires_permission"


@dataclass
class ToolMetadata:
    """Tool metadata for classification and discovery.
    
    Inspired by Claude Code's Tool type definition.
    """
    name: str
    description: str
    capabilities: set[ToolCapability] = field(default_factory=set)
    input_schema: dict[str, Any] = field(default_factory=dict)
    is_enabled: bool = True
    max_result_size_chars: int = 10_000
    tags: list[str] = field(default_factory=list)
    
    @property
    def is_read_only(self) -> bool:
        """Check if tool is read-only."""
        return ToolCapability.READ_ONLY in self.capabilities
    
    @property
    def is_destructive(self) -> bool:
        """Check if tool can modify/delete data."""
        return ToolCapability.DESTRUCTIVE in self.capabilities
    
    @property
    def is_concurrency_safe(self) -> bool:
        """Check if tool is safe for concurrent execution."""
        return ToolCapability.CONCURRENCY_SAFE in self.capabilities


# ---------------------------------------------------------------------------
# Tool Protocol (inspired by Claude Code's Tool interface)
# ---------------------------------------------------------------------------

class Tool(Protocol):
    """Tool protocol defining a complete tool lifecycle.
    
    Inspired by Claude Code's Tool type which includes:
    - call: Execution logic
    - description: Dynamic description generation
    - validate_input: Input validation
    - check_permissions: Permission checking
    - Metadata: is_read_only, is_destructive, etc.
    """
    
    @property
    def name(self) -> str: ...
    
    @property
    def description_template(self) -> str: ...
    
    def get_description(self, args: dict[str, Any], options: dict[str, Any] | None = None) -> str: ...
    def validate_input(self, args: dict[str, Any]) -> tuple[bool, str]: ...
    def check_permissions(self, args: dict[str, Any], context: ToolContext) -> tuple[bool, str]: ...
    def call(
        self,
        args: dict[str, Any],
        context: ToolContext,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> ToolResult: ...
    def is_enabled(self) -> bool: ...
    def is_read_only(self, args: dict[str, Any]) -> bool: ...
    def is_destructive(self, args: dict[str, Any]) -> bool: ...


@dataclass(slots=True)
class BackgroundTaskResult:
    taskId: str
    type: str
    command: str
    pid: int
    status: str
    startedAt: int


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str
    backgroundTask: BackgroundTaskResult | None = None
    awaitUser: bool = False
    verification: dict[str, object] | None = None
    # Set only by the host's atomic file writer. Prose cannot trigger checks.
    _file_changed: bool = field(default=False, kw_only=True, repr=False)
    # Actual host check feedback, never inferred from model-controlled prose.
    # None means no conclusive set of public implementation checks ran.
    _implementation_checks_failed: bool | None = field(default=None, kw_only=True, repr=False)
    # Only edit_file's unmatched/ambiguous anchor failures set this. Permission
    # denials, cancellation and arbitrary error prose cannot reopen repair.
    _edit_anchor_failed: bool = field(default=False, kw_only=True, repr=False)
    # Descriptive invocation evidence. This never authorizes or blocks a call.
    capability: dict[str, Any] | None = field(default=None, kw_only=True)
    # A stateful tool sets this only after an irreversible mutation has
    # completed and its result has been finalized.  The Agent Loop must retain
    # that truthful result even if cancellation raced with the finalization
    # boundary; ordinary tool results keep the default cancellation behavior.
    _must_deliver: bool = field(default=False, repr=False, compare=False)
    # A fully finalized child workflow may return control to its parent after
    # the immutable receipt is delivered. Cancellation or uncertain commit
    # state still takes the terminal authority path. This is host-owned and
    # never inferred from model text or the tool's display output.
    _continue_parent: bool = field(default=False, repr=False, compare=False, kw_only=True)
    # Ephemeral current-invocation workflow authority; never read from output.
    _workflow_delivery_receipt: object | None = field(default=None, repr=False, compare=False, kw_only=True)
    # A strict worker declaration was accepted. This is neither verification
    # nor a commit receipt; it only ends this worker's implementation loop.
    _implementation_submitted: bool = field(default=False, repr=False, compare=False, kw_only=True)
    # A bound Reviewer capability rejected only the semantic declaration.
    # The loop must also verify the capability/channel identity before retrying.
    _review_submission_rejected: bool = field(default=False, repr=False, compare=False, kw_only=True)
    _review_evidence_incomplete: bool = field(default=False, repr=False, compare=False, kw_only=True)
    # Host-owned acknowledgement of an ephemeral result. The Agent Loop calls
    # this only after appending the result to its own transcript; worker
    # completion or a timed-out tool thread does not prove delivery.
    _delivery_ack: Callable[[], None] | None = field(
        default=None, repr=False, compare=False, kw_only=True
    )
    # Lifecycle control must never parse ``output``.  Human-readable error
    # wording can evolve independently while this closed marker remains the
    # authoritative internal signal.
    _termination_reason: ToolTerminationReason | None = field(
        default=None,
        repr=False,
        compare=False,
    )


_SKILL_LOAD_POLICY_IDENTITY_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,127})?"
)
_SKILL_LOAD_POLICY_SOURCES = frozenset(
    {"project", "user", "compat_project", "compat_user"}
)
_SKILL_LOAD_POLICY_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SKILL_LOAD_POLICY_REASON_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SKILL_LOAD_POLICY_PAYLOAD_VERSION = 3
_SKILL_LOAD_POLICY_LEGACY_PAYLOAD_VERSION = 2
MAX_SKILL_LOAD_POLICY_BINDINGS = 128
_SKILL_LOAD_BINDING_PAYLOAD_FIELDS = frozenset(
    {
        "qualified_name",
        "source",
        "content_digest",
        "routing_identity_digest",
    }
)
_SKILL_LOAD_POLICY_LEGACY_PAYLOAD_FIELDS = frozenset(
    {
        "version",
        "allowed_bindings",
        "denied_qualified_names",
        "reason_code",
    }
)
_SKILL_LOAD_POLICY_PAYLOAD_FIELDS = (
    _SKILL_LOAD_POLICY_LEGACY_PAYLOAD_FIELDS | {"allow_catalog_scope"}
)


@dataclass(frozen=True, slots=True)
class SkillLoadBinding:
    """Immutable identity of the exact Skill version authorized to load."""

    qualified_name: str
    source: str
    content_digest: str
    routing_identity_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.qualified_name, str)
            or self.qualified_name != self.qualified_name.strip()
            or _SKILL_LOAD_POLICY_IDENTITY_RE.fullmatch(self.qualified_name)
            is None
            or ".." in self.qualified_name
        ):
            raise ValueError("Skill binding qualified_name is invalid")
        if (
            not isinstance(self.source, str)
            or self.source not in _SKILL_LOAD_POLICY_SOURCES
        ):
            raise ValueError("Skill binding source is invalid")
        for field_name in ("content_digest", "routing_identity_digest"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or _SKILL_LOAD_POLICY_DIGEST_RE.fullmatch(value) is None
            ):
                raise ValueError(f"Skill binding {field_name} is invalid")


def skill_load_binding_from_value(value: object) -> SkillLoadBinding | None:
    """Project a routed/catalog Skill value into its executable binding."""
    if isinstance(value, SkillLoadBinding):
        return value
    if isinstance(value, Mapping):
        getter = value.get
    else:
        getter = lambda name, default=None: getattr(value, name, default)
    try:
        qualified_name = getter("qualified_name")
        if not qualified_name:
            qualified_name = getter("qualifiedName")
        if not qualified_name:
            qualified_name = getter("name")
        source = getter("source")
        content_digest = getter("content_digest")
        routing_identity_digest = getter("routing_identity_digest")
        if not all(
            isinstance(item, str)
            for item in (
                qualified_name,
                source,
                content_digest,
                routing_identity_digest,
            )
        ):
            return None
        return SkillLoadBinding(
            qualified_name=qualified_name,
            source=source,
            content_digest=content_digest,
            routing_identity_digest=routing_identity_digest,
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SkillLoadPolicy:
    """Host-owned authority for loading exact executable Skill versions.

    Inventory fallback is represented by the complete route-time catalog
    binding set. An empty set blocks every Skill. Name denials are exact
    qualified identities and always win. ``allow_catalog_scope`` instead
    states name-level intent over the turn's whole catalog snapshot:
    ``allowed_bindings`` must stay empty and every catalog binding not
    explicitly denied is permitted.
    """

    allowed_bindings: frozenset[SkillLoadBinding] = field(
        default_factory=frozenset
    )
    denied_qualified_names: frozenset[str] = field(default_factory=frozenset)
    reason_code: str = "inventory-fallback"
    allow_catalog_scope: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allow_catalog_scope, bool):
            raise TypeError("allow_catalog_scope must be a bool")
        if not isinstance(self.allowed_bindings, frozenset) or not all(
            isinstance(binding, SkillLoadBinding)
            for binding in self.allowed_bindings
        ):
            raise TypeError(
                "allowed_bindings must be a frozenset of SkillLoadBinding"
            )
        if self.allow_catalog_scope and self.allowed_bindings:
            raise ValueError(
                "a catalog-scoped policy cannot enumerate allowed_bindings"
            )
        if len(self.allowed_bindings) > MAX_SKILL_LOAD_POLICY_BINDINGS:
            raise ValueError("allowed_bindings exceeds the binding limit")
        qualified_names = {
            binding.qualified_name for binding in self.allowed_bindings
        }
        if len(qualified_names) != len(self.allowed_bindings):
            raise ValueError(
                "allowed_bindings cannot authorize multiple versions of one Skill"
            )
        if not isinstance(self.denied_qualified_names, frozenset) or not all(
            isinstance(name, str)
            and name == name.strip()
            and _SKILL_LOAD_POLICY_IDENTITY_RE.fullmatch(name) is not None
            and ".." not in name
            for name in self.denied_qualified_names
        ):
            raise TypeError(
                "denied_qualified_names must be safe qualified identities"
            )
        if len(self.denied_qualified_names) > MAX_SKILL_LOAD_POLICY_BINDINGS:
            raise ValueError("denied_qualified_names exceeds the identity limit")
        if (
            not isinstance(self.reason_code, str)
            or _SKILL_LOAD_POLICY_REASON_RE.fullmatch(self.reason_code) is None
        ):
            raise ValueError("Skill load policy reason_code is invalid")

    def permits(self, binding: SkillLoadBinding) -> bool:
        if not isinstance(binding, SkillLoadBinding):
            return False
        if binding.qualified_name in self.denied_qualified_names:
            return False
        if self.allow_catalog_scope:
            return True
        return binding in self.allowed_bindings


def intersect_skill_load_policies(
    parent: SkillLoadPolicy | None,
    child: SkillLoadPolicy | None,
) -> SkillLoadPolicy | None:
    """Compose nested Skill authority without allowing privilege expansion.

    A missing child policy means child routing failed before it could make a
    decision and always closes Skill loading, even under inventory fallback.
    """
    if child is None:
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            denied_qualified_names=(
                parent.denied_qualified_names
                if isinstance(parent, SkillLoadPolicy)
                else frozenset()
            ),
            reason_code="child-routing-unavailable",
        )
    if parent is not None and not isinstance(parent, SkillLoadPolicy):
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            reason_code="invalid-parent-policy",
        )
    if child is not None and not isinstance(child, SkillLoadPolicy):
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            denied_qualified_names=(
                parent.denied_qualified_names
                if isinstance(parent, SkillLoadPolicy)
                else frozenset()
            ),
            reason_code="invalid-child-policy",
        )
    if parent is None:
        return child

    # A catalog-scoped side contributes the whole catalog; intersecting with
    # it therefore keeps the other side's enumerated set (or scope) verbatim.
    denied = parent.denied_qualified_names | child.denied_qualified_names
    if parent.allow_catalog_scope and child.allow_catalog_scope:
        allowed = frozenset()
        allow_catalog_scope = True
    elif parent.allow_catalog_scope:
        allowed = child.allowed_bindings
        allow_catalog_scope = False
    elif child.allow_catalog_scope:
        allowed = parent.allowed_bindings
        allow_catalog_scope = False
    else:
        allowed = parent.allowed_bindings.intersection(child.allowed_bindings)
        allow_catalog_scope = False
    if len(denied) > MAX_SKILL_LOAD_POLICY_BINDINGS:
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            reason_code="nested-denial-overflow",
        )
    return SkillLoadPolicy(
        allowed_bindings=allowed,
        denied_qualified_names=denied,
        reason_code="nested-policy-intersection",
        allow_catalog_scope=allow_catalog_scope,
    )


def _validated_skill_load_policy_denials(
    value: object,
    *,
    field_name: str,
 ) -> frozenset[str]:
    if value is None:
        raise ValueError(f"{field_name} must be a list")
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    if len(value) > MAX_SKILL_LOAD_POLICY_BINDINGS:
        raise ValueError(f"{field_name} exceeds the identity limit")

    normalized: set[str] = set()
    for identity in value:
        if (
            not isinstance(identity, str)
            or identity != identity.strip()
            or _SKILL_LOAD_POLICY_IDENTITY_RE.fullmatch(identity) is None
            or ".." in identity
        ):
            raise ValueError(f"{field_name} contains an invalid identity")
        if identity in normalized:
            raise ValueError(f"{field_name} contains a duplicate identity")
        normalized.add(identity)
    return frozenset(normalized)


def _skill_load_binding_to_payload(
    binding: SkillLoadBinding,
) -> dict[str, str]:
    return {
        "qualified_name": binding.qualified_name,
        "source": binding.source,
        "content_digest": binding.content_digest,
        "routing_identity_digest": binding.routing_identity_digest,
    }


def _validated_skill_load_policy_bindings(
    value: object,
) -> frozenset[SkillLoadBinding]:
    if value is None:
        raise ValueError("allowed_bindings must be a list")
    if not isinstance(value, list):
        raise TypeError("allowed_bindings must be a list or null")
    if len(value) > MAX_SKILL_LOAD_POLICY_BINDINGS:
        raise ValueError("allowed_bindings exceeds the binding limit")
    bindings: set[SkillLoadBinding] = set()
    qualified_names: set[str] = set()
    for item in value:
        if (
            not isinstance(item, dict)
            or frozenset(item) != _SKILL_LOAD_BINDING_PAYLOAD_FIELDS
        ):
            raise ValueError("allowed_bindings contains invalid fields")
        if not all(
            isinstance(item[field_name], str)
            for field_name in _SKILL_LOAD_BINDING_PAYLOAD_FIELDS
        ):
            raise TypeError("allowed_bindings fields must be strings")
        binding = skill_load_binding_from_value(item)
        if binding is None:
            raise ValueError("allowed_bindings contains an invalid binding")
        if binding in bindings:
            raise ValueError("allowed_bindings contains a duplicate binding")
        if binding.qualified_name in qualified_names:
            raise ValueError(
                "allowed_bindings contains multiple versions of one Skill"
            )
        bindings.add(binding)
        qualified_names.add(binding.qualified_name)
    return frozenset(bindings)


def serialize_skill_load_policy(
    policy: SkillLoadPolicy | None,
) -> dict[str, object] | None:
    """Project a policy into the strict, bounded subprocess JSON contract."""
    if policy is None:
        return None
    if not isinstance(policy, SkillLoadPolicy):
        raise TypeError("skill load policy must be a SkillLoadPolicy")
    allowed = [
        _skill_load_binding_to_payload(binding)
        for binding in sorted(
            policy.allowed_bindings,
            key=lambda item: (
                item.qualified_name,
                item.source,
                item.content_digest,
                item.routing_identity_digest,
            ),
        )
    ]
    denied = sorted(policy.denied_qualified_names)
    _validated_skill_load_policy_bindings(allowed)
    _validated_skill_load_policy_denials(
        denied,
        field_name="denied_qualified_names",
    )
    if (
        not isinstance(policy.reason_code, str)
        or _SKILL_LOAD_POLICY_REASON_RE.fullmatch(policy.reason_code) is None
    ):
        raise ValueError("skill load policy reason_code is invalid")
    return {
        "version": _SKILL_LOAD_POLICY_PAYLOAD_VERSION,
        "allowed_bindings": allowed,
        "denied_qualified_names": denied,
        "reason_code": policy.reason_code,
        "allow_catalog_scope": policy.allow_catalog_scope,
    }


def deserialize_skill_load_policy(payload: object) -> SkillLoadPolicy | None:
    """Validate and rebuild one policy from the subprocess JSON contract."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise TypeError("skill load policy payload must be an object or null")
    fields = frozenset(payload)
    if fields == _SKILL_LOAD_POLICY_PAYLOAD_FIELDS:
        expected_version = _SKILL_LOAD_POLICY_PAYLOAD_VERSION
        allow_catalog_scope = payload.get("allow_catalog_scope")
        if not isinstance(allow_catalog_scope, bool):
            raise TypeError("allow_catalog_scope must be a bool")
    elif fields == _SKILL_LOAD_POLICY_LEGACY_PAYLOAD_FIELDS:
        # v2 payloads predate catalog scoping; absence defaults to False.
        expected_version = _SKILL_LOAD_POLICY_LEGACY_PAYLOAD_VERSION
        allow_catalog_scope = False
    else:
        raise ValueError("skill load policy payload fields are invalid")
    version = payload.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != expected_version
    ):
        raise ValueError("skill load policy payload version is unsupported")
    allowed = _validated_skill_load_policy_bindings(
        payload.get("allowed_bindings")
    )
    denied = _validated_skill_load_policy_denials(
        payload.get("denied_qualified_names"),
        field_name="denied_qualified_names",
    )
    reason = payload.get("reason_code")
    if (
        not isinstance(reason, str)
        or _SKILL_LOAD_POLICY_REASON_RE.fullmatch(reason) is None
    ):
        raise ValueError("skill load policy reason_code is invalid")
    return SkillLoadPolicy(
        allowed_bindings=allowed,
        denied_qualified_names=denied,
        reason_code=reason,
        allow_catalog_scope=allow_catalog_scope,
    )


@dataclass(slots=True)
class ToolContext:
    cwd: str = "."
    permissions: Any | None = None
    _implementation_candidate_guard: Any | None = field(default=None, repr=False, compare=False, kw_only=True)
    _implementation_analysis_complete: bool = field(default=False, repr=False, compare=False, kw_only=True)
    _logical_agent_max_total_tokens: int | None = field(default=None, repr=False, compare=False, kw_only=True)
    _logical_agent_preserve_total_tokens: int = field(default=0, repr=False, compare=False, kw_only=True)
    _runtime: dict | None = None
    _event_sink: "AgentEventSink | None" = None
    _step: int | None = None
    _mcp_current_state_registry: "McpCurrentStateRegistry | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    _skill_usage_tracker: "SkillUsageTracker | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Cooperative cancellation for the owning Turn. A tool that starts its own
    # long-running work (notably the sub-agent `task` tool) must forward this
    # so cancelling the parent Turn also stops the nested work instead of
    # leaving it to burn tokens in the background.
    _cancellation_token: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Absolute monotonic deadline for one nested Task invocation. Kept
    # separate from `_cancellation_token` so the parent Turn's token identity
    # and cancellation authority are preserved exactly.
    _task_deadline_monotonic: float | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _task_timeout_seconds: float | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Shared turn-level token/model-call/cost budget. The top-level loop owns
    # one instance and passes it through every ToolContext; sub-agents must
    # use the same object rather than creating independent budgets.
    _agent_budget: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Set by _execute_single_tool when a tool worker timed out. Long-running
    # tools (notably the `task` sub-agent) check it cooperatively and unwind
    # instead of continuing after the parent has already returned.
    _tool_abandoned: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Turn-scoped, bounded note mailbox shared by the top-level loop and every
    # sub-agent/workflow phase. Collaboration tools use this object.
    _subagent_mailbox: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Turn-scoped owner for asynchronous read-only sub-agents. The top-level
    # agent loop creates it once, nested loops share it, and only the owner
    # shuts it down at the end of the turn.
    _subagent_lifecycle: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Nesting depth of the agent loop that owns this context. The top-level
    # loop is 0; a sub-agent spawned by the `task` tool runs at 1. Used to
    # stop unbounded sub-agent recursion.
    _agent_depth: int = 0
    # Connection-local UI channel (ConversationPresentation). Deliberately
    # separate from `_event_sink`: presentation is never authority, so a tool
    # that runs its own nested loop can surface live progress here without
    # writing nested lifecycle events into the parent Run's journal or
    # disturbing the permission-approval session's tool tracking.
    _presentation: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # The fields below were appended (rather than inserted above) so existing
    # embedders that still construct ToolContext positionally retain the exact
    # pre-V2 argument order.
    # Host-owned process isolation capability for deterministic verification.
    # It is injected by an embedder/runtime administrator and is never exposed
    # as task/model input. Absence deliberately makes repository-code checks
    # inconclusive rather than running them unsandboxed on the host.
    _verifier_sandbox_backend: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Host-pinned command-name -> absolute executable capabilities. Verifier
    # requests can select only a name already present in this private map.
    _verifier_trusted_executables: dict[str, str] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # The subset registered explicitly by the host user (env channel). These
    # paths are exempt from the verifier's default $HOME untrusted root but
    # never from the workspace rejection.
    _verifier_explicit_trusted_executables: dict[str, str] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Original top-level user message captured by Agent Loop, before model
    # delegation. Only used to preserve explicit independent review requests.
    _workflow_user_request: str | None = field(default=None, repr=False, compare=False, kw_only=True)
    # Host/UI-owned one-time approval provider for high-risk workflows.
    # Public task JSON and Worker output cannot manufacture this capability.
    _workflow_human_approval_provider: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Production host mode: state-changing sub-agent work must enter through
    # WorkflowRequest.v1 and the Central Scheduler. Direct tests/legacy
    # embedders may leave this false while migrating.
    _central_workflow_v2_enforced: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    # Explicit non-production waiver: allow a structured workflow to commit
    # without a persisted commit-ready record. Missing recorders fail closed
    # unless this is set; the waiver is recorded as commitReadyStatus=waived.
    # Production hosts never set it; it cannot override the enforced mode.
    _workflow_commit_audit_waived: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    # True only for the Task invocation that created this deadline. The one
    # deadline covers workflow snapshotting, dispatch, verification and commit;
    # nested tasks always inherit or shorten it.
    _task_deadline_owned: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    # Scheduler-owned logical role. Public callers select legacy agent types;
    # this internal marker narrows a physical `general` worker into a bounded
    # Executor without exposing a second direct write entry point.
    _logical_agent_role: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Physical model-call ceiling sealed in the scheduler Assignment. One
    # call is included for tool-disabled closing; public task input cannot set
    # this value or grant additional shared calls/tokens/time.
    _logical_agent_max_model_calls: int | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _logical_agent_scope: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _logical_agent_workspace_root: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Internal marker for every phase running inside a disposable workflow
    # transaction, including legacy phases without a structured logical role.
    # It closes process-global/project memory injection and reflection as an
    # undeclared channel into the frozen snapshot.
    _workflow_phase: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    # Skill files contain executable model instructions. The canonical router
    # projects its allow/deny decision into this host-owned field so a model
    # cannot bypass routing by calling ``load_skill`` with a guessed name.
    _skill_load_policy: SkillLoadPolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Turn-scoped, bounded store of finished read-only sub-agent
    # conversations. The `task` tool saves completed explore/plan runs here
    # so the model can resume one via continue_subagent_id instead of
    # paying for a fresh exploration. Absence disables continuation
    # fail-closed.
    _subagent_sessions: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Loop-scoped todo list state ({"tasks": [...], "counter": int}).
    # Each agent loop owns one so a sub-agent's todo_write can never
    # clobber the parent's or a sibling's list; absence falls back to the
    # tool's process-global list for bare-ToolContext embedder calls.
    _todo_state: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )


Validator = Callable[[Any], Any]
Runner = Callable[[Any, ToolContext], ToolResult]

ToolCapabilityDomain = Literal[
    "file",
    "code",
    "search",
    "web",
    "system",
    "memory",
    "communication",
    "analysis",
    "execution",
    "unknown",
]
ToolCapabilityScope = Literal["readonly", "write", "destructive", "external"]

_TOOL_CAPABILITY_DOMAINS = frozenset({
    "file",
    "code",
    "search",
    "web",
    "system",
    "memory",
    "communication",
    "analysis",
    "execution",
    "unknown",
})
_TOOL_CAPABILITY_SCOPES = frozenset({
    "readonly",
    "write",
    "destructive",
    "external",
})


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    validator: Validator
    run: Runner
    metadata: ToolMetadata | None = None
    # Optional per-call refinement of `is_concurrency_safe`. Some tools are
    # safe to parallelize only for certain inputs — the sub-agent tool, for
    # example, is safe for its read-only agent types but not for the one that
    # can write files and raise permission prompts. Returning False for a
    # given call sends only that call to the serial batch.
    concurrency_safe_for: Callable[[Any], bool] | None = None
    # Routing-only classification. These values help match Skills to tools;
    # they never grant execution permission or change mutation safeguards.
    # Legacy and dynamically discovered tools may leave both values unset and
    # use CapabilityRegistry's explicitly unverified compatibility fallback.
    capability_domain: ToolCapabilityDomain | None = None
    capability_scope: ToolCapabilityScope | None = None
    capability_profile: CapabilityProfile | None = None
    capability_for: CapabilityClassifier | None = None

    def __post_init__(self) -> None:
        if self.capability_profile is not None and not isinstance(
            self.capability_profile, CapabilityProfile
        ):
            raise TypeError("capability_profile must be CapabilityProfile")
        if self.capability_for is not None and not callable(self.capability_for):
            raise TypeError("capability_for must be callable")
        if (self.capability_domain is None) != (self.capability_scope is None):
            raise ValueError(
                "capability_domain and capability_scope must be declared together"
            )
        if (
            self.capability_domain is not None
            and self.capability_domain not in _TOOL_CAPABILITY_DOMAINS
        ):
            raise ValueError(f"Unknown capability domain: {self.capability_domain}")
        if (
            self.capability_scope is not None
            and self.capability_scope not in _TOOL_CAPABILITY_SCOPES
        ):
            raise ValueError(f"Unknown capability scope: {self.capability_scope}")
        if (
            self.capability_profile is not None
            and self.capability_domain is not None
            and (
                self.capability_profile.domain.value != self.capability_domain
                or self.capability_profile.scope.value != self.capability_scope
            )
        ):
            raise ValueError("capability_profile must match explicit domain/scope")

    def call_is_concurrency_safe(self, call_input: Any) -> bool:
        """Whether this specific invocation may run in the concurrent batch."""
        if not self.is_concurrency_safe:
            return False
        if self.concurrency_safe_for is None:
            return True
        try:
            return bool(self.concurrency_safe_for(call_input))
        except Exception:  # noqa: BLE001 - an undecidable call stays serial
            return False


    @property
    def is_read_only(self) -> bool:
        """Check if this tool is read-only (safe for concurrent execution)."""
        if self.metadata:
            return self.metadata.is_read_only
        # Fallback: heuristic based on tool name
        return self.name in _READ_ONLY_TOOL_NAMES
    
    @property
    def is_concurrency_safe(self) -> bool:
        """Check if this tool is safe for concurrent execution."""
        if self.metadata:
            return self.metadata.is_concurrency_safe or self.metadata.is_read_only
        return self.is_read_only


# Heuristic: tool names that are known to be read-only. Only real, currently
# registered tools belong here — a stale name would silently grant its future
# namesake concurrent, abandon-on-timeout treatment.
_READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file", "list_files", "grep_files", "file_tree", "file_line_count",
    "find_symbols", "find_references", "get_ast_info",
    "code_review", "diff_viewer",
    "web_fetch", "web_search",
    "browse_skill_directory", "search_skills",
})


RegistryCapabilityProvider = Callable[[], Any]
RegistryBoundToolFactory = Callable[
    [RegistryCapabilityProvider],
    ToolDefinition,
]


@dataclass(frozen=True, slots=True)
class _RegistrySnapshot:
    tools: tuple[ToolDefinition, ...]
    index: Mapping[str, ToolDefinition]
    skills: tuple[dict[str, Any], ...]


class ToolRegistry:
    def __init__(
        self,
        tools: list[ToolDefinition],
        skills: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        disposer: Callable[[], Any] | None = None,
        mcp_current_state_registry: McpCurrentStateRegistry | None = None,
        registry_bound_tool_factories: list[RegistryBoundToolFactory] | None = None,
        skill_catalog_factory: Callable[
            [], tuple[list[dict[str, Any]], list[ToolDefinition]]
        ] | None = None,
    ) -> None:
        self._skill_catalog_factory = skill_catalog_factory
        self._mcp_servers = list(mcp_servers or [])
        self._disposer = disposer
        self._owns_runtime_resources = True
        self._mcp_current_state_registry = mcp_current_state_registry
        factories = list(registry_bound_tool_factories or [])
        self._registry_bound_tool_factories: dict[
            str,
            RegistryBoundToolFactory,
        ] = {}
        capability_snapshot_holder: dict[str, Any] = {}

        def capability_registry_provider() -> Any:
            snapshot = capability_snapshot_holder.get("snapshot")
            if snapshot is None:
                raise RuntimeError(
                    "ToolRegistry capability snapshot is not initialized"
                )
            return snapshot

        try:
            bound_tools: list[ToolDefinition] = []
            existing_names = {tool.name for tool in tools}
            for factory in factories:
                tool = factory(capability_registry_provider)
                if not isinstance(tool, ToolDefinition):
                    raise TypeError(
                        "registry-bound tool factory must return ToolDefinition"
                    )
                if tool.name in existing_names:
                    raise ValueError(
                        f"registry-bound tool duplicates existing tool: {tool.name}"
                    )
                existing_names.add(tool.name)
                bound_tools.append(tool)
                self._registry_bound_tool_factories[tool.name] = factory

            all_tools = tuple([*tools, *bound_tools])
            self._snapshot = _RegistrySnapshot(
                tools=all_tools,
                index=MappingProxyType({tool.name: tool for tool in all_tools}),
                skills=tuple(copy.deepcopy(skills or [])),
            )
            if factories:
                # Import lazily to keep the foundational tooling module free of
                # a module-import cycle. The snapshot is built only after the
                # final tool collection exists, so every bound tool observes
                # exactly this registry's surface.
                from minicode.capability_registry import (
                    build_tool_capability_snapshot,
                )

                capability_snapshot_holder["snapshot"] = build_tool_capability_snapshot(
                    self
                )
        except BaseException:
            # MCP discovery may already have started child processes before
            # registry-bound tools are assembled. A failed binding must not
            # strand those resources merely because no registry is returned.
            if disposer is not None:
                try:
                    disposer()
                except BaseException as cleanup_error:  # noqa: BLE001
                    _logger.warning(
                        "ToolRegistry binding cleanup failed: %s",
                        type(cleanup_error).__name__,
                    )
            raise

    def list(self) -> list[ToolDefinition]:
        return list(self._snapshot.tools)

    def list_all(self) -> list[str]:
        return list(self._snapshot.index)

    def get_skills(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self._snapshot.skills))

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        return list(self._mcp_servers)

    def find(self, name: str) -> ToolDefinition | None:
        # O(1) lookup via cached index
        return self._snapshot.index.get(name)

    def refresh_skill_catalog(self) -> bool | None:
        """Replace Skill discovery tools together, only between owner turns.

        MCP connections and ordinary tools keep their identities. A filtered
        registry and any previously captured tool keep their original snapshot.
        The owner must not call this while its agent turn is still running.
        None means the registry does not own a refreshable Skill catalog.
        """
        if self._skill_catalog_factory is None:
            return None
        skills, replacements = self._skill_catalog_factory()
        if skills == list(self._snapshot.skills):
            return False
        replacement_index = {tool.name: tool for tool in replacements}
        if len(replacement_index) != len(replacements) or not set(
            replacement_index
        ).issubset(self._snapshot.index):
            raise ValueError("Skill refresh must replace existing unique tools")
        # Build and validate the whole replacement before publishing it. Bound
        # capability tools must capture the new registry's actual tool surface.
        staged = ToolRegistry(
            [
                replacement_index.get(tool.name, tool)
                for tool in self._snapshot.tools
                if tool.name not in self._registry_bound_tool_factories
            ],
            skills=skills,
            mcp_servers=self._mcp_servers,
            mcp_current_state_registry=self._mcp_current_state_registry,
            registry_bound_tool_factories=list(
                self._registry_bound_tool_factories.values()
            ),
        )
        self._snapshot = staged._snapshot
        return True

    def filtered(
        self,
        predicate: Callable[[ToolDefinition], bool],
        *,
        skills: list[dict[str, Any]] | None = None,
    ) -> ToolRegistry:
        """Return a non-owning filtered registry with fresh bound-tool state.

        Registry-bound tools are recreated rather than copied so their
        capability providers describe the final filtered collection. The
        returned registry borrows MCP-backed definitions from this registry;
        this registry remains their lifecycle owner and must outlive it.
        """
        selected_tools: list[ToolDefinition] = []
        selected_factories: list[RegistryBoundToolFactory] = []
        for tool in self._snapshot.tools:
            if not predicate(tool):
                continue
            factory = self._registry_bound_tool_factories.get(tool.name)
            if factory is None:
                selected_tools.append(tool)
            else:
                selected_factories.append(factory)
        filtered = ToolRegistry(
            selected_tools,
            skills=self.get_skills() if skills is None else skills,
            mcp_servers=self._mcp_servers,
            mcp_current_state_registry=self._mcp_current_state_registry,
            registry_bound_tool_factories=selected_factories,
        )
        filtered._owns_runtime_resources = False
        return filtered

    def overlay(
        self,
        replacements: list[ToolDefinition],
        *,
        remove_names: frozenset[str] = frozenset(),
    ) -> ToolRegistry:
        """Borrow this registry with replacements and fresh capability bindings.

        Replacements override same-name tools and append new names. Removals
        apply to the base surface, so a replacement can reinstall that name.
        Catalog refresh remains available without resurrecting removed tools
        or replacing an explicit override. This registry retains ownership of
        shared runtime resources and must outlive the returned overlay.
        """
        if any(not isinstance(tool, ToolDefinition) for tool in replacements):
            raise TypeError("registry overlay requires ToolDefinition replacements")
        overrides = {tool.name: tool for tool in replacements}
        if len(overrides) != len(replacements):
            raise ValueError("registry overlay has duplicate replacement names")
        removed = frozenset(remove_names)
        excluded = removed | overrides.keys()
        tools = [tool for tool in self._snapshot.tools
                 if tool.name not in excluded
                 and tool.name not in self._registry_bound_tool_factories]
        factories = [factory for name, factory in self._registry_bound_tool_factories.items()
                     if name not in excluded]
        catalog_factory = self._skill_catalog_factory
        refresh = None
        if catalog_factory is not None:
            def refresh() -> tuple[list[dict[str, Any]], list[ToolDefinition]]:
                skills, refreshed_tools = catalog_factory()
                return skills, [overrides.get(tool.name, tool) for tool in refreshed_tools
                                if tool.name not in removed or tool.name in overrides]

        overlay = ToolRegistry(
            [*tools, *replacements], skills=self.get_skills(),
            mcp_servers=self._mcp_servers,
            mcp_current_state_registry=self._mcp_current_state_registry,
            registry_bound_tool_factories=factories,
            skill_catalog_factory=refresh,
        )
        overlay._owns_runtime_resources = False
        return overlay

    def describe_capability(
        self, tool_name: str, arguments: object = _CAPABILITY_ARGUMENTS_UNSET
    ) -> CapabilityProfile | None:
        """Inspect static or invocation-specific effects without running a tool."""
        from minicode.capability_registry import (
            describe_capability_call,
            describe_tool_capability,
        )

        tool = self.find(tool_name)
        if tool is None:
            return None
        profile = describe_tool_capability(tool)
        if arguments is _CAPABILITY_ARGUMENTS_UNSET:
            return profile
        return describe_capability_call(profile, tool.capability_for, arguments)

    def execute(self, tool_name: str, input_data: Any, context: ToolContext) -> ToolResult:
        """Execute a tool with comprehensive error protection.
        
        The global exception safety net catches ALL exceptions (except
        KeyboardInterrupt/SystemExit) and converts them to error ToolResults,
        preventing a single tool crash from cascading into a full session failure.
        
        Protection layers:
        1. Tool not found → error result
        2. Validation error → error result with input details
        3. Execution error → error result with traceback excerpt
        4. Output too large → smart truncation
        5. Unexpected errors → error result (never propagates to caller)
        """
        tool = self.find(tool_name)
        if tool is None:
            return ToolResult(ok=False, output=f"Unknown tool: {tool_name}")

        result: ToolResult | None = None
        try:
            abandoned = context._tool_abandoned
            if abandoned is not None and abandoned.is_set():
                raise ToolExecutionAbandoned(
                    "owning tool execution was abandoned before dispatch"
                )
            if self._mcp_current_state_registry is not None:
                context._mcp_current_state_registry = (
                    self._mcp_current_state_registry
                )
            # Phase 1: Input validation (with error context)
            try:
                parsed = tool.validator(input_data)
            except (ValueError, TypeError, KeyError) as ve:
                safe_projection = getattr(ve, "tool_output", None)
                if (
                    getattr(ve, "_model_safe_tool_output", False) is True
                    and callable(safe_projection)
                ):
                    projected = safe_projection()
                    if (
                        isinstance(projected, str)
                        and re.fullmatch(
                            r"error\[[a-z_]{1,64}\]: [^\r\n]{1,256}",
                            projected,
                        )
                        is not None
                    ):
                        return ToolResult(ok=False, output=projected)
                # Raw input can carry file contents or secrets a previous
                # turn exfiltrated into arguments; echo only the validator's
                # own message, never the input itself.
                return ToolResult(
                    ok=False,
                    output=f"Input validation error in {tool_name}: {ve}",
                )
            
            # Phase 2: Execution (with crash protection)
            if abandoned is not None and abandoned.is_set():
                raise ToolExecutionAbandoned(
                    "owning tool execution was abandoned before execution"
                )
            # Classification is descriptive audit output, not a permission or
            # scheduling gate. Do not let metadata failures replace a tool result.
            capability_payload = None
            try:
                profile = self.describe_capability(tool_name, input_data)
                if profile is not None:
                    capability_payload = profile.to_dict()
            except Exception:  # noqa: BLE001 - preserve completed tool effects
                _logger.debug("Capability description unavailable for %s", tool_name)
            # The owner may have abandoned execution while metadata was being
            # described. Keep this checkpoint adjacent to the real dispatch.
            abandoned = context._tool_abandoned
            if abandoned is not None and abandoned.is_set():
                raise ToolExecutionAbandoned(
                    "owning tool execution was abandoned before execution"
                )
            result = tool.run(parsed, context)
            if capability_payload is not None:
                result.capability = capability_payload
            if getattr(result, "_must_deliver", False) is True:
                return result
            
            # Phase 3: Output sanitization
            if result.output is None:
                result.output = ""
            
            # Smart truncation for large outputs
            if result.output and len(result.output) > _LARGE_OUTPUT_THRESHOLD:
                result.output = _smart_truncate_output(result.output, tool_name)
            
            return result
            
        except (KeyboardInterrupt, SystemExit, ToolExecutionAbandoned):
            if getattr(result, "_must_deliver", False) is True:
                return result
            # Control-flow exceptions propagate until state has been finalized.
            raise
        except Exception as error:  # noqa: BLE001
            if getattr(result, "_must_deliver", False) is True:
                return result
            # Global safety net: convert any unhandled exception to error result
            # This prevents a single buggy tool from crashing the entire session
            safe_projection = getattr(error, "tool_output", None)
            if (
                getattr(error, "_model_safe_tool_output", False) is True
                and callable(safe_projection)
            ):
                try:
                    projected = safe_projection(tool_name)
                except Exception:  # noqa: BLE001 - projection is best-effort
                    projected = None
                projected_match = (
                    re.match(r"^error\[([a-z_]{1,64})\]: ", projected)
                    if isinstance(projected, str)
                    else None
                )
                if (
                    projected_match is not None
                    and len(projected.encode("utf-8")) <= 1_024
                    and "\n" not in projected
                    and "\r" not in projected
                ):
                    _logger.info(
                        "Tool %s returned controlled error[%s].",
                        tool_name,
                        projected_match.group(1),
                    )
                    return ToolResult(ok=False, output=projected)

            import traceback
            tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
            # Include last 5 lines of traceback for debugging
            tb_excerpt = "".join(tb_lines[-5:]).strip()
            error_type = type(error).__name__
            
            # The message and traceback routinely carry absolute local paths
            # and interpreter internals. This output goes straight to the
            # model (and the user), so it is redacted to a closed shape:
            # exception type and tool name only. `tb_excerpt` is kept for the
            # local log, which is not model-visible.
            _logger.warning(
                "Tool %s crashed: %s: %s\n%s",
                tool_name, error_type, error, tb_excerpt,
            )
            return ToolResult(
                ok=False,
                output=(
                    f"error[tool_crashed]: Tool {tool_name} failed with "
                    f"{error_type}. Details were written to the local log."
                ),
            )

    def dispose(self) -> None:
        if not self._owns_runtime_resources:
            return
        # Background shells are process-owned rather than tied to one MCP
        # client. Every runtime entry point disposes its registry, making this
        # the shared idempotent application-shutdown boundary.
        from minicode.background_tasks import shutdown_background_tasks

        shutdown_background_tasks()
        if self._disposer is not None:
            self._disposer()
