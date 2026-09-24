# 第二轮修复复核：共享验证器

离线入口：`PYTHONPATH=. python3 artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py`。
脚本只构造 `TaskEvidence`、调用真实 `parse_llm_candidate`、validator、value gate、rule synthesizer；不调用模型或写正式记忆。输出见 `grounding_probe.json`。

## 1. F3 的新目标核对没有应用 cwd，仍可误确认另一目录的测试

- 定位：`minicode/reflection_synthesis.py:531-537`，关键行 534。
- 失败调用：`pytest tests/test_auth.py (cwd: /repo/backend)`；通过调用：`pytest tests/test_auth.py (cwd: /repo/frontend)`。
- `_invocation_path_resources()` 会剥掉 `(cwd: ...)`；调用 `_workspace_paths_equivalent()` 时又没有传 `failed_cwd` / `successful_cwd`。于是两边都进入 `rel:tests/test_auth.py`，错误相等。
- 探针 `different_cwd`：真实 parser + validator 接受 `confirmed` 恢复，value gate `persistable=true`；规则也产生 `confirmed`。删掉 earlier failed check 后变为拒绝，说明误确认正来自红绿分支，而非文本重叠或 full-suite fallback。
- 同一个疏漏还造成反向误拒绝：`pytest /repo/backend/tests/test_auth.py` 与 `pytest tests/test_auth.py (cwd: /repo/backend)` 实际同一目标，却被降为 inferred。
- 同函数还会接受 `pytest tests/test_auth.py → pytest smoke`（单侧无多组件路径便退回执行器名）、`auth.py + smoke.py → smoke.py`（任一文件相交）、`test_auth.py::test_expired → test_auth.py::test_valid`（node selector 丢失）。可与主审的“目标身份仍不完整”合并，避免重复列问题。

建议：共享一个保留 cwd、目标集合与具体 selector 的验证目标标识；不要让这个 validator 分支重新从有损路径投影推断身份。未知目标关系应拒绝确认。

## 2. 红绿检查尚未与 recovery 所关联的错误相连

- 定位：`minicode/reflection_synthesis.py:571-581`，关键行 580。
- 场景：auth 出错，另一个 billing 测试也失败；recovery 明确只关联 auth 的 error；之后 billing 同目标重跑通过。
- 当前循环会扫描全部 earlier failed 同 kind 检查，只要 billing 的 failed/pass 目标匹配，就把 auth recovery 确认为已验证；没有核对 failed check 的 `call_id` 是否来自 `recovery.related_error_ids` 指向的错误调用。
- 探针 `unrelated_failed_check`：recovery 的错误 call 是 `auth-failure`，红绿检查 call 是另一个 `failed-call`；validator 接受、value gate `persistable=true`、规则 `confirmed`。移除不相干的 billing failed check 便拒绝。

建议：红绿的 red side 先限定在 recovery 的真实 error call / source event 关联集合；再核对通过侧的目标和时序。

## 已闭合与未当作新漏洞的部分

- F1 按引用继承 decision 状态的代码已闭合原先“真实摘录 + 改写升级 inferred”路径；主审已复验原探针。
- F7 invocation 同时进入 parser 的可摘录来源、validator 的 event-keyed 来源与 tool event type 索引，原先合同错位有对应修复。
- 未把作者已声明的“真实摘录不证明自由正文全部语义结论”当作新的程序漏洞。本报告反例使用原句拼接，且没有 `evidence_quotes`，独立于语义泛化限制。
