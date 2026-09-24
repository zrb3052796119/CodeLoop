# Context-aware semantic intent fallback

This module is an opt-in, advisory fallback between deterministic intent
parsing and multi-layer Skill routing. It is not an autonomous router and it
does not grant Skill, tool, filesystem, command, or permission authority.

## Decision order

1. Deterministic bilingual rules, operation polarity, entities, and bounded
   conversation context run first.
2. Rules distinguish affirmative instructions from supported forms of
   prohibitions, retractions, quoted/reported actions, and hypothetical events.
   Recognized non-instructions are excluded from semantic fallback.
3. An unresolved request, or one eligible uncovered clause in a partially
   recognized request, may make one bounded call to the configured primary
   model. Existing rule operations remain authoritative; semantic proposals
   only append operations for the selected original span.
4. The response must be strict JSON containing zero to four allowlisted
   affirmative intent/action pairs. An empty operation list is an explicit
   abstention.
5. Every operation is calibrated. The complete response is accepted only when
   its minimum calibrated confidence reaches the configured threshold.
6. The parser revalidates every pair and reconstructs clauses/evidence locally
   before the advisory result reaches Skill ranking.

Semantic output can therefore improve the routing query, but it cannot create
explicit Skill invocation authority or authorize a high-risk action. Delete,
execute, move, rename, merge, and split proposals are rejected.

The deterministic parser also distinguishes a requested explanation from an
action inside its subject, and observed execution failures from instructions
to run a program. Instructions to record failures or handle errors during a
run retain their execution operation. The same detected background boundaries
are applied when selecting a clause for semantic fallback.

A read request can govern an action-shaped noun: searching for a generation
function or reading the code that deletes a file does not request creation or
deletion. Explicit subsequent commands retain their own operations. Supported
content-attribution statements such as `the button label is "Delete account"`
are masked with their original offsets before rule matching and semantic
selection. A later colloquial request remains eligible for fallback. For a
single recognized request for meaning, semantic proposals are restricted to
read, analyze, and compare. These are bounded grammatical checks; unrecognized
source frames still require further coverage and evaluation.

Supported advisory questions (`how should we refactor ...?`) and questions
about a command's effects carry reading authority. Their inner action verbs
are interpreted within that sentence's request role. A polite request such as
`could you update app.py?`, or an explicit later command, retains its own action.
The same bounded reading frames constrain model proposals, so the model cannot
turn an effects question into a write request. Unrecognized wording remains
subject to the existing rules and semantic fallback.

Adjacent prohibitions retain their own scope, so reviewing a change followed
by `this time do not refactor or run tests` preserves the review. A completed
non-use status followed by an explicit deletion does not negate that deletion;
actual prohibitions and withdrawn deletion requests remain effective.
An explicit analysis request after a hypothetical event can start its own
scope. Factual background stays attached to a following analysis request so
clause splitting does not turn one unresolved diagnosis into two competing
fallback targets.

File references are resolved separately from actions. Recognized operations
can receive an unambiguous target from the current input or bounded history;
binding a file never creates an operation. A request to add another test needs
an explicit compatible test target in history, rather than merely the only
file mentioned in a historical report. Relative former/latter references
require a pair, while explicit first/second/last references use source order.

## Runtime controls

The fallback is admitted only when all of these controls are present:

- the shared Agent Turn budget;
- an active durable observation sink;
- cooperative cancellation and the caller's absolute deadline;
- a provider adapter supporting disabled tools, bounded output tokens and
  response bytes, zero retries, temperature zero, and isolated reasoning state.

The classifier keeps at least one model call available for the normal Agent.
Other advisory model preludes—Skill embedding, context compaction, Memory
curation/hybrid verification, and reflection—use the same reservation rule,
so they cannot consume that final call. A transmitted request that fails is
charged a conservative input-token estimate when provider usage is
unavailable.

A write-capable general sub-agent runs in a killable child process, so it
cannot share the parent's in-memory lock directly. Before spawning it, the
parent atomically reserves a finite call/token/cost lease and leaves one call
for itself. The child is constructed from that lease rather than the runtime's
full limits. A normal, internally consistent receipt is settled exactly only
when token counts came from the provider and the model is covered by the
versioned price catalog; the receipt is bound to a per-lease random nonce.
Estimated token counts, unpriced models, missing, malformed, replayed,
over-limit, or hard-killed results are charged at the full reserved lease. The
same conservative charge applies when a model reservation remains open or a
transmitted provider call returns without authoritative usage/cost data.
This intentionally favors a hard upper bound over optimistic accounting after
an unobservable process death.

Successful transport emits `model.started`, `model.completed`, and
`model.costed`; failed transport emits `model.started` and `model.failed`.
Events contain operation IDs, purpose, duration, bounded usage/cost projection,
status, operation count, confidence minima, and calibration identity. They do
not contain request text, model output, exception text, paths, or commands.
Completion events also record a whitelisted stop reason, a bounded content
character count, whether the output limit was reached, and the requested JSON
and reasoning controls. A length-truncated response is rejected even if its
prefix happens to contain valid JSON.

