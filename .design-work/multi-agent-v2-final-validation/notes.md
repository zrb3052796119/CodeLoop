# Notes: Multi-Agent final scenario validation

## Frozen scope

- Fixed revision: `56d8cd8`
- Historical baseline: `f8ae6fd`
- Production targets: `workflow_runner.py`, `workflow_finalization.py`, `central_scheduler.py`, `conflict_resolution.py`, audit/trace/artifact boundaries.
- Existing unrelated workspace changes are out of scope.

## Scenario matrix

- Frozen narrow command: `.venv-acceptance/bin/python -m pytest -q tests/test_workflow_finalization_resilience.py tests/test_workflow_runner_module.py tests/test_workflow_v2_structured_routing.py tests/test_multi_agent_v2_conflict_policy.py tests/test_multi_agent_v2_recovery.py tests/test_scheduler_audit_journal.py`.
- Baseline result on fixed code: `133 passed`.
- Diff mapping selected `tests/test_workflow_runner_module.py`, `tests/test_workflow_v2_structured_routing.py`, and `tests/test_workflow_finalization_resilience.py` as the highest-ranked native fixtures.
- Semantic probe planner found 249 candidate changed-line mutations and retained eight high-confidence guards for review; only behavior-relevant probes will be executed.

## Phase 2 coverage inventory

| Scenario | Existing evidence | Gap selected for Phase 3 |
|---|---|---|
| Worker crash / malformed or forged output | Scheduler unit matrix plus structured Analyst crash, malformed Analyst/Executor/Reviewer hand-backs, capability revocation, and forged-audit rejection | Add production-path Executor/Reviewer crash and Verifier-infrastructure failure cases only if they exercise distinct adapters |
| Global/stage timeout and late result | Every Scheduler stage has deadline/late-result unit coverage; ballot, Adjudicator, Human, observer, and cancellation ports have bounded-runner coverage | Add committed final-audit hang because existing audit/observation hang tests are read-only and uncommitted |
| Conflict voting and escalation | 2-of-3 sealed independent ballots, deterministic priority, expert and human routes, port failures/timeouts/capacity, frozen evidence and identity binding | Add an actual unsealed majority and typed wrong-route/out-of-package authority results |
| Audit replay / tampering | Closed trace and artifact contracts, disk tamper detection, commit-ready/final-audit join, exact idempotent write-once replay, rich E2E forged authority cases | Preserve as regression evidence; separately record that cryptographic/WORM anchoring against a fully privileged filesystem attacker is outside the current threat model |
| Commit truth / recovery | Promotion reconciliation, cancellation/deadline after commit, audit false/interrupt, snapshot/trace/cleanup failures, and mandatory delivery | Add committed + hung audit with late completion and runner-capacity recovery |

## Explicit product boundaries found

- `ConflictCoordinator.execute` is a bounded standalone Scheduler entry point. Automatic detection and insertion of a conflict subflow into the task workflow is explicitly deferred in `role-boundary-spec.md`; unresolved task-path conflicts therefore stop at the current gate and cannot authorize commit.
- Patch repair/reverify/rereview, clean-snapshot Executor retry, and a product-owned human approval UI remain explicit deferrals.
- In-process timeouts bound the caller and reject late authority results, but cannot forcibly terminate arbitrary Python callback code. Process isolation would be required for a true hard kill.

## Potential follow-up risks, not silently promoted into this test slice

- A final audit carries `workflowId`/`taskId` but is not cryptographically anchored to a host Run against a fully privileged cross-Run replay. Decide whether host misuse/filesystem-adversary protection belongs in the product threat model before changing the durable schema.
- Audit write-once tests cover complete exact replay, but not every injected crash point between artifact links and manifest sealing. This is a durability-hardening follow-up unless Phase 3 exposes a concrete recovery failure.

## Errors encountered

- Initially looked for `prove_regression.py` under the diff-guided skill; the script belongs to `test-signal/scripts`. Corrected before running any historical proof.
- A delegated historical proof attempt could not create a Git worktree under the managed read-only `.git` directory. It was rerun with narrowly scoped worktree permission and produced the recorded `PROVEN` results.
- The first full-suite run was sandboxed. Tests that bind local HTTP sockets failed with `PermissionError: [Errno 1] Operation not permitted`, producing 127 failures and 125 setup errors. The same tree and Python environment were rerun with the required local test permissions and completed with `5069 passed, 2 skipped`.
- Line tracing made the wall-clock-sensitive portable audit test exceed its own deadline on both trees. That coverage attempt was classified `INVALID` and removed; the public behavior remains covered by the same-byte historical proof, while the non-timing seal test supplies only honest `REACH_ONLY` changed-line evidence.

## Phase 3 additions

- Five conflict counterexamples: an unsealed 3-of-3 majority plus four typed wrong-route/out-of-package authority decisions.
- Three structured stage-adapter failures: Executor crash, Reviewer crash, and Verifier infrastructure crash, each with rollback and replayable audit assertions.
- One committed final-audit timeout/capacity recovery scenario.
- One portable public-entry regression for the same committed audit timeout against `f8ae6fd`.
- One direct typed-cancellation identity boundary test, added after its semantic probe initially survived.

## Signal evidence

- `historical-bounded-audit-signal.json`: `PROVEN`; identical portable test passes current and fails normally on `f8ae6fd` because the old synchronous audit port remains blocked beyond the local budget.
- `diff-guided-evidence.json`: deliberately `REACH_ONLY`; the old seal test fails during setup before invoking the seal, so it is not promoted into a behavioral counterexample.
- `test-signal-report.json`: `STRONG`; five reviewed semantic probes are all `KILLED`. Every probe patch is retained under `probes/` with a SHA-256 binding in the report.

## Final verification

- Focused finalization/conflict/recovery/audit slice: `181 passed`.
- Complete multi-Agent module slice: `529 passed`.
- Full repository: `5069 passed, 2 skipped`.
- Ruff (`E,F`, `E501` ignored per repository instructions): all production modules and changed tests passed.
- `git diff --check`: passed.
