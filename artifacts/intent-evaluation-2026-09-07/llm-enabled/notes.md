# Notes

- Baseline: primary 26/50; all labeled checks 23/50; semantic fallback disabled in actual runtime configuration.
- Same frozen 50 synthetic prompts and same scorer will be used, including bounded prior conversation for context cases.
- Production defaults: timeout 1.5s, acceptance threshold 0.72, output 256 tokens. One normal-agent call must remain available.

## Live default run
- Model: glm-5.3-flash, custom OpenAI-compatible adapter, configured open.bigmodel.cn endpoint. Credentials were used only for authentication and never persisted to artifacts.
- 18 calls all reached timeout; 32 cases skipped semantics. No score changes.
- 12,561 recorded input tokens are conservative timeout estimates; there is no successful provider usage/cost receipt. Do not interpret missing cost events as zero cost.
- Real RunObservation with isolated RunJournal persists events. User settings and production code stay unchanged; the runtime override enables only these evaluation processes.
- Independent harness review confirms production controls and correct cost-status field.

## 10-second diagnostic
- Scores: primary 33/50, all labeled checks 31/50 (+8 cases / +16 percentage points from baseline).
- Improvements: C02, C06, C09, C14, C27, C28, C29, C45. C45 fills the missing configure/update before test/execute.
- 10 accepted, seven invalid_response, one timeout, 32 skipped. Triggered-call median 5.59s; wall-clock 92.76s.
- Seven invalid responses all report 256 output tokens; six have no content. Do not claim the precise cause without raw completions.
- C26 review/compare is a plausible alternative; original scoring retained. C33 adds search/read and changes primary, so no pass-to-fail cases does not imply no semantic changes.
- Existing five false affirmative writes/deletes among 22 read/analyze cases remain identical.
- Provider receipts: 9,919 input + 3,306 output tokens; timeout adds 698 estimated input. Model pricing unavailable, not zero cost.
- Independent audit reproduced scores, checked all persisted events, matched model operation IDs, and confirmed source/label/runner hashes.
