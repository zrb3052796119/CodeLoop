**合成 Skill 目录实物清单**

这份清单直接扫描上一轮保留下来的 E01 测试工作区，没有重新生成目录。实际存在 1000 个 SKILL.md、62 个 SKILL_DIR.md；原 run.json 的 actual_catalog_count 也是 1000。

文件位于项目内隐藏的 .repair-work/.../workspace/.mini-code/skills 中，每道题使用独立测试工作区；没有安装到主项目或个人的正式 Skill 库。

构成：15 个基础测试流程＋4 个新增测试流程＋12 个特制近邻干扰条目＋969 个批量模板干扰条目＝1000。全部都是合成测试材料；目标流程也围绕样例项目编写，不是收集到的成熟第三方技能。

这里将独立的、含前置元数据和指令正文、能被实际 Skill 注册表加载的 SKILL.md 算作一个条目。数量不代表独立能力数，也不代表技能质量。大多数干扰正文只有通用范围检查；缺少真实脚本、配套资源及领域深度。

模板通过 50 个业务领域与 20 类操作按固定索引组合，前 12 个位置替换成特制近邻项。基础目录共 12 个，加 50 个领域目录形成 62 个一级 Directory；新增技能放入已有目录并补充目录描述。

这组实验能检验千级条目下的目录展示、展开、检索、加载和正文传递。目标描述、任务与证据均为测试设计，部分近邻描述明确排除目标操作，可能使区分更容易，不能据此宣称真实千级技能库的准确率。

[全部名称、描述、类型与文件 SHA](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/catalog-inventory.json>)

[生成规则](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-completion-2026-09-08/eval/fixture_builder.py:106>)；[4 个新增流程定义](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/extra_fixture.py:12>)。

**实际文件样例**

- 基础流程技能：[api/error-contract](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/api/error-contract/SKILL.md>)（18 行）

- 新增流程技能：[batch-export/snapshot-pagination](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/batch-export/snapshot-pagination/SKILL.md>)（18 行）

- 近邻干扰条目：[api/error-contract-translation](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/api/error-contract-translation/SKILL.md>)（8 行）

- 模板干扰条目：[address-book/access-audit](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/address-book/access-audit/SKILL.md>)（8 行）

**全部 1000 个条目，按一级目录分组**

[address-book](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/address-book/SKILL_DIR.md>)：19 个

```text
address-book/access-audit
address-book/connection-pool
address-book/error-contract
address-book/expiry-analysis
address-book/health-check
address-book/idempotency
address-book/idempotent-consumer-template
address-book/import-cycle
address-book/latency-diagnosis
address-book/pipeline-diagnosis
address-book/precedence
address-book/provider-resilience
address-book/query-plan
address-book/redaction
address-book/retry-budget
address-book/rollback-plan
address-book/startup-guide
address-book/test-isolation
address-book/timeout-budget
```

[analytics](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/analytics/SKILL_DIR.md>)：20 个

```text
analytics/access-audit
analytics/connection-pool
analytics/error-contract
analytics/event-order
analytics/expiry-analysis
analytics/health-check
analytics/idempotency
analytics/idempotent-consumer-template
analytics/import-cycle
analytics/latency-diagnosis
analytics/pipeline-diagnosis
analytics/precedence
analytics/provider-resilience
analytics/query-plan
analytics/redaction
analytics/retry-budget
analytics/rollback-plan
analytics/startup-guide
analytics/test-isolation
analytics/timeout-budget
```

[api](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/api/SKILL_DIR.md>)：2 个

```text
api/error-contract
api/error-contract-translation
```

[architecture](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/architecture/SKILL_DIR.md>)：2 个

```text
architecture/import-cycle
architecture/import-graph-render
```

[audit-trail](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/audit-trail/SKILL_DIR.md>)：20 个

```text
audit-trail/access-audit
audit-trail/connection-pool
audit-trail/error-contract
audit-trail/event-order
audit-trail/expiry-analysis
audit-trail/health-check
audit-trail/idempotency
audit-trail/idempotent-consumer-template
audit-trail/import-cycle
audit-trail/latency-diagnosis
audit-trail/pipeline-diagnosis
audit-trail/precedence
audit-trail/provider-resilience
audit-trail/query-plan
audit-trail/redaction
audit-trail/retry-budget
audit-trail/rollback-plan
audit-trail/startup-guide
audit-trail/test-isolation
audit-trail/timeout-budget
```

