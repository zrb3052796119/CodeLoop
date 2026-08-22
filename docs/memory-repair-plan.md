# 持久化记忆修复计划

**目标**:让 CodeLoop 在真实使用中产出的记忆是可用的——归档正确、内容干净、只记项目知识。

**状态**:**已完成**。P0–P5 全部修复,验收 5 项标准真实端到端全部通过,`3711 passed`,7 个变异全部被对应测试杀死。

---

## 背景:为什么之前像在打地鼠

前几轮的修复是被动的——靠真实数据撞出问题才修。这次先把整条管线拆开,把**所有**已知缺陷列全,再动手。

管线共 6 段,缺陷分布如下:

```
① 执行       agent_loop 写 trace          ← P0 审批墙 / P1 任务描述串台
② 证据抽取   TraceEvidenceExtractor       ← P2 成功输出被判成错误
③ claim 合成 RuleReflectionSynthesizer    ← P3 工具故障 / P4 截断碎片 / P5 决策碎碎念
④ 校验       ReflectionClaimValidator     ← 无已知缺陷
⑤ 价值闸门   ReflectionValueGate          ← 无已知缺陷
⑥ 落库/检索  MemoryPipeline               ← 已知限制：打分器吃文本长度（本次不动）
```

---

## P0 — 审批墙(阻断性,必须先修)

**现象**:审批框显示 `[REDACTED SENSITIVE REVIEW]`,安全原因 `Command review is unavailable.`,只有"拒绝"一个按钮。

**根因**:`permission_approval.py:569`

```python
preview, command_redacted = _redact_review_text(preview, workspace=workspace)
if command_redacted or reason_redacted:
    preview = _REDACTED_REVIEW          # 脱敏成功了，却还是整条丢掉
```

`_redact_review_text` 只要把工作区路径或 home 路径替换成 `[LOCAL_PATH]` 就返回 `changed=True`。逻辑自相矛盾:**脱敏的目的是让内容变得可安全展示,结果脱敏成功反而什么都不给看**。

**后果**:agent 只要在命令里写绝对路径(最自然的写法)就被墙住 → 工具反复失败/超时 → 这些失败又被记成 error_pattern。**它是后面一半噪音的源头。**

**方案(实施时修正)**:最初想的"脱敏成功就展示"不够——前端要求 `complete && !redacted` 才允许批准,而 `_command_review_is_unsafe` 更早就用 `_contains_local_absolute_path` 把整条判死了。最终做法:**工作区内的绝对路径改写成相对路径,不算脱敏**;工作区外的照旧。改动只作用于命令审批,文件差异路径保持严格(`rewrite_workspace` 参数)。

**安全权衡**:改后 `python -m pytest [LOCAL_PATH]/tests -q` 这类预览可见。具体路径仍被替换,泄漏的只有"存在某个本地路径"这一事实。**收益是你能看清再决定批不批,而不是被迫拒绝**——对安全是净提升。`_command_review_is_unsafe` 对真正危险的命令形态(复杂 shell、敏感值)的整条隐藏保持不变。

**回退**:`_command_review_is_unsafe` 里把 `local` 换回 `structured_values`。

**动了一条锁定测试**:`test_command_review_never_serializes_local_absolute_paths` 原本把"工作区内绝对路径"也断言为不可审查。该测试真正保证的安全属性是"序列化结果不含本地绝对路径",改写后仍然成立;我把这个用例拆成独立测试 `test_an_in_workspace_absolute_path_stays_reviewable`,保留安全断言、更新预期行为。

- [x] 修复
- [x] 测试(含反向:脱敏失败时仍整条隐藏)

---

## P1 — 任务描述串台(收益最大)

**现象**:跑 pytest 的任务,记忆写着 `Task Context: 你能用网络搜索小红是谁嘛？`

**根因**:`agent_loop.py:283`

```python
def _extract_task_description(messages):
    for msg in messages:          # 正向遍历
        if msg.get("role") == "user" ...:
            return content[:500]  # 返回【第一条】用户消息
```

取的是会话**最早**那条用户消息,不是当前任务。

**后果**:多轮会话里**每条记忆都被贴上会话首个提示词的标签**。检索按这段文字匹配,所以记忆归错档、按真实主题永远搜不到。`applies_when` 也跟着变成 `When 你能用网络搜索小红是谁嘛？.`

**方案**:反向遍历,取**最后一条**真实用户消息(仍跳过 `Continue` / `Your last` 这类续跑桩)。

- [x] 修复
- [x] 测试

---

## P2 — 成功输出被判成错误

**现象**:
```
Observed error pattern for test_runner / ToolError: ⊘ Skipped: 0
Observed error pattern for test_runner / ToolError: ✓ [::unknow
```

**根因(诊断修正)**:原以为是"成功被判成错误"——**错了**。`is_error = not result.ok` 是对的,test_runner 确实失败了(有测试没过)。真实缺陷是 `_salient_line` **不认识这个工具的输出格式**:它的失败标记是 `✗ Failed:` / `❌ Failures:`,而正则只认 `FAILED`/`ERROR`,匹配不到就退回"取最后一行",抓到了计数行和被截断的通过项。P2 与 P4 实为同一问题,合并处理。

