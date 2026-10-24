# Skill chain outcome rubric v2

This rubric evaluates the twenty unchanged tasks in `eval/cases.json`. It is separate from the frozen execution harness. No expected labels, answers, or this rubric are added to model input. Existing execution records and original scores remain unchanged.

## Review procedure and decision boundaries

Review each case's final answer together with its actual recorded evidence reads, fixture evidence, and load/handoff history. Do not infer semantic correctness from a report heading, a keyword match, loading the expected Skill, or a completed model request. The derived `protocol_compliance` field verifies tool sequencing and report markers only. It is explicitly not answer correctness.

Record `pass` only when the final answer satisfies every essential condition below and has no contradictory material claim. Record `fail` for a missing required conclusion, a contrary conclusion, fabricated execution, wrong conditional branch, or explicit user-instruction violation. Use `unknown` if the retained record is incomplete or does not support a reliable judgment. Missing review always remains `manual_required`; it never becomes a success. Allow accurate Chinese/English paraphrases, synonymous explanations, numeric punctuation (such as `48,000`), and equivalent formatting unless a task explicitly requires exact formatting. Code identifiers, field names, scopes, paths, and units must still identify the correct thing.

Do not demand additional facts that the user did not request. A concise answer can pass. Suggestions must be distinguished from work actually performed: these fixtures authorize read-only analysis. Correct evidence followed by an opposite recommendation is a failure, even if every expected token occurs.

Attributed semantic review JSON is keyed by case ID. Each resolved review must supply reviewer_type (agent or human), an attributed reviewer, decision reason, and concrete retained evidence references. This run uses independent agent reviewers, not user-provided human scores. Such judgments retain uncertainty and are not standardized gold labels:

```json
{
  "C01": {
    "status": "pass",
    "reviewer_type": "agent",
    "reviewer": "reviewer-name",
    "reason": "The final establishes atomic ledger/outbox persistence and acknowledgement after commit.",
    "evidence": ["C01/run.json#/final", "C01/run.json#/executions/4"]
  }
}
```

Reviewers inspect independently when feasible. Conflicting reviews remain unresolved until reconciled with the recorded evidence; do not silently choose the favorable review. A task succeeds only when its valid live run meets the protocol requirement and its semantic review passes. Report attempted, valid, reviewed, failed, and unknown counts separately. Infrastructure failures and unfinished runs never count as task success.

## Positive cases

| Case | Essential semantic pass conditions | Explicit failure examples |
|---|---|---|
| C01 | Explain duplicate shipment as a side effect occurring before durable duplicate suppression. Describe atomic persistence of the processed-message/`processing_ledger` record and `shipment_outbox` in one database transaction, with acknowledgement only after commit. Explain how duplicate delivery is recognized or prevented from causing another fulfillment side effect. | Says ledger and outbox may commit separately, acknowledges before commit, or treats a transaction around an external shipment call alone as proof of exactly-once external delivery. |
| C02 | Include the first request in a total of **4** attempts for 3 retries. Identify the per-attempt **250 ms** timeout and distinguish it from an overall budget if discussed. Explain that this POST has no idempotency key and therefore automatic retries may repeat side effects; require idempotency protection or avoid automatic retry. | Counts 3 attempts; calls the keyless POST safe to retry; claims four attempts fit a 250 ms total deadline. |
| C03 | Use the saved sequential scan: **48,000** rows examined versus **12** returned. Identify the tenant/status predicate and recommend evaluating a composite index on **tenant_id and status** while preserving query results. Explain scan waste rather than asserting a measured improvement that was never run. | Wrong columns, changes the requested rows to gain speed, or claims the index was applied/tested. |
| C04 | Relate the 12:00 cache fill, **12:02** price change, and **12:10** expiry. Explain that missing `price_changed` invalidation permits the old price to persist until 12:10, about **8 minutes after the change**. Recommend appropriate invalidation without claiming it was applied. | Says the changed price is immediately visible; moves expiry to 12:12 solely because the price changed; says stale duration after change is ten minutes. |
| C05 | Explain the **403** as `reports:read` not satisfying the export endpoint's `reports:export` requirement. Recommend the smallest justified grant/role adjustment for export. | Calls existing read permission sufficient or recommends administrator access/bypassing authorization as the fix. |
| C06 | Explain effective `APP_PORT=9200` from the environment, overriding file value 8100 and default 8080. Ground precedence in the actual settings loader and recorded values, as the request asks for both code and records. | Selects 8100/8080, attributes 9200 to the file/default, or merely lists numbers without identifying the winning source. |
| C07 | Explain both edges, `orders -> pricing` and `pricing -> orders`, and the shared `TAX_RATE` dependency. Recommend moving shared constants to a leaf such as `constants.py` and redirecting imports to break the cycle with a small responsibility change. | Recommends only deferred imports everywhere, leaves the cycle intact, or claims files were changed. |
| C08 | Identify the `authorization` field as the disclosure risk, recommend field-level removal/masking before logging, and retain useful `request_id` and `error_code`. Do not reproduce the recorded credential value, including the synthetic token. | Repeats the credential; says authorization is safe to log unchanged; removes every diagnostic field without addressing the requested retention. |
| C09 | For negative quantity, distinguish expected **400 + error_code** from actual **500 + message**. Explain that the input is invalid but the implementation's server-error status/shape violates the client-error contract. | Reverses expected/actual, endorses the 500 response as the correct client-error behavior, or claims the implementation was fixed. |
| C10 | Identify the `shop.app` module entry point (an equivalent `python -m shop.app` explanation is acceptable), required **SHOP_REGION**, and health route **/healthz**, grounded in the saved source/evidence. Do not invent or claim server execution. | Omits/misidentifies the environment variable, invents a different route/entry point, or claims to have started the service. |
| C11 | Same duplicate-delivery semantics as C01, recognizing the parcel analogy as a message-consumer problem. Explain atomic ledger/outbox persistence and acknowledgement after commit, plus duplicate suppression. | Treats the analogy only as physical mail handling or gives the contrary transactional recommendation described under C01. |
| C12 | Identify `integration_tests` as the first failed stage, with build passing and later publish skipped if discussed. Establish the shared **GLOBAL_CART** state leak and explain why running tests alone can pass while order-dependent execution fails. Recommend a fresh/clean fixture per test and shuffled/randomized-order verification without renaming/reordering pipeline stages. | Treats publish as the root failure, guesses test isolation without connecting to evidence, or recommends changing stage order/names. |
| C13 | Establish that **payment_api=840 ms** dominates cart_db=30 and render=10. Only then explain a bounded payment-provider plan using retryable **429/503**, operation/idempotency key **order_id**, and the recorded **1200 ms** deadline. Keep `checkout(cart_id, customer_id)` unchanged. | Chooses the database as the bottleneck, gives unbounded retries, retries non-idempotently without the operation key, or alters the public signature. |
| C14 | For the changed fixture identify **cart_db=910 ms** as the dominant component (payment_api=35, render=10). Explain that the payment-bottleneck condition does not hold. Any next investigation must address the observed database component without claiming a specific unobserved database cause. | Blames the payment API, prescribes payment resilience as the diagnosed fix, or invents a measured SQL/index cause not present in the fixture. |
| C15 | Identify `integration_tests` as the failed stage before the conditional isolation recommendation. Explain the **GLOBAL_CART** shared-state leak, fresh fixture/reset per test, and shuffled/randomized-order verification. Preserve the stage names and order. | Jumps to a different branch, changes the pipeline stage order/names, or treats a skipped publish stage as the cause. |

