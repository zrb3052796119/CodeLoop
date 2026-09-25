# Prompt caching (Anthropic adapter)

## What ships

`AnthropicModelAdapter` sends Anthropic-style prompt-cache breakpoints on
the three blocks that dominate a growing turn:

1. the **system prompt** — one text block with `cache_control: ephemeral`;
2. the **tool catalog** — the last serialized tool carries the marker (the
   provider caches the prefix up to a breakpoint, so one marker covers
   system + all tools);
3. the **conversation tip** — the last content block of the last message.
   Each step of a multi-step run caches its full conversation prefix; the
   next step (whose history grew by one exchange) reads that prefix at
   cache price and only pays full input for the new tail. The longer the
   task, the bigger the saving — this is the breakpoint that matters for
   8–12-step explore runs.

In plain terms: without message breakpoints, an N-step run re-pays full
input price for its history N times. With them, step k reads steps
1..k-1's prefix at the (much cheaper) cache-read rate.

Total markers per request: 3 of the provider's 4 allowed — headroom kept.
The receive side was already in place (`ModelUsage.cache_read_tokens` /
`cache_creation_tokens` flow onto `AgentStep.usage`; `cost_tracker` has
per-model cache-read tiers). Marker placement never mutates the
serialized-tool cache or the converted messages (blocks are copied).

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
attribution and the cost tracker's cache-read tiers. In a live multi-step
run, `cache_read_tokens` should dominate `input_tokens` from the second
model call onward.

## Not in this round (deliberate)

- **OpenAI adapter**: the configured Aliyun v1 gateway does not support
  prompt caching; revisit if the route changes.
- **Sub-agent prompt cross-sibling reordering**: sibling sub-agents embed
  task-specific routing in their system prompts, so their systems differ
  by design; within one sub-agent's own multi-step run the system is
  constant and the message breakpoint covers the growing history.
- **Prefix-stability telemetry**: a live-run report of cache hit rates;
  observable now via usage fields, worth a dashboard later.
