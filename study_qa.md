# MiniCode 学习问答记录

本文档记录学习 MiniCode 过程中已经问过的问题、你的回答摘要、校正后的标准答案，以及你主动提出的关键追问。它用于后续复习和面试前查漏补缺。

## 1. 项目总览

### Q1: MiniCode 和普通聊天机器人最大的区别是什么？

你的回答摘要：

> MiniCode 不是简单问答系统，而是接收用户要求后调用大模型，大模型可能调用工具、Skill、MCP 完成用户要求，并不断推理，直到认为任务完成。

标准答案：

> MiniCode 是本地 AI Coding Agent，不是普通问答系统。用户任务会进入 Agent Loop，LLM 根据上下文判断是直接回答还是发起工具调用；工具结果会回填到 messages，再由模型继续推理，直到生成最终回答或达到步数限制。MCP 工具会被包装进 Tool Registry，Skill 更偏任务方法论，模型主要通过 Tool Calling 触发本地工具执行。

关键点：

- 普通聊天机器人主要生成文本。
- MiniCode 能读写文件、搜索代码、执行命令、维护上下文和记忆。
- 核心是 Query Loop + Tool Use 的任务执行闭环。

### Q2: `main.py` 是不是 Agent 的大脑？

你的回答摘要：

> 不是，它主要负责主页面和一些组件初始化。

标准答案：

> `main.py` 不是 Agent 大脑，而是启动入口和运行时组装器。它负责解析参数、加载配置、初始化模型、工具、权限、记忆、上下文和会话状态，然后根据 stdin/TUI 模式把用户输入交给 `run_agent_turn()`。真正执行闭环在 `agent_loop.py`。

关键点：

- TUI 页面主要在 `tty_app.py` 和 `tui/`。
- `main.py` 的核心价值是组装 runtime。

### Q3: 为什么要构建 `messages` 再交给 `run_agent_turn()`？

你的回答摘要：

> 大模型回答首先需要消息列表，然后交给 `run_agent_turn()` 调用 LLM 回答。

标准答案：

> `messages` 是 Agent Loop 的上下文状态载体，包含 system prompt、用户问题、模型回复、工具调用记录和工具结果。`run_agent_turn()` 基于这份 messages 驱动“模型决策 -> 工具执行 -> 结果回填 -> 模型继续推理”的循环。

关键点：

- `messages` 不只是聊天记录。
- `messages` 是工具调用闭环的状态存储。

### Q4: `headless.py` 和 `gateway.py` 为什么最终走同一条执行链路？

你的回答摘要：

> gateway 是 HTTP 服务，它和 headless 最后都会调用 `run_headless(prompt)`。

标准答案：

> `gateway.py` 只是把 HTTP 请求转换成 `run_headless(prompt)`；`headless.py` 初始化工具、权限、模型和 messages 后，最终调用 `run_agent_turn()`。不同入口复用同一个 Agent 执行核心。

### Q5: 用户输入到最终回答的链路是什么？

你的回答摘要：

> 系统启动入口，用户输入问题后初始化组件，把用户问题添加到 messages，保存历史会话，重新构建系统提示词，开启权限 hook，开始回答，结束后打印最后 AI 回答。

标准答案：

```text
程序启动
-> main.py 解析参数
-> 加载 runtime config
-> 初始化 tools / skills / MCP
-> 初始化 PermissionManager
-> 初始化 model adapter
-> 初始化 ContextManager
-> 初始化 MemoryManager / UserProfile
-> 构建初始 system prompt
-> 进入 TUI 或 stdin 输入循环

用户输入问题
-> 追加到 transcript
-> messages.append(user message)
-> 保存输入 history
-> 根据用户问题检索相关 memory
-> 重新构建 system prompt
-> permissions.begin_turn()
-> 调用 run_agent_turn()
   -> 模型推理
   -> 如需工具，执行 tool call
   -> 工具结果写回 messages
   -> 模型继续推理
   -> 最终生成 assistant answer
-> permissions.end_turn()
-> 找到最后一条 assistant message
-> 打印给用户
-> 记录任务结果
-> 保存 session
```

纠偏点：

- 组件初始化主要发生在程序启动阶段，不是用户输入之后才初始化。
- `permissions.begin_turn()` / `end_turn()` 是一轮任务的权限作用域，不是普通 hook。
- Prompt Cache 友好设计发生在 prompt 构建阶段，通过稳定静态前缀和动态后缀分离实现。

## 2. Query Loop + Tool Use

### Q6: `run_agent_turn()` 为什么要有 `while max_steps`？

你的回答摘要：

> 限制最大回复次数，避免模型无限循环回复，浪费 token。

标准答案：

> `max_steps` 限制的是一轮 Agent 任务里的模型-工具循环次数，不只是最大回复次数。它防止模型反复调用工具、空转、修复失败后继续重试，导致无限循环和 token/时间成本失控。

### Q7: `AgentStep` 的 `assistant` 和 `tool_calls` 分别代表什么？

你的回答摘要：

> `assistant` 表明这一步是模型正常回答问题，`tool_calls` 表明模型正在调用工具。

标准答案：

> `assistant` 表示模型这一步输出文本，可能是最终回答，也可能是 progress。`tool_calls` 表示模型这一步不是直接回答，而是请求系统执行一个或多个工具。

### Q8: 为什么工具执行后要追加 `assistant_tool_call` 和 `tool_result`？

你的回答摘要：

> 是为了上下文，提示模型已经决定使用这个工具，并且工具结果已经存在上下文里，可以作为依据回答问题。

标准答案：

> `assistant_tool_call` 记录模型当时选择了什么工具、传了什么参数；`tool_result` 记录工具真实执行结果。两者构成可追踪的工具调用上下文，让下一轮模型知道“我刚才请求了什么操作”和“系统实际返回了什么证据”。

### Q9: 为什么工具执行后是 `continue`，不是直接 `return`？

你的回答摘要：

> 工具执行完并写回 messages 后，还需要 LLM 来进行最终回复。

标准答案：

> 工具执行只是中间步骤，不是用户最终要的答案。工具结果回填 messages 后，需要再次调用 LLM，让模型基于工具结果判断下一步：继续调用工具、修复错误，还是生成 final answer。所以这里必须 `continue` 回到循环顶部。

### 你的追问: 如果模型返回 final answer，是不是不会再进行下一步 LLM 调用？

标准答案：

> 对。如果 `next_step.type == "assistant"`，内容不为空，并且不是 progress，`agent_loop.py` 会把它追加为 assistant message，然后 `return current_messages`，本轮 `run_agent_turn()` 结束，不再继续下一次 `model.next()`。

### 你的追问: `execute()` 里的 `tool` 就是 `ToolDefinition` 吗？

标准答案：

> 对。`ToolRegistry.execute()` 里 `tool = self.find(tool_name)` 得到的是本地注册好的 `ToolDefinition` 实例。`ToolRegistry` 初始化时用 `t.name` 作为 key、`ToolDefinition` 作为 value 建立 `_tool_index`，所以后续才能调用 `tool.validator(...)` 和 `tool.run(...)`。

## 3. Tool Calling 系统

### Q10: 模型为什么不能直接执行 Python 函数，而要通过 `ToolDefinition`？

你的回答摘要：

> 模型只能看到工具注册表里的工具名、描述和参数定义，看不到 Python 函数。模型分析应该使用哪个工具后封装到 calls，系统再拿出 name 和 params 执行。

标准答案：

> 模型运行在外部 LLM API 或 adapter 后面，它只接收工具的结构化描述：`name`、`description`、`input_schema`。它不能直接拿到 Python 函数引用，也不应该直接执行本地代码。模型只能返回 tool call，真正的函数查找、参数校验、权限检查和执行都由本地 runtime 完成。

### Q11: `input_schema` 和 `validator` 分别解决什么问题？

你的回答摘要：

> `input_schema` 告诉 LLM 应该传入怎样的 JSON，`validator` 把模型传来的 JSON 转换成想要的参数结构。

标准答案：

> `input_schema` 给模型看，约束模型应该生成什么 JSON 参数对象；`validator` 给本地 runtime 用，校验模型传来的不可信参数，并转换成工具 `run` 需要的内部结构。

### Q12: `_execute_single_tool()` 和 `ToolRegistry.execute()` 的职责有什么区别？

你的回答摘要：

> `_execute_single_tool` 包含 `ToolRegistry.execute`，先找到对应工具名称和参数，然后调用注册表执行，最后返回工具结果。

标准答案：

> `_execute_single_tool()` 属于 agent loop 调度包装层，负责从 tool call 中取 `toolName` 和 `input`，更新 UI/store 状态，构造 `ToolContext`，调用 `tools.execute()` 并兜底异常。`ToolRegistry.execute()` 属于工具系统执行入口，负责根据工具名查找 `ToolDefinition`、执行 validator、调用 `tool.run`、处理 unknown tool、validation error、tool crash 和大输出截断。

### Q13: `read_file` 为什么要调用 `resolve_tool_path()`？

你的回答摘要：

> 防止 AI 智能体传入恶意或错误路径，访问或破坏不该访问的系统文件。

标准答案：

> 模型传来的 path 不可信。`resolve_tool_path()` 会把相对路径解析到 workspace 下，归一化路径，并通过 `PermissionManager.ensure_path_access()` 检查是否越界。即使是只读工具，也要防止路径逃逸和敏感文件读取。

### Q14: `run_command` 为什么比 `read_file` 需要更强权限审查？

你的回答摘要：

> `read_file` 只读文件，不会对用户数据产生影响；`run_command` 可能执行 git、npm、rm 等命令，严重危害用户数据。

标准答案：

> `read_file` 通常是只读风险，主要风险是信息泄露；`run_command` 可以执行任意本地程序，风险包括删除文件、修改 Git 状态、安装依赖、启动网络请求、泄露环境变量、执行恶意脚本等。因此它需要命令白名单、危险命令分类、shell snippet 风险识别、自动模式判断和人工确认。

### Q15: 新增 `file_line_count` 工具后，为什么不用 `line_count` 这个名字？

你的回答摘要：

> `_load_utility_wrapper_tools` 里面已有 `line_count_tool`，不能重复命名，因为 `name` 很重要，重复可能导致调用错误。

标准答案：

> `line_count` 已经在 `text_utils.py` 里存在，并且在 full/utility profile 下会被加载。如果 core 里再注册同名工具，`ToolRegistry` 的 `_tool_index` 以 `name` 为 key，后注册的可能覆盖先注册的，导致模型以为自己调用的是某个语义，实际执行的是另一个语义。工具名必须稳定、唯一、语义清楚。

### Q16: `file_line_count` 被加入 `_CORE_TOOLS` 后，模型如何知道它存在？

你的回答摘要：

> 系统初始化时，skills、tools、MCP 等被组装起来，并在模型调用时传给 LLM，模型可以根据描述发现它们。

标准答案：

> 启动时 `create_default_tool_registry()` 把 `_CORE_TOOLS` 组装成 `ToolRegistry`。模型适配器调用 LLM 前会把 `tools.list()` 中每个 `ToolDefinition` 序列化成 provider 支持的 tools schema，例如 Anthropic 的 `input_schema` 或 OpenAI 的 function `parameters`。模型因此能看到 `file_line_count` 的名字、描述和参数格式。

### Q17: 如果模型传入 `{"path": ""}` 会发生什么？

你的回答摘要：

> 通过 `_validate` 后，`ToolRegistry` 仍然会返回一个 `ToolResult`，但 `ok=False`，`output` 是 `ValueError("path is required")` 的报错信息。

标准答案：

```text
ToolRegistry.execute()
-> find("file_line_count")
-> tool.validator(input)
-> _validate 发现 path 为空
-> raise ValueError("path is required")
-> ToolRegistry 捕获 ValueError
-> 返回 ToolResult(ok=False, output="Input validation error in file_line_count: path is required ...")
-> agent_loop 把这个 tool_result 写回 messages
-> 下一轮 LLM 可以根据错误修正参数或换工具
```

## 4. 模型适配层

### Q18: `model_registry.py` 的主要作用是什么？

你的回答摘要：

> 根据配置名和配置进行路由，转换成 MiniCode 能统一识别的路径以及获取 API key 等信息，用来调用 LLM。

标准答案：

> `model_registry.py` 负责模型/provider 的统一选择和适配器创建。它根据模型名、环境变量和 runtime 配置判断 provider，比如 Anthropic、OpenAI、OpenRouter、Custom 或 Mock，然后构造对应的 `ModelAdapter`，并把 base_url、api_key、model 等 provider 配置整理好。它不直接调用 LLM，真正 HTTP 请求发生在 adapter 中。

### Q19: 为什么 OpenRouter / Custom endpoint 也走 `OpenAIModelAdapter`？

你的回答摘要：

> OpenAI 的接口协议支持得更多，tools、messages、system prompt 分得很清晰。

标准答案：

> 因为 OpenRouter 和很多自定义模型服务通常实现的是 OpenAI-compatible API，也就是接口形态接近 `/v1/chat/completions`，messages、tools、tool_calls 的格式也接近 OpenAI function calling。因此复用 `OpenAIModelAdapter` 可以少写一套协议转换逻辑。重点是 OpenRouter / Custom endpoint 兼容 OpenAI API 协议。

### Q20: Anthropic 和 OpenAI 暴露工具 schema 的格式有什么不同？

你的回答摘要：

> OpenAI 多了 `type: function` 等部分。

标准答案：

```text
Anthropic:
{
  "name": tool.name,
  "description": tool.description,
  "input_schema": tool.input_schema
}

OpenAI:
{
  "type": "function",
  "function": {
    "name": tool.name,
    "description": tool.description,
    "parameters": tool.input_schema
  }
}
```

核心差异：

- Anthropic 直接是 tool schema。
- OpenAI 包一层 `type=function / function`，并把 `input_schema` 叫 `parameters`。

### Q21: Adapter 为什么要把 provider 返回的工具调用转成 `AgentStep`？

你的回答摘要：

> MiniCode 是一个智能体，agent loop 只能接收统一的 AgentStep，方便判断下一步是回复还是工具调用。

标准答案：

> Adapter 把 provider-specific response 转成 `AgentStep`，相当于把外部协议边界收口在模型适配层。不同 provider 的原始响应格式不同，但 agent loop 只需要处理统一的 `AgentStep(type="assistant" | "tool_calls")`。

### Q22: 为什么说模型适配层让 `agent_loop.py` 更干净？

你的回答摘要：

> 和第四题一致，agent loop 只需要接收统一的 AgentStep，不需要做一大堆 if 判断。

标准答案：

> `agent_loop.py` 不需要判断 Anthropic 的 `tool_use`、OpenAI 的 `tool_calls`、不同 stop_reason 或 message 格式。它只处理统一的 `AgentStep(type="assistant" | "tool_calls")`，因此主循环可以专注于任务执行闭环。

### 你的追问: 请求方向把 MiniCode 的 messages 和 ToolDefinition 转成 provider API 格式，是为了让 LLM 认识到消息和工具吗？

标准答案：

> 对。请求方向转换有两个目的：让 LLM 看懂对话上下文，以及让 LLM 看懂可用工具。MiniCode 内部的 `ChatMessage` 和 `ToolDefinition` 是本地结构，Anthropic/OpenAI API 不认识这些 Python 对象和内部 role，因此 adapter 必须转换成 provider 规定的 JSON 协议格式。

### 你的追问: LLM 告诉我们需要调用工具，就是返回 AgentStep，然后系统看到 `type=tool_calls` 开始执行工具吗？

标准答案：

> 中间要补一层。LLM 原始响应不是 MiniCode 的 `AgentStep`，而是 provider 自己的 `tool_use` 或 `tool_calls` 格式。ModelAdapter 会把原始响应解析成 `AgentStep(type="tool_calls", calls=[...])`；`agent_loop.py` 接收到这个 `AgentStep` 后，才进入 `_execute_single_tool()` 和 `ToolRegistry.execute()` 的工具执行流程。

### Q23: 为什么 MiniCode 内部要有自己的 `ChatMessage`，而不是直接使用 OpenAI 或 Anthropic 的格式？

你的回答摘要：

> 如果直接使用 OpenAI 或 Anthropic 的格式，他们的 message role 对不上。MiniCode 定义了六种消息类型，不同模型对消息格式要求不同，因此请求前要做消息转换。

标准答案：

> MiniCode 用自己的 `ChatMessage` 是为了让 `agent_loop` 和上层逻辑不绑定某个 provider。Provider 协议差异被限制在 adapter 层。内部统一使用 `system/user/assistant/assistant_progress/assistant_tool_call/tool_result`，adapter 负责翻译成 Anthropic 或 OpenAI 各自的 message 格式。

### Q24: `assistant_tool_call` 在 Anthropic 和 OpenAI 中分别转换成什么？

你的回答摘要：

> Anthropic 转成 `{"type": "tool_use", "id": ..., "name": ..., "input": ...}`；OpenAI 转成 assistant message with `tool_calls`。

标准答案：

```text
Anthropic:
assistant_tool_call
-> {"type": "tool_use", "id": ..., "name": ..., "input": ...}

OpenAI:
assistant_tool_call
-> {
  "role": "assistant",
  "content": None,
  "tool_calls": [{
    "id": ...,
    "type": "function",
    "function": {
      "name": ...,
      "arguments": ...
    }
  }]
}
```

### Q25: `tool_result` 在 Anthropic 和 OpenAI 中分别转换成什么？

你的回答摘要：

> Anthropic 转成 `tool_result` block；OpenAI 转成 `role=tool` message。

标准答案：

```text
Anthropic:
tool_result
-> {
  "type": "tool_result",
  "tool_use_id": ...,
  "content": ...,
  "is_error": ...
}

OpenAI:
tool_result
-> {
  "role": "tool",
  "tool_call_id": ...,
  "content": ...
}
```

### Q26: 为什么 `assistant_progress` 要包成 `<progress>...</progress>` 再发给模型？

你的回答摘要：

> MiniCode 有 `assistant_progress` 表示模型没完成，是阶段性进程，但 Anthropic 和 OpenAI 都没有这个内部 role，因此包装成 assistant text 并加一层标志。

