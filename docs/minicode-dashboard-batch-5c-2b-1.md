# MiniCode Dashboard Batch 5C-2B.1: MCP Workspace Diagnostic Isolation

## Root cause and repair boundary

The Batch 5C-2B Dashboard projector suppressed unmatched server keys only after
calling a zero-argument global Registry snapshot. That was too late: an MCP
client in another workspace could already be probed, reconciled to `failed`,
counted against the response budget, and represented by accumulated diagnostics.
Those global effects could make a visible workspace appear limited and replace
exact current aggregates with nulls.

The repair is a deep Registry seam, not a frontend filter:

```python
McpCurrentStateRegistry.snapshot_for(
    server_keys: Collection[str],
) -> McpCurrentStateSnapshot
```

`snapshot_for()` requires a finite `Collection`, checks its size before
iteration, validates every key with the shared opaque server-key grammar, and
uses a deterministic key window. The allowlist is applied before liveness
probes, state reconciliation, grouping, response budgeting, and request-local
diagnostics. Empty allowlists are exact even when unrelated instances and global
diagnostics exist. Existing `snapshot()` remains the compatible global process
view with its prior probe, diagnostic, ordering, immutability, and concurrency
behavior.

Scoped reads ignore accumulated Registry diagnostics that cannot be attributed
to selected keys. A selected probe exception safely changes only that selected
instance to `failed/other` and returns the fixed request-local `probe_failed`
diagnostic without exception text, key, name, path, PID, command, args, env, or
secret. A selected key set over the response budget is limited deterministically;
unmatched Registry entries never cause scoped limiting.

## Projector and Gateway composition

`McpCurrentStateLoader` is now:

```python
Callable[[frozenset[str]], Mapping[str, object]]
```

`project_current_mcp_state()` retains the existing 2,000-name input budget,
computes the bounded opaque key set once, calls the scoped loader exactly once,
and preserves configured display order. Raw keys remain internal. Loader absence,
failure, invalid schema, limited snapshot, and partial configuration keep the
existing safe nullable semantics.

`run_gateway()` still owns exactly one Registry. POST `/run` receives that
object, while the Dashboard loader captures the same object and delegates to
`snapshot_for(server_keys)`. HTTP remains Registry-unaware and only invokes the
ReadModel. Non-Connections pages never call the scoped loader.

Configured, current, and retained historical facts remain independent. A
complete nonlimited empty scoped snapshot produces exact `0` aggregates and
`not_registered`, which is explicitly not an offline claim. Limited/error/
unavailable/partial reads retain null aggregates and `byState`. Current/history
opposites and disabled+current-ready remain valid simultaneous facts.

## RED and automated evidence

The first production-independent RED failed exactly because
`McpCurrentStateRegistry` had no `snapshot_for` method. The projector RED then
proved the zero-argument loader could not carry the bounded workspace allowlist.
The final tests cover:

- unmatched ready probes are not called or reconciled;
- unmatched probe failures, response budgets, and unattributable global
  diagnostics do not affect the visible workspace;
- selected probe failures remain safe and visible;
- empty allowlists are exact;
- arbitrary iterables are rejected and oversized collections are rejected
  before consumption;
- the loader receives one exact bounded frozen key set;
- same server names in two workspaces never cross-associate;
- selected/unmatched lifecycle operations remain race- and deadlock-free;
- non-Connections routes never consume the scoped loader.

The related MCP/current-state/ReadModel/Gateway/HTTP matrix passed 129 tests.
Registry/projection tests passed 42; the composition suite passed 7. Ruff,
explicit `py_compile`, full `compileall`, and `node --check` for both production
JavaScript files passed. Runtime dependencies remain `[]`; pyright and mypy are
not installed and are reported unavailable rather than passed.

## Installed-wheel evidence

The isolated wheel smoke runs outside the source cwd with `PYTHONNOUSERSITE=1`
and only the wheel target on `PYTHONPATH`. It proves the wheel contains the
scoped implementation and that installed Gateway, ReadModel, HTTP, and
`StdioMcpClient` share one Registry. While a matching client remains ready,
`GET /api/v1/connections` reports registered/active/ready counts `1/1/1` and a
ready card. After close it reports exact `0/0/0` and `not_registered`. A ready
other-workspace probe that throws is never called by either HTTP read.

## Browser evidence

An isolated HOME/workspace/Gateway fixture was checked at an actual 1280×900
viewport. Acceptance covered exact empty, selected ready, ready release,
starting, timeout, process exit, safe probe failure, current/history opposite,
disabled+ready, selected limited, fail-once Retry, and unmatched other-workspace
entries. All eight main routes and all five Memory subroutes rendered. The three
Connections fact columns did not overlap; no horizontal overflow occurred;
console warning/error logs were empty; the Dock remained mock/read-only; and the
DOM contained no server key, secret, absolute fixture path, exception text, or
`[object Object]`.

The final screenshot is
`artifacts/minicode-dashboard-batch-5c-2b-1-connections.jpg`. It is 1280×900 and
has no black padding at the bottom. Browser tabs, viewport override, fixture
Gateway, HOME/workspace, and temporary directories were removed.

## Final certification and deferred work

Both final full regressions passed `1985 passed, 2 skipped, 3 warnings` in
84.00s and 82.44s. The warnings are only the three existing unregistered
benchmark markers. The official evaluator passed 108 cases with 37 confirmed
gaps, zero remote calls, and Phase 3B true. Accepted gold remained SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3033592, and mtime ns `1784135857000000000`.

Batch 5 is closed only at this scoped read/certification boundary. Batch 6 has
not been implemented. Cross-process current state, heartbeat, polling, push,
persistence, process controls, and changes to MCP request timing, Agent Loop,
RunJournal semantics, Memory, Session, TUI, or Headless defaults remain deferred.
