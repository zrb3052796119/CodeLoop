# MiniCode Dashboard Batch 8D Roadmap

## 1. 目标

Batch 8D 在进入 Batch 9 前补齐两个个人本地 Demo 真正需要的管理动作：

1. 删除一段完整的 Dashboard 会话；
2. 删除当前 Workspace 的一条 Project Memory。

它不是通用数据管理后台，也不是批量清空工具。核心目标是让用户在页面中能够明确预览、确认、删除并看到所有相关页面收敛到同一个真实结果。

## 2. 为什么不能只增加两个按钮

MiniCode 的一段会话分散在三个真实存储中：

```text
Session：user/assistant 正文和 Session 元数据
Turn：网页请求身份、状态和 Session/Run 关联
Run：标题、生命周期、Tool/Model/Memory 等安全运行摘要
```

现有 `delete_session()` 只删除 Session base、delta 和 index。若只调用它，Runs 页面仍会留下用户问题的标题或摘要。这正是此前手工清理后仍能看到对话摘要的原因。

因此 Batch 8D 将页面上的“删除会话”定义为“删除完整会话”：删除选中的 Session，以及只与该 Session 关联的终态 Turn 和终态 Run。

Project Memory 也不只是一行 `memory.json` 数据。一条 Memory 还可能出现在审批审计和其他条目的 `related_to` 关联里。删除必须同时清理这些表示，否则页面计数和关联图会留下孤儿数据。

## 3. Batch 8D-1：后端删除权威与 HTTP 契约

### 3.1 Conversation Deletion Authority

新增一个不依赖 Web 的深模块，提供两类接口：

```python
snapshot(session_id) -> deletion preview
delete(session_id, deletion_revision) -> deletion result
```

`snapshot()` 必须只读，并返回：

- schema/version 和 `mode=read-write`；
- 当前 Workspace 与 Session 的安全身份；
- 将被删除的 Session、终态 Turn、终态 Run 数量；
- 是否存在活动中的 Turn/Run；
- 不透明 `deletionRevision`；
- 固定低基数诊断，不返回正文、路径或内部异常。

`delete()` 必须在获得真实存储协调权后重新计算同一计划并验证 revision。下列情况必须拒绝且不开始删除：

- Session/关联记录不属于当前 Workspace；
- Session ID 非法或路径越界；
- preview 已过期；
- Turn 为 accepted、cancel_requested、running 或 committing；
- Run 仍处于非终态或持有 writer；
- 任何必要存储处于 busy/conflict/unavailable。

删除范围严格为：

- 选中的 Session base、合法 delta 和 Session index 项；
- `sessionId` 等于该 Session 的终态 Turn；
- `sessionId` 等于该 Session 的终态 Run；
- 不删除 `sessionId=null`、其他 Session 或其他 Workspace 的 Run/Turn。

由于三份存储不是一个事务，authority 必须使用确定顺序和幂等对账。进程在中途退出或 HTTP 成功响应丢失时，再次 GET/POST 能识别残留的关联记录并继续清理，不能因为 Session base 已经消失就把孤儿 Run/Turn 永久判成不可删除。只有三类目标均不存在时才返回完整成功。

### 3.2 Project Memory Deletion Authority

同样提供只读 preview 和带 revision 的 delete：

```python
snapshot(memory_id) -> deletion preview
delete(memory_id, deletion_revision) -> deletion result
```

它必须复用现有 Memory coordinated writer，并只接受当前 Workspace 的 `Project` scope。一次成功操作需要：

- 删除目标 Memory entry；
- 删除该 entry 的审批审计记录；
- 从其他条目的 `related_to` 中移除目标 ID；
- 重建索引并原子保存 Project scope；
- 返回安全计数，不返回 Memory 正文、hash、文件路径或原始异常。

pending、approved、rejected、held、archived 条目均可删除，但必须通过当前 revision。User/Local Memory 和整个 scope 清空不在本批范围。

### 3.3 HTTP 路由

建议使用动作资源而不是带 body 的通用 `DELETE`：

```text
GET  /api/v1/sessions/{session_id}/deletion
POST /api/v1/sessions/{session_id}/deletion

GET  /api/v1/memory/project/{memory_id}/deletion
POST /api/v1/memory/project/{memory_id}/deletion
```

POST body 只有一个精确字段：

```json
{"deletionRevision":"delrev_<64 hex>"}
```

保持现有写接口的安全边界：loopback-only、严格同源 Origin、JSON MIME、1 KiB body、拒绝重复键/额外字段/query、`Cache-Control: no-store`、无 CORS、固定错误信封。HTTP handler 只组合 authority，不直接操作路径。

建议错误状态：

