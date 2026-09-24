# Phase 4 independent intent holdout authorship

Author: independent delegated agent `/root/phase4_holdout`.
Created: 2026-09-07 (Asia/Shanghai).
State: sealed before any parser evaluation.

## Independent source restrictions

The author used only the parent task's specification and the two expressly allowed format files: `.repair-work/intent-phase3/score_holdout.py` and `.repair-work/intent-phase3/score_loader.py`. They were read as text only. The loader was not imported or executed, and its referenced file was not opened. The author did not read production code, repository tests, previous datasets, earlier reports, or evaluation results. No parser was imported, invoked, or tested. All utterances, conversation contexts, and human labels were newly authored from the specification and general programming knowledge, without using another agent's generated cases or implementation-specific feedback.

## Distribution

- Total: 50 cases, consecutive identifiers U01 through U50.
- Language: 40 Chinese utterances and 10 English utterances.
- Read only: 30 cases (24 Chinese, 6 English).
- Actual modification or execution: 20 cases (16 Chinese, 4 English).
- With conversation context: 16 cases, each using `context.recent_turns` as a list of role/content objects and `context.active_task` as a string.
- Coverage includes routine programming questions, code location, fault diagnosis, design discussion, quoted or negated requests, compound requests, source changes, configuration, tests, and documentation.
- Every case has a human rationale. All 50 utterance strings are unique.

## Scoring conventions

`holdout.json` is a UTF-8 JSON array of case objects. `expected` is the finite set of acceptable primary intents; `unknown` is never accepted. Primary alternatives are limited to reasonable terminology differences in read-only requests. `expected_read_only` expresses whether the user authorizes actual repository modification or command execution. Explaining code, diagnosing a fault, reviewing an implementation, discussing a plan, and designing tests without creating or running them are read only. Executing tests is not read only even when source editing is explicitly excluded.

Each `required_operation_groups` entry is required (AND between groups). Within one group, any listed choice is sufficient (OR between choices). A choice requires its `intent` and any one member of its `actions` list on the same affirmed operation. Quoted instructions, future possibilities, and expressly prohibited actions are not affirmative authorization. Context is provided only in the explicit context object, and the human rationale is label documentation, not user input. Primary-label and operation-group expectations were determined before any parser evaluation.

## Validation and seal

Validation checked JSON round-trip equality, object structure, intent/action vocabulary, consecutive identifiers, unique utterances, language counts, read-only counts, and context structure/count. Validation operated only on newly authored data and did not call the existing scorer or any parser. Files were written once after those checks, and the serialized holdout was read back only to confirm its hash.

Holdout SHA-256: `971d0ebc2894d5f1eea093094beab5d8733fa72b81b3b8171276102d75be2bb5`

The authorship file's own SHA-256 is recorded separately in the author's completion message, avoiding a self-referential hash. After completion the author will keep the dataset sealed until the parent explicitly releases it; the completion message contains paths, counts, and hashes only.
