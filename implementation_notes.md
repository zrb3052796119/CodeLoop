# MiniCode Reliability 1B-1C.1 Implementation Notes

## Outcome

Phase 2A certification now separates deterministic acceptance, advisory
wall-clock observation and explicit strict enforcement. The evaluator always
measures real latency, and the original `canonical P95 <= 5.0 ms` threshold is
unchanged. Default pytest and CLI acceptance validate correctness, quality,
integrity/no-network and deterministic save budgets without allowing one
machine-scheduling sample to decide success. The explicit
`--enforce-wall-clock-performance` CLI mode makes that same real measurement
strictly enforceable.

The prior deterministic projection removed latency values but retained the
P95-derived legacy gate, strict result and acceptance. It now removes or
normalizes every wall-clock derivative while retaining all deterministic
behavior and acceptance evidence. Synthetic tests prove opposite wall-clock
results project identically and deterministic budget changes remain visible.

The default CLI writes generated `memory-retrieval-phase2a-evaluation`
outputs. Any output resolving to an accepted Phase 2A artifact/doc is rejected
before evaluation or writing. Legacy `performance_gates` are retained only as
truthful observations.

## Exact source and pin delta

The Phase 2A certification delta is exactly:

- `scripts/evaluate_memory_retrieval_phase2a.py`;
- `scripts/memory_retrieval_phase2a_evaluator.py`;
- `tests/test_memory_retrieval_phase2a_evaluator.py`.

Only those three `PHASE2A_FROZEN_HASHES` entries changed in
`scripts/memory_retrieval_phase2b_evaluator.py`, for reason
`phase2a_wall_clock_policy_hardening`. That file is otherwise pin-only and
hashes
`e8c075c3e114c2c5f9c1645e1b53ea365973de883eb3f6a8b2c833ecbef0765d`.
Only its corresponding `PHASE2B_FROZEN_HASHES` entry changed in the semantic
evaluator. Normalization tests prove both pin-only deltas and controlled
tampering tests prove one-file mismatch reporting without automatic resigning
or accepted-artifact writes.

No `minicode/` file, algorithm, fixture, accepted Phase 2A/2B artifact,
semantic gold, threshold, dependency or Dashboard behavior changed. No v40
was created.

## Certification

The single explicit strict run was preceded by CPU idle samples of 79.75% and
85.31% with load 2.37/2.61/2.41. It measured canonical P50/P95
1.748625/2.768958 ms, passed the unchanged 5.0 ms gate, reported zero remote
calls and exited 0. It was not retried, and all `/tmp` outputs were removed.

Validation results:

- Phase 2A directed: 105 passed;
- Phase 2B/consolidation: 56 passed;
- semantic freeze: 34 passed;
- first complete suite: 3355 passed, 2 skipped, 3 existing warnings in
  207.13 seconds;
- official semantic evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls and passed;
- second complete suite: 3355 passed, 2 skipped, 3 existing warnings in
  207.33 seconds;
- scoped Ruff, py_compile, compileall and production JavaScript syntax:
  passed.

Accepted semantic gold remains
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3,033,592 and mtime_ns 1,784,135,857,000,000,000. Behavior projection
and per-case fingerprint remain `b9fabf0a...bbd60` and `b73da444...8667`.

Production verifier remains v39/parent v38, 62/62, candidate/current true and
all v1-v39 manifest integrity true. Functional Audit remains 185 capabilities,
124 pass and exactly seven non-WEB issues. The certified wheel/browser
evidence from Reliability 1B-1C is reused because production and formal
frontend bytes are identical; packaging tests ran in both complete suites.

Reliability 1B-1C.1 passes and Reliability 1B-1C is closed. Reliability 1B-2
was not entered. Detailed evidence is in
`docs/minicode-reliability-1b-1c1-phase2a-certification-hardening.md`.

---

# MiniCode Reliability 1B-1C Implementation Notes

## Outcome

The built-in core/read-only `web_search` Tool now uses a fixed, serial,
bounded Baidu→DuckDuckGo provider chain. One immutable deep provider boundary
owns fixed endpoint construction, closed provider outcomes, separate streaming
HTML parsers and safe textual result-URL projection. The thin Tool adapter owns
strict request validation, a shared 15-second monotonic deadline, six-second
per-provider caps, exactly-once fallback and low-cardinality redacted output.

`minicode/tools/http_utils.py` exposes a GET-only bounded final-status response
interface for providers while retaining the exact existing `HTTP >= 400 ->
http_error` behavior of `web_fetch` and `http_request`. Destination validation,
the shared bounded resolver, all-public DNS policy, IP pinning, TLS hostname,
per-hop redirects and the 1 MiB/64 KiB response limits remain authoritative.

The exact production delta is:

- changed `minicode/tools/http_utils.py`;
- modified and newly protected `minicode/tools/web_search.py`;
- added `minicode/tools/search_providers.py`.

Archive, Memory, Agent Loop, Session, RunJournal, MCP, Dashboard and Permission
behavior remain unchanged. Runtime dependencies remain empty. Optional live
external-network smoke was not run.

## Historical pre-hardening certification status

Functional Audit now records 185 capabilities with 124 pass, 44 partial,
7 fail, 1 unavailable, 6 blocked and 3 not reachable. `WEB-001` and `WEB-002`
are closed; `tool.web_search` is pass for deterministic, installed-wheel,
safety, truthfulness and status with no issues. The remaining seven issues are
`SEC-002`, `SEC-004`, `TOOL-001`, `TOOL-002`, `TOOL-003`, `SEC-005` and
`MEM-001`; the audit command therefore still exits 1 by design.

Active production baseline v39 has parent v38, protects 62 sources and has SHA
`9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`.
Its lineage is exactly one changed, two added-to-protection and zero removed
files; candidate/current and every v1–v39 manifest pin pass. Immutable v38
retains SHA
`49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.

Search/provider tests pass 159 cases; network/Tool/Audit focused tests pass
333; resolver/Permission/Agent/Gateway compatibility passes 632 with two
skips; production-baseline/semantic tests pass 239; installed-wheel tests pass
9. Scoped Ruff, `py_compile`, compileall and formal JavaScript syntax checks
pass.

The official evaluator remains 108 cases, 37 confirmed gaps, Phase 3B true,
zero remote calls and passed. Accepted semantic gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3,033,592 and mtime_ns 1,784,135,857,000,000,000.

After the decoded-provider-target review fix, the first full suite passed
`3314 passed, 2 skipped, 3 warnings` in 205.85 seconds. The second full suite
did not pass: `2 failed, 3312 passed, 2 skipped, 3 warnings` in 208.12
seconds. Both failures are in the unchanged Phase 2A evaluator. Its generated
report records canonical retrieval P95 `5.269083 ms` against the frozen
`5.0 ms` threshold, and a read-only system sample immediately afterward was
`84.38% CPU idle`. The test also proved two evaluations can disagree on that
timing-derived gate. No rerun was used to select a passing sample, and no
Memory code, test, threshold, manifest or gold was changed.

A subsequent same-contract review added a raw trailing-control rejection
before URL trimming. Its RED reproduced the unsafe acceptance; final scoped,
compatibility, wheel, baseline and static gates pass. The failed full suite was
not rerun after this final guard, in order not to select a lucky timing result.

Temporary loopback servers, wheel/install directories, isolated HOME and
workspace fixtures are context-owned and cleaned. This section records the
pre-hardening state; the blocker is resolved by the certified 1B-1C.1 work
above. Reliability 1B-2 was not entered. Detailed web-search evidence is in
`docs/minicode-reliability-1b-1c-web-search-provider-chain.md`,
`docs/minicode-functional-reliability-audit-1a.md` and
`docs/memory-retrieval-production-baseline-v39.md`.

---

# MiniCode Dashboard Batch 9D-1A Implementation Notes

## Outcome

The formal Dashboard now uses a Waku-inspired Local Agent Control Room visual
system and a responsive three-column Shell. A shared semantic token contract
governs surfaces, text, borders, states, local typography, spacing, shape,
elevation, motion, layout and stacking in both Light and Dark modes. Navigation,
Page Header, Chat Dock, resizers, reopen controls, dialogs, toasts and base
controls are visually coherent without changing page-internal data structures.

The exact production delta is only:

- `minicode/web/static/index.html`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/assets/app.js`

`cost-format.js`, every Python runtime source, REST/SSE schema, Store, action,
approval/deletion authority, single EventSource, polling fallback, Agent Loop,
Memory/Pricing behavior and dependency list remain frozen.

## Certification

Production baseline v34 has parent v33, protects 56 sources and has SHA
`3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb`.
Its lineage is exactly three changed, zero added and zero removed files;
candidate/current and every v1-v34 manifest pin pass. v33 and all earlier
manifest bytes remain immutable.

The final wheel SHA is
`b472c5a9bbbb1f195a10673c5ad8cedf9ea1520820d33c9257bb08bbeb2ac61a`.
It served the exact formal resources from an isolated install and unrelated
working directory. The final focused matrix passed 215 tests, baseline tests
passed 171, Phase 2B passed 28, static checks passed, and two full suites passed
`2943 passed, 2 skipped, 3 existing warnings`.

The official evaluator remains 108 cases, 37 confirmed gaps, Phase 3B true,
zero remote calls and passed. Accepted semantic gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3,033,592 and mtime_ns 1,784,135,857,000,000,000.

Real browser evidence at 1280, 1024, 700 and 480 px covers all routes, the
current Memory route set, Light/Dark, both approvals, deletion dialog, Data
Health, long records, resize, collapse/reopen, Draft and focus return. Page
console logs were empty and no horizontal overflow, overlap, object rendering,
secret or formal absolute path was found. Detailed evidence is in
`docs/minicode-dashboard-batch-9d-1a.md` and the Waku audit.

Batch 9D-1A is complete. Batch 9D-1B is next. Batch 9A-2/9A-3, 9B and 9C remain
deferred by user and incomplete; Batch 9D-2 remains only a future Dashboard
Visual Release Candidate until those stages resume.

---

# MiniCode Dashboard Batch 8A-2.2 Implementation Notes

## Outcome

The shared file-review boundary now converts every verified Workspace-local
resolved target to one canonical POSIX relative Diff label. Real absolute,
relative, dot-segment, safe-parent, and filesystem-alias inputs therefore show
the same safe review through `write_file`, `edit_file`, and `patch_file`.

The Permission projector additionally requires exact target-matching Diff
headers and fails closed on real sensitive/absolute/control content in the
unchanged Diff body. This second production change was required by deterministic
REDs after the producer-only repair; no frontend Allow exception was added.

## Scope and certification

The exact production delta is `minicode/file_review.py` plus
`minicode/permission_approval.py`. Frontend bytes, REST/SSE schemas, broker
state semantics, PermissionManager/TUI caches and choices, Conversation/Agent
Loop/Session/RunJournal, Memory components, and runtime dependencies remain
unchanged. Batch 8C-2 was not entered.

Active v28 manifest SHA is
`75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5`.
It certifies 50/50 files, exact two-changed/zero-added/zero-removed lineage,
candidate/current equality, and v1-v28 integrity. v27 and accepted semantic
gold remain byte-identical. The official evaluator passes 108 cases, 37 gaps,
Phase 3B, and remote 0.

Final focused matrices pass 237 Tool/Permission/HTTP/frontend tests, 179
baseline/semantic tests, and 9 installed-wheel tests. Scoped Ruff, py_compile,
compileall, and formal JavaScript syntax checks pass. Both final full suites
pass 2,572 tests with two skips and only three historical benchmark warnings.

At 1280×900 the isolated real-Gateway browser proved relative safe headers,
both Allow/Deny buttons, exact-once Allow, side-effect-free Deny/Cancel,
sensitive deny-only behavior, restart clearing, all eight main routes and five
Memory routes, no overflow/overlap, zero console warning/error, and no seeded
path/secret/object disclosure. Detailed evidence is in
`docs/minicode-dashboard-batch-8a-2-2.md` and
`docs/memory-retrieval-production-baseline-v28.md`.

---

# MiniCode Dashboard Batch 8A-2.1 Implementation Notes

## Outcome

The formal frontend now independently fails closed for hidden, incomplete,
redacted, truncated, path, unknown, and internally contradictory permission
reviews. `permissionReviewConsistent()` is shared by payload validation and the
independent Allow guard; complete safe edit and command reviews remain
allowable.

`retirePermissionTurn()` is the single terminal cleanup boundary. It tombstones
the Turn, fences stale pending GET and decision POST generations, immediately
removes its local permission items, clears acting state, and starts one
authoritative pending reconciliation before Chat clears active identity. Fresh
authority may expose another Turn, but the retired Turn cannot revive during
the page lifetime. No permission decision or Chat request is sent
automatically.

## Scope and certification

The sole production change is `minicode/web/static/assets/app.js`; formal HTML,
CSS, cost formatting, Gateway, approval/HTTP/event backends, PermissionManager,
Conversation, Agent Loop, RunJournal, and all other production sources remain
frozen. Pending GET, decision POST, one EventSource, SSE invalidation, and the
existing polling fallback retain their authority roles. Dependencies remain
empty, and no Batch 8B/9 behavior was implemented.

Active v25 manifest SHA is
`c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef`.
It certifies the exact one-changed/zero-added/zero-removed v24→v25 delta, 45/45
current sources, candidate equality, and v1-v25 integrity; immutable v24 keeps
SHA `f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`.
Accepted semantic gold SHA/size/mtime are unchanged, and official evaluation
passes 108 cases / 37 gaps / Phase 3B / remote 0.

Focused matrices pass 100 Permission frontend, 150 Chat/Cancel/Turn, 46
Change Feed/SSE/live refresh, and 76 Web/HTTP/packaging tests. Static checks,
wheel/install/smoke, and v25 verification pass. Final full suites both pass
2,445 tests with two skips and three historical warnings.

The real isolated Gateway browser at 1280x900 proved safe Allow/Deny,
path/hidden/truncated deny-only, immediate and durable Cancel retirement,
fresh-other-Turn recovery, restart clearing, no overflow/overlap, zero console
warning/error, and no seeded disclosure. Full evidence is in
`docs/minicode-dashboard-batch-8a-2-1.md` and
`docs/memory-retrieval-production-baseline-v25.md`. Batch 8A is closed.

---

# MiniCode Dashboard Batch 8A-2 Implementation Notes

## Outcome

The formal Session dock now presents real loopback permission requests and
supports explicit Allow-once and Deny-once decisions. Change Feed and SSE use
schema v2 with a seventh content-free `permissions` resource; pending REST is
the list authority, decision REST is the write authority, and PermissionManager
remains the only judge. Batch 8A is closed without entering Batch 8B.

## Architecture and retained contracts

- Gateway composes one broker for Conversation, permission HTTP, and the Change
  Feed revision loader. Non-loopback remains unavailable and fail-closed.
- Public permission revisions are salted one-way hashes of the broker revision.
  No pending review or identity enters Changes, SSE, heartbeat, or reset.
- The browser owns one non-persistent strict Store, the existing single
  EventSource, and the existing polling fallback. It has no permission timer,
  cache, automatic decision, Chat replay, or RunJournal reconstruction.
- Exact validators, Allow double conditions, item-Turn binding, generation
  fencing, single-flight POST, GET-only Retry, deny-only projection, escaped
  reviews, and Cancel fencing keep the frontend no broader than the backend.
- The Waku layout, global SSE path, cursor/ring semantics, Chat NDJSON, final
  Session authority, Agent Loop, Memory, Skill, MCP, Pricing, TUI, and Headless
  behaviors remain intact. Dependencies stay empty.

## Certification

The exact seven-production-file v23→v24 delta is protected by manifest SHA
`f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`;
all 45 files, candidate equality, and v1-v24 integrity pass. Accepted semantic
gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
3,033,592 bytes, mtime_ns 1784135857000000000. Official evaluation passes
twice at 108 cases / 37 gaps / Phase 3B / remote 0.

Focused matrices pass 292, 321, and 315 tests; the baseline suite passes 126;
the installed-wheel suite passes 9. Scoped Ruff, py_compile, compileall, and all
formal JavaScript syntax checks pass. Both complete suites pass 2,437 tests with
2 skips and only 3 historical benchmark-marker warnings.

The real application browser at 1280x900 and 430x900 proved pending recovery,
exact-once Allow, side-effect-free Deny/Cancel, restart clearing, SSE recovery,
all main and Memory routes, no overflow/overlap, no path/object disclosure, and
zero page console warnings/errors. Detailed contracts and evidence are in
`docs/minicode-dashboard-batch-8a-2.md` and
`docs/memory-retrieval-production-baseline-v24.md`.

---

# MiniCode Dashboard Batch 8A-1.1 Implementation Notes

## Outcome

The Gateway permission projector now classifies structured command/argv before
rendering. Credentials, local absolute paths, complex shell forms, ambiguity,
redaction, and truncation all collapse to a fixed deny-only review. Safe simple
and Workspace-relative commands remain operation-scoped and reviewable. UTF-8
truncation now includes its marker inside every declared byte budget.

The production delta is exactly `minicode/permission_approval.py`; the broker
state machine, PermissionManager authority, TUI cache semantics, Headless and
non-loopback behavior, Agent/Session/Memory/Skill/MCP/Pricing/Chat/SSE
contracts, dependencies, and formal frontend are unchanged. No Batch 8A-2 UI
or store was implemented.

## Evidence

The untouched suite passed 2,377 tests with two skips and three historical
warnings. The required RED produced 22 failures and 36 passes, proving split
credential, local-path, real HTTP serialization, deny-only, and UTF-8 budget
defects. Expanded GREEN passes 73 Permission/HTTP/Conversation/Event tests and
proves zero sensitive subprocess starts after deny, timeout, or cancel, exactly
one safe start after allow, no Web allow cache, and content-free Run Detail.

Active production certification advances from immutable v22 to v23. v23 has
45 protected files and the exact one-file changed/zero-added/zero-removed
lineage documented in `docs/memory-retrieval-production-baseline-v23.md`.
Accepted semantic gold and formal frontend hashes remain the previously pinned
values. Final scoped static checks pass; the 733,226-byte isolated wheel and
Gateway approval smoke pass; official evaluation is 108/37/Phase 3B/remote
0/pass twice; and the final complete suites both pass 2,420 tests with two skips
and only three historical benchmark warnings. Accepted gold SHA, size, and
mtime remain unchanged.

The complete security contract and Batch 8A-2 handoff are in
`docs/minicode-dashboard-batch-8a-1.1.md`.

---

# MiniCode Dashboard Batch 8A-1 Implementation Notes

## Outcome

The loopback Gateway now composes one process-local, Workspace-scoped approval
authority. Real Dashboard Chat Tools block inside the existing
`PermissionManager`, can be inspected and decided through strict no-store HTTP,
and resume only for the precise current operation. Deny, timeout, cancellation,
capacity failure, unsafe review, Gateway close, restart, and remote binding all
fail closed. No approval UI or persistent permission was added.

## Architecture and retained contracts

- `minicode.permission_approval` is the single deep state-machine, wait,
  identity, projection, cleanup, and revision owner; it has no Web dependency.
- `minicode.permission_event_contract` is the shared strict content-free event
  validator used by the broker, RunJournal, and Run Detail ReadModel.
- PermissionManager remains the judge. New internal `allow_operation` and
  `deny_operation` values never enter existing TUI/session/persistent caches.
- Conversation creates an approval session inside the existing RunObservation,
  installs its prompt/checkpoint temporarily, and restores both in `finally`.
- Runtime callbacks isolate approval context, Run observation, and 7C
  presentation failures. Agent Loop explicitly copies ContextVar state through
  its nested Tool executor, preserving concurrent same-name operation binding.
- Strict GET pending and POST decision adapters are loopback-only, same-origin
  when Origin is present, query-free, bounded, duplicate-key-safe, and return no
  review text on decisions. Non-loopback composition remains unavailable.
- Final filesystem and subprocess checkpoints close Allow-versus-Cancel races
  immediately before the protected side effect.
- Formal frontend, Change Feed/SSE mapping, Chat NDJSON frames, Session/Turn/Run
  authorities, TUI, Headless, Memory, Skill, MCP, pricing, and dependencies are
  unchanged.

## Certification

Initial full baseline was 2338 passed; the missing authority/HTTP RED failed at
collection as expected. Focused GREEN passes 34 tests and the wide compatibility
matrix passes 466. Scoped Ruff, py_compile, compileall, all production
JavaScript checks, and isolated installed-wheel smoke pass. The first final full
suite passes 2377 with 2 skips and only 3 historical benchmark warnings; the
second final suite repeats the same 2377/2/3 result. Final isolated real Gateway
HTTP approval acceptance passes allow, deny, same-target reapproval, and
cancel/late-allow cases 4/4.

Active v22 SHA is
`a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`.
It protects 45/45 production sources with exact seven changed and seven added
protected entries; candidate equality and every v1–v22 pin pass. Official
semantic evaluation remains 108/37/Phase 3B/remote 0/pass. Accepted gold SHA,
3,033,592-byte size, and mtime_ns are unchanged. Formal HTML/CSS/JS hashes are
byte-identical to v21.

The complete authority, state, HTTP, security, event, and Batch 8A-2 handoff
contract is recorded in
`docs/dashboard-batch-8a-1-permission-approval.md`; the protected lineage is in
`docs/memory-retrieval-production-baseline-v22.md`. Batch 8A-2 and 8B remain
unimplemented.

---

# MiniCode Dashboard Batch 7C Implementation Notes

## Outcome

The existing Dashboard Chat POST now optionally returns a strict connection-only
NDJSON presentation stream. It exposes genuine provider Assistant deltas and
redacted Tool start/finish state before the synchronous request completes, then
uses committed Sessions REST as final authority. JSON Chat remains compatible;
global SSE remains one content-free invalidation source.

## Architecture

- `minicode/conversation_presentation.py` defines the three-method no-throw core
  seam and does not depend on Web code.
- `AgentTurnRuntime.execute(..., presentation=None)` connects only the existing
  genuine Assistant provider callback and separately isolated Tool callbacks.
- `ConversationTurnService.turn(..., presentation=None)` propagates the seam
  only to runtimes whose signatures accept it, then performs the unchanged
  Turn/Run/Session transaction.
- `minicode/web/chat_stream.py` owns exact NDJSON schemas, Unicode-safe 4 KiB
  frames, 128 KiB/512 budgets, same-name FIFO pairing, a unified lock, opaque
  Tool stream IDs, and permanent no-throw detach after write failure.
- `minicode/web/chat_http.py` negotiates `application/x-ndjson` on the existing
  route while retaining pre-header JSON errors, synchronous handler-thread
  execution, bounded write timeout, and the existing JSON success/error path.
- formal `app.js` owns one non-persistent stream store/parser. Stream, Cancel,
  Status, and SSE invalidation races share terminal deduplication; only a
  successful committed Session reread replaces provisional content.

## Contracts retained

No Assistant/Tool body enters DashboardEventStream, Change Feed, RunJournal,
TurnStore, Session partial fields, logs, diagnostics, localStorage, or
sessionStorage. Tool input/output, thinking, paths, commands, URLs, provider
identity, usage, exceptions, and credentials are never projected. Agent Loop,
provider adapters, Memory, Skill, MCP, TUI, Headless, pricing, and permission
semantics are unchanged.

## Certification

Active v21 SHA is
`5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff`.
Exact v20→v21 lineage is six changed files and two added modules, zero removed;
all 38 protected hashes, candidate equality, and v1-v21 pins pass. Semantic
evaluation remains 108/37/remote 0/pass and accepted gold SHA/size/mtime are
unchanged.

