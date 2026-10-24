# P0 完整随机验收 — 9f936942

**该冻结提交的 P0 验收通过：14,923 passed / 8 skipped / 0 failed / 0 errors / 3 warnings，退出码 0。** 打包时 main 尚未合入；后续 P1/P3 合并候选及 Phase 2 实验不在本包的证明范围内。

| 项目 | 原始记录 |
| --- | --- |
| 源码提交 | `9f9369421486f018c837f84d21bd727aed6ab326` |
| 源码归档 SHA-256 | `7e497a3ac5022f9fa1f9b8b6f15981d04f8c3f4ace644ab9eefd9600aae764d5` |
| 副本 | 干净源码的独立本地 clone，detached exact HEAD |
| 环境 | macOS、Python 3.12.8、pytest 9.1.1、pytest-randomly 4.1.0；独立 HOME，移除继承的 `MINI_CODE_*`；无自定义逐用例诊断插件 |
| 随机种子 | `20261024` |
| 开始 → 结束（Asia/Shanghai） | 2026-10-24 23:16:53.215103 → 2026-10-25 00:10:47.626483 |
| 时间 | pytest 3231.54 秒；外层进程 3234.411 秒；JUnit suite 3230.704 秒，计时边界不同 |

完整 argv 与结构化记录见 [manifest.json](manifest.json)。原始命令为 `python -m pytest tests -vv -ra --tb=short --randomly-seed=20261024 --junitxml=…`；没有拼接 R2 或诊断续跑的计数。

## 计数、跳过与警告

日志显示 **14,927 items / 4 skipped**：14,927 个收集用例中 14,923 通过、4 个运行时跳过。JUnit 另外收录 4 个收集阶段跳过的模块，因此为 **14,931 条 testcase、8 项跳过**。未收集模块中的测试数量未知，不能将其视为仅 4 个未执行测试。

| 阶段 | 节点（均在 `tests/` 下） | 原因 |
| --- | --- | --- |
| 收集 | `test_implementation_progress_analysis.py:11` | `could not import 'numpy': No module named 'numpy'` |
| 收集 | `test_implementation_regression_analysis.py:11` | 同上 |
| 收集 | `test_single_vs_multi_analysis.py:15` | `Optional experiment-analysis dependency`；该处 `numpy` importorskip |
| 收集 | `test_three_arm_analysis.py:15` | `Optional analysis dependency`；该处 `numpy` importorskip |
| 运行 | `test_evaluation_header_timeout.py::test_verified_https_slow_headers_keep_hostname_and_certificate_checks[tls]` | `ephemeral local TLS certificate fixture` |
| 运行 | `test_evaluation_header_timeout.py::test_untrusted_https_certificate_is_still_rejected[tls]` | `ephemeral local TLS certificate fixture` |
| 运行 | `test_integration.py::TestLiveAPI::test_simple_question` | `ANTHROPIC_API_KEY not set — skipping live API test` |
| 运行 | `test_integration.py::TestLiveAPI::test_tool_use_via_api` | 同上 |

3 条 `DeprecationWarning` 的原文相同：`This process (pid=97403) is multi-threaded, use of fork() may lead to deadlocks in the child.` 触发测试均通过，未将警告从记录中移除：

1. `tests/test_memory_curator_schedule.py::test_forked_child_does_not_clean_up_an_inherited_transaction_fd`，来源 `tests/test_memory_curator_schedule.py:985`。
2. `tests/test_memory_curator_schedule.py::test_forked_child_cannot_inherit_nested_scheduler_lock_authority`，来源 Python 3.12 标准库 `multiprocessing/popen_fork.py:66`。
3. `tests/test_memory_lock_sharding_red.py::test_forked_manager_cannot_publish_inherited_dirty_project_snapshot`，来源 `tests/test_memory_lock_sharding_red.py:869`。

本结果只适用于记录的源码、环境和随机种子；不能外推生产任务能力、其他平台或未执行的可选分析/live API 测试。既有 CI 安装/导入 smoke 不能证明模块来自新构建的 wheel，本包不提供 wheel 来源验收。运行元数据中的 `apiCallsAuthorizedForThisRun: false` 是运行授权记录，不是全网络流量测量结果。

## 原字节归档

[acceptance.tar.gz](acceptance.tar.gz) 保存以下 5 个文件，未收录源码树。未压缩文件共 4,425,019 字节，压缩包 596,106 字节，SHA-256 为 `03a818305047f25c09639adb133267a259877e27297a4aa481b3dabb2415ab99`。逐文件长度和 SHA-256 在 manifest 中；打包前后原文件一致，全部成员已恢复并与原始文件逐字节比对。

| 恢复后路径 | 内容 |
| --- | --- |
| `evidence/final-candidate-pytest.log` | 完整原始 pytest 输出，包括跳过和警告 |
| `evidence/final-candidate-results.xml` | 原始 JUnit XML |
| `evidence/final-candidate-run-meta.json` | 源码、种子、起止时间、退出码、外层耗时 |
| `evidence/final-candidate-summary.json` | 从原始记录提取的结构化汇总 |
| `evidence/run_final_candidate.py` | 当时使用的运行包装脚本，作为证据保留，不在恢复时执行 |

原始文件中的本机路径反映实际运行环境；恢复和校验不依赖这些路径。运行包装脚本包含本机解释器及临时目录位置，不是可直接迁移的运行入口。历史失败和诊断中断另存于[诊断包](../phase0-diagnostics/README.md)，没有被本包覆盖。执行状态见 [release baseline](../../../docs/release-baseline-2026-10-24.md) 与[治理记录](../../../docs/governance-trial-2026-10-24.md)。

## 校验并恢复

在仓库根目录运行以下标准库代码，只创建新的临时目录，不覆盖仓库文件、不执行包内脚本。保留本目录的 README、manifest 与压缩包即可重做校验。

```python
from pathlib import Path
import hashlib
import json
import tarfile
import tempfile

bundle = Path("artifacts/governance-trial-2026-10-24/phase0-acceptance-9f936942")
manifest = json.loads((bundle / "manifest.json").read_text())
archive = bundle / manifest["archive"]["path"]
sha256 = lambda data: hashlib.sha256(data).hexdigest()
assert archive.stat().st_size == manifest["archive"]["bytes"]
assert sha256(archive.read_bytes()) == manifest["archive"]["sha256"]
records = {record["path"]: record for record in manifest["files"]}
assert len(records) == manifest["memberCount"]
restored = Path(tempfile.mkdtemp(prefix="codeloop-p0-acceptance-"))
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
assert sum(record["bytes"] for record in records.values()) == manifest["uncompressedMemberBytes"]
print(f"Verified and restored {len(records)} files to {restored}")
```
