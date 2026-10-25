# Phase 2 r3：32 次结果与可恢复证据

16 个历史任务 × single/auto × 1 次，32/32 完成；执行器于 2026-10-25 10:45:43（Asia/Shanghai）正常退出。single 成功 8/16，auto 成功 7/16。完整结论与本期默认决策见[结果页](../../../docs/real-task-comparison-r3-results-2026-10-25.md)，注册与事后修订限制见[r3 说明](../../../docs/real-task-comparison-r3-2026-10-25.md)。r2 仍为独立不完整试跑，未并入本包成绩。

归档共 **1,064 个成员，37,015,050 原始字节 → 10,708,747 gzip 字节**。`evidence.tar.gz` SHA-256：`568fd4ed38adf095c07d9639b3ac41ccecea5b962ca65492be7c16869196523c`。逐成员大小与 SHA 见 [manifest.json](manifest.json)；输入、原件、派生记录的分类见 tar 内 `source-index.json`。这不是新的实验注册、评分或 gate。

| tar 内位置 | 内容与边界 |
| --- | --- |
| `run/`、`registration/`、`attestations/` | 冻结与注册原件、启动/结束身份；另含事后只读统计审阅及计数证明，不将这些审阅冒充运行时事件 |
| `report/`、`report-software/` | 未改的 reporter v3 源码与 `report.md`、`summary.json`、32 行 `trials.csv` |
| `trials/`、`run/results.json` | 全部成功、失败、超时的原始结果；没有重判、填零或选择性遗漏 |
| `records/<id>/` | 原始执行/清理/验收 JSON、receipt、stage、process 记录和裁判日志；缺失项在 `availability.json` 明示 |
| `records/<id>/transport-usage.json`、journal 投影 | 内容受限的派生调用/用量/事件记录，保留原 SHA、未知值与来源；不包含 provider 回答、推理、工具参数 |
| `shared/`、`objects/`、`run/frozen-code.zip` | 一份冻结 ZIP 与按 SHA 去重的源码、oracle、support、交付文件；可恢复原 `tasks.json` 的精确字节 |
| `records/<id>/snapshots/` | 三类具名保留快照的差异、删除与权限记录；是归档时观察，不冒充 agent 结束瞬间 |
| `archive-software/` | 本次使用且已实际恢复核验的离线打包/恢复脚本；恢复不执行模型、oracle 或交付源码 |

冻结 manifest SHA 为 `b32718a77f1680cf2d7d465d655a95bdcdcf5c6c745c3d30e1b9e430193d3b01`；任务原始文件 SHA 为 `7464fc17a4725efaecac103a0d0b6a65d7442ffbbab61687037716592707d933`；注册 SHA 为 `eaa9645d3a2a39649d1e80c28ae3db39fc3c9bab89f8586e12515378babee5b2`。实际生产保持 P0，harness 含既定 transport 修复，不含 P1/PTY 后续改动。Git 标签为原收据提供的身份；真正的源码、oracle 与结果绑定由冻结文件和逐字节 SHA 证明。

判据身份经过公开包、展开设计、最终冻结 task 三阶段。12 个 oracle 字节完全相同；4 个 read oracle 每阶段只替换同一父目录的两处字面表示，已逐字核对，无语义变化。`study-design.json` 的对应 oracle SHA 指向准备阶段目录版本，`preparedOriginalOracleSha256` 是历史元数据；不能将其冒充最终 oracle SHA。最终执行字节以冻结 `tasks.json` 及验收收据为准，原设计不回写。既有[首个 API 前等价证明](../phase2-preparation-r3/prepared-equivalence-proof.json)保留这些阶段的身份。分析计划中的“尚未调用”等准备状态也保持历史原文。

完整 token 总量未知，≥55,261,208 / ≥39,553,249 是各组全部已知 provider usage 的下界，不是完整 cell 小计或可比较的节省幅度。cache 属于 input；`execution.json` 的内部 `budget.used_cost_usd` 估算不等于未知自定义路由的实付费用。实际货币费用与人工批准/纠正次数保持 null。

32 份 transport 记录中的 2,087 请求全部为主角色、无子代理 ID，task 调用为 0；31 份保留 transcript 消息头与空 subagent 数组提供补充核对，超时 cell 缺少这两份最终文件。无 task 事件时宿主不写空 collaboration 文件，因此原 reporter 的详细角色证据覆盖仍为 **0/32**。本包不伪造空 collaboration witness，也不能仅凭缺文件认定零委派；直接计数的来源和限制见 `attestations/r3-report-readonly-review.*`。本轮未包含已观察到的多 agent 执行，不能给出协作或记忆收益结论。

所有 HOME、凭证、SQLite、原 provider 响应与私有 transcript 正文继续留在本机。公开内容足以复核既有判定、调用小计、冻结输入与纳入的交付字节；原 reporter 的完整私有协作复核仍需原私有文件及历史路径处理，不能称公开包可独立重放一切。原收据中的临时绝对路径只保留来源元数据，不发布对应目录内容。链接和特殊文件仅记形态，不自动恢复。

归档已安全解包全部 1,064 个普通相对成员并逐 SHA/字节比对；全部内容对象、原始 tasks JSON 与所有可用 snapshot 差异库存已核验。另实际运行恢复器恢复 104,186,505 字节原始任务输入，以及 84 份可用快照的 21,272 个普通文件实例并核对字节与权限；12 份未保留快照维持缺失。没有启动 oracle、测试或模型，没有重写原试次。

恢复示例（仓库根目录执行；输出目录须不存在）：先用 `manifest.json` 校验压缩包及恢复脚本 SHA，再提取工具。

```sh
shasum -a 256 artifacts/governance-trial-2026-10-24/phase2-results-r3/evidence.tar.gz
tar -xOf artifacts/governance-trial-2026-10-24/phase2-results-r3/evidence.tar.gz archive-software/restore_delivery.py > /tmp/codeloop-r3-restore.py
python3 /tmp/codeloop-r3-restore.py --package artifacts/governance-trial-2026-10-24/phase2-results-r3 --output /tmp/codeloop-r3-inputs --inputs
python3 /tmp/codeloop-r3-restore.py --package artifacts/governance-trial-2026-10-24/phase2-results-r3 --output /tmp/codeloop-r3-parser --trial write-profile-parser-r1-single --snapshot agent-workspace-observed-after-judgement
```

恢复保留历史路径证据，不自动重定位或重跑实验。`manifest.json` 与 tar 内原/派生来源索引共同限定可复核范围；旧 pilot、失败 CI 和注册记录均不被本包取代。

脚本与 manifest 的 `archiverVersion` 保留准备时固定字符串 `r3-results-archive-draft-v2-not-executed`，没有为改标签重写已审脚本；该历史标签不代表本包尚未执行。实际打包和恢复状态以上述已完成记录为准。
