# Response diagnostics repair

The structured generation wrapper now retains only `stop_reason` (a fixed
allowlist) and `reasoning_present` (boolean or unknown/null). The adapter's
reasoning text and arbitrary provider strings never enter these fields.
Anthropic thinking block types can establish presence without copying blocks.

These fields propagate through `LLMSynthesisAttempt`, each recovery episode
outcome, the explicit offline review representation, and the synthetic
evaluator's physical-call capture. Missing diagnostics, unrecognized stop
reasons, provider errors, and calls skipped by budgets retain unknown/null.

`length` and `max_tokens` reject the response as `output_limit_reached` before
JSON parsing, including a syntactically complete, otherwise valid candidate.
An ordinary empty response still reports `empty_response`. Evaluation summaries
count these cases separately. Provider usage remains recorded for rejected
responses.

Validation used the real OpenAI and Anthropic adapter implementations with
HTTP intercepted by deterministic local responses. No live model or formal
memory was used in this subtask.

- Before implementation: the new 14 cases failed; the empty length response
  concretely returned `empty_response` instead of `output_limit_reached`.
- After implementation: all 14 cases passed, including full valid JSON plus a
  length stop, ordinary completion, unknown metadata, private reasoning, and
  episode/evaluator propagation.
- Related regression surface: 164 passed across diagnostics, reflection LLM,
  structured client, recovery lesson, production shadow, and evaluator tests.
- Ruff F checks passed for the four changed source/test files.

Reproduce:

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_reflection_response_diagnostics.py tests/test_reflection_llm.py tests/test_reflection_structured_client.py tests/test_recovery_lesson.py tests/test_recovery_lesson_shadow.py tests/test_recovery_lesson_shadow_evaluator.py
```
