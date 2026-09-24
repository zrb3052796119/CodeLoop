# 第三轮逐题对照

严格通过要求该题全部预先标注检查满足。这里只解析，没有执行样例动作。UNKNOWN 与实际肯定写入/执行分开统计。

## old50 / disabled

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| C01 | 帮我写一个 Python 函数，把一组订单按用户 ID 分组。 | True → True | code/create → code/create | — |
| C02 | 给登录接口加上每分钟最多五次的限流功能。 | False → False | 无 → 无 | primary: expected ['code'], got unknown; missing affirmative intent: code; action for code: expected one of ['create', 'update'] |
| C03 | 把 src/cache.py 里的缓存过期时间改成 300 秒。 | True → True | code/update → code/update | — |
| C04 | Please add a CLI command that exports the current settings as JSON. | True → True | code/create → code/create | — |
| C05 | 服务一启动就报 ModuleNotFoundError，帮我定位原因。 | True → True | 无 → 无 | — |
| C06 | 用户点一次支付却扣了两次钱，查一下是哪里出了问题。 | False → False | 无 → 无 | primary: expected ['debug'], got unknown; missing affirmative intent: debug |
| C07 | Fix the race condition in worker.py that occasionally loses queued jobs. | True → True | debug/update → debug/update | — |
| C08 | 重构一下订单模块，把重复的价格计算抽成公共函数，行为保持一致。 | True → True | refactor/update → refactor/update | — |
| C09 | 这个函数两百多行了，拆成几个职责单一的小函数。 | False → False | 无 → 无 | primary: expected ['refactor', 'code'], got unknown |
| C10 | 解释一下 src/session.py 是怎么保存会话的。 | True → True | 无 → 无 | — |
| C11 | asyncio.gather 和 TaskGroup 有什么区别？举个小例子说明。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False |
| C12 | Walk me through how a request reaches the database in this project. | True → True | 无 → 无 | — |
| C13 | 在项目里找出所有调用 refresh_token 的地方。 | True → True | 无 → 无 | — |
| C14 | 帮我查一下 FastAPI 官方文档里关于流式响应的说明。 | False → False | 无 → 无 | primary: expected ['search'], got unknown; missing affirmative intent: search; read_only: expected True, got False |
| C15 | 配置文件放在哪个目录？帮我在仓库里找找。 | True → True | 无 → 无 | — |
| C16 | Review the changes in auth.py and point out security issues. | True → True | 无 → 无 | — |
| C17 | 帮我审查这个 PR，重点看异常处理有没有遗漏。 | True → True | 无 → 无 | — |
| C18 | 给 parse_date 函数补单元测试，覆盖空字符串和非法日期。 | True → True | test/create → test/create | — |
| C19 | 运行 tests/test_cache.py，告诉我哪些用例没通过。 | True → True | test/execute → test/execute | — |
| C20 | Add regression tests for the timeout bug we fixed in client.py. | True → True | test/create → test/create | — |
| C21 | 更新 README.md，补上 Docker 部署步骤。 | True → True | document/update, document/update → document/update, document/update | — |
| C22 | 给这个 API 写一份接入文档，说明请求参数和错误码。 | True → True | document/create → document/create | — |
| C23 | 把 pytest 的默认超时时间配置成 30 秒。 | False → False | 无 → 无 | primary: expected ['configure'], got unknown; missing affirmative intent: configure; action for configure: expected one of ['create', 'update'] |
| C24 | Set up GitHub Actions to run lint on every pull request. | True → True | code/update → code/update | — |
| C25 | Python 的字典为什么能保持插入顺序？ | False → False | 无 → 无 | primary: expected ['question', 'explain'], got unknown; read_only: expected True, got False |
| C26 | 这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。 | False → False | 无 → 无 | primary: expected ['question', 'explain'], got unknown; read_only: expected True, got False |
| C27 | 你好呀，今天状态怎么样？ | False → False | 无 → 无 | primary: expected ['chat'], got unknown; missing affirmative intent: chat |
| C28 | 记住：以后给我举 Python 例子时，优先使用标准库。 | True → True | memory/create → memory/create | — |
| C29 | 我之前让你记住的代码风格偏好是什么？ | False → False | 无 → 无 | primary: expected ['memory', 'question'], got unknown; read_only: expected True, got False |
| C30 | 清空当前对话，开始一个新会话。 | False → False | 无 → 无 | primary: expected ['system'], got unknown; missing affirmative intent: system |
| C31 | 别改 payment.py 的代码，也不要运行测试；只解释它里面 process_payment 为什么先校验金额。 | True → True | 无 → 无 | — |
| C32 | 刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。 | True → True | 无 → 无 | — |
| C33 | 暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。 | True → True | 无 → 无 | — |
| C34 | 不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。 | True → True | 无 → 无 | — |
| C35 | 撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。 | True → True | 无 → 无 | — |
| C36 | 工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。 | True → True | 无 → 无 | — |
| C37 | 同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。 | True → True | 无 → 无 | — |
| C38 | 假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。 | True → True | 无 → 无 | — |
| C39 | 产品说明书里举了个例子：“搜索用户邮件并保存到本地”。帮我润色这句话，让操作范围更清楚。 | False → False | 无 → 无 | primary: expected ['document'], got explain; missing affirmative intent: document; action for document: expected one of ['update'] |
| C40 | 网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。 | False → False | 无 → 无 | primary: expected ['explain'], got question; missing affirmative intent: explain |
| C41 | 先修复 parser.py 在空输入时抛异常的问题，再为这个边界情况添加单元测试。 | True → True | debug/update, test/create → debug/update, test/create | — |
| C42 | 重构 cache.py 里重复的淘汰逻辑，保留现有行为；同时补充 README.md 中的缓存策略说明。 | True → True | refactor/update, document/update → refactor/update, document/update | — |
| C43 | 搜索项目里所有调用 fetch_user 的地方，然后解释其中 request_handler.py 的调用链。 | True → True | 无 → 无 | — |
| C44 | 审查 auth.py 的登录校验逻辑，并把发现的问题整理成 SECURITY_REVIEW.md。 | True → True | document/create → document/create | — |
| C45 | 在 settings.yaml 中把日志级别调为 DEBUG，然后运行 test_logging.py 验证日志输出。 | False → False | test/execute → test/execute | missing affirmative intent: configure; action for configure: expected one of ['update']; missing requested operation order: ['configure', 'test'] |
| C46 | 继续审查后一个文件有没有重复扣款的风险。 | True → True | 无 → 无 | — |
| C47 | 就按这个方案改，函数签名保持不变。 | False → False | code/update → code/update | primary: expected ['refactor'], got code; missing affirmative intent: refactor; action for refactor: expected one of ['update', 'split'] |
| C48 | 再加一个 None 输入的用例。 | True → True | test/create → test/create | — |
| C49 | 这个能处理一下吗？ | True → True | 无 → 无 | — |
| C50 | 把那一行改对就行，其他内容不动。 | False → False | 无 → 无 | primary: expected ['document'], got unknown; missing affirmative intent: document; action for document: expected one of ['update']; missing entity files: examples.md |

