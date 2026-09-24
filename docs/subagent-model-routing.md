# Per-agent model routing

MiniCode can route `explore`, `plan`, `general`, and workflow phase agents to
a dedicated OpenAI-compatible provider while the parent agent keeps its own
model. The checked-in illustrative child route is:

- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- model: `qwen3.6-flash`
- provider: `openai-compatible`

The model identifier is configurable. The sanitized public live-acceptance
projection currently covers `qwen3.7-plus` for Explore and `qwen3.7-max` for
Plan/General, not the `qwen3.6-flash` example below. Validate the exact model
available to your own DashScope account before relying on that route.

## Enable the Qwen route

Copy `.env.example` to the user-owned global configuration, then replace the
existing sub-agent values there (do not append duplicate keys):

```bash
mkdir -p ~/.mini-code
chmod 700 ~/.mini-code
cp .env.example ~/.mini-code/.env
chmod 600 ~/.mini-code/.env
```

Edit `~/.mini-code/.env`:

```dotenv
MINI_CODE_SUBAGENT_API_KEY=your-dashscope-api-key
MINI_CODE_SUBAGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MINI_CODE_SUBAGENT_MODEL=qwen3.6-flash
```

A non-empty `MINI_CODE_SUBAGENT_API_KEY` enables the child route by default.
If the key is empty, routing stays disabled and child agents inherit the
parent model. Setting `MINI_CODE_SUBAGENT_ROUTING_ENABLED=true` explicitly
without a valid dedicated key fails before any child model call; it never
falls back to a parent credential.

Use these optional variables to select a different model by agent role:

```dotenv
MINI_CODE_SUBAGENT_EXPLORE_MODEL=qwen3.6-flash
MINI_CODE_SUBAGENT_PLAN_MODEL=qwen3.6-flash
MINI_CODE_SUBAGENT_GENERAL_MODEL=qwen3.6-flash
```

A workflow is an orchestrator rather than a model call. Its research, plan,
execute, and review phases use the corresponding `explore`, `plan`, or
`general` model setting above.

The equivalent `settings.json` shape is:

```json
{
  "subagentRouting": {
    "enabled": true,
    "provider": "openai-compatible",
    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "apiKey": "your-dashscope-api-key",
    "defaultModel": "qwen3.6-flash",
    "models": {
      "explore": "qwen3.6-flash",
      "plan": "qwen3.6-flash",
      "general": "qwen3.6-flash"
    }
  }
}
```

Environment variables take precedence over settings. Child prompts, selected
memory, and tool results are sent to the configured remote model, so enable
the route only for workspaces whose data policy permits that transfer.

## General-subagent budget isolation

Write-capable `general` agents run in a separate process so the parent can
terminate a blocked provider call. A Python budget object cannot be shared
across that boundary. Before spawning the worker, the parent atomically
reserves a finite lease from the shared turn budget (model calls, tokens, and
cost), while retaining one parent model call. Concurrent workers therefore
cannot each observe and spend the same remaining balance. Even when the
parent budget has unbounded fields, the child lease uses finite runtime
defaults.

The worker strictly validates the parent-generated lease and constructs its
local budget from those limits; semantic intent classification, Skill
routing, and Agent execution all consume that one allowance. On a normal
result, the child returns only content-free usage counters, the matching lease
ID, and a parent-generated random nonce. The parent checks the nonce, result
status/version, model-turn lower bound, and all usage ceilings before settling
the lease once. This prevents stale receipts from another Turn from matching a
new numeric lease ID. Prompt text, model output, and exceptions are not part of
the receipt.

If the process is hard-killed, exits without a result, or returns a missing,
malformed, replayed, mismatched, or over-limit receipt, the parent
conservatively charges the entire reserved lease. It does the same when the
child still has an active model-call reservation or a transmitted request has
no authoritative provider usage/cost receipt. Exact settlement requires both
provider-reported token counts and a price from the versioned catalog;
estimated counts or an unpriced model therefore receive the full lease charge.
This may overstate actual usage, but it prevents unobservable subprocess work
from escaping the parent turn budget.

The worker process is trusted runtime code, not a security boundary against a
hostile process running as the same OS user. The nonce protects against stale
or accidentally crossed result files; it cannot attest usage against a child
that can read its own request and deliberately forge its own result. A threat
model that treats the child itself as hostile must either charge every lease in
full or broker provider calls and accounting in the parent process.
As with the parent budget itself, token and price totals are known only after
a provider response; the lease is an admission/accounting ceiling, not a
guarantee that one already-admitted provider response cannot report an
over-limit output or external charge.
