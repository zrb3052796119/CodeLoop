# 沉淀层测试信号复核

结果：**STRONG**，所选 **3/3** 合成故障均被预期断言捕获。

| 故障 | 实测失败 |
|---|---|
| 绕过 strict 总开关 | strict 下错误创建 USER 偏好；历史依赖状态错误变为 declared |
| 自动批准声明式依赖 | capture/inject 中 approval_status 从 pending 错变 approved |
| 绕过 pending 年龄阈值 | 恰好 7 天的 pending 被提前渲染 |

原工作区修改前后相关测试均 **122 passed**。三个探针使用技能规定的 run_fault_probe.py，每次在独立 worktree 内先跑绿色基线，再施加单一故障。已检查失败均为上述行为断言，不是导入、编译或测试基础设施失败。没有幸存故障，也没有因此修改测试或生产实现。

初次直接使用工作区运行器被 `.git/worktrees` 沙箱权限阻止，原始记录保留为 `initial-blocked-*`。随后在 `/private/tmp` 建立仅含源码、测试和运行配置的快照（1,346 个文件，约 25.6 MB），逐文件校验哈希，仅在快照内初始化 Git。所有探针仍由原技能脚本执行。共享生产文件和相关测试的哈希未变化；临时 worktree 和快照均已清理。

独立审查：strict 用真实管线与技术 trace 检查 USER 零产出；声明依赖使用真实 read_file、trace recorder、ReflectionEngine 和 MemoryPipeline；年龄阈值固定时钟检查 7 天边界及 6/8 天配置，完整模块另检查实际注入与审批状态不变。没有发现阻塞级测试设计问题。

边界：这是当前代码的三个合成反例，仅证明所选守卫的测试信号。Git HEAD 不含现有未提交的实验模块，故未声称真实历史回归达到 PROVEN。规范结果见 `test-signal.json`，完整运行输出见各探针 JSON。
