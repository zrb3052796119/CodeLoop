# Phase 3: AgentRouter fail-closed

独立窗口，仅修改 `minicode/agent_router.py` 与新增 `tests/test_agent_router_fail_closed.py`。没有模型请求、stage/commit、冻结评测材料或执行宿主变更。

- `available_models=None` 继续采用内置目录；显式空字典不再继承内置模型。
- 普通路由仅选当前 tier 的主模型或声明的 fallback，保持声明顺序；候选耗尽抛 `RuntimeError`，不再任取无关模型。
- `force_model` 在实际路由时必须存在于可用目录；构造函数和 setter 指定的已登记自定义模型均保留，失败不追加路由决策历史。
- 这是本地目录成员资格检查，不是凭证或 provider 可达性验证。

兼容影响：原先依赖空目录隐式启用内置模型、缺候选任取模型、或强制未登记模型的调用现在会抛 `RuntimeError`。已登记显式模型、默认目录及声明 fallback 保持行为。

上游 SmartRouter 的异常发生在 `switch_to` 前，现有 Agent Loop / CyberneticOrchestrator 捕获异常并保留当前模型。本轮不改变该边界、不绑定默认 SmartRouter 当前未连接的 ModelSwitcher，也不更新 tier、模型目录、学习策略或任何控制器，以免把缺候选修复扩展成新的模型切换行为。

## Evidence

- 原文件：`agent_router.before.py`，与修复前提交 `e85a95803e248e157523e10dd0f1fe5bbed3f9bb` 的真实 blob 完全一致。
- baseline SHA256：`4a4d0d77034076f18452dc89a3703e7d7d7ed7895a93a5da5f6b707dbf8b6127`
- fixed SHA256：`92f4e2eb9fe27b5e41733dd6bb6fbc11bf1b4087c82396383a62606e0b19e506`
- 同字节测试 SHA256：`1eb1a6623f3b37c07af93bd8d8495cc5f563eb1e30e95a5e5f90f01a135f8d74`
- 初始当前源码目标回归：5 failed、6 passed，全部失败为 `DID NOT RAISE RuntimeError`；没有导入、语法或环境失败。见 `router-before-tests.txt`。
- test-counterexample bundled verifier：**PROVEN**；同解释器、相同命令和测试字节，固定侧 11 passed，真实修复前快照 5 failed / 6 passed。见 `router-counterexample.json` / `.txt`。
- 最终回归：`python3 -m pytest tests/test_agent_router_fail_closed.py tests/test_smart_router.py -q` → **18 passed in 0.33s**。见 `router-regression.txt`。计数与反例验证重叠，不累加。
- `git diff --check -- minicode/agent_router.py tests/test_agent_router_fail_closed.py` 通过。

临时完整源码快照只用于隔离反例执行；原目标文件、真实 Git 身份及完整日志永久保留于本目录。
