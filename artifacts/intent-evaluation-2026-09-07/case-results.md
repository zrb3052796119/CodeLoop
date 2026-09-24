# 50 条逐题结果（原始冻结口径）

通过指执行前标注的主类型、必要操作、否定、动作、实体、只读与顺序要求全部通过；不表示下游任务执行成功。

| ID | 分组 | 问题 | 预期主类型 | 实际主类型 | 实际操作 | 主类型 | 标注检查 | 差异 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | 代码编写 | 帮我写一个 Python 函数，把一组订单按用户 ID 分组。 | code | code | code/create | 通过 | 通过 | — |
| C02 | 代码编写 | 给登录接口加上每分钟最多五次的限流功能。 | code | unknown | 无 | 未通过 | 未通过 | primary: expected ['code'], got unknown; missing affirmative intent: code; action for code: expected one of ['create', 'update'] |
| C03 | 代码编写 | 把 src/cache.py 里的缓存过期时间改成 300 秒。 | code | code | code/update | 通过 | 通过 | — |
| C04 | 代码编写 | Please add a CLI command that exports the current settings as JSON. | code | code | code/create | 通过 | 通过 | — |
| C05 | 故障排查 | 服务一启动就报 ModuleNotFoundError，帮我定位原因。 | debug | debug | debug/analyze | 通过 | 通过 | — |
| C06 | 故障排查 | 用户点一次支付却扣了两次钱，查一下是哪里出了问题。 | debug | unknown | 无 | 未通过 | 未通过 | primary: expected ['debug'], got unknown; missing affirmative intent: debug |
| C07 | 故障排查 | Fix the race condition in worker.py that occasionally loses queued jobs. | debug / code | debug | debug/update | 通过 | 通过 | — |
| C08 | 重构 | 重构一下订单模块，把重复的价格计算抽成公共函数，行为保持一致。 | refactor | refactor | refactor/update | 通过 | 通过 | — |
| C09 | 重构 | 这个函数两百多行了，拆成几个职责单一的小函数。 | refactor / code | unknown | 无 | 未通过 | 未通过 | primary: expected ['refactor', 'code'], got unknown |
| C10 | 解释 | 解释一下 src/session.py 是怎么保存会话的。 | explain | explain | explain/read | 通过 | 通过 | — |
| C11 | 解释 | asyncio.gather 和 TaskGroup 有什么区别？举个小例子说明。 | explain / question | unknown | 无 | 未通过 | 未通过 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False |
| C12 | 解释 | Walk me through how a request reaches the database in this project. | explain | explain | explain/read | 通过 | 通过 | — |
| C13 | 查找 | 在项目里找出所有调用 refresh_token 的地方。 | search | search | search/read | 通过 | 通过 | — |
| C14 | 查找 | 帮我查一下 FastAPI 官方文档里关于流式响应的说明。 | search | unknown | 无 | 未通过 | 未通过 | primary: expected ['search'], got unknown; missing affirmative intent: search; read_only: expected True, got False |
| C15 | 查找 | 配置文件放在哪个目录？帮我在仓库里找找。 | search / question | configure | configure/update | 未通过 | 未通过 | primary: expected ['search', 'question'], got configure; missing affirmative intent: search; read_only: expected True, got False |
| C16 | 代码审查 | Review the changes in auth.py and point out security issues. | review | review | review/analyze | 通过 | 通过 | — |
| C17 | 代码审查 | 帮我审查这个 PR，重点看异常处理有没有遗漏。 | review | debug | debug/update | 未通过 | 未通过 | primary: expected ['review'], got debug; missing affirmative intent: review; read_only: expected True, got False |
| C18 | 测试 | 给 parse_date 函数补单元测试，覆盖空字符串和非法日期。 | test | test | test/create | 通过 | 通过 | — |
| C19 | 测试 | 运行 tests/test_cache.py，告诉我哪些用例没通过。 | test | test | test/execute | 通过 | 通过 | — |
| C20 | 测试 | Add regression tests for the timeout bug we fixed in client.py. | test | test | test/create | 通过 | 通过 | — |
| C21 | 文档 | 更新 README.md，补上 Docker 部署步骤。 | document | code | code/update; configure/update | 未通过 | 未通过 | primary: expected ['document'], got code; missing affirmative intent: document; action for document: expected one of ['update', 'create'] |
| C22 | 文档 | 给这个 API 写一份接入文档，说明请求参数和错误码。 | document | document | document/create; explain/read | 通过 | 通过 | — |
| C23 | 配置 | 把 pytest 的默认超时时间配置成 30 秒。 | configure | unknown | 无 | 未通过 | 未通过 | primary: expected ['configure'], got unknown; missing affirmative intent: configure; action for configure: expected one of ['create', 'update'] |
| C24 | 配置 | Set up GitHub Actions to run lint on every pull request. | configure / code | code | code/update | 通过 | 通过 | — |
| C25 | 普通问答 | Python 的字典为什么能保持插入顺序？ | question / explain | unknown | 无 | 未通过 | 未通过 | primary: expected ['question', 'explain'], got unknown; read_only: expected True, got False |
| C26 | 普通问答 | 这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。 | question / explain | unknown | 无 | 未通过 | 未通过 | primary: expected ['question', 'explain'], got unknown; read_only: expected True, got False |
| C27 | 闲聊 | 你好呀，今天状态怎么样？ | chat | unknown | 无 | 未通过 | 未通过 | primary: expected ['chat'], got unknown; missing affirmative intent: chat |
| C28 | 记忆 | 记住：以后给我举 Python 例子时，优先使用标准库。 | memory | unknown | 无 | 未通过 | 未通过 | primary: expected ['memory'], got unknown; missing affirmative intent: memory |
| C29 | 记忆 | 我之前让你记住的代码风格偏好是什么？ | memory / question | unknown | 无 | 未通过 | 未通过 | primary: expected ['memory', 'question'], got unknown; read_only: expected True, got False |
| C30 | 系统操作 | 清空当前对话，开始一个新会话。 | system | unknown | 无 | 未通过 | 未通过 | primary: expected ['system'], got unknown; missing affirmative intent: system |
| C31 | 否定/撤回 | 别改 payment.py 的代码，也不要运行测试；只解释它里面 process_payment 为什么先校验金额。 | explain | explain | 禁止 code/update; 禁止 test/execute; explain/read | 通过 | 通过 | — |
| C32 | 否定/撤回 | 刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。 | explain | debug | debug/update; explain/read | 未通过 | 未通过 | primary: expected ['explain'], got debug; unexpected affirmative intent: debug; missing negated intent: debug; forbidden affirmative action: debug/update; read_only: expected True, got False |
| C33 | 否定/撤回 | 暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。 | debug | explain | 禁止 refactor/update; 禁止 test/create; explain/read | 未通过 | 未通过 | primary: expected ['debug'], got explain; missing affirmative intent: debug |
| C34 | 否定/撤回 | 不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。 | explain | unknown | 禁止 search/read; 禁止 explain/read | 未通过 | 未通过 | primary: expected ['explain'], got unknown; missing affirmative intent: explain; missing negated intent: memory |
| C35 | 否定/撤回 | 撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。 | review | code | test/create; review/analyze; code/delete | 未通过 | 未通过 | primary: expected ['review'], got code; unexpected affirmative intent: test; missing negated intent: test; forbidden affirmative action: test/create; forbidden affirmative action: code/delete; read_only: expected True, got False |
| C36 | 引用/假设 | 工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。 | explain | explain | explain/read | 通过 | 通过 | — |
| C37 | 引用/假设 | 同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。 | review | review | review/analyze | 通过 | 通过 | — |
| C38 | 引用/假设 | 假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。 | explain | code | code/delete; explain/read | 未通过 | 未通过 | primary: expected ['explain'], got code; unexpected affirmative intent: code; forbidden affirmative action: code/delete; read_only: expected True, got False |
| C39 | 引用/假设 | 产品说明书里举了个例子：“搜索用户邮件并保存到本地”。帮我润色这句话，让操作范围更清楚。 | document | explain | explain/read | 未通过 | 未通过 | primary: expected ['document'], got explain; missing affirmative intent: document; action for document: expected one of ['update'] |
| C40 | 引用/假设 | 网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。 | explain | question | question/read | 未通过 | 未通过 | primary: expected ['explain'], got question; missing affirmative intent: explain |
| C41 | 多意图 | 先修复 parser.py 在空输入时抛异常的问题，再为这个边界情况添加单元测试。 | debug / test | debug | debug/update; test/create | 通过 | 通过 | — |
| C42 | 多意图 | 重构 cache.py 里重复的淘汰逻辑，保留现有行为；同时补充 README.md 中的缓存策略说明。 | refactor / document | refactor | refactor/update; document/update | 通过 | 通过 | — |
| C43 | 多意图 | 搜索项目里所有调用 fetch_user 的地方，然后解释其中 request_handler.py 的调用链。 | search / explain | search | search/read; explain/read | 通过 | 通过 | — |
| C44 | 多意图 | 审查 auth.py 的登录校验逻辑，并把发现的问题整理成 SECURITY_REVIEW.md。 | review / document | document | review/analyze; document/create | 通过 | 通过 | — |
| C45 | 多意图 | 在 settings.yaml 中把日志级别调为 DEBUG，然后运行 test_logging.py 验证日志输出。 | configure / test | test | test/execute | 通过 | 未通过 | missing affirmative intent: configure; action for configure: expected one of ['update']; missing requested operation order: ['configure', 'test'] |
| C46 | 上下文/模糊 | 继续审查后一个文件有没有重复扣款的风险。 | review | review | review/analyze | 通过 | 未通过 | missing entity files: payment.py |
| C47 | 上下文/模糊 | 就按这个方案改，函数签名保持不变。 | refactor | code | code/update | 未通过 | 未通过 | primary: expected ['refactor'], got code; missing affirmative intent: refactor; action for refactor: expected one of ['update', 'split'] |
| C48 | 上下文/模糊 | 再加一个 None 输入的用例。 | test | test | test/create | 通过 | 未通过 | missing entity files: utils.py |
| C49 | 上下文/模糊 | 这个能处理一下吗？ | unknown | unknown | 无 | 通过 | 通过 | — |
| C50 | 上下文/模糊 | 把那一行改对就行，其他内容不动。 | document | unknown | 无 | 未通过 | 未通过 | primary: expected ['document'], got unknown; missing affirmative intent: document; action for document: expected one of ['update']; missing entity files: examples.md |

## 上下文

上下文只包含先前消息，未将当前问题混入历史。

### C46

- user：请帮我审查 order.py 和 payment.py 的实现。
- assistant：我先从 order.py 开始，接下来会检查 payment.py。

### C47

- user：在 cache.py 里，refresh_cache 函数特别长，我想重构它。
- assistant：可以把拉取数据与更新缓存分开，同时保留外部接口。

### C48

- user：请给 utils.py 中的 normalize_name 加边界情况测试。
- assistant：我会覆盖空字符串、前后空格和只有空格的输入。

### C50

- user：文档 examples.md 中的启动命令写错了，应该是 python -m demo。
- assistant：已定位到“快速开始”的启动命令。

