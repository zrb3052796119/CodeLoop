# MiniCode AI Coding Agent 学习路线计划

本计划围绕你的简历描述展开：参考 Claude Code 架构，实现 AI Coding Agent，基于 Query Loop + Tool Use 构建任务执行闭环，并重点理解 Skill 路由、自进化记忆、分层上下文压缩、多 Agent 协作、权限与安全审查。

学习目标不是只看懂文件名，而是做到四件事：

1. 能画出系统从用户输入到最终回答的完整链路。
2. 能解释每个核心模块为什么存在、解决什么问题、有什么取舍。
3. 能做一个小改动，例如新增工具、新增 Skill、调整记忆检索或权限规则。
4. 能在面试中经得住追问，尤其是 Query Loop、Tool Calling、Memory、Prompt Cache、Multi-Agent 和安全控制。

## 0. 学习对象说明

这个仓库里有三套相关代码：

- `minicode/`: 当前根目录有效 Python 包，`pyproject.toml` 安装和测试默认使用它。
- `py-src/minicode/`: Python 迁移/实验镜像，包含你简历里提到的很多能力点，适合学习 Agent 架构。
- `ts-src/`: TypeScript 参考实现，和 Claude Code 风格更接近，适合作为对照阅读。

建议学习主线：

```text
先读 py-src/minicode/ 理解能力设计
再对照 minicode/ 看当前有效实现
最后用 ts-src/ 理解 Claude Code 风格来源
```

## 1. 总体路线

推荐学习周期：4 周到 6 周。

每个阶段按这个顺序推进：

```text
先跑通 -> 再画链路 -> 再读核心代码 -> 再做小实验 -> 最后用面试语言复述
```

阶段安排：

| 阶段 | 主题 | 目标 |
| --- | --- | --- |
| 第 1 阶段 | 项目总览 | 知道 MiniCode 是什么，主入口在哪里，用户请求如何进入系统 |
| 第 2 阶段 | Query Loop + Tool Use | 看懂 agent_loop 如何驱动模型和工具循环 |
| 第 3 阶段 | Tool Calling 系统 | 看懂工具定义、注册、执行、结果回填 |
| 第 4 阶段 | 模型适配层 | 看懂 Anthropic/OpenAI/mock adapter 如何统一成内部协议 |
| 第 5 阶段 | Skill 能力体系 | 看懂 Tool、Skill、Capability、Intent 的关系 |
| 第 6 阶段 | Memory System | 看懂跨会话记忆、用户画像、记忆注入与防污染问题 |
| 第 7 阶段 | Context + Prompt Cache | 看懂上下文分层、压缩、工具结果外置和缓存友好设计 |
| 第 8 阶段 | 多 Agent 协作 | 看懂主 Agent 控制、子 Agent ToolCall 化、结果最小传递 |
| 第 9 阶段 | 权限与安全审查 | 看懂危险命令过滤、工具权限、prompt 注入风险防御 |
| 第 10 阶段 | 简历答辩 | 把技术亮点转化成面试可讲的项目故事 |

## 当前进度

| 阶段 | 状态 | 当前结论 |
| --- | --- | --- |
| 第 1 阶段：项目总览 | 已完成 | MiniCode 是本地 AI Coding Agent，不是普通问答系统；`main.py` 是入口和运行时组装器，真正任务执行由 `agent_loop.py` 驱动 |
| 第 2 阶段：Query Loop + Tool Use | 基本完成 | 已掌握 `run_agent_turn()` 最小闭环和工具执行链路：模型只输出工具意图，本地 runtime 通过 `ToolRegistry` 受控执行，结果回填后继续推理 |
| 第 3 阶段：Tool Calling 系统 | 已完成 | 已掌握工具定义、注册、查找、校验、执行和返回结果；已新增并验证默认文件工具 `file_line_count` |
| 第 4 阶段：模型适配层 | 已完成 | 已掌握 adapter 选择、工具 schema 序列化、消息格式转换、provider 响应解析为统一 `AgentStep` |
| 第 5 阶段：Skill 能力体系 | 已完成 | 已掌握并实现 Skill 发现、按需加载、Intent/Capability 路由、Top-K 精排和 prompt 注入 |
| 第 6 阶段：Memory System | 已完成 | 已掌握三层存储、显式记忆写入、普通/高级注入、Reranker、自进化反思写入和后台记忆治理 |
| 第 7 阶段：Context + Prompt Cache | 已完成 | 已掌握基础上下文统计、工具结果外置化、重复读文件去重、microcompact、Session Memory Compact、Full Compact、Reactive Compact 和 ContextCybernetics 控制层 |
| 第 8 阶段：中心化多 Agent 协作 | 已完成 | 已掌握主 Agent 通过 `task` ToolCall 受控调用子 Agent、子 Agent 独立上下文执行、结果最小化回传、只读工具过滤、权限边界和 Worktree/AgentTeam 扩展定位 |
| 第 9 阶段：权限与安全审查 | 已完成 | 已掌握路径边界检查、写入 diff 审批、命令风险分类、权限模式和 prompt 注入检测；安全边界主要落在工具层和权限层，而不是只依赖模型自律 |

