"""Descriptive capability contracts shared by tools and routing snapshots.

These records describe possible effects; they are never execution permission.
Missing evidence stays unknown, independently on each effect axis.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CapabilityDomain(str, Enum):
    FILE = "file"
    CODE = "code"
    SEARCH = "search"
    WEB = "web"
    SYSTEM = "system"
    MEMORY = "memory"
    COMMUNICATION = "communication"
    ANALYSIS = "analysis"
    EXECUTION = "execution"
    UNKNOWN = "unknown"


class CapabilityScope(str, Enum):
    READONLY = "readonly"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class CapabilitySource(str, Enum):
    EXPLICIT = "explicit"
    BUILTIN = "builtin"
    LOCAL_CONFIG = "local_config"
    MCP_ANNOTATIONS = "mcp_annotations"
    LEGACY_NAME = "legacy_name"
    UNKNOWN = "unknown"


class CapabilityStatus(str, Enum):
    DECLARED = "declared"
    HINTED = "hinted"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CapabilityEffects:
    """Independent effect axes, relative to the operation's target resources.

For delegation, read_only describes the worker's workspace access, not the
host's bookkeeping. External includes model/service calls, even for reads.
None means the contract does not establish that property.
"""

    read_only: bool | None = None
    destructive: bool | None = None
    external: bool | None = None
    executes_code: bool | None = None
    delegates: bool | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{name} must be a bool or None")
        if self.read_only is True and self.destructive is True:
            raise ValueError("read-only effects cannot also be destructive")

    def to_dict(self) -> dict[str, bool | None]:
        return asdict(self)


def effects_for_scope(scope: CapabilityScope | str) -> CapabilityEffects:
    """Minimal facts encoded by a legacy scope, without inventing other facts."""
    scope = CapabilityScope(scope)
    if scope is CapabilityScope.READONLY:
        return CapabilityEffects(read_only=True, destructive=False)
    if scope is CapabilityScope.WRITE:
        return CapabilityEffects(read_only=False)
    if scope is CapabilityScope.DESTRUCTIVE:
        return CapabilityEffects(read_only=False, destructive=True)
    return CapabilityEffects(external=True)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{field_name} must be a list or tuple of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain nonempty strings")
    if len(value) > 64 or any(len(item) > 256 for item in value):
        raise ValueError(f"{field_name} exceeds metadata limits")
    return tuple(dict.fromkeys(item.strip() for item in value))


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    domain: CapabilityDomain
    scope: CapabilityScope
    source: CapabilitySource = CapabilitySource.EXPLICIT
    status: CapabilityStatus = CapabilityStatus.DECLARED
    effects: CapabilityEffects = field(default_factory=CapabilityEffects)
    purposes: tuple[str, ...] = ()
    supported_operations: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        for name, enum in (
            ("domain", CapabilityDomain),
            ("scope", CapabilityScope),
            ("source", CapabilitySource),
            ("status", CapabilityStatus),
        ):
            object.__setattr__(self, name, enum(getattr(self, name)))
        if not isinstance(self.effects, CapabilityEffects):
            raise TypeError("effects must be CapabilityEffects")
        # Unknown axes are valid for aggregate tools, but explicit facts must
        # not contradict the compatibility scope presented to consumers.
        if self.scope is CapabilityScope.READONLY and (
            self.effects.read_only is False or self.effects.destructive is True
        ):
            raise ValueError("read-only scope conflicts with mutation effects")
        if self.scope in {CapabilityScope.WRITE, CapabilityScope.DESTRUCTIVE} and (
            self.effects.read_only is True
        ):
            raise ValueError("mutation scope conflicts with read-only effects")
        if self.scope is CapabilityScope.DESTRUCTIVE and self.effects.destructive is False:
            raise ValueError("destructive scope conflicts with nondestructive effects")
        if self.scope is CapabilityScope.EXTERNAL and self.effects.external is False:
            raise ValueError("external scope conflicts with local-only effects")
        for name in ("purposes", "supported_operations", "evidence"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if any(
            re.fullmatch(r"[a-z_]+\.[a-z_]+", operation) is None
            for operation in self.supported_operations
        ):
            raise ValueError("supported_operations must use intent.action syntax")
        if self.schema_version != "1.0":
            raise ValueError("Unsupported capability profile schema version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "domain": self.domain.value,
            "scope": self.scope.value,
            "source": self.source.value,
            "status": self.status.value,
            "effects": self.effects.to_dict(),
            "purposes": list(self.purposes),
            "supported_operations": list(self.supported_operations),
            "evidence": list(self.evidence),
        }


CapabilityClassifier = Callable[[Mapping[str, Any]], CapabilityProfile]


def unresolved_call(
    profile: CapabilityProfile,
    reason: str,
) -> CapabilityProfile:
    """Keep failed invocation classification distinct from a known danger."""
    return CapabilityProfile(
        domain=profile.domain,
        scope=profile.scope,
        source=profile.source,
        status=CapabilityStatus.INVALID,
        effects=CapabilityEffects(),
        evidence=(reason,),
    )
