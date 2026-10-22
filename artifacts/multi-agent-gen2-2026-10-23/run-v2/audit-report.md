# 三组评测独立数据审计

模式：final；证据有效：True；已审计 6/6 个 trial。

失败或协作未触发本身不代表数据无效。错误须查明后再发布正式比较；warning 保留并解释，不删除相关试次。

- warning / budget_calls_include_admission_rejections / gen2-registry-lifetime-r1-single：{"used_model_calls": {"budget": 24, "provider": 23}}; exactly 1 validated pre-HTTP refusals explain only the call counter difference; physical requests and token usage unchanged

## 独立机制与停止原因

以下按原始证据重算；停止原因可共现，属于事后诊断，不改变原始 errorClass、success 或预注册检验。仅 response 提及的原因标为未独立确认；缺证据保留 unknown。
请求截止按时间证据细分：request_deadline_per_request 为达到冻结的单次请求时限；request_deadline_global 为请求受全局剩余执行时间限制；request_deadline 为证据不足或两条界限重合。此处不推断网络或模型推理原因。

| trial | 实际叶子 / 送达叶子 | 协议合规 | HTTP 前拒绝 | 停止证据 | 终态判定 |
| --- | ---: | --- | ---: | --- | --- |
| gen2-read-context-r1-single | 0 / 0 | False | 0 | host_exception (host_result) | 无失败判定 |
| gen2-registry-lifetime-r1-explicit | 1 / 1 | True | 0 | unknown (unknown) | 无失败判定 |
| gen2-summary-budget-r1-single | 0 / 0 | False | 0 | unknown (unknown) | 无失败判定 |
| gen2-read-context-r1-explicit | 1 / 1 | True | 0 | unknown (unknown) | 无失败判定 |
| gen2-summary-budget-r1-explicit | 1 / 1 | True | 0 | unknown (unknown) | oracle_failed |
| gen2-registry-lifetime-r1-single | 0 / 0 | False | 1 | transport_admission_token_budget (validated_transport_admission) | 无失败判定 |

冻结来源从 ZIP 独立重算，不依赖当前工作区源码；SQLite 以只读模式读取，包含失败及未完成请求的 provider 回执。费用为冻结标准 API 单价等价成本，非套餐实际扣费。

Only surviving evidence can be audited. No archived code, model request or oracle was executed. Missing usage remains unknown; failed task/protocol outcomes remain valid observations.