[auth](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/auth/SKILL_DIR.md>)：2 个

```text
auth/access-audit
auth/access-report-format
```

[backup](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/backup/SKILL_DIR.md>)：19 个

```text
backup/access-audit
backup/connection-pool
backup/error-contract
backup/expiry-analysis
backup/health-check
backup/idempotency
backup/idempotent-consumer-template
backup/import-cycle
backup/latency-diagnosis
backup/pipeline-diagnosis
backup/precedence
backup/provider-resilience
backup/query-plan
backup/redaction
backup/retry-budget
backup/rollback-plan
backup/startup-guide
backup/test-isolation
backup/timeout-budget
```

[batch-export](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/batch-export/SKILL_DIR.md>)：21 个

```text
batch-export/access-audit
batch-export/connection-pool
batch-export/error-contract
batch-export/event-order
batch-export/expiry-analysis
batch-export/health-check
batch-export/idempotency
batch-export/idempotent-consumer-template
batch-export/import-cycle
batch-export/latency-diagnosis
batch-export/pipeline-diagnosis
batch-export/precedence
batch-export/provider-resilience
batch-export/query-plan
batch-export/redaction
batch-export/retry-budget
batch-export/rollback-plan
batch-export/snapshot-pagination
batch-export/startup-guide
batch-export/test-isolation
batch-export/timeout-budget
```

[billing](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/billing/SKILL_DIR.md>)：19 个

```text
billing/access-audit
billing/connection-pool
billing/error-contract
billing/event-order
billing/expiry-analysis
billing/health-check
billing/idempotency
billing/idempotent-consumer-template
billing/import-cycle
billing/latency-diagnosis
billing/pipeline-diagnosis
billing/precedence
billing/provider-resilience
billing/query-plan
billing/redaction
billing/rollback-plan
billing/startup-guide
billing/test-isolation
billing/timeout-budget
```

[cache](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/cache/SKILL_DIR.md>)：2 个

```text
cache/expiry-analysis
cache/expiry-dashboard
```

[catalog](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/catalog/SKILL_DIR.md>)：19 个

```text
catalog/access-audit
catalog/connection-pool
catalog/error-contract
catalog/event-order
catalog/expiry-analysis
catalog/health-check
catalog/idempotency
catalog/idempotent-consumer-template
catalog/import-cycle
catalog/latency-diagnosis
catalog/pipeline-diagnosis
catalog/precedence
catalog/provider-resilience
catalog/query-plan
catalog/redaction
catalog/rollback-plan
catalog/startup-guide
catalog/test-isolation
catalog/timeout-budget
```

[catalog-import](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/catalog-import/SKILL_DIR.md>)：19 个

```text
catalog-import/access-audit
catalog-import/connection-pool
catalog-import/error-contract
catalog-import/expiry-analysis
catalog-import/health-check
catalog-import/idempotency
catalog-import/idempotent-consumer-template
catalog-import/import-cycle
catalog-import/latency-diagnosis
catalog-import/pipeline-diagnosis
catalog-import/precedence
catalog-import/provider-resilience
catalog-import/query-plan
catalog-import/redaction
catalog-import/retry-budget
catalog-import/rollback-plan
catalog-import/startup-guide
catalog-import/test-isolation
catalog-import/timeout-budget
```

[cdn](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/cdn/SKILL_DIR.md>)：19 个

```text
cdn/access-audit
cdn/connection-pool
cdn/error-contract
cdn/expiry-analysis
cdn/health-check
cdn/idempotency
cdn/idempotent-consumer-template
cdn/import-cycle
cdn/latency-diagnosis
cdn/pipeline-diagnosis
cdn/precedence
cdn/provider-resilience
cdn/query-plan
cdn/redaction
cdn/retry-budget
cdn/rollback-plan
cdn/startup-guide
cdn/test-isolation
cdn/timeout-budget
```

[checkout](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/checkout/SKILL_DIR.md>)：3 个

```text
checkout/latency-diagnosis
checkout/payment-dashboard
checkout/provider-resilience
```

[ci](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/ci/SKILL_DIR.md>)：3 个

```text
ci/pipeline-diagnosis
ci/test-isolation
ci/test-report-format
```

[config](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/config/SKILL_DIR.md>)：3 个

```text
config/config-file-format
config/dns-resolution
config/precedence
```

[crm](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/crm/SKILL_DIR.md>)：19 个

