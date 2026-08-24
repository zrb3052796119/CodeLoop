# CodeLoop Contribution and Lineage Boundary

This document separates the imported MiniCode Python baseline from the work
performed in CodeLoop. It exists so that a reviewer, collaborator, or
interviewer can evaluate the project without confusing “present in this
repository” with “authored from scratch here.”

> 中文摘要：CodeLoop 是 MiniCode Python 的深度衍生项目。初始版本已经拥有主
> Agent Loop、工具、TUI、上下文压缩、Memory、Skill 路由和同步子 Agent。
> 初始 Memory 也已经具备通用恢复合成、sanitizer、内容哈希审批和渲染 ID
> 反馈。后续主要工作是强化反馈/隔离/检索边界，修复多轮压缩保真，扩展有边界
> 协作，并建立可复现实验。下表给出可从 Git 历史和测试中核对的边界。

## Classification Method

The repository's first commit,
[`3036dd7`](https://github.com/zrb3052796119/CodeLoop/commit/3036dd76e4ca676541a79a64dc6d24ec20baf433),
is treated as the **observable imported baseline**. This is a repository
lineage boundary, not a claim that the commit is byte-for-byte identical to a
particular upstream revision: its message already mentions security hardening
and a dashboard.

The labels below mean:

- **Inherited** — substantial implementation was already present in the first
  imported commit.
- **Extended** — the baseline abstraction remains, while CodeLoop added a
  material execution path, contract, or safety boundary.
- **Rebuilt** — the public purpose remains related to the baseline, but its
  control/data flow was materially reworked and covered by new evidence.
- **Added** — no equivalent production module or connected runtime path was
  present in the imported baseline.

This classification is deliberately more conservative than counting lines or
files changed.

## Portfolio Ownership Snapshot

| Field | Auditable statement |
| --- | --- |
| Maintainer | `git shortlog -sne 3036dd7..HEAD` reports one primary post-import Git author, `zhourunbo`. Several commits include AI `Co-Authored-By` trailers, so this is not presented as proof of solo work. |
| Project period | 2026-07-27 to present, based on commit dates. |
| Personal scope | The post-import engineering and evaluation work classified below; no percentage-of-upstream or lines-owned claim is made. |
| Upstream precision | The imported baseline did not record a source revision, so an exact upstream commit-to-commit delta cannot be reconstructed from this Git history. |
| Product status | Engineering/research prototype with a usable local CLI; not an OS-sandboxed or externally benchmarked production agent. |

Representative, reviewable commits through the current release:

| Commit | Change represented |
| --- | --- |
| [`d1fa2e2`](https://github.com/zrb3052796119/CodeLoop/commit/d1fa2e2) | Memory corroboration channels and fixes found through real agent use. |
| [`4290b7c`](https://github.com/zrb3052796119/CodeLoop/commit/4290b7c) | Sub-agent containment, capability boundaries, and visibility. |
| [`b43a0d8`](https://github.com/zrb3052796119/CodeLoop/commit/b43a0d8) | Parallel read-only sub-agents and streamed progress. |
| [`df0482e`](https://github.com/zrb3052796119/CodeLoop/commit/df0482e) + [`fb6f3a8`](https://github.com/zrb3052796119/CodeLoop/commit/fb6f3a8) | Memory workspace and revocation/persistence boundaries. |
| [`3b9f6f1`](https://github.com/zrb3052796119/CodeLoop/commit/3b9f6f1) | Frozen quality-promotion gates. |
| [`6c65df8`](https://github.com/zrb3052796119/CodeLoop/commit/6c65df8) | Bounded recovery loops and stopping conditions. |
| [`f2772f7`](https://github.com/zrb3052796119/CodeLoop/commit/f2772f7) | A-profile runtime integrations across Memory, compaction, Skill, and agents. |
| [`4d61d0a`](https://github.com/zrb3052796119/CodeLoop/commit/4d61d0a) | Paired Memory acceptance studies and their reports. |
| [`ae6c646`](https://github.com/zrb3052796119/CodeLoop/commit/ae6c646) | Evidence-driven Runtime hardening, acceptance contracts, and release tests. |
| [`e1a4b17`](https://github.com/zrb3052796119/CodeLoop/commit/e1a4b17) | Portable persistence locks and Windows-safe storage/read/observation paths. |

These commits establish the post-import repository work. They do not repair the
missing exact upstream revision or licensing grant; those remain explicit
limitations rather than facts inferred from authorship.

## Inherited Runtime Foundation

The following capabilities existed at the imported baseline and should be
credited to MiniCode Python and its contributors.

| Capability already present | Representative baseline paths | CodeLoop usage |
| --- | --- | --- |
| Main provider/tool loop | `minicode/agent_loop.py`, Anthropic/OpenAI adapters | Retained as the central execution loop. |
| Local coding tools and permission flow | `minicode/tools/`, approval and command handling | Retained, then hardened at selected boundaries. |
| Terminal UI, sessions, gateway/dashboard | `minicode/tui/`, session and gateway modules | Retained and integrated with newer events. |
| Cybernetic orchestration/controllers | `minicode/cybernetic_orchestrator.py`, context/cost/progress controllers | Retained; not claimed as a new CodeLoop invention. |
| Context compaction and 3-failure breaker | `minicode/context_compactor.py`, `context_manager.py` | Retained, then rebuilt in specific repeated-compression and forced-recovery paths. |
| Persistent Memory, generic recovery synthesis, sanitizer, content-hash approval, and rendered-ID feedback | `minicode/memory.py`, `memory_pipeline.py`, reflection/approval/retrieval/reranker modules | Retained, then hardened and connected to new feedback/retrieval/evaluation paths. |
| Skill discovery/routing | `minicode/skill_router.py`, `skills.py`, `load_skill` | Extended with semantic and cross-run evidence paths. |
| Synchronous task/sub-agent tool | task tool and nested agent loop | Extended with lifecycle, result, budget, and routing contracts. |
| 3-OS × 2-Python CI matrix | `.github/workflows/ci.yml` | Retained and extended with deterministic quality-gate execution. |

## CodeLoop Work After the Baseline

### 1. Persistent Memory: from storage to an evidence-controlled loop

**Classification: extended + hardened.**

CodeLoop connects the full cycle:

```text
structured failure
  → corrected action
  → successful corrected-tool result
  → targeted recovery classification
  → recovery claim
  → safety/approval decision
  → persistent entry
  → canonical retrieval
  → exact rendered-entry attribution
  → positive/negative feedback or quarantine
```

Material work includes:

- broader operational recovery synthesis, recovery suggestions, root-cause
  summaries, and stop-condition handling on top of the inherited generic
  reflection path;
- hardening of the inherited sanitizer/content-hash/approval lifecycle with
  persistence-boundary checks and projection hygiene;
- corroborated user-correction and independent-verification feedback,
  idempotent observation receipts, automatic downgrade/rejection, and
  quarantine, restricted to entries actually rendered for the turn;
- canonical hybrid retrieval with promotion evidence and an explicit privacy
  gate for remote Memory embeddings (an optional LLM verifier/challenger has a
  separate data-egress boundary);
- V1–V5 frozen manifests and acceptance contracts that preserve failed
  attempts instead of silently replacing them.

In the featured `auth-policy` case, “verified” in
`auto_approve_verified` means that the Runtime classified a successful
corrected `read_file` as targeted tool-recovery evidence. No independent test
command ran inside that learning Run; the experiment's external marker/tool
oracle was evaluated after the Run. The
[sanitized attribution artifact](./artifacts/persistent-memory-large-study-v3/auth-policy-attribution.json)
makes this distinction machine-readable.

Representative production paths:

- `minicode/reflection_evidence.py`
- `minicode/reflection_synthesis.py`
- `minicode/memory.py`
- `minicode/memory_approval.py`
- `minicode/memory_pipeline.py`
- `minicode/memory_hybrid.py`
- `minicode/memory_hybrid_runtime.py`

Representative evidence:

- [3-minute end-to-end case](./docs/PORTFOLIO_CASE_STUDY.en.md)
- [48-pair path-recovery study](./docs/2026-08-21--persistent-memory-large-study--r1--robustness-check.md)
- [36-pair non-path study](./docs/2026-08-22--non-path-persistent-memory--r1--robustness-check.md)
- [Final repair acceptance](./docs/persistent-memory-repair-acceptance-2026-08-23.md)

### 2. Context fidelity and bounded recovery

**Classification: rebuilt.**

The baseline already compacted context and had a three-failure circuit breaker.
CodeLoop addressed long-session
failure modes that only appear across repeated compression or recovery:

- a new summary includes the previous summary instead of breaking the history
  chain;
- a parent-owned task ledger keeps a bounded goal, explicit constraints, typed
  verification facts, and failed-tool error codes outside lossy compression;
- provider-native tool-call/result turns remain atomic;
- the most recent user instruction is reinserted verbatim;
- provider-reported usage calibrates token estimation when available;
- unchanged failing states are deduplicated and forced paths now honor the
  inherited breaker; materially changed states may retry;
- compaction failures become privacy-bounded structured Run observations.

Representative paths: `minicode/context_compactor.py`,
`minicode/context_manager.py`, `minicode/conversation.py`, and
`minicode/cybernetic_orchestrator.py`.

Evidence: [quality-gate contract](./docs/agent-quality-gates.md) and
[large-file repair replay](./docs/north-star-memory-compaction-repairs-2026-08-21.md).

### 3. Multi-agent lifecycle, result protocol, and model routing

**Classification: extended + added.**

The baseline had a synchronous nested task tool. CodeLoop added:

- asynchronous `spawn / poll / cancel` for read-only `explore` and `plan`
  roles;
- cooperative cancellation, deadlines, bounded mailbox/result size, nesting
  limits, and a parent/child shared turn budget;
- a general structured result contract with `summary`, `files`, `risks`, and
  `verification` fields;
- stable `subagentId` values on completion events so journals, results, and
  parent work can be joined;
- role-specific OpenAI-compatible model routing with isolated credentials and
  fail-closed fallback behavior;
- versioned workflow-review verdicts that become inconclusive when malformed.

Important boundary: asynchronous lifecycle is not a process supervisor. It is
read-only and cancellation is cooperative; a Python thread already blocked in
a provider socket cannot be forcibly killed.

Representative paths: task/sub-agent tooling, `minicode/subagent_result.py`,
`minicode/subagent_model_routing.py`, and `minicode/run_events.py`.

Evidence: [sub-agent model routing](./docs/subagent-model-routing.md) and
[live routing acceptance](./docs/model-routing-live-acceptance-2026-08-23.md).

### 4. Skill evidence feedback

**Classification: extended.**

The imported baseline already routed Skills. CodeLoop connected cross-run
evidence to live ranking while bounding its authority:

- optional remote embedding signals with local alias fallback;
- evidence qualified by Skill source, directory, content digest, intent type,
  and action type;
- minimum sample/confidence requirements and capped rank deltas;
- explicit-name load-before-final contract;
- audit records and rollback controls; no automatic Skill-body rewrite or
  version promotion.

Representative paths: `minicode/skill_router.py`,
`minicode/skill_evidence.py`, `minicode/skill_semantics.py`, and Skill version
handling. Evidence: [Skill routing feedback](./docs/skill-routing-feedback.md)
and the sealed 60-case routing fixture used by the quality gate.

### 5. Configuration, privacy, and evaluation discipline

**Classification: added + extended.**

CodeLoop also adds the less visible release engineering needed to make the
runtime inspectable:

- a global credential file with process-environment precedence and a migration
  path from legacy settings;
- refusal to let a target project's `.env` redirect primary, embedding, or
  child-model credentials;
- privacy-bounded logs and public-artifact separation from raw journals,
  temporary workspaces, and local Memory;
- deterministic `current` and `a` quality profiles with hash-bound fixtures and
  manifest; `current` also pins the recorded result hash, while `a`
  intentionally accepts fresh results that join to the same manifest;
- external oracles, per-case telemetry contracts, and adversarial integrity
  tests for frozen acceptance evidence;
- a deterministic quality gate added to the inherited Linux/macOS/Windows and
  Python 3.11/3.12 CI matrix.

Representative paths: `minicode/config.py`, `minicode/config_migration.py`,
`minicode/env_file.py`, `minicode/logging_config.py`, `.env.example`,
`scripts/evaluate_agent_quality.py`, and `.github/workflows/ci.yml`.

## Claims That Are Supported

The following wording matches checked-in evidence:

- “I extended an existing Python coding-agent runtime with an evidence-gated
  persistent-learning loop, long-context fidelity controls, bounded sub-agent
  lifecycle, and reproducible acceptance gates.”
- “In a controlled 48-pair synthetic path-recovery study, relevant approved
  Memory reduced repository tool calls by 79.2% and task input tokens by 57.6%.”
- “Non-path lessons showed category-dependent results: command recovery and
  verification rules improved, while abstract project constraints did not.”
- “The internal A profile passes a deterministic offline gate against a
  hash-bound fixture/manifest contract; it is not a third-party grade.”

## Claims That Are Not Supported

Do not describe the project as:

- a coding agent built entirely from scratch;
- proven to reduce all coding-task cost by 57%–79%;
- production-safe or OS-sandboxed;
- a fully asynchronous process-isolated multi-agent platform;
- generally superior to another agent based on the internal A profile;
- licensed under MIT/Apache unless a valid root license and upstream reuse
  basis are established.

## How to Audit the Boundary Locally

```bash
# Inspect the imported baseline.
git show --stat 3036dd76e4ca676541a79a64dc6d24ec20baf433

# Inspect all changes after that observable boundary.
git diff --stat 3036dd76e4ca676541a79a64dc6d24ec20baf433..HEAD

# Inspect one subsystem rather than relying on this narrative.
git log --oneline -- minicode/memory.py minicode/reflection_synthesis.py
git log --oneline -- minicode/context_compactor.py
git log --oneline -- minicode/subagent_model_routing.py
git log --oneline -- minicode/skill_evidence.py
```

For behavior rather than authorship, run:

```bash
python scripts/evaluate_agent_quality.py --profile a
python -m pytest -q
```

## Upstream and Licensing Note

- Python upstream: [QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python)
- Related TypeScript project: [LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)

At the time this boundary was documented, this repository had no root license
file, and the inspected Python upstream did not expose one either. A public
repository is not itself a license grant. This document provides attribution
and engineering lineage; it does not create redistribution or commercial-use
rights. Confirm those terms before reuse.
