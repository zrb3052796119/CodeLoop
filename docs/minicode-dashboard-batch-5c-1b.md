# MiniCode Dashboard Batch 5C-1B

## Outcome and boundary

Batch 5C-1B extends the existing read-only `GET /api/v1/connections`
response with bounded historical MCP runtime observations from retained Runs.
It does not report current MCP state. `liveMcpCount` remains `null`, every
runtime projection reports `current: unavailable`, and historical presence is
reported as `stale`, never live, online, connected, healthy, or heartbeat data.

No Agent Loop, MCP client, RunJournal writer, Memory, Session, Skill, TUI,
Overview, Ops, route, write control, polling, SSE, or third-party runtime
dependency was added or changed. All 19 v10-protected production files remain
unchanged.

## Architecture and API seam

`minicode.web.mcp_runtime_aggregation` is the independent read-side module. It
uses only the public `RunJournal.list_runs()` / `list_events()` interfaces plus
the shared `mcp_server_key()` and `normalize_mcp_runtime_payload()` contracts.
The scan is capped at 100 Runs, 1,000 events per Run, 100 events per page, and
20 low-cardinality diagnostics.

`DashboardReadModel.connections()` keeps the raw validated effective server
name only as an internal association value. User/project merge semantics are
unchanged. The internal value is removed before response projection, and the
API exposes only the existing safe server summary plus additive historical
runtime fields.

The latest observation is selected deterministically by
`(timestamp, run_id, sequence)`. Every candidate payload is normalized again;
malformed, unknown-field, wrong-workspace, or inconsistent event records are
excluded. Deleted or renamed servers contribute only to a unique unmatched
count. Their keys are never returned.

The additive response contains:

- summary counts for configured, observed configured, unobserved configured,
  and unmatched historical servers;
- an aggregate `mcpRuntime` with historical count and last-observed time;
- explicit scan `coverage`, limits, retained/scanned Run counts, and truncation;
- one `runtime` object per effective server, including outcome,
  `connectionAttempted`, observed protocol, and retained-window count.

Configuration and Journal failures are isolated. A configuration source error
does not prevent valid runtime projection, and a global Journal failure leaves
configuration visible while marking only runtime facts unavailable/error. One
bad Run or event cannot block valid retained observations from other Runs.

## UI behavior

Connections → MCP keeps the accepted Waku shell and separates each card into:

1. current configuration (`configured`, `disabled`, or `error`);
2. current MCP status, always unavailable;
3. retained Run history, rendered with cautious historical styling and copy.

The page distinguishes request success, connection failure, request failure,
and no observation in the scanned window. A disabled server remains disabled
even when a historical observation exists. The coverage card states that data
is historical/partial and that current status is unavailable. The existing
manual refresh, request-id stale-response guard, Retry state, and HTML escaping
remain in use; no automatic data refresh was added.

## Packaging, safety, and verification

The new Python module is included by the existing `minicode.web*` setuptools
package discovery. The installed-wheel smoke creates an isolated effective MCP
configuration and retained event, then verifies the installed Gateway response
and the absence of the internal server key.

Final verification results:

- related ReadModel/HTTP/frontend/wheel matrix: 129 passed;
- complete pytest, before and after certification: 1902 passed, 2 skipped;
- warnings: only the three existing unregistered benchmark markers;
- Ruff, explicit `py_compile`, full `compileall`, and both production
  `node --check` commands passed;
- v10 verifier: candidate match, 19/19 protected files, v1-v10 integrity true;
- semantic evaluator: 108 cases, 37 confirmed gaps, zero remote calls, passed;
- accepted gold remained SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
  with unchanged mtime and size.

Browser acceptance used an isolated HOME/workspace and real RunJournal records
for historical request success, historical connection failure on a disabled
server, no matching observation, and one unmatched removed server. At 1280 by
900 pixels the three-column layout had no horizontal overflow. All eight main
routes and five Memory subroutes rendered, manual Connections refresh
recovered the complete historical view, and browser warning/error logs were
empty. No absolute path, server key, object-coercion text, or current
online/healthy claim appeared in the Connections DOM. The listener, tab,
viewport override, and fixture data were cleaned.

## Source-driven implementation detail and next seam

The current public `RunJournal.list_runs()` API has no workspace filter. To
preserve the hard 100-Run bound, Connections reads that bounded retained
Journal page and then rejects every Run/event whose persisted workspace id does
not equal the resolved current workspace id. Consequently `retainedRuns`
describes the safely available Journal total, while all MCP observation counts
and server associations are strictly current-workspace facts inside the scanned
window. No unbounded pre-filter scan or new protected Journal API was added.

The Batch 2/current-state seam is deliberately still closed. A future phase
needs a separately authorized source of current MCP connection state; it must
not reinterpret these retained observations or reuse `stale` as live state.
