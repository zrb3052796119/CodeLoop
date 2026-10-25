# macOS CI 失败与后续定向证明

本包保存原 CI run `40111546762` 的 macOS Python 3.12.10 完整负证据，以及其后定向调查与修复。基线为 `ff2f18eadeea3fb71de431fc002aee21a3f6481a`，job `120924144874`：**14,915 passed / 8 failed / 8 skipped / 0 errors**，JUnit 14,931 项、3616.111 秒。raw log 与原始 XML 仅保存一份。**本包不是最终候选完整套件或修复后原生 CI 的通过证明。**

| 原失败/调查范围 | 修复与证据界限 | 包内入口 |
| --- | --- | --- |
| 三项 hot-store 时序断言 | 原 CI trace 保留；已在此前修复中用实际 worker 和受控累计预算替换宽泛 wall-clock 断言。这里不重复归档此前证明 | `ci/`；另见[已有 hot-store 证明](../hot-store-budget-proofs/README.md) |
| adapter 绝对截止与并发验证时序 | 仅修 fixture。真实 reader、剩余 socket timeout、真实 Future 完成、并发 barrier、请求顺序与 evidence 对应均保留；局部时钟/模块绑定不改共享标准库 | `timing/report.json`、最终补丁、独立审查 |
| guard-import / public-preflight 解释器前提 | 显式注册装有 pytest 的宿主解释器，并核对实际 child 路径/版本。真实 Seatbelt、scope、报告与拒绝断言保留。guard 原 CI 明确记录 Apple shim/Xcode 拒绝后 fallback 缺 pytest；preflight 原截断错误本身不能证明相同内因 | `guard-import/`、`public-preflight/` |
| 独立工具链目录的 metadata 可达性 | 真实独立验证目录暴露生产缺口：已信任 prefix 内容可读，祖先却不能 stat。只为这些明确受信任 prefix 增加祖先目录 literal 的 metadata 规则，未授权祖先子树内容、写入或网络。此控制实验不是原 CI 精确原因的证明 | `public-preflight/source.patch`、before/after、receipts、独立审查 |
| evaluation transport 队列消费滞后 | 真实 reader/Queue/SQLite 控制实验确认：消费侧记录开销可在检查已排队及时字节前误触 idle。既有脚本按 reader receive 时间验证间隔；真实过长 receive gap、绝对截止、取消、迟到 EOF/异常仍拒绝，usage 失败时保留 | `transport/report.json`、原 fixture lag 反例、最终同字节边界与独立审查 |

对应源提交为 `54367b5d6dd860f285d543f34b4248463959a482`、`1f39822bef33eb244d853bf46627a7bb308be542`、`693f504aab0befd42cadba58cbeb72f4703058c2`；精确八个最终文件 SHA 见 manifest。封包时与当前源码核对，没有修改生产、脚本或测试。

验证范围不能累加：

- timing 最终两文件 77 passed（3.25 秒）；相同最终测试放回原 `ff2f18ea` 生产，三个目标通过（1.64 秒）。六个合成故障准确失败，覆盖错误 deadline 类型、timeout 不递减、串行验证和结果/evidence 逆序。旧 fixture 两个控制反例表明时序假设过强，不声称恢复原 CI 调度。
- guard 最终文件 6 passed（2.45 秒）；真实无 pytest 的 copied venv 仍无报告，注册宿主可正确报告 TypeError 并满足全部沙箱拒绝断言。本机旧 fixture 原本也通过，因此没有将本机绿包装为原 CI 红的复现。
- sandbox 最终同一真实集成测试在旧代码为 1 failed / 3 passed，修改后 sandbox + preflight + guard 合计 17 passed；更广 verifier/public/v40/v41 集合 108 passed。这些范围有重叠。真正放开祖先子树内容的合成故障准确失败；首个仅改变目录 literal 读操作的变异仍通过，因为它未授予子文件内容，这个无效反例也原样保留。
- transport 原 fixture 的真实消费滞后控制：旧脚本失败，修改后通过。最终同字节十边界旧脚本 3 failed / 7 passed，修改后 10 passed；三类故障均失败。87 项相关测试包含 31 transport 与十边界，不相加。首次受外层 sandbox 限制的运行 80 passed / 7 failed（进程表扫描 PermissionError）及拒绝 cleanup receipt 保留；同字节宿主运行 87 passed（16.02 秒）。未调用网络模型。
- 既有离线 quality profile：`current` 88/88、`a` 57/57，均 `remoteCallCount=0`。元数据的 HEAD 是运行记录中的 `3c844a39`，实际生产工作树已含后续修复；绑定的生产 SHA/agent-loop SHA 与 `sourceUnchanged=true` 才描述这次来源。它们是既有 fixture/记录的重放，不是新模型能力或 Phase2 成绩。

原 CI 没有 reader/queue 精确时序；transport 控制实验只证明一个真实、足以导致错误的缺陷。观察时间是 reader read 返回时刻，不是内核包到达时刻；本证明未触发队列背压。transport 属于 Phase2 固定 harness，正式调用前须保留旧预注册记录并重新冻结变化后的依赖；本包不授权跳过该步骤。所有独立审查均为只读，没有额外测试次数。

## 完整性与恢复

共 139 个普通文件，原始 3,290,070 字节，gzip 487,510 字节。仅使用负责人冻结的明确白名单，包含负例、原始日志、XML、receipts、来源 SHA、必要小型 reproducer、最终测试/脚本快照及补丁；不包含 venv、HOME、完整源码树、工作区、密钥或无关重复 CI 日志。报告中的绝对路径是当次来源，恢复不执行日志或脚本。

按路径排序的 USTAR、mode 0644、uid/gid/mtime 0、空 owner；gzip mtime 0、空 filename。重复打包字节一致，实际写入后恢复所有成员并逐字节匹配。`manifest.json` 记录归档与每个成员的 SHA-256；`provenance/` 保留白名单和打包器。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/macos-ci-followup-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {entry['path']: entry for entry in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-macos-ci-'))
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

复现使用独立检出、隔离 HOME、清除 provider 凭证及已有 dev 依赖；真实 macOS sandbox 与进程表检查需要对应宿主权限，不能把外层 sandbox 拒绝误判为被测生产失败。各报告明确区分实际历史缺陷、fixture 前提修复、控制反例和合成故障。
