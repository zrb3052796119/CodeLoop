# Verified Operational-Recovery Memory

Date: 2026-08-21

## Outcome

MiniCode now persists the recovery method when an agent repairs a failed tool
call by changing its structured input and then obtains a directly linked
successful result. This path is tool-agnostic: it does not require the tool to
appear in a verifier or built-in-tool allowlist. The method is stored as a
verified recovery claim, auto-approved when safe, and available to later
Memory retrieval.

The production incident shapes are covered directly:

- Literal shell operators passed as Ruff arguments are replaced by a working
  `bash -lc` invocation.
- A failing `test_runner` invocation is replaced by pytest under the project's
  virtual-environment Python.
- A missing `read_file` path is corrected after inspecting the workspace.
- An `edit_file` context mismatch is corrected using the current file text.
- A rejected search query is reformulated and successfully executed.
- A future tool name unknown to MiniCode follows the same evidence contract.

## Evidence contract

An operational recovery is emitted only when all of these conditions hold:

1. A recorded tool call failed and has ErrorEvidence.
2. A later invocation is materially different.
3. For arbitrary tools, both calls use the same tool and retain the same bounded
   objective: path calls share a stable directory suffix; non-path calls have
   bounded structured-input similarity and occur within eight calls.
4. For verifier commands that legitimately switch tools or interpreters, both
   invocations perform the same verification kind and share a resource or
   recognized verifier engine.
5. The later result is genuinely successful. A generic successful ToolResult
   becomes a targeted `tool_recovery` verification tied to that exact call.

The successful tool call is the recovery action and its later tool result is
the verification. This preserves the causal ordering required for a confirmed
claim. Unchanged retries, unrelated successes, different paths, same basenames
in different directories, and shell-masked zero-test/file-not-found output do
not qualify.

## Persisted method

The recovery action deterministically records the sanitized before/after
invocations, for example:

> Changed the lint invocation from `ruff check ...` to `bash -lc '...ruff check...'`; the corrected invocation then passed verification.

For arbitrary tools, the same deterministic form records the sanitized JSON
input before and after the correction. Sensitive keys are redacted before the
trace or Memory layer sees them. It does not treat the generic retry nudge as a
completed action and does not ask an LLM to invent the causal explanation.

## Verification

- Operational and generic recovery regressions: 29 passed.
- Reflection and related Memory regressions: 552 passed.
- Broad Memory/agent behavior: 817/817 effective behaviors passed after
  rerunning the 42 local HTTP cases with loopback permission.
- Complete repository regression: 4052 passed, 2 skipped.
- Ruff and whitespace checks: passed.

## Scope

This change affects future reflection runs. Existing pending error-only entries
are not silently rewritten because doing so would bypass their recorded
approval and provenance history.

The system deliberately does not auto-confirm an arbitrary cross-tool
substitution when there is no shared verification objective and no corrected
retry. In that shape the trace does not prove that the second tool fixed the
first tool's failure; forcing a durable causal lesson would trade recall for
false memory. Such an unverified suggestion remains non-durable or reviewable
until a successful call closes the evidence chain.
