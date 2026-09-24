# Final review probes: three reproducible regressions

All three interim risks reproduced against the completed CodeLoop working tree. No finding was withdrawn. These are defects in the delivered working-tree change, not failures to import the target or execute the probe harness.

## Provenance and scope

- Target: `/private/tmp/codeloop-completion-recheck-20260918`.
- Git HEAD: `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`; reviewed changes are uncommitted.
- `minicode/tools/test_runner.py` SHA-256: `a87b98b17990487d38677d7c968c04f36de8bbaaf8051b3d6a5dcaa83726a28c`.
- Completed: 2026-09-18 13:48:26 UTC.
- Interpreter: host `.venv-acceptance/bin/python`, Python 3.12.8; pytest 9.1.1.
- A fresh `-I -B` process explicitly inserts the target at `sys.path[0]`; every imported `minicode` module is asserted to originate below the target root and its origin is recorded in `results.json`.
- Target source was hashed before and after execution and remained unchanged. Target `git status --short` remained the two existing modified source/test files plus the existing Memory lock.
- All fixtures and evidence were written under this host `review-probes` directory. No target source/test edits, target commits, model calls, or full test-suite runs occurred.
- The subprocess observer delegates directly to real `subprocess.run`; it does not fabricate results. Fixture pytest processes have plugin autoload disabled and their own minimal config with cacheprovider disabled.

## P2 — optional flags disappear above 200 selected files

Location: `minicode/tools/test_runner.py:191`–193; optional arguments follow at 197–204.

The real `_build_pytest_command` receives the same actual directory and requested `verbose=True`, `coverage=True`, `pattern="selected"`, with 200 versus 201 selected files.

| Case | Expected | Actual |
| --- | --- | --- |
| 200 files | Preserve `-v`, `--cov`, `-k` | All present; explicit file list |
| 201 files | Preserve `-v`, `--cov`, `-k` | All absent; command is only `python -m pytest <directory>` |

This probe directly exercises production argument construction and requires no installed coverage plugin. The full commands are in `results.json` under `missing_optional_flags`.

## P2 — directory fallback runs files excluded by the filename filter

Location: `minicode/tools/test_runner.py:191`–193, interacting with filename selection at 234–240.

Fixture: 201 passing files named `test_selected_000.py` through `test_selected_200.py`, plus `test_other.py` containing a failing `test_selected_outside_filename`. Input pattern is `selected`, so the production filename filter selects the 201 passing files only.

| Execution | Expected/actual result |
| --- | --- |
| Actual public `test_runner_tool.run` | **1 failed, 201 passed**, process exit 1, `ok=False`; unselected `test_other.py` executed |
| Exact selected file list passed to pytest | **201 passed**, process exit 0 |
| Directory command with missing `-k selected` restored only for diagnosis | **1 failed, 201 passed**, process exit 1 |

The third run isolates this from the missing-flag finding: merely restoring `-k` does not preserve filename selection, because `-k` also matches the function name in the otherwise unselected file. Production code was not changed for this diagnostic command. Evidence: `selection-actual.stdout.txt`, `selection-explicit-oracle.stdout.txt`, `selection-directory-with-pattern.stdout.txt`, and their commands/statuses in `results.json`.

## P2 — passing warning text becomes a failed verification

Location: `minicode/tools/test_runner.py:323`–326, using unanchored output searches at 64–70.

The single passing fixture emits `warnings.warn('1 failed historical request; 2 errors already recovered')` and then `assert True`.

| Observable | Expected | Actual |
| --- | --- | --- |
| Real pytest result | Pass | **1 passed, 1 warning**, process exit 0 |
| Tool verdict | `ok=True` | `ok=False` |
| Verification receipt | `outcome="passed"` | `outcome="failed"` |
| Parsed failure/error counts | 0 / 0 | 1 / 2, taken from the warning |

Evidence: `warning-actual.stdout.txt` contains the actual passing pytest summary; `warning-actual.tool-output.txt` contains the incorrect failure report. `results.json` records process exit code, parsed output, and failed verification receipt.

## Existing new regression coverage

The delivered `tests/test_tools.py` adds these three cases:

- `test_test_runner_runs_every_discovered_file_beyond_ten` (line 436): inspects arguments for 12 files using a synthetic successful subprocess result.
- `test_test_runner_reports_failure_in_file_beyond_ten` (line 481): real 12-file end-to-end failure test.
- `test_test_runner_never_reports_pass_when_output_reports_failures` (line 519): synthesizes exit 0 with `3 passed, 1 failed` output.

None reaches 201 files or distinguishes a final failure summary from diagnostic warning text. Their presence therefore does not refute these boundary defects. This audit did not rerun these existing tests; the parent task owns the broader regression verification.

## Reproduce

From any directory, run:

```sh
'/Users/zhourunbo/code/coding agent/MiniCode-Python-main/.venv-acceptance/bin/python' -I -B '/Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/agent-completion-fix-2026-09-18/continuation/review-probes/run_probes.py'
```

The script exits 0 only when all three described defects reproduce; it is a counterexample collector, not a passing product regression suite. `results.json` records `defect_reproduced: true` for each. The reviewed source is preserved as `target-test_runner.py.snapshot` for exact line/byte comparison.