标准答案：

> Anthropic 和 OpenAI 没有 MiniCode 的 `assistant_progress` role，所以 adapter 把它转成普通 assistant text，并用 `<progress>...</progress>` 标记。这样下一轮模型能知道这条 assistant 文本不是最终交付，而是之前的阶段性状态，避免误认为任务已经完成。

### 你的追问: adapter 里面是不是既有消息转换、工具暴露格式转换，也有模型调用？

标准答案：

> 对。`ModelAdapter` 主要有三类职责：把 MiniCode 内部 messages 转成 provider API 格式；把 `ToolDefinition` 转成 provider 能识别的 tools schema；在 `next()` 中发起模型 API 调用，并把响应解析成 MiniCode 内部 `AgentStep`。

### 你的追问: `next()` 里会根据 LLM 的回复生成 `AgentStep` 吗？

标准答案：

> 对。`next()` 会根据 LLM 原始响应判断是普通文本还是工具调用，并最终返回统一的 `AgentStep(type="assistant")` 或 `AgentStep(type="tool_calls")`。这是模型适配层最关键的职责之一。

### Q27: Anthropic adapter 如何判断模型返回的是工具调用？

你的回答摘要：

> 在 `next()` 里取到模型返回的 `data`，然后取 `data.content`，如果 block 类型是 `tool_use`，就说明模型返回的是工具调用。

标准答案：

> Anthropic adapter 遍历 `data.get("content", [])`，每个 block 有 `type`。如果 `block_type == "tool_use"`，就说明模型请求工具调用，然后组装 `{"id": block["id"], "toolName": block["name"], "input": block.get("input")}`，最后返回 `AgentStep(type="tool_calls", calls=...)`。

### Q28: OpenAI adapter 为什么要对 `function.arguments` 做 `json.loads()`？

你的回答摘要：

> 因为从模型中得到的是 JSON 字符串，通过 `json.loads` 将 input 转换成 MiniCode 需要的类型，然后添加到 tool_calls 里，随着 AgentStep 传出去。

标准答案：

> OpenAI 返回的 `function.arguments` 是 JSON 字符串，而 MiniCode 内部的 tool call `input` 需要是 Python dict。adapter 通过 `json.loads()` 把字符串解析成 dict，然后组装成 `{"id": ..., "toolName": ..., "input": parsed_input}`，放进 `AgentStep(type="tool_calls", calls=...)` 里交给 `agent_loop`。

### Q29: 如果 adapter 返回 `AgentStep(type="tool_calls")`，agent_loop 下一步会做什么？

你的回答摘要：

> 根据调用工具数量选择单个执行还是并发执行等。

标准答案：

> `agent_loop` 会取 `next_step.calls`。如果只有一个 call，就直接 `_execute_single_tool()`；如果多个 call，会用 `ToolScheduler` 分成并发安全工具和串行工具。执行后把 `assistant_tool_call` 和 `tool_result` 写回 messages，然后 `continue` 进入下一轮模型推理。

### Q30: 如果 adapter 返回 `AgentStep(type="assistant", kind="progress")`，为什么 agent_loop 不直接结束？

你的回答摘要：

> 有判断，如果含有 progress 回复，就添加一条消息，然后 continue，因此不会结束。

标准答案：

> `progress` 表示阶段性状态，不是最终交付。`agent_loop` 会追加 `assistant_progress` 和继续提示，然后 `continue` 让模型继续下一步，而不是把它当 final answer 返回给用户。

### Q31: `AgentStep` 对模型适配层和 agent_loop 解耦有什么作用？

你的回答摘要：

> `AgentStep` 可以从不同模型适配层得到相同格式，这样 agent_loop 只需要看 `type` 是 assistant 还是 tool_calls。

标准答案：

> `AgentStep` 是 provider response 和 agent_loop 之间的统一协议。不同 adapter 负责把各自模型返回格式转成 AgentStep，agent_loop 只看 `type`、`calls` 和 `content`，不需要理解 Anthropic 或 OpenAI 的原始协议。

## 5. 第 5 阶段：Skill 能力体系

### Q32: Skill 和 Tool 最大区别是什么？

你的回答摘要：

> Skill 本身是指引文档，而 Tool 是写好的 Python 文件。

标准答案：

> Tool 是真实可执行的原子动作，通常由 Python 函数实现，并通过 `ToolDefinition` 暴露给模型；Skill 是高层任务方法论，通常是一份 `SKILL.md` 文档，用来指导模型遇到某类任务时按稳定流程执行。Tool 解决“能不能做”，Skill 解决“怎么做更稳定”。

需要加强：

> Tool 不一定等同于“一个 py 文件”，更准确说是一个注册进 `ToolRegistry` 的 `ToolDefinition`。它背后可以来自 Python 函数、MCP 工具或其他封装。

### Q33: 为什么 system prompt 里只放 Skill 摘要，而不是把所有 `SKILL.md` 全文都塞进去？

你的回答摘要：

> 如果把 Skill 全文都塞进去，会极大增加输入 token 负担。

标准答案：

> 因为 Skill 数量增长后，全部注入 prompt 会带来 token 成本高、上下文噪声大、功能重叠导致选择混乱、Prompt Cache 命中率变差等问题。MiniCode 当前做法是启动时只注入 `name + description` 摘要，模型需要完整方法论时再通过 `load_skill` 按需读取。

### Q34: 模型想读取某个 Skill 全文时，实际调用的是哪个工具？

你的回答摘要：

> 调用 `load_skill`，它可以读取到某个 Skill 的全文。

标准答案：

> 调用 `load_skill` 工具。它的输入是 `{"name": "skill-name"}`，内部通过 `load_skill(cwd, name)` 查找对应目录下的 `SKILL.md`，然后把 `SKILL: / SOURCE: / PATH:` 和全文内容作为 `ToolResult` 返回给模型。

### Q35: `intent_parser.py` 和 `capability_registry.py` 分别为 Skill 路由提供了什么基础能力？

你的回答摘要：

> Intent 对用户输入做意图解析；Capability 把工具包装成可描述、可检索、可分类的能力元数据。

标准答案：

> `intent_parser.py` 把用户原始输入解析成稳定结构，例如 `intent_type`、`action_type`、`entities`、`keywords`、`complexity_hint`，为后续召回哪些 Skill 提供任务侧信号。`capability_registry.py` 把工具抽象成带 domain、scope、description、dependencies、tags 的能力元数据，为后续按领域、标签、权限范围和依赖关系检索能力提供工具侧信号。

阶段性结论：

```text
Skill 当前代码链路：
discover_skills()
-> ToolRegistry(skills=...)
-> tools.get_skills()
-> build_system_prompt() 注入 Skill 摘要
-> 模型需要全文时调用 load_skill
-> load_skill 读取 SKILL.md 并返回给模型
```

下一步学习 Skill 路由基础：

1. `intent_parser.py` 如何把用户输入转成 `ParsedIntent`。
2. `capability_registry.py` 如何把工具登记成可检索能力。
3. 如何把 Intent、Capability、Skill 摘要组合成一个二阶段召回与精排设计。

### Q36: `ParsedIntent` 里哪些字段最适合用来召回 Skill？

你的回答摘要：

> `raw_input` 表示用户原始输入，`intent_type` 表示用户想干什么类型的任务，`action_type` 表示动作，`confidence` 表示置信度，`entities` 表示实体，`keywords` 表示关键词，`complexity_hint` 表示复杂度。

标准答案：

> 最适合直接用于 Skill 召回的是 `intent_type`、`action_type`、`entities`、`keywords` 和 `complexity_hint`。`raw_input` 适合做语义检索或兜底匹配；`confidence` 更适合作为 gating 信号，例如置信度低时减少自动路由、交给 LLM 判断或保守加载通用 Skill；`timestamp` 只是记录解析时间，一般不参与 Skill 召回。

需要加强：

> `ParsedIntent` 实际还有 `timestamp` 字段。回答时不需要把所有字段都等权看待，面试中重点说“哪些字段用于召回、哪些用于兜底和风险控制”会更专业。

### Q37: `CapabilityMetadata` 里的 `domain` 和 `scope` 分别解决什么问题？

你的回答摘要：

> 把系统中的各种能力限定领域和权限范围。收到 intent 信息之后，可以根据 domain 和 scope 判断哪些能力可以被查询，哪些权限需要被控制。

标准答案：

> `domain` 解决“能力属于哪个业务/技术领域”的问题，例如 file、code、search、web、memory、execution；它适合用于能力检索和路由。`scope` 解决“这个能力的风险级别/权限边界是什么”的问题，例如 readonly、write、destructive、external；它适合用于权限审查、安全策略和执行顺序控制。

### Q38: 为什么 `run_command` 这类能力应该被标成 `DESTRUCTIVE`？

你的回答摘要：

> 因为 `run_command` 有破坏性，可以执行 git、rm 等命令。

标准答案：

> `run_command` 能执行任意 shell 命令，风险远高于普通读文件工具。它可能删除或覆盖文件、修改 Git 状态、安装/执行脚本、访问网络、读取环境变量或泄露敏感信息，所以应该归为 `DESTRUCTIVE`，并进入更强的权限审查链路。

### Q39: 当前代码里的 Skill 路由是完整的二阶段召回/精排吗？

你的回答摘要：

> 不是。当前具备 Skill 发现、按需加载、摘要注入，但未实现根据 `ParsedIntent` 检索 Skill。你提问：当前是不是把 Skill 摘要传到系统提示词中，让 LLM 自己判断是否调用哪个 Skill；如果实现 intent 检索，是不是会找到更准确的 Skill，目前只是基础设施搭好了？

标准答案：

> 当前不是完整的二阶段召回/精排。当前机制是：系统发现 Skill，把 `name + description` 摘要注入 system prompt，然后由 LLM 根据这些摘要自行决定是否调用 `load_skill`。这是一种“LLM 基于摘要自选 Skill”的机制，不是“系统根据 ParsedIntent 自动检索 top-k Skill”的机制。

关键澄清：

```text
当前实现：
所有 Skill 摘要 -> system prompt -> LLM 自己判断是否 load_skill

理想二阶段路由：
ParsedIntent + Skill metadata/tags/examples
-> 粗召回候选 Skill
-> 精排 top-k
-> 只注入或自动加载少量最相关 Skill
```

如果实现了基于 intent 的检索，确实能让 Skill 选择更准确、prompt 更干净、token 成本更低。但也要注意检索召回可能漏掉隐含相关 Skill，所以通常需要保留通用 Skill、LLM 判断或兜底策略。

### Q40: 如果用户说“帮我解释 agent_loop.py 为什么会调用工具”，应该召回什么类型的 Skill？

你的回答摘要：

> 跟读文件相关类型的 Skill。

标准答案：

> 这个任务首先是 `EXPLAIN + READ`，相关文件是 `agent_loop.py`，所以应该优先召回“代码阅读/架构解释/Agent Loop 讲解”类 Skill，而不是单纯“读文件”类 Skill。读文件是 Tool 层能力，Skill 层更关注解释方法论，例如先定位入口、追踪调用链、解释状态流、最后总结设计意图。

更准确的候选 Skill 类型：

```text
codebase-explanation
architecture-reading
agent-loop-analysis
tool-calling-explanation
```

对应工具能力可以是：

```text
read_file
grep_files
find_symbols
```

### 第 5 阶段实现记录：SkillRouter

本次实现内容：

```text
用户输入
-> parse_intent(user_input)
-> register_tool_capabilities(tools)
-> SkillRouter.route(skills, intent, capability_registry, top_k=5)
-> build_system_prompt(..., skills=routed_top_k, skill_routing=metadata)
-> LLM 只看到当前任务最相关的 Skill 摘要
-> 如需全文，再调用 load_skill
```

核心结论：

> 当前系统已经不只是“把所有 Skill 摘要交给 LLM 自己选”，而是在 prompt 构建前基于 `ParsedIntent` 和 `CapabilityRegistry` 做自动召回、打分、排序和 Top-K 注入。若没有强匹配，则回退到全量摘要，避免漏召回。

面试表达：

> 我把 Skill 路由从纯 prompt 暴露升级成了 runtime 侧的二阶段基础实现：先用 `IntentParser` 提取任务类型、动作、关键词和实体，再用 `CapabilityRegistry` 提供当前系统能力的 domain/scope 信号，最后由 `SkillRouter` 对 Skill 摘要打分、精排并只注入 Top-K。这样既保留 `load_skill` 按需加载全文，又降低了 Skill 增长后的 prompt 噪声和 token 成本。

### Q41: `RoutedSkill` 和普通 `SkillSummary` 最大区别是什么？

你的回答摘要：

> Skill 路由不再把全量 Skill 摘要都给系统提示词，而是把 top 5 信息给 LLM，从而降低 token 和噪音。

标准答案：

> `SkillSummary` 是发现阶段的原始 Skill 摘要，只包含 `name`、`description`、`path`、`source`。`RoutedSkill` 是经过路由器打分后的候选结果，除了原始字段外，还包含 `score` 和 `reasons`，能解释为什么这个 Skill 被选中、分数多少、排序依据是什么。

需要加强：

> 你的回答说对了路由效果，但问题问的是数据结构差异。面试时要先回答结构差异，再补充效果：`RoutedSkill = SkillSummary + score + reasons`。

### Q42: 为什么需要同时保留 `score` 和 `signal_score`？

你的回答摘要：

> 如果只保留 `score`，只要来源于工程内就会 `+0.3`，这样永远不会出现无任何相关 Skill 的情况；因此加入 `signal_score` 判断有没有真正匹配。

标准答案：

> `score` 用于最终排序，包含 source bonus；`signal_score` 只统计真正的任务匹配信号，例如 intent/action、keyword、entity、capability domain/scope。这样可以避免一个完全不相关但来自 project 的 Skill 只靠 source bonus 阻止 fallback。

### Q43: `CapabilityRegistry` 在 SkillRouter 里提供了什么额外信息？

你的回答摘要：

> SkillRouter 不只看用户输入，还知道当前系统有哪些能力。你表示这部分还不是很理解，需要再讲一下 CapabilityRegistry 的作用。

标准答案：

> `CapabilityRegistry` 把当前可用工具转成统一的能力元数据，例如 `read_file -> domain=file, scope=readonly`，`grep_files -> domain=search, scope=readonly`，`run_command -> domain=execution, scope=destructive`。SkillRouter 根据用户 intent 推断任务需要哪些能力领域和风险范围，再和 registry 中实际存在的能力求交集，最后把这些 domain/scope 当作 Skill 打分信号。

### Q44: 为什么 routed prompt 里不能继续写死 `systematic-debugging` 这类 Skill 名字？

你的回答摘要：

> 如果继续写死，即使 Top-K 没选中它们，它们也会泄漏回 prompt，破坏 Top-K 过滤效果。

标准答案：

> 正确。routed prompt 的目标是只暴露当前任务最相关的 Skill。如果 prompt guide 里又硬编码未选中的 Skill 名字，就相当于绕过 SkillRouter，把未召回 Skill 重新放回上下文，导致 token 噪声和选择干扰。

### Q45: 如果用户输入“帮我调试 pytest 失败”，SkillRouter 大概会用哪些信号把 debug/test 类 Skill 排到前面？

你的回答摘要：

> `intent_type`、`action_type`、`capability_domains`、`capability_scopes`。

标准答案：

> 主要信号包括：`intent_type=debug`、`action_type=analyze/execute`、keywords 中的 `debug`、`pytest`、`failure`、`test`，entities 中的测试文件路径，Capability domain 中的 `file/search/execution/code`，以及 scope 中的 `readonly/destructive`。如果某个 Skill 的 name/description 包含 `debug`、`pytest`、`test failure`、`run command` 等词，它会获得更高分。

### Q46: MiniCode 原来的 Skill 机制和现在实现后的 Skill 路由有什么区别？

你的回答摘要：

> 原本虽然有意图识别和 Capability，但没有用于 Skill 选择，而是把所有 Skill 摘要放进系统提示词，让 LLM 判断需要哪些 Skill 并加载全文。现在可以根据意图识别和 Capability，把得分高的几个 Skill 摘要注入系统提示词，降低噪音和输入 token。

标准答案：

> 原机制是“全量 Skill 摘要注入 + LLM 自行选择 load_skill”；现在是“runtime 先基于 ParsedIntent 和 CapabilityRegistry 路由、打分、Top-K 精排，再把少量相关 Skill 摘要注入 prompt”。区别在于 Skill 选择从纯 LLM prompt 判断前移到了 Agent runtime，能降低上下文噪声和 token 成本。

### Q47: `ParsedIntent`、`CapabilityRegistry`、`SkillRouter` 三者分别负责什么？

你的回答摘要：

> `ParsedIntent` 负责把用户输入结构化；`CapabilityRegistry` 告诉系统有哪些能力；`SkillRouter` 结合二者进行 Skill 路由。

标准答案：

> `ParsedIntent` 提供任务侧信号：用户想做什么、动作是什么、关键词、实体和复杂度。`CapabilityRegistry` 提供系统能力侧信号：当前有哪些工具能力、分别属于什么 domain、风险 scope 是什么。`SkillRouter` 把任务侧信号、能力侧信号和 Skill 的 name/description/source 进行匹配打分，输出 Top-K `RoutedSkill`。

### Q48: 为什么最终 prompt 里只注入 Top-K Skill，而不是全部 Skill？

你的回答摘要：

> 有了 Skill 路由后，可以更准确注入 Skill，降低噪音和 token 消耗。

标准答案：

> Skill 数量增长后，全量注入会带来 token 成本高、上下文噪声大、功能重叠和模型选择混乱。Top-K 注入让模型优先看到当前任务最相关的 Skill，同时保留 fallback 避免漏召回，并继续通过 `load_skill` 按需读取全文。

### Q49: 如果面试官问“你这个 Skill 路由真的实现了吗，不只是写在简历里吧？”怎么结合代码回答？

你的回答摘要：

