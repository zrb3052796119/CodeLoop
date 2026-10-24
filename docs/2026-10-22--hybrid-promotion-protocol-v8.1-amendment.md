# 协议修订预注册：hybrid-canonical v8 → v8.1（verifier 泳道失败容差）

- 日期：2026-10-22
- 批准：owner（会话内明确批准选项 1"协议修订"）
- 依据：三份不可变失败报告（v9/v10/v11，`.repair-work/memory-retrieval-quality-2026-09-03/`）
  证明零失败要求在 qwen3.7-flash ~2.5% 基础 schema 违约率下不可满足（实测 0/3）。

## 修订内容（唯一语义变更）

主门后置校验中 verifier 泳道：

- v8：`failed_call_count != 0` 即否决（一票否决任何 model.failed 事件）
- v8.1：`failed_call_count <= ceil(attempts × 0.05)` 且 `usage_unavailable_count == 0`
  可通过；重试已自愈（每 case 终裁完整性由 canonical row join 继续强制）。
  challenger / resolver 泳道维持零失败。

阈值常量：`QWEN_PROMOTION_VERIFIER_FAILED_CALL_RATE = 0.05`
（`minicode/memory_hybrid_promotion_contract.py`）。

## 不变的部分

- 判定语义（admit/veto 规则、prompt、响应 schema）零改动——失败调用由
  确定性回退兜底，不产生额外准入。
- 数据集 schema、数量、反捷径要求零改动。
- 信任锚机制、实现冻结、one-shot 纪律零改动。

## 生效方式

- `HYBRID_PROTOCOL_VERSION` → `hybrid-canonical-v8.1`（旧证据自然失效）。
- v12 双数据集重封 → 压力门 → 主门（一次性纪律照旧）。