## old50 / live

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| C01 | 帮我写一个 Python 函数，把一组订单按用户 ID 分组。 | True → True | code/create → code/create | — |
| C02 | 给登录接口加上每分钟最多五次的限流功能。 | True → True | code/update → code/update | — |
| C03 | 把 src/cache.py 里的缓存过期时间改成 300 秒。 | True → True | code/update → code/update | — |
| C04 | Please add a CLI command that exports the current settings as JSON. | True → True | code/create → code/create | — |
| C05 | 服务一启动就报 ModuleNotFoundError，帮我定位原因。 | True → True | 无 → 无 | — |
| C06 | 用户点一次支付却扣了两次钱，查一下是哪里出了问题。 | True → True | 无 → 无 | — |
| C07 | Fix the race condition in worker.py that occasionally loses queued jobs. | True → True | debug/update → debug/update | — |
| C08 | 重构一下订单模块，把重复的价格计算抽成公共函数，行为保持一致。 | True → True | refactor/update → refactor/update | — |
| C09 | 这个函数两百多行了，拆成几个职责单一的小函数。 | False → True | 无 → refactor/update | — |
| C10 | 解释一下 src/session.py 是怎么保存会话的。 | True → True | 无 → 无 | — |
| C11 | asyncio.gather 和 TaskGroup 有什么区别？举个小例子说明。 | True → True | 无 → 无 | — |
| C12 | Walk me through how a request reaches the database in this project. | True → True | 无 → 无 | — |
| C13 | 在项目里找出所有调用 refresh_token 的地方。 | True → True | 无 → 无 | — |
| C14 | 帮我查一下 FastAPI 官方文档里关于流式响应的说明。 | True → True | 无 → 无 | — |
| C15 | 配置文件放在哪个目录？帮我在仓库里找找。 | True → True | 无 → 无 | — |
| C16 | Review the changes in auth.py and point out security issues. | True → True | 无 → 无 | — |
| C17 | 帮我审查这个 PR，重点看异常处理有没有遗漏。 | True → True | 无 → 无 | — |
| C18 | 给 parse_date 函数补单元测试，覆盖空字符串和非法日期。 | True → True | test/create → test/create | — |
| C19 | 运行 tests/test_cache.py，告诉我哪些用例没通过。 | True → True | test/execute → test/execute | — |
| C20 | Add regression tests for the timeout bug we fixed in client.py. | True → True | test/create → test/create | — |
| C21 | 更新 README.md，补上 Docker 部署步骤。 | True → True | document/update, document/update → document/update, document/update | — |
| C22 | 给这个 API 写一份接入文档，说明请求参数和错误码。 | True → True | document/create → document/create | — |
| C23 | 把 pytest 的默认超时时间配置成 30 秒。 | True → False | configure/update → 无 | primary: expected ['configure'], got unknown; missing affirmative intent: configure; action for configure: expected one of ['create', 'update'] |
| C24 | Set up GitHub Actions to run lint on every pull request. | True → True | code/update → code/update | — |
| C25 | Python 的字典为什么能保持插入顺序？ | True → True | 无 → 无 | — |
| C26 | 这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。 | False → False | 无 → 无 | primary: expected ['question', 'explain'], got unknown; read_only: expected True, got False |
| C27 | 你好呀，今天状态怎么样？ | True → True | 无 → 无 | — |
| C28 | 记住：以后给我举 Python 例子时，优先使用标准库。 | True → True | memory/create → memory/create | — |
| C29 | 我之前让你记住的代码风格偏好是什么？ | True → True | 无 → 无 | — |
| C30 | 清空当前对话，开始一个新会话。 | False → False | 无 → 无 | primary: expected ['system'], got unknown; missing affirmative intent: system |
| C31 | 别改 payment.py 的代码，也不要运行测试；只解释它里面 process_payment 为什么先校验金额。 | True → True | 无 → 无 | — |
| C32 | 刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。 | True → True | 无 → 无 | — |
| C33 | 暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。 | True → True | 无 → 无 | — |
| C34 | 不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。 | True → True | 无 → 无 | — |
| C35 | 撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。 | True → True | 无 → 无 | — |
| C36 | 工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。 | True → True | 无 → 无 | — |
| C37 | 同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。 | True → True | 无 → 无 | — |
| C38 | 假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。 | True → True | 无 → 无 | — |
| C39 | 产品说明书里举了个例子：“搜索用户邮件并保存到本地”。帮我润色这句话，让操作范围更清楚。 | False → False | 无 → 无 | primary: expected ['document'], got explain; missing affirmative intent: document; action for document: expected one of ['update'] |
| C40 | 网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。 | False → False | code/update → 无 | primary: expected ['explain'], got question; missing affirmative intent: explain |
| C41 | 先修复 parser.py 在空输入时抛异常的问题，再为这个边界情况添加单元测试。 | True → True | debug/update, test/create → debug/update, test/create | — |
| C42 | 重构 cache.py 里重复的淘汰逻辑，保留现有行为；同时补充 README.md 中的缓存策略说明。 | True → True | refactor/update, document/update → refactor/update, document/update | — |
| C43 | 搜索项目里所有调用 fetch_user 的地方，然后解释其中 request_handler.py 的调用链。 | True → True | 无 → 无 | — |
| C44 | 审查 auth.py 的登录校验逻辑，并把发现的问题整理成 SECURITY_REVIEW.md。 | True → True | document/create → document/create | — |
| C45 | 在 settings.yaml 中把日志级别调为 DEBUG，然后运行 test_logging.py 验证日志输出。 | True → True | configure/update, test/execute → configure/update, test/execute | — |
| C46 | 继续审查后一个文件有没有重复扣款的风险。 | True → True | 无 → 无 | — |
| C47 | 就按这个方案改，函数签名保持不变。 | False → False | code/update → code/update | primary: expected ['refactor'], got code; missing affirmative intent: refactor; action for refactor: expected one of ['update', 'split'] |
| C48 | 再加一个 None 输入的用例。 | True → True | test/create → test/create | — |
| C49 | 这个能处理一下吗？ | True → True | 无 → 无 | — |
| C50 | 把那一行改对就行，其他内容不动。 | False → False | 无 → 无 | primary: expected ['document'], got unknown; missing affirmative intent: document; action for document: expected one of ['update']; missing entity files: examples.md |