> 在 prompt 构建前用 `IntentParser` 解析用户任务，得到 `intent_type`、`action_type`、`keywords`、`entities`；再把 `ToolRegistry` 里的工具注册成 `CapabilityMetadata`，获得当前系统能力的 domain 和 scope。`SkillRouter` 基于这些信号对 Skill 的 name 和 description 打分，按 score、source priority、name 排序，只把 Top-K 相关 Skill 注入 prompt。如果没有强匹配，则 fallback 到全量摘要，避免漏召回。

标准答案：

> 可以从代码链路回答：`minicode/skill_router.py` 实现 `RoutedSkill`、`SkillRoutingResult` 和 `SkillRouter.route()`；`capability_registry.py` 中 `register_tool_capabilities()` 把工具注册成 domain/scope 能力元数据；`main.py`、`headless.py` 和 `tui/input_handler.py` 在构建 system prompt 前调用 `parse_intent()` 和 `SkillRouter.route()`；`prompt.py` 根据 `skill_routing` 输出 `Routed skills for intent: ...`。测试在 `tests/test_skill_router.py` 覆盖了 explain/debug 排序、Capability 加分、Top-K、fallback 和 prompt 过滤。

第 5 阶段最终结论：

```text
Skill 机制已经从“全量摘要暴露给 LLM”升级为“ParsedIntent + CapabilityRegistry 驱动的 runtime 路由与 Top-K 精排”。
```

## 6. 第 6 阶段：Memory System

### Q50: `USER / PROJECT / LOCAL` 三层记忆分别适合存什么？

你的回答摘要：

> USER 存跨项目记忆，比如用户偏好语言、偏好简洁表达；PROJECT 存项目共享记忆，比如项目架构和技术决策；LOCAL 存本机私有记忆，比如本机路径和环境。

标准答案：

> 正确。`USER` 是跨项目长期偏好和通用经验；`PROJECT` 是当前项目共享的架构、规范、决策和测试习惯；`LOCAL` 是当前机器私有的信息，例如本地路径、环境变量约定、临时调试信息，不适合提交共享。

### Q51: `MemoryEntry` 为什么不只是一个字符串？

你的回答摘要：

> 一条记忆除了 scope 和内容，还需要分类、标签、引用次数等，这样后续能更准确找到与用户问题相关的记忆。

标准答案：

> 正确。`content` 只是记忆正文；`category`、`tags`、`usage_count`、`domains`、`tier`、`updated_at` 等字段用于检索、排序、压缩、长期沉淀和治理。没有这些结构化字段，Memory 就只能做简单文本拼接，无法做相关性排序、使用频率反馈、分层存储和长期维护。

### Q52: 用户输入 `# Prefer pytest before release` 后，系统怎么处理？

你的回答摘要：

> 系统检查出这是记忆命令，不会进入 LLM，会直接保存到 PROJECT 记忆。

标准答案：

> 正确。`main.py` 会先调用 `memory_mgr.handle_user_memory_input(user_input)`。以 `#` 开头的输入会被当作显式记忆写入，默认 scope 是 `PROJECT`，category 是 `directive`，content 是去掉 `#` 后的文本，tags 包含 `chat`。保存后直接返回 `Saved memory (project): ...`，不会进入本轮 LLM 推理。

### Q53: 普通用户问题进入 LLM 前，Memory 是怎么检索并注入 prompt 的？

你的回答摘要：

> 用户提出问题后，系统三层检索 user -> project -> local，通过 BM25、子字符串命中、标签分类命中等找到相关记忆，格式化成 md 后注入 prompt。

标准答案：

> 整体流程对，但顺序需要改：`get_relevant_context(query=user_input)` 在 query 模式下按 `LOCAL -> PROJECT -> USER` 的优先级组织注入结果。底层检索会使用 BM25、子字符串命中、tag/category 命中、domain、usage_count 和 recency 等信号。检索结果被格式化成 Markdown，然后由 `prompt.py` 包成 `Project Memory & Context` 注入 system prompt。

### Q54: `MemoryManager` 和 `UserProfileManager` 的区别是什么？

你的回答摘要：

> `MemoryManager` 更像历史经验库，例如某次 bug、项目约定、测试习惯；`UserProfileManager` 更像用户偏好配置，例如喜欢中文、喜欢详细解释、偏好 pytest。你觉得 `UserProfileManager` 和 user 记忆很像。

标准答案：

> 它们确实都可能保存“用户相关信息”，但边界不同。`UserProfileManager` 管的是稳定、显式、结构化的用户画像配置，来自 `USER.md`，例如语言、回答风格、代码风格、常用框架。`MemoryManager` 的 `USER` scope 管的是跨项目可复用的历史经验和事实记忆，来自 `memory.json/MEMORY.md`，可以被搜索、计数、排序、分层和长期沉淀。

关键区别：

```text
UserProfileManager = 稳定偏好配置，结构固定，像 settings/profile
MemoryManager(USER) = 跨项目经验记忆，条目化，可检索，可排序
```

### Q55: 当前主链路用的是普通记忆注入还是高级注入？

你的回答摘要：

> 主链路会调用 `MemoryManager.get_relevant_context()`；如果上下文管理器存在，会额外调用 `MemoryInjector.inject_for_task()`。

标准答案：

> 正确。`main.py/headless.py/tui/input_handler.py` 在构建 system prompt 时使用的是普通注入，即 `memory_mgr.get_relevant_context(...)`。进入 `agent_loop.py` 后，如果 `context_manager` 存在，会初始化 `MemoryInjector`，并通过 `CyberneticOrchestrator -> MemoryPipeline -> MemoryInjector.inject_for_task()` 条件性追加高级记忆。因此当前不是“完全高级注入”，而是“普通注入作为稳定基线，高级注入作为 agent_loop 内部的条件增强”。

### Q56: 为什么 query 模式下普通记忆注入顺序是 `LOCAL -> PROJECT -> USER`？

你的回答摘要：

> 当前机器私有环境优先，其次当前项目，最后才是跨项目用户记忆。

标准答案：

> 正确。`LOCAL` 最贴近当前机器环境，例如本地路径、依赖安装状态、临时调试信息；`PROJECT` 是当前项目共享知识，例如架构、规范、测试习惯；`USER` 是跨项目偏好和通用经验。普通 prompt 注入优先使用更具体、更靠近当前执行环境的记忆。

### Q57: `MemoryManager.search()` 为什么不能只做字符串包含匹配？

你的回答摘要：

> 只做子字符串包含匹配可能导致召回不准确，也可能漏召回重要记忆；结合多种信号可以把相关记忆都找到。

标准答案：

> 方向正确，但要再补充“排序和去噪”。子字符串只能处理字面命中，遇到同义词、代码术语变体、标签分类、历史高频经验和新近决策时效果差。`MemoryManager.search()` 结合 BM25、子字符串、tag/category、domain、usage_count 和 recency，不只是为了召回更多，还为了把更可靠、更贴近当前任务的记忆排到前面，并过滤明显无关的内容。

### Q58: `MemoryInjector` 里 `context_usage >= 0.90` 为什么直接选择 `NONE`？

你的回答摘要：

> 说明上下文窗口用得很多，不能再输入记忆，否则可能导致问题回答不出来。

标准答案：

> 正确。上下文使用率超过 90% 时，最重要的是保住当前任务消息、工具结果和必要约束。如果继续注入记忆，会挤占关键上下文，导致模型遗漏最新状态、工具返回或用户要求。因此控制器直接选择 `NONE`，宁可不注入历史记忆，也不破坏当前任务稳定性。

### Q59: `MemoryReranker` 解决的是 BM25 检索里的什么问题？

你的回答摘要：

> 只用 BM25 不重排序，可能导致语义召回的记忆不准确，召回效果不好。

标准答案：

> 基本正确。更具体地说，BM25 主要看词面相关，可能召回“词相似但任务无关”的记忆，也可能无法判断跨领域噪音和互相矛盾的记忆。`MemoryReranker` 用轻量 LLM 对 BM25 top candidates 做二次筛选，选择真正相关的 3-5 条，拒绝跨领域噪音，识别冲突，并生成简短上下文摘要。

### Q60: 自进化记忆沉淀和用户手动 `# ...` 写入记忆有什么区别？

你的回答摘要：

> 自进化记忆沉淀是系统在执行完一轮对话后，自动提取关键决策、错误、工具调用等写入 PROJECT memory。

标准答案：

> 正确。手动 `# ...` 是用户显式告诉系统“这条信息要记住”，通常直接写入 PROJECT 记忆并跳过 LLM 推理；自进化记忆沉淀是任务结束后由 `agent_loop` 触发，系统根据执行过程自动反思，把关键决策、错误、工具使用、经验教训和任务上下文转成结构化 PROJECT 记忆。

### Q61: `ReflectionEngine.reflect()` 主要从 `execution_trace` 里提取哪些信息？

你的回答摘要：

> 每一步的记录，包括工具调用、LLM 回复和错误信息。

标准答案：

> 方向正确，但要区分设计意图和当前实现。设计上，`execution_trace` 应该包含工具调用、assistant 输出和错误信息；`ReflectionEngine.reflect()` 会从中拆出 `tool_calls`、`errors` 和 `assistant_msgs`，再生成成功/失败判断、关键决策、错误列表、经验教训、改进建议、confidence 和 task_context。当前 `agent_loop.py` 传入的 trace 比较简化，主要是 step/error/assistant 的聚合记录，不是完整逐步轨迹。

### Q62: 为什么反思结果要有 `confidence`，不能每次都写入长期记忆？

你的回答摘要：

> 为了保证不是什么任务都沉淀。成功任务通常会写入记忆，而失败任务如果错误较多，可能不会写入。

标准答案：

> 正确。自动记忆写入如果没有质量门槛，会把失败尝试、错误判断、临时状态和低价值过程全部写进长期记忆，后续检索时会污染模型判断。`confidence` 是一个简单的写入门控：成功任务基础分更高，错误会扣分，适量工具调用会加分，只有超过阈值才写入。

### Q63: 为什么自动反思默认写入 `PROJECT`，而不是 `USER`？

你的回答摘要：

> 任务经验通常和当前项目相关，不应该默认写进全局 USER。

标准答案：

> 正确。自动反思得到的经验往往绑定当前仓库、文件结构、测试命令、架构决策和工具调用路径。默认写入 PROJECT 能避免污染跨项目 USER 记忆。只有用户偏好、跨项目通用习惯或明确适用于所有项目的经验，才适合进入 USER。

### Q64: `MemoryCuratorAgent` 在整个 Memory System 里解决什么问题？

你的回答摘要：

> 它负责后台治理，统计记忆健康状态，清理失效文件引用，将零散记忆聚合，做 tier 晋升/降级，并建立 related_to 关系。

标准答案：

> 正确。`MemoryCuratorAgent` 是后台记忆治理组件，不是查询时实时检索器。它会归档近重复记忆、校验引用文件是否还存在、把相关记忆合成为 insight、维护 tier 晋升/降级，并通过 `related_to` 建立记忆图谱，避免记忆库长期增长后变成低质量文本堆积。

第 6 阶段最终结论：

```text
MiniCode 的 Memory System 已经形成基础闭环：
显式写入 -> 三层存储 -> 相关性检索 -> prompt 注入 -> 高级注入控制 -> 任务后反思 -> PROJECT 记忆沉淀 -> 后台治理 -> 后续复用。
```

## 第 6 阶段重点复盘：Memory System

这一部分是简历里“自进化记忆沉淀”的核心支撑，建议面试前重点复盘。

### 1. Memory 模块总图

```text
用户显式记忆输入
-> handle_user_memory_input()
-> MemoryManager.add_entry(PROJECT)
-> memory.json / MEMORY.md

普通用户问题
-> MemoryManager.get_relevant_context(query=user_input)
-> MemoryManager.search()
-> BM25 + substring + tag/category + domain + usage_count + recency
-> prompt.py 注入 Project Memory & Context
-> LLM 基于相关记忆回答

agent_loop 高级链路
-> context_manager 存在
-> MemoryInjector / MemoryPipeline 条件性注入
-> 根据 context_usage / retrieval_quality / recent_failure / task_repetition 控制注入强度
-> 可选 MemoryReranker 做 LLM 二次精排

任务结束后
-> agent_loop 构造 execution_trace
-> orch.reflect_on_task()
-> MemoryPipeline.write()
-> ReflectionEngine.reflect()
-> ReflectionResult.to_memory_entry()
-> MemoryManager.add_entry(PROJECT)
-> 后续任务检索复用

后台治理
-> MemoryCuratorAgent.maintain()
-> 去重归档 / 失效校验 / insight 合成 / tier 晋升降级 / related_to 链接
```

### 2. 三层存储和物理文件

逻辑 scope：

```text
USER    = 跨项目用户经验和偏好
PROJECT = 当前项目共享记忆，架构、规范、测试习惯、任务反思
LOCAL   = 当前机器私有记忆，本地路径、环境、临时调试信息
```

物理存储：

```text
USER    -> ~/.mini-code/memory/
PROJECT -> .mini-code-memory/
LOCAL   -> .mini-code-memory-local/
```

每个 scope 下都会保存：

```text
memory.json  = 结构化主数据，保留 id / scope / category / tags / usage_count / domains / tier / related_to
MEMORY.md    = 人类可读副本，方便查看和编辑
```

注意：

```text
长期/短期记忆不是独立数据库，而是 MemoryEntry.tier 字段：
WORKING / SHORT_TERM / LONG_TERM / ARCHIVAL
```

### 3. 普通问题如何查询和注入记忆

主入口：

```text
main.py / tui/input_handler.py
-> memory_mgr.get_relevant_context(query=user_input)
```

query 模式注入优先级：

```text
LOCAL -> PROJECT -> USER
```

原因：

```text
LOCAL 最贴近当前机器执行环境
PROJECT 最贴近当前项目事实
USER 是跨项目偏好和通用经验
```

检索排序信号：

```text
BM25                  = 文本相关性
substring             = 直接字面命中
tag/category          = 标签和分类命中
domain                = 当前任务领域加权
usage_count           = 历史被复用次数
recency               = 新近记忆加权
dedupe                = 内容去重
```

面试表达：

```text
普通记忆注入发生在 prompt 构建前。系统根据用户输入检索 LOCAL、PROJECT、USER 三层记忆，用 BM25、标签、分类、领域、使用次数和新鲜度综合排序，然后格式化为 Markdown 注入 system prompt。这样模型看到的是和当前问题相关的项目上下文，而不是全量历史记录。
```

### 4. 高级记忆注入链路

当前项目不是完全用高级注入，而是：

```text
普通注入 = 稳定基线
高级注入 = agent_loop 内部条件增强
```

触发条件：

```text
进入 run_agent_turn()
并且 context_manager 存在
-> 初始化 MemoryInjector / MemoryPipeline
-> 尝试追加高级记忆上下文
```

高级链路：

```text
task_description + current_files + MemoryInjectionSignal
-> MemoryInjectionController.decide()
-> MemoryManager.search()
-> MemoryInjector._calculate_relevance()
-> 可选 MemoryReranker.curate()
-> 去重 / 截断 / tag 补召回
-> 注入 system prompt
```

MemoryInjectionSignal 包含：

```text
context_usage           = 当前上下文压力
retrieval_quality       = 检索质量
user_correction_count   = 用户纠正次数，代表记忆污染风险
recent_failure          = 最近是否失败，需要找历史修复经验
task_repetition         = 是否重复任务，可以复用经验
active_domains          = 当前任务领域
```

注入模式：

```text
NONE     = 上下文压力极高时不注入
SUMMARY  = 上下文较紧张，只放少量短记忆
STANDARD = 默认注入
STRONG   = 检索质量高、上下文充足或失败恢复时加强注入
```

关键规则：

```text
context_usage >= 0.90 -> NONE
context_usage >= 0.75 -> SUMMARY
retrieval_quality 高且上下文充足 -> STRONG
recent_failure -> 倾向 STRONG，辅助错误恢复
user_correction_count > 0 -> 提高阈值，减少污染
```

MemoryReranker 解决的问题：

```text
BM25 偏词面相关，可能召回“词像但任务无关”的记忆。
Reranker 用 LLM 对 top candidates 二次筛选，保留真正相关的 3-5 条，拒绝跨领域噪音，识别冲突，并生成摘要。
```

面试表达：

```text
高级记忆注入不是简单 search，而是一个受上下文预算和任务状态约束的控制过程。MemoryInjector 会根据上下文压力、检索质量、失败恢复、任务重复和当前领域动态决定注入模式、数量和阈值；必要时再通过 MemoryReranker 对 BM25 候选做 LLM 精排，降低跨领域噪音和错误记忆污染。
```

### 5. 自进化记忆沉淀链路

核心调用链：

```text
agent_loop 任务结束
-> orch.reflect_on_task()
-> MemoryPipeline.write()
-> ReflectionEngine.reflect()
-> ReflectionResult.to_memory_entry()
-> MemoryManager.add_entry(PROJECT)
```

每一步职责：

```text
agent_loop
  判断任务成功/失败，构造 execution_trace。

orch.reflect_on_task()
  CyberneticOrchestrator 的转发入口，把任务描述和 trace 交给 MemoryPipeline。

MemoryPipeline.write()
  统一写入口，调用 ReflectionEngine，判断 confidence，决定是否写入 PROJECT memory。

ReflectionEngine.reflect()
  从 execution_trace 中提取 tool_calls、errors、assistant_msgs，生成成功/失败、关键决策、错误、经验教训、改进建议、task_context 和 confidence。

ReflectionResult.to_memory_entry()
  把反思结果转换成 Memory 系统认识的 content/category/tags/domains 格式。

MemoryManager.add_entry(PROJECT)
  创建 MemoryEntry 并写入 .mini-code-memory/memory.json 和 MEMORY.md。
```

自动反思默认写 PROJECT 的原因：

```text
任务经验通常和当前仓库、文件结构、测试命令、架构决策相关。
默认写 USER 会污染跨项目记忆。
```

confidence 的作用：

```text
不是所有任务都值得沉淀。
成功任务基础分高，错误会扣分，适量工具调用会加分。
只有超过阈值才写入，避免失败尝试和低价值过程污染记忆库。
```

当前实现边界：

