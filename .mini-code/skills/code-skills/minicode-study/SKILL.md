---
name: "minicode-study"
description: "系统化学习 MiniCode Coding Agent 项目的 Skill。覆盖架构总览、Agent Loop、记忆系统、控制论控制器、工具系统等核心模块。"
directory: "code-skills"
domains: ["code", "analysis"]
scopes: ["readonly", "external"]
purposes: ["codebase explanation", "architecture walkthrough"]
supported_operations: ["explain.read"]
activation_terms: ["minicode", "memory.py", "agent_loop.py", "main.py", "cybernetic", "记忆系统", "控制论"]
tools:
  - "read_file"
  - "grep_files"
  - "file_tree"
  - "find_symbols"
  - "get_ast_info"
  - "find_references"
  - "task"
  - "code_review"
  - "web_search"
keywords:
  - "minicode"
  - "study"
  - "learning"
  - "codebase-walkthrough"
  - "agent-loop"
  - "architecture"
examples:
  - "加载 Skill 后，问：请带我走一遍 main.py 到 agent_loop.py 的启动流程"
  - "解释 memory.py 的三层记忆检索管线是如何工作的"
---

# MiniCode Study — 系统化学习 Skill

## 简介

MiniCode 是一个基于工程控制论的终端 AI Coding Agent。本 Skill 提供结构化学习路径。

## 学习路径

### 路径 1：入门 — 从启动到第一次对话

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | main.py | 程序如何启动？如何组装 runtime？ |
| 2 | cli_commands.py | CLI 参数如何解析？ |
| 3 | headless.py | 无头模式如何进入同一链路？ |
| 4 | session.py | 会话如何管理？ |
| 5 | agent_loop.py | run_agent_turn 如何驱动循环？ |
| 6 | prompt.py | System prompt 如何构建？ |
| 7 | permissions.py | 权限系统如何检查？ |

### 路径 2：记忆系统（3 层检索管线）

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | memory.py | 存储层级：WORKING→SHORT→LONG→ARCHIVAL |
| 2 | memory_pipeline.py | 检索管线流程 |
| 3 | memory_reranker.py | LLM 重排序逻辑 |
| 4 | memory_injector.py | 记忆注入 system prompt |
| 5 | memory_curator_agent.py | 后台整理和链接 |
| 6 | timeline_memory.py | 时间线事件管理 |

### 路径 3：控制论系统（核心特色，15+ 控制器）

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | cybernetic_orchestrator.py | 控制器如何编排？ |
| 2 | context_cybernetics.py | PID 如何调节上下文窗口？ |
| 3 | feedback_controller.py | 负反馈如何纠正偏差？ |
| 4 | feedforward_controller.py | 前馈如何预测性调整？ |
| 5 | predictive_controller.py | 卡尔曼滤波器如何预测？ |
| 6 | adaptive_pid_tuner.py | PID 参数如何自适应？ |
| 7 | context_compactor.py | 4 阶段渐进压缩策略 |
| 8 | decision_audit.py | 决策审计记录 |

### 路径 4：工具系统

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | tooling.py | ToolRegistry 如何管理工具？ |
| 2 | tools/__init__.py | 工具如何批量注册？ |
| 3 | mcp.py | MCP 工具如何包装？ |
| 4 | safe_execution.py | 命令执行安全性 |

### 路径 5：TUI 终端界面

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | tty_app.py | TUI 入口和主循环 |
| 2 | tui/chrome.py | 界面布局 |
| 3 | tui/transcript.py | 对话渲染 |
| 4 | tui/input_handler.py | 工具调用前端展示 |
| 5 | tui/markdown.py | Markdown 终端渲染 |

### 路径 6：多智能体系统

| 步骤 | 文件 | 核心问题 |
|------|------|---------|
| 1 | multi_agent/orchestrator.py | 调度子 Agent |
| 2 | multi_agent/patterns.py | 协作模式 |
| 3 | multi_agent/role_analyzer.py | 角色分析 |

## Q&A 数据库

项目根目录下的 study_qa.md 记录了学习过程中的问答。用于复习和自我检测。

## 学习技巧

- find_symbols 快速了解文件定义了哪些类和函数
- get_ast_info 了解文件导入结构和统计信息
- grep_files 追踪函数调用链
- find_references 找函数的所有引用点
- task agent_type=explore 让子 Agent 快速探索子模块