```text
crm/access-audit
crm/connection-pool
crm/error-contract
crm/expiry-analysis
crm/health-check
crm/idempotency
crm/idempotent-consumer-template
crm/import-cycle
crm/latency-diagnosis
crm/pipeline-diagnosis
crm/precedence
crm/provider-resilience
crm/query-plan
crm/redaction
crm/retry-budget
crm/rollback-plan
crm/startup-guide
crm/test-isolation
crm/timeout-budget
```

[currency](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/currency/SKILL_DIR.md>)：19 个

```text
currency/access-audit
currency/connection-pool
currency/error-contract
currency/expiry-analysis
currency/health-check
currency/idempotency
currency/idempotent-consumer-template
currency/import-cycle
currency/latency-diagnosis
currency/pipeline-diagnosis
currency/precedence
currency/provider-resilience
currency/query-plan
currency/redaction
currency/retry-budget
currency/rollback-plan
currency/startup-guide
currency/test-isolation
currency/timeout-budget
```

[customer-support](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/customer-support/SKILL_DIR.md>)：19 个

```text
customer-support/access-audit
customer-support/connection-pool
customer-support/error-contract
customer-support/expiry-analysis
customer-support/health-check
customer-support/idempotency
customer-support/idempotent-consumer-template
customer-support/import-cycle
customer-support/latency-diagnosis
customer-support/pipeline-diagnosis
customer-support/precedence
customer-support/provider-resilience
customer-support/query-plan
customer-support/redaction
customer-support/retry-budget
customer-support/rollback-plan
customer-support/startup-guide
customer-support/test-isolation
customer-support/timeout-budget
```

[data-retention](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/data-retention/SKILL_DIR.md>)：19 个

```text
data-retention/access-audit
data-retention/connection-pool
data-retention/error-contract
data-retention/expiry-analysis
data-retention/health-check
data-retention/idempotency
data-retention/idempotent-consumer-template
data-retention/import-cycle
data-retention/latency-diagnosis
data-retention/pipeline-diagnosis
data-retention/precedence
data-retention/provider-resilience
data-retention/query-plan
data-retention/redaction
data-retention/retry-budget
data-retention/rollback-plan
data-retention/startup-guide
data-retention/test-isolation
data-retention/timeout-budget
```

[database](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/database/SKILL_DIR.md>)：2 个

```text
database/query-plan
database/query-plan-serialization
```

[docs](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/docs/SKILL_DIR.md>)：2 个

```text
docs/startup-guide
docs/startup-logo
```

[email-renderer](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/email-renderer/SKILL_DIR.md>)：20 个

```text
email-renderer/access-audit
email-renderer/connection-pool
email-renderer/error-contract
email-renderer/event-order
email-renderer/expiry-analysis
email-renderer/health-check
email-renderer/idempotency
email-renderer/idempotent-consumer-template
email-renderer/import-cycle
email-renderer/latency-diagnosis
email-renderer/pipeline-diagnosis
email-renderer/precedence
email-renderer/provider-resilience
email-renderer/query-plan
email-renderer/redaction
email-renderer/retry-budget
email-renderer/rollback-plan
email-renderer/startup-guide
email-renderer/test-isolation
email-renderer/timeout-budget
```

[event-archive](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/event-archive/SKILL_DIR.md>)：20 个

```text
event-archive/access-audit
event-archive/connection-pool
event-archive/error-contract
event-archive/event-order
event-archive/expiry-analysis
event-archive/health-check
event-archive/idempotency
event-archive/idempotent-consumer-template
event-archive/import-cycle
event-archive/latency-diagnosis
event-archive/pipeline-diagnosis
event-archive/precedence
event-archive/provider-resilience
event-archive/query-plan
event-archive/redaction
event-archive/retry-budget
event-archive/rollback-plan
event-archive/startup-guide
event-archive/test-isolation
event-archive/timeout-budget
```

[experiments](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/experiments/SKILL_DIR.md>)：20 个

```text
experiments/access-audit
experiments/connection-pool
experiments/error-contract
experiments/event-order
experiments/expiry-analysis
experiments/health-check
experiments/idempotency
experiments/idempotent-consumer-template
experiments/import-cycle
experiments/latency-diagnosis
experiments/pipeline-diagnosis
experiments/precedence
experiments/provider-resilience
experiments/query-plan
experiments/redaction
experiments/retry-budget
experiments/rollback-plan
experiments/startup-guide
experiments/test-isolation
experiments/timeout-budget
```

