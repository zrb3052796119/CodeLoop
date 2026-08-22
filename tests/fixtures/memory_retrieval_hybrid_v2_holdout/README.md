# Hybrid Memory v2 Holdout

This directory is the untouched, synthetic decision holdout for the Hybrid
Memory v2 relevance protocol. It was authored and frozen before the first v2
embedding, training, threshold-selection, or retrieval run.

The 32 cases contain 16 positives and 16 hard negatives. They cover English,
Chinese and cross-language requests, USER/PROJECT/LOCAL scopes, zero-overlap
paraphrases, symptom-to-cause/recovery, configuration, aliases, corrections,
paths, negation, reversed ordering, ambiguous queries, unsafe content and
lifecycle-ineligible memories. The shared background pool adds stable ranking
pressure without using any real user or workspace data.

`frozen.sha256` is authoritative and excludes itself. After the first v2 model
run this fixture is immutable; any correction requires a new directory and a
new holdout version.
