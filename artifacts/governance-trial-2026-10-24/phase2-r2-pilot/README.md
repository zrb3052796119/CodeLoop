# Phase 2 r2：停止的规格试跑

**原计划 32 次：5 次完成、1 次中断未判分、26 次未运行。不能据此比较架构收益。** [一页处置报告](../../../docs/real-task-comparison-r2-pilot-2026-10-25.md)说明公开契约缺失、原始结果和后续新注册方向。原 r2 manifest SHA 为 `32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332`。

5 条完成记录与 `results.json` 在停止前后逐字节相同。活动 cell 没有被 finalize，没有补 trial row，没有改判 HardTimeout。停止由 root 依据既有方案授权作出，用户随后明确确认新轮方向；停止 receipt 的 `humanDecisionObserved=null` 与后续确认文件分别保留，不倒填时序。旧原件和失败没有删除或重跑。

完成部分为 116 次物理请求、4,046,418 token；中断部分另有 14 次已开始请求，其中 13 次可得 provider 用量，已知 token 下界 268,879。整个试跑至少 130 次物理请求、4,315,297 token，最终 token 总额和实际费用未知。原 budget 内美元估计不是账单，人工批准/纠正需求仍未知。

## 内容与来源

`evidence.tar.gz` 保存 142 个成员，原大小 4,853,339 字节，压缩后 2,687,216 字节；SHA-256 `24582a9f46e7042c93c4e3bec5e225dfff6c4eccc8bc258fc52f7addf84b0b90`。逐项大小、SHA 及完整性说明见 `manifest.json`。所有成员已实际恢复并与来源逐字节比对。

[独立封包复核](r2-pilot-independent-review.md)另确认 134 项原字节来源、五条原记录与停止前哈希、全部成员恢复及计数边界。该审阅者独立于本次封包，但曾参与报告器和停止工具，不冒称为独立实验设计审计。

- `trials/`、`run/results.json`、`records/`：原始完成记录、响应、执行/判分 receipt、输出以及实际修改文件。包含生成回归的结果，未重新执行 oracle。
- `interrupted-delivery/`、`stop/intervention-01/`：未完成交付、删除文件清单、准确进程清理和已知部分用量；不构成第 6 条得分。
- `run/`：冻结身份、原 runtime ZIP、设计与实际 wrapper 退出记录。大规模任务源码以原预注册 Git 清单重建，不重复加入本包。
- `report/`、`report-software/`、`spec-audit/`：5/32 的原始描述汇总、版本固定的报告器、事后静态规格诊断和解释限制。生成汇总的 success/缺失值未被处置报告修改。

各 `transport-usage-projection.json` 是按明确字段清单导出的用量记录，不冒充原始 provider 正文。原 transport、transcript 和 SQLite 仍完整留在本机，其 SHA/大小见 `private-originals-retained.json`；未将 provider 响应/推理正文、私有 HOME 或凭证库纳入公开包。`source-index.json` 区分原字节与派生材料。选定文本的有限凭证模式扫描没有命中，不宣称能证明任意秘密不存在。

包内旧 `stop/README.md` 的“尚未执行”和早期准备材料描述的是准备当时状态。实际执行以 `stop/intervention-01/`、实际 wrapper metadata 和随后用户确认记录为准；保留旧文件是为了不改写过程证据。恢复材料不会自动运行任何代码，不能再次执行其中的旧进程终止命令。

## 只读校验与安全恢复

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
bundle = Path('artifacts/governance-trial-2026-10-24/phase2-r2-pilot')
manifest = json.loads((bundle / 'manifest.json').read_text())
archive = bundle / manifest['archive']
assert archive.stat().st_size == manifest['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == manifest['archiveSha256']
expected = {item['path']: item for item in manifest['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-r2-pilot-'))
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
        seen.add(member.name)
assert seen == set(expected)
print(restored)
```