The isolated installed wheel passes the formal static/health/SSE/changes/
JSON-Chat/NDJSON-Chat/Status/Cancel/`run` surface. Final full suites pass 2338
with 2 skips and only the 3 historical benchmark warnings. Scoped Ruff,
py_compile, compileall, and formal JavaScript syntax pass; dependencies stay
empty.

At 1280x900 the real Gateway browser fixture showed three genuine incremental
Assistant states before completion, Tool running then success/error, REST final
replacement, refresh non-replay, truthful response-disconnect recovery,
cancel_requested with a late delta, durable cancellation, one SSE and zero
healthy polling, all 8 main and 5 Memory routes, no overflow/overlap, no page
console issue, and no seeded disclosure.

Full detail is in
`docs/dashboard-batch-7c-connection-scoped-streaming.md` and
`docs/memory-retrieval-production-baseline-v21.md`.

Batch 8A permission approval and Batch 8B MCP controls were not implemented.

---

# MiniCode Dashboard Batch 1 Implementation Notes

## Outcome

The approved Waku-style Dashboard now ships as a production, standard-library Web shell served by `minicode-gateway`. It remains explicitly `mock / read-only` and does not read or write Session, Memory, Skill, MCP, Agent, or TUI state.

## Architecture and interfaces

- `minicode.web.MiniCodeWebHandler` is the HTTP GET seam. It owns packaged resource loading, URL decoding, traversal rejection, deterministic MIME types, `Cache-Control: no-store`, the health routes, and JSON 404 behavior.
- `minicode.gateway.MiniCodeGatewayHandler` subclasses that Web handler and retains the existing `/run` composition plus a 1 MiB request-body limit.
- Resources are resolved with `importlib.resources.files("minicode.web")`; they do not depend on the process working directory.
- `GET /` serves `static/index.html`; `GET /assets/...` serves only validated descendants of `static/assets/`.
- `GET /health` retains the exact `{"ok": true, "service": "minicode-gateway"}` payload. `GET /api/v1/health` exposes the same contract.
- Unknown `/api/...` GET and POST routes return a structured `not_found` JSON envelope. Other unknown routes retain the legacy short JSON 404.
- `POST /run` retains its existing success, missing-prompt, `SystemExit`, and generic exception response shapes.

## Packaging

- `minicode.web` is a regular Python package.
- setuptools package data explicitly includes the production HTML, CSS, and JavaScript.
- Namespace discovery is disabled so the preserved `dashboard_prototype/` reference server is not accidentally shipped in the wheel.
- An automated test builds a wheel from a temporary source copy, verifies its resource entries, installs it into an isolated target, and serves `/` plus `/assets/app.js` from that installation.

## UI migration

- The production HTML uses `/assets/styles.css` and `/assets/app.js`.
- The confirmed three-column layout, eight primary hash routes, and five Memory subroutes are retained.
- Persistent header/navigation/session labels identify the page as `mock / read-only` and `data not connected`.
- The production mock workspace is generic; no user-machine absolute path appears in the formal assets.
- `dashboard_prototype/` remains in place and was not edited.

## Changed files

- `minicode/web/__init__.py` — Web package interface.
- `minicode/web/http.py` — standard-library GET handler and static resource implementation.
- `minicode/web/static/index.html` — production Dashboard document.
- `minicode/web/static/assets/styles.css` — approved styles plus mock-state styling.
- `minicode/web/static/assets/app.js` — approved mock UI with production asset context and no machine path.
- `minicode/gateway.py` — thin Web + `/run` composition and bounded body parsing.
- `pyproject.toml` — package discovery and package-data configuration.
- `tests/test_dashboard_web.py` — HTTP routes, MIME/cache, mock labeling, API errors, traversal, and body limits.
- `tests/test_packaging.py` — package-data declaration and isolated installed-wheel smoke test.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — durable implementation record required by the planning workflow.

## Verification

- Targeted Dashboard/packaging tests: 23 passed.
- Final full pytest: 1420 passed, 2 skipped, 3 existing benchmark-marker warnings.
- `py_compile`, `compileall`, Ruff, and production `node --check`: passed.
- Local HTTP smoke: root, assets, both health routes, structured API 404, and traversal rejection passed.
- Browser: Overview, Runs, Sessions, Memory, Skills, Connections, Ops, System, and every Memory subroute rendered. Overview and Memory Retrieval had no visible layout regression, no horizontal overflow, and no console warning/error.
- `pyright` and `mypy` were not installed.

## Plan/source deviations

- The rollout plan mentions workspace CLI configuration and optional browser opening in Batch 1, while the current Gateway source exposes only host/port environment configuration and the requested Batch 1 page has no real workspace data. This implementation preserves `MINI_CODE_GATEWAY_HOST` / `MINI_CODE_GATEWAY_PORT` and does not add an unused workspace option or `--open`; those remain release/real-data concerns.
- No Git commit was created because this workspace is not a Git worktree. The sibling repository was deliberately not modified.

## Batch 2 seam

- Add `GET /api/v1/snapshot` above the existing structured API fallback in `MiniCodeWebHandler`.
- Introduce `DashboardReadModel` behind that route; the Web handler should consume only redacted, bounded dictionaries rather than MiniCode runtime objects.
- Keep the health/static interfaces unchanged and replace the frontend's module-local mock `DATA` through a small fetch/store adapter.
- Use `schemaVersion`, `generatedAt`, workspace identity, source freshness, and per-source diagnostics in the snapshot. Do not fabricate Memory retrieval diagnostics before RunJournal instrumentation exists.

---

# MiniCode Dashboard Batch 2A Implementation Notes

## Outcome

Batch 2A adds the first real read-only vertical slice. `GET /api/v1/snapshot` now projects local Workspace, Session, Memory, Skill, Gateway, and configured-MCP metadata through one versioned, redacted contract. Only Overview consumes this contract. Runs, cost, tokens, tools, and errors are explicitly unavailable until their canonical journals exist.

## Changed files

- `minicode/web/read_model.py` — new deep read-only projection module, source isolation, bounded reads, and final redaction.
- `minicode/web/__init__.py` — exports `DashboardReadModel`.
- `minicode/web/http.py` — adds the injected `/api/v1/snapshot` route and generic 500 envelope.
- `minicode/gateway.py` — composes one startup read model using environment/cwd workspace resolution.
- `minicode/web/static/index.html` — replaces global mock badges with read-only/loading states while preserving the simulated dock.
- `minicode/web/static/assets/app.js` — adds the snapshot store and real Overview rendering; legacy mock data remains isolated to deferred pages.
- `minicode/web/static/assets/styles.css` — adds source, partial, unavailable, loading, error, and retry presentation.
- `tests/test_dashboard_read_model.py` — isolated contract, source, corruption, safety, limits, and workspace tests.
- `tests/test_dashboard_web.py` — snapshot HTTP/security/failure tests and frontend source-contract checks.
- `tests/test_packaging.py` — installed-wheel snapshot smoke with isolated HOME/workspace.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — durable Batch 2A plan, evidence, and handoff.

## DashboardReadModel interface and dependencies

The external interface is intentionally one method:

```python
DashboardReadModel.snapshot() -> dict[str, object]
```

Construction accepts a resolved workspace, optional MiniCode data directory, and injectable Session/Skill loaders plus a clock. Production uses `DashboardReadModel.from_environment()` with this priority:

1. `MINI_CODE_DASHBOARD_WORKSPACE`;
2. Gateway startup working directory.

The HTTP handler receives the model through the `ThreadingHTTPServer` composition object. A request never chooses or changes the workspace, and the same read-model instance is reused while lightweight source files are reread for each snapshot.

Dependencies are public `list_sessions()`, `discover_skills()`, `read_mcp_config_file()`, and the public Memory scope/tier/entry/file types. The HTTP handler contains no source business logic.

## Snapshot contract

The successful response is HTTP 200, JSON UTF-8, and `Cache-Control: no-store`. Its stable top-level shape is:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-07-16T07:28:05Z",
  "mode": "read-only",
  "status": "partial",
  "workspace": {
    "id": "ws_<path-hash>",
    "name": "workspace-name",
    "path": "/resolved/workspace",
    "status": "live"
  },
  "overview": {
    "sessions": {},
    "memory": {},
    "skills": {},
    "connections": {},
    "runs": {"status": "unavailable", "count": null},
    "usage": {
      "status": "unavailable",
      "costUsd": null,
      "tokensIn": null,
      "tokensOut": null,
      "toolCalls": null,
      "errors": null
    }
  },
  "sources": {},
  "diagnostics": []
}
```

Every `sources` entry uses only `live`, `stale`, `unavailable`, or `error`, with `updatedAt` and a bounded optional message. `diagnostics` contains only source, stable code, and generic bounded message.

## Overview metric sources

- Workspace: `Path.resolve()` of the startup-selected workspace; ID is a stable truncated SHA-256 of the normalized path.
- Sessions: `list_sessions()`, filtered by each metadata workspace resolved against the current workspace; only count and latest timestamp leave the module.
- Memory: side-effect-free reads of User, Project, and Local memory files; returns total/known counts, per-scope counts, tier counts, and category counts. No content leaves the module.
- Skills: `discover_skills(workspace)`; only total and the fixed public source vocabulary counts leave the module.
- Connections: Gateway is `live`; global/project MCP files contribute only unique configured server count. MCP live count remains null/unavailable.
- Runs and usage: no canonical source exists before RunJournal/usage journaling, so every value is null/unavailable.

## Read-only, failure, and redaction strategy

`MemoryManager` is deliberately not constructed because its load path can migrate, recover, back up, and save files. The read model uses existing Memory value types over a no-write parser. Each Memory scope fails independently; an incomplete total is `null`, while `knownCount` preserves successfully read scope counts.

Session indexes are preflight-validated because the current public loader intentionally turns corruption into an empty list. Corruption becomes a Session source error rather than a false zero.

Session, Memory, Skill, and MCP failures are caught separately. Valid snapshots remain HTTP 200 with local source errors. Only an unexpected failure outside all source adapters returns the generic `snapshot_failed` HTTP 500 envelope.

Session/Memory/MCP source files are limited to 2 MiB and must resolve inside their configured root. Entry/list counts and output collection depth/length are also bounded.

Final recursive redaction runs immediately before the dict leaves `snapshot()`. It covers bearer credentials, `sk-*` values, credential assignments, sensitive keys, oversized strings/collections, and deep structures. The response never contains Session messages, transcript entries, Skill bodies/descriptions/paths, Memory content, MCP command/args/env, provider configuration, environment variables, or exception text.

## Frontend behavior

Overview owns a small independent store with `loading`, `loaded`, `partial`, and `error` phases plus manual refresh. It fetches `/api/v1/snapshot`, validates schema version/basic shape, and updates only the middle Overview DOM. On failure it shows a bounded local error and Retry button; static routes remain usable.

Overview displays real Workspace, Session, Memory, Skill, Gateway, MCP-configured, source status, and freshness data. Run/usage cards say `暂无运行数据` / `尚未接入`; they contain no mock cost, Run, Token, tool, or error values. Navigation badges show real counts or an em dash for unavailable sources. The right dock remains explicitly mock/read-only.

## Verification

- Dashboard/read-model/packaging: 40 passed.
- Full pytest: 1437 passed, 2 skipped, 3 existing benchmark-marker warnings.
- Python compile, full compileall, Ruff, `node --check`, formal-source secret scan, and JS debug scan: passed.
- Wheel: built from a temporary source copy, installed into an isolated target, and served HTML, JS, and snapshot using an isolated HOME/workspace.
- HTTP: versioned schema, UTF-8 JSON, no-store, source counts, unavailable nulls, structured 404, health compatibility, run compatibility, and seeded-secret absence passed.
- Browser: controlled live Overview counts, source freshness, all main/Memory routes, no overflow, no layout regression, failure card, Retry recovery, and zero console warning/error entries passed.
- `pyright` and `mypy` were unavailable and are not claimed.

## Deferred fields and Batch 2B interface

Stable Batch 2B seams are:

- Keep `DashboardReadModel.snapshot()` and schema version 1 additive; add page-oriented bounded projections without exposing runtime objects.
- Keep `GET /api/v1/snapshot`, `/health`, `/api/v1/health`, `/run`, cache policy, and structured errors compatible.
- Reuse the existing `sources` and `diagnostics` vocabulary for real Sessions, Memory, Skills, Connections, and System page adapters.
- Replace only each deferred page's legacy mock renderer through the same fetch/store pattern; do not couple pages to MiniCode storage schemas.
- Preserve `runs` and `usage` as unavailable until RunJournal and canonical usage events exist. Memory retrieval diagnostics likewise remain deferred until journal instrumentation.

## Plan/source deviations

- The broader Batch 2 roadmap mentions six real pages, but the explicit Batch 2A request limits implementation to Overview; other page lists remain mock and visibly labeled as such.
- `MemoryManager` was not used as a loader because its current public construction path is not read-only. Existing public Memory value types and storage contract were used without changing Memory behavior.
- `AppState` counters were not used because no stable cross-process Gateway state makes them authoritative; Run/usage data remains unavailable.
- MCP configuration is counted but never presented as live connection state because no stable ToolRegistry interface exists.
- Workspace selection adds the requested environment override and cwd fallback; no unused CLI option or HTTP workspace selector was introduced.
- No Git commit was created because this workspace has no Git metadata, and no adjacent repository was touched.

---

# MiniCode Dashboard Batch 2B-1 Implementation Notes

## Outcome

Batch 2B-1 makes the Sessions page and Memory Overview, Scopes, and Lifecycle pages consume real, bounded, read-only projections. Session history and Memory content stay confined to the Gateway's startup-resolved workspace/data roots. Memory Retrieval, Memory Injection, runtime WorkingMemoryTracker data, Runs, and usage remain explicitly unavailable; the right-side Session Dock remains a separate, clearly labeled mock/read-only state source.

No Session or Memory write path, Agent Loop, MemoryPipeline operation, TUI behavior, runtime dependency, database, realtime transport, or Batch 2B-2/Batch 3 feature was added.

## Changed files

- `minicode/web/read_model.py` — adds safe page read errors, Sessions list/detail projections, Memory summary/item projection, stable cursors, filtering, budgets, safety gating, redaction, and source-local failure isolation.
- `minicode/web/http.py` — adds the three versioned GET routes, strict bounded query parsing, structured request errors, and secret-free 500 envelopes.
- `minicode/web/__init__.py` — exports `DashboardReadError` with the existing public Web interfaces.
- `minicode/web/static/assets/app.js` — adds independent Sessions, Session-detail, and Memory stores plus real page renderers; keeps the mock Dock and deferred pages separate.
- `minicode/web/static/assets/styles.css` — adds master/detail, Memory filters/items, source diagnostics, unavailable, retry, and responsive presentation without changing the Waku shell.
- `tests/test_dashboard_page_read_model.py` — adds isolated Sessions/Memory contract, paging, filter, corruption, safety, budget, path, side-effect, and cursor tests.
- `tests/test_dashboard_web.py` — adds route forwarding, HTTP contract/error, real frontend store, and local secret-free smoke coverage.
- `tests/test_packaging.py` — extends the installed-wheel smoke to the Sessions and Memory APIs and the new production JavaScript.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — record the Batch 2B-1 decisions and verification evidence.

`gateway.py`, Session persistence, Memory persistence/retrieval/pipeline modules, the TUI, and the preserved prototype were not changed for this batch.

## DashboardReadModel interfaces

The Batch 2A seam remains compatible:

```python
DashboardReadModel.snapshot() -> dict[str, object]
```

Batch 2B-1 adds:

```python
DashboardReadModel.sessions(*, limit=None, cursor=None) -> dict[str, object]
DashboardReadModel.session_detail(session_id, *, limit=None, cursor=None) -> dict[str, object]
DashboardReadModel.memory(*, scope=None, tier=None, category=None,
                          limit=None, cursor=None) -> dict[str, object]
```

`DashboardReadError(status, code, message)` is the only request-error seam consumed by the HTTP adapter. File access, projection, filtering, paging, redaction, budgets, and local error recovery stay in the read model. The HTTP handler only parses routes/queries, selects status codes, serializes JSON, and applies `no-store`.

## Sessions contracts and policy

`GET /api/v1/sessions` returns `schemaVersion`, `generatedAt`, `mode`, `source`, `items`, `page`, and `diagnostics`. Each item contains only `id`, `createdAt`, `updatedAt`, a redacted/bounded `title`, a redacted/bounded `lastMessagePreview`, `messageCount`, hashed `workspaceId`, and `status=saved`. Full messages, transcript data, permissions, Skills, MCP configuration, provider configuration, and arbitrary workspace paths are absent.

`GET /api/v1/sessions/{sessionId}` returns the same common envelope plus bounded `session`, `messages`, `page`, and `diagnostics`. The session projection contains ID/timestamps/count/workspace ID/status and `visibleMessageCount`. Each message contains only original `index`, `role`, redacted/bounded `content`, and `truncated`.

Only `user` and `assistant` string messages are visible. `system`, `tool`, `thinking`, `assistant_progress`, permission content, transcript entries, Skill bodies, MCP env, and other saved fields are never projected. A base Session plus at most 50 named delta files is parsed without calling the Session recovery/save loader.

Sessions are sorted by `(updatedAt desc, createdAt desc, id)` and page with an opaque URL-safe cursor bound to the last stable key. List pages default to 20 and max at 100. Detail pages default to 50 and max at 100, using a Session-ID-bound message offset cursor. A cursor is limited to 512 characters and strictly validates types, finite timestamps, kind, shape, ID, and filters; JSON booleans are explicitly rejected as numeric cursor values.

## Memory contract and policy

`GET /api/v1/memory` returns the common envelope plus:

- `summary`: `total`, `knownTotal`, `complete`, `byScope`, `byTier`, and `byCategory`;
- `scopes`: independent User/Project/Local source state, count, and safe semantic location label;
- `items`: ID, scope, category, tier, bounded content, timestamps, retrieval/injection counts, usefulness, lifecycle/safety/approval state, `contentHidden`, and `truncated`;
- `page`, normalized `filters`, and bounded `diagnostics`.

Allowed filters are fixed `scope` and `tier` enums plus a 1–64 character safe category. Unknown, duplicate, blank, or malformed query parameters return structured 400 responses. Items are sorted by `(updatedAt desc, createdAt desc, scope, id)` and use an opaque cursor bound to the complete current filter set and last sort key. Pages default to 20 and max at 100.

User, Project, and Local files are read independently with the Batch 2A no-side-effect parser. The code never constructs `MemoryManager`, performs canonical retrieval, calls `MemoryPipeline`, or changes counters/mtimes. Persisted lifecycle/safety/approval metadata is retained and a pure current safety assessment can only make the result more restrictive. Content is visible only when the resulting entry is `safe`, `approved`, and `active`; all other entries retain metadata but use `[Content hidden by safety policy]`.

## Safety and failure boundaries

- The workspace is resolved once at read-model construction. No HTTP query can select a workspace, directory, or file.
- Session IDs and Memory IDs have strict length/character grammars. Encoded traversal forms are rejected before filesystem access.
- Session/Memory source files resolve inside the configured MiniCode data root or workspace root. File and directory symlink escapes are rejected.
- A source file is capped at 2 MiB. Session base+deltas share a 2 MiB total budget; delta count is capped at 50. Index, message, entry, diagnostic, cursor, collection, and response limits are explicit.
- Session previews are capped at 240 characters; message content at 2,000 characters each and about 20,000 content characters per response; Memory content at 1,000 characters each and about 20,000 content characters per response.
- Bearer values, `sk-*`, credential assignments, Authorization/Cookie, token/password/secret/API-key/credential fields, provider credentials, nested sensitive keys, and bounded diagnostics pass through common final redaction.
- A malformed Session metadata record, Session delta, or Memory entry is skipped with a generic bounded diagnostic. A corrupt Session index or individual Session file returns a legal `source.status=error` payload. A corrupt Memory scope leaves other scopes/items available and reports `total=null`, `knownTotal`, `complete=false`, scope state, and diagnostics. Only a failure that prevents a legal projection returns the generic route-specific 500 JSON envelope.

## Frontend state and routes

The existing incremental fetch/store design now has independent `snapshotStore`, `sessionsStore`, `sessionDetailStore`, and `memoryStore` state. Each page supports idle/loading/loaded/partial/error, refresh/retry, paging, and retained successful data. Session detail responses are guarded by both request ID and selected Session ID, so a late old response cannot overwrite a newer selection.

Sessions load on route entry, show historical read-only metadata in the middle master/detail region, and do not alter the right-side `currentMockSession`/`openMockSession` Dock state. Memory Overview uses real scope/tier counts; Scopes uses real items and scope filters; Lifecycle uses real tier counts and entry states. Every backend string is escaped before HTML interpolation.

Retrieval says RunJournal/run-level retrieval events are unavailable and shows no candidate/selected/rendered/suppressed counts. Injection says normalized run-level injection/controller events are unavailable and shows no mode/token/rendered values. WorkingMemoryTracker is static architecture plus unavailable, with no mock entry/token numbers. Deferred main pages remain visibly mock/read-only.

## Verification

- Final related Sessions/Memory/Dashboard/packaging matrix: 198 passed.
- Final full suite: 1498 passed, 2 skipped, with the same 3 unregistered benchmark-marker warnings.
- Ruff, `py_compile`, full `compileall`, and production `node --check`: passed.
- `tests/test_packaging.py`: 9 passed. It builds a wheel from an isolated source copy, verifies packaged static entries, installs the wheel into an isolated target, starts the installed Gateway with isolated HOME/workspace, and smokes HTML, JavaScript, snapshot, Sessions, and Memory.
- HTTP fixture smoke: health, versioned health/snapshot, Sessions list/detail, Memory, and production JavaScript returned 200 UTF-8/no-store responses; seeded Session/Memory secrets were absent.
- Browser Sessions fixture: 21 records paged without duplication; selecting a Session showed only 2 visible user/assistant messages from 5 saved roles, with secrets removed; the mock Dock stayed independent.
- Browser Memory fixture: 23 entries showed User=1, Project=21, Local=1 and Working=1, Short-term=19, Long-term=2, Archival=1. Scopes paged to completion, unsafe content stayed hidden, and Lifecycle used real counts.
- Browser unavailable routes: Retrieval, Injection, and WorkingMemoryTracker showed explicit unavailable states with no fabricated runtime data.
- Browser global regression: Overview, Runs, Skills, Connections, Ops, System, Sessions, and all Memory routes opened without horizontal overflow or visible Waku-shell regression. The controlled console had zero warning/error entries.
- Browser failure/retry: fail-once Sessions and Memory endpoints produced local error cards and Retry buttons; the second requests recovered to legal empty pages without breaking navigation or producing console errors.
- `pyright` and `mypy` were unavailable and are not claimed.

## Stable next seams

Batch 2B-2 can add Skills, Connections, and System page projections as new bounded `DashboardReadModel` methods and versioned GET routes while reusing `source`, `diagnostics`, `DashboardReadError`, startup workspace resolution, redaction, budgets, and page-store patterns. It should not expand `/api/v1/snapshot` into a detail dump.

Batch 3 can introduce a canonical RunJournal/usage event contract for Runs, Memory retrieval, Memory injection, usage, tools, errors, and runtime WorkingMemoryTracker snapshots. Until that journal exists, the current unavailable contracts should remain stable rather than deriving fake run events from persisted Memory files. The mock right Dock can become real only behind a separate Session/Agent interaction design with explicit write and permission semantics.

## Plan/source deviations

- `tests/test_session_metadata.py` mentioned during an intermediate verification command does not exist in the current source tree; verification used the actual `tests/test_session.py` together with the requested Memory/Dashboard suites.
- The current Session schema can contain delta files, so detail reads include up to 50 valid `delta_####.json` files under the same bounded root/total-size policy instead of limiting the projection to the base file.
- `MemoryManager` and the public Session recovery loader were intentionally not reused because their behavior is broader than a strict no-write projection. Existing public value types and on-disk contracts were reused through bounded, side-effect-free readers.
- No Git commit was created because the workspace has no Git metadata; no repository was initialized and no adjacent repository was touched.

