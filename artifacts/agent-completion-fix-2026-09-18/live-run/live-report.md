# CodeLoop 真实模型复测

**结论：本次真实运行验证了预算触顶后的部分结果交接。样例修复任务尚未完成。** 运行还暴露一个新事件未注册的日志遗漏，已补修并通过本地真实日志回归。

## 实际运行

- Run：`run_ad516d64824a4663807c9c29f374c5fc`；生产 `AgentTurnRuntime`，模型 `glm-5.3-flash`，不是模拟响应。
- 时间：109.57 秒。
- 调用：8 次模型调用；8 次均有 provider 返回的用量。输入 158,738 tokens，输出 2,985 tokens，总计 161,723。
- 工具：10 次，只读——list_files 1、file_tree 1、grep_files 3、read_file 5。
- 子 Agent：0。没有验证子 Agent 路由或调用阿里云。
- 实际预算：8 次调用、400,000 tokens、$5，符合本次授权范围。

## 收尾证据

第 8 次模型调用用于总结；从这次调用开始之后，没有任何工具执行。最终回复保留了已定位的实现和 `test_files[:10]` 截断假设，同时明确说明未改代码、未写回归、未运行 pytest/ruff，任务未完成。

消息标记 `_partial=true`；规范任务状态 `unknown`，`completionSucceeded=false`、`goalAchieved=false`、`verificationStatus=unverified`。任务退出没有被误报为目标达成。

目标源码与测试文件均匹配基线 Git HEAD；仅产生了运行时锁文件 `.minicode-memory-authority.lock`。本轮未向样例仓库提交修改或创建 PR。

## 实测发现并修复的日志问题

真实日志缺少 `execution.completion_limited`：新的收尾与延长事件尚未登记在 `RunJournal.EVENT_TYPES`，被持久化层拒收。模型回复、partial 标记和 task.outcome 独立证明收尾逻辑正常，但原始日志有此观测缺口。

已登记 `execution.completion_limited`、`execution.steps_extended`，新增3项测试通过实际 Agent 循环、RunObservation、RunJournal 落盘再重开读取事件。修改前3项全部失败于事件缺失；修改后含相关既有测试 **131 passed**。Ruff、编译、diff检查通过。此日志补修使用本地确定性测试验证，没有追加真实模型调用；原始运行日志保持不改写。

## 结论范围

这次小预算运行验证了真实模型的触顶收尾；没有证明完整的定位→修改→测试→PR闭环，也没有现场触发35步窗口延长或50步硬上限。后两者仍以之前的本地回归测试为证。一次运行也不代表稳定成功率。

## 证据

- [模型最终回复](response.md)
- [独立审计（16项事实核对）](independent-audit.json)
- [运行统计](summary.json)
- [排除内部推理字段的公开过程](public-transcript.json)
- [日志回归修改前失败](journal-regression-before.txt)
- [日志回归修改后通过](journal-regression-after.txt)
