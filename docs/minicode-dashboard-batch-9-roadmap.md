# MiniCode Dashboard Batch 9 Roadmap

## Current boundary

Batch 8C, Batch 9A-1, Batch 9D-1A and Batch 9D-1B are complete. MiniCode
already provides real Dashboard Chat, Sessions, Runs, usage/cost, Memory and
Tool approvals, MCP state, SSE invalidation, local persistence, the v34 visual
Shell and the v35 Agent Observatory/core-page hierarchy. The user has deferred
Batch 9A-2, 9A-3, 9B and 9C; those stages are not complete, passed or
implemented. Work now continues only through the bounded Batch 9D visual track.

The known Dashboard conversational user-fact Memory intake gap is deliberately
deferred. Batch 9 must not claim to solve it.

## Batch 9A — Data lifecycle and recovery

### 9A-1: Storage inventory, health snapshot and dry-run cleanup contract

- Inventory Session, Run, Turn, Memory, approval/audit, settings and lock stores.
- Identify the active Workspace and the exact Workspace-scoped storage roots.
- Add a read-only health/plan interface that reports counts, bytes, schema state,
  corruption diagnostics and what a requested cleanup would affect.
- Define reset scopes such as conversations, observability and Project Memory
  without accepting arbitrary filesystem paths.
- No deletion, migration or automatic recovery in this sub-batch.

Acceptance: the same fixture must deterministically explain why deleting
Sessions does not delete Runs, and a current-Workspace plan must never include
another Workspace or User Memory.

### 9A-2: Safe retention, cleanup and explicit reset CLI — deferred by user

- Implement a local CLI over the 9A-1 plan with `--dry-run` as the default and an
  explicit apply mode.
- Reuse Session/Run/Turn/Memory authorities and locks; do not duplicate raw
  deletion rules in the Dashboard frontend.
- Support current-Workspace cleanup and narrowly named categories.
- Stop or reject active-writer conflicts, preserve lock/config/User Memory by
  default, and verify postconditions after apply.
- Keep destructive controls out of the web UI for this batch.

Acceptance: a seeded current Workspace becomes empty only in the selected
categories; another Workspace and all preserved global data remain byte-identical.

### 9A-3: Corruption isolation, index rebuild and compatibility recovery — deferred by user

- Define supported schema versions and legacy read behavior for each store.
- Isolate one corrupt Run, Turn, Session delta or Memory entry without hiding
  healthy neighbors.
- Rebuild disposable indexes from authoritative records.
- Make recovery explicit and reportable; read-only Dashboard requests must not
  silently rewrite storage.
- Add restart and interrupted-cleanup fixtures.

## Batch 9B — Measured performance and durability

### 9B-1: Repeatable local scale baselines — deferred by user

- Measure realistic personal-demo sizes: long Sessions, thousands of Runs,
  Memory catalogs and repeated SSE reconnects.
- Record API latency, first render, refresh work, file count, memory use and open
  resource counts.
- Establish budgets before optimizing; no speculative rewrites.

### 9B-2: Targeted optimization and endurance — deferred by user

- Optimize only the measured bottlenecks, preferring pagination, bounded scans,
  reusable indexes and incremental DOM updates.
- Exercise TUI and Gateway together, Gateway restart loops, cancellation,
  approvals and cleanup while SSE clients reconnect.
- Check file-descriptor, thread, listener, temporary-file and memory growth.

## Batch 9C — Local security, packaging and operation

### 9C-1: Localhost boundary audit — deferred by user

- Re-audit every write endpoint for loopback binding, Origin, Content-Type, body
  limits, duplicate JSON keys, path/cursor validation and safe error envelopes.
- Prove approval/Turn identities cannot cross Workspace or operation boundaries.
- Keep remote access disabled by default; do not invent enterprise auth.

### 9C-2: Canonical startup and shutdown — deferred by user

- Provide one documented Gateway launch workflow with explicit Workspace, host
  and port reporting.
- Add clear port-in-use/configuration diagnostics and clean shutdown ordering.
- Verify no stale pending permissions, turns, listeners or worker resources
  survive restart.

### 9C-3: Installation and usage packaging — deferred by user

- Build and install the wheel in isolation without source-tree assumptions.
- Verify static assets and all public endpoints from unrelated working dirs.
- Publish one authoritative quick-start, troubleshooting, data-location,
  cleanup/reset and upgrade guide.

## Batch 9D — Final interface and release acceptance

### 9D-1A: Waku audit, visual system and three-column Shell — completed

- Perform a focused Waku-style visual pass without changing backend contracts.
- Unify status names, colors, spacing, typography and loading/empty/error/retry
  states across all pages.
- Verify keyboard focus, semantic controls, reduced motion, contrast and narrow
  layout behavior.

### 9D-1B: Core-page visual refactor — completed

- Apply the stable 9D-1A system to core page internals without changing their
  business authorities.
- The selected Agent Observatory hierarchy now drives Overview, and Runs,
  Sessions and all Memory subroutes share the same core-page visual contract.
- No backend, Store, action, REST/SSE schema, timer, poller or dependency
  changed.

### 9D-1C: Remaining-page visual unification — not started

- Complete the page-internal consistency pass after the core-page work.

### 9D-2: Dashboard Visual Release Candidate acceptance — not started

- From a fresh isolated environment: install, start, chat, approve a Tool,
  observe live Runs/Sessions, restart, recover data, exercise Memory approval,
  perform dry-run cleanup and then apply a scoped cleanup.
- Verify console cleanliness, no path/secret leakage, no horizontal overflow and
  deterministic shutdown.
- Freeze the final protected-source baseline, preserve semantic gold, publish a
  release checklist and stop unless a release-blocking defect is found.

Because storage recovery, performance/durability and operational/security
hardening are deferred, 9D-2 cannot be described as full release certification
unless the user resumes and completes those stages.

## Explicitly deferred

- Dashboard natural-language user-fact Memory intake and automatic conversational
  Memory candidates.
- Optional Batch 8B local management controls.
- Remote access, accounts, teams, database/queue infrastructure, WebSocket,
  arbitrary shell administration and enterprise observability.

## Recommended next task

Proceed to **Batch 9D-1C: remaining-page visual unification** using the stable
v34 Shell and v35 core-page interfaces. Batch 9D-1B is complete; 9D-2 has not
started. The conversational user-fact Memory intake gap remains explicitly
deferred.
