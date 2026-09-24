# Intent recognition evaluation — 2026-09-07

## Goal
Run approximately 50 new user requests against the current MiniCode intent parser and provide a reproducible Chinese quality assessment.

## Phases
- [x] Locate parser, schema and available evaluation tools.
- [x] Freeze 50 manually labeled questions before seeing outputs.
- [x] Run the actual parser, record structured outputs and timings.
- [x] Review errors and run focused existing regression checks.
- [x] Deliver a report with strengths, failures and scope limitations.

## Decisions
- Preserve all pre-existing changes and historical root planning files; keep this task's records here.
- Evaluate synthetic requests; do not claim a representative production accuracy rate.
- Measure primary intent separately from operations, polarity, entities and constraints.
- Do not execute actions mentioned in sample prompts.
- No production source changes are requested.

## Errors Encountered
- Shell environment probe found no .venv Python; used installed Python 3.12 successfully.

## Status
Complete. First run: primary 26/50; all pre-labeled checks 23/50; zero exceptions; all 50 semantic_status=disabled. Independent audit confirms scoring and source hashes. Post-hoc acceptance of two category-equivalent cases gives 28/50 primary and 25/50 labeled checks; an additional gray case remains separate. Existing targeted regressions: 333/333 passed, exit 0. Six representative failures match the public parse_intent entrypoint exactly except timestamp. Deliverables: report.md, case-results.md, results.json and audit.md. No production source changes or sample action execution.
