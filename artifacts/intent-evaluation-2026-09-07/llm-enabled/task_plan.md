# LLM semantic fallback paired evaluation

## Goal
Enable the production semantic fallback for a live replay of the same 50 frozen requests; quantify changes against the disabled baseline.

## Phases
- [x] Inspect current provider, configuration and required production controls.
- [x] Build a replay harness retaining original prompts, labels and scoring.
- [x] Run with semantic fallback enabled and otherwise unchanged defaults.
- [x] Analyze improvements, regressions, remaining failures, call counts and latency.
- [x] Deliver paired report and reproducible evidence.

## Decisions
- User authorizes enabling semantic inference and sending the synthetic evaluation prompts to the configured model.
- Preserve baseline outputs and labels; no production parser/prompt changes.
- Enable through the evaluation runtime first; isolate the experiment from unrelated active sessions.
- Use the actual configured provider/model, budget, cancellation, deadline and durable event telemetry.
- Never execute actions described in test questions; never print credentials.
- Report default timeout results separately from any justified diagnostic timeout sensitivity run.

## Status
Complete. Default run: 18 live calls all timeout, primary 26/50 and all labeled checks 23/50 unchanged. Separate 10-second run: primary 33/50; all labeled checks 31/50; eight improvements and no pass-to-fail cases; 18 calls (10 accepted, 7 invalid, 1 timeout). Both independent scoring/journal audits passed. Report and all 50 paired rows delivered. No production code, labels or global configuration changed.

## Errors
- Initial source-location guesses did not exist; located factory in model_registry.py using rg.
- All default semantic calls timed out; retained the run intact and started a separate 10-second diagnostic.
