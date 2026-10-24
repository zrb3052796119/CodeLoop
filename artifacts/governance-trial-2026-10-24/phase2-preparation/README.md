# Phase2 preparation: 16 real historical tasks

Status: offline preparation only. No paid/model trial has run, and the execution host has not been formally frozen. Phase0 readiness is required before the existing runner's `freeze` and `run` steps. This is a retrospective internal comparison, not a new promotion gate.

`manifest.json` contains 5 read, 7 write and 4 refactor tasks, reconstructed from this repository's real Git changes. It records 32 ordered single/auto cells, full commit IDs, source-tree IDs, canonical Python inventory SHA-256, reference-test hashes/selectors, prompts, private-oracle hashes, allowed changes, limits and provenance. The reference tests pass for all 16 commits (92 checks); the same bytes reject every corresponding parent. Parent failures caused by an absent newly added API establish feature absence, not a preexisting behavioral bug.

Each `cases/<id>/` contains a prompt, private oracle, scoped historical reference patch, and parent/reference proof logs. Read cases additionally contain a private valid diagnostic and adversarial proof logs. Neither these files nor the reference patch is placed in the agent workspace. Paths in proof logs are normalized to `@REPOSITORY@` and `@STUDY_ROOT@`; the manifest retains both original and normalized digests. `host-oracle-preflight-results.json` records the original prepared-oracle hashes (path relocation changes four hashes, without changing their logic).

`SHA256SUMS.json` covers this package except itself. The package stores no full source tree; `rebuild.py` verifies local Git objects and reconstructs sources outside the repository. It uses argv subprocess calls, never shell interpolation, and never loads provider credentials or calls a model. Missing Git objects must be obtained separately; it does not fetch them. Use the same Python environment as the study runner, with pytest and the project's dependencies installed.

From the repository root:

```sh
.venv-acceptance/bin/python artifacts/governance-trial-2026-10-24/phase2-preparation/rebuild.py --check
.venv-acceptance/bin/python artifacts/governance-trial-2026-10-24/phase2-preparation/rebuild.py --output /private/tmp/codeloop-governance-20261024/phase2-portable
```

The output path must not exist; it contains expanded inputs and `execution/` prepared by the existing runner. This command does not repeat the recorded tests; it verifies their material hashes and Git provenance. Historical test bytes can be recovered with `git show <referenceRef>:<historical_test_path>` and their exact selectors are in the manifest. Private oracle support fixtures are embedded in `oracle.py`.

After Phase0 passes, use the host environment that permits process enumeration, and freeze before any model receipt:

```sh
.venv-acceptance/bin/python scripts/run_three_arm_real.py freeze --output /private/tmp/codeloop-governance-20261024/phase2-portable/execution
.venv-acceptance/bin/python scripts/run_three_arm_real.py run --output /private/tmp/codeloop-governance-20261024/phase2-portable/execution
```

The second command invokes the user's configured `custom / glm-5.3-flash` route for both arms and all roles. Economic budgets are unlimited; tokens, physical calls and elapsed time are primary costs. Unknown USD is null. Human interventions remain unknown without a human-event source; journal and automatic policy approvals are separate. One repeat per cell, related tasks from one repo, reconstructed prompts and possibly known history limit generalization.

CodeLoop is not an OS sandbox. Hidden files are outside the agent's logical workspace, and the prompt omits their paths, but authorized Python can access host-readable paths. This is non-adversarial separation, not a claim of confidentiality against hostile code. See `protocol.md` and `independent-review.md` for acceptance boundaries.
