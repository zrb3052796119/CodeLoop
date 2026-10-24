# Phase 1 — two local attempts (not latest PR acceptance)

**Second local full suite passed: false.**
Second local combined checks passed: **false**.
All results below are derived from completed run metadata and JUnit XML at packaging time.
The initial failed attempt remains preserved; a later pass does not turn it green.

| Attempt | Commit | Passed | Failed | Errors | Skipped | Exit | JUnit seconds | Wrapper seconds |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| First local attempt | `eb17c62a5510b6746c5fc677618303c9b83b96e6` | 14932 | 1 | 0 | 8 | 1 | 2889.078 | 2892.346 |
| Second local attempt | `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` | 14932 | 14 | 0 | 8 | 1 | 2631.148 | 2634.330 |

Both suite attempts used seed `20261024` and exact detached source clones.
Source archive hashes, commands, restoration manifest hashes, timestamps, complete pytest logs,
JUnit XML, wrappers and independently derived summaries are retained for each attempt.
The source archives themselves are verified against metadata but excluded to avoid duplicating source trees.
JUnit and wrapper durations measure different boundaries; pytest's printed duration remains in its raw log.

All failed nodes from both local attempts, directly from XML (details remain in summaries):

- First local attempt: `tests.test_memory_hot_store::test_nested_budget_caps_cumulative_wait_across_multiple_commands`
- Second local attempt: `tests.test_memory_retrieval_production_baseline_v40::test_v40_preserves_every_unrelated_v39_production_hash`, `tests.test_memory_retrieval_production_baseline_v40::test_v40_candidate_is_the_exact_multi_track_release_delta`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_candidate_rejects_a_missing_runtime_dependency`, `tests.test_memory_retrieval_production_baseline_v41::test_verify_active_v41_requires_release_join_even_after_manifest_pin`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_writer_returns_an_existing_candidate_only_after_full_pin_validation`, `tests.test_memory_retrieval_production_baseline_v41::test_staged_v41_candidate_is_read_only_and_v40_stays_active`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_candidate_closes_the_exact_hybrid_runtime_delta`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_print_is_deterministic_without_machine_metadata`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_writer_never_repairs_a_tampered_pinned_candidate`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_release_join_fails_closed_after_bound_artifact_tampering`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_writer_exclusively_creates_one_unpinned_candidate`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_writer_removes_final_target_when_directory_fsync_fails`, `tests.test_memory_retrieval_production_baseline_v41::test_v41_release_join_verifies_real_files_and_gates_selection`, `tests.test_memory_retrieval_production_baseline_v41::test_release_join_writer_does_not_create_a_missing_receipt_after_pin`

The repaired quality evaluations are offline replay of recorded evidence, with production bytes
verified equal to the repaired frozen suite even if intervening commits changed only docs/evidence:

- `current`: passed=true, exit=0, 21.459s, remote calls=0.
- `a`: passed=true, exit=0, 21.461s, remote calls=0.

Static compileall/Ruff checks passed: **true**. Each recorded source/test hash
was checked against the repaired frozen clone. This package does not claim a native Windows/Linux/macOS
CI pass, live model generalization, or any result from the separate Phase 0 baseline.
A later PR candidate requires CI evidence tied to that candidate; matching production bytes in
quality evaluations does not establish that its complete test suite passed.
Existing targeted proof packages are referenced by repository history and are not duplicated here.

## Integrity and scope

Only explicit allowlisted run/quality/static files, generated summaries, and this packager are archived.
No HOME, private-notes-backup, credentials, source tree tar, wheel directory or other proof package is copied.
`manifest.json` lists every member's byte count and SHA-256, plus the gzip hash and README hash.
Tar ownership/mode/timestamps and gzip mtime are fixed. The script decompresses its generated archive,
checks every regular member byte-for-byte, then rechecks inputs before writing these three files.
It refuses incomplete/timeout/interrupted runs, mismatched XML/exit codes, changed sources and an
existing destination. Run `package_phase1_acceptance.py --write` only after the repaired wrapper exits;
without `--write` it validates and reports without creating the final package.
