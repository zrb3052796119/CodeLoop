# MiniCode 持久化教训与上下文压缩北极星验收

日期：2026-08-21

## 结论

本次使用生产 `AgentTurnRuntime`、真实模型适配器、真实内置工具、
MemoryManager、ContextCompactor 与 RunJournal，执行了预注册的 20 个主任务，
并追加 4 个跨压缩边界压力任务。全部输入均为合成项目内容。

- **上下文压缩：有作用，但只能条件通过。** 主任务 10/10 在真实
  `context.compacted` 后保留目标、事实、被否方案、约束、决策或组合状态；
  两轮连续压缩也保留了早期标记。长 Skill 的工具结果在下一轮压缩后仍能
  恢复。但大文件任务出现明显压缩失败振荡，虽然最终答对，却消耗 25 次
  模型调用、31 次工具调用、257,088 输入 token 和 153 秒。
- **持久化教训：链路已真实工作，但尚未证明稳定优势。** 两次真实工具错误
  都生成了经过验证的恢复教训；只有 1/2 自动批准并在下一任务注入。另一条
  被安全扫描误判为可疑而停在 pending。四组冷热配对全部完成任务，Memory
  没有带来成功率差异，工具调用平均只减少 0.25 次，精确符号检验 p=1.0。

因此不能客观地说“持久化记忆已经让 Agent 显著变强”。更准确的结论是：
**写入→批准→检索→注入→不再重复同一错误的完整正例已经存在，但 learn-to-
inject 的可靠性和可测收益仍不足以达到 A 级。**

## 主 20 任务

| 指标 | 结果 |
| --- | --- |
| 完整 Agent 任务 | 20 |
| 功能目标完成 | 20/20 |
| 严格 case oracle | 16/17 |
| 不安全动作 | 0 |
| 用户干预 | 0 |
| Memory 任务 | 10 |
| 压缩任务 | 10 |
| 有效压缩后关键状态保留 | 10/10 |
| 验证教训写入 | 2/2 |
| 真实教训下一任务注入 | 1/2 |
| 计划中的 warm 任务发生非空注入 | 3/4 |

主清单 SHA-256：
`fa229a048daa8fbcab3c11f12b87317b9fab1625f14c31e906d3f7ac0fa4d407`。

## Memory 因果链

### 成功链：authentication policy

1. 第一任务调用错误路径 `src/auth_policy.py`，`read_file` 真实失败。
2. Agent 搜索工作区，改为 `backend/src/auth_policy.py` 并成功。
3. Reflection 生成 confirmed recovery，Memory 自动批准。
4. 下一独立 Run 检索并注入同一 entry ID。
5. 原始错误由 1 次降为 0；warm 任务使用 4 次工具调用，cold 对照使用 5 次。

### 失败链：runtime config

1. 第一任务对不存在的 `config` 调用 `list_files`，随后以根目录
   `list_files {"path":""}` 恢复并完成任务。
2. 恢复教训成功写入，claim 与验证链均为 confirmed。
3. trace 安全扫描逐字符串检查时把合法空路径当成“empty memory content”。
4. 整个 entry 被标记为 `suspicious/pending`，下一 Run 报
   `no_active_memories`，因此没有注入。

这不是模型没总结恢复方法，而是审批前的安全扫描发生了假阳性。

## 冷热配对

| Pair | Warm 注入 | Warm 工具调用 | Cold 工具调用 | 差值 |
| --- | --- | --- | --- | --- |
| auth | 是 | 4 | 5 | -1 |
| runtime | 否 | 6 | 5 | +1 |
| deploy | 是 | 4 | 5 | -1 |
| schema | 是 | 5 | 5 | 0 |

Warm−cold 差值为 `[-1,+1,-1,0]`，均值 -0.25，中位数 -0.5；三组非平局
中 warm 两胜一负，双侧精确符号检验 p=1.0。样本不支持稳定提效结论。

![Memory 冷热配对](../analysis-output/memory-compaction-north-star-20/figures/figure-01-memory-pairs.png)

## 上下文压缩

主压缩任务共记录 10 次有效边界，估算释放 44,879 token，以下状态均准确
保留：

- 最早任务目标；
- 中段已验证事实；
- 被明确否决的方案；
- 不可违反的约束；
- 已接受的架构决策；
- 同时分布在历史不同位置的目标、事实、被否方案；
- 两个连续任务中的 summary chain；
- 长 Skill 加载后的任务执行。

大文件 case 是重要反例：它只记录到最初的 pre-request 压缩，随后发生大量
读取与模型循环并多次输出 Auto Compact failure。答案正确不等于压缩稳定；
该路径应判为“功能通过、效率/稳定性失败”。

![压缩与下游工作量](../analysis-output/memory-compaction-north-star-20/figures/figure-02-compaction.png)

## 4 个补充压力任务

- Skill 跨边界链有效：第一任务真实调用 `load_skill`，只回答 READY；第二任务
  再次发生有效压缩且没有重新加载 Skill，仍返回了只存在于旧 Skill 正文中的
  `SKILL-AFTER-COMPACT-88`。
- 文件跨边界链无效：第一任务违背指令，没有调用 `read_file` 就回答 READY；
  因而第二任务不存在可被压缩保留的文件结果。该 case 保持失败，但不能作为
  “压缩丢失工具结果”的证据。

补充清单 SHA-256：
`81d94d559d6c285181d7337412e4fd6e8d29c8f3fe4e765b6c1a91e103e9e600`。

## 建议修复优先级

1. **P1：修复 trace safety 空字符串假阳性。** 扫描 trace 时跳过空白结构值，
   但继续扫描实际 Memory 内容；增加 `path=""`、空 args、空可选字段反例。
2. **P1：治理大工具结果下的压缩振荡。** 把 Auto Compact failure 和 circuit
   breaker 状态写入 Run 事件；在同一内容指纹连续失败时停止重复触发，并为
   普通大工具结果建立可验证的分页/摘要恢复路径。
3. **P2：强化真实任务 oracle。** 增加“指定工具确实成功完成”的 oracle；当前
   已修复空 `memory.rendered` 假阳性，但 required-tool compliance 仍应 fail-closed。
4. **P2：扩大 Memory 配对样本。** 至少覆盖 20 对、多个错误家族和重复运行，
   再判断成功率、错误复犯率、token 与时延收益。

## 证据入口

- 严格分析：[analysis-report.md](../analysis-output/memory-compaction-north-star-20/analysis-report.md)
- 统计附录：[stats-appendix.md](../analysis-output/memory-compaction-north-star-20/stats-appendix.md)
- 图表目录：[figure-catalog.md](../analysis-output/memory-compaction-north-star-20/figure-catalog.md)
- 主结果：[results.json](../artifacts/north-star-memory-compaction-20/results.json)
- 逐任务结果：[task-results.json](../artifacts/north-star-memory-compaction-20/task-results.json)
- 汇总指标：[summary.json](../artifacts/north-star-memory-compaction-20/summary.json)
