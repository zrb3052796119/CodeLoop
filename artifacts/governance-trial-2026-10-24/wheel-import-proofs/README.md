# Wheel 导入来源与现有检查器修复证明

本包区分三类证据，**不是完整随机套件验收**，也不保证所有递归依赖的来源。

| 范围 | 源码身份与结果 | 入口 |
| --- | --- | --- |
| 原有 CI 脚本的实际运行 | `9f936942`、`eb17c62a` 均 exit 0；其 `-I` 首次导入可能由 editable 满足，因此不能单凭这两次结果证明 wheel 来源 | `legacy-checks/summary.json` |
| 修复检查器本身 | `71347fd5d0a3d81c5b5d7279f4accb55152c12ed` 原脚本 + 最终测试字节：3 failed / 2 passed；相同测试字节 + 修复：5 passed。旧源码 SHA、最终两文件 SHA、命令和时间均在报告中 | `checker/report.json`、`before.log`、`after.log` |
| 两份真实发布 wheel 的独立来源证明 | exact Git archive `9f9369421486f018c837f84d21bd727aed6ab326` 与 `eb17c62a5510b6746c5fc677618303c9b83b96e6`，分别 build/install 后用 `python -I -S` 导入；各自 9 个模块的 resolved `__file__` 全在对应 installed target，sys.path 无 site-packages / 源码 / editable | `wheel-only/<identity>/report.json` 与实际 stdout/stderr |

检查器修复在首次导入前显式插入 installed target，并逐个校验 9 个 required modules 的来源；删除“首次失败才插入 target”的 fallback。真实 runtime 测试也不再把 wheel_build / installed_imports 失败通过 `return` 变成绿色。三个反例实际创建本地 Git 仓库、构建与安装 wheel、执行子进程；只注入物理 ambient 目录，不伪造进程结果：完整 wheel 必须优先使用 wheel；缺失未跟踪依赖必须失败；wheel 通过 `__path__` 引入外来模块必须被来源断言拒绝。

独立 wheel-only 证明比修复后的正常 CI 环境隔离更强：`-S` 关闭 site 初始化，只显式加入 installed target；唯一额外复制的依赖为本机已安装的 certifi，原路径和逐文件 SHA 在各自报告中。它不联网。包内保留当次 archive / wheel SHA 与构建命令，**没有复制完整 archive、wheel、源码、installed tree 或 certifi 内容**。这些 SHA 标识当次产物，不承诺异时 wheel 构建的时间戳及字节完全相同。

`independent-review/summary.md` 保存 `/root/architecture_audit` 的只读复核简述，root 也已复核无阻塞；不将其称为额外测试执行。Windows portable 问题不属于本包。

## 包内容与完整性

27 个成员，原始 58,591 字节，压缩后 10,945 字节。`manifest.json` 固定每个成员和 `proofs.tar.gz` 的 SHA-256。tar 成员按路径排序、mode 0644、uid/gid/mtime 为 0、owner name 为空；gzip mtime 为 0、filename 为空。重复打包字节相同，已向新的临时目录恢复全部 27 个成员，并逐成员与原文件字节比对。

除最终测试字节和最小补丁外，没有复制生产树、缓存或临时安装目录。原日志保留原字节；其中绝对路径是运行时溯源，不是下载地址。`checker/run_tests.py` 与 `wheel-only/prove.py` 保留实际执行 harness，异机复现须把本机解释器和输出路径换成自己的已安装环境。

在仓库根目录验证并恢复：

```python
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

bundle = Path("artifacts/governance-trial-2026-10-24/wheel-import-proofs")
manifest = json.loads((bundle / "manifest.json").read_text())
archive = bundle / manifest["archive"]
assert archive.stat().st_size == manifest["archiveBytes"]
assert sha256(archive.read_bytes()).hexdigest() == manifest["archiveSha256"]
expected = {entry["path"]: entry for entry in manifest["entries"]}
assert len(expected) == manifest["entryCount"]
restored = Path(tempfile.mkdtemp(prefix="codeloop-wheel-import-proofs-"))
seen = set()
with tarfile.open(archive, "r:gz") as tar:
    for member in tar.getmembers():
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and ".." not in name.parts
        assert member.name in expected and member.name not in seen
        data = tar.extractfile(member).read()
        entry = expected[member.name]
        assert len(data) == entry["bytes"]
        assert sha256(data).hexdigest() == entry["sha256"]
        target = restored.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        assert target.read_bytes() == data
        seen.add(member.name)
assert seen == set(expected)
print(restored)
```

## 从本地 Git 复核检查器红绿

使用新的独立检出与已安装 dev/build 依赖的 Python，不修改正在运行测试的 checkout：

1. 检出 `71347fd5d0a3d81c5b5d7279f4accb55152c12ed`，把包内 `checker/test_clean_archive_package.final.py` 复制为该检出的 `tests/test_clean_archive_package.py`；保留原检查器。
2. 独立 HOME、清除 provider 凭证和 `MINI_CODE_*`、设置 `PYTHONPATH` 指向该检出、`PIP_NO_INDEX=1`、`PIP_DISABLE_PIP_VERSION_CHECK=1`，执行 `python -m pytest --randomly-seed=20261024 tests/test_clean_archive_package.py -q`。预期三个 ambient 反例失败、另两个通过；环境准备/构建失败不算这个反例的证明。
3. 仅应用补丁中的检查器部分：`git apply --include=scripts/check_clean_archive_package.py <恢复目录>/checker/final.patch`。测试已是最终相同字节，不重复应用测试补丁。执行完全相同命令，预期 5 passed。

最终测试 SHA 为 `0f7f2c60a2ca0eb062547ee02d50ee72e12a04db775893fc05d277d53c6b4df6`。这组结果证明现有检查器确实修复来源误判；两份 actual wheel 的 `-I -S` 记录另行证明对应历史 Git 提交的九模块可从其 wheel 导入，两类结论不可互相替代。
