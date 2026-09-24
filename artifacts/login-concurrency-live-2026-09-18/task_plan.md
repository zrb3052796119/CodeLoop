# CodeLoop 真实模型端到端实测

## 目标
根据用户追加要求，选择真实 GitHub 项目 CodeLoop，修复 pytest wrapper 只执行前10文件导致漏报的真实问题。用当前生产 Runtime 的真实模型多Agent完成修改与验证，并创建真实 PR。

## 阶段
- [x] 检查模型配置和生产入口，准备隔离与证据采集
- [x] 建立并预先复现测试漏跑故障，冻结独立验收条件
- [x] 执行真实模型任务，保留角色、工具、验证及错误事件
- [x] 独立复验修复与回归测试，保存diff（本轮按用户纠正不测试Git交付）
- [x] 输出结论及局限

## 边界
不修改现有业务源码；不暴露凭据；不将模型声明当成通过证据。
用户已授权自行选择真实 GitHub 仓库并创建PR。目标为 zrb3052796119/CodeLoop，base main=638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b。GitHub CLI/浏览器尚未登录。

## 状态
正在准备。用户工作区存在 488 项已有变更，全部保留。

## 选择依据
远程 main 与本地 origin/main SHA 相同；独立 clone /private/tmp/codeloop-live-pr-20260918，分支 codex/fix-complete-test-discovery。主模型 glm-5.3-flash，子模型 qwen3.7-plus/max。只读保存配置，不记录凭据。

## 用户纠正后的验收口径
重点为测试CodeLoop收到自然语言任务后自主拆解/规划/工具调用/完成任务的能力。原先给定步骤与多Agent角色的prompt尚未执行，现已替换为纯问题描述，不预设角色/工具/修改文件/检查命令。PR与GitHub登录不作为这轮完成条件。审批只代表允许在隔离clone内工作，不代替模型决策。

## 已完成独立验收
外部oracle四项全部通过：直接pytest基准有效、末位失败被发现、12个通过文件全部运行、单文件范围不扩大。CodeLoop自己新增的两个测试在旧版本生产代码上均失败（2 failed, 33 deselected），未替它修改代码。当前仍等待CodeLoop扩大验证范围后的自然结束。

## 最终状态
测试完成。CodeLoop自主完成修复及回归验证，但生产DEBUG 35步上限使它未正常总结，规范task.outcome=failed。多Agent未触发。详情report.md。
