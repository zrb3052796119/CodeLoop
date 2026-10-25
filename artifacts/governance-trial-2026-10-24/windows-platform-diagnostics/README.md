# Historical Windows platform diagnostics

This package preserves the **failed native-Windows full-suite results** from CI run [40114448888](https://github.com/zrb3052796119/CodeLoop/actions/runs/40114448888). The PR head was `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5`; the raw checkout logs identify merge ref `5d3f61f77361dc107cb8bb2cc23e582563c16fcc`. These are historical results, **not acceptance of the current candidate, not a repaired Windows result, and not a GO conclusion**.

| Native Windows job | Passed | Failed | Errors | Skipped |
|---|---:|---:|---:|---:|
| Python 3.11 / 120932734066 | 13,222 | 1,390 | 246 | 112 |
| Python 3.12 / 120932734106 | 13,217 | 1,395 | 246 | 112 |

The original full raw logs and JUnit XML are included without truncation. Successful earlier install/package steps do not negate the full-suite failures. Job metadata can contain stale nested step fields; final JUnit and raw logs are retained as the outcome evidence.

The archive also contains mutually exclusive traceback-substring classifications, per-event indexes, representative traces, hashes comparing 13 inspected source files against the old PR head, and exact historical README/pyproject/CI material. Initial commit `3036dd76` already contained the three-OS matrix. This is evidence of a compatibility-contract gap, not evidence that the entire project was originally POSIX-only. Counts are failure/error records, including setup/teardown; they are not proven independent causes or promises of how many tests a proposed fix will recover.

`decision/Windows-platform-decision.md` offers two scope choices: explicitly narrow the current native-Windows contract to an experimental subset, or include broader native-Windows porting and verification in this work. **The decision was pending when this historical bundle was formed. The user has since selected A: Linux/macOS are the formal targets and native Windows is experimental; see the approved [platform policy](../../../docs/platform-support.md).** The archived decision and manifest retain their original packaging-time status. No platform promise, CI matrix, skip policy, production source, or currently running live experiment was modified when preparing this package.

`proofs.tar.gz` contains 21 members (54,442,111 raw bytes; 6,114,696 compressed bytes). The archive uses sorted USTAR files with fixed ownership/mode/time and a gzip header with no filename and mtime zero. It was compressed once at low priority while live work was running. Every member was actually restored to a fresh temporary directory and checked byte-for-byte and by SHA-256; no repeated-compression or new test-run claim is made. No source checkout, virtual environment, cache, or private runtime settings are included.

To inspect, verify `proofs.tar.gz` against `manifest.json`, restore only the listed regular relative-path members into an empty directory, and verify each member's byte length and SHA-256. Keep the historical failure classification attached to any downstream summary.
