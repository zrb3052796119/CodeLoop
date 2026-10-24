# Phase 0 诊断证据 — 2026-10-24

这是修复过程的诊断归档，**不是最终候选的完整测试通过证明**。打包时最终全量运行尚未开始；main 合入与 Phase 2 实付调用仍等待最终验收。

压缩包 [diagnostics.tar.gz](diagnostics.tar.gz) 保存 346 个顶层证据文件：原始日志、JUnit XML、R2 事件记录、关键红绿证明及初始/诊断源码哈希。共 18,480,500 字节，压缩后 3,175,381 字节。没有临时源码目录、Python 源文件、补丁、凭证文件或个人笔记正文。逐文件字节数、SHA-256 和压缩包 SHA-256 见 [manifest.json](manifest.json)。归档已完整解压到新临时目录，每个成员均与 manifest 及原始文件逐字节比对一致。

## 结果边界

| 记录 | 原始结果 | 解释 |
| --- | --- | --- |
| R1，`bd86d0198b00f32a6afd2b4d6aea1bdb1999547b` | 收集 2 errors / 4 skipped，36.99 秒 | 缺失忽略目录下的测试依赖；没有执行完整套件 |
| R2，`cb327c3796b0d34fb3235a15fec6f1988f072adf` | 5355 passed / 58 failed 后中断，1809.00 秒 | 逐用例诊断插件的文件写入被测试 monkeypatch 禁止；局部计数来自原始事件记录 |
| R2 未完成节点续跑 | 9271 passed / 139 failed / 8 skipped / 22 errors / 5413 deselected；pytest 2382.44 秒 | 同一冻结源码，仅执行先前未完成节点；外层进程 2385.16 秒。不能与前次拼成一轮完整套件 |
| current / a quality | 88/88、57/57 | 离线开发集。通过不能证明真实任务生产力，也不能替代最终完整测试 |
| 最终候选完整随机运行 | 待开始 | 不在此包中，不填写虚构的提交或结果 |

本包中的路径与命令反映运行时环境。有些 proof 指向本机源码副本；这些副本未重复入库，包内保留其哈希身份。恢复本包不依赖这些目录，但仅凭哈希不能重建未提交的历史树。完整执行状态见 [release baseline](../../../docs/release-baseline-2026-10-24.md) 与 [治理记录](../../../docs/governance-trial-2026-10-24.md)。

## 证据索引

恢复后全部文件位于新目录的 `evidence/` 下。以下是入口；完整文件清单以 manifest 为准。

| 范围 | 关键文件 |
| --- | --- |
| 初始与诊断身份 | `head.txt`、`baseline-manifest.json`、`full-source-manifest.json`、`full-r2-source-manifest.json`、`core-wip-source-manifest.json` |
| 两次全量尝试、诊断续跑 | `full-run-meta.json`、`full-pytest.log`、`full-results.xml`；`full-r2-run-meta.json`、`full-r2-pytest.log`、`full-r2-results.xml`、`full-r2-events.jsonl`；`remainder-r2-run-meta.json`、`remainder-r2-pytest.log`、`remainder-r2-results.xml`、`remainder-r2-failures.json` |
| session 路径污染 | `global-isolation-r2-repair.json`、`global-isolation-r2-pair.log`、`global-isolation-r2-pair-fixed.log` |
| 评测 task 包装污染 | `workflow-scope-pollution-repair.json`、`workflow-scope-r2-red.log`、`workflow-scope-r2-green.log`、`workflow-scope-counterexample.log`；`feedback-runner-pollution-r2-red.log`、`feedback-runner-pollution-current-green.log` |
| 模块缓存污染 | `http-module-pollution-proof.json`、`http-policy-feedback-repair.md`、`http-module-pollution-r2-red.log`、`http-module-pollution-full-http-green.log`、`http-module-pollution-r2-fixed-tests-green.log` |
| MCP 实际错误反例 | `mcp-proof.json`、`mcp-before.txt`、`mcp-after.txt` |
| 请求 schema 预算及失败诊断审计 | `phase-schema-runtime-repair.md`、`phase-schema-real-proof.json`、`phase-schema-real-proof-harness-error.json`、`phase-schema-final-critical.log`、`candidate-audit-error.txt`、`late-analyst-audit-after.log` |
| checkpoint 未执行证明 | `runtime-bootstrap-review-repair.md`、`bootstrap-duration-real-proof.json`、`runtime-bootstrap-review-final-seeded.log` |
| 历史 verifier-only 提交、长 trace 投影 | `legacy-projection-repair.json`、`legacy-projection-counterexample-manifest.json`、`legacy-projection-import-proof.jsonl`、`legacy-projection-exact-counterexample-guarded.log`、`edit-failure-legacy-current-final.log`、`legacy-policy6-mandatory-review-negative.log`、`repeated-repair-projection-green.log` |
| 其他 fixture 分类 | `runtime-triage.json`、`root-r2-second-fixture-repairs.json`、`root-late-fixture-repairs.json`、`continuation-cluster-repair.json`、`failed-write-fixture-repair.json`、`native-handoff-repair.json`、`handoff-todo-repair.json` |
| quality、功能审计、v41 | `quality-current-latest.json`、`quality-a-latest.json`、`functional-audit-real-proof.json`、`functional-audit-final.json`、`v41-regression-proof.json`、`phase0-memory-baseline-repair.md` |