## prior40 / disabled

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| N01 | 值班手册里的“清空缓存目录再重启进程”是什么意思？请解释这条操作会影响什么；这次不要执行任何系统操作。 | False → False | 无 → 无 | missing negated intent: system |
| N02 | 假设把价格计算从订单服务拆成独立模块，会多出哪些接口耦合？现在不重构，只分析这样划分的利弊。 | True → True | 无 → 无 | — |
| N03 | 我撤回“给 import_jobs.py 加自动重试”的要求。先说明任务失败后直接退出可能有哪些后果，不要改这份文件。 | True → True | 无 → 无 | — |
| N04 | 今天先别写迁移脚本，也别改数据库连接配置；这两件事都暂停。 | False → False | 无 → 无 | missing negated intent: configure |
| N05 | 培训材料里出现了 `pytest -x`。这个参数跟默认跑法有什么不同？这次不要运行测试，只把差别讲明白。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; missing action alternative group: explain \| question |
| N06 | 如果将 worker.toml 的并发数从 4 调到 40，数据库连接池可能发生什么？我只想评估容量，暂时不要修改配置。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review'], got unknown; missing negated intent: configure; missing action alternative group: explain \| question \| review |
| N07 | 帮我在 architecture.md 里找提到“重写路由器”的段落，找到后告诉我位置；引号中的内容只是检索词，路由代码别改。 | False → False | 无 → 无 | primary: expected ['search'], got unknown; missing affirmative intent: search; action for search: expected one of ['read', 'analyze'] |
| N08 | 重启服务那件事先取消。现在只从 gateway.log 中找出包含 upstream timeout 的记录，列出出现时间即可。 | False → False | 无 → 无 | missing negated intent: system |
| N09 | 有人建议“把入门指南整篇删掉重新写”。这轮不用编辑文档，请只评审 onboarding.md 的章节顺序是否方便新人上手。 | False → False | 无 → 无 | missing negated intent: document |
| N10 | 日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。解释一下它想解决什么问题，先不要重命名或移动任何文件。 | False → False | 无 → 无 | missing negated intent: system; missing entity files: profile.json; missing entity files: profile.old.json |
| N11 | 请新增 coupon_codes.py，实现一个优惠码规范化函数：去掉首尾空格，统一转成大写，并拒绝空字符串。 | True → True | code/create → code/create | — |
| N12 | 把 invoice_totals.py 里的金额汇总改成使用 Decimal，避免多笔小数金额相加时出现精度误差。 | True → True | code/update → code/update | — |
| N13 | 请删除 feed_reader.py 中不再使用的 parse_legacy_feed 函数，同时去掉这个文件对它的调用。 | True → True | code/delete → code/delete | — |
| N14 | 请将 preview.toml 的监听地址改为 127.0.0.1，端口设成 8127，保存配置。 | False → False | code/update → code/update | primary: expected ['configure'], got code; missing affirmative intent: configure; action for configure: expected one of ['update'] |
| N15 | 新建 docs/localization.md，写一份给译者看的占位符说明，包含一个正确例子和一个错误例子。 | True → True | document/create → document/create | — |
| N16 | 为 normalize_city 新增测试，放到 tests/test_city_names.py，至少覆盖中文城市名、空白输入和带连字符的英文名。 | True → True | test/create → test/create | — |
| N17 | 请把 warehouse_report.py 里揉在一起的汇总计算和终端打印拆成两个函数，输出格式保持一致，直接完成这次重构。 | True → True | refactor/update → refactor/update | — |
| N18 | 现在重启本机的 minicode-preview 服务，让它加载刚保存的配置。 | False → False | 无 → 无 | primary: expected ['system'], got unknown; missing affirmative intent: system; action for system: expected one of ['execute'] |
| N19 | 请记住我的长期偏好：讲解 Python 示例时使用四个空格缩进，注释尽量写中文。以后都按这个偏好来。 | True → True | memory/create → memory/create | — |
| N20 | 把 assets/old-logo.svg 移到 archive/old-logo.svg，保留文件原有内容，不需要另外复制一份。 | True → True | code/move → code/move | — |
| N21 | 运行 csv_importer.py 时，带 BOM 的表头导致第一列取不到值。帮我定位这个问题的原因，先给出诊断结论。 | True → True | 无 → 无 | — |
| N22 | 生成器表达式和列表推导式在内存占用、重复遍历方面有什么区别？用读取大日志的场景解释一下。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing action alternative group: explain \| question |
| N23 | 在仓库里找一下 session_expired 这个事件是从哪里发出的，把定义位置和发送它的调用点列出来。 | True → True | 无 → 无 | — |
| N24 | 请审查 task_queue.py 的并发控制，重点看两个消费者同时领取任务时会不会重复处理，给出具体风险点。 | True → True | 无 → 无 | — |
| N25 | 运行 tests/test_inventory.py 里的测试，把通过数、失败数和失败用例名称告诉我。 | True → True | test/execute → test/execute | — |
| N26 | 在 docs/export_format.md 里补充日期字段的时区约定，以及空值在导出文件中的表示方式。 | True → True | document/update → document/update | — |
| N27 | 写一个 Python 函数，把一组左右闭区间合并成互不重叠的区间，返回值按起点排序。 | True → True | code/create → code/create | — |
| N28 | Python 的浅拷贝为什么还会共享内层列表？我总把它和深拷贝弄混，麻烦给一个小例子讲清楚。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing action alternative group: explain \| question |
| N29 | 搜索 Python 官方文档，确认 pathlib.Path.replace 在目标文件已经存在时的行为，并附上对应页面链接。 | True → True | 无 → 无 | — |
| N30 | 早上好，昨天的讨论很有帮助，今天也请多关照。 | False → False | 无 → 无 | primary: expected ['chat'], got unknown; missing affirmative intent: chat; action for chat: expected one of ['unknown', 'read']; read_only: expected True, got False |
| N31 | 就照这个来，计价函数和配套测试都新增，边界要覆盖恰好跨入下一档的重量。 | False → False | 无 → 无 | primary: expected ['code', 'test'], got unknown; missing affirmative intent: code; missing affirmative intent: test; action for code: expected one of ['create', 'update']; action for test: expected one of ['create', 'update']; missing entity files: package_rates.py; missing entity files: tests/test_package_rates.py |
| N32 | 先别删，取消刚才的要求。只解释为什么还需要保留这份导出快照。 | False → False | 无 → 无 | missing negated intent: system |
| N33 | 给 receipt_parser.py 增加识别电子发票号码的逻辑，并在 docs/receipts.md 里补上支持范围和输入例子。 | True → True | code/update, document/update → code/update, document/update | — |
| N34 | 先在代码库里找谁调用了 invalidate_user_cache，再解释它为什么要等事务提交之后才触发。 | True → True | 无 → 无 | — |
| N35 | 请评审 permission_rules.py 是否漏掉只读访客的边界情况，然后运行 tests/test_permissions.py，报告现有测试覆盖了哪些相关分支。 | True → True | test/execute → test/execute | — |
| N36 | 采用第二种，把它迁过去并改好引用；完成后说明这次模块职责怎么划分。 | False → False | code/update → code/update | missing entity files: metrics_view.py; missing entity files: date_display.py |
| N37 | 这轮只做第一个，第二个先不做。按前面说的把校验函数实现好即可。 | False → False | 无 → 无 | primary: expected ['code'], got unknown; missing affirmative intent: code; missing negated intent: document; action for code: expected one of ['create', 'update']; missing entity files: sku_validator.py; missing entity files: docs/sku_rules.md |
| N38 | 记住这个偏好，以后给我的示例默认采用它；另外在 README.md 里补一段采用这种写法的理由。 | True → True | memory/create, document/update → memory/create, document/update | — |
| N39 | 将 .coveragerc 的统计范围调整为 src/catalog，接着执行 tests/test_catalog.py，看看这部分覆盖率是多少。 | False → False | test/execute → test/execute | missing affirmative intent: configure; action for configure: expected one of ['update']; missing requested operation order: ['configure', 'test'] |
| N40 | 那就把那个处理好吧。 | True → True | 无 → 无 | — |

