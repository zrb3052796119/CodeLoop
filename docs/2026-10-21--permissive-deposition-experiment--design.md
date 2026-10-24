# Permissive Deposition Experiment — Design & Rollback

- **日期**: 2026-10-21
- **状态**: 代码落地，默认关闭（strict）
- **动机**: 2026-10-21 记忆模块审查发现：写路径把 ReflectionValueGate 拒绝的 claim 全部丢弃——即使 claim validator 已确认其内容属实（单次观测错误、环境错误、无 durable signal 三类）。而 2026-08/09 的污染实验实测：注入噪声的代价是 token 开销而非任务失败，被丢弃的好教训却不可恢复。成本不对称，写入门控方向错了。
- **假设**: 把 value-gate 拒绝的 validator-clean claim 沉淀为 draft 而不是丢弃，能提高真实沉淀量，且不损害任务质量（读侧相关性门 + PID 预算继续兜底）。

## 开关

```bash
export MINI_CODE_MEMORY_DEPOSITION_MODE=strict   # 默认，行为与实验前完全一致
export MINI_CODE_MEMORY_DEPOSITION_MODE=capture  # 拒绝的 claim 写为 pending draft（仅入审批队列，永不可注入）
export MINI_CODE_MEMORY_DEPOSITION_MODE=inject   # draft 额外自动批准、可检索，TTL 过期后检索层自动跳过
export MINI_CODE_MEMORY_DEPOSITION_DRAFT_TTL_DAYS=14  # inject 档 TTL，默认 14 天
```

## 改动清单（全部为增量，strict 路径零改动）

| 文件 | 改动 |
|---|---|
| `minicode/memory_deposition_experiment.py` | 新模块：模式解析（fail-closed 到 strict）、TTL、draft 元数据、过期判定 |
| `minicode/memory_pipeline.py` | `write_result` 丢弃点接入 `_commit_deposition_drafts`：独立确定性 operation_id（`sha256(primary)[:32]`），走既有 `commit_reflection_batch` 契约；失败仅记日志，绝不影响主写路径；主 outcome 的 reason_codes / payload digest **不变** |
| `minicode/memory_retrieval.py` | `_active_entries` 增加 `draft_entry_retrievable` 过滤（过期 inject-draft 不进语料；无后台 sweeper 需求） |
| `tests/test_memory_deposition_experiment.py` | 9 个测试：模式 fail-closed、operation_id 确定、TTL、strict 回归、capture/inject 行为、过期 |

## 各档位语义

- **capture**: draft 为 `approval_status=pending`（复用 builder 的 USER_REVIEW_REQUIRED），`is_active=False`，物理上不可注入；进入审批队列供人工晋升。
- **inject**: draft 翻转为 `AUTO_APPROVE_VERIFIED` → approved + active，可检索；metadata 带 `deposition_draft.{reason_codes, expires_at, injectable}`；TTL 过期由检索层过滤（条目仍在盘上，惰性失效）。
- 两档都**只收 validator 通过但 value gate 拒绝的 claim**——内容真实性与 groundedness 已验证，被质疑的只是"是否值得长期记住"。`no_valid_claim`（无任何合法 claim）与 trace 安全扫描仍按原逻辑。

## 回退步骤

1. `unset MINI_CODE_MEMORY_DEPOSITION_MODE`（或设回 `strict`）——下一个写操作起行为与实验前逐字节一致。
2. 已落盘的 draft 条目：capture 档本就 pending 不可注入，无需处理；inject 档到期后自动退出语料。如需彻底清理，删除 `metadata.deposition_draft` 非空的条目即可。
3. 代码回退（如需）：revert 本文件列出的 4 个文件的改动；strict 模式下它们是纯增量，revert 无数据迁移问题。

## 观测

- 日志关键字：`MemoryPipeline: deposition draft committed mode=...`（成功）、`deposition draft commit failed`（副作用失败，主路径不受影响）。
- 产出指标（后续补 dashboard）：每会话 draft 数、capture→active 晋升率、inject draft 注入命中率、TTL 过期占比。

## 已知边界（v1 有意不做）

- prompt 渲染不给 draft 加"低置信"标记（渲染契约有确定性测试，改动留到有数据之后）；
- conversation fact 的正则黑名单未放宽；
- Curator 晋升/降级自动化未接——capture 档目前靠人工审批队列。

## 增量 v2（同日）：环境类错误合成召回

管线级实测发现更大的上游瓶颈：connection refused 等环境类错误在**规则合成器**就被整体跳过（`reflection_synthesis.py` 的 `_environment_scoped_error` → `continue`），连 claim 都不产出，draft 层救不到。

**改动**：`RuleReflectionSynthesizer.synthesize` 中该跳过点改为条件化——`strict` 模式行为逐字不变；非 strict 模式下环境错误正常生成 `error_pattern` claim（附加 limitation"Environment-scoped failure"），随后被 value gate 以 `environment_scoped_error_pattern` 拒绝，再由实验管线落为 draft。设计理由：被拦的大多数确实是"废话记忆"，但其中混着"本项目必须先启动本地服务"这类真正值钱的工作流教训——draft 的短 TTL 正好把两者都接住、让垃圾自己过期。

**实测**（`pipeline_probe.py`，`env_connection` 场景）：strict 0 条（不变）；capture 1 draft；inject 1 draft 且检索命中。`tests/test_memory_deposition_experiment.py` 增至 13 个用例，reflection 系列 132 个既有测试全过。
