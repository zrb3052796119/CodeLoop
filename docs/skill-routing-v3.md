# Skill Routing V3

Skill discovery defaults to the executing model choosing from the authorized
catalog. Small catalogs expose Skill descriptions directly; large catalogs
expose Directory descriptions and read-only discovery tools. Deterministic
parsing retains explicit user directives and constraints. The older V3
candidate-selection pipeline remains available through `mode="routed"`.

## Selection modes

`skillSelection.mode` selects between two turn-level selection paths. Set it
in the global `~/.mini-code/settings.json` (`skillSelection.mode`) or override per-process with
`MINI_CODE_SKILL_SELECTION_MODE`; invalid values log a warning and fall back
to `model`.

- `routed` (explicit compatibility option): the deterministic v3 pipeline documented below —
  intent parsing, candidate retrieval, and the optional remote reranker
  decide which Skills are shown and loadable.
- `model` (default): the system prompt carries the full authorized catalog — every
  Skill's `qualified_name` plus complete description under an "Available
  skills:" header with a model-first usage guide — and the executing model
  chooses Skills itself, loading bodies on demand with `load_skill`.

In `model` mode the load policy is catalog-scoped: instead of enumerating
bindings it states "the turn's whole catalog snapshot minus explicit denials
and constraint vetoes" (`allow_catalog_scope=True`, reason code
`model-catalog`). The scope is name-level intent only — the loader still
resolves the requested name against the turn's bound catalog snapshot and
re-verifies the exact binding identity and content digest before reading the
file, and denials always win. Catalogs of any size stay in model mode. The
subprocess payload carries the flag at version 3; version 2 payloads remain
parseable with the scope defaulting to `False`. Nested policy intersection
keeps the rule that denials union: scope ∩ scope stays scoped, and scope ∩
enumerated yields the enumerated side's set (scope contributes everything).
Only deny-all closes Skill loading for the turn. Unresolved or ambiguous
natural-language Skill references no longer close loading — they are
recorded in `outcome_reason` with `-unresolved-not-enforced` /
`-ambiguous-not-enforced` suffixes, and the loader returns a correctable
error for unknown names. Intent parsing still runs because authority and
constraint vetoes depend on it, but scoring, embeddings, and the candidate
reranker are skipped entirely, and `required_skill_names` is effectively
empty (no routing-driven load-before-final enforcement). The mode is wired
at all five entry points, including sub-agents.

### Hierarchical escalation (budget-driven)

Within `model` mode, the flat full-catalog projection is itself bounded by
`skillSelection.flatCatalogCharBudget` (env
`MINI_CODE_SKILL_FLAT_CATALOG_CHARS`, default 8000, clamped to 1000–200000).
When the estimated flat rendering (`qualified_name` + description + per-line
overhead per visible Skill) exceeds that budget and at least one visible
Skill lives in a SKILL_DIR directory, the turn escalates to
`prompt_mode="hierarchical"`: the prompt shows one card per directory
(`- {directory}: {description} ({n} skills, ref {short_hash})`, where the
ref is the first 12 hex of a sha256 over the sorted child content digests)
plus an `Undirectoried skills:` section with full descriptions for
root-level Skills. Directoried Skills are then expanded on demand through
two read-only tools that share the routing snapshot and re-apply the turn
policy at run time:

- `browse_skill_directory(directory_names, offset?, limit?)` — paged
  expansion of one or several directories (`[""]` addresses root-level
  skills), `TRUNCATED: yes - call again with offset N` continuation like
  `read_file`, pages bounded by `skillSelection.skillPageCharBudget` (env
  `MINI_CODE_SKILL_PAGE_CHARS`, default 4000, clamped to 500–50000).
- `search_skills(query, directory_names?, offset?, limit?)` — lexical token
  overlap plus alias-concept coverage over name/description/keywords of all
  authorized skills; `directory_names` only biases a preferred section whose
  ordering interleaves preferred and global matches without dropping either
  tail. Offsets refer to the complete stable sequence, including at `limit=1`.

