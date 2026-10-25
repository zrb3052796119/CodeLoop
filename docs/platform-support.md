# 本期平台支持与验收范围

2026-10-25，用户在审阅 Windows 完整诊断后明确选择：**本期正式支持 Linux/macOS，原生 Windows 标为实验性，保留可见失败诊断和移植清单**。这是对既有 README 平台承诺的显式收窄，不是将旧 Windows 失败改判为通过。P1 候选 `e6492f9b` 已完成本期正式平台验收，Phase 1 GO，已通过 [PR #2](https://github.com/zrb3052796119/CodeLoop/pull/2) 合入 main `ed21acb3`。 合入后 main seed `52` 另有一项 deadline fixture 失败，修复及最新 CI 状态见[基线跟进](release-baseline-2026-10-24.md#合入后的-seed-52-跟进)；平台范围不变。

| 能力 / 验证 | Linux、macOS（Python 3.11 / 3.12） | 原生 Windows（Python 3.11 / 3.12） |
| --- | --- | --- |
| 安装、编译、scoped Ruff、离线 current 质量回放 | CI 必过 | 实验性子集检查必过 |
| packaging smoke、声明的入口导入、cron 空配置、gateway 本地接口、指定9模块的已安装 wheel 来源 | CI 必过 | 原生检查已有通过证据，之后继续必过；不等于实际模型工作流通过 |
| 完整 runtime suite | 正式支持目标，必须完整通过；失败仍阻塞本期合入 | 独立完整诊断继续运行；已知失败，不作正式支持承诺 |
| workflow / Memory / 安全证据 I/O / 进程树 | 由正式平台完整套件验收 | 移植未完成，可能显式拒绝或失败 |
| POSIX 实验守护与恢复脚本 | 保持原安全与清理契约 | 尚无等价原生 Windows 契约 |

这里的“支持”仍是研究项目的验收目标，不是生产级保证。WSL 属于 Linux 执行环境，不能作为原生 Windows 通过证据；本轮也未单独认证 WSL。

当前 [CI `40130139264`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40130139264) / seed `50`：Linux Python 3.11/3.12 各 **14,952 passed / 19 skipped**，macOS 两版各 **14,963 passed / 8 skipped**，均 **0 failed / 0 errors**。PR head 为 `e6492f9b`，实际 checkout merge 为 `b0091e50`。最终六项成功来自 attempt 1 的五项与 attempt 2 重跑的 macOS 3.11 整个 job；原上传 `ENOTFOUND` 失败和缺失 XML 保留，其他五个新 API ID 属继承记录。Windows 两项只验实验性包/静态子集，不能据此消除完整 runtime 的已知失败。详见[最终支持矩阵证据](../artifacts/governance-trial-2026-10-24/phase1-supported-matrix/README.md)。

## CI 状态如何读取

[CI](../.github/workflows/ci.yml)保留三 OS 的安装、编译、lint、离线质量与包检查，完整套件仅对本期正式支持的 Linux/macOS 作为必过验收。Windows job 名明确为 Experimental package checks。

[Windows runtime diagnostics](../.github/workflows/windows-runtime-diagnostic.yml)单独跑两版 Python 的完整、未过滤测试套件；没有批量 skip、`continue-on-error` 或成功退出码替换。该 workflow 会真实显示失败并始终尝试上传已生成的 JUnit，它不属于本期正式 runtime 的合入要求。每个 workflow 保留自己的随机 seed 与原始结果。CI 主徽章只表示前述 CI 范围，不代表 Windows 全功能通过。当前仓库 main 未设置服务器端分支保护；此处描述的是本期维护者采用的合入政策，没有暗中修改远端保护设置。

## 已知原生 Windows 结果

旧候选 `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 的 CI run `40114448888`（实际 merge ref `5d3f61f77361dc107cb8bb2cc23e582563c16fcc`）完整失败：

| Python | passed | failed | errors | skipped |
| --- | ---: | ---: | ---: | ---: |
| 3.11 | 13,222 | 1,390 | 246 | 112 |
| 3.12 | 13,217 | 1,395 | 246 | 112 |

两版原始日志、JUnit、逐事件分类、13个来源文件的版本对照及旧平台承诺均保存在[Windows 完整诊断证据包](../artifacts/governance-trial-2026-10-24/windows-platform-diagnostics/README.md)。failures/errors 记录包含 setup/teardown，不能看成同等数量的独立根因。后续 `6e7ba88f` 的原生安装/打包/committed-archive 步骤已通过；该事实不覆盖其完整套件，也不能消除上表。

## 移植清单

- 可信 Git：现有 `MINICODE_TRUSTED_GIT_EXECUTABLE` 绝对路径配置可用于宿主注册；不得转为信任任意工作区或 PATH 可执行文件。
- 安全 I/O：为 candidate、verification 和恢复脚本提供 Windows 等价的不跟随链接及目录身份保证；不能把 `O_NOFOLLOW` 等标志直接替为零。
- 持久化：curator 权限、目录同步、Memory portable 创建顺序、目录长短名/重解析点身份、journal 字节计数。
- 路径契约：原生盘符和反斜杠、规范化的证据引用、越界/伪造/摘要绑定的负例。
- 进程树：POSIX 信号和 `/bin/ps` 的原生替代及后代终止证明；普通 terminate 不能自动当成等价。
- 编码及 fixture：UTF-8、文本/二进制换行、只在测试中直接引用 POSIX 属性的场景，以及未归因余项。

每一域需在原生 Windows 上保留故障反例、边界验证和最终完整矩阵。未来扩大支持范围应明确更新本文件；本期不新增控制论模块或能力 gate。