```text
execution_trace 当前较简化，主要是 step/error/assistant 的聚合记录，不是完整逐步轨迹。
agent_loop 已经传入 error trace 时，orch.reflect_on_task() 仍可能 append error，存在重复 error 的实现瑕疵。
MemoryPipeline.write() 与 ReflectionEngine 内部持久化之间也存在潜在重复写入风险，需要后续去重或统一写入口。
```

面试表达：

```text
MiniCode 的自进化记忆沉淀发生在任务结束阶段。agent_loop 根据执行结果构造 execution_trace，通过 CyberneticOrchestrator 转给 MemoryPipeline.write。MemoryPipeline 调用 ReflectionEngine 对执行轨迹做结构化复盘，提取关键决策、错误、工具使用、经验教训和任务上下文，并通过 confidence 控制写入质量。达标后反思结果会转成 MemoryEntry 写入 PROJECT 级记忆，后续任务再通过检索和注入复用这些经验。
```

### 6. 后台记忆治理

核心组件：

```text
MemoryCuratorAgent
```

职责：

```text
统计记忆健康状态
归档近重复记忆
校验记忆中引用的文件是否还存在
将相关记忆合成为 insight
维护 tier 晋升/降级
建立 related_to 记忆关系图
```

tier 生命周期：

```text
SHORT_TERM 使用次数 >= 5 且超过 7 天
-> LONG_TERM

LONG_TERM 超过 30 天没访问
-> ARCHIVAL，并压缩内容

LONG_TERM / ARCHIVAL 最近又被访问
-> SHORT_TERM
```

面试表达：

```text
MemoryCuratorAgent 解决的是记忆库长期增长后的质量治理问题。它不是查询时实时检索器，而是在后台对记忆做去重、失效校验、insight 合成、tier 生命周期管理和 related_to 链接，避免记忆系统退化成低质量文本堆积。
```

### 7. 高频追问题

Q: 当前主链路是不是完全使用高级 MemoryInjector？

A:

```text
不是。main/headless/tui 构建 system prompt 时使用的是 MemoryManager.get_relevant_context() 普通注入；进入 agent_loop 后，如果 context_manager 存在，会条件性使用 MemoryInjector / MemoryPipeline 追加高级注入。
```

Q: 长期记忆和短期记忆是不是明确分开存储？

A:

```text
没有分成独立存储库。它们通过 MemoryEntry.tier 字段区分，物理上仍然存在 USER/PROJECT/LOCAL 对应目录的 memory.json 和 MEMORY.md 中。
```

Q: 为什么不用全量历史对话当 memory？

A:

```text
全量历史对话噪音大、token 成本高，也很难做生命周期治理。MiniCode 把可复用事实、决策、经验和任务上下文转成 MemoryEntry，并通过 category、tags、domains、usage_count、tier 等元数据支持检索、排序、压缩和治理。
```

Q: 自进化记忆沉淀现在实现得成熟吗？

A:

```text
基础闭环已经实现，包括任务后反思、confidence 门控、PROJECT 写入和后台治理。但当前 execution_trace 比较简化，部分写入路径可能存在重复，后续可以优化为完整 trace、统一写入口和更严格的去重/验证机制。
```

---

## 第 7 阶段：分层上下文压缩与长会话治理

### 1. ContextManager 总览

学习目标：

```text
理解长任务下 messages 为什么会失控，以及 MiniCode 如何估算上下文 token、判断上下文压力、保护 system prompt、压缩历史消息。
```

核心文件：

```text
minicode/context_manager.py
minicode/context_compactor.py
minicode/layered_context.py
minicode/prompt_pipeline.py
minicode/agent_loop.py
```

ContextManager 的定位：

```text
ContextManager = messages 的体检器 + 基础兜底压缩器

它不负责调用模型，也不负责执行工具，而是维护 messages、context_window、compaction_history、_compaction_level 和 token cache。
```

主链路：

```text
main/headless/tui
-> 创建 ContextManager
-> run_agent_turn(..., context_manager=context_mgr)
-> agent_loop 设置 context_manager.messages = current_messages
-> get_stats() 估算 token 使用率
-> 优先走 ContextCompactor 高级链路
-> fallback 到 ContextManager.compact_messages()
```

### 2. 我的阶段回答与标准答案

Q1: 为什么 Agent 长会话一定需要上下文管理？

我的回答：

```text
如果不做上下文管理，messages 越来越大，超出模型上下文窗口，模型回答的时候可能会被截断。
```

标准回答：

```text
正确，但可以更完整。上下文失控不仅会导致回答被截断，还可能导致请求阶段直接超过模型上下文窗口、provider 截断早期关键信息、工具结果挤占任务信息，以及 token 成本和延迟升高。
```

Q2: estimate_tokens() 为什么只是估算，而不是精确 tokenizer？

我的回答：

```text
真正 tokenizer 成本高，而且每轮都要频繁计算，可以根据 estimate_tokens 来估计 token 消耗。
```

标准回答：

```text
正确。精确 tokenizer 依赖具体模型和 provider，每轮高频统计会增加开销。上下文管理主要需要判断是否接近阈值，不需要精确计费，因此使用近似估算更工程化。
```

Q3: system_tokens 和 conversation_tokens 为什么要分开统计？

我的回答：

```text
不同类型的消息进入模型时，不只是 content 有 token，消息结构本身也有成本；因此 estimate_message_tokens 会根据 role 加额外开销。
```

标准回答：

```text
这只回答了 role overhead 的一部分。system_tokens 和 conversation_tokens 分开统计，更重要的是区分高优先级基础指令和动态增长对话。system prompt 一般不能随便压缩或删除，conversation 才是主要增长来源和压缩对象。
```

代码位置：

```text
minicode/context_manager.py:get_stats()

if msg.get("role") == "system":
    system_tokens += msg_tokens
else:
    conversation_tokens += msg_tokens
```

压缩时保护 system message 的代码：

```text
minicode/context_manager.py:compact_messages()

system_messages = [m for m in self.messages if m.get("role") == "system"]
other_messages = [m for m in self.messages if m.get("role") != "system"]
```

最终拼回：

```text
compacted = system_messages + [compaction_marker] + filtered
```

当前实现边界：

```text
当前代码已经分开统计 system_tokens / conversation_tokens，并在压缩时保护 system messages；
但还没有充分利用这两个指标做更细粒度的策略分流，例如 system prompt 过大时给出单独诊断。
```

Q4: 为什么工具调用和工具结果不能随便删一半？

我的回答：

```text
如果只删一半，模型上下文会变得不一致。所以压缩时要尽量保持工具调用语义完整。
```

标准回答：

```text
正确。assistant_tool_call 表示模型请求了什么工具和参数，tool_result 表示本地执行结果。只保留其中一半会破坏模型对历史状态的理解。压缩时应该成对保留，或者压成一条摘要消息。
```

代码位置：

```text
minicode/context_manager.py:compact_messages()

if msg.role == assistant_tool_call and next role == tool_result:
    summary = self._compress_tool_pair(call_msg, result_msg)
    compressed.append({"role": "assistant", "content": summary})
```

Q5: compaction_level 为什么越压缩越激进？

我的回答：

```text
对一个对话来说，上下文越长越危险，因此越压缩越需要激进。随着对话长度增加，输入 token 越来越多，因此需要更早干预。
```

标准回答：

```text
基本正确。更精确地说，反复触发压缩说明对话增长速度已经很危险，如果每次都等到 95% 才压缩，下一轮工具结果或用户输入很容易直接溢出。因此系统把阈值逐步降低：第一次快满了才压，后续更早压。
```

代码位置：

```text
minicode/context_manager.py:should_auto_compact()

threshold = AUTOCOMPACT_THRESHOLD - (self._compaction_level * 0.10)
threshold = max(0.60, threshold)
```

以及：

```text
minicode/context_manager.py:_finalize_compaction()

self._compaction_level = min(self._compaction_level + 1, 3)
```

### 3. 高频追问

Q: 第三个问题这一部分在代码哪里体现了？为什么我没看见？

标准回答：

```text
你没看错。代码确实分开统计了 system_tokens 和 conversation_tokens，也确实在压缩时保护 system messages；但当前实现没有进一步根据这两个指标做策略分流。

所以更严谨的说法是：
当前实现已经区分统计 system_tokens / conversation_tokens，并在压缩时保护 system messages；
但还没有充分利用这两个指标做更细粒度的诊断或策略控制。
```

对应代码：

```text
minicode/context_manager.py:get_stats()
minicode/context_manager.py:compact_messages()
minicode/context_manager.py:_finalize_compaction()
```

可改进点：

```text
/context 命令可以展示：

System tokens: ...
Conversation tokens: ...

这样用户能判断是 system prompt 太大，还是历史对话太长。
```

---

## 补充复盘：三层 Skill 路由系统

### 1. 三层结构

MiniCode 当前实现的三层 Skill 路由是：

```text
Skill Directory 目录层
-> High-level Skill 工作流层
-> Atomic Tool 原子工具能力层
```

它的核心目标：

```text
不再把所有 Skill 摘要全量塞进 prompt，而是在构建 system prompt 前先做自动召回、打分、排序和 Top-K 注入。
```

### 2. Directory 层

目录层文件：

```text
.mini-code/skills/<directory>/SKILL_DIR.md
```

示例：

```text
.mini-code/skills/code-understanding/SKILL_DIR.md
```

职责：

```text
先判断任务大方向，例如代码理解、调试、测试、重构、文档。
```

解析代码：

```text
minicode/skills.py:SkillDirectorySummary
minicode/skills.py:_directory_from_file()
minicode/skills.py:discover_skill_directories()
```

### 3. Skill 层

Skill 层文件：

```text
.mini-code/skills/<directory>/<skill>/SKILL.md
```

示例：

```text
.mini-code/skills/code-understanding/codebase-explanation/SKILL.md
```

它会被解析成 qualified_name：

```text
code-understanding/codebase-explanation
```

模型如果需要完整工作流，应该调用：

```text
load_skill("code-understanding/codebase-explanation")
```

解析代码：

```text
minicode/skills.py:SkillSummary
minicode/skills.py:_skill_from_file()
minicode/skills.py:discover_skills()
minicode/skills.py:load_skill()
```

### 4. Tool / Capability 层

Tool 层不是 Skill 文件，而是系统已有原子工具：

```text
read_file
grep_files
load_skill
run_command
edit_file
```

这些工具会被注册进 CapabilityRegistry：

```text
minicode/capability_registry.py:register_tool_capabilities()
```

工具会被推断出 domain 和 scope：

```text
read_file / write_file -> file
grep_files             -> search
run_command            -> execution / destructive
web / fetch / http     -> external
```

作用：

```text
SkillRouter 不只看用户问题，也看当前系统实际具备哪些工具能力。
Skill 声明的 tools 如果存在且 domain/scope 匹配当前任务，会加分；
readonly 任务里依赖 destructive/external 工具的 Skill 会被降权。
```

### 5. 路由执行链路

主链路：

```text
用户输入
-> parse_intent(user_input)
-> register_tool_capabilities(tools)
-> tools.get_skills()
-> SkillRouter.route(...)
-> build_system_prompt(...)
```

代码位置：

```text
minicode/main.py
minicode/headless.py
minicode/skill_router.py
minicode/prompt.py
```

SkillRouter 的关键步骤：

```text
1. 根据 ParsedIntent 得到 intent_type/action_type/keywords/entities
2. 根据 CapabilityRegistry 得到当前可用 domains/scopes
3. Directory recall：先给目录打分
4. Skill ranking：再给目录内或候选 Skill 打分
5. Tool affinity：根据 Skill 声明的 tools 加分或降权
6. score/source/name 排序
7. 取 Top-K，默认 5
8. 如果没有强匹配，fallback 到全量摘要机制
```

核心代码：

```text
minicode/skill_router.py:SkillRouter.route()
minicode/skill_router.py:_score_directories()
minicode/skill_router.py:_score_skill()
minicode/skill_router.py:_tool_affinity()
minicode/skill_router.py:_score_text()
```

### 6. Prompt 注入

路由结果不会自动注入 Skill 全文，只注入摘要：

```text
Routed skills for intent: explain/read
Capability domains: code, file, search
Capability scopes: readonly

Routed skill directories:
- code-understanding: ...

Routed skills:
- code-understanding/codebase-explanation: ...
  likely tools: read_file, grep_files, load_skill
```

代码位置：

```text
minicode/prompt.py
```

设计原因：

```text
只注入摘要可以降低 token 成本和 prompt 噪音；
Skill 全文仍通过 load_skill 按需读取，避免 Skill 变多后污染上下文。
```

面试表达：

```text
MiniCode 的三层 Skill 路由把能力组织成 Directory、Skill 和 Tool 三层。Directory 负责先缩小任务领域，Skill 负责提供高层工作流，Tool/Capability 负责判断这个工作流是否适配当前系统能力和权限风险。运行时会先解析用户意图，再结合 CapabilityRegistry 对目录和 Skill 做二阶段召回与精排，只把 Top-K 相关 Skill 摘要注入 prompt，全文仍由模型通过 load_skill 按需读取，从而降低 Skill 增长后的 token 成本、检索噪声和功能重叠问题。
```

---

## 学习记录维护问题

Q: 你是不是忘记把我的回答和问题添加到 QA 里面了？为什么会出现这个问题？

A:

```text
是的，漏记了。原因不是 study_qa.md 自动更新失败，而是我在最近几轮切换到了调试 DeepSeek/TUI bug 和讲解上下文管理，没有执行“每轮学习问答后同步 study_qa.md”的文件编辑步骤。这个项目没有自动 hook 帮我记录问答，必须显式修改 study_qa.md。

后续每完成一个学习小节，尤其是你回答阶段问题后，需要立即同步：
1. 你的原回答
2. 标准回答或修正
3. 关键代码位置
4. 高频追问
```

---

## 第 7 阶段补充：ContextCompactor 高级上下文治理链路

### 本节主题

这一节学习的是 `ContextCompactor`，也就是项目中更接近简历描述“分层上下文压缩”的高级链路。

核心文件：

```text
minicode/context_compactor.py
minicode/agent_loop.py
```

核心流程：

```text
current_messages
-> ToolResultBudgetManager：大工具结果外置化 + 预览占位
-> ReadDedupManager：重复 read_file 内容去重
-> MicrocompactEngine：旧工具结果微压缩
-> AutoCompactDispatcher：高水位自动压缩
   -> SessionMemoryCompactEngine：优先用记忆作为摘要基底
   -> Full Compact：规则型结构化摘要
-> ReactiveCompactEngine：模型 API 报超限后的兜底压缩
```

### 代码定位

```text
minicode/context_compactor.py:153  ToolResultBudgetManager
minicode/context_compactor.py:284  ReadDedupManager
minicode/context_compactor.py:347  MicrocompactEngine
minicode/context_compactor.py:437  SessionMemoryCompactEngine
minicode/context_compactor.py:591  AutoCompactDispatcher
minicode/context_compactor.py:749  _generate_structured_summary()
minicode/context_compactor.py:835  ReactiveCompactEngine
minicode/context_compactor.py:952  ContextCompactor
minicode/agent_loop.py:705         创建 ContextCompactor
minicode/agent_loop.py:751         请求前运行上下文治理链路
minicode/agent_loop.py:1257        read_file 工具结果去重接入点
```

### 重要修正

`context_compactor.py` 文件头里写了 Full Compact 是 `model-generated summary`，但实际代码并没有调用 LLM。

真实实现是：

```text
_generate_structured_summary() 通过规则从 messages 中抽取：
- 用户讨论过的话题
- 调用过的工具
- 涉及的文件
- 出现过的错误
```

所以面试时更准确的说法是：

```text
Full Compact 当前实现是规则型结构化摘要，不是 LLM 生成摘要。
```

### 用户本轮回答

Q1: 为什么大工具结果不能一直完整留在 `messages` 里？

用户回答：

```text
一直完整保留在messages，会导致上下文窗口爆炸，token消耗多。
```

标准回答：

```text
对。完整保留大工具结果会导致 messages 快速膨胀，每次请求 LLM 都要重复携带这些内容，从而增加 token 成本、降低 Prompt Cache 收益、挤占上下文窗口，并且让模型被旧日志或旧文件内容干扰。
```

Q2: `ToolResultBudgetManager` 是直接删除大结果，还是把它换成了什么？

用户回答：

```text
不是直接删除，而是把他的结果外置化，同时进行缓存友好占位压缩。
```

标准回答：

```text
对。它不是直接删除，而是把完整工具结果写入 .mini-code-tool-results/，然后把 messages 中原来的长内容替换成短 preview stub。stub 会保留工具名、落盘文件名、首尾预览和省略提示。
```

Q3: `ReadDedupManager` 为什么要同时看 `file_path` 和 `content_hash`？

用户回答：

```text
因为llm可能在后续修改了这个文件，因此再读的话要对比路径和内容哈希。
```

标准回答：

```text
对。只看 file_path 不够，因为同一个路径的文件可能被 write_file、edit、run_command 等操作改过；只看 content_hash 也不够，因为不同文件可能内容一样，但语义位置不同。因此用 path + content_hash 判断“同一个文件的同一份内容”是否已经读过。
```

Q4: `Session Memory Compact` 和 `Full Compact` 的核心区别是什么？

用户回答：

```text
SessionMemory是用记忆做摘要基底，把已有项目记忆、用户记忆、环境记忆作为压缩后的摘要基础，然后保留最近一段 tail 消息；而Full Compact，规则型结构化摘要，而是规则抽取：用户讨论的话题，调用的工具等等。
```

标准回答：

```text
对。Session Memory Compact 依赖 MemoryManager.get_relevant_context()，用已有记忆作为摘要基础，再保留最近 tail，并尽量避免切断 tool_use/tool_result 对。Full Compact 不依赖记忆，而是从旧 messages 中规则抽取 topics、tools、files、errors，生成一个结构化 summary，再保留最近少量消息。
```

Q5: `Reactive Compact` 是在请求前主动触发，还是在模型 API 报错后兜底触发？

用户回答：

```text
报错后兜底触发。
```

标准回答：

