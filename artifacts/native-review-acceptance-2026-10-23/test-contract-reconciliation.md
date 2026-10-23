# Completion review 测试契约校正（非冻结验收材料）

本组只修改 8 个既有测试文件。原 15 个失败 node 在原八格冻结 `run-v1/execution-host.zip` 的修前 production 上全部复现：**15 failed / 15.99s**。修正后的同 8 文件，在当前代码上 **38 passed / 45.63s**，在同一修前 production 上 **38 passed / 53.80s**。因此这 15 项属于既存 fixture / 证据协议脱节，并非 R1/R2 引入的回归。

所有执行为本地 pytest；探针实际执行、原生工具实际调用，provider 边界使用测试 fake transport；没有公网或付费模型调用、skip、xfail 或生产代码修改。

## 逐文件原因与保留的验证

| 测试文件（tests/） | 原失败数 | 校正及保留的约束 |
| --- | ---: | --- |
| test_completion_assertion_correction_authority.py | 1 | 自定义 auditor 原只读 probe source，未读独立 invalidity material。先证明 submit 被拒、未退休旧 probe，再通过真实 review_evidence_read 全分页读取绑定的 supersession_review_material 后提交。保留 ordinary reviewer 不得自授 assertion-input correction、独立裁决、原断言、源文件不变与恢复认证。 |
| test_completion_coverage_audit.py | 1 | 普通 final 只产生 interrupted pending 状态，不产生已完成 decision 的 authorityRef。改从实际 audit request 中的 authorityRef 打开真实持久化记录，并核 saved authority 身份与 proposalDigest；继续要求 inconclusive、auditPending、完整历史已保存但未在初始 prompt 重复。 |
| test_completion_evidence_cursor.py | 1 | 原断言假定已被替代的 factored obligation / mandatory raw receipt dependency tree。现契约是完整 source + independent authority 的自包含 material，原 execution traces 可选。测试先读首个不完整页并证明仍拒绝、伪造 cursor+offset 被拒，再读取完整多页且核 SHA、old/new/effective source 完全相等、完整 authority 与 original obligation digest 绑定；仅此后允许批准。原始档案 source 字节、不可变产品、真实三次 pytest 与恢复认证仍保留。测试名改为 test_supersession_needs_complete_sources_and_independent_authority_material。 |
| test_completion_exception_attribution.py | 4 | 精确 adjudicator 工具集合漏列既有只读 completion_evidence_read。只补该工具名，仍精确限制集合；真实异常归因、修复后复测、无结论不得误导 writer 等原断言不变。 |
| test_completion_expression_correction.py | 2 | 同上，仅工具集合加入 display-only reader；独立表达式纠正、执行、审批、恢复原断言不变。 |
| test_completion_fixture_keywords.py | 1 | 同上，仅工具集合加入 display-only reader；fixture keyword 修复后真实执行与原断言保护不变。 |
| test_completion_review_evidence_delivery.py | 2 | review_test 与 review_probe_read 返回模型可读 display view；旧 fixture 把摘要与完整 receipt 比较。改沿实际 documentRef/candidateDigest 用 completion_evidence_read 读至 nextCursor=null，核 originalDigest 与 wrapper identity 后比较完整原文。仍证明 history 与 current 分离、旧 candidate receipt 不冒充当前、错误 view 拒绝；display reader 明确不产生 completeDelivered 权限。 |
| test_completion_review_provider_tools.py | 3 | 真实 OpenAI adapter fake provider 原只支持 review/audit 两腿，遗漏独立 inspection 和 display reader。补执行 inspection 的真实 read_file + native submit，按真实 tool_call_id 选择 audit probe read 响应并支持 cursor。精确验证 review→inspection→audit 六次请求的三套 registry、原生 submit schemas、无父写工具 / tool_choice 泄漏、原 adapter cache 不变，产品不变且最终 approved。 |

## 复现材料

- 修前基准：`/tmp/native-review-before-x1x4ue1e`（root 从原冻结 host 解包，原测试拷贝）。
- 原失败 node 清单：`contract-reconciliation-nodes.json`；旧 production 原测试结果：`contract-reconciliation-before.txt`。
- 当前 production 最终 8 文件结果：`contract-reconciliation-after.txt`。
- 修前 production + 最终测试隔离 overlay：`contract-reconciliation-old-production.json` 记录 cwd、完整命令、冻结 archive SHA、全部 minicode Python 文件逐字节相等验证，以及 8 文件修改前后 SHA；结果见 `contract-reconciliation-old-production.txt`。未改变共享修前 baseline。
- 初次修正结果 `contract-reconciliation-after-first.txt` 为 36 pass / 2 fail；其 history-reader 摘要对原文差异保留在 `contract-reconciliation-display-diff.txt`，最终已通过实际读取 reader 顶层原文解决。
- `git diff --check` 对这 8 文件通过。该组已交 root 做完整审查域联合回归，之后不再改测试。

## 执行命令

解释器统一为原运行器的 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`。修前先用 `-m pytest -q` 加 JSON 所列 15 个 node；最终当前/修前均用以下 8 文件（完整 argv 同机器记录）：

```text
-m pytest -q tests/test_completion_assertion_correction_authority.py tests/test_completion_coverage_audit.py tests/test_completion_evidence_cursor.py tests/test_completion_exception_attribution.py tests/test_completion_expression_correction.py tests/test_completion_fixture_keywords.py tests/test_completion_review_evidence_delivery.py tests/test_completion_review_provider_tools.py
```
