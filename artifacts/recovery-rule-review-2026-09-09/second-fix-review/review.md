# 第二轮修复复核

2026-09-09。审查当前源码及 implementation.md 文末的第二轮修复。本次未修改生产代码或原报告；探针使用合成轨迹/脚本响应，无真实模型调用、命令执行或正式记忆写入。

## 结论

上一轮三组探针已按预期变化；独立重跑 16 个相关模块得到 **350 passed in 3.27s**。F1、F6、F7 与已检查的计数/结果修复有效。但 F2–F5 仍有具体反例，尚不足以宣布全部问题闭合或启用生产学习。

| 原问题 | 本次核验 |
|---|---|
| F1 摘录升级证据状态 | 原 decision、root_cause 及生产 gap_fill 探针均正确拒绝；未发现新的实质问题 |
| F2 目标身份与选择范围 | 原四反例关闭；新增 marker、inline cd、输出文件、missing fixture 场景仍错误确认 |
| F3 红绿关系 | 原 auth/billing 简单反例关闭；cwd 丢失及借用另一错误的红绿链仍成立 |
| F4 candidate followup | git status 不再抢占；共用 python 的无关调用仍可占满配额 |
| F5 投影证据完整性 | 原单失败场景 parent/sub 都通过；四个无关失败可挤掉真正 red side |
| F6 结果 ID 和逐片段结果 | 两片段 ID 唯一、逐片段 persistable_claims 正确填充 |
| F7 invocation 摘录 | 测试同时验证 parser 与 validator 接受真实输入引用 |
| 调用计数/observations | 预算拒绝计 0，非持久化 observation 被保留；未发现新的实质问题 |

新 Agent 仍未接线、真实模型 shadow 评估仍未开展，实施记录对此披露准确。下述问题均能在离线程序行为中证明，与已披露的任意正文语义推广限制不同。

## 需要继续修正的发现

### 1. [P1] 排除 -m 造成误确认，而非保守漏报

位置：[reflection_evidence.py:867](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:867>)、[reflection_evidence.py:2536](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2536>)。

`pytest tests/test_auth.py -m=expired` 失败 → 编辑 README → `pytest tests/test_auth.py -m=valid` 成功，仍生成 confirmed 的 README 修复经验并通过 value gate。`-m` 的忽略使验证范围变化消失。

`python -m pytest` 的解释器参数和 pytest 的 marker 参数需要按命令位置/程序语义区分；无法判定时应将匹配结果保留为 unknown/candidate，不能把被忽略的选择参数视为范围相同。当前注释还把 `--lf` 描述为展示参数，而该选项会改变执行的测试集合，说明“展示与选择”的分类本身需核验。

### 2. [P1] inline cd 与共同输出路径仍能成为同一测试的证明

位置：[reflection_evidence.py:895](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:895>)、[reflection_evidence.py:921](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:921>)、[reflection_evidence.py:966](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:966>)。

两项真实工具输入格式的合成轨迹均得到 confirmed/persistable recovery：

- `bash -lc 'cd /repo/workspace && pytest tests/test_auth.py'` 失败，随后相同 cd 下 billing 测试通过。`/repo/workspace` 既被作为锚，又仍留在可交集资源中。
- auth 与 billing 测试都指定 `--junitxml=/tmp/results.xml`。共同报告输出被当成共同业务目标。

结构化 cwd 被剥离是有效修复，但对整条调用做路径正则提取仍丢失参数角色。所有绝对路径及其父目录也不能自动当作实际 cwd：测试文件、配置和输出文件都可能包含绝对路径。

建议将 cwd/真实 cd、输入目标、输出/配置路径分别表示；仅在目标身份及验证范围成立时确认。对任意 shell 包装无法解析的部分保留候选关系。

### 3. [P1] missing-target 例外未证明缺失对象就是请求目标

位置：[reflection_evidence.py:989](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:989>)、[reflection_evidence.py:2190](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2190>)。

复现：运行已有 `tests/test_auth.py`，因缺少 `fixtures/credentials.json` 抛 FileNotFoundError；改跑 `backend/tests/test_auth.py` 成功。新例外把它记录为 confirmed 的路径修正。

`_is_missing_target_failure()` 只在错误消息中匹配“文件不存在”，未将缺失路径与请求目标绑定。FileNotFoundError 可能来自目标代码读取的 fixture、配置或依赖文件，不意味着测试文件本身不存在。

