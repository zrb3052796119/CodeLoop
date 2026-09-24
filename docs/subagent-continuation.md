# Sub-agent continuation (`continue_subagent_id`)

## Motivation

Before this feature every `task` call was one-shot: if the parent model's
follow-up question arrived after an `explore`/`plan` sub-agent finished
("which function actually issues the token?"), the only option was a
complete re-exploration — re-paying the tree/grep/read pass and the
parent's tool-call budget for context the child already had.

ZCode (Z.ai's open-source coding agent harness) closes this gap with
agent handles: a finished sub-agent's id can be resumed via
`SendMessage`, so follow-ups continue the stored conversation
(`subagent/runner.ts`, `tool/handlers/send-message.ts`). This feature
adopts the capability in MiniCode's contract-first style:
`continue_subagent_id` on the `task` tool.

## Contract

- Only `action='run'` with `agent_type` in `('explore', 'plan')` may
  carry `continue_subagent_id`; `_validate` rejects anything else.
- The id must be a `sub_[0-9a-f]{32}` value returned in this same user
  turn's task result header (`Continue: pass continue_subagent_id='…'`).
- The stored conversation resumes: a fresh system prompt (with the new
  run's result-key instruction) replaces the stored one, the prior
  user/assistant/tool messages are carried over verbatim, and the new
  prompt is appended as the final user message, prefixed with a
  `[continuation of sub-agent …]` marker so the child answers instead of
  re-exploring.
- A continuation is a new `_run` for accounting: new `subagent_id`, its
  own journal entry and `subagent.completed` observation (still
  content-free), and it draws on the same shared `AgentTurnBudget`.

## Fail-closed boundaries

Every failure mode is an explicit error ToolResult — never a silent
fallback into a fresh sub-agent:

| Condition | Error |
| --- | --- |
| store missing on the ToolContext (embedder stub) | `error[subagent_continuation_unavailable]` |
| scheduler-assigned worker (ANALYST/EXECUTOR/REVIEWER/ADJUDICATOR routes) | `error[subagent_continuation_unavailable]` |
| unknown id (lists retained ids) | `error[unknown_continue_subagent_id]` |
| stored agent_type ≠ declared agent_type | `error[subagent_type_mismatch]` |
| `action='spawn'` or non-read-only type | `ValueError` at validation |

## Bounds

- Sessions are turn-scoped: created next to the `SubagentMailbox` in
  `run_agent_turn`, die with the turn, and never leave the process.
- At most `MAX_RETAINED_SESSIONS = 8` conversations are retained; older
  entries evict FIFO (`minicode/subagent_sessions.py`).
- Only successfully completed (or soft-landed partial) read-only runs
  are saved; failures and scheduler workers are never stored.
- The async `spawn`/`poll` lifecycle is out of scope by design; the
  schema documents `action='run'` as the only continuation path.

## Companion prompt-layer change

The `task` tool description now carries the delegation heuristics
(when to delegate, when to search directly, "once you have delegated a
search, do not also run it yourself", self-contained prompts, and the
`'medium'` / `'very thorough'` breadth knob the explore agent adapts
to), mirroring ZCode's finding that the model reliably consults the
tool description at the moment it decides whether to delegate.