[feature-flags](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/feature-flags/SKILL_DIR.md>)：19 个

```text
feature-flags/access-audit
feature-flags/connection-pool
feature-flags/error-contract
feature-flags/expiry-analysis
feature-flags/health-check
feature-flags/idempotency
feature-flags/idempotent-consumer-template
feature-flags/import-cycle
feature-flags/latency-diagnosis
feature-flags/pipeline-diagnosis
feature-flags/precedence
feature-flags/provider-resilience
feature-flags/query-plan
feature-flags/redaction
feature-flags/retry-budget
feature-flags/rollback-plan
feature-flags/startup-guide
feature-flags/test-isolation
feature-flags/timeout-budget
```

[file-storage](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/file-storage/SKILL_DIR.md>)：20 个

```text
file-storage/access-audit
file-storage/connection-pool
file-storage/error-contract
file-storage/event-order
file-storage/expiry-analysis
file-storage/health-check
file-storage/idempotency
file-storage/idempotent-consumer-template
file-storage/import-cycle
file-storage/latency-diagnosis
file-storage/pipeline-diagnosis
file-storage/precedence
file-storage/provider-resilience
file-storage/query-plan
file-storage/redaction
file-storage/retry-budget
file-storage/rollback-plan
file-storage/startup-guide
file-storage/test-isolation
file-storage/timeout-budget
```

[fraud](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/fraud/SKILL_DIR.md>)：19 个

```text
fraud/access-audit
fraud/connection-pool
fraud/error-contract
fraud/event-order
fraud/expiry-analysis
fraud/health-check
fraud/idempotency
fraud/idempotent-consumer-template
fraud/import-cycle
fraud/latency-diagnosis
fraud/pipeline-diagnosis
fraud/precedence
fraud/provider-resilience
fraud/query-plan
fraud/redaction
fraud/rollback-plan
fraud/startup-guide
fraud/test-isolation
fraud/timeout-budget
```

[fulfilment](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/fulfilment/SKILL_DIR.md>)：20 个

```text
fulfilment/access-audit
fulfilment/connection-pool
fulfilment/error-contract
fulfilment/event-order
fulfilment/expiry-analysis
fulfilment/health-check
fulfilment/idempotency
fulfilment/idempotent-consumer-template
fulfilment/import-cycle
fulfilment/latency-diagnosis
fulfilment/pipeline-diagnosis
fulfilment/precedence
fulfilment/provider-resilience
fulfilment/query-plan
fulfilment/redaction
fulfilment/retry-budget
fulfilment/rollback-plan
fulfilment/startup-guide
fulfilment/test-isolation
fulfilment/timeout-budget
```

[geocoding](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/geocoding/SKILL_DIR.md>)：19 个

```text
geocoding/access-audit
geocoding/connection-pool
geocoding/error-contract
geocoding/expiry-analysis
geocoding/health-check
geocoding/idempotency
geocoding/idempotent-consumer-template
geocoding/import-cycle
geocoding/latency-diagnosis
geocoding/pipeline-diagnosis
geocoding/precedence
geocoding/provider-resilience
geocoding/query-plan
geocoding/redaction
geocoding/retry-budget
geocoding/rollback-plan
geocoding/startup-guide
geocoding/test-isolation
geocoding/timeout-budget
```

[http](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/http/SKILL_DIR.md>)：3 个

```text
http/client-lifecycle
http/retry-budget
http/retry-metrics-export
```

[identity-sync](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/identity-sync/SKILL_DIR.md>)：20 个

```text
identity-sync/access-audit
identity-sync/connection-pool
identity-sync/error-contract
identity-sync/event-order
identity-sync/expiry-analysis
identity-sync/health-check
identity-sync/idempotency
identity-sync/idempotent-consumer-template
identity-sync/import-cycle
identity-sync/latency-diagnosis
identity-sync/pipeline-diagnosis
identity-sync/precedence
identity-sync/provider-resilience
identity-sync/query-plan
identity-sync/redaction
identity-sync/retry-budget
identity-sync/rollback-plan
identity-sync/startup-guide
identity-sync/test-isolation
identity-sync/timeout-budget
```

[image-transform](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/image-transform/SKILL_DIR.md>)：19 个

