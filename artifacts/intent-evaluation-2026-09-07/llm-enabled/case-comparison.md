# 同一批 50 题：关闭 / 默认开启 / 开启且超时 10 秒

评分沿用原始冻结预期，未事后改题改标签。全部检查通过仅指已标注要求，不代表所有参数与字段精确正确。

| ID | 问题 | 关闭：主类型 / 检查 | 开启 1.5s：状态 / 检查 | 开启 10s：主类型 / 状态 / 检查 | 变化 | 10s 操作 |
| --- | --- | --- | --- | --- | --- | --- |
| C01 | 帮我写一个 Python 函数，把一组订单按用户 ID 分组。 | code / 通过 | skipped / 通过 | code / skipped / 通过 | 冻结分数不变 | code/create |
| C02 | 给登录接口加上每分钟最多五次的限流功能。 | unknown / 未通过 | timeout / 未通过 | code / accepted / 通过 | 新增通过 | code/create |
| C03 | 把 src/cache.py 里的缓存过期时间改成 300 秒。 | code / 通过 | skipped / 通过 | code / skipped / 通过 | 冻结分数不变 | code/update |
| C04 | Please add a CLI command that exports the current settings as JSON. | code / 通过 | skipped / 通过 | code / skipped / 通过 | 冻结分数不变 | code/create |
| C05 | 服务一启动就报 ModuleNotFoundError，帮我定位原因。 | debug / 通过 | skipped / 通过 | debug / skipped / 通过 | 冻结分数不变 | debug/analyze |
| C06 | 用户点一次支付却扣了两次钱，查一下是哪里出了问题。 | unknown / 未通过 | timeout / 未通过 | debug / accepted / 通过 | 新增通过 | debug/analyze |
| C07 | Fix the race condition in worker.py that occasionally loses queued jobs. | debug / 通过 | skipped / 通过 | debug / skipped / 通过 | 冻结分数不变 | debug/update |
| C08 | 重构一下订单模块，把重复的价格计算抽成公共函数，行为保持一致。 | refactor / 通过 | skipped / 通过 | refactor / skipped / 通过 | 冻结分数不变 | refactor/update |
| C09 | 这个函数两百多行了，拆成几个职责单一的小函数。 | unknown / 未通过 | timeout / 未通过 | refactor / accepted / 通过 | 新增通过 | refactor/update |
| C10 | 解释一下 src/session.py 是怎么保存会话的。 | explain / 通过 | skipped / 通过 | explain / skipped / 通过 | 冻结分数不变 | explain/read |
| C11 | asyncio.gather 和 TaskGroup 有什么区别？举个小例子说明。 | unknown / 未通过 | timeout / 未通过 | unknown / invalid_response / 未通过 | 冻结分数不变 | 无 |
| C12 | Walk me through how a request reaches the database in this project. | explain / 通过 | skipped / 通过 | explain / skipped / 通过 | 冻结分数不变 | explain/read |
| C13 | 在项目里找出所有调用 refresh_token 的地方。 | search / 通过 | skipped / 通过 | search / skipped / 通过 | 冻结分数不变 | search/read |
| C14 | 帮我查一下 FastAPI 官方文档里关于流式响应的说明。 | unknown / 未通过 | timeout / 未通过 | search / accepted / 通过 | 新增通过 | search/read |
| C15 | 配置文件放在哪个目录？帮我在仓库里找找。 | configure / 未通过 | skipped / 未通过 | configure / skipped / 未通过 | 冻结分数不变 | configure/update |
| C16 | Review the changes in auth.py and point out security issues. | review / 通过 | skipped / 通过 | review / skipped / 通过 | 冻结分数不变 | review/analyze |
| C17 | 帮我审查这个 PR，重点看异常处理有没有遗漏。 | debug / 未通过 | skipped / 未通过 | debug / skipped / 未通过 | 冻结分数不变 | debug/update |
| C18 | 给 parse_date 函数补单元测试，覆盖空字符串和非法日期。 | test / 通过 | skipped / 通过 | test / skipped / 通过 | 冻结分数不变 | test/create |
| C19 | 运行 tests/test_cache.py，告诉我哪些用例没通过。 | test / 通过 | skipped / 通过 | test / skipped / 通过 | 冻结分数不变 | test/execute |
| C20 | Add regression tests for the timeout bug we fixed in client.py. | test / 通过 | skipped / 通过 | test / skipped / 通过 | 冻结分数不变 | test/create |
| C21 | 更新 README.md，补上 Docker 部署步骤。 | code / 未通过 | skipped / 未通过 | code / skipped / 未通过 | 冻结分数不变 | code/update；configure/update |
| C22 | 给这个 API 写一份接入文档，说明请求参数和错误码。 | document / 通过 | skipped / 通过 | document / skipped / 通过 | 冻结分数不变 | document/create；explain/read |
| C23 | 把 pytest 的默认超时时间配置成 30 秒。 | unknown / 未通过 | timeout / 未通过 | unknown / invalid_response / 未通过 | 冻结分数不变 | 无 |
| C24 | Set up GitHub Actions to run lint on every pull request. | code / 通过 | skipped / 通过 | code / skipped / 通过 | 冻结分数不变 | code/update |
| C25 | Python 的字典为什么能保持插入顺序？ | unknown / 未通过 | timeout / 未通过 | unknown / invalid_response / 未通过 | 冻结分数不变 | 无 |
| C26 | 这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。 | unknown / 未通过 | timeout / 未通过 | review / accepted / 未通过 | 冻结分数不变 | review/compare |
| C27 | 你好呀，今天状态怎么样？ | unknown / 未通过 | timeout / 未通过 | chat / accepted / 通过 | 新增通过 | chat/read |
| C28 | 记住：以后给我举 Python 例子时，优先使用标准库。 | unknown / 未通过 | timeout / 未通过 | memory / accepted / 通过 | 新增通过 | memory/create |
| C29 | 我之前让你记住的代码风格偏好是什么？ | unknown / 未通过 | timeout / 未通过 | memory / accepted / 通过 | 新增通过 | memory/read |
| C30 | 清空当前对话，开始一个新会话。 | unknown / 未通过 | timeout / 未通过 | unknown / invalid_response / 未通过 | 冻结分数不变 | 无 |
| C31 | 别改 payment.py 的代码，也不要运行测试；只解释它里面 process_payment 为什么先校验金额。 | explain / 通过 | skipped / 通过 | explain / skipped / 通过 | 冻结分数不变 | 禁止 code/update；禁止 test/execute；explain/read |
| C32 | 刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。 | debug / 未通过 | skipped / 未通过 | debug / skipped / 未通过 | 冻结分数不变 | debug/update；explain/read |
| C33 | 暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。 | explain / 未通过 | timeout / 未通过 | search / accepted / 未通过 | 冻结分数不变 | 禁止 refactor/update；禁止 test/create；search/read；explain/read |
| C34 | 不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。 | unknown / 未通过 | skipped / 未通过 | unknown / skipped / 未通过 | 冻结分数不变 | 禁止 search/read；禁止 explain/read |
| C35 | 撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。 | code / 未通过 | skipped / 未通过 | code / skipped / 未通过 | 冻结分数不变 | test/create；review/analyze；code/delete |
| C36 | 工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。 | explain / 通过 | timeout / 通过 | explain / timeout / 通过 | 冻结分数不变 | explain/read |
| C37 | 同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。 | review / 通过 | timeout / 通过 | review / invalid_response / 通过 | 冻结分数不变 | review/analyze |
| C38 | 假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。 | code / 未通过 | skipped / 未通过 | code / skipped / 未通过 | 冻结分数不变 | code/delete；explain/read |
| C39 | 产品说明书里举了个例子：“搜索用户邮件并保存到本地”。帮我润色这句话，让操作范围更清楚。 | explain / 未通过 | skipped / 未通过 | explain / skipped / 未通过 | 冻结分数不变 | explain/read |
| C40 | 网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。 | question / 未通过 | timeout / 未通过 | question / invalid_response / 未通过 | 冻结分数不变 | question/read |
| C41 | 先修复 parser.py 在空输入时抛异常的问题，再为这个边界情况添加单元测试。 | debug / 通过 | skipped / 通过 | debug / skipped / 通过 | 冻结分数不变 | debug/update；test/create |
| C42 | 重构 cache.py 里重复的淘汰逻辑，保留现有行为；同时补充 README.md 中的缓存策略说明。 | refactor / 通过 | skipped / 通过 | refactor / skipped / 通过 | 冻结分数不变 | refactor/update；document/update |
| C43 | 搜索项目里所有调用 fetch_user 的地方，然后解释其中 request_handler.py 的调用链。 | search / 通过 | skipped / 通过 | search / skipped / 通过 | 冻结分数不变 | search/read；explain/read |
| C44 | 审查 auth.py 的登录校验逻辑，并把发现的问题整理成 SECURITY_REVIEW.md。 | document / 通过 | skipped / 通过 | document / skipped / 通过 | 冻结分数不变 | review/analyze；document/create |
| C45 | 在 settings.yaml 中把日志级别调为 DEBUG，然后运行 test_logging.py 验证日志输出。 | test / 未通过 | timeout / 未通过 | configure / accepted / 通过 | 新增通过 | configure/update；test/execute |
| C46 | 继续审查后一个文件有没有重复扣款的风险。 | review / 未通过 | skipped / 未通过 | review / skipped / 未通过 | 冻结分数不变 | review/analyze |
| C47 | 就按这个方案改，函数签名保持不变。 | code / 未通过 | skipped / 未通过 | code / skipped / 未通过 | 冻结分数不变 | code/update |
| C48 | 再加一个 None 输入的用例。 | test / 未通过 | skipped / 未通过 | test / skipped / 未通过 | 冻结分数不变 | test/create |
| C49 | 这个能处理一下吗？ | unknown / 通过 | timeout / 通过 | unknown / invalid_response / 通过 | 冻结分数不变 | 无 |
| C50 | 把那一行改对就行，其他内容不动。 | unknown / 未通过 | skipped / 未通过 | unknown / skipped / 未通过 | 冻结分数不变 | 无 |

注：C26 的 review/compare 是可接受的选型比较近义分类，但冻结标签未覆盖它；C40/C47 延续上一轮的近义口径，均未改动原始统计。C33 新增 search/read 且主类型改变，虽然冻结分数仍未通过，不能表述为语义结果完全不变。