```text
对。Reactive Compact 是 post-API-failure recovery，用来处理 prompt too long、context length exceeded、media size exceeded 等模型 API 已经拒绝请求后的恢复场景。请求前主动治理主要由 ToolResultBudgetManager、MicrocompactEngine、AutoCompactDispatcher 等完成。
```

### 本节面试表达

```text
MiniCode 的上下文治理不是简单删除历史消息，而是分层压缩。请求前先做轻量优化：大工具结果落盘并用 preview stub 占位，重复 read_file 通过 path + content hash 去重，旧工具结果通过 microcompact 替换为稳定 marker。当上下文达到高水位后，AutoCompactDispatcher 会优先尝试 Session Memory Compact，用已有记忆作为摘要基底并保留最近 tail；如果没有可用记忆或收益不足，再退回 Full Compact，用规则方式抽取话题、工具、文件和错误形成结构化摘要。如果模型 API 已经报 prompt too long，则 ReactiveCompactEngine 做强制压缩或激进截断兜底。
```

---

## 第 7 阶段补充：ContextCyberneticsOrchestrator 控制论上下文调度层

### 本节主题

这一节学习的是 `ContextCyberneticsOrchestrator`。它不是新的压缩算法，而是包在 `ContextCompactor` 外面的控制层。

核心区别：

```text
ContextCompactor：负责怎么压缩。
ContextCyberneticsOrchestrator：负责什么时候压缩、压多狠、选哪种策略。
```

核心文件：

```text
minicode/context_cybernetics.py
minicode/context_compactor.py
minicode/agent_loop.py
tests/test_context_cybernetics.py
```

### 代码定位

```text
minicode/context_cybernetics.py:98   ContextPressureSensor
minicode/context_cybernetics.py:183  ContextPIDController
minicode/context_cybernetics.py:275  PredictiveOverflowGuard
minicode/context_cybernetics.py:368  AdaptiveThresholdManager
minicode/context_cybernetics.py:450  CompactionStrategySelector
minicode/context_cybernetics.py:521  CyberneticFeedbackLoop
minicode/context_cybernetics.py:598  ContextCyberneticsOrchestrator
minicode/context_cybernetics.py:662  run_cycle()
minicode/context_cybernetics.py:750  try_reactive_recover()
minicode/agent_loop.py:705          创建 ContextCompactor
minicode/agent_loop.py:712          创建 ContextCyberneticsOrchestrator
minicode/agent_loop.py:775          每轮请求模型前运行 run_cycle()
minicode/agent_loop.py:941          模型 API 报 prompt too long 后走 reactive recovery
```

### 主链路

```text
每轮请求 LLM 前
-> ContextCyberneticsOrchestrator.run_cycle(current_messages)
-> ContextPressureSensor 统计 usage_ratio / growth_rate / acceleration / anomaly
-> PredictiveOverflowGuard 预测未来是否溢出
-> ContextPIDController 根据 setpoint=0.70 计算压缩强度
-> AdaptiveThresholdManager 根据任务意图、错误率、延迟调整阈值
-> CompactionStrategySelector 选择 microcompact / session_memory / full
-> ContextCompactor.process_request() 执行具体压缩
-> CyberneticFeedbackLoop 记录压缩效果，必要时调整 PID 参数
```

### 用户本轮回答

Q1: `ContextCompactor` 和 `ContextCyberneticsOrchestrator` 的职责区别是什么？

用户回答：

```text
ContextCompactor是上下文压缩的具体实现，而ContextCyberneticsOrchestrator是为了调控应该在什么时候进行不同的压缩策略。
```

标准回答：

```text
对。ContextCompactor 是执行层，负责工具结果外置化、read_file 去重、microcompact、session memory compact、full compact、reactive compact 等具体压缩动作。ContextCyberneticsOrchestrator 是控制层，负责根据上下文压力、增长趋势、预测风险、错误率、延迟和任务类型决定是否压缩、压缩强度以及策略选择。
```

Q2: `ContextPressureSensor` 除了当前 token 使用率，还会额外记录哪些趋势信息？

用户回答：

```text
当前时间，估测token数量，消息数，速度，加速度，异常情况。
```

标准回答：

```text
基本正确，但要注意 token_count 不是 Sensor 自己估算出来的，而是 run_cycle() 先用 estimate_fn 对 messages 求和后传入 Sensor。ContextPressureSensor 记录 timestamp、usage_ratio、token_count、message_count、growth_rate、acceleration、anomaly。其中 growth_rate 表示上下文增长速度，acceleration 表示增长速度的变化，anomaly 表示 sudden spike、accelerating growth 或 oscillation 等异常。
```

Q3: 为什么有了 85% 阈值后，还需要 `PredictiveOverflowGuard`？

用户回答：

```text
在有些情况下，他的token可能在短时间内快速增长，因此需要预测。
```

标准回答：

```text
对。固定 85% 阈值只能看到当前是否已经接近上限，但看不到增长趋势。PredictiveOverflowGuard 会根据平滑后的 usage 和 growth_rate 预测未来若干轮是否会超过 overflow_threshold，并给出 urgency。这样即使当前还没到 85%，只要预计很快会溢出，也能提前触发压缩。
```

Q4: PID 输出的 `0~1` 大概代表什么？

用户回答：

```text
代表压缩的强度。
```

标准回答：

```text
对。PID 输出被 clamp 到 0~1，表示上下文压力对应的压缩强度。数值越高，表示越需要更强的压缩。后续会和预测 urgency 合并成 combined_intensity，再由 CompactionStrategySelector 映射成 MICROCOMPACT、SESSION_MEMORY 或 FULL 等策略。
```

Q5: 为什么 `ContextCyberneticsOrchestrator` 最后还是要调用 `ContextCompactor.process_request()`？

用户回答：

```text
目前是只有了上下文压缩的策略，具体的实施还需要process_request来进行。
```

标准回答：

```text
对。ContextCyberneticsOrchestrator 只负责控制决策，不直接改写 messages。真正执行大工具结果外置化、微压缩、自动压缩和消息替换的是 ContextCompactor.process_request()。这体现了控制层和执行层分离：Orchestrator 决定是否行动和行动强度，Compactor 负责实际执行。
```

### 本节面试表达

```text
MiniCode 在 ContextCompactor 之上又加了一层 ContextCyberneticsOrchestrator，用控制论闭环管理上下文窗口。它每轮请求模型前会先通过 ContextPressureSensor 统计 usage_ratio、growth_rate、acceleration 和 anomaly，再用 PredictiveOverflowGuard 根据增长趋势预测未来是否会溢出。随后 PID 控制器以 70% 上下文使用率为目标水位计算压缩强度，AdaptiveThresholdManager 根据任务意图、错误率和延迟动态调整阈值，CompactionStrategySelector 把压缩强度映射为 microcompact、session memory compact 或 full compact。最终真正执行压缩的仍然是 ContextCompactor.process_request()。执行后 CyberneticFeedbackLoop 记录压缩效果，并在发现震荡时调整 PID 参数。
```

### 高频追问

Q: 这个模块是不是直接提升模型推理能力？

A:

```text
不是直接提升模型推理能力，而是通过控制上下文长度、减少无效旧信息、提前避免上下文溢出，间接提升长任务稳定性和推理效率。
```

Q: 为什么不只用固定阈值？

A:

```text
固定阈值只能看当前状态，不能感知增长速度、异常突增、未来溢出风险、错误率和延迟。ContextCyberneticsOrchestrator 用传感、预测、PID 和反馈把压缩从静态阈值升级成动态控制。
```

---

## 第 8 阶段：中心化多 Agent 协作入口与主线

### 本节主题

这一节学习 MiniCode 中多 Agent 协作的主线：子 Agent 不是独立接管系统，而是被主 Agent 通过 `task` 工具以 ToolCall 方式调用。

核心文件：

```text
minicode/tools/task.py
minicode/tools/__init__.py
py-src/minicode/tools/multi_agent_tool.py
py-src/minicode/multi_agent/orchestrator.py
py-src/minicode/multi_agent/patterns.py
py-src/minicode/multi_agent/shared_memory.py
```

### 核心链路

```text
主 Agent 在 agent_loop 中运行
-> 模型决定调用 task 工具
-> ToolRegistry.execute("task", input, context)
-> task_tool._run()
-> 创建子 Agent 的 model / tools / permissions / sub_messages
-> 子 Agent 独立运行 run_agent_turn()
-> 提取子 Agent 最终 assistant message
-> 拼接 header + final_message
-> 作为 ToolResult 返回主 Agent
-> 主 Agent 继续规划、验收和最终回答
```

### 代码定位

```text
minicode/tools/__init__.py:27       task_tool 被导入
minicode/tools/__init__.py:54       task_tool 注册进默认工具列表
minicode/tools/task.py:24          AGENT_TYPES 定义 explore / plan / general
minicode/tools/task.py:83          _run() 执行子 Agent
minicode/tools/task.py:127         根据 allowed_tools 过滤工具注册表
minicode/tools/task.py:141         子 Agent 权限隔离
minicode/tools/task.py:150         构造 sub_messages
minicode/tools/task.py:170         子 Agent 内部调用 run_agent_turn()
minicode/tools/task.py:186         提取最终 assistant message
minicode/tools/task.py:200         拼接摘要 header
minicode/tools/task.py:210         超长结果截断
py-src/minicode/tools/multi_agent_tool.py:80       multi_agent_orchestrate 工具
py-src/minicode/multi_agent/orchestrator.py:33     泛化多 Agent Orchestrator
py-src/minicode/multi_agent/patterns.py:128        SequentialPattern
py-src/minicode/multi_agent/patterns.py:171        ParallelPattern
py-src/minicode/multi_agent/shared_memory.py:17    SharedMemory
```

### 用户本轮回答

Q1: 为什么说 `task` 工具体现了“子 Agent 以 ToolCall 方式受控执行”？

用户回答：

```text
task工具就是系统新开了一个子agent，然后把子agent的最终回复和header组合到一块当成toolresult返回结果。
```

标准回答：

```text
对。task 本身是一个 ToolDefinition，会被注册到 ToolRegistry。主 Agent 只能通过模型返回 tool call 的方式调用它；task_tool._run() 内部创建子 Agent 的独立工具集、权限、消息上下文，并调用 run_agent_turn() 执行。子 Agent 执行完后只把 header + final assistant message 包装成 ToolResult 返回，控制权仍回到主 Agent。
```

Q2: `explore` / `plan` 这类只读子 Agent 为什么比 `general` 更安全？

用户回答：

```text
explore/plan这类子agent只有可读工具执行，没有destructive类型的工具，因此更安全。
```

标准回答：

```text
对。explore / plan 的 allowed_tools 只包含 read_file、list_files、grep_files、file_tree、find_symbols、find_references、get_ast_info、code_review 等只读或分析工具，工具注册表层面就过滤掉 write_file、patch_file、run_command 等高风险工具。general 的 allowed_tools=None，能看到完整工具集，因此风险更高，需要继承父级权限提示能力。
```

Q3: 子 Agent 为什么要有自己的 `sub_messages`，而不是直接共用主 Agent 的 `messages`？

用户回答：

```text
容易造成上下文污染，子agent的思考过程会污染到主agent，同时token消耗也会增多，上下文压缩变得频繁。
```

标准回答：

```text
对。子 Agent 独立 sub_messages 可以隔离探索过程、工具调用日志和中间推理，避免把大量局部搜索结果灌进主 Agent 上下文。这样既降低主上下文 token 压力，也避免子任务的局部假设污染主 Agent 的全局判断。
```

Q4: 为什么只把子 Agent 的最终摘要返回给主 Agent，而不是返回完整执行过程？

用户回答：

```text
为了减少主上下文压力子 Agent 可以消耗自己的上下文做探索；主 Agent 只接收最终摘要。
```

标准回答：

```text
对。这就是结果最小化传递。子 Agent 可以在自己的上下文里读文件、搜索、分析，但主 Agent 只需要关键发现、结论、风险和必要证据。完整执行过程会增加 token 成本、污染主上下文，还会让主 Agent 被大量低价值中间日志干扰。
```

Q5: `py-src/minicode/multi_agent/` 这套 Orchestrator 和 `minicode/tools/task.py` 的区别是什么？

用户回答：

```text
它是更泛化的多 Agent 实验框架。
```

标准回答：

```text
对。minicode/tools/task.py 是当前有效包中更接近 Claude Code Task tool 的中心化子 Agent 调度入口，主 Agent 通过 ToolCall 调用一个受限子 Agent。py-src/minicode/multi_agent/ 是更泛化的实验框架，支持 sequential、parallel、hierarchical、consensus、tool_mediated 等编排模式，还包含 RoleAnalyzer、SharedMemory、MessageQueue 和 AdaptiveWorkflow。前者更适合简历里“中心化、受控、不移交控制权”的主线，后者可以作为扩展设计或实验能力补充。
```

### 本节面试表达

```text
MiniCode 的多 Agent 协作采用中心化设计。主 Agent 不把控制权交给其他 Agent，而是通过 Tool Calling 调用 task 工具创建子 Agent。task 工具会根据 agent_type 创建独立的消息上下文、过滤后的工具注册表、独立权限管理和最大轮数限制，然后让子 Agent 跑一轮完整的 run_agent_turn。子 Agent 的中间上下文不会直接进入主上下文，最终只把摘要结果作为 ToolResult 返回给主 Agent。这样既能把代码搜索、规划、分析等子任务隔离出去，又能保持主 Agent 对任务规划、权限审批、质量验收和最终回答的控制。
```

### 高频追问

Q: 为什么这个设计比多个 Agent 自由群聊更适合 Coding Agent？

A:

```text
Coding 场景要求权限可控、状态一致、结果可验收。自由群聊式多 Agent 容易带来状态同步、责任归属、权限越界和上下文污染问题；中心化设计让主 Agent 统一规划和验收，子 Agent 只做受限子任务，更容易控制安全和质量。
```

Q: 这个系统的子 Agent 是不是完全隔离？

A:

```text
不是完全进程级隔离。当前 task 工具主要做到上下文隔离、工具集过滤、权限管理和最大轮数限制；它仍在同一项目 cwd 下运行。更强的文件系统隔离或 Worktree 隔离需要结合 task_graph / safe_execution 等模块继续学习。
```

---

## 第 8 阶段补充：子 Agent 权限边界、路径边界与 Worktree 实现程度

### 本节主题

这一节学习中心化多 Agent 协作中的安全边界：`task` 子 Agent 如何通过工具过滤、权限管理、路径检查、写入审批和命令风险控制保证受控执行，以及 Worktree / AgentTeam 在当前项目中实现到什么程度。

核心结论：

```text
task 子 Agent 当前已经实现上下文隔离、工具集过滤、权限继承/隔离、最大轮数限制和结果摘要回传。
Worktree / AgentTeam 相关模块存在，但更多是可接入的增强能力或实验雏形，不是 task 工具默认执行路径。
```

### 代码定位

```text
minicode/tools/task.py:124        创建完整工具注册表
minicode/tools/task.py:127        根据 allowed_tools 过滤子 Agent 可见工具
minicode/tools/task.py:141        子 Agent 权限隔离
minicode/workspace.py:8           resolve_tool_path()
minicode/tools/read_file.py:59    read_file 执行前解析安全路径
minicode/tools/write_file.py:18   write_file 执行前解析安全路径
minicode/file_review.py:32        apply_reviewed_file_change()
minicode/file_review.py:44        写文件前 ensure_edit(diff)
minicode/tools/run_command.py:247 shell snippet / unknown command 风险判断
minicode/tools/run_command.py:254 命令执行前 ensure_command()
minicode/permissions.py:263      ensure_path_access()
minicode/permissions.py:329      ensure_command()
minicode/permissions.py:402      ensure_edit()
minicode/task_graph.py:279       WorktreeIsolator
minicode/safe_execution.py:127   WorktreeIsolator
minicode/safe_execution.py:359   execute_safely()
minicode/agent_protocol.py:176   TeamRegistry
```

### 用户本轮回答

Q1: 为什么说 `explore/plan` 的安全不是只靠 prompt？

用户回答：

```text
子agent有很多安全保证，比如 注册工具的时候不注册具有破坏性的工具，同时传入权限控制的prompt也设置为none，就不会扩大权限，在工具执行的过程中也会判定路径边界，写文件额外审批，命令执行的风险控制等。
```

标准回答：

```text
对。explore/plan 的安全不是靠提示词自律，而是多层机制共同保证：第一，task 工具在构造子 Agent ToolRegistry 时只注册 read_file、grep_files、file_tree、code navigation 等只读/分析工具；第二，只读子 Agent 使用 PermissionManager(context.cwd, prompt=None)，无法主动弹出人工审批扩大权限；第三，具体工具执行时仍会通过 resolve_tool_path() 做路径边界检查；第四，写入工具即使存在也要经过 diff 审批，命令工具会做风险分类和权限确认。
```

Q2: `PermissionManager(context.cwd, prompt=None)` 对只读子 Agent 有什么意义？

用户回答：

```text
代表不会弹出人工审批从而扩大权限。
```

标准回答：

```text
对。prompt=None 表示这个子 Agent 没有交互式审批能力。它访问 cwd 外路径或执行需要审批的高风险操作时，系统不会让它自己请求用户授权，而是直接失败。这样可以避免只读子 Agent 通过审批流程扩大权限边界。
```

Q3: 文件路径边界主要在哪个函数里检查？`read_file` 和 `write_file` 如何接入它？

用户回答：

```text
workspace里的resolve_tool_path，直接在真正操作前调用一次，获取到真正安全的路径。
```

标准回答：

```text
对。路径边界主要在 minicode/workspace.py 的 resolve_tool_path(context, input_path, intent) 中检查。read_file 在读取前调用 resolve_tool_path(context, path, "read")；write_file 在写入前调用 resolve_tool_path(context, path, "write")。如果 context.permissions 存在，会进一步调用 PermissionManager.ensure_path_access()；如果没有 permissions，也会兜底阻止路径逃逸 workspace。
```

补充：

```text
write_file 还会进入 apply_reviewed_file_change()，生成 unified diff 后调用 PermissionManager.ensure_edit()，因此写文件不只是路径检查，还多了一层 diff 审批。
```

