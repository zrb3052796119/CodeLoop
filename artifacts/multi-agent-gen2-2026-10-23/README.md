# Gen-2 工程六格

正式冻结材料位于 `run-v2/`，预注册正文为仓库 `docs/2026-10-23--multi-agent-gen2-preregistration.md`（冻结副本 `run-v2/analysis-plan.md`）。模型调用必须传入包含全部冻结材料的完整 Git commit；没有提交不能运行。

```sh
python3 scripts/run_multi_agent_gen2.py run --output artifacts/multi-agent-gen2-2026-10-23/run-v2 --preregistered-commit <完整提交SHA>
python3 scripts/audit_multi_agent_gen2.py --input artifacts/multi-agent-gen2-2026-10-23/run-v2
python3 scripts/analyze_multi_agent_gen2.py --input artifacts/multi-agent-gen2-2026-10-23/run-v2
```

运行需要本地进程查询与既有模型网络访问能力；不降低进程清理、未知用量或生成测试标准。

三题分别是读取上下文坐标、借用 registry 生命周期、摘要 schema 预算，均取真实父9aaa与修复ad20的原始源码；三题同字节oracle均证明基线目标失败、参考通过。任务与全部历史数字不可比。旧完整任务包已删除，排重证据的可复核范围见 `overlap-review.md` 和 `pre-freeze-review.md`。

`candidate-pack-v1/v2/v3` 和首次 `run/` 是模型运行之前的离线准备迭代，保留在本地：v2修正非零退出假红判定，v3删除公开命令中生产白名单不允许的选项；没有模型重抽。真正六格只能从提交后的 `run-v2/manifest.json` 固定顺序各跑一次。

预注册已在零模型调用状态提交为 `f131d7f3f9cc47dc00d7e64a977c466ec4804237`。用户明确授权既有智谱端点后，固定六格全部完成：**5/6（single 3/3、explicit 2/3）**，继续门通过。独立终审6/6有效、0错误、1个由HTTP前预算拒绝解释的计数警告。没有重跑、补抽或更换模型。

结论与边界见 [完整报告](report.md)，机器判定见 [gen2-gate.json](run-v2/gen2-gate.json)，按角色开销见 [gen2-report.md](run-v2/gen2-report.md)。原始账本、请求和验收副本在本地被Git忽略的 `run-v2/private/` 保留；版本控制保存精选结果与审计摘要，不能仅靠摘要重建全部原始证据。
