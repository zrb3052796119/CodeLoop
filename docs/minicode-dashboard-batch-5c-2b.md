# MiniCode Dashboard Batch 5C-2B: MCP Current-State Projection

## Scope and truth boundary

Batch 5C-2B adds a read-only projection from the Gateway-owned
`McpCurrentStateRegistry` to the existing `GET /api/v1/connections` response and
Connections UI. It does not add a route, persistence, process control, polling,
SSE, WebSocket, heartbeat, or a cross-process coordinator.

Connections now displays three independent facts:

| Fact | Source | Meaning | It does not mean |
|---|---|---|---|
| Configured | effective user/project MCP config | The server is present in the current resolved configuration | started, ready, or reachable |
| Current | one validated registry snapshot | A configured server has registered client instances in this Gateway Python process at `checkedAt` | global state, another process, heartbeat health, or future availability |
| Historical | retained current-workspace RunJournal events | A bounded retained request observation happened in an earlier/current Run | current process state or readiness |

Current and historical state may disagree without either overwriting the other.
A disabled configuration may also have a current registered instance; the UI
shows both facts rather than changing either source.

## Read-side architecture

`minicode.web.mcp_current_projection` is the deep read-side module. Its public
function is:

```python
project_current_mcp_state(
    workspace,
    configured_server_names,
    snapshot_loader,
    *,
    configured_set_complete=True,
) -> McpCurrentProjection
```

The module calls the optional zero-argument loader at most once, validates its
result with `normalize_mcp_current_state_snapshot()`, computes the shared
workspace key with `mcp_server_key()`, and returns frozen/slotted projection
records. It does not read configuration, RunJournal, HTTP state, files, or MCP
processes. It has no cache.

Raw server keys and unmatched snapshot identities are never returned. Only the
configured names supplied by the current workspace can receive per-server
current facts. A valid complete empty snapshot therefore yields exact zero
current counts and `not_registered` cards. Loader/schema failures yield fixed
low-cardinality diagnostics without exception text.

The three current aggregate counts are exact only when both the current snapshot
and configured set are complete:

- `registeredConfiguredMcpCount`
- `activeMcpInstanceCount`
- `liveMcpCount` (configured servers whose projected state is exactly `ready`)

If the loader is missing/fails, the snapshot is invalid/limited, or a config
source is partial, all three values and `mcpCurrent.byState` are `null`. Already
matched cards remain visible for limited or config-partial reads. A missing card
uses `snapshot_limited` only when snapshot coverage is limited; otherwise it uses
`not_registered`, which is not an offline claim.

## Gateway composition and ownership

`run_gateway()` creates exactly one `McpCurrentStateRegistry` before serving. The
same object is stored on the HTTP server for `POST /run` and captured by the
Dashboard loader as `registry.snapshot().to_dict()`. The HTTP handler remains
registry-unaware and only calls the injected `DashboardReadModel` interface.

The fallback handler path constructs a read model without a loader. It reports
`mcpCurrent.status=unavailable` and does not create or discover a second registry.
Overview, Runs, Sessions, Memory, Skills, Ops, System, health, and static-page GETs
never invoke the current loader. Each Connections read invokes it once at request
time, so manual Refresh/Retry obtains a fresh snapshot.

## Additive response contract

Schema version remains `1` and mode remains `read-only`. Existing `mcpRuntime`,
top-level historical `coverage`, and each server's `runtime` object retain their
Batch 5C-1B meaning. The additive `mcpCurrent` object explicitly carries:

- `status`: `live`, `unavailable`, or `error`;
- `current`: `process-local` only for a valid snapshot, otherwise `unavailable`;
- state version, checked time, and exact matched `byState` counts when precise;
- Gateway-process/cross-process-unavailable/no-heartbeat coverage;
- configured-set completeness, association scope, unmatched suppression, and
  limited status;
- fixed diagnostics and a bounded explanatory message.

Each server receives an additive `current` object. `liveStatus` is derived only
from that object: the four live states are `idle`, `starting`, `ready`, and
`failed`; a missing/limited source is `unavailable`; a source failure is `error`.
Historical outcomes never set `liveStatus`.

Config, current, and historical failures remain independent and the HTTP response
stays 200. The combined source is `error` if any source errors, `stale` when the
current loader is unavailable, and otherwise `live`. Limited coverage or current
diagnostics are expressed by coverage and the frontend partial phase, not a new
backend status word. A live current `checkedAt` is preferred as `source.updatedAt`;
retained history timestamps are never presented as current check time.

## UI behavior

The accepted Waku shell and three-column server fact layout remain intact. Each
MCP card separately renders current configuration, current Gateway process state,
and retained Run history. `ready`, `starting`, `idle`, `failed`,
`not_registered`, `snapshot_limited`, source-unavailable, and source-error/Retry
copy is explicit and escaped. Nullable counts use the shared unavailable formatter
and are never coerced with `|| 0`.

Current Gateway-process coverage and retained historical coverage are separate
cards. Page metadata states process snapshot, historical partial, no global state,
and no process control. Refresh and Retry reuse the existing request-id stale
response guard. There is no automatic refresh, EventSource, WebSocket, or process
action button. The Dock remains mock/read-only.

## Deferred boundary

No subsequent Batch is implemented here. Cross-process visibility, process
management, heartbeat/health claims, persistent current state, and push updates
remain unavailable and require separate authorization and contracts.

## Final acceptance evidence

- Final focused matrices passed: 84 projection/current/Gateway/HTTP tests, 73 existing MCP tests, 199 Dashboard/frontend/packaging tests, and the isolated wheel suite at 9/9. The post-review frontend/wheel repeat passed 69 tests.
- Both final full regressions passed `1970 passed, 2 skipped, 3 warnings`; the warnings are the three pre-existing benchmark marker warnings.
- Every modified Python file passed Ruff and `py_compile`; `python -m compileall -q minicode scripts tests`, `node --check` for `app.js` and `cost-format.js`, wheel build, isolated installation, and installed Gateway/API/static/`/run` smoke passed. Runtime dependencies remain `[]`.
- The official 108-case evaluator passed with 37 confirmed gaps and zero remote calls. Accepted gold SHA-256, mtime ns, and size remained `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, `1784135857000000000`, and `3033592` before and after.
- Browser acceptance at 1280×900 covered exact empty state; concurrent two-instance ready state and cleanup; starting/failed states; safe timeout/process-exit categories; both current/history disagreement directions; disabled configuration plus current ready; limited coverage; fail-once Retry recovery; all eight main routes; and all five Memory routes. There was no horizontal overflow or three-column overlap, console warning/error logs were zero, and the DOM contained no secret, absolute path, server key, object-coercion string, or forbidden global/health claim.
- Final empty-state screenshot: `artifacts/minicode-dashboard-batch-5c-2b-connections.jpg` (1280×900). Browser tabs, viewport override, temporary Gateway, test subprocesses, HOME/workspace, and fixture data were removed.
- The isolated wheel smoke proves both the installed Client ready→close registry lifecycle and the installed Gateway's exact-empty Connections projection. An additional same-moment installed active-ready HTTP assertion was attempted after certification, but the execution environment rejected the run before test execution because its external execution allowance was exhausted; the unexecuted test-only change was reverted. Source-tree concurrent `/run` active-ready HTTP behavior remains covered by the focused suite and browser acceptance.
