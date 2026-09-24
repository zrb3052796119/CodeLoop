# CapabilityRegistry：来源与调用效果建模

2026-09-05。能力声明用于描述工具、辅助 Skill 匹配和检查调用；执行权限仍由原有 ToolContext、权限管理器及工具实现决定。本轮没有新增 Intent 正则。

## 建模内容

`minicode/capability_contracts.py` 定义不可变的 `CapabilityProfile`。保留原有 domain/scope，增加以下信息：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 当前为 `1.0` |
| `source` | builtin、explicit、local_config、mcp_annotations、legacy_name 或 unknown |
| `status` | declared、hinted、inferred、unknown 或 invalid |
| `effects.read_only` | 相对于该操作目标资源的只读性 |
| `effects.destructive` | 是否有破坏性效果 |
| `effects.external` | 是否涉及外部服务、模型或用户交互 |
| `effects.executes_code` | 是否涉及命令或受委派程序执行；普通 Python 数据变换不计入 |
| `effects.delegates` | 是否启动委派工作 |
| `purposes` / `supported_operations` | 显式用途及 `intent.action` 语义 |
| `evidence` | 来源与适用边界说明；不保存原始调用参数或异常内容 |

每个效果轴使用 `true / false / null`；`null` 表示合同没有确定该性质。`declared` 表示本地或工具作者声明，不代表运行时证明。外部读取可以同时是 `read_only=true` 和 `external=true`。确定矛盾的 scope/effects 会被合同拒绝，未知值仍合法。

原枚举由新模块定义，仍可从 `capability_registry` 导入。旧工具的 `capability_domain/capability_scope` 继续生效。仅靠名字得到的旧分类保留兼容字段，但标为 `legacy_name/inferred`，效果全部未知。不会把 opaque 名字的旧 `destructive` scope 当作已证实的破坏能力。

Core 29 项、full 56 项内置工具均使用显式 builtin 声明，覆盖 file、code、search、web、system、communication、analysis、execution。当前没有独立 memory 工具，保留该枚举而不虚构工具填充它。完整声明见 [能力清单](../.repair-work/capability-modeling/final-artifacts/builtin-capability-inventory.json)。

## 按真实参数细分

调用分类复用经过审查的纯 validator，不运行工具、解析 DNS 或提前申请权限。任意动态工具的 validator 不会自动在描述阶段执行。

| 调用 | 描述 |
| --- | --- |
| `git status/diff/log/review` | code/readonly；执行 Git 子进程，配置和 hook 的外部效果未知 |
| `git commit` | code/write；破坏性及 hook 外部效果未知 |
| `task run/spawn` 的 explore/plan | execution/external；worker 工作区只读，仍可能调用模型并更新内部协作状态 |
| `task run/spawn` 的 general/workflow | execution/external；工作区效果未知，不能用 prompt 中的“只读”取得保证 |
| `task poll` | system/readonly；观察任务，可更新内部确认状态，不启动新的委派循环 |
| `task cancel` | system/write；改变任务生命周期，不代表编辑工作区文件 |
| HTTP GET/HEAD/OPTIONS | 外部读取提示；`status=hinted`，服务器实际行为没有保证 |
| HTTP POST/PUT/PATCH、DELETE | 写入/破坏性提示；原有网络权限检查仍生效 |
| 任意 `run_command` | 验证参数形状；执行程序，文件、破坏性及网络效果保留未知 |
| 六种 archive 工具 | 按真实 validator 检查；解压仍会写文件，不因被忽略的 `mode=list` 变成只读 |

不接受的调用会返回 `status=invalid` 且效果全部未知。没有调用分类器的工具只返回静态合同，这不代表其参数已经验证。`uuid_generate` 的 Python v1 fallback 可能调用系统命令，因此其静态 `executes_code` 为未知。

## MCP 元数据

