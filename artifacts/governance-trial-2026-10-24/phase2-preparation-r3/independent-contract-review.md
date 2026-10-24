# Independent closure review — public contract draft v3

**GO for the static contract review: ICR-1 through ICR-4 are closed.** This is not authorization to start a trial and does not revise r2 outcomes.

The reviewed files exactly match the author-provided v3 SHA256 identities:
- `public-contract-amendment-draft.md`: `cbd13e4e908ac4bd33a4e81aa17e9da43510a97458bfc9a5199990f3c18ba7ba`
- `public-contract-amendment-draft.json`: `5f2bc5e4a595ffc50193f866d5a8831a5bc4bea9c51937c8e14bd1448003ea12`
- `public-task-appendices.json`: `3be39861adf8bcc147459578e28948cf259f931dad01e96f8cc90bb3ebccc09f`

| Finding | Status | Basis |
|---|---|---|
| ICR-1 | Closed | Eight distinct image result events are explicitly listed, including unsupported_image_extension. Raw runtime predicates for hasModel/hasKey/httpsRoute/visualOnly are disclosed; seven testcases remains a minimum rather than an implied seven-event limit. |
| ICR-2 | Closed | Copy destination must be a child of frozen-workspace-*; injected WorkflowIsolationError type/text propagates; actual apply goes through original _run_checked with no --check and explicit use_operation_guard=False. Names, call shape and error observation are now stated together. |
| ICR-3 | Closed | All five read appendices state pytest-main-thread-only counted execution, Python assert syntax requirement, and separate 70-second current/historical child limits without enlarging outer evaluation budget. |
| ICR-4 | Closed | The added no-zero missing-usage behavior was removed. The revised text preserves existing missing subfield and source semantics. |

All 16 original prompt/oracle hashes still match the frozen r2 input. All 16 standalone appendix hashes and proposed appended-prompt hashes recompute correctly, and each standalone appendix appears in the Markdown and contains the corresponding JSON row text. Only the intended seven appendices changed: five read tasks, cache-fallback, and frozen-change. The other nine are byte-equal to v2.

The original changes-requested review remains untouched, and its copies under `contract-draft-history/v2/` are byte-equal. This new file records closure separately; it does not erase the original findings.

No new blocker was found in the revised text. No oracle or tests were executed; no model output was read; no API, trial write, draft edit, or frozen/repository write occurred. Offline mechanics validation and a common new freeze remain prerequisites of any separately authorized new experiment.

Exact per-case identities and closure locations are in `public-contract-independent-review-v3.json`.