| Condition | Result |
| --- | --- |
| Feature disabled, fully covered request, or only non-instruction clauses | Zero semantic calls |
| Missing budget, inactive telemetry, unsupported adapter, or reserved final call | `UNKNOWN`; zero semantic calls |
| Explicit model abstention, malformed/tool-bearing/high-risk output, or low confidence | `UNKNOWN`; metered call |
| Classifier-local timeout or provider failure | `UNKNOWN`; balanced failure telemetry |
| User cancellation or parent deadline | Propagated as turn control flow |

## Privacy and configuration

Enabling the feature sends the current request plus a bounded excerpt of recent
user/assistant turns, the active task, and categorical previous intent to the
configured model provider. It is disabled by default for that reason.

Configure it in `~/.mini-code/.env`:

```dotenv
MINI_CODE_INTENT_SEMANTIC_ENABLED=true
MINI_CODE_INTENT_SEMANTIC_TIMEOUT_SECONDS=5.0
MINI_CODE_INTENT_SEMANTIC_MIN_CONFIDENCE=0.72
MINI_CODE_INTENT_SEMANTIC_MAX_INPUT_CHARS=4000
MINI_CODE_INTENT_SEMANTIC_MAX_OUTPUT_BYTES=4096
MINI_CODE_INTENT_SEMANTIC_MAX_OUTPUT_TOKENS=512
MINI_CODE_INTENT_SEMANTIC_JSON_MODE=true
MINI_CODE_INTENT_SEMANTIC_REASONING_EFFORT=low
MINI_CODE_INTENT_SEMANTIC_RESERVE_MODEL_CALLS=1
```

Bounds are validated: timeout `0.05–10` seconds, confidence `0.50–0.85`, input
`256–20000` characters, response `256–16384` bytes, output `32–1024` tokens,
and reserved calls `1–10`. Invalid values fall back conservatively or disable
construction.

Reasoning effort accepts `default`, `low`, `high`, or `max`; `default` sends no
override. JSON object output and reasoning effort are sent only when the
adapter advertises the corresponding capability. Currently this is restricted
to the exact `glm-5.3-flash` model on official BigModel completion routes;
other models and compatible proxies receive no extra provider parameters.
JSON object mode guarantees neither the local schema nor semantic correctness.
The local validator, budgets, cancellation, and zero-retry policy still apply.

The five-second default allows headroom above the approximately 1.4-second
median of the initial four-case GLM delivery probe. This is a timeout budget,
not a latency promise. Existing explicit settings take precedence; upgrading
does not edit the user's private configuration.

## Calibration from corrected outcomes

One observation represents one complete classifier response, not one operation:

- `rawConfidence` is the minimum raw confidence across all operations;
- `correct` is true only when the complete ordered operation set is correct;
- compound responses with any missing, extra, wrong, or misordered operation
  are labeled false;
- `split` identifies a pre-assigned fit or holdout partition.

The strict input schema contains no prompt, output, path, identifier, or free
text:

```json
{
  "schemaVersion": 1,
  "observations": [
    {"rawConfidence": 0.81, "correct": true, "split": "fit"},
    {"rawConfidence": 0.76, "correct": false, "split": "holdout"}
  ]
}
```

Fit a candidate with distinct partitions:

```bash
minicode-calibrate-intent observations.json calibration.json \
  --version 2026-09-experiment-v1 \
  --model-key openai/gpt-4o-mini \
  --fit-split fit \
  --evaluation-split holdout \
  --report-output calibration-report.json
```

The fitter uses Beta-smoothed fixed-width bins followed by weighted monotonic
PAV. The report compares raw and calibrated Brier score/ECE, coverage,
selective accuracy, and its Wilson 95% lower bound. Fitting and evaluation
partitions are mandatory and must differ. Neither the artifact nor the report
can contain arbitrary fields.

Artifacts are bound to the exact model key and classifier contract version.
Changing the prompt, output schema, generation policy, intent/action taxonomy,
or context policy requires a contract-version change and refitting. The current
contract is `intent-semantic-classifier-v3`; prior v2 experimental artifacts
are incompatible with the changed generation defaults.

## Honest maturity boundary

This repository does not contain a trustworthy corpus of real production
corrections. The built-in mapping is a conservative bootstrap policy with zero
claimed samples and `productionFit: false`; it is not empirical probability
calibration.

Schema v1 deliberately cannot claim `productionFit: true`. External candidate
artifacts are rejected by default. A controlled experiment may opt in only when
the model and classifier contract match exactly:

```dotenv
MINI_CODE_INTENT_SEMANTIC_CALIBRATION_PATH=/absolute/path/to/calibration.json
MINI_CODE_INTENT_SEMANTIC_ALLOW_UNREVIEWED_CALIBRATION=true
```

That flag means “run an unreviewed experiment,” not “promote to production.” A
future production schema still needs provenance, a fixed collection window,
minimum samples by intent/action, independent holdout review, explicit metric
gates, and a promotion record. There is no online self-training.
