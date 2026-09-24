# Prompt caching (Anthropic adapter)

## What ships

`AnthropicModelAdapter` now sends Anthropic-style prompt-cache breakpoints on
the two blocks that are resent on every model call of a turn and never
change within it:

1. the **system prompt** — sent as a single text block with
   `cache_control: {"type": "ephemeral"}`;
2. the **tool catalog** — the last serialized tool carries the marker (the
   provider caches the prefix up to a breakpoint, so one marker on the last
   tool covers system + all tools).

Within one agent turn (parent or sub-agent), every step re-sends both
blocks; from the second step on, the provider serves them from cache at the
cache-read price instead of full input price. The receive side was already
in place — `ModelUsage.cache_read_tokens` /
`cache_creation_tokens` are parsed from provider usage, flow onto
`AgentStep.usage`, and `cost_tracker` already has per-model cache-read
pricing tiers. Only the send side was missing.

## Verified support

- **bigmodel Anthropic-compatible endpoint** (parent glm route): accepts
  `cache_control` (research 2026-09-24; community reports confirm both
  open.bigmodel.cn and api.z.ai).
- **Aliyun (DashScope) OpenAI-compatible route** (sub-agent qwen route):
  no Anthropic-style prompt caching; the OpenAI adapter is intentionally
  untouched.

## Controls

| Control | Effect |
| --- | --- |
| runtime `"promptCache": false` | markers off for that adapter instance |
| env `MINI_CODE_PROMPT_CACHE=0` | operator kill-switch, wins over runtime |
| provider 400 naming `cache_control` | one automatic marker-free retry (non-stream path), then normal error handling |

Unknown Anthropic-compatible gateways therefore cannot hard-fail a model
call because of the markers: they either honor them or the adapter degrades
to the plain body once.

## Where to see the effect

Provider usage fields arrive on every step: `usage.cache_read_tokens` /
`usage.cache_creation_tokens` on the `AgentStep`, reflected in token
attribution and the cost tracker's cache-read tiers. A live session shows
cache reads > 0 from the second model call of a turn onward.

## Not in this round (deliberate)

- **Message-level breakpoints** (conversation-prefix caching): needs a
  moving breakpoint policy on the message list; system+tools are the
  stable bulk, conversation caching is the next increment.
- **OpenAI adapter**: the configured Aliyun v1 gateway does not support
  prompt caching; revisit if the route changes.
- **Sub-agent prompt cross-sibling reordering**: sibling sub-agents embed
  task-specific routing in their system prompts, so their systems differ
  by design; within one sub-agent's own multi-step run the system is
  constant and already benefits from the system breakpoint.
