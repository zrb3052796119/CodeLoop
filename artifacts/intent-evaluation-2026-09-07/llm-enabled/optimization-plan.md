# Intent optimization roadmap analysis

## Goal
Explain the measured failure mechanisms and propose an evidence-based optimization sequence without changing production behavior or running more inference.

## Phases
- [x] Review baseline and live paired evidence.
- [x] Independently partition remaining failures and validate key source mechanisms.
- [x] Verify provider-specific structured-output guidance.
- [x] Deliver prioritized roadmap with validation criteria and limits.

## Decisions
- Keep measured facts separate from hypotheses and proposed targets.
- Preserve original labels; separate semantic equivalents from action/constraint failures.
- Recommend decision-layer changes, not an accumulation of phrase-specific regex patches.
- Do not implement or enable global configuration in this discussion turn.

## Status
Complete. Independent counts: 12 skipped, five invalid-response failures, two accepted-label failures. Three coarse-label equivalents separated; key issues are correction admission/append-only merge, discourse scope/primary selection, provider output budget, and missing context target binding. Official model card and provider JSON documentation checked. Roadmap delivered in optimization-roadmap.md; no new inference or production changes.
