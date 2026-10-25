# CI 取消与最终观测 fixture 修复证明

本包合并同一旧 CI 的两项测试前提修复。只改 `tests/test_agent_cancellation.py` 和 `tests/test_workflow_v2_structured_routing.py`，生产、脚本与配置未改；ROOT 与运行中的 Phase 2 r3 冻结输入未动。后续两项 fixture 已修并通过定向验证，**包含它们的下一候选完整 CI 尚待启动，Phase 1 go 未成立**。旧候选 `1decc813` 的 CI `40124691703` 另行运行/归档，不包含这两项未提交修订，不倒填其结果。

## 原始 CI

| 身份 | 原始结果 |
| --- | --- |
| run `40121258480`，job `120953074058`，macOS / Python 3.12 | 14,960 passed / 2 failed / 0 errors / 8 skipped，14,970 tests |
| PR head `715dd1226ef6994990283994ab8db9293185b176` | 实际 checkout merge `d793e1e673f16cd7d2e6ab0c632a5b3e273ca290` |
| concurrent cancellation 用例 | parent join 后 `finished` 仅有 read_file，原断言假定两个已运行回调均完成 |
| hung final observation 用例 | 整个 workflow 的 `0.516591542 < 0.5` 断言失败，后面的 audit 断言未执行 |

`ci-40121258480/` 只保存一份完整 raw job log、JUnit XML、身份/结果 summary 和定点提取，两个修复共用。两个目标测试在 `715dd122` 与 `1decc813` 原字节相同；保留旧失败，不声称掌握 CI 当时的完整线程调度或额外墙钟时间来源。

## 取消与回调完成的顺序

生产在取消时使用 nonblocking executor shutdown，parent turn 返回不意味着已运行工具回调全部结束。旧原字节在受控真实线程顺序中精确复现缺少一个名字；额外事实记录显示 parent 已退出并抛 `TurnCancellationRequested`、model.calls=1，而 list_files 仍停在 append 前。受控顺序证明原前提不成立，不冒充原 CI 的精确交错。

最终 fixture 在两工具都进入真实 barrier 后发出取消；工具持续阻塞，直到测试确认 parent 已退出、异常身份正确、模型只调用一次、两个工具均未标记完成。finally 再释放并独立等待工具 body 完成。原有两个名字和单次模型调用断言保留。最终测试 SHA `1eac1087a5f188cdb656be1bed8263fe50d7b8ae05738df602911f53ba316d5e`：

- 全文件 **37 passed / 1.18 秒**；恢复后的单节点通过包含在该范围内，不相加。
- 同一最终字节拒绝三个合成故障：强制 blocking shutdown、吞取消请求、实际多执行一次 ScriptedModel.next。
- 中间 fixture 曾给工具自动释放和 parent join 使用相同 5 秒超时，可能掩盖错误等待；其源码和运行记录保留，但不算最终证明。最终工具释放只由 finally 持有；5 秒 join/wait 是测试死锁 watchdog。
- `before-final-facts-meta.json` 的 sourceBefore[test] 记录的是当时工作树中的最终文件；**实际 argv 执行保留的 before-test.py**。旧文件字节/原 SHA、argv 和 facts 一起构成旧 fixture 证明，不能把 final SHA 误称为被执行旧文件。

## 最终观测的端口预算

生产的 20ms 测试配置约束一个 observation port，不保证之前的 filesystem、scheduler、audit 工作合计小于 500ms。局部外层时钟偏移使旧测试失败，同时真实 port 约 20ms 产生正确 typed deadline；原 native 单节点本身通过。这个反例证明断言范围错误，**没有证明历史生产超时缺陷**。

最终 fixture 仍走真实 workflow、worker 和 port runner，仅使用已有 runner 的局部 clock 注入及真实 Event 握手。按 0→10→20ms 检查 typed deadline，要求 worker 未释放/返回时 parent 已返回；检查 audit 持久化、hung worker 仍占容量、finally 释放并 join、late result 不改 output/audit、audit replay 与容量恢复。最终 SHA `d53981e126aaffebc31da5a02b76e90bdd1e218ac8713dd91d6eb104cbf8d5c7`：

- 邻接 **18 passed / 1.07 秒**，包含目标；native/受控单节点重复不再叠加。
- 同字节拒绝三个合成故障：翻倍 port budget、过早释放容量、跳过 audit persistence。容量故障随后产生的 semaphore over-release warning 也保留。
- 原 outer-clock 插件曾复用同一 receipt 文件名，旧 receipt 被后一次执行覆盖。保留两次原始 log；`outer-clock-proof.json` 只属于后一次 final-outer-duration，不能用于证明先前执行。primary 证据使用 `original-final-outer-counterexample-*` 与 `final-labeled-outer-duration-*` 各自独立的 log/meta/receipt。

两个修复各有非作者只读审查，没有把作者的执行算成审阅者重跑。六项 synthetic faults 是断言敏感性证明，不计六个历史产品 bug。初始 Ruff cache 权限失败及 cancellation 较宽检查遇到的既存 BLE001 原始红记录保留；最终无缓存、CI 对应的 E/F（排除 E501）检查通过，不泛称全部规则通过。未为合包重跑测试、quality、API 或 CI。

## 源码与既有 quality

`identity/post-two-fixtures-quality-identity.json` 是 root 的只读来源对比：既有 quality `source-before.json`（SHA `40e75eb600c6779aafa5b79e1f5364527d7404e41ca97c03c9e69664fc1a139e`）中 **439 个输入文件仍逐项相同**；本轮两测试不在该输入集合内。生产 319 个 Python 文件内容摘要仍为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`。因此引用[原 quality 证据](../phase1-post-policy-quality/README.md)的 current 88/88、a 57/57、各 0 API，**没有新执行，也不推断两个新 fixture 或完整 CI 已通过**。

仅包含必要测试快照/差分、控制顺序与合成故障插件、报告、身份和原始日志；不含 HOME、env、缓存、数据库、完整源码树或模型/provider 记录。`packaging/source-map.json` 固定白名单来源及分类。observation 原文件可由 Git `715dd122` 与差分重建，最终文件全文保留；审阅者已核对差分、原/新字节及 43 个作者白名单成员。

## 完整性与恢复

95 个成员，原始 3181569 字节，gzip 556969 字节。归档 SHA-256 `1417b39932a7d1838a8fd4d81b4a19d443cf321e611011cf42ae972d822527af`。排序 USTAR、mode0644、uid/gid/mtime0、空 owner；gzip 空 filename、mtime0、level1。重复打包字节相同，已从实际归档安全恢复全部成员并逐字节对照原件；逐成员 SHA 见 manifest。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/ci-cancellation-observation-proofs')
m = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / m['archive']
assert archive.stat().st_size == m['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == m['archiveSha256']
expected = {entry['path']: entry for entry in m['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-fixture-proof-'))
seen = set()
with tarfile.open(archive, 'r:gz') as tar:
    for member in tar:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name in expected and member.name not in seen
        data = tar.extractfile(member).read()
        assert len(data) == expected[member.name]['bytes']
        assert sha256(data).hexdigest() == expected[member.name]['sha256']
        target = restored.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        assert target.read_bytes() == data
        seen.add(member.name)
assert seen == set(expected) and len(seen) == m['entryCount']
print(restored)
```
