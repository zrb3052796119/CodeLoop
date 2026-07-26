# MiniCode Dashboard Batch 5C-2A: Process-local MCP Current State

## Scope and truth boundary

Batch 5C-2A introduces one bounded, thread-safe, process-local registry for MCP
client lifecycle facts. It does not expose those facts through the Dashboard yet.
The existing Connections API/UI remains the Batch 5C-1B configuration plus
retained-Run history view, with current MCP state explicitly unavailable until
Batch 5C-2B.

The registry is not a persistent store, a RunJournal projection, a cross-process
coordinator, a heartbeat service, or a process supervisor. A snapshot may perform
only the already-registered, side-effect-free liveness probes supplied by clients;
it may not start a client, send an MCP request, read configuration, touch the
filesystem, or poll in the background.

## Audited ownership graph

```text
Gateway process
  run_gateway()
    owns exactly one McpCurrentStateRegistry for server lifetime
    owns ThreadingHTTPServer
      concurrent POST /run handler
        run_headless(..., mcp_current_state_registry=same registry)
          owns one top-level ToolRegistry for that request
            owns zero or more StdioMcpClient instances
            task tool execution, if used
              owns one nested ToolRegistry for that task
                owns zero or more StdioMcpClient instances
          finally: ToolRegistry.dispose() -> client.close() -> unregister
      GET Dashboard/API handler
        does not consume the registry in 5C-2A
        cannot create/start/request an MCP client

Standalone headless process
  run_headless() default argument is None
    owns its ToolRegistry and MCP clients
    does not register process-current state

Classic CLI/TUI process
  main() owns one long-lived ToolRegistry
    TUI reuses that registry
    main() finally disposes it
    no process-current registry is composed in 5C-2A
```

The actual `create_mcp_backed_tools()` implementation performs eager discovery at
construction time despite older lazy-initialization wording in its docstring. This
batch preserves that business timing. Observation wraps the existing lifecycle; it
does not add discovery, subprocess creation, MCP requests, retries, threads, or
polling.

## Ownership and visibility table

| Runtime/owner | Client lifetime | Registry visible? | Cleanup owner | 5C-2A Dashboard use |
|---|---|---:|---|---|
| Gateway top-level `/run` | One request/tool registry | Yes, shared Gateway registry | Headless `finally` | None |
| Gateway nested `task` tool | One nested task/tool registry | Yes, inherited through tool context | Task `finally` | None |
| Concurrent Gateway `/run` calls | Independent client instances | Yes, aggregate by server key | Each request independently | None |
| Standalone Headless | One invocation | No by default | Headless `finally` | Not applicable |
| Classic CLI/TUI | Long-lived CLI registry | No in this batch | `main()` `finally` | Not applicable |
| Retained RunJournal events | Persistent run history | Never imported into registry | RunJournal retention | Existing historical view only |
| Dashboard GET/read model | Request-local read projection | Registry stored on server only | Server lifetime | Deliberately not consumed |

Because the registry is process-local, MCP clients in a different Python process
cannot appear in a Gateway snapshot. A Gateway restart starts with an empty
registry. An empty registry means no active registered client, not a claim that a
configured server is offline.

## Lifecycle contract

One enabled `StdioMcpClient` optionally registers an opaque instance at
construction (or again on reuse after a final close). Its state transitions are:

```text
registered idle
  -> starting before the existing start attempt
  -> ready only after initialize/initialized and a true process liveness probe
  -> failed after initialization exhaustion or observed process death
  -> unregistered on final public close
```

Protocol-candidate cleanup is internal cleanup and must not unregister the client.
A failed tool/resource/prompt request does not demote a still-alive process from
`ready`; a false liveness check records `failed/process_exit`. Final `close()`
removes the instance instead of retaining a synthetic `closed` state. Observer
failures, including control-flow `BaseException` subclasses, are isolated from
business return values, raised exceptions, retries, and cleanup.

## Registry and snapshot boundary

The registry accepts only shared `mcp_server_key()` identities and closed protocol
and failure-kind vocabularies from the existing MCP event contract. Internal
instance tokens, server names, command/configuration, PID/process handles, request
payloads, errors, paths, environment values, and secrets are never projected.

Snapshots are immutable value objects with deterministic ordering and fresh JSON
projection. Multiple active instances for one server key aggregate by the fixed
precedence `ready > starting > failed > idle`; ties use deterministic update order.
Hard instance, response-server, and diagnostic budgets prevent unbounded process
memory or response growth. Budget and observer faults use fixed diagnostic codes,
never exception text.

## Composition seam reserved for Batch 5C-2B

`run_gateway()` owns the registry and stores it on the HTTP server object.
`MiniCodeGatewayHandler` passes that same object only into Gateway-sourced Headless
runs. `run_headless()`, `create_default_tool_registry()`,
`create_mcp_backed_tools()`, and nested task registries accept an optional registry;
`None` is the standalone/default compatibility path and performs no observation
identity, clock, token, or registry work.

Batch 5C-2B may add a bounded read adapter that consumes the server-owned registry.
It must preserve this module's closed snapshot contract and the distinction among
current process facts, effective configuration, and retained Run history. No such
adapter, route projection, frontend state, polling, SSE, or write control is part
of 5C-2A.
