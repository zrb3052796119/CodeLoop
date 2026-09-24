# 第三轮独立边界审查

状态：同一组 28 个探针已完成修复后复核，2 组新增 P2 回归全部关闭；未发现范围内尚未处理的新增回归。仍有 2 个预先注明的 baseline 旧缺口，不能将这组探针称为全部正确。

## 方法与边界

只读生产代码，未读第三轮封存 holdout、未调用真实模型。固定 28 个自拟问题：10 个引用归属 / 真请求边界、10 个名词动作 / 后续真动作、8 个否定状态 / 作用域。每个完全相同的问题分别在实际 baseline 和当前候选上运行纯规则与固定 CODE/UPDATE 伪适配器。伪适配器仅验证模型候选准入以及送给模型的请求是否被意外清空，不能证明真实模型成功率。

- baseline：`.repair-work/intent-phase3/baseline`
- 问题：`independent-review-probes.json`
- 问题 SHA-256：`ec36838d42e3d5e256d3e8398e932eb7a9cccf7676367f35f0c550aa277ed24c`
- 运行器：`independent_review_probe.py`
- 首次证据：`independent-review-baseline.json`、`independent-review-current.json`；包含全量生产源文件哈希、逐题操作与模型准入输入。
- 原 baseline 与 current 的三个题面字段（id / text / expected）完全相同；28 题一经运行即固定，不向独立新题集扩张。

复现（工作目录为仓库根目录）：

```sh
python3 .repair-work/intent-phase3/independent_review_probe.py .repair-work/intent-phase3/baseline .repair-work/intent-phase3/independent-review-probes.json /tmp/phase3-review-baseline.json
python3 .repair-work/intent-phase3/independent_review_probe.py . .repair-work/intent-phase3/independent-review-probes.json /tmp/phase3-review-current.json
```

## 新增发现

### P2：归属陈述 mask 会吞掉需要保留的真实请求（A01 / A02 / A03 / A08）

位置：`minicode/intent_request_roles.py` 的 `_ATTRIBUTION_PREFIX_RE` 中文前缀通配与 `attributed_literal_statement_spans` 的 `_REQUEST_PREFIX_RE` 排除判断。

以下请求在 baseline 都会调用固定语义适配器，并保留其 UPDATE proposal；首次 current 将整句 mask，根本没有调用适配器，最终 UNKNOWN / 无操作：

- A01：确保按钮文案是“执行数据库迁移”。
- A02：保证按钮标题为“删除账号”。
- A03：调整按钮文案为“执行数据库迁移”。
- A08：修复按钮，它的标签是“删除账号”。

原因：归属陈述的中文前缀允许任意 72 字及逗号；一个有限请求前缀列表无法保证剩余前缀均为陈述主语，因而将真正的“确保 / 保证 / 调整 / 修复”连同对象说明一起屏蔽。固定 proposal 的含义只是证明原先存在的语义补全通路被删去，不声称真实模型一定能完成它们。建议收窄完整陈述主语的肯定识别，拒绝跨越前置独立命令的逗号，避免靠持续扩充否定列表识别任意命令。

### P2：名词 governor 会吞掉带完成体与立即副词的第二动作（N01）

位置：`minicode/intent_parser.py` 的 `_NOMINAL_GOVERNOR_RESET_RE` 和 `_is_nominal_effectful_action`。

N01：审查完代码立刻生成函数。

baseline 纯规则识别 REVIEW/ANALYZE + CODE/CREATE；首次 current 只剩 REVIEW/ANALYZE，且伪适配器也无补救机会。`生成` 后面直接接 `函数`，较早的 `审查` 被认作名词 governor，然而中间 `完代码立刻` 表示前一个动作完成之后的真实第二命令。应保留这类有明确完成体 / 时序副词的动作边界，同时用真正名词说明句作负对照，避免把任意出现“立刻”的说明都解开。

## 已有缺口与改善（不计新增回归）

- N05 `Review app.py before updating the function.`：两版都未识别 UPDATE，是原有缺口。
- N07 `查看用于生成报告的脚本。`：baseline 误认 CREATE；current 纯规则去掉该 CREATE 后仍 UNKNOWN，固定语义 UPDATE 仍能通过。它尚未正确成为只读动作，但 baseline 已经存在不该有的写动作，不能把它列作新增回归。
- A07 的背景标签被模型补出 UPDATE 在 current 被阻止；N04 / N08 / N10 的名词动作误触发减少，后续真动作被保留。
- S07 保留了先前被否定范围吞掉的 REVIEW；S08 的过时不用状态不再取消当前 DELETE。
- 8 个固定 scope 探针没有发现取消状态例外解除真正否定的新问题。

## 后续复核

作者修复后，已仅重跑原来这同一组 28 题；题面与标签完全相同，SHA-256 不变，未扩展问题，未读 holdout。最终输出保存在 `independent-review-final.json`。

- A01 / A02 / A03 / A08：固定 UPDATE 适配器都重新收到完整真实请求并准入 UPDATE。中文归属陈述改为有限 UI 来源名词链，整组发现关闭。
- N01：纯规则与伪适配器路径都恢复 REVIEW/ANALYZE + CODE/CREATE。完成体与立即副词后续命令边界修复，发现关闭。
- 除上述 5 个问题外，其余 23 个问题的操作、极性与适配器输入与首次 current 完全相同。A07 背景标签的误写阻止效果仍然保留；scope 8 例结果无变化。
- N05 / N07 仍是已明确记录的 baseline 旧缺口，未将其重新计为新增回归，也未降低期望值将其算对。
- 最终输出中记录的每个生产源文件 SHA-256 与复核结束时工作区一致；没有对生产代码、测试、holdout、评分器或模型设置进行修改。

因此本次有限范围的独立审查可以关闭，结论仅针对这 28 个固定边界探针及所读增量，不等价于真实模型成功率或泛化保证。
