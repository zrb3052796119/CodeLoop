# Gen-2 调查记录

- 用户明确更换旧题集策略，旧包缺失不再是任务阻塞条件。
- 原门与固定模型/预算保持；新题集只与自身single/explicit组比较。
- 工作区含原用户memory等修改和本轮Phase0/1实现，提交必须限定本次gen-2预注册材料，运行宿主需要独立保存准确源码快照。
- 截至首次读取，2026-10-13之后可达提交仅五个，正在核对是否存在真实可用的修复前/后版本。
- 已确认三题可以来自同一ad20汇总修复提交。没有要求三个不同提交；真实父基线9aaa完整源码保留，参考只覆盖相同sourcePaths的ad20精确blob。
- 新题为gen2-read-context、gen2-registry-lifetime、gen2-summary-budget；与旧8题按具体触发/责任边界/验收断言排重。旧完整任务包缺失的证据范围限制明确记录，未把历史host中出现的同模块误算作题目重叠。
- candidate-pack-v1的7条公开兼容测试在baseline/reference均通过，三份隐藏oracle基线均exit1、参考均exit0。counterexample-proof/另存技能验证器的三份PROVEN结果；均无网络模型调用。
- 新runner和独立auditor单独实现，复用既有真实transport/共享预算/进程清理/共同裁判。旧runner1800秒及旧ID硬编码保持历史原状，本代实际600秒另冻结。
- 独立审阅发现离线proof不应把任意非零退出当目标失败：已统一oracle行为JSON收据，baseline必须exit1且目标errors/failures非空，reference必须exit0且无失败；新增6个反例测试拒绝ImportError/exit2/空错误/绿色baseline。candidate-pack-v2与counterexample-proof-v2保留修订后的最终候选证明，三个case均PROVEN。
- 首次实际freeze未通过：summary-budget公开命令带生产白名单禁止的`-o addopts=`。保留freeze-validation.txt及run/，未运行模型；删掉该命令选项，生产白名单不改。candidate-pack-v3重验全部通过，run-v2正式freeze成功。
- 正式run-v2的宿主归档约2.3MB，15个registration文件，实际600秒配置、全部公开pytest机器回执与源码来源校验通过；independent partial audit valid=true、expected=6、audited=0。
- 本地54项联合测试已在允许进程查询的环境通过（final-local-tests.txt）。受限沙箱会让共同裁判的ps扫描报PermissionError，真实运行须同样在允许进程查询的环境中执行；不降低清理门。