**方案**:失败标记(`✗❌⚠`)纳入 salient 正则;成功/装饰行(`✓⊘📊📈🧪`)整行排除;全是成功行时返回空,让 claim 降级或不产生,而不是引用一个成功行当失败信号。

- [x] 定位
- [x] 修复
- [x] 测试

---

## P3 — 工具故障漏网(超时)

**现象**:`Tool 'run_command' timed out after 120s` / `Tool 'task' timed out after 120s` 被记成项目知识。

**根因**:`_TOOLING_FAULT_RE` 只匹配 `error[tool_crashed]` 和 `error[sub_agent_depth_exceeded]`,超时是另一种写法,漏了。

**方案**:把超时纳入同一类。工具超时描述的是 agent 自身运行状况,不是被编辑的项目。

- [x] 修复
- [x] 测试

---

## P4 — 截断碎片(第二种形态)

**现象**:`✓ [::unknow` —— `[::unknown]` 被切断。

**根因**:上一轮修的是 ANSI 残片;这是普通文本在字符上限处被切断,`_salient_line` 没有识别"最后一行是半截"的规则。

**方案**:末行明显被截断时(尾部是半个词/半个括号结构),不作为 salient line 采用。

- [x] 修复
- [x] 测试

---

## P5 — decision claim 收录模型碎碎念

**现象**:
```
Type: decision
Statement: Crucial finding: **"collected 154 items"** — that's the actual total...
           So this means either: 1. The user's reported failure already got fixed...
```

思考过程被整段当成"决策",带 markdown、编号猜测、either/or。

**根因**:`choice = text` —— 整段 assistant 文本直接当 statement。另外合并路径 `existing["statement"] = f"{...} {text}"` 也追加整段,**这才是之前那条臃肿 constraint 的真正来源**(上一轮修的是另一处)。

**方案**:只取包含触发词的那一句;去 markdown;推测句(either/maybe/可能…)不算决策,全是推测则不产生 decision。分句规则修正两处:中文 `。` 后无空格、枚举标记 `1.` 不得断句。

- [x] 修复
- [x] 测试

---

## 验收标准

全部完成后,执行一次真实端到端:

1. 在本仓库制造一个失败测试
2. 多轮会话:先问一个无关问题,再让 agent 修这个测试(复现 P1 场景)
3. 检查产出的记忆必须满足:
   - Task Context 是**修测试**那条,不是无关问题
   - 有 `recovery` 或 `root_cause` claim,带 `verified_solution` 信号
   - 无 ANSI、无 `truncated]`、无 `[::unknow` 类碎片、无 `[REDACTED]` 误伤
   - 无 `tool_crashed` / 超时类 claim
   - `applies_when` 命名具体制品(测试节点 id 或文件路径)
4. 全量测试套件通过

---

## 本次明确不做

- **检索打分器吃文本长度** —— 修的是垃圾来源,不是打分器。属于独立改进。
- **`correction` 通道无 emitter** —— 需要新增用户纠正识别,是新功能不是修 bug。
- **`AutoModeChecker` 未接线** —— 放宽安全默认值,需你单独决定。
- **每条记忆需人工批准** —— 你设计的安全闸门,保持不变。


---

## 验收结果(真实 agent 跑出,DeepSeek)

多轮会话:先问"你能用网络搜索小红是谁嘛？",再让 agent 修一个失败的 pytest。产出:

```
Task Context: 跑 python -m pytest tests/ -q，有测试失败。诊断原因，修改源码，重跑直到通过。

Claim:
  Type: recovery
  Statement: After FAILED tests/test_renew.py::test_renew_after_transfer -
             leasekit.lease.StaleTokenError: the fencing token was not refreshed
             before, the recovery action was: Changed src/leasekit/lease.py,
             after which run_command succeeded on python.
  Applies when: When run_command fails on tests/test_renew.py::test_renew_after_transfer
                with StaleTokenError.
  Verification: verify-000003

signals: ['confirmed_dependency', 'confirmed_error_recovery_verified', 'verified_solution']
```

| 验收标准 | 结果 |
|---|---|
| ① Task Context 是当前任务,不是会话首个提问 | ✅ |
| ② 有 recovery/root_cause + `verified_solution` | ✅ |
| ③ 无 ANSI / `truncated]` / 碎片 / 误伤 `[REDACTED]` | ✅ |
| ④ 无 `tool_crashed` / 超时类 claim | ✅ |
| ⑤ `applies_when` 命名具体制品 | ✅ |
| ⑥ 全量测试通过 | ✅ 3711 passed |

### 验收中发现并补修的一项

⑤ 首轮未通过:`applies_when` 只有 `lease.transfer`。原因是 `agent_loop._redact_trace_text` 在 **trace 层**就按 500 字符**头部**截断,pytest 的 `FAILED tests/...` 摘要在末尾早被切掉——证据层的中段省略修复够不着它。已把两层的截断策略统一到 `bound_keeping_both_ends`,重跑后 ⑤ 通过。
