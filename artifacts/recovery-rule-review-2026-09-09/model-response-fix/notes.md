# Diagnosis notes

Prior experiment: 9 calls exceeded a 15-second local deadline. A 60-second diagnostic returned after 23.9 seconds with empty content, 1933 input tokens and 1200 completion tokens (the configured cap). Structured reflection dropped AgentStep diagnostics.stopReason and reasoningContent presence, so the cause was not determined.

Hypotheses to test: default deep reasoning consumes the constrained output budget; supported JSON and low-reasoning controls are unused; local request timeout incorrectly aborts the remaining episodes.

## Confirmed request mismatch

- Official API docs: GLM-5.3-Flash thinking cannot be disabled; reasoning_effort defaults to max and supports low/high/max. Sources: https://docs.z.ai/api-reference/llm/chat-completion and https://docs.z.ai/guides/capabilities/thinking .
- Current runtime matches the existing audited official-route capability. The adapter already advertises JSON-object output and low/high/max, but reflection previously sent neither control.
- Dedicated reflection now passes low reasoning + JSON-object only for advertised capabilities, per-call output limit, tools disabled and zero retries. Normal Agent requests and caller runtime remain unchanged. No unverified enable_thinking=false parameter is introduced.
- Five real-adapter wire tests pass. Four of them were run against the actual pre-control source snapshot after pytest HOME isolation: 3 failed at expected wire assertions and 1 legacy compatibility control passed. Initial direct preloading was rejected by repository HOME-isolation checks; the plugin now loads at pytest_sessionstart.
- Local provider deadline must become provider_timeout, while parent deadline and cancellation propagate. This keeps unrelated episodes running within their own remaining budget.

## Real probes and additional contract fix

- First fixed-control probe (same 15s / 1200 cap): 6.58 seconds, 323 completion tokens, nonempty JSON. Parsing rejected it as task_summary_mismatch because the model had naturally summarized the task title.
- Existing parser had always required exact host-derived task_summary and outcome, but the generation schema did not express those constants. Added per-request schema const values and a common instruction to copy them verbatim, without relaxing the parser. Prompt hash now includes that instruction.
- Second probe: 10.39 seconds, 515 output tokens, stop_reason=stop, reasoning_present=true, parsed successfully. One validated recovery claim was conservative/inferred and retained as an observation, not promoted into memory.
- Full verification: 50 related modules, 1106 passed in 9.05s; Ruff F and git diff --check passed. The new tests comprise 6 request/metadata contract tests, 14 response diagnostic tests, and 9 local-deadline tests.

## Successful full replay and candidate evidence repair

- Complete real replay: 10 provider calls, 10 parsed, 0 timeout/empty/truncation; 6 admitted lessons (4 recovery/confirmed, 2 warning/inferred), 4 inferred candidate observations. Median 8.77s; 19314 input and 4322 output tokens, cost unknown.
- Independent adjudication of all six admitted outputs found no blocking issue. Four candidate observations exposed a dropped failed retry and an unproven timeout cause in applicability. These observations were not admitted.
- Candidate projections now retain up to four failed same-objective followups, selected before limits and bounded by later independent recovery chains or successful followup. They keep failed status and complete call/event evidence, without entering successful context bindings. Nine new tests: before 6 failed/3 passed; after all pass.
- Added bounded-evidence and observed-applicability prompt rules. Targeted real replay: 2/2 parsed, ~8.02s and 7.89s, correct explicit failed rerun and purely observed timeout applicability, neither admitted. Independent review closed both specified regressions; one nonblocking attempted-versus-changed wording note remains documented.
- Final 51 modules: 1115 passed in 9.14s; Ruff F and git diff --check pass. Independent projection review: 55 passed, no blocking issues. User runtime, production default switch and durable memory selection unchanged.
