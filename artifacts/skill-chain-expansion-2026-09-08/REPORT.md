**Skill 链路扩展实测 · 2026-09-08**

本轮新增 30 道任务，复跑 10 道旧题，另做 4 次预算诊断。40 次主测试均保留首次结果；生产代码、路由配置和旧结果未改动。新增正例的正确加载及正文交接为 **21/24（87.5%）**，还不能认为稳定达到 90%。

**主测试结果**

新任务有 24 道应使用 Skill、6 道不应加载正文；旧题复跑有 8 道正例、2 道负例。加载与交接按真实工具轨迹评分，答案质量由代理逐题复核，不是人工金标。

| 指标 | 30 道新增任务 | 10 道旧题复跑 |
|---|---:|---:|
| 正确加载所需能力，无无关附加加载 | 22/24（91.7%） | 7/8（87.5%） |
| 正确加载，且完整正文进入后续已完成模型调用 | **21/24（87.5%）** | **7/8（87.5%）** |
| 加载后证据读取及阶段依赖符合 Skill 协议 | 21/24（87.5%） | 6/8（75.0%） |
| 不应加载正文的负例发生误触发 | 0/6 | 0/2 |
| 核心用户任务完成（代理复核） | 25/30（83.3%） | 9/10（90.0%） |
| 完整答复严格正确（代理复核） | 18/30（60.0%） | 5/10（50.0%） |
| 正常完成 / 步数耗尽 / 调用错误 | 29 / 1 / 0 | 10 / 0 / 0 |

“正文交接”需要正文摘要校验一致、出现在实际后续请求中并得到模型响应；仅 load 返回成功不够，也不能证明模型遵循了正文。流程分另检查加载后的证据读取及条件阶段顺序，允许独立任务并行。指定报告标题和词串的协议分为 16/24、6/8，只是格式观察，不能当作答案准确率。负例“未误触发”只表示未加载或通过普通读取暴露正文，不等于输出格式正确，例如 E17 多写了翻译之外的内容。

**新增能力能否不靠硬编码触发**

新增了 4 种此前没有的 Skill，只在隔离夹具中加入 SKILL.md 并扩展所属 Directory 描述；没有增添技能名称或关键词路由规则。目录总量保持 1000 个 Skill、62 个 Directory。新增能力对应 6 道题全部正确加载并交接正文：

| 新能力 | 任务 | 加载及交接 |
|---|---|---:|
| scheduler/business-day-window：当地业务日转 UTC 边界 | E21、E25 | 2/2 |
| config/dns-resolution：DNS 与负缓存时间线 | E22 | 1/1 |
| http/client-lifecycle：失败路径资源释放 | E23 | 1/1 |
| batch-export/snapshot-pagination：快照分页与写出重放 | E24、E26 | 2/2 |

这支持“目录描述 → 展开技能 → 模型选择加载”能发现新增能力。它测试的是新增文件加明确目录描述；没有测试自动生成目录摘要的质量，也不是 1000 个真实用户技能的随机样本。

目录构成补充：新增组的 1000 条为 15 个基础合成流程＋4 个新增合成流程＋12 个近邻干扰项＋969 个批量模板干扰项；旧题复跑为 15＋985。每条都有实际 SKILL.md 并进入注册表，但多数干扰正文只有通用范围检查。目标 Directory 描述直接包含目标能力，部分近邻描述明确排除目标操作，因此这比真实、重叠且质量不齐的千级技能库更容易区分。千级表示注册条目规模，不表示 1000 个成熟独立能力。文件保留在每题隐藏的测试工作区，没有安装进正式 Skill 库。[完整目录清单及生成定义](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/CATALOG.md>)。

**失败发生在哪里**

1. **模型跳过发现。** E06 配置优先级、E10 启动信息和复跑 R03 都直接读代码，没有 browse/search/load。相关 Directory 卡已进入上下文，轨迹未显示策略拒绝或候选不可见。E10 最终答对，说明“不用 Skill”和“任务答错”必须分开。
2. **发现太晚。** E08 前 8 轮大量读文件，第 9 轮才展开 logging，第 10 轮 load 成功后触及步数上限，正文没进入下一次模型请求，也没交付答案。
3. **加载后仍有交付和事实错误。** E01/E21/E24 答复不完整；E03/E29 将有限性能证据写成确定归因或收益，E06/R03 错把 Python 函数参数理解为惰性求值，E22/E23/E26 也有边界解释不严谨。核心结论正确不保证附带解释正确。
4. **条件流程会被压缩。** R05 在同一轮加载两项 CI 技能，未完成第一项规定的加载后取证再选择下一项；两项正文都交接成功，但严格流程分失败。R08 这次修正了旧运行的顺序问题，流程稳定性会双向波动。
5. **工具错误文案增加干扰。** R10 查找不存在的 Skill 时，权限拒绝附带“意外错误、请重试”，未知目录又附带资源限额解释，产生一次无用重试。最终缺失判断正确，这不是 provider 或网络故障。

