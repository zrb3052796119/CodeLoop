# Measurement round (2026-09-24)

Evidence for the two shipped capabilities — sub-agent continuation and the
explore turn budget — plus the plumbing that makes future usage measurable
from journals.

## 1. Continuation savings (deterministic, offline)

`python scripts/evaluate_continuation_savings.py`

Scenario: an explore sub-agent maps a small repo (file_tree → grep → read →
summary = 3 tool calls / 4 model turns), then the same follow-up
("Which function issues the invoice?") is asked twice — once by continuing
the stored conversation, once by a fresh explore that must redo the survey.
Numbers come from the real task-tool result headers (production accounting
path).

Result (`artifacts/continuation-savings-2026-09-24/report.json`):

| Path | model turns | tool calls |
| --- | --- | --- |
| initial survey | 4 | 3 |
| follow-up via continuation | 1 | 0 |
| follow-up via fresh explore | 4 | 3 |

**Savings for the follow-up: 100% of tool calls, 75% of model turns.**
Deterministic (scripted model), so this is reproducible byte-for-byte; it
bounds what continuation can save per follow-up, not a live average.

Accounting fix shipped alongside: a continuation run's header used to
re-count the inherited conversation's tool calls (a falsy-zero fallback
bug); continuation runs now trust the event sink, which bills only this
run's calls.

## 2. Adoption measurability

Continuation runs are now distinguishable, content-free, in both journals
and the parent's observation stream:

- journal `start` records carry `continuedFromSubagentId` (canonical
  `sub_…` id only; anything else fails the read);
- observation payloads gained **v5** = v4 + `continuedFromSubagentId`
  (v1–v4 shapes untouched, so stored journals still normalize);
- `SubagentRunSummary.continued_from` exposes it to readers.

`python scripts/analyze_continuation_adoption.py <run_dir>…` reports, over
read-only runs: total, continuation count, adoption rate, and mean model
turns / tool calls for continuation vs non-continuation runs. Adoption
accumulates as you use the agent normally; re-run the analyzer whenever
you want the number.

## 3. Explore turn-count A/B harness

`python scripts/run_explore_turn_ab.py --turns 8,12 --live`

- Frozen case set: `tests/fixtures/explore_turn_ab_cases.json` (10 real
  exploration asks, including enumeration-heavy ones).
- Per case × turn cap: runs the real task tool with
  `MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS` set, records ok /
  contractStatus / partial / modelTurns / toolCalls, and summarizes
  success / fallback / partial rates plus means per configuration.
- `--scripted` (default) validates the pipeline without network; the full
  scripted self-check passes on all 10 cases at both caps.
- `--live` **spends API quota**. Optional `--limit N` and `--repeats N`.

**Live smoke (2026-09-24, 1 case at cap 8): pipeline verified end to end —
the request reached the provider and the refusal was captured cleanly in
the report — but execution was denied by the provider ("Access to model
denied… make sure you are eligible"), i.e. an account-entitlement issue on
the configured sub-agent route, not a harness defect. The full 8v12 A/B
should be run once the route's model access is restored.**

## Interpretation guide

- The savings benchmark bounds per-follow-up savings; live averages depend
  on how often follow-ups occur (the analyzer's adoption rate).
- For 8v12: decide on `successRate` / `partialRate` deltas at equal
  `contractStatus` distribution; a cap is safe to lower only when
  `partialRate` at 8 is ~0 across the frozen set and `meanModelTurns`
  meaningfully drops. Do not compare a `fallbackRate` between scripted and
  live runs — scripted explorers never write the typed note contract.

## 4. Live 8v12 results (2026-09-25, eligibility restored)

`python scripts/run_explore_turn_ab.py --turns 8,12 --live --repeats 1`
(report: `artifacts/explore-turn-ab-live-2026-09-25/report.json`; 10 frozen
cases × 2 caps on the Aliyun sub-agent route):

| cap | success | partial | meanModelTurns | meanToolCalls |
| --- | --- | --- | --- | --- |
| 8 | 1.0 | 0.2 | 5.4 | 6.6 |
| 12 | 1.0 | 0.1 | 5.9 | 7.3 |

**Decision per the pre-registered criterion: keep 12 as the default.** At
cap 8, two cases (`concurrency-usage`, `schema-drift`) needed 9 turns and
soft-landed partial; the criterion required ~0 partial at 8. Most cases
finish well under either cap (means 5.4–5.9), so the 12 ceiling rarely
binds — the cost of keeping it is small, the cost of trimming mid-survey
surprises is not.

Honest caveats: single run per cell, live step counts vary run-to-run
(`map-module` took 4 turns under cap 8 and 7 under cap 12 on the same
prompt), and `modelTurns` counts admitted provider calls, so in-step
retries can push a case past its step cap (a soft-landed row showing 9
turns against a cap of 8 is expected accounting, not a bug).

**Follow-up finding (independent of turn count):** every live run returned
`contractStatus: fallback` — **CORRECTION 2026-09-25 (later same day): this
measurement was partially a harness artifact.** The A/B harness called
`task_tool.run` directly without attaching the turn's `SubagentMailbox`, so
the typed-result instruction was never delivered to the sub-agent at all
and the closing reminder could not fire. The harness now attaches a
mailbox (matching the production loop).

## 5. Contract-compliance fix and corrected live verification (2026-09-25)

Two changes shipped (`tests/test_subagent_contract_compliance.py`, 6
tests):

1. the typed-result instruction is now imperative ("MANDATORY FINAL STEP…
   non-compliant"), with the JSON shape block byte-identical;
2. after a successful non-partial run whose note is missing, the task tool
   runs **one bounded closing reminder** (≤2 extra steps, fresh outcome
   capture, same budget/journal/sink; partial runs and runs without a
   mailbox are skipped by design). The result header states the outcome
   verbatim: `recovered via closing reminder` or `not submitted despite
   one closing reminder`.

Live verification (5 frozen cases, cap 12, with the corrected harness;
`artifacts/explore-turn-ab-contract-check2-2026-09-25`):

| case | contract | note |
| --- | --- | --- |
| map-module | reported | note submitted |
| trace-call-flow | reported | note submitted |
| find-config-owners | reported | note submitted |
| enumerate-error-handling | fallback | 13 turns / 28 tool calls — hit the cap, soft-landed partial; the reminder is skipped for partial runs by design |
| test-inventory | reported | note submitted |

**4/5 reported vs 0/20 in the (flawed) baseline.** Honest reading: the
live model complies when the instruction actually reaches it; the
remaining fallback case is the documented partial boundary, not
disobedience. Small sample, single run; the corrected harness makes every
future A/B comparable.