## prior40 / live

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| N01 | 值班手册里的“清空缓存目录再重启进程”是什么意思？请解释这条操作会影响什么；这次不要执行任何系统操作。 | False → False | 无 → 无 | missing negated intent: system |
| N02 | 假设把价格计算从订单服务拆成独立模块，会多出哪些接口耦合？现在不重构，只分析这样划分的利弊。 | True → True | 无 → 无 | — |
| N03 | 我撤回“给 import_jobs.py 加自动重试”的要求。先说明任务失败后直接退出可能有哪些后果，不要改这份文件。 | True → True | 无 → 无 | — |
| N04 | 今天先别写迁移脚本，也别改数据库连接配置；这两件事都暂停。 | False → False | 无 → 无 | missing negated intent: configure |
| N05 | 培训材料里出现了 `pytest -x`。这个参数跟默认跑法有什么不同？这次不要运行测试，只把差别讲明白。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; missing action alternative group: explain \| question |
| N06 | 如果将 worker.toml 的并发数从 4 调到 40，数据库连接池可能发生什么？我只想评估容量，暂时不要修改配置。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review'], got unknown; missing negated intent: configure; missing action alternative group: explain \| question \| review |
| N07 | 帮我在 architecture.md 里找提到“重写路由器”的段落，找到后告诉我位置；引号中的内容只是检索词，路由代码别改。 | True → True | 无 → 无 | — |
| N08 | 重启服务那件事先取消。现在只从 gateway.log 中找出包含 upstream timeout 的记录，列出出现时间即可。 | False → False | 无 → 无 | missing negated intent: system |
| N09 | 有人建议“把入门指南整篇删掉重新写”。这轮不用编辑文档，请只评审 onboarding.md 的章节顺序是否方便新人上手。 | False → False | 无 → 无 | missing negated intent: document |
| N10 | 日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。解释一下它想解决什么问题，先不要重命名或移动任何文件。 | False → False | 无 → 无 | missing negated intent: system; missing entity files: profile.json; missing entity files: profile.old.json |
| N11 | 请新增 coupon_codes.py，实现一个优惠码规范化函数：去掉首尾空格，统一转成大写，并拒绝空字符串。 | True → True | code/create → code/create | — |
| N12 | 把 invoice_totals.py 里的金额汇总改成使用 Decimal，避免多笔小数金额相加时出现精度误差。 | True → True | code/update → code/update | — |
| N13 | 请删除 feed_reader.py 中不再使用的 parse_legacy_feed 函数，同时去掉这个文件对它的调用。 | True → True | code/delete → code/delete, code/update | — |
| N14 | 请将 preview.toml 的监听地址改为 127.0.0.1，端口设成 8127，保存配置。 | False → False | code/update → code/update | primary: expected ['configure'], got code; missing affirmative intent: configure; action for configure: expected one of ['update'] |
| N15 | 新建 docs/localization.md，写一份给译者看的占位符说明，包含一个正确例子和一个错误例子。 | True → True | document/create, document/create → document/create, document/create | — |
| N16 | 为 normalize_city 新增测试，放到 tests/test_city_names.py，至少覆盖中文城市名、空白输入和带连字符的英文名。 | True → True | test/create → test/create | — |
| N17 | 请把 warehouse_report.py 里揉在一起的汇总计算和终端打印拆成两个函数，输出格式保持一致，直接完成这次重构。 | True → True | refactor/update → refactor/update | — |
| N18 | 现在重启本机的 minicode-preview 服务，让它加载刚保存的配置。 | False → False | 无 → 无 | primary: expected ['system'], got unknown; missing affirmative intent: system; action for system: expected one of ['execute'] |
| N19 | 请记住我的长期偏好：讲解 Python 示例时使用四个空格缩进，注释尽量写中文。以后都按这个偏好来。 | True → True | memory/create → memory/create | — |
| N20 | 把 assets/old-logo.svg 移到 archive/old-logo.svg，保留文件原有内容，不需要另外复制一份。 | True → True | code/move → code/move | — |
| N21 | 运行 csv_importer.py 时，带 BOM 的表头导致第一列取不到值。帮我定位这个问题的原因，先给出诊断结论。 | True → True | 无 → 无 | — |
| N22 | 生成器表达式和列表推导式在内存占用、重复遍历方面有什么区别？用读取大日志的场景解释一下。 | True → True | 无 → 无 | — |
| N23 | 在仓库里找一下 session_expired 这个事件是从哪里发出的，把定义位置和发送它的调用点列出来。 | True → True | 无 → 无 | — |
| N24 | 请审查 task_queue.py 的并发控制，重点看两个消费者同时领取任务时会不会重复处理，给出具体风险点。 | True → True | 无 → 无 | — |
| N25 | 运行 tests/test_inventory.py 里的测试，把通过数、失败数和失败用例名称告诉我。 | True → True | test/execute → test/execute | — |
| N26 | 在 docs/export_format.md 里补充日期字段的时区约定，以及空值在导出文件中的表示方式。 | True → True | document/update → document/update | — |
| N27 | 写一个 Python 函数，把一组左右闭区间合并成互不重叠的区间，返回值按起点排序。 | True → True | code/create → code/create | — |
| N28 | Python 的浅拷贝为什么还会共享内层列表？我总把它和深拷贝弄混，麻烦给一个小例子讲清楚。 | True → True | 无 → 无 | — |
| N29 | 搜索 Python 官方文档，确认 pathlib.Path.replace 在目标文件已经存在时的行为，并附上对应页面链接。 | True → True | 无 → 无 | — |
| N30 | 早上好，昨天的讨论很有帮助，今天也请多关照。 | True → True | 无 → 无 | — |
| N31 | 就照这个来，计价函数和配套测试都新增，边界要覆盖恰好跨入下一档的重量。 | True → True | code/create, test/create → code/create, test/create | — |
| N32 | 先别删，取消刚才的要求。只解释为什么还需要保留这份导出快照。 | False → False | 无 → 无 | missing negated intent: system |
| N33 | 给 receipt_parser.py 增加识别电子发票号码的逻辑，并在 docs/receipts.md 里补上支持范围和输入例子。 | True → True | code/update, document/update → code/update, document/update | — |
| N34 | 先在代码库里找谁调用了 invalidate_user_cache，再解释它为什么要等事务提交之后才触发。 | True → True | 无 → 无 | — |
| N35 | 请评审 permission_rules.py 是否漏掉只读访客的边界情况，然后运行 tests/test_permissions.py，报告现有测试覆盖了哪些相关分支。 | True → True | test/execute → test/execute | — |
| N36 | 采用第二种，把它迁过去并改好引用；完成后说明这次模块职责怎么划分。 | True → True | refactor/update, code/update → refactor/update, code/update | — |
| N37 | 这轮只做第一个，第二个先不做。按前面说的把校验函数实现好即可。 | False → False | 无 → 无 | primary: expected ['code'], got unknown; missing affirmative intent: code; missing negated intent: document; action for code: expected one of ['create', 'update']; missing entity files: sku_validator.py; missing entity files: docs/sku_rules.md |
| N38 | 记住这个偏好，以后给我的示例默认采用它；另外在 README.md 里补一段采用这种写法的理由。 | True → True | memory/create, document/update → memory/create, document/update | — |
| N39 | 将 .coveragerc 的统计范围调整为 src/catalog，接着执行 tests/test_catalog.py，看看这部分覆盖率是多少。 | True → True | configure/update, test/execute → configure/update, test/execute | — |
| N40 | 那就把那个处理好吧。 | True → True | 无 → 无 | — |

