# 两格 bounded 验证：最终独立复核

日期：2026-10-23。使用封板审计器只读审计 `run-v1`；未执行模型、oracle 或归档代码，未修改 harness、生产源码或冻结材料。

**证据审计有效，行为验收未通过。** `final-independent-audit.json` 为 final、2/2 有效、0 错误、0 警告；stock 的最终状态是 `budget_exhausted`，不是预注册要求的预算内 `approved` / `rejected`。config 仅作记录，不新增验收门。

| 指标 | stock-allocation | config-overlay |
| --- | ---: | ---: |
| 全格墙钟（秒） | 168.808343209 | 486.501346667 |
| 执行开始至 review 阶段入口（秒） | 78.772464991 | 396.454992056 |
| review 活跃墙钟（秒） | 90.026242708 | 90.032385833 |
| 物理请求：父 / 原生审查 / 合计 | 2 / 4 / 6 | 6 / 3 / 9 |
| 已知 token：父 / 原生审查 / 合计 | 26,912 / 22,314 / 49,226 | 100,866 / 13,054 / 113,920 |
| unknown usage 请求 | 1 | 1 |
| inspection / reconciliation | 均未进入 | 均未进入 |
| 原生终态 | budget_exhausted | budget_exhausted |
| requirementsAccepted / acceptedFinal | false / false | false / false |
| 候选 oracle、生成回归、范围验收 | 全部通过 | 全部通过 |

合计 15 次物理请求（父 8、原生审查 7），13 次有 provider usage、2 次 unknown；已知 token 下界 163,146。未知用量保持未知，不计零；总用量和完整费用不可证明，审计仍可确认失败记录完整。产物验收通过表示候选通过独立评测检查，不代表宿主接受最终交付。两格实际最终回执均为 `delivery=partial`、`lastPublicChecks=passed`、`completionReview=budget_exhausted`、`requirementsAccepted=false`，没有在耗尽后再发父模型请求。

review 墙钟按 observer 首次 `phase_selected(review)` 至最终决定计算，包含工具和本地工作。26/32ms 的截止后收尾是预算终态处理，并非阶段获准延长；两格分别用了 4/8 和 3/8 次阶段请求，触发原因都是 `wall_clock`。阶段上限、链与收尾预留审计一致；收尾时间预留分别为 78.206719483 秒和 60 秒。两格均正常返回 partial，未触达全格 600 秒硬截止。审计字段 `decisionWithinChain=false` 同时包含阶段截止检查，不能据此声称整个链级时限已耗尽。

两次失败物理请求（stock call 6、config call 9）均已收到 HTTP 200，错误为 `ModelCallDeadlineExceeded`、`errorPhase=stream`、`streamDone=false`，与 review 阶段截止相符。15 次请求均记录 HTTP 200；未发现 TLS / 证书 / URLError 失败。config 首次父请求耗时 184.900790083 秒，`stopReason=length`，provider 输出计数 8,192，正常流结束但可见 response 长度为 0、无工具调用。这是输出上限终止的父阶段延迟证据，不是 TLS 失败；此处不披露隐藏推理内容，也不推断延迟的唯一原因。

实际 schema 覆盖有限：7 次原生请求正文均包含 R1 更新后的 `review_test` 三字段同给/同省描述，完成提交的 findings 描述也存在。已完成的原生工具执行只有读取/目录/搜索（stock 11 次、config 6 次）；未执行 `review_test`、`review_probe_read` 或 `completion_review_submit`，没有观察到已执行工具的 schema 拒绝。inspection 未进入，其 `ready_for_evidence` 分支未实际覆盖。因此只能确认描述真实呈现，不能把“未出现 schema 错误”作为 R1 实测收益或旧拒绝分支已通过的证明。

**R3 不激活。** 两格均在首次 review 耗尽，未出现 inspection 耗时瓶颈；各只有一条链和一次审查尝试，无跨 turn 重复 inspection 的实测成本。冻结设计的两个激活条件均未满足。本结果不支持跳过或删除任何审查阶段，也不构成模型是唯一瓶颈的因果结论。

冻结与清理复核通过：16 个 registrationFiles 加 manifest 与预注册提交 `0b6dc79612fdb276359a121440750a4834fdb684` 一致；source host 为 `c9002748014a507b1bb86d0202cfffa2e77d43e0`，来源保持原归档基线加声明 overlay。manifest SHA256 为 `d19ff66a177b7ac5169e9731d6a33c42bef838f7572c989b514a5756161a42f2`；实际 host archive SHA256 为 `84b37cba1a38cec1cd84ce3ba685a1ed392c8119299a073cfd918bd24b18164a`。两个脚本仍为封板 SHA，环境及证书绑定通过。两格运行进程及各自 oracle / regression 的清理收据均为 complete，survivor、unconfirmed、证据错误均为空。没有补跑、替换失败或修改冻结结果。

复核命令（仅审计已有证据）：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B \
  -c 'import runpy,sys; from pathlib import Path; sys.path.insert(0,str(Path("scripts").resolve())); runpy.run_path("scripts/audit_native_review_bounded.py",run_name="__main__")' \
  --input artifacts/native-review-acceptance-2026-10-23/run-v1 \
  --output artifacts/native-review-acceptance-2026-10-23/final-independent-audit.json
```

证据位置：`final-independent-audit.json`；每格 `private/<trial>/native-review-observation.jsonl`、`transport-summary.json`、`parent-requests/*.json`、`response.txt`、`execution-host.json`、`process-cleanup.json` 及 `judgement/*/cleanup.json`。
