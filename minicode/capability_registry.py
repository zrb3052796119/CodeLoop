"""Capability Registry - Self-describing tool registration system.

Inspired by Skill architecture: each capability self-describes, self-registers,
has dependency graph. Tools are not isolated functions but triggerable,
readable, extensible capability units.
"""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from minicode.capability_contracts import (
    CapabilityClassifier,
    CapabilityDomain,
    CapabilityEffects,
    CapabilityProfile,
    CapabilityScope,
    CapabilitySource,
    CapabilityStatus,
    effects_for_scope,
    unresolved_call,
)
from minicode.logging_config import get_logger

logger = get_logger("capability_registry")


@dataclass(frozen=True)
class CapabilityMetadata:
    name: str
    domain: CapabilityDomain
    scope: CapabilityScope
    description: str
    version: str = "1.0.0"
    author: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    required_permissions: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    profile: CapabilityProfile | None = None

    def __post_init__(self) -> None:
        # Accept the historical list-shaped constructor inputs while storing
        # an immutable snapshot. Capability metadata participates in routing
        # indexes, so mutating it after registration would silently corrupt
        # the registry's semantic view.
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(
            self,
            "required_permissions",
            tuple(self.required_permissions),
        )
        object.__setattr__(self, "examples", tuple(self.examples))
        object.__setattr__(self, "tags", tuple(self.tags))
        if self.profile is None:
            object.__setattr__(self, "profile", CapabilityProfile(
                domain=self.domain,
                scope=self.scope,
                effects=effects_for_scope(self.scope),
            ))
        elif not isinstance(self.profile, CapabilityProfile):
            raise TypeError("profile must be CapabilityProfile")
        if self.profile.domain != self.domain or self.profile.scope != self.scope:
            raise ValueError("Capability profile must match its metadata projection")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "domain": self.domain.value,
            "scope": self.scope.value, "description": self.description,
            "version": self.version, "author": self.author,
            "dependencies": list(self.dependencies),
            "required_permissions": list(self.required_permissions),
            "examples": list(self.examples), "tags": list(self.tags),
            "created_at": self.created_at, "updated_at": self.updated_at,
            "profile": self.profile.to_dict(),
        }


@runtime_checkable
class Capability(Protocol):
    @property
    def metadata(self) -> CapabilityMetadata: ...
    def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...
    def validate(self, params: dict[str, Any]) -> tuple[bool, str]: ...


@dataclass
class RegisteredCapability:
    metadata: CapabilityMetadata
    handler: Callable[..., Any]
    validator: Callable[[dict[str, Any]], tuple[bool, str]] | None = None
    instance: Any | None = None
    call_count: int = 0
    total_execution_time: float = 0.0
    last_used: float = 0.0
    classifier: CapabilityClassifier | None = field(default=None, repr=False)

    def execute(self, params: dict[str, Any]) -> Any:
        start = time.time()
        self.call_count += 1
        self.last_used = start
        try:
            if self.instance is not None:
                result = self.handler(self.instance, **params)
            else:
                result = self.handler(**params)
            self.total_execution_time += time.time() - start
            return result
        except Exception:
            self.total_execution_time += time.time() - start
            raise

    def validate(self, params: dict[str, Any]) -> tuple[bool, str]:
        if self.validator:
            return self.validator(params)
        return True, ""

    @property
    def avg_execution_time(self) -> float:
        return self.total_execution_time / self.call_count if self.call_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "call_count": self.call_count,
            "avg_execution_time_ms": round(self.avg_execution_time * 1000, 2),
            "last_used": self.last_used,
        }