10 道旧题使用相同原文、同一夹具摘要、相同目录和 8 轮/8000 总输出预算。所选 8 个正例此前加载及交接为 8/8，本次为 7/8，下降来自 R03；严格流程两次都是 6/8，但具体失败题不同。观察器本轮修复了截止时间和取消信号转发，因此这属于近似条件复跑；不能将任何差异全归因为模型随机性。主测试未出现调用超时。

**4 次预算诊断，单独报告**

根据主测试失败事后选题，每题只补跑一次，保留原始失败。E01/E21/E24 单次输出上限从 1600 调至 4096、总输出从 8000 调至 12000，步数仍为 10；E08 只将步数 10 调至 16。模型、任务、夹具及单次调用 60 秒截止保持一致，实际请求已核对预算生效。

| 任务 | 本次诊断结果 | 可支持的判断 |
|---|---|---|
| E01 队列事务 | 第 8 次调用触及 60 秒截止；无最终答复 | 放宽输出未解决本次交付 |
| E08 日志脱敏 | 4 次调用完成，严格复核通过 | 4 轮低于原上限，不能归因于改成 16 步 |
| E21 时区边界 | 答案完整，核心正确；严格失败 | 最后一次调用报告输出 2230 token，答复完整；仍写错时差及部分时段解释 |
| E24 导出分页 | 第 4 次调用触及 60 秒截止；无最终答复 | 放宽输出未解决本次交付 |

诊断共 4 次，核心完成 2/4、严格正确 1/4，调用截止 2/4，全部保留在分母。提高额度不是已验证的普遍解法；调用时限、发现时机和模型答复质量需一起考虑。原日志没有保存 provider finish_reason，1600 token 命中上限与答复截断只构成线索，不能认定所有不完整答案都由这一项造成。诊断评审由另一代理执行，E21/E24 与题目作者有重合，已披露在记录中。

**下一步优化优先级**

先让主模型在大量读文件之前明确一次发现决策：选择相关目录，或明确无需 Skill；保留负例退出路径，避免强制所有任务加载。再为加载后取证和最终答复预留预算，减少反复宽泛扫描，修正不存在技能的错误分类。最后约束答案区分记录事实、推断和待验证建议，用新题及其他真实仓库多次复跑验收。以上是本轮证据支持的下一轮方案，本轮未实施生产改动。

**实验边界与复现**

使用已配置 glm-5.3-flash、model 路由，intent hints 与 prefetch 关闭；不是带意图线索的对照实验，不能据此评判意图模块价值。模型在真实 Agent Loop 中使用目录浏览、搜索、load_skill 和只读文件工具；只读工具范围、关闭工作链和记忆均属于评测隔离条件。新题最多 10 轮、旧题 8 轮，主测试单次输出 1600、总输出 8000、单次调用 60 秒。4 个独立进程并发，冻结预期标签不进入模型输入。

30 道新题覆盖长上下文干扰、中英混合、近邻能力、独立复合任务、证据条件成立/不成立、元信息查询、明确禁用和不存在技能。其中多题共享能力与夹具，并非 30 种独立新能力。冻结前审查将 E26 的相关幂等技能列为允许辅助，主流程要求未删，冻结版本及调整记录均保留。

新增正例 21/24 的题级 Wilson 95% 描述区间约为 **69.0%–95.7%**；旧题 7/8 约为 52.9%–97.8%。这些是相关、人工设计题目的描述近似，不是自然用户分布的置信保证。不能把新题、重复题与事后挑选的预算诊断合并成一个生产准确率。主测试 205 次模型调用，provider 记录输入 1,450,532、输出 75,476 token，缓存读取字段合计 1,141,504，usage 无缺失；缓存字段不与输入简单相加计费。诊断中两次超时调用用量未知，不伪造为零。

离线验证：评分器 7 项测试、调用观察器 6 项测试通过，两个实际 Agent Loop 离线 smoke 通过（均不进入准确率），预算观察器确认实际转发 4096。运行前后冻结文件摘要一致，minicode 生产 Python 文件与上一轮一致。未跑新的生产回归，因为本轮没有生产代码变化。

资料：[完整任务与冻结预期](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/cases.json>)；[40 次分层指标](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/summary.json>)；[40 题代理复核](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/reviews.json>)；[4 次预算诊断](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/budget-diagnostics.json>)；[来源完整性校验](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/skill-chain-expansion-2026-09-08/source-verification.json>)；[方法与运行前审查](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/audit-notes.md>)；[原始执行器](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/runner.py>)。每条汇总均绑定原始 run SHA，原始请求、响应、工具轨迹和夹具保留在下表链接的同目录。

**逐题结果**

