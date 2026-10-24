# Continuous request 时序前提修复证明

本次仅修 `tests/test_continuous_request_policy.py` 的前提。生产 `minicode` 与脚本保持 `8fba3787` / `715dd122` 的字节；未改 ROOT 或 Phase 2 输入。两次旧 CI 的原始失败保留，**包含本次测试修订的最新版完整 CI 尚待完成，不宣称 Phase 1 go**。

| 原始来源 | 观察结果 |
| --- | --- |
| CI `40118490617`，macOS 3.12，PR head `6e7ba88f080134591087af154c14f3ec63be98b3`，实际 merge `4176b4d3ad121f031f84d913e674d9d9d073a650` | caller 用例在 reader receive-gap 检查抛 `EvaluationIdleTimeout` |
| CI `40120281015`，Ubuntu 3.12，PR head `8fba3787dca239ec16d6790174a1d824c8c0b74e`，实际 merge `616f10a82a84c8131fed873b5a4b20fe764090d2` | caller 异常类型符合预期，但 failed/unknownUsageCalls 为 0，原断言要求 1 |

`ci/` 包含两个 job 的完整 raw log、JUnit、summary 与逐项 failure extract；`proof/source-comparison.json` 核对两个 CI head、`715dd122` 的原 fixture/transport 相同。原 fixture 在 transport/SQLite 初始化前启动 60ms caller deadline；heartbeat 的 15ms wait 也不保证真实 reader 间隔小于 50ms idle 上限。受控 70ms setup 使请求在登记前正确被拒绝、计数为 0；受控 55ms 首字节间隔使 idle 正确先于 caller 触发。两个旧 fixture 用例分别精确失败，证明前提缺口；**原 CI 未保存精确调度轨迹，不声称恢复其实际交错**。

最终 fixture 用模块局部时钟和首次心跳已持久化的握手，保留真实 adapter、reader、queue、SQLite ledger、取消 token。caller 两个间隔分别 30ms/31ms，均低于未变的 50ms idle，累计 61ms 跨过未变的 60ms caller deadline；idle 独立检查 51ms 间隔，cancel 在 HTTP 返回前请求。精确异常类别、物理打开次数、started/failed/unknown 都为 1 及响应关闭继续验证；新增已过期 caller 必须不打开 HTTP、三项计数均为 0。没有放宽阈值或增加 sleep。

最终同一测试 SHA `f73325d0f18305d10cee66b636f7aa118c39020fc19589c2a1610065864852ce` 的结果：

- 邻接四文件 **113 passed / 4.91 秒**；其中包含本文件，结果不可相加。
- 四项临时合成故障分别丢弃 caller 期限、替换 idle 限额、吞取消请求、漏记 terminal failure；前三项在错误异常类别处红，最后一项在计数断言处红。它们证明断言敏感性，**不是历史生产 bug**。
- 移除故障后本文件 **15 passed / 0.91 秒**；Ruff 与 diff-check 通过。每个 final run 的 meta/imports 记录同一测试与生产 SHA、P1 导入路径、argv、seed `20261024` 及退出码。
- `independent-review.*` 为非作者只读审查；审阅了上述作者运行日志，没有把它们算作审阅者独立执行。

旧、新 fixture 的同一调度插件前后运行均保留；新 fixture 的时钟/reader 明确替代了插件所控制的旧输入，所以 after 绿本身不构成任意调度稳健性证明。最终四故障与真实邻接路径是补充断言强度的证据。`after-file/after-setup/after-gap` 是 import 排序前的中间验证，不冒充最终相同字节。初次邻接调用误写不存在的 `test_model_call_control.py`，0 测试收集失败；`invalid-missing-node-*` 保留原始失败，不计产品故障或通过。早期 cache 写入警告来自沙箱权限；final 运行已将 cache 指向 tmp。

包只含必要 fixture、最小补丁、控制调度/故障插件、来源与验证记录，不含 HOME、数据库、env、缓存、完整源码或模型/provider 记录。报告中绝对路径是当次来源，可按 Git 身份准备独立检出；不要在冻结目录运行重现。所有本地证明未调用真实模型或 oracle，未重跑 CI。

## 完整性与恢复

共 56 个成员，原始 5367333 字节，gzip 743235 字节。采用排序 USTAR、mode 0644、uid/gid/mtime 0、空 owner；gzip mtime 0、空 filename。重复打包字节相同，从实际归档安全恢复全部成员后逐字节核对原件。`manifest.json` 记录归档及每成员 SHA-256。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/continuous-request-timing-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {entry['path']: entry for entry in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-request-timing-'))
seen = set()
with tarfile.open(archive, 'r:gz') as tar:
    for member in tar.getmembers():
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name in expected and member.name not in seen
        data = tar.extractfile(member).read()
        entry = expected[member.name]
        assert len(data) == entry['bytes']
        assert sha256(data).hexdigest() == entry['sha256']
        target = restored.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        assert target.read_bytes() == data
        seen.add(member.name)
assert seen == set(expected) and len(seen) == manifest['entryCount']
print(restored)
```