- 400：非法 ID/body/revision；
- 404：当前 Workspace 中不存在该目标或可对账残留；
- 409：stale revision、active/busy/conflict；
- 503：本地存储暂时不可用；
- 500：固定脱敏内部失败。

## 4. Batch 8D-2：Dashboard 管理界面

### 4.1 删除会话

在 Sessions 详情头部增加“删除会话”。点击后先 GET 权威 preview，再显示确认对话框：

- 显示安全 Session 标识与将删除的 Session/Turn/Run 数量；
- 明确说明 Runs 中的关联执行记录也会删除；
- 明确不可撤销；
- active/busy 状态只显示原因和关闭/重试，不提供危险确认。

确认后只发送一次 POST。成功后：

- tombstone 当前 Session 并递增读取/动作 generation；
- 清除 Session 选中项和对应 `sessionStorage`；
- 若 Dock 正在继续该 Session，切换为“新 Session”；
- 保留未发送 draft，但绝不自动发送或自动重试；
- 重新读取 Sessions、Runs、Overview 和 Turn authority；
- 旧 GET/POST/SSE 完成不能让删除项复活。

### 4.2 删除 Project Memory

只在 Project scope 条目上显示删除入口。确认框使用安全 preview，展示 category、tier、当前状态和关联清理计数；如果内容被隐藏，绝不为了确认而重新展示正文。

成功后 tombstone 该 Memory ID，刷新 Memory、Memory approvals 和 Overview。User/Local 条目不出现删除按钮，SSE 只能触发 GET 对账，不能自动触发 POST。

### 4.3 交互要求

- 独立易失 deletion stores，不把 revision、preview 或决定写入 browser storage；
- GET 与 POST 使用分离的 generation fence；
- destructive 按钮、焦点圈和危险色符合现有 Waku 风格；
- 对话框具备标题、说明、焦点锁定、Esc 取消、关闭后焦点返回；
- 窄屏确认框不溢出，长 ID/诊断可换行；
- loading、stale、busy、partial、lost-response、retry 和成功状态均诚实呈现。

## 5. 必须通过的验收

### 后端

- RED/GREEN 覆盖真实 public authority 和 HTTP，不只测试 private helper；
- 删除一个 Session 后，其正文、index、合法 delta、关联终态 Turn 和 Run 均不存在；
- 其他 Session、其他 Workspace、无 Session 的 Run、User/Local/Project Memory 均保持不变；
- active Turn/Run、stale revision、跨 Workspace、路径/符号链接、lock busy 和并发写入全部 fail-closed；
- 中途故障、重启、重复 POST 和响应丢失可通过幂等对账安全完成；
- Project Memory 删除后 entry、entry audit、backlink 均消失，索引和其他条目保持有效；
- API/日志/错误不泄露正文、Prompt、路径、diff、command、credential 或原始异常。

### 前端与浏览器

- 选中 Session 和 Project Memory 均可预览并显式确认删除；
- 删除会话后 Sessions、Runs、Overview 和 Dock 自动收敛，不再显示旧标题/摘要；
- 删除 Memory 后 Memory/Approvals/Overview 自动收敛；
- 取消确认不产生任何写入；POST 永不自动重放；
- lost response、409、503、Gateway 重启和 stale response 不会误报成功或复活数据；
- 1280×900 与窄屏无重叠/横向溢出，console warning/error 为 0；
- wheel 隔离安装后的真实 Gateway 覆盖全部新路由和删除 smoke。

### 认证

- 保持 `dependencies = []`；
- 只冻结实际变更的下一版 production baseline；
- v1–v30 manifest 和 accepted semantic gold 保持不变；
- scoped static checks、正式 JS syntax、聚焦矩阵、evaluator 和两轮完整 pytest 全绿；
- 不虚报浏览器、wheel、pyright 或 mypy 结果。

## 6. 明确不做

- 批量删除/一键清空；
- User 或 Local Memory 删除；
- 独立 Run 管理页面的任意删除；
- 正在执行的 Turn/Run 强制删除；
- undo、回收站、历史回填、远程管理；
- 任意路径或文件浏览；
- 数据库、后台任务队列、WebSocket 或新第三方依赖；
- 修复普通对话事实不进入持久 Memory 的既有产品缺口。

## 7. 实施顺序

```text
Batch 8D-1
  审计/RED → deletion authorities → strict HTTP → crash/retry/concurrency
  → wheel/full-suite/baseline certification

Batch 8D-2
  frontend RED → volatile stores → confirmation UX → stale fencing
  → REST/SSE reconciliation → browser/wheel/full-suite certification

完成后恢复 Batch 9A-1
```

不要把 8D-1 和 8D-2 合并成一个超大批次。后端 deletion truth 稳定后再让页面拥有删除能力。
