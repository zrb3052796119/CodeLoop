# PTY 正常退出输出丢失：修复与定向证明

旧 [CI run `40114448888`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40114448888) 的 macOS Python 3.11 集成测试收到成功但空白的 `run_command` 输出。本包保存该节点的原始 XML 摘录、原源码身份、真实 PTY 控制实验、最终补丁和测试、原红新绿日志及独立审查。**这是一项生产修复；最新候选的完整 CI 验收仍待定。**

两个独立因果得到验证：本机 Darwin 在最后一个 slave 关闭时可丢弃未读数据；保留 slave 后，旧 `select` 返回空、子进程写出并退出、`poll` 随即结束循环的交错仍会跳过已排队输出。真实 `echo`、PTY、agent 和 Mock adapter 重现了空白回答，未伪造工具结果；原 CI 的实际交错没有被记录，不能据此认定它发生了哪一种交错。

修复保留父进程 slave 至排空结束，每轮检查直接子进程退出，先清理其进程组，再以零超时 readiness 查询读取已有数据。取消、绝对 deadline、输出 head/tail 上限、EOF/EIO 处理和异常清理均保留。这里的非阻塞指 readiness 查询，master 描述符仍由唯一 reader 读取；既有进程组清理有独立的有界 grace/reap，不承诺硬墙钟时限。生产 AST 仅 `_run_with_pty` 改变，pipe fallback 未纳入本次修复。

- 最终相同测试字节：旧源码 **4 failed / 2.86 秒**，修复后 **4 passed / 1.48 秒**；HOME 隔离后核验实际导入路径与 SHA。
- 生命周期文件、原失败集成节点和截断测试共 **19 passed / 3.40 秒**，包括上述 4 项，不能相加。
- 真实最终空 readiness 边界仍拒绝取消/截止；人工删除最后 checkpoint 后两项探针均失败。这是故障敏感性证明，不是原 CI 时序记录。
- 独立真实 PTY 大输出 smoke 写出 500,019 字节并验证 head/tail 和保留上限；独立审查 GO，无阻塞。它不代表额外完整测试通过。
- CI 等价 scoped Ruff、测试文件严格 E/F 与 diff-check 通过。未运行新完整套件、quality 或修复后的原生 CI。

精确生产 SHA-256：`750fddb1f2011bb617f2b779626853a4348dff8cafa3cdf513fd84125f2b0562`；测试 SHA-256：`a428918666d9133611cf874cea2297d9bc0d8a3f53fba50ca38740348c5a1e5d`。原 `3cadcb75` 生产 SHA 为 `1468b1979be8df4556ac63231f122f5ca0075d92fb563a6669b5f6ae74245b91`。ROOT 保持 Phase 2 冻结源码，此后发现的缺陷不倒改正在运行的 32-cell 实验、不触发重跑。此前平台范围批准不受此包影响。

首次探针不完整的正对照假设、独立 smoke 的错误 marker 期望均保留并标明。两次被 HOME 隔离或源身份断言拒绝的重放日志保留在本机，`report.json` 说明其无效原因，不计为红绿证据。

## 恢复与完整性

明确白名单共 **44 个文件 / 125,678 字节**，归档 **28,986 字节**，SHA-256 `4e9ec3796e6d087576a0fbcb15098ee40c53fb8f29bc16caf76f1b13aa298be8`。仅压缩一次；实际恢复全部成员并逐字节匹配来源。`manifest.json` 记录每项大小与 SHA。未包含 HOME、缓存、密钥、完整源码树或未批准日志；包内绝对路径只是当次记录，恢复不自动执行脚本。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/pty-output-followup-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {entry['path']: entry for entry in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-pty-'))
seen = set()
with tarfile.open(archive, 'r:gz') as tar:
    for member in tar:
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
assert seen == set(expected)
print(restored)
```
