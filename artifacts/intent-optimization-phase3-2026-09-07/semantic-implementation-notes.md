# Phase 3 semantic admission implementation

## Contract
Given a current request containing attributed source text or a bounded request for an explanation, parsing with a fixed high-confidence semantic proposal must not confer write/execution authority from that source; actual current writing requests retain their own original spans.

## Implementation
- `minicode/intent_request_roles.py` recognizes complete quoted-content attribution statements, such as `网页按钮的文案是…` and `The button label is…`. Source subjects are finite noun chains. Arbitrary Chinese/English prefixes are deliberately not accepted: they can contain actual commands such as 确保/调整 or `The task is to ensure`.
- The parser extends its existing protected literal spans with those statements, so deterministic matching uses the same background boundary.
- The semantic fallback removes only those attribution statements from its source-aligned active text. Ordinary quoted objects and current colloquial request tails remain available, with original offsets.
- Proposal validation applies a narrow explanation-only action restriction to the current semantic request span. A proposal may still supply a read/analyze/compare label. Unknown colloquial requests and visible compound requests remain eligible for the existing semantic fallback.
- Existing high-risk semantic action exclusions, confidence bounds, call budgets, cancellation and provider configuration remain unchanged.

## Verified final evidence
- Exact final test file: `tests/test_intent_semantic_authority_phase3.py`, SHA-256 `f16a4a1e6638f1d9c110eae9729faae9825ad48d6fa786d11ba89c68ad060b2c`.
- Real pre-phase-3 production snapshot: `.repair-work/intent-phase3/baseline`.
- Same-byte proof: **57 current tests passed; baseline 34 failed and 23 passed; PROVEN**. `semantic-proof.json` and `semantic-authority-proof.json` are identical copies of the verifier output.
- Related semantic / literal / phase-1 discourse / phase-2 request and entity regressions: **356 passed** (`semantic-related-regression.log`).
- Critical Ruff checks on the new helper and test: passed.
- Independent review rechecked its frozen 28 probes after the correction. The previously swallowed genuine commands (确保, 保证, 调整, 修复 plus comma background) now preserve baseline behavior. Review results are in `independent-review-final.json`.

## Proof interpretation and remaining limits
- The fixed provider emits the highest accepted confidence, 0.85, and a fabricated clause; production must retain its own text/spans.
- CODE CREATE/UPDATE and CONFIGURE UPDATE are actual new effect-admission regressions. TEST EXECUTE was already excluded globally; those cases also check that irrelevant source payload is not sent to the model, and must not be counted as newly blocked execution authority.
- The 34 baseline failures include both unwanted effect acceptance and source-isolation failures. They are not 34 independently sampled real-world accuracy gains.
- The attribution grammar is intentionally bounded; source phrasing outside the recognized noun frames is not claimed covered. The explanation fence handles a bounded single-clause meaning/impact request. Visible coordination declines this narrow restriction to preserve possible independent commands.
- No real model calls and no phase-3 holdout data were used for implementation or tests. No source changes are planned after this freeze unless independent review finds a concrete new regression.
