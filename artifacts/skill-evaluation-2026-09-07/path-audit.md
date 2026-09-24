# Skill retrieval/injection path audit — 2026-09-07

Read-only production-chain inspection for the approximately 20 new task evaluation. No production code or task corpus was changed; no task actions or remote calls were executed by this audit. Runtime inspection printed a strict non-secret allowlist only.

## Actual current runtime

`load_runtime_config(cwd)` and `build_skill_router(cwd)` were inspected in a fresh Python process:

| Component | Current effective state |
| --- | --- |
| Primary model | `glm-5.3-flash`, provider `custom` |
| Intent semantic classifier | **Disabled**; configured timeout 5 s, minimum confidence 0.72 |
| Candidate semantic reranker | **Disabled**; configured timeout 1.5 s, minimum confidence 0.80 |
| Embedding matcher | **Present/enabled**, model `text-embedding-v3` |
| Deterministic alias matcher | Always constructed |
| Evidence feedback adapter | Present, but `decision_count=0` in the inspected runtime |
| Default top K | 5 |
| Embedding thresholds | signal 0.60; unknown-intent signal 0.67; boost 0.52 |
| Embedding failure cooldown | 300 s shared by workspace/provider identity |

Earlier intent evaluations enabled their classifier in memory; this did not persist the runtime switch. Do not describe the current global runtime as having the LLM fallback enabled. A separate evaluation-local variant may preserve the user's earlier preference, but it must be labelled as an override.

Embedding configuration comes from process environment and the private user `.mini-code/.env`, not project credentials. No credential or endpoint was printed. Matcher construction does not call the remote service. Routing can call it. A cold cache can perform bounded document batches and a query request; a failure can cause subsequent tasks to use aliases only for five minutes. Therefore `reranker_status=disabled` does **not** mean routing was network-free, and an embedding-enabled declaration does not establish that a query embedding succeeded.

## Canonical production chain

1. `minicode/tools/__init__.py:create_default_tool_registry` discovers Skills once, projects the catalog to dictionaries, and creates a `load_skill` tool bound to that same snapshot. `include_mcp=False` prevents launching external MCP processes. `build_tool_capability_snapshot(tools)` produces a frozen, registry-specific capability view.
2. All principal paths reach `minicode/skill_routing_turn.py:route_skill_turn`: CLI `_route_skills_for_prompt` (`minicode/main.py`), TUI background turn (`minicode/tui/input_handler.py`), and headless/dashboard runtime (`minicode/agent_runtime.py`). CLI passes its already parsed Intent; TUI/headless use injected semantic adapters at the common seam. Context must come from the actual prior message list via `IntentContext.from_messages`, not be silently dropped.
3. `SkillRouter.route` resolves explicit requests/denials against the real catalog, checks required-operation conflicts, computes topic/action/capability/alias/embedding scores, admits candidates, optionally reranks an already admitted subset, and returns selected Skills plus directories. Reranking cannot invent a new catalog candidate. Explicit authority is handled separately from inferred candidate recall.
4. `route_skill_turn` returns the canonical Intent and effective `prompt_skills`, `prompt_mode`, `outcome`, and `outcome_reason`. No selection normally yields a compact **name-only inventory fallback**, not an empty Skill section. Deny-all yields suppression. Unresolved/ambiguous names require clarification and have no load authority. An explicit Skill whose mandatory operation conflicts with a prohibition yields `constraint_conflict` and clarification.
5. `build_system_prompt(cwd, permission_summary, extras)` consumes this actual projection. Routed Skills inject **name, description, likely tools, and selected directory descriptions**. The complete SKILL.md body is **not automatically inserted**. Fallback injects names only and explicitly says they are inventory, not evidence of a match.
6. The model must choose `load_skill`. The real tool validates its name, checks the turn's `SkillLoadPolicy`, reads the exact snapshot-bound file, verifies its content digest, rejects stale bindings and bodies above 180,000 characters, records `SkillUsageTracker`, emits `skill.loaded`, and returns `SKILL/SOURCE/PATH` plus the full original file text.
7. `run_agent_turn` appends that successful result as a `tool_result` message and supplies it on the next model call. This subsequent call's input is the evidence that the body actually entered model context. A model saying it used a Skill is insufficient evidence.

Useful interfaces: `SkillRoutingTurnRequest`, `SkillRoutingTurnAdapters`, `route_skill_turn`; `build_skill_router`; `build_tool_capability_snapshot`; `build_system_prompt`; `create_load_skill_tool`; `ToolContext(_skill_load_policy=..., _skill_usage_tracker=..., _event_sink=...)`; `required_skill_names_for_routing`; `skill_load_policy_for_routing`; `run_agent_turn`.