```text
image-transform/access-audit
image-transform/connection-pool
image-transform/error-contract
image-transform/expiry-analysis
image-transform/health-check
image-transform/idempotency
image-transform/idempotent-consumer-template
image-transform/import-cycle
image-transform/latency-diagnosis
image-transform/pipeline-diagnosis
image-transform/precedence
image-transform/provider-resilience
image-transform/query-plan
image-transform/redaction
image-transform/retry-budget
image-transform/rollback-plan
image-transform/startup-guide
image-transform/test-isolation
image-transform/timeout-budget
```

[inventory](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/inventory/SKILL_DIR.md>)：20 个

```text
inventory/access-audit
inventory/connection-pool
inventory/error-contract
inventory/event-order
inventory/expiry-analysis
inventory/health-check
inventory/idempotency
inventory/idempotent-consumer-template
inventory/import-cycle
inventory/latency-diagnosis
inventory/pipeline-diagnosis
inventory/precedence
inventory/provider-resilience
inventory/query-plan
inventory/redaction
inventory/retry-budget
inventory/rollback-plan
inventory/startup-guide
inventory/test-isolation
inventory/timeout-budget
```

[invoice-pdf](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/invoice-pdf/SKILL_DIR.md>)：19 个

```text
invoice-pdf/access-audit
invoice-pdf/connection-pool
invoice-pdf/error-contract
invoice-pdf/expiry-analysis
invoice-pdf/health-check
invoice-pdf/idempotency
invoice-pdf/idempotent-consumer-template
invoice-pdf/import-cycle
invoice-pdf/latency-diagnosis
invoice-pdf/pipeline-diagnosis
invoice-pdf/precedence
invoice-pdf/provider-resilience
invoice-pdf/query-plan
invoice-pdf/redaction
invoice-pdf/retry-budget
invoice-pdf/rollback-plan
invoice-pdf/startup-guide
invoice-pdf/test-isolation
invoice-pdf/timeout-budget
```

[localization](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/localization/SKILL_DIR.md>)：19 个

```text
localization/access-audit
localization/connection-pool
localization/error-contract
localization/expiry-analysis
localization/health-check
localization/idempotency
localization/idempotent-consumer-template
localization/import-cycle
localization/latency-diagnosis
localization/pipeline-diagnosis
localization/precedence
localization/provider-resilience
localization/query-plan
localization/redaction
localization/retry-budget
localization/rollback-plan
localization/startup-guide
localization/test-isolation
localization/timeout-budget
```

[logging](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/logging/SKILL_DIR.md>)：2 个

```text
logging/log-rotation
logging/redaction
```

[loyalty](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/loyalty/SKILL_DIR.md>)：19 个

```text
loyalty/access-audit
loyalty/connection-pool
loyalty/error-contract
loyalty/event-order
loyalty/expiry-analysis
loyalty/health-check
loyalty/idempotency
loyalty/idempotent-consumer-template
loyalty/import-cycle
loyalty/latency-diagnosis
loyalty/pipeline-diagnosis
loyalty/precedence
loyalty/provider-resilience
loyalty/query-plan
loyalty/redaction
loyalty/rollback-plan
loyalty/startup-guide
loyalty/test-isolation
loyalty/timeout-budget
```

[marketing](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/marketing/SKILL_DIR.md>)：20 个

```text
marketing/access-audit
marketing/connection-pool
marketing/error-contract
marketing/event-order
marketing/expiry-analysis
marketing/health-check
marketing/idempotency
marketing/idempotent-consumer-template
marketing/import-cycle
marketing/latency-diagnosis
marketing/pipeline-diagnosis
marketing/precedence
marketing/provider-resilience
marketing/query-plan
marketing/redaction
marketing/retry-budget
marketing/rollback-plan
marketing/startup-guide
marketing/test-isolation
marketing/timeout-budget
```

[media](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/media/SKILL_DIR.md>)：20 个

```text
media/access-audit
media/connection-pool
media/error-contract
media/event-order
media/expiry-analysis
media/health-check
media/idempotency
media/idempotent-consumer-template
media/import-cycle
media/latency-diagnosis
media/pipeline-diagnosis
media/precedence
media/provider-resilience
media/query-plan
media/redaction
media/retry-budget
media/rollback-plan
media/startup-guide
media/test-isolation
media/timeout-budget
```

[merchant-console](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/merchant-console/SKILL_DIR.md>)：19 个