@dataclass(frozen=True)
class FrozenRegisteredCapability:
    """Read-only public view returned by a frozen registry.

    Handler, validation, and statistics state are copied out of the mutable
    registration, so callers cannot reach back through the routing snapshot
    returned by :meth:`CapabilityRegistry.get`.
    """

    metadata: CapabilityMetadata
    _handler: Callable[..., Any] = field(repr=False, compare=False)
    _validator: Callable[[dict[str, Any]], tuple[bool, str]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _instance: Any | None = field(default=None, repr=False, compare=False)
    _call_count: int = field(default=0, repr=False, compare=False)
    _total_execution_time: float = field(default=0.0, repr=False, compare=False)
    _last_used: float = field(default=0.0, repr=False, compare=False)
    classifier: CapabilityClassifier | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_registered(
        cls,
        capability: RegisteredCapability,
    ) -> FrozenRegisteredCapability:
        """Detach a frozen public view from the mutable registration object."""
        return cls(
            metadata=capability.metadata,
            _handler=capability.handler,
            _validator=capability.validator,
            _instance=capability.instance,
            _call_count=capability.call_count,
            _total_execution_time=capability.total_execution_time,
            _last_used=capability.last_used,
            classifier=capability.classifier,
        )

    def execute(self, params: dict[str, Any]) -> Any:
        start = time.time()
        object.__setattr__(self, "_call_count", self._call_count + 1)
        object.__setattr__(self, "_last_used", start)
        try:
            if self._instance is not None:
                result = self._handler(self._instance, **params)
            else:
                result = self._handler(**params)
            object.__setattr__(
                self,
                "_total_execution_time",
                self._total_execution_time + time.time() - start,
            )
            return result
        except Exception:
            object.__setattr__(
                self,
                "_total_execution_time",
                self._total_execution_time + time.time() - start,
            )
            raise

    def validate(self, params: dict[str, Any]) -> tuple[bool, str]:
        if self._validator:
            return self._validator(params)
        return True, ""

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def total_execution_time(self) -> float:
        return self._total_execution_time

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def avg_execution_time(self) -> float:
        return self.total_execution_time / self.call_count if self.call_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "call_count": self.call_count,
            "avg_execution_time_ms": round(self.avg_execution_time * 1000, 2),
            "last_used": self.last_used,
        }


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: dict[
            str,
            RegisteredCapability | FrozenRegisteredCapability,
        ] = {}
        self._domain_index: dict[CapabilityDomain, set[str]] = {}
        self._tag_index: dict[str, set[str]] = {}
        self._dependency_graph: dict[str, set[str]] = {}
        self._tool_capability_names: set[str] = set()
        self._frozen = False

    @property
    def is_frozen(self) -> bool:
        """Whether the registry's capability membership is immutable."""
        return self._frozen

    def freeze(self) -> CapabilityRegistry:
        """Prevent structural changes while retaining read and execute access.

        The detached public views retain execution statistics; freezing
        protects the semantic membership and indexes used by routing.
        """
        if self._frozen:
            return self
        self._capabilities = {
            name: FrozenRegisteredCapability.from_registered(capability)
            for name, capability in self._capabilities.items()
            if isinstance(capability, RegisteredCapability)
        }
        self._frozen = True
        return self

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("CapabilityRegistry snapshot is frozen")

    def _discard_indexes(
        self,
        name: str,
        cap: RegisteredCapability | FrozenRegisteredCapability,
    ) -> None:
        domain_names = self._domain_index.get(cap.metadata.domain)
        if domain_names is not None:
            domain_names.discard(name)
            if not domain_names:
                self._domain_index.pop(cap.metadata.domain, None)
        for tag in cap.metadata.tags:
            tag_names = self._tag_index.get(tag)
            if tag_names is not None:
                tag_names.discard(name)
                if not tag_names:
                    self._tag_index.pop(tag, None)

    def register(self, metadata: CapabilityMetadata, handler: Callable[..., Any],
                 validator: Callable | None = None, instance: Any | None = None,
                 *, classifier: CapabilityClassifier | None = None) -> RegisteredCapability:
        self._ensure_mutable()
        name = metadata.name
        previous = self._capabilities.get(name)
        if previous is not None:
            logger.warning("Capability '%s' already registered, updating", name)
            self._discard_indexes(name, previous)

        cap = RegisteredCapability(metadata=metadata, handler=handler, validator=validator, instance=instance, classifier=classifier)
        self._capabilities[name] = cap
        # A generic registration with the same name takes ownership away from
        # the ToolRegistry reconciliation path.  Tool-backed registrations add
        # the marker again after this method returns.
        self._tool_capability_names.discard(name)

        domain = metadata.domain
        if domain not in self._domain_index:
            self._domain_index[domain] = set()
        self._domain_index[domain].add(name)

        for tag in metadata.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(name)

        self._dependency_graph[name] = set(metadata.dependencies)
        logger.debug("Registered capability: %s (%s)", name, domain.value)
        return cap

    def unregister(self, name: str) -> bool:
        self._ensure_mutable()
        if name not in self._capabilities:
            return False
        cap = self._capabilities.pop(name)
        self._discard_indexes(name, cap)
        self._dependency_graph.pop(name, None)
        self._tool_capability_names.discard(name)
        logger.debug("Unregistered capability: %s", name)
        return True

    def get(
        self,
        name: str,
    ) -> RegisteredCapability | FrozenRegisteredCapability | None:
        return self._capabilities.get(name)

    def has(self, name: str) -> bool:
        return name in self._capabilities

    def describe_call(self, name: str, arguments: object) -> CapabilityProfile | None:
        """Describe a proposed invocation without running it or granting access.

        Only host-installed classifiers run here. Server annotations never
        install executable callbacks. An invalid/unsupported invocation remains
        unknown rather than inheriting an optimistic classification.
        """
        capability = self.get(name)
        if capability is None:
            return None
        return describe_capability_call(
            capability.metadata.profile, capability.classifier, arguments
        )

    def list_all(self) -> list[str]:
        return list(self._capabilities.keys())

    def list_by_domain(self, domain: CapabilityDomain) -> list[str]:
        return list(self._domain_index.get(domain, set()))

    def list_by_tag(self, tag: str) -> list[str]:
        return list(self._tag_index.get(tag, set()))

    def search(self, query: str) -> list[tuple[str, float]]:
        query_lower = query.lower()
        results: list[tuple[str, float]] = []
        for name, cap in self._capabilities.items():
            score = 0.0
            if query_lower in name.lower():
                score += 1.0
            if query_lower in cap.metadata.description.lower():
                score += 0.5
            for tag in cap.metadata.tags:
                if query_lower in tag.lower():
                    score += 0.3
            if query_lower in cap.metadata.domain.value.lower():
                score += 0.2
            if score > 0:
                results.append((name, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_dependencies(self, name: str) -> set[str]:
        return self._dependency_graph.get(name, set()).copy()

    def get_all_dependencies(self, name: str) -> set[str]:
        visited: set[str] = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for dep in self._dependency_graph.get(current, set()):
                if dep not in visited:
                    stack.append(dep)
        visited.discard(name)
        return visited

    def check_dependencies(self, name: str) -> tuple[bool, list[str]]:
        deps = self._dependency_graph.get(name, set())
        missing = [d for d in deps if d not in self._capabilities]
        return len(missing) == 0, missing

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_capabilities": len(self._capabilities),
            "domains": {domain.value: len(caps) for domain, caps in self._domain_index.items()},
            "tags": {tag: len(caps) for tag, caps in self._tag_index.items()},
            "most_used": sorted(
                [(name, cap.call_count) for name, cap in self._capabilities.items()],
                key=lambda x: x[1], reverse=True,
            )[:10],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": {
                name: cap.to_dict() for name, cap in self._capabilities.items()
            },
            "stats": self.get_stats(),
        }


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry


