# Hybrid Memory v3 Holdout

This is the untouched synthetic promotion holdout for the concrete-object
query gate plus Hybrid Memory v2.1 relevance verifier. It was authored and
byte-frozen after v2 exposed batch-context sensitivity on an underspecified
query, and before the first v3 model or embedding run.

The 24 cases contain 12 positives and 12 hard negatives. The 24 shared
background memories are fully adjudicated as irrelevant for every case.
Dense recall is measured against the global eligible corpus; canonical
precision is measured against each case target plus the shared backgrounds.

The fixture is immutable after the first run. Corrections require a new
versioned holdout and an audit note; thresholds, prompts, labels and query-gate
rules may not be tuned from v3 results.