```text
merchant-console/access-audit
merchant-console/connection-pool
merchant-console/error-contract
merchant-console/expiry-analysis
merchant-console/health-check
merchant-console/idempotency
merchant-console/idempotent-consumer-template
merchant-console/import-cycle
merchant-console/latency-diagnosis
merchant-console/pipeline-diagnosis
merchant-console/precedence
merchant-console/provider-resilience
merchant-console/query-plan
merchant-console/redaction
merchant-console/retry-budget
merchant-console/rollback-plan
merchant-console/startup-guide
merchant-console/test-isolation
merchant-console/timeout-budget
```

[notifications](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/notifications/SKILL_DIR.md>)：19 个

```text
notifications/access-audit
notifications/connection-pool
notifications/error-contract
notifications/event-order
notifications/expiry-analysis
notifications/health-check
notifications/idempotency
notifications/idempotent-consumer-template
notifications/import-cycle
notifications/latency-diagnosis
notifications/pipeline-diagnosis
notifications/precedence
notifications/provider-resilience
notifications/query-plan
notifications/redaction
notifications/rollback-plan
notifications/startup-guide
notifications/test-isolation
notifications/timeout-budget
```

[pricing-rules](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/pricing-rules/SKILL_DIR.md>)：20 个

```text
pricing-rules/access-audit
pricing-rules/connection-pool
pricing-rules/error-contract
pricing-rules/event-order
pricing-rules/expiry-analysis
pricing-rules/health-check
pricing-rules/idempotency
pricing-rules/idempotent-consumer-template
pricing-rules/import-cycle
pricing-rules/latency-diagnosis
pricing-rules/pipeline-diagnosis
pricing-rules/precedence
pricing-rules/provider-resilience
pricing-rules/query-plan
pricing-rules/redaction
pricing-rules/retry-budget
pricing-rules/rollback-plan
pricing-rules/startup-guide
pricing-rules/test-isolation
pricing-rules/timeout-budget
```

[promotions](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/promotions/SKILL_DIR.md>)：20 个

```text
promotions/access-audit
promotions/connection-pool
promotions/error-contract
promotions/event-order
promotions/expiry-analysis
promotions/health-check
promotions/idempotency
promotions/idempotent-consumer-template
promotions/import-cycle
promotions/latency-diagnosis
promotions/pipeline-diagnosis
promotions/precedence
promotions/provider-resilience
promotions/query-plan
promotions/redaction
promotions/retry-budget
promotions/rollback-plan
promotions/startup-guide
promotions/test-isolation
promotions/timeout-budget
```

[queue](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/queue/SKILL_DIR.md>)：3 个

```text
queue/idempotency
queue/idempotency-schema-migration
queue/idempotent-consumer-template
```

[rate-limits](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/rate-limits/SKILL_DIR.md>)：19 个

```text
rate-limits/access-audit
rate-limits/connection-pool
rate-limits/error-contract
rate-limits/expiry-analysis
rate-limits/health-check
rate-limits/idempotency
rate-limits/idempotent-consumer-template
rate-limits/import-cycle
rate-limits/latency-diagnosis
rate-limits/pipeline-diagnosis
rate-limits/precedence
rate-limits/provider-resilience
rate-limits/query-plan
rate-limits/redaction
rate-limits/retry-budget
rate-limits/rollback-plan
rate-limits/startup-guide
rate-limits/test-isolation
rate-limits/timeout-budget
```

[recommendations](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/recommendations/SKILL_DIR.md>)：19 个

```text
recommendations/access-audit
recommendations/connection-pool
recommendations/error-contract
recommendations/event-order
recommendations/expiry-analysis
recommendations/health-check
recommendations/idempotency
recommendations/idempotent-consumer-template
recommendations/import-cycle
recommendations/latency-diagnosis
recommendations/pipeline-diagnosis
recommendations/precedence
recommendations/provider-resilience
recommendations/query-plan
recommendations/redaction
recommendations/rollback-plan
recommendations/startup-guide
recommendations/test-isolation
recommendations/timeout-budget
```

[reporting](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/reporting/SKILL_DIR.md>)：20 个

```text
reporting/access-audit
reporting/connection-pool
reporting/error-contract
reporting/event-order
reporting/expiry-analysis
reporting/health-check
reporting/idempotency
reporting/idempotent-consumer-template
reporting/import-cycle
reporting/latency-diagnosis
reporting/pipeline-diagnosis
reporting/precedence
reporting/provider-resilience
reporting/query-plan
reporting/redaction
reporting/retry-budget
reporting/rollback-plan
reporting/startup-guide
reporting/test-isolation
reporting/timeout-budget
```

