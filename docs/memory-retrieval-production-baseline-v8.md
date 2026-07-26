# Memory Retrieval Production Baseline v8 Certification

## Certification decision

`memory-retrieval-production-v8` is the active production-source baseline and
the child of immutable v7. It accepts only the Batch 4B-1 observation seam:
the successful Model-call boundary can compute a fail-closed catalog quote and
emit `model.costed` after `model.completed`.

This baseline does not accept changes to Model requests, retry or switching
behavior, Tool/Assistant behavior, Memory Retrieval, prompts, Session
persistence, permissions, TUI behavior, or the accepted 108-case semantic
artifact. Cost remains an observation computed from usage and a fixed rate
catalog, not a Provider invoice.

## Exact v7 to v8 delta

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `canonical_model_cost_observation` |
| `minicode/run_journal.py` | changed | `canonical_model_cost_observation` |
| `minicode/pricing.py` | added | `canonical_model_cost_observation` |

No protected file was removed. `run_journal.py` changes only its closed event
type allowlist so the existing same-Run writer can accept `model.costed`.

## Catalog evidence and arithmetic

Catalog `minicode-pricing-2026-07-17-v1`, version 1, supports two canonical
OpenAI entries when the completed adapter supplies an exact safe identity:

| Canonical key | Input | Cached input | Output | Official source |
|---|---:|---:|---:|---|
| `openai/gpt-4o` | $2.50 / 1M | $1.25 / 1M | $10.00 / 1M | <https://developers.openai.com/api/docs/models/gpt-4o> |
| `openai/gpt-4o-mini` | $0.15 / 1M | $0.075 / 1M | $0.60 / 1M | <https://developers.openai.com/api/docs/models/gpt-4o-mini> |

Sources were retrieved on 2026-07-17. OpenAI `prompt_tokens` includes the
`cached_tokens` detail, so uncached input is `input - cacheRead`; cached input
is then priced once at its cache rate. Cache creation is not a separately
billed response bucket for these entries. Unknown, custom, dynamically routed,
non-official endpoints, missing buckets, invalid usage, and unsupported token
semantics are unavailable rather than default-priced.

All rates and intermediates use `Decimal`. Each component is converted to
nano-USD and rounded independently to the nearest integer with
`ROUND_HALF_EVEN`; `amountNanoUsd` is the exact integer sum of the four
components.

## Event and read boundary

A successful call is observed as `model.started`, `model.completed`, then
`model.costed`, sharing one `operationId`. A failed call remains
`model.started`, `model.failed`. With no event sink, identity resolution,
catalog lookup, Decimal work, and observer IDs remain absent.

Run Detail permits only the version, operation ID, fixed status/quality,
currency, catalog ID, priced canonical key, integer total/components, or a
fixed unavailable reason. Invalid or unreconciled priced payloads downgrade to
`pricing_failed`. Overview, Runs summary, Ops, and frontend Cost totals remain
`unavailable/null` for Batch 4B-2.

## Immutable evidence

Pinned manifest SHA-256 values remain unchanged for v1-v7. The v8 manifest is:

`13a70abaed1091d17bc137fcffab336349ab6d22cf7f503133bf6efd1cb37726`

Default verification is read-only:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py
```

`--print-v8` is deterministic and read-only. `--write-v8` owns only the fixed
v8 target after validating every pinned v1-v7 manifest and lineage edge.

## Semantic behavior equivalence

The accepted artifact remains
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
the complete behavior projection remains
`b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
and the 108-case fingerprint remains
`b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