第 2 阶段当前拆解进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| `run_agent_turn()` 最小闭环 | 已完成 | `while max_steps` 限制模型-工具循环次数，避免无限循环和 token/时间成本失控 |
| `AgentStep` 输出类型 | 已完成 | `assistant` 表示模型输出文本，可能是 final 或 progress；`tool_calls` 表示模型请求系统执行工具 |
| 工具结果回填 | 已完成 | `assistant_tool_call` 记录模型选择的工具和参数，`tool_result` 记录工具真实输出，供下一轮 LLM 推理 |
| `_execute_single_tool()` 到 `ToolRegistry.execute()` | 已完成 | `_execute_single_tool()` 属于 agent loop 调度包装层；`ToolRegistry.execute()` 属于工具系统执行总入口，负责查找工具、参数校验、运行和异常保护 |
| Tool 安全边界 | 已完成 | `resolve_tool_path()` 处理路径归一化和越界检查；`run_command` 需要更强审查，因为它可能删除文件、修改 Git、执行脚本、泄露环境变量或发起网络操作 |

已经掌握的工具调用链路：

```text
模型看到 ToolDefinition 的 name / description / input_schema
-> 模型返回 tool call：toolName + input
-> agent_loop._execute_single_tool() 提取工具名和参数
-> 构造 ToolContext(cwd, permissions, runtime)
-> ToolRegistry.execute(toolName, input, context)
-> find(toolName) 找到 ToolDefinition
-> validator(input) 校验不可信 JSON 参数
-> run(parsed, context) 执行真实 Python 工具
-> 返回 ToolResult(ok, output)
-> agent_loop 写入 assistant_tool_call 和 tool_result
-> continue 进入下一轮 LLM 推理
```

关键面试表达：

```text
Tool Calling 在 MiniCode 里不是让模型直接执行代码，而是把模型的工具意图转换成受控的本地执行：模型只输出工具名和 JSON 参数，Agent Loop 负责调度，ToolRegistry 负责查找、校验和运行，PermissionManager 负责路径和命令安全边界，执行结果再回填给模型继续推理。
```

第 3 阶段实操进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| 拆解 `read_file` 工具 | 已完成 | `ToolDefinition` 由 `name`、`description`、`input_schema`、`validator`、`run` 组成 |
| 新增文件行数工具 | 已完成 | 新增 `file_line_count`，避免和已有 full profile 文本工具 `line_count` 同名冲突 |
| 注册默认工具 | 已完成 | 在 `minicode/tools/__init__.py` 和 `py-src/minicode/tools/__init__.py` 加入 `_CORE_TOOLS` |
| 验证工具执行 | 已完成 | 通过 `ToolRegistry.execute("file_line_count", {"path": "README.md"}, ToolContext(...))` 验证返回 `ToolResult(ok=True, output=...)` |

第 3 阶段结论：

```text
一个 Tool 的生命周期是：
定义 ToolDefinition
-> 注册到 _CORE_TOOLS
-> ToolRegistry 建立 name 到 ToolDefinition 的索引
-> 模型返回 toolName + input
-> ToolRegistry.execute() 查找工具
-> validator 校验模型传入的不可信 JSON
-> run(parsed, ToolContext) 执行真实逻辑
-> 返回 ToolResult
-> agent_loop 把结果回填到 messages
```

第 3 阶段面试表达：

```text
MiniCode 的 Tool Calling 不是让模型直接运行 Python 函数，而是通过 ToolDefinition 暴露结构化工具能力。模型只负责选择工具和生成 JSON 参数，本地 runtime 负责工具查找、参数校验、权限上下文和真实执行。所有工具统一返回 ToolResult，agent_loop 再把工具调用记录和结果写回 messages，供下一轮模型继续推理。
```

第 3 阶段边界：

```text
本阶段只关注工具在本地 runtime 中如何定义、注册、查找、校验、执行和返回结果。
工具 schema 如何发给不同 LLM Provider，放到第 4 阶段模型适配层统一学习。
```

第 4 阶段预告：

```text
ToolDefinition
-> Anthropic adapter: name / description / input_schema
-> OpenAI adapter: type=function / function.name / function.description / function.parameters
-> provider 返回 tool_use/tool_calls
-> adapter 转回内部 AgentStep(calls=[...])
```

第 4 阶段拆解进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| Adapter 选择与 Provider 路由 | 已完成 | `model_registry.py` 根据模型名、环境变量和 runtime 配置选择 Anthropic/OpenAI/OpenRouter/Custom/Mock adapter |
| 工具 schema 序列化 | 已完成 | Anthropic 使用 `{name, description, input_schema}`；OpenAI 使用 `{type: "function", function: {name, description, parameters}}` |
| 消息格式转换 | 已完成 | MiniCode 内部 `ChatMessage` 会被 adapter 翻译成 provider messages，避免 `agent_loop.py` 绑定某个 API 协议 |
| 响应解析成 `AgentStep` | 已完成 | Anthropic 的 `tool_use` 和 OpenAI 的 `tool_calls` 都会被转成 `AgentStep(type="tool_calls")`；普通文本转成 `AgentStep(type="assistant")` |

