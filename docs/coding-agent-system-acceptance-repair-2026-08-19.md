# MiniCode 系统验收修复报告（2026-08-19）

## 结论

**通过。** MiniCode 现在达到“高质量、可实际使用的 coding agent”门槛：真实模型的 Skill 路由、跨轮项目记忆、并行多-agent 协作均通过，最终完整套件为 `3837 passed, 2 skipped`，构建、Ruff、compileall、依赖一致性和 scoped 安全检查通过。

建议按正常工程护栏使用：限制模型调用、保留权限审批、任务后检查 diff 和测试。它已不再是上一份报告中的“仅监督式实验品”，但也不应被理解为可以绕过代码审查和 CI 的无限制自治系统。

## 阻断项修复结果

| 原阻断项 | 修复结果 | 证据 |
|---|---|---|
| 交互入口 `cwd` NameError | workspace 现在作为参数传入路由器，并有入口回归测试 | 完整套件通过；真实默认 Registry 探针通过 |
| 语义 Skill 假阳性反转成功结果 | 仅显式请求的 Skill 可成为最终强制项；语义推荐保持 advisory | Run `run_3306c04c018e46f8a5215c30f563decf` canonical success |
| 项目政策未持久化 | 成功读取政策文件会提取稳定约束，带 provenance 自动批准并写入 Project Memory | Runs `run_c5d0578344824820b1f6d3d88e9d2f39`、`run_d199dac698e742a99f1b249901a25965` |
| dev 环境不可复现 | dev extra 补齐 `jsonschema`、`setuptools`、`wheel`；fresh editable install 成功 | 完整套件在新环境通过 |
| 生产 Ruff/F821 | undefined-name、类型导入和歧义变量已清理 | `ruff check minicode` 通过 |
| sdist 污染 | 新增明确 manifest，排除旧的重复源码、测试和实验树 | sdist 约 864 KB，wheel 约 928 KB |
| sub-agent 轮次显示超限 | 以 `model.started` 统计真实 provider call，不再按批量 tool-call message 计数 | Run `run_080793698ad747b0b7626fe10b72c610` 为 4/12、6/12 |

## 真实环境问题重放

### Skill 路由

真实模型加载项目 Skill `acceptance-review`，读取目标文件并返回 `ORBIT-7421` 和 Skill 正文独有标记 `SKILL_REPAIR_FINAL_9C2E`。路由仍推荐了一个无关的全局 Skill，但它没有被错误升级为 required；任务、Skill attribution 和 Run 均为 success。

### 持久化记忆

第一轮读取 `POLICY.md` 后写入 approved/active 的 Project Memory `project-1787116046749089000-8f8b634f`，provenance 包含 Run、事件 ID、semantic key 和 `stable_project_constraint` 依据。第二轮明确禁止读文件，检索与渲染同一 Memory ID，并正确回答 `YYYY-MM-DD`。

### 多-agent

两个 explore agent 并行分析同一一次性 fixture：一个追踪 `api.post_invoice -> service.create_invoice -> storage.save_invoice`，另一个定位测试隔离风险。两者均识别 `storage._INVOICES` 为未重置的模块级可变状态；父任务 success，未修改源文件。

## 最终门禁

```text
Build:     PASS — isolated sdist + wheel
Types:     PARTIAL — compileall PASS；仓库未配置 mypy/pyright
Lint:      PASS — Ruff production target
Tests:     PASS — 3837 passed, 2 skipped
Security:  PASS（本次范围）— 安全回归、TLS/凭据静态扫描；运行时第三方依赖为 0
Diff:      PASS — 本次文件 scoped diff/whitespace
Overall:   READY
```

检索认证专项另有 `229 passed`，accepted semantic gold 未被改写；multi-agent 专项 `42 passed`；`pip check` 无破损依赖。

## 保留限制

- 仓库尚未配置静态类型检查器，因此不能把 compileall/Ruff 等同于完整类型证明。
- 当前远程模型在成本遥测中仍是 `model_unpriced`；调用数/token 上限有效，USD 上限需要补模型价格目录。
- 当前机器的配置端点需要 `SSL_CERT_FILE=/etc/ssl/cert.pem` 才能通过系统 CA 验证。
- 语义路由的精度仍可继续优化；现在它只产生建议噪声，不再破坏任务结果。
