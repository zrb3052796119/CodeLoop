# Per-agent model routing

MiniCode can route `explore`, `plan`, `general`, and workflow phase agents to
a dedicated OpenAI-compatible provider while the parent agent keeps its own
model. The checked-in default child route is:

- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- model: `qwen3.6-flash`
- provider: `openai-compatible`

## Enable the Qwen route

Copy `.env.example` to `.env` and fill in only the dedicated credential:

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
