# 持久化记忆完整性复审（2026-07-30）

## 结论

当前模块**还不能认定为完整**。

显式记忆的主链已经比较成熟：User / Project / Local 三作用域、显式写入、
安全检查、待审批状态、审批/拒绝、确定性检索、受预算约束的提示词注入、
使用反馈、反思写入和 Project Memory 定向删除都有实现和测试。

但“持久化记忆”作为生产生命周期仍有四个 P1 级缺口：

1. 已撤销的记忆在长生命周期进程中仍可能被注入一次；
2. Project / Local 记忆根目录可通过符号链接把写入引到 Workspace 外；
3. 普通对话事实没有进入可审批、可跨 Session 检索的生产入口；
4. 容量淘汰和遗忘/删除没有形成一致、可审计的全作用域闭环。

因此，更准确的状态是：**显式记忆功能主链可用，持久化生命周期与并发/
安全边界不完整，尚不具备完整发布认证条件。**

## 已具备的能力

| 能力 | 结论 | 证据 |
|---|---|---|
| 三作用域持久化 | 已实现 | `MemoryPaths` 管理 User / Project / Local |
| 显式写入 | 已实现 | `# ...`、`/memory add [scope:] ...` |
| 安全与审批 | 已实现 | 自动反思默认进入 review；只有 active + approved + safe 才可注入 |
| 检索与注入 | 主链已实现 | 统一检索器、相关性门、冲突压缩、条数/Token 预算 |
| 反馈 | 已实现 | 仅对实际渲染的记忆记录注入与反馈 |
| 跨进程写协调 | 部分实现 | 全局 RLock + `flock`；写前可刷新磁盘 revision |
| Project 定向删除 | 已实现 | 删除 entry、对应审批审计和 backlink，并带 revision/fence |
| 健康检查 | 已实现 | 只读、有界、逐 Store 隔离 |
| 普通会话事实摄取 | 未实现 | 功能审计稳定报告 `MEM-001` |
| 全作用域忘记/保留 | 未实现 | TUI 无 forget/delete；User/Local 无等价删除 authority |
| 恢复与耐久认证 | 未完成 | Batch 9A-2、9A-3、9B、9C 仍明确 deferred |

## 优先级发现

### P1：撤销后的旧快照仍可进入一次提示词

`CanonicalMemoryRetriever.retrieve()` 先从长生命周期
`MemoryManager.memories` 复制 active entries，检索阶段使用
`record_usage=False`，不会进入刷新磁盘 revision 的协调写路径。提示词先被修改，
随后 `record_retrievals_and_injections()` 才触发刷新。

隔离复现：

```text
persisted_status: rejected
persisted_active: false
stale_content_injected: true
live_status_after_injection: rejected
```

这意味着 Dashboard 或另一个进程拒绝/删除记忆后，正在运行的 TUI 等长生命周期
进程仍可能把被撤销内容注入下一次模型请求。刷新最终会发生，但发生在内容已经进入
提示词之后。

修复边界应位于检索 authority：在生成候选快照前，在同一 Store 协调边界内刷新并
取得带 revision 的 active snapshot；注入提交前还应校验该 revision 没有变化。

### P1：直接 Memory 写路径会跟随作用域根符号链接

`MemoryStoreCoordinator` 只对全局锁根使用 `O_NOFOLLOW`。实际 Project / Local
作用域的 `_ensure_scope_path()` 使用普通 `Path.mkdir()`，`_atomic_write()` 在
该父目录中创建临时文件并 `os.replace()`，都会跟随已存在的符号链接。
`MemoryPipeline.save_state()` 也直接 `os.makedirs()` + `open(..., "w")`。

隔离复现把 `workspace/.mini-code-memory` 指向 Workspace 外的目录，随后普通
`MemoryManager.add_entry()` 成功在外部生成：

```text
memory.json: true
MEMORY.md: true
approval_audit.json: true
```

Dashboard 的 `MemoryApprovalAuthority` 已经有 no-follow 校验，但 TUI、反思和
`MemoryManager` 直接写路径绕过了它。这说明安全策略位于一个旁路接口，而不是
真正的持久化 Store seam。

修复应把根目录/目标文件的 no-follow 校验、目录 fd、原子替换和锁统一下沉到一个
深的持久化 Module，所有作用域写入和 pipeline state 都必须经过它。

### P1：普通对话事实仍不能跨 Session 持久化

生产入口只拦截 `# ...` 和 `/memory add ...` 等显式命令。Agent Loop 的自动写入
是执行轨迹反思，其 Value Gate 会正确拒绝没有 durable execution evidence 的
普通陈述；它不是用户事实摄取器。

内置 Memory 功能审计用“`小花是我唯一的好朋友。`”验证当前路径，结果：

