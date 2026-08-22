# MiniCode Coding Agent 修复日志（2026-08-19）

> 状态：已完成。本文记录复审问题的修复与验证结果。

## 修复批次

- Batch A：上下文与 Turn 正确性防火墙 — 已完成
- Batch B：Supervisor 安全与 typed outcome — 已完成
- Batch C：Skill 不可变绑定与持续生效 — 已完成
- Batch D：Memory authority 与 claim 生命周期 — 已完成

## 验证

### Batch A

- Dashboard Turn 使用运行期稳定 identity，在压缩后的最终列表重新定位 user/assistant；
  内部 identity 不进入 Session 持久化。
- Full Compact 先确定最终 tail，再摘要精确 dropped set；长 transcript 采用分块摘要，
  不再留下未覆盖的中段。
- Session Memory 只作为 durable 补充，必须同时生成当前 dropped transcript 摘要。
- protected user 不再因 token-floor 回扩而重复。
- Token 不减少时返回原上下文；`force` 只跳过触发门，不跳过效果门。
- Cybernetic FULL/SESSION 策略通过显式 actuator 执行，FULL 不再漂移成 Session Memory。

验证结果：

- 聚焦 compactor/cybernetics：`145 passed`。
- 扩展 context/turn/model-event：`213 passed`。
- Python 编译检查与 diff whitespace 检查通过。

### Batch B

- 顶层 turn 创建共享预算；并发模型调用先原子预留 token，再按 reservation identity 结算，
  消除 check-then-spend 超卖窗口。
- 仅只读工具进入可放弃的外层 timeout worker；写工具留在所属执行线程，避免返回后仍继续写入。
- 子 agent 通过 typed `CanonicalTaskOutcome` 汇报；模型 fallback 文本、review 失败不会再被包装成成功。
- 并行 sidecar journal 首次建目录改为 race-safe。

验证结果：

- 聚焦非网络回归：`172 passed`。
- 扩展 agent/sub-agent/context/reflection 回归：`203 passed`。
- Python 编译检查与 diff whitespace 检查通过。
- 另有 4 个 MCP 测试因 sandbox 禁止 loopback bind 而失败；对应非网络变更路径已通过。

### Batch C

- Skill discovery 在读取前拒绝 root 下的 symlink 并校验 resolved containment。
- 路由 catalog 的公开名、resolved path、source 与 digest 成为同一加载凭证；turn 中内容漂移会 fail-closed。
- embedding cache 绑定 model + endpoint，并丢弃非法 vector。
- explore/plan 子 agent 获得提示要求的 `load_skill`；workflow explore 获得 note 协作工具。
- 路由命中的 Skill 未实际加载时，Agent final 不再直接成功。
- 已加载 Skill 指令绕过通用输出持久化、microcompact，并在 Full/Session/Reactive 压缩中保留完整 call/result pair。

验证结果：

- 聚焦 Skill/Agent：`139 passed`。
- Context retention：`149 passed`。
- 扩展非网络全链：`339 passed, 6 deselected`；deselected 为 sandbox 下的 gateway loopback 测试。

### Batch D

- Memory 检索从 revision 检查到候选选择、控制器决策和 prompt 渲染均处于同一事务快照；
  authority 不可读、scope/file symlink 或 revision 漂移时 fail-closed，不再回退到陈旧内存视图。
- `memory.json` 同时承载 entry 与 approval audit，作为唯一权威原子提交；
  `MEMORY.md`、`approval_audit.json` 降为可重建 projection。项目记忆删除也改为一次原子提交。
- Reflection 按 claim 独立落库与审批：强证据不会夹带批准弱 claim；同 semantic key 的新陈述
  显式 supersede 旧结论，完全相同的陈述才做 recurrence reinforcement。
- 一轮产生或强化的全部 Memory ID 都绑定到 Run；父 agent 与子 agent 的累计记录均保留，
  后续 accept/correct/reject 可以覆盖这一轮的全部结论。
- ProjectFacts 只接受成功工具结果确认的依赖；失败的 pip/npm install 不再成为 confirmed fact。
  每条事实携带 provenance，并支持带原因的 retraction tombstone。
- 普通个人陈述进入 USER scope；技术经验继续进入 PROJECT scope，并记录 run/turn/event provenance。

验证结果：

- Memory authority/retrieval/reflection/deletion 主链：`807 passed`。
- Agent/Skill/context/sub-agent 跨模块链：`338 passed`。
- 删除、路径安全与只读非网络聚焦链：`151 passed, 2 deselected`；两项 deselected 均因
  sandbox 禁止 loopback socket bind。
- 最终变更 seam 聚焦回归：`95 passed`。
- `python3 -m compileall -q minicode` 与 scoped `git diff --check` 均通过。

## 验证边界

- 未运行需要本机 loopback HTTP/MCP server 的测试；当前 sandbox 拒绝 socket bind。
- 仓库整体 `git diff --check` 仍会报告用户已有的 `.mini-code-memory/MEMORY.md` 行尾空格；
  本次涉及的 production/test/doc 范围检查通过，未改写该用户数据文件。
