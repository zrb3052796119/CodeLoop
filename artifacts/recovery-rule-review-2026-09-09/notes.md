# Review notes

- Canonical package: root minicode/; py-src and ts-src contain older copies.
- Working tree has extensive pre-existing changes. Review current files, do not reset or edit source.
- Core files: agent_reflection.py, reflection_evidence.py, reflection_synthesis.py, reflection_llm.py, memory_pipeline.py.
- Existing LLM reflection and curator Agent mean recommendation must account for current integration before proposing a new general-purpose Agent.

## Confirmed current behavior

- Production calls `ReflectionLLMConfig.from_runtime` in cybernetic_orchestrator.py:154; absent override it selects llm_shadow. The shadow branch keeps rules as production output (agent_reflection.py:446–447). Direct ReflectionEngine() uses dataclass default rule. Actual user runtime settings were not inspected.
- llm/gap_fill is whole-task rather than per-episode: any rule durable claim skips all model generation (agent_reflection.py:388–393).
- Semantic grounding is literal containment: reflection_synthesis.py:1471–1486 and 1519–1530. This prevents equivalent paraphrases and does not establish entailment of added claims.
- Production flow: agent_loop.py:5073–5123 → cybernetic_orchestrator.py:653 → MemoryPipeline.write_result (1998) → ReflectionEngine.reflect → extractor/synthesizer/validator/value gate → _build_reflection_claim_writes (2466) → commit_reflection_batch (2163).
- Existing LLMReflectionSynthesizer is single-call, structured, no tools (reflection_llm.py:1518), with separate adapter, budget, timeout/cancel and output limits. Reuse it as the dedicated learning worker.
- General task child Agent normally instantiates project MemoryManager (tools/task.py:2425–2448); avoid recursive memory learning. Turn-scoped child lifecycle final barrier + cancellation (subagent_lifecycle.py:185–263) is not a persistent background queue.
- Child reviewer reproduced cross-objective recovery admission; current run_command schema supports args, so argument-blind target matching is a production-input issue. Exact probes pending.

## Verification

`.venv-acceptance/bin/python -m pytest -q tests/test_reflection_synthesis.py tests/test_reflection_llm_modes.py tests/test_reflection_derived_recovery.py tests/test_reflection_operational_recovery.py tests/test_reflection_generic_tool_recovery.py tests/test_reflection_tool_semantics.py tests/test_reflection_persistence_authority.py`

Result: 128 passed in 1.79s. All local tests; no live model calls or production memory writes were performed for this review.

## Minor tooling issue

No tests/conftest.py exists; inspected selected test modules directly. This did not affect test execution.
