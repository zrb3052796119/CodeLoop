# CodeLoop 治理基线 — 2026-10-24

**当前状态：验收进行中，尚未满足合入 main 的条件。** 分组修复通过不等于当前全部源码通过随机顺序全量测试。

## 可复现环境

- 原始开发 HEAD：`88ec62d0ca7d8b6b090c31fda1835642d7e0e603`；原始 main：`638b7dd1`。
- 本机：macOS / Python 3.12.8 / pytest 9.1.1 / pytest-randomly 4.1.0。
- 全量诊断随机种子：`20261024`；禁止 `-x`。CI 使用 `github.run_number`，每个 OS/Python 矩阵任务打印并保存相同种子和 JUnit XML。
- 测试在不可变源码副本中运行；使用独立 HOME，剥离继承的 `MINI_CODE_*` 配置。HTTP 测试允许本机 loopback；不借用真实模型凭证。
- 原始 WIP、源码哈希、原始日志和 XML 保存在本机 `/private/tmp/codeloop-governance-20261024/`。最终提交的绿基线及证据哈希待本轮验收完成后填入。

## 已验证项目

| 项目 | 结果 | 限制 |
| --- | --- | --- |
| `compileall` + `ruff check minicode --select=E,F --ignore=E501` | 通过 | 是项目现有 scoped lint，不宣称全仓全部规则零问题 |
| `evaluate_agent_quality.py --profile current` | 88/88 checks，通过 | 离线开发集，不是实际任务能力证明 |
| `evaluate_agent_quality.py --profile a` | 57/57 checks，通过 | 同上；冻结输入及阈值未改 |
| MCP SSE 错误路径 | 新测试 4/4 通过；初始源码 3 failed/1 passed | 同字节测试确认 NameError 修复，非简单文字断言 |
| focused include search | 19/19 通过；相关 139/139 通过 | 16 个旧测试未 opt-in bounded；是 fixture 契约迁移，不是生产权限修复 |
| v41 staged closure | 24/24 通过；相关 193/193 通过 | 40 份历史 pin 未改；active 仍 v40；不代表 v41 获准激活 |
| functional audit | 207 能力、59 注册工具、0 findings；7 项审计回归通过 | 129 pass / 55 partial / 1 unavailable / 6 blocked / 16 not_reachable；不把未测能力算通过。实际历史源码反例 5 failed / 2 passed |
| 原有 core WIP 依赖组 | 随机 76 文件：1060 passed / 1 failed，641.94 秒；提示契约修正后该文件 18 passed | 失败为旧提示词断言；原始日志保留，不表述为 1061 项同次全绿 |
| skill-chain 干净副本依赖 | 本地及无 `.repair-work` 副本各 29/29 通过 | 最小 evaluator/scorer 源码及冻结输入已纳入版本控制 |
| 报告压缩归档 | 恢复/公开链接 13/13；相关消费者 132/132 通过 | 仅归档 4 份生成报告；8 份大测试/回放输入保留原路径与字节 |

## 全量诊断记录

| 尝试 | 不可变诊断提交 | 结果 |
| --- | --- | --- |
| R1 | `bd86d0198b00f32a6afd2b4d6aea1bdb1999547b` | 收集阶段 2 errors / 4 skipped，36.99 秒；两个跟踪测试依赖忽略目录，已修复 |
| R2 | `cb327c3796b0d34fb3235a15fec6f1988f072adf` | 收集 14,849 items / 4 skipped；完成 5355 passed / 58 failed 后，诊断日志插件被禁止文件访问的测试拦截并中断（1809 秒）；不是全量结果 |

R2 的局部事件日志和原始失败均保留。随后仅对未完成节点做诊断续跑；续跑不作全量通过证据，最终候选必须从头运行且不加载该逐用例文件日志插件。

这些诊断提交位于本机临时副本。R2 启动后新增的修复不能倒填到 R2 的源码身份；完成修复后必须对最终候选重新验收。

原报告的 `14,522 passed / 359 failed / 22 errors` 没有取得对应源码和原始日志。本轮已证明一处 session 路径污染：隔离文件单跑 16 passed；前置跨进程保存测试后 2 failed / 15 passed。污染测试在父进程直接修改 session 全局路径未恢复；改为 monkeypatch 后同序列 17 passed、相关两文件 32 passed。未改隔离断言或通用 conftest。这不能证明原报告约 190 项 workflow 失败都是同一原因；其余失败仍逐族区分生产缺陷、fixture 漂移与环境约束。

## 反馈回路建议

本轮先记录成本，不新增 gate。开发时用变更相关单测（秒级）和有明确边界的集成回归（分钟级）；PR/合并候选保留随机全量。历史实测约 34 分钟只作参考，本轮实际时长以最终 XML/运行元数据为准。后续拆档应由实际测试耗时和隔离依赖决定，不能凭文件名把慢测试整体豁免。
