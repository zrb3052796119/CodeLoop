# Persistent Memory Repair and Acceptance — 2026-08-23

## Executive result

The persistent Memory repair is locally green and now has three deterministic
cross-restart cases for each of five lesson families. The final external V5
acceptance is prepared but **not yet executed**: the platform rejected the
provider call because the account API-usage limit was exhausted. The session
UI reported a recovery window ending 2026-08-25 23:45, but no independent,
credential-free provider failure artifact was retained for that timestamp.
No result was fabricated and no prior artifact was overwritten.

Current evidence must therefore be read in three distinct layers:

1. **Deterministic persistence/retrieval acceptance:** 15/15 cases passed,
   three per lesson family. Each case persists a lesson, creates a fresh
   `MemoryManager`, retrieves through the canonical pipeline, and proves that
   the exact stored Memory ID was rendered. This layer begins with an explicitly
   approved synthetic entry and disables Hybrid, so it proves durable storage
   and lexical canonical retrieval—not reflection generation or Hybrid quality.
2. **Historical live acceptance:** V4 executed ten real provider-backed
   synthetic tasks, two per family. It passed 7/10 cases and 84/91 oracles;
   all 10 cases passed exact Memory attribution. Its three failures were kept
   as evidence and led to the final protocol/oracle repairs.
3. **Post-final-fix live acceptance:** V5 freezes the same ten tasks and is
   ready to run, but has no `live-results.json` yet because of the external
   usage-limit block.

This is a strong local repair result, not yet a final provider-backed 10/10
certification.

## Five supported lesson families

| Lesson family | Deterministic cases | Result | What is proven |
|---|---|---:|---|
| Path/resource recovery | `path-auth-policy`, `path-invoice-schema`, `path-release-manifest` | 3/3 | A stable resource-location lesson survives restart and the exact ID is injected for a matching task. |
| Command recovery | `command-release-contract`, `command-client-check`, `command-migration-check` | 3/3 | A verified command recovery is persisted and recalled for the same operational contract. |
| Code-fix recovery | `code-stable-sort`, `code-cache-key`, `code-default-copy` | 3/3 | A verified behavioral change can be recalled without over-binding the lesson to a one-off source path. |
| Stable verification rule | `verify-parser`, `verify-ledger-config`, `verify-web-contract` | 3/3 | A project verification requirement survives restart and is injected with its `verification_rule` identity. |
| Project constraint/decision | `constraint-no-network`, `constraint-append-migrations`, `constraint-json-compat` | 3/3 | A durable project rule is retrieved as a constraint/decision rather than a generic approach. |

The matrix lives in
`tests/fixtures/persistent_memory_lesson_matrix.json` and is executed by
`tests/test_persistent_memory_lesson_matrix.py`. The test module reports 16
passes: 15 behavioral cases plus one taxonomy-integrity check.

## Repairs completed

### Persistence and projection boundary

- Recursive sanitization now covers content, metadata, provenance, traces and
  nested values, including credential-shaped values and sensitive key-name
  variants.
- Raw task text is no longer durable authority; bounded fingerprints and
  sanitized evidence are stored instead.
- Approval and lifecycle authority are bound to content/tier/revision hashes,
  so mutation after approval cannot bypass review.
- Rejected or unsafe legacy entries are not projected into `MEMORY.md`; stale
  projections are rebuilt from canonical authority.
- If `memory.json` exists but cannot be decoded, production loading now stays
  empty and readonly loading fails closed. A stale `MEMORY.md` can be migrated
  only when JSON authority has never existed, so corrupt authority cannot
  reverse-promote derived Markdown.
- Authorization schemes and complete password/passphrase assignments are
  redacted through the line boundary, including whitespace and punctuation;
  persistence redaction remains idempotent.
- `ProjectFactsStore` now rejects dependency-prompt injection and unsafe mixed
  batches at write, load and render boundaries.

### Feedback and causality

- Explicit user correction quarantines the exact rendered lesson immediately.
- Independently bound verification failures quarantine an exact lesson after
  two corroborated failures. Whole-turn failure without exact Memory IDs fails
  closed and cannot punish every rendered entry.
- Feedback now carries a stable Run/observation identity through the Agent,
  pipeline and explicit-user-signal paths. Each entry persists only the SHA-256
  receipt and polarity; replay through a fresh manager is a no-op, a conflicting
  polarity fails closed, and two distinct verification IDs are required to
  satisfy the quarantine threshold. The receipt window is bounded to the most
  recent 256 observations per entry; older deliveries are outside that finite
  idempotency window.
- Transient rate limits, network/5xx failures, TLS/DNS errors, locks and similar
  environmental failures cannot become durable causal recovery claims,
  including the file-edit branch where an unrelated edit sits between the
  failure and a later successful retry.