第 4 阶段结论：

```text
ModelAdapter 是 MiniCode 和外部 LLM API 之间的协议翻译层。
请求方向：
MiniCode ChatMessage + ToolDefinition
-> provider messages + tools schema

响应方向：
provider text / tool_use / tool_calls
-> AgentStep(type="assistant" | "tool_calls")

因此 agent_loop.py 只处理统一的 AgentStep，不需要理解 Anthropic、OpenAI、OpenRouter 或 custom endpoint 的原始协议。
```

第 4 阶段面试表达：

```text
MiniCode 通过 model_registry.py 选择具体 ModelAdapter。Adapter 在请求方向把内部 messages 和 ToolDefinition 翻译成 provider API 格式，在响应方向把 provider 返回的文本和工具调用解析成统一 AgentStep。这样 agent_loop.py 只需要判断 AgentStep.type 是 assistant 还是 tool_calls，从而和具体 LLM 协议解耦。
```

学习问答记录：

```text
阶段性问答、你的回答、标准答案和你的关键追问已整理到 study_qa.md。
```

已经掌握的启动链路：

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

注意：

- 组件初始化主要发生在程序启动阶段，不是在用户输入之后才初始化。
- `messages` 是 Agent Loop 的上下文状态载体，不只是普通聊天记录。
- Prompt Cache 友好设计发生在 prompt 构建阶段，通过稳定静态前缀和动态后缀分离实现，不是最后一步单独执行。

## 2. 第 1 阶段：项目总览

目标：知道这个项目到底是什么，而不是直接陷进细节。

必读文件：

- `README.zh-CN.md`
- `docs/ARCHITECTURE_DIAGRAM.md`
- `docs/CODE_WIKI.md`
- `pyproject.toml`
- `py-src/minicode/main.py`
- `py-src/minicode/headless.py`

你需要回答：

1. MiniCode 和普通聊天机器人有什么区别？
2. 为什么它叫 AI Coding Agent，而不是代码问答助手？
3. 用户输入之后，最先进入哪个模块？
4. 交互式 TUI、headless、gateway 几种入口有什么区别？

核心理解：

```text
MiniCode = LLM + Agent Loop + Tool Registry + Permission + Memory + Context Governance + TUI
```

阶段产出：

- 画一张自己的系统总览图。
- 用 3 句话讲清楚项目定位。
- 能指出 `main.py`、`agent_loop.py`、`tooling.py`、`model_registry.py` 分别负责什么。

验收标准：

你能不看代码讲出下面这条链路：

```text
用户输入
-> main.py / TUI
-> messages
-> run_agent_turn()
-> model.next()
-> tool calls
-> ToolRegistry.execute()
-> tool_result 回填
-> model 继续推理
-> final answer
```

## 3. 第 2 阶段：Query Loop + Tool Use 主闭环

目标：彻底看懂 `agent_loop.py`。

必读文件：

- `py-src/minicode/agent_loop.py`
- `py-src/minicode/types.py`
- `py-src/minicode/main.py`
- `py-src/minicode/tui/input_handler.py`

重点函数：

- `run_agent_turn()`
- `_model_next()`
- `_execute_single_tool()`
- `_should_treat_assistant_as_progress()`
- `_is_recoverable_thinking_stop()`

需要理解的主流程：

```text
1. 拿到当前 messages
2. 初始化控制器、上下文、任务对象
3. 调用 model.next(messages)
4. 判断模型返回：
   - assistant final content
   - assistant progress
   - tool calls
5. 如果有 tool calls，执行工具
6. 把 assistant_tool_call 和 tool_result 写回 messages
7. 继续下一轮 model.next()
8. 直到模型返回最终答案或达到 max_steps
```

重点问题：

1. `agent_loop.py` 为什么不是简单调用一次模型？
2. 工具调用结果为什么必须回填到 messages？
3. `assistant_progress` 和 `assistant` 的区别是什么？
4. 空回复、max_tokens、pause_turn 为什么需要 nudge？
5. `max_steps` 解决什么风险？

阶段实验：

- 用 mock model 跑一次 headless。
- 在 `run_agent_turn()` 里加临时日志，观察 step 数、tool call、tool result。
- 找一个工具调用失败场景，观察 agent loop 如何把错误反馈给模型。

阶段产出：

- 写一段 300 字解释：Query Loop + Tool Use 是如何构成任务执行闭环的。
- 画一张 `run_agent_turn()` 时序图。

## 4. 第 3 阶段：Tool Calling 系统

目标：看懂模型如何“使用工具”，以及工具如何被系统安全执行。

必读文件：

- `py-src/minicode/tooling.py`
- `py-src/minicode/tools/__init__.py`
- `py-src/minicode/tools/read_file.py`
- `py-src/minicode/tools/write_file.py`
- `py-src/minicode/tools/patch_file.py`
- `py-src/minicode/tools/run_command.py`
- `py-src/minicode/tools/grep_files.py`

核心概念：

