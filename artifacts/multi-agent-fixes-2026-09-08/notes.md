# Fix notes

Prior review and four deterministic probes: ../multi-agent-review-2026-09-08/.
Baseline manifest: baseline.json. Existing worktree changes predate this work and must be preserved.

Current corrections: role/evidence scopes, unified delegation circuit, complete promotion baseline incl empty patch, parent continuation and failure/receipt preservation implemented. Parent regression file currently has 12 cases; existing cancellation/loop and finalization suites green.
Independent review identified and fixed continuation budget propagation, observer-finalization exception, incomplete-progress wording, and shallow receipt copying.
Concurrent unrelated Skill-selection edits observed in config, skill_router, skill_routing_turn, subagent_worker, main, tui and an agent_loop load barrier. Preserve them and exclude them from this task change attribution.
The concurrent Skill task also adds skill_selection_mode to agent_runtime.py's call_context; this task owns only final_response_text in that file. The strict intent call_context test currently fails from that separate change; do not attribute it to the continuation fix.
Validation tool note: python3 -m ruff unavailable for Python 3.12; use installed ruff CLI (/Users/zhourunbo/miniconda3/bin/ruff). tooling.py has an unchanged pre-existing E731 finding at the getter lambda; do not attribute it to this fix. First combined README patch had an English-context mismatch and changed no files; reapplied exact local patches.

Final: 939 related tests pass across 49 files. Five unchanged test sets are PROVEN against the original source. Full Python 3.13 run: 9640 passed, 151 failed, 128 errors, 2 skipped; 279 failed cases retried with loopback enabled: 257 passed, 22 failed. The 22 remaining failures have class-specific old-source/current-source evidence and are outside the requested multi-agent corrections. Complete limitations are in implementation-summary.md; no claim of an all-green whole repository.