# MiniCode Dashboard Batch 3B-1.1 Certification Notes

## Outcome and original failure

Batch 3B-1.1 re-certifies the Memory Retrieval production-source freeze after the planned lifecycle-only entrypoint instrumentation. The original focused reproduction was `25 passed, 2 failed`: `test_network_formal_state_and_frozen_assets_are_unchanged` and `test_prior_frozen_assets_and_production_files_match_recorded_hashes` both still applied the historical v1 hashes to current entrypoints.

Read-only classification proved that the v1 mismatch set was exactly:

- `minicode/headless.py`;
- `minicode/main.py`;
- `minicode/tui/input_handler.py`.

The other seven production files, Phase 1's 15 files, Phase 2A's 8 files, Phase 2B's 12 files, and the 18-file semantic-gap dataset all matched. No unexpected fourth mismatch was certified.

## Entrypoint audit

Current-source inspection shows the three differences at the existing top-level Agent boundaries: Headless calls `observe_run()` with a source override and null Session ID, classic non-TTY CLI uses `source=tui` and null Session ID, and TTY uses `source=tui` plus `state.session.session_id`. The enclosed `run_agent_turn()` messages, cwd, model, tools, runtime, context manager, Memory manager, callbacks, and return processing retain their existing shapes. Lifecycle tests directly prove enabled/no-op/healthy/failing-Journal equivalence for output, exceptions, permissions, disposal, Session/context state, and TTY completion.

The unchanged v1 hashes directly protect `agent_loop.py`, `context_compactor.py`, `memory.py`, `memory_pipeline.py`, `memory_retrieval.py`, `memory_injector.py`, and `memory_candidate_consolidation.py`. No v1 source-body backup exists, so a line-by-line historical diff is unavailable. The lifecycle-only conclusion is the combined result of preserved hashes, current-source inspection, the Batch 3B-1 audit record, entrypoint equivalence tests, and exact semantic behavior equivalence; it is not presented as unavailable textual evidence.

## Changed files and production boundary

- `tests/fixtures/memory_retrieval_production_freeze/v1.json` — historical ten-file source evidence.
- `tests/fixtures/memory_retrieval_production_freeze/v2.json` — active twelve-file source evidence and explicit lineage.
- `scripts/memory_retrieval_production_baseline.py` — closed schema, lineage, hashing, tamper detection, deterministic candidate, pinned manifests, verify-by-default, and fixed-target writer.
- `scripts/generate_memory_retrieval_production_baseline.py` — small command-line entrypoint.
- `scripts/memory_retrieval_semantic_gap_evaluator.py` — active-v2 integrity gate plus deterministic semantic certification projection; evaluator version is 1.1.0.
- `tests/test_memory_retrieval_production_baseline.py` — v1 history, v2 lineage/current match, schema, tamper, read-only default, explicit writer, and cross-environment determinism tests.
- `tests/test_memory_retrieval_semantic_gap_evaluator.py` — active-v2/prior-freeze gates, pinned v1 artifact, full semantic projection equivalence, and projection-coverage tests.
- `docs/memory-retrieval-production-baseline-v2.md` — independent certification record.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — cumulative planning and evidence.

No production logic file was modified in Batch 3B-1.1. Agent Loop, every Memory module, Headless, main CLI, TTY, Gateway, Run lifecycle, RunJournal, Dashboard, datasets, gold labels, and Phase 1/2A/2B frozen artifacts remain untouched.

## Versioned source-baseline contract

The manifests live outside the already frozen semantic-gap dataset. v1 remains `memory-retrieval-production-v1` with its complete original ten-file map and no parent. Its raw manifest SHA-256 is `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`.

v2 is `memory-retrieval-production-v2`, parented to v1, with raw manifest SHA-256 `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`. Common-file changes are exactly the three entrypoints and use only `lifecycle_observer_entrypoint`. All other common hashes are identical and no file was removed.

`run_lifecycle.py` and `run_journal.py` are v2-only `addedFiles` with `lifecycle_observer_dependency`. They are now direct dependencies that can affect reaching Agent execution, so v2 protects them without pretending they existed in v1. Gateway is an upstream delegator into protected Headless, and TTY state types do not invoke retrieval/Agent execution, so neither was added to the historical Memory Retrieval production set.

The closed manifest validator rejects extra/missing schema keys, malformed digests, absolute/traversing/wildcard paths, unknown reason codes, undeclared changes, additions, and removals. Both manifest files have pinned raw hashes. v1 remains operational evidence: tests prove current v1 mismatches are exactly the declared child changes rather than merely retaining an unused constant.

## Generation and verification

`python3 scripts/generate_memory_retrieval_production_baseline.py` is read-only verification by default. `--print-v2` prints the candidate; `--write-v2` is the only writer and targets only the fixed v2 path after verifying the exact v1 mismatch set. No arbitrary output path exists. JSON is UTF-8, key-sorted, and contains no timestamp, process data, absolute machine path, or environment-derived metadata.

Two candidates produced under different working directories, isolated user directories, and `PYTHONHASHSEED=1/777` were byte-identical. Their SHA-256 was the pinned v2 manifest hash. A controlled temporary mutation of `memory_retrieval.py` failed verification, identified only that path, disabled candidate acceptance, and left the manifest byte-identical.

## Semantic behavior certification

The accepted v1 gold remains `artifacts/memory-retrieval-semantic-gap-baseline.json`, now pinned at SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`.

Current v2 and accepted v1 have an identical complete semantic projection SHA-256: `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`. The projection contains dataset identity/counts/splits/freeze, all three retrieval arms, every case's deterministic candidate/rank/score and downstream IDs, controller/first-loss data, overall/sealed metrics, Recall@1/3/5/10/20, MRR, NDCG, downstream recall/precision, hard-negative and leakage counts, semantic-gap adjudication/confirmed IDs, counter disagreements, side-effect semantics, remote calls, and frozen Phase 2B regression data. It excludes latency, performance samples, temporary paths, process data, formal-tree timestamps, and current execution metadata.

The accepted and current deterministic per-case fingerprint is unchanged at `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`. Dataset counts remain 108 total, 72 positive, 36 hard negative, 72 analysis, and 36 sealed. Remote calls, diagnostic counter side-effect cases, and diagnostic filesystem side-effect cases are all zero. Formal state before/after evaluation is equal.

## Verification results

- Production baseline plus semantic-gap certification: 39 passed.
- Original semantic-gap evaluator file alone: 29 passed.
- Lifecycle/entrypoint regression: 34 passed.
- RunJournal and Dashboard Runs: 29 passed.
- Requested Memory Retrieval matrix: 137 passed.
- Complete pytest: 1619 passed, 2 skipped, 0 failed in 63.78 seconds, with the same three existing unregistered benchmark-marker warnings and no new warning.
- Touched-file Ruff: passed.
- `py_compile` and `compileall -q minicode scripts tests`: passed.
- Both manifests parsed and validated; active verification reported pinned v1/v2, exact lineage, twelve matching files, and an independently matching candidate.
- Manifest/tool safety scan and deterministic repeat generation: passed.
- Project runtime dependencies remain `[]`; no third-party dependency was added.

## Deviations, limits, and Batch 3B-2 readiness

- The prompt requested historical change review, but no v1 source bodies are retained. The certification therefore explicitly distinguishes direct hashes/tests/current-source evidence from the bounded inference about the absent textual diff.
- The semantic evaluator's schema remains the accepted v1 semantic baseline because retrieval behavior did not change; only its implementation version advances to 1.1.0 for source-baseline awareness.
- v2 is not algorithm-change acceptance, does not backfill historical Runs, and must be re-certified after any future protected source change.
- The integrity prerequisite for considering Batch 3B-2 is now green. This does not implement or authorize Batch 3B-2; model/tool/assistant/Memory/Skill/usage events, Ops, SSE, and writes remain out of scope.
- The workspace has no Git metadata. No repository was initialized, no commit was created, and no adjacent repository was accessed.

---

# MiniCode Dashboard Batch 2B-2 Implementation Notes

## Outcome

Batch 2B-2 completes the planned Batch 2 real read-only page set. Skills, Connections, and System now use dedicated schema-version-1 Gateway routes and independent frontend stores. Overview, Sessions, and Memory retain their existing real projections. Runs, Ops telemetry, Memory Retrieval/Injection, runtime WorkingMemoryTracker, Skill routing, live MCP telemetry, SSE, usage, and every write operation remain explicitly mock or unavailable until RunJournal and later command batches exist.

The implementation adds no third-party runtime dependency and does not construct an MCP client, ToolRegistry, capability registry, Agent runtime, AppState snapshot, or external process. It does not modify Skill, MCP, config, Session, Memory, Agent Loop, MemoryPipeline, or TUI behavior.

## Changed files

- `minicode/web/read_model.py` — adds bounded Skills discovery/projection, configuration-only Connections, safe-field System, safe package-version fallback, source isolation, and reuse of the safer catalog adapters in Overview summaries.
- `minicode/web/http.py` — adds the three versioned GET routes, strict query whitelists, and generic secret-free failures.
- `minicode/web/static/assets/app.js` — adds `skillsStore`, `connectionsStore`, and `systemStore`, real page renderers, refresh/retry/paging/filtering, and truthful unavailable runtime sections.
- `minicode/web/static/assets/styles.css` — adds restrained catalog filters, Skill metadata, configured/read-only status presentation, and responsive layout support.
- `tests/test_dashboard_catalog_read_model.py` — new isolated Skills/Connections/System behavior, safety, failure, paging, source, and side-effect suite.
- `tests/test_dashboard_web.py` — extends route/query/failure/secret tests and frontend source-contract regressions.
- `tests/test_packaging.py` — extends installed-wheel smoke to all three new read routes.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — durable Batch 2B-2 decisions, review fixes, verification evidence, and Batch 3 handoff.

`gateway.py`, `pyproject.toml`, `minicode/skills.py`, `config.py`, `mcp.py`, `tooling.py`, `state.py`, `capability_registry.py`, the prototype, persistence modules, Agent Loop, MemoryPipeline, Session saver, and TUI were not changed.

## DashboardReadModel interfaces

Existing interfaces remain compatible. Batch 2B-2 adds:

```python
DashboardReadModel.skills(*, source=None, directory=None,
                          limit=None, cursor=None) -> dict[str, object]