- Recovery extraction now supports the real `edit_file` aliases (`old/new` as
  well as normalized search/replace forms), uses red-green semantic anchors,
  and projects verified behavior without overfitting to the source fixture
  path.
- Strong claim types suppress a weaker duplicate `approach` claim.

### Canonical Hybrid retrieval

- Canonical retrieval now activates the accepted Qwen embedding profile and
  evidence-bound `deepseek-chat` verifier/challenger route.
- The verifier uses an immutable production profile instead of inheriting the
  main model's temperature, token limit, retry or endpoint settings.
- V4 recorded 18/18 `memory.retrieved` events with Hybrid requested and active,
  Qwen embedding, evidence-bound override, and 0/18 lexical fallbacks. Ten
  verifier and ten challenger calls completed for the ten selected memories.
- A bounded cache and content-free runtime events were added; runtime embedding
  caches are excluded from source-tree mutation oracles.

### Exact attribution and acceptance integrity

- `memory_attributed` joins the exact source lesson ID to the ID rendered in a
  declared later turn and verifies the expected claim type. Public results do
  not serialize Memory IDs or lesson text.
- `verification_passed` now consumes normalized `task.verified` evidence and
  requires `kind=tests` plus a source in `run_command_exit` or `test_runner`.
  Workflow review, lint and build observations cannot satisfy it. This removes
  the V4 registry false negative without weakening the test requirement.
- Public results bind the raw manifest SHA-256, a canonical per-case contract
  SHA-256, and a credential-free MiniCode source-tree snapshot SHA-256.
  Every case also binds the exact bytes of a private evidence file;
  resume loads that byte snapshot once, validates its manifest/source/case/run/
  oracle/runtime identities, re-derives every public status and counter, and
  rejects a coherent public-only rewrite. Private V5 evidence is written
  before the public result. POSIX hosts enforce owner-only directory mode 0700
  and file mode 0600. Windows hosts inherit the parent directory ACL; this
  acceptance does not independently verify an owner-only DACL. The evidence no
  longer stores raw model responses or provider exception messages.
  `--resume` rejects a
  changed prompt, fixture, oracle, source tree, duplicate result or missing
  identity before executing any case. The manifest is parsed and hashed from a
  single byte snapshot, closing the initial parse/hash TOCTOU window. Identity
  is rechecked before and after every case and before final return; a source or
  manifest change aborts before that case result is written. Resumed and fresh
  case results share one strict allow-listed schema, so a passed result with a
  removed oracle cannot be reused. External manifest paths are projected to a
  filename so result JSON cannot expose an absolute home path.
- The result envelope records the manifest case count, exact selected IDs,
  completed count, selection status, finalization state, and whole-suite
  status. A successful `--limit 1` run is explicitly `suiteStatus=incomplete`
  and returns nonzero; only a finalized result covering every manifest case
  can say `suiteStatus=passed`. Per-case writes remain unfinalized until the
  last identity check, so an interrupted second case cannot leave a partial
  file that looks like a complete green suite.
- V5 also binds the constructed adapter and its credential-free wire route
  before every turn: `OpenAIModelAdapter`/`openai_compatible`, provider
  `custom`, model `deepseek-v4-pro`, and endpoint
  `https://api.deepseek.com/v1/chat/completions`. A mock adapter, changed port,
  alternate path, provider or model fails before Agent execution. Each passed
  case carries the profile hash, and resume validates the case, evidence and
  envelope identities. The current local configuration's safe projection
  matches this contract; API keys, headers and prompts are excluded.

### DeepSeek thinking/tool replay

V4 exposed two provider 400s. Across its 73 tool-call model responses, exactly
two had both visible text and tool calls; both failed on the following request,
while all 71 tool-only responses continued normally.

The root cause was deterministic: MiniCode stored one provider assistant turn
as two messages—an assistant text message followed by a separate tool-call
assistant message. Only the second carried `reasoning_content`. The repair now
stores and replays `content`, `reasoning_content`, and all tool calls as one
atomic assistant turn, with tool results following it. Multi-tool and streaming
forms are covered. Anthropic now groups the same `assistantTurnId` into one
assistant block containing all tool uses, and context compaction moves its cut
point back to the start of an atomic multi-tool turn. A pinned `load_skill`
call also preserves every sibling call and result in that provider turn, so
compaction cannot create an invalid partial replay. Hidden reasoning is stored
and token-counted once per provider turn rather than once per tool call.

This matches the official DeepSeek requirement that reasoning attached to
thinking-mode tool-call turns be fully replayed in later requests:
[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/).

The protocol tests were RED before the fix (4 failed, 3 passed) and GREEN after
it (7 passed).