The page budget includes headings and continuation instructions. An oversized
description is explicitly shortened while preserving its exact load name;
loading still returns the full Skill body.

Both tools return descriptions only; bodies still load exclusively through
`load_skill`, and the load policy is identical to flat model mode
(`allow_catalog_scope=True`, reason `model-catalog`). An over-budget catalog
without any SKILL_DIR directory never escalates. Deny-all closes discovery
for the turn; an unknown directory name returns a correctable error listing
the valid ones.

Independently of the mode, a load batch barrier applies: if a model
response's tool-call batch contains a `load_skill` for a Skill not yet
loaded this task, only the `load_skill` calls execute; every other call in
the batch receives a synthetic failure result
(`error[deferred_until_skill_loaded]: not executed in this batch; re-propose after reading the skill body`)
and is never dispatched. Already-loaded Skills do not trigger the barrier. Malformed load arguments
receive normal validation errors and conservatively defer sibling calls so
the model can correct the request on its next step.

Structured force/disable interfaces (`/skill`, `/skills off`)
remain unimplemented.

### Intent clues and task stages

`skillSelection.intentHints` and `skillSelection.intentPrefetch` remain opt-in
experiments (both default to `false`). Hints supplement the original user
request; recognized operations may omit conditions or ordering and are not an
execution plan. The model establishes the current stage and its evidence,
loads the smallest sufficient Skill set, and can discover additional Skills
when later conditions hold. A project read before discovery is valid behavior.

### Catalog changes during a conversation

CLI and TUI refresh the catalog at each user-turn boundary, atomically replacing
the shared discovery/load snapshot while retaining existing ordinary tools and
MCP connections. New Skills become discoverable on the next turn. Changed or
deleted Skill bodies are removed from previous load exchanges so stale
instructions are not reused. An edit during an active turn still fails the
loader's digest check; it is picked up at the next turn boundary.

## Legacy routed decision flow

1. Parse the request into ordered, clause-level operations with affirmed or
   negated polarity.
2. Apply explicit Skill directives. Unambiguous requested Skills are retained;
   denied Skills are removed before semantic processing.
3. Retrieve a bounded Top-K candidate pool from Skill metadata, one affirmed
   operation at a time. Intent, capability, tool, source, and historical
   feedback may rank a candidate but cannot grant explicit Skill authority.
4. If enabled and supported, send only the bounded request projection and
   candidate manifest to a remote reranker.
5. Validate the native-schema response locally. The response may select only
   input candidate IDs or abstain. Invalid, duplicated, invented, over-limit,
   low-confidence, timed-out, cancelled, unsupported, or over-budget responses
   cannot add a Skill.
6. Preserve explicitly requested Skills and project the final decision through
   the existing `SkillRoutingResult` compatibility interface.
7. Compile that decision into a host-owned `SkillLoadPolicy`. The model can
   load only the selected Skill bindings for a routed turn; each binding fixes
   `qualified_name`, source, content digest, and routing-identity digest.
   Explicit denials remain effective even when routing falls back to an
   inventory.

## Deterministic contracts

The deterministic layer is more than a single intent label. It preserves an
ordered list of clause-level operations, including intent, action, polarity,
span, bounded entities, evidence, and whether bounded conversation context was
used. This lets the router associate each candidate with the exact operation
indexes it can serve. A Skill with non-empty `supported_operations` may never
claim an incompatible operation, even when that Skill was explicitly named.

Context is used only to resolve a continuation or omitted subject. A new
concrete target does not inherit an old one. Negated and completed-action
mentions are retained as non-authorizing evidence rather than converted into
new work. Generic non-engineering search, poetry, greeting, arithmetic, and
similar requests may receive an intent operation without thereby becoming a
match for an engineering Skill.

## Executable Skill boundary

Skill Markdown is executable model instruction, so prompt filtering alone is
not an authority boundary. `load_skill` checks the turn policy before opening
the bound Skill file:

- a routed turn may load only its selected qualified bindings;
- inventory fallback may load any catalog binding except an explicitly denied
  one;
- deny-all, unresolved explicit names, ambiguous explicit names, and routing
  assembly failure close Skill loading;
- nested agents receive the intersection of parent and child policies, and the
  parent policy is serialized through the general-agent subprocess boundary;
- a stale digest, unsafe path, ambiguous bare name, or invalid policy fails
  closed.

The policy controls only Skill instruction loading. Tool permissions, path
containment, command review, and cancellation remain separately enforced by
their existing execution boundaries.

## Capability and catalog isolation

Built-in `ToolDefinition` objects declare an explicit routing-only domain and
scope. Legacy or dynamic tools retain a conservative fallback, while unknown
tools default to destructive rather than being guessed safe. Capability
snapshots are immutable, metadata-only views: executing a capability snapshot
cannot invoke its backing Tool.

Every live `ToolRegistry`, including a filtered sub-agent registry, binds
`propose_skill` to its own capability snapshot. It does not consult the
process-global compatibility registry, so two concurrent turns with different
tool surfaces cannot contaminate each other's Skill proposals. Project Skill
tests additionally require every declared Tool's effect scope to appear in the
Skill frontmatter.

## Remote safety controls

The reranker is disabled by default. When enabled, it:

- shares the Agent Turn token, model-call, and cost accounting;
- reserves at least one model call for normal Agent execution;
- uses a short local deadline bounded by the parent turn deadline;
- disables tools, retries, and mutable reasoning state;
- bounds input characters, output tokens, response bytes, and candidate count;
- requires an adapter that explicitly advertises native strict JSON Schema;
- emits content-free model lifecycle and Skill decision provenance events;
- propagates cancellation and the parent deadline instead of swallowing them.

OpenAI-compatible naming alone is not evidence of native JSON Schema support.
Unknown, custom, OpenRouter, and Anthropic transports remain unsupported until
their wire contract is explicitly implemented and tested.

## Configuration

Configure values in the private user environment (`~/.mini-code/.env`) or the
process environment. Provider credentials remain in the existing provider
variables and are never stored in this configuration block.

```dotenv
MINI_CODE_SKILL_RERANKER_ENABLED=false
MINI_CODE_SKILL_RERANKER_TIMEOUT_SECONDS=1.5
MINI_CODE_SKILL_RERANKER_MIN_CONFIDENCE=0.80
MINI_CODE_SKILL_RERANKER_MAX_CANDIDATES=8
MINI_CODE_SKILL_RERANKER_MAX_INPUT_CHARS=6000
MINI_CODE_SKILL_RERANKER_MAX_OUTPUT_BYTES=2048
MINI_CODE_SKILL_RERANKER_MAX_OUTPUT_TOKENS=128
MINI_CODE_SKILL_RERANKER_RESERVE_MODEL_CALLS=1
```

Activation requires an explicitly supported remote provider and its API key.
If either is missing, routing stays deterministic and no provider call occurs.

## Production rollout

Keep the feature disabled while collecting shadow labels. Before enabling it
for automatic selection, evaluate candidate Recall@K, selective precision,
abstention rate, invalid-response rate, p95/p99 latency, and cost on a
time-separated, duplicate-cluster-separated holdout. Confidence thresholds
must be calibrated from corrected outcomes for the exact model and contract;
the small historical pilot is diagnostic data, not a production calibration
artifact.

## Constraint semantics and the non-defensive routing principle

`minicode/intent_constraints.py` owns typed evidence for test creation and
execution constraints. Parser reconciliation and mandatory-operation routing
consume `TestConstraint`, including action, state, scope/subtypes, source span,
and discourse. `RELEASED` removes a prohibition; it does not itself authorize
an operation. `supported_operations` describes operations a Skill can serve;
`required_operations` describes steps its workflow must perform.