```text
memory.conversation_fact: fail
MEM-001: Ordinary user facts are not persisted/retrievable across Sessions.
```

这不是偶发失败；功能审计将它标为环境无关、稳定复现，Roadmap 也明确把
Dashboard natural-language user-fact intake 列为 deferred。

正确补齐方式不是放宽反思 Value Gate，而是增加独立的 conversation candidate
Adapter：抽取候选事实、记录来源 Session/Turn、选择作用域、去重，默认进入审批，
通过后再参与检索与注入。

### P1：容量淘汰和“忘记”不是一致的持久化事务

每个 `MemoryFile` 默认最多 200 条、内容上限名义上为 25 KiB。超过限制时
`_enforce_limits()` 直接 `pop(0)`，没有淘汰原因、tombstone、审批审计更新或
backlink 清理。

隔离复现把上限设为 1 后写入两条：

```text
first_entry_evicted: true
second_entry_retained: true
orphan_first_audit_records: 1
```

此外：

- `size_bytes` 实际统计 Python 字符数，不是 UTF-8 bytes，也不包含元数据和审计；
- `MemoryManager.delete_entry()` 只删 entry，不处理审批审计/backlink；
- `clear_scope()` 只替换 `MemoryFile`，不形成带计划、确认和结果的删除事务；
- Project 有专门删除 authority，但 User / Local 没有等价的用户可用入口；
- TUI 只有 pending/review/approve/reject/restore，没有 delete/forget。

所以用户既可能在无提示下失去已批准记忆，也不能通过一致的公开流程彻底忘记
User / Local 内容。现有 9A-1 健康检查能发现部分 orphan，但 9A-2 retention/reset
仍未实现。

## 结构性原因

记忆核心相关文件合计约 10,189 行，其中 `minicode/memory.py` 单文件 3,928 行。
`MemoryManager` 同时承担 schema、文件格式、兼容加载、自动恢复、安全、审批、
搜索、反馈、维护和 CLI；`MemoryPipeline` 宣称“四方法接口”，实际还暴露
initialize、stats、read、inject、write、feedback、maintain、save_state，
而审批、删除、检索器和 curator 又直接访问 Manager 或其内部状态。

这不是单纯的“代码太长”，而是 Module depth 不足：Interface 很宽，权威状态、
路径安全和生命周期策略分散在多个 seam。符号链接旁路和旧快照注入正是这种
重复策略的结果。

建议收敛为一个深的 `MemoryRepository`（名称可调整）：

- `snapshot_for_retrieval(request) -> {revision, active_entries}`
- `propose(candidate) -> pending/approved identity`
- `decide(id, review_revision, decision)`
- `forget(scope, id, deletion_revision)`
- `record_usage(revision, selected_ids, rendered_ids, evidence)`
- `plan_retention(scope) / apply_retention(plan_revision)`

该 Module 内部独占路径校验、锁、revision、原子写、审计、backlink 和恢复策略。
TUI、Dashboard、Agent Loop、reflection、curator 只作为 Adapter，不再直接操作
`manager.memories` 或私有保存方法。

## 验证结果

- 重点持久化/审批/检索/删除集：`145 passed`。
- 完整 Memory 相关测试集：733 个唯一测试均可通过。沙箱内 24 个 HTTP 用例因
  禁止绑定 `127.0.0.1` 失败；在允许本机回环后，两份 HTTP 文件 `42 passed`。
- Memory 功能审计：退出码 1，5 个能力中 3 pass、1 partial、1 fail；唯一问题为
  稳定 P1 `MEM-001`。
- 三个额外隔离探针稳定复现：符号链接越界写、撤销后旧内容注入一次、容量淘汰
  留下孤儿审计。

测试集质量总体较好，但当前断言偏重 happy path、索引一致性和 bounded output，
缺少以下红测：

1. 另一个进程 reject/delete 后，旧 Manager 绝不能再渲染该内容；
2. 三个作用域的根或目标文件为 symlink 时，所有写路径必须 fail closed；
3. 容量淘汰必须产生可解释、可审计且 backlink 一致的 retention 结果；
4. 两个隔离 Session 完成普通事实的候选、审批、检索和注入；
5. User / Project / Local 的 forget 都验证磁盘内容、审计、backlink 和重启后状态。

## 建议执行顺序

1. 先为旧快照注入和 symlink 写旁路增加红测并修复，这两项是现有能力的正确性/
   安全缺陷。
2. 收敛唯一持久化 Store seam，禁止运行时和各 authority 直接操作内部文件状态。
3. 实现 conversation fact candidate intake，保持默认需审批，不削弱反思 Value Gate。
4. 完成全作用域 forget、可计划 retention、corruption recovery 和 interrupted-write
   验证。
5. 最后再做耐久/并发压力与发布认证；在此之前不要将模块标记为“完整”。