## Two distinct gates

**Load-before-final:** `required_skill_names_for_routing` includes only selected Skills whose `explicitly_requested` is true. Implicit recommendations are advisory. The agent loop does not automatically load them and does not fail a final answer merely because the implicit recommendation was not loaded. For explicit names, one premature final triggers a nudge; a repeated attempt without successful loading ends as a failed turn. A successful load is tracked even without event logging.

**Mandatory workflow operations:** `supported_operations` defines supported request lanes; `required_operations` defines unavoidable steps. Router `_required_operation_denied` vetoes a Skill when the user's prohibition conflicts with a mandatory step, including typed/scoped test creation/execution constraints. This is admission protection, not a mechanism proving that every declared workflow step was ultimately executed. `skill_load_policy_for_routing` carries this veto to the actual loader so inventory fallback cannot bypass it.

## Evidence necessary per new case

| Layer | Required observation | Not sufficient |
| --- | --- | --- |
| Catalog | frozen qualified name/source/path, `content_digest`, `routing_identity_digest`, metadata and directory snapshot | Codex's displayed skills catalog; it is not necessarily MiniCode's catalog |
| Intent | primary pair, affirmed/negated operations, context use, semantic diagnostics | Prompt has a matching keyword |
| Candidate recall | `routing.inferred_candidates` qualified identities and `operation_indexes`; explicit requested identities separately | `candidate_count` alone, or finding an expected name in fallback inventory |
| Selection | ordered `selected_skills`/`selected`, scores/reasons, `explicitly_requested`, directories; classify top-1 vs any-top-K and forbidden extras separately | One relevant Skill among several irrelevant recommendations |
| Prompt injection | actual `build_system_prompt` output/hash, `prompt_mode`, exact selected description/directory presence; names-only fallback marked separately | Calling routing without building the prompt |
| Authority | requested/denied/unresolved/ambiguous/deny-all, constraint vetoes, required names and actual policy bindings | A prompt-level instruction alone |
| Body loading | real loader `ok`, full-body exact equality or hash tied to catalog digest, successful `skill.loaded` identity, no truncation; denied loads contain no body | A synthetic loader result or merely a file being readable |
| Agent handoff | next model input contains the exact real loader result; actual model-originated name recorded separately from harness-triggered loads | Scripted test being reported as spontaneous LLM choice |
| Remote behavior | model/embedding start/completion/error events, request purpose, token usage where available, timeout/circuit skip status | Configured enabled flag or HTTP success alone |

`SkillRoutingResult.to_dict()` intentionally omits the inferred candidate identities and authority resolution. An evaluator must capture those **in-memory attributes separately**; do not change production telemetry to expose them. The `skill.loaded` event proves only an already-successful load, not task correctness.

## Minimal safe evaluation design

1. Freeze approximately 20 new task labels against the discovered MiniCode catalog before running. Include explicit, natural paraphrase, compound, context-follow-up, deny-all, selective denial, nonexistent Skill, and unsupported/no-Skill requests. Distinguish expected candidate alternatives from exact sets that must all be selected.
2. Main retrieval run: actual `route_skill_turn`, actual discovered catalog and built-in capability snapshot, actual `build_system_prompt`; stop before running any task actions. If using current defaults, embedding is live and must be observed. If injecting a no-op embedding seam for an offline diagnostic, report that ablation explicitly and preserve the actual feedback adapter if comparing only embedding.
3. For every selected Skill, exercise the real snapshot-bound `load_skill` with the routing policy. This is a **loadability/body-integrity test**, not a spontaneous selection score. On negative cases, directly probe a representative prohibited identity under the policy and require denial without body leakage.
4. Use a small scripted adapter through the real `run_agent_turn` with **only the real load_skill tool exposed** to verify message handoff and explicit final-gate behavior. The script should capture the next-call messages and assert the exact returned body is present. Keep this infrastructure contract result separate from retrieval accuracy and from LLM skill-choice accuracy.
5. If measuring actual model choice, use the configured model, authentic routed system prompt and task, but restrict execution to `load_skill` and stop at the first non-load action/final or a fixed small call budget. Record natural first choice separately from any load occurring after the explicit required-Skill nudge. Never execute the requested implementation, shell, browser, write, or deployment actions. If a meta instruction to stop after selection is added, document that this is a controlled selection probe rather than full normal task completion.
6. Keep results and journals under the evaluation directory; freeze source, catalog, cases, scorer, prompt and loader digests. Do not report these synthetic tasks as normal successful workflow outcomes to the live Skill evidence ledger.

