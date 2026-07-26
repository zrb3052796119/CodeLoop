# Reflection LLM Holdout

This dataset is independent from `reflection_golden`. Labels describe only
facts available from `TaskEvidence`; scripted provider responses are offline,
deterministic test fixtures and are not measurements of a real model.

Each expected claim cites extracted event IDs. Cases cover cross-event
synthesis, multilingual evidence, partial recovery, causal traps, low-value
tasks, prompt injection, redacted secret shapes, and provider/parser failures.
