# 逐题对照
旧50保留原计分器；新40按首次候选运行前冻结的作者备选动作分组计分。
## 旧50：LLM开启
| 题号 | 优化前 | 优化后 | 原文 | 优化后未通过项 |
| --- | --- | --- | --- | --- |
| C01 | 通过 | 通过 | 帮我写一个 Python 函数，把一组订单按用户 ID 分组。 | — |
| C02 | 通过 | 未通过 | 给登录接口加上每分钟最多五次的限流功能。 | primary: expected ['code'], got unknown；missing affirmative intent: code；action for code: expected one of ['create', 'update'] |
| C03 | 通过 | 通过 | 把 src/cache.py 里的缓存过期时间改成 300 秒。 | — |
| C04 | 通过 | 通过 | Please add a CLI command that exports the current settings as JSON. | — |
| C05 | 通过 | 通过 | 服务一启动就报 ModuleNotFoundError，帮我定位原因。 | — |
| C06 | 通过 | 通过 | 用户点一次支付却扣了两次钱，查一下是哪里出了问题。 | — |
| C07 | 通过 | 通过 | Fix the race condition in worker.py that occasionally loses queued jobs. | — |
| C08 | 通过 | 通过 | 重构一下订单模块，把重复的价格计算抽成公共函数，行为保持一致。 | — |
| C09 | 通过 | 通过 | 这个函数两百多行了，拆成几个职责单一的小函数。 | — |
| C10 | 通过 | 通过 | 解释一下 src/session.py 是怎么保存会话的。 | — |
| C11 | 未通过 | 通过 | asyncio.gather 和 TaskGroup 有什么区别？举个小例子说明。 | — |
| C12 | 通过 | 通过 | Walk me through how a request reaches the database in this project. | — |
| C13 | 通过 | 通过 | 在项目里找出所有调用 refresh_token 的地方。 | — |
| C14 | 通过 | 通过 | 帮我查一下 FastAPI 官方文档里关于流式响应的说明。 | — |
| C15 | 未通过 | 通过 | 配置文件放在哪个目录？帮我在仓库里找找。 | — |
| C16 | 通过 | 通过 | Review the changes in auth.py and point out security issues. | — |
| C17 | 未通过 | 通过 | 帮我审查这个 PR，重点看异常处理有没有遗漏。 | — |
| C18 | 通过 | 通过 | 给 parse_date 函数补单元测试，覆盖空字符串和非法日期。 | — |
| C19 | 通过 | 通过 | 运行 tests/test_cache.py，告诉我哪些用例没通过。 | — |
| C20 | 通过 | 通过 | Add regression tests for the timeout bug we fixed in client.py. | — |
| C21 | 未通过 | 通过 | 更新 README.md，补上 Docker 部署步骤。 | — |
| C22 | 通过 | 通过 | 给这个 API 写一份接入文档，说明请求参数和错误码。 | — |
| C23 | 未通过 | 通过 | 把 pytest 的默认超时时间配置成 30 秒。 | — |
| C24 | 通过 | 通过 | Set up GitHub Actions to run lint on every pull request. | — |
| C25 | 未通过 | 通过 | Python 的字典为什么能保持插入顺序？ | — |
| C26 | 未通过 | 未通过 | 这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。 | primary: expected ['question', 'explain'], got unknown；read_only: expected True, got False |
| C27 | 通过 | 通过 | 你好呀，今天状态怎么样？ | — |
| C28 | 通过 | 通过 | 记住：以后给我举 Python 例子时，优先使用标准库。 | — |
| C29 | 通过 | 通过 | 我之前让你记住的代码风格偏好是什么？ | — |
| C30 | 未通过 | 未通过 | 清空当前对话，开始一个新会话。 | primary: expected ['system'], got unknown；missing affirmative intent: system |
| C31 | 通过 | 通过 | 别改 payment.py 的代码，也不要运行测试；只解释它里面 process_payment 为什么先校验金额。 | — |
| C32 | 未通过 | 通过 | 刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。 | — |
| C33 | 未通过 | 未通过 | 暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。 | primary: expected ['debug'], got explain；missing affirmative intent: debug |
| C34 | 未通过 | 通过 | 不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。 | — |
| C35 | 未通过 | 通过 | 撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。 | — |
| C36 | 通过 | 通过 | 工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。 | — |
| C37 | 通过 | 通过 | 同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。 | — |
| C38 | 未通过 | 通过 | 假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。 | — |
| C39 | 未通过 | 未通过 | 产品说明书里举了个例子：“搜索用户邮件并保存到本地”。帮我润色这句话，让操作范围更清楚。 | primary: expected ['document'], got explain；missing affirmative intent: document；action for document: expected one of ['update'] |
| C40 | 未通过 | 未通过 | 网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。 | primary: expected ['explain'], got question；missing affirmative intent: explain |
| C41 | 通过 | 通过 | 先修复 parser.py 在空输入时抛异常的问题，再为这个边界情况添加单元测试。 | — |
| C42 | 通过 | 通过 | 重构 cache.py 里重复的淘汰逻辑，保留现有行为；同时补充 README.md 中的缓存策略说明。 | — |
| C43 | 通过 | 通过 | 搜索项目里所有调用 fetch_user 的地方，然后解释其中 request_handler.py 的调用链。 | — |
| C44 | 通过 | 通过 | 审查 auth.py 的登录校验逻辑，并把发现的问题整理成 SECURITY_REVIEW.md。 | — |
| C45 | 通过 | 通过 | 在 settings.yaml 中把日志级别调为 DEBUG，然后运行 test_logging.py 验证日志输出。 | — |
| C46 | 未通过 | 未通过 | 继续审查后一个文件有没有重复扣款的风险。 | missing entity files: payment.py |
| C47 | 未通过 | 未通过 | 就按这个方案改，函数签名保持不变。 | primary: expected ['refactor'], got code；missing affirmative intent: refactor；action for refactor: expected one of ['update', 'split'] |
| C48 | 未通过 | 未通过 | 再加一个 None 输入的用例。 | missing entity files: utils.py |
| C49 | 通过 | 通过 | 这个能处理一下吗？ | — |
| C50 | 未通过 | 未通过 | 把那一行改对就行，其他内容不动。 | primary: expected ['document'], got unknown；missing affirmative intent: document；action for document: expected one of ['update']；missing entity files: examples.md |