| 概念 | 作用 |
| --- | --- |
| `ToolDefinition` | 描述一个工具的名称、描述、参数 schema、校验和执行函数 |
| `ToolRegistry` | 工具注册表，负责查找和执行工具 |
| `ToolContext` | 工具执行上下文，包含 cwd、权限、runtime |
| `ToolResult` | 工具执行结果，包含成功/失败、输出、是否等待用户 |
| `input_schema` | 暴露给模型的工具参数结构 |

重点理解：

模型并不直接调用 Python 函数。模型看到的是工具描述和 JSON schema，然后返回类似这样的结构：

```json
{
  "toolName": "read_file",
  "input": {
    "path": "py-src/minicode/agent_loop.py"
  }
}
```

然后 `agent_loop.py` 通过 `ToolRegistry.execute()` 执行真实函数。

阶段实验：

1. 读懂一个只读工具：`read_file.py`。
2. 读懂一个写入工具：`patch_file.py`。
3. 读懂一个高风险工具：`run_command.py`。
4. 新增一个简单工具，例如 `line_count`，统计文件行数。

阶段产出：

- 能讲清 Tool 的生命周期：

```text
定义 -> 注册 -> 暴露给模型 -> 模型选择 -> 参数校验 -> 权限检查 -> 执行 -> 返回 ToolResult -> 回填上下文
```

## 5. 第 4 阶段：模型适配层

目标：看懂系统如何兼容不同 LLM Provider。

必读文件：

- `py-src/minicode/model_registry.py`
- `py-src/minicode/anthropic_adapter.py`
- `py-src/minicode/openai_adapter.py`
- `py-src/minicode/mock_model.py`
- `py-src/minicode/config.py`

重点理解：

不同模型 API 协议不同：

- Anthropic 使用 Messages API 和 tool_use block。
- OpenAI 使用 Chat Completions 和 tool_calls/function calling。
- OpenRouter 和 custom endpoint 通常走 OpenAI-compatible 协议。
- MockModel 用于测试，不依赖真实 API。

系统通过 Adapter 把这些差异统一成内部的 `AgentStep`。

你需要回答：

1. 为什么需要 `model_registry.py`？
2. 为什么不能在 `agent_loop.py` 里直接写 Anthropic/OpenAI 的请求逻辑？
3. Anthropic tool use 和 OpenAI tool calls 如何映射到统一结构？
4. mock model 对测试有什么价值？

阶段产出：

- 画一张模型适配图。
- 能用一句话解释：

```text
Model Adapter 的作用是屏蔽不同 LLM API 的协议差异，让 agent_loop 只关心模型下一步是回答还是调用工具。
```

## 6. 第 5 阶段：Skill 能力体系

目标：理解简历里的“Skill 分层路由系统”。

必读文件：

- `py-src/minicode/skills.py`
- `py-src/minicode/tools/load_skill.py`
- `py-src/minicode/intent_parser.py`
- `py-src/minicode/capability_registry.py`
- `py-src/minicode/agent_router.py`

核心区分：

| 层级 | 含义 | 例子 |
| --- | --- | --- |
| 原子 Tool | 可执行动作 | read_file、grep_files、patch_file、run_command |
| 高层 Skill | 任务方法论 | TDD、系统调试、代码审查、方案设计 |
| Capability | 可被描述、检索、调度的能力元数据 | 文件读写、搜索、执行、代码理解 |
| Intent | 用户任务意图 | debug、refactor、explain、implement |

重点理解：

Tool 解决“能不能做”，Skill 解决“怎么做更稳定”。

当前拆解进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| Skill 发现机制 | 已完成 | `skills.py` 扫描项目级/用户级 `.mini-code/skills` 与兼容 `.claude/skills` 目录，只收集 `SKILL.md` 的摘要信息 |
| Skill 按需加载 | 已完成 | `load_skill` 作为普通 Tool 暴露给模型，模型需要完整方法论时再读取对应 `SKILL.md` |
| Intent / Capability 关系 | 已完成 | `intent_parser.py` 解析用户意图，`capability_registry.py` 把工具抽象成可描述、可检索的能力 |
| Skill 路由实验 | 已完成 | 已新增 `SkillRouter`，在每轮用户输入后基于 intent/capability 对 Skill 做 Top-K 精排并注入 prompt |

Skill 路由要解决的问题：

1. Skill 越来越多，全部塞进 prompt 会浪费 token。
2. 功能重叠会导致模型选择混乱。
3. 只靠关键词召回会有噪声。
4. 需要结合任务意图、标签、适用边界、示例做召回和精排。

建议你按这个二阶段路由模型理解：

```text
第一阶段：粗召回
输入任务 -> intent parser -> 根据关键词、标签、领域召回候选 Skill

第二阶段：精排
候选 Skill -> 根据适用边界、示例匹配、历史效果、token 成本排序

最后：只把少量最相关 Skill 注入 prompt 或通过 load_skill 按需加载
```

阶段实验：

- 新建一个 Skill 文档，例如 `debug-python-test-failure`。
- 给它写清楚用途、触发条件、步骤、反例。
- 修改或模拟路由逻辑，让某个 debug 任务能命中它。

阶段产出：

- 能解释 Skill 和 Tool 的区别。
- 能解释为什么 Skill 需要检索，而不是全部注入 prompt。
- 能说明 Skill 自进化增长后会出现什么问题，以及如何治理。