下表“检索交接”在正例表示正确加载并交接，在负例表示未误加载正文；“流程”仅适用于正例。“核心/严格”是用户答复的代理复核，独立于检索。

| 题目（原始运行） | 检索交接 / 负例无误触发 | 流程 | 核心 | 严格 | 调用数 |
|---|---|---|---|---|---:|
| [E01 历史干扰／队列事务](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E01/run.json>) | 通过 | 通过 | 失败 | 失败 | 7 |
| [E02 重试与尝试次数](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E02/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E03 自然比喻／SQL 扫描](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E03/run.json>) | 通过 | 通过 | 通过 | 失败 | 5 |
| [E04 缓存陈旧窗口](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E04/run.json>) | 通过 | 通过 | 通过 | 通过 | 5 |
| [E05 最小授权范围](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E05/run.json>) | 通过 | 通过 | 通过 | 通过 | 7 |
| [E06 旧交接／配置优先级](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E06/run.json>) | 失败 | 失败 | 通过 | 失败 | 5 |
| [E07 Python 循环导入](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E07/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E08 日志隐藏字段](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E08/run.json>) | 失败 | 失败 | 失败 | 失败 | 10 |
| [E09 独立 API 与日志](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E09/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E10 入口／环境／健康路径](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E10/run.json>) | 失败 | 失败 | 通过 | 通过 | 4 |
| [E11 已知测试阶段／状态隔离](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E11/run.json>) | 通过 | 通过 | 通过 | 失败 | 9 |
| [E12 发布失败／条件不成立](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E12/run.json>) | 通过 | 通过 | 通过 | 通过 | 5 |
| [E13 数据库耗时／排除支付](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E13/run.json>) | 通过 | 通过 | 通过 | 通过 | 5 |
| [E14 支付耗时／条件成立](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E14/run.json>) | 通过 | 通过 | 通过 | 通过 | 7 |
| [E15 名称作为数据／转换](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E15/run.json>) | 通过 | — | 通过 | 通过 | 1 |
| [E16 禁用 Skill／计算比值](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E16/run.json>) | 通过 | — | 通过 | 通过 | 2 |
| [E17 旧任务结束／仅翻译](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E17/run.json>) | 通过 | — | 失败 | 失败 | 1 |
| [E18 仅查询技能元信息](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E18/run.json>) | 通过 | — | 通过 | 通过 | 2 |
| [E19 不存在的指定技能](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E19/run.json>) | 通过 | — | 通过 | 通过 | 2 |
| [E20 算术／条件不成立](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E20/run.json>) | 通过 | — | 通过 | 通过 | 1 |
| [E21 新能力／当地日边界](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E21/run.json>) | 通过 | 通过 | 失败 | 失败 | 6 |
| [E22 新能力／DNS 负缓存](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E22/run.json>) | 通过 | 通过 | 通过 | 失败 | 5 |
| [E23 新能力／客户端释放](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E23/run.json>) | 通过 | 通过 | 通过 | 失败 | 6 |
| [E24 新能力／快照分页](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E24/run.json>) | 通过 | 通过 | 失败 | 失败 | 4 |
| [E25 当地日／反事实校验](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E25/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E26 导出写出重放](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E26/run.json>) | 通过 | 通过 | 通过 | 失败 | 4 |
| [E27 独立授权与日志](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E27/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E28 独立配置与入口](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E28/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [E29 数据库分支后查 SQL](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E29/run.json>) | 通过 | 通过 | 通过 | 失败 | 7 |
| [E30 CI 发布失败／反事实](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/E30/run.json>) | 通过 | 通过 | 通过 | 通过 | 4 |
| [R01 旧题 C01](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R01/run.json>) | 通过 | 通过 | 失败 | 失败 | 7 |
| [R02 旧题 C03](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R02/run.json>) | 通过 | 通过 | 通过 | 失败 | 5 |
| [R03 旧题 C06](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R03/run.json>) | 失败 | 失败 | 通过 | 失败 | 6 |
| [R04 旧题 C08](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R04/run.json>) | 通过 | 通过 | 通过 | 失败 | 6 |
| [R05 旧题 C12](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R05/run.json>) | 通过 | 失败 | 通过 | 失败 | 6 |
| [R06 旧题 C13](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R06/run.json>) | 通过 | 通过 | 通过 | 通过 | 6 |
| [R07 旧题 C14](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R07/run.json>) | 通过 | 通过 | 通过 | 通过 | 4 |
| [R08 旧题 C15](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R08/run.json>) | 通过 | 通过 | 通过 | 通过 | 7 |
| [R09 旧题 C17](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R09/run.json>) | 通过 | — | 通过 | 通过 | 3 |
| [R10 旧题 C19](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/.repair-work/skill-chain-expansion-2026-09-08/live-01/R10/run.json>) | 通过 | — | 通过 | 通过 | 5 |
