# Checked-candidate baseline verification

On 2026-10-22, exported `minicode`, `tests`, and `pyproject.toml` with `git archive HEAD` at commit `e092a9af973360e3239d4d49aeb1adf18e56146d` into `/var/folders/f4/9n10ft0d23v18qrjy2_l_wy40000gn/T/minicode-head-candidate-m87143nx`. The command ran with system Python 3.12 and existing dependencies, without package installation.

Command (working directory was the exported HEAD tree):

```sh
python3 -m pytest -q --tb=short tests/test_checked_candidate_workflow.py
```

Result: **3 failed, 2 passed in 2.78s**. The three approval/review-repair/review-no-budget cases stopped after the first failed pytest, with one model call and `execute_phase_failed`. The existing fixed token prefix used the prior input estimate plus 4096 plus 512, and did not leave enough for the intended final edit. These failures predate the current working changes.

The working test now prices the next actual `reserve_model_call` input and preservation floor, then records the existing historical prefix on the same 500000-token/40-call ledger. It does not change either cap or fabricate a completion receipt. Review is requested explicitly under policy v6. Real independent pytest probes were added because the previous read-only approval fixture did not satisfy the existing approval evidence contract.

A subsequent repair case exposed a separate candidate-receipt mismatch: the Executor check capability includes active `review_regression_*` checks, while `parse_execution_candidate` receives only the original workflow verification checks. All completed checks must remain bound to their actual bytes and authority; the test does not waive this validation.

The runner now includes only original request check IDs in the candidate's formal receipt declarations. Extra check IDs are accepted for this separation only when they exactly match IDs derived from active entries in the review probe ledger; unknown IDs are rejected. Probe source, execution evidence, failures, replays, and final coverage gates remain in the existing ledger and audit. The repair regression observes failed / failed / passed / passed probe executions before commit, keeps the same Executor session, and verifies the persisted candidate and coverage evidence.

Final working-tree verification:

```sh
python3 -m pytest -q --tb=short tests/test_checked_candidate_workflow.py tests/test_scheduler_checked_candidate.py tests/test_workflow_scoped_budget_delivery.py tests/test_workflow_candidate_checkpoints.py tests/test_workflow_delivery_summary_safety.py tests/test_workflow_native_handoff_regressions.py
```

Result: **48 passed in 32.33s**. `git diff --check` passed. This is a working-tree result, not a HEAD baseline result.
