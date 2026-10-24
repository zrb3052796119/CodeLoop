# 重复修复测试：按候选与命令关联结果

旧 [CI run `40115957909`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40115957909)（`3c844a39`，macOS Python 3.11）在 `test_repeated_candidate_repair.py` 期待 `[0, 2, 0, 1]`，实际观察到 `[0, 2, 1, 0]`。**本次仅修 fixture，生产仍与已推送 `8fba3787` 相同；最新候选完整 CI 验收仍待定。**

生产对独立检查并发执行，再按请求 index 聚合结果和 evidence；fixture 却在每个 verifier 返回时 append，误把完成顺序当请求顺序。真实线程 Barrier/Event 强制第二轮 regression 先于 compat 完成，原测试字节精确复现上述失败，同时真实工作流已 committed、模型调用数仍为 7。探针保留真实 subprocess/pytest/返回结果；它证明一种允许的交错，不恢复原 CI 未记录的线程时序。

修正按 `(assignmentId, patchDigest)` 确认候选轮次，并绑定 `checkId` 与实际请求 argv，拒绝重复或缺失 check。每轮仍精确要求 compat=0，regression 依次为 2→1→0；Analyst 按 regression 身份选择当前失败，不再选择最后完成项。没有排序裸退出码，原预算、同一 Executor/session、冻结候选、review、commit 和持久审计断言保留。

验证范围是同一个测试的多次执行，不能累加为不同用例：旧 fixture 在受控顺序下 **1 failed / 8.28 秒**；新 fixture 同一顺序 **1 passed / 8.90 秒**；无插件运行 **1 passed / 8.31 秒**；所有轮次均反向完成 **1 passed / 8.78 秒**。这是相同生产下的 fixture 修正，不是同一测试的旧/新生产红绿证明。直接执行最终 helper 与最终断言，真实观测结果可通过；六类人工关联错误（交换 check/exit、重复、错误 argv、跨候选、缺项、轮次错误）均被拒绝。这些是测试敏感性反例，不是六个生产缺陷。

独立只读审查 GO；scoped Ruff 和 diff-check 通过。没有重跑完整套件或 quality。当前 `8fba3787` CI 保留原身份，不能倒填本次新 fixture。ROOT 与 Phase 2 源码未改。最终测试 SHA-256：`206bffcdc88b678941e20f8e46c0762fa98a7584857e0967cb4c1b6ec7f51ba7`。

## 完整性与恢复

明确白名单共 **29 个普通文件 / 135,104 字节**，单次 gzip **23,858 字节**，SHA-256 `ca950f286b69055f3fd1a398ee9e9722c69bfa0412acd59e8f2003c7eab35270`。包含失败摘录、来源身份、原/新测试字节、局部时序插件、日志、补丁、断言反例和审查。未包含 HOME、缓存、完整源码树、密钥或无关 CI 日志。实际恢复全部成员并逐字节匹配；每项 SHA 和大小见 manifest。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
bundle = Path('artifacts/governance-trial-2026-10-24/repeated-repair-order-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {item['path']: item for item in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='repeated-repair-order-'))
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
assert seen == set(expected)
print(restored)
```

包内绝对路径只是当次来源记录；恢复不会自动执行代码。复现需要独立 HOME、项目 dev 依赖及真实子进程权限。
