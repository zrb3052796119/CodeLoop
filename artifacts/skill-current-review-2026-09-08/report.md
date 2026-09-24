# 当前 Skill 系统审查（2026-09-08）

结论：模型选择、Directory 分层发现和正文加载屏障已经实现，方向与前面的设计讨论一致；目前仍需修复发现工具和会话刷新，并重新验收评测结果。当前环境实际配置还是 `routed`，所以代码中的新模式尚未成为当前默认运行路径。不能根据现有 A/B 报告宣称完整系统的 Skill 成功率已经稳定提升。

本次为只读源码审查、既有测试和独立本地复现，没有修改生产代码、原测试或用户配置，没有新增在线模型评测。审查前后校验了 `minicode/`、`tests/` 的 664 个 Python 文件，均未变化、无新增。工作区原有变更保留。

## 实际状态

| 项目 | 核实结果 |
| --- | --- |
| 当前模型 | `glm-5.3-flash`，custom provider |
| 当前 Skill 模式 | `routed`；`model` 已实现，但当前没有开启 |
| 当前目录规模 | 17 个 Skill，11 个 Directory；平铺估算 2178 字符 |
| 分层阈值 | 默认 `flatCatalogCharBudget=8000`；现有目录切换 model 后仍为平铺描述 |
| 分页预算 | `skillPageCharBudget=4000`；存在超长首条突破预算的问题 |
| 意图辅助 | `intentHints=false`、`intentPrefetch=false`；语义意图分类器和 Skill reranker 也关闭 |
| 模型选择路径 | canonical Intent 仍解析；跳过原相关性打分、embedding 和 reranking；意图提示可选 |
| 加载权限 | model/hierarchical 使用 catalog scope；明确禁止和父子权限交集仍执行 |
| 正文交接 | 新 Skill 与普通动作混合调用时，动作被真实延后，正文进入下一次模型请求 |

配置证据：[runtime-summary.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-current-review-2026-09-08/runtime-summary.json>)。当前模式是运行状态观察，不把保守默认值本身列为代码缺陷。

## 已复现的实现缺口

### 1. P2：CLI/TUI 缺少跨轮目录快照刷新

CLI 在启动时创建 registry，后续路由重复使用 `tools.get_skills()`；TUI 同样使用会话的 `args.tools`。同一会话内新增 Skill 不会进入下一轮目录，修改已有 Skill 后，旧摘要仍可见，加载却被正确的摘要/正文一致性校验拒绝。提示用户“刷新目录”也没有完成会话级刷新。

独立复现：第一轮只有 `alpha v1`；新增 `beta`、更新 `alpha v2` 后，同一 registry 的下一轮仍只有 `alpha v1`。加载 alpha 返回 `Unknown or stale skill binding`，beta 返回加载拒绝；重建 registry 后两者均能正常加载。headless 每次构建运行环境，与长驻交互入口行为不同。

定位：[main.py:165](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/main.py:165>)、[main.py:275](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/main.py:275>)、[input_handler.py:727](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tui/input_handler.py:727>)。

建议：在 turn 边界检测目录变化，统一替换 catalog、发现/加载工具及相关模型工具缓存。不要只让 loader 单独实时重扫，那会破坏同轮快照一致性。

### 2. P2：带目录偏好的搜索提前裁掉候选，分页无法补救

`search_skills` 先按当前页配额截取 preferred/global，再对截断后的列表应用 offset。`TRUNCATED` 也只检查截断列表，因此大量未返回匹配被错误地当成不存在。

独立复现：1000 个匹配、`limit=50` 时只返回 50 个，却标记 `TRUNCATED: no`；`offset=50` 返回空页，950 个候选无法通过保持其他参数不变的分页取到。另一个合法边界 `limit=1` 将全局配额变成 0：首选目录没有匹配、其他目录有唯一目标时，结果为空；移除目录偏好即可找到目标。

定位：[browse_skills.py:341](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/browse_skills.py:341>)。

建议：保留完整稳定的候选集合，将配额用于逐页输出，并根据实际剩余候选产生续页信息。这一项直接影响千级目录下的跨目录检索。

### 3. P2：加载屏障在工具校验前解引用不合法输入，导致整轮异常

新屏障直接对 `call.input` 调用 `.get()`。模型返回非空 JSON 数组、字符串或数字时，Agent Loop 抛出 `AttributeError`，无法把工具错误交回模型修正。当前 OpenAI compatible adapter 的 `json.loads` 可以产出这些值，并没有将其限制为对象。直接经过 ToolRegistry 的相同坏输入则会被封装成失败结果。

独立复现：输入 `["alpha"]`、`"alpha"`、`42` 均在首次模型调用后使 `run_agent_turn` 异常退出。

定位：[agent_loop.py:3760](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_loop.py:3760>)；入口证据：[openai_adapter.py:1090](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/openai_adapter.py:1090>)、[openai_adapter.py:1217](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/openai_adapter.py:1217>)。

建议：预检先验证输入类型，保留 ToolRegistry 的错误反馈路径；同时确保有效的新加载仍触发屏障。

### 4. P2：页面预算对首条描述不生效

browse/search 都为保证 offset 前进而无条件输出第一条，但 Skill description 没有相应长度限制。临时真实 SKILL.md 的 description 长度为 12015 字符，在 4000 字符预算下，两个工具分别返回 12072 和 12082 字符，且标记 `TRUNCATED: no`；将预算降至 500 结果相同。

