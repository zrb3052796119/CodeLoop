# CodeLoop continuation: interim static review

Final follow-up: after CodeLoop stopped, all three findings were reproduced against source SHA-256 `a87b98b17990487d38677d7c968c04f36de8bbaaf8051b3d6a5dcaa83726a28c`. See `review-probes/report.md` and `review-probes/results.json`; the original interim snapshot below is retained for provenance.

Reviewed at 2026-09-18 13:42 UTC. This is a provisional review of a live working tree; CodeLoop is still running and may change the implementation. Confirm all findings against its final snapshot before treating them as final defects.

- Reviewed target: `/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py`
- Observed SHA-256: `c7853bbdcb995da7a032db6285abe3f39e6dec6419005917ffe915d91b7b60f5`
- Scope: source and Git diff reads only. No target edits, target test execution, model calls, or intervention in the live run.

## 1. P2: directory fallback skips all optional pytest arguments

At lines 186–188, `_build_pytest_command` returns immediately when the target is a directory and the filtered discovered list contains more than 200 files. The additions for `-v`, `--cov`, and `-k` occur later at lines 192–199, so this branch cannot include any of them. The output still reports the requested pattern and coverage at lines 293–294. This is directly visible in the current control flow.

Verification hypothesis after the live run: build an actual directory fixture with 201 selected test files, request `verbose=True`, `coverage=True`, and a pattern, and inspect the subprocess arguments (or exercise the completed runner under an appropriate oracle). Require the optional arguments in both 200-file and 201-file cases. A coverage plugin need not be installed merely to check argument construction.

## 2. P2: directory fallback changes the selected file set

Discovery uses its own filename and directory rules (lines 18–34), then `_run` filters filenames by the literal pattern (lines 229–235). Above 200 files, the helper discards that explicit selected list and passes the whole target directory to pytest (lines 186–188). Even after restoring `-k`, pytest's directory collection and expression matching are not equivalent to the previous filename filtering: tests in a nonmatching filename can match a function name, and project `python_files`/ignore rules can change the collected set.

Verification hypothesis after the live run: 201 `test_selected_*.py` files plus a nonselected filename containing a test function whose name includes `selected`; run with pattern `selected` and check the expected file-selection contract. Separately consider a project with custom pytest collection rules. Decide and document the intended selection contract rather than assuming directory collection preserves it. This is a distinct concern from the missing flags in finding 1.

## 3. P2: arbitrary diagnostic text can now overturn a successful exit code

The new lines 318–321 force failure whenever the parsed counts contain failures or errors. However, `_parse_pytest_output` searches all stdout/stderr using unanchored patterns such as `(\d+) failed` and `(\d+) error` (lines 59–65), rather than identifying pytest's final summary. A warning in an otherwise passing suite such as `warnings.warn("1 failed historical request")` can satisfy this parser even when pytest exits 0. Previously, those loose counts affected reporting; this new verdict override promotes them into an execution failure.

Verification hypothesis after the live run: run a genuinely passing fixture whose warning output includes `1 failed` and/or `2 errors`; require the runner to report success. A real failing test with nonzero pytest status must still report failure. If textual summaries are required as an additional authority, parse the actual summary or a structured result rather than scanning all diagnostics.

## Review boundary

These are current-snapshot findings and proposed oracles, not executed reproduction results. The original `test_files[:10]` truncation is removed in the observed source. This review does not conclude that CodeLoop's eventual delivery is correct or incorrect, and makes no claims about an unobserved final commit or PR.