## Frozen live evidence

| Version | Scope | Cases | Oracles | Interpretation |
|---|---|---:|---:|---|
| V1 | Initial ten-case run | 0/10 | 52/81 | Retained first failure evidence; lesson write/reuse and oracle contracts were incomplete. |
| V2 | Three-case smoke | 2/3 | 22/23 | Fixed token normalization/oracle plumbing; not a full acceptance. |
| V3 | Ten cases with exact attribution | 7/10 | 83/91 | Found code-recovery path overfitting and a remaining reasoning replay error. |
| V4 | Same ten tasks after recovery projection fixes | 7/10 | 84/91 | Exact attribution 10/10; found atomic tool-turn bug and tool-coupled registry oracle. |
| V5 | Same ten tasks after final protocol, compaction, oracle and evidence-integrity fixes | Pending | Pending | Manifest is frozen and validated; provider execution blocked by usage limit. |

SHA-256 identities:

| Artifact | SHA-256 |
|---|---|
| V1 manifest | `b73b47102aac8eabfc2595555ca6aafca2000af0573c4e0bdec8d788796b1a93` |
| V1 result | `a9ede723a4c2e4b93d8842085b8061dc222b5e64580c15b9414effd4d22ef53f` |
| V2 manifest | `ab21f4010adeb192bd9a4fae2f25dfb93912fbd31b0430ececbe674a88a82bc0` |
| V2 result | `7000c38ff3558bb62766dd0af1c3170f976658b72e81fae1264eb50e214ff559` |
| V3 manifest | `a4644b34673cba48b21d9114c86f5948809c345f6446ca0e0a2444bfa7cc3cbf` |
| V3 result | `b64954ef63b8c99f3230927c5bef508f59b1c3d0fc4a6f786e279b5714525f5f` |
| V4 manifest | `beb99521e26b63408fdb075745f1fd04c1a11e8fa6cb8f2ba0ea6acc5cd6a788` |
| V4 result | `373d3e7b85e07e2a2781ba247db1fea72d7e10beb1935e31c2a5b51ad473c184` |
| V5 manifest | `72db4d34a756fe63d35e978e4a65a38bf95f16d1de88fc17da9f59efd8cfc288` |

V2 and V3 bind the preceding frozen manifest identity. V4 and V5 bind both the
preceding manifest and its retained first-attempt result identity. V1 through
V4 were not modified by later retries.

## Verification performed

- Python 3.13.13, pytest 9.0.3 and Ruff 0.15.16 were used. The exact 22-file
  persistence/Hybrid/reflection/protocol/runner command recorded below passed
  417/417 (exit 0):

```bash
python -m pytest -q \
  tests/test_memory_persistence_boundary.py \
  tests/test_memory_feedback_quarantine.py \
  tests/test_memory_layering.py \
  tests/test_memory_regressions.py \
  tests/test_memory_value_end_to_end.py \
  tests/test_project_facts_security.py \
  tests/test_memory_hybrid_v2.py \
  tests/test_cybernetic_orchestrator.py \
  tests/test_anthropic_adapter.py \
  tests/test_openai_reasoning_roundtrip.py \
  tests/test_context_compactor.py \
  tests/test_reflection_synthesis.py \
  tests/test_reflection_approach_and_change_summary.py \
  tests/test_reflection_operational_recovery.py \
  tests/test_reflection_generic_tool_recovery.py \
  tests/test_live_north_star_runner.py \
  tests/test_persistent_memory_lesson_matrix.py \
  tests/test_persistent_memory_repair_acceptance_manifest.py \
  tests/test_persistent_memory_repair_acceptance_v2_manifest.py \
  tests/test_persistent_memory_repair_acceptance_v3_manifest.py \
  tests/test_persistent_memory_repair_acceptance_v4_manifest.py \
  tests/test_persistent_memory_repair_acceptance_v5_manifest.py
```

- The latest six-file protocol/runner/compaction/V5/matrix command passed
  182/182: OpenAI replay 7, Anthropic replay 4, compaction 85, live runner 66,
  V5 builder 4 and lesson matrix 16.

```bash
python -m pytest -q \
  tests/test_openai_reasoning_roundtrip.py \
  tests/test_anthropic_adapter.py \
  tests/test_context_compactor.py \
  tests/test_live_north_star_runner.py \
  tests/test_persistent_memory_repair_acceptance_v5_manifest.py \
  tests/test_persistent_memory_lesson_matrix.py
```

- The deterministic lesson matrix passed 15/15 behavioral cases (16/16 with
  taxonomy integrity). The full reflection family passed 535 tests; the three
  transient-file-edit regressions also pass current production and fail the
  real pre-fix HEAD. The reflection command was
  `python -m pytest -q tests/test_reflection*.py`.
