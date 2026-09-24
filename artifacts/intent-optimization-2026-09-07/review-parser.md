# Parser 独立只读审查

**最终结论：初审发现的五项新增问题均已修复；固定范围 16 条复核全部通过，包括原 50 题 C36/C37。没有剩余可确认的本轮新增阻断项。**

审查范围：本轮开始时保存的 `.repair-work/intent-phase1/baseline/minicode/{intent_parser.py,intent_negation.py}` 与当前文件。未修改生产代码，未读取或运行 `holdout.json`。采用自行构造的指令与只返回固定写提议的 synthetic adapter；未调用真实 LLM。以下定位对应本次审查快照，作者仍可能更新文件。

## 初审发现（均已完成修复）

### [P1] 新增中文记忆动词仍需区分命令与被讨论对象

位置：`minicode/intent_parser.py:2121`（新增裸 `记住|记下` 模式）。

复现：

- `请审查记住密码功能` → `memory/create/affirmed`，语义 fallback 不调用。
- `请解释记下来的信息` → `memory/create/affirmed`，语义 fallback 不调用。
- `记住密码的代码在哪里？` → `memory/create/affirmed`。

前两例在基线均无 affirmed memory 操作，能继续进入未知请求的语义兜底；当前直接给出未请求的记忆写入意图，与本轮“讨论动作不能变成操作”的目标冲突。应要求当前动词由用户命令支配，并排除 `记下来的…` 定语和“审查/解释/查找…记住密码…”任务对象。保留真实 `记住默认使用 Python`、`别记下这段对话` 的正反向控制。

### [P2] 假设前提吞掉逗号后的明确真实命令

位置：`minicode/intent_parser.py:4657–4677`（hypothetical 判断）。

复现：`假设项目使用 Python，请为它创建单元测试。`

- 基线：affirmed `test/create`。
- 当前：无 operations；`_semantic_fallback_target(...)` 返回 `None`，LLM 无法补漏。

同类：`假设测试通过，请修复 auth.py`、`Suppose tests pass, please delete temp.py.`。代码只接受 `就/则/便/那么` 和一组时序重启词，未识别逗号后的 `请…`/`please…` 独立命令。应把前提与明确请求拆开或识别有句法边界的命令重启，不能仅继续扩充假设词黑名单。也需要维持 `假设有人现在删除 api.py` 仍是被讨论事件的反例。

### [P2] 同一 clause 的事故描述让未知恢复请求失去 LLM 入口

位置：`minicode/intent_parser.py:8495–8505`（按所有已识别候选判定整个 clause 非指令）。

复现：`误删了 auth.py，帮我恢复` → 当前无 operations、`_semantic_fallback_target(...) is None`，synthetic adapter 的请求列表为空。

`删除` 是已识别的事故动词，`恢复` 没有已识别候选；`all(...)` 因只看已识别候选而把整个文本当作事故描述，吞掉真正需要语义理解的尾句。这是新增门禁带来的真实任务漏识别。应仅阻断事故片段，把独立的 `帮我恢复` 尾句作为未覆盖请求交给 LLM，并且保留 `auth.py` 的安全目标上下文。

### [P2] 裸“撤回/收回”否定词误伤业务功能名称

位置：`minicode/intent_negation.py:178`（新增无语法边界的 `撤回|收回`）。

复现：`请审查撤回消息的代码` 在基线得到 affirmed `review/analyze`，当前无 operations；`请审查收回权限的代码`、`解释撤回消息的代码` 同样受影响。虽然 `_semantic_fallback_target` 返回全句，`parse` 的 `unresolved_negation` 会随后阻断语义调用。

这里“撤回消息/收回权限”是被审查的业务功能，不是在撤回审查请求。新增否定词应与“撤回刚才让你写测试的请求”区别，依据其与动作/请求的支配关系或明确名词语境限定，而不应将文本里任何一次出现都解释为撤回。

### [P2] 文档内容继承覆盖明确的独立代码目标

位置：`minicode/intent_parser.py:6545–6568`（`_DOCUMENT_CONTENT_CONTINUATION_RE` 命中后直接替换 `pattern_candidates`）。

