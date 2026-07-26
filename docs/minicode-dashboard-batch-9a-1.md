# MiniCode Dashboard Batch 9A-1

## Scope and conclusion

Batch 9A-1 adds a bounded, read-only persistence inventory. The sole health
authority is `minicode.storage_health.PersistenceHealthReader`; the HTTP adapter
and Dashboard only validate and project its schema-v1 result.

This batch does not delete, clean, retain, repair, rebuild, migrate, compact or
reset anything. It does not construct Session, Turn, RunJournal, Memory,
Permission, MCP or deletion managers. In particular, `destructiveActionsAvailable`
is always `false`.

The inventory uses two fixed roots selected at Gateway startup:

- `MINI_CODE_DIR`, normally the user's MiniCode data directory.
- The resolved startup Workspace.

Neither the API nor the reader accepts a path, Workspace override or arbitrary
root from a request.

## Authoritative inventory

“Layout” below is a safe logical description, not a path returned by the API.
`data/` means `MINI_CODE_DIR`; `workspace/` means the fixed startup Workspace.
“9A-2” is planning only.

| Data source | Authority | Scope | Durability | Safe storage layout | Sensitive content | Writer / locking | Current retention | Current corruption behavior | 9A-2 reset eligibility | 9A-3 recovery responsibility | Explicit exclusion |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Session index | `minicode.session`, `minicode.session_store` | Workspace view over user store | persistent | `data/sessions_index.json` | IDs, titles, Workspace association | cross-process Session store transaction; atomic replace | `cleanup_old_sessions()` exists but is never called by health | Session reader validates entries; health reports drift/partial | planned with Session base/deltas as one authority | isolate malformed index, reconcile only under explicit recovery | never reset foreign Workspace Sessions |
| Session base | `minicode.session` | Workspace | persistent | `data/sessions/<session-id>.json` | prompts, assistant messages, Tool/Skill/MCP state | Session transaction; atomic temp replace | explicit old-session cleanup | invalid base is rejected; health parses identity only and returns no body | planned | bounded quarantine/reconciliation, not automatic load-time mutation | user/global Sessions for other Workspaces |
| Session generation deltas | `minicode.session` | Workspace | persistent | `data/sessions/deltas/<session-id>/delta_<sequence>.json` | incremental message/state content | same Session transaction; generation fencing | full-save delta cleanup | invalid/stale delta is skipped by Session load; health reports partial | planned with parent Session | reconcile generation and orphan deltas | foreign Session delta trees |
| Conversation Turns | `minicode.conversation_turn_store` | Workspace | persistent | `data/dashboard/workspaces/<workspace-id>/turns/*.json` | user message, response, errors and lifecycle | per-record atomic temp replace; store creation/retention on writer paths | bounded terminal/temp cleanup when claiming work | fail-closed record validation; health isolates file failures | planned | bounded corrupt-record isolation | active/nonterminal Turns until an explicit safe plan |
| RunJournal index | `minicode.run_journal` | Workspace | persistent | current Workspace Run root `index.json` | Run IDs and update metadata | temporary directory lock plus atomic index replace | refreshed best effort; Run retention is separate | reader can reconstruct bounded list; health reports index drift | planned with Runs | explicit bounded index rebuild | no raw index deletion while Runs remain |
| Run metadata | `minicode.run_journal` | Workspace | persistent | one `metadata.json` per retained Run | prompt/title summary, lifecycle, usage metadata | exclusive writer token and atomic metadata replace | `enforce_retention()` exists but is never called by health | strict identity/schema validation; health degrades only Run store | planned | isolate malformed Run directories | active writer-owned Runs |
| Run events | `minicode.run_journal` | Workspace | persistent | one append-only `events.ndjson` per retained Run | model/tool/failure/memory/skill observations | active Run writer token; append and fsync | bounded Run retention | malformed/partial events are bounded; health never returns payloads | planned with Run metadata/index | event-tail isolation; never synthesize events | unrelated Workspace Runs |
| Deletion fences and receipts | `minicode.deletion_store` | Workspace | persistent | current Workspace `deletions/` ledger | content-free hashed identity, revision, timestamps | process lock + file lock; exclusive fence; atomic receipt | expired/over-limit receipt cleanup on completed deletion | strict small JSON records; health reports invalid/temp/fence state | planned, but active fence is a blocker | abandoned/partial-operation recovery | no new bulk-delete authority |
| User Memory | `minicode.memory`, `minicode.memory_store` | User | persistent | `data/memory/memory.json` and `MEMORY.md` | durable user facts/preferences | coordinated Memory RLock + cross-process lock; atomic scope writes | tier lifecycle exists; no health-triggered maintenance | normal manager can migrate/recover/back up; health bypasses it and only reports partial | excluded | recovery must preserve global ownership and require explicit user action | always outside Workspace reset |
| Project Memory | same | Workspace | persistent | `workspace/.mini-code-memory/` | shared architecture, decisions and facts | coordinated Memory writer; atomic replace | tier lifecycle/curation | normal manager may migrate/recover; health never invokes it | planned | explicit project-scope recovery | no User Memory or arbitrary scope expansion |
| Local Memory | same | Local Workspace | persistent | `workspace/.mini-code-memory-local/` | private local decisions/facts | coordinated Memory writer; atomic replace | tier lifecycle/curation | same read behavior as other Memory scopes | planned, but must remain separately named in dry-run | explicit local-scope recovery | must never be treated as shared Project source |
| Memory approval audit — User | `minicode.memory`, `minicode.memory_approval` | User | persistent | User Memory `approval_audit.json` | decision actors/reasons and entry references | same Memory transaction | no independent cleanup | invalid audit is isolated; health detects missing entry reference | excluded | global audit repair only with explicit user authority | outside Workspace reset |
| Memory approval audit — Project | same | Workspace | persistent | Project Memory `approval_audit.json` | decisions and entry references | same Memory transaction | entry deletion removes targeted audit records | invalid/orphan reference becomes partial | planned with Project Memory | reconcile audit/backlinks atomically with Memory | no independent blind audit wipe |
| Memory approval audit — Local | same | Local Workspace | persistent | Local Memory `approval_audit.json` | decisions and entry references | same Memory transaction | no independent cleanup | invalid/orphan reference becomes partial | planned with Local Memory | same-scope recovery | no User audit |
| Memory backlinks | `minicode.memory` | owning Memory scope | persistent | `related_to` fields in each scope's `memory.json` | relationship between durable facts | same Memory transaction and index rebuild | targeted Project deletion removes backlinks | health checks references by identity without returning content | follows owning Memory store | rebuild/reconcile within one coordinated writer | never a separate raw-file wipe |
| Memory pipeline state | `minicode.memory_pipeline` | Workspace | persistent | Project Memory `pipeline_state.json` | counters/cache stats/curator history | direct JSON write; directory may be created by writer | none | load is best effort; health only bounded-parses | planned | schema isolation or explicit reset | does not authorize Memory content reset |
| Persisted Tool results | `minicode.context_compactor` | Workspace | persistent | `workspace/.mini-code-tool-results/` files | potentially complete Tool output | atomic file persistence by budget manager | no general cleanup authority | health uses `stat` only; never reads Tool output | planned | orphan/result lifecycle policy | never expose filenames/content or accept a path |
| Persistent permissions | `minicode.permissions` | User | persistent | `data/permissions.json` | path/command policy patterns | explicit permission saves | none | config loader validates/falls back; health reports malformed JSON | excluded | user-global configuration recovery | outside Workspace reset |
| Permission approval broker | `minicode.permission_approval` | Process | process-local | no disk store | pending Tool review projections | in-process lock/TTL cleanup | bounded in-memory cleanup | disappears on process exit | not applicable | none; restart is the boundary | not a persistence fact |
| MiniCode settings/provider configuration | `minicode.config` | Configuration/User | source | `data/settings.json` plus compatible user settings source | provider keys, model, env and preferences | explicit settings writer; otherwise source file | none | config load may raise/fall back; health parses structure but returns no keys/values | excluded | separate configuration recovery | never Workspace-reset credentials/provider config |
| MCP configuration | `minicode.config`, `minicode.mcp` | Configuration | source | explicit user MCP file plus Workspace MCP file; compatible embedded settings remain configuration | command, args, env and server names | explicit config edit | none | load validates mapping; health returns only aggregate file facts | excluded | separate safe config validation | never reset MCP sources or expose command/env |
| MCP current-state registry | `minicode.mcp_current_state` | Process | process-local | no disk store | registered client state, failure categories | in-process registry lock and unregister lifecycle | instance cleanup | process snapshot only | not applicable | none; restart clears it | not a durable “offline” fact |
| Global user profile | `minicode.user_profile` | User | source | `data/USER.md` | personal preferences/instructions | explicit profile writer | none | parser falls back/raises by caller; health uses `stat` only | excluded | profile-specific recovery | outside Workspace reset |
| Project profile | `minicode.user_profile` | Configuration/Workspace source | source | `workspace/.mini-code/USER.md` | project instructions/preferences | explicit profile writer | none | health uses `stat` only | excluded | source-file recovery | do not treat as runtime residue |
| Native user Skills | `minicode.skills` | User | source | `data/skills/**/SKILL.md` | executable instructions/tool policy | install copies Skill source | none | discovery skips unreadable sources; health uses bounded traversal + `stat` | excluded | Skill-specific validation | never Workspace-reset user Skills |
| Compatible user Skills | `minicode.skills` | User | source | compatible user Skill root | executable instructions/tool policy | external/source owned | none | same bounded source behavior | excluded | owned by source ecosystem | outside Workspace reset |
| Native project Skills | `minicode.skills` | Configuration/Workspace source | source | `workspace/.mini-code/skills/**/SKILL.md` | project instructions/tool policy | install/source edit | none | discovery skips malformed/unreadable; health uses `stat` only | excluded | Skill-specific recovery | not ordinary runtime data |
| Compatible project Skills | `minicode.skills` | Configuration/Workspace source | source | compatible Workspace Skill root | project instructions/tool policy | external/source owned | none | same bounded source behavior | excluded | owned by source ecosystem | outside runtime reset |
| History/context/supervisor | `minicode.history`, `minicode.context_manager`, `minicode.cybernetic_supervisor` | User | persistent | fixed files under `data/` | prompts, context summaries, supervisor observations | direct/atomic module-specific writes | module-specific clear or overwrite | health parses JSON where needed and emits only fixed diagnostics | excluded from Workspace reset until ownership is redesigned | each owner handles recovery | not silently attributed to current Workspace |
| Tasks/task graphs/decision audit/log | `minicode.task_tracker`, `minicode.task_graph`, `minicode.decision_audit`, logging | User/mixed legacy | persistent | fixed bounded directories/files under `data/` | task text, decisions, log/error text | module-specific direct writes | module-specific delete/list; logs external | health uses bounded traversal/stat and labels legacy aggregate | excluded | authority-specific recovery | no bulk wipe in 9A-2 |
| Installed MiniCode artifacts | `minicode.install` | User | source/persistent artifact | fixed `data/bin/` subtree | executable code, normally no user prompts | installer-owned copy | installer-owned | health uses bounded traversal/stat | excluded | installer responsibility | never Workspace runtime cleanup |
| Workspace cron config | `minicode.cron_runner` | Configuration | source | `workspace/.mini-code/cron.json` | commands/prompts/schedules | explicit source edit | runner-specific | health parses structure but returns no content | excluded | configuration recovery | never normal Workspace data reset |
| Gateway runtime | `minicode.gateway`, `minicode.web.read_model` | Process | process-local | no disk store | active services and composed authorities | process lifetime | shutdown | disappears on restart | not applicable | restart boundary | not a persistence fact |
| Change Feed | `minicode.web.change_feed` | Process projection | process-local | no canonical disk store | hashes/revisions of bounded resources | in-process synchronization | replacement on observation | source failures are resource-local | not applicable | restart/re-observe | never use as deletion authority |
| SSE replay/clients | `minicode.web.event_stream` | Process | process-local | bounded replay/client state in memory | content-free invalidations | stream lock/process lifetime | bounded replay eviction | restart produces stream reset | not applicable | reconnect/replay contract | no disk cleanup |
| WorkingMemory | `minicode.working_memory` | Process | process-local | no canonical disk store | current execution context | process/Run lifetime | in-memory limits | unavailable after restart; Run events are only historical observations | not applicable | none | not a disk fact |
| Lock/temp/backup artifacts | Session, Turn, Run, Memory and deletion writers | owning store | metadata/artifact | fixed names beneath the owning roots | may contain writer metadata or copies of sensitive files | writer-specific locks and atomic replace | some writers clean on success; stale residue can remain | health classifies by `lstat`, never follows/removes; affected store becomes partial | only as a future dry-run item after owner validation | 9A-3 decides stale-vs-active and recovery | never infer safe deletion from age alone |