Q4: 为什么不能说“每个子 Agent 默认都在 Worktree 中运行”？

用户回答：

```text
当前 task 工具没有默认调用 execute_safely()，也没有默认给每个子 Agent 创建 worktree。
```

标准回答：

```text
对。项目里确实有 task_graph.py 和 safe_execution.py 提供 WorktreeIsolator、create_isolation()、execute_in_isolation()、execute_safely() 等能力，但 minicode/tools/task.py 当前主线只是构造子 Agent 的独立 messages、过滤工具集、配置 PermissionManager，然后调用 run_agent_turn()。它没有默认调用 execute_safely()，也没有默认为每个 task 子 Agent 创建 git worktree。
```

Q5: 当前项目里的 `AgentTeam` / `Worktree` 更适合在简历里怎么表述，才不容易被追问穿？

用户回答：

```text
git worktree 创建隔离分支/目录，并提供 Worktree 隔离、AgentTeam 协议与多模式编排的扩展雏形。
```

标准回答：

```text
对。更稳妥的表述是“提供 Worktree 隔离、AgentTeam 协议与多模式编排的扩展雏形”，不要写成“所有子 Agent 默认运行在 Worktree 中”或“完整支持 Fork/Worktree/AgentTeam 生产级协作”。当前项目的主线是 task ToolCall 的中心化受控子 Agent；Worktree 和 AgentTeam 是可接入的增强模块。
```

### 本节面试表达

```text
MiniCode 的中心化多 Agent 协作在安全上不是只依赖 prompt 约束。主 Agent 通过 task ToolCall 创建子 Agent 时，会根据 agent_type 构造过滤后的 ToolRegistry，explore/plan 只暴露只读和代码导航工具；只读子 Agent 使用 prompt=None 的 PermissionManager，无法主动扩大 cwd 外访问或高风险操作权限。具体文件工具执行前会通过 resolve_tool_path() 做路径边界检查，写文件还会生成 diff 并走 ensure_edit() 审批，run_command 会识别 shell snippet、危险命令和未知命令并调用 ensure_command()。项目还提供 WorktreeIsolator、safe_execution 和 AgentTeam 协议等扩展模块，但当前 task 主线默认没有为每个子 Agent 创建 Worktree，因此我会将其表述为可接入的隔离能力和协作协议雏形。
```

---

## 第 9 阶段：权限与安全审查总览

### 本节主题

这一节关注 MiniCode 为什么不能只依赖大模型“自觉安全”，而是必须把安全边界落实到工具调用链路里。

核心链路是：

```text
用户请求 / 模型 ToolCall
-> ToolRegistry 参数校验
-> 工具内部路径解析
-> PermissionManager 路径/命令/写入审批
-> AutoModeChecker 风险分类
-> 人工确认或阻断
-> 执行后输出风险提示
```

### 代码定位

```text
minicode/workspace.py
- resolve_tool_path()：工具执行前解析路径，并检查是否越过 workspace 边界。

minicode/permissions.py
- PermissionManager.ensure_path_access()：路径访问权限判断。
- PermissionManager.ensure_command()：命令执行权限判断。
- PermissionManager.ensure_edit()：文件写入 diff 审批。
- _classify_dangerous_command()：识别 git reset、rm -rf、npm publish 等危险命令。

minicode/auto_mode.py
- PermissionMode：DEFAULT / AUTO / PLAN / BYPASS。
- RiskLevel：SAFE / LOW / MEDIUM / HIGH / DANGEROUS。
- AutoModeChecker.assess_risk()：根据工具名和参数做风险分类。
- detect_prompt_injection()：检测 prompt 注入模式。

minicode/tools/run_command.py
- 识别 shell snippet、后台命令、危险命令和未知命令。
- 在高风险场景调用 PermissionManager.ensure_command()。

minicode/file_review.py
- apply_reviewed_file_change()：生成 unified diff，并调用 ensure_edit()。

minicode/tui/input_handler.py
- 用户输入里检测到 prompt 注入时，记录 warning，并追加系统安全提示。
```

### 补充问题：什么是 Prompt 注入？

Q: 什么是 prompt 注入？它是危险吗？

标准回答：

```text
Prompt 注入是一类安全风险。它指的是用户输入、网页内容、文件内容或工具输出中夹带了“忽略之前的规则”“绕过权限”“读取密钥”“执行危险命令”等指令，试图让模型违背原本的系统规则、开发者规则或用户真实意图。

在普通聊天系统里，它可能只是让模型跑题；但在 Coding Agent 里更危险，因为模型可以调用工具。如果没有工具层权限边界，prompt 注入可能诱导模型删除文件、泄露 API key、修改代码、执行恶意命令或污染长期记忆。

MiniCode 当前对 prompt 注入的处理不是直接硬阻断，而是检测后写入日志和系统安全警告。原因是单靠规则很难百分百判断某段文本是否真的是攻击，直接阻断会误伤正常开发内容。真正的安全边界仍然落在 ToolRegistry、参数校验、路径边界、危险命令分类和人工确认上。
```

### 本轮问答

Q1: `resolve_tool_path()` 主要解决什么问题？

用户回答：

```text
判断目标路径是否是安全的路径，具体来说，如果路径是在工作路径，就允许；如果不是，就具体分析。
```

标准回答：

```text
对。resolve_tool_path() 是工具层路径边界的入口。它会把模型传入的相对路径或绝对路径解析成真实路径，然后判断这个路径是否在 workspace 内。如果 context.permissions 存在，会继续交给 PermissionManager.ensure_path_access() 判断；如果没有权限管理器，也会兜底阻止路径逃逸 workspace。
```

补充：

```text
这里的关键不是“模型说这个路径安全”，而是工具真正执行读写之前，由代码重新解析和检查路径。这样可以防止模型被 prompt 注入或错误参数诱导去访问 workspace 外的敏感文件。
```

Q2: 为什么写文件前还要经过 `apply_reviewed_file_change()` / `ensure_edit()`？

用户回答：

```text
为了确保写的内容合规。
```

标准回答：

```text
方向正确，但要说得更精确。写文件审批不是自动判断内容语义一定合规，而是把“将要发生的文件变更”转换成 unified diff，让权限系统或人工审批能够看清楚具体改了什么。PermissionManager.ensure_edit() 可以根据文件路径、diff 内容和当前权限策略决定允许一次、允许本轮、拒绝一次、长期拒绝，或者拒绝并给模型反馈。
```

更准确的面试表达：

```text
读文件只需要路径边界检查，而写文件会改变用户代码，所以 MiniCode 在路径检查之后又增加了 diff 审批层。它不是直接把模型生成的内容写入磁盘，而是先生成可审查的 diff，再通过 ensure_edit() 走权限判断或人工确认，从而把模型输出转化为可控的文件变更。
```

Q3: 为什么 `run_command` 比 `read_file` 风险高？

用户回答：

```text
run_command直接会对用户数据作出修改，因此风险等级很高。
```

标准回答：

```text
对，但还可以补充得更完整。run_command 的风险不只是“可能修改文件”，还包括执行任意解释器脚本、删除文件、修改 git 状态、安装或发布包、访问网络、泄露环境变量、启动后台进程，以及执行 curl | bash 这类下载后立即执行的命令。因此它需要比 read_file 更强的风险分类和权限审查。
```

代码对应：

```text
run_command.py 会先识别 shell snippet、后台命令和危险片段。
permissions.py 里还会分类 git reset --hard、git clean、git push --force、rm -rf、npm publish 等高危命令。
auto_mode.py 会把危险模式标记为 HIGH 或 DANGEROUS，并决定提示、阻断或自动批准。
```

Q4: `AUTO`、`PLAN`、`BYPASS` 分别是什么意思？

用户回答：

```text
AUTO是安全操作自动批准，风险操作提示或阻断，类似于codex的请求批准；PLAN是只读模式；BYPASS是跳过权限，类似于你的完全访问。
```

标准回答：

```text
基本正确。AUTO 会根据工具名、参数和风险模式自动判断：安全读操作可以放行，高风险操作提示或阻断。PLAN 是只读/计划模式，通常只允许 read_file、list_files、grep_files、load_skill 这类安全工具，写文件和命令执行会被拒绝。BYPASS 是跳过权限检查，风险最高，只适合受信任环境或调试场景。
```

补充：

```text
DEFAULT 不是完全自动，也不是完全阻断，它表示常规权限策略。具体是否提示还取决于 ensure_path_access()、ensure_command()、ensure_edit() 的判断逻辑。
```

Q5: 当前项目检测到 prompt 注入后会不会直接阻断？

用户回答：

```text
检测后提示，他不会直接阻断，因为不确定到底是不是prompt注入攻击。
```

标准回答：

```text
对。MiniCode 当前对 prompt 注入的处理是检测、记录 warning，并向 messages 追加一条安全系统提示，提醒模型不要遵循可疑指令。它不会直接把请求阻断，因为规则检测容易误判，例如用户可能是在学习 prompt 注入案例或分析恶意文本。真正的硬边界仍由工具层权限系统执行。
```

### 本节面试表达

```text
MiniCode 的权限安全不是只靠 prompt 约束，而是把风险控制下沉到工具执行链路。模型只能产生 ToolCall，真正执行前会经过 ToolRegistry 参数校验、resolve_tool_path 路径边界检查、PermissionManager 的路径/写入/命令审批，以及 AutoModeChecker 的风险分类。读文件主要关注路径边界；写文件会额外生成 diff 并走 ensure_edit；命令执行会识别 shell snippet、危险 git 操作、rm -rf、curl | bash 等高风险模式。对于 prompt 注入，系统采用检测和警告机制，但不直接硬阻断，避免误伤正常任务；最终安全性依赖工具权限、路径边界、危险命令分类和人工确认形成的多层审查链路。
```

---

## Skill 三层路由补充：Capability 的作用与召回过程

### 用户问题

```text
Skill 三层路由已经实现了，我知道第一层是 Directory，第二层是详细 Skill，第三层是 Capability，但是不太清楚 Capability 的作用是什么，以及路由是怎么召回的。
```

### 标准回答

```text
Capability 不是第三层要注入给 LLM 的长文档，也不是直接执行的 Skill。它的作用是把当前系统真实可用的 Tool 抽象成“能力画像”，例如 read_file 属于 file/readonly，grep_files 属于 search/readonly，run_command 属于 execution/destructive。

SkillRouter 召回时会同时看三类信号：
1. 用户任务被 ParsedIntent 解析出来的 intent/action/keywords/entities。
2. Skill Directory 和 Skill 自身的 name、description、domains、scopes、keywords、tools。
3. 当前 ToolRegistry 里真实存在的工具能力，也就是 CapabilityRegistry。

因此 Capability 的核心价值是：让路由知道“当前系统实际能做什么”和“某个 Skill 依赖的工具是否可用、是否适合当前任务”。它不是替代 Skill，而是给 Skill 排序加上工具能力和风险边界信号。
```

### 路由过程

```text
用户输入
-> parse_intent(user_input)
-> register_tool_capabilities(tools)
-> SkillRouter.route(skills, intent, capability_registry)
-> 根据 intent + capability 计算当前需要的 domains/scopes
-> 第一阶段召回 Directory
-> 第二阶段在命中的 Directory 内给 Skill 打分
-> 第三阶段用 tool affinity 加分或降权
-> Top-K 注入 system prompt
-> LLM 如需完整流程，再调用 load_skill(qualified_name)
```

### Capability 在打分里的具体作用

```text
1. 参与计算当前任务相关的能力范围。
   例如“解释 agent_loop.py 为什么调用工具”通常是 explain/read，
   SkillRouter 会倾向 code/file/search/analysis + readonly。

2. 给 Directory 和 Skill 的文本匹配加分。
   如果某个目录或 Skill 标注了 code/file/search/readonly，
   而这些正好是当前任务需要的 capability domain/scope，就会加分。

3. 做 tool affinity。
   如果某个 Skill 声明 tools: [read_file, grep_files, load_skill]，
   且这些工具真实注册在 CapabilityRegistry 里，就会获得基础加分；
   如果工具的 domain/scope 和当前任务匹配，还会继续加分。

4. 做风险降权。
   如果当前任务是 read/analyze 这种只读任务，但某个 Skill 依赖 run_command、web_fetch 这类 destructive/external 工具，
   它不会被直接过滤，但会被降权，避免只读问题优先召回危险工作流。
```

### 一句话记忆

```text
Directory 负责粗分类，Skill 负责具体工作流，Capability 负责告诉路由“这些工作流依赖的真实工具是否存在、能力类型是否匹配、风险边界是否合适”。
```

### 追问：Skill 没声明 tools 时，第三阶段是否还会加分？

用户问题：

```text
第三阶段是否执行，需要看 skill 里面是否声明了工具？如果没有声明，就不从这里加分吗？
```

标准回答：

```text
对。SkillRouter 代码上仍然会调用 _tool_affinity()，但如果这个 Skill 的 frontmatter 里没有 tools 字段，或者 tools 为空列表，那么 _tool_affinity() 遍历不到任何工具，最终 affinity_score = 0，不会从工具亲和度这里加分，也不会因为工具风险被降权。

但这不代表这个 Skill 不能被召回。它仍然可以通过 Directory 命中、name/description 命中、keywords/entities 命中、domains/scopes 文本命中获得分数。tools 字段只是第三阶段 tool affinity 的加分/降权依据，不是 Skill 能否被召回的必要条件。

如果 Skill 声明了 tools，但对应工具没有真实注册到 CapabilityRegistry，也不会获得 tool affinity 加分。只有“Skill 声明的 tool 存在于当前 ToolRegistry，并且被注册成 Capability”时，才会有 tool 存在加分、domain/scope 匹配加分，或者只读任务下 destructive/external 工具的降权。
```

### 追问：新增 Skill 如何归属到某个 Directory？必须声明 tools 吗？

用户问题：

```text
如果想添加一个 skill，它该怎么被识别到哪一个 directory 里面？不需要工具吗？
```

标准回答：

```text
新增 Skill 的目录归属主要靠文件结构和 frontmatter，不靠工具。

推荐结构是：

.mini-code/skills/<directory>/SKILL_DIR.md
.mini-code/skills/<directory>/<skill>/SKILL.md

discover_skills() 会扫描 .mini-code/skills 下的目录。如果某个目录下存在 SKILL_DIR.md，它就被识别为 Skill Directory；然后继续扫描这个目录下面每个子目录的 SKILL.md，把这些嵌套 Skill 归到该 Directory 下。

Skill 的 directory 字段有两个来源：
1. 如果 SKILL.md frontmatter 写了 directory，就使用这个值。
2. 如果没写 directory，就继承父级 SKILL_DIR.md 的 name。

因此最稳妥的写法是：物理路径放在对应目录下面，同时在 SKILL.md frontmatter 里显式写 directory，并保证它和 SKILL_DIR.md 的 name 一致。
```

示例：

```text
.mini-code/skills/code-understanding/SKILL_DIR.md
.mini-code/skills/code-understanding/trace-agent-loop/SKILL.md
```

```yaml
---
name: trace-agent-loop
description: Trace how user input flows through agent_loop and tool calls.
directory: code-understanding
domains: [code, file, search, analysis]
scopes: [readonly]
keywords: [agent_loop, tool call, trace, messages]
tools: [read_file, grep_files, load_skill]
examples:
  - "Trace why agent_loop.py calls tools"
---
```

补充：

```text
tools 不是必填字段。不写 tools，这个 Skill 仍然能通过目录、name、description、domains、scopes、keywords、entities 被召回；只是不会获得第三阶段 tool affinity 的加分或降权。

如果这个 Skill 是一个纯方法论、写作规范、架构解释流程，可以不声明 tools。
如果这个 Skill 明确依赖 read_file、grep_files、run_command、load_skill 等工具，建议声明 tools，这样路由器可以判断工具是否真实存在、能力是否匹配、风险是否合适。
```

### 追问：新增 Skill 是否应该由 LLM 辅助判断目录？

用户问题：

```text
如果新增一个 Skill，让用户自己判断需要把 Skill 放到哪个目录下是不是不太合理？是不是需要增加一个工具，同时让 LLM 辅助判断应该放到哪个目录下面？
```

标准回答：

```text
对。如果目标只是手动维护几个种子 Skill，让用户自己选目录可以接受；但如果目标是简历里写的“Skill 自进化增长”，人工判断目录就不够合理。因为 Skill 数量增长后，用户不一定清楚已有目录边界，也容易出现重复目录、功能重叠、目录命名不一致、metadata 缺失等问题。

更合理的设计是新增一个 Skill 管理工具，例如 propose_skill 或 install_skill_with_routing。这个工具不直接让 LLM 随便写文件，而是先读取现有 Skill Directory 摘要和已有 Skill 元信息，再根据新 Skill 的 name、description、keywords、domains、scopes、tools、examples 做目录候选排序，最后输出建议：

1. 放入已有目录，并给出理由。
2. 创建新目录，并给出目录 name/description/domains/scopes/keywords。
3. 判断是否与已有 Skill 重复或应该合并。
4. 生成规范 frontmatter 和目标路径。
5. 需要写入时走权限审批。

这里 LLM 的作用是做语义判断和解释理由，CapabilityRegistry 的作用是校验工具名是否真实存在、推断 domains/scopes、判断是否存在危险工具；最终落盘仍应由工具层执行，而不是让模型直接自由写文件。
```

推荐链路：

```text
用户提出新增 Skill
-> propose_skill 读取现有 directories + skills + capabilities
-> LLM/规则混合生成目录候选和 metadata
-> 检查重复 Skill / 相似 Skill
-> 输出建议路径和 SKILL.md frontmatter
-> 用户确认
-> write_file / install_skill 写入
-> discover_skills() 下一轮自动发现
```

面试表达：

```text
当前三层路由解决的是“已有 Skill 如何被召回和精排”；如果要支撑 Skill 自进化增长，还需要补一个 Skill 管理工具，在新增 Skill 时基于已有目录、Skill 元信息和 CapabilityRegistry 做自动归类、去重和 metadata 生成。LLM 负责语义判断，工具层负责读取现有目录、校验工具能力和受控落盘，这样才能避免 Skill 增长后目录混乱和检索噪声变大。
```

