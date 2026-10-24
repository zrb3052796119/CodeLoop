# 五会话真实模型审计

完成 5 个真实会话，记录 29 次模型调用。
**尚未完全通过验收：声明依赖在第三次读取后被 recurrence_promoted 自动批准，违反必须保留人工审批的要求。**
六类目标经验：即时 durable + draft 捕获 4/6；在副本上将读取时钟推进 8 天后为 6/6。

| 经验形态 | 实际存储状态 | 即时 常规检索 / Recent draft notes | 8 天后 常规检索 / Recent draft notes |
|---|---|---|---|
| 规范约束 | approved / durable | 1 / 0 | 1 / 0 |
| 单次工具失败 | pending | 0 / 0 | 0 / 1 |
| 权限拒绝 | pending | 0 / 0 | 0 / 2 |
| 语法错误修复 | approved / deposition draft | 1 / 0 | 1 / 0 |
| 用户偏好 | approved / deposition draft | 1 / 0 | 1 / 0 |
| 声明依赖 | approved / deposition draft | 1 / 0 | 1 / 0 |

approved 的 TTL 草稿可走常规检索通道；Recent draft notes 为单独草稿块，因此该列为 0 不代表未被检索。表格是六个相关查询的 ID 并集，不代表每个提示词同时命中所有条目。6/6 指 durable + draft 捕获及读侧资格。
USER 偏好与项目条目分开统计；声明依赖的 epistemic_status、limitations、审批状态保留在 analysis.json。
对 3 条 pending 完整条目进行前后比较：True；真实审计源记忆文件哈希保持不变：True。

8 天后结果只来自临时副本上的读取时钟推进，没有修改条目时间戳、审批或生命周期，也没有追加模型调用。
同一副本切换到 strict / capture 的读取对照也记录于 analysis.json；pending draft 不应渲染。

| 会话 | 模型调用 | 状态 | reflection trace |
|---|---:|---|---:|
| S1 | 5 | completed | 1 |
| S2 | 7 | completed | 1 |
| S3 | 6 | completed | 1 |
| S4 | 9 | completed | 1 |
| S5 | 2 | completed | 1 |

S4 还创建了 AGENTS.md，故不能把 S5 中文回复当作仅由记忆引起的证明。
原始响应、工具事件、反思输入和逐会话漏斗日志均在同目录 JSON 中保留；修复后的离线真实 trace 重放单独报告，不能替代这里的原始模型运行结果。