## 7. 第 6 阶段：Memory System 与自进化记忆沉淀

目标：理解跨会话复用、错误修复加速、用户画像和经验沉淀。

必读文件：

- `py-src/minicode/memory.py`
- `py-src/minicode/memory_injector.py`
- `py-src/minicode/memory_reranker.py`
- `py-src/minicode/user_profile.py`
- `py-src/minicode/agent_reflection.py`
- `py-src/minicode/memory_pipeline.py`

记忆类型：

| 类型 | 内容 |
| --- | --- |
| 用户画像 | 用户偏好、回答风格、常用技术栈 |
| 项目记忆 | 项目架构、编码规范、目录约定 |
| 程序性经验 | 解决某类问题的步骤 |
| 情景记忆 | 某次 bug、某次决策、某次失败原因 |
| 错误修复经验 | 曾经踩过的坑和有效修复方式 |

推荐理解闭环：

```text
执行任务
-> 记录过程和结果
-> 反思哪些经验可复用
-> 提炼成结构化记忆
-> 分类存储
-> 更新索引
-> 后续任务按需检索
-> 注入 prompt 辅助推理
```

重点问题：

1. 什么内容值得进入长期记忆？
2. 什么内容不应该进入长期记忆？
3. 如何避免错误记忆污染？
4. 用户隐私和 API key 等敏感信息如何处理？
5. 记忆检索如何平衡相关性和 token 成本？

阶段实验：

- 手动写入一条项目记忆。
- 用不同 query 检索它。
- 观察记忆如何被注入 system prompt。
- 设计一条“不应该存储”的记忆，并说明原因。

当前拆解进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| 三层存储路径 | 已完成 | `MemoryPaths.for_workspace()` 将 USER 放在 `~/.mini-code/memory/`，PROJECT 放在 `.mini-code-memory/`，LOCAL 放在 `.mini-code-memory-local/` |
| 显式记忆写入 | 已完成 | `main.py` 在进入 LLM 前先调用 `handle_user_memory_input()`；`# ...` 和 `/memory add ...` 会直接写入记忆并跳过本轮模型推理 |
| 记忆检索排序 | 已完成 | `MemoryManager.search()` 使用 BM25、子字符串、tag/category、usage_count、recency 和 domain 等信号做相关性排序 |
| 普通 prompt 注入 | 已完成 | 用户真实输入后，`main.py` 用 `get_relevant_context(query=user_input)` 检索相关记忆，并通过 `prompt.py` 注入 `Project Memory & Context` |
| 高级注入控制 | 已完成 | `MemoryInjector` 根据 context usage、retrieval quality、recent failure、task repetition 决定注入模式、数量和阈值；当前主入口是普通注入，agent_loop 内部在上下文管理链路开启时条件性使用高级注入 |
| LLM 二次重排 | 已完成 | `MemoryReranker` 可在 BM25 top candidates 后用轻量 LLM 选择真正相关记忆、过滤跨领域噪音、识别冲突并生成摘要 |
| 自进化写入链路 | 已完成 | `agent_loop -> orch.reflect_on_task() -> MemoryPipeline.write() -> ReflectionEngine.reflect() -> MemoryManager.add_entry(PROJECT)`，把任务执行经验沉淀为项目记忆 |
| 后台记忆治理 | 已完成 | `MemoryCuratorAgent` 负责去重归档、失效校验、相关记忆合成 insight、tier 晋升/降级和 related_to 链接 |

阶段产出：

- 写一段“自进化记忆沉淀机制”的面试表达。
- 画出执行、反思、提炼、存储、检索、复用闭环。

## 8. 第 7 阶段：分层上下文压缩与 Prompt Cache

目标：理解长任务下上下文为什么会失控，以及系统如何治理。

必读文件：

- `py-src/minicode/context_manager.py`
- `py-src/minicode/context_compactor.py`
- `py-src/minicode/context_cybernetics.py`
- `py-src/minicode/prompt.py`
- `py-src/minicode/prompt_pipeline.py`
- `py-src/minicode/working_memory.py`

上下文可以分层理解：

| 层级 | 例子 | 处理策略 |
| --- | --- | --- |
| 静态系统指令 | 角色、规则、工具使用协议 | 保持稳定，利于 Prompt Cache |
| 项目上下文 | README、架构、规范 | 摘要后注入 |
| 会话消息 | 用户要求、模型推理、阶段性结论 | 保留关键决策 |
| 工具结果 | 文件内容、grep 输出、测试日志 | 大结果外置或摘要 |
| 工作记忆 | 当前任务状态、todo、已知事实 | 结构化维护 |
| 长期记忆 | 项目经验、用户偏好 | 按需检索注入 |

核心机制：

```text
大工具结果外置化
-> 用稳定占位符替换
-> 生成摘要预览
-> 需要时再检索原文
-> 接近上下文上限时触发兜底压缩
```

Prompt Cache 重点：

- 静态 prompt 前缀越稳定，缓存收益越高。
- 动态信息放在后面，减少前缀抖动。
- 不要每轮都把无关工具结果塞进 prompt。

