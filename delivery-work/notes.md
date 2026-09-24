# 交付核对记录

- 项目对外名 CodeLoop，Python 包名 minicode-py，导入包 minicode。
- pyproject.toml: 0.1.0，Python >=3.11，默认依赖 certifi；开发依赖与 memory-hybrid 分离。
- 当前工作区有大量既有修改和新增源文件，必须按工作区快照交付，不重置、提交或覆盖它们。
- 已有 .env 和运行记录，不能整体压缩仓库。
- 文档目标：接收人可以独立安装、配置、首次运行、验收和提供有效故障反馈。
- 默认 Skill selection 已是 model；不复述旧 README 的 routed 流程为默认。
- --validate-config 出错可能仍 exit 0，headless 的任务失败也可能打印 Error 后 exit 0；验收要检查内容。
- headless 没有 --prompt 参数，gateway 通过环境变量选 host/port。
- 源码精简包不含全量测试和 artifacts；附带独立已安装包自检。
- 当前没有根 LICENSE；保留来源声明，不编造许可证。
- 本次验证 macOS15.5 arm64 / Python3.12.8。fresh venv构建/安装成功，自检13checks PASS。
- 原工作区聚焦测试86通过/1失败；严格审批模式复核仍相同，支持失败与fast edit无关的代码定位。
- 剩余断言为 installed_allow_writes：测试拦Path.write_text，生产现用os.fdopen/os.replace；未为交付修改既有测试。
- 最终交付文件清单/文档链接/运行源码与wheel一致性见 final-validation.json。
