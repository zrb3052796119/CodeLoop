# RecoveryLessonAgent implementation review

Scope: `minicode/recovery_lesson.py`, `tests/test_recovery_lesson.py`, `tests/test_recovery_agent_loop.py`; read surrounding synthesis/pipeline code to confirm contracts. No source edits, real model calls, or real memory writes. The supplied implementation document accurately states that this component is not wired into production.

Validation: `.venv-acceptance/bin/python -m pytest -q tests/test_recovery_lesson.py tests/test_recovery_agent_loop.py` → **10 passed**. `test_recovery_agent_loop.py` covers existing runtime retry/circuit behavior, not the new memory learner. The separate deterministic probe is `probe_recovery_episode.py`; recorded output is `probe_recovery_episode_output.txt`.

## Findings

### P2 — Preserve the evidence establishing a verified episode's relation

Location: `minicode/recovery_lesson.py:302-306` (and the TaskEvidence projection at 311-323).

`_episode_verifications()` marks an explicit recovery verified using `_verification_reproduces_earlier_failure()` on the parent evidence. `episode_to_task_evidence()` subsequently keeps only the passing checks. The failed check which proves the red→green relation disappears, so the shared validator evaluates the same claim against a different, incomplete relationship graph.

Reproduced with `pytest tests/test_auth.py` failing, an auth edit and an explicit recovery action, followed by the same test passing with generic output `1 passed`. The explicit recovery has no direct verification call ID; the parent establishes its link from the red→green pair. Results:

- Parent checks: c1=failed, c3=passed; episode status=verified.
- Child checks: c3=passed.
- Same correct confirmed recovery claim: parent valid claims=1; child valid claims=0 with `confirmed_recovery_verification_unrelated`.

Preserve the bounded failed verification records and referenced files used to establish the relation, together with their event positions. The projection should not invalidate a relation that the episode extractor just established.

### P2 — Match a retry's objective before picking the first same-tool success

Location: `minicode/recovery_lesson.py:253-263`.

The candidate feed only checks tool name, changed invocation and later success. All commands use `run_command`, so an unrelated successful command displaces the actual recovery attempt. Once this first match is selected, the error is claimed and its real retry is never included.

Reproduced: `python scripts/report.py (timeout: 1)` fails → `git status` succeeds → `python scripts/report.py (timeout: 120)` succeeds. The extracted episode follows up with `git status`; the 120-second invocation is absent. The very case this component is intended to preserve reaches the learner with the wrong input change.

Match a bounded objective identity (including resources and cwd) and keep the success that belongs to that objective. Treat parameter changes such as timeout separately from target changes.

### P2 — Namespace accepted claim IDs across episodes before aggregation

Location: `minicode/recovery_lesson.py:397-403`; generator contract at `minicode/reflection_llm.py:953`; existing consumer at `minicode/memory_pipeline.py:2027-2049`.

Each synthesizer response restarts claim numbering at `llm-claim-000001`, while the new agent concatenates all accepted claims without renaming. Two independent verified episodes with different semantic keys produced two accepted lessons having the same ID.

The existing pipeline builds a `valid_by_id` dictionary and then deduplicates accepted IDs. Feeding these aggregated lessons through that contract retains only the last claim with the repeated ID. This is an integration blocker, not a claim of an existing production write bug: the component is still disconnected.

Assign stable episode-qualified IDs before returning claims or outcomes. Verify two independent episodes survive both aggregation and the existing write-selection contract.

## Smaller diagnostics issues

- `EpisodeLessonOutcome.persistable_claims` is never filled. A successful one-episode run returns one global lesson, `claim_count=1`, but zero per-episode persistable claims. Fix beside aggregation at 397-403.
- `outcome.attempted=True` at line 369 happens before a provider call is admitted. An exhausted `AgentTurnBudget(max_model_calls=1)` (the synthesizer preserves one call) yields real client calls=0, reported `llm_called`=1, failure=`agent_budget_exhausted`. Distinguish attempted processing from actual provider calls before using this metric for shadow cost or call-rate evaluation.

## Scope clarification, not an independent blocking finding

A sensible inferred warning for a single timeout retry passes claim validation but is intentionally refused by the existing value gate (`single_observation_error_pattern`). The result retains the claim count and rejection reason, not the text of the rejected observation. This preserves conservative persistence; documentation should distinguish “candidate episode available to the learner” from “inferred observations retained for later learning/evaluation.”

## Related quote issue handed to the shared-validator reviewer

The new envelope exposes `ToolEvidence.invocation`, but `reflection_llm._claim_evidence_texts()` does not include invocations. Quoting the exact provided `python scripts/report.py (timeout: 120)` is rejected as `evidence_quote_not_grounded`. The shared validator's event index also omits pure tool-call evidence. The probe includes this case; it belongs with the review of quote-source parity.