[returns](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/returns/SKILL_DIR.md>)：19 个

```text
returns/access-audit
returns/connection-pool
returns/error-contract
returns/event-order
returns/expiry-analysis
returns/health-check
returns/idempotency
returns/idempotent-consumer-template
returns/import-cycle
returns/latency-diagnosis
returns/pipeline-diagnosis
returns/precedence
returns/provider-resilience
returns/query-plan
returns/redaction
returns/rollback-plan
returns/startup-guide
returns/test-isolation
returns/timeout-budget
```

[scheduler](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/scheduler/SKILL_DIR.md>)：21 个

```text
scheduler/access-audit
scheduler/business-day-window
scheduler/connection-pool
scheduler/error-contract
scheduler/event-order
scheduler/expiry-analysis
scheduler/health-check
scheduler/idempotency
scheduler/idempotent-consumer-template
scheduler/import-cycle
scheduler/latency-diagnosis
scheduler/pipeline-diagnosis
scheduler/precedence
scheduler/provider-resilience
scheduler/query-plan
scheduler/redaction
scheduler/retry-budget
scheduler/rollback-plan
scheduler/startup-guide
scheduler/test-isolation
scheduler/timeout-budget
```

[search](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/search/SKILL_DIR.md>)：19 个

```text
search/access-audit
search/connection-pool
search/error-contract
search/event-order
search/expiry-analysis
search/health-check
search/idempotency
search/idempotent-consumer-template
search/import-cycle
search/latency-diagnosis
search/pipeline-diagnosis
search/precedence
search/provider-resilience
search/query-plan
search/redaction
search/rollback-plan
search/startup-guide
search/test-isolation
search/timeout-budget
```

[search-indexer](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/search-indexer/SKILL_DIR.md>)：19 个

```text
search-indexer/access-audit
search-indexer/connection-pool
search-indexer/error-contract
search-indexer/expiry-analysis
search-indexer/health-check
search-indexer/idempotency
search-indexer/idempotent-consumer-template
search-indexer/import-cycle
search-indexer/latency-diagnosis
search-indexer/pipeline-diagnosis
search-indexer/precedence
search-indexer/provider-resilience
search-indexer/query-plan
search-indexer/redaction
search-indexer/retry-budget
search-indexer/rollback-plan
search-indexer/startup-guide
search-indexer/test-isolation
search-indexer/timeout-budget
```

[session-store](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/session-store/SKILL_DIR.md>)：20 个

```text
session-store/access-audit
session-store/connection-pool
session-store/error-contract
session-store/event-order
session-store/expiry-analysis
session-store/health-check
session-store/idempotency
session-store/idempotent-consumer-template
session-store/import-cycle
session-store/latency-diagnosis
session-store/pipeline-diagnosis
session-store/precedence
session-store/provider-resilience
session-store/query-plan
session-store/redaction
session-store/retry-budget
session-store/rollback-plan
session-store/startup-guide
session-store/test-isolation
session-store/timeout-budget
```

[settlement](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/settlement/SKILL_DIR.md>)：19 个

```text
settlement/access-audit
settlement/connection-pool
settlement/error-contract
settlement/expiry-analysis
settlement/health-check
settlement/idempotency
settlement/idempotent-consumer-template
settlement/import-cycle
settlement/latency-diagnosis
settlement/pipeline-diagnosis
settlement/precedence
settlement/provider-resilience
settlement/query-plan
settlement/redaction
settlement/retry-budget
settlement/rollback-plan
settlement/startup-guide
settlement/test-isolation
settlement/timeout-budget
```

[shipping](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/shipping/SKILL_DIR.md>)：19 个

```text
shipping/access-audit
shipping/connection-pool
shipping/error-contract
shipping/event-order
shipping/expiry-analysis
shipping/health-check
shipping/idempotency
shipping/idempotent-consumer-template
shipping/import-cycle
shipping/latency-diagnosis
shipping/pipeline-diagnosis
shipping/precedence
shipping/provider-resilience
shipping/query-plan
shipping/redaction
shipping/rollback-plan
shipping/startup-guide
shipping/test-isolation
shipping/timeout-budget
```

[sms-delivery](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/sms-delivery/SKILL_DIR.md>)：19 个

