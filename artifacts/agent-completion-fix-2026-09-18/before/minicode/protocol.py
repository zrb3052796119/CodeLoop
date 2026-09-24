"""Protocol - Tool interface standardization.

Inspired by Runtime Triad:
protocol + client kernel + server kernel = runtime triad
Tools are not isolated functions but protocol implementations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from minicode.logging_config import get_logger

logger = get_logger("protocol")


class ProtocolType(str, Enum):
    FILE_OPERATION = "file_operation"
    CODE_ANALYSIS = "code_analysis"
    WEB_ACCESS = "web_access"
    COMMAND_EXEC = "command_exec"
    SEARCH_QUERY = "search_query"
    COMMUNICATION = "communication"
    DATA_STORAGE = "data_storage"
    CUSTOM = "custom"


class ProtocolDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class ProtocolDefinition:
    name: str
    protocol_type: ProtocolType
    direction: ProtocolDirection
    version: str = "1.0.0"
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    optional_params: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    error_codes: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def validate_input(self, params: dict[str, Any]) -> tuple[bool, str]:
        for param in self.required_params:
            if param not in params:
                return False, f"Missing required parameter: {param}"
        all_allowed = set(self.required_params) | set(self.optional_params)
        for param in params:
            if param not in all_allowed:
                return False, f"Unknown parameter: {param}"
        return True, ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "protocol_type": self.protocol_type.value,
            "direction": self.direction.value, "version": self.version,
            "description": self.description, "input_schema": self.input_schema,
            "required_params": self.required_params, "optional_params": self.optional_params,
            "output_schema": self.output_schema, "error_codes": self.error_codes,
        }


@dataclass
class ProtocolMessage:
    protocol_name: str
    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    message_id: str = ""
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_name": self.protocol_name, "message_type": self.message_type,
            "payload": self.payload, "headers": self.headers,
            "timestamp": self.timestamp, "message_id": self.message_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def request(cls, protocol_name: str, payload: dict[str, Any], correlation_id: str = "") -> "ProtocolMessage":
        return cls(protocol_name=protocol_name, message_type="request", payload=payload, correlation_id=correlation_id)

    @classmethod
    def response(cls, protocol_name: str, payload: dict[str, Any], correlation_id: str) -> "ProtocolMessage":
        return cls(protocol_name=protocol_name, message_type="response", payload=payload, correlation_id=correlation_id)

    @classmethod
    def error(cls, protocol_name: str, error_code: str, error_message: str, correlation_id: str = "") -> "ProtocolMessage":
        return cls(protocol_name=protocol_name, message_type="error", payload={"code": error_code, "message": error_message}, correlation_id=correlation_id)


@runtime_checkable
class ProtocolHandler(Protocol):
    @property
    def protocol_definition(self) -> ProtocolDefinition: ...
    def handle(self, message: ProtocolMessage) -> ProtocolMessage: ...
    def is_available(self) -> bool: ...


class ProtocolRegistry:
    def __init__(self):
        self._definitions: dict[str, ProtocolDefinition] = {}
        self._handlers: dict[str, ProtocolHandler] = {}
        self._type_index: dict[ProtocolType, set[str]] = {}

    def register_definition(self, definition: ProtocolDefinition) -> None:
        name = definition.name
        self._definitions[name] = definition
        ptype = definition.protocol_type
        if ptype not in self._type_index:
            self._type_index[ptype] = set()
        self._type_index[ptype].add(name)
        logger.debug("Registered protocol definition: %s (%s)", name, ptype.value)

    def register_handler(self, protocol_name: str, handler: ProtocolHandler) -> None:
        if protocol_name not in self._definitions:
            raise ValueError(f"Protocol '{protocol_name}' not defined")
        self._handlers[protocol_name] = handler
        logger.debug("Registered protocol handler: %s", protocol_name)

    def get_definition(self, name: str) -> ProtocolDefinition | None:
        return self._definitions.get(name)

    def get_handler(self, name: str) -> ProtocolHandler | None:
        return self._handlers.get(name)

    def has_protocol(self, name: str) -> bool:
        return name in self._definitions

    def list_protocols(self) -> list[str]:
        return list(self._definitions.keys())

    def list_by_type(self, protocol_type: ProtocolType) -> list[str]:
        return list(self._type_index.get(protocol_type, set()))

    def dispatch(self, message: ProtocolMessage) -> ProtocolMessage:
        protocol_name = message.protocol_name
        definition = self._definitions.get(protocol_name)
        if not definition:
            return ProtocolMessage.error(protocol_name, "PROTOCOL_NOT_FOUND", f"Protocol '{protocol_name}' not registered", message.correlation_id)
        handler = self._handlers.get(protocol_name)
        if not handler:
            return ProtocolMessage.error(protocol_name, "HANDLER_NOT_FOUND", f"No handler registered for protocol '{protocol_name}'", message.correlation_id)
        if message.message_type == "request":
            is_valid, error = definition.validate_input(message.payload)
            if not is_valid:
                return ProtocolMessage.error(protocol_name, "INVALID_INPUT", error, message.correlation_id)
        try:
            return handler.handle(message)
        except Exception as e:
            logger.exception("Protocol handler error: %s", protocol_name)
            return ProtocolMessage.error(protocol_name, "HANDLER_ERROR", str(e), message.correlation_id)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_protocols": len(self._definitions),
            "total_handlers": len(self._handlers),
            "unhandled_protocols": [name for name in self._definitions if name not in self._handlers],
            "types": {ptype.value: len(names) for ptype, names in self._type_index.items()},
        }


def _create_file_operation_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="file_operation", protocol_type=ProtocolType.FILE_OPERATION,
        direction=ProtocolDirection.BIDIRECTIONAL,
        description="Standard protocol for file read/write/append operations",
        required_params=["operation", "path"], optional_params=["content", "encoding", "create_dirs"],
        error_codes={
            "FILE_NOT_FOUND": "The specified file does not exist",
            "PERMISSION_DENIED": "Insufficient permissions for the operation",
            "PATH_TOO_LONG": "The file path exceeds maximum length",
            "INVALID_ENCODING": "The specified encoding is not supported",
        },
    )


def _create_code_analysis_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="code_analysis", protocol_type=ProtocolType.CODE_ANALYSIS,
        direction=ProtocolDirection.INBOUND,
        description="Protocol for code analysis, review, and suggestion",
        required_params=["action", "code"], optional_params=["language", "context", "rules"],
        error_codes={
            "INVALID_CODE": "The provided code is invalid or malformed",
            "UNSUPPORTED_LANGUAGE": "The programming language is not supported",
            "ANALYSIS_FAILED": "Code analysis failed internally",
        },
    )


def _create_web_access_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="web_access", protocol_type=ProtocolType.WEB_ACCESS,
        direction=ProtocolDirection.OUTBOUND,
        description="Protocol for web fetching and searching",
        required_params=["action"], optional_params=["url", "query", "headers", "timeout"],
        error_codes={
            "NETWORK_ERROR": "Failed to connect to the target",
            "TIMEOUT": "The request timed out",
            "INVALID_URL": "The URL format is invalid",
            "SSRF_BLOCKED": "Access to internal addresses is blocked",
        },
    )


def _create_command_exec_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="command_exec", protocol_type=ProtocolType.COMMAND_EXEC,
        direction=ProtocolDirection.OUTBOUND,
        description="Protocol for command execution",
        required_params=["command"], optional_params=["cwd", "env", "timeout", "shell"],
        error_codes={
            "COMMAND_NOT_FOUND": "The specified command was not found",
            "EXECUTION_FAILED": "Command execution failed",
            "TIMEOUT": "Command execution timed out",
            "PERMISSION_DENIED": "Insufficient permissions to execute",
        },
    )


def _create_search_query_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="search_query", protocol_type=ProtocolType.SEARCH_QUERY,
        direction=ProtocolDirection.OUTBOUND,
        description="Protocol for code and content search",
        required_params=["query"], optional_params=["scope", "filters", "limit", "case_sensitive"],
        error_codes={
            "INVALID_QUERY": "The search query is invalid",
            "NO_RESULTS": "No results found for the query",
            "SEARCH_FAILED": "Search operation failed",
        },
    )


_registry: ProtocolRegistry | None = None


def get_protocol_registry() -> ProtocolRegistry:
    global _registry
    if _registry is None:
        _registry = ProtocolRegistry()
        _registry.register_definition(_create_file_operation_protocol())
        _registry.register_definition(_create_code_analysis_protocol())
        _registry.register_definition(_create_web_access_protocol())
        _registry.register_definition(_create_command_exec_protocol())
        _registry.register_definition(_create_search_query_protocol())
    return _registry