## priorfresh40 / disabled

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| H01 | 上周我已经把用户缓存删掉了。现在请解释一下 TTL 到期与主动失效有什么区别。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H02 | 需求单写着“创建 deploy.sh，再执行它上传构建产物”。这里只帮我审一下这段流程是否合理，不要创建文件，也不要执行脚本。 | False → False | 无 → 无 | primary: expected ['review', 'explain', 'question'], got unknown; missing required operation group 1: [{'intent': 'review', 'actions': ['read', 'analyze']}, {'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| H03 | 如果以后把导入器改成异步，哪些地方可能出现竞态？先分析可能性，我还没决定要不要改。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review', 'refactor'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}, {'intent': 'refactor', 'actions': ['read', 'analyze']}] |
| H04 | 日志里那行 rm -rf staging-cache 是昨天的操作记录。帮我查清今天的 PermissionError 为什么发生，只读排查即可。 | False → False | 无 → 无 | primary: expected ['debug', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'debug', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}] |
| H05 | 麻烦只读检查一下连接池有没有泄漏的迹象，把可疑位置列出来，先别修。 | True → True | 无 → 无 | — |
| H06 | 同事建议“删掉兼容旧协议的分支”，我还拿不准。比较一下删除和保留各有什么代价，给我判断依据。 | False → False | 无 → 无 | primary: expected ['explain', 'review', 'question', 'refactor'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['compare', 'analyze']}, {'intent': 'review', 'actions': ['compare', 'analyze']}, {'intent': 'question', 'actions': ['compare', 'analyze']}, {'intent': 'refactor', 'actions': ['compare', 'analyze']}] |
| H07 | The tutorial says, “Create secrets.env and run the deployment script.” I only want an explanation of why that sequence is used. | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| H08 | 假设我们把 SQLite 换成 PostgreSQL，现有事务逻辑会有什么变化？这轮只讨论，不改配置。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review', 'configure'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'review', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'configure', 'actions': ['read', 'analyze', 'compare']}] |
| H09 | 单元测试里有个字符串是“重命名账户并删除会话”。这段字符串为什么会触发转义错误？解释一下即可。 | True → True | 无 → 无 | — |
| H10 | Did yesterday's migration delete the audit table? Inspect the migration diff and tell me what actually changed; leave the database alone. | False → False | 无 → 无 | primary: expected ['review', 'search', 'explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'search', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H11 | 给我新建一个 receipt_codec.py，提供把收据字典编码成 JSON 字节的函数。 | False → False | 无 → 无 | primary: expected ['code'], got unknown; missing required operation group 1: [{'intent': 'code', 'actions': ['create']}] |
| H12 | normalize_handle 现在会把内部空格也删掉，请改成只去掉首尾空白，内部空格保留。 | False → False | 无 → 无 | primary: expected ['code', 'debug'], got unknown; missing required operation group 1: [{'intent': 'code', 'actions': ['update']}, {'intent': 'debug', 'actions': ['update']}] |
| H13 | 旧的 sandbox_preview.py 已经不用了，直接从项目里删除这个文件。 | False → True | 无 → code/delete | — |
| H14 | 在项目根目录执行 python tools/rebuild_manifest.py，让它重新生成清单。 | False → False | test/execute → test/execute | primary: expected ['system', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| H15 | 帮我跑一下 python -m pytest tests/test_receipts.py，把失败摘要发在这里。 | True → True | test/execute → test/execute | — |
| H16 | 把 session_helpers.py 重命名为 session_store.py，并把项目中的导入路径一起改好。 | True → True | code/rename, code/update → code/rename, code/update | — |
| H17 | 请把 pyproject.toml 里的默认日志级别从 INFO 调成 WARNING，保存修改。 | False → False | debug/update → debug/update | primary: expected ['configure', 'code'], got debug; missing required operation group 1: [{'intent': 'configure', 'actions': ['update']}, {'intent': 'code', 'actions': ['update']}] |
| H18 | Please add a new Python CLI in tools/check_receipts.py that prints the number of invalid receipt records. | True → True | code/create → code/create | — |
| H19 | Remove the temporary print statements from the request handler and save the cleaned-up file. | False → False | 无 → 无 | primary: expected ['code', 'refactor', 'debug'], got unknown; missing required operation group 1: [{'intent': 'code', 'actions': ['update', 'delete']}, {'intent': 'refactor', 'actions': ['update', 'delete']}, {'intent': 'debug', 'actions': ['update', 'delete']}] |
| H20 | Go ahead and run python tools/export_catalog.py --format json from this checkout. | False → False | test/execute → test/execute | primary: expected ['system', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| H21 | async with 离开代码块时究竟会调用什么？用一个小例子讲清楚就行。 | True → True | 无 → 无 | — |
| H22 | 项目里是在哪里给 X-Trace-Id 请求头赋值的？帮我找到入口和相关调用点。 | True → True | 无 → 无 | — |
| H23 | 这个服务运行一晚后内存一直涨。先根据现有代码找可能的原因，不要改代码或启动服务。 | True → True | 无 → 无 | — |
| H24 | README 的快速开始已经过时了，请更新为现在的 uv sync 和 uv run 启动方式。 | False → False | 无 → 无 | primary: expected ['document'], got unknown; missing affirmative intent: document; missing required operation group 1: [{'intent': 'document', 'actions': ['update']}] |
| H25 | 把 Ruff 的行宽配置设成 100，写进现有的 pyproject.toml。 | False → False | document/create → document/create | primary: expected ['configure', 'code'], got document; missing required operation group 1: [{'intent': 'configure', 'actions': ['update']}, {'intent': 'code', 'actions': ['update']}] |
| H26 | 看一下这个补丁有没有遗漏边界条件，优先检查空输入和重复记录，给我审查意见。 | False → False | 无 → 无 | primary: expected ['review'], got unknown; missing affirmative intent: review; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['read', 'analyze']}] |
| H27 | 这里选择生成器而不是列表会节省多少内存？结合当前这段数据流分析一下。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'review', 'actions': ['read', 'analyze', 'compare']}] |
| H28 | Python 的浅拷贝和深拷贝遇到嵌套字典时有什么差别？ | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H29 | Where is ReceiptValidationError defined, and which modules import it? | False → False | 无 → 无 | primary: expected ['search', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'search', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| H30 | What does “Task was destroyed but it is pending” mean in asyncio, and what usually causes it? | False → False | 无 → 无 | primary: expected ['explain', 'debug', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'debug', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| H31 | 就改它吧：空字符串也当作无效收据处理。 | False → False | 无 → 无 | primary: expected ['code', 'debug'], got unknown; missing required operation group 1: [{'intent': 'code', 'actions': ['update']}, {'intent': 'debug', 'actions': ['update']}] |
| H32 | 先别重构，解释一下它为什么会把关闭连接的异常吞掉。 | True → True | 无 → 无 | — |
| H33 | 找出订单编号的生成函数，再解释它为什么能避免同一秒内重复。 | False → True | code/create → 无 | — |
| H34 | 给这个修复补一个覆盖空列表的回归测试，然后运行对应的测试文件。 | True → True | test/create, test/execute → test/create, test/execute | — |
| H35 | 把导出函数增加一个 include_archived 参数，随后更新 README 中的调用示例。 | False → False | document/update → document/update | missing required operation group 1: [{'intent': 'code', 'actions': ['update']}, {'intent': 'refactor', 'actions': ['update']}] |
| H36 | 审一下这个改动有没有并发风险，这次不用重构，也不用跑测试。 | False → True | 无 → 无 | — |
| H37 | 先暂停刚才的实现。这轮只告诉我，这两个字段为什么不能合成一个？ | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H38 | Use the second approach, and create tests/test_naive_receipt_time.py with a case for a missing timezone offset. | False → False | test/create → test/create | missing required operation group 1: [{'intent': 'code', 'actions': ['update']}, {'intent': 'refactor', 'actions': ['update']}] |
| H39 | Find both parser implementations, then compare how they handle incomplete records. Keep this as an investigation only. | False → False | 无 → 无 | missing required operation group 2: [{'intent': 'explain', 'actions': ['compare', 'analyze']}, {'intent': 'review', 'actions': ['compare', 'analyze']}, {'intent': 'question', 'actions': ['compare', 'analyze']}] |
| H40 | Now run that command and report whether it passes. | True → True | test/execute → test/execute | — |

## priorfresh40 / live

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| H01 | 上周我已经把用户缓存删掉了。现在请解释一下 TTL 到期与主动失效有什么区别。 | True → True | 无 → 无 | — |
| H02 | 需求单写着“创建 deploy.sh，再执行它上传构建产物”。这里只帮我审一下这段流程是否合理，不要创建文件，也不要执行脚本。 | True → True | 无 → 无 | — |
| H03 | 如果以后把导入器改成异步，哪些地方可能出现竞态？先分析可能性，我还没决定要不要改。 | True → False | 无 → 无 | primary: expected ['explain', 'question', 'review', 'refactor'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}, {'intent': 'refactor', 'actions': ['read', 'analyze']}] |
| H04 | 日志里那行 rm -rf staging-cache 是昨天的操作记录。帮我查清今天的 PermissionError 为什么发生，只读排查即可。 | True → True | 无 → 无 | — |
| H05 | 麻烦只读检查一下连接池有没有泄漏的迹象，把可疑位置列出来，先别修。 | True → True | 无 → 无 | — |
| H06 | 同事建议“删掉兼容旧协议的分支”，我还拿不准。比较一下删除和保留各有什么代价，给我判断依据。 | False → False | 无 → 无 | primary: expected ['explain', 'review', 'question', 'refactor'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['compare', 'analyze']}, {'intent': 'review', 'actions': ['compare', 'analyze']}, {'intent': 'question', 'actions': ['compare', 'analyze']}, {'intent': 'refactor', 'actions': ['compare', 'analyze']}] |
| H07 | The tutorial says, “Create secrets.env and run the deployment script.” I only want an explanation of why that sequence is used. | True → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| H08 | 假设我们把 SQLite 换成 PostgreSQL，现有事务逻辑会有什么变化？这轮只讨论，不改配置。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review', 'configure'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'review', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'configure', 'actions': ['read', 'analyze', 'compare']}] |
| H09 | 单元测试里有个字符串是“重命名账户并删除会话”。这段字符串为什么会触发转义错误？解释一下即可。 | True → True | 无 → 无 | — |
| H10 | Did yesterday's migration delete the audit table? Inspect the migration diff and tell me what actually changed; leave the database alone. | False → False | 无 → 无 | primary: expected ['review', 'search', 'explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'search', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H11 | 给我新建一个 receipt_codec.py，提供把收据字典编码成 JSON 字节的函数。 | True → True | code/create → code/create | — |
| H12 | normalize_handle 现在会把内部空格也删掉，请改成只去掉首尾空白，内部空格保留。 | True → True | code/update → code/update | — |
| H13 | 旧的 sandbox_preview.py 已经不用了，直接从项目里删除这个文件。 | False → True | 无 → code/delete | — |
| H14 | 在项目根目录执行 python tools/rebuild_manifest.py，让它重新生成清单。 | False → False | test/execute → test/execute | primary: expected ['system', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| H15 | 帮我跑一下 python -m pytest tests/test_receipts.py，把失败摘要发在这里。 | True → True | test/execute → test/execute | — |
| H16 | 把 session_helpers.py 重命名为 session_store.py，并把项目中的导入路径一起改好。 | True → True | code/rename, code/update → code/rename, code/update | — |
| H17 | 请把 pyproject.toml 里的默认日志级别从 INFO 调成 WARNING，保存修改。 | False → False | debug/update → debug/update | primary: expected ['configure', 'code'], got debug; missing required operation group 1: [{'intent': 'configure', 'actions': ['update']}, {'intent': 'code', 'actions': ['update']}] |
| H18 | Please add a new Python CLI in tools/check_receipts.py that prints the number of invalid receipt records. | True → True | code/create → code/create | — |
| H19 | Remove the temporary print statements from the request handler and save the cleaned-up file. | True → True | refactor/update → refactor/update | — |
| H20 | Go ahead and run python tools/export_catalog.py --format json from this checkout. | False → False | test/execute → test/execute | primary: expected ['system', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| H21 | async with 离开代码块时究竟会调用什么？用一个小例子讲清楚就行。 | True → True | 无 → 无 | — |
| H22 | 项目里是在哪里给 X-Trace-Id 请求头赋值的？帮我找到入口和相关调用点。 | True → True | 无 → 无 | — |
| H23 | 这个服务运行一晚后内存一直涨。先根据现有代码找可能的原因，不要改代码或启动服务。 | True → True | 无 → 无 | — |
| H24 | README 的快速开始已经过时了，请更新为现在的 uv sync 和 uv run 启动方式。 | True → False | document/update → 无 | primary: expected ['document'], got unknown; missing affirmative intent: document; missing required operation group 1: [{'intent': 'document', 'actions': ['update']}] |
| H25 | 把 Ruff 的行宽配置设成 100，写进现有的 pyproject.toml。 | True → True | configure/update, document/create → configure/update, document/create | — |
| H26 | 看一下这个补丁有没有遗漏边界条件，优先检查空输入和重复记录，给我审查意见。 | True → False | 无 → 无 | primary: expected ['review'], got unknown; missing affirmative intent: review; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['read', 'analyze']}] |
| H27 | 这里选择生成器而不是列表会节省多少内存？结合当前这段数据流分析一下。 | True → True | 无 → 无 | — |
| H28 | Python 的浅拷贝和深拷贝遇到嵌套字典时有什么差别？ | True → True | 无 → 无 | — |
| H29 | Where is ReceiptValidationError defined, and which modules import it? | True → True | 无 → 无 | — |
| H30 | What does “Task was destroyed but it is pending” mean in asyncio, and what usually causes it? | True → True | 无 → 无 | — |
| H31 | 就改它吧：空字符串也当作无效收据处理。 | True → True | code/update → code/update | — |
| H32 | 先别重构，解释一下它为什么会把关闭连接的异常吞掉。 | True → True | 无 → 无 | — |
| H33 | 找出订单编号的生成函数，再解释它为什么能避免同一秒内重复。 | False → True | code/create → 无 | — |
| H34 | 给这个修复补一个覆盖空列表的回归测试，然后运行对应的测试文件。 | True → True | test/create, test/execute → test/create, test/execute | — |
| H35 | 把导出函数增加一个 include_archived 参数，随后更新 README 中的调用示例。 | True → True | code/update, document/update → code/update, document/update | — |
| H36 | 审一下这个改动有没有并发风险，这次不用重构，也不用跑测试。 | False → True | 无 → 无 | — |
| H37 | 先暂停刚才的实现。这轮只告诉我，这两个字段为什么不能合成一个？ | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze', 'compare']}, {'intent': 'question', 'actions': ['read', 'analyze', 'compare']}] |
| H38 | Use the second approach, and create tests/test_naive_receipt_time.py with a case for a missing timezone offset. | False → False | test/create → test/create | missing required operation group 1: [{'intent': 'code', 'actions': ['update']}, {'intent': 'refactor', 'actions': ['update']}] |
| H39 | Find both parser implementations, then compare how they handle incomplete records. Keep this as an investigation only. | True → True | 无 → 无 | — |
| H40 | Now run that command and report whether it passes. | True → True | test/execute → test/execute | — |

