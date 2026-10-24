# 修复后的真实 trace 离线重放

沿用真实五会话已经捕获的反思输入，使用当前代码在全新临时记忆库中重放。新增模型调用为 0；这不是修复后重新跑了一遍模型。

验收结果：通过；15/15 项检查通过。
inject 即时 durable + draft 捕获 4/6；模拟读取时钟推进 8 天后 6/6。

| 模式 | 声明依赖审批 | 重复观测次数 | 即时草稿块渲染 |
|---|---|---:|---:|
| strict | 无声明泳道条目 | 0 | 0 |
| capture | pending | 3 | 0 |
| inject | pending | 3 | 1 |

- 声明依赖第三次观测后仍须人工审批，保留 inferred 与未实际使用的 limitation，不进入常规检索通道。
- USER 偏好在伴随技术工作时仍进入 USER scope；capture 保持 pending。
- 单次失败与权限拒绝保持 pending，新鲜条目不注入，8 天后仅按 draft 渲染。
- 三模式读取前后 pending 完整记录不变；原五份 trace 的 SHA256 不变。
- strict / capture 的 Recent draft notes 为空。

原始真实模型运行共 29 次调用，在第三次声明观测时曾触发错误自动批准。原始 report.md / analysis.json 保留该失败；本离线结果证明修复，不覆盖历史。
6/6 是经验捕获及读侧资格；渲染 ID 来自六个相关查询的并集，不意味着每个提示词同时包含所有记忆。S4 同时写入 AGENTS.md，后续中文回答不能单独归因于记忆。