MCP 服务器的 annotations 只作为提示。`readOnlyHint` 等字段必须是 JSON boolean；缺失保持未知，错误类型标 invalid；不根据远程工具的 read/delete 等名字推定行为。所有真正的 MCP 调用保留外部服务边界。缓存的资源/提示词目录读取则是本地 system/readonly 操作。

本地可以在已有 `mcpServers` 配置中，为指定服务器的**原始 descriptor 名**声明元数据：

```json
{
  "mcpServers": {
    "records": {
      "command": "node",
      "args": ["server.js"],
      "toolCapabilities": {
        "lookup_record": {
          "domain": "search",
          "scope": "readonly",
          "effects": {
            "read_only": true,
            "destructive": false,
            "external": true
          },
          "purposes": ["record lookup"],
          "supported_operations": ["search.read"]
        }
      }
    }
  }
}
```

有效本地声明优先于服务端 hints，记录 `local_config/declared`。字段错误或矛盾会使该工具的声明变为 invalid，不回退到名字推断，不关闭整个服务器，也不改变工具原有执行权限。hint 冲突只记录固定标记。协议依据是 [MCP ToolAnnotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)；本轮没有升级 transport/protocol version。

## 生产接口与消费者

```python
tools = create_default_tool_registry(cwd, include_mcp=False)
static = tools.describe_capability("git")
call = tools.describe_capability("git", {"action": "status"})
snapshot = build_tool_capability_snapshot(tools)
same_call = snapshot.describe_call("git", {"action": "status"})
```

省略 arguments 读取静态合同；显式传 `None` 是非法调用形状。快照保留当时的 profile 和分类器，后续注册变化不污染既有快照。

- Skill Router 只使用 declared 工具元数据参与能力分数。只读任务的负分要求声明的 `read_only=false` 或 `destructive=true`；未知效果、外部只读不产生该负分，也不新增 veto。
- Skill 提案的 `tool_validation` 包含完整 `capability_profile`，可以查看来源和不确定性。
- `ToolRegistry.execute` 返回的 `ToolResult.capability` 携带调用描述。这是预期效果，不是执行后已发生效果的证明。

描述回调收到原始参数的深拷贝；描述与序列化在执行前的独立异常边界内完成。失败不覆盖工具结果，也不修改真实参数。描述结束后重新检查执行所有权。`ToolResult.capability` 是 keyword-only 新字段，旧位置参数含义保留。

## 验收口径

新增独立结构化调用 oracle 共 40 条，先于实现冻结，来源是真实工具契约分析，**不是线上流量或自然语言盲测**。调用合法性 40/40 一致，11 条非法调用的效果均未知。29 条有效调用的 domain 为 27 一致、2 差异：旧 oracle 将 poll/cancel 记为 execution，本轮设计归为 system，原标签没有修改。

效果轴分别报告：read_only 为 22 精确、5 保守未知、2 不可比；external 为 28 精确、1 保守未知。没有将未知或不可比计为精确通过，也没有合并成总准确率。详见 [逐条调用评估](../.repair-work/capability-modeling/invocation-evaluation.md)。

独立实现审查发现并验证修复了三处执行隔离问题及位置参数兼容问题；同一组 5 个测试在真实修复前源码上失败、修复后通过。56 项内置工具另做了逐项效果审查。最终测试、静态检查、冻结哈希与已知限制见 [本轮验收记录](../.repair-work/capability-modeling/acceptance-report.md)。

专项测试 142/142，通过退出。集成断言 206/206，但真实用户目录并发变化触发全局状态守卫，使会话退出码为 1。完整 Skill 回归 749 通过、5 个旧失败，失败 node ID 与原基线一致；joined 96/96、wild 严格 76/110 和宽松 89/110 均保持原结果。默认 Ruff 的存量发现也保留记录，不将本轮描述为整个项目全绿。

此前 Skill/Intent 的五条 catalog 失败、v4 误拦截和长引用问题仍是单独记录的遗留问题。本轮不以能力建模的结果宣称这些问题已经解决。
