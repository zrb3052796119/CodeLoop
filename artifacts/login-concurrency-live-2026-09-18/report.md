# CodeLoop 自主执行真实模型实测

结论：自主定位、规划、修改、回归测试和失败后重试已得到真实执行证据；这次完整任务未成功收尾。没有触发多Agent。

## 给CodeLoop的原始任务

> 我发现 test_runner 跑目录时会误报通过：一个包含12个 test_*.py 文件的目录里，有一个用例失败，直接用 pytest 能看到失败，但工具却报告通过。请帮我定位并修复，补充能防止复发的回归测试，运行相关检查，最后说明原因、改了什么和验证结果。

未向模型给出根因、修改位置、测试代码、Agent角色或工具顺序。宿主没有替它修复目标代码，也没有在运行中追加提示。

## 环境与边界

- 实际调用当前工作树CodeLoop生产入口 create_agent_turn_runtime → runtime.execute；无ScriptedModel或模拟Worker。
- 主模型 glm-5.3-flash；配置存在Qwen子模型，但本轮没有调用。
- 目标是GitHub CodeLoop main 638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b的独立clone，位于 /private/tmp/codeloop-live-pr-20260918。
- 运行使用隔离home/Memory，禁用MCP和混合记忆，自动批准隔离目录中的编辑和有限测试命令。复现脚本的直接python执行被此实测审批拒绝，CodeLoop自行转用pytest；不能据此评价默认交互审批体验。
- 本轮提示没有要求Git提交/PR；用户已纠正验收重点为CodeLoop自主执行。因此本轮不检验PR交付，也不等价于登录并发故障验收。
- 调用预算80次、token上限150万、总时限20分钟；实际终止信息是生产Agent Loop的工具步数上限。DEBUG前馈配置为35步（minicode/feedforward_controller.py），达到上限后主Agent没有最终总结保留步。

## 实际执行链

1. 自主列目录、搜索和读取实现及现有测试，确认根目录minicode为有效源码。
2. todo_write创建5项清单：根因、复现、修复、回归测试、验证。
3. 创建复现脚本；越界写入和脚本命令被拒后，转为在现有测试文件中复现。
4. 自主新增2个回归测试：完整文件列表的单元测试，以及真实pytest子进程的第12文件失败场景。
5. 在未修复代码上运行新测试：2 failed, 33 deselected。
6. 移除pytest调用中的 test_files[:10] 截断。
7. 重跑新测试：2 passed；整个tests/test_tools.py：35 passed；ruff对两份修改文件检查通过。
8. 自主扩大验证：先因缺jsonschema收集失败，最终排除3个依赖缺失模块运行，得到4431 passed、4 failed、3 skipped；另重跑跨进程失败测试仍失败。不能描述成全量测试通过。
9. 自主清理复现脚本、检查Git差异、更新待办，然后达到步数上限。
10. 最终返回：`Reached the maximum tool step limit for this turn.`；task.outcome为failed，goalAchieved=false。

## 真实调用量

- 耗时：792.96秒（约13分13秒）。
- 模型调用：35次；工具调用：44次。
- 输入token累计：1,487,364；输出token：17,125。输入按每次请求累加，包含重复上下文；没有可靠价格记录，不报告费用。
- 子Agent调用：0；task工具调用：0；workflow审批：0。
- 工具分布：{"list_files": 1, "file_tree": 2, "grep_files": 8, "read_file": 13, "todo_write": 2, "write_file": 2, "run_command": 9, "edit_file": 2, "git": 3, "batch_delete": 1, "file_line_count": 1}。

## 宿主独立验收（不计作CodeLoop自身能力）

- 旧版基线：12文件只运行10个，末位失败被漏报。
- 修复后外部oracle全部通过：运行全部12文件、末位失败正确报告、全通过时12 passed、单文件范围保持。
- 把CodeLoop新增的两个测试放入独立旧生产代码副本：2 failed, 33 deselected；证明它们能拒绝旧缺陷。
- 结束后重新执行tests/test_tools.py：35 passed；完整ruff检查与git diff --check通过。
- 最终仅两份跟踪文件有修改，保存为agent-generated.patch。当前原工作区的生产代码未被本次测试修改。

## 本次发现的局限

- 小改动自主走主Agent直接修改路径，未覆盖真实多Agent调度。
- 扩大验证前没有先满足开发依赖；缺jsonschema、setuptools等使扩大验证不完整。没有对全部失败做原版对照，因此不采信“全部都是既有环境问题”的无条件断言。
- 记录中把预期回归断言失败附加为网络错误提示，把缺模块错误附加为资源不足提示，故错误分类提示存在误导风险。
- 35步全部用于工具工作，主Agent最终只返回上限提示；完成情况没有正常汇总。待办全部完成不能替代真实任务完成状态。

## 证据文件

- live-run/prompt.txt：实际输入。
- live-run/public-transcript.json：工具参数、结果和公开进度文本，排除系统提示与模型内部推理字段。
- live-run/summary.json：耗时、调用量、子Agent列表。
- oracle/target-baseline.json、oracle/after-fix.json：外部验收结果。
- regression-on-old-code.txt：新增测试在旧生产代码上失败。
- focused-tests.txt、lint.txt、diff-check.txt：结束后独立复验。
- experiment-manifest.json：基线和源码哈希。