## fresh50 / disabled

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| T01 | 这段 Python 为什么输出 6？请一步一步解释：<br>nums = [1, 2, 3]<br>print(sum(nums)) | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T02 | 帮我在项目里找一下 get_current_user 的定义和调用位置。 | True → True | 无 → 无 | — |
| T03 | 帮我审查一下订单金额计算逻辑，重点看四舍五入和负数处理，先只给审查意见。 | True → True | 无 → 无 | — |
| T04 | 接口偶尔报 KeyError: user_id，能帮我分析可能的原因吗？暂时不要改文件。 | True → True | 无 → 无 | — |
| T05 | 这个类有点难维护。你觉得应该怎样重构？只讨论方案就好。 | False → False | refactor/update → refactor/update | forbidden affirmative action: refactor/update; read_only: expected True, got False; missing required operation group 1: [{'intent': 'refactor', 'actions': ['analyze', 'read']}, {'intent': 'review', 'actions': ['analyze', 'read']}, {'intent': 'explain', 'actions': ['analyze', 'read']}, {'intent': 'question', 'actions': ['analyze', 'read']}] |
| T06 | 读一下 README，告诉我本地开发环境需要哪些前置条件。 | False → False | 无 → 无 | primary: expected ['explain', 'document', 'search', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'document', 'actions': ['read', 'analyze']}, {'intent': 'search', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T07 | pytest 里的 -k 和 -m 分别有什么用？给我举例就行，不用真的运行测试。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'test'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'test', 'actions': ['read', 'analyze']}] |
| T08 | 日志里出现了“删除临时目录后重新执行任务”这句话。这通常是在提示什么？我现在只想理解日志。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'debug'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'debug', 'actions': ['read', 'analyze']}] |
| T09 | 如果把数据库连接池上限从 20 改成 100，会有哪些性能影响？先帮我分析，不要调整配置。 | False → False | 无 → 无 | primary: expected ['question', 'explain', 'configure', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'question', 'actions': ['analyze', 'read']}, {'intent': 'explain', 'actions': ['analyze', 'read']}, {'intent': 'configure', 'actions': ['analyze', 'read']}, {'intent': 'review', 'actions': ['analyze', 'read']}] |
| T10 | 不要添加缓存。先找出当前哪些函数已经在用缓存。 | True → True | 无 → 无 | — |
| T11 | 那个命令会改动工作区里的文件吗？先解释一下它的影响。 | False → False | code/update → code/update | primary: expected ['question', 'explain', 'review'], got code; forbidden affirmative action: code/update; read_only: expected True, got False; missing required operation group 1: [{'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}] |
| T12 | 它为什么在传入空列表时会报错？先帮我找原因。 | True → True | 无 → 无 | — |
| T13 | 比较一下用 threading.Lock 和 threading.RLock 的区别，尤其是同一线程重复获取锁的情况。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['compare']}, {'intent': 'question', 'actions': ['compare']}, {'intent': 'review', 'actions': ['compare']}] |
| T14 | async with 退出代码块时会做什么？请用连接释放的例子讲清楚。 | True → True | 无 → 无 | — |
| T15 | 审查这句注释是否准确：# delete the old value before inserting the replacement。只检查注释和代码行为是否一致。 | True → True | 无 → 无 | — |
| T16 | 终端提示“请先运行 npm install，再执行 npm run dev”。这两步各自做什么？我还不准备执行。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T17 | 先暂停刚才的实现。告诉我预计会涉及哪些文件，以及每个文件需要改什么；现在不要修改文件或执行命令。 | False → False | 无 → 无 | primary: expected ['explain', 'review', 'search', 'code'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}, {'intent': 'search', 'actions': ['read', 'analyze']}, {'intent': 'code', 'actions': ['read', 'analyze']}] |
| T18 | 假设我们把 parse_config 重命名成 load_config，哪些调用方可能受影响？请查找并列出位置，别真的重命名。 | False → False | 无 → 无 | primary: expected ['search', 'refactor', 'review'], got unknown; missing required operation group 1: [{'intent': 'search', 'actions': ['read', 'analyze']}, {'intent': 'refactor', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}] |
| T19 | git status 里的 “modified” 和 “untracked” 分别是什么意思？ | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T20 | 找出源码中的 TODO，并按模块整理成一份回复里的清单。 | True → True | 无 → 无 | — |
| T21 | 前一个方案和后一个方案的失败恢复能力有什么差别？帮我比较一下，先不选也不实施。 | False → False | 无 → 无 | primary: expected ['review', 'explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['compare']}, {'intent': 'explain', 'actions': ['compare']}, {'intent': 'question', 'actions': ['compare']}] |
| T22 | 先找到 retry_request 的定义，然后解释它为什么只重试网络异常。 | False → False | 无 → 无 | primary: expected ['search', 'explain'], got debug |
| T23 | 你说的“删掉兼容层”具体指什么？我只是问一下，不是在让你现在删。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T24 | 不用补单元测试；请指出这个函数有哪些分支还没有被现有测试覆盖。 | False → False | 无 → 无 | primary: expected ['test', 'review'], got unknown; missing required operation group 1: [{'intent': 'test', 'actions': ['analyze', 'read']}, {'intent': 'review', 'actions': ['analyze', 'read']}] |
| T25 | 在 utils/slug.py 里新建 slugify(text) 函数：转成小写，把连续空白替换成一个短横线。 | False → False | document/create → document/create | primary: expected ['code'], got document; missing required operation group 1: [{'intent': 'code', 'actions': ['create', 'update']}] |
| T26 | 修复 first_item 在空列表上抛出 IndexError 的问题，空列表时改为返回 None。 | True → True | debug/update → debug/update | — |
| T27 | 把这个负责下载、解析和保存的长函数拆成三个小函数，保持现有行为和外部接口不变。 | False → False | 无 → 无 | primary: expected ['refactor', 'code'], got unknown; missing required operation group 1: [{'intent': 'refactor', 'actions': ['split', 'update']}, {'intent': 'code', 'actions': ['split', 'update']}] |
| T28 | 运行 pytest tests/unit，看看单元测试能不能全部通过。 | True → True | test/execute → test/execute | — |
| T29 | 给 parse_date 新增两个单元测试，分别覆盖闰年 2 月 29 日和非法月份。 | True → True | test/create → test/create | — |
| T30 | 更新 README 的安装说明，把 Python 最低版本写成 3.11，并增加虚拟环境创建步骤。 | True → True | document/update, configure/update → document/update, configure/update | — |
| T31 | 修改 pyproject.toml，把 Ruff 的 line-length 设置为 100。 | True → True | code/update → code/update | — |
| T32 | 删除项目根目录里那个已经不用的 scratch_output.txt 临时文件。 | True → True | code/delete → code/delete | — |
| T33 | 把 helpers.py 重命名为 string_helpers.py，并更新受影响的导入。 | True → True | code/rename, code/update → code/rename, code/update | — |
| T34 | 将 scripts/clean_cache.py 移到 tools/clean_cache.py，并相应更新 README 中的路径。 | True → True | code/move, document/update → code/move, document/update | — |
| T35 | 不要把它整个重写，只修复刚才那个空字符串触发的异常。 | True → True | debug/update → debug/update | — |
| T36 | 按你刚才说的方案实现吧，直接修改这个模块。 | True → True | code/update → code/update | — |
| T37 | 先修复保存设置时丢失时区的问题，再运行相关测试验证。 | True → True | debug/update, test/execute → debug/update, test/execute | — |
| T38 | 先不要运行它，只把缺少的超时测试补上。 | False → False | 无 → 无 | primary: expected ['test'], got unknown; read_only: expected False, got True; missing required operation group 1: [{'intent': 'test', 'actions': ['create', 'update']}] |
| T39 | 错误提示说“删除 .cache/demo 后重新执行 python build_demo.py”。请照这个提示操作，删掉该缓存目录并重新运行这个构建命令。 | False → False | test/execute → test/execute | primary: expected ['system', 'debug', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['delete']}, {'intent': 'debug', 'actions': ['delete']}, {'intent': 'code', 'actions': ['delete']}]; missing required operation group 2: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'debug', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| T40 | 刚才说的不改代码先放一边。现在请把它改成忽略空行，保留其他行为。 | True → True | code/update → code/update | — |
| T41 | What does git rebase --onto mean? Explain it with a small branch example. | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T42 | Find where request_id is added to our application logs. | False → False | 无 → 无 | primary: expected ['search'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'search', 'actions': ['read', 'analyze']}] |
| T43 | Would that change make duplicate requests more likely? Analyze the trade-off before we implement anything. | False → False | 无 → 无 | primary: expected ['question', 'explain', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'question', 'actions': ['analyze', 'read', 'compare']}, {'intent': 'explain', 'actions': ['analyze', 'read', 'compare']}, {'intent': 'review', 'actions': ['analyze', 'read', 'compare']}] |
| T44 | The README says "run python manage.py migrate, then restart the server". Tell me what those steps do; do not execute them. | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T45 | Compare eager loading and lazy loading for this data access layer. Focus on query count and memory usage. | False → False | 无 → 无 | primary: expected ['explain', 'review', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['compare']}, {'intent': 'review', 'actions': ['compare']}, {'intent': 'question', 'actions': ['compare']}] |
| T46 | Please review that patch for off-by-one errors. Do not edit it yet. | False → False | 无 → 无 | primary: expected ['review'], got unknown; missing required operation group 1: [{'intent': 'review', 'actions': ['analyze', 'read']}] |
| T47 | Add a --dry-run option to the cleanup CLI. When enabled, print the files that would be removed without removing them. | False → False | test/execute → test/execute | primary: expected ['code'], got test; missing required operation group 1: [{'intent': 'code', 'actions': ['create', 'update']}] |
| T48 | Run just that test now. | True → True | test/execute → test/execute | — |
| T49 | Update setup.cfg so that flake8 ignores E203. Do not install any packages. | False → False | 无 → 无 | primary: expected ['configure', 'code'], got unknown; read_only: expected False, got True; missing required operation group 1: [{'intent': 'configure', 'actions': ['update']}, {'intent': 'code', 'actions': ['update']}] |
| T50 | Create docs/troubleshooting.md with a short section explaining how users can reset their local cache. | False → False | 无 → 无 | primary: expected ['document'], got unknown; missing required operation group 1: [{'intent': 'document', 'actions': ['create', 'update']}] |

