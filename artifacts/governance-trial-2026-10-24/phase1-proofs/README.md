# Phase 1 定向证据包

本包保存首刀的同字节行为对照、独立审查及被否决的中间抽取，不代表全量验收已经通过。

- 原基线 `9f936942` 与最终候选相同 95 项测试均通过；最终定向及邻接 336 项通过。
- 独立审查发现恢复成功后中断观察会恢复旧列表；同一 8 项测试在原版通过、初抽取失败、修正后通过。中间错误源码保留在压缩包内。
- 会话域验证包含真实并行共享对象身份检查。独立 loop 审查修复后 13 项通过；会话域由原实现者兼容复核，不声称独立双盲审查。
- `manifest.json` 固定每个原始文件与压缩包的 SHA-256，打包后已逐成员解压比对。不同阶段及重叠测试日志不应相加当成独立覆盖。

在仓库根目录恢复到空目录：

```sh
mkdir /tmp/codeloop-phase1-proofs
tar -xzf artifacts/governance-trial-2026-10-24/phase1-proofs/proofs.tar.gz -C /tmp/codeloop-phase1-proofs
```

`implementation/implementation-report.json` 给出源码与测试 SHA、AST 口径和执行结果；`review/independent-code-review.md` 解释审查发现及修正。原始脚本含当时临时目录路径，重跑时须按记录的提交及 SHA 重建目录，不能把该路径当成公开下载地址。正式随机全量结果另行记录。
