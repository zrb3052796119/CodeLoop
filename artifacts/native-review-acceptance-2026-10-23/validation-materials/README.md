# 两格 bounded 验证准备

当前正式 `run-v1/` 已完成 prepare；尚未正式冻结、注册提交或真实模型执行。`draft-manifest.json` 的 source/production/host/registration SHA 均为 `null`，不能据此运行。`prepared-registration/` 是可检查的任务与预注册材料副本，不是运行结果。

固定原 Phase 2 的 `trigger-stock-allocation` → `trigger-config-overlay`，各一次，沿原 schedule 投影。题面、源码、公开测试、隐藏 oracle、参考修复和原离线证明保持逐对象一致；原始完整文件副本及其冻结 manifest SHA 一并保留。任务 workspace 只加载 shared_files 与 case.files，隐藏材料及执行宿主均留在宿主侧。

GLM-5.3-Flash，40 物理请求 / 500000 token / 600 秒，output 8192、temperature 0、adapter retry 0、seed 20261023。原生审查 `bounded`；旧 implementation phase quota 模式保持关闭。TLS 使用标准库现有 `SSL_CERT_FILE=/etc/ssl/cert.pem`，验证保持开启，证书文件 SHA256 为 `9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994`。

stock 的最后一次真实原生审查必须在本链和阶段预算内到达 `approved` 或 `rejected`；approved 还要求 canonical task outcome 成功、正常结束。后续只有父工作超时不抹掉已形成的 rejected；后续新原生审查 budget_exhausted 则失败，不能挑此前结果。同候选、同 owner 的缓存 negative 仅当实质决定精确相同，且此前真实 native 请求产生的决定在预算内，才继承权威；可忽略的字段仅是既有外层 `completionVersion/executorId/attempt/continuesSameExecutor/evidenceRef/repairDecisionRef/evidenceDirectory`，所有嵌套 findings、probes、receipts、source bindings 保持精确。延迟缓存交接标 `delayedHandoff`。

config 无行为门。两格仍须有效终态审计；未知用量保留为 unknown/unverifiable，不填零，也不加一个 config usageComplete 行为门。宿主 oracle 判断的修复成功与 accepted final 分开报告。无重跑、不换题、不换模型、不追加轮次。

宿主构造使用原八格 `execution-host.zip`（SHA 在 draft 与脚本固定）加正式 `artifacts/native-review-acceptance-2026-10-23/host-overlay.json` 的精确 11 个生产文件及两个新脚本；覆盖字节只读专用 source host commit。`host-file-plan.json` 列出每个运行依赖及来源，未覆盖文件保留原 archive 字节，因此原冻结 memory 基线不变。不得对当前混合工作树直接打包。独立 auditor 不导入 runner、不解包执行代码、不运行 oracle 或 provider。

执行顺序（由 root 完成前置与提交后操作）：

1. 结束 §7.1 裁决修复与 §7.2 保持证书验证的 TLS 预检；关闭本地完成审查回归门。root 创建专用 source host commit，内容为原冻结基线加批准的修复；新 runner/auditor 也进入该提交。独立回归可使用额外测试 overlay，但它们不进入任务 workspace。
2. 正式运行统一使用原绝对 Python `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B` 和 `SSL_CERT_FILE=/etc/ssl/cert.pem`；venv 仅用于离线测试。`run-v1/` 已 prepare，下面保留其命令供记录，不要对该目录再次 prepare。freeze 与 audit 均为离线步骤：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B scripts/native_review_bounded_validation.py prepare \
  --output artifacts/native-review-acceptance-2026-10-23/run-v1

SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B scripts/native_review_bounded_validation.py freeze \
  --output artifacts/native-review-acceptance-2026-10-23/run-v1 \
  --source-host-commit SOURCE_HOST_FULL_SHA \
  --overlay-manifest artifacts/native-review-acceptance-2026-10-23/host-overlay.json

SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B \
  -c 'import runpy,sys; from pathlib import Path; sys.path.insert(0,str(Path("scripts").resolve())); runpy.run_path("scripts/audit_native_review_bounded.py",run_name="__main__")' \
  --input artifacts/native-review-acceptance-2026-10-23/run-v1 --allow-partial \
  --output artifacts/native-review-acceptance-2026-10-23/pre-run-audit.json
```

审计器的 `-c` 启动段只显式加入本仓库固定的 `scripts` 目录，解决 `-I` 隔离模式默认不搜索脚本邻接模块的问题；不修改审计脚本，也不加载 runner。以上命令从仓库根目录执行。

3. freeze 生成 source provenance、完整 host zip/inventory、环境版本与包清单、公开 pytest 预检及 manifest。独立 partial audit 应为有效 0/2，仅有未开始警告。root 将该输出的全部 registrationFiles 与 manifest 注册到当前工作分支提交；运行入口会核对实际 Git 对象逐字节，并验证注册提交是当前 HEAD 的祖先。当前输出布局预计为 16 个 registrationFiles 加 manifest，共 17 个文件；最终提交清单以实际 manifest 的 registrationFiles 全部键加 manifest.json 为准，不把审计报告或运行结果放进冻结目录。
4. 只有前置/精确宿主回归/独立审计/注册全部完成后，使用注册提交启动唯一两格；该命令尚未执行：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B scripts/native_review_bounded_validation.py run \
  --output artifacts/native-review-acceptance-2026-10-23/run-v1 \
  --preregistered-commit REGISTRATION_FULL_SHA
```

5. 运行严格串行。已有 trial execution-host/transport/execution-start 证据会拒绝重跑；HardTimeout 保留恢复账本、清理和共同独立 judge。完成后只读审计：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B \
  -c 'import runpy,sys; from pathlib import Path; sys.path.insert(0,str(Path("scripts").resolve())); runpy.run_path("scripts/audit_native_review_bounded.py",run_name="__main__")' \
  --input artifacts/native-review-acceptance-2026-10-23/run-v1 \
  --output artifacts/native-review-acceptance-2026-10-23/final-independent-audit.json
```

取证：原 physical ledger + 完整父 provider 请求正文 + scope/cleanup/generated-test/oracle judge 继承原 harness。新增 `private/<trial>/native-review-observation.jsonl` 只旁路持久化已有完成事件与 allowance 函数的选择时刻、截止/额度；原函数调用一次，返回与异常不变。阶段墙钟含工具与本地工作，截断 span 标 censored；HTTP 请求数另以真实 schema 和物理账本复核，不能把网络延迟当完整阶段耗时。原生事件的完整副本保留 RunJournal 投影会省略的 completionReview/requirementsAccepted。

离线联合验证：`27` 个新反例/集成测试加原 trigger runner/auditor `80` 项，合计 `107 passed in 8.06s`。涵盖真实本地 runtime、实际公开 pytest、fake adapter 失败后的 unknown usage、observer 不吞原 deadline 异常，以及终态、缓存权威、阶段限额与注册前拒绝。测试网络入口替换为进程内脚本，不进行真实外发。最终日志为 `final-offline-regression.txt`；真实冻结后的 source provenance / registration commit 检查须在 root 创建 source commit 后再做。
