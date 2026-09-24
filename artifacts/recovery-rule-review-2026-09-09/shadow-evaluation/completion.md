# RecoveryLessonAgent：shadow 接线与有界评测

已完成按恢复片段的评测器、默认关闭的生产 shadow 入口和专用上下文/提示词。**当前还不能替代规则生成器：真实调用未产出可解析的经验，不能判断语义质量或收益。**

## 完成的实现

- `RecoveryLessonAgent` 明确接收 episode 的候选/验证状态、关联调用及事件顺序。专用指令要求候选只作推断性观察、验证成功不等于根因成立，正文每个事实分句和适用范围都需所引证据支持。真实摘录不自动证明新增结论。
- 调用默认每任务最多 2 次、配置上限 10 次，失败同样计入上限；继续共享父级预算、取消令牌和 deadline。保留生成但被拒绝的 claims 供显式离线审查，记录实际调用标记、耗时与 nullable 用量。
- `CyberneticOrchestrator` 复用已有、经过远程许可检查的无工具 client，接入 `ReflectionEngine.complete_shadow()` 的独立分支。MemoryPipeline 仍先提交规则结果，再完成 shadow；新结果不会进入规则选择、审批或记忆 metadata。
- 通过 `recoveryLessonShadowEnabled` / `MINI_CODE_RECOVERY_LESSON_SHADOW_ENABLED` 显式开启；默认关闭，仅 `llm_shadow` 生效。`recoveryLessonMaxCalls` 默认 2。用户私有 `.env` 白名单和进程环境优先级均有回归。
- runtime 指标只记录任务哈希、计数、原因码和用量；不记录调用参数、路径、摘录或正文。旧 whole-task 指标和新的 episode 指标分别聚合。未知模型不再把成本函数的默认价格冒充该模型的费用估计。
- 专用评测器比较同一 episode 投影下的 Rule 和 Agent，固定合成数据为 11 cases、10 episodes（6 verified、4 candidate，其中一个任务含两个独立 episode），另含 2 个不应产生 episode 的身份冲突前检。支持全批请求上限、超时/取消的部分报告、实际消息 hash、脱敏受限响应捕获。评测不调用 MemoryPipeline。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 相关测试面 | **43 模块，973 passed in 6.93s**；本阶段新增 69 项 |
| Ruff F / git diff --check | 通过 |
| 规则记忆隔离 | 测试验证实际 provider 回调发生在提交之后；开启前后内容、审批与 metadata 相同 |
| 默认关闭 / 抽样 / 无 client / 预算 / 幂等 / 取消 | 回归通过 |
| 离线 scripted 对照 | 10 scripted、0 provider；Agent 准入 6 条，same-episode Rule 也是 6 条；0 候选误确认 |
| 真实 15 秒批次 | 9 次请求均触及 deadline；双 episode 任务剩余一段未处理 |
| 剩余 1 次、60 秒诊断 | 约 23.9 秒返回用量，但正文为空；1933 input / 1200 output tokens，无法 parse |

最初在网络沙箱内的 10 次 client 调用尝试均返回 URLError，单独 DNS 检查为 `gaierror`，整个失败批次约 38 ms，保存在 `live-sandbox-attempt.json`。自动审批允许在网络限制外复跑后，合计使用 **10 次外部请求尝试**（9 次 15 秒 + 1 次 60 秒），没有继续加请求。

最后一次的输出 token 数恰好达到配置的 1200 上限，但没有保留 finish reason 或推理 token 明细，所以只能把生成预算不足视为待验证的解释，不能断言具体原因。超时请求用量未知，全部请求的总 token 和总费用也未知；不能把最后一次已知用量当成整批用量。

## 准入决定与后续工作

本阶段完成了工程接线和可复跑评测。真实模型得到 **0 条可解析输出、0 条可作语义裁定的 claim**，因此不报告 precision/recall 或“优于规则”。离线的绿色结果也只证明合同和计量按预期工作。

保持新 shadow 开关默认关闭，正式记忆仍走原来的规则路径。下一步应先解决当前反思模型的响应预算/正文输出问题，在明确支持的配置下让同一 smoke 数据产生有效 JSON，再做逐分句证据裁定与更大的对照样本；本轮没有修改用户的模型配置或自动切换生产选择。

## 复跑与证据

```sh
.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --output artifacts/recovery-lesson-scripted.json

.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --execute --max-calls 10 --timeout 15 \
  --output artifacts/recovery-lesson-live.json
```

第二条会使用当前授权的模型配置，需可用网络；它会产出评测报告，不写正式记忆。当前 15 秒/1200 token 配置已在本轮失败，不建议把复跑本身当成上线验收。

- [测试日志](surface-tests.txt)、[模块清单](test-files.json)、[源码 hash](source-hashes.json)、[验证摘要](validation.json)
- [离线对照](offline.json)、[真实 15 秒批次](live.json)、[60 秒诊断](live-diagnostic-60s.json)
- [逐片段可裁定性记录](adjudication.json)、[接线记录](integration.md)
- 使用说明：`docs/recovery-lesson-shadow.md`