_TOOL_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tool_name_tokens(tool_name: str) -> frozenset[str]:
    return frozenset(_TOOL_NAME_TOKEN_RE.findall(tool_name.lower()))


def infer_tool_domain(tool_name: str) -> CapabilityDomain:
    tokens = _tool_name_tokens(tool_name)
    if tokens & {"ask", "chat", "email", "mail", "message", "notification", "notify"}:
        return CapabilityDomain.COMMUNICATION
    if tokens & {"archive", "directory", "file", "files", "filesystem", "folder", "path"}:
        return CapabilityDomain.FILE
    if tokens & {"find", "grep", "lookup", "search"}:
        return CapabilityDomain.SEARCH
    if tokens & {"api", "fetch", "http", "https", "network", "url", "web"}:
        return CapabilityDomain.WEB
    if tokens & {"command", "exec", "execute", "run", "shell"}:
        return CapabilityDomain.EXECUTION
    if tokens & {"ast", "code", "diff", "reference", "review", "symbol"}:
        return CapabilityDomain.CODE
    if "memory" in tokens:
        return CapabilityDomain.MEMORY
    if tokens & {"analysis", "analyze", "inspect"}:
        return CapabilityDomain.ANALYSIS
    if tokens & {"config", "configuration", "setting", "settings", "system"}:
        return CapabilityDomain.SYSTEM
    return CapabilityDomain.UNKNOWN


