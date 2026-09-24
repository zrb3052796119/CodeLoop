# Memory Retrieval Semantic Gap v2 Candidate

The candidate described here was subsequently accepted as the immutable
`memory-retrieval-semantic-gap-baseline-v2.json` after production v40 passed
source certification. The original v1 and candidate artifacts remain intact.

## Status

- Behavior candidate: `passed` on all 108 synthetic cases.
- Python hash seeds: `1` and `777` produced the same case fingerprint.
- Remote calls: `0`.
- Source certification: `certified` against `memory-retrieval-production-v40`.
- Overall acceptance: `true` in the v2 accepted artifact.
- v40 manifest SHA-256: `52d7131db710ef61674e776f931426e6efa8e91764df95eaa86612295f79e4f7`.
- v40 verification: `73/73` current files, candidate match true, v1-v40 manifest integrity true.

## Frozen evidence

- Immutable v1 artifact SHA-256: `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`.
- v1 case fingerprint: `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
- v2 candidate artifact SHA-256: `7c68c5e1d767c748a57b12e3972f43d64d11593d3e428a0a91f70234320b291f`.
- v2 accepted artifact SHA-256: `4f1bb69ce1f79a3a91253c7a11078c0668fd25999d29dd39f8fef96b7687333f`.
- v2 case fingerprint: `74556bb634a4afb28102a67d3e261206569cf4ca4d6d5d1e5a316b3fb3e9989c`.
- v2 behavior projection SHA-256: `f7d8e45028c7c55955f2825ee36bb06f8f2e638ad9ec51de1035b311b99b507e`.

## Explicit v1-to-v2 migration

v1 trusted a serialized `usefulness_score` even when it had no supporting
feedback counters. v2 derives usefulness from success and failure counters, so
an unevidenced persisted score no longer receives retrieval authority.

The frozen fixture contains 32 background entries and 110 case entries. No
entry has persisted counters; exactly the 32 background entries carry a bare
nonzero usefulness value, always `0.2`. Replaying current code with only the v1
score-loading semantics restored reproduces the immutable v1 case fingerprint.
Current counter-derived loading reproduces the v2 fingerprint.

The two replays have exactly 10,388 changed leaves. Their normalized paths and
counts are pinned in the evaluator and limited to candidate ordering, ranking,
scores, suppression, and the six/seven primary-rank changes. Any additional
field or count change fails the migration gate instead of being absorbed by a
new gold file.

## Retrieval snapshot

- Candidate Recall@20: `0.472222`.
- MRR@20: `0.275645`.
- NDCG@5: `0.284146`.
- Rendered recall: `0.166667`.
- Hard-negative rendered rate: `0.555556`.

These are synthetic-suite measurements, not production traffic estimates.

## Reproduction

```bash
.venv-acceptance/bin/python scripts/evaluate_memory_retrieval_semantic_gap.py \
  --output artifacts/memory-retrieval-semantic-gap-v2-candidate.json \
  --write-v2-candidate
```

The normal runner refuses to overwrite either the immutable v1 artifact or the
protected v2 candidate path. The explicit flag can refresh only the v2
candidate path, and only after all behavior-candidate gates pass.