## Public schema

`PersistenceHealthReader.snapshot()` returns exactly:

- `schemaVersion`, fixed at `1`.
- `generatedAt`, exact UTC millisecond timestamp.
- `mode`, fixed at `read-only`.
- overall `status`.
- safe Workspace `id` and display `name`, never an absolute path.
- aggregate counts and bytes.
- the 25 fixed Store projections used by the Dashboard.
- a planning-only maintenance projection.
- fixed-vocabulary, store-scoped diagnostics.

Counts are non-boolean safe integers or `null`. Process-local Stores always use
`null` counts/bytes/time because their in-memory state is not a disk fact.
Diagnostics never contain raw exceptions, paths, commands, environment values,
credentials, prompts, Memory, Session or Tool content.

## Bounded and no-write inspection

- At most 25,000 directory entries are visited per snapshot.
- A JSON/NDJSON file is parsed only when its structure is required, and only up
  to 2 MiB.
- Source files, profiles, Skills, Tool outputs, locks and ordinary artifacts use
  `lstat`/`stat` facts only; their content is not read.
- The schema validator caps the encoded response at 256 KiB.
- Every path is fixed by the Store map. Each chain is classified with `lstat`;
  symlinks and special files are rejected and no symlink is followed outside an
  allowed root.
- Each Store fails independently. A malformed Memory file does not hide Session,
  Run or configuration facts.
