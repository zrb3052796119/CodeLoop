# P2 Learning Evidence and Memory Value Hardening

Date: 2026-08-21

## Outcome

MiniCode's read-only learning acceptance is now evidence-bearing instead of
response-bearing. A North-Star task cannot pass merely because the model
repeats the expected text: the runner verifies in-order, same-Turn tool
operation pairs and the persistent-memory learning cases require both an
observed recovery and a successful read on every turn.

The canonical `learningSuccess=null` behavior for unverified read-only answers
was deliberately preserved. Assistant prose is still not allowed to certify
its own correctness. Independently evidenced recovery remains sufficient for
the reflection pipeline to approve and persist a lesson.

## Repairs

### Strict tool-operation oracles

- Added `tool_succeeded` and `tool_failed` to the live North-Star runner.
- A call counts only when `tool.started` precedes `tool.finished` in the same
  Turn, both carry the same non-empty operation ID and exact tool name, and the
  finish event is paired with the required outcome.
- Duplicate finish events, orphan events, cross-Turn joins, wrong tool names
  and wrong outcomes do not count.
- `min` is bounded and `everyTurn=true` can require the threshold independently
  in every Turn.
- All eight persistent-memory cases require a successful `read_file` in every
  Turn. The two learning-chain cases additionally require an observed
  `read_file` failure.

### Premature final-answer recovery

The first real v2 smoke exposed a production Agent Loop defect: after finding a
corrected path, the model returned `Let me read it.` as its final answer and
the Turn stopped before the promised tool action. The loop now recognizes a
bounded set of English and Chinese next-action tails after tool execution,
records them as progress, and requests the concrete action. Explicit final
content is never reclassified and the retry is capped at two attempts.

### Deterministic learning experiment

The second v2 smoke proved successful reads but created no lesson because the
model avoided the intentionally wrong path by inspecting first. The v3
learning prompts therefore preregister the first exact `read_file` call and
require the resulting error through `tool_failed`. This distinguishes a
verified recovery experiment from ordinary navigation and avoids lowering the
Memory value gate.

### Cross-reload lesson value proof

A new end-to-end test performs the complete durable lifecycle:

1. a failed operation, recovery and passing verifier create one approved
   lesson;
2. a fresh `MemoryManager` and `MemoryPipeline` reload and inject that lesson;
3. one negative Turn verdict plus a failed verification update only the exact
   rendered lesson;
4. a neighboring unrelated entry receives no retrieval, injection or feedback
   counters.

## Real-model evidence

The frozen v3 manifest is
`artifacts/north-star-memory-compaction-20-v3/manifest.json`.

- Suite: `minicode-memory-compaction-live-20-2026-08-21-v3`
- Shape: 17 cases / 20 tasks
- Manifest SHA-256:
  `bf39c66b07e34cd3127bb068d587bc0ec1bf047ac1a18831df71458856a2797d`
- Live smoke: `memory-chain-auth`, two Turns, 8/8 oracles
- Result SHA-256:
  `e0820a7ceb48e3efd13c89e050e64994a045a310a01d4a526137e96b275dcb12`
- Usage: 12 model calls, 76,705 input tokens, 1,073 output tokens, 15.344 s
- First Turn: paired `read_file(error)`, later paired
  `read_file(success)`, one approved lesson written
- Second Turn: `memory.rendered` reported `renderedCount=1` and
  `injected=true`; paired `read_file(success)` and marker oracle passed
- No source edits, unsafe actions or user intervention

The v2 failed and after-fix smoke artifacts remain in
`artifacts/north-star-memory-compaction-20-v2/` as diagnostic evidence; they
were not overwritten or relabeled as passing.

## Verification

- Focused Agent/Memory/live-runner matrix: 90 passed.
- Memory and reflection cross-module matrix: 1,344 passed in the sandbox; its
  24 loopback-only failures were rerun outside the sandbox and passed 42/42.
- Final complete real-environment suite after all changes: 4,094 passed,
  2 skipped in 265.31 seconds.
- Ruff, compileall and `git diff --check`: passed.
- Offline deterministic quality gate: passed; Skill routing 60/60,
  compaction fidelity 12/12 and recorded North-Star 50/50.

The offline 50-task score is historical recorded evidence and was not
presented as a fresh provider run. The fresh provider evidence in this repair
is the v3 two-Turn memory-chain smoke described above.

## Remaining boundary

Only one of the two v3 learning-chain cases was freshly executed. The v3
manifest is ready for a future full 20-task provider run, but that larger run
is not required to establish this repair's code and single-chain behavior.