DashboardReadModel.connections() -> dict[str, object]
DashboardReadModel.system() -> dict[str, object]
```

`DashboardReadError` remains the request-error seam. The HTTP adapter parses only whitelisted parameters, calls one method, selects a status, and serializes UTF-8/no-store JSON. Discovery, merging, filtering, status decisions, redaction, budgets, and recovery remain inside the deep read-model module.

## Skills contract and discovery

`GET /api/v1/skills` returns:

- the common `schemaVersion`, `generatedAt`, `mode`, `source`, and `diagnostics` envelope;
- `summary.total`, fixed four-source counts, `directoryCount`, and up to 100 safe directory names;
- `items` containing only `name`, `qualifiedName`, bounded description plus truncation flag, source, directory, bounded domains/scopes/tools/keywords, and `exampleCount`;
- `page` and normalized `source`/`directory` filters.

No item contains `path`, full Markdown, directory-description body, examples, executable content, or absolute paths. Project, user, compat-project, and compat-user roots preserve the existing effective precedence. Exact qualified names use first-root wins semantics.

The public `discover_skills()` implementation reads complete files without Dashboard byte/root constraints, so production page discovery reuses its public `SkillSummary`, frontmatter, and description semantics through an internal bounded adapter. Each root/directory/file resolves inside the configured workspace or MiniCode data root. Files are capped at 2 MiB, roots and entries are capped, malformed UTF-8/frontmatter/metadata is skipped locally, and neither `load_skill()` nor any installer/remover is called. An injected non-default `skill_loader` remains supported for isolated tests and Batch 2A compatibility.

Skills sort deterministically by case-folded qualified name, qualified name, source rank, and name. Pages default to 20 and max at 100. The opaque URL-safe cursor is bound to both filters and the last complete sort key. Descriptions are capped at 400 characters; each metadata list returns at most 20 values of 64 characters; examples are count-only; the response text budget is 30,000 characters.

## Connections contract and MCP semantics

`GET /api/v1/connections` returns:

- common envelope and diagnostics;
- summary with real Gateway status, effective configured MCP count, `liveMcpCount=null`, and completeness;
- a fixed local HTTP Gateway projection;
- explicit `mcpRuntime.status=unavailable`;
- effective MCP server summaries containing only redacted/bounded name, effective scope, configured/disabled/error status, unavailable live status, and optional protocol;
- independent user/project config-source states and counts.

Configuration precedence follows current MiniCode behavior for this explicit page scope: user `~/.mini-code/mcp.json` first, then project `.mcp.json` overrides the same server name, with top-level per-server merge and nested env merge. The project scope owns an overridden effective record. Configured never means connected/live. Session history is not used as current MCP state.

No command, args, env name/value, cwd, Authorization, Cookie, token, provider configuration, tool/resource/prompt count, process status, or latency leaves the read model. Files are independently bounded/root-checked. Invalid entries and nested fields are locally diagnosed. Review found that a malformed nested env could originally break an override merge; it now becomes an empty ignored field, preserves the safe server summary, marks the source incomplete, and has a dedicated regression. Responses expose at most 100 of up to 1,000 effective configured servers and report bounded truncation.

## System contract and safe-field whitelist

`GET /api/v1/system` contains only:

- application name, package version, and Dashboard schema version;
- Python version, semantic platform family, CPU architecture, and fixed process mode `gateway`;
- hashed workspace ID, safe workspace name, and readable/error status;
- feature statuses for Dashboard, Sessions, Memory, Skills, MCP config/runtime, Runs, usage, SSE, and writes;
- semantic storage-source statuses with `writable=null` because permissions are deliberately not inspected;
- bounded generic diagnostics.

The version seam is `importlib.metadata.version("minicode-py")`; source checkout safely falls back to `0.1.0`. System reuses the no-write Session, Memory, Skill, and MCP source adapters and isolates their failures. It does not return or inspect the Python executable, HOME, environment, sys.path, argv, PID/process details, provider/model configuration, raw paths/config, permissions, Session/Memory content, Skill bodies, or MCP execution fields. It never creates a directory or health-check file and never connects externally.

## Frontend behavior

`skillsStore`, `connectionsStore`, and `systemStore` each own independent idle/loading/loaded/partial/error state, request generation, refresh, and retry. Skills additionally owns filter state, cursor paging, and a filter-key guard so an old response cannot overwrite a new filter. Existing snapshot, Sessions, Session-detail, and Memory stores are unchanged.

Skills shows real totals/source/directory filters and safe summaries, with loading/empty/partial/error/retry/load-more states. Routing is a static unavailable card waiting for Batch 3 `skill.routed` events and has no scores, use counts, or active state.

Connections shows the real local Gateway separately from MCP configuration. The MCP view states that configured is not connected, shows true empty config when applicable, and has no connect/reconnect/edit/test controls or simulated latency/tool/resource values.

System shows only the whitelist fields and feature/storage statuses. It has no configuration, cleanup, model, or restart controls. Runs, usage, MCP runtime, SSE, and writes are visibly unavailable.

All backend strings pass `esc()` before HTML interpolation. Runs and Ops retain their existing visibly mock/read-only state. The right-side Session Dock remains an independent mock/read-only store.

## Failure, redaction, and resource behavior

- All source files retain the 2 MiB limit and configured-root symlink checks; Skill directory/root symlink escapes are also rejected.
- Query parameters are whitelist-only. Skills source and directory have strict enums/grammars; limit is 1–100; cursor is max 512 characters and validates kind, filters, complete key, and types.
- Common recursive redaction covers `sk-*`, Bearer values, credential/password/secret/token/API-key/Authorization/Cookie assignments, sensitive keys, nested provider credentials, collection depth/counts, diagnostics, names, descriptions, metadata lists, versions, platform values, and workspace labels.
- A Skill file/root, MCP source/entry/field, Session index, or Memory scope fails independently. Legal partial projections stay HTTP 200 with `source.status=error` plus diagnostics; frontend phase becomes partial. Only failures outside projection recovery return route-specific generic 500 envelopes.
- No read path writes, migrates, installs, deletes, repairs, reconnects, starts a client/server, or changes source bytes/mtime.

## Verification

- New catalog read-model suite: 24 passed.
- Final related Dashboard/packaging/Skill/config/MCP/Session/Memory matrix: 244 passed.
- Final full suite: 1531 passed, 2 skipped, with the same 3 existing unregistered benchmark-marker warnings in 55.26s.
- Ruff, `py_compile`, full `compileall`, production `node --check`, formal static path/secret scan, debug-statement scan, no-write/runtime-construction scan, and dependency check passed.
- Runtime dependencies remain `[]`. `pyright` and `mypy` were unavailable and are not claimed.
- Wheel/package: the 9 packaging tests build a wheel from an isolated source copy, inspect packaged assets, install it into an isolated target, start the installed Gateway, and smoke HTML, JavaScript, snapshot, Sessions, Memory, Skills, Connections, and System.
- HTTP fixture: all health and read routes returned UTF-8 JSON/no-store; 21 Skills, 3 effective MCP configurations, real System fields, Sessions, and Memory were present; seeded Skill/MCP/Session/Memory secrets were absent.
- Browser Skills: 21 items paged to completion; User and engineering filters returned 3 and 15 items; full bodies, paths, and secrets were absent; Routing was unavailable.
- Browser Connections: Gateway was live; three MCP records were configuration-only with unavailable live state; no simulated latency/tool/resource values or execution fields appeared.
- Browser System: version, Python, platform, architecture, workspace ID/name, and feature/storage status rendered; forbidden paths/process/config/secrets were absent.
- Browser global regression: eight main routes, five Memory subroutes, Skills Routing, and Connections MCP rendered with no horizontal overflow or visible shell regression. Right Dock stayed mock/read-only.
- Browser fail-once server: Skills, Connections, and System each showed a local error and Retry, then recovered to legal empty/live pages. Browser warning/error console entries were zero.

## Batch 2 completion and Batch 3 seam

Batch 2 now has real read-only Overview, Sessions, Memory Overview/Scopes/Lifecycle, Skills, Connections, and System. Snapshot remains an Overview summary rather than a detail dump. Runs and Ops remain mock/read-only; Memory Retrieval/Injection, WorkingMemoryTracker runtime data, Skill routing, MCP live telemetry, usage/cost/tokens/tools/errors, SSE, and writes remain unavailable.

Batch 3 should add a canonical `RunJournal` event seam without changing these page contracts: `skill.routed`, `memory.retrieved`, `memory.rendered`, model usage, tool/assistant events, run lifecycle, and runtime freshness can then feed Runs, Ops, Skill Routing, Memory runtime pages, and MCP/runtime freshness. Event recording must stay optional/best-effort and separate from future `RunController` write authority.

## Plan/source deviations

- The requested data source preferred `discover_skills()`/`discover_skill_directories()`, but their current full-file discovery lacks Dashboard size/root/symlink and granular-error guarantees. The implementation preserves their schema and precedence through a bounded internal adapter instead of weakening the established safety contract.
- Connections deliberately reads only the explicitly requested user MCP and project `.mcp.json` sources. It does not call full runtime settings/provider resolution because that would broaden exposure and is unnecessary for the page.
- Connections top-level source is `stale` for healthy configuration-only data and `error` for partial source/entry failures; the frontend maps the latter to partial. No `partial` value was added to the established backend source vocabulary.
- System uses a static `0.1.0` source fallback because the current `minicode` package exposes no `__version__`; installed wheels use package metadata.
- No Git commit was created because this workspace has no Git metadata; no repository was initialized and no adjacent repository was touched.

# MiniCode Dashboard Batch 3A Implementation Notes

## Scope and outcome

Batch 3A adds the canonical, versioned RunJournal foundation and a real read-only Runs page. It does not instrument Agent execution, change `/run`, or connect TUI, Headless, Session, Memory, Skill, MCP, usage, or Ops runtime data. Existing Dashboard pages and the independent mock/read-only Session Dock remain compatible.

## Changed files

- `minicode/run_journal.py` — new deep module for Run/Event records, validation, redaction, storage, recovery, ownership, pagination, and explicit retention.
- `minicode/web/read_model.py` — adds current-workspace Runs list/detail projections through the RunJournal public interface.
- `minicode/web/http.py` — adds strict `GET /api/v1/runs` and `GET /api/v1/runs/{run_id}` routing.
- `minicode/web/static/assets/app.js` — replaces mock Runs data with independent real list/detail stores, filters, paging, refresh/retry, and coverage states; makes Ops explicitly unavailable.
- `minicode/web/static/assets/styles.css` — adds Run lifecycle, master/detail, event, metric-unavailable, and responsive styles.
- `tests/test_run_journal.py` — new journal contract, recovery, validation, concurrency, isolation, retention, and failure tests.
- `tests/test_dashboard_runs_read_model.py` — new safe projection, paging/filter, visibility, recovery, and request validation tests.
- `tests/test_dashboard_web.py` — extends HTTP route/failure/secret and frontend-contract coverage.
- `tests/test_packaging.py` — verifies RunJournal and Runs APIs from an isolated installed wheel.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — preserve and extend the cumulative implementation record.

`gateway.py`, Agent Loop, Headless, CLI/TUI, Session persistence, MemoryPipeline, Skill/MCP runtime, prototype assets, and project dependencies were not changed for Batch 3A.

## RunJournal interface

The public seam is:

```python
RunJournal.create_run(...)
RunJournal.append_event(run_id, event_type, payload=None, ...)
RunJournal.transition(run_id, status, ...)
RunJournal.list_runs(status=None, source=None, limit=20, cursor=None)
RunJournal.get_run(run_id)
RunJournal.list_events(run_id, limit=50, cursor=None)
RunJournal.enforce_retention(...)
```

Versioned `RunRecord`, `RunEvent`, `RunPage`, `EventPage`, and `RetentionResult` value objects plus specific validation, transition, ownership, not-found, and storage errors keep callers independent of layout and recovery details.

Each workspace uses the existing stable ID algorithm and stores canonical data under `<data_dir>/dashboard/workspaces/<workspace-id>/runs/<run-id>/`. A Run directory contains atomically replaced `metadata.json`, append-only `events.ndjson`, and transient writer ownership. `runs/index.json` is best-effort and disposable; reads do not depend on it.

Run IDs and Event IDs are generated, validated opaque identifiers. A writer token, process ID, and in-process mutex enforce one writer per Run while allowing independent Runs to be created concurrently. Directory creation and traversal are symlink-safe. Event lines are bounded JSON, appended with no-follow/append semantics where available, flushed, and fsynced. Metadata uses a same-directory temporary file, fsync, atomic replace, and directory fsync.

Lifecycle transitions are event-first, then metadata checkpoint. Readers reconcile status, timestamps, count, and sequence from valid events, so a lagging or advanced metadata file cannot fabricate a lifecycle event. Legal transitions are enforced, terminal retries are idempotent, and terminal Runs cannot return to running. Middle corrupt lines are locally skipped with bounded generic diagnostics; an incomplete final line is ignored; append refuses to continue after a partial last record.

Payloads are strict JSON values with finite-number, depth, string, collection, event, and file budgets. Paths, exceptions, enums, and other Python objects are rejected. Sensitive keys and token-like values are redacted before disk. Run metadata is restricted to `origin`, `retryOf`, and `correlationId`.

Retention is never implicit on GET or write. The explicit operation can cap terminal age, terminal count, and total bytes; it validates ownership boundaries, follows no symlink, removes only terminal Run directories, and isolates deletion failures. Empty/read-only queries do not create storage or change mtimes.

## Dashboard and HTTP contracts

`DashboardReadModel.runs(...)` returns safe current-workspace summaries, fixed Journal/instrumentation coverage, source state, status totals, filters, and cursor paging. `run_detail(...)` returns safe Run fields and fixed event summaries without payload. Usage, cost, tokens, tool calls, and error counts remain `unavailable`/`null` rather than inferred.

`GET /api/v1/runs` accepts only `status`, `source`, `limit`, and `cursor`. `GET /api/v1/runs/{run_id}` accepts only `limit` and `cursor`. Invalid IDs/parameters return structured 400 responses; missing, foreign-workspace, or invalid records remain invisible as 404; unexpected errors use route-specific generic envelopes without secrets. Existing health, snapshot, Session/Memory/Skill/Connections/System routes, static serving, and `POST /run` retain their behavior.

The Runs UI supports empty/loading/loaded/partial/error/retry states, status/source filters, list paging, event paging, and stale-response guards for filters and selection changes. It clearly states that the Journal is ready while TUI, Headless, and Gateway instrumentation awaits Batch 3B. It exposes no cancel, retry-run, delete, or write control. Overview, Memory runtime subpages, Skill Routing, Ops, and metrics retain explicit unavailable states rather than interpreting Journal zero as complete MiniCode history.

## Verification

- Dedicated RunJournal + Runs read-model suite: 29 passed.
- Related Dashboard/read-model/packaging matrix: 173 passed in 29.92s.
- Final full suite: 1570 passed, 2 skipped, and 3 existing unregistered benchmark-marker warnings in 60.67s.
- Ruff passed all Batch 3A touched Python files. Whole-repository Ruff still has 681 pre-existing findings outside this scope; none were mass-edited.
- `py_compile` passed for the RunJournal/Web/Gateway modules; `compileall -q minicode tests` and production `node --check` passed.
- Runtime dependency inspection returned `[]`; no third-party dependency or build chain was added. pyright/mypy were unavailable and are not claimed.
- The nine packaging tests built an isolated wheel, verified the RunJournal/assets, installed it into an isolated target, started its Gateway, and loaded the Runs APIs and static assets.
- HTTP smoke passed health, versioned health, snapshot, Runs list/detail, Sessions, Memory, Skills, Connections, System, JavaScript, cache/content-type behavior, secret absence, and unchanged `/run` validation.
- Browser acceptance passed empty and populated Journals, 23-Run list paging, status/source filters, 58-event detail paging, corrupt-record isolation, fail-once error/retry recovery, all eight main routes, five Memory subroutes, Skill Routing, Connections MCP, responsive overflow checks, and zero console warning/error entries.

## Batch 3B handoff

Batch 3B should connect execution surfaces through an optional structured event sink without changing the RunJournal storage contract. Composition code can create a Run, pass an event callback into `run_agent_turn()`, map lifecycle/model/tool/assistant/Memory/Skill events to `append_event()`, and finish with `transition()`. Journal errors must be isolated and best-effort so observability never changes Agent behavior.

The event vocabulary already reserves lifecycle, model, tool, assistant, Memory retrieval/rendering, Skill routing, and usage-bearing events. Real usage/cost/tokens, Ops aggregation, MCP/runtime freshness, SSE, cancellation, retry, delete, write authorization, and retention scheduling remain future work.

## Plan/source deviations

- Full-repository Ruff is not clean: 681 findings predate Batch 3A and are outside the requested scope. Touched-file Ruff is clean.
- pyright and mypy are not installed in the workspace, so no type-check result is claimed.
- No Git commit was created because the workspace has no Git metadata; no repository was initialized or adjacent repository modified.

# MiniCode Dashboard Batch 3B-1 Implementation Notes

## Scope and outcome

Batch 3B-1 connects only top-level execution lifecycle observation for TUI, Headless, and Gateway. Each valid Agent task now produces queued, running, and exactly one completed/failed/interrupted terminal transition through the Batch 3A `RunJournal`. It does not add model, tool, assistant, Memory, Skill, usage, cost, token, MCP runtime, Ops, SSE, cancellation, retry, delete, retention, or Dashboard write behavior. `agent_loop.py`, RunJournal storage/public contracts, Session persistence, MemoryPipeline, Skill/MCP execution, and the mock/read-only right Dock remain unchanged.

## Changed files

- `minicode/run_lifecycle.py` — new best-effort lifecycle adapter and its small public seam.
- `minicode/headless.py` — observes each valid direct Headless task; accepts a source override used only by Gateway and test-only Journal injection/disable seams.
- `minicode/gateway.py` — delegates a valid `/run` to Headless with `source=gateway` and does not create another Run.
- `minicode/main.py` — observes only classic non-TTY Agent turns after local routing has completed.
- `minicode/tui/input_handler.py`, `minicode/tui/state.py` — observe the event-driven TTY worker and associate the real `SessionData.session_id`; add test-only Journal injection/disable fields.
- `minicode/web/read_model.py` — publishes lifecycle-live/historical-partial coverage and a bounded journaled Runs summary in Overview.
- `minicode/web/static/assets/app.js` — renders real Overview/Run lifecycle coverage and preserves explicit unavailable boundaries.
- `minicode/web/static/assets/styles.css` — adds lifecycle-only state styling and a 1400 px master/detail stacking breakpoint found during browser review.
- `tests/test_run_lifecycle.py`, `tests/test_run_entrypoint_lifecycle.py` — adapter, three-entrypoint, exception, cleanup, duplicate, Session ID, invalid-request, and behavior-equivalence coverage.
- `tests/test_dashboard_runs_read_model.py`, `tests/test_dashboard_read_model.py`, `tests/test_dashboard_catalog_read_model.py`, `tests/test_dashboard_web.py`, `tests/test_packaging.py` — coverage, Overview, frontend, responsive, HTTP, wheel, isolated install, and installed `/run` regressions.
- `task_plan.md`, `notes.md`, `implementation_notes.md` — cumulative plan, evidence, and implementation record.

The workspace has no Git metadata, so the file list is maintained from the implementation record rather than a Git diff.

## Actual execution call graph and unique observation points

```text
direct Headless -> run_headless(source=headless) -> observe_run -> run_agent_turn
Gateway POST /run -> run_headless(source=gateway) -> observe_run -> run_agent_turn
interactive TTY -> _handle_input -> _run_agent_background -> observe_run -> run_agent_turn
classic non-TTY CLI -> main input loop -> observe_run(source=tui) -> run_agent_turn
```

Gateway intentionally reuses the Headless observation seam with an explicit source override. It never opens its own lifecycle context, so one valid `/run` creates exactly one `gateway` Run and no extra `headless` Run. Interactive `run_tty_app()` is not wrapped globally; only its per-turn background worker is observed. Memory commands, slash/local commands, direct tool shortcuts, empty input, re-rendering, health/read requests, invalid Gateway bodies, and the internal `tools/task.py` subagent path return or execute outside these observation points.

## Lifecycle adapter contract and boundaries

The stable public seam is:

```python
observe_run(
    *,
    workspace: str | Path,
    source: str,
    title: str,
    session_id: str | None = None,
    journal_factory: JournalFactory | None = None,
    enabled: bool = True,
) -> ContextManager[None]
```

Context entry constructs the Journal, calls `create_run()` (the canonical queued event), and immediately transitions to running. A normal context exit transitions to completed. An ordinary `Exception` transitions to failed with fixed reason `execution_failed` and re-raises the original object. `KeyboardInterrupt` or `SystemExit` transitions to interrupted with fixed reason `execution_interrupted` and re-raises the original object. A normal Agent Loop return is therefore executor completion, including the loop's existing assistant fallback behavior; it is not claimed as a task-quality verdict.

Headless begins observation after prompt/cwd validation and before runtime/component construction. The classic non-TTY CLI begins after its local Memory/command/tool routing and system-prompt preparation, immediately around its top-level Agent turn. TTY begins inside the per-turn worker immediately before Agent execution and includes the existing context-state save and result publication. Gateway validation completes before Headless is called.

Only `RunJournal.create_run()` and `RunJournal.transition()` are used. The adapter does not append lower-level events, know storage paths, import Web code, or change the Journal contract. Titles are whitespace-normalized and bounded before the Journal applies its canonical redaction. Fixed generic observation warnings contain neither prompt nor exception text.

## Session, exception, cleanup, and failure isolation

TTY reads `state.session.session_id` at worker start and stores that real ID; absent sessions use null. Direct Headless and classic non-TTY CLI use null because those paths do not own a persisted `SessionData`. Gateway is also null because it delegates the current one-shot Headless path.

Journal factory, create, running-transition, terminal-transition, and logging failures are caught inside the observation module. A failed running transition leaves a truthful queued Run and disables the later terminal transition. Observation can also be explicitly disabled for equivalence tests. No Journal failure replaces Agent messages, return text, HTTP status/JSON, exception identity/type, permission cleanup, tool disposal, TTY state restoration, autosave/context behavior, or render completion.

Headless retains its existing ordinary Agent-error conversion to `Error: ...`, but the exception first crosses the lifecycle context and is recorded failed. Component-construction errors retain their previous propagation. Existing config-error conversion remains a `SystemExit`, so it is recorded interrupted rather than reclassified. TTY still catches ordinary worker exceptions into its existing result channel; `KeyboardInterrupt`/`SystemExit` cross the observer, record interrupted, propagate from the worker, and still execute permission/UI cleanup. Classic non-TTY cleanup is now guarded by `finally`, preserving `permissions.end_turn()` if Agent or observation fails.

## Dashboard coverage and Overview contract

Runs list/detail and Overview expose the fixed coverage object:

```json
{
  "journal": "live",
  "tui": "live",
  "headless": "live",
  "gateway": "live",
  "historical": "partial",
  "scope": "lifecycle-only"
}
```

`live` means the execution code path is instrumented, not that a process is currently online. `historical=partial` means pre-instrumentation tasks were not backfilled. Snapshot uses one bounded `list_runs(limit=1)` projection to publish journaled count, by-status totals, latest update time, and coverage. An empty Journal truthfully reports zero; it is not interpreted as complete MiniCode history. Runs and Overview state that a normal return is not task-quality truth.

Cost, tokens, usage, tool calls, model/tool/assistant events, errors as a metric, Memory retrieval/rendering/injection, Skill routing, WorkingMemoryTracker data, MCP runtime, Ops telemetry, SSE, and Dashboard write controls remain unavailable/null. Memory Retrieval/Injection and Skill Routing explicitly wait for Batch 3B-2. The frontend performs manual refresh only; the existing one-second metadata clock is not a data poll.

## Behavior-equivalence and regression evidence

The execution tests compare disabled/no-op, healthy, and failing Journals over the same fake execution. They assert identical Headless return text, Gateway status/JSON, TTY messages/transcript/result, original exception behavior, `tools.dispose()`, permission begin/end, and Session/context-save behavior. Dedicated adapter and entrypoint suites cover completed, failed, `KeyboardInterrupt`, `SystemExit`, invalid/empty requests, component failures, every Journal phase failure, TTY real Session ID, local-command exclusion, and the Gateway one-Run/no-headless invariant.

- Dedicated lifecycle/entrypoint suites: 34 passed.
- Related RunJournal, Dashboard, packaging, Agent Loop, TTY/TUI, integration, and release matrix: 265 passed, 2 skipped.
- Complete pytest: 1605 passed, 2 skipped, 2 failed, with 3 existing unregistered benchmark-marker warnings in 65.53 s. Both failures are the semantic-gap evaluator's production-file freeze gate: its recorded hashes intentionally include `minicode/headless.py`, `minicode/main.py`, and `minicode/tui/input_handler.py`, the three entrypoints this batch was explicitly required to change. All Phase 1/2A/2B frozen assets still match, and the other seven production-retrieval hashes match. The baseline was not rewritten because doing so would modify an unrelated Memory evaluator and mask the planned entrypoint changes.
- Touched-file Ruff passed. `py_compile`, full `compileall -q minicode tests`, and production `node --check` passed. `pyright` and `mypy` are unavailable and are not claimed.
- Runtime dependency inspection remains empty; no framework, frontend build chain, or third-party runtime package was added.
- The nine packaging tests build and inspect an isolated wheel, install it into an isolated target, start the installed Gateway, load packaged assets/all read APIs, and execute an installed `/run` that creates exactly one completed `gateway` Run.

## HTTP and browser acceptance

An isolated HOME/workspace Gateway used a controlled local fake execution. A real direct Headless call created one completed `headless` Run. One real HTTP `POST /run` returned the compatible `{ok: true, response: ...}` envelope and created exactly one completed `gateway` Run. A legal TUI Journal fixture supplied `source=tui` and `sessionId=browser-session-01`; separate automated TTY integration tests prove the actual TTY worker wiring and real Session ID, so the fixture is not represented as an interactive TUI browser run. Secrets in password, API-key, and Bearer-shaped titles were redacted on disk/API/UI.

Runs showed exactly queued, started, and completed lifecycle events; cost, tokens, tools, and errors remained unavailable. Overview showed three journaled Runs, by-status totals, lifecycle-live coverage, historical partial, and lifecycle-only scope. Sessions, Memory, Skills, Connections, Ops, and System stayed within their established real/unavailable boundaries. All eight primary routes, all five Memory subroutes, Skill Routing, and the MCP/Connections view opened without horizontal document overflow. The right Dock stayed mock/read-only and browser console warning/error entries were zero.

The first 1280 px visual screenshot exposed compressed vertical metadata in the two-column Runs master/detail region. A focused responsive fix stacks only the Sessions/Runs master-detail grids at viewport widths up to 1400 px. Re-verification measured a 602 px Run row, one grid column, no overflow, normal text wrapping, unchanged three-shell columns, and no console entries. Screenshots are retained at `/tmp/minicode-dashboard-batch3b1-runs-before.png` and `/tmp/minicode-dashboard-batch3b1-runs-after.png` for this implementation session.

A separate fail-once read-model fixture showed the localized Runs error card and Retry control; clicking Retry recovered to all three Runs without reload, overflow, or console errors. Health, snapshot, Runs, Sessions, Memory, Skills, Connections, System, JavaScript, cache behavior, `/run`, source uniqueness, and secret absence also passed local HTTP smoke checks.

## Batch 3B-2 stable seam and plan/source deviations

Batch 3B-2 can add an optional structured execution-event sink below the three established top-level contexts and map model/tool/assistant/Memory/Skill/usage events to the existing Run ID. It should keep `observe_run()` as the owner of lifecycle transitions and failure isolation, avoid a second Gateway context, preserve real TTY Session IDs, and leave future write authority in a separate `RunController` design.

Deviations and source-driven choices:

- The current Gateway calls Headless directly, so source override is the smallest non-duplicating seam; a separate Gateway observer would create two Runs.
- The current non-TTY CLI path has no `SessionData`, so it is correctly recorded as `source=tui`, `sessionId=null` rather than inventing an ID.
- The current Agent Loop converts some provider/tool failures into assistant fallback messages. Lifecycle completed follows the top-level return contract and does not claim semantic success.
- Current Headless config failure is an existing `SystemExit`; preserving exception behavior makes its terminal state interrupted, while ordinary construction exceptions are failed.
- Run storage follows actual execution cwd. The isolated installed-wheel smoke starts in the configured Dashboard workspace instead of changing Headless cwd semantics.
- The Batch 3A handoff proposed a generic event sink and lower-level events, but this explicitly narrower 3B-1 implements lifecycle transitions only; the rest remains for 3B-2.
- The full-suite freeze gate predates this requested entrypoint instrumentation and cannot remain byte-identical at the same time; its expected hashes were deliberately not updated.
- No Git commit was created because the workspace has no Git metadata; no repository was initialized and no adjacent repository was touched.

## Batch 3B-1.1 final certification index

The subsequent re-certification is complete. The detailed cumulative record is under `MiniCode Dashboard Batch 3B-1.1 Certification Notes` above, and the standalone authority is `docs/memory-retrieval-production-baseline-v2.md`. Active production v2 matches all 12 protected files; v1 remains pinned; the declared common-file difference is exactly Headless, main CLI, and TTY input; the two observability dependencies are explicit additions; semantic behavior projection matches accepted v1; and final pytest is `1619 passed, 2 skipped, 0 failed` with only the three existing benchmark-marker warnings. No production logic changed in Batch 3B-1.1, and Batch 3B-2 remains unimplemented.

# MiniCode Dashboard Batch 3B-2A Implementation Notes

## Scope and outcome

Batch 3B-2A adds only callback-derived `tool.started`, `tool.finished`, and one safe returned `assistant.completed` marker to the existing top-level Run. The same `RunObservation` adapter is used by direct Headless, Gateway-through-Headless, classic non-TTY CLI, and the event-driven TTY worker. Runs detail now renders the real Tool/Assistant timeline through a strict read-only whitelist.

Agent Loop and RunJournal were not modified. Model/usage/Memory/Skill/MCP-runtime events, cost/token/tool aggregates, Ops, SSE, cancellation, retry-run, delete, retention scheduling, and Dashboard writes remain out of scope and unavailable. No runtime dependency was added.

## Changed files

- `minicode/run_lifecycle.py` — yields the small no-throw observation handle, owns opaque Tool operation IDs, FIFO pairing, Assistant once-only gating, and best-effort `append_event()` isolation.
- `minicode/headless.py` — supplies observation-only Tool callbacks and records one returned Assistant marker.
- `minicode/main.py` — applies the same callbacks/returned-message policy to classic non-TTY Agent turns.
- `minicode/tui/input_handler.py` — wraps the existing UI Tool callbacks, invoking observation only after the original callback succeeds, and records the returned Assistant marker in the same Run.
- `minicode/web/read_model.py` — adds per-event Tool/Assistant whitelist projections, updates coverage, and keeps all metrics unavailable.
- `minicode/web/static/assets/app.js`, `styles.css` — render Tool name/outcome and Assistant length without payload/body, explain callback limitations, and retain the Waku three-column shell.
- `scripts/memory_retrieval_production_baseline.py`, `scripts/memory_retrieval_semantic_gap_evaluator.py` — add active v3 certification and v1/v2/v3 semantic wording.
- `tests/fixtures/memory_retrieval_production_freeze/v3.json` — deterministic v3 manifest.
- `tests/test_run_trace_observation.py`, `test_run_entrypoint_lifecycle.py`, Dashboard/packaging/baseline/semantic tests — add observer, entrypoint, projection, UI, wheel, lineage, tamper, determinism, and behavior-equivalence coverage.
- `docs/memory-retrieval-production-baseline-v3.md`, `task_plan.md`, `implementation_notes.md` — certification and cumulative implementation record.

The workspace has no Git metadata; no repository was initialized, no commit was created, and no adjacent repository was touched.

## Actual callback semantics

Serial Tools call `on_tool_start(tool_name, tool_input)` immediately before execution and `on_tool_result(tool_name, output, is_error)` after execution. Concurrent-safe Tools execute with callbacks disabled, then start/result callbacks are replayed during ordered result processing after completion. Callbacks contain no stable original Tool call ID, Agent step, or trustworthy duration.

`on_assistant_message` is not terminal-only: context summary, empty-response fallback, await-user Tool output, and other returned/fallback paths can use it. Stream, thinking, and progress callbacks are separate. Therefore this batch does not persist `on_assistant_message`, stream chunks, thinking, or progress.

## Observation interface and event contract

`observe_run(...)` still owns queued/running/terminal lifecycle but now yields:

```python
class RunObservation:
    def tool_started(self, tool_name: str) -> None: ...
    def tool_finished(self, tool_name: str, *, is_error: bool) -> None: ...
    def assistant_completed(
        self, *, content_present: bool, content_length: int
    ) -> None: ...
```

The handle exposes neither Run ID nor Journal/storage paths. Disabled, create-failed, start-failed, append-failed, and terminal modes are safe no-ops. Journal/log exceptions remain generic and never replace Agent results, exceptions, cleanup, or lifecycle terminal transitions.

Valid Tool names use the bounded `[A-Za-z0-9_.:-]` grammar; invalid values collapse to `unknown` rather than leaking fragments. Each persisted start receives an observer-local `toolop_<32 hex>` ID. Pending operations are queued independently per normalized Tool name and results pair FIFO. A result without a persisted start records `paired=false` without an operation ID. Dangling starts do not fabricate finishes. Only Tool name, local operation ID, `success|error`, and paired boolean are persisted; input, output, error body, path, command, URL, query, prompt, credentials, step, and duration are absent.

After `run_agent_turn()` returns normally, each entrypoint finds the last Assistant message. It records exactly one `assistant.completed` before `run.completed`, with `contentPresent`, a clamped non-negative length, and `kind=returned_assistant`. The body is discarded. A normal return with no Assistant records explicit `contentPresent=false, contentLength=0`; a thrown Agent exception records no Assistant completion. Fallback Assistant messages can produce a completion marker, but that marker means only that Assistant output was returned.

## ReadModel, frontend, and coverage

Run detail never returns a raw payload. Independent field whitelists validate Tool name, local operation ID, outcome, paired boolean, Assistant presence, bounded length, and fixed kind. Unknown or invalid fields are dropped. Lifecycle events retain generic summaries. Cost, tokens, Tool-call aggregate, and errors remain `unavailable/null` and are not inferred from Tool events.

Coverage is now:

```json
{
  "journal": "live",
  "tui": "live",
  "headless": "live",
  "gateway": "live",
  "historical": "partial",
  "scope": "lifecycle-tool-assistant",
  "model": "unavailable",
  "usage": "unavailable",
  "memory": "unavailable",
  "skills": "unavailable"
}
```

`live` means the code path is wired, not that a process is online. Historical Runs were not backfilled. Tool events are callback-derived with no step/duration; Assistant is a returned-output marker without body; the page is manually refreshed and is not streaming. Overview continues to show lifecycle counts only.

The Runs table displays lifecycle, Tool start/finish, escaped Tool name, restrained success/error state, Assistant completion, and content length. It exposes no input/output/body, raw payload, false step/duration/model, cost/token aggregate, or write controls. Memory Retrieval/Injection, Skill Routing, Ops, and MCP runtime remain explicitly unavailable; the right Dock remains mock/read-only.

## v3 production and semantic certification

The immutable manifest pins are v1 `b5434d98...3b417`, v2 `15df83ef...51bab`, and v3 `0722314f...6522`. The exact v2→v3 changed set is `run_lifecycle.py`, `headless.py`, `main.py`, and `tui/input_handler.py`; there are no additions/removals. Agent Loop, RunJournal, and every protected Memory/Context file retain v2 hashes.

The default tool verifies active v3; `--print-v3` is read-only and deterministic; `--write-v3` targets only the fixed manifest after strict pinned-parent and exact-delta validation. v1/v2 remain individually verifiable and byte-identical.

The full 108-case semantic evaluator passed. Accepted artifact SHA-256 remains `5629d6cf...fdd3b`, deterministic behavior projection remains `b9fabf0a...bbd60`, and per-case fingerprint remains `b73da444...8667`. Dataset/splits/arms/candidates/Gate/consolidation/rendering/controller/metrics/adjudication/frozen state all match; remote calls and diagnostic counter/filesystem side effects are zero; formal state is unchanged.

## Verification and browser acceptance

- Observer/lifecycle/entrypoint, RunJournal, Dashboard, HTTP, frontend, packaging, baseline, and semantic suites passed.
- Complete pytest: `1629 passed, 2 skipped, 0 failed` in 63.84 s, with only three existing benchmark-marker warnings.
- Touched-file Ruff, `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, and dependency inspection passed; dependencies remain `[]`.
- Packaging tests built and inspected a wheel, isolated-installed it, started the installed Gateway, loaded assets and every read API, and used installed `/run` to produce the same six-event Gateway timeline without secrets.
- Isolated browser HOME/workspace acceptance generated one real Gateway Run with a controlled fake Tool/model path. Detail order was queued, started, Tool start, Tool finish, Assistant completed, completed. UI showed `read_file`, `success`, and `24 chars`; title/input/output secrets were absent from read APIs/page; metrics stayed unavailable.
- Eight main routes, five Memory subroutes, Skill Routing, and MCP opened without horizontal overflow. Retrieval/Injection/Routing/Ops remained unavailable and the Dock remained mock/read-only. A stopped-server refresh produced the localized Runs error and Retry; restart plus Retry recovered the real Run. Browser console warning/error entries were zero.