- Missing roots are empty facts and are not created.
- No Manager, retention hook, cleanup, migration, backup, repair or rebuild path
  is invoked, and no lock is acquired.

`GET /api/v1/data-health` accepts no query parameters, returns JSON with
`Cache-Control: no-store`, and uses a fixed safe 500 envelope for an unexpected
failure. Unknown API routes retain the existing structured 404.

## Batch 9A-2 recommended boundary — not implemented

Batch 9A-2 should consume Store IDs and dispositions, never raw paths from the
browser:

1. Create a Workspace-scoped dry-run plan with fresh authority revisions.
2. Reuse Batch 8D's per-conversation and Project Memory deletion authorities for
   records they already own; do not bypass their fences, Session transaction,
   Turn/Run writer guards or Memory coordinator.
3. Add retention policies separately for terminal Turns/Runs/Sessions, Project
   Memory, Local Memory, pipeline state and Tool-result artifacts.
4. Require an explicit CLI command plus a second confirmation that names the
   resolved Workspace identity and dry-run counts.
5. Exclude User Memory, User approval audit, persistent permissions, provider and
   MCP configuration, profiles, Skills, installed artifacts, global/legacy user
   records and every process-local Store.
6. Refuse execution when a planned Store is partial/unavailable or a deletion
   fence/writer is active.