## fresh50 / live

| 题号 | 问题 | 严格通过（前→后） | 肯定写/执行（前→后） | 本轮未通过检查 |
| --- | --- | --- | --- | --- |
| T01 | 这段 Python 为什么输出 6？请一步一步解释：<br>nums = [1, 2, 3]<br>print(sum(nums)) | True → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T02 | 帮我在项目里找一下 get_current_user 的定义和调用位置。 | True → True | 无 → 无 | — |
| T03 | 帮我审查一下订单金额计算逻辑，重点看四舍五入和负数处理，先只给审查意见。 | True → True | 无 → 无 | — |
| T04 | 接口偶尔报 KeyError: user_id，能帮我分析可能的原因吗？暂时不要改文件。 | True → True | 无 → 无 | — |
| T05 | 这个类有点难维护。你觉得应该怎样重构？只讨论方案就好。 | False → False | refactor/update → refactor/update | forbidden affirmative action: refactor/update; read_only: expected True, got False; missing required operation group 1: [{'intent': 'refactor', 'actions': ['analyze', 'read']}, {'intent': 'review', 'actions': ['analyze', 'read']}, {'intent': 'explain', 'actions': ['analyze', 'read']}, {'intent': 'question', 'actions': ['analyze', 'read']}] |
| T06 | 读一下 README，告诉我本地开发环境需要哪些前置条件。 | True → True | 无 → 无 | — |
| T07 | pytest 里的 -k 和 -m 分别有什么用？给我举例就行，不用真的运行测试。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'test'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'test', 'actions': ['read', 'analyze']}] |
| T08 | 日志里出现了“删除临时目录后重新执行任务”这句话。这通常是在提示什么？我现在只想理解日志。 | True → False | 无 → 无 | primary: expected ['explain', 'question', 'debug'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'debug', 'actions': ['read', 'analyze']}] |
| T09 | 如果把数据库连接池上限从 20 改成 100，会有哪些性能影响？先帮我分析，不要调整配置。 | False → False | 无 → 无 | primary: expected ['question', 'explain', 'configure', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'question', 'actions': ['analyze', 'read']}, {'intent': 'explain', 'actions': ['analyze', 'read']}, {'intent': 'configure', 'actions': ['analyze', 'read']}, {'intent': 'review', 'actions': ['analyze', 'read']}] |
| T10 | 不要添加缓存。先找出当前哪些函数已经在用缓存。 | True → True | 无 → 无 | — |
| T11 | 那个命令会改动工作区里的文件吗？先解释一下它的影响。 | False → False | code/update → code/update | primary: expected ['question', 'explain', 'review'], got code; forbidden affirmative action: code/update; read_only: expected True, got False; missing required operation group 1: [{'intent': 'question', 'actions': ['read', 'analyze']}, {'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}] |
| T12 | 它为什么在传入空列表时会报错？先帮我找原因。 | True → True | 无 → 无 | — |
| T13 | 比较一下用 threading.Lock 和 threading.RLock 的区别，尤其是同一线程重复获取锁的情况。 | False → False | 无 → 无 | primary: expected ['explain', 'question', 'review'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'explain', 'actions': ['compare']}, {'intent': 'question', 'actions': ['compare']}, {'intent': 'review', 'actions': ['compare']}] |
| T14 | async with 退出代码块时会做什么？请用连接释放的例子讲清楚。 | True → True | 无 → 无 | — |
| T15 | 审查这句注释是否准确：# delete the old value before inserting the replacement。只检查注释和代码行为是否一致。 | True → True | 无 → 无 | — |
| T16 | 终端提示“请先运行 npm install，再执行 npm run dev”。这两步各自做什么？我还不准备执行。 | False → False | 无 → 无 | primary: expected ['explain', 'question'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T17 | 先暂停刚才的实现。告诉我预计会涉及哪些文件，以及每个文件需要改什么；现在不要修改文件或执行命令。 | True → False | 无 → 无 | primary: expected ['explain', 'review', 'search', 'code'], got unknown; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'review', 'actions': ['read', 'analyze']}, {'intent': 'search', 'actions': ['read', 'analyze']}, {'intent': 'code', 'actions': ['read', 'analyze']}] |
| T18 | 假设我们把 parse_config 重命名成 load_config，哪些调用方可能受影响？请查找并列出位置，别真的重命名。 | True → True | 无 → 无 | — |
| T19 | git status 里的 “modified” 和 “untracked” 分别是什么意思？ | False → True | 无 → 无 | — |
| T20 | 找出源码中的 TODO，并按模块整理成一份回复里的清单。 | True → True | 无 → 无 | — |
| T21 | 前一个方案和后一个方案的失败恢复能力有什么差别？帮我比较一下，先不选也不实施。 | True → False | 无 → 无 | primary: expected ['review', 'explain', 'question'], got unknown; read_only: expected True, got False; missing required operation group 1: [{'intent': 'review', 'actions': ['compare']}, {'intent': 'explain', 'actions': ['compare']}, {'intent': 'question', 'actions': ['compare']}] |
| T22 | 先找到 retry_request 的定义，然后解释它为什么只重试网络异常。 | False → False | 无 → 无 | primary: expected ['search', 'explain'], got debug |
| T23 | 你说的“删掉兼容层”具体指什么？我只是问一下，不是在让你现在删。 | True → False | 无 → 无 | primary: expected ['explain', 'question'], got chat; missing required operation group 1: [{'intent': 'explain', 'actions': ['read', 'analyze']}, {'intent': 'question', 'actions': ['read', 'analyze']}] |
| T24 | 不用补单元测试；请指出这个函数有哪些分支还没有被现有测试覆盖。 | True → True | 无 → 无 | — |
| T25 | 在 utils/slug.py 里新建 slugify(text) 函数：转成小写，把连续空白替换成一个短横线。 | False → False | document/create → document/create | primary: expected ['code'], got document; missing required operation group 1: [{'intent': 'code', 'actions': ['create', 'update']}] |
| T26 | 修复 first_item 在空列表上抛出 IndexError 的问题，空列表时改为返回 None。 | True → True | debug/update → debug/update | — |
| T27 | 把这个负责下载、解析和保存的长函数拆成三个小函数，保持现有行为和外部接口不变。 | False → False | 无 → 无 | primary: expected ['refactor', 'code'], got unknown; missing required operation group 1: [{'intent': 'refactor', 'actions': ['split', 'update']}, {'intent': 'code', 'actions': ['split', 'update']}] |
| T28 | 运行 pytest tests/unit，看看单元测试能不能全部通过。 | True → True | test/execute → test/execute | — |
| T29 | 给 parse_date 新增两个单元测试，分别覆盖闰年 2 月 29 日和非法月份。 | True → True | test/create → test/create | — |
| T30 | 更新 README 的安装说明，把 Python 最低版本写成 3.11，并增加虚拟环境创建步骤。 | True → True | document/update, configure/update → document/update, configure/update | — |
| T31 | 修改 pyproject.toml，把 Ruff 的 line-length 设置为 100。 | True → True | code/update → code/update | — |
| T32 | 删除项目根目录里那个已经不用的 scratch_output.txt 临时文件。 | True → True | code/delete → code/delete | — |
| T33 | 把 helpers.py 重命名为 string_helpers.py，并更新受影响的导入。 | True → True | code/rename, code/update → code/rename, code/update | — |
| T34 | 将 scripts/clean_cache.py 移到 tools/clean_cache.py，并相应更新 README 中的路径。 | True → True | code/move, document/update → code/move, document/update | — |
| T35 | 不要把它整个重写，只修复刚才那个空字符串触发的异常。 | True → True | debug/update → debug/update | — |
| T36 | 按你刚才说的方案实现吧，直接修改这个模块。 | True → True | refactor/update, code/update → refactor/update, code/update | — |
| T37 | 先修复保存设置时丢失时区的问题，再运行相关测试验证。 | True → True | debug/update, test/execute → debug/update, test/execute | — |
| T38 | 先不要运行它，只把缺少的超时测试补上。 | True → False | test/create → 无 | primary: expected ['test'], got unknown; read_only: expected False, got True; missing required operation group 1: [{'intent': 'test', 'actions': ['create', 'update']}] |
| T39 | 错误提示说“删除 .cache/demo 后重新执行 python build_demo.py”。请照这个提示操作，删掉该缓存目录并重新运行这个构建命令。 | False → False | test/execute → test/execute | primary: expected ['system', 'debug', 'code'], got test; missing required operation group 1: [{'intent': 'system', 'actions': ['delete']}, {'intent': 'debug', 'actions': ['delete']}, {'intent': 'code', 'actions': ['delete']}]; missing required operation group 2: [{'intent': 'system', 'actions': ['execute']}, {'intent': 'debug', 'actions': ['execute']}, {'intent': 'code', 'actions': ['execute']}] |
| T40 | 刚才说的不改代码先放一边。现在请把它改成忽略空行，保留其他行为。 | True → True | code/update → code/update | — |
| T41 | What does git rebase --onto mean? Explain it with a small branch example. | True → True | 无 → 无 | — |
| T42 | Find where request_id is added to our application logs. | False → True | 无 → 无 | — |
| T43 | Would that change make duplicate requests more likely? Analyze the trade-off before we implement anything. | True → True | 无 → 无 | — |
| T44 | The README says "run python manage.py migrate, then restart the server". Tell me what those steps do; do not execute them. | False → True | 无 → 无 | — |
| T45 | Compare eager loading and lazy loading for this data access layer. Focus on query count and memory usage. | True → True | 无 → 无 | — |
| T46 | Please review that patch for off-by-one errors. Do not edit it yet. | True → True | 无 → 无 | — |
| T47 | Add a --dry-run option to the cleanup CLI. When enabled, print the files that would be removed without removing them. | False → False | test/execute → test/execute | primary: expected ['code'], got test; missing required operation group 1: [{'intent': 'code', 'actions': ['create', 'update']}] |
| T48 | Run just that test now. | True → True | test/execute → test/execute | — |
| T49 | Update setup.cfg so that flake8 ignores E203. Do not install any packages. | True → True | configure/update → configure/update | — |
| T50 | Create docs/troubleshooting.md with a short section explaining how users can reset their local cache. | True → True | document/create → document/create | — |

