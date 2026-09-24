# Continuation independent audit plan

## Scope and evidence boundary

This plan audits the real CodeLoop continuation against
`/private/tmp/codeloop-completion-recheck-20260918` after the parent reports that
the model run has finished. The independent auditor will not edit that target's
production code or tests, call a model, or run target checks before that signal.
Independent audit commands and results will be reported separately from the
model's own tool calls and verification evidence.

Preparation performed only read-only repository metadata and evidence reads.
The target HEAD was `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`; tracked files were
clean, with only an untracked `.minicode-memory-authority.lock`. This is the
known original faulty production baseline. The prior small-budget run made no
code changes and ended with an explicit partial hand-back. Its eight model
calls and read-only tools must not be counted as this continuation's calls.

No target tests, lint commands, reproduction scripts, or oracle were executed
during this preparation.

Read-only preparation SHA256 values:

| File | SHA256 |
| --- | --- |
| External `test_suite_completeness.py` oracle | `784ef1c6dc34b66fcc4509c067a96a31d85135aec2566db089cb822f2de55124` |
| Target `minicode/tools/test_runner.py` | `2a709671ff4398574709794b6b104ef5524ec149f6b0a8248d31b891f6ee80e0` |
| Target `tests/test_tools.py` | `a6b8941dd648dda6de44a7592f662d3e3578e65c36d55762e49b6f13fe9494d1` |

## 1. Freeze the completed model output

After the run ends, record its run ID, actual prompt and final response, typed
task outcome, `_partial` marker, model/tool counts, tool transcript, and emitted
completion/extension events. Record target HEAD, `git status --short`, binary
diff, changed file list, and SHA256 of final production/test files before any
audit command. Any unexpected modifications during audit require pausing the
comparison and reporting the mismatch, rather than silently mixing versions.

Check the public transcript for actual edits, test commands, outcomes, and
retry decisions. A todo marked complete is not verification. A completed run
record alone is not proof that the user goal was achieved. Report a partial
hand-back as partial even if the independent oracle later passes.

## 2. Run the existing independent behavioral oracle once

Use the unchanged external script:

`artifacts/login-concurrency-live-2026-09-18/oracle/test_suite_completeness.py`

Run from the host repository, after the model stops:

```sh
python3 artifacts/login-concurrency-live-2026-09-18/oracle/test_suite_completeness.py \
  --repo /private/tmp/codeloop-completion-recheck-20260918 \
  --expect fixed \
  --output artifacts/agent-completion-fix-2026-09-18/continuation/oracle-after.json
```

The oracle dynamically imports the target's real `minicode.tools.test_runner`
and verifies its module origin. It creates temporary test workloads outside
the target, uses real pytest subprocesses without mocks, disables ambient
third-party plugin loading, and counts test-created execution marker files.
Its acceptance conditions are:

- Direct pytest first proves the workload: 12 executed files, 11 passing tests,
  and the twelfth failing sentinel test.
- The target wrapper executes all 12 files and returns failure with 11 passed,
  1 failed, and typed verification outcome `failed`.
- For 12 passing files, the wrapper executes all 12 and reports 12 passed.
- A selected single passing file stays within that scope: exactly one marker,
  1 passed, and success despite the neighboring failing file.

Retain command, stdout, stderr, exit code, and the complete JSON report. A
target import/dependency failure is an audit blocker, not evidence that the
fix is incorrect. Do not change the oracle or target to manufacture a pass.

## 3. Prove the model's exact new regression tests against original production

Discover model-added regression nodes from the completed diff; do not reuse
the previous experiment's two tests unless the model actually produced those
same tests in this continuation. Freeze the final test bytes and SHA256.

Run the exact selected test command against current target production and an
isolated copy of Git revision `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`, using
the same interpreter, environment/import policy, selection, and test bytes.
The `test-counterexample` verifier at
`/Users/zhourunbo/.codex/skills/test-counterexample/scripts/prove_regression.py`
can create that isolated baseline with `--repo <target> --base-ref <revision>`
and copy each final test file supplied through `--test-path`. Select only the
relevant new test nodes with the actual pytest node IDs or `-k` expression;
record the full selection and command in the result.

Only the final test files (and indispensable test-only support files) may be
copied to the baseline. Never copy the production fix. Do not mutate or reset
the live target. Do not replace the original baseline with a synthetic mutant.

Acceptance requires current production to pass and original production to
fail for the intended missing-file behavior. Inspect every selected baseline
failure: import errors, missing fixtures/dependencies, filesystem permissions,
and unrelated failures do not count as proof. Keep the exact command/stdout/
stderr and hashes under `continuation/regression-proof/`. Report absent new
tests as an unmet task requirement rather than supplying tests on the model's
behalf.

## 4. Review scope and verify the final delivery

Read the final production/test diff for minimality, unintended changes,
permission handling, single-file scope, and preserved existing behavior.
Verify the touched test module and changed-file lint only when needed to
confirm the model's claimed result or resolve a concrete concern. Do not
repeat already confirmed checks without such a reason, expand to an unrelated
full repository run, install dependencies, or add excludes merely to obtain a
passing result. If the continuation prompt explicitly requires a full-suite
run, that request remains authoritative and any unverified portion is reported.

Compare the final answer with actual model-produced evidence. Separate:

- CodeLoop's autonomous work and its own test/lint results;
- host independent oracle and same-test baseline proof;
- remaining failures, unverified scope, and any runtime limitation;
- actual subagent calls, if any, without equating host collaboration to
  CodeLoop multi-agent execution;
- actual commit/push/PR results only if included in the authorized continuation
  and evidenced by real repository/remote state.

Do not count independent audit work as extra model success. If the model ends
partial, the audit can establish fix correctness while still reporting that
the full requested delivery was incomplete.

## 5. Outputs

Expected evidence: `oracle-after.json`, `regression-proof/proof.json` plus raw
outputs, frozen final diff/hashes, and a concise `independent-audit.md` with a
claim-to-evidence mapping. This planning file is not an execution result.
