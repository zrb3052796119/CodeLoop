# Git status parsing repair

Mode: GENERATE. Status: **PROVEN** by the bundled `test-counterexample` verifier.

Contract: Given a real repository containing index changes, worktree changes, and untracked paths, the status tool must preserve filenames, count X/Y changes independently, and never classify `??` as staged.

## Files and change

- Production: `minicode/tools/git.py`.
- New regression suite: `tests/test_git_status_regressions.py`.
- Required pre-edit snapshot: `artifacts/agent-completion-fix-2026-09-18/resume-2/before/minicode/tools/git.py` (workspace-relative).
- The pre-existing capability declarations, input validation, subprocess environment filtering, commit checks, and permission handling are preserved.

The subprocess wrapper now removes only final CR/LF characters rather than stripping semantic leading spaces. Status uses Git's stable porcelain v1 format. X and Y are counted independently, untracked entries have a separate count, and path extraction uses the preserved original columns. Existing clean-tree text and 30-entry display limit remain.

## Real repository regression results

The fixture creates isolated local repositories under pytest temporary directories, with local fixture identity, hooks disabled, and commit signing disabled. No status/subprocess results are mocked. Only fixture repositories receive setup commits; the host project and isolated CodeLoop target receive no commits or Git mutations.

Before editing production:

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_git_status_regressions.py -p no:cacheprovider
```

Result: **5 failed, 2 passed**. Failures directly demonstrated the truncated `lpha.py` path, unstaged/untracked entries counted as staged, and missing Y-column counts for `MM` and `RM`. See `red.txt`.

After editing production, the same command returned **7 passed**. See `green.txt`. The seven cases cover first-line unstaged changes, untracked files, mixed `MM`, combined categories, staged rename plus unstaged edit `RM`, staged-only control, and clean-tree control.

The exact final test bytes were also checked against an isolated copy of the saved real pre-edit production:

```sh
.venv-acceptance/bin/python /Users/zhourunbo/.codex/skills/test-counterexample/scripts/prove_regression.py \
  --fixed-root . \
  --buggy-root artifacts/agent-completion-fix-2026-09-18/resume-2/git-status-proof/baseline \
  --test-path tests/test_git_status_regressions.py \
  --python .venv-acceptance/bin/python \
  --result artifacts/agent-completion-fix-2026-09-18/resume-2/git-status-proof/proof.json \
  -- '{python}' -m pytest '{tests}' -q -p no:cacheprovider --import-mode=importlib
```

Verifier result: **PROVEN**, fixed exit 0 / 7 passed; baseline exit 1 / 5 failed, 2 passed for the intended assertions. The copied baseline Git source is byte-identical to the required saved snapshot. `proof.json` records identical test SHA-256; `provenance.json` records source/test hashes; `change.diff` isolates this task's edits from earlier user changes.

## Existing related checks

```sh
.venv-acceptance/bin/python -m pytest -q \
  tests/test_git_status_regressions.py tests/test_capability_invocations.py tests/test_tools.py \
  -k 'git or subprocess_environment_strips_credentials' -p no:cacheprovider
```

Result: **16 passed, 99 deselected**. See `related-tests.txt`. This covers relevant existing Git capability contracts and the shared subprocess environment filter in addition to the new regressions.

`git diff --check -- minicode/tools/git.py tests/test_git_status_regressions.py` passed.

## Lint and limits

```sh
.venv-acceptance/bin/python -m ruff check minicode/tools/git.py tests/test_git_status_regressions.py
```

Unfiltered Ruff reports three existing findings in unchanged code: `BLE001` (broad exception handling), `PLW1510` (missing explicit `check`), and `RUF059` (unused commit stdout). Running Ruff on the saved pre-edit file produces the same three findings. This task leaves them untouched. With only these existing codes ignored, both changed files pass:

```sh
.venv-acceptance/bin/python -m ruff check minicode/tools/git.py tests/test_git_status_regressions.py --ignore BLE001,PLW1510,RUF059
```

No full repository suite was run. No separate type-check command is configured in `pyproject.toml`. The new tests cover ordinary tracked/untracked, mixed modification, and rename states; they do not establish a new conflict or submodule presentation contract.

No models were called. The isolated target `/private/tmp/codeloop-completion-recheck-20260918` was not changed. No host commit or push was performed.
