# MiniCode Production Reliability Hardening

Date: 2026-08-21

## Outcome

The P1 production-reliability repair is complete and passes the full real-
environment test suite. Provider calls made by the main Agent and its
auxiliary subsystems now share one bounded call/token/cost authority, inherit
turn cancellation and deadlines where a real provider is involved, and emit
joinable content-free model lifecycle observations.

## What changed

### Provider deadlines and cancellation

- OpenAI-compatible and Anthropic adapters bound each socket request and retry
  delay by the remaining Agent deadline.
- Cancellation/deadline checkpoints run before transmission, after response
  reads and while consuming streamed lines.
- Context compaction no longer uses an executor timeout that can return while
  an untracked provider thread continues running.
- Reflection and curator adapter calls inherit the parent cancellation token
  and deadline. Legacy test doubles remain compatible through signature-
  inspected optional arguments.

The remaining technical boundary is explicit: Python's standard `urllib`
cannot asynchronously kill an arbitrary in-flight socket read at the exact
instant a cancellation flag changes. The active read is nevertheless bounded
by the remaining socket deadline, and cancellation is observed at every safe
read boundary.

### Shared budget and observations

- Default per-turn ceilings are finite: 1,000,000 total tokens, 80 model calls
  and USD 5.00, with existing runtime/environment overrides preserved.
- Main Agent calls, compaction, Hybrid Memory verifier/challenger, remote
  embeddings, reflection and memory curation settle against the same
  `AgentTurnBudget` instance.
- Models outside the small canonical pricing catalog use the existing
  conservative advisory catalog for budget enforcement, while the canonical
  cost observation remains honestly marked priced or unavailable.
- Auxiliary calls emit correlated `model.started`, `model.completed`,
  `model.costed` or `model.failed` events with an operation ID and bounded
  purpose. Prompts, memory content, local paths and credentials are excluded.

### Safe diagnostics and release evidence

- `/status` now consumes an allowlisted runtime summary. It reports model
  identities, credential-presence booleans and effective turn limits without
  serializing credential values.
- `python -m scripts.check_release_reproducibility` is a read-only check for
  the exact Git HEAD, clean checkout and deterministic quality profile.
- The current deterministic quality profile passes. The checkout is correctly
  classified as non-reproducible because it contains extensive user-owned
  tracked and untracked work; the repair did not reset, delete or commit it.

## Verification

- Scoped Ruff: passed.
- Python compileall: passed.
- Focused reflection/curator compatibility matrix: 122 passed.
- Cross-module Agent, timeout, budget, context, Hybrid Memory, embedding,
  retrieval, config and journal matrix: 371 passed.
- Full real-environment suite: 4086 passed, 2 skipped in 270.61 seconds.
- Deterministic current quality profile: passed with zero failed checks.

## Remaining non-P1 work

- A release artifact is not reproducible until the user-owned dirty worktree is
  intentionally reviewed and committed or otherwise captured.
- Successful read-only tasks without an explicit verification signal can still
  produce an unknown learning outcome; this is a memory-quality policy issue,
  not a provider-control defect.
- The project Memory store observed during the audit had no active project
  lessons, so persistent-memory value should continue to be evaluated with
  real accepted lessons rather than synthetic retrieval alone.
- The current North-Star corpus is curated/synthetic plus recorded live runs;
  a larger independently sampled real-project benchmark remains the best next
  evidence for an A+ claim.

## Operator action

A credential value was exposed in an earlier ad-hoc diagnostic output during
the audit. It was not written into repository files, and the product now has a
safe projection for future diagnostics. The exposed credential should still
be rotated because output history cannot be revoked by a code change.
