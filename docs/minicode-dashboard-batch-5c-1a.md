# MiniCode Dashboard Batch 5C-1A — MCP Runtime Fact Contract

## Scope

Batch 5C-1A adds optional, best-effort, run-scoped MCP runtime observation for real MCP `tools/call` requests. It does not add live/global MCP status, heartbeat, polling, SSE/WebSocket, Dashboard write operations, Overview/Ops MCP aggregation, or Connections runtime aggregation.

## Initial baseline

Before changes, the project was re-verified:

- `python -m pytest -q`: `1860 passed, 2 skipped, 3 warnings`
- `python scripts/generate_memory_retrieval_production_baseline.py`: active `memory-retrieval-production-v9`, candidate matches true, 15/15 current protected files match, v1–v9 manifest integrity true.

The three warnings are the existing unregistered benchmark-marker warnings.

## MCP creation and call graph audit

- `StdioMcpClient` is constructed only by `create_mcp_backed_tools()` in `minicode/mcp.py`, reached from `create_default_tool_registry()` in `minicode/tools/__init__.py`. Tests also construct it directly.
- Discovery path: `create_default_tool_registry()` → `create_mcp_backed_tools()` → `StdioMcpClient(...)` → `list_tools()` / `list_resources()` / `list_prompts()` → `_ensure_started()` → `start()` → `_spawn_process()` → `request("initialize")` → `notify("notifications/initialized")` → `request("tools/list"|"resources/list"|"prompts/list")`.
- Tool request path: generated MCP `ToolDefinition.run` → `StdioMcpClient.call_tool()` → `_ensure_started()` → `request("tools/call")` → `ToolResult` formatting or original exception path.
- `request()` does not call `_ensure_started()`; direct callers must start first or go through a method that does.
- `close()` fails pending requests, terminates/kills the subprocess, clears process/protocol/thread state, and resets caches. It is exposed via the registry disposer and remains outside runtime attribution.

## eager/lazy conclusion

`create_mcp_backed_tools()` still claims clients defer startup until first tool call, but construction currently calls `list_tools()`, `list_resources()`, and `list_prompts()`. Each list method calls `_ensure_started()`, so successful discovery starts the MCP server. This batch records that fact but intentionally does not change eager/lazy semantics.

## Run ownership

- Headless constructs `ToolRegistry` inside `observe_run`; Gateway reuses Headless and therefore also constructs it inside the gateway Run.
- Classic non-TTY CLI constructs `ToolRegistry` before per-input `observe_run`.
- TTY constructs one `ToolRegistry` before `run_tty_app()` and reuses it across submitted Runs.
- MCP discovery, `/mcp` management, local tool shortcuts, resource/prompt discovery outside a tool invocation, disposer/close, and Dashboard Connections config scanning remain unattributed if no current `ToolContext._event_sink` exists.
- TUI can reuse a long-lived client across Runs. The client never stores a sink; each MCP tool wrapper passes the current invocation's sink/step from `ToolContext` into `call_tool()` only for that call.

## Observation seam

The implemented seam is:

```text
RunObservation
  → run_agent_turn(event_sink=...)
  → _execute_single_tool(..., step, event_sink)
  → ToolContext(_event_sink=..., _step=...)
  → MCP Tool wrapper
  → StdioMcpClient.call_tool(..., event_sink=..., step=..., workspace=...)
  → mcp.runtime.observed
```

`ToolContext` fields default to `None`; direct/non-Agent tool calls stay compatible and unobserved unless they explicitly provide a sink.

## Event schema

The only new event is `mcp.runtime.observed`:

```json
{
  "mcpVersion": 1,
  "serverKey": "mcpsrv_<32 lowercase hex>",
  "transport": "stdio",
  "activity": "tool_request",
  "outcome": "request_succeeded | connection_failed | request_failed",
  "connectionAttempted": true,
  "protocol": "content-length | newline-json",
  "failureKind": "timeout | command_not_found | process_exit | protocol_error | request_error | other"
}
```

`protocol` is included only when the client has actually selected a protocol. `failureKind` appears only for failed outcomes.

## serverKey

`serverKey` is generated centrally in `minicode/mcp_observation.py`:

```text
mcpsrv_ + first 32 hex chars of SHA-256(stable_workspace_id(workspace) + NUL + server_name)
```

It is deterministic for the same workspace/server name, differs across workspaces, does not store workspace paths, and does not include command, args, env, cwd, URLs, credentials, headers, or current time.

## failureKind and connectionAttempted

`connectionAttempted` is based on runtime state before `_ensure_started()`:

- not started or started but process dead → true
- already started and alive → false
- `_ensure_started()` failure → `connection_failed`, `connectionAttempted=true`
- `tools/call` request failure after startup → `request_failed`, preserving the pre-request connection-attempt fact

Failures are classified into low-cardinality `failureKind` values without returning original exception text.

## sink=None behavior

When no sink is provided, MCP call execution returns immediately through the existing path: no server key generation, no projection, no RunJournal append, no observation clock use, and no runtime event.

## Generic Tool events vs MCP runtime events

Existing `tool.started` / `tool.finished` remain the source of generic tool lifecycle, counts, success/error, operation pairing, and tool names. MCP runtime observation only records transport/runtime facts for real MCP `tools/call` termination. Normal ordering is:

```text
tool.started
mcp.runtime.observed
tool.finished
```

for both success and ordinary MCP request/connection errors.

## RunJournal and Run Detail

`RunJournal` now accepts `mcp.runtime.observed` but validates a closed MCP schema, rejects unknown MCP fields, invalid enums, bool-as-int, success-with-failureKind, and sensitive raw fields. Run Detail projects only the MCP whitelist and degrades invalid payloads to empty details instead of echoing raw payload.

Run coverage adds:

```text
mcpRuntime = partial
mcpRuntimeScope = run-scoped observation
mcpRuntimeHistorical = partial
mcpRuntimeCurrent = unavailable
mcpRuntimeCrossProcess = unavailable
```

## Timeline UI

Run Detail Timeline displays safe strings such as “MCP request succeeded”, “MCP connection failed”, low-cardinality failure kind, truncated `serverKey`, transport, and whether a connection was attempted or an existing connection was observed. It does not display online/current/live status, raw exceptions, command, args, env, tool input/output, stderr, or paths.

## Connections boundary

Connections remains configuration-only. It reads config files and continues to report configured server count, sources, enabled/disabled state, safe server names, and protocol summaries. It does not instantiate MCP clients and cannot claim current connected/online/healthy state because RunJournal events are historical run-scoped facts, may be stale, may come from another process, and are not heartbeats.

## Modified behavior intentionally not changed

This batch does not change MCP command validation, args validation, env passing, protocol candidate order, initialize/request timeouts, JSON-RPC framing, tool/resource/prompt formatting, process termination strategy, eager discovery, tool names/descriptions/schemas, ToolResult, permissions, disposer behavior, retries, Session, Gateway routing, pricing/cost truth, Memory Retrieval algorithms, or Context/WorkingMemory algorithms.

## v9 → v10 protected delta

Changed protected files:

- `minicode/agent_loop.py`
- `minicode/run_journal.py`

Newly protected files:

- `minicode/mcp.py`
- `minicode/mcp_event_contract.py`
- `minicode/mcp_observation.py`
- `minicode/tooling.py`

Removed protected files: none.

## Stable interface for Batch 5C-1B

Batch 5C-1B can safely join configuration and runtime facts by recomputing `mcp_server_key(workspace, server_name)`. It can aggregate retained `mcp.runtime.observed` events as historical/last-observed facts, but must preserve stale/current semantics and must not reinterpret these events as live process state.
