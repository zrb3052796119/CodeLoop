# CodeLoop 超时恢复与继续实测

更新时间：2026-09-19（Asia/Shanghai）。本体两处修复及验证完成；任务交付仍未完成。Qwen 对照已准备，但因自动审批拒绝外发，尚未执行，正在等待用户明确授权。

## 已完成的本体修复

1. 主 Agent 在有界收尾模式下每轮最多自动重试一次模型 TimeoutError。每次重试重新通过共享预算准入，使用新模型操作 ID，遵守取消、原截止时间及执行步数限制；已经完成的工具回执原样保留，不重放工具。连续超时或没有重试空间，直接返回宿主观察到的部分进展，不再向失效服务追加总结请求。Legacy/子 Agent 超时行为保持兼容。
2. Git 状态工具保留 porcelain 输出的前导状态空格，按 X/Y 两列独立计算暂存与工作区改动，未跟踪文件单独计算，修正首行文件名丢首字母的问题。

生产文件为 `minicode/agent_loop.py` 与 `minicode/tools/git.py`；新增 `tests/test_primary_model_timeout_recovery.py`、`tests/test_git_status_regressions.py`。本轮前快照在 `before/`，仅本轮差异在 [task-only.patch](task-only.patch)，未覆盖已有用户改动。

## 验证证据

| 检查 | 修改前 | 修改后 |
| --- | --- | --- |
| 同字节超时恢复回归 | 10 failed / 3 passed | 13 passed |
| 同字节真实 Git 状态回归 | 5 failed / 2 passed | 7 passed |
| 主循环相关 8 文件检查 | — | 178 passed |
| Git 相关选择检查 | — | 16 passed / 99 deselected |
| 最终独立集成 15 模块 | — | **299 passed** |

最终 18 个任务源/测试文件的 CI 式 Ruff、编译、限定 diff 检查通过，检查前后哈希一致。以上范围有重叠，不相加成一个测试总数。Git 文件在更严格的默认 Ruff 下仍有三类既有告警，已与旧快照核对；CI 规则通过。本次未执行独立静态类型检查。

最终超时同字节证明以 [timeout-regression-proof.json](timeout-regression-proof.json) 为准，初始 `timeout-red.json` 对应较早测试版本，不能混用其哈希。[独立验收报告](independent-host-verification/report.md) 保存完整命令、结果及静态检查范围。

## 真实 GLM 复测

Run `run_a7aae85c367440fcbc2f4b0a1247a45d` 使用生产 CodeLoop runtime、现有 glm-5.3-flash 路由、真实非流式请求，保留隔离目标工作区。此次明确设置 maxOutputTokens=8192、open/read 各阶段超时180秒、底层HTTP重试上限1；整轮模型准入40次、100万tokens、内部成本guard $5、截止时间1200秒。配置只在测试进程内生效。

- 第一次模型请求180003ms超时，写入model.failed；生产主循环自动重新准入，发起第二次请求。
- 第二次同样180003ms超时，随后直接输出partial摘要；没有第三次模型请求，没有工具执行，没有误报任务完成。
- 全程360.29秒，2次模型请求、0次成功返回、0次工具、0个子Agent；task.outcome=unknown，goalAchieved=false，事件执行收尾已真实落盘。
- 共享预算记录2次调用及26046个估算输入tokens；provider没有返回usage，所以日志中已报告tokens为0并不表示真实用量为0。内部cost计数为0也不证明没有产生账单。原始 [summary.json](live-run/summary.json) 与 [response.md](live-run/response.md) 保留这些差异。

这证明了真实超时的有界重试和诚实收尾，不能证明原任务已完成，也不能把模型服务超时归因成35步上限。非流式等待HTTP响应头可能同时包含服务端排队/生成时间，目前没有足够证据区分底层原因；见 [诊断记录](transport-budget-diagnosis.md)。没有修改模型价格表或猜测真实价格。

## 目标任务与待授权对照

目标仍是 `/private/tmp/codeloop-completion-recheck-20260918` 中 `test_runner` 的真实漏跑修复任务。此前自主修改通过36项相关测试，但三项审查问题（201文件参数丢失、选择集合扩大、warning文字误判失败）仍待CodeLoop处理。此次GLM没有执行工具，未产生新目标修改、commit、push或PR。GitHub CLI仍未登录。

为区分当前模型服务问题与执行框架问题，准备了临时选用已配置 `subagentModels.general` 模型 qwen3.7-max 的同任务续办。仍使用生产runtime和同一权限边界；清除主模型transport和自定义headers，专用凭据从该子Agent路由构造；不会修改持久配置。独立本地预检已确认端点 `token-plan.cn-beijing.maas.aliyuncs.com`、凭据来源匹配、没有主模型headers，未发送模型请求。

拟用38次模型准入、95万tokens、内部guard $4，低于扣除先前估算后的剩余额度；原20分钟窗口已过，因此对照另设明确的15分钟窗口。移除了历史中的内部推理字段，保留真实任务/工具记录。它是宿主选择的跨模型续办，**不是生产自动fallback、不是子Agent派发，也不是只改变模型的严格A/B试验**。

自动审批拒绝启动该对照，原因是缺少用户对具体第三方接口、测试代码/任务记录外发及后续PR动作的明确授权。拒绝发生在创建进程前，对照尚未执行。已向用户发送包含端点、数据、操作范围的明确问题，授权前不会启动。记录见 [approval-status.json](qwen-followup/approval-status.json)。

本次没有代替CodeLoop修改目标候选代码或创建PR。完整累计本体补丁在上级 `task-only.patch`（18文件，按manifest中的各自起始快照生成）；本轮4文件补丁独立保存在本目录。
