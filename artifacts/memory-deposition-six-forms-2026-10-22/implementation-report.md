# 六类经验沉淀：实现与验收

## 已交付
- USER 偏好泳道：普通陈述及伴随成功/失败技术工作的陈述均可捕获；capture 待审，inject 为有 TTL 的草稿。
- 声明依赖：四类 manifest 的可见完整声明生成 inferred claim，明确未执行限制，始终 pending；inject 使用单独草稿区。
- 声明重复出现只累计 recurrence，不以重复次数自动批准；人工审批保留。同一操作重试保留 TTL，正常重放已有草稿事务。
- 旧 pending：inject 下超过 7 天的安全 PROJECT 条目只读展示，保留原审批与生命周期，包含撤销/版本变化校验。
- 漏斗日志、三个独立功能开关及 7 天/14 天配置。默认 strict 保持关闭。

## 重建审计
沿用原审计五个提示词和测试夹具，运行真实 ReflectionEngine/MemoryManager/MemoryPipeline。

| 经验 | inject 结果 |
| --- | --- |
| 规范约束/验证规则 | durable |
| 单次工具失败 | pending；第 8 天按 draft 展示 |
| 权限拒绝 | pending；第 8 天按 draft 展示 |
| 语法修复 | draft |
| 用户称呼/语言偏好 | USER draft |
| requirements 依赖声明 | pending draft，带未验证限制 |

当场 4/6，模拟第 8 天后 6/6；pending 条目的完整 to_dict 保持一致。详细结果在 ../memory-deposition-yield-audit-v1.json。

原历史审计缺少完整工具参数/输出，因此这属于重建管线审计，不能冒称原实测的精确重放。原 33% 沿用历史报告，不将当前 strict 对照组当作同一基线。

## 验证
- 真实审计发现问题后的最终专项、审批权威和审计组合：235 passed（5.28 秒），详见 live/recurrence-regression/final-tests.log。
- 真实缺陷对照：同一回归测试在修复后 14 passed，在保存的实际修复前源码上 11 failed / 3 passed；失败均为声明被错误批准。TTL 重试测试修复后 4 passed，修复前 3 failed / 1 passed。两项独立证明均为 PROVEN，证据位于 live/recurrence-regression/，不代表历史整个 HEAD 工程通过测试。
- 专项/审计最新运行：127 passed。早期专项与 Redis/store 合计 192 passed；后续大范围及单独复跑中出现下述两项 Redis 时序失败，按最新证据保留未通过结论。
- 用户画像、上下文压缩、运行事件、反馈版本集成：178 passed。
- 3/3 合成故障探针被预期断言捕获；独立复跑 122 passed。详见 test-signal/review.md。
- 语法编译、聚焦 Ruff E9/F/I、git diff --check 通过。
- 较大范围回归：2899 passed，20 failed（550.22 秒）。其中 4 项压缩失败已在相同隔离条件下用 HEAD Memory 文件复现（同为 4 failed, 32 passed）；14 项 v40/v41 源码冻结校验因当前源码不同于固定证据而拒绝；2 项 Redis 时序断言在独立复跑中仍略超 200ms（约 200.4ms）；该既有热存储模块本次未改，两个解释器加载的都是 190ms 预算，直接探针观测到约 197–200ms 的系统调度耗时，不能把它们标为通过。冻结证据包含本次改变的文件及此前未提交改动，未修改或重写历史证据。完整日志为 regression-tests-unrestricted.log。

## 真实模型审计
- 用户明确授权后运行 live/run_live_audit.py：五个会话全部完成、29 次模型调用，无超时。此前审批拒绝仅作为历史记录保留，已不阻塞执行。
- 真实审计捕获全部六类形态，但发现声明依赖第三次出现时被通用 recurrence 规则错误批准。因此 live/results/report.md 明确记录原始实测未完全通过；原始 trace 和权威记录未修改。
- 修复后在全新隔离存储上重放原五份完整反思输入，15/15 检查通过：即时 4/6，模拟读取时钟推进 8 天后 6/6；声明在 capture/inject 中重复三次仍 pending、inferred；USER 偏好、旧 pending 只读渲染、模式回滚均符合要求。见 live/results/fixed-code-trace-replay.md。
- 重放新增模型调用为 0；没有把修复后离线验证冒称为又一次真实模型审计。8 天后结果来自临时副本上的时钟推进，并非实际等待 8 天。
- S4 模型在夹具内写了 AGENTS.md，因此 S5 中文回复不能单独证明记忆导致了语言选择；验收依据为实际 USER 条目和读取结果。

## 运行限制
- 不重置原实验工作区；不改历史冻结证据、模型 prompt 或响应 schema；保留已有未提交修改。
- strict 的旧 manifest 确认路径保留历史行为；新声明只记录包名。截断 JSON/TOML 不推断缺失内容。
- 真实审计关闭 Redis，验证磁盘回退；上述大范围回归的 20 项失败仍如实保留，未宣称全套测试通过。

## 文件入口
- 配置与行为说明：docs/2026-10-22--six-form-memory-deposition.md
- 主写入/读取：minicode/memory_pipeline.py
- 重复声明审批守卫：minicode/memory.py
- 实验开关：minicode/memory_deposition_experiment.py
- 依赖证据/合成：minicode/reflection_evidence.py、minicode/reflection_synthesis.py
- 审计：scripts/memory_deposition_yield_audit.py
