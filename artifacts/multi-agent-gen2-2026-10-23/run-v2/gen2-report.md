# Gen-2 工程六格结果

本代与所有历史数字不可比；六格只判断继续/停止，不判断统计优越性。

判定：通过工程门，可继续 Phase 2。

成功 5/6；single 3/3；explicit 2/3。

| 题目 | 组别 | 成功 | 请求 | 已知 token | Agent 秒 | 新增测试 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| gen2-read-context | single | True | 18 | 490097 | 469.0 | True |
| gen2-registry-lifetime | explicit | True | 14 | 257759 | 373.4 | True |
| gen2-summary-budget | single | True | 21 | 492045 | 394.9 | True |
| gen2-read-context | explicit | True | 9 | 167132 | 286.9 | True |
| gen2-summary-budget | explicit | False | 15 | 292345 | 321.7 | True |
| gen2-registry-lifetime | single | True | 23 | 485239 | 309.0 | True |

未满足条件：无

Executor 实际开始模型请求的试次：3；发出编辑工具请求的试次：3。

| 组别/角色 | 请求数 | 已知 token | 未知用量请求 | 请求耗时合计/秒 |
| --- | ---: | ---: | ---: | ---: |
| explicit:analyst | 0 | 0 | 0 | 0.0 |
| explicit:executor | 29 | 641506 | 0 | 763.8 |
| explicit:main | 9 | 75730 | 0 | 187.8 |
| explicit:plan | 0 | 0 | 0 | 0.0 |
| single:analyst | 0 | 0 | 0 | 0.0 |
| single:executor | 0 | 0 | 0 | 0.0 |
| single:main | 62 | 1467381 | 0 | 1114.6 |
| single:plan | 0 | 0 | 0 | 0.0 |

explicit 非 Executor 请求占比：23.7%。该比例为描述指标，不等同全部协议税。
请求耗时合计不能当作阶段墙钟时间；未知用量保留为未知，已知 token 只作下界。所有失败保留，未补抽或选优。
