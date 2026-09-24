# CodeLoop 面试资料

## Knowledge

- [主循环](../../minicode/agent_loop.py)
  `run_agent_turn`；重点读模型调用、工具结果回填、进度与最终回答的区分。
- [协议类型](../../minicode/types.py)
  `ChatMessage`、`ToolCall`、`AgentStep`；用于解释模型输出与本地执行之间的接口。
- [工具注册表](../../minicode/tooling.py)
  `ToolRegistry.execute`；用于追踪工具查找、参数验证、执行与错误转换。
- [贡献边界](../../CONTRIBUTIONS.md)
  核对继承基线；其中架构描述需结合当前代码，不能默认全部同步。
- [task 工具](../../minicode/tools/task.py)
  当前写任务委派入口。`_run` 中子进程分支排除了 Scheduler Executor 和 workflow phase；不能声称所有写子 Agent 都能硬终止。
- [Claude Code 官方架构说明](https://code.claude.com/docs/en/how-claude-code-works)
  解释收集上下文、执行、验证的 agentic loop；用于对齐公开设计，不能据此推断未公开内部实现。
- [Anthropic 官方 Tool Use 文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
  客户端工具调用协议的第一手资料。CodeLoop 自己的消息结构以本地代码为准。

## Wisdom
通过本任务中的口述回答与连续追问练习；尚未收集外部面试反馈。

## Gaps
- 用户个人负责范围尚待其口述确认；仓库贡献清单不能证明每行代码由谁手写。
- 冻结质量门与当前工作区可能存在版本差异，讨论数字时需要核对工件版本。
