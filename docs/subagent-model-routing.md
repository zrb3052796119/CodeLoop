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