## Batch 3B-2B handoff

Future model, usage, Memory, and Skill observability should extend the same best-effort adapter or an equally small structured sink and continue using `RunJournal.append_event()`; entrypoints and Gateway composition should not be wrapped again. Each new event needs a minimal producer contract, no body/raw-config fields, its own ReadModel whitelist, unavailable-to-live coverage transition, behavior-equivalence tests, and a new production baseline if any protected file changes. RunController/write authority, SSE, Ops aggregation, and real-time streaming remain separate designs.

# MiniCode Dashboard Batch 3B-2B Implementation Notes

## 1. Scope and outcome

Batch 3B-2B adds only safe Model request-boundary observability around actual `_model_next()` calls. It records `model.started`, `model.completed`, and `model.failed` in the same top-level Run already owned by Batch 3B-1/2A lifecycle composition. Direct Headless, Gateway-through-Headless, classic non-TTY CLI, and interactive TTY share the same adapter.

This batch does not persist Prompt, messages, output, exception bodies, thinking, streaming chunks, Model/provider identity, usage, cache data, cost, tokens, or duration. It adds no Memory, Skill, Context, Recovery, MCP-runtime, Ops, SSE, cancellation, retry-run, delete, retention, or Dashboard write behavior. Agent business behavior, Memory Retrieval behavior, Session persistence, TUI state semantics, RunJournal storage/state machine, and the mock/read-only right Dock remain unchanged. Runtime dependencies remain empty.

## 2. Changed files

Production and UI:

- `minicode/run_events.py` — new independent optional `AgentEventSink`, best-effort emitter, and observer-local Model operation ID factory.
- `minicode/agent_loop.py` — adds `event_sink=None` and instruments only the immediate `_model_next()` boundary.
- `minicode/run_lifecycle.py` — makes `RunObservation` satisfy the sink and preserves the supplied event step through the existing writer.
- `minicode/headless.py`, `minicode/main.py`, `minicode/tui/input_handler.py` — pass the existing observation into Agent Loop; existing Tool/Assistant callback wiring is unchanged.
- `minicode/web/read_model.py` — adds strict Model-event detail projection and truthful coverage.
- `minicode/web/static/assets/app.js`, `minicode/web/static/assets/styles.css` — render the safe Model timeline and updated live/unavailable boundaries.

Certification, tests, and records:

- `scripts/memory_retrieval_production_baseline.py`, `scripts/memory_retrieval_semantic_gap_evaluator.py` — immutable v1/v2/v3 plus active v4 verification.
- `tests/fixtures/memory_retrieval_production_freeze/v4.json` — deterministic fixed-target v4 manifest.
- `tests/test_agent_event_sink.py`, `tests/test_agent_model_events.py` — new sink and real Model-call operation matrices.
- `tests/test_run_trace_observation.py`, `tests/test_run_entrypoint_lifecycle.py`, Dashboard read-model/frontend tests, `tests/test_packaging.py`, and baseline/semantic tests — adapter, entrypoint, projection, UI, wheel/install, lineage, tamper, and semantic regressions.
- `docs/memory-retrieval-production-baseline-v4.md`, `task_plan.md`, `notes.md`, `implementation_notes.md` — certification and cumulative evidence.

`minicode/gateway.py`, `minicode/run_journal.py`, Agent Memory/Context modules, Session persistence modules, and TUI state/storage modules were not changed. The workspace has no Git metadata; no repository was initialized, no adjacent repository was used, and no commit was created.

## 3. Actual `_model_next()` call graph

`_model_next()` is defined once and has one lexical call site inside `run_agent_turn()`'s main while loop. Each iteration increments `step`, runs existing hook/controller work, optionally starts metrics, then performs at most one `_model_next()` invocation. No Tool, Assistant, recovery, finalization, Dashboard, or lifecycle path calls `_model_next()` elsewhere.

```text
Headless --------------------------┐
Gateway -> Headless(source=gateway)├-> observe_run -> run_agent_turn -> _model_next
classic non-TTY CLI ---------------┤
interactive TTY worker ------------┘
```

The internal `tools/task.py` sub-agent remains outside the top-level Run composition and was not expanded in this batch.

## 4. Model operation and retry semantics

One Model operation means one actual `_model_next()` invocation. A configured sink receives `model.started` immediately before the call and exactly one matching `model.completed` or `model.failed` after it. Both events carry the real Agent step and the same observer-local `modelop_<32 lowercase hex>` ID.

A normally returned `AgentStep` is completed before downstream interpretation. Empty Assistant content, progress, thinking `pause_turn`/`max_tokens`, Tool calls, and final Assistant output are therefore successful Model operations. Empty/thinking retry logic enters a new while iteration; only the next actual call receives a new operation ID and incremented step.

Generic prompt-overflow recovery and ModelSwitcher recovery leave the original operation failed. If recovery is effective and execution continues, the next real Model call starts a distinct operation at the next real step. A failed operation never receives a completed event. A max-step fallback without another Model call creates no Model event.

## 5. Exception classification

- `KeyboardInterrupt` and `SystemExit`: emit `model.failed/failureKind=interrupted`, then propagate the original object unchanged.
- `ConnectionError`: emit `network`, then preserve the existing Assistant fallback/normal return.
- `TimeoutError`: emit `timeout`, then preserve the existing Assistant fallback/normal return.
- Other `Exception`: emit `provider_error`, then preserve existing Context recovery, ModelSwitcher, or fallback behavior.

Event payloads never receive exception type or text. Existing business logs and fallback response text were not redesigned.

## 6. Agent Event Sink interface and dependency boundary

The public seam is intentionally small:

```python
class AgentEventSink(Protocol):
    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None: ...
```

Agent Loop imports only `AgentEventSink`, `emit_event_safely`, and `new_model_operation_id` from `run_events.py`. The new module imports no Agent Loop, lifecycle, RunJournal, Web, Memory, Session, Skill, or global execution state. Agent Loop does not import Run lifecycle, Journal, or Dashboard code.

`event_sink=None` is the production default and performs no emission or operation-ID generation. The safe helper passes the original payload object without copying or enriching it. It catches ordinary observation exceptions and emits only a generic payload/error-free warning; logging failure is also isolated. Execution `KeyboardInterrupt`/`SystemExit` is not swallowed.

## 7. RunObservation, lifecycle, and duplicate strategy

`RunObservation.emit(...)` forwards the structured event to the same best-effort lifecycle writer and preserves the supplied step. It exposes no Run ID, Journal instance, data directory, or storage path. Disabled/create-failed/start-failed/append-failed states remain no-throw/no-op. Lifecycle context entry/terminal behavior remains the sole owner of queued/running/completed/failed/interrupted transitions.

Model events come only from Agent Loop. Tool events remain exclusively callback-derived at the three composition entrypoints. Assistant completion remains exclusively derived once from normally returned messages at those entrypoints. Consequently there is no Model/Tool/Assistant duplication and Gateway still creates one Run by reusing Headless with `run_source="gateway"`.

## 8. Persisted Model event contract

```json
{"type":"model.started","step":1,"payload":{"operationId":"modelop_..."}}
{"type":"model.completed","step":1,"payload":{"operationId":"modelop_...","resultType":"tool_calls","contentPresent":false,"toolCallCount":1}}
{"type":"model.failed","step":1,"payload":{"operationId":"modelop_...","failureKind":"provider_error"}}
```

`resultType` is only `assistant|tool_calls`. `contentPresent` is the actual boolean presence of returned content. `toolCallCount` is a bounded non-negative integer and never a boolean. Failure kinds are fixed safe categories. No provider call ID, Model name, provider, Prompt, message list, Tool input/output, Assistant body, exception text, usage, cost, cache, token, or duration field is produced.

## 9. Runs ReadModel whitelist

Run detail continues to return no raw payload. Model projection independently accepts only a valid `modelop_<32 hex>` ID, fixed result/failure enum, real boolean content flag, bounded non-boolean integer Tool-call count, and the event envelope's real step. Unknown or invalid payload fields are dropped rather than passed through. Tool/Assistant whitelists from Batch 3B-2A are unchanged.

Run metrics for cost, tokens, Tool-call aggregate, and errors remain `unavailable/null`; they are not inferred from Model or Tool event counts. A Model failure is an attempt-level event and does not override canonical Run status.

## 10. Frontend and coverage changes

Runs renders Model request started, completed, and failed rows; real step; `assistant|tool calls`; Tool-call count; and fixed failure kind. It does not expand raw payload or display operation IDs as provider identities. It does not display Prompt, messages, output, Model/provider, usage, cost, tokens, or duration. The existing Tool name/outcome and Assistant length rows are unchanged.

Coverage is now:

```json
{
  "journal": "live",
  "tui": "live",
  "headless": "live",
  "gateway": "live",
  "historical": "partial",
  "scope": "lifecycle-model-tool-assistant",
  "model": "live",
  "tool": "live",
  "assistant": "live",
  "usage": "unavailable",
  "memory": "unavailable",
  "skills": "unavailable"
}
```

`live` means the code path is instrumented, not that a Provider is online or the page streams. Historical Runs were not backfilled. Overview still aggregates canonical lifecycle counts only. Memory Retrieval/Injection, Skill Routing, Connections live status, Ops metrics, and the right Dock retain their established unavailable/mock-read-only boundaries.

## 11. Behavior-equivalence evidence

Tests compare `event_sink=None`, a healthy `RunObservation`/recording sink, and a sink that raises on every ordinary emit. They assert equal Model call counts, messages, fallback/recovery results, original exception propagation, Headless response, Gateway HTTP status/JSON, TTY transcript/state, permissions, Tool/Assistant callback parameters/counts, Session/context save, tools disposal, and lifecycle terminal outcome.

Focused cases cover normal Assistant, Tool then Assistant, empty-response retry, `KeyboardInterrupt`, `SystemExit`, connection, timeout, generic provider failure, effective Context recovery, successful ModelSwitcher recovery, sink/logger failure, payload identity, and operation-ID uniqueness/grammar.

## 12. v4 manifest, pin, and exact lineage

The fixed manifest is `tests/fixtures/memory_retrieval_production_freeze/v4.json`. Pins are:

- v1 `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2 `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3 `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4 `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`

The exact v3→v4 changed set is `agent_loop.py`, `run_lifecycle.py`, `headless.py`, `main.py`, and `tui/input_handler.py`. The only addition is `run_events.py`; nothing is removed. v4 protects 13 files. RunJournal remains at `20f41213...144c1`, and all protected Memory/Context sources retain prior hashes.

The default verifier is read-only and reports active v4, candidate match, all four pins, exact v1→v2/v2→v3/v3→v4 lineage, and 13/13 current hashes. `--print-v4` is deterministic across cwd/HOME/PYTHONHASHSEED. `--write-v4` targets only the fixed fixture. A controlled Agent Loop tamper fails verification without rewriting the manifest.

## 13. Memory Retrieval semantic equivalence

The accepted artifact SHA remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The complete deterministic behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and the 108-case per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` for v1/v2/v3/v4.

All dataset bytes, splits, arms, candidates, ranks, scores, Gate, consolidation, rendering, counters, feedback, controller, metrics, adjudication, remote-call zero, diagnostic side-effect zero, and formal-state equality checks passed. No threshold, evaluator criterion, gold artifact, or retrieval algorithm was modified.

## 14. Test, package, HTTP, and browser results

- Focused Event Sink/Model/lifecycle/entrypoint/Agent Loop/integration: 86 passed, 2 skipped.
- Combined Journal/Dashboard/frontend/packaging/Agent regression: 231 passed, 2 skipped.
- Complete Memory Retrieval matrix: 187 passed.
- Baseline and semantic certification: 57 passed.
- Complete pytest: 1647 passed, 2 skipped, 0 failed in 63.37 seconds; only three existing unregistered benchmark-marker warnings remain.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, dependency inspection, wheel build, isolated install, installed Gateway/all read APIs/assets, and installed `/run` smoke passed. No third-party runtime dependency was added.

An isolated HOME/workspace Gateway used safe fake Model/Tool implementations through the real Agent Loop. The normal Tool Run produced 10 events in the required order with step 1 Tool request and step 2 Assistant request. The failure path produced started/failed at step 1, a new ID and started/completed at step 2 after ModelSwitcher recovery, Assistant completion, and final `run.completed`. API inspection proved ID pairing/uniqueness and absence of seeded Tool input/output, provider error, and Assistant-body secrets.

The browser rendered both timelines, all eight main routes, and all five Memory subroutes. Model failure did not change the recovered Run's completed pill. Usage/Memory/Skill sections remained unavailable and the Dock remained mock/read-only. Stopping the server made Runs enter its localized error with `重试`; restart plus Retry restored both Runs. At 1280 px, document scroll width equaled viewport width, and browser development logs were empty.

## 15. Source-driven deviations, encountered issue, and future seams

- Gateway continues to delegate Headless because a separate Gateway observer would duplicate the Run.
- Tool callbacks still lack a real step/duration, so this batch does not invent either; Model events alone carry the real Agent step.
- Existing provider fallback strings/logs may include their current error text, but event persistence/projection never does. Redesigning fallback text is outside this batch.
- A final hardening pass found that Agent Loop initially generated an operation ID even when `event_sink=None`. It was changed so the default path performs no ID generation, then v4 was deliberately regenerated and repinned.
- The first browser fixture was launched through a login shell, which changed cwd to the user home. Its HOME was isolated, so no real data was read or written, but the Dashboard workspace ID did not match the recorded Runs. The fixture was discarded and rerun through a non-login shell with a new isolated HOME/workspace; product code was unchanged.
- pyright and mypy are unavailable in this workspace and are not claimed. Whole-repository Ruff was not used as an out-of-scope legacy cleanup gate; all touched Python files are clean.

The stable Batch 3C/4 seam is the optional `AgentEventSink` plus `RunObservation.emit(...)` and the existing strict ReadModel projection pattern. Future Memory, Skill, usage/cost/token/cache/duration, MCP runtime, Ops, SSE, or write-control work must define a separate minimal safe event contract, retain best-effort failure isolation, add independent projection/frontend tests, and create a new production baseline when protected sources change. v4 authorizes only Model request-boundary observability.

---

# MiniCode Dashboard Batch 3C-1 Implementation Record

## 1. Scope and result

Batch 3C-1 adds real, read-only observation of the already-computed Skill Routing and final Memory Retrieval/Injection results to the existing top-level Run. It adds no routing, retrieval, rendering, injection, storage, Session, Memory, Skill, TUI, Tool, Model, or Agent behavior. It adds no route, runtime dependency, polling, SSE, write control, usage/cost/duration metric, WorkingMemory, Context, MCP runtime, Ops aggregation, or real Chat behavior.

The three events are `skill.routed`, `memory.retrieved`, and `memory.rendered`. Headless, Gateway-through-Headless, classic non-TTY, and interactive TTY reuse their existing `RunObservation`. Gateway still delegates Headless and therefore creates one Run and one routing event.

## 2. Production observation seams

Headless emits immediately after its single existing `SkillRouter.route(...)` result. Classic CLI and TTY keep their pre-existing prompt-construction order and emit the same already-computed result once after entering the Run. No caller routes again.

Agent Loop observes `orch.memory_pipeline.last_retrieval_result` immediately after the single existing `orch.inject_memories(...)` call returns. `None` means that the production stage did not execute and produces no Memory event. A true zero-result retrieval remains a real result and is projected normally. The observer never calls MemoryManager, MemoryPipeline retrieval/injection, a retriever, a prompt parser, or a filesystem inference path.

Observer projection and sink failures are isolated behind generic payload-free warnings. Tests prove identical messages, single Memory search, single retrieval/injection counters, one injected content occurrence, and equal success/failure counters for no sink versus a sink that raises.

## 3. Safe persisted payloads

`skill.routed` contains only schema version 1, controlled intent/action, bounded total and selected counts, at most 20 safe selected items (`qualifiedName`, controlled source, safe directory, finite bounded score), computed truncation, and a fallback boolean.

`memory.retrieved` contains only schema version 1, bounded candidate/selected/suppressed counts, no-match boolean, and a fixed no-match reason or `other`. `memory.rendered` contains only schema version 1, bounded rendered count and normalized token estimate, fixed controller mode, and injected boolean.

Prompt, messages, output, Skill description/path/reasons/tools/affinity, Memory content/IDs/query/hash/diagnostics, provider data, raw exception information, usage, cost, and duration never enter the payload. Negative tests cover invalid enums, negative/oversized counts, booleans masquerading as integers, NaN/Infinity, unsafe names/paths, malformed items, unknown fields, and malicious nested values.

## 4. Read-only API and frontend

No new business endpoint was added. The existing Runs list and detail routes remain the sole runtime source. `DashboardReadModel` independently applies a second strict whitelist and never exposes raw payload. It validates schema versions, enums, count/type bounds, finite scores, Skill grammar, source, directory, and list bounds; it computes truncation rather than trusting stored input.

Coverage is now `lifecycle-model-tool-assistant-skill-memory`, with those five instrumented paths live and historical coverage partial. Usage, cost, tokens, duration, WorkingMemory, Context, MCP runtime, Ops, and writes remain unavailable.

The independent `runtimeTraceStore` serves Skills Routing, Memory Retrieval, and Memory Injection. It performs manual Runs list/detail GETs only, with separate list/detail request IDs and selected-Run identity guards against stale responses. It does not share the persistent Memory, Skills Catalog, Runs, Sessions, or Connections store and does not poll.

The pages explicitly render loading, loaded, empty, historical/no-event, partial, error, Retry, and manual-refresh states. Retrieval shows candidates → selected → rendered plus suppressed; Injection shows rendered count, normalized Memory token estimate, controller mode, and injected state; Routing shows safe selected names/source/directory/score, counts, intent/action, fallback, and truncation. Historical Runs are not backfilled or inferred from directories/files.

## 5. v5 production certification

`memory-retrieval-production-v5` is the active baseline. The v1-v4 fixtures and pins remain immutable:

- v1 `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2 `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3 `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4 `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`
- v5 `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`

The exact v4→v5 delta changes `agent_loop.py`, `run_events.py`, `headless.py`, `main.py`, and `tui/input_handler.py`; nothing is added or removed. Run lifecycle/Journal, Skill Router, Memory Manager/Pipeline/Retrieval/Injector/consolidation, and Context compactor retain v4 hashes. Historical v4 generation/writing now validates pinned immutable evidence instead of rebuilding it from active source.

The default verifier reports all five pins, every lineage edge, candidate equality, and 13/13 active hashes. The extended semantic evaluator retains accepted artifact SHA `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`, behavior projection SHA `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and 108-case fingerprint `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` across v1-v5.

## 6. Verification results

- Focused event/entrypoint/Agent Loop/cybernetic-flow slice: 44 passed.
- Dashboard/read-model/frontend/packaging regression during implementation: 160 passed.
- Baseline certification: 16 passed. Full 108-case semantic certification: 29 passed.
- Complete pytest: 1656 passed, 2 skipped, 0 failed in 63.44 seconds; only three existing unregistered benchmark-marker warnings.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, read-only v5 verifier, and nine wheel/isolation/install packaging tests passed. Runtime dependencies remain empty. pyright and mypy are unavailable and are not claimed.

An isolated HOME/workspace Gateway accepted a real `POST /run` through Headless and the real Agent Loop. The nine ordered events were queued, started, Skill routed, Memory retrieved, Memory rendered, Model started/completed, Assistant completed, and Run completed. Safe facts were one of one Skill selected, one Memory candidate/selected/rendered, zero suppressed, 48 normalized tokens, standard controller mode, and injected true. Seeded Skill description/path and Memory content markers were absent from Runs API and browser DOM.

Browser acceptance covered the historical no-event state, real Routing/Retrieval/Injection views, all eight main routes, all five Memory subroutes, server-down error and Retry recovery, mock/read-only Dock, and unavailable Ops/usage boundaries. At 1280 px, document and viewport width were both 1280; warning/error logs were empty. Evidence is `/tmp/minicode-dashboard-batch3c1-memory-injection.png`. The isolated listener and browser test tab were closed.

## 7. Source-driven deviations and future seam

- The implementation uses existing Runs list/detail routes rather than adding a redundant runtime endpoint.
- Gateway is unchanged because a Gateway-specific observer would duplicate the Headless Run and events.
- Memory algorithms are unchanged because the final production result already distinguishes not-executed, zero-result, suppressed, and rendered states.
- The browser backend does not support a `networkidle` wait; supported DOM-loaded plus exact state checks were used without changing product code.

The future seam is the existing optional `AgentEventSink`, the three bounded events, strict `DashboardReadModel` projection, and manual runtime store. Usage/cost/cache/duration, WorkingMemory, Context, MCP runtime, Ops, SSE, write controls, and real Chat require separate minimal contracts and a new production baseline. v5 authorizes only Skill Routing and final Memory result observability.

---

# MiniCode Dashboard Batch 3C-1.1 Implementation Record

## Problem reproduction and root cause

Before the fix, the real workspace returned eight valid Skills but `source.status=error` with two `skill_read_failed` diagnostics. They corresponded to ordinary files at `.mini-code/skills/.DS_Store` and `.claude/skills/.DS_Store`.

`_discover_skill_records()` sorted every Skill-root child, incremented the bounded `scanned` counter, and passed every entry to `_validate_source_directory()`. A regular file necessarily failed the directory requirement, so harmless filesystem metadata was treated as a broken Skill directory and also consumed the 10,000-entry discovery budget. The frontend correctly rendered the backend error and required no change.

## Minimal scanner correction

Each direct root entry is now classified from `lstat()` metadata before `scanned += 1`:

- regular non-directory files are silently ignored and never read;
- real directories continue through the unchanged strict resolver/root-boundary/directory validator;
- every symlink continues through that same validator, so escaping directory links and non-directory links retain their prior diagnostic behavior;
- metadata `OSError` and unsupported special entry types remain localized `skill_read_failed` diagnostics without path or exception text;
- only directory/symlink candidates consume discovery budget.

No filename special case exists for `.DS_Store`, README, desktop.ini, metadata.json, or any other name. The fix does not use globbing and does not relax source-file containment, size, UTF-8, frontmatter, Skill-name, pagination, response-budget, or read-only constraints.

## Regression coverage

Six public `DashboardReadModel.skills()` regressions were added:

