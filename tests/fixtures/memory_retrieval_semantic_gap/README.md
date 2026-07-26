# Memory Retrieval Semantic Gap Dataset

This directory is a synthetic, offline Retrieval Phase 3A stress dataset. It measures where the existing deterministic lexical retrieval path loses semantically relevant memories. It is not sampled from production traffic and must not be interpreted as a production recall estimate.

## Composition

- 72 positive cases: 12 semantic-relation categories with 6 cases each.
- 36 hard-negative cases: 12 contrast categories with 3 cases each.
- Analysis split: 48 positive and 24 negative cases.
- Sealed decision split: 24 positive and 12 negative cases.
- One shared pool of 32 unrelated, active synthetic memories supplies stable ranking pressure. It never changes in response to a query.

Each positive category reserves cases 01-04 for analysis and 05-06 for the sealed split. Each negative category reserves cases 01-02 for analysis and case 03 for the sealed split. The split was fixed before the first baseline run.

## Freeze Policy

The case files, background pool, schema, README, dataset manifest, and annotation record are validated and hashed before the first retrieval run. `frozen.sha256` is the authoritative byte-level manifest and excludes itself. Baseline output must not be used to alter labels, queries, entries, or splits. A factual correction after freezing requires a new entry in `adjudication.md` that preserves the old manifest hash and records the exact reason and replacement hash.

## Token Overlap

Overlap is computed only from the query and primary memory content. Text is NFKC-normalized and lowercased. ASCII words and numbers are tokenized as alphanumeric terms; contiguous Chinese text is whitespace-insensitive and represented by character bigrams. Common English stop words are removed. The stored score is Jaccard overlap rounded to six decimals. This diagnostic tokenizer is independent of the production retriever.

## Hard-Negative Semantics

Each hard negative marks every case-local entry in `must_exclude_ids`. `allow_wide_candidate` distinguishes a tolerable diagnostic candidate from an ineligible entry that must never be generated. A **false candidate** occurs only when a `must_exclude_id` appears in an arm where that case sets `allow_wide_candidate=false`; allowed wide candidates are reported separately as candidate noise. A **false injection** occurs whenever any `must_exclude_id` reaches the rendered IDs, regardless of whether it was allowed in the wider candidate set. `must_gate_reject`, `allow_consolidator`, and `allow_rendered` describe the expected downstream boundary independently, so candidate noise is never reported as an injection failure.

## Privacy And Safety

All content is newly authored synthetic material. It contains no real MiniCode memory, session, workspace message, secret, credential, or raw provenance. Attack-shaped negative cases use explicit placeholders and never contain usable secrets. The evaluator operates only on temporary homes and emits IDs, counts, bounded synthetic excerpts, hashes, and reason codes.
