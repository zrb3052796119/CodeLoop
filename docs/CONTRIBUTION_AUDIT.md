# CodeLoop Contribution Audit

This is the evidence map behind the shorter portfolio narrative in the
[English README](../README.md), [Chinese README](../README.zh-CN.md), and
[lineage boundary](../CONTRIBUTIONS.md). It prevents one well-instrumented
Memory case from being mistaken for the scope of the entire project.

> 中文结论：CodeLoop 的后续工作不是“只改了持久化记忆和多 Agent”。可审计的
> 四条并列主线是：**Skill 多层路由、上下文保真、多 Agent Runtime、持久化经验**；
> 评测/配置/跨平台可靠性是贯穿四条主线的第五层工程支撑。

## Audit Boundary

The first repository commit,
[`3036dd7`](https://github.com/zrb3052796119/CodeLoop/commit/3036dd76e4ca676541a79a64dc6d24ec20baf433),
is the **observable imported baseline**. It already mentions security hardening
and a dashboard, and it does not record an exact upstream revision. Therefore:

- “present after `3036dd7`” is evidence of post-import repository work;
- it is not proof that every post-import line was designed without upstream or
  AI assistance;
- components already present in `3036dd7` remain inherited even when later
  changes are large;
- changed-line counts are supporting context, not an ownership percentage.

## Contribution Map

| Track | Imported foundation | Material post-baseline work | Classification | Primary evidence |
| --- | --- | --- | --- | --- |
| Skill routing | Regex intent parser; scoped catalog; directory → Skill scorer; keyword/entity/domain/scope/tool/source signals; top-k metadata; `load_skill`. | Real abstention; bilingual intent and aliases; optional Qwen/OpenAI-compatible embeddings; strict explicit invocation and load-before-final; digest-bound snapshot loading; loaded-Skill attribution; bounded cross-Run reranking; frozen adversarial gate. | Inherited foundation; decision/load/feedback paths materially rebuilt. | 60/60 offline routing gate; router, semantics, explicit grammar, loader, evidence, and feedback tests. |
| Context fidelity | Layered `ContextCompactor`; separate `ContextManager` algorithm; 85%/95% thresholds; reactive overflow; three-failure breaker. | Summary-of-summary; compression-immune typed task ledger; tool-use/result and assistant-turn atomicity; latest-instruction reinsertion; canonical single path; usage-assisted calibration; unchanged-state retry identity; forced-path breaker compliance. | Rebuilt over inherited compaction skeleton. | 12/12 repeated-compaction gate; continuity, task-ledger, single-path, and provider-turn tests. |
| Multi-Agent runtime | Synchronous `explore / plan / general` nested task tool with isolated history, tool filtering, and plain-text result. | Async `spawn / poll / cancel` for read-only `explore/plan`; depth and concurrency containment; parent-bound structured v1 envelope; stable `subagentId` joins; shared call/token/cost accounting; deadlines; per-role child-model routing; isolated synchronous Workflow. | Extended foundation plus added lifecycle/protocol modules. | Lifecycle, structured-protocol, journal, budget, deadline, routing, workflow-transaction, and isolation tests. |
| Persistent Memory | Storage, reflection/recovery synthesis, sanitizer, approval, lexical retrieval/reranking, content hashes, and rendered-entry feedback. | Broader operational recovery; exact structured recovery evidence; corroborated/idempotent feedback; projection hygiene and quarantine; canonical evidence-gated hybrid retrieval; privacy gates; rendered-ID acceptance attribution; V1–V5 deterministic contracts. | Extended and hardened. | 48 path pairs, 36 non-path pairs, V1–V5 contracts with V5 live/provider execution pending, Memory regression matrix. |
| Evaluation and reliability | CLI/TUI/dashboard/permissions/RunJournal and a 3-OS × 2-Python CI matrix. | Hash-bound `current`/`a` profiles; sealed fixtures/manifests; external oracles; global credential ownership; privacy-safe public projections; Windows storage/path/atomic-replace fixes; deadline and race-contract hardening; UI/CLI integration work. | Added evaluation/config paths; hardened inherited surfaces and matrix. | Full regression suite, deterministic profiles, package smoke, cross-platform CI. |

## 1. Skill Routing Is a Full Contribution Track

### Current layered decision path

```text
scoped discovery
  → intent / action / entities / bilingual keywords
  → directory + capability context
  → query-specific lexical / entity evidence
  → always-on Chinese-English alias evidence
  → optional thresholded Qwen-compatible embedding evidence
  → strict explicit-invocation authority or evidence admission
  → weak-signal / margin / top-k control
  → bounded historical-evidence reranking
  → digest-bound load_skill
  → loaded-Skill attribution
```

The important separation is **admission versus ranking authority**:

- broad intent, directory, capability, tool, and source scores may reorder a
  candidate but cannot independently admit it;
- an `UNKNOWN` query has a stricter semantic gate;
- weak inferred evidence is limited to one suggestion;
- an explicit `$skill` or supported English/Chinese invocation outranks
  inference, while negation and ordinary word collisions do not;
- cross-Run evidence applies only to candidates admitted without it and cannot
  defeat abstention or explicit user authority;
- `load_skill` rechecks the discovered source, canonical path, and SHA-256
  digest, so a routed old version cannot silently load a changed file.

When the router abstains, `selected=[]`. The prompt still exposes a bounded
**name-only inventory** so the model knows Skills exist; it does not inject rich
candidate descriptions/tools. This is precise abstention, not total catalog
absence.

### Code, tests, and commits

| Evidence type | References |
| --- | --- |
| Production code | `minicode/intent_parser.py`, `minicode/skill_router.py`, `minicode/skill_semantics.py`, `minicode/skill_evidence.py`, `minicode/skill_feedback.py`, `minicode/skills.py`, `minicode/tools/load_skill.py`, enforcement in `minicode/agent_loop.py`. |
| Focused tests | `tests/test_skill_router.py`, `tests/test_skill_semantics.py`, `tests/test_skill_explicit_reference_grammar.py`, `tests/test_skill_evidence_ledger.py`, `tests/test_skill_routing_feedback.py`, `tests/test_skills.py`. |
| Frozen evaluation | `tests/fixtures/agent_quality/skill-routing.json`, evaluated by `scripts/agent_quality_evaluator.py`. It contains 40 positive/explicit and 20 abstention/adversarial cases. |
| Representative commits | [`d1fa2e2`](https://github.com/zrb3052796119/CodeLoop/commit/d1fa2e2): abstention, digest attribution, evidence/version ledgers. [`f2772f7`](https://github.com/zrb3052796119/CodeLoop/commit/f2772f7): bilingual semantics, embedding route, explicit contract, bounded live feedback, 60-case gate. [`ae6c646`](https://github.com/zrb3052796119/CodeLoop/commit/ae6c646): embedding/config/runtime hardening. |

The checked-in deterministic result is 60/60 with top-1, abstention, and exact
required-Skill rates of 1.0, forbidden-selection rate 0.0, and
`remoteCallCount=0`. This is not a live Qwen benchmark. The optional remote
path uses OpenAI-compatible transport tests, cache/degradation tests, and a
separate smoke script; Qwen here means the embedding route, not the child-agent
chat model.

## 2. Context Fidelity Is More Than “Has Compression”

The baseline already had sophisticated layered compaction and a breaker, but
two compression implementations could disagree and repeated compression could
lose older summaries. The post-baseline work changed the reliability contract:

- previous summaries are fed into the next summarization round as bounded,
  lower-authority context;
- a parent-owned `TaskLedger` keeps a bounded goal, explicit constraints,
  typed verification facts, and failed-tool error codes outside lossy cycles;
- tool calls/results are matched by ID and provider-native multi-tool assistant
  turns remain atomic;
- the latest user instruction is reinserted verbatim;
- `ContextManager` delegates to the canonical compactor and shares the 85%
  threshold instead of maintaining a second 95% algorithm;
- observed token usage feeds a bounded EMA when available; it is usage-assisted,
  not guaranteed provider-exact accounting;
- retry identity includes message state, so the same failed state cannot spin
  while materially changed state may recover.

Representative code: `minicode/context_compactor.py`,
`minicode/context_manager.py`, `minicode/task_ledger.py`,
`minicode/conversation.py`, and `minicode/cybernetic_orchestrator.py`.

Representative tests: `tests/test_compaction_summary_continuity.py`,
`tests/test_task_ledger.py`, `tests/test_context_compaction_single_path.py`, and
the provider-turn/tool-pair cases in `tests/test_context_compactor.py`.

The frozen compaction profile passes 12/12 forced one-to-five-round cases for
summary chain—including a rejected-approach sentinel—ledger sentinels, latest
user instruction, loaded Skill content, tool-pair integrity, and non-negative
savings. The rejected approach lives in the summary-fidelity contract, not an
inferred task-ledger plan. The gate proves these contracts on the fixture, not
zero semantic drift in every arbitrary long conversation.

## 3. Multi-Agent Work Covers Lifecycle, Protocol, and Routing

The baseline had synchronous nested tasks. The post-baseline contribution is a
bounded runtime around them:

- `explore` and `plan` may use a Turn-scoped read-only
  `spawn / poll / cancel` lifecycle with job limits, ownership checks,
  idempotent cancellation, and a finalization barrier;
- recursive task delegation is constrained and read-only work may execute in
  parallel while write-capable General work remains serialized;
- results use a parent-bound v1 `summary / files / risks / verification`
  envelope; malformed evidence degrades to inconclusive rather than being
  trusted;
- stable `subagentId` values join the parent ToolResult, content-free
  `subagent.completed` event, and detailed sidecar;
- parent and children share model-call, token, and cost admission/accounting;
- Task deadlines propagate to built-in provider socket timeouts and retry
  backoff;
- explicit per-role OpenAI-compatible child routes use isolated credentials;
  invalid enabled routes fail closed, while an unconfigured route inherits the
  parent for compatibility;
- Workflow is a synchronous, disposable-snapshot Plan → Execute → Review
  transaction with a strict versioned verdict, not a persistent team.

Representative code: `minicode/tools/task.py`,
`minicode/subagent_lifecycle.py`, `minicode/subagent_result.py`,
`minicode/subagent_observation.py`, `minicode/subagent_journal.py`,
`minicode/agent_budget.py`, `minicode/model_call_control.py`, and
`minicode/subagent_model_routing.py`.

Representative tests: `tests/test_subagent_lifecycle.py`,
`tests/test_subagent_structured_protocol.py`,
`tests/test_subagent_run_journal.py`, `tests/test_subagent_model_routing.py`,
`tests/test_sub_agent_isolation.py`,
`tests/test_workflow_workspace_transaction.py`, and
`tests/test_production_reliability_hardening.py`.

Boundaries: asynchronous lifecycle is read-only; cancellation is cooperative
thread cancellation, not process isolation; accounting is not an absolute
provider-spend hard limit; `qwen3.6-flash` is a configuration example, while
the published live acceptance used `qwen3.7-plus` and `qwen3.7-max`.

## 4. Persistent Memory Is an Evidence-Controlled Loop

The Memory track is the best quantified, but it is one of the four tracks.
Post-baseline changes focus on exact evidence and authority:

```text
failed operation → corrected operation → policy-qualified recovery
  → sanitized / approved claim → canonical retrieval
  → exact rendered-entry IDs → corroborated feedback or quarantine
```

The strongest current experiment is the 48-pair synthetic path-recovery study:
reuse-stage tool calls were 50 versus 240 and task input tokens were 652,911
versus 1,539,738. The 36-pair non-path study had smaller and
category-dependent effects. These results support controlled task-family
claims, not universal coding-productivity claims.

Representative code: `minicode/reflection_evidence.py`,
`minicode/reflection_synthesis.py`, `minicode/memory.py`,
`minicode/memory_pipeline.py`, `minicode/memory_hybrid.py`, and
`minicode/memory_hybrid_runtime.py`.

Representative evidence: the
[three-minute vertical slice](./PORTFOLIO_CASE_STUDY.en.md),
[48-pair report](./2026-08-21--persistent-memory-large-study--r1--robustness-check.md),
[36-pair report](./2026-08-22--non-path-persistent-memory--r1--robustness-check.md),
and [V1–V5 contract history; V5 live pending](./persistent-memory-repair-acceptance-2026-08-23.md).

## 5. Cross-Cutting Evaluation and Reliability

The imported repository already had a CLI, TUI, dashboard, permission flow,
RunJournal, and Linux/macOS/Windows × Python 3.11/3.12 CI. Safe attribution is
“redesigned, integrated, and hardened,” not “built from scratch.”

Post-baseline work includes:

- deterministic `current` and internal `a` profiles with hash-bound fixtures,
  manifests, and fail-closed result validation;
- frozen Skill and compaction contracts plus 50 recorded provider/runtime
  tasks across 10 categories and 30 workspace-writing cases;
- global credential ownership that prevents a target repository's `.env` from
  redirecting primary, embedding, or child-model endpoints/keys;
- privacy-safe public projections separated from ignored raw journals,
  temporary workspaces, and local Memory;
- Windows lock, path, atomic-replace, command-length, SSE-home, and
  deterministic timeout/race fixes that made the inherited CI matrix actually
  stable for the expanded runtime;
- CLI/TUI/dashboard presentation and sub-agent progress integration.

The 50-task evidence was generated by real agent/runtime calls against isolated
synthetic repositories. CI re-evaluates the sealed recording offline; it does
not rerun the provider. The published A evidence transparently joins 49
retained results with one independently adjudicated rerun rather than claiming
one fresh, monolithic 50-task rerun.

## Portfolio-Safe Summary

> I extended an imported Python coding-agent runtime along four peer tracks:
> a bilingual, abstaining, digest-bound multi-layer Skill route with bounded
> cross-Run ranking feedback; long-context fidelity and a compression-immune
> task ledger; bounded sub-agent lifecycle, result, budget, deadline, and model
> routing contracts; and an evidence-controlled persistent-learning loop. I
> tied them to deterministic adversarial gates, controlled provider studies,
> and a hardened cross-platform CI matrix while keeping inherited foundations
> and experimental limits explicit.

Do not reduce this to “a Memory RAG project,” but also do not claim a
from-scratch coding agent, live-Qwen CI benchmark, persistent process-isolated
agent team, externally certified A grade, or universal 57%–79% productivity
gain.