## Existing verification references inspected

- `tests/test_skill_routing_turn.py`: canonical context/adapters/prompt projection contracts.
- `tests/test_skill_load_policy.py`: selected-only loading, denial before opening body, unresolved/deny-all fail-closed and immutable binding checks.
- `tests/test_agent_loop.py:test_routed_skill_must_be_loaded_before_final_is_accepted` and `test_routed_skill_refusal_is_not_reported_as_success`: explicit final gate and successful tool-result handoff.

These existing fixtures were examined only to identify production interfaces and assertions. They are not new evaluation tasks and must not be counted in the new 20-task accuracy denominator.

## Follow-up: real choice probe and incidental storage

The root's proposed probe is preferable to restricting model-visible tools: retain the full built-in tool schema and actual routed prompt, call the real model, execute **only** model-originated `load_skill` calls, and stop on any other requested task action without executing it. This avoids making Skill choice artificially easy by deleting competing tools. A call may naturally begin with `read_file` or `list_files`; that is “no immediate Skill load observed before the probe stopped,” not proof the model would never load a Skill later in a full run.

Concrete call API:

```python
from minicode.model_call_control import call_model_next

step = call_model_next(
    model,
    messages,
    optional_kwargs={},
    deadline_monotonic=deadline,
)
# step.type: assistant | tool_calls
# step.calls: [{id, toolName, input}, ...]
# step.content, step.reasoningContent, step.usage preserve response evidence.
```

Use the actual model registry adapter constructed with the complete tool registry. `OpenAIModelAdapter.next` accepts bounded output/retry overrides if needed, but such probe-specific controls should be frozen and reported. `call_model_next` forwards cancellation/deadline controls; it does not itself emit model usage events or settle a shared turn budget. An evaluator calling it directly must account for calls/usage separately, following the existing production/evaluation observation conventions.

For every legitimate loader execution, use `tools.execute("load_skill", call["input"], context)` (or run the actual tool after its actual validator). The `ToolContext` must carry the true `skill_load_policy_for_routing(routing)`, a real `SkillUsageTracker`, and the evaluation event sink. A loader with no policy would test a weaker, non-production authorization path.

The next input must preserve the provider assistant turn's grouping, exactly as the loop does:

```python
messages.append({
    "role": "assistant_tool_call",
    "toolUseId": call["id"], "toolName": call["toolName"],
    "input": call["input"], "assistantTurnId": stable_id_for_this_step,
    # On the first call only, preserve step.content and step.reasoningContent
    # in content / reasoningContent when present.
})
messages.append({
    "role": "tool_result",
    "toolUseId": call["id"], "toolName": call["toolName"],
    "content": result.output, "isError": not result.ok,
})
```

`minicode/openai_adapter.py:_to_openai_messages` groups `assistant_tool_call` rows with a common `assistantTurnId`, converts `tool_result` to provider role `tool`, and retains its content verbatim. Checking the exact returned loader text in both the next-call internal messages and this production conversion proves the body reaches the payload boundary; a completed next provider call then provides the remote handoff evidence. For mixed batches containing any non-load call, do not silently omit that original call or fabricate its success. Stop the probe, or explicitly report a separately controlled continuation with an honest blocked result. A successful load in such a stopped batch is not yet a confirmed subsequent-model handoff.

This hand-driven probe does not execute the agent loop's explicit final gate. Validate that independently with the existing real-loop gate tests; if reproducing a required-load nudge manually, report it as a simulated nudge and keep natural first choice separate.

Observed endpoint and storage (only hostname/path, no secret):

- Embedding host `dashscope.aliyuncs.com`, path `/compatible-mode/v1/embeddings`.
- Embedding cache `/Users/zhourunbo/code/coding agent/MiniCode-Python-main/.mini-code/skill-embeddings.json`; routing may atomically fill/update it even though task actions are not executed.
- `create_default_tool_registry` calls `observe_skill_catalog_safely`, which may append previously unseen identities to `/Users/zhourunbo/code/coding agent/MiniCode-Python-main/.mini-code/skill_versions.json` using `.skill_versions.lock` in the same directory. This is normal catalog bookkeeping, not task execution. Existing identical versions are not appended again.
- Default `RunJournal` runs root is `/Users/zhourunbo/.mini-code/dashboard/workspaces/ws_2e3c835ba91e91fa/runs`. Pass an evaluation-local `data_dir` for probe events and avoid success attribution to the production evidence ledger.
- `build_skill_router` reads existing evidence via `SkillEvidenceLedger(RunJournal(workspace)).snapshot()`; current decision count is zero. It does not publish synthetic task outcomes by itself.
