# Phase 2 诊断证据

## 旧自动触发数据的范围

旧合成八题是single/multi两组、每题3次：auto 0/24，32calls/200000tokens/240秒、单请求60秒、输出4096。旧真实八题是single/auto/explicit三组、每题2次：auto 0/16，40calls/500000tokens/600秒。原方案把“合成八题”与“原0/16”混在一起，需纠正。

来源：`docs/multi-agent-effectiveness.md`的2026-10-14真实三组与2026-10-13合成两组章节；`docs/archive/multi-agent-retired-2026-10-19/historical-reports/single-vs-multi-2026-10-13/final-audit.md`。

两份旧任务目录和auto轨迹均已删除，`docs/archive/multi-agent-retired-2026-10-19/cleanup-result.json`记录removed=true、retainedFiles=[]，README明确仅保留历史报告；Git可达历史无原tasks/manifest。`run_single_vs_multi_live.py`依赖外部tasks.json，不含原任务生成器。遗存failed-candidate-source.zip和real-memory-corroboration单条explicit不能代替旧auto输入或轨迹。

存活报告能证明task/workflow/leaf请求为0。合成审计保留“direct-edit budget ... escalates ... automatically”的静态提示，但同时指出非空审批callback使强制升级分支不会执行。这不是动态进度建议在某一轮已呈现的证据。现存材料无法回答建议实际何时出现、当时余量以及模型下一步是否拒绝采纳。因此“时机、措辞、激励”均不能被单独确认为历史原因。

## 当前代码可证的呈现问题

- `coding_progress.py`已包含无条件“A coordinator should delegate ... workflow”句子，常规提示每四次成功读取批出现，最多三次。旧接线未传当前task能力，因此无task的执行者也可收到这句建议。
- `agent_loop.py`使用`bool(successful_reads)`计批次；“8”不能写成八次工具调用。批次量仅辅助判断是否值得改变调查方式，不能取代任务语义判断。
- `prompt.py`仍只说明3800字符内联限制，漏掉Phase1已有`@file:相对路径`正文通道及800字符摘要，与task schema说明不一致。
- 现有预算和deadline进度提醒有独立升级语义。每turn一次必须仅约束委派建议，不能整体削减提醒次数。
- gen-2六格没有auto组，single移除task、explicit父只保留task/todo，不能用于测自动采纳。

## 最小修复选择

统一主提示与task工具描述为独立问题、明确交接物、预算允许时的正向形状示例；对已知位置单点修复保持直接工具。修正文件正文说明。现有进度反馈仅在当前具备task能力、未发生编辑、持续调查且不处于现有预算/时间压力时，提出一次条件性的委派选项；模型仍根据任务形状选择，宿主不自动启动任务，不新增准入层。

## 评测限制

Phase2通过标准仍为auto触发率≥25%且单文件负例零误触发。旧输入不可恢复，下一次必须单独冻结新八题、预先标注负例、明确分母及真实执行/送达口径；保留准确提示呈现与后续行为轨迹，声明与历史不可比。当前只做实现与本地机制验证，不能据此宣称真实触发率提升。

## 本地复现与审阅发现

- 修前使用真实run_agent_turn和read_file、不给task工具，连续12批读取的模型输入仍包含3次“应委派workflow”句子。这证明当前呈现与工具能力脱节；模型决策脚本化，不算真实auto效果样本。
- 首版能力过滤的独立审阅发现旧bounded阶段模式的定价边界：40000 token、40 calls、当前输入估计1000、已用8 calls、剩余900秒时，提示前的phase preview可以是implement，加入提示约319个估算token后则变为finalize，此时task只准poll。
- 一度准备让候选提示全文参与纯预判。随后核实`bounded_implementation_phases`是显式旧阶段配额模式（默认False），与有限共享预算独立；gen-2执行也未启用它。最终采用更窄处理：新建议仅用于默认continuous模式或无阶段控制器的父Agent；显式旧bounded模式保留普通进度、预算和截止提醒以及新版系统指引，不追加该建议。原phase定价/投影逻辑保持原样。没有新增模型调用或gen-2结果变更。
- 扩展回归5项恢复断言失败，使用修前loop/progress源码重验也是相同5项；controller字节未改。fixture未传bounded=True却断言旧阶段配额，已显式配置该模式，原断言全部保留，文件58项通过。
- 最终16文件251项联合回归通过（14.07秒），新增18项包含其中不累加；同公开loop API的2项反例旧断言失败、修后通过。独立复核无阻断。

## 新八题授权与前瞻设计

用户明确选择“冻结新八题，继续 Phase 2”；沿用已授权智谱端点和GLM-5.3-Flash。任务构建、运行器与独立审计并行准备，不调用模型筛题。六题有合理调查/隔离实现空间，两题明确给出文件/函数的单点负例；全部auto各一次，分母固定8。有效触发要求真实物理执行且宿主正式送达；负例任何启动请求（被拒也计）即误触发。保持固定预算、失败原样保留、不补抽。

冻结前准备父物理请求正文证据，绑定call ID，不含认证headers，核查动态建议实际呈现与下一步选择。只做观测，不改生产送达协议；异步结果的证据必须来自实际父消费确认，不能用“已创建”或“已完成”充当送达。尚未发生Phase2真实模型调用。
