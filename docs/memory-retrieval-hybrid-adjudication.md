# Retrieval Phase 3B Adjudication

## First-Loss Distribution

| Stage | Analysis | Sealed | Holdout |
|---|---:|---:|---:|
| Dense miss | 2 | 0 | 3 |
| Fusion rank drop | 3 | 2 | 0 |
| Semantic Gate false negative | 31 | 21 | 26 |
| Rendered success | 12 | 1 | 7 |
| Hard-negative false candidate | 9 | 3 | 6 |
| Hard-negative false render | 9 | 7 | 13 |
| Hard-negative rejected before candidate | 6 | 2 | 5 |

CandidateConsolidator false suppression, controller-disabled loss, and budget loss are zero.

## Source Outcomes

| Outcome | Analysis | Sealed | Holdout |
|---|---:|---:|---:|
| Lexical-only success | 0 | 0 | 0 |
| Dense-only success | 17 | 19 | 14 |
| Both success | 29 | 5 | 19 |
| Neither success | 2 | 0 | 3 |
| Hybrid rescued from lexical miss | 14 | 17 | 14 |
| Hybrid introduced hard-negative noise | 1 | 1 | 1 |
| Semantic Gate removed candidate noise | 9 | 3 | 6 |
| Semantic Gate removed correct memory | 31 | 21 | 26 |

## Dimension Findings

- Cross-language retrieval candidates improve, but the Gate accepts no sealed `en->zh` positive and only 1/11 sealed `zh->en` positives. Holdout accepts no `en->zh` or `zh->en` positive.
- Holdout zero-overlap candidate recall is 17/20, but post-Gate recall is 0/20.
- Holdout USER candidate recall is 11/13, but post-Gate recall is 0/13.
- Holdout LOCAL post-Gate recall is 5/10, PROJECT is 2/13, USER is 0/13.
- Negation/opposite hard negatives render 4/4 on holdout.
- Same-basename/different-path hard negatives render 3/3 on holdout.
- Same-symptom/different-root hard negatives render 2/4; same-domain/different-object renders 2/3.
- Safety/lifecycle negative candidates never violate index eligibility; any safe background rendering remains a relevance false positive, not a lifecycle leak.

## Root Cause

E5 cosine scores cluster in a narrow high range, and the global absolute threshold plus top-1 margin is not invariant across language direction or relation type. Raising the threshold suppresses many good cross-language and zero-overlap pairs while still admitting lexically and semantically close contradictions. Lowering it increases hard-negative rendering. The analysis grid contains no global threshold configuration that jointly satisfies recall and precision requirements.

## Decision

The evidence supports retaining local embeddings as a candidate-generation research direction. It rejects the current RRF selection and threshold-only Semantic Gate as a production relevance decision. No threshold may be retuned on these sealed/holdout results. A new iteration requires a new protocol version and independent holdout.
