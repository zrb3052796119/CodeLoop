# Phase 2 independent holdout authorship

- Author: independent subagent `/root/phase2_fresh_holdout`.
- Status: **FROZEN**. All question and annotation edits ended before the timestamp below.
- Frozen at (UTC): `2026-09-07T07:10:09.310235+00:00`.
- Dataset: `holdout.json`.
- Dataset SHA-256: `c2fdf309e37976007f0b5c6803a1c2c318daa7f96834e06d348597fb50daed53`.

## Scope and independence

This holdout was authored from the parent task instructions and the supplied intent/action enumerations. The only existing project content inspected was the `score` function in `artifacts/intent-evaluation-2026-09-07/run_evaluation.py`, extracted by its AST source range. No production implementation, existing test questions, parser output, evaluation results, reports, or another agent's changes were inspected. No model inference or parser evaluation was invoked. Structural checks read only this newly authored dataset.

The initial authoring task includes self-review. One wording/annotation refinement was made during that review, before freezing, to distinguish an implementation update from creation of a separate test module. No change was informed by parser behavior or measured outcomes.

## Composition

- 40 cases, uniquely identified `H01` through `H40`.
- Four categories of exactly 10 cases each: background/quotation/hypothesis/read-only scope; affirmative creation/editing/deletion/execution recall; everyday development/search/explanation/documentation; contextual reference and compound tasks.
- Main user utterances: 30 Chinese and 10 English. English cases are H07, H10, H18, H19, H20, H29, H30, H38, H39, H40.
- 23 read-only cases and 17 cases that authorize a state change or command execution.
- 8 cases include prior conversation and active-task context.
- 6 cases require multiple operation groups; 2 explicitly check negated intent labels.

## Annotation semantics

`expected` lists acceptable primary intents. `required_operation_groups` is conjunctive across groups and disjunctive within each group: at least one intent/action alternative must match in every group. `actions` inside an alternative lists acceptable action values for that intent. No case uses `expected_actions`, avoiding its per-key conjunction pitfall.

The annotations permit reasonable intent alternatives where file or command tasks may belong to code, system, configuration, refactoring, or testing. Write authority is checked by actual actions and the explicit read-only expectation, rather than inferred solely from an intent label. Read-only cases forbid affirmative create/update/delete/execute/merge/split/move/rename actions. Quoted instructions, historical actions, and hypothetical changes do not become affirmative execution requests.

Compound cases retain separate necessary operations. Ordered checks are used only where both sequence and intent labels are explicit. Negated-label checks are limited to unambiguous, explicitly rejected tasks. Entity assertions were left optional to avoid assuming an entity-key schema unavailable under the independence boundary; contextual cases still test resolution of the current operation and scope.

## Self-review and freeze

Semantic review checked the current request against background, quotations, hypotheses, previous turns, and active tasks. Structural checks verified count, consecutive IDs, four equal categories, unique text, language counts, valid enum values, nonempty operation alternatives, context role values, consistent read-only prohibitions, and lack of contradictory affirmed/forbidden requirements. This was a dataset audit, not an evaluation of a parser.

After this freeze, the author must not alter questions or expected annotations in response to implementation details or actual results. Any later benchmark revision must be a separately versioned dataset with a new authorship record; this file and its frozen dataset remain unchanged.