1. Ordinary files across all four roots coexist with four valid Skills, produce live/zero diagnostics, leak no names/content, preserve all file bytes/mtime, and create/delete nothing.
2. 10,050 physical regular files do not consume the production 10,000-entry limit; a valid Skill sorted after them remains discoverable without `discovery_limited`, pagination, or cursor.
3. The exact project and compat-project `.DS_Store` arrangement returns live, two valid Skills, and correct source counts.
4. Invalid UTF-8, unterminated frontmatter, invalid name, and an escaping child-directory symlink remain four real diagnostics while a valid Skill is locally returned and all secret text stays absent.
5. A root-entry metadata failure remains a safe path/error-free diagnostic while another valid Skill is returned.
6. A candidate Skill-directory enumeration failure remains localized while a separate valid Skill is still returned, without exposing exception text or filesystem paths.

The existing oversized source, root-symlink escape, Skill-file symlink escape, response budget, filter, cursor, malformed-record, and partial valid-data tests remain green.

## Real workspace before and after

Before: total 8, project 7, compat_project 1, status error, two `skill_read_failed` diagnostics.

After: total 8, project 7, compat_project 1, status live, diagnostics empty. User and compat_user remain zero. The same eight qualified Skills are readable.

The project `.DS_Store` remains size 10244, mtime_ns `1783223438916710950`, SHA-256 `560240a5eb8ed6b79a81903c2e0caa269f9ab4c0bacf4dd5bcae107998b1b158`. The compat-project file remains size 6148, mtime_ns `1782306818727388795`, SHA-256 `a03f3791ecb2c5f850735ab6d920f9582e225ad994524a1170bf752c5e7341b5`.

## v5, packaging, and complete verification

- Skill Catalog: 30 passed. Dashboard Web: 52 passed. Packaging/wheel/isolated install: 9 passed.
- Complete pytest: 1662 passed, 2 skipped, zero failures in 64.07 seconds; only three existing benchmark-marker warnings.
- Touched Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, and unchanged production `node --check` passed. Runtime dependencies remain empty. pyright/mypy are unavailable and are not claimed.
- Installed-wheel Gateway fixture includes `.DS_Store`, README, project Skill, compat-project Skill, all read APIs, and `/run`; Skills returns live/2/zero diagnostics with correct source counts and no ordinary content/path leak.
- The read-only v5 verifier reports candidate match, all five pins, exact lineage, and 13/13 protected sources. v1-v5 manifests are byte-identical to their pins; no v6, dataset, artifact, semantic gold, fingerprint, threshold, or evaluator change was made.

## Browser acceptance

An isolated HOME/workspace contained two grouped project Skills, one compat-project Skill, both `.DS_Store` files, README, and a separate RunJournal routing event. Skills Catalog displayed `read-only · live`, count 3, all three correct cards, no ordinary file or diagnostic, and working source/directory filters. Routing displayed the independent `project/runtime-route` event from RunJournal.

All eight main routes and five Memory subroutes rendered. At 1280 px the document width was exactly 1280 with no horizontal overflow. Browser warning/error logs were empty and the right Dock remained mock/read-only. Screenshot: `/tmp/minicode-dashboard-batch3c11-skills-catalog-live.png`. The browser page and Gateway listener were closed.

## Explicit non-changes and next phase

No frontend asset, SkillRouter, `minicode/skills.py` production discovery semantics, runtime Skill event, Agent Loop, Headless, main/TTY entrypoint, Memory source, RunJournal, lifecycle, Gateway behavior, dependency, or historical baseline was modified. The only production behavior change is direct-root ordinary-file classification inside Dashboard ReadModel.

The next planned product phase returns to Batch 4A Canonical Model Usage.


# MiniCode Dashboard Batch 5C-1A MCP Runtime Fact Contract Notes

Batch 5C-1A adds run-scoped, best-effort `mcp.runtime.observed` events for real MCP `tools/call` requests only. The MCP audit found that `StdioMcpClient` is constructed in `create_mcp_backed_tools()`, registry construction still eagerly calls `list_tools()`, `list_resources()`, and `list_prompts()`, and those discovery calls can start the server outside a Run for classic CLI/TTY. This batch intentionally does not change that eager behavior and does not attribute discovery, `/mcp` management, local shortcuts, resources/prompts discovery, disposer `close()`, or Dashboard Connections scans to a Run.

The observation seam is per invocation: `run_agent_turn(event_sink=...)` passes the current sink and step into `_execute_single_tool`, which creates `ToolContext(_event_sink=..., _step=...)`; MCP tool wrappers pass those values into `StdioMcpClient.call_tool()` for that request only. The client never stores a sink, so long-lived TUI clients can cross Runs without retaining a previous Run's observation handle. With `sink=None`, no server key, payload projection, event ID, RunJournal append, or failure projection is performed.

The event schema is closed: `mcpVersion=1`, `serverKey=mcpsrv_<32 lowercase hex>`, `transport=stdio`, `activity=tool_request`, `outcome=request_succeeded|connection_failed|request_failed`, `connectionAttempted=bool`, optional observed `protocol=content-length|newline-json`, and failed-only `failureKind=timeout|command_not_found|process_exit|protocol_error|request_error|other`. `serverKey` is centralized in `minicode/mcp_observation.py` as SHA-256 of stable workspace identity plus configured server name; command, args, env, cwd, URLs, headers, credentials, stderr, tool input/output, JSON-RPC payloads, exception text, PIDs, operation IDs, and local paths are not persisted.

Generic `tool.started`/`tool.finished` remain responsible for tool lifecycle/count/success/error facts. MCP runtime observation only records transport/request termination facts and is emitted between generic start and finish for ordinary MCP success/failure. `RunJournal` validates the closed MCP payload and rejects unknown MCP event types or sensitive extra fields; Run Detail projects only the whitelist and degrades invalid MCP payloads to empty details. The Timeline shows minimal safe strings such as request succeeded/connection failed/request failed and never says current connected/online. Connections remains configuration-only because retained Run facts are historical, may be stale or cross-process, and are not heartbeats.

Production baseline v10 was added with parent `memory-retrieval-production-v9`, reason code `mcp_runtime_observation`, changed protected files `minicode/agent_loop.py` and `minicode/run_journal.py`, newly protected files `minicode/mcp.py`, `minicode/mcp_event_contract.py`, `minicode/mcp_observation.py`, and `minicode/tooling.py`, and no removed files. v1–v9 manifests remain pinned and unchanged; v10 protects 19 files. Batch 5C-1B can join retained runtime facts with Connections configuration using the shared server key algorithm, but must preserve last-observed/stale semantics and must not claim live/current status.

# MiniCode Dashboard Batch 5B-2.1 Context Reconciliation Hardening Notes

Batch 5B-2.1 added `_OperationReconciliation` so Context aggregation reconciles operation-level facts deterministically before projecting Dashboard totals. Conflict operations now exit trusted totals instead of mixing incompatible evidence, and WorkingMemory latest selection was made deterministic for equal/ambiguous retained timestamps. The accepted verification baseline for that hardening was `1860 passed, 2 skipped` with only the three existing benchmark-marker warnings; production baseline `memory-retrieval-production-v9` remained active with 15/15 protected files matching and no protected-manifest expansion.

# MiniCode Dashboard Batch 5B-2 Implementation Notes

## Scope and outcome

Batch 5B-2 adds read-only Context, Recovery, and WorkingMemory aggregation for the Dashboard. The only factual input is persisted RunJournal events: `context.compacted`, `recovery.started`, `recovery.completed`, and `working_memory.observed`. The Dashboard does not execute compaction/recovery, does not construct Context runtime objects, and does not read the process-local WorkingMemory singleton.

## Files changed

- `minicode/web/context_aggregation.py` — new bounded aggregation module with strict event projection, `contextOperationId` reconciliation, duplicate/conflict/orphan/dangling diagnostics, known/unknown token coverage, breakdowns, and latest retained process-local WorkingMemory observation selection.
- `minicode/web/read_model.py` — reuses the existing single bounded Run event scan to add `ContextAggregate` alongside Model/Cost/Tool/Failure; exposes Run Detail metrics, Runs List summaries, Snapshot overview fields, and Ops fields.
- `minicode/web/static/assets/app.js` — renders Context/Recovery/WorkingMemory on Overview, Runs, Ops, and Memory Lifecycle, including partial instrumentation/historical wording and process-local WorkingMemory boundaries.
- `tests/test_context_aggregation.py` — new focused unit coverage for event validation, recovery pairing, duplicate/conflict/orphan/dangling handling, token semantics, WorkingMemory latest observation rules, and singleton tripwires.
- Dashboard/packaging tests updated for additive Batch 5B-2 fields and operation ID hiding.

## Safety and correctness boundaries

- Protected execution-chain files were not modified: `minicode/agent_loop.py`, `minicode/run_events.py`, `minicode/run_journal.py`, and `minicode/working_memory.py` remain untouched.
- Active production baseline remains `memory-retrieval-production-v9`; no v10 was created.
- WorkingMemory is never summed across Runs. Cross-Run projection reports only snapshot observation count, Runs with snapshots, and the latest retained process-local observation.
- `knownTokensFreed` is explicitly known-only; unknown compactions are counted separately and not coerced to zero.
- Operation IDs are used for server-side reconciliation only and are no longer exposed in Timeline details.

## Verification performed

- Initial focused baseline before changes: `222 passed`.
- Focused context/dashboard/package regression: `231 passed`.
- Dashboard Memory/Catalog regressions: `77 passed`.
- Production baseline verifier: `30 passed`.
- Semantic certification focused tests: `2 passed`; 108-case dataset count and accepted behavior projection unchanged.
- Full pytest: `1848 passed, 2 skipped, 3 warnings` (only the existing benchmark marker warnings).
- `python -m compileall -q minicode scripts tests` passed.
- `node --check minicode/web/static/assets/app.js` and `node --check minicode/web/static/assets/cost-format.js` passed.
- Static asset sensitive string scan passed for operation IDs, local paths, and secret patterns.
- Wheel build, isolated install, and installed Gateway smoke passed.
- Local Gateway HTTP acceptance with direct compaction, recovered recovery, dangling recovery, invalid Context event, and multiple WorkingMemory snapshots passed.

## Browser acceptance note

A real browser automation driver was not available in this environment (`playwright` module absent; `chromium-cli` absent). I did not bypass that. HTTP and DOM-oriented route tests plus JavaScript syntax and installed Gateway smoke were used as the acceptance substitute.

# MiniCode Dashboard Batch 5C-1A.2 Certification Integrity Fix

## Root cause and repaired artifact boundary

The Batch 5C-1A.1 evaluator defaulted its generated JSON output to the accepted Phase 3A gold path. Because performance fields are machine-dependent, repeated official runs changed the file from one non-authoritative digest to another and made certification order-dependent. The same change also re-pinned both semantic certification and the Phase 3B hybrid evaluator to a generated `c275...` digest.