```text
sms-delivery/access-audit
sms-delivery/connection-pool
sms-delivery/error-contract
sms-delivery/expiry-analysis
sms-delivery/health-check
sms-delivery/idempotency
sms-delivery/idempotent-consumer-template
sms-delivery/import-cycle
sms-delivery/latency-diagnosis
sms-delivery/pipeline-diagnosis
sms-delivery/precedence
sms-delivery/provider-resilience
sms-delivery/query-plan
sms-delivery/redaction
sms-delivery/retry-budget
sms-delivery/rollback-plan
sms-delivery/startup-guide
sms-delivery/test-isolation
sms-delivery/timeout-budget
```

[stock-sync](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/stock-sync/SKILL_DIR.md>)：19 个

```text
stock-sync/access-audit
stock-sync/connection-pool
stock-sync/error-contract
stock-sync/expiry-analysis
stock-sync/health-check
stock-sync/idempotency
stock-sync/idempotent-consumer-template
stock-sync/import-cycle
stock-sync/latency-diagnosis
stock-sync/pipeline-diagnosis
stock-sync/precedence
stock-sync/provider-resilience
stock-sync/query-plan
stock-sync/redaction
stock-sync/retry-budget
stock-sync/rollback-plan
stock-sync/startup-guide
stock-sync/test-isolation
stock-sync/timeout-budget
```

[subscriptions](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/subscriptions/SKILL_DIR.md>)：19 个

```text
subscriptions/access-audit
subscriptions/connection-pool
subscriptions/error-contract
subscriptions/event-order
subscriptions/expiry-analysis
subscriptions/health-check
subscriptions/idempotency
subscriptions/idempotent-consumer-template
subscriptions/import-cycle
subscriptions/latency-diagnosis
subscriptions/pipeline-diagnosis
subscriptions/precedence
subscriptions/provider-resilience
subscriptions/query-plan
subscriptions/redaction
subscriptions/rollback-plan
subscriptions/startup-guide
subscriptions/test-isolation
subscriptions/timeout-budget
```

[tax](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/tax/SKILL_DIR.md>)：19 个

```text
tax/access-audit
tax/connection-pool
tax/error-contract
tax/event-order
tax/expiry-analysis
tax/health-check
tax/idempotency
tax/idempotent-consumer-template
tax/import-cycle
tax/latency-diagnosis
tax/pipeline-diagnosis
tax/precedence
tax/provider-resilience
tax/query-plan
tax/redaction
tax/rollback-plan
tax/startup-guide
tax/test-isolation
tax/timeout-budget
```

[vendor-import](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/vendor-import/SKILL_DIR.md>)：20 个

```text
vendor-import/access-audit
vendor-import/connection-pool
vendor-import/error-contract
vendor-import/event-order
vendor-import/expiry-analysis
vendor-import/health-check
vendor-import/idempotency
vendor-import/idempotent-consumer-template
vendor-import/import-cycle
vendor-import/latency-diagnosis
vendor-import/pipeline-diagnosis
vendor-import/precedence
vendor-import/provider-resilience
vendor-import/query-plan
vendor-import/redaction
vendor-import/retry-budget
vendor-import/rollback-plan
vendor-import/startup-guide
vendor-import/test-isolation
vendor-import/timeout-budget
```

[warehouse](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/warehouse/SKILL_DIR.md>)：19 个

```text
warehouse/access-audit
warehouse/connection-pool
warehouse/error-contract
warehouse/event-order
warehouse/expiry-analysis
warehouse/health-check
warehouse/idempotency
warehouse/idempotent-consumer-template
warehouse/import-cycle
warehouse/latency-diagnosis
warehouse/pipeline-diagnosis
warehouse/precedence
warehouse/provider-resilience
warehouse/query-plan
warehouse/redaction
warehouse/rollback-plan
warehouse/startup-guide
warehouse/test-isolation
warehouse/timeout-budget
```

[webhooks](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/workspace/.mini-code/skills/webhooks/SKILL_DIR.md>)：20 个

```text
webhooks/access-audit
webhooks/connection-pool
webhooks/error-contract
webhooks/event-order
webhooks/expiry-analysis
webhooks/health-check
webhooks/idempotency
webhooks/idempotent-consumer-template
webhooks/import-cycle
webhooks/latency-diagnosis
webhooks/pipeline-diagnosis
webhooks/precedence
webhooks/provider-resilience
webhooks/query-plan
webhooks/redaction
webhooks/retry-budget
webhooks/rollback-plan
webhooks/startup-guide
webhooks/test-isolation
webhooks/timeout-budget
```
