# Gen-2 只读运行审计

冻结运行：`run-v2`。预注册提交：`f131d7f3f9cc47dc00d7e64a977c466ec4804237`。

观察者只读结果与证据；不修改源码、冻结材料、候选或结果，不启动模型、不重跑、不操作运行进程。此文档位于 registration 之外。

## 2026-10-22T17:12:11.458242+00:00

已确认冻结顺序为六格，当前尚无终态行。首个完整终态写入后才执行 partial 审计；尚未对运行结果作出结论。

## 2026-10-22T17:16:36.456871+00:00 — 首个终态 partial 审计

终态数：1/6。独立审计 valid=true；auditedTrials=1。

```json
{
  "terminalRows": [
    {
      "id": "gen2-read-context-r1-single",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "modelCalls": 18,
      "durationSeconds": 469.02727945894003
    }
  ],
  "audit": {
    "valid": true,
    "mode": "partial",
    "expectedTrials": 6,
    "auditedTrials": 1,
    "deliveryEvidenceMode": "task_result_owner_ack",
    "protocolVersion": "multi-agent-gen2-v1",
    "issues": [
      {
        "severity": "warning",
        "code": "incomplete_results",
        "trial": null,
        "detail": "1/6 terminal rows"
      },
      {
        "severity": "warning",
        "code": "unfinalized_ledger",
        "trial": "gen2-registry-lifetime-r1-explicit",
        "detail": "Retain and recover requests; do not rerun this trial"
      }
    ],
    "trials": [
      {
        "id": "gen2-read-context-r1-single",
        "arm": "single",
        "modelCalls": 18,
        "unknownUsageCalls": 0,
        "inputTokens": 470438,
        "outputTokens": 19659,
        "cacheReadTokens": 249792,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.28901416,
        "estimatedCostCny": 0.28901416,
        "totalTokens": 490097,
        "success": true,
        "protocolSatisfied": false,
        "recomputedCollaboration": {
          "actualLeafRuns": 0,
          "leafModelCalls": 0,
          "deliveredLeafResults": 0,
          "successfulLeafRuns": 0,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 0,
          "collaborationExecuted": false,
          "collaborationProtocolSatisfied": false,
          "deliveredWorkflowResults": 0,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "host_exception",
              "source": "trial.errorClass",
              "evidence": "AgentBudgetExceeded",
              "authority": "host_result"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": "AgentBudgetExceeded"
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      }
    ],
    "limits": "Only surviving evidence can be audited. No archived code, model request or oracle was executed. Missing usage remains unknown; failed task/protocol outcomes remain valid observations."
  }
}
```

## 2026-10-22T17:46:35.575777+00:00 — 六格终态 final 审计

独立审计 valid=true；expectedTrials=6；auditedTrials=6。