The intended veto rule is a **current, explicit, directly relevant
`DIRECTIVE + DENIED` constraint that conflicts with a required operation**.
A denial scoped to unrelated test types must not become a global prohibition.
Status reports, quotations, discussion of wording, positive negation idioms,
and explicitly released prohibitions must not veto a compatible Skill.
An explicit Skill request still cannot override a real mandatory-step
conflict. Invalid executable metadata and Skill-load identity failures retain
their separate contract checks.

This is the acceptance contract, not a claim that every language form already
implements it correctly. The 2026-09-05 frozen audit below found remaining
divergence in quotation handling, negation idioms, constraint release, and
direct-denial recognition. Do not compensate for those gaps by adding broad
keyword bans. Shared semantics need to cover the failing discourse and scope
boundaries before promotion.

## Historical offline evidence and limits

The preceding documented deterministic repair passed two complementary suites
without a provider call:

- 96/96 joined cases covering operation parsing, context switches, compound
  requests, explicit authority, negation, no-Skill outcomes, clarification,
  prompt projection, and candidate-operation association;
- 42/42 examples declared by the 16 real project Skills, routed through real
  discovery, the core Tool capability snapshot, and the production turn seam.
  Those examples exercise 32/32 Skill-operation contract entries (15 unique
  operation strings), including compound prompts and operation-index
  association.

A separately frozen 110-case wild-language challenge set records the harder
boundary. Its immutable pre-repair report was 12/110 strict and 24/110 lenient;
that repair's documented result was 78/110 strict and 91/110 lenient. The
predeclared solid-oracle subset is 75/85 strict and 84/85 lenient. Relevant
safety measurements are 24/24 forbidden-Skill checks, 11/12 context-use exact,
37/46 negative-constraint case exact, and 41/50 negated-operation recall. The
evaluator reports zero remote calls and checks that its production sources do
not change during a run.

The 42 examples are catalog contract tests, not unseen production evidence.
The 96-case joined set runs the real parser/router seam over 12 synthetic
Skills and nine synthetic Capabilities; it is curated and deterministic, not
an unbiased, time-separated production holdout. The 110-case set was frozen
before repair but has now been repeatedly inspected, so it is a regression set,
not fresh production evidence; its 25 predeclared ambiguous cases remain weak
(3/25 strict, 7/25 lenient). These results support the deterministic safety
boundaries, but they do not establish universal language understanding or
calibrate a remote classifier. The remote semantic fallback therefore remains
disabled until a new time-separated, duplicate-cluster-separated sample has
enough reviewed false-positive and false-negative labels for the exact
provider/model/contract.

## Frozen final acceptance, 2026-09-05

**Acceptance is not green.** The latest candidate was frozen before independent
v4 authorship. The manifest covers 254 production Python files and 16 real
Skill frontmatter files; all 270 remain byte-for-byte unchanged during this
audit. The historical wild corpus and baseline are also pinned independently.
Evidence is retained under
[`skill-routing-final-v4`](../.acceptance-work/skill-routing-final-v4/).

Measurements on the frozen candidate:

| Check | Latest frozen candidate | Meaning |
| --- | --- | --- |
| Full intent regression after performance changes | 1746/1746, 13.86 s | Confirms the previously interrupted run |
| Full Skill scope including integrations | 1205 passed, 5 failed / 1210 | 26 core files plus 13 integration files |
| Full Skill core regression | 749 passed, 5 failed / 754, 469.74 s | All 26 Skill/load/proposal test files ran to completion |
| Skill integration scope | 456/456, 62.44 s | Same-scope rerun with local socket-bind permission |
| Production catalog regression | 128 passed, 5 failed / 133 | All five core failures are in this file |
| Required-operation redteam | 170/170 | 168 language cases and two fixture-contract checks |
| Real Skill examples | 44/44 across 16 Skills | Catalog examples are regression anchors, not unseen evidence |
| Existing protected-literal regression | 22/22, 1.62 s | Covers the existing four large-payload layouts |
| Joined evaluator | 96/96 | Existing curated regression contract |
| Wild evaluator, strict / lenient | 76/110 / 89/110 | Both two successes below the preceding documented 78/91 |
| Wild solid-oracle subset, strict / lenient | 73/85 / 82/85 | Existing inspected repair corpus |
| Wild forbidden-Skill checks | 24/24 | This subset does not prove all constraint forms are correct |
| Independent v4 solid-oracle exact | 80/124 (64.52%) | Explicit request resolution, veto, and selection all match |
| Independent v4 false veto | 33/88 (37.50%) | Primary metric; includes 16 easy Skill anchors |
| Independent v4 main-workflow false veto | 33/72 (45.83%) | Removes the 16 easy anchors from the compatible denominator |
| Independent v4 missed veto | 4/36 (11.11%) | Three also wrongly selected the prohibited workflow |
| Scoped Python compilation | 65 files passed | Production, tests, and evaluator sources in the intent/Skill families |
| Repository `git diff --check` | Passed | Whitespace only |
| Scoped Ruff `E9,F` | Passed | Syntax/undefined-name hygiene |
| Scoped Ruff `E9,F,I` | 6 import findings | Three production and three test files; failed |
| Ruff 0.16.4 default profile | 281 findings | Includes 225 implicit string-concatenation findings; failed |

The five core failures are four natural-denial parameter cases and one explicit
Skill conflict: `复测不用做`, `复测就不要再做了`, `复测这一步不用再做`,
`avoiding modifications to the test files`, and
`Use testing/regression-from-bug-report then fix auth without tests`.
The first four have no required-operation veto; the last still selects the
conflicting Skill. The green 170-case redteam does not cover these failures.
See [`skill-core-failures.json`](../.acceptance-work/skill-routing-final-v4/skill-core-failures.json).
The first integration attempt had 388 passes, 46 failures, and 22 setup errors,
all caused by sandbox denial of local socket binding. The same 456 tests passed
after automatic approval of local test-server permission. Both attempts are
retained; those environmental failures are separate from the five reproducible
production-catalog failures. Scope and commands are in
[`skill-and-frozen-acceptance.md`](../.acceptance-work/skill-routing-final-v4/skill-and-frozen-acceptance.md).

The documented 78/91 per-case report was not available in this workspace, so
the exact two changed examples cannot be attributed from aggregate counts.
The current `neg-01` and `neg-05` cases visibly misinterpret Chinese double
negation and return `no_skill`; those are confirmed current failures, not a
proven identification of the two historical regressions.

Independent source review found four P1 issues, with exact outputs, source
spans, and timeout stacks in
[`source-review.md`](../.acceptance-work/skill-routing-final-v4/source-review.md):

- Single quotes, multiline code fences, and Japanese corner quotes are
  protected by the parser but can be reintroduced as live denials by typed
  reconciliation.
- Positive reminders such as `Do not forget to run tests` and
  `Never hesitate to write regression tests` are interpreted as denials.
- A later matching `RELEASED` constraint does not retire an earlier `DENIED`
  constraint because mandatory-step routing still uses `any(DENIED)`.
- A protected quote between two visible instructions leaves a large internal
  whitespace span that triggers regex backtracking. Independent probes at
  2 KB and 4 KB exceeded six seconds; 16 KB exceeded four seconds.

The four existing 16 KB layouts now measured 0.156–0.344 s in a single replay,
consistent with the reported improvement from the earlier multi-second
behavior. That improvement is layout-specific: it does not establish bounded
performance for all long quotations. No dead private definition was found by
the limited AST/reference audit of `intent_constraints.py` and
`intent_negation.py`; the router's legacy operation fallback is still reachable.

The independent v4 author receives only public behavior requirements, the
candidate manifest metadata, and actual Skill contracts. It receives no
implementation, old prompts, prior failures, or current outputs. Prompts and
oracles are sealed before execution. The real `route_skill_turn` seam uses
filesystem discovery and core Tool capability metadata, with no remote
adapters and with network connections blocked and counted. The primary metric
is false veto among author-declared solid cases expected to remain compatible;
missing selection, missed veto, unresolved explicit requests, and ambiguous
oracles are reported separately. This is independently authored synthetic
challenge evidence, not observed production traffic or model calibration.

