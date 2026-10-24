# Phase 2 r3 冻结：发布时零模型调用

这是公开契约更正后的新完整 32 次注册，用户已确认执行。原 [r2 试跑](../phase2-r2-pilot/README.md)保留 5 次完成、1 次中断未判分和 26 次未运行，未改标签或选择性重跑。本目录发布时 r3 尚无模型调用或结果；之后的运行记录与这份冻结分开保存。

- 冻结时间：2026-10-24 19:14:59 UTC，即 2026-10-25 03:14:59 Asia/Shanghai。
- 注册 manifest：`eaa9645d3a2a39649d1e80c28ae3db39fc3c9bab89f8586e12515378babee5b2`，见 [portable 注册包](../phase2-preparation-r3/README.md)。
- 本次冻结 manifest：`b32718a77f1680cf2d7d465d655a95bdcdcf5c6c745c3d30e1b9e430193d3b01`。
- 任务：`7464fc17a4725efaecac103a0d0b6a65d7442ffbbab61687037716592707d933`；展开设计：`017c3b222abe6feea7937b082ce22e60a4b9670b594e98ffaa01e74c6fcdacf9`。
- 产品：`13151ca1031c9ec8b4ffec4fa23b43509946018a61fa8a01445aad65af4bccd8`；评测器：`15898623cbc94e6a313cee6032bd008dc691ac0fac8739711405d32d4044a4ef`。
- runtime ZIP：`97d69b81ffd80d4acdaa00e024e9caebf0338c3ec305b932b2ae5ce21edaee7a`，执行结束后归档。

[冻结复核](freeze-review.md)确认相对 r2 只有 createdAt、taskSha256、studyDesignSha256、frozenCodeArchiveSha256 四个 manifest 字段不同；330 个 ZIP 成员名称、顺序和解出字节完全相同，ZIP 本身变化仅来自分析计划文件的时间戳。999 个支持文件和当前 329 个 Python 源码逐字一致；同一 GLM 5.3 Flash 路由、32 次顺序、角色上限、时间及无限经济预算保持不变。新注册绑定通过，误用旧注册会被拒绝。该审阅者准备了 portable 包，但独立于 root 的冻结执行；root 已另做原始材料比对。

产品仍为 P0 `9f936942`，评测器沿用 `8b15640a`；不包含 P1 抽取与后续 PTY 输出修复，该已知限制没有被抹去。冻结当时 HEAD 为 `2706e00e`，仅有文档/证据新增未提交，生产和脚本字节未变；实际发布提交与启动 HEAD 将记录在运行 metadata 中。命令、返回值、真实耗时见 `freeze-invocation.json` 和 `freeze.log`。

正式调用前须发布本注册、冻结与旧试跑处置，校验工作树及源码、确认新目录没有既有运行结果。启动后不换题、不调验收、不重跑失败 cell；报告结束前保持执行 checkout 的生产与脚本不变。不得覆盖现有 manifest 或再次对本目录 freeze。新注册的目的、测量口径与解释限制见[说明](../../../docs/real-task-comparison-r3-2026-10-25.md)。