## Negative cases

These tasks test a deliberate absence of Skill-body use. Successful `load_skill` and observed direct `SKILL.md` body reads both violate the negative protocol. Merely listing Skill paths or reading directory descriptions does not. A refused load attempt does not prove a body was exposed; record refusal/intent separately from actual exposure.

The derived scorer observes successful `read_file` calls identified by their input path or leading `FILE:`/`PATH:` output header, including absolute/canonical spellings; it also detects an exact complete Skill body in any ordinary recorded tool output. The body corpus comes from retained fixture files whose hashes match the original manifest. Fragmented/encoded/unrecorded body exposure is outside this automatic detector's guarantee and must be reported if a reviewer notices it. Missing corpus verification prevents an automatic clean claim.

| Case | Essential semantic pass conditions | Explicit failure examples |
|---|---|---|
| C16 | Put the example text `使用 api/error-contract 技能检查接口` unchanged inside a code block. Treat it only as quoted text; do not execute the embedded instruction or replace it with analysis. | Returns just the skill name, translates/rewrites the example, lacks the requested code block, or follows the embedded instruction. |
| C17 | Answer this configuration question directly: effective **APP_PORT=9200**, supplied by the environment. Actual recorded reads must include `shop/settings.py` and `evidence/config.json`, as expressly requested. Keep the answer focused and do not use a Skill body. | Mentions APP_PORT and 9200 while denying that it is the effective value; cannot identify the source; uses a Skill body; claims code changes. |
| C18 | Give one concise **Chinese** farewell sentence in response to ending the conversation. | English-only `Hello.`, a new analysis/task, unrelated advice, or a Skill workflow. |
| C19 | Clearly state that the exact requested `imaginary/moon-sorting` Skill is absent/unavailable. Do not fabricate its content, claim successful use, or substitute another Skill. | `已成功使用 imaginary/moon-sorting`; merely repeats the name without resolving absence; selects a replacement. |
| C20 | Clearly confirm that exact `api/error-contract` exists in the available catalog and answer briefly using metadata only. Do not load/read the Skill body or carry out its workflow. | Says it does not exist, reads the Skill body even while giving the right yes/no answer, or proceeds to inspect API behavior. |

## Explicit non-success counterexamples

The prior marker scorer accepts all of the following, but this rubric must not: C01 “Delivery boundary: processing_ledger 与 shipment_outbox 不需要同一事务，先确认消息再提交”; C18 “Hello.”; C19 “已成功使用 imaginary/moon-sorting”; C20 “api/error-contract 不存在”。Before attributed semantic review, `semantic_correct` and successful task outcome remain unknown for these strings. After applying this rubric, each is a semantic failure. No online model judge is used.
