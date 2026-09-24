# 第四轮独立审查结论

在共享生产源码最终整合后，审查发现的明确新增命令回退均已修复，新增的两条口语只读 fallback 也已通过错误写入 proposal 的对照验证。本结论仅覆盖下述审查范围，不表示所有疑问、讨论、口语请求均已正确解析。

审查者没有读取本轮 U 50 的题目或作者文件；所有开发探针均在揭示盲测前独立编写。样本描述的命令从未执行，语义对照使用固定 proposal，不冒充真实模型结果。

## 已发现并修复的问题

| 问题 | 复现示例 | 最终结果 |
|---|---|---|
| 疑问范围覆盖同句后续显式命令 | `这个脚本会删除文件吗，另外请删除 tmp/cache.json？`、`How should we refactor this class, and please update app.py?` | READ 与后续真实 DELETE/UPDATE 分开保留 |
| 全局疑问归一化覆盖已拆分的顺便/括号命令 | `这个函数会修改状态吗，顺便帮我把 app.py 改成异步？`、`这个脚本会删除文件吗（顺便删除 tmp/cache.json）？` | 后续真实 UPDATE/DELETE 保留 |
| 宾语前置的礼貌请求误当第三方行为疑问 | `这个函数能否帮我重构一下？` 等 4 条 | 保留 affirmed UPDATE |
| 英语被动礼貌请求被语义 gate 拦截 | `Could this function please be refactored?` 等 4 条 | 与真实基线一样接纳固定 UPDATE proposal |
| 恢复只读 fallback 后接纳错误写动作 | `先聊聊这些开关对输出的影响，不用实际执行测试。`、`这俩选项是咋配合起来的啊，不必急着运行测试。` | UPDATE/CREATE/EXECUTE proposal 被拒，READ 被接纳 |
| 新口语 gate 将名词修饰词当作只读动词 | `说明文字换成英文。`、`解释文字帮我润色一下。`、`分析报告换成中文。` 等 5 条 | 收窄新增 gate 到叠词谈话动词，5 条固定 UPDATE 均恢复接纳 |

证据保存在 `review-independent-final.json`、`review-fronted-final.json`、`review-semantic-final.json`、`review-recall-authority-final.json`、`review-nominal-read-prefix-final.json`。相同脚本分别在真实本轮前快照及共享生产上执行；`review-final-integrity.json` 记录 258 个生产 Python 文件、5 个探针脚本及最终结果的 SHA256，并验证复测期间源码不变。

## 仍然存在的边界

独立 60 条开发探针的粗粒度动作授权检查由 37/60 变为 44/60。它只检查只读是否无实际写动作、命令是否存在指定动作，不检查所有主意图和槽位，不能当作意图识别准确率或替代冻结盲测。

剩余 16 条失败中，12 条只读误授予动作是基线已存在的模式，包括 `你觉得这段代码应该怎么重构？`、`这个脚本能删除缓存文件吗？`、`执行这个命令会删除文件吗？`、`Will the cleanup script delete cached files?`、`How can you delete a cache directory safely?`、`Should we refactor this function? Just discuss the approach.`。它们没有命中新增的完整只读语法范围。

另 2 条真实命令在基线即漏识别。粗粒度统计还显示 2 条“基线通过→最终失败”：`这个函数会改动输入吗？请改成不修改输入。`、`你觉得应该怎样重构？选一个方案实现。`。检查实际操作范围可见，基线的 UPDATE 来自前文疑问中的动词，未独立正确识别后文请求；最终去掉了该错误写权限，但后文实现请求仍未被补足。不能把基线偶然出现 UPDATE 当作正确解析，也不能把最终结果当作已经完成用户命令。

固定 proposal 对照中的 `这段说明得怎么看呀？我暂时不打算运行测试。` 仍可接纳错误 UPDATE/CREATE。基线原本错误授予 EXECUTE；本轮修复了否定范围并恢复了 fallback，但完整只读 gate 因名词“说明”含动作标记而未命中，因此这条尚未完全修复。已向主代理明确反馈，本轮保留为已知边界，不能宣称所有口语只读入口已安全。

## 评测方法审查

`evaluate.py` 相对上轮只增加数据集路由与本轮命名，未修改 score 语义；评分器使用固定路径，源码、数据、评分器与运行器哈希检查保留。`summarize.py` 已采纳建议，将 live 配对验证从仅 classifier 配置扩大到整个已记录 runtime（含模型与 provider）。

既有开发集复用上轮历史 live 作为前值，可作运行结果参考，不能把单次线上差值全归因于源码。预先声明的固定 proposal 跨真实版本复播能比较入口及接纳行为，也不能冒充新的线上准确率。审查未核验尚未产生的本轮最终盲测结果。