复现：`更新 README.md，添加 utils.py 中的函数来输出文件内容`。基线第二操作为 affirmed `code/create`，目标 `utils.py`；当前第二操作为 `document/update`，但仍带 `utils.py` 目标。`更新 README.md，添加函数打印日志内容` 也将明确添加函数误分为文档更新。

新规则只检查前一操作为文档更新、当前句尾带“内容/步骤/说明”等词，就覆盖所有候选；它没有排除当前句出现明确的新非文档文件、函数或接口任务对象。应把继承限定在文档内容补语，对明确独立代码对象保留其动作候选。

## 初审时发现的额外边界缺口（亦已修复）

`_semantic_clause_is_non_instruction` 依赖 `candidates` 非空，假设里的同义表述未被规则识别时会重新进入 LLM：`假设 api.py 消失了`、`假设有人把 api.py 清掉了` 都被完整发送给 synthetic adapter，并接受其 affirmed `code/update` 提议。这个行为在旧版也可能存在，因此不作为新增回归；但它说明当前门禁只覆盖规则认识的假设动词，尚不能概括为“假设/引用均不会由兜底重新变成命令”。建议在此次修复中补测，或明确记录限制。

## 其他检查

- 本轮的否定句句界 reset 没有暴露独立问题；裸否定词新增引入了上述业务名词回归。
- 真实 `先审查代码并删除未使用的函数` 与 `如果测试通过，请删除临时文件` 保持 affirmed 后续动作。
- 文档补充内容继承修复原用例，但显式非文档目标有上述新增回归。
- 上述 findings 已发给主 agent 与 parser 作者，并在作者修复后完成固定范围复核。

## 作者修复后复核（范围固定）

对原五组样例及原 50 题 C36/C37 重新使用实际 parser 和固定写提议的 synthetic adapter 检查，没有添加新样本：

| 原问题 | 复核结果 |
|---|---|
| 记住/记下作为被讨论对象 | 已修复。`请审查记住密码功能`、`请解释记下来的信息`、`记住密码的代码在哪里？` 均不再产生规则 memory 写入。 |
| 假设前提吞真实命令 | 已修复。Python 项目创建测试、测试后修复 auth.py、英文 please delete 均保留各自真实操作。 |
| 事故后未知恢复请求失去 fallback | 已修复入口。模型只收到 `帮我恢复`，不会收到误删命令。 |
| 撤回/收回业务名词 | 已修复。审查和解释保持 affirmed/read-only。 |
| 文档继承覆盖独立代码目标 | 已修复。`utils.py` 函数添加保留 code/create，与 README 的 document/update 分开。 |
| 原 50 题 C36/C37 | 两题均跳过 semantic adapter，保留 explain/read 与 review/analyze，无写操作。 |
| 纯假设但无规则候选 | 原报告两个样例均不再调用 semantic adapter。 |

复核保留的既有局限：未知的“审查记住密码功能/解释记下来的信息”仍依赖 LLM，synthetic 错误写提议仍可被接受，这与旧版未知请求通路一致；`帮我恢复` 的 semantic 请求与操作不继承事故描述中的 auth.py 文件实体。两者属于后续语义复核/上下文结构路线，不视为本轮新增回归。

## 初审快照

- `minicode/intent_parser.py` SHA-256: `207a8b56b8169591dc3ef070ef8055ef0622a2d68c8124e6c718150ddcb4fb7e`
- `minicode/intent_negation.py` SHA-256: `12ece6a4cd9b4103540a978a31bb4aa6b4293bf95aebfb44c348546aaf6f07b2`

## 最终复核快照

固定范围：初审五组共 12 条原样例、候选为空的纯假设 2 条、原 50 题 C36/C37，共 **16 条全部通过**。仅实际 parser 与 synthetic adapter，无真实模型调用。

- `minicode/intent_parser.py` SHA-256: `cf22233c0ce4d813fef3d62d9aee6f299da303be3c32996522431278ad44e5cf`
- `minicode/intent_negation.py` SHA-256: `5d9f4cddec17ed05440566970fba2b1c406fcf8e28aff6b1b9321a5a641bd94e`
