# Recovery lesson shadow smoke set

This is fixed, synthetic runtime-shaped trace data. The evaluator reads these
files directly; it never reads real task history or writes durable memory.

There are **11 cases, 10 episodes**:

| Cases | Episodes | Annotation |
|---|---:|---|
| auth-expiry, bilingual-offset, checksum-before-publish, cache-invalidation | 4 | Verified, with narrow applicability and forbidden broader conclusions |
| two-independent-recoveries | 2 | Independent lease and billing repairs; references must not cross episodes |
| unverified-failed, retry-still-failed, unverified-modification | 3 | Candidate negatives; no successful repair proof |
| report-timeout-budget | 1 | Same report succeeds after a timeout change, with causality unproven |
| precheck-changed-selection, precheck-version-only | 0 | Identity/execution conflicts; no model call should occur |

Each episode has allowed scope, forbidden conclusions, an expected recovery
status, and a mandatory manual semantic-review requirement. `offline_response`
contains a hand-authored proposition or an abstention. The scripted adapter
resolves its citations against the fixed projected trace. It does not simulate
or measure a model's ability to produce that proposition.

## Running

From the repository root, deterministic contract smoke (the default):

```sh
.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --output artifacts/recovery-lesson-shadow-offline.json
```

Bounded real-provider smoke:

```sh
.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --execute --max-calls 10 --timeout 15 \
  --output artifacts/recovery-lesson-shadow-live.json
```

`--execute` uses the current runtime's reflection model and preserves
`allowRemoteReflectionModel`. It does not enable remote calls by itself or
write configuration. An unavailable or disallowed client stops before any
request. Omitting `--timeout` preserves the configured timeout; offline defaults
to 15 seconds. Repeat `--case-id` to select cases, for example
`--case-id auth-expiry --case-id report-timeout-budget`.

`--max-calls` is a whole-batch limit, default 10, accepted range 0–30. The same
shared budget spans every case and episode. A wrapper enforces the bound again
at the actual `generate_json` boundary. Each case has at most two calls.
Budget skips are separate from physical calls and provider failures.

## Reading the report

Schema version 1 contains dataset/content hash, safe runtime summary, limits,
case prechecks, whole-task Rule output, and per-episode records. Each episode
contains its exact projected evidence and Rule baseline **on that same
projection**, the model's parsed generated claims, admitted lessons,
observations, validation issues, and pending semantic review.

Every physical call records a hash of the complete actual messages, timeout
and output-token parameters, latency, usage and cost when available. Its
response text is sanitized and bounded by the configured UTF-8 byte limit, so
parser failures can still be reviewed. These content-bearing records belong
only in the explicit synthetic review artifact, never production metrics.

`provider_calls` is zero in scripted mode; its separate `scripted_calls` is not
model evidence. Unknown token usage or cost stays **null**, with known count,
unknown count and known subtotal reported separately. An incomplete cost sum
is never presented as a complete total or as zero.

A provider-local deadline preserves the already observed call records and
continues with the next case. If a two-episode case aborts before returning its
result, prior raw responses remain available but their unavailable outcomes
are explicitly marked; empty claim arrays in those records are not evidence
that the model abstained. Cancellation returns a partial report and does not
start later cases.

`extraction_and_candidate_safety_passed` checks only extraction expectations and
absence of confirmed candidate recovery/root-cause lessons. `pipeline_completed`
separately requires that all expected generations returned parseable outcomes.
Neither means the model's claims are semantically correct. The report always
leaves semantic quality and production readiness unestablished.

Review each generated clause against its citations, causal confidence,
applicability, limitations, and added value over the same-episode Rule baseline.
In particular, a real quote does not justify a broader uncited conclusion.
Keep an independent adjudication artifact instead of converting validator
acceptance into a quality label. This ten-episode smoke set is directional,
not a statistically sufficient deployment evaluation.