def infer_tool_scope(tool_name: str) -> CapabilityScope:
    normalized = tool_name.lower()
    tokens = _tool_name_tokens(tool_name)
    if tokens & {
        "command",
        "delete",
        "destroy",
        "drop",
        "exec",
        "execute",
        "move",
        "purge",
        "remove",
        "rename",
        "run",
        "truncate",
    }:
        return CapabilityScope.DESTRUCTIVE
    if normalized.startswith("mcp__") or tokens & {
        "api",
        "download",
        "email",
        "fetch",
        "http",
        "https",
        "mail",
        "network",
        "notification",
        "notify",
        "post",
        "publish",
        "request",
        "send",
        "upload",
        "web",
    }:
        return CapabilityScope.EXTERNAL
    if tokens & {"copy", "create", "edit", "modify", "patch", "save", "update", "write"}:
        return CapabilityScope.WRITE
    if tokens & {
        "analysis",
        "analyze",
        "check",
        "compare",
        "count",
        "describe",
        "find",
        "grep",
        "inspect",
        "list",
        "lookup",
        "query",
        "read",
        "search",
        "show",
        "validate",
        "view",
    }:
        return CapabilityScope.READONLY
    # Preserve the historical scope for compatibility. Profiles mark this as
    # inferred with unknown effects; consumers must not treat it as evidence
    # of either destructive behavior or safety.
    return CapabilityScope.DESTRUCTIVE


def describe_tool_capability(tool: Any) -> CapabilityProfile:
    """Resolve the declaration first, retaining attribution on compatibility guesses."""
    profile = getattr(tool, "capability_profile", None)
    if profile is not None:
        if not isinstance(profile, CapabilityProfile):
            raise TypeError("capability_profile must be CapabilityProfile")
        return profile
    domain = getattr(tool, "capability_domain", None)
    scope = getattr(tool, "capability_scope", None)
    if domain is not None and scope is not None:
        return CapabilityProfile(
            domain=CapabilityDomain(domain),
            scope=CapabilityScope(scope),
            effects=effects_for_scope(scope),
            evidence=("explicit-tool-fields",),
        )
    return CapabilityProfile(
        domain=infer_tool_domain(tool.name),
        scope=infer_tool_scope(tool.name),
        source=CapabilitySource.LEGACY_NAME,
        status=CapabilityStatus.INFERRED,
        effects=CapabilityEffects(),
        evidence=("legacy-name-compatibility", "effects-unverified"),
    )


def describe_capability_call(
    profile: CapabilityProfile,
    classifier: CapabilityClassifier | None,
    arguments: object,
) -> CapabilityProfile:
    if not isinstance(arguments, Mapping):
        return unresolved_call(profile, "invalid-call-shape")
    if classifier is None:
        # This describes the tool, not validation or acceptance of these args.
        return profile
    try:
        # A descriptive callback must never mutate the invocation that will
        # actually execute, including nested JSON containers.
        result = classifier(deepcopy(dict(arguments)))
        if not isinstance(result, CapabilityProfile):
            return unresolved_call(profile, "invalid-classifier-result")
        return result
    except Exception:  # noqa: BLE001 - descriptive metadata cannot break execution
        # Do not copy exception messages: validators may include input/secrets.
        return unresolved_call(profile, "call-classification-failed")


