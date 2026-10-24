# Phase 2 冻结更正 r2：首次模型调用之前

本版明确替代并撤销[原冻结](../phase2-freeze/README.md)，旧 manifest 和记录保留。两版此时均为 **0 模型试次 / 0 结果**。离线验证发现评测器把消费者写入 ledger 的延迟误算为流式空闲：字节已经排队，仍可能在读队列之前超时。修复先检查队列，再按 reader 返回时间检查真正的读取间隔；绝对 deadline、取消和迟到 EOF 仍有检查。它不声称知道 socket 缓冲区的到达时刻。

生产 `minicode` 字节仍与 P0 `9f936942` 完全一致。只更新评测器 `evaluation_transport.py` 及其回归测试，源提交为 `8b15640a`；同字节边界检查旧实现 3 failed / 7 passed → 修复后 10 passed，87 项相邻回归通过。原 API 执行记录不存在，不是在看到模型结果后调整实验。

32 个 single/auto cell、16 个历史任务、提示、验收、源快照、顺序、GLM 5.3 Flash 路由、角色轮数和预算均不变。新目录只重定位 oracle-support 的绝对路径；归一化后 tasks 完全相同。两版 manifest 仅 createdAt、harnessSha256、frozenCodeArchiveSha256 和该路径衍生的 taskSha256 不同。

新 manifest SHA-256：`32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332`。经济 token / call / money 三项上限仍为 null；角色显式12/8/15，任务1800秒，请求300秒、idle120秒、连接30秒。原始源码 ZIP 由 manifest 绑定，执行结束后统一归档。

`freeze-record.json` 记录撤销关系、来源与变更范围；`preparation-equality.json` 保留准备阶段的相等检查。正式运行前再校验发布记录、独立审查、来源与零既有结果；冻结后不换题、不改裁判、不重跑失败 cell。

独立复核见 `independent-review.md/json`：两个 ZIP 均330成员，仅 transport 内容改变；999个support文件校验通过；tasks只含8处准备目录路径替换，归一化后原字节相等。
