from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from minicode.capability_contracts import (
    CapabilityDomain,
    CapabilityEffects,
    CapabilityProfile,
    CapabilityScope,
    CapabilitySource,
    CapabilityStatus,
    effects_for_scope,
)
from minicode.mcp_observation import (
    classify_mcp_failure,
    emit_mcp_runtime_observation_safely,
    mcp_server_key,
)
from minicode.tooling import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from minicode.mcp_current_state import McpCurrentStateRegistry

# 安全常量：禁止在命令参数中出现的危险字符
DANGEROUS_SHELL_CHARS = set('|&;`$(){}<>\n\r')

# MCP payload 大小上限（防止恶意服务端制造 OOM）
MAX_MCP_PAYLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# 允许的命令白名单（常见的 MCP 服务器命令）
ALLOWED_COMMANDS = {
    'node', 'npm', 'npx', 'python', 'python3', 'pip', 'pip3',
    'uv', 'deno', 'bun', 'cargo', 'go', 'java', 'javac',
    'ruby', 'gem', 'dotnet', 'curl', 'wget',
}


JsonRpcProtocol = str

# ---------------------------------------------------------------------------
# Timeout policy (layered, mirroring mainstream agent MCP clients)
# ---------------------------------------------------------------------------
# Tool calls routinely exceed a few seconds (indexes, browsers, builds), so a
# single hardcoded 5s budget made every real server unusable. Layers:
# initialize handshake -> list requests -> tool/resource/prompt calls, each
# overridable per server via config["timeout"] (seconds) or the env var for
# the tool-call default.
INITIALIZE_TIMEOUT_SECONDS = 10.0
LIST_TIMEOUT_SECONDS = 10.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 60.0
_HTTP_TRANSPORT_LABEL = "streamable-http"
# MCP spec version this client speaks. The server may negotiate an older one
# in its initialize result; we honor whatever it answers (recorded, not
# enforced) so older servers keep working.
_SUPPORTED_PROTOCOL_VERSION = "2025-06-18"

_ENV_TEMPLATE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _configured_tool_timeout(config: dict[str, Any]) -> float:
    """Resolve the per-server tool call timeout (seconds)."""
    configured = config.get("timeout")
    if isinstance(configured, (int, float)) and not isinstance(configured, bool):
        if configured > 0:
            return float(configured)
    env_override = os.environ.get("MINI_CODE_MCP_TOOL_TIMEOUT")
    if env_override:
        try:
            parsed = float(env_override)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_TOOL_TIMEOUT_SECONDS


