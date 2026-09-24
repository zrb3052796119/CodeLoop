# Independent validator / episode review

Scope: verify the latest R4, R5, and R6 fixes and inspect their immediate integration. No production source edits, provider calls, or persistent-memory writes.

## Closed findings

Re-ran the previous `objective-contract-review/validator_episode_probe.py` without changing it. Fresh output is in `previous_validator_episode_probe.json`; historical artifacts were not overwritten.

- **R4:** Auth recovery borrowing billing red-green is rejected with honest, added, substituted, and empty claim error references. An unbound error (`call_id=None`) is also rejected; rule synthesis leaves it inferred. The red-green helper now derives the error-call set from the current recovery and fails closed when that set is empty.
- **R5:** Rendered cwd is restored in episode matching. Other-directory report commands are excluded and the actual backend retry survives the follow-up limit.
- **R6:** All retained verification calls are restored to projected evidence. The previous fifth-green counterexample now passes identically against parent and episode evidence.

Relevant regression modules: **46 passed**.

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_recovery_lesson.py tests/test_recovery_misassociation_regression.py
```

## [P1] Multiple recovery references merge error provenance across distinct temporal boundaries

**Location:** `minicode/reflection_synthesis.py:1636-1641`, passed to the per-recovery check at `:1650-1655`. The affected textual evidence fallback is at `:621-631`.

R4 correctly stopped taking the error set from `claim.related_error_ids`, but the caller now flattens the authoritative error sets of **all** cited recoveries into one list. It passes this same union when checking each individual recovery. As a result, a recovery can still borrow another recovery's error message through the text-overlap linkage branch.

The new offline probe has this explicit ordering:

1. An auth error is followed by the auth repair at position 3.
2. An unrelated billing check reports `Invoice check passed` at position 5.
3. The billing repair happens later at position 6.

The auth repair has no relevant passing check; the billing check predates the billing repair. Independently checking each recovery with its own errors produces false, and a parsed claim citing either recovery alone is rejected as `confirmed_recovery_verification_unrelated`. Merely changing `related_recovery_ids` to include both causes the same claim to pass the validator and value gate as confirmed/persistable. The auth recovery now receives the billing error's `Invoice` token, and its earlier timestamp supplies a temporal boundary the billing recovery cannot satisfy.

| References in the parsed claim | Validator | Value gate |
| --- | --- | --- |
| Auth recovery only | Rejected | Not persistable |
| Billing recovery only | Rejected | Not persistable |
| Both recoveries | Accepted | Persistable |

This is a concrete proof-chain mixing issue, separate from judging whether abstract prose is semantically supported.

**Fix:** Resolve and pass each recovery's own error list inside the per-recovery relationship check. Avoid forming a union before temporal and objective linkage. Add a regression in which no individual cited recovery has a valid complete proof chain and ensure adding references cannot manufacture one.

Reproduce:

```sh
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/multi_recovery_scope_probe.py
```

The probe and its captured output (`multi_recovery_scope_probe.json`) use real parsing, validation, and value-gate code; they invoke no model or memory APIs.