### 如何读负证据

原始失败、初次收集错误、环境限制和无效反例尝试均保持原字节。`phase-schema-real-proof-harness-error.json` 明确是缺少测试辅助文件造成的无效基线，不算生产缺陷证明。早期 `pollution-diagnosis.md` 及部分阶段笔记称污染尚未证实，代表当时进度；后来的三项最小顺序反例才建立因果关系。不能把历史笔记中的暂定判断当作本次最终分类，也不能把局部分组绿结果拼成完整绿基线。

fixture 迁移和生产修复分别记录：前者通常在相同 R2 生产代码上使用最终 fixture 也通过；后者要求相同最终测试在真实旧生产代码上触发目标失败。部分日志里出现的 `api_key=sk-…` 是仓库脱敏测试已有的固定参数值，不是运行凭证。

## 校验并恢复

在仓库根目录运行以下标准库脚本；只创建新的临时目录，不覆盖仓库文件。它校验压缩包、成员清单、类型、路径、长度与 SHA-256，并在写入后再次比对字节。

```python
from pathlib import Path
import hashlib
import json
import tarfile
import tempfile

bundle = Path("artifacts/governance-trial-2026-10-24/phase0-diagnostics")
manifest = json.loads((bundle / "manifest.json").read_text())
archive = bundle / manifest["archive"]["path"]
sha256 = lambda data: hashlib.sha256(data).hexdigest()
assert archive.stat().st_size == manifest["archive"]["bytes"]
assert sha256(archive.read_bytes()) == manifest["archive"]["sha256"]
records = {record["path"]: record for record in manifest["files"]}
assert len(records) == manifest["memberCount"]
restored = Path(tempfile.mkdtemp(prefix="codeloop-phase0-evidence-"))
with tarfile.open(archive, "r:gz") as source:
    members = source.getmembers()
    assert len(members) == len(records)
    assert {member.name for member in members} == set(records)
    for member in members:
        parts = Path(member.name).parts
        assert member.isfile() and len(parts) == 2 and parts[0] == "evidence"
        assert parts[1] not in {".", ".."}
        data = source.extractfile(member).read()
        record = records[member.name]
        assert len(data) == record["bytes"]
        assert sha256(data) == record["sha256"]
        target = restored / member.name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        assert target.read_bytes() == data
print(f"Verified and restored {len(records)} files to {restored}")
```

保留此目录的三个文件即可恢复和验证证据。后续完整基线应保存为单独、明确标注源码身份的运行记录，不覆盖或替换本包的负结果。
