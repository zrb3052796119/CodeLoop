# Failed-write continuation 并发顺序 fixture 证明

本包只涉及 `tests/test_failed_write_candidate_continuation.py`：正式检查可并发完成，旧 fixture 把 observer 追加顺序当成请求顺序。生产、脚本及 ROOT/r3 冻结输入未改。旧 `1decc813` 的 CI40124691703 已结束，四个正式 runtime job 中3个通过；macOS3.11有下述唯一新失败。历史候选 `fd485a56` 的 CI40127563208 仍在运行且不含本次修订；下一候选完整CI待启动，Phase1 go仍pending。

## 原始失败与修复

run40124691703，job120963503461，PR head `1decc8134528aa9795f32deab58493a288153c02`，实际checkout `a1066ea1c6efbacd71290cf1ab771b03cd8e7843`，CI seed48。macOS/Python3.11完整XML为 **14962 passed / 1 failed / 0 errors / 8 skipped**。原完整raw、XML及summary在 `original-ci/` 各存一份，既有公开manifest中未发现同字节raw。fixture在第二次fake Executor调用内硬判formal退出码`[0,1]`，实际`[1,0]`被loop捕获成模型错误，最终execute_phase_failed。

受控plugin只用真实Barrier/Event让同一assignment/digest的regression先完成真实super.run及observer追加；不替换命令结果。原Git1de fixture同症状失败。历史CI具体交错未知，此处是匹配反序的确定性反例，不冒充精确重放。

最终fixture以assignmentId/patchDigest分轮，对checkId及**实际Verifier请求中的argv**绑定结果，拒绝重复、缺失和错配；没有排序裸退出码，也不声称返回的check摘要自带argv。初轮精确compat=0/regression=1（green参数则0），最终轮精确两者=0。原nonformal串行检查、同Executor/model/session、预算与计账、父目录保护、独立review、最终源码和audit/commit-ready断言均保留。

| 局部执行（seed20261024） | 结果 |
| --- | --- |
| 原fixture + 受控反序，仅failed参数 | 1 failed / 2.05秒 |
| 最终fixture + 同一排序plugin，两个参数 | 2 passed / 10.54秒 |
| 最终fixture + 自然调度、只读观测记录，两个参数 | 2 passed / 10.52秒 |

最后两行是**相同2用例的重复执行，不能相加成4个测试**。三次运行生产SHA相同并逐次前后不变；动态import来自P1。旧/新调用选择器范围不同，未声称pytest argv整体相同。最终两情形均committed且6次fixture模型请求；无真实API。精确最终测试SHA `bb1d2c2ea53786ab1c302e1a7de99e212f301d5ce1addb0b200c4e7a4085c558`。

从成功运行的真实观测记录出发，直接执行最终helper和循环外断言原AST，7种人工记录错配均被拒绝：重复/缺失/未知check、错argv、拆分assignment/digest、退出码错配。这是局部断言敏感性，非端到端故障、非7个历史产品bug或新增pytest数量。scoped Ruff与diff-check通过；`static-checks.json`注明其来自工具输出记录，没有伪造独立process原log。root已复核patch、原日志seed、report、meta、allowlist及敏感性脚本，无阻塞；原report中的“待root最终复审”为该报告写入时状态，原件保留。

## 来源与局限

`identity/failed-write-quality-identity.json`记录root本次只读复核：既有quality的439输入字节全同，当前改动测试不在其中。生产319文件摘要仍为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`，继续引用[既有quality](../phase1-post-policy-quality/README.md)的current88/88、a57/57、各0API；没有重跑quality，不由来源相同推断完整CI通过。

仅纳入已审29成员，另加root授权的1份quality身份证明。原29项allowlist未改，SHA `203ef4cfca2536e8b24b99e72e04ff3b28d1172256de9fc4e467eda21cc67eb3`。不含HOME、cache、临时工作区、凭证、个人笔记、模型/provider原文或r3记录。原CIraw/XML不是局部目标测试结果；原失败不被新绿覆盖。

## 完整性与恢复

**30成员**，原始2766291字节，gzip479873字节；archive SHA-256 `c05e84362e3910ef4b4a4cb512ed04dec322cab21d9895d164c39325e11bb326`。排序USTAR，mode0644、uid/gid/mtime0、空owners；gzip空filename、mtime0、level1。重复构建字节相同；已实际安全恢复每个成员并逐字节对照源文件，逐项SHA见manifest。

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile, tempfile
bundle = Path('artifacts/governance-trial-2026-10-24/failed-write-order-proofs')
m = json.loads((bundle / 'manifest.json').read_text())
a = bundle / m['archive']
assert a.stat().st_size == m['archiveBytes']
assert sha256(a.read_bytes()).hexdigest() == m['archiveSha256']
expected = {e['path']: e for e in m['entries']}
out = Path(tempfile.mkdtemp(prefix='failed-write-proof-'))
seen = set()
with tarfile.open(a, 'r:gz') as archive:
    for member in archive:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name in expected and member.name not in seen
        data = archive.extractfile(member).read()
        assert len(data) == expected[member.name]['bytes']
        assert sha256(data).hexdigest() == expected[member.name]['sha256']
        target = out.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        assert target.read_bytes() == data
        seen.add(member.name)
assert seen == set(expected) and len(seen) == m['entryCount']
print(out)
```
