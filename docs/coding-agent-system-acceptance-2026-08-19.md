# MiniCode Coding Agent 系统验收报告（2026-08-19）

## 结论

**不通过生产放行。** 当前版本可以作为有人监督的实验性 coding agent 使用，但还不是高质量、可无人值守运行的 coding agent。

它的模型执行能力已经可用：真实环境里能读代码、加载指定 Skill、完成修复、请求权限、运行测试，也能并行调度两个 explore agent 并给出准确结论。阻止放行的不是“模型不会写代码”，而是控制平面仍会把成功任务误判失败、交互入口存在可复现异常、持久化记忆没有兑现，以及开发/测试环境不可复现。

建议成熟度：**监督式试用 6/10；自主生产 4/10**。

## 真实场景结果

| 场景 | 结果 | 客观证据 |
|---|---|---|
| S1：修复库存模块并加载指定 Skill | **系统失败 / 编码成功** | 基线 4 failed；只改 `inventory.py`；独立复测 4 passed；最终回答包含 Skill 正文独有标记 `SKILL_ACCEPTANCE_7F3A`。但路由额外选中无关的 `code-skills/minicode-study`，因未加载它而把已验证成功的任务改判 `failed`。Run `run_3617543427a9443f9aa508125d5061cb`。 |
| S2：两个并行 agent 分析架构 | **通过** | 正确给出 `post_invoice -> create_invoice -> save_invoice`，识别 `_INVOICES` 全局可变状态导致测试污染；两个重叠 sub-agent 记录均完成，父任务为 success，未修改文件。Run `run_16081c80539c404d9ec1402872f6338e`。 |
| S3：跨轮记住项目日期格式 | **失败** | 第一轮读取 `POLICY.md` 后答对 `YYYY-MM-DD`，但没有写入 Memory；第二轮禁止读文件时检索 0 条并诚实回答 `UNKNOWN`。Runs `run_4dc56f7007cd4c37bcf61548f6927f3a`、`run_bb4697b94ae2422bbd3508d6add49d35`。 |
| S4：上下文压缩与 Turn 身份 | **通过回归门禁** | 187 项通过，覆盖 loaded-Skill pinning、full compact、exact dropped-middle、非负 token savings 与 Turn identity。 |

## 阻断问题

### P0：当前交互入口会在 Skill 路由时报错

`minicode/main.py:125` 的 `_route_skills_for_prompt` 使用了函数作用域中不存在的 `cwd`。用真实默认 ToolRegistry 的最小探针稳定复现：

```text
NameError: name 'cwd' is not defined
```

Ruff 同样报告该 F821。此问题直接影响普通交互式提示词路径，必须在发布前修复并增加入口级回归测试。

### P1：Skill 误路由会否决已完成的正确任务

S1 中，用户明确要求加载项目 Skill，代理确实加载并遵守了它，代码和测试也全部正确；路由器却同时把一个全局学习 Skill 判成 required。最终的强制检查覆盖了正确答案，把 canonical outcome 改成 failed。

这说明 required Skill 当前不是可靠的安全闸，而是可能产生假失败的控制点。应当收紧召回阈值、区分“显式指定”和“语义推荐”，只有显式指定或高置信硬约束才能阻止最终提交。

### P1：持久化记忆没有学习稳定项目约束

明确写在项目政策文件中的稳定格式约束，在完成一次成功读取后没有形成可检索、带 provenance 的 Memory。后续运行只能回答 UNKNOWN。诚实 abstain 值得肯定，但不等于持久化记忆可用。

### P1：声明的开发环境无法完整运行测试

`pyproject.toml` 只声明 pytest 作为 dev dependency，但评估器直接依赖 `jsonschema`。未过滤的完整测试因此有 3 个 collection error。真实权限环境排除这 3 个模块后为：

```text
3786 passed, 4 failed, 3 skipped
```

四个失败中，两个仍由缺少 `jsonschema` 引起；一个跨进程测试假设源码包已可导入；一个 wheel 测试假设当前解释器已安装 `setuptools`。这更多是环境/打包契约问题而非核心逻辑回归，但仍然阻止“可重复验收”。

## 重要非阻断问题

- Ruff 全目标 238 个问题，其中 207 个可自动修复；生产包仍有 11 个问题，包括 5 个 undefined-name。
- 真实请求最初因 Python CA 链报错；设置 `SSL_CERT_FILE=/etc/ssl/cert.pem` 后成功。配置校验只验证字段，没有验证实际端点健康或 TLS 路径。
- 所有真实模型调用都显示 `model_unpriced`，所以 token/call 上限有效，但金额预算上限不可依赖。
- S1 即使 canonical outcome 失败仍写 ProjectFacts，并把本地模块 `inventory` 误记为外部依赖。
- Run 的 lifecycle `completed` 与 canonical task outcome `failed` 可同时出现；外部消费者若只看 lifecycle 会误判成功。
- wheel 可正常构建和安装，但 sdist 清单过宽，包含大量 `py-src`、`ts-src` 和 tests 内容。
- 多 agent 日志中出现 `model_turns=21` 与 `max_turns=12` 并存，至少说明指标定义或展示不一致；本次没有观察到共享 model-call ceiling 被突破。

## 通过项

- 真实 bugfix 的代码正确且改动最小，测试未被篡改。
- 指定 Skill 确实被加载，正文独有标记进入最终回答，Skill digest 与 attribution 可追踪。
- 编辑和命令执行经过交互权限批准。
- 两个 read-only sub-agent 可以并行运行、汇总并形成 typed parent outcome。
- 上下文压缩关键回归 187/187 通过，已加载 Skill 在 micro/full compact 中保持完整。
- `compileall` 通过。
- `uv build` 成功；wheel 在全新临时虚拟环境安装成功，CLI `--help` 正常。
- 未发现生产代码关闭 TLS 校验或嵌入真实 API key 的静态迹象。

## 发布建议

在以下条件完成前，不建议用于无人值守改代码、自动提交或 CI 自动修复：

1. 修复 `cwd` 入口异常并用真实 CLI 入口测试覆盖。
2. 修复 required Skill 的假阳性与成功任务反转，重跑 S1。
3. 为项目规则建立可审计的 Memory 写入/召回路径，重跑 S3。
4. 补齐 dev/test 依赖并让全新环境的完整测试全绿。
5. 清零生产代码的 F821，并建立 ruff + 类型检查门禁。

完成前，它适合开发者在场、每次检查 diff 和测试结果的监督式使用；不适合把“任务已完成”的判断完全交给系统。

## 验收边界

- 真实模型与真实 CLI/Headless 路径均被调用；所有写代码场景都在 `/private/tmp/minicode-acceptance-20260819` 的一次性 fixture 中执行。
- 当前仓库原本已有大量未提交改动，本次没有清理或覆盖它们。
- 未做依赖漏洞审计：环境没有配置 vulnerability scanner，因此报告不声称供应链安全已通过。
- 上下文压缩采用生产回归套件而非刻意制造高成本远程 token 溢出；这是本次验收的明确限制。