## 新40：LLM开启
| 题号 | 优化前 | 优化后 | 原文 | 优化后未通过项 |
| --- | --- | --- | --- | --- |
| N01 | 未通过 | 未通过 | 值班手册里的“清空缓存目录再重启进程”是什么意思？请解释这条操作会影响什么；这次不要执行任何系统操作。 | missing negated intent: system |
| N02 | 通过 | 通过 | 假设把价格计算从订单服务拆成独立模块，会多出哪些接口耦合？现在不重构，只分析这样划分的利弊。 | — |
| N03 | 未通过 | 未通过 | 我撤回“给 import_jobs.py 加自动重试”的要求。先说明任务失败后直接退出可能有哪些后果，不要改这份文件。 | missing entity files: import_jobs.py |
| N04 | 未通过 | 未通过 | 今天先别写迁移脚本，也别改数据库连接配置；这两件事都暂停。 | missing negated intent: configure |
| N05 | 未通过 | 未通过 | 培训材料里出现了 `pytest -x`。这个参数跟默认跑法有什么不同？这次不要运行测试，只把差别讲明白。 | primary: expected ['explain', 'question'], got unknown；missing action alternative group: explain \| question |
| N06 | 未通过 | 未通过 | 如果将 worker.toml 的并发数从 4 调到 40，数据库连接池可能发生什么？我只想评估容量，暂时不要修改配置。 | primary: expected ['explain', 'question', 'review'], got unknown；missing negated intent: configure；missing action alternative group: explain \| question \| review |
| N07 | 通过 | 通过 | 帮我在 architecture.md 里找提到“重写路由器”的段落，找到后告诉我位置；引号中的内容只是检索词，路由代码别改。 | — |
| N08 | 未通过 | 未通过 | 重启服务那件事先取消。现在只从 gateway.log 中找出包含 upstream timeout 的记录，列出出现时间即可。 | missing negated intent: system；missing entity files: gateway.log |
| N09 | 未通过 | 未通过 | 有人建议“把入门指南整篇删掉重新写”。这轮不用编辑文档，请只评审 onboarding.md 的章节顺序是否方便新人上手。 | missing negated intent: document |
| N10 | 未通过 | 未通过 | 日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。解释一下它想解决什么问题，先不要重命名或移动任何文件。 | primary: expected ['explain', 'question'], got debug；missing negated intent: system；forbidden affirmative action: debug/update；missing entity files: profile.json；missing entity files: profile.old.json；read_only: expected True, got False；missing action alternative group: explain \| question |
| N11 | 通过 | 通过 | 请新增 coupon_codes.py，实现一个优惠码规范化函数：去掉首尾空格，统一转成大写，并拒绝空字符串。 | — |
| N12 | 通过 | 通过 | 把 invoice_totals.py 里的金额汇总改成使用 Decimal，避免多笔小数金额相加时出现精度误差。 | — |
| N13 | 通过 | 通过 | 请删除 feed_reader.py 中不再使用的 parse_legacy_feed 函数，同时去掉这个文件对它的调用。 | — |
| N14 | 未通过 | 未通过 | 请将 preview.toml 的监听地址改为 127.0.0.1，端口设成 8127，保存配置。 | primary: expected ['configure'], got code；missing affirmative intent: configure；action for configure: expected one of ['update'] |
| N15 | 通过 | 通过 | 新建 docs/localization.md，写一份给译者看的占位符说明，包含一个正确例子和一个错误例子。 | — |
| N16 | 通过 | 通过 | 为 normalize_city 新增测试，放到 tests/test_city_names.py，至少覆盖中文城市名、空白输入和带连字符的英文名。 | — |
| N17 | 通过 | 通过 | 请把 warehouse_report.py 里揉在一起的汇总计算和终端打印拆成两个函数，输出格式保持一致，直接完成这次重构。 | — |
| N18 | 未通过 | 未通过 | 现在重启本机的 minicode-preview 服务，让它加载刚保存的配置。 | primary: expected ['system'], got unknown；missing affirmative intent: system；action for system: expected one of ['execute'] |
| N19 | 未通过 | 通过 | 请记住我的长期偏好：讲解 Python 示例时使用四个空格缩进，注释尽量写中文。以后都按这个偏好来。 | — |
| N20 | 未通过 | 未通过 | 把 assets/old-logo.svg 移到 archive/old-logo.svg，保留文件原有内容，不需要另外复制一份。 | primary: expected ['system', 'code'], got unknown；missing entity files: assets/old-logo.svg；missing entity files: archive/old-logo.svg；missing action alternative group: system \| code |
| N21 | 未通过 | 未通过 | 运行 csv_importer.py 时，带 BOM 的表头导致第一列取不到值。帮我定位这个问题的原因，先给出诊断结论。 | primary: expected ['debug'], got test；read_only: expected True, got False |
| N22 | 通过 | 通过 | 生成器表达式和列表推导式在内存占用、重复遍历方面有什么区别？用读取大日志的场景解释一下。 | — |
| N23 | 通过 | 通过 | 在仓库里找一下 session_expired 这个事件是从哪里发出的，把定义位置和发送它的调用点列出来。 | — |
| N24 | 通过 | 通过 | 请审查 task_queue.py 的并发控制，重点看两个消费者同时领取任务时会不会重复处理，给出具体风险点。 | — |
| N25 | 通过 | 通过 | 运行 tests/test_inventory.py 里的测试，把通过数、失败数和失败用例名称告诉我。 | — |
| N26 | 通过 | 通过 | 在 docs/export_format.md 里补充日期字段的时区约定，以及空值在导出文件中的表示方式。 | — |
| N27 | 通过 | 通过 | 写一个 Python 函数，把一组左右闭区间合并成互不重叠的区间，返回值按起点排序。 | — |
| N28 | 未通过 | 通过 | Python 的浅拷贝为什么还会共享内层列表？我总把它和深拷贝弄混，麻烦给一个小例子讲清楚。 | — |
| N29 | 通过 | 通过 | 搜索 Python 官方文档，确认 pathlib.Path.replace 在目标文件已经存在时的行为，并附上对应页面链接。 | — |
| N30 | 通过 | 通过 | 早上好，昨天的讨论很有帮助，今天也请多关照。 | — |
| N31 | 未通过 | 通过 | 就照这个来，计价函数和配套测试都新增，边界要覆盖恰好跨入下一档的重量。 | — |
| N32 | 未通过 | 未通过 | 先别删，取消刚才的要求。只解释为什么还需要保留这份导出快照。 | missing negated intent: system；missing entity files: export_dump.csv |
| N33 | 未通过 | 未通过 | 给 receipt_parser.py 增加识别电子发票号码的逻辑，并在 docs/receipts.md 里补上支持范围和输入例子。 | missing affirmative intent: code；action for code: expected one of ['update'] |
| N34 | 通过 | 通过 | 先在代码库里找谁调用了 invalidate_user_cache，再解释它为什么要等事务提交之后才触发。 | — |
| N35 | 通过 | 通过 | 请评审 permission_rules.py 是否漏掉只读访客的边界情况，然后运行 tests/test_permissions.py，报告现有测试覆盖了哪些相关分支。 | — |
| N36 | 通过 | 通过 | 采用第二种，把它迁过去并改好引用；完成后说明这次模块职责怎么划分。 | — |
| N37 | 未通过 | 未通过 | 这轮只做第一个，第二个先不做。按前面说的把校验函数实现好即可。 | primary: expected ['code'], got unknown；missing affirmative intent: code；missing negated intent: document；action for code: expected one of ['create', 'update']；missing entity files: sku_validator.py；missing entity files: docs/sku_rules.md |
| N38 | 通过 | 通过 | 记住这个偏好，以后给我的示例默认采用它；另外在 README.md 里补一段采用这种写法的理由。 | — |
| N39 | 未通过 | 未通过 | 将 .coveragerc 的统计范围调整为 src/catalog，接着执行 tests/test_catalog.py，看看这部分覆盖率是多少。 | missing entity files: .coveragerc |
| N40 | 通过 | 通过 | 那就把那个处理好吧。 | — |

