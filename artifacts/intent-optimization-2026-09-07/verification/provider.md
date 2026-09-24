# Semantic provider controls

Implemented opt-in per-call JSON object output and GLM-5.3-Flash reasoning effort in the existing OpenAI-compatible adapter. No global model defaults were changed. No real provider calls were made by this subtask.

## Contract

- `next(json_object_override: bool = False, reasoning_effort_override: str | None = None)`
- `supports_json_object: bool`, independent of `supports_native_json_schema`.
- `supported_reasoning_efforts: tuple[str, ...]`, currently `("low", "high", "max")` only for audited GLM routes.
- The factory grants these capabilities only for exact model `glm-5.3-flash`, provider `custom` or `openai`, HTTPS host `open.bigmodel.cn`, default port or 443, and full `/api/paas/v4/chat/completions` or `/api/coding/paas/v4/chat/completions` path.
- Arbitrary custom hosts, unverified paths, other models and OpenRouter do not inherit support. The factory overwrites runtime capability claims; active control calls also re-check the actual route to prevent stale capability reuse after route mutation.
- JSON object and native JSON Schema overrides are mutually exclusive. Both disable tools; local semantic payload validation remains the classifier’s responsibility.
- Reasoning effort accepts only low/high/max and is sent only when explicitly overridden. Normal calls retain existing request bodies and tools; overrides do not mutate adapter runtime.
- Existing token, response-byte, timeout, deadline, cancellation, retry and diagnostics behavior is preserved. Existing `StepDiagnostics.stopReason` and `ModelUsage` can be consumed without recording reasoning content.

## Validation

37 new tests inspect real request construction through a captured urllib Request, using synthetic response bodies. Together with model-call controls, native schema, reasoning replay, registry config and subagent routing tests: **129 passed in 2.20 seconds**.

The exact three parameterized request tests were run against the pre-fix source snapshot after verifying the imported adapter’s absolute path. All three fail because the baseline `next` has no JSON-object override, while the same tests pass on the current implementation. Initial naive pytest invocation from the snapshot directory was rejected as evidence: repo config had selected current imports. The final isolation explicitly preloads the snapshot adapter before pytest.

The bundled `test-counterexample/scripts/prove_regression.py` additionally copied the identical complete 37-test file into an isolated clone of the snapshot: current **37 passed**, baseline **36 failed, 1 passed**, status **PROVEN** (`proof.json`). Failures are the intended missing new parameters/capability attributes; the pre-existing unsupported-native-schema check still passes.

`python3 -m ruff` was unavailable because Ruff is not installed in this runtime. Python imports and pytest collection succeeded.

Scoped production diff is in `changes.patch`; `validation.json` records source SHA-256 hashes and the verification result. Earlier user changes remain intact.

## References and limits

The official [GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash) documents the three reasoning effort levels and defaults. BigModel’s [structured output guide](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output) was supplied in the task as the JSON-object contract; a fresh tool fetch timed out during this subtask. Wire tests prove emitted parameters and retained bounds. They do not prove live endpoint acceptance or a classification accuracy improvement; the parent task’s live comparison supplies that evidence.