- `python -m ruff check minicode scripts --no-cache` passed. With the same Ruff
  version, `python -m ruff check minicode scripts tests --no-cache` reports 227
  historical test findings, while literal `python -m ruff check . --no-cache`
  reports 820; neither broader scope is claimed green.
- `python -m compileall -q minicode scripts` passed. `git diff --check` passed
  for tracked changes; it does not inspect the current untracked artifacts and
  tests.
- The final unsandboxed, loopback-enabled command `python -m pytest -q` passed
  **4,424 tests with 2 skips**, exit 0, in 339.49 seconds. Its first post-fix
  diagnostic run exposed one legacy mock-call shape (`observation_id=None`);
  after preserving the old call signature when no Run ID exists, the exact
  failing test plus 42 related tests passed before this clean full rerun.
- The run used HEAD `4d61d0a6bd2c8c7301c8469fc4ebab6ff73811e0` plus the current dirty
  worktree: 66 tracked/indexed paths and 37 untracked paths at closeout. The
  tracked binary diff SHA-256 was
  `66b6189c71b1a2dc6d650459c0a5c4c1ad8ca595bc615d441c8af1dbe5cce05e`;
  the runner's credential-free production source snapshot was
  `eac808c572d6632e5c4d2565476ef204ed591ae021e6142d5e0ed4fc4b87676c`.
  This is evidence for these bytes, not for HEAD alone; untracked contents are
  not bound by the tracked patch hash, and no signed archive/JUnit log is
  claimed.

## Privacy and evidence handling

- Provider-backed runs contain only synthetic source files, prompts, markers
  and verification commands authorized for this acceptance.
- Public `live-results.json` files contain aggregate IDs, counts, statuses,
  oracle names and identity hashes, not Memory IDs, lesson bodies, prompts,
  provider credentials, request headers or absolute home paths.
- Historical raw evidence directories contain temporary workspace paths, lock
  files and generated bytecode. New V5 case evidence is minimal and omits raw
  responses/error messages, but the surrounding workspace/journal tree still
  should not be published wholesale without a separate export/redaction step.
- Existing documentation may contain credential-shaped examples; the new V5
  artifact and this report contain no detected `sk-...` credential value.

## Remaining limitations

1. **V5 live result is pending.** Local tests prove the repaired contracts, but
   only a successful provider-backed V5 run can close the observed live defect.
2. Exact Memory attribution proves that a declared lesson was rendered; it does
   not prove the model succeeded only because of that lesson.
3. This ten-case suite is a warm functional acceptance, not a cold-control
   efficacy benchmark. Warm/cold benefit claims must continue to cite the
   separate paired studies.
4. V5 now binds the constructed adapter, model and exact credential-free
   request URL before every turn, and the separate model-routing acceptance
   observed matching outbound request metadata. It still does not
   cryptographically attest proxies or the provider's server-side
   implementation behind that endpoint/model alias. Hybrid Run events likewise
   prove the evidence-bound verifier branch, not independently the verifier's
   final network destination.
5. V4 recorded 11 approximately 15-second reflection shadow timeouts. The
   deterministic fallback preserved task completion, but reflection latency
   and provider availability remain observable reliability work.
6. Older V1/V2 raw evidence trees do not have a complete per-file checksum
   index, although their frozen manifest and result identities are retained.
7. `sourceCodeSha256` is a credential-free on-disk source-tree snapshot checked
   at case boundaries. It detects ordinary persistent drift, but is not an
   atomic attestation of already imported Python bytes; an adversarial
   edit/load/restore sequence requires a read-only content-addressed execution
   image or external attestation.
8. `ruff check minicode scripts --no-cache` is green. The broader exact command
   `ruff check minicode scripts tests --no-cache` still has 227 historical test
   findings; literal `ruff check . --no-cache` has a larger 820-finding baseline.
9. Public/private result hashes and schema checks detect public-only, stale,
   partial and incoherent evidence; they are not a MAC or external signature. A
   malicious actor able to rewrite both private and public evidence coherently
   remains outside this local acceptance trust model.
10. The working tree is intentionally dirty while this repair is under review.
   Manifest/source identities bind the production acceptance surface, but the
   verification statements are not yet bound to a commit or signed worktree
   archive.

## Command to close V5 after quota recovery

```bash
python scripts/run_north_star_live.py \
  --manifest artifacts/persistent-memory-repair-acceptance-v5/manifest.json \
  --output artifacts/persistent-memory-repair-acceptance-v5/live-results.json
```

Do not use `--resume` for the first V5 run. If any case fails, retain that
result unchanged, diagnose from its evidence directory, and create V6 rather
than overwriting V5.