7. Record stepwise outcomes. If only part of a plan commits, rerun authoritative
   GETs and resume idempotently from verified remaining records; never claim a
   cross-store filesystem transaction.

Batch 9A-3, not 9A-2, owns corruption isolation, index rebuild and compatibility
recovery. A health diagnostic is evidence for planning, not permission to repair.

## Final certification and Phase 2B timing policy

Batch 9A-1 is formally closed at production baseline v33. No production or
Dashboard byte changed during final recertification, so the existing isolated
wheel and desktop/narrow browser evidence remains authoritative.

Phase 2B now has two explicit certification layers:

- Default `pytest` and `evaluate_memory_retrieval_phase2b.py` use deterministic
  acceptance. Correctness, integrity, deterministic core, formal-tree safety,
  frozen assets, no-network and candidate-cap invariants remain mandatory.
- Real wall-clock P50/P95 and peak-memory values are always measured and written
  as observations. They become CLI exit criteria only with the explicit
  `--enforce-wall-clock-performance` flag intended for a controlled benchmark
  environment.

The performance limits were neither deleted nor raised: canonical P95 remains
bounded by `2.866455 ms` and consolidator-100 P95 by `10 ms` in strict mode.
Batch 9A-1.2.1 removed the one residual default-test assertion that still
required a real consolidator sample to pass the 10 ms gate. The replacement
requires finite non-negative observations, exact observation/report equality,
honest gate classification and correct advisory/strict exit behavior. Synthetic
tests cover every wall-clock failure combination, exact threshold equality,
nonzero network calls and both candidate-cap failures.

