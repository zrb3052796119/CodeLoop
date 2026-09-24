# MCP Server Configuration

MiniCode speaks the Model Context Protocol with two transports:

- **stdio** — a local child process (default when `command` is set)
- **Streamable HTTP** — remote/hosted servers (set `url`, or `"type": "http"`)

Servers are configured under the `mcpServers` key of the runtime config
(merged across config files):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "remote-api": {
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${MY_MCP_TOKEN}" },
      "timeout": 120
    }
  }
}
```

## Fields

| Field | Transport | Meaning |
|---|---|---|
| `command` / `args` / `env` / `cwd` | stdio | Process invocation. Commands are validated against an allowlist (or standard system bin directories); shell metacharacters in args are rejected. |
| `protocol` | stdio | Framing hint: `newline-json` (default MCP stdio framing) or `content-length`. Omit for auto-detection. |
| `url` | http | Streamable HTTP endpoint. Setting it (or `type: http`) selects the HTTP transport. |
| `type` | either | `http` / `streamable-http` for remote servers. `sse` is rejected with a clear error — the spec removed HTTP+SSE in 2025-03-26. |
| `headers` | http | Extra request headers. Values support `${VAR}` expansion. |
| `timeout` | both | Tool-call timeout in seconds (default 60, env override `MINI_CODE_MCP_TOOL_TIMEOUT`). Initialize/list requests use fixed budgets. |
| `enabled` | both | `false` skips the server entirely. |
| `toolCapabilities` | both | Host-authored per-tool capability declarations used by routing and the read-only approval exemption. |

## Environment variable expansion

`command`, `args`, `env` values, `url` and `header` values support
`${VAR}` and `${VAR:-default}` expansion (the Claude Code / Gemini CLI
convention), so secrets stay out of config files:

```json
{ "url": "${MCP_BASE_URL:-https://mcp.example.com}/mcp",
  "headers": { "Authorization": "Bearer ${MCP_TOKEN}" } }
```

An unset variable without a default fails server startup with a clear
error instead of silently sending a literal `${TOKEN}`.

## Permissions

MCP tool calls go through the workspace permission policy
(`ensure_mcp_tool`): plan mode blocks undeclared tools, interactive
sessions prompt per tool (allow once / always / deny once / always), and
only tools declared read-only in local `toolCapabilities` skip approval.
Server-provided annotations are untrusted hints and never exempt a call.

## Behavior notes

- **Lazy start** — servers start on first use, not at agent startup; a
  failed server is retried on the next call.
- **Protocol negotiation** — the client initializes with spec version
  `2025-06-18` and honors whatever version the server answers.
- **Dynamic tool lists** — `notifications/tools/list_changed` from the
  server invalidates the cached tool list (same for resources/prompts).
- **Session recovery (HTTP)** — a 404 on a sessioned request resets the
  session; the next call transparently re-initializes. First-connect
  transient network errors retry up to 3 times with backoff.
- **Tool errors vs protocol errors** — a tool result with `isError: true`
  is returned to the model as `ok=False` with its content intact so the
  model can react to the failure text; transport/protocol failures raise
  instead.