The accepted gold has been restored byte-for-byte from the locally retained pre-5C workspace archive and is again pinned at `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The official evaluator now defaults generated JSON to `artifacts/memory-retrieval-semantic-gap-evaluation.json`; it rejects the accepted path even when explicitly supplied. Tests run the real official CLI and prove accepted bytes, SHA, and mtime are unchanged before/after, then re-run semantic certification.

## v10 contract completion

`minicode/mcp_event_contract.py` remains the unchanged singleton normalization contract used by RunJournal and Dashboard ReadModel. It is now included in every v10 expected-added, candidate, manifest, active-hash, and packaging assertion. v10 protects 19 files and is pinned at `bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`; its exact v9→v10 delta is two changed files (`agent_loop.py`, `run_journal.py`) and four added/protected files (`mcp.py`, `mcp_event_contract.py`, `mcp_observation.py`, `tooling.py`). v1–v9 bytes and pins are unchanged. A controlled contract mutation produces exactly one mismatch and never rewrites the manifest.

## Final verification and scope

The final mandated sequence passed with `1891 passed, 2 skipped` before the verifier/evaluator and the same result afterward. The baseline verifier reported candidate match, 19/19 current files, exact lineage, and all v1–v10 integrity flags true. The evaluator reported 108 cases, 37 confirmed gaps, zero remote calls, all integrity gates true, and evaluation passed while accepted SHA/mtime stayed unchanged. Accepted/generated behavior projection and per-case fingerprints remain `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60` and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

Focused MCP/Journal/Dashboard/wheel coverage passed 131 tests with localhost permission; installed-wheel Gateway smoke and explicit contract packaging passed. Ruff, `py_compile`, `compileall`, JavaScript syntax checks, secret/path scans, and dependency inspection passed. The three warnings are the existing unregistered benchmark markers. No runtime dependency was added, no Git repository or commit was created, and Batch 5C-1B, runtime behavior, Agent Loop behavior, Dashboard UI/API behavior, Session, Memory, and TUI remain unchanged.

# MiniCode Dashboard Batch 5C-1B Historical MCP Runtime Aggregation

## Scope and architecture

Batch 5C-1B associates effective user/project MCP configuration with retained,
run-scoped `mcp.runtime.observed` facts inside the existing read-only
`GET /api/v1/connections` route. The independent
`minicode.web.mcp_runtime_aggregation` module owns bounded Journal reads,
shared-contract normalization, deterministic latest selection, and safe
aggregate projections. `DashboardReadModel.connections()` remains the public
composition seam and removes its raw validated association name before API
serialization.

The scan limits are 100 Runs, 1000 events per Run, 100 events per page, and 20
diagnostics. Every Run/event must match the resolved workspace id, every MCP
payload is revalidated with `normalize_mcp_runtime_payload()`, and server
association uses the shared `mcp_server_key()` only. Deleted/renamed keys enter
one unique unmatched count and are never returned. The last fact is selected by
`(timestamp, run_id, sequence)`.

The current public RunJournal list interface has no workspace filter. To avoid
an unbounded pre-filter scan or a protected Journal change, Connections reads
the bounded Journal page first, then rejects all non-current-workspace
Run/event records. `retainedRuns` is therefore the safely available Journal
total; every runtime observation count and server association is a strict
current-workspace fact inside that scanned window.

## API and frontend semantics

Schema version 1, the route, existing Gateway/config fields, and
`liveMcpCount=null` remain compatible. Additive aggregate/server runtime fields
always say `current=unavailable` and `historical=partial`; `stale` means only
that retained observations exist. Configuration and Journal failures are
isolated, one bad Run/event remains local, empty history is unavailable rather
than error, and no read creates storage or starts MCP/process work.

Connections → MCP retains the accepted Waku shell. Each server card separately
shows current configuration, current MCP status unavailable, and retained Run
history. Request success, connection failure, request failure, and no observed
fact have cautious historical copy and styling. Disabled servers remain
disabled. Coverage, empty/partial/error/Retry, manual refresh, request-id stale
response protection, and HTML escaping are present; no polling, SSE, WebSocket,
connect control, current-status inference, Overview/Ops aggregation, or new
route was added.

## Verification and acceptance

Focused aggregation/ReadModel/HTTP/frontend/wheel verification passed 129
tests. Complete pytest passed 1902 tests with 2 skipped before and after
certification; only the three existing benchmark-marker warnings remain.
Touched Ruff, explicit `py_compile`, full `compileall -q minicode scripts
tests`, and both production JavaScript syntax checks passed. Runtime
dependencies remain empty, and the installed-wheel Gateway smoke proves the
new module and historical association work outside the source tree without key
or path disclosure.

The read-only v10 verifier reports candidate match, 19/19 protected sources,
and v1-v10 integrity true. The 108-case semantic evaluator reports 37 confirmed
gaps, zero remote calls, and pass. Accepted gold remains SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
mtime `1784135857`, size `3033592` before and after evaluation.

Browser acceptance used an isolated HOME/workspace plus real RunJournal facts
for request success, disabled connection failure, unobserved configuration,
and an unmatched removed server. All eight main routes and all five Memory
subroutes rendered at 1280×900 without horizontal overflow. Manual Connections
refresh returned to the complete historical view, console warning/error logs
were empty, and the DOM exposed no absolute path, server key, object-coercion
text, or current online/healthy claim. The viewport, tab, listener, and fixture
data were cleaned.

No v10-protected file, Agent Loop, MCP runtime behavior, Journal writer,
Memory, Session, Skill, TUI, Overview/Ops aggregation, dependency, or baseline
manifest changed. A future current-state batch needs a separately authorized
live source and must not reinterpret these retained facts.

# MiniCode Dashboard Batch 5C-2A Process-local MCP Current State

## Architecture and lifecycle

`minicode.mcp_current_state` is the new deep module. It owns frozen/slotted
server, coverage, diagnostic, and snapshot values; a closed strict normalizer;
an opaque instance handle; and a bounded `RLock`-protected registry. The hard
defaults are 256 active instances, 100 projected servers, and 20 diagnostics.
Ready liveness probes run outside the lock, transition revisions prevent stale
probe results from overwriting newer work, and a registry-local monotonic
sequence gives deterministic same-time aggregation. The fixed precedence is
`ready > starting > failed > idle`.

The snapshot exposes only schema/state version 1, process/process-local scope,
checked and updated timestamps, the shared `mcpsrv_<32 hex>` key, state, active
instance count, closed protocol/failure enums, fixed diagnostics, and explicit
Gateway-process/cross-process-unavailable/no-heartbeat coverage. Names,
configuration, command/args/env/cwd/URL, PID/process/thread/handle/token,
request data, stderr, exception text, and credentials never enter the value
contract. The registry performs no file, Journal, Session, Memory, network,
subprocess, request, or background polling work.

`StdioMcpClient(state_registry=None)` is the compatibility path and skips key,
handle, clock, token, and registry work. When supplied, one client registers
idle, marks starting around the existing real candidate attempts, becomes
ready only after initialize/initialized plus a live `poll()`, records safe
failure on initialization exhaustion or observed death, and unregisters on
public close. Protocol-candidate cleanup uses a private process cleanup path so
the same registration survives fallback. A live-process request failure stays
ready. All observer calls catch `BaseException`; return values, original
control-flow identity, request/spawn counts, protocol order, and process cleanup
remain business-owned.

`run_gateway()` owns exactly one registry on its `ThreadingHTTPServer`.
`MiniCodeGatewayHandler` passes the same reference to Gateway Headless runs,
which pass it through `create_default_tool_registry()` and
`create_mcp_backed_tools()`. `ToolRegistry` injects the dependency into the
private ToolContext field only when present. Nested Task registries inherit it
and now dispose their owned full registry even on setup/run failure. Standalone
Headless, classic CLI, and TUI remain unobserved by default. Dashboard GET and
Connections do not consume the server reference in 5C-2A.

The complete process ownership graph, visibility matrix, state transitions,
and Batch 5C-2B read seam are in `docs/minicode-dashboard-batch-5c-2a.md`.

## v11 certification

The real v10 → v11 source delta is three changed protected files
(`headless.py`, `mcp.py`, `tooling.py`) plus four newly protected callers
(`gateway.py`, `mcp_current_state.py`, `tools/__init__.py`, `tools/task.py`).
The 23-file v11 manifest is SHA-256
`c5d12d47e25db4ebd566f066420d398f7b04a53b518a407003784d8261371c71`.
v1–v10 pins are byte-identical; v10 remains `bc94fe...dbdb`. Exact current-state
module and MCP-wiring tamper tests are read-only. Detailed lineage is recorded
in `docs/memory-retrieval-production-baseline-v11.md`.

## Verification status

Contract/registry tests passed 22; MCP/ToolRegistry/Headless/Gateway passed 59;
Dashboard Connections/Packaging passed 90. Both final full regressions passed
1948 tests with 2 skipped; only the three existing benchmark-marker warnings
remain. The v11 verifier is green at 23/23 with all v1-v11 pins true. The
108-case evaluator passed with 37 confirmed gaps and zero remote calls while
accepted gold SHA/mtime/size remained unchanged.

Ruff, explicit `py_compile`, full `compileall`, both production JavaScript
syntax checks, dependency `[]`, sensitive runtime/static scans, installed-wheel
client/Gateway lifecycle, and source fake-MCP lifecycle all passed. Existing
Overview and Connections were visually checked at 1280×720: no three-column
overlap or horizontal overflow, no console warnings/errors, and Connections
still says historical/current unavailable without current online/healthy copy.
All browser tabs, temporary Gateway listeners, and fake MCP subprocesses were
cleaned.

# MiniCode Dashboard Batch 5C-2B Connections MCP Current-State Projection

## Architecture and contract

`minicode.web.mcp_current_projection` is the sole read-side boundary between
effective MCP configuration and a Gateway process snapshot. Its one public
projector accepts the resolved workspace, configured raw names, and an optional
zero-argument loader. It bounds configured input at 2,000 entries, invokes the
loader at most once, revalidates with the closed current-state normalizer, uses
the shared opaque key function internally, and returns frozen/slotted aggregates
and per-config records without exposing names or keys.

`DashboardReadModel` injects the loader but invokes it only in `connections()`.
Configuration, current snapshot, and retained historical aggregation remain
independent failure domains. Exact current counts and `byState` exist only for a
complete effective configuration and a valid nonlimited snapshot; missing,
failed, invalid, limited, or config-partial sources use null aggregates. Matched
cards remain usable when only aggregate precision is unavailable.

`run_gateway()` owns one `McpCurrentStateRegistry`. The same object is stored for
POST `/run` and captured by the request-time loader. The HTTP handler does not
know the registry contract and the compatibility fallback never constructs a
second registry. No other Dashboard page consumes the loader.

The Connections UI keeps the Waku three-column configuration/current/history
layout. It has separate current and historical coverage cards, strict nullable
count formatting, four process-local states, safe error categories, manual
Refresh/Retry, and request-id stale-response protection. Its client-side contract
validates live/non-live source shapes, aggregate precision, `byState` totals,
protocol/failure relations, and per-server/source consistency. It explicitly
shows Gateway-process scope, cross-process unavailable, heartbeat false, no
global state, and no process control.

## v12 and semantic certification

The v12 parent is immutable v11. Both the v11 builder and writer now return only
the pinned historical manifest when present; they cannot reconstruct or rewrite
history from current sources. v12 protects the same 23 files, has SHA-256
`a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7`,
and declares the sole protected change `minicode/gateway.py` with reason
`mcp_current_state_projection`. All v1-v12 pins and current hashes verify.

The accepted semantic artifact remained byte- and metadata-identical before and
after the official evaluator: SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
mtime ns `1784135857000000000`, size `3033592`. The behavior projection and
per-case fingerprints remain `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`
and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
The evaluator passed 108 cases with 37 confirmed gaps and zero remote calls.

## Verification and deviations

Focused matrices passed 84 projection/current/Gateway/HTTP tests, 73 existing
MCP tests, 199 Dashboard/frontend/packaging tests, and 9 isolated wheel tests.
The post-review frontend/wheel repeat passed 69 tests. Both final full regressions
passed `1970 passed, 2 skipped, 3 warnings` in 122.94s and 122.93s; only the three
existing benchmark-marker warnings remain.

All modified Python files passed Ruff and `py_compile`; full `compileall`, both
production JavaScript syntax checks, wheel build, isolated installation, installed
Gateway/API/static/`/run` smoke, dependency `[]`, and sensitive static scans
passed. A deliberately broader Ruff audit reported 88 existing diagnostics in
unmodified legacy files; this Batch did not change or conceal them. The workspace
has no Git metadata, so no repository or commit was created.

The installed-wheel smoke proves the packaged `StdioMcpClient` ready→close
registry lifecycle and the packaged Connections exact-empty HTTP projection. A
post-certification attempt to add one combined installed active-ready HTTP check
was rejected before execution because the environment's external execution
allowance was exhausted. The unexecuted test-only edit was reverted, preserving
the twice-full-tested tree. Source Gateway concurrent active-ready HTTP projection
is covered by the focused composition suite and browser fixture.

The pre-change sandbox initially denied localhost binding. The unchanged suite
passed with the required local-bind permission. A stale `/tmp` formal-stage guard
described an earlier tree; only that temporary guard was recaptured after the
evaluator proved the current formal tree itself was unchanged. No `~/.mini-code`
state or accepted gold was written.

Browser acceptance at 1280×900 covered valid empty state; two concurrent ready
instances and decrement/cleanup; starting; timeout/process-exit failures; both
current/history disagreement directions; disabled+ready; limited projection;
fail-once Retry; all eight main routes; and all five Memory routes. There was no
horizontal overflow or three-column overlap, console warning/error count was
zero, and the DOM exposed no secret, absolute path, server key, object coercion,
or forbidden global/health claim. The final screenshot is
`artifacts/minicode-dashboard-batch-5c-2b-connections.jpg`. All temporary tabs,
viewport overrides, listeners, subprocesses, HOME/workspace, and fixture data
were cleaned.

## Explicitly deferred

This Batch adds no cross-process state, heartbeat, long-term health, persistent
current state, background refresh, SSE/WebSocket, Dashboard MCP process control,
or TUI live-session projection. Those require separately authorized contracts.

---

# MiniCode Dashboard Batch 5C-2B.1 Workspace Diagnostic Isolation

## Deep scoped current-state seam

The defect was caused by filtering after the global Registry snapshot. By then,
unmatched ready probes had already run, global diagnostics had accumulated, and
the global response budget could already mark coverage limited. The new
`McpCurrentStateRegistry.snapshot_for(Collection[str])` validates a finite,
bounded opaque-key allowlist and applies it before probes, reconciliation,
grouping, response limits, and request-local diagnostics. `snapshot()` keeps its
unscoped compatibility behavior.

Scoped snapshots never copy unattributable accumulated global diagnostics. Only
selected probe exceptions produce the fixed request-local `probe_failed`
diagnostic and safe `other` failure category. Empty allowlists are exact and do
not call the clock/probe path in a way that creates diagnostics. Selection and
response truncation are deterministic; probes remain outside the lock and
revision checks still prevent stale probe results from overwriting newer state.

The projector now computes its bounded configured key tuple once, passes one
exact `frozenset` to a scoped loader, and retains original configured display
order. The Gateway-owned Registry is shared by POST `/run` and that loader. No UI
redesign or business-interface change was necessary.

## v13 and final evidence

The first RED failed on the absent `snapshot_for` interface; the projector RED
then failed on the former zero-argument loader seam. The completed mandatory
matrix covers unmatched probes/diagnostics/limits, empty and oversized inputs,
selected safe failures, exact loader keys, same-name workspace isolation,
non-Connections non-consumption, and concurrency.

The installed wheel now proves same-moment ready HTTP projection (`1/1/1`) and
post-close exact zeros (`0/0/0`) while an unmatched throwing probe remains at
zero calls. It runs outside source cwd and user site-packages.

v13 protects the same 23 files, has manifest SHA-256
`ef295a3aa3dcfc522d4cc421310434de3013772122f3b913b6b137144a96fc2c`,
and declares exactly `minicode/gateway.py` and
`minicode/mcp_current_state.py` changed from immutable v12 with reason
`mcp_current_state_workspace_isolation`. All v1-v13 pins and current hashes pass.

Both final full regressions passed `1985 passed, 2 skipped, 3 warnings` in
84.00s and 82.44s. The official evaluator passed 108 cases, 37 gaps, zero remote
calls, and Phase 3B. Accepted gold SHA/size/mtime ns remained
`5629d6...fdd3b` / `3033592` / `1784135857000000000`.

Browser acceptance at 1280×900 covered every required current-state scenario,
eight main routes, and five Memory subroutes. Three-column facts did not overlap,
horizontal overflow and console problems were zero, unmatched probe calls stayed
zero, and no key/path/secret/exception/object text leaked. The new full viewport
screenshot is `artifacts/minicode-dashboard-batch-5c-2b-1-connections.jpg` and
contains no black bottom padding. All temporary resources were cleaned.

Batch 5 is now complete. Batch 6 remains unimplemented; current-state
persistence, cross-process truth, heartbeat, polling/push, and process controls
remain explicitly deferred.

---

# MiniCode Dashboard Batch 6A Durable Session Turn Truth

## Deep commit seam

The TUI main loop now calls `consume_finished_tty_turn()` instead of owning
message-copy/save branches. The seam consumes one `done` marker under the
existing lock, clears it before I/O, adopts only a real returned message list,
and delegates coherent Session synchronization plus immediate persistence to
`commit_finished_tty_turn()`. Repeated main-loop observations are no-ops.

Normal completion commits the Agent's final messages. Ordinary failure and
interrupt results do not invent an assistant message; the already submitted
user message may be saved. Persistence failure leaves the Agent result intact,
retains dirty state, and uses the fixed safe notice `Session save deferred; will
retry.` The exit full save remains a final retry.

## Atomic Session files

Base Session, delta, and index JSON now share same-directory temporary write,
flush, fsync, and `os.replace()` semantics. Failed replacement preserves the
last complete target. Deltas now actually run after the first full save and carry
history, permissions, Skill/MCP, updated time, and metadata in addition to
message/transcript offsets. Loading validates before mutation, handles overlap,
skips corrupt/gapped deltas, and advances beyond corrupt sequence numbers.

This is deliberately a single-writer-per-Session design. Batch 6A adds no
cross-process locking or conflict resolution.

## Read-only Web behavior

The schema-v1 Sessions APIs were sufficient and unchanged. The Sessions page
and Dock share one bounded list/detail store, one selected Session, request-ID
and selection-revision race guards, and deduplicated message pagination. A
minimal sessionStorage record contains only workspace ID and opaque Session ID.
Valid selection survives reload and Refresh; missing or foreign selection falls
back to the current workspace's latest Session.

The Dock now renders real safe user/assistant Session projections with source
state, history, Retry, Refresh, Load More, and empty/error behavior. Its input
and button are disabled; mock Session data, simulated replies, and Web `/run`
calls are absent. Linked TUI Runs navigate through their existing safe Session
ID, while null Headless/Gateway associations remain explicitly unlinked.

## Certification and limits

The related Session/TUI/Dashboard/HTTP matrix passed 224 tests and packaging
passed 9. Modified Python files pass Ruff and py_compile; compileall and both
production JavaScript syntax checks pass. Two final full runs each passed
`1996 passed, 2 skipped, 3 warnings`. A broader Ruff audit still reports 85
pre-existing unrelated diagnostics.

The installed wheel commits two turns through the public seam without the exit
finalizer, reloads them in a new process, and serves them through installed
Gateway Sessions APIs and packaged assets outside source cwd/user site.
Dependencies remain empty.

v13 remains active at 23/23 with all v1-v13 pins true because no protected
source changed. The official 108-case evaluator passed with 37 gaps and zero
remote calls; accepted gold SHA/size/mtime ns remained unchanged.

At 1280×900, browser acceptance covered latest/restore/Refresh/fallback,
failure+Retry, a late response race, 50+10 pagination, Run association, eight
main routes, and five Memory routes. Columns measured 208/682/380 pixels without
overlap or horizontal overflow; console warning/error was zero and disclosure
scans were clean. The final verified image is
`artifacts/minicode-dashboard-batch-6a-sessions.jpg`. All temporary browser and
Gateway resources were cleaned.

Dashboard chat, Session write/management APIs, push/polling, live token streams,
Run/MCP controls, multi-process Session coordination, Batch 6B, and Batch 7 are
not implemented.

---

# MiniCode Dashboard Batch 6A.2 Cross-Process Session Writes

## Outcome and deep seam

Batch 6A.2 upgrades Session storage from process-local writer safety to
cooperative multi-process writer coordination for one local POSIX
`MINI_CODE_DIR`. `minicode.session_store.session_store_transaction(data_dir)`
is the small external interface over dynamic lock-path selection, secure regular
file opening, bounded monotonic `fcntl.flock`, and unlock/close release.

The fixed per-root target is `<current MINI_CODE_DIR>/session-store.lock`. It is
created/restricted as `0600`, opened with CLOEXEC/NOFOLLOW when supported,
verified by descriptor/path regular-file identity, never written with payload,
and never deleted. Symlink, directory, FIFO, permission, replacement, and unsafe
open failures produce only `SessionStoreLockError`; bounded contention produces
`SessionStoreBusyError`. KeyboardInterrupt/SystemExit retain identity and holder
process death relies on operating-system advisory-lock release.

Every writer now has one invariant order: process-local RLock, cross-process
transaction, acquired-lock authoritative state check, then the entire base/
delta/cleanup/index/delete transaction. `save_session()`, `delete_session()`,
and `cleanup_old_sessions()` acquire once and call already-locked helpers. The
lock covers index load-mutate-replace rather than only `os.replace()`.

## Same-Session conflict and reader boundary

An internal Session revision combines base presence, persistence generation,
and legal delta next-sequence. After flock acquisition, disk base generation and
delta tail are reread and must match. A mismatch raises the fixed
`SessionWriteConflictError` before metadata or file mutation. The stale writer is
not merged and cannot reuse a delta filename, roll state back, or force-full save
an old snapshot. A later writer must reload the current Session first. Separate
base presence preserves legacy generation-zero behavior.

Autosave retains its established contract: coordination/conflict failures return
False, remain dirty, and can retry without replacing Agent behavior. Readers stay
lock-free and see complete old/new individual JSON files through atomic replace,
but base/delta/index together are not claimed as a multi-file atomic snapshot.

The design supports cooperating macOS/Linux processes on one local filesystem.
Windows, NFS/network filesystems, multiple machines, non-cooperating writers,
distributed locks, leases, heartbeat, PID ownership, and stale lock-file deletion
are unsupported.

## TDD and certification

The two original spawned-process REDs were deterministic: two different Session
bases survived while only one shared-index ID remained, and a stale same-Session
writer returned success while overwriting `delta_0000`. The result was `2 failed
in 0.23s`. The final 16-test cross-process matrix covers those cases plus
save/delete, sequential latest reload, timeout byte equality, Autosave retry,
abrupt holder exit, secure targets/open failure, empty 0600 persistent lock,
cleanup/visibility, dynamic roots, control-flow identity, and injected monotonic
timeout.

The complete Session/TUI/Dashboard/HTTP focus passed 199 tests; packaging passed
9; modified-file Ruff/py_compile, repository compileall, and both node syntax
checks passed. The installed wheel contains the new module and passed synchronized
two-process Session writes plus Gateway/API/static compatibility outside source
cwd. The final evaluator-after suite passed `2052 passed, 2 skipped, 3 warnings`.

Production baseline remains v13 with candidate equality, 23/23 protected files,
and all v1-v13 integrity pins true. The 108-case semantic evaluator passed with
37 gaps and zero remote calls; accepted gold SHA/size/mtime ns remained
`5629d6...fdd3b` / `3033592` / `1784135857000000000`. Dependencies remain `[]`.
Formal frontend assets are byte-identical, so no browser visual rerun is claimed.
All bounded test processes, installs, listeners, and temporary files were cleaned.

The detailed interface and limit record is
`docs/minicode-dashboard-batch-6a-2.md`. Batch 6B and Dashboard Chat remain
unimplemented.

---

# MiniCode Dashboard Batch 6B-1 Synchronous Chat

## Outcome and architecture

Batch 6B-1 implements the formal right-side Chat Dock as one synchronous,
Session-backed Agent turn. `minicode.agent_runtime` extracts the minimal stable
Agent composition shared by Headless and Chat. The Web-independent
`ConversationTurnService` owns Session/workspace truth, one linked Gateway Run,
one Agent execution, post-Agent save, fixed domain failures, and cleanup.
`minicode.web.chat_http` owns closed, bounded HTTP parsing. Gateway only composes
the read model, one MCP current-state registry, Chat service, and handler.

The strict route is `POST /api/v1/chat/turns`; it accepts only `message` and an
optional `sessionId`, rejects duplicate/unknown keys and invalid framing before
runtime construction, and returns safe versioned JSON. New and continued turns
both use real Sessions. A success is impossible until Session save succeeds.
Exactly one `source=gateway` Run is linked to that real Session; journal failure
may return `runId=null`. The Agent never runs under the Session flock. A stale
save returns 409 without merge, retry, overwrite, or rerun; busy and runtime
failures are fixed 503s. Agent/no-assistant failures return fixed 500, create no
fake assistant, and best-effort commit only the truthful user message.

The Dock uses a separate in-memory request-generation store with explicit new
or selected-Session mode and idle/submitting/success/error/conflict phases.
Submitting disables duplicates. Success explicitly refreshes Sessions/detail,
Runs, Snapshot, and Ops. Conflict refreshes Session state without resend;
not-found reconciles selection; 500/503 retain the draft. Messages, drafts, and
errors are not placed in browser storage. `/run` remains byte-contract compatible
and retains null Session association.

## Certification

The service suite passed 11 tests and strict Chat HTTP/restart passed 24,
including spawned-process stale conflict, actual lock busy, workspace isolation,
truthful failures, journal degradation, and commit failure. The broad focused
matrix passed after one legacy Gateway fixture gained the now-required workspace
attribute. Installed-wheel coverage passed 9 tests and exercised packaged static
assets plus a real Chat Session and linked Run outside source cwd.

Modified Python Ruff/py_compile, repository compileall, and both JavaScript
syntax checks passed. Production baseline v14 protects 26 files with exact
three-file changed and three-file added lineage, manifest SHA
`c00bff9983800f3d1ae579aaa5ed20de2671b3e3162aa8942db709b91d5093ce`,
candidate equality, and all v1-v14 pins true; v13 remains unchanged. The offline
semantic evaluator passed 108 cases, 37 confirmed gaps, and zero remote calls.
Accepted gold stayed `5629d6...fdd3b`, 3,033,592 bytes, mtime ns
`1784135857000000000`.

The 1280×900 isolated browser run verified first/new and continued turns,
submitting disablement, Agent failure, manual recovery, deterministic real 409,
Gateway restart recovery, history switching, a second Session, all eight main
routes, five Memory routes, Run/Session linkage, escaped XSS text, and absence of
path/secret/object leakage. Measured columns were 208/682/380 px with no overlap
or horizontal overflow; console warnings/errors were zero. All temporary browser,
viewport, Gateway, HOME, workspace, Session, and Run resources were cleaned.

The evaluator-after final suite passed `2095 passed, 2 skipped, 3 warnings in
98.01s`; the warnings are the existing unregistered benchmark markers. The two
intermediate legacy assertion failures were resolved without changing the frozen
Phase 2A file or v13 evidence. Dependencies remain `[]`. Streaming, SSE/WebSocket,
polling, token progress, cancellation, background/idempotent jobs,
authentication, write controls, and Batch 6B-2 remain deliberately unimplemented.
The detailed contract is `docs/minicode-dashboard-batch-6b-1.md`.

---

# MiniCode Dashboard Batch 6B-2A Durable Turn Identity

## Outcome and deep boundaries

Dashboard Chat now generates a cryptographically strong
`turn_<32 lowercase hex>` before submission. The Web-independent Conversation
service hashes workspace, target Session/new marker, and the complete normalized
message, then atomically claims a workspace-scoped Turn before it constructs any
runtime, Run, or Agent execution.

`ConversationTurnStore` is the durable request-status authority; Session remains
the sole conversation-content authority; RunJournal remains best-effort
telemetry. Turn files contain only a hash, closed status, safe references,
timestamps, fixed error code, commit indexes, and an internal owner token. They
never contain message/Assistant/system/tool/Memory/Skill/MCP content, raw errors,
credentials, provider identity, or absolute paths.

The store uses strict 16 KiB schema validation, bool/int separation, no-follow
regular-file checks, symlink-safe fixed directories, `0600` same-directory temp
files, fsync, atomic replace, and bounded best-effort retention. Its claim lock
coordinates concurrent HTTP requests within one Gateway process; it does not
claim distributed or multi-machine coordination.

## Duplicate and crash recovery

Matching live duplicates return `turn_in_progress`; fingerprint mismatches return
`turn_id_conflict`; completed duplicates reconstruct the result from Session;
failed/interrupted duplicates return the original terminal failure. None executes
another Agent or creates another Run.

Session persistence now includes a private marker with exact turn/user/assistant
indexes, validated against message roles and saved with the finished messages.
It is absent from public Session APIs and DOM, and legacy Sessions default to no
markers. If Session commit succeeded but Turn completion did not, a new Gateway
promotes the Turn to completed using that marker. If Agent executed but no Session
marker exists, the abandoned Turn becomes interrupted and is never replayed.

## HTTP and browser behavior

The existing strict synchronous POST accepts an optional compatible `turnId` and
returns it on success. `GET /api/v1/chat/turns/{turnId}` exposes a versioned,
no-store, read-only allowlist with status and safe Session/Run references, but no
content, fingerprint, owner, marker, or path. Missing/foreign records are the same
fixed 404.

The independent frontend Chat store generates IDs with Web Crypto and persists
only workspace/active-turn/target-Session metadata before fetch. A refresh makes
one status request. Completed refreshes Sessions/detail/Runs/Snapshot/Ops;
running remains manually checkable; failed/interrupted/missing clear the active
identity without resend. No timer, polling, SSE, WebSocket, streaming, background
job, or cancel API was added. The Dock accurately says synchronous, recoverable,
and no live updates.

## v15 and certification

The exact v14→v15 protected lineage changes `minicode/conversation.py` and
`minicode/web/chat_http.py`, newly protects Turn Store, Session, Web HTTP, and the
formal app JS, removes nothing, and totals 30 protected files. Manifest SHA is
`f9e6254c59f8e7b4065c70aba28c20e8d53361e252866a1519264be92704df7a`.
Candidate equality, current 30/30 hashes, and all v1-v15 integrity pins pass.

Turn/identity/Conversation focus passed 34, Chat HTTP/restart 40, compatibility
133, all Dashboard 234, packaging/wheel 9, v15 baseline 63, and semantic contract
32 tests. Scoped Ruff, py_compile, compileall, formal/prototype node checks, local
HTTP smoke, and the official evaluator pass. The evaluator remains 108 cases,
37 gaps, zero remote calls, with unchanged projection/per-case fingerprints and
unchanged accepted gold SHA/size/mtime. The evaluator-after full suite passed
`2144 passed, 2 skipped, 3 warnings in 107.18s`.

At 1280×900, an isolated Gateway/fake Agent verified normal and XSS-bearing
turns, response-loss refresh while running, one reconciliation, manual
running/completed recovery, fixed failed/interrupted behavior, all eight main and
five Memory routes, no overlap/overflow/disclosure, and zero console warnings or
errors. All temporary processes, listeners, browser state, HOME/workspace data,
and the acceptance helper were cleaned.

The precise interface and limit records are
`docs/minicode-dashboard-batch-6b-2a.md` and
`docs/memory-retrieval-production-baseline-v15.md`. Explicit cancellation is
left to Batch 6B-2B. Batch 7 may consume this stable Turn status but must not
replace its identity authority.

---

# MiniCode Dashboard Batch 6B-2B Cooperative Cancellation

## Deep implementation seam

Batch 6B-2B keeps `ConversationTurnStore` as the only durable Turn authority and
adds the closed states `cancel_requested`, `committing`, and `cancelled`.
`request_cancel()` persists an idempotent request and signals the Store-owned
process-local token. `begin_commit()` makes the cancel/complete decision while
holding the same Store lock, so HTTP completion order and thread scheduling do
not define truth.

The new `TurnCancellationToken` is intentionally content-free. Agent/runtime
callers accept it optionally, and the Agent Loop checks it around every point at
which a new Model/Tool operation or recovery attempt could start. Work already
inside a Provider or Tool cannot be killed safely and may finish; Tool side
effects cannot be rolled back. Once MiniCode observes the request, it starts no
new Agent work and the Conversation layer cannot pass the commit gate.

Session remains the content fact. If cancellation wins, the completed message
set and marker are never saved. If committing wins, the exact Session marker
remains authoritative even if the final Turn write is lost. Restart changes an
abandoned cancel-requested record to cancelled unless the marker proves
completed. RunJournal observes cancellation as interrupted with fixed reason
`execution_cancelled`, but cannot decide the Turn outcome.

## HTTP and UI

Gateway composes the strict empty-object Cancel POST implemented in
`minicode.web.chat_http`. Its response exposes only safe Turn/Session/Run
references, state, timestamp, and `cancellationAccepted`. Status GET remains the
durable read source. The frontend has a single active cancel control, durable
active-Turn metadata, separate operation-generation stale guards, honest
cancel-requested/committing/cancelled/result-unavailable text, and explicit
fresh-ID resend only. It adds no polling, background retry, SSE, WebSocket, or
streaming.

## v16 and verification

The exact v15→v16 lineage changes eight protected files, adds three protected
files, removes none, and results in 33 protected files. Manifest SHA is
`80fa4db12cb43f904a0d89cf0d32df7bd389fda1001c55b6447d7d1a5355decb`.
Candidate/current equality and every historical integrity pin pass. Official
semantic behavior and accepted gold remain unchanged.

Focused Store/Agent/Conversation/HTTP/Dashboard/wheel matrices, scoped Ruff,
py_compile, full compileall, JavaScript syntax, semantic evaluation, and isolated
real-Gateway browser acceptance all pass. Browser coverage includes both race
winners, restart, result unavailability, XSS, complete route coverage, layout,
and empty warning/error logs. The evaluator-after final suite passed
`2202 passed, 2 skipped, 3 warnings in 118.19s`; cleanup evidence is captured in
`docs/minicode-dashboard-batch-6b-2b.md`.

Batch 7 may reuse Turn ID, Cancel POST, status GET, Session marker authority, and
the closed state vocabulary. It must not infer truth from response order or add
a competing Turn authority.

---

# MiniCode Dashboard Batch 6B-2B.1 Cancellation Boundary Hardening

## State-machine correction

The Turn Store now exposes typed atomic start and failure decisions. At the
accepted boundary, a persisted cancel request becomes `cancelled` while holding
the Store lock and explicitly refuses execution. On an ordinary exception path,
the same durable cancellation authority prevents a later `failed` write. The
Conversation service maps both outcomes to its existing cancellation domain
error and never infers state from exception text.

This closes the exact `accepted → cancel_requested → mark_running` race. The
original synchronous POST now returns structured `turn_cancelled`, the record is
immediately terminal, and Runtime, Model, Tool, Session content, Assistant
messages, and completed Run side effects remain absent. The existing
`begin_commit` boundary is unchanged: cancel wins before it; committing wins
after it.

## Frontend and certification

The formal Chat Dock adds `cancel_requested` and `committing` to the existing
manual status-check eligibility. No timer, polling, automatic retry/resend,
streaming, or new control API was added. Existing generation guards remain the
stale-response boundary.

Only three production files changed and v17 records that exact delta:
`minicode/conversation.py`, `minicode/conversation_turn_store.py`, and
`minicode/web/static/assets/app.js`. Its manifest SHA is
`2ac1d7185488dd1008407e4711fc3777213dcc1cd405e104f44bf6ca20206857`;
the protected set remains 33 files and every v1-v17 pin passes.

Focused and compatibility matrices, 83 baseline tests, the official 108-case
zero-remote evaluator, two complete 2218-test suites, scoped Ruff, compile
checks, production JavaScript syntax, and 9 installed-wheel/Gateway tests passed.
Accepted semantic gold SHA/size/mtime remained unchanged.

At 1280×900 an isolated real Gateway verified normal completion, both new manual
recovery states, response loss, real process restart reconciliation, no
horizontal overflow, an empty page warning/error console, and no secret/path/
object leakage. Temporary browser, Gateway, HOME/workspace, wheel, and evaluator
resources were cleaned. Detailed evidence is in
`docs/minicode-dashboard-batch-6b-2b1.md` and
`docs/memory-retrieval-production-baseline-v17.md`.

No Batch 7 feature or new runtime dependency was introduced.
# Batch 8C-1 — Persistent Memory Approval Authority + HTTP Contract

- Added typed `MemoryApprovalPolicy` and persisted it without migrating
  historical approval states. Explicit user saves retain current behavior;
  automatic safe/suspicious writes persist as pending and unsafe writes as
  rejected.
- Added `MemoryStoreCoordinator`, the shared RLock→flock transaction used by
  all audited durable Memory writers. It reloads changed authority before
  mutation and prevents cross-process silent overwrite.
- Added `MemoryApprovalAuthority.snapshot()/revision()/decide()` with bounded,
  deny-only-on-redaction/truncation review projection and content/state-bound
  `memoryreviewrev_*` stale fencing.
- Added strict loopback GET pending and POST decision routes. They use fixed
  safe errors, strict JSON transport, same-origin fencing, no CORS and no
  workspace/path input.
- Reused existing Memory approval audit and `resources.memory` Change Feed;
  added no database, RunJournal event, SSE resource, EventSource or polling.
- The exact v25→v26 protected lineage is four changed plus five newly protected
  files, 50 files total. v26 manifest SHA is
  `b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936`.
- The formal Dashboard HTML, app.js, CSS and cost formatter are unchanged.
- Full certification evidence is recorded in
  `docs/minicode-dashboard-batch-8c-1.md` and
  `docs/memory-retrieval-production-baseline-v26.md`.
- Final suites: `2500 passed, 2 skipped, 3 existing warnings` twice. The
  official evaluator between them passed 108 cases / 37 gaps / Phase 3B true /
  zero remote calls, and the accepted semantic gold stayed byte/stat identical.

---

# Batch 8C-1.1 — Memory Approval Read-Only Snapshot Hardening

- Replaced the approval authority's read-time `MemoryManager` construction with
  a private bounded parser that has no transaction, migration, recovery,
  cleanup, audit, or save capability. `snapshot()`, `revision()`, and the real
  pending GET now perform zero writes for empty, current, legacy, fallback, and
  fail-closed corrupt states.
- Hardened reads against symlinked MiniCode/scope/file paths, directory
  replacement, oversized sources, special files, and blocking FIFOs using
  no-follow directory-relative descriptors, identity validation, nonblocking
  open, regular-file checks, and fixed caps.
- Preserved the decision path exactly as the coordinated RLock→flock→reload→
  validate→typed decide→audit→atomic-save authority. GET revisions remain POST
  compatible; stale, retry, terminal conflict, scope isolation, and HTTP
  vocabulary are unchanged.
- Added exhaustive real-loopback and multiprocess tests, installed-wheel
  no-write/legacy/decision compatibility, v27 tamper/lineage coverage, and
  exact frontend/gold immutability checks.
- v27 changes only `minicode/memory_approval.py`, adds/removes nothing, protects
  50 files, and has manifest SHA
  `18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013`.
  All v1-v27 pins, candidate hashes, and current hashes pass.
- Final counted suites passed `2525 passed, 2 skipped, 3 existing warnings`
  twice. The official evaluator passed 108 cases / 37 gaps / Phase 3B true /
  zero remote calls between them; accepted semantic gold and formal frontend
  bytes remained unchanged. Runtime dependencies remain empty and task-owned
  wheel/install resources were removed.
- Batch 8C-2 and the independent Permission/File Review reject-only Tool
  approval defect were not entered.

Detailed contracts are in `docs/minicode-dashboard-batch-8c-1-1.md` and
`docs/memory-retrieval-production-baseline-v27.md`.

---

# Batch 8A-2.2.1 — Invisible Control Character Diff Fidelity Hardening

- The v28 public-seam RED produced 18 deterministic failures: 17 dangerous
  values were incorrectly reviewable and a lone surrogate failed before a
  pending review could serialize. A later range RED proved U+2065 needs explicit
  U+2060–U+206F enforcement because its current Unicode category is `Cn`.
- `minicode/file_review.py` now classifies both real file bodies before line
  splitting and emits one fixed safe marker. `minicode/permission_approval.py`
  reuses that classifier before raw Diff parsing and during projection. Raw
  invisible controls cannot reach TUI, broker serialization, HTTP, DOM, events,
  logs, exceptions, Tool output, or RunJournal.
- Safe tab/LF/CRLF/Unicode/emoji remains allowable. All required C0/C1,
  splitline, format/bidi/zero-width/BOM and surrogate cases are deny-only;
  dangerous Allow is 409 `permission_not_reviewable`, while Deny/Cancel/timeout/
  restart leave files unchanged.
- v29 is active at SHA
  `e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1`.
  Its exact v28→v29 delta changes only `minicode/file_review.py` and
  `minicode/permission_approval.py`; v1–v28 remain immutable and all 50 current
  files match.
- Final complete suites passed `2773 passed, 2 skipped, 3 warnings` twice. The
  official evaluator passed 108 cases / 37 gaps / Phase 3B true / zero remote
  calls. Accepted semantic gold and all four formal frontend files remain
  byte/stat identical; runtime dependencies remain empty.
- The installed-wheel real Gateway/Tool smoke passed. Isolated 1280×900
  in-app-browser acceptance covered safe Allow, seven dangerous display cases,
  Cancel, authority restart/old-ID fencing, eight main plus five Memory routes,
  layout, clean console, and DOM leak scans. Batch 8C-2 was not entered.

Detailed evidence is in `docs/minicode-dashboard-batch-8a-2-2-1.md` and
`docs/memory-retrieval-production-baseline-v29.md`.

---

# Batch 8C-2 — Memory Approval Store + UI

- Added a standalone volatile `memoryApprovalStore` and strict exact-key,
  byte-bounded pending/decision validators in the existing formal Dashboard
  application. Approval state is not merged with Memory, Permission, Chat, or
  runtime stores and is never placed in browser storage.
- Added the sixth `#memory/approvals` subroute and compact Waku master/detail
  workspace. Complete safe/suspicious reviews may be approved or rejected;
  redacted, truncated, unsafe, incomplete, oversized, or inconsistent reviews
  are fail-closed and expose only Reject.