阶段实验：

- 找一次 `read_file` 大文件输出，思考它是否应该完整进入上下文。
- 观察 `context_compactor` 如何做摘要、去重或压缩。
- 设计一个占位符格式，例如：

```text
[TOOL_RESULT_REF id=abc path=tests/output.log summary="pytest failed: 3 errors"]
```

阶段产出：

- 能解释“摘要预览 -> 占位替换 -> 按需检索 -> 超限兜底”的闭环。
- 能说明分层上下文压缩如何同时提升稳定性、降低 token 成本、提高 Prompt Cache 收益。

当前拆解进度：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| 基础上下文统计 | 已完成 | `ContextManager` 负责估算消息 token、区分 system/conversation tokens，并在基础压缩中保护 system 消息 |
| 大工具结果外置化 | 已完成 | `ToolResultBudgetManager` 将超大 `tool_result` 写入 `.mini-code-tool-results/`，并用 preview stub 替换上下文中的长内容 |
| 重复文件读取去重 | 已完成 | `ReadDedupManager` 使用 `file_path + content_hash` 判断同一文件同一内容是否重复读取，重复时返回短占位提示 |
| 时间微压缩 | 已完成 | `MicrocompactEngine` 定期清理旧工具结果，只保留最近工具结果，旧内容替换为稳定 marker |
| Session Memory Compact | 已完成 | `SessionMemoryCompactEngine` 使用 `MemoryManager.get_relevant_context()` 作为摘要基底，并保留最近 tail，避免切断工具调用/结果对 |
| Full Compact | 已完成 | 当前实现不是 LLM 总结，而是规则型结构化摘要，抽取 topics、tools、files、errors 后保留最近消息 |
| Reactive Compact | 已完成 | 模型 API 报 `prompt too long` 等错误后，走强制 full compact 或激进截断兜底 |
| ContextCybernetics 控制层 | 已完成 | `ContextCyberneticsOrchestrator` 在 `ContextCompactor` 外层做感知、预测、PID 强度计算、阈值调整、策略选择和反馈记录 |
| 学习问答沉淀 | 已完成 | 已将本阶段普通查询、高级链路、自进化记忆关联、ContextCompactor 和 ContextCybernetics 问答记录到 `study_qa.md` |

第 7 阶段面试表达：

```text
MiniCode 的上下文治理不是简单裁剪历史，而是分层压缩。请求前先对大工具结果做落盘外置和 preview 占位，对重复 read_file 做 path + content hash 去重，对旧工具结果做 microcompact。上下文达到高水位后，系统优先使用已有 Memory 做 Session Memory Compact，失败时退回规则型 Full Compact；模型 API 已经报超限时，再通过 Reactive Compact 强制压缩或激进截断。外层还有 ContextCyberneticsOrchestrator，通过 usage_ratio、growth_rate、预测 urgency、错误率、延迟和任务意图动态决定压缩时机、压缩强度和具体策略。
```

## 9. 第 8 阶段：中心化多 Agent 协作

目标：理解为什么用主 Agent 控制子 Agent，而不是让多个 Agent 自由通信。

必读文件：

- `py-src/minicode/tools/task.py`
- `py-src/minicode/multi_agent_agent.py`
- `py-src/minicode/multi_agent/orchestrator.py`
- `py-src/minicode/multi_agent/types.py`
- `py-src/minicode/multi_agent/shared_memory.py`

推荐理解模型：

```text
主 Agent:
  负责理解用户目标、拆解任务、审批工具、质量控制、最终回答

子 Agent:
  作为 ToolCall 被调用，只执行受限子任务
  返回摘要结果，不接管主流程
```

这样做的好处：

1. 控制权不转移，主 Agent 始终负责最终质量。
2. 不需要复杂的 Agent 间共识和状态同步。
3. 子 Agent 结果最小化返回，降低上下文污染。
4. 可以给子 Agent 限制路径、工具、权限。
5. 更适合强约束 coding 场景。

需要重点理解：

- 子 Agent 什么时候值得用？
- 子 Agent 返回什么内容最合适？
- 主 Agent 如何验收子 Agent 的结果？
- Fork、Worktree、Agent Team 分别适合什么场景？

阶段实验：

- 设计一个任务：主 Agent 负责规划，子 Agent 负责搜索某个模块。
- 要求子 Agent 只返回文件列表、关键发现、风险点，不返回全部日志。
- 观察这样是否减少主上下文压力。

阶段产出：

- 能解释“中心化多 Agent 协作”和“去中心化 Agent 群聊”的区别。
- 能说明为什么这个项目选择前者。

当前状态：

