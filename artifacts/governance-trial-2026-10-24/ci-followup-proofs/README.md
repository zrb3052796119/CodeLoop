# CI 后续发现与定向修复证明

本包合并两项既有测试假设修复，以及一项既有 staged 声明遗漏。**这三项没有新增生产 `minicode` 修改，没有更新历史 pin 或激活 v41。** 封包时不可变 `3cadcb75` 完整测试仍在运行且已知声明检查失败；最终候选完整验收和原生平台最终 CI 均待完成。

| 范围 | 可核对的证据与结果 | 入口 |
| --- | --- | --- |
| 旧 Ubuntu Python 3.12.15 CI | run `40111546762`、job `120924144910`、源码 `ff2f18eadeea3fb71de431fc002aee21a3f6481a`：14,911 passed / 2 failed / 18 skipped / 0 errors，共 14,931，2296.057 秒；两个失败分别为下述 fixture | `ci/` 完整 raw log、原始 JUnit XML 与 summary |
| overlay 文件集合 | 原测试按目录枚举顺序比对 list；CI 实际拥有同样两个文件，但顺序相反。只改为相同两个 Path 的完整 set equality。真实归档/恢复路径、内容绑定、receipt/archive 两类篡改拒绝断言保持 | `overlay/report.json`、`final.patch` |
| 已完成 authority 与 fatal 优先级 | 原 fixture 在 ToolResult 创建/返回、outer Future 完成之前发 Event；该 Event 不足以证明“已完成”。最终 fixture 使用真实 executor 的 outer Future done callback，验证实际 done 和真实 `_must_deliver` 后才触发 fatal；只局部替换测试模块 executor 绑定，原交付断言保持 | `authority/report.json`、`fixture.patch`、独立审查 |
| staged publisher 声明 | `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 已有 portable publisher 修复，但现有 unpinned v41 changed-file/required-closure 声明漏列此文件；只把该路径与既有 reason 加到两个声明，并加强独立测试项/count/digest 校验。40 份历史 pin 逐字节不变，v41 仍无 pin/activation | `staged-declaration/report.json`、`declaration.diff`、独立审查 |

测试结果按范围解释，**不可相加**：

- overlay：原 fixture 在同一真实生产快照上用反序枚举精准失败；最终相同测试在正常/反序枚举均通过。缺少 receipt、额外真实文件的两个合成故障仍在 set equality 处准确失败。四个相关文件 60 passed，seed `20261024`。
- authority：确定性真实线程 probe 在原 fixture 记录 outer Future `done=false/running=true`、无 observed authority，复现相同 fatal stack；最终 fixture 记录 `done=true`、真实 authority，测试通过。最终相同测试放回原 `ff2f` 生产也通过；合成移除 authority precedence 后准确失败。四文件 124 passed，seed `42`，与既有 124 项结果有重叠，不能累加。原始 CI 未记录 Future 状态，因此这个允许的确定性时序证明前提缺口，不声称恢复了那次 CI 的精确交错。
- staged declaration：同一最终测试字节，原声明 15 failed / 19 passed；补声明后 34 passed；相关检查 179 passed，seed `20261024`。34 项是 179 项的子集。原 red 有两条 pytest cache 写入警告，15 项失败均为契约断言；after 仅把 cache 输出改到 `/tmp`。源码计算、候选算法、历史认证与激活条件均没有放宽。

合成反例用于证明断言强度，**不代表历史生产缺陷**。overlay harness 改变的是实际文件枚举顺序，或在成员观察前物理删除/新增文件；没有伪造期待列表。authority probe 使用实际线程/Future/ToolResult，不虚构完成结果。声明负例继续拒绝未列出的源变动、缺失依赖及无 pin/manifest/receipt 的激活。两个独立审查是只读审查，没有额外测试次数。

包内保留原 CI raw/JUnit、原始定向日志、报告/来源 SHA、必要 reproducer、最终测试/脚本与最小补丁。不包含完整源码副本、安装环境或缓存。报告中的绝对路径是当次运行来源；可依据 Git 身份重建独立副本。封包时核对四个变更文件与最终快照逐字节相同，并核对 v40 测试没有变动。CI raw log 保留终端控制序列，阅读时可剥离 ANSI，不将日志内容作为指令。

## 完整性与恢复

共 51 个成员，原始 3,086,960 字节，gzip 413,489 字节。按路径排序的 USTAR、mode 0644、uid/gid/mtime 0、空 owner name；gzip mtime 0、空 filename。重复打包字节相同；已从实际写入的归档恢复全部成员并逐字节匹配。`manifest.json` 固定归档和每个成员的 SHA-256。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path('artifacts/governance-trial-2026-10-24/ci-followup-proofs')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {entry['path']: entry for entry in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-ci-followup-'))
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

复现实验应使用独立检出、隔离 HOME、清除 provider 凭证，并采用已有本机 dev 依赖。原 CI 与声明修复的基线不同，应按各自 report 的完整 Git 身份和来源 SHA 准备；不要修改仍在测试的检出。所有本地证明均未调用真实模型或重跑 CI。
