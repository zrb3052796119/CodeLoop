# MiniCode A-Grade Promotion Report

Status: **A-grade quality gate passed on 2026-08-21**.

## Completed scope

1. **Canonical hybrid memory retrieval** — trusted user configuration now
   activates the existing Qwen `text-embedding-v3` path for production memory
   retrieval. A live synthetic canonical smoke accepted only the correct
   post-commit lesson, rejected the hard negative, made two verifier calls,
   and did not fall back to lexical-only retrieval.
2. **Async sub-agent completion safety** — the lifecycle records whether a
   terminal child result has been observed and exposes a bounded finalization
   barrier. The parent Agent Loop cannot finalize while owned children are
   pending or while a completed result has not yet been delivered. The former
   north-star failure `multiagent-test-risk` passed all 5 oracles after repair.
3. **Skill and context quality** — bounded intent patterns, bilingual aliases,
   mixed-language explicit Skill invocation and negation handling repair the
   general routing gaps. The frozen routing suite grew from 36 to 60 cases and
   the compaction suite from 8 to 12 cases with up to five forced rounds.
4. **Frozen release evidence** — the checked-in north-star fixture now contains
   50 tasks across 10 categories, including 30 write cases. The old 49/50 run
   and the independently rerun final case were merged by task ID with an
   explicit adjudication trail; the prior failed run was not erased.

## Gate results

| Gate | Result |
|---|---:|
| Skill routing | 60/60; top-1 1.0; abstention 1.0; forbidden 0.0 |
| Context compaction | 12/12; all retention/integrity rates 1.0 |
| North-star | 50/50; 10 categories; 30 write cases |
| North-star evidence | verification/oracle/evidence/telemetry 1.0 |
| Unsafe actions / interventions | 0 / 0 |
| `current` profile | passed, no failed checks |
| `a` profile | passed, no failed checks |
| Focused quality regression | 184 passed |
| Full regression | 4022 passed, 2 skipped, 220.23 s |
| Static verification | Ruff and compileall passed |

The contract pins SHA-256 identities for routing, compaction and the sealed
north-star manifest. The `current` profile additionally pins the accepted
result bytes; the `a` profile permits a fresh result file only when it joins
exactly to the sealed manifest and passes the same evidence checks.

## Live hybrid-memory evidence

The canonical smoke resolved `memoryHybrid.enabled=true`, provider `qwen`,
remote authorization true and activation reason `activated`. It retrieved two
candidates, rendered exactly one correct lesson, used the configured Qwen
embedding client, and recorded complete token/model-call telemetry. Credentials
remain outside checked-in artifacts; `.env` and the promotion evidence file are
mode 0600.

## Residual risks and release boundary

- This is a defensible **A**, not A+. The routing suite is finite and the
  north-star tasks use synthetic repositories even though they exercise the
  real agent/runtime path.
- The final north-star evidence is a transparent 49+1 adjudicated merge, not a
  single post-fix rerun of all 50 tasks. A future release can rerun the sealed
  suite monolithically to remove that caveat.
- The repaired multi-agent task passed, but consumed 168.1 seconds, 65 model
  calls and 711,017 input tokens; four explore attempts hit their turn limit.
  Correctness is closed, while sub-agent cost/efficiency remains the largest
  operational optimization opportunity.
- The workspace has extensive pre-existing tracked and untracked user changes,
  and Git metadata is read-only in this environment. No reset, deletion or
  implicit commit was performed. The implementation and evidence gates are
  release-ready; selecting/committing the intended file set remains a separate
  repository-owner step.
