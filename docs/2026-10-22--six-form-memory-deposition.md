# 六类经验沉淀（v1）

本次在既有 permissive deposition 实验上增加用户偏好、声明式依赖和旧 pending 的读侧草稿通道。默认仍为 strict，三个新通道只在实验模式下生效，不改模型 prompt 或响应 schema。

## 配置

```sh
export MINI_CODE_MEMORY_DEPOSITION_MODE=inject
# 下列开关在 capture/inject 下默认开启，0 单独关闭。
export MINI_CODE_MEMORY_DEPOSITION_USER_PREFERENCES=1
export MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES=1
export MINI_CODE_MEMORY_DEPOSITION_PENDING_DRAFTS=1
export MINI_CODE_MEMORY_PENDING_DRAFT_DAYS=7
export MINI_CODE_MEMORY_DEPOSITION_DRAFT_TTL_DAYS=14
```

unset `MINI_CODE_MEMORY_DEPOSITION_MODE` 或设置 strict 可关闭新增行为。非法模式和非法功能开关均关闭新通道；非法或非正 TTL/年龄阈值回退默认值。新增偏好草稿在切回 strict/capture 或关闭偏好开关后不可检索。既有一般实验草稿沿用原来的 TTL/回滚行为。

## 三个通道

| 通道 | capture | inject |
| --- | --- | --- |
| 用户偏好 | USER `user_preference`、pending、draft | USER 草稿自动批准，可检索，有 TTL |
| 声明依赖 | PROJECT、pending、draft | 仍 pending，只进入 `Recent draft notes`，有 TTL |
| 旧普通 pending | 不展示 | 安全、有效 PROJECT pending，更新时间严格超过阈值时作为 draft 展示 |

偏好从用户任务描述中提取不超过 200 字符的第一人称陈述，去除并列任务命令。普通 conversation fact 优先；有技术工作时不再拒绝偏好。技术反思成功写入时，也以独立确定性 USER 操作捕获偏好。该通道沿用安全扫描及事务/重试契约，并给既有用户画像观察器提供抽取后的陈述。

requirements.txt、package.json、pyproject.toml 的 project.dependencies、Pipfile 的依赖声明生成 `declared` LibraryEvidence。claim 是 `inferred`，明确包含 `Declared in manifest; not yet exercised in this trace.`，价值信号为 `declared_dependency`，不属于强信号。声明不能因同批次其他已验证 claim 或一般草稿回退而自动批准；只有已确认依赖进入现有 Project Facts。

重复读取同一声明只累计 recurrence，不触发通用的三次自动批准；切回 strict 后，已有声明条目也保持这一审批边界。人工批准仍可正常使用。声明草稿的同一操作重试保留原 TTL 并重放事务，不延长有效期。

为保持 strict 历史行为，旧解析器已经把某些原始 requirements/结构化 dependency 内容当作 confirmed 的路径保持原样。新 declared 路径在 strict 下不产生条目。声明只存包名，不存版本约束。完整 JSON/TOML 才能解析；被 trace 截断的结构化 manifest 不推断缺失内容，requirements 只接受完整可见行。

旧 pending 只在读取时解释，不修改 approval_status、lifecycle、updated_at 或 durable 计数。渲染顺序是 Redis 草稿、声明依赖草稿、旧普通 pending，共用剩余条数及 token 预算。条目需安全、active、非锁定/归档，且无普通实验草稿元数据。注入 ID 纳入 `injected_draft_ids`；后续审批、拒绝、删除、内容更新、模式切换会使旧草稿证明失效。

## 漏斗日志

每次正常结束的 write_result 输出一行（无记忆正文）：

```text
MemoryPipeline: deposition candidates=N validated=N value_accepted=N auto_approved=N pending=N drafted=N lost=N mode=inject
```

candidates/validated/value_accepted 是 claim 数，并计入独立用户陈述。auto_approved/pending 是实际写入或重放条目的当前审批状态；drafted 是其中带 deposition_draft 的条目数，因此与审批数重叠。lost 是候选中未物化的数量；已提交/重放的 Project Facts 也视为捕获。旧 pending 的读侧展示另外记录 `aged pending rendered as draft` 日志，不伪报为新写入。

## 可重复验收

```sh
python -m scripts.memory_deposition_yield_audit --out artifacts/memory-deposition-yield-audit-v1.json
```

脚本使用原审计五个提示词和夹具重建 trace，经真实 ReflectionEngine、MemoryManager、MemoryPipeline 执行，在全新隔离目录落盘。旧审计未保存完整参数/输出，因此本脚本不是精确历史重放，也不调用模型。它拒绝重置已有工作区。

重建审计 inject 当场捕获 4/6；模拟第 8 天，工具单次失败和权限拒绝作为旧 pending 草稿可见，合计 6/6。两条 pending 仍待人工审批。原 33% 是旧实测报告数字，不能用当前 strict 控制组数字替代。

## 真实模型审计与修复验证

经明确授权，已在独立夹具中完成相同五个场景的真实模型审计：5 个会话、29 次模型调用，无超时。真实轨迹覆盖了全部六类经验，并暴露了原有 recurrence 自动批准规则对声明依赖的误升级；原始结果保留为未完全通过，未改写证据。

修复后使用这五份完整反思输入进行离线重放，不执行工具或模型。15 项检查全部通过：即时捕获 4/6，在临时副本上推进读取时钟 8 天后 6/6；声明重复三次仍为 pending、inferred，只进入草稿区；偏好为 USER scope；pending 完整记录与原始 trace 均未改写。最终相关测试 235 passed。修复后未另跑真实模型会话，因此离线重放与原始实测分开报告。

- [原始真实模型结果](../artifacts/memory-deposition-six-forms-2026-10-22/live/results/report.md)
- [修复后真实 trace 离线重放](../artifacts/memory-deposition-six-forms-2026-10-22/live/results/fixed-code-trace-replay.md)
- [完整实现及回归限制](../artifacts/memory-deposition-six-forms-2026-10-22/implementation-report.md)