定位：[browse_skills.py:215](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/browse_skills.py:215>)、[browse_skills.py:373](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/browse_skills.py:373>)。

建议：为单条超长描述定义截断/续读规则，把标题及返回头部也计入预算，同时保证游标可以前进。仅删除首条例外会造成翻页停滞。

上述问题均为特定输入或目录变化下的确定性复现，不表示当前 17 个 Skill 的每次请求都会触发。

## A/B 报告应如何解读

432 个独立 run 记录确实存在，设计为 24 题 × 2 个规模 × 3 个目录变体 × 3 个实验组。规模实际为 118/1029 个 Skill。101 次成功加载的正文与对应 SKILL.md 一致；但全部 `confirmed_handoff` 都为 false，这批记录尚不能证明正文进入了下一次模型请求。现有数据能支持描述性观察，但存在以下验收问题：

1. **307/432 次以 `nonload_tool_requested` 结束，完整执行链没有跑完。** 原始窗口会执行同批 Skill 工具，但遇到普通工具请求即停止后续回合。因此 `load_skill + list_files` 可以计加载成功，而 `browse + list_files` 没有机会继续到下一轮加载。预取更容易减少一步浏览，这种截断会偏向它，不能把比较直接解释为完整 Agent Loop 的收益。当前 runner 已改变这部分继续执行逻辑，原记录与现行运行器也需重新对齐。
2. **wrong-placement 的四道显式名称题存在标签冲突。** 例如 T10 明确要求 `payment-gateway/idempotency`，变体评分目标却改为 `message-queue/idempotency`。遵从用户指定名称的真实成功加载被算错。剔除这四题后，剩余描述性结果仍很弱（各组 S100 为 1/15，S1000 为 0/15），但同样受执行窗口限制，不能据此直接判定生产模型的最终检索能力。
3. **报告分母存在笔误。** 唯一超时是负例 T20，因此三个实验组的正例分母都应为 114。受限窗口的正确加载数为 A=18/114、B=18/114、B2=31/114。负例共 90 次，其中 89 次有效、1 次超时；不是报告所写的 119/120。
4. **每个实验单元只运行一次。** 尚不能称为“repeatable benefit”。当前没有同一随机条件下的重复测量，也没有本轮新增的在线复测。

决定性任务原文：[tasks.json:145](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-hierarchy-ab-2026-09-08/tasks.json:145>)。完整分析见 [evaluation-review.md](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-current-review-2026-09-08/evaluation-review.md>)，重算证据保存在 [evaluation-review-evidence.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-current-review-2026-09-08/evaluation-review-evidence.json>)。详细分析还区分了“至少一个正确加载”和“没有额外错误加载”的评分，并核对了提示注入、正文来源和成本统计。

## 验证结果与限制

| 检查 | 结果 |
| --- | --- |
| 发现、加载屏障、路由模式、策略、目录契约、配置和 prompt | 167 通过，1 失败 |
| 显式名称、生产路由回归、Agent Loop 和恢复 | 394 通过 |
| CLI/TUI/headless/网关相关入口 | 44 通过；其中 5 个本地网关测试首次因沙箱不能监听端口失败，允许 localhost 临时监听后全部通过 |
| 合计，不重复计重跑 | **605 通过，1 失败**，共 606 个测试实例 |
| 定向 Ruff | 6 个相关文件全部通过 |
| 发现与交接独立复现 | 根审查任务再次执行，复现结果一致 |

唯一代码/数据相关测试失败是 [test_skill_catalog_contracts.py:309](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/tests/test_skill_catalog_contracts.py:309>)：`quality/lint-and-static-cleanup` 的 supported_operations 已是 `[code.update, debug.update]`，测试仍预期 `[code.update]`。需要确认最终契约并对齐；这不等同于应当删除新增能力。

本地 model 模式另复核了之前两项失败：显式 `code-skills/minicode-study` 不再产生额外的 unresolved 名称，实际 loader 成功；“不要加载或使用任何 Skill”得到 deny_all，目录为空，实际 loader 拒绝。这证明对应确定性问题已修复，不代表已重跑之前的 20 题在线评测。

屏障的逆序正对照也通过：普通动作排列在新 load_skill 之前时仍没有执行，正文出现在下一次模型输入。父子策略交集和 explore/plan 发现工具接入未发现新增断路。没有证据将设计上允许的 catalog scope 误报为越权。

该审查没有执行整个仓库的全量测试，没有证明任意千级目录布局、未分目录的大 catalog 或端到端任务质量均已满足要求。意图解析模块仍在工作，新模式只是移除了它对 Skill 相关性候选的硬性裁决；是否启用 hints/prefetch 应由修正后的完整执行评测决定。

## 建议顺序

先完成会话刷新、搜索分页、输入健壮性和页面预算修复；对齐剩余契约测试。然后在明确的测试配置中开启 model 模式，用现有 17 个 Skill 复测真实完整执行链，再修正变体标签并重跑百级/千级评测。分别记录候选发现、正确加载、正文进入下一次请求和最终任务完成，避免把这些阶段混成一个成功率。

本轮保留了原有配置和全部实现，尚未执行上述修复或模式切换。

复现与测试材料：[审查工作目录](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-current-review-2026-09-08/>)；[源码只读校验](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-current-review-2026-09-08/read-only-check.json>)。