def _tool_capability_definitions(
    tools: Any,
) -> dict[str, tuple[CapabilityMetadata, Callable[..., Any], CapabilityClassifier | None]]:
    definitions: dict[str, tuple[CapabilityMetadata, Callable[..., Any], CapabilityClassifier | None]] = {}
    for tool_name in tools.list_all():
        try:
            tool_def = tools.find(tool_name)
            if not tool_def:
                continue

            def _make_handler(name: str) -> Callable[..., Any]:
                def _handler(**kwargs: Any) -> Any:
                    del kwargs
                    raise RuntimeError(
                        f"Tool capability '{name}' is routing metadata only; "
                        "execute it through ToolRegistry with a host-owned "
                        "ToolContext"
                    )
                return _handler

            profile = describe_tool_capability(tool_def)

            metadata = CapabilityMetadata(
                name=tool_name,
                domain=profile.domain,
                scope=profile.scope,
                description=tool_def.description or f"Tool: {tool_name}",
                tags=["tool", tool_name],
                profile=profile,
            )
            definitions[tool_name] = (
                metadata, _make_handler(tool_name), getattr(tool_def, "capability_for", None)
            )
        except Exception as e:  # noqa: BLE001 - one malformed tool is isolated
            logger.debug("Failed to register tool %s as capability: %s", tool_name, e)
    return definitions


def _reconcile_tool_capabilities(
    registry: CapabilityRegistry,
    tools: Any,
) -> CapabilityRegistry:
    definitions = _tool_capability_definitions(tools)
    previously_managed = set(registry._tool_capability_names)

    # Remove tool capabilities that are not present in this ToolRegistry.
    # Manually/decorator-registered capabilities are deliberately preserved.
    for stale_name in previously_managed - definitions.keys():
        registry.unregister(stale_name)

    for tool_name, (metadata, handler, classifier) in definitions.items():
        if registry.has(tool_name) and tool_name not in previously_managed:
            logger.debug(
                "Preserving non-tool capability '%s' during tool reconciliation",
                tool_name,
            )
            continue
        registry.register(metadata, handler, None, classifier=classifier)
        registry._tool_capability_names.add(tool_name)
    return registry


def build_tool_capability_snapshot(tools: Any) -> CapabilityRegistry:
    """Build a structurally frozen capability view for one ToolRegistry.

    The returned registry owns fresh metadata, handlers, and indexes.  It does
    not read or mutate the process singleton, so later turns and independently
    filtered ToolRegistries cannot contaminate one another's routing inputs.
    """
    return _reconcile_tool_capabilities(CapabilityRegistry(), tools).freeze()


def register_tool_capabilities(tools: Any) -> CapabilityRegistry:
    """Reconcile the compatibility singleton with the current ToolRegistry.

    Existing callers still receive the process registry, but tool-backed
    entries are refreshed on every call and stale tool entries are removed.
    New routing code should prefer :func:`build_tool_capability_snapshot`.
    """
    return _reconcile_tool_capabilities(get_registry(), tools)


def capability(name: str, domain: CapabilityDomain, scope: CapabilityScope,
               description: str, version: str = "1.0.0",
               dependencies: list[str] | None = None,
               permissions: list[str] | None = None,
               tags: list[str] | None = None,
               examples: list[str] | None = None):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        metadata = CapabilityMetadata(
            name=name, domain=domain, scope=scope, description=description,
            version=version, dependencies=dependencies or [],
            required_permissions=permissions or [], tags=tags or [], examples=examples or [],
        )
        sig = inspect.signature(func)
        def validator(params: dict[str, Any]) -> tuple[bool, str]:
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                if param.default is inspect.Parameter.empty and param_name not in params:
                    return False, f"Missing required parameter: {param_name}"
            return True, ""
        get_registry().register(metadata, func, validator)
        return func
    return decorator


def register_instance_capability(instance: Any, method_name: str,
                                 metadata: CapabilityMetadata) -> RegisteredCapability:
    handler = getattr(instance, method_name)
    return get_registry().register(metadata, handler, instance=instance)
