# 运行前独立核对

日期：2026-10-23。本记录不使用模型评测结果。

## 任务与隐藏验收

Root逐项读取八题公开需求、基线、参考修复与隐藏oracle。六题分别涉及分页排序/游标/页面组合、事件身份/重试/计数、折扣资格/逐行计价/订单上限、库存聚合/顺序消耗/汇总、时区区间/报告过滤、递归覆盖/环境值解析；两题明确给出单函数位置。全部只有2至3个自然接口（负例1个），没有凑文件、凑读取次数或强制委派。

冻结前提出并完成的修正：隐藏检查保存可变actual时须快照，避免后续修改使收据与比较时点不符；分页和折扣补直接公共接口断言；配置合并明确新字典中的None语义，并覆盖来自override的嵌套对象独立性。pack-v1/v2是没有模型调用的离线迭代，保留在材料目录；正式输入只取pack-v3。

Root读取pack-v3重新核对八题id、顺序、标签、oracle实际值/期望值与passed的一致性、公开检查退出码、目标反例退出码及task/reference/proof哈希。8题共83个检查，基线45个失败，参考0个失败；公开基线和参考均通过。源生成器只用标准库与本地pytest，无生产模型导入或远端调用。任务包的generation为trigger-v1，表示本套合成构造版本；本实验phase为trigger-gen2，协议为multi-agent-trigger-v1。

| 题目 | oracle检查数 | 基线目标失败数 | 源码文件数 |
| --- | ---: | ---: | ---: |
| record-pagination | 19 | 9 | 3 |
| import-retry | 9 | 8 | 3 |
| cart-discounts | 12 | 6 | 3 |
| stock-allocation | 7 | 5 | 3 |
| report-timezones | 10 | 5 | 2 |
| config-overlay | 8 | 6 | 2 |
| limit-clamp | 8 | 3 | 1 |
| extension-case | 10 | 3 | 1 |

公开题目没有委派要求或triggerExpectation标签，参考和oracle不进入files映射。一次临时文本扫描错误地把公开合法的“positive-limit validation”当作正例标签泄漏；修正扫描对象后通过，没有为该扫描更改题目。

## 运行与审计

运行前联合回归：8个测试文件，**164 passed in 6.63s**，退出码0；在ps可用环境运行，未修改原清理断言。详见prereg-joint-regression.txt。范围包括新任务构建、运行器、独立审计/分析及旧gen-2对应框架。git diff --check通过。

实际离线runtime smoke使用真实adapter/工具链，首请求真实system/task形状指引与直接写能力均可见；8个读取批次后，第9个实际请求包含动态建议，同一物理响应选择write_file。完整正文在send前与ledger call ID绑定，成功、失败和大正文均覆盖，未记录认证headers。原生completion/coverage审查费用保留且与task采用区分。此为脚本化模型机制验证，不是付费效果样本。

独立审查确认auto保留直接工具和task、默认continuous、600秒实际期限、真实Git字节guard先于provider初始化、冻结宿主入口与无重跑检查。混有启动参数或缺ID的poll/cancel保守记ambiguous；无效审计报告只给待验证记录值。异步queued/started取消均通过真实lifecycle反例：合法终态ACK可审计，无返回结果不计采用；queued取消单列观测，不制造执行记录；无ACK、错误owner、身份/哈希漂移仍拒绝。runner与只读审计计数一致。生产协议未因这些评测观测新增字段或角色。

最终候选pack-v3与执行框架均封板。官方run-v1已冻结，8题公开基线预检通过；独立partial审计valid=true、0/8，无执行记录。宿主归档SHA256为a118a493563821da50b8d2e15385f9cdc686ee433c36e44b8f0a3990b88d7d3d；9项冻结材料将与manifest一起提交。尚无Phase2模型请求。