- Approve/Reject performs one POST bound to `(memoryId, reviewRevision)`, uses
  independent action generations, validates the full response, and reconciles
  through authority GET. Fixed stale/conflict/not-found/already-decided errors
  reconcile without resending; busy/network states retain verified review state
  and never automatically retry a decision.
- Reused the existing single EventSource and `resources.memory` invalidation
  dispatcher for approval refresh. No new SSE resource, EventSource, polling,
  WebSocket, database, daemon, MCP control, or remote administration path was
  introduced.
- The exact production delta is only
  `minicode/web/static/assets/app.js` and
  `minicode/web/static/assets/styles.css`; no backend production source changed.
  v30 is active at SHA
  `55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd`,
  protects 50/50 files, records no add/remove, and preserves every v1–v29 pin.
- Final full suites passed `2788 passed, 2 skipped, 3 existing warnings` twice.
  The official evaluator passed 108 cases / 37 confirmed gaps / Phase 3B true /
  zero remote calls; accepted semantic gold stayed byte/stat identical. Wheel,
  static, focused, real HTTP, and installed-package gates passed.
- Isolated browser acceptance at 1280×900 and 700×900 covered all eight main
  routes and all six Memory routes, approve/reject/deny-only behavior, external
  SSE reconciliation, disconnect preservation, restart recovery, keyboard
  focus, clean application console, no horizontal overflow, and no private
  path/secret/object leakage. All task-owned browser, Gateway, HOME, workspace,
  wheel, and install resources were removed.
- Batch 8C is complete. Batch 8B/9, Memory edit/delete, Allow always, Tool
  Permission merge, and any broader control plane remain explicitly out of
  scope.

Detailed evidence is in `docs/minicode-dashboard-batch-8c-2.md` and
`docs/memory-retrieval-production-baseline-v30.md`.

---

# Batch 8D-1 — Conversation and Project Memory Deletion Authorities

- Added backend-only `ConversationDeletionAuthority` and
  `ProjectMemoryDeletionAuthority`, plus strict GET/POST adapters for the four
  `/deletion` routes. HTTP owns parsing and safe errors only; authorities own
  scope, revision, locking, storage mutation and verification.
- Added content-free Workspace deletion fences and finite ten-minute receipts.
  Conversation writers that can expand the target set honor the same fence;
  deterministic retry finishes partial work without restoring deleted data.
- Conversation removal deletes linked terminal Turns and Runs before Session
  delta/base/index. Active or unsafe records block before mutation. Project
  Memory removal deletes the exact entry, all approval audit records and all
  Project backlinks under the existing coordinated writer; User/Local and
  adjacent records remain unchanged.
- The existing Change Feed now observes `approval_audit.json` under its existing
  `memory` resource. No new EventSource, polling, frontend state or browser UI
  was added.
- v31 is active with exact 7-file changed / 4-file added / 0-file removed
  lineage and SHA
  `d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`.
  v1-v31 integrity and 54/54 current source equality pass.
- First complete regression exposed three compatibility-only failures; minimal
  fixes preserved the existing Gateway test double, Session low-information
  lock error and semantic evaluator's new active version. Their focused rerun
  passed 160/160.
- Official semantic evaluation passes 108 cases, 37 confirmed gaps, Phase 3B
  true and zero remote calls. Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3033592 and mtime_ns 1784135857000000000.

Detailed contracts are in `docs/minicode-dashboard-batch-8d-1.md`.

## Batch 8D-1 final certification

- A final deterministic concurrency RED proved that a second Project Memory
  deleter could wait behind the coordinated writer and then miss the completed
  operation. `ProjectMemoryDeletionAuthority` now checks the finite receipt
  inside that writer boundary and returns `already_absent`. Thread Event and
  spawned-process Barrier tests prove the handoff.
- Expanded deletion/HTTP certification covers cross-process Session fencing,
  process-exit partial recovery, lost POST response reconciliation, Turn/Run
  state transitions, approval/backlink staleness, all supported lifecycle
  states, corrupt metadata, symlink rejection and every fixed HTTP error map.
- Final focused suite: 555 passed in 52.67s.
- Final full suite 1: 2845 passed, 2 skipped, 3 existing benchmark mark warnings
  in 187.31s.
- Final full suite 2: 2845 passed, 2 skipped, 3 existing benchmark mark warnings
  in 187.91s.
- Ruff, py_compile, compileall and every formal JavaScript `node --check`
  passed. pyright and mypy were unavailable.
- Final v31 SHA is
  `d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`;
  active/current/candidate matches 54/54 and v1-v31 integrity is true. v30
  remains `55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd`.
- Final wheel SHA is
  `d52d98d3c6eb124eb24661bf85b7bb3c91271970e4cbd9f9e33d2af4c71b6726`;
  isolated installed Gateway smoke passed the four deletion routes, legacy read
  convergence, both health routes and `/run` compatibility.
- Official evaluator passed 108 cases, 37 confirmed gaps, Phase 3B true and
  zero remote calls. Accepted gold remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3033592 and mtime_ns 1784135857000000000 before and after.

---

# Batch 8D-2 — Dashboard Deletion UI + Reconciliation

- Added strict exact-key, byte/count/enum-bounded validators for all v31
  Conversation and Project Memory preview/result schemas. Kind, target and
  revision are operation-bound; invalid payloads are discarded and raw server
  messages are never rendered.
- Added independent volatile Conversation and Project Memory deletion stores
  and one accessible Waku confirmation surface. Every open performs GET,
  destructive confirmation sends one POST, and ready/busy/partial/stale/
  unconfirmed/completed states never auto-submit.
- Added short in-memory tombstones and independent request/action generation
  fences across Sessions, Session detail, Runs, Run detail, Dock, Memory and
  Memory Approval. Completion is not claimed from local removal or a 404;
  existing REST collections must confirm absence.
- Conversation deletion preserves the unsent draft and never sends it, clears
  only the matching stored selection and switches a matching Dock continuation
  to new mode. Project deletion preserves Memory filters and invalidates stale
  Approval selection/actions without making an Approval decision.
- Reused the single EventSource and existing sessions/memory resources for
  GET-only preview/collection reconciliation. No backend authority/schema,
  new transport, timer, dependency or Batch 9 behavior was added.
- The formal production delta is exactly `app.js` and `styles.css`.
  `index.html` and `cost-format.js` remain byte-identical. v32 is active at SHA
  `9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1`;
  v31 remains byte-identical, all 54 current sources match and v1-v32 integrity
  is true.
- Final wheel SHA is
  `b7e5ccd3304d552fc9c2d9d38d93bd92090877b84baf57fde8c737371b0ae838`.
  Its isolated Gateway served the exact final assets and completed both real
  deletion flows with authority collection convergence.
- Isolated Browser acceptance at 1280x900 and 700x900 covered all routes,
  real delete, busy/partial, draft/Dock, Memory/Approval, responsive layout,
  focus entry/Esc/return, visual/DOM footer order, console and DOM safety.
  Deterministic formal tests supplement network-drop and Tab-boundary behavior
  that the in-app Browser cannot reliably synthesize.
- Broad focused tests passed 574; the final focused rerun passed 267. Ruff,
  py_compile, compileall and production JavaScript syntax passed. The official
  evaluator passed 108 cases / 37 gaps / Phase 3B true / remote 0, and accepted
  gold plus `dependencies=[]` stayed unchanged.
- Final complete suites on the frozen production state passed `2855 passed, 2
  skipped, 3 existing warnings` in 188.35s and 188.25s.

Detailed evidence is in `docs/minicode-dashboard-batch-8d-2.md` and
`docs/memory-retrieval-production-baseline-v32.md`. Batch 8D is complete;
Batch 9 was not entered.

---

# Batch 9A-1 — Persistence Inventory + Read-only Data Health

- Added the standalone `PersistenceHealthReader.snapshot()` authority with 25
  fixed Store projections, exact schema-v1 validation, a 25,000-entry global
  traversal budget, 2 MiB parsed-file cap, 256 KiB response cap, safe integers,
  no-follow file opens, per-Store failure isolation and fixed diagnostics.
- The reader does not construct Session/Turn/Run/Memory/Permission/MCP/deletion
  managers, acquire their locks, or invoke cleanup, retention, migration,
  recovery, repair or rebuild. Empty roots remain absent. Source/profile/Skill/
  Tool-result content is stat-only.
- Added strict query-free `GET /api/v1/data-health` with `no-store`, safe fixed
  error envelopes and one Gateway-composed reader. Unknown API behavior and all
  existing read/write routes are unchanged.
- Added the System/Data Health view with loading, empty, live, partial, error,
  stale-retention and retry states. It reuses the single existing EventSource
  only as a GET invalidation trigger and exposes no maintenance action.
- RED evidence covered the missing core/HTTP/frontend authorities and a later
  Workspace-isolation gap where foreign Session bases were being read. A real
  browser RED also found global `.live` CSS colliding with Store status classes;
  the final UI uses `status-*` classes.
- The inventory and future reset boundary are documented in
  `docs/minicode-dashboard-batch-9a-1.md`. Batch 9A-2 remains planning-only and
  Batch 9A-3 remains the sole future recovery owner.
- Final production baseline v33 has parent v32, 56 protected files and exact
  delta: changed `gateway.py`, `web/http.py`, `app.js`, `styles.css`; added
  `storage_health.py`, `web/storage_health_http.py`; removed none. Manifest SHA
  is `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`.
  Candidate/current match and v1-v33 integrity are all true.
- Focused evidence includes 24 core/frontend tests, 75 Dashboard/HTTP tests, 411
  Session/Turn/Run/Memory/Approval/Deletion/SSE tests, 165 baseline tests, 84
  final wheel/Dashboard/HTTP tests and 190 final baseline/core tests. Scoped
  Ruff, py_compile, compileall and all formal JavaScript syntax checks passed.
- Browser acceptance used an isolated HOME/Workspace/Gateway at 1280x900 and
  700x900. It covered empty/non-empty/partial/error/stale/restart recovery,
  eight main routes, five Memory subroutes, Chat Dock collapse/reopen, Session
  and Project Memory delete-entry presence without executing deletion, zero
  horizontal overflow, zero page console warning/error and no path/secret/
  `[object Object]` leakage. All temporary resources were cleaned.
- The official semantic evaluator passed 108 cases with 37 confirmed gaps,
  Phase 3B true and zero remote calls. Accepted gold remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3033592, mtime_ns 1784135857000000000 and inode 18469292.
- One evaluator-before full suite passed cleanly at 2891 passed, 2 skipped and 3
  existing warnings. Final frozen-state full execution reached 2889 passed with
  only the two existing Phase 2B wall-clock assertions failing. Read-only process
  evidence showed Tencent Meeting at about 107% CPU and WPS at about 31% CPU;
  isolated Phase 2B reruns failed only
  `canonical_p95_not_materially_above_phase2a`. No threshold, evaluator or
  production behavior was changed to conceal this external scheduling blocker.

## Batch 9A-1.2 — Phase 2B deterministic gate repair and closure

- The repaired seam is evaluator-only. `evaluate_performance_policy()` accepts
  already-measured values and returns classified deterministic/wall-clock gates,
  advisory/strict mode, strict result and enforcement result without reading
  clocks, files, environment variables or network state.
- Default evaluator/pytest acceptance still requires every correctness,
  integrity, deterministic-core, no-network and candidate-cap invariant. Real
  wall-clock samples remain in `performance.gates`, `performance.observations`
  and generated Markdown, but only the explicit
  `--enforce-wall-clock-performance` CLI flag makes them affect exit status.
  The canonical limit remains exactly `2.866455 ms`.
- The Phase 2B JSON schema received a compatible v1 extension and evaluator
  version advanced to 1.1.0. `deterministic_phase2b_view()` continues to exclude
  the complete performance object, holdout latency/peak memory and Phase 2A
  latency, so enforcement mode and machine observations cannot alter the
  deterministic core or the 108-case semantic projection.
- Public evaluator/CLI tests cover advisory/strict threshold sides, inclusive
  limits, missing strict results, invalid values, no-network/candidate caps,
  unknown CLI arguments, schema/report honesty and two byte-identical default
  deterministic-core outputs.
- Formal certification: default Phase 2B `26 passed` three consecutive times;
  the sole strict benchmark passed at canonical P95 `2.770667 ms` against the
  unchanged `2.866455 ms` limit; full pytest passed twice at
  `2907 passed, 2 skipped, 3 warnings`; v33 verifier and the 108-case official
  evaluator are green; all scoped static checks pass.
- Production baseline v33, its 56 protected files, dependencies `[]`, accepted
  gold SHA/size/mtime, complete semantic projection and per-case fingerprint are
  unchanged. Phase 2B frozen asset hashes were advanced only for the versioned
  evaluator/CLI/schema/tests/generated reports. No production source, frontend,
  fixture, threshold, v1-v33 manifest or accepted truth changed; no v34 exists.
  Batch 9A-1 is formally closed and Batch 9A-2 is next.

## Batch 9A-1.2.1 — residual default wall-clock assertion removal

- A read-only default-test audit found one residual direct assertion on the real
  consolidator-100 P95. This was the RED. No other scoped Phase 2B,
  semantic-gap or baseline default test makes a real benchmark sample decide
  pytest success.
- The only test change replaces that assertion with report-honesty checks:
  finite/non-negative/non-bool observation, observation/formal-field equality,
  exact threshold-to-gate classification, `strictPassed` aggregation and
  advisory-versus-strict exit behavior. Synthetic values cover all three
  wall-clock failure combinations, inclusive equality, a network count of one
  and each candidate cap at 257.
- The target test hash changed from
  `fc36869382c4f8a41b33188374543b68eedae4d14ed5fd50cfb31c97a158706d`
  to
  `828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3`;
  the sole synchronized pin reason is
  `remove_remaining_default_wall_clock_assertion`. The other 11 Phase 2B pins
  remain byte-identical.
- Default Phase 2B passed 28 tests three consecutive times. The only strict run,
  written entirely to `/tmp`, exited 0 with canonical P95 `2.794834 ms`,
  consolidator P95 `2.680833000340499 ms`, reference `2.1233 ms`, unchanged
  material limit `2.866455 ms`, `strictPassed=true`, and remote calls 0.
- Full pytest passed twice at `2909 passed, 2 skipped, 3 warnings`. v33 verification
  remains parent v32, 56/56 and all v1-v33 integrity true. The official evaluator
  remains 108 cases, 37 confirmed gaps, Phase 3B true, remote calls 0,
  `evaluation_passed=true` and `phase2b_assets_unchanged=true`; all static checks
  pass.
- Gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`. Strict/frozen deterministic
  cores are byte-identical at
  `f47002d15be904b9f73953a0e7a537c1fd14c327810129bafb8fcb6c51873559`;
  semantic projection/per-case fingerprints remain
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`
  and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- No production/frontend byte, evaluator logic, algorithm, threshold, fixture,
  manifest, accepted gold or formal Phase 2B artifact changed. Existing
  wheel/browser evidence is reused, no v34 exists, Batch 9A-1 remains closed,
  and Batch 9A-2 is next.

## Reliability 1B-1B — Web Fetch Safe Transport Boundary

- Replaced the built-in `web_fetch` prefix-only URL guard, implicit urllib
  redirect opener and unbounded body read with typed request normalization and
  `execute_safe_get()`.
- Added `SafeHttpResponse` and `execute_safe_http()` as the shared deep
  transport seam in `http_utils.py`. Both `http_request` and `web_fetch` now
  reuse destination validation, the one 4/8/12 bounded resolver, validated-IP
  HTTP/HTTPS transport, explicit max-3 redirects, one monotonic deadline and
  the 1 MiB / 64 KiB response reader.
- `web_fetch` remains core-profile and read-only, with no new Permission
  approval. It accepts only HTML/text/JSON-like media, fails closed on
  non-identity encoding, renders after the wire budget and emits status,
  media type, wire bytes, rendered characters and truncation state.
- Added 78 deterministic `web_fetch` safety tests covering closed input,
  IPv4/IPv6/mapped/mixed DNS, pinning, rebinding, redirects, zero target send,
  declared/streamed/chunked response budgets, deadline, content types,
  charset, HTML filtering and error redaction.
- Updated Functional Audit facts: `SEC-003` closed; `SEC-004` is archive-only;
  `tool.web_fetch` is all-pass. Final matrix is 185 capabilities, 123 pass,
  8 fail and 9 issues. The audit command still returns 1 by design because
  unrelated open issues remain.
- Packaging tests include all safe transport modules and a non-source-cwd
  installed smoke for web_fetch JSON/HTML/text, private/mixed/redirect-private,
  dns_error/timeout/resolver_busy, oversized response, existing http_request,
  Gateway routes and Dashboard assets. Packaging is 9 passed.
- Active baseline is v38, parent v37, manifest SHA
  `49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`,
  60 protected files and exact delta: changed `http_utils.py`, added to
  protection `web_fetch.py`, removed none. v1-v38 integrity and current/
  candidate match are true.
- Official semantic evaluation remains 108 cases, 37 confirmed gaps,
  Phase 3B true, remote calls 0 and passed. Accepted gold SHA/size/mtime_ns
  remains frozen.
- Final gates: web/HTTP/resolver 161, broad compatibility 391, Functional
  Audit contract 4, baseline 196, semantic tests 32, packaging 9; Scoped Ruff,
  py_compile, compileall and JavaScript syntax passed. Two complete suites
  passed `3147 passed, 2 skipped, 3 warnings` in 210.38s and 210.29s.
- Optional live external-network smoke was not run. `web_search`, archive
  behavior, Agent Loop, Memory, Session, RunJournal, MCP, Dashboard frontend,
  runtime dependencies and Reliability 1B-2 were not modified or entered.
