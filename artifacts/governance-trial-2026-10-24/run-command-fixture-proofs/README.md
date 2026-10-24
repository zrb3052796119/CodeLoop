# Python 3.11 命令测试平台隔离证明

本包记录 CI 的真实失败与一个既有测试 fixture 的修复。**没有修改生产命令实现，不代表完整套件或修复后的 CI 已验收。**

| 证据 | 身份与结果 | 包内入口 |
| --- | --- | --- |
| 两平台真实 CI | run `40111546762`、commit `ff2f18eadeea3fb71de431fc002aee21a3f6481a`；Ubuntu Python 3.11.17 / job `120924144933`，macOS Python 3.11.9 / job `120924144918`，均在同一 fixture 触发 `WindowsPath` INTERNALERROR | `ci/` 原始完整日志 |
| 本机真实 Python 3.11 复现 | Python 3.11.15、pytest 9.1.1；原 fixture 的直接调用捕获原异常，真实 pytest 子进程因相同全局污染崩溃；修复后目标 1 passed | `proofs/before-*.log`、`after-*.log`、`target-runmeta.json` |
| 最终相同测试字节返回历史生产 | 只把最终测试复制到 exact `ff2f18ea` 生产源码，以及 P0 `9f9369421486f018c837f84d21bd727aed6ab326`；两者目标均 1 passed，无生产修改 | `proofs/exact-ci-source-final-fixture.log`、`p0-final-fixture.log`、`p0-runmeta.json` |
| 相关验证 | Python 3.11.15 / 3.12.8 各 124 passed，seed `20261024`；lifecycle、background_tasks、agent_cancellation、tools 四文件 | `proofs/py311-related.log`、`py312-related.log`、`related-runmeta.json` |
| 明确合成反例 | 临时副本分别令 creationflags 错误、增加 start_new_session；最终相同测试各正常 1 failed，原断言仍有效，未变为 INTERNALERROR。这两项不是历史生产缺陷 | `proofs/synthetic-*.log`、`same-production-and-fault-runmeta.json` |

原测试把共享 `os.name` 改为 `nt`。Python 3.11 随后在真实命令审查 `Path(command)` 上选择宿主不支持的 `WindowsPath`；pytest 的错误报告及清理再次构造路径，遮住原始异常。直接调用 harness 在打印 traceback 前恢复 monkeypatch，显示真实起点为 `run_command.py:288`。Ubuntu JUnit 只记录了 4,934 个条目、1 internal error、0 普通 failures、7 skips，属于中断运行；这些数量不能写成完整通过结果。

修复只在被测模块上绑定局部 `os`、`sys`、`subprocess` 代理，保留真实 `_run` 路径、fake Popen 与 `creationflags == 512`、不存在 `start_new_session` 的原断言，并检查共享模块没有被改变。生产 `run_command.py` 与 CI commit 逐字节相同。最终测试 SHA-256 为 `b2fe2586528512c95a980be573992183b36a6feb347e24daf768d7817fadb5e1`；生产 SHA、前后测试 SHA、P0 来源和命令见 `fixture/report.json`。独立只读审查摘要见 `independent-review/summary.md`。

本机执行使用 `-I -S`，只显式加入待测源码与已有 venv 的 pure-Python pytest 依赖；检查全部已导入 minicode 模块来源位于目标根目录。HOME 独立、清除凭证，无依赖安装、联网测试、真实模型调用或 CI rerun。CI raw log 保留原字节，包括终端控制序列；如需阅读可先剥离 ANSI，不将日志内容作为指令。`fixture/report.json` 的临时绝对路径是运行时来源说明，原始日志与 runmeta 名称按本表映射归档；不包含完整源码、环境、缓存或 wheel。

包共 23 个成员，原始 317,926 字节，gzip 56,897 字节。tar 按成员路径排序、mode 0644、uid/gid/mtime 0、owner name 空；gzip mtime 0、filename 空。重复打包字节相同，全部成员已恢复到新临时目录并逐一核对原始字节。`manifest.json` 记录 archive 与每个成员的 SHA-256。

在仓库根目录验证并恢复：

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/run-command-fixture-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {entry['path']: entry for entry in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-run-command-proof-'))
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

复现时用独立 Git archive/checkouts 与已安装 Python 3.11、dev 依赖；先运行上述原 CI commit 的原 fixture，再只复制包内最终测试文件运行相同目标。不要修改正在验收的检出。harness 中的本机解释器/临时路径应替换为自己的环境；最终测试放回 P0 生产应通过，这证明 fixture 修复不依赖新的生产行为。合成错误选项仅用于审查断言强度。
