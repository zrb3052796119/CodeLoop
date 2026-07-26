# Memory Retrieval Phase 3B Independent Holdout

This directory contains a wholly synthetic, offline decision holdout for Retrieval Phase 3B. It is independent of the Phase 3A fixture and contains newly authored queries, memories, negative controls, and a fixed 64-entry background pool.

## Composition

- 60 cases: 36 positive and 24 hard negative.
- 11 positive semantic categories and 7 hard-negative categories.
- One `independent_holdout` split. It is never available to model, representation, fusion, or Gate calibration.
- Positive cases cover USER, PROJECT, and LOCAL scopes plus English, Chinese, and both cross-language directions.
- Hard negatives distinguish allowed candidate noise from lifecycle/safety entries that are ineligible for indexing.

## Freeze Rule

`frozen.sha256` is generated only after JSON Schema, uniqueness, label, eligibility, resource, privacy, and normalized-overlap checks pass. It excludes itself. No real hybrid model result may be generated before this manifest exists. Any post-freeze correction requires a new dataset version and a new independent holdout; this version is not edited after evaluation.

## Privacy And Resource Boundary

All content is synthetic and contains no real memory, session, prompt, trace, credential, environment value, or workspace message. Each case has at most four local entries, content is capped at 2,000 characters, metadata/provenance are bounded, and unsafe-shaped controls contain placeholders only.