| 子任务 | 状态 | 要点 |
| --- | --- | --- |
| 入口文件定位 | 已完成 | 当前有效包主线是 `minicode/tools/task.py`，`task_tool` 注册在默认工具列表中；`py-src/minicode/multi_agent/` 是更泛化的实验性多 Agent 编排框架 |
| 主 Agent / 子 Agent 边界 | 已完成 | 主 Agent 通过 ToolCall 调用 `task`，子 Agent 独立运行 `run_agent_turn()`，最终以 `ToolResult` 返回，控制权不转移 |
| 结果最小化传递 | 已完成 | 子 Agent 使用独立 `sub_messages` 消耗自己的上下文做探索，主 Agent 只接收 `header + final_message` 摘要结果，避免完整日志污染主上下文 |
| 权限和路径边界 | 已完成 | `explore/plan` 通过工具注册表过滤实现只读约束，`prompt=None` 防止子 Agent 自行扩大权限；文件工具通过 `resolve_tool_path()` 做路径边界检查，写文件走 diff 审批，命令执行走风险分类和 `ensure_command()` |
| Fork / Worktree / Agent Team | 已完成 | 当前项目提供 `WorktreeIsolator`、`safe_execution.execute_safely()`、`TeamRegistry` 和多模式编排框架，但它们不是 `task` 子 Agent 默认路径；简历应表述为可接入增强能力或扩展雏形 |

## 10. 第 9 阶段：权限与安全审查

目标：理解真实开发环境下 Coding Agent 为什么必须可控。

必读文件：

- `py-src/minicode/permissions.py`
- `py-src/minicode/auto_mode.py`
- `py-src/minicode/tools/run_command.py`
- `py-src/minicode/tools/write_file.py`
- `py-src/minicode/tools/patch_file.py`

安全链路：

```text
工具自检
-> 参数校验
-> 路径边界检查
-> 危险命令分类
-> 权限策略匹配
-> 必要时人工确认
-> 执行后结果审查
```

重点风险：

- 删除文件：`rm -rf`
- 覆盖代码：`git reset --hard`、`git checkout --`
- 任意代码执行：`python`、`bash`、`node`
- 权限放大：`chmod 777`
- 外部网络访问和数据泄露
- prompt 注入让模型忽略安全规则

你需要能回答：

1. 为什么 `python script.py` 也可能是高风险命令？
2. 读文件和写文件的权限边界有什么区别？
3. 自动模式如何避免失控？
4. prompt 注入攻击可能怎样发生？
5. 人工确认放在哪些场景最合理？

阶段产出：

- 总结一条多层安全审查链路。
- 用面试语言说明“规则过滤 + 工具自检 + AI 风险分类 + 人工确认”的组合价值。

当前拆解进度：

| 子任务 | 状态 | 关键文件/结论 |
|---|---|---|
| 路径边界检查 | 已完成 | `workspace.resolve_tool_path()` + `PermissionManager.ensure_path_access()`：工具真正读写前先解析并校验目标路径，默认限制在 workspace 内。 |
| 写入审批链路 | 已完成 | `write_file` 先做路径检查，再进入 `apply_reviewed_file_change()` 生成 diff，并通过 `ensure_edit()` 审批。 |
| 命令风险分类 | 已完成 | `run_command` 会识别 shell snippet、危险命令、未知命令，并在需要时调用 `ensure_command()`。 |
| 权限模式 | 已完成 | `DEFAULT`、`AUTO`、`PLAN`、`BYPASS` 分别对应常规审批、自动风险判断、只读计划模式、跳过权限检查。 |
| Prompt 注入防御 | 已完成 | 当前实现是检测 + 日志/系统警告，不直接硬阻断；真正的安全边界仍落在工具注册、参数校验、路径检查和权限审批上。 |

## 11. 补充专题：控制论模块与稳定性治理（可选）

目标：理解简历里“提升复杂任务下执行准确率、上下文稳定性与推理效率”的技术支撑。

必读文件：

- `py-src/minicode/feedback_controller.py`
- `py-src/minicode/feedforward_controller.py`
- `py-src/minicode/stability_monitor.py`
- `py-src/minicode/predictive_controller.py`
- `py-src/minicode/self_healing_engine.py`
- `py-src/minicode/cost_control.py`

核心理解：

Agent 运行过程中会出现这些信号：

- 上下文使用率升高
- 工具错误率升高
- 模型空回复或中断
- 成本过高
- 任务进度停滞
- 重复读文件或重复执行无效命令

控制器做的事：

```text
观测信号
-> 判断系统状态
-> 输出控制动作
-> 调整上下文、预算、并发、恢复策略
-> 继续执行
```

阶段产出：

- 能说明控制论模块不是“玄学”，而是对 Agent runtime 的状态反馈。
- 能举例：上下文快满时触发压缩，工具错误率升高时降低并发或引导模型换策略。

## 12. 第 10 阶段：简历答辩与最终实战

完成下面 5 个小任务，说明你已经真正看懂项目：

1. 跑通一次 mock 模式的 headless 任务。
2. 新增一个简单 Tool，并注册到 ToolRegistry。
3. 写一个简单 Skill，并说明它的触发边界。
4. 手动添加一条项目记忆，并观察它如何影响 prompt。
5. 设计一个上下文压缩场景，说明哪些内容保留、哪些内容摘要、哪些内容外置。

完成后整理成一份 1 页项目讲解：

```text
项目背景
核心架构
主执行链路
五个技术亮点
遇到的难点
技术取舍
可改进方向
```

## 13. 简历表达建议

建议把简历里的表述统一为下面这些术语：

