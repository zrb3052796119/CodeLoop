# Independent review: validator / episode integration

Read-only review of production code. Only review artifacts added. No model provider or durable memory API called.

Validation: `.venv-acceptance/bin/python -m pytest -q tests/test_recovery_lesson.py tests/test_recovery_misassociation_regression.py` → **36 passed**. New offline probes demonstrate gaps not covered by those tests.

Run:

```sh
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.py
```

Output: `validator_episode_probe.json` next to this report.

## [P1] Red-side error scope still depends on untrusted claim fields and becomes global when empty

**Locations:** `minicode/reflection_synthesis.py:1637-1640`, `:1650-1655`, `:576-584`.

The new red-green helper correctly rejects auth recovery borrowing billing red-green when the proposed claim faithfully names only `error-auth`. But `_reject_unlinked_recovery_verification()` obtains `related_errors` from **the candidate's** `claim.related_error_ids`, not from each recovery's `RecoveryEvidence.related_error_ids`. The model can add `error-billing`, substitute it, or leave `related_error_ids` empty. All three variants pass `parse_llm_candidate`, `ReflectionClaimValidator`, and `ReflectionValueGate` as confirmed/persistable. Evidence is unchanged, and the recovery still names only `error-auth`.

Probe `claim_controlled_red_scope` controls:

| Candidate `related_error_ids` | Validator / value gate |
| --- | --- |
| `[error-auth]` | Rejected as unrelated |
| `[error-auth, error-billing]` | Accepted / persistable |
| `[error-billing]` | Accepted / persistable |
| `[]` | Accepted / persistable |

The empty variant is a separate unsafe fallback in the same contract: `_verification_reproduces_earlier_failure()` turns an empty call-ID set into `None`, which disables the filter. It also triggers when a valid related `ErrorEvidence` has no `call_id` (an allowed shape). Probe `unbound_error_widens_red_scope` shows both the submitted candidate and the rule synthesizer then accept unrelated billing red-green for the auth recovery.

**Fix:** Derive the authoritative error/call set independently for each cited recovery from its own evidence linkage; validate claim references against it. If no related error can be bound to a failed call, the red-green branch must return false instead of searching the whole task. Test truthful, added, substituted, omitted, and null-call-ID references against the same evidence. Existing regression `test_recovery_cannot_borrow_an_unrelated_errors_red_green` only covers the truthful-claim variant.

## [P2] Episode objective parsing still discards the rendered cwd

**Location:** `minicode/recovery_lesson.py:209-211`.

The validator restores `(cwd: ...)` through `_objective_from_rendered()`, but `_same_operation_objective()` passes rendered invocations directly to `call_objective()`. That function strips the modifiers without recovering cwd. The candidate feed therefore treats `python scripts/report.py (cwd: /repo/backend; timeout: 1)` and the same relative script under `/repo/other0` as the same target. Two such other-directory successes occupy the two follow-up slots and discard the actual `/repo/backend` retry.

Probe `episode_cwd_displacement` uses the existing timeout trace and actual `TraceEvidenceExtractor`, and reports `real_retry_retained=false`. It remains a candidate, so this finding is a wrong episode/evidence loss issue rather than a demonstrated confirmed-memory promotion.

**Fix:** Have one public rendered-invocation adapter recover cwd once, or retain the structured objective on ToolEvidence; every consumer must receive the same anchors before matching and limiting.

## [P2] The fifth admitted verification loses its invocation during episode projection

**Locations:** `minicode/recovery_lesson.py:256-263`, `:405-425`.

`extract_recovery_episodes()` keeps every linked verification but truncates `followup_calls` to four. `episode_to_task_evidence()` restores invocation context only for failed checks. With five passing checks of the same repaired target, the fifth verification is still exposed to the model as valid evidence, but its invocation is absent; its candidate passes against the parent and fails against the episode (`confirmed_recovery_verification_unrelated`).

Probe `projection_loses_fifth_check_invocation` reports parent `{valid:true,persistable:true}` and episode `{valid:false,persistable:false}`. It demonstrates that the stated parent/episode admission invariant still breaks beyond the existing one-passed-check tests.

**Fix:** Bound whole proof chains together (verification and invocation), or restore invocation records for all retained checks. Do not retain a selectable verification while discarding context the shared validator requires to establish linkage.