实现结果：

```text
已新增只读工具 propose_skill，用来补齐“新增 Skill 时如何归类”的入口。

它不会直接创建文件，而是读取现有 Skill Directory、已有 Skill 元信息和 CapabilityRegistry 后，输出推荐目录、候选目录分数、目标路径、frontmatter、重复风险、工具能力校验和下一步建议。

这意味着当前 Skill 体系现在分成两条链路：

1. 已有 Skill 路由：
   用户任务 -> ParsedIntent -> CapabilityRegistry -> SkillRouter -> Directory/Skill Top-K 注入 prompt。

2. 新增 Skill 归类：
   新 Skill 描述 -> propose_skill -> 推荐目录/路径/frontmatter/重复风险/工具校验 -> 用户确认 -> write_file 受控落盘 -> 下一轮 discover_skills() 发现。

它仍然不在工具内部调用 LLM。LLM 作为调用方负责理解用户想新增的 Skill，propose_skill 负责把这个意图和项目已有 Skill 体系做结构化匹配。
```

### 实测问题：模型为什么绕过 propose_skill 直接创建了 Skill？

现象：

```text
用户要求新增一个教学 Skill，并希望先走 propose_skill 提案流程。
实际 MiniCode 直接创建了 .mini-code/skills/code-skills/minicode-study/SKILL.md，
没有先输出 propose_skill 的目录推荐和 frontmatter 提案。
```

排查结论：

```text
工具注册没有问题。当前 ToolRegistry 能看到 propose_skill，CapabilityRegistry 也能注册该工具。

直接调用 propose_skill 时，结果正常：
recommended_directory: code-understanding
target_path: .mini-code/skills/code-understanding/minicode-project-teaching/SKILL.md
tool_validation: read_file/grep_files/load_skill 已识别

真正的问题是系统 prompt 里原本只说“如果匹配 listed skill，调用 load_skill”，但没有规定“新增 Skill 必须先调用 propose_skill，再等待用户确认，最后才能 write_file”。因此模型仍可能把“新增 Skill”当成普通文件生成任务，直接调用 write_file。
```

修复：

```text
已在 build_system_prompt() 的静态规则中加入 Skill authoring policy：
- 用户请求创建/新增/安装/生成/写入新 Skill 或 SKILL.md 时，优先调用 propose_skill。
- 在 propose_skill 返回 target_path/frontmatter 且用户确认前，不允许直接 write_file 创建新 Skill。
- 如果 propose_skill 不可用，先说明自动目录路由不可用，再考虑写文件。

同时增强 propose_skill 的工具描述，使其明确是“创建新 Skill 前使用”的只读提案工具。
```

验证：

```text
pytest tests/test_prompt.py tests/test_propose_skill.py -q
PYTHONPATH=py-src pytest py-src/tests/test_prompt.py py-src/tests/test_propose_skill.py -q
pytest -q

结果：
目标测试通过；根包全量测试 753 passed, 2 skipped。
```

---

## MCP 学习笔记：MiniCode 如何接入外部工具服务器

### MCP 是什么

```text
MCP 可以理解为一种“让 Agent 连接外部能力的标准协议”。它不是模型本身，也不是 MiniCode 内置工具，而是一套让外部服务向 Agent 暴露 tools、resources、prompts 的通信方式。

在 Coding Agent 里，MCP 的价值是：不用把所有能力都写进主项目，只要外部服务按 MCP 协议提供能力，MiniCode 就可以把它包装成普通 Tool，让 LLM 像调用本地工具一样调用外部工具。
```

### MiniCode 中的 MCP 链路

```text
配置 mcpServers
-> load_effective_settings() 合并全局/项目 MCP 配置
-> create_default_tool_registry()
-> create_mcp_backed_tools()
-> StdioMcpClient 启动 MCP server 进程
-> initialize / tools/list / resources/list / prompts/list
-> 把 MCP tool 包装成 ToolDefinition
-> ToolRegistry 注册
-> 模型看到 mcp__server__tool 形式的工具
-> Agent Loop 正常执行 ToolCall
-> MCP client 发送 tools/call JSON-RPC 请求
-> 外部 MCP server 返回结果
-> ToolResult 回填 messages
```

### 三类 MCP 能力

```text
1. tools
   外部服务暴露的可执行能力。MiniCode 会包装成 mcp__server__tool 这种工具名。

2. resources
   外部服务暴露的可读取资源。MiniCode 提供 list_mcp_resources 和 read_mcp_resource。

3. prompts
   外部服务提供的提示词模板。MiniCode 提供 list_mcp_prompts 和 get_mcp_prompt。
```

### 和本地 Tool / Skill 的关系

```text
本地 Tool：MiniCode 代码里直接实现的 Python ToolDefinition。
MCP Tool：外部 MCP server 暴露的工具，被 MiniCode 包装成 ToolDefinition。
Skill：方法论文档，本身不执行；需要时通过 load_skill 读取。

所以 MCP 接入后，在 Agent Loop 看来仍然是 Tool Calling，只是工具执行端从“本地 Python 函数”变成了“外部 MCP server”。
```

### 当前实现特点

```text
MiniCode 当前 MCP 实现以 stdio 子进程为主，通过 JSON-RPC 通信。

安全上会做：
- MCP command 白名单检查。
- 禁止危险 shell。
- 参数禁止 shell 元字符。
- payload 大小限制。
- 请求超时和进程退出清理。

工程上会做：
- 启动时读取 mcpServers 配置。
- 创建 StdioMcpClient。
- 尝试发现 tools/resources/prompts。
- 把工具注入 ToolRegistry。
- 在 prompt 中提示已连接 MCP server。
```

### 面试表达

```text
MiniCode 的 MCP 接入本质是把外部服务能力标准化为内部 ToolDefinition。系统从 settings 或 .mcp.json 读取 mcpServers 配置，通过 StdioMcpClient 启动外部 MCP server，并使用 JSON-RPC 完成 initialize、tools/list、resources/list、prompts/list 等握手和能力发现。发现到的 MCP tools 会被包装成 mcp__server__tool 形式注册进 ToolRegistry，因此 Agent Loop 不需要区分本地工具和外部 MCP 工具，仍然走统一的 ToolCall -> ToolResult 回填链路。这个设计把外部生态能力接入和核心 Agent Loop 解耦，同时通过命令白名单、参数校验、payload 限制、超时和进程清理保证基础安全性。
```

---

## 第 10 阶段：项目复盘与简历答辩

### 面试题 1：请用 1-2 分钟介绍 MiniCode 项目

用户回答：

```text
你好面试官，MiniCode这个项目是基于Claude code架构设计出来的一个coding agent，他是接入llm之后可以帮助用户进行代码编写、审查、修改等功能的智能体。MiniCode 是本地Ai Coding Agent，不是普通的问答系统，用户任务进入Agent Loop之后，LLM根据上下文判断是直接回答还是进行工具调用；工具结果会回填到messages里面，再由模型继续推理，直到生成最终回答或者达到步数限制。普通的聊天机器人主要是生成文本，而MiniCode可以读写文件、搜索代码、维护上下文和记忆，他的核心是Query Loop + Tool Use的任务执行闭环。
```

反馈：

```text
回答方向正确，已经讲清楚了“不是普通 ChatBot”，也说出了 Agent Loop、Tool Calling、messages 回填、步数限制和 Query Loop + Tool Use 闭环。

需要提升的是架构层次感。当前回答偏执行流程，还可以在 1-2 句话里补上系统由哪些核心模块组成：模型适配层、工具系统、Skill 路由、Memory、上下文压缩、多 Agent 和权限安全。这样面试官会感觉你讲的是系统架构，而不是只会讲一段循环逻辑。
```

升级版表达：

```text
MiniCode 是一个参考 Claude Code 架构实现的本地 AI Coding Agent，目标不是做普通问答，而是在真实代码仓库中完成代码阅读、修改、调试、审查和任务执行。它的核心架构是 Query Loop + Tool Use：用户任务进入系统后，系统会构建包含项目上下文、Skill、Memory、MCP 和权限信息的 prompt，模型基于上下文决定是直接回答还是发起工具调用；工具由本地 runtime 受控执行，结果再回填到 messages 中，让模型继续推理，直到输出最终回答或达到步数上限。

和普通 ChatBot 最大区别是，ChatBot 主要生成文本，而 MiniCode 具备可执行闭环。它通过 ToolRegistry 管理读写文件、搜索代码、运行命令、加载 Skill、调用 MCP 等能力；通过 Skill 路由降低能力检索噪声；通过 Memory 和上下文压缩维持长任务稳定性；通过权限审查控制文件写入和危险命令。因此它更像一个面向软件工程场景的受控任务执行系统，而不是单轮文本生成系统。
```

### 面试题 2：为什么 Coding Agent 需要 Query Loop + Tool Use 闭环？

用户回答：

```text
你好面试官，我回答以上问题。如果Coding Agent 没有闭环，也就是您说的只调用一次就回答问题，llm可能只会回答一些简单的问题，而不是根据本地项目文件得到的综合结果。llm只能凭借自己的知识库来回答问题，而不能看到这个项目的整体情况。
```

反馈：

```text
这个回答抓住了一个核心点：单次 LLM 调用缺少本地项目事实，容易凭记忆回答，不能基于真实仓库分析。

但还不够完整。Coding Agent 需要闭环，不只是为了“读到项目文件”，更是为了支持多步任务执行：先观察、再计划、再调用工具、再根据工具结果修正判断、再继续执行，直到完成任务。工具结果回填不仅提供事实，还提供反馈信号，例如文件内容、命令结果、测试失败、权限拒绝、错误输出等。
```

升级版表达：

```text
Coding Agent 需要 Query Loop + Tool Use，是因为真实开发任务通常不是一次 LLM 调用能完成的。单次调用只能基于已有上下文生成文本，无法主动读取项目文件、搜索调用链、运行测试、观察错误结果，也无法根据工具反馈修正自己的判断。

闭环的价值在于把任务拆成“模型决策 -> 工具执行 -> 结果观察 -> 继续推理”的过程。比如模型先判断需要读入口文件，工具返回文件内容后，它再决定搜索某个函数引用；如果执行测试失败，失败日志会回填到 messages，模型可以基于新的证据继续定位问题。这样 Agent 的行为不再是一次性猜答案，而是基于真实环境反馈逐步收敛。

同时闭环也需要边界控制，所以 MiniCode 会设置最大步数、权限审查和工具结果回填格式，避免模型无限循环或不受控执行。
```

### 面试题 3：为什么 assistant_tool_call 和 tool_result 都要写回上下文？

用户回答：

```text
不可以，如果只把tool_result写回上下文，模型不知道这是哪次工具、哪些工具调用返回的结果，因为llm可能会调用多个工具，工具调用结果有可能是多条的，因此不论是在记忆模块还是在上下文压缩模块，都一定要把工具调用和调用结果放到一起，避免留下单独的某一个
```

反馈：

```text
回答正确，已经抓住了“工具调用和工具结果必须成对保留”的重点，也联系到了上下文压缩和记忆模块。

可以再补两层架构表达：第一，这是为了维护 Tool Calling 协议语义，模型需要知道自己上一轮发起了哪个工具、参数是什么、返回结果对应哪个调用；第二，这是为了保证后续压缩、摘要、调试和审计都能恢复任务因果链。如果只保留 tool_result，会丢失“为什么调用、调用了什么、参数是什么”的上下文。
```

升级版表达：

```text
不行。assistant_tool_call 和 tool_result 是一组因果关系，不能只保留结果。assistant_tool_call 记录的是模型在上一轮决策中选择了哪个工具、传了什么参数；tool_result 记录的是本地 runtime 执行后的真实结果。只有两者一起写回 messages，模型下一轮才能知道这个结果对应哪次工具调用，尤其是在一次响应里有多个 tool call 或并发工具调用时更重要。

从架构上看，这也是为了维护统一的 Tool Calling 协议和可审计性。后续无论是模型继续推理、上下文压缩、记忆沉淀，还是排查工具执行错误，都需要保留“决策 -> 执行 -> 结果”的完整链路。如果只留下 tool_result，系统就会丢失调用意图和参数，模型可能误用结果，上下文压缩也可能把工具调用语义切断。
```

### 面试题 4：为什么需要 Model Adapter，而不是在 Agent Loop 里直接写 OpenAI/Anthropic 调用？

用户回答：

```text
我认为模型适配是很重要的，仅在Agent Loop 里面写OpenAI 或 Anthropic 的 API 调用逻辑是远远不够的，就拿工具调用来说，不同模型（gpt/opous）接收到的工具定义也是不同的，如果在Agent Loop里面写入，会有很多ififif嵌套的条件判断表示，这样系统稳定性是很差的，因此我们设计了模型适配层，在模型适配这一层直接把工具调用、messages消息队列等进行了相应的修改，修改成了系统统一的一个类。
```

反馈：

```text
回答方向正确。你抓住了 provider 差异会污染 Agent Loop 的问题，也说到了不同模型工具定义和 messages 格式不同。

需要修正两个表达：第一，不要说“修改成系统统一的一个类”，更准确是“适配成 MiniCode 内部统一协议”，例如统一的 ChatMessage / ToolDefinition / AgentStep。第二，面试时少说 ififif，可以说“分支逻辑膨胀、核心循环和 provider 细节耦合，影响可维护性和可测试性”。
```

升级版表达：

```text
MiniCode 需要 Model Adapter，是为了把不同模型供应商的协议差异隔离在边界层，而不是污染 Agent Loop。OpenAI、Anthropic、OpenRouter 等 provider 在 messages 格式、system/developer 角色、tool schema、tool call 返回结构、错误格式上都不完全一致。如果这些差异直接写在 Agent Loop 里，核心循环会充满 provider 分支判断，导致工具调用、结果解析、异常处理和后续扩展都很难维护。

所以 MiniCode 的设计是：Agent Loop 只依赖内部统一协议，例如内部 messages、ToolDefinition 和 AgentStep。请求前由 adapter 把内部消息和工具定义转换成 provider API 格式；响应后由 adapter 再把 provider 返回解析成统一的 AgentStep。这样 Agent Loop 只需要判断“assistant final/progress”还是“tool_calls”，不需要关心底层是 OpenAI 还是 Anthropic。

这个设计的收益是核心任务执行逻辑稳定，新增模型只需要新增 adapter，不需要改 Agent Loop；同时也更容易测试 mock adapter 和多 provider 行为。
```

### 面试题 5：为什么需要 ToolRegistry，而不是在 Agent Loop 里手动 if tool.name 执行？

用户回答：

```text
你好面试官，我回答这个问题。设计ToolRegistry的目的是为了让llm认识到有这些工具，如果在Agent Loop直接调用Python函数，会面临两个问题，不同供应商的工具调用格式不同，还要if runtime == openai？等等，这样会有一些条件分支，同时，还需要 if tool.name == read.file ，条件分支套着条件分支，后续扩展很难维护。
```

反馈：

```text
方向是对的：你抓住了“不要在 Agent Loop 里写大量 if tool.name 分支”和“扩展困难”这个核心点。

但这里有一个边界需要分清：不同供应商工具调用格式不同，主要是 Model Adapter 要解决的问题；ToolRegistry 主要解决的是 MiniCode 内部工具的统一注册、查找、参数校验、执行、异常保护和结果返回。也就是说，adapter 负责把 provider 的 tool call 转成内部工具调用意图，ToolRegistry 负责根据这个意图找到并执行真实工具。
```

升级版表达：

```text
ToolRegistry 的核心价值是把工具执行从 Agent Loop 里抽离出来，形成统一的工具注册和执行层。如果在 Agent Loop 里写 if tool_name == read_file、if tool_name == grep_files 这种分支，随着工具数量增加，核心循环会越来越臃肿，新增工具也需要改 Agent Loop，工具参数校验、权限检查、异常处理和返回格式也容易分散在不同地方。

MiniCode 的做法是把每个工具抽象成 ToolDefinition，包含 name、description、input_schema、validator 和 run。模型只能看到工具描述和参数 schema，真正执行时由 ToolRegistry 根据 tool name 查找工具，先做参数校验，再调用对应 run，最后统一返回 ToolResult。这样 Agent Loop 不需要知道 read_file 或 run_command 的具体实现，只负责调度“模型决策 -> 工具执行 -> 结果回填”。

从架构上看，Model Adapter 解决 provider 协议差异，ToolRegistry 解决内部工具生命周期管理。两者配合后，新增模型不需要改工具系统，新增工具也不需要改 Agent Loop。
```

### 面试题 6：Tool 已经能告诉模型有哪些能力了，为什么还要设计 Skill？Skill 和 Tool 的边界是什么？

用户回答：

```text
你好面试官，我来回答这个问题。其实这个问题本质上就是tool和skill的区别，我认为如果llm是大脑的话，tool就是这个机器人的手，tool告诉了llm我能做什么，比如我可以读文件，写文件，跑命令；而skill相当于是技能点加成，他告诉了llm 我该用哪一种准则去完成这件事，完成这件事需要哪些步骤，从本质来说，工具是一个py文件，而skill是md指导性文档。
```

反馈：

```text
这个回答很容易让面试官听懂，尤其是“Tool 是手，Skill 是方法论”这个比喻是有效的。

但面试里不能只停留在比喻和文件类型。需要进一步讲设计原因：只有 Tool 时，模型知道“能做什么”，但不知道“面对某类任务应该按什么流程做、有哪些边界、先后顺序是什么、什么情况不要做”。Skill 解决的是复杂任务的方法论复用、行为约束和 token 控制，不是执行能力本身。
```

升级版表达：

