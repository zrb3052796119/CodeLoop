# CodeLoop

<p align="center">
  <strong>一个以证据闭环为核心的 Python 本地 Coding Agent Runtime。</strong>
</p>

<p align="center">
  把经过验证的失败恢复沉淀为跨会话经验；让长任务在多轮压缩后仍保留关键状态；
  用显式生命周期、共享预算和结构化结果约束子 Agent。
</p>

<p align="center">
  <a href="./README.md">English</a>
  ·
  <a href="./docs/PORTFOLIO_CASE_STUDY.md">3 分钟案例</a>
  ·
  <a href="./CONTRIBUTIONS.md">贡献边界</a>
</p>

<p align="center">
  <a href="https://github.com/zrb3052796119/CodeLoop/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/zrb3052796119/CodeLoop/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Package" src="https://img.shields.io/badge/package-minicode--py-555?style=flat-square">
</p>

CodeLoop 是 [MiniCode Python](https://github.com/QUSETIONS/MiniCode-Python)
的深度衍生版本，**不是从零实现的 Coding Agent**。项目保留了兼容的
`minicode` 导入路径与 `minicode-py` 命令，在此基础上重点重构和扩展了四条链路：

1. 一次已经验证的工具失败，能否安全地变成下一次新对话可复用的经验？
2. 多轮上下文压缩后，目标、已验证事实、被否方案、工具调用完整性和最新指令能否继续保真？
3. 主 Agent 能否把任务交给子 Agent，同时仍掌握生命周期、归因和共享预算？
4. Skill 路由能否从跨 Run 证据中变准，同时避免过期或不相关反馈劫持路由？

在已测试范围内，答案是“可以”；超出实验范围的部分则明确列在本文末尾。这个项目追求的是**可检查的运行证据、可复现的质量门和失败时的保守退化**，而不是宽泛的能力口号。

> **复用提示：**这个衍生仓库当前没有根目录 LICENSE，检查到的 Python
> 上游也没有公开许可证。代码可以在此查看和评审，但公开可见不等于获得重新分发
> 或商业使用授权。详见[上游与致谢](#上游与致谢)。

## 项目履历快照

| 项目 | 范围 |
| --- | --- |
| 角色 | 维护者与导入后的主要 Git 作者；部分提交带 AI co-author trailer，因此不宣称“完全独立完成”或个人占比。初始导入没有记录精确上游 revision，不能精确复原 upstream diff。 |
| 时间 | 2026-07-27 至今 |
| 主要负责 | 持久化经验的证据闭环、上下文保真修复、有边界子 Agent 生命周期/模型路由、Skill 反馈和评测发布纪律。 |
| 当前状态 | 可实际使用本地 CLI 的工程/研究原型；不宣称达到生产安全或通用 benchmark 领先。 |
| 最难设计决策 | 严格区分 Memory 的“检索候选”“实际渲染”“得到因果佐证”，避免一次成功错误奖励所有搜索候选。 |

## 30 秒看懂证据

| 证据 | 结果 | 能说明什么，不能说明什么 |
| --- | ---: | --- |
| [仓库回归测试](./.github/workflows/ci.yml) | **4,446 passed, 2 skipped** | 2026-08-24 在 Python 3.12 对 [Runtime/测试提交 `e1a4b17`](https://github.com/zrb3052796119/CodeLoop/commit/e1a4b17) 完成的本地发布验收；说明已实现行为受到回归保护，不代表通用 Agent 智能。推送后 CI 会在 3 OS × 2 Python 矩阵重跑。 |
| [内部 A 档评估](./docs/agent-quality-gates.md) | **Skill 60/60**、**压缩 12/12**、**记录任务 50/50** | 完全离线且 `remoteCallCount=0`。fixture/manifest 有哈希；与 `current` 不同，`a` 有意允许同 manifest 的新结果，不固定某一份 result 哈希。不是第三方认证。 |
| [路径恢复配对实验](./docs/2026-08-21--persistent-memory-large-study--r1--robustness-check.md) | **48 对 Memory / cold** | 工具调用 50 vs 240（**-79.2%**）；输入 token 652,911 vs 1,539,738（**-57.6%**）。只覆盖合成、只读的恢复任务。 |
| [非路径经验配对实验](./docs/2026-08-22--non-path-persistent-memory--r1--robustness-check.md) | **36 对 Memory / cold** | 平均工具 7.50 vs 11.50（**-34.8%**）；严格成功 32/36 vs 28/36。不同经验类别差异明显。 |
| [大文件修复回放](./docs/north-star-memory-compaction-repairs-2026-08-21.md) | **5/5 外部检查** | 单次随机回放中模型调用 25→5、输入 token 257,088→49,541；说明故障环消失，不能当作稳定因果效应。 |

如果只看一个材料，先看 [3 分钟完整案例](./docs/PORTFOLIO_CASE_STUDY.md)：
它串起了“错误工具调用 → 找到正确方法 → 验证 → 生成经验 → 新对话检索注入 →
减少重复探索”的完整证据链，然后再链接到更大规模的配对实验。公开的 entry-ID
join 是经过策展、带源哈希的 attestation；隐私 sidecar 未公开，因此它不是一份可由
第三方独立重放完整 provider trace 的证明。

## 相比导入基线，我改了什么

仓库最初导入的版本已经具备主 Agent Loop、模型适配器、本地工具、TUI、
`CyberneticOrchestrator`、上下文压缩、Memory、Skill 路由，以及同步的
task/子 Agent 工具。这些不能被描述成 CodeLoop 从零新增的模块。

| 方向 | 导入基线之后的 CodeLoop 工作 | 证据面 |
| --- | --- | --- |
| 持久化经验 | 在基线已有反思/审批之上，强化 corroborated/idempotent 反馈、隔离与投影卫生、通用操作恢复、canonical Hybrid 检索和验收归因。 | warm/cold 配对实验、V1–V5 验收合同、Memory 回归矩阵。 |
| 上下文保真 | summary-of-summary 摘要链；保存目标/显式约束/typed fact/失败码的压缩免疫账本；原子 provider 工具轮次、usage 校准、未变化状态重试身份；让强制路径遵守基线已有熔断器。 | 重复压缩门禁和大文件回放。 |
| 多 Agent Runtime | 只读 `spawn` / `poll` / `cancel`、结构化结果、`subagentId` 日志 join、共享 Turn 预算、deadline 与按角色模型路由。 | 生命周期、取消、结果协议、路由、日志与预算测试。 |
| Skill 反馈 | 将跨 Run 证据账本接回有界在线排序；证据绑定 Skill source/directory/content digest 和 intent/action context，并受样本量、置信度与最大调整幅度约束。 | 冻结的中英双语/对抗路由集和证据账本测试。 |
| 发布纪律 | 在基线已有 3 OS × 2 Python CI 上增加全局凭据边界、隐私安全失败投影、确定性质量档位、冻结 manifest、外部 oracle 与公开证据完整性检查。 | CI、本地全量验证和 clean-checkout 检查。 |

更细的继承/新增/实质重构清单与提交谱系见
[CONTRIBUTIONS.md](./CONTRIBUTIONS.md)。

## 系统如何协作

```mermaid
flowchart LR
    User["当前仓库中的用户任务"] --> Loop["Agent Loop"]
    Loop --> Tools["文件 · 搜索 · 编辑 · 命令"]
    Tools --> Obs["有边界的结构化观测"]
    Obs --> Loop

    Loop --> TaskAPI["Task API"]
    TaskAPI --> Children["explore / plan / general / workflow"]
    RoleRoute["按角色模型路由"] --> Children
    Children --> Result["结构化结果 + subagentId"]
    Result --> Loop

    Obs --> Compact["上下文压缩器"]
    Ledger["压缩免疫任务账本"] --> Compact
    Compact --> Loop

    Obs --> Reflect["反思 + 恢复策略合成"]
    Reflect --> Evidence["安全 · 验证 · 审批"]
    Evidence --> Store["项目 / 用户 Memory"]
    Store --> Retrieve["BM25 + 证据门控 Hybrid 检索"]
    Retrieve --> Loop

    SkillCatalog["Skill 元数据"] --> SkillRoute["意图 + 语义路由"]
    SkillEvidence["有边界的证据账本"] --> SkillRoute
    SkillRoute --> Loop
```

模型仍然负责选择下一步允许的工具。外围 Runtime 负责记录结构化观测、约束重试与委派，并且只把满足强证据条件的结果变成长期状态。

## 四项核心能力

### 1. 有真实证据链的持久化经验

Memory 写入不是“把模型总结存下来”。一条可持久化的恢复经验必须能从结构化事件中证明：某个动作失败，随后出现了与它对应的修正动作，并且修正结果满足恢复策略。只有强恢复信号能够自动批准；模糊 claim 保持 pending 或被拒绝。这里的 verified recovery 可能指 targeted corrected-tool evidence，并不总是独立测试命令，案例文档会明确区分。条目会做内容哈希绑定、安全清洗和审计，后续还可以根据负反馈降权、拒绝或隔离。

读取侧的 canonical retrieval 将词法证据与可选 Hybrid 通道组合。远程 Memory embedding 需要单独显式授权，因为已批准的经验可能离开本机。每次真实渲染都会记录精确 entry ID，因此后续成功或用户纠偏能够归因到“当时到底注入了哪条经验”。

继续阅读：[Hybrid Memory 检索](./docs/memory-hybrid-retrieval.md)、
[路径恢复大样本](./docs/2026-08-21--persistent-memory-large-study--r1--robustness-check.md)、
[非路径经验实验](./docs/2026-08-22--non-path-persistent-memory--r1--robustness-check.md)。

### 2. 不丢任务状态的上下文压缩

CodeLoop 使用一条 canonical 压缩路径。它不会拆开 provider 原生的工具调用/结果对；每一轮新摘要都会吸收上一轮摘要，并重新插入最新用户指令。父 Agent 持有的任务账本将有界目标、显式约束、typed verification fact 和失败工具错误码放在有损摘要周期之外；它不会语义推断完整计划或任意 open work。provider 返回真实 usage 时，本地 token 估计会据此校准。

压缩失败会按“策略 + 未变化的消息状态”去重，并受熔断器限制；内容发生实质变化后才允许重试，原地不变的状态不能无限振荡。

继续阅读：[上下文修复报告](./docs/north-star-memory-compaction-repairs-2026-08-21.md)、
[质量门合同](./docs/agent-quality-gates.md)。

### 3. 有边界的多 Agent 协作

目前有四种 task 角色：

| 角色 | 典型工作 | 生命周期 |
| --- | --- | --- |
| `explore` | 只读探索仓库 | 可通过 `spawn` / `poll` / `cancel` 异步运行。 |
| `plan` | 只读分析与实现计划 | 可使用同一套异步生命周期。 |
| `general` | 聚焦的实现或分析委派 | 同步执行，可以获得写工具。 |
| `workflow` | 带版本协议的 review/decision 工作流 | 同步执行；裁决格式错误时 fail-closed。 |

每个子 Agent 与父 Agent 共享同一份 Turn 预算。通用结果使用
`summary / files / risks / verification` 结构，完成事件携带稳定
`subagentId`，便于日志、结果和父任务三方 join。取消是协作式的：它能阻止排队中和后续工作，但不能强杀一个已经阻塞在 provider socket 内的 Python 线程。

可选的 OpenAI-compatible 独立路由可以让子 Agent 使用 `qwen3.6-flash`
等轻量模型，同时父 Agent 保留更强主模型。该名称是配置示例；仓库公开的 live
验收覆盖的是 `qwen3.7-plus` 与 `qwen3.7-max`，不是这个示例。详见
[子 Agent 模型路由](./docs/subagent-model-routing.md)。

### 4. 带有界反馈的 Skill 路由

Skill 发现综合显式名称、意图、元数据、中英别名和可选语义信号。用户明确点名的 Skill 必须在最终回答前加载。跨 Run 结果可以改变排序，但证据必须匹配当前 Skill source/directory/content digest 和 intent/action context，并通过样本量/置信门；调整幅度有上限且可审计。证据不能静默改写 Skill 正文，也不能自动晋升新版本。

继续阅读：[Skill 路由反馈](./docs/skill-routing-feedback.md)。

## 快速开始

### 1. 安装 CodeLoop

macOS / Linux：

```bash
git clone https://github.com/zrb3052796119/CodeLoop.git
cd CodeLoop
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Windows PowerShell：

```powershell
git clone https://github.com/zrb3052796119/CodeLoop.git
Set-Location CodeLoop
py -m venv .venv
& .\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
```

### 2. 只配置一次全局主模型

macOS / Linux：

```bash
mkdir -p ~/.mini-code
chmod 700 ~/.mini-code
cp .env.example ~/.mini-code/.env
chmod 600 ~/.mini-code/.env
```

Windows PowerShell：

```powershell
$configDir = Join-Path $HOME ".mini-code"
New-Item -ItemType Directory -Force $configDir
Copy-Item .env.example (Join-Path $configDir ".env")
```

编辑 `~/.mini-code/.env`，只启用一种主模型 provider 配置。仓库提供的示例覆盖 Anthropic、OpenAI、OpenRouter 和自定义 OpenAI-compatible 端点。不要提交真实密钥。

```bash
python -m minicode.main --validate-config
```

未修改的示例故意没有启用真实凭据，必须在编辑后才能通过。这个命令只检查本地结构与安全规则，不会调用 provider，也不能证明远程密钥真实有效。

进程环境变量优先级最高；旧的 `~/.mini-code/settings.json` 仍作为兼容回退。目标项目自己的 `.env` 不会参与主模型、embedding 或子 Agent 端点/凭据路由，因此打开一个不受信任的仓库不能重定向全局持有的密钥。可以在 CLI 中运行 `/config-paths` 查看真实配置来源。

### 3. 在你真正想修改的项目目录中启动

macOS / Linux：

```bash
cd /path/to/your/project
minicode-py
```

Windows PowerShell：

```powershell
Set-Location C:\path\to\your\project
minicode-py
```

**当前工作目录就是 CodeLoop 要读取和修改的目标项目。** 安装以后，不需要把 CodeLoop 源码复制进每个项目。也可以在虚拟环境中使用 `python -m minicode.main` 启动。

### 可选：把子 Agent 路由到 Qwen

在 `~/.mini-code/.env` 中替换已有的三行子 Agent 配置，不要在文件末尾重复追加；重复 key 会被拒绝：

```dotenv
MINI_CODE_SUBAGENT_API_KEY=replace-me
MINI_CODE_SUBAGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MINI_CODE_SUBAGENT_MODEL=qwen3.6-flash
```

这份凭据与父 Agent、embedding key 相互独立。key 为空时，子 Agent 为了兼容会继承父模型。`qwen3.6-flash` 是可替换的配置示例；当前公开 live 验收覆盖 `qwen3.7-plus` / `qwen3.7-max`。按角色覆盖模型的方法见
[子 Agent 模型路由](./docs/subagent-model-routing.md)。

### 可选：启用 Hybrid Memory 检索

Hybrid 默认关闭，并且要求通过 promotion evidence。local E5 路由只保证 embedding 在本机；如果启用 LLM verifier/challenger，query 和候选 Memory 文本/元数据仍可能发送给其 provider。Qwen embedding 路由还必须额外设置
`MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING=true`。完整的隐私授权、provider、模型路径与证据文件配置见
[Hybrid Memory 检索](./docs/memory-hybrid-retrieval.md)。

## 如何验证

发布前使用以下命令做聚焦验证：

```bash
python -m compileall -q minicode scripts
python -m ruff check minicode/ --select=E,F --ignore=E501
python scripts/evaluate_agent_quality.py --profile current
python scripts/evaluate_agent_quality.py --profile a
python -m pytest -q
```

两档质量门都不会调用远程模型。`current` 固定 fixture、manifest 和记录结果哈希；`a` 固定 fixture/manifest 合同，但有意接受同一 manifest 的新结果，默认命令评估仓库中的记录结果。`current` 是 CI 回归档，`a` 是项目声明的内部晋级阈值。50 个记录的 north-star case 覆盖 10 类任务，其中 30 个会写工作区。仓库自带门禁不会假装重新执行这 50 个任务。

GitHub Actions 会在 Linux、macOS、Windows 和 Python 3.11/3.12 矩阵上执行安装、编译、限定范围 Ruff、打包 smoke、确定性 `current` 门禁和全量测试。

## 仓库导航

| 路径 | 作用 |
| --- | --- |
| `minicode/` | 安装与测试使用的 canonical Runtime 包。 |
| `tests/` | 单元、集成、对抗、验收合同和回归测试。 |
| `scripts/` | 质量门、live runner、分析器与冻结 manifest 构建器。 |
| `artifacts/` | 策展后的公开 manifest/result；原始 journal、workspace 和本地 Memory 均被忽略。 |
| `docs/` | 架构说明、实验报告、验收审计和使用指南。 |
| `py-src/` | 历史/参考目录；它不是 `pyproject.toml` 实际安装的包。 |

推荐入口：

- [3 分钟作品集案例](./docs/PORTFOLIO_CASE_STUDY.md)
- [贡献和上游边界](./CONTRIBUTIONS.md)
- [使用指南](./docs/USAGE_GUIDE.md)
- [Agent 质量门](./docs/agent-quality-gates.md)
- [持久化 Memory 修复验收](./docs/persistent-memory-repair-acceptance-2026-08-23.md)
- [模型路由 live 验收](./docs/model-routing-live-acceptance-2026-08-23.md)
- [优化历史](./docs/OPTIMIZATION_SUMMARY.md)

## 诚实的限制

- 最强的效率数据来自合成仓库。它们在受控条件下证明了机制，但不是对所有真实编码任务的生产力承诺。
- 路径恢复经验在已测范围内最成熟；命令恢复和验证规则证据较强；抽象项目约束在当前配对实验中仍是负结果。
- V5 修复后的 provider 验收尚未执行。确定性测试已通过；最近完成的 V4 provider 运行是 7/10 case、84/91 oracle、10/10 精确 Memory 归因。
- 异步生命周期目前只开放给只读 `explore` 和 `plan`；取消是协作式的，不是进程隔离。
- CodeLoop 有审批、路径和凭据边界，但它**不是操作系统沙箱**。评估陌生任务时应使用临时分支、容器或可丢弃环境。
- 模型 provider 可能接收 prompt 和相关仓库内容。私有代码使用前应先核对 provider 数据政策。
- 仓库当前没有根目录 LICENSE；检查到的 Python 上游也没有公开根许可证。公开可见不等于获得许可，重新分发或商业使用前需要确认条款。

## 上游与致谢

- Python 上游：[QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python)
- MiniCode TypeScript 项目：[LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)
- 本仓库导入基线：[`3036dd7`](https://github.com/zrb3052796119/CodeLoop/commit/3036dd76e4ca676541a79a64dc6d24ec20baf433)

继承了什么、增加了什么、哪些属于实质重构，详见
[CONTRIBUTIONS.md](./CONTRIBUTIONS.md)。
