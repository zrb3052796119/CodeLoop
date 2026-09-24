# Host CodeLoop final independent verification

**Result: 223 passed in 2.09 seconds.** CI-style Ruff, scoped Python compilation,
and scoped `git diff --check` also passed. All 15 scoped production/test source
file hashes were unchanged by verification. No real model was called and no
temporary target repository was inspected or modified during this check.

## Exact scope and environment

Interpreter: repository `.venv-acceptance/bin/python`, Python 3.12.8.

The seven task-specific test modules were:

- `tests/test_primary_agent_completion.py`
- `tests/test_turn_completion_policy.py`
- `tests/test_bounded_completion_entrypoints.py`
- `tests/test_error_classification_regressions.py`
- `tests/test_verification_prompt_guidance.py`
- `tests/test_completion_journal_events.py`
- `tests/test_completion_recent_changes.py`

Existing related coverage comprised `test_agent_flow.py`,
`test_subagent_soft_landing.py`, `test_run_journal.py`, `test_run_lifecycle.py`,
and `test_prompt.py`. The optional `tests/test_agent_budget.py` does not exist
in this workspace. It was recorded as absent rather than treated as a passing
or skipped test module.

This is a new, explicit 12-module scope. The old artifact recorded “353 passed”
without its command, so this result is **not** presented as a recreation of
that exact historical selection. `scope.json` preserves the complete file list,
and `results.json` preserves every executed argument array, working directory,
exit status, duration, stdout, and stderr.

Lint used the repository CI rules `--select=E,F --ignore=E501`, restricted to
the eight task production files and seven task-specific test modules. Compilation
used the same file scope. Diff checking was restricted to these task paths,
without inspecting unrelated existing worktree changes. No separate mypy or
pyright check was performed.

## Recent-change summary semantics

The observer now keeps at most 64 distinct recently changed paths. A later
observed mutation of an existing path refreshes its position; adding a new path
beyond that capacity evicts the oldest. The partial summary prints the most
recent 12 paths, which prevents early reproduction fixtures from permanently
crowding out later source and regression-test changes.

The final implementation also preserves observed tool-call order inside a
single result batch, including repeated paths, rather than sorting a set of
paths alphabetically. The added `test_large_batch_keeps_late_changes_in_call_order`
executes 70 fixture writes followed by two application writes in one batch and
checks that the final application paths remain in the summary. The parent’s
saved before result failed precisely because `app/login.py` was absent; the
current four recent-change tests pass within the 223-test run. The corresponding
parent evidence is `../recent-batch-before.txt` and `../recent-batch-after.txt`.

The journal tests run the actual Agent loop with deterministic model responses,
write through `RunObservation` and `RunJournal`, reopen journal storage, and
check limited-completion and extension events. They verify real persistence,
not merely an in-memory callback. They do not constitute another real-provider
model run.

## Remaining boundaries

- The summary is a bounded record of observed mutations, not a complete Git
  diff or an assertion that the user's task succeeded. Older paths can be
  evicted; the most recently touched paths are not necessarily the most
  semantically important files.
- Observation remains limited to supported successful write tools and safe,
  bounded regular-file reads. Unsupported shell mutations and oversized or
  unsafe paths are not inferred as progress.
- A batch is observed by comparing its before and after fingerprints. A change
  fully undone within that batch is not net progress; intermediate states are
  not a durable audit trail.
- This deterministic host test run checks current local behavior. It does not
  establish model compliance, completion success rate, or real-provider
  multi-agent behavior.

## Evidence

- `results.json`: full commands and results.
- `tests.stdout.txt`: `223 passed in 2.09s`.
- `lint.stdout.txt`: `All checks passed!`.
- `compile.stdout.txt` / `compile.stderr.txt`: both empty, exit 0 in results.
- `diff-check.stdout.txt` / `diff-check.stderr.txt`: both empty, exit 0.
- `hash-comparison.json`: before/after SHA256 and unchanged status.
- `run_host_checks.py`: reproducible executor for this exact saved scope.