The target test SHA advanced from
`fc36869382c4f8a41b33188374543b68eedae4d14ed5fd50cfb31c97a158706d`
to
`828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3`
with reason `remove_remaining_default_wall_clock_assertion`. Only that
`PHASE2B_FROZEN_HASHES` entry changed; the other 11 pins remain byte-identical.
The one Batch 9A-1.2.1 strict execution recorded canonical P95 `2.794834 ms`,
consolidator-100 P95 `2.680833000340499 ms`, reference `2.1233 ms`, material
limit `2.866455 ms`, `strictPassed=true`, exit 0 and zero remote calls. It was
not retried.

Final evidence:

- Default Phase 2B evaluator tests: three consecutive `28 passed` runs.
- Complete pytest: `2909 passed, 2 skipped, 3 warnings` twice.
- Production verifier: v33, parent v32, 56/56 files, candidate/current matches,
  v1-v33 manifest integrity, dependencies `[]`.
- Official semantic evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  remote calls 0, evaluation passed and `phase2b_assets_unchanged=true`.
- Accepted gold remains SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime_ns `1784135857000000000`.
- The temporary strict and frozen formal deterministic cores are byte-identical
  at `f47002d15be904b9f73953a0e7a537c1fd14c327810129bafb8fcb6c51873559`.
  Accepted/generated semantic projection remains
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
  per-case fingerprint remains
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- The formal Phase 2B artifact remains SHA
  `2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996`,
  size `94181`, mtime_ns `1784815255303450427`.
- Scoped Ruff, targeted `py_compile`, full `compileall`, `node --check` for
  `app.js` and `cost-format.js` all pass. pyright and mypy are not installed.

Production and formal frontend bytes still match v33, so the existing isolated
wheel and browser evidence is reused. No production code, evaluator logic,
algorithm, threshold, dataset, fixture, accepted gold, formal Phase 2B artifact
or v1-v33 manifest changed. No v34 was created. Batch 9A-2 is the next authorized
task; no Batch 9A-2 or Batch 9A-3 behavior is included here.