```text
Tool 和 Skill 的边界是：Tool 解决“能执行什么动作”，Skill 解决“面对某类任务应该怎么做”。Tool 是可执行能力，比如读文件、搜索代码、运行命令、写文件；它通过 name、description、input_schema 暴露给模型，并由 ToolRegistry 受控执行。Skill 本身不执行，它是一个 Markdown 方法论文档，描述某类任务的流程、判断准则、边界条件、示例和最佳实践，需要时由模型通过 load_skill 按需读取。

如果系统只有 Tool，模型虽然知道自己能读文件、跑命令、改代码，但在复杂任务里仍然可能不知道正确的工作流，例如调试失败测试时应该先看失败日志、定位测试、形成假设、最小修复、跑 focused test，而不是一上来大范围改代码。Skill 把这种高层经验沉淀成可复用的工作流，降低模型在复杂任务中的随机性。

所以 MiniCode 里 Tool 是原子执行层，Skill 是高层方法论层。Tool 通过 ToolRegistry 执行，Skill 通过 Skill 路由和 load_skill 进入上下文。两者结合后，模型既知道“有哪些手段”，也知道“按什么策略使用这些手段”。
```

### 面试题 7：为什么不能把所有 Skill 摘要或全文都塞进 prompt？为什么要做 Skill 路由和 Top-K 精排？

用户回答：

```text
你好面试官，我来回答这个问题。把所有的Skill摘要或全文都塞进prompt很占用上下文窗口，随着skill数量增长，全部注入prompt会带来token成本高，上下文噪音大；设计了Skill路由和Top-K精排是为了选取通过用户输入的问题进行意图识别和能力注册，然后选择适当的几个Skill，把摘要给注入到prompt里面，然后模型判断真正需要的话，才决定使用load-skill 进行按需读取，把全文内容作为ToolResult返回给模型。
```

反馈：

```text
回答很好，已经讲到了 token 成本、上下文噪音、意图识别、能力注册、Top-K 摘要注入和 load_skill 按需读取全文。

可以再补一个架构点：Skill 增长后，不只是 token 成本问题，还有召回空间过大、功能重叠、相似 Skill 干扰模型决策的问题。Skill 路由的价值是先用结构化信号缩小候选空间，再把少量相关摘要交给模型，而不是把选择压力完全交给 LLM。
```

升级版表达：

```text
不能把所有 Skill 全部塞进 prompt，主要有三个问题。第一是 token 成本和上下文窗口占用，Skill 数量增长后，全文注入会很快挤占真正任务上下文。第二是噪声问题，很多 Skill 与当前任务无关，全部注入会干扰模型判断。第三是能力重叠问题，随着 Skill 自进化增长，相似 Skill 可能越来越多，如果完全让模型在 prompt 里自己挑，会增加选择不稳定性。

所以 MiniCode 做了 Skill 路由和 Top-K 精排。用户输入进来后，先通过 ParsedIntent 提取 intent、action、keywords、entities，再通过 CapabilityRegistry 知道当前系统有哪些真实工具能力。SkillRouter 先召回相关 Directory，再在目录内对具体 Skill 排序，最后结合 tools 声明做 tool affinity 加分或风险降权，只把 Top-K 相关 Skill 摘要注入 prompt。

同时 MiniCode 不自动注入 Skill 全文，而是保留 load_skill 按需读取。这样 prompt 里只放“候选 Skill 摘要”，模型判断某个 Skill 真正适用时，再调用 load_skill 读取完整方法论。这个设计在 Skill 增长时能降低 token 成本和检索噪声，也保留了复杂任务下的高层工作流指导。
```

### 面试题 8：CapabilityRegistry 在 Skill 路由中起什么作用？

用户回答：

```text
CapabilityRegistry会根据工具的名称把这个工具的能力给他显示出来。这里的skill路由是有三层，第一层召回是根据用户输入的问题进行意图识别，然后根据intent type和action type以及目录名、目录描述选到适当的目录下；第二层是在命中的Directory内给依据skill的name、描述、domain、scope等等给其打分，第三层则是用到了CapabilityRegistry，即如果这个skill声明了tools，如果这些tools也真实存在，且这些tools的能力正好对应skill的domain和scopes等，就加分。如果只根据用户问题和skill描述做匹配，容易出现召回不准、召回太粗、噪音多等问题
```

反馈：

```text
回答已经很接近标准答案。你讲清楚了三层路由，也讲到了 tools 真实存在和 domain/scope 匹配加分。

可以补两个关键点：第一，CapabilityRegistry 不只是“显示工具能力”，而是把当前系统真实可用工具抽象成能力画像，例如 file/search/execution + readonly/write/destructive。第二，它不仅能加分，也能做风险降权，例如只读任务里依赖 destructive/external 工具的 Skill 会被降权。
```

升级版表达：

```text
CapabilityRegistry 在 Skill 路由里解决的是“当前系统真实具备什么能力，以及这些能力的风险边界是什么”。它会把 ToolRegistry 里的真实工具抽象成能力元数据，例如 read_file 是 file/readonly，grep_files 是 search/readonly，run_command 是 execution/destructive。这样 SkillRouter 不只是看用户问题和 Skill 文本，还能知道某个 Skill 声明依赖的工具是否真实存在、能力类型是否匹配当前任务、风险级别是否合适。

在三层路由里，前两层主要做 Directory 召回和 Skill 文本/元数据打分；第三层 tool affinity 会用 CapabilityRegistry 检查 Skill 声明的 tools。如果工具存在，会有基础加分；如果工具的 domain/scope 和当前 intent/action 推导出的能力需求匹配，会进一步加分；如果当前是 read/analyze 这种只读任务，但 Skill 依赖 destructive 或 external 工具，则会降权。

如果没有 CapabilityRegistry，只根据用户问题和 Skill 描述匹配，系统只能做文本相似度判断，无法判断这个工作流依赖的工具当前是否可用，也无法区分只读分析和高风险执行类 Skill，容易造成召回粗、噪音大、风险边界不清晰。
```

### Skill 路由补充：intent/action 和 domain/scope 的区别

用户问题：

```text
第一步判断是否匹配，是不是主要依据 Skill 自身的 domain/scope 和用户问题通过意图识别解析出来的内容？domains 和 scopes 具体是什么？感觉它们和 intent_type/action_type 很像。
```

标准回答：

```text
第一步 Directory 匹配不是直接看“Skill 自身”本身，而是看目录层的信息，以及目录下 Skill 汇总出来的 domains/scopes/keywords。

用户问题会先经过 parse_intent()，得到 intent_type 和 action_type。然后 SkillRouter 会把 intent/action 映射成当前任务需要的 capability domains/scopes，再拿这些 domains/scopes 去匹配 Directory 和 Skill 的元数据。
```

概念区分：

```text
intent_type:
描述用户想做什么类型的任务。
例如 explain / debug / refactor / test / document。

action_type:
描述用户想执行什么动作。
例如 read / analyze / create / update / execute。

domain:
描述能力属于什么领域。
例如 code / file / search / memory / execution / analysis。

scope:
描述能力的权限和风险范围。
例如 readonly / write / destructive / external。
```

一句话：

```text
intent/action 描述用户意图；
domain/scope 描述能力画像和风险边界。
```

示例：

```text
用户：解释 agent_loop.py 为什么会调用工具

parse_intent:
intent_type = explain
action_type = read

路由推导出的能力需求：
domains = code / file / search / analysis
scopes = readonly

因此标注了 code/file/search/analysis + readonly 的目录或 Skill 更容易被召回。
```

另一个示例：

```text
用户：帮我运行 pytest 并修复失败测试

parse_intent:
intent_type = debug 或 test
action_type = analyze 或 execute

路由推导出的能力需求：
domains = code / file / search / execution
scopes = readonly / destructive

因此 pytest-debugging 这类 Skill 如果标注了 execution，并声明 run_command，就更容易被召回。
```

最终记忆：

```text
intent/action 是“用户要干什么”；
domain/scope 是“完成这件事需要哪类能力，以及风险范围多大”。
```

### 面试题 9：MiniCode 为什么要设计 Memory System？

面试官问题：

```text
MiniCode 为什么要设计 Memory System？
为什么不能只依赖当前 messages 作为上下文？
USER / PROJECT / LOCAL 三层记忆分别解决什么问题？
```

你的回答：

```text
随着会话交流次数以及上下文压缩次数的增加，上下文窗口压力增大，LLM 很难提取到最开始用户要求的内容，因此不能只依赖 messages 作为上下文。

USER 记忆是跨项目记忆，记录了这个用户的偏好，比如语言、通用经验；
PROJECT 是这个项目的共享记忆，比如这个项目的架构、规范、测试习惯等；
LOCAL 是当前机器的私有信息，比如本地路径、临时调试信息等。
```

面试官反馈：

```text
这个回答方向是对的，已经说清楚了 messages 的局限，以及三层记忆的大致职责。

但从架构答辩角度，还可以再强调两点：
1. Memory System 不只是“存更多上下文”，而是把短期对话中的稳定信息沉淀成可检索、可复用的长期资产。
2. USER / PROJECT / LOCAL 的分层不是简单分类，而是为了解决复用范围、隐私边界和项目一致性的问题。
```

更适合面试的回答：

```text
MiniCode 不能只依赖 messages，因为 messages 是短期上下文，会随着多轮工具调用、长会话和上下文压缩不断增长、裁剪和摘要化。它适合承载当前任务的即时状态，但不适合长期保存用户偏好、项目约定、历史修复经验和本地环境信息。

因此系统引入 Memory System，把对话和执行过程中稳定、可复用的信息沉淀成长期记忆，并在后续任务开始前按需检索注入 prompt。这样既能降低上下文窗口压力，也能让 Agent 在跨会话任务中保持一致性。

USER 记忆解决跨项目复用问题，例如用户偏好中文、回答风格、常用技术栈和通用工作习惯。

PROJECT 记忆解决项目内共享知识问题，例如项目架构、代码规范、测试命令、历史 bug 修复经验和重要技术决策。

LOCAL 记忆解决本机私有环境问题，例如本地路径、虚拟环境、机器特定配置和临时调试信息。它不应该默认扩散到全局或项目共享层。

所以 Memory System 的核心价值不是简单扩展上下文，而是把“短期对话状态”和“长期可复用经验”分开管理，并通过分层作用域控制复用范围和安全边界。
```

最终记忆：

```text
messages 是短期工作区；
memory 是长期经验库；
USER / PROJECT / LOCAL 分别对应跨项目偏好、项目共享知识、本机私有环境。
```

### 面试题 10：什么是自进化记忆沉淀？

面试官问题：

```text
MiniCode 的“自进化记忆沉淀”具体解决什么问题？
为什么不是每次对话结束都直接把完整对话存进 Memory？
```

你的回答：

```text
自进化记忆沉淀可以在任务结束后由 agent loop 触发，系统根据执行过程自动反思，把关键决策、错误、工具使用、经验教训和任务上下文转成结构化 PROJECT 记忆。

每次对话结束把完整对话存入 Memory，会让系统难以准确寻找相关文本，噪音多，同时负担也重。

这个部分我还真不是特别熟悉。
```

面试官反馈：

```text
这个回答方向是对的，尤其是“不能存完整对话”和“要沉淀关键经验”这两个点说到了。

还可以再补强三点：
1. 自进化记忆沉淀不是简单保存聊天记录，而是把执行过程中的稳定经验提炼成可复用资产。
2. 系统不是所有任务都写入记忆，而是通过 ReflectionEngine 计算 success、errors、lessons、confidence，再按阈值决定是否持久化。
3. 写入 PROJECT 记忆，是因为这类任务经验通常和当前代码库相关，不应该默认污染 USER 全局偏好。
```

项目中的主链路：

```text
agent_loop 任务结束
-> cybernetic_orchestrator.reflect_on_task()
-> MemoryPipeline.write()
-> ReflectionEngine.reflect()
-> MemoryManager.add_entry(PROJECT)
```

各组件职责：

```text
agent_loop:
负责执行任务闭环，并在任务结束时留下 execution_trace。

CyberneticOrchestrator:
统一调度记忆注入、反思、上下文治理等高级能力。

MemoryPipeline:
作为记忆系统统一入口，负责 read / inject / write / maintain 四类操作。

ReflectionEngine:
从 execution_trace 中提取任务是否成功、工具调用、错误、关键决策、经验教训和置信度。

MemoryManager:
负责真正把结构化记忆写入 USER / PROJECT / LOCAL 对应存储。
```

更适合面试的回答：

```text
自进化记忆沉淀解决的是 Coding Agent 跨会话学习和经验复用的问题。

如果 Agent 每次都只依赖当前 messages，那么一次任务里踩过的坑、项目里的约定、成功的调试路径，在下次会话中就很难被复用。MiniCode 在任务结束后，会基于 execution_trace 做一次反思，把工具调用、错误、关键决策、任务结果和经验教训提炼成结构化记忆。

它不会把完整对话直接塞进 Memory，因为完整对话里有大量临时推理、冗余上下文、工具原始输出和无关噪音。直接保存会导致检索不准、存储膨胀，也会把短期状态误当成长期知识。

所以系统采用“执行 -> 反思 -> 提炼 -> 置信度过滤 -> 分类存储 -> 后续检索复用”的闭环。成功任务通常更容易沉淀为经验；失败或错误较多的任务会降低 confidence，避免把不可靠经验写入长期记忆。

这类任务经验默认写入 PROJECT 记忆，因为它大多和当前代码库、测试命令、架构约定、历史 bug 有关，不应该默认写入 USER 全局记忆。
```

最终记忆：

```text
自进化记忆沉淀不是存完整对话；
它是把 execution_trace 里的稳定经验提炼成结构化 PROJECT 记忆；
通过 confidence 过滤，避免把噪音和失败经验污染长期记忆。
```

### 面试题 11：上下文压缩和 Memory System 有什么区别？

面试官问题：

```text
MiniCode 为什么要做上下文压缩？
上下文压缩和 Memory System 有什么区别？
```

你的回答：

```text
随着交流次数的增加，上下文窗口使用率越来越高，这个时候就需要上下文压缩。

如果不进行上下文压缩，LLM 只能看到最近用户提出的问题以及自己的反思过程。

记忆系统是通过把相关文本直接注入到系统提示词里，而上下文压缩则是通过把 messages 队列给进行压缩进行的，这是他们的本质区别。
```

面试官反馈：

```text
你的方向是对的：Memory System 和上下文压缩不是一回事。

但“不压缩时 LLM 只能看到最近内容”这句话要改得更严谨：
不压缩时，问题首先是 messages 会越来越大，导致 token 成本升高、Prompt Cache 收益下降、模型注意力变差，严重时会超出上下文窗口或被迫截断，最终才表现为早期关键信息丢失。

另外，Memory System 不是简单“把相关文本直接注入系统提示词”，更准确地说，是先检索相关长期记忆，再按预算和相关性注入 prompt。
```

更适合面试的回答：

```text
MiniCode 做上下文压缩，是因为 Coding Agent 的 messages 增长非常快。

一轮任务里可能包含用户需求、模型推理、工具调用、工具结果、文件内容、命令输出、错误日志等。如果一直完整保留，messages 会快速接近上下文窗口上限，带来 token 成本高、模型注意力分散、Prompt Cache 收益下降和超限失败等问题。

上下文压缩解决的是“当前会话工作区太大”的问题。它主要处理 messages 队列，把长工具结果外置化，把大段内容替换成缓存友好的占位符，并通过结构化摘要保留任务目标、关键决策、工具调用链路和最近上下文。

Memory System 解决的是“长期经验如何跨会话复用”的问题。它把用户偏好、项目约定、历史经验、环境信息等稳定知识保存下来，在新任务开始时按相关性检索并注入 prompt。

所以二者的边界是：
上下文压缩管理短期会话上下文；
Memory System 管理长期可复用知识。

它们也会协作：压缩后的摘要可以保留当前任务状态，Memory 检索可以补回历史经验。这样 Agent 既不会因为上下文太长而失稳，也不会因为压缩丢掉跨会话经验。
```

最终记忆：

```text
上下文压缩 = 治理当前 messages，解决短期上下文太长的问题；
Memory System = 检索长期经验，解决跨会话复用的问题；
两者都可能进入 prompt，但来源、生命周期和目标不同。
```

### 面试题 12：为什么设计中心化多 Agent 协作？

面试官问题：

```text
MiniCode 为什么要设计中心化多 Agent 协作？
为什么不让多个 Agent 之间自由通信、互相调度？
```

你的回答：

```text
多个 Agent 自由通信、互相调度会严重污染 messages 队列，以及有可能会触发严重的安全权限问题。
```

面试官反馈：

```text
这个回答抓住了两个重要点：上下文污染和权限风险。

但面试里还需要补上“架构权衡”：
中心化多 Agent 不是为了追求复杂协同，而是为了在 Coding Agent 场景里保持主流程可控。主 Agent 负责规划、审批、质量控制和最终决策；子 Agent 只作为受控 ToolCall 执行局部任务，并返回最小必要结果。
```

更适合面试的回答：

```text
MiniCode 采用中心化多 Agent 协作，是因为 Coding Agent 的核心目标不是开放式聊天协作，而是在真实代码仓库里稳定、安全地完成任务。

如果让多个 Agent 自由通信、互相调度，会带来几个问题：

第一，主上下文会被污染。多个 Agent 的中间推理、探索过程和无关细节如果都进入主 messages，会快速增加 token 成本，也会干扰主 Agent 对任务状态的判断。

第二，状态一致性会变差。多个 Agent 如果都能独立规划和修改代码，很容易出现重复工作、目标冲突、文件修改冲突和责任边界不清。

第三，权限风险会放大。子 Agent 如果能自由调用写文件、执行命令、访问外部资源，就很难统一做路径边界、命令风险、人工审批和 prompt 注入防御。

所以 MiniCode 让主 Agent 保留控制权：主 Agent 负责统一规划、审批和质量控制；子 Agent 通过 task 这类工具被调用，只拿到受限上下文和受限工具集，执行完成后只返回摘要结果，而不是把完整过程塞回主上下文。

这样既能利用子 Agent 做并行探索、代码阅读、方案分析，又不会引入复杂的 Agent 间协调基础设施，也能把安全边界收敛在主 Agent 和工具权限层。
```

最终记忆：

```text
中心化多 Agent 的关键不是“多个 Agent 自由协作”，而是“主 Agent 控制，子 Agent 受控执行”。
主 Agent 负责规划、审批、质量；
子 Agent 负责局部探索和执行；
结果最小化回传，避免上下文污染和权限扩散。
```
