# 两格 bounded 验证：独立只读审阅

2026-10-23。审阅当前 `scripts/native_review_bounded_validation.py`、`scripts/audit_native_review_bounded.py`，并追到既有 gen-2 / trigger / common executor、物理 transport 和生产完成门的调用路径。只读审查，没有调用 provider、重跑模型格、修改脚本或冻结材料。

结论：本次发现的门定义与时间归因问题已在冻结前修正；复核最后修改后，未发现尚未解决的 P1/P2 阻断。这个结论只覆盖验证执行与审计契约，不预言 stock 会通过，不把离线测试当作真实模型验收。

## 已核实的执行边界

- `prepare` 精确选取原冻结题包中的 stock、config 两个对象及对应公开文件、oracle、reference 和 proof；顺序固定 stock → config，各一次 auto。没有重写题目或加入新的提示。
- `assemble_host`（runner:313 起）以指定 SHA256 的原 trigger zip 为底，只允许 11 个生产文件及 2 个新 harness 文件的精确覆盖清单，覆盖内容用 `git show <sourceHostCommit>:<path>` 读取。宿主清单逐文件标记 baseline/commit 来源；工作区其余脏改动不会被隐式采集。
- `dispatch`（runner:374 起）先验证预注册提交及注册字节，再进入归档宿主的 `-I -B` entrypoint；运行前校验实际生产、harness、route 与 transport hash。既有持久执行证据会拒绝同格重跑；公共 hydrate 只装 shared files / case files，私有 oracle 与参考修复不交给模型。
- 旧 common 执行链继续实际设置 40 physical requests / 500000 tokens / 600 秒截止、8192 输出上限、300/120/30 秒 timeout，固定 `glm-5.3-flash`、temperature 0、adapter retry 0。默认 continuous 实现路径不被旧 implementation phase quota 替代。SQLite 请求账本及父请求正文仍由既有独立审计复核。
- TLS 使用 `/etc/ssl/cert.pem` 的注册 SHA256，freeze / dispatch 都核对字节并设置 `SSL_CERT_FILE`，保留证书与主机名验证；没有禁用验证的旁路。
- `NativeObservation`（runner:237 起）只在真实 create/select allowance 调用及既有 host events 周围记录私有观察；保留原返回值与异常，不改角色、prompt、schema、阶段额度或生产接受语义。阶段耗时包含本地工作，未完成区间标为 censored；实际 native HTTP 另由 provider-visible terminal tool schema 与物理账本归类。

## 冻结前发现及修复复核

1. config unknown usage 原先会被额外的全两格 usage 门否决，偏离“config 只描述”。已去掉额外行为门；unknown 继续原样记账并标 unverifiable，未伪造零成本，也未取消证据有效性审计。
2. 阶段原先仅核验请求数及 deadline 不刷新，漏验 90/150/60 秒上限。现在同时要求 stage deadline ≤ chain deadline、≤ 首次进入该阶段时间加规定秒数，native authority 决定时间也须位于相应阶段和链截止以内。
3. 原先总 row duration ≤ 600 会抹除预算内 rejected 后的父侧超时。现在 gate 按真实审查终态及其时间，父超时、最终未交付仍单独保留；approved 则仍须宿主 `task.outcome` 成功、`completionSucceeded=true`、正常返回形成 accepted final。
4. 原先最后一次相同候选的缓存负裁决必须有新的 HTTP，导致合法重交接假阴性。现在仅同 executor / candidate、完整实质决定相等的 rejected 可以追溯较早的真实 native authority；只排除既有外层 envelope/storage 七字段，嵌套 findings、probes、checks、source bindings 不排除。晚到的精确重交接按原 authority 时间，并单列 `delayedHandoff`。换候选、改 findings 或后续真实 `budget_exhausted` 都不能借旧 rejected 过门。

最终审计从匹配 review-start 的最后审查决定取终态；没有新 native review 的公开检查失败不会冒充新的 native 终态。unknown/inconclusive、缺 review/authority、`budget_exhausted` 均不通过；config 只描述；Phase 2 采纳率/trigger 指标不参与本门。oracle 修复成功与 accepted final 分别报告。

独立阅读了对应反例与已有离线日志：`validation-materials/final-offline-regression.txt` 为新 27 项及原 trigger 80 项合计 **107 passed in 8.06s**。这次独立审阅没有重复运行测试；结果来源是该已留存日志。正式两格仍需在最终注册提交后各执行一次，届时依据原始账本与私有观察重新出具审计结果。