The independent author sealed 132 handwritten prompts with shuffle seed
`260905417`: 124 solid (88 compatible, 36 required-veto) and eight predeclared
ambiguous. Sixteen solid cases are new-wording anchors for the 16 project
Skills. After sealing, a separate output-blind oracle review found no required
label corrections or additional ambiguous cases. Exact full-prompt comparison
against 878 old prompt/tail texts found zero overlap and zero duplicates within
v4. This check does not establish semantic-template or real-traffic independence.
The corpus is 68–288 characters per prompt; long-text behavior is covered by
the separate performance audit above.

Every first-pass result is retained in
[`blind-v4-first-pass.json`](../.acceptance-work/skill-routing-final-v4/blind-v4-first-pass.json),
with the sealed
[`author protocol`](../.acceptance-work/skill-routing-final-v4/author-seal.json)
and [`oracle audit`](../.acceptance-work/skill-routing-final-v4/oracle-audit.md).
No production code or oracle was changed in response to the results.

| V4 compatible category | False veto / compatible cases |
| --- | ---: |
| Historical context | 8/12 |
| Quoted content | 4/12 |
| Reported status | 4/10 |
| Double negation | 3/10 |
| Unrelated scope | 7/12 |
| Ordering and permission | 2/8 |
| Discourse correction/release | 5/8 |
| Skill anchors | 0/16 |

There were 37 missing expected selections among 88 compatible solid cases:
33 were explicit constraint vetoes and four were other selection failures.
Twelve of all 124 solid prompts lost explicit Skill-request recognition
(overlapping the preceding failures). The four missed-veto cases are
`blind-v4-080`, `-092`, `-098`, and `-104`; the first three wrongly select the
workflow, while `-104` also loses the explicit request and remains unselected.
The eight ambiguous cases are 6/8 exact and remain outside the primary metric.
All 132 cases completed without runtime errors; no network connection was
attempted. Observed routing latency was p50 0.311 s, p95 0.466 s, maximum
0.592 s on this machine, under concurrent acceptance work.

Freeze hashes:

```text
wild corpus     1022624a46db7abfacb933010bd36c4f11500bdf3969df74cf1e0c17944f8997
wild baseline   0b647d4294df24ffc6c297dda486fa67c1683b0f2869545b9177fa2fb755935e
v4 corpus       79b96c6639cb637af682e059a39e2a061f641c35ba2609726e9e5760a2f4bc8b
v4 first pass   a2ce71b71eb17691c5929ec7085e39f45f7c5d9ade8437439a9847a719aa1bd2
```

Reproducible commands and exact test scopes are retained in
[`static-scope.json`](../.acceptance-work/skill-routing-final-v4/static-scope.json),
[`skill-test-scope.json`](../.acceptance-work/skill-routing-final-v4/skill-test-scope.json),
and [`frozen-integrity.json`](../.acceptance-work/skill-routing-final-v4/frozen-integrity.json).
The v4 runner refuses to overwrite the first-pass evidence. Any repair using
these now-inspected samples must report its rerun as regression evidence and
use another fresh sealed corpus for a new blind generalization claim.

## Capability modeling follow-up — 2026-09-05

The subsequent CapabilityRegistry change is documented in
[`capability-modeling.md`](capability-modeling.md). Tool metadata now carries
source, declaration status and independent effect axes. Legacy name guesses,
MCP hints and invalid declarations do not contribute tool affinity. Read-only
affinity penalties require a declared mutation effect; unknown effects and
external read-only operations do not create that penalty or a new Skill veto.

The preceding v4 numbers remain historical acceptance evidence. This follow-up
changes Capability producers and consumers, so the previous source freeze is
not a claim about the new code. It does not repair the separately recorded
Intent/constraint defects or relabel any frozen oracle.