def _expand_env_template(value: str) -> str:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in config strings.

    The Claude Code / Gemini CLI convention: secrets never live in the
    config file, they are referenced from the environment. An unset
    variable without a default is an error so misconfiguration surfaces at
    startup instead of silently sending a literal "${TOKEN}".
    """
    def replace(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if fallback is not None:
            return fallback
        raise RuntimeError(
            f"MCP config references undefined environment variable {name!r}"
        )

    return _ENV_TEMPLATE_RE.sub(replace, value)


@dataclass(slots=True)
class McpServerSummary:
    name: str
    command: str
    status: str
    toolCount: int
    error: str | None = None
    protocol: str | None = None
    resourceCount: int | None = None
    promptCount: int | None = None


def _sanitize_tool_segment(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() or char in {"_", "-"} else "_" for char in value)
    normalized = normalized.strip("_")
    return normalized or "tool"


def _validate_mcp_command(command: str) -> None:
    """验证 MCP 命令的合法性"""
    from pathlib import Path
    
    normalized = Path(command).resolve().as_posix()
    
    # 不允许路径遍历字符
    if '..' in normalized or '~' in normalized:
        raise RuntimeError("Invalid MCP command: contains path traversal characters")
    
    # 提取命令的基本名称
    base_command = Path(command).name.lower()
    # 处理 .exe 后缀
    if base_command.endswith('.exe'):
        base_command = base_command[:-4]
    
    # 如果是绝对路径，需要额外验证
    if Path(command).is_absolute():
        # 检查是否在常见的系统目录中
        home_posix = str(Path.home().as_posix())
        allowed_system_dirs = [
            '/usr/bin', '/usr/local/bin', '/usr/local/sbin', '/usr/sbin', '/opt',
            # macOS Homebrew
            '/opt/homebrew/bin', '/opt/homebrew/sbin',  # Apple Silicon
            '/usr/local/Cellar',  # Intel
            # Linux extras
            '/snap/bin',  # Ubuntu Snap
            '/home/linuxbrew/.linuxbrew/bin',  # Homebrew on Linux
            # User-level tool directories (pip --user, pipx, cargo, nvm, etc.)
            f'{home_posix}/.local/bin',
            f'{home_posix}/.cargo/bin',
            f'{home_posix}/.nvm',
        ]
        if os.name == 'nt':
            allowed_system_dirs.extend([
                'C:\\Program Files',
                'C:\\Program Files (x86)',
                'C:\\Windows\\System32',
            ])
        
        is_in_allowed_dir = any(normalized.lower().startswith(d.lower()) for d in allowed_system_dirs)
        
        # 不在允许的系统目录且不在白名单中
        if not is_in_allowed_dir and base_command not in ALLOWED_COMMANDS:
            raise RuntimeError(
                f"MCP command \"{command}\" is not in the allowed list. "
                f"Use a whitelisted command or place the executable in a standard system directory."
            )
        
        # 禁止危险的系统 shell
        dangerous_shells = ['cmd.exe', 'command.com', 'powershell.exe', 'pwsh.exe']
        if any(normalized.lower().endswith(d) for d in dangerous_shells):
            raise RuntimeError(
                f"MCP command \"{command}\" is a dangerous system shell. "
                f"Direct execution of shells is not allowed for security reasons."
            )
        return
    
    # 相对路径必须在白名单中
    if base_command not in ALLOWED_COMMANDS:
        raise RuntimeError(
            f"MCP command \"{command}\" is not in the allowed list. "
            f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}. "
            f"Use absolute paths for custom commands."
        )


def _validate_mcp_args(args: list[str]) -> None:
    """验证 MCP 参数不包含危险的 shell 元字符"""
    for arg in args:
        for char in arg:
            if char in DANGEROUS_SHELL_CHARS:
                raise RuntimeError(
                    f"Invalid MCP argument: contains dangerous shell character '{char}'. "
                    f"MCP server arguments cannot contain shell metacharacters for security reasons."
                )


def _normalize_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    return schema if isinstance(schema, dict) else {"type": "object", "additionalProperties": True}


_CAPABILITY_CONFIG_FIELDS = frozenset(
    {"domain", "scope", "effects", "purposes", "supported_operations"}
)
_CAPABILITY_EFFECT_FIELDS = frozenset(
    {"read_only", "destructive", "external", "executes_code", "delegates"}
)
_MCP_BOOLEAN_ANNOTATIONS = (
    "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint",
)


def _unknown_mcp_profile(
    *,
    source: CapabilitySource = CapabilitySource.UNKNOWN,
    status: CapabilityStatus = CapabilityStatus.UNKNOWN,
    evidence: tuple[str, ...],
) -> CapabilityProfile:
    return CapabilityProfile(
        domain=CapabilityDomain.UNKNOWN,
        scope=CapabilityScope.EXTERNAL,
        source=source,
        status=status,
        effects=CapabilityEffects(external=True),
        evidence=evidence,
    )


def _mcp_annotation_profile(descriptor: dict[str, Any]) -> CapabilityProfile:
    """Keep server assertions descriptive; never use them as permissions.

    MCP ToolAnnotations are explicitly untrusted hints. Missing boolean
    annotations remain unknown instead of turning protocol defaults into
    observed effects. openWorldHint describes entities, not the transport.
    """
    if "annotations" not in descriptor:
        return _unknown_mcp_profile(evidence=("mcp_annotations_absent",))
    annotations = descriptor["annotations"]
    if not isinstance(annotations, dict) or any(
        name in annotations and type(annotations[name]) is not bool
        for name in _MCP_BOOLEAN_ANNOTATIONS
    ) or ("title" in annotations and not isinstance(annotations["title"], str)):
        return _unknown_mcp_profile(
            source=CapabilitySource.MCP_ANNOTATIONS,
            status=CapabilityStatus.INVALID,
            evidence=("mcp_annotations_invalid",),
        )
    if not any(name in annotations for name in _MCP_BOOLEAN_ANNOTATIONS):
        return _unknown_mcp_profile(evidence=("mcp_annotations_no_effect_hints",))

    read_only = annotations.get("readOnlyHint")
    destructive = annotations.get("destructiveHint")
    evidence = ["mcp_server_hints_unverified"]
    if read_only is True:
        # The protocol says destructive/idempotent hints have no meaning for
        # read-only tools, including when the server sends their defaults.
        destructive = False
    elif "idempotentHint" in annotations:
        evidence.append(f"idempotent:{str(annotations['idempotentHint']).lower()}")
    if "openWorldHint" in annotations:
        evidence.append(f"open_world:{str(annotations['openWorldHint']).lower()}")
    return CapabilityProfile(
        domain=CapabilityDomain.UNKNOWN,
        scope=CapabilityScope.EXTERNAL,
        source=CapabilitySource.MCP_ANNOTATIONS,
        status=CapabilityStatus.HINTED,
        effects=CapabilityEffects(
            read_only=read_only,
            destructive=destructive,
            external=True,
        ),
        evidence=tuple(evidence),
    )


def _mcp_capability_profile(
    config: dict[str, Any], descriptor: dict[str, Any], descriptor_name: str,
) -> CapabilityProfile:
    """Resolve an exact per-server tool declaration without name inference."""
    remote = _mcp_annotation_profile(descriptor)
    if "toolCapabilities" not in config:
        return remote
    declarations = config["toolCapabilities"]
    if not isinstance(declarations, dict):
        return _unknown_mcp_profile(
            source=CapabilitySource.LOCAL_CONFIG,
            status=CapabilityStatus.INVALID,
            evidence=("local_tool_capabilities_invalid",),
        )
    if descriptor_name not in declarations:
        return remote

    declaration = declarations[descriptor_name]
    try:
        if not isinstance(declaration, dict) or (
            set(declaration) - _CAPABILITY_CONFIG_FIELDS
        ):
            raise ValueError("invalid declaration fields")
        domain = CapabilityDomain(declaration["domain"])
        scope = CapabilityScope(declaration["scope"])
        effect_values = declaration.get("effects", {})
        if not isinstance(effect_values, dict) or (
            set(effect_values) - _CAPABILITY_EFFECT_FIELDS
        ) or any(
            value is not None and type(value) is not bool
            for value in effect_values.values()
        ):
            raise ValueError("invalid effect fields")
        if effect_values.get("external") is False:
            raise ValueError("MCP calls cross an external service boundary")
        effects = replace(
            effects_for_scope(scope), **{**effect_values, "external": True}
        )
        if effects.read_only is True and effects.destructive is True:
            raise ValueError("conflicting read-only effects")
        if scope is CapabilityScope.READONLY and effects.read_only is not True:
            raise ValueError("conflicting read-only scope")
        if scope is CapabilityScope.DESTRUCTIVE and effects.destructive is not True:
            raise ValueError("conflicting destructive scope")
        if scope in {CapabilityScope.WRITE, CapabilityScope.DESTRUCTIVE} and (
            effects.read_only is True
        ):
            raise ValueError("conflicting mutation scope")
        sequences: dict[str, tuple[str, ...]] = {}
        for name in ("purposes", "supported_operations"):
            values = declaration.get(name, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip()
                for value in values
            ):
                raise ValueError("invalid semantic sequence")
            sequences[name] = tuple(dict.fromkeys(value.strip() for value in values))
        evidence = ["local_tool_capability_declared"]
        if remote.status is CapabilityStatus.INVALID:
            evidence.append("mcp_annotations_invalid")
        elif remote.status is CapabilityStatus.HINTED and any(
            getattr(effects, name) is not None
            and getattr(remote.effects, name) is not None
            and getattr(effects, name) != getattr(remote.effects, name)
            for name in ("read_only", "destructive")
        ):
            evidence.append("mcp_annotations_conflict")
        return CapabilityProfile(
            domain=domain,
            scope=scope,
            source=CapabilitySource.LOCAL_CONFIG,
            status=CapabilityStatus.DECLARED,
            effects=effects,
            purposes=sequences["purposes"],
            supported_operations=sequences["supported_operations"],
            evidence=tuple(evidence),
        )
    except (KeyError, TypeError, ValueError):
        # A typo in an explicit declaration must not silently become a known
        # capability through the lower-priority server hints or its name.
        return _unknown_mcp_profile(
            source=CapabilitySource.LOCAL_CONFIG,
            status=CapabilityStatus.INVALID,
            evidence=("local_tool_capability_invalid",),
        )


def _format_content_block(block: Any) -> str:
    if not isinstance(block, dict):
        return json.dumps(block, indent=2, ensure_ascii=False)
    if block.get("type") == "text" and "text" in block:
        return str(block["text"])
    return json.dumps(block, indent=2, ensure_ascii=False)


def _format_tool_call_result(result: Any) -> ToolResult:
    if not isinstance(result, dict):
        return ToolResult(ok=True, output=json.dumps(result, indent=2, ensure_ascii=False))
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list) and content:
        parts.append("\n\n".join(_format_content_block(block) for block in content))
    if "structuredContent" in result:
        parts.append("STRUCTURED_CONTENT:\n" + json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
    if not parts:
        parts.append(json.dumps(result, indent=2, ensure_ascii=False))
    return ToolResult(ok=not bool(result.get("isError")), output="\n\n".join(parts).strip())


def _format_read_resource_result(result: Any) -> ToolResult:
    if not isinstance(result, dict):
        return ToolResult(ok=False, output=json.dumps(result, indent=2, ensure_ascii=False))
    contents = result.get("contents", [])
    if not contents:
        return ToolResult(ok=True, output="No resource contents returned.")
    rendered = []
    for item in contents:
        header_lines = [f"URI: {item.get('uri', '(unknown)')}"]
        if item.get("mimeType"):
            header_lines.append(f"MIME: {item['mimeType']}")
        header = "\n".join(header_lines) + "\n\n"
        if isinstance(item.get("text"), str):
            rendered.append(header + item["text"])
        elif isinstance(item.get("blob"), str):
            rendered.append(header + "BLOB:\n" + item["blob"])
        else:
            rendered.append(header + json.dumps(item, indent=2, ensure_ascii=False))
    return ToolResult(ok=True, output="\n\n".join(rendered))


def _format_prompt_result(result: Any) -> ToolResult:
    if not isinstance(result, dict):
        return ToolResult(ok=False, output=json.dumps(result, indent=2, ensure_ascii=False))
    header = f"DESCRIPTION: {result['description']}\n\n" if result.get("description") else ""
    body_parts = []
    for message in result.get("messages", []):
        role = message.get("role", "unknown")
        content = message.get("content")
        if isinstance(content, str):
            rendered = content
        elif isinstance(content, list):
            rendered = "\n".join(
                str(part["text"]) if isinstance(part, dict) and "text" in part else json.dumps(part, indent=2, ensure_ascii=False)
                for part in content
            )
        else:
            rendered = json.dumps(content, indent=2, ensure_ascii=False)
        body_parts.append(f"[{role}]\n{rendered}")
    output = (header + "\n\n".join(body_parts)).strip()
    return ToolResult(ok=True, output=output or json.dumps(result, indent=2, ensure_ascii=False))


class _McpClientMixin:
    """Protocol logic shared by every MCP transport.

    Subclasses own the transport specifics — process + framing for stdio,
    HTTP POST + SSE responses for Streamable HTTP — and provide
    ``start()`` (including the initialize handshake), ``request()``,
    ``notify()``, ``_is_process_alive()`` and ``close()``. Caching, server
    notifications, timeout policy and runtime observation live here so the
    two transports cannot drift apart.
    """

    def _init_common(
        self,
        server_name: str,
        config: dict[str, Any],
        cwd: str,
        *,
        state_registry: McpCurrentStateRegistry | None = None,
    ) -> None:
        self.server_name = server_name
        self.config = config
        self.cwd = cwd
        self.protocol: JsonRpcProtocol | None = None
        self.next_id = 1
        self.tool_timeout = _configured_tool_timeout(config)
        self.negotiated_protocol_version: str | None = None
        # Lazy init state
        self._started = False
        self._start_error: str | None = None
        self._tools_cache: list[dict[str, Any]] | None = None
        self._resources_cache: list[dict[str, Any]] | None = None
        self._prompts_cache: list[dict[str, Any]] | None = None
        self._state_registry = state_registry
        self._state_handle: object | None = None
        self._ensure_state_registration()

    # -- lifecycle state ---------------------------------------------------

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def start_error(self) -> str | None:
        return self._start_error

    def _ensure_state_registration(self) -> None:
        if self._state_registry is None or self._state_handle is not None:
            return
        try:
            server_key = mcp_server_key(self.cwd, self.server_name)
            self._state_handle = self._state_registry.register(
                server_key,
                probe=self._is_process_alive,
            )
        except BaseException:
            # Current-state observation must not affect client construction.
            self._state_handle = None

    def _mark_state_starting(self) -> None:
        if self._state_registry is None or self._state_handle is None:
            return
        try:
            self._state_registry.mark_starting(self._state_handle)
        except BaseException:
            pass

    def _mark_state_ready(self, protocol: JsonRpcProtocol | None) -> None:
        if (
            self._state_registry is None
            or self._state_handle is None
            or protocol is None
        ):
            return
        try:
            if self._is_process_alive():
                self._state_registry.mark_ready(
                    self._state_handle,
                    protocol=protocol,
                )
            else:
                self._state_registry.mark_failed(
                    self._state_handle,
                    failure_kind="process_exit",
                    protocol=protocol,
                )
        except BaseException:
            pass

    def _mark_state_failed(
        self,
        failure: BaseException | None,
        *,
        protocol: JsonRpcProtocol | None = None,
    ) -> None:
        if self._state_registry is None or self._state_handle is None:
            return
        try:
            self._state_registry.mark_failed(
                self._state_handle,
                failure_kind=classify_mcp_failure(failure),
                protocol=protocol,
            )
        except BaseException:
            pass

    def _unregister_state(self) -> None:
        if self._state_registry is not None and self._state_handle is not None:
            try:
                self._state_registry.unregister(self._state_handle)
            except BaseException:
                pass
            self._state_handle = None

    def _ensure_started(self) -> None:
        """Ensure the server is started before making a request."""
        if self._started and not self._is_process_alive():
            self._mark_state_failed(
                RuntimeError("MCP process exited"),
                protocol=self.protocol,
            )
            # Transport hook: stdio tears down the dead process here.
            closer = getattr(self, "_on_connection_lost", None)
            if callable(closer):
                closer()
        if not self._started:
            self.start()

    # -- initialize handshake ----------------------------------------------

    def _initialize_params(self) -> dict[str, Any]:
        return {
            "protocolVersion": _SUPPORTED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mini-code", "version": "0.1.0"},
        }

    def _record_initialize_result(self, result: Any) -> None:
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if isinstance(version, str) and version:
            # Honor the server's negotiated version; never enforce ours.
            self.negotiated_protocol_version = version

    # -- server notifications ----------------------------------------------

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "notifications/tools/list_changed":
            # Server added/removed tools; drop the cache so the next
            # list_tools reflects reality instead of a stale snapshot.
            self._tools_cache = None
        elif method == "notifications/resources/list_changed":
            self._resources_cache = None
        elif method == "notifications/prompts/list_changed":
            self._prompts_cache = None
        elif method == "notifications/message":
            params = message.get("params")
            text = params.get("data") if isinstance(params, dict) else None
            if isinstance(text, str) and text.strip():
                self.stderr_lines.append(f"[server] {text.strip()[:200]}")
                self.stderr_lines = self.stderr_lines[-8:]

    # -- primitives ---------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """List tools with caching. Starts server lazily if not started."""
        if self._tools_cache is not None:
            return self._tools_cache
        self._ensure_started()
        result = self.request("tools/list", {}, timeout_seconds=LIST_TIMEOUT_SECONDS)
        self._tools_cache = list(result.get("tools", []) if isinstance(result, dict) else [])
        return self._tools_cache

    def list_resources(self) -> list[dict[str, Any]]:
        """List resources with caching. Starts server lazily if not started."""
        if self._resources_cache is not None:
            return self._resources_cache
        self._ensure_started()
        result = self.request("resources/list", {}, timeout_seconds=LIST_TIMEOUT_SECONDS)
        self._resources_cache = list(result.get("resources", []) if isinstance(result, dict) else [])
        return self._resources_cache

    def read_resource(self, uri: str) -> ToolResult:
        self._ensure_started()
        return _format_read_resource_result(
            self.request("resources/read", {"uri": uri}, timeout_seconds=self.tool_timeout)
        )

    def list_prompts(self) -> list[dict[str, Any]]:
        """List prompts with caching. Starts server lazily if not started."""
        if self._prompts_cache is not None:
            return self._prompts_cache
        self._ensure_started()
        result = self.request("prompts/list", {}, timeout_seconds=LIST_TIMEOUT_SECONDS)
        self._prompts_cache = list(result.get("prompts", []) if isinstance(result, dict) else [])
        return self._prompts_cache

    def get_prompt(self, name: str, args: dict[str, str] | None = None) -> ToolResult:
        self._ensure_started()
        return _format_prompt_result(
            self.request(
                "prompts/get",
                {"name": name, "arguments": args or {}},
                timeout_seconds=self.tool_timeout,
            )
        )

    def call_tool(
        self,
        name: str,
        input_data: Any,
        *,
        event_sink: Any | None = None,
        step: int | None = None,
        workspace: str | None = None,
    ) -> ToolResult:
        connection_attempted = (not self._started) or (self._started and not self._is_process_alive())
        try:
            self._ensure_started()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            if self._state_registry is not None:
                try:
                    if not self._is_process_alive():
                        self._mark_state_failed(error, protocol=self.protocol)
                except BaseException:
                    pass
            emit_mcp_runtime_observation_safely(
                event_sink,
                step=step,
                workspace=workspace or self.cwd,
                server_name=self.server_name,
                outcome="connection_failed",
                connection_attempted=True,
                protocol=self.protocol,
                failure=error,
            )
            raise
        try:
            result = self.request(
                "tools/call",
                {"name": name, "arguments": input_data or {}},
                timeout_seconds=self.tool_timeout,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            emit_mcp_runtime_observation_safely(
                event_sink,
                step=step,
                workspace=workspace or self.cwd,
                server_name=self.server_name,
                outcome="request_failed",
                connection_attempted=connection_attempted,
                protocol=self.protocol,
                failure=error,
            )
            raise
        emit_mcp_runtime_observation_safely(
            event_sink,
            step=step,
            workspace=workspace or self.cwd,
            server_name=self.server_name,
            outcome="request_succeeded",
            connection_attempted=connection_attempted,
            protocol=self.protocol,
        )
        return _format_tool_call_result(result)


class StdioMcpClient(_McpClientMixin):
    """MCP stdio client with lazy initialization.

    The server process is not started until the first request is made,
    reducing startup time and resource usage when MCP servers are configured
    but not immediately needed. Protocol logic (caching, notifications,
    timeouts) comes from ``_McpClientMixin``; this class owns process
    lifecycle and the two stdio framing variants.
    """
    def __init__(
        self,
        server_name: str,
        config: dict[str, Any],
        cwd: str,
        *,
        state_registry: McpCurrentStateRegistry | None = None,
    ) -> None:
        self._init_common(server_name, config, cwd, state_registry=state_registry)
        self.process: subprocess.Popen[bytes] | None = None
        self._pending: dict[int, Queue[Any]] = {}
        self._lock = threading.Lock()
        self.stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._stdout_thread: threading.Thread | None = None

    def _protocol_candidates(self) -> list[JsonRpcProtocol]:
        configured = self.config.get("protocol")
        if configured == "content-length":
            return ["content-length"]
        if configured == "newline-json":
            return ["newline-json"]
        return ["content-length", "newline-json"]

    def _on_connection_lost(self) -> None:
        # Called by _McpClientMixin._ensure_started when the process died.
        self._close_process()

    def start(self) -> None:
        """Start the MCP server process (idempotent).

        If already started, returns immediately.
        If previously failed, retries the connection.
        """
        self._ensure_state_registration()
        if self._started:
            return

        if self._start_error is not None and self.process is None:
            # Previous attempt failed — reset for retry
            self._start_error = None

        last_error: Exception | None = None
        last_protocol: JsonRpcProtocol | None = None
        for protocol in self._protocol_candidates():
            last_protocol = protocol
            self._mark_state_starting()
            try:
                self._spawn_process()
                self.protocol = protocol
                result = self.request(
                    "initialize",
                    self._initialize_params(),
                    timeout_seconds=INITIALIZE_TIMEOUT_SECONDS,
                )
                self._record_initialize_result(result)
                self.notify("notifications/initialized", {})
                self._started = True
                self._start_error = None
                self._mark_state_ready(protocol)
                return
            except Exception as error:  # noqa: BLE001
                last_error = error
                self._close_process()

        self._start_error = str(last_error or f'Failed to connect MCP server "{self.server_name}".')
        self._mark_state_failed(last_error, protocol=last_protocol)
        raise RuntimeError(self._start_error)

    def _is_process_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _spawn_process(self) -> None:
        command = _expand_env_template(str(self.config.get("command", "")).strip())
        if not command:
            raise RuntimeError(f'MCP server "{self.server_name}" has no command configured.')

        # 安全验证：检查命令和参数的合法性
        _validate_mcp_command(command)
        raw_args = [
            _expand_env_template(str(arg))
            for arg in list(self.config.get("args", []) or [])
        ]
        _validate_mcp_args(raw_args)

        process_cwd = Path(self.cwd)
        if self.config.get("cwd"):
            process_cwd = (process_cwd / str(self.config["cwd"])).resolve()
        env = os.environ.copy()
        for key, value in dict(self.config.get("env", {}) or {}).items():
            env[str(key)] = _expand_env_template(str(value))

        popen_kwargs: dict = {}
        if os.name == "nt":
            # Prevent a console window from popping up for the child process
            CREATE_NO_WINDOW = 0x08000000
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            self.process = subprocess.Popen(  # noqa: S603
                [command, *raw_args],
                cwd=str(process_cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **popen_kwargs,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Command not found: {command}. Install it first and ensure it is available in PATH.") from None

        self.stderr_lines = []
        with self._lock:
            self._pending = {}
        self._stderr_thread = threading.Thread(target=self._consume_stderr, daemon=True)
        self._stderr_thread.start()

    def _consume_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            try:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self.stderr_lines.append(text)
                    self.stderr_lines = self.stderr_lines[-8:]
            except Exception:
                continue

    def _ensure_stdout_thread(self) -> None:
        if self._stdout_thread is not None:
            return
        self._stdout_thread = threading.Thread(target=self._consume_stdout, daemon=True)
        self._stdout_thread.start()

    def _consume_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                line_bytes = self.process.stdout.readline()
                if not line_bytes:
                    break

                try:
                    line = line_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue

                stripped = line.strip()
                if not stripped:
                    continue

                if len(line_bytes) > MAX_MCP_PAYLOAD_BYTES:
                    self.stderr_lines.append(
                        f"MCP payload too large: {len(line_bytes)} bytes (limit {MAX_MCP_PAYLOAD_BYTES})"
                    )
                    continue

                # Auto-detect protocol if not determined yet
                if self.protocol is None:
                    if line.lower().startswith("content-length:"):
                        self.protocol = "content-length"
                    else:
                        self.protocol = "newline-json"

                if self.protocol == "newline-json":
                    try:
                        self._handle_message(json.loads(stripped))
                    except json.JSONDecodeError:
                        continue
                else:
                    # Content-length protocol
                    # The current 'line' is the first header line
                    header_lines = [line.rstrip("\r\n")]
                    while True:
                        next_line_bytes = self.process.stdout.readline()
                        if not next_line_bytes:
                            return
                        try:
                            next_line = next_line_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            return
                        h_stripped = next_line.rstrip("\r\n")
                        if h_stripped == "":
                            break
                        header_lines.append(h_stripped)

                    content_length = 0
                    for header in header_lines:
                        if header.lower().startswith("content-length:"):
                            try:
                                content_length = int(header.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                            break

                    if content_length > MAX_MCP_PAYLOAD_BYTES:
                        self.stderr_lines.append(
                            f"MCP payload too large: {content_length} bytes (limit {MAX_MCP_PAYLOAD_BYTES})"
                        )
                        continue

                    if content_length > 0:
                        body_bytes = self.process.stdout.read(content_length)
                        if len(body_bytes) < content_length:
                            return
                        try:
                            self._handle_message(json.loads(body_bytes.decode("utf-8")))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
        finally:
            # Bug 2: Notify pending requests when process exits
            if self.process:
                exit_code = self.process.poll()
                error_msg = {"error": {"code": -1, "message": f"MCP server process exited (code={exit_code})"}}
                with self._lock:
                    for req_id, q in list(self._pending.items()):
                        q.put(error_msg)
                    self._pending.clear()

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if not isinstance(message_id, int):
            # Server-initiated notification (tools/list_changed, logging,
            # progress). Previously these were dropped silently, which made
            # dynamic tool updates invisible to the agent.
            try:
                self._handle_notification(message)
            except Exception:  # noqa: BLE001 - notifications are best-effort
                pass
            return
        with self._lock:
            queue = self._pending.pop(message_id, None)
            if queue is not None:
                queue.put(message)

    def send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError(f'MCP server "{self.server_name}" is not running.')
        
        payload_bytes = json.dumps(message, ensure_ascii=False).encode("utf-8")
        
        if self.protocol == "newline-json":
            self.process.stdin.write(payload_bytes + b"\n")
            self.process.stdin.flush()
            self._ensure_stdout_thread()
            return
        
        header = f"Content-Length: {len(payload_bytes)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header + payload_bytes)
        self.process.stdin.flush()
        self._ensure_stdout_thread()

    def notify(self, method: str, params: Any) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Any, timeout_seconds: float = 5.0) -> Any:
        message_id = self.next_id
        self.next_id += 1
        response_queue: Queue[Any] = Queue(maxsize=1)
        with self._lock:
            self._pending[message_id] = response_queue
        self.send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        try:
            message = response_queue.get(timeout=timeout_seconds)
        except Empty as error:
            with self._lock:
                self._pending.pop(message_id, None)
            stderr = "\n".join(self.stderr_lines)
            raise RuntimeError(
                f"MCP {self.server_name}: request timed out for {method}" + (f"\n{stderr}" if stderr else "")
            ) from error
        if message.get("error"):
            details = message["error"].get("data")
            suffix = f"\n{json.dumps(details, indent=2, ensure_ascii=False)}" if details else ""
            raise RuntimeError(f"MCP {self.server_name}: {message['error']['message']}{suffix}")
        return message.get("result")

    def _close_process(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            for queue in pending:
                queue.put({"error": {"message": f'MCP server "{self.server_name}" closed before completing the request.'}})
        
        if self.process is not None:
            try:
                # 跨平台进程终止
                if os.name == "nt":
                    # Windows: 使用 taskkill 终止进程树
                    try:
                        subprocess.run(
                            ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                            capture_output=True,
                            timeout=5
                        )
                    except subprocess.TimeoutExpired:
                        # taskkill 本身超时，强制 kill
                        try:
                            self.process.kill()
                        except OSError:
                            pass
                    except Exception:
                        try:
                            self.process.kill()
                        except OSError:
                            pass
                else:
                    # Unix: 先 SIGTERM，超时后 SIGKILL
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try:
                            self.process.kill()
                        except OSError:
                            pass

                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass  # 进程可能已经退出
            finally:
                self.process = None
        
        self.protocol = None
        self._stdout_thread = None
        self._stderr_thread = None
        # Reset lazy init state
        self._started = False
        self._tools_cache = None
        self._resources_cache = None
        self._prompts_cache = None

    def close(self) -> None:
        """Dispose the process and unregister this client instance."""
        try:
            self._close_process()
        finally:
            self._unregister_state()


class HttpMcpClient(_McpClientMixin):
    """MCP client over the Streamable HTTP transport (2025-03-26 spec).

    Remote/hosted MCP servers are the mainstream deployment shape; a
    stdio-only client cannot reach any of them. Each request is one HTTP
    POST carrying a JSON-RPC message; responses come back either as plain
    JSON or as an SSE stream, and the session id from ``Mcp-Session-Id``
    is echoed on subsequent requests. Server-initiated requests over a
    background GET stream are not supported — this client only issues
    client-initiated requests.
    """

    _CONNECT_RETRY_DELAYS = (0.5, 1.0, 2.0)

    def __init__(
        self,
        server_name: str,
        config: dict[str, Any],
        cwd: str,
        *,
        state_registry: McpCurrentStateRegistry | None = None,
    ) -> None:
        self._init_common(server_name, config, cwd, state_registry=state_registry)
        url = _expand_env_template(str(config.get("url", "")).strip())
        if not url:
            raise RuntimeError(f'MCP server "{server_name}" has no url configured.')
        if not url.lower().startswith(("http://", "https://")):
            raise RuntimeError(
                f'MCP server "{server_name}" url must be http(s), got: {url}'
            )
        self._endpoint = url
        self._headers: dict[str, str] = {
            str(key): _expand_env_template(str(value))
            for key, value in dict(config.get("headers") or {}).items()
        }
        self._session_id: str | None = None
        self.stderr_lines: list[str] = []
        self.protocol = _HTTP_TRANSPORT_LABEL

    def _is_process_alive(self) -> bool:
        # HTTP has no resident process; the "connection" is the session,
        # and a lost session re-initializes on the next request.
        return True

    def _reset_session(self) -> None:
        self._session_id = None
        self._started = False
        self._tools_cache = None
        self._resources_cache = None
        self._prompts_cache = None

    def start(self) -> None:
        """Initialize the remote session (idempotent, retried on failure)."""
        self._ensure_state_registration()
        if self._started:
            return
        self._mark_state_starting()
        try:
            result = self._request_with_retry("initialize", self._initialize_params())
            self._record_initialize_result(result)
            self.notify("notifications/initialized", {})
            self._started = True
            self._start_error = None
            self._mark_state_ready(_HTTP_TRANSPORT_LABEL)
        except Exception as error:  # noqa: BLE001
            self._start_error = str(error)
            self._mark_state_failed(error, protocol=_HTTP_TRANSPORT_LABEL)
            raise RuntimeError(self._start_error) from error

    def _request_with_retry(self, method: str, params: Any) -> Any:
        """Initialize with bounded retries for transient connection errors.

        Mirrors Claude Code's first-connect behavior: instant network
        failures (DNS, refused, TLS) retry a few times before giving up,
        since hosted endpoints frequently hiccup on the very first hit.
        """
        last_error: Exception | None = None
        for attempt in range(len(self._CONNECT_RETRY_DELAYS) + 1):
            try:
                return self.request(
                    method,
                    params,
                    timeout_seconds=INITIALIZE_TIMEOUT_SECONDS,
                )
            except _McpSessionExpiredError:
                raise
            except RuntimeError as error:
                last_error = error
                if "authentication" in str(error):
                    raise
                if attempt < len(self._CONNECT_RETRY_DELAYS):
                    time.sleep(self._CONNECT_RETRY_DELAYS[attempt])
        raise last_error  # type: ignore[misc]

    def notify(self, method: str, params: Any) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._post_message(message, timeout=10.0, expect_response=False)
        except Exception as error:  # noqa: BLE001 - notifications are fire-and-forget
            self.stderr_lines.append(f"[http] notify {method} failed: {error}")

    def request(self, method: str, params: Any, timeout_seconds: float = 5.0) -> Any:
        message_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        response = self._post_message(message, timeout=timeout_seconds, expect_response=True)
        if response.get("error"):
            details = response["error"].get("data")
            suffix = f"\n{json.dumps(details, indent=2, ensure_ascii=False)}" if details else ""
            raise RuntimeError(f"MCP {self.server_name}: {response['error']['message']}{suffix}")
        return response.get("result")

    def _post_message(
        self,
        message: dict[str, Any],
        *,
        timeout: float,
        expect_response: bool,
    ) -> dict[str, Any]:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        outbound = urllib.request.Request(  # noqa: S310
            self._endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(outbound, timeout=timeout) as response:  # noqa: S310
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                if not expect_response:
                    return {}
                if response.status == 202:
                    raise RuntimeError(
                        f"MCP {self.server_name}: server returned 202 Accepted "
                        "for a request that requires a response."
                    )
                return self._parse_response_body(response, message.get("id"))
        except urllib.error.HTTPError as error:
            raise self._http_error(error) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"MCP {self.server_name}: connection failed: {error.reason}"
            ) from error

    def _parse_response_body(
        self, response: Any, expected_id: Any
    ) -> dict[str, Any]:
        content_type = (response.headers.get_content_type() or "").lower()
        raw = response.read(MAX_MCP_PAYLOAD_BYTES + 1)
        if len(raw) > MAX_MCP_PAYLOAD_BYTES:
            raise RuntimeError(
                f"MCP {self.server_name}: response exceeds "
                f"{MAX_MCP_PAYLOAD_BYTES} bytes"
            )
        if not raw:
            raise RuntimeError(f"MCP {self.server_name}: empty response body.")
        if content_type == "text/event-stream":
            return self._response_from_sse(
                raw.decode("utf-8", errors="replace"), expected_id, self.server_name
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError(
                f"MCP {self.server_name}: response is not valid JSON "
                f"({content_type})."
            ) from error
        if not isinstance(parsed, dict):
            raise RuntimeError(f"MCP {self.server_name}: malformed response.")
        return parsed

    @staticmethod
    def _response_from_sse(
        stream_text: str, expected_id: Any, server_name: str
    ) -> dict[str, Any]:
        """Extract the response for ``expected_id`` from an SSE stream."""
        data_lines: list[str] = []
        for line in stream_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        for payload in data_lines:
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == expected_id:
                return message
        raise RuntimeError(
            f"MCP {server_name}: SSE stream ended without a response "
            f"for request id {expected_id}."
        )

    def _http_error(self, error: urllib.error.HTTPError) -> Exception:
        status = error.code
        if status == 404 and self._session_id is not None:
            # The session expired server-side. Reset so the next call
            # transparently re-initializes a fresh session.
            self._reset_session()
            return _McpSessionExpiredError(
                f"MCP {self.server_name}: session expired (404); "
                "a new session will be initialized on the next call."
            )
        if status in (401, 403):
            return RuntimeError(
                f"MCP {self.server_name}: authentication required (HTTP {status}). "
                "Configure credentials via the headers field, e.g. "
                '{"Authorization": "Bearer ${MY_TOKEN}"}.'
            )
        detail = error.read(400).decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        return RuntimeError(
            f"MCP {self.server_name}: HTTP {status}{suffix}"
        )

    def close(self) -> None:
        """Best-effort session termination, then unregister."""
        try:
            if self._session_id is not None:
                request = urllib.request.Request(  # noqa: S310
                    self._endpoint,
                    method="DELETE",
                    headers={"Mcp-Session-Id": self._session_id},
                )
                try:
                    urllib.request.urlopen(request, timeout=5.0)  # noqa: S310
                except Exception:  # noqa: BLE001 - shutdown is best-effort
                    pass
                self._session_id = None
        finally:
            self._unregister_state()


class _McpSessionExpiredError(RuntimeError):
    """Internal marker: the HTTP session was lost and can be re-initialized."""


def _authorize_mcp_tool_call(
    context: Any,
    *,
    tool_id: str,
    server_name: str,
    tool_name: str,
    declared_read_only: bool,
) -> None:
    """Gate one MCP dispatch behind the workspace permission policy.

    MCP servers cross an external process boundary; without this check a
    server that writes files or runs commands would execute with zero
    approval in every permission mode. Raises PermissionDeniedError (with
    its model-safe projection) when the call is not authorized.
    """
    from minicode.permissions import PermissionDeniedError

    permissions = getattr(context, "permissions", None)
    ensure = getattr(permissions, "ensure_mcp_tool", None)
    if callable(ensure):
        ensure(
            tool_id,
            server_name=server_name,
            tool_name=tool_name,
            declared_read_only=declared_read_only,
        )
        if not declared_read_only:
            checkpoint = getattr(permissions, "ensure_operation_active", None)
            if callable(checkpoint):
                checkpoint()
        return
    # No permission manager (embedded/test contexts): fail closed for
    # anything not declared read-only by host config, exactly like
    # workspace path resolution blocks outside-workspace targets.
    if not declared_read_only:
        raise PermissionDeniedError(
            f"MCP tool {tool_id} requires approval but no permission manager "
            "is available.",
            code="mcp_tool_requires_approval",
        )


def create_mcp_backed_tools(
    *,
    cwd: str,
    mcp_servers: dict[str, dict[str, Any]],
    state_registry: McpCurrentStateRegistry | None = None,
) -> dict[str, Any]:
    """Create MCP-backed tools with lazy server initialization.
    
    Instead of starting all MCP servers at startup (which is slow and
    resource-intensive), this function creates lightweight client objects
    that defer server startup until the first tool call.
    
    Benefits:
    - Faster startup: no waiting for MCP server processes to initialize
    - Lower resource usage: servers that aren't needed never start
    - Resilience: a failed server doesn't block other servers
    - Auto-retry: servers are retried on first use after failure
    """
    clients: list[_McpClientMixin] = []
    tools: list[ToolDefinition] = []
    servers: list[dict[str, Any]] = []
    resource_index: dict[str, dict[str, Any]] = {}
    prompt_index: dict[str, dict[str, Any]] = {}

    for server_name, config in mcp_servers.items():
        if config.get("enabled") is False:
            servers.append(asdict(McpServerSummary(name=server_name, command=config.get("command", ""), status="disabled", toolCount=0, protocol=config.get("protocol"))))
            continue

        server_type = str(config.get("type", "")).strip().lower()
        display_command = str(config.get("url") or config.get("command", ""))
        if server_type in {"sse", "http+sse"}:
            # Legacy HTTP+SSE was removed from the spec in 2025-03-26.
            servers.append(asdict(McpServerSummary(
                name=server_name,
                command=display_command,
                status="error",
                toolCount=0,
                error="SSE transport is not supported (removed from the MCP spec). Use Streamable HTTP: {\"url\": \"...\"} or type http.",
            )))
            continue

        try:
            if config.get("url") or server_type in {"http", "streamable-http", "streamable_http"}:
                client = HttpMcpClient(
                    server_name,
                    config,
                    cwd,
                    state_registry=state_registry,
                )
            else:
                client = StdioMcpClient(
                    server_name,
                    config,
                    cwd,
                    state_registry=state_registry,
                )
        except Exception as error:  # noqa: BLE001 - one bad server must not block the rest
            servers.append(asdict(McpServerSummary(
                name=server_name,
                command=display_command,
                status="error",
                toolCount=0,
                error=str(error)[:200],
            )))
            continue
        clients.append(client)

        # Register server with "pending" status — will be connected lazily
        servers.append(
            asdict(
                McpServerSummary(
                    name=server_name,
                    command=display_command,
                    status="pending",
                    toolCount=0,
                    protocol=config.get("protocol"),
                )
            )
        )
        
        # Eagerly discover tools/resources/prompts on first use via
        # the lazy client. Register placeholder tools now that will
        # resolve to the actual MCP tool on first call.
        # 
        # We register a single "gateway" tool per server that triggers
        # lazy init, plus we'll discover and register actual tools
        # after the first successful connection.
        # 
        # For simplicity, we still try to discover tools at creation
        # time but don't fail if the server can't start yet.
        try:
            descriptors = client.list_tools()
            try:
                resources = client.list_resources()
            except Exception:  # noqa: BLE001
                resources = []
            try:
                prompts = client.list_prompts()
            except Exception:  # noqa: BLE001
                prompts = []

            for resource in resources:
                resource_index[f"{server_name}:{resource.get('uri')}"] = {"serverName": server_name, "resource": resource}
            for prompt in prompts:
                prompt_index[f"{server_name}:{prompt.get('name')}"] = {"serverName": server_name, "prompt": prompt}

            for descriptor in descriptors:
                wrapped_name = f"mcp__{_sanitize_tool_segment(server_name)}__{_sanitize_tool_segment(str(descriptor.get('name', 'tool')))}"
                descriptor_name = str(descriptor.get("name", "tool"))
                input_schema = _normalize_input_schema(descriptor.get("inputSchema"))
                capability_profile = _mcp_capability_profile(
                    config, descriptor, descriptor_name
                )
                # Only a host-authored local declaration of read-only scope
                # may skip the approval flow; server annotations are hints.
                declared_read_only = (
                    capability_profile.scope is CapabilityScope.READONLY
                    and capability_profile.source is CapabilitySource.LOCAL_CONFIG
                )

                def _validator(value: Any) -> Any:
                    return value

                def _run(
                    input_data: Any,
                    _context,
                    *,
                    _client=client,
                    _descriptor_name=descriptor_name,
                    _server_name=server_name,
                    _tool_id=wrapped_name,
                    _declared_read_only=declared_read_only,
                ):
                    _authorize_mcp_tool_call(
                        _context,
                        tool_id=_tool_id,
                        server_name=_server_name,
                        tool_name=_descriptor_name,
                        declared_read_only=_declared_read_only,
                    )
                    return _client.call_tool(
                        _descriptor_name,
                        input_data,
                        event_sink=getattr(_context, "_event_sink", None),
                        step=getattr(_context, "_step", None),
                        workspace=getattr(_context, "cwd", None),
                    )

                tools.append(
                    ToolDefinition(
                        name=wrapped_name,
                        description=str(descriptor.get("description") or f"Call MCP tool {descriptor_name} from server {server_name}."),
                        input_schema=input_schema,
                        validator=_validator,
                        run=_run,
                        capability_domain=capability_profile.domain.value,
                        capability_scope=capability_profile.scope.value,
                        capability_profile=capability_profile,
                    )
                )

            # Update server status to connected
            for i, s in enumerate(servers):
                if s["name"] == server_name:
                    servers[i] = asdict(
                        McpServerSummary(
                            name=server_name,
                            command=display_command,
                            status="connected",
                            toolCount=len(descriptors),
                            protocol=client.protocol,
                            resourceCount=len(resources),
                            promptCount=len(prompts),
                        )
                    )
                    break
        except Exception as error:  # noqa: BLE001
            # Lazy init: don't fail — server will be retried on first tool call
            # Just update status to reflect the error
            for i, s in enumerate(servers):
                if s["name"] == server_name:
                    servers[i] = asdict(
                        McpServerSummary(
                            name=server_name,
                            command=display_command,
                            status="error",
                            toolCount=0,
                            error=str(error)[:200],
                            protocol=config.get("protocol") or (
                                client.protocol if isinstance(client, _McpClientMixin) else None
                            ),
                        )
                    )
                    break

    if resource_index:
        tools.append(
            ToolDefinition(
                name="list_mcp_resources",
                description="List available MCP resources exposed by connected MCP servers.",
                input_schema={"type": "object", "properties": {"server": {"type": "string"}}},
                validator=lambda value: {"server": value.get("server")} if isinstance(value, dict) else {"server": None},
                capability_domain="system",
                capability_scope="readonly",
                run=lambda input_data, _context: ToolResult(
                    ok=True,
                    output="\n".join(
                        f"{entry['serverName']}: {entry['resource'].get('uri')}"
                        + (f" ({entry['resource'].get('name')})" if entry["resource"].get("name") else "")
                        + (f" - {entry['resource'].get('description')}" if entry["resource"].get("description") else "")
                        for entry in resource_index.values()
                        if not input_data.get("server") or entry["serverName"] == input_data["server"]
                    )
                    or "No MCP resources available.",
                ),
            )
        )

        def _read_resource(input_data: dict, _context) -> ToolResult:
            client = next((item for item in clients if item.server_name == input_data["server"]), None)
            if client is None:
                return ToolResult(ok=False, output=f"Unknown MCP server: {input_data['server']}")
            return client.read_resource(input_data["uri"])

        tools.append(
            ToolDefinition(
                name="read_mcp_resource",
                description="Read a specific MCP resource by server and URI.",
                input_schema={"type": "object", "properties": {"server": {"type": "string"}, "uri": {"type": "string"}}, "required": ["server", "uri"]},
                validator=lambda value: value,
                run=_read_resource,
                capability_domain="unknown",
                capability_scope="external",
            )
        )

    if prompt_index:
        tools.append(
            ToolDefinition(
                name="list_mcp_prompts",
                description="List available MCP prompts exposed by connected MCP servers.",
                input_schema={"type": "object", "properties": {"server": {"type": "string"}}},
                validator=lambda value: {"server": value.get("server")} if isinstance(value, dict) else {"server": None},
                capability_domain="system",
                capability_scope="readonly",
                run=lambda input_data, _context: ToolResult(
                    ok=True,
                    output="\n".join(
                        f"{entry['serverName']}: {entry['prompt'].get('name')}"
                        + (
                            " args=["
                            + ", ".join(
                                f"{arg.get('name')}{'*' if arg.get('required') else ''}"
                                for arg in entry["prompt"].get("arguments", [])
                            )
                            + "]"
                            if entry["prompt"].get("arguments")
                            else ""
                        )
                        + (f" - {entry['prompt'].get('description')}" if entry["prompt"].get("description") else "")
                        for entry in prompt_index.values()
                        if not input_data.get("server") or entry["serverName"] == input_data["server"]
                    )
                    or "No MCP prompts available.",
                ),
            )
        )

        def _get_prompt(input_data: dict, _context) -> ToolResult:
            client = next((item for item in clients if item.server_name == input_data["server"]), None)
            if client is None:
                return ToolResult(ok=False, output=f"Unknown MCP server: {input_data['server']}")
            return client.get_prompt(input_data["name"], input_data.get("arguments"))

        tools.append(
            ToolDefinition(
                name="get_mcp_prompt",
                description="Fetch a rendered MCP prompt by server, prompt name, and optional arguments.",
                input_schema={"type": "object", "properties": {"server": {"type": "string"}, "name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["server", "name"]},
                validator=lambda value: value,
                run=_get_prompt,
                capability_domain="unknown",
                capability_scope="external",
            )
        )

    return {
        "tools": tools,
        "servers": servers,
        "dispose": lambda: [client.close() for client in clients],
    }