- `AI Coding Agent`
- `Query Loop + Tool Use`
- `Tool Calling`
- `Skill Routing`
- `Memory System`
- `Prompt Cache`
- `Context Compaction`
- `Multi-Agent Collaboration`
- `Permission & Safety Review`

建议简历版本：

```text
MiniCode | AI 应用开发 | 2025.12 - 2026.04

项目简介：
参考 Claude Code 架构设计并实现 AI Coding Agent，基于 Query Loop + Tool Use 构建任务执行闭环，重点设计 Skill 路由、自进化记忆沉淀、分层上下文压缩、多 Agent 协作与权限安全审查等机制，提升复杂任务下的执行准确率、上下文稳定性与推理效率。

技术栈：
Agent、Tool Calling、Memory System、Prompt Cache、Claude Code、Hermes、Python

技术亮点：
- Skill 能力体系：设计 Skill 分层路由系统，将原子 Tool、高层 Skill 与 Skill 目录分层组织，结合任务意图识别、元信息标签、适用边界与示例进行二阶段召回与精排，降低 Skill 自进化增长下的检索噪声、功能重叠、召回空间过大与 Token 成本。
- 自进化记忆沉淀：将执行过程中的程序性经验、情景记忆、用户画像提炼为可复用记忆资产，构建“执行-反思-提炼-分类存储-索引更新-按需复用”闭环，实现跨会话复用、错误修复加速与 Skill 能力生长。
- 分层上下文压缩：将大工具结果外置化、缓存友好型占位压缩与结构化笔记摘要结合，构建“摘要预览-占位替换-按需检索-超限兜底”的上下文治理闭环，提升长会话稳定性、Prompt Cache 收益并降低 Token 成本。
- 中心化多 Agent 协作：以主 Agent 统一规划、审批与质量控制，子 Agent 以 ToolCall 方式受控执行，通过不移交控制权、最小化结果传递、工具权限约束与路径边界限制保障安全性，提升并行执行效率与任务稳定性。
- 权限与安全审查：构建规则过滤、工具自检、AI 风险分类与人工确认的多层审查链路，提升 Agent 在真实开发环境下的可控性与安全性。
```

## 14. 面试官视角追问

### 基础架构

1. 你能画出用户输入到最终回答的完整链路吗？
2. `agent_loop.py` 在系统里承担什么职责？
3. 为什么这个系统需要 Query Loop，而不是一次模型调用？
4. Tool Result 为什么要写回 messages？
5. `max_steps` 的作用是什么？设置太大或太小分别有什么问题？

### Tool Calling

6. Tool 的 schema 是给谁看的？
7. Tool 的参数校验在哪里做？
8. 如果模型传了错误参数，系统如何处理？
9. 读工具、写工具、命令执行工具的风险等级有什么区别？
10. 新增一个 Tool 需要改哪些地方？

### Skill Routing

11. Tool 和 Skill 的区别是什么？
12. 为什么 Skill 不应该全部塞进 prompt？
13. Skill 数量增长后会带来哪些问题？
14. 你说的二阶段召回与精排具体怎么做？
15. 如何评估一个 Skill 是否应该被召回？

### Memory System

16. 什么内容适合沉淀为长期记忆？
17. 什么内容不应该进入记忆系统？
18. 自动记忆写入如何避免错误信息污染？
19. 用户画像和项目记忆应该如何隔离？
20. 记忆检索如何控制 token 成本？

### Context + Prompt Cache

21. 长会话中上下文为什么会失控？
22. 大工具结果为什么不能直接完整塞进 prompt？
23. 什么是缓存友好的 prompt 结构？
24. 如何设计占位符，让模型知道有外置结果但不丢关键信息？
25. 上下文压缩会不会损失推理质量？如何降低损失？

### Multi-Agent

26. 为什么选择中心化多 Agent，而不是去中心化 Agent 通信？
27. 子 Agent 以 ToolCall 方式执行有什么好处？
28. 子 Agent 应该返回完整日志还是摘要？为什么？
29. 主 Agent 如何验收子 Agent 的结果？
30. 多 Agent 并行会带来哪些安全和状态一致性问题？

### 权限与安全

31. 如何识别危险命令？
32. 为什么 `python`、`bash`、`node` 这类命令也需要谨慎？
33. prompt 注入攻击在 Coding Agent 里可能如何发生？
34. 人工确认应该放在哪些操作前？
35. 自动模式下如何避免 Agent 失控？

### 项目取舍

36. 这个项目相比 Claude Code 的核心差异是什么？
37. 这个项目相比普通 RAG Agent 的差异是什么？
38. 你认为这个项目最大的工程难点是什么？
39. 如果让你重构这个项目，你会优先改哪里？
40. 如何量化“执行准确率、上下文稳定性、推理效率”的提升？

## 15. 后续辅导方式

后续可以按阶段推进。每次只学一个模块：

```text
第 N 次学习：
1. 我先带你读入口链路
2. 你复述流程
3. 我指出理解偏差
4. 我给你一个小实验
5. 最后用面试官问题考你
```

建议第一轮从 `py-src/minicode/agent_loop.py` 开始，因为它是 Query Loop + Tool Use 的中心。
