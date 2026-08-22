# Final-answer / `ask_user` contract repair

Date: 2026-08-21

## Defect

The live `context-goal-retention` acceptance case retained the correct goal
through compaction, but the model placed its answer in an `ask_user` tool call.
The Agent Loop correctly classified that turn as awaiting input (`unknown`),
while the headless runner copied the question text into its returned response.
The case therefore passed content retention but failed canonical completion.

This was an execution-capability defect, not an outcome-classification defect:
a headless process cannot receive a follow-up answer and should never advertise
or honor a tool whose contract is to wait for one.

## Repair

User interaction is now an explicit runtime capability.

| Execution surface | `ask_user` exposed | Loop may await user |
| --- | --- | --- |
| TUI / interactive main | yes | yes |
| Dashboard conversation | yes | yes |
| Headless CLI | no | no |
| North-Star live runner | no | no |
| Nested sub-agent | no | no |

The repair has four enforcement layers:

1. The default tool registry can omit user-interaction tools.
2. Non-interactive prompts state that `ask_user` is unavailable and require an
   assistant final response.
3. The Agent Loop rejects a hallucinated or accidentally retained `ask_user`
   call when interaction is disabled, returns a tool error to the model, and
   continues the turn.
4. The tool description tells interactive models that `ask_user` requests new
   blocking information; it is not an answer, result, status, or confirmation
   transport.

The canonical outcome remains fail-closed. A legitimate interactive
clarification still pauses with `status=unknown`; arbitrary tool text is never
promoted to success by language heuristics.

## Verification

- English and Chinese answer-shaped misuse: rejected in non-interactive mode;
  the model continues and returns through the assistant channel.
- Legitimate interactive clarification: pauses after one model call and keeps
  canonical completion false.
- Focused cross-module regression: 130 passed.
- Complete regression: 4074 passed, 2 skipped in 251.89 seconds.
- Real-provider replay:
  `context-goal-retention` passed all 5/5 oracles with one model call, no user
  intervention, an effective context compaction, and canonical success.

Evidence:

- `artifacts/north-star-memory-compaction-20/repair-replay-ask-user.json`
- `artifacts/north-star-memory-compaction-20/repair-replay-ask-user-evidence/`

## Remaining boundary

An interactive model can still choose `ask_user` unnecessarily; its schema
cannot prove that information is genuinely missing. The description and
system prompt strongly constrain that choice, while the runtime preserves the
question because an interactive user can answer it. Avoiding false pauses
there without blocking valid clarification would require an explicit task
state or user-decision contract, not text-shape heuristics.
