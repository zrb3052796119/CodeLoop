---
name: codebase-explanation
description: Explain codebase architecture, agent loop flow, tool calling, and prompt construction.
directory: code-understanding
domains: [code, file, search, analysis]
scopes: [readonly]
purposes: ["codebase explanation", "runtime trace"]
supported_operations: [explain.read, search.read]
activation_terms: [code, codebase, module, file, function, class, architecture, auth, query, config, configuration, script, docs, agent_loop, "tool call", "call flow", prompt, route_skill_turn, cli, import, imports, reference, references, caller, callers, 代码, 代码库, 模块, 文件, 函数, 类, 架构, 配置, 调用, 调用方, 调用链, 流程, 引用, 拼出]
tools: [read_file, grep_files, load_skill]
keywords: [agent_loop, architecture, tool calling, prompt, explain, config, configuration, import, imports, caller, callers, reference, data flow]
examples:
  - "Explain how agent_loop.py handles tool calls"
  - "Trace how a user prompt reaches the model"
  - "Find every caller of route_skill_turn in this codebase"
---

# Codebase Explanation

Use this workflow to explain code behavior.

1. Identify the entrypoint and the relevant modules.
2. Read the smallest set of files needed to trace the flow.
3. Search for protocol types, adapter boundaries, and registry calls.
4. Explain the runtime chain using concrete file references.
5. Separate what is implemented from what is only planned.