read_file 的显式缺失目标纠正可以保留，但需要工具提供的缺失对象身份或可核验的精确路径关系；仅凭异常类别和加前缀后缀匹配不足以确认迁移。

### 4. [P1] F3 的共享验证仍未形成同一条错误—恢复—验证链

位置：[reflection_synthesis.py:534](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:534>)、[reflection_synthesis.py:571](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:571>)。

`_verification_target_matches()` 没有将 invocation 中的 cwd 传给 `_workspace_paths_equivalent()`。因此 `tests/test_auth.py` 分别位于 `/repo/backend` 和 `/repo/frontend` 时，红绿路径仍确认成功；反向的同目标绝对/相对写法则可能被拒绝。

更直接的关系漏洞：红绿搜索扫描所有早期 failed checks，不要求该失败 call 属于 `recovery.related_error_ids`。仅关联 auth 错误的恢复，可以借用另一个 billing 错误的“失败 → 通过”来获得 confirmed。原句候选经 parser、validator、value gate 都通过，规则生成器也得到 confirmed；移除 billing 的 failed check 后则拒绝。

探针还覆盖同文件不同 `::test_node`、从多目标中移除失败文件、单侧无路径后按执行器名回退等误确认。修正应统一使用同一目标身份实现，并先从该 recovery 关联的错误 call 找 red side，再核对 green side。单纯检查任意一对目标相似的红绿调用不足以验证本次恢复。

### 5. [P2] F4 仍允许无关调用占满片段

位置：[recovery_lesson.py:288](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:288>)。

报告脚本 timeout:1 失败后，依次成功运行 `python scripts/cleanup.py`、`python scripts/lint.py`，最后真正重试报告脚本 timeout:120。所有调用共享解释器 token `python`，前两个无关成功占满 `[:2]`，真实重试被完全丢弃。

这仍是原 F4 的边界：从“同工具名”改成“任意共词”没有建立操作对象关系。应先匹配实际脚本/命令操作目标，再应用片段数量上限；不应让噪声事件的先后决定保留哪条恢复。

### 6. [P2] F5 仍可能在找到 red side 之前截断证据

位置：[recovery_lesson.py:350](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:350>)。

在原有效 auth 红绿场景之前加入四次其他文件的 pytest 失败。`_episode_failed_check_context()` 先按相同 kind 取最早四次失败，然后停止；第五次真正的 auth 失败未进入子证据。

结果：episode 仍标记 verified，同一候选 `parent_valid=1`、`episode_valid=0`，报 `confirmed_recovery_verification_unrelated`。

应先按 passing check 的目标及 recovery 的关联错误确定必需失败节点，再对额外上下文限量。验收需要增加无关事件数量/顺序变化后，核心证据仍保留的检查。

## 下一步建议

保留已验证有效的 F1、F6、F7 修复。剩余工作应围绕统一的操作对象与验证范围身份收敛：它要区分 cwd、目标、选择器和输出，并绑定 error/recovery/check 的 call IDs；无法可靠建立的关系交给子 Agent 作为 candidate 分析，程序不提前授予 confirmed。

在规则抽取、共享 validator 和 episode 筛选中复用同一个身份/关系合同。片段限额在关系筛选之后应用。这样才能避免每补一种命令写法，就在另一条路径引入不同的确认标准。

## 证据与验证范围

本次只重跑所列相关测试面，未重跑用户所述全部 589+172 项，也未以全量收集结果替代执行结果。350 项测试通过，说明原用例得到覆盖；本报告新增边界探针仍暴露上述程序行为。

- [目标识别探针](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/identity_probe.py>) / [结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/identity_probe.json>)：marker、inline cd、共同输出文件、missing fixture 四项均错误确认。
- [共享验证探针](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py>) / [结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.json>)：包含正确/错误目标对照及删除早期失败检查对照。
- [片段证据探针](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/episode_probe.py>) / [结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/second-fix-review/episode_probe.json>)：同解释器噪声、失败上下文配额。
- 上一轮探针重跑结果另存为 `previous_identity_probe.json`、`previous_quote_probe.json`、`previous_episode_probe.txt`，保留历史报告不覆盖。

所有新增探针均已由主审独立重跑。这里 persistable 指通过反思/validator/value gate；没有执行正式存储提交，不能据此声称用户记忆已被污染。
