# Installed-wheel smoke failure attribution

The failure is a pre-existing test instrumentation mismatch, unrelated to this multi-agent fix. No production source, test expectation, fixture, or baseline was changed during this diagnosis.

`tests/test_packaging.py:1786` (generated `installed-wheel-smoke.py:1613`) expects one `Path.write_text` call. Its wrapper at lines 1737–1744 only observes that API. The actual write path is `write_file_tool.run` → `apply_reviewed_file_change` → `_atomic_write_text`, which writes using `os.fdopen(...).write`, flushes and fsyncs, and promotes through `os.replace` (`minicode/file_review.py:100–123`). It does not call `Path.write_text`.

The failed smoke had already passed all preceding assertions: the approval was accepted, the worker thread ended without an error, and the target content matched `"installed-absolute-safe\n"`. The original target still contains those exact 24 bytes. Therefore this assertion failure does not indicate a failed write or permission operation.

The real pre-fix snapshot, current checkout, and installed wheel all have identical source hashes for `file_review.py`, `tools/write_file.py`, `permissions.py`, and `workspace.py`. Their hashes also match the root's preserved pre-fix manifest. `tooling.py` differs because of this task's new ToolResult field, but its old version produces the identical observed behavior in a separate interpreter.

`packaging-write-counter-probe.py` executes the real tool, PermissionManager, and ToolContext from each of those three production roots in a fresh temporary workspace. Only the user approval is supplied by a deterministic allow-once prompt. All three results agree:

- One edit approval; `ToolResult.ok` is true.
- The final target contains the exact requested text.
- Zero `Path.write_text` calls for that target.
- Exactly one real `os.replace`, from a same-directory temporary file containing the requested text.
- The destination equals the counter's target even after canonical path normalization, excluding a `/private` path-alias cause.

The minimal probe does not exercise HTTP transport again; the original installed-wheel smoke had already passed the relevant HTTP approval assertions before reaching this counter assertion. No agent loop or runtime dispatch is involved in the failing direct tool call.

Full three-way hashes, original target evidence, loaded module paths and probe results are in `packaging-diagnosis.json`. Individual runs are also saved as `packaging-write-counter-current.json`, `packaging-write-counter-old.json`, and `packaging-write-counter-installed.json`.

Reproduction uses `/Users/zhourunbo/miniconda3/bin/python` with:

```text
python artifacts/multi-agent-fixes-2026-09-08/packaging-write-counter-probe.py <production-root>
```

This is a diagnosis only. Updating the smoke to observe the actual committed write boundary would be a separate test maintenance change; merely changing the expected count to zero would weaken its intended assertion.
