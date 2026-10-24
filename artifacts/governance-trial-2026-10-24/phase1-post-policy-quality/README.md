# Phase 1 请求时序 fixture 修订后的离线 quality 记录

这是已经完成的两次离线运行的原始记录归档，没有为封包重跑检查。执行源码为 P1 HEAD `715dd1226ef6994990283994ab8db9293185b176` 加未提交的请求时序 fixture，测试文件 SHA-256 `f73325d0f18305d10cee66b636f7aa118c39020fc19589c2a1610065864852ce`。`source-before.json` / `source-after.json` 保存 `minicode`、`scripts` 和该测试共 **439 个文件**的 SHA，执行前后相同；该测试修订没有改生产代码。

| profile | 结果 | remoteCallCount | 外层耗时 |
| --- | --- | --- | --- |
| current | 88/88，gate passed，exit 0 | 0 | 14.28 秒 |
| a | 57/57，gate passed，exit 0 | 0 | 15.06 秒 |

`current.json` / `a.json` 就是原始 stdout，未另造运行 log；两个 `.stderr` 是真实空 stderr。`*-meta.json` 保留精确 argv、开始/结束 Unix 时间、未舍入秒数、退出码、结果 SHA 及 HOME 隔离说明。`run_phase1_post_policy_quality.py` 为实际使用的 wrapper，其控制台仅打印 meta，没有额外独立日志。未收录 `*-home`、env、缓存或 provider 记录。

生产 `minicode/**/*.py` 共 **319 个文件**，内容集合 SHA-256 为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`，包含已提交的 PTY 修复。计算方法为按路径排序，逐项拼接 UTF-8 仓库相对 POSIX 路径、一个 NUL、原始文件字节，然后计算 SHA-256；它不是文件 SHA 映射的 JSON 摘要。`packaging-verification.json` 是封包时的追加核验记录，确认当前源文件逐项匹配两份运行快照并复算该摘要，不冒充第三次执行。之前 `fff5e4db…` 是 PTY 修复前的历史源码身份，不能用作本次运行身份。

**这些是离线开发集结果，不是新模型任务成绩，不代替最新版完整 CI，也不单独满足 Phase 1 go。** 两个 profile 的检查范围有重叠，不按 145 个独立案例宣传。原始 wrapper 清除 `MINI_CODE_*`、指定 API key 和 pytest 配置变量，使用独立 HOME；实际输出均为 `offline-deterministic` / `remoteCallCount=0`。

## 完整性与恢复

共 10 个成员，原始 137622 字节，gzip 49006 字节。排序 USTAR、mode0644、uid/gid/mtime0、空 owner；gzip mtime0、空 filename。重复打包字节相同，实际归档已安全恢复并逐成员对照原件。`manifest.json` 固定压缩包及每成员 SHA。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/phase1-post-policy-quality')
m = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / m['archive']
assert archive.stat().st_size == m['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == m['archiveSha256']
expected = {entry['path']: entry for entry in m['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-quality-'))
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
assert seen == set(expected) and len(seen) == m['entryCount']
print(restored)
```