```json
{
  "terminalRows": [
    {
      "id": "gen2-read-context-r1-single",
      "arm": "single",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": false,
      "modelCalls": 18,
      "totalTokens": 490097,
      "durationSeconds": 469.02727945894003,
      "unknownUsageCalls": 0
    },
    {
      "id": "gen2-registry-lifetime-r1-explicit",
      "arm": "explicit",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": true,
      "modelCalls": 14,
      "totalTokens": 257759,
      "durationSeconds": 373.42811974999495,
      "unknownUsageCalls": 0
    },
    {
      "id": "gen2-summary-budget-r1-single",
      "arm": "single",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": false,
      "modelCalls": 21,
      "totalTokens": 492045,
      "durationSeconds": 394.9317845420446,
      "unknownUsageCalls": 0
    },
    {
      "id": "gen2-read-context-r1-explicit",
      "arm": "explicit",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": true,
      "modelCalls": 9,
      "totalTokens": 167132,
      "durationSeconds": 286.88678850000724,
      "unknownUsageCalls": 0
    },
    {
      "id": "gen2-summary-budget-r1-explicit",
      "arm": "explicit",
      "success": false,
      "oracleSuccess": false,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": true,
      "modelCalls": 15,
      "totalTokens": 292345,
      "durationSeconds": 321.67885379190557,
      "unknownUsageCalls": 0
    },
    {
      "id": "gen2-registry-lifetime-r1-single",
      "arm": "single",
      "success": true,
      "oracleSuccess": true,
      "regressionTestPassed": true,
      "scopePassed": true,
      "collaborationProtocolSatisfied": false,
      "modelCalls": 23,
      "totalTokens": 485239,
      "durationSeconds": 309.04508404200897,
      "unknownUsageCalls": 0
    }
  ],
  "audit": {
    "valid": true,
    "mode": "final",
    "expectedTrials": 6,
    "auditedTrials": 6,
    "deliveryEvidenceMode": "task_result_owner_ack",
    "protocolVersion": "multi-agent-gen2-v1",
    "issues": [
      {
        "severity": "warning",
        "code": "budget_calls_include_admission_rejections",
        "trial": "gen2-registry-lifetime-r1-single",
        "detail": "{\"used_model_calls\": {\"budget\": 24, \"provider\": 23}}; exactly 1 validated pre-HTTP refusals explain only the call counter difference; physical requests and token usage unchanged"
      }
    ],
    "trials": [
      {
        "id": "gen2-read-context-r1-single",
        "arm": "single",
        "modelCalls": 18,
        "unknownUsageCalls": 0,
        "inputTokens": 470438,
        "outputTokens": 19659,
        "cacheReadTokens": 249792,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.28901416,
        "estimatedCostCny": 0.28901416,
        "totalTokens": 490097,
        "success": true,
        "protocolSatisfied": false,
        "recomputedCollaboration": {
          "actualLeafRuns": 0,
          "leafModelCalls": 0,
          "deliveredLeafResults": 0,
          "successfulLeafRuns": 0,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 0,
          "collaborationExecuted": false,
          "collaborationProtocolSatisfied": false,
          "deliveredWorkflowResults": 0,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "host_exception",
              "source": "trial.errorClass",
              "evidence": "AgentBudgetExceeded",
              "authority": "host_result"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": "AgentBudgetExceeded"
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      },
      {
        "id": "gen2-registry-lifetime-r1-explicit",
        "arm": "explicit",
        "modelCalls": 14,
        "unknownUsageCalls": 0,
        "inputTokens": 240711,
        "outputTokens": 17048,
        "cacheReadTokens": 105344,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.18025712000000002,
        "estimatedCostCny": 0.18025712000000002,
        "totalTokens": 257759,
        "success": true,
        "protocolSatisfied": true,
        "recomputedCollaboration": {
          "actualLeafRuns": 1,
          "leafModelCalls": 11,
          "deliveredLeafResults": 1,
          "successfulLeafRuns": 1,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 1,
          "collaborationExecuted": true,
          "collaborationProtocolSatisfied": true,
          "deliveredWorkflowResults": 1,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "unknown",
              "source": null,
              "evidence": "No recorded stopping cause; token utilization alone is insufficient",
              "authority": "unknown"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": null
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      },
      {
        "id": "gen2-summary-budget-r1-single",
        "arm": "single",
        "modelCalls": 21,
        "unknownUsageCalls": 0,
        "inputTokens": 475713,
        "outputTokens": 16332,
        "cacheReadTokens": 226944,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.29694192,
        "estimatedCostCny": 0.29694192,
        "totalTokens": 492045,
        "success": true,
        "protocolSatisfied": false,
        "recomputedCollaboration": {
          "actualLeafRuns": 0,
          "leafModelCalls": 0,
          "deliveredLeafResults": 0,
          "successfulLeafRuns": 0,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 0,
          "collaborationExecuted": false,
          "collaborationProtocolSatisfied": false,
          "deliveredWorkflowResults": 0,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "unknown",
              "source": null,
              "evidence": "No recorded stopping cause; token utilization alone is insufficient",
              "authority": "unknown"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": null
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      },
      {
        "id": "gen2-read-context-r1-explicit",
        "arm": "explicit",
        "modelCalls": 9,
        "unknownUsageCalls": 0,
        "inputTokens": 151868,
        "outputTokens": 15264,
        "cacheReadTokens": 24704,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.15015232,
        "estimatedCostCny": 0.15015232,
        "totalTokens": 167132,
        "success": true,
        "protocolSatisfied": true,
        "recomputedCollaboration": {
          "actualLeafRuns": 1,
          "leafModelCalls": 6,
          "deliveredLeafResults": 1,
          "successfulLeafRuns": 1,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 1,
          "collaborationExecuted": true,
          "collaborationProtocolSatisfied": true,
          "deliveredWorkflowResults": 1,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "unknown",
              "source": null,
              "evidence": "No recorded stopping cause; token utilization alone is insufficient",
              "authority": "unknown"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": null
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      },
      {
        "id": "gen2-summary-budget-r1-explicit",
        "arm": "explicit",
        "modelCalls": 15,
        "unknownUsageCalls": 0,
        "inputTokens": 277568,
        "outputTokens": 14777,
        "cacheReadTokens": 120576,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.19470167999999996,
        "estimatedCostCny": 0.19470167999999996,
        "totalTokens": 292345,
        "success": false,
        "protocolSatisfied": true,
        "recomputedCollaboration": {
          "actualLeafRuns": 1,
          "leafModelCalls": 12,
          "deliveredLeafResults": 1,
          "successfulLeafRuns": 1,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 1,
          "collaborationExecuted": true,
          "collaborationProtocolSatisfied": true,
          "deliveredWorkflowResults": 1,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "unknown",
              "source": null,
              "evidence": "No recorded stopping cause; token utilization alone is insufficient",
              "authority": "unknown"
            }
          ],
          "terminalOutcomes": [
            "oracle_failed"
          ],
          "originalErrorClass": null
        },
        "admissionRejectedCount": 0,
        "validatedAdmissionRejections": 0
      },
      {
        "id": "gen2-registry-lifetime-r1-single",
        "arm": "single",
        "modelCalls": 23,
        "unknownUsageCalls": 0,
        "inputTokens": 471415,
        "outputTokens": 13824,
        "cacheReadTokens": 233792,
        "tokenUsageComplete": true,
        "cacheUsageComplete": true,
        "usageComplete": true,
        "failedCalls": 0,
        "inFlightCalls": 0,
        "knownCostLowerBoundCny": 0.28257776000000007,
        "estimatedCostCny": 0.28257776000000007,
        "totalTokens": 485239,
        "success": true,
        "protocolSatisfied": false,
        "recomputedCollaboration": {
          "actualLeafRuns": 0,
          "leafModelCalls": 0,
          "deliveredLeafResults": 0,
          "successfulLeafRuns": 0,
          "readAnalysisLeafRuns": 0,
          "implementationLeafRuns": 0,
          "collaborationExecuted": false,
          "collaborationProtocolSatisfied": false,
          "deliveredWorkflowResults": 0,
          "actualAnalystLeafRuns": 0,
          "deliveredAnalystLeafResults": 0,
          "externalAnalysisLeafRuns": 0,
          "externalLeafRuns": 0
        },
        "diagnostics": {
          "stopEvidence": [
            {
              "code": "transport_admission_token_budget",
              "source": "transport.sqlite/admission_rejections",
              "rejection": 1,
              "evidence": {
                "reason": "token_budget",
                "currentRequests": 23,
                "currentChargedTokens": 485239,
                "reservedTokens": 16597
              },
              "authority": "validated_transport_admission"
            }
          ],
          "terminalOutcomes": [],
          "originalErrorClass": null
        },
        "admissionRejectedCount": 1,
        "validatedAdmissionRejections": 1
      }
    ],
    "limits": "Only surviving evidence can be audited. No archived code, model request or oracle was executed. Missing usage remains unknown; failed task/protocol outcomes remain valid observations."
  }
}
```
