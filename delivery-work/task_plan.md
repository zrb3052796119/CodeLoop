# CodeLoop 同学交付任务

## Goal
以当前工作区为准生成可转交的中文上手、操作、验收、维护说明和排除个人数据的源码包。

## Phases
- [x] 1. 建立独立计划，保留已有工作区修改。
- [x] 2. 核对入口、配置、依赖、打包边界与测试方式。
- [x] 3. 生成交付文档、示例配置和可分发文件。
- [x] 4. 在隔离目录验证安装及离线运行，检查文档，交付。

## Decisions
- 默认同学自行安装，使用自己的 API Key；覆盖 macOS/Linux 与 Windows。
- 交付当前工作区快照（含必要的未提交源码），不以旧 Git HEAD 冒充当前版本。
- 不覆盖根目录已有 task_plan.md / notes.md；工作记录放 delivery-work。
- 排除 .env、用户配置、会话/Memory、历史实验、Git 历史和本机虚拟环境。

## Errors Encountered
- 初始目录检索输出过大，改为限定目录和选择性读取。
- zsh 无匹配通配符导致一次目录列表失败；改用确切路径。
- 初次聚焦 pytest 引用了不存在的 test_headless.py / test_gateway.py；根据实际测试文件重选。
- 沙箱内聚焦测试 78 passed / 9 failed，9 项均在绑定 loopback HTTP 端口时被沙箱拒绝；已请求在沙箱外原样重跑此组测试。
- 沙箱外重跑为 86 passed / 1 failed。剩余测试拦截 Path.write_text，但生产保存已改为 fdopen + os.replace；已确认不是 fast edit 导致，记录现状不改生产代码。
- 自检初版因系统沙箱能力探针创建子进程而被自检阻断；将明确隔离该非目标能力并保持其余外呼/子进程阻断。
- 最终完整性检查捕获到并行脚本格式化使 self_check.py 晚于ZIP；待agent结束后已重打ZIP并重新验证。

## Status
完成：6份中文文档与配置/自检；源码ZIP、wheel、manifest、SHA256SUMS。已安装离线自检13项通过，267个wheel运行文件与ZIP逐字节一致；文档相对链接与白名单完整性通过。既有聚焦测试86通过/1旧写入观测断言未通过，已如实记录。
