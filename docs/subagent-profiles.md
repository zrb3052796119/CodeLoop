# Project agent profiles (`.mini-code/agents/*.md`)

A project can define additional read-only sub-agent types without touching
code. One Markdown file per type: a closed-key frontmatter block declares
the metadata, and the body becomes that type's system prompt.

Profiles load from **two roots**: the project
(`<project>/.mini-code/agents`, authoritative) and the user
(`~/.mini-code/agents`, personal types available in every project). On a
name collision the project definition wins — the definition closer to the
code being worked on is the one that applies. Load errors carry an origin
prefix (`project:…` / `user:…`).

```
---
name: test-locator
description: Locate failing tests and their fixtures.
max_turns: 8
allowed_tools: read_file, list_files, grep_files, find_symbols, todo_write
---
You are a test-locator agent. Find the test and fixture paths for a
reported failure and return them with the exact symbols to run.
```

After the file exists, the next turn's `task` tool description lists the
type and its `agent_type` enum accepts it; dispatch resolves profiles from
the project root at call time, so edits apply between turns.

## Contract

| Field | Rules |
| --- | --- |
| `name` | `^[a-z][a-z0-9-]{1,31}$`; must not be a reserved built-in |
| `description` | 1–400 chars; shown in the task tool's type menu |
| `max_turns` | integer 1–200 |
| `allowed_tools` | non-empty subset of the read-only whitelist (explore ∪ plan tool set, including `todo_write`), comma/space separated |
| body | non-empty; becomes the system prompt |

Reserved built-ins — `explore`, `plan`, `general`, `workflow` — **cannot be
overridden** by a project file: built-in behavior stays code-defined so a
project cannot weaken the workflow write gate or the legacy `general`
route.

## Fail-closed boundaries

- A malformed file (bad frontmatter line, unknown/duplicate key, missing
  field, out-of-range `max_turns`, tool outside the whitelist, empty
  body, reserved or malformed name) is rejected with a readable,
  per-file reason. It never loads partially.
- Load errors are surfaced in two places: the per-turn task tool
  description ("Rejected profile files…") and the
  `error[unknown_agent_type]` result, so the model can self-correct.
- Custom types are **always read-only and synchronous**:
  - `allowed_tools` is intersected against the whitelist at load time;
    write tools (`edit_file`, `write_file`, `run_command`, …) are
    structurally unreachable.
  - `action='spawn'` (async lifecycle) and `continue_subagent_id`
    (continuation) remain built-in `explore`/`plan` only.
  - Concurrency classification treats every non-`general`/`workflow`
    type as read-only, so custom types share the parallel batch.

## Accounting and routing

- A custom-type run is a normal in-process sub-agent: shared turn budget,
  sidecar journal, content-free `subagent.completed` observation, and the
  typed `SUBAGENT RESULT v1` hand-back with the header advertising the
  custom type.
- Model routing: `subagentModels["<type-name>"]` in the runtime config
  selects a per-type model; otherwise the `default` entry (or the parent
  model when dedicated routing is disabled) applies. No new credential
  channels are opened by profiles.
- `max_turns` comes from the profile (bounded 1–200); the
  `MINI_CODE_SUBAGENT_<TYPE>_MAX_TURNS` env override applies to built-in
  types only.

## Relationship to the workflow gate

Nothing here changes the write-path contract: state-changing work still
must go through `agent_type='workflow'` and the central scheduler.
Profiles only add narrowly-scoped read-only searchers, analysts, and
locators — the "audit-flavored explore" family that used to require code
changes per type.
