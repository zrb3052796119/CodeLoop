# Controlled handoff supplement

This supplement answers only whether an already successful, model-originated Skill body can be sent intact on a real subsequent model call while preventing task execution. It is **separate from the original spontaneous retrieval/loading scores** and does not rerun timed-out or failed selection cases.

Frozen original evaluation manifest: `61108d3a2fabcfdc394de9f24361f291ab4218fb143c22132bba849ad8981bbd` (all 290 files unchanged).

Supplement script: `controlled_handoff.py`, SHA-256 `5e0016c774b68c9dc3395140e701fc1821347cc66ebcba49c74c71ad8b77fca1`.

Eligibility is based solely on actual probe evidence, without expected labels: the original final completed response contains a mixed batch of `load_skill` and non-load calls; at least one load in that batch actually succeeded with exact catalog body integrity and has no confirmed subsequent-provider handoff; total original model calls plus this one remain at most four. The original 20-task live run must be complete before supplemental live execution.

Local inspection found 11 eligible tasks: S01, S02, S06, S07, S08, S09, S10, S11, S12, S13, S17. All had one original model call and will have at most two after this supplement. S07 and S17 remain eligible even though their loaded Skill may be incorrect: eligibility does not use answer labels or conceal retrieval mistakes. S15 already has natural body handoff and is excluded. S18/S20 timeouts are excluded.

Each request begins with the **exact original pre-response message list**. The entire actual final assistant response is reconstructed with every tool call, original call ID, input, shared assistant turn grouping, text and reasoning. Previously executed loader calls receive their exact original outputs/statuses. Every other original call receives `isError=true` and this factual notice:

> Controlled evaluation: this tool was not executed. No file contents, command output, or task results were obtained. Do not infer file contents or task outcomes from this error. 本评测没有执行此工具；不得据此推断文件内容或任务结果。

This feedback is an explicit experimental intervention. It permits a structurally complete provider turn while truthfully saying that no task tools ran. It must not be described as normal uninterrupted task execution or spontaneous handoff. No expected labels, additional user instructions, substitute task text, fabricated file contents, or successful fake task results are supplied.

The same production core tool schemas remain visible. The configured production adapter and fixed GLM endpoint are used for exactly **one** request per eligible task, with a 30-second deadline, zero retries and 2,048 output-token limit. The returned response is recorded and execution stops immediately; **there is no tool-dispatch site in this script**, including for any new `load_skill` request. The script does not call the embedding service.

Original result/request/response/schema hashes, full body hashes and the original catalog policy are bound before requesting. The request artifact records internal messages, production OpenAI message conversion, their hashes, exact body presence, and all error-feedback interventions. An isolated journal records started/completed/failed events and usage. A failed provider call is not a confirmed handoff. All artifacts are written only to a new `controlled-handoff/` directory, except the unchanged production registry's normal catalog-version observation behavior.

Local validation (no network): reconstructed all 11 eligible cases; checked original message prefixes unchanged, every original call replayed in order, original loader results exact, all unexecuted notices marked errors, original 290-input freeze unchanged, and zero tool execution dispatch sites by AST inspection. Critical Ruff checks passed.

Usage:

```text
python3 .repair-work/skill-evaluation-2026-09-07/controlled_handoff.py --inspect
python3 .repair-work/skill-evaluation-2026-09-07/controlled_handoff.py --live
```

`--inspect` is the default and performs no network request. Root owns live execution under the user's existing authorization. The script refuses to overwrite an existing supplemental run. Report supplemental body-delivery counts beside the original natural handoff count; do not add them to the original selection or end-to-end scores.
