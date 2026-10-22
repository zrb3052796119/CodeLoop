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

当前预注册提交记录的是零模型调用状态。本地联合测试54通过、正式冻结包独立partial audit有效（0/6、仅未运行警告）。后续结果与终态审计保存在run-v2，不能将这些离线验证当作模型效果验收。
