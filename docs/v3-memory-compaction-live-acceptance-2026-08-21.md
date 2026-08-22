# V3 Memory and Compaction Live Acceptance

Date: 2026-08-21

## Verdict

The frozen v3 Memory/compaction suite passed on its first complete provider
execution: **17/17 cases, 20/20 Agent Turns and 94/94 declared oracles**. No
case was resumed, retried, merged or manually adjudicated into a pass.

This establishes A-grade reliability for the tested persistence and context
retention contracts. It does not establish that injected Memory makes the
Agent faster: the four single-run warm/cold comparisons all succeeded, but the
warm tasks used more tools in this sample.

## Frozen authority and result

- Suite: `minicode-memory-compaction-live-20-2026-08-21-v3`
- Manifest: `artifacts/north-star-memory-compaction-20-v3/manifest.json`
- Manifest SHA-256:
  `bf39c66b07e34cd3127bb068d587bc0ec1bf047ac1a18831df71458856a2797d`
- Result: `artifacts/north-star-memory-compaction-20-v3/full-results-initial.json`
- Result SHA-256:
  `2adb31d18a7617a57168417326ebcabbd56182c0a7057423f88a72023e9c4e7d`
- Total usage: 84 model calls, 576,240 input tokens and 10,674 output
  tokens
- Summed case duration: 130.666 seconds
- Unsafe actions: 0
- User interventions: 0

An evidence-contract check joined every result to the exact frozen case and
oracle IDs. All 17 private evidence records have `failure=null`, no oracle
failures and the expected number of Run IDs.

## Persistent Memory

### Learning-chain reliability

Both independent learning chains passed the complete contract:

1. the preregistered wrong-path `read_file` finished as a paired error;
2. a corrected-path `read_file` finished as a paired success;
3. one safe, independently verified lesson was written and automatically
   approved;
4. the next Turn retrieved and injected that exact lesson;
5. the next Turn again performed a paired successful source read and returned
   the exact marker.

The auth and runtime entries are both `approved`, use
`auto_approve_verified`, are safety-classified `safe`, and each recorded one
retrieval plus one injection. Intended warm injection passed **4/4** across
learned auth/runtime and seeded deploy/schema tasks.

### Efficacy boundary

Warm and cold functional success were both **4/4**. Tool-call differences
`warm − cold` were:

| Pair | Warm tools | Cold tools | Difference |
| --- | ---: | ---: | ---: |
| auth | 5 | 4 | +1 |
| runtime | 6 | 4 | +2 |
| deploy | 5 | 2 | +3 |
| schema | 3 | 2 | +1 |

All four warm tasks also used at least as many model calls and more input
tokens. This is one stochastic execution per condition, so it is not a causal
regression estimate. The allowed claim is: **Memory is persisted, selected,
injected and usable reliably; an efficiency advantage is not demonstrated.**

## Context compaction

- Critical marker retention: **10/10 Turns**.
- Turns with an effective compaction: **10/10**.
- Effective compaction events: 10.
- Estimated tokens freed: 45,049.
- Covered state: goal, fact, rejected approach, constraint, decision, combined
  state, large file result, loaded Skill result and two-round summary
  continuity.
- Large-file result: 7 model calls, 2 tool calls, 53,878 input tokens. The
  historical 25-model/31-tool compaction oscillation did not recur.

## Event integrity

- Tool starts: 43.
- Tool finishes: 43.
- Paired finishes: 43.
- Tool errors: 3, all followed by successful task completion. Two were the
  required learning-chain failures; one was an unnecessary read during the
  constraint-retention task.
- Canonical task outcomes: 20 success completions.
- `learningSuccess`: null for all 20 because these read-only responses have no
  independent correctness verifier. This preserves the fail-closed learning
  boundary; verified recovery still supplies separate Memory approval
  authority.

## Code verification

- Complete real-environment suite after product changes: **4,094 passed,
  2 skipped** in 265.31 seconds.
- Final focused Agent/live-runner/Memory loop: 39 passed.
- Changed production/test/script Ruff: passed.
- Changed-file compileall and diff check: passed.
- Offline deterministic quality gate: passed with no remote calls.

Repository-wide gates are not yet clean:

- `ruff check .` reports 820 existing issues, mainly in duplicated `py-src/`,
  `ts-src/` and benchmark trees outside this repair.
- Whole-worktree diff check finds trailing whitespace in the user-owned
  `.mini-code-memory/MEMORY.md` projection.
- Git state at HEAD `3b9f6f1`: 139 tracked/staged changes plus 113 untracked
  paths. The tested workspace is therefore not release-reproducible from HEAD.

## Tooling boundary

`scripts/analyze_memory_compaction_north_star.py` remains a legacy v1
analysis utility: it requires old fixed result/addendum paths and hard-codes
historical failure prose. It was not used as v3 authority. The metrics in this
report were extracted directly from the frozen manifest, the matching result
document and Run Journals.

## Promotion judgment

- Persistence and lesson activation reliability: **A** for the tested scope.
- Context-compaction retention and stability: **A** for the tested scope.
- Demonstrated Memory efficiency benefit: **B / inconclusive**.
- Release reproducibility: **not ready** because the worktree is not clean or
  reconstructable from the current Git revision.

The next highest-value work is a repeated-seed paired Memory experiment plus a
small, safety-preserving utilization prompt change, followed by modernization
of the analysis script and Git/release cleanup.
