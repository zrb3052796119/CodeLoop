# Phase 2 r3：完整公开契约的新注册

本版在 r2 因规格缺口停止后，按用户确认重新注册完整 16 题 × 2 组。原结果见 [r2 试跑](../phase2-r2-pilot/README.md)，不替换失败 cell，不合并新旧成绩。[新轮说明](../../../docs/real-task-comparison-r3-2026-10-25.md)与[冻结身份](../phase2-freeze-r3/README.md)分别记录设计和执行状态。

公开 `manifest.json` SHA-256：`eaa9645d3a2a39649d1e80c28ae3db39fc3c9bab89f8586e12515378babee5b2`。它固定新 studyId、运行后修订说明及 16 个新增公开契约后的 prompt SHA；任务来源、oracle、选择器、顺序、模型、预算和分析方案不变。

新增说明可直接查看 [16 题契约附录](public-contract-additions.md)与 [JSON](public-task-appendices.json)。[静态复审](independent-contract-review.md)已关闭 ICR-1～4；[root 独立材料核对](root-independent-review.json)另确认提示精确追加、其他注册字段不变、999 个支持文件及 329 个 Python 源码逐字相同。准备阶段有一次错误范围搜索带回少量后续输出片段，发生在契约复审和候选字节固定之后；完整披露在注册 amendment 中，不宣称全程盲态，也未据此再改题。

原 138 文件完整 portable 包压缩存于 `package.tar.gz`：1,615,242 → 358,672 字节，SHA-256 `f2e19a68e93a7db019387449052b5fbe77d7c23c9ed5c8529a30b1dde7507944`；文件清单见 `package-manifest.json`，全部成员已实际恢复比对。压缩包内的原 92 项参考检查、114 项 runner 检查及旧审阅是历史证据，本轮只复用其同字节覆盖范围，没有重跑或改称新结果。包内 README 的“未冻结”描述注册准备时点，后续冻结另存。

现有 `rebuild.py` 验证了 16 题和 25 个历史提交，并调用原 runner `prepare` 成功。准备过程中只将四个 oracle 的八处支持目录字面量迁移到新目录，逆替换后全部 oracle 与已审候选逐字相同。初次准备 wrapper 的源码清单漏项检查失败也保留在准备记录中；它不是模型试次失败。下列重建仅校验与展开，不冻结、不调用模型。

```python
from hashlib import sha256
import importlib.util
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
repo = Path.cwd()  # 在完整 CodeLoop 仓库根目录运行
bundle = repo / 'artifacts/governance-trial-2026-10-24/phase2-preparation-r3'
index = json.loads((bundle / 'package-manifest.json').read_text())
archive = bundle / index['archive']
assert archive.stat().st_size == index['archiveBytes']
assert sha256(archive.read_bytes()).hexdigest() == index['archiveSha256']
expected = {entry['path']: entry for entry in index['entries']}
restored = Path(tempfile.mkdtemp(prefix='codeloop-r3-package-'))
seen = set()
with tarfile.open(archive, 'r:gz') as tar:
    for member in tar:
        path = PurePosixPath(member.name)
        assert member.isfile() and not path.is_absolute() and '..' not in path.parts
        assert member.name in expected and member.name not in seen
        data = tar.extractfile(member).read()
        assert len(data) == expected[member.name]['bytes']
        assert sha256(data).hexdigest() == expected[member.name]['sha256']
        target = restored.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        seen.add(member.name)
assert seen == set(expected)
assert (restored / 'manifest.json').read_bytes() == (bundle / 'manifest.json').read_bytes()
spec = importlib.util.spec_from_file_location('r3_rebuild', restored / 'rebuild.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT, module.PACKAGE = repo, restored
design = json.loads((restored / 'manifest.json').read_text())
cache = module.verify(design)
output = Path(tempfile.mkdtemp(prefix='codeloop-r3-rebuild-')) / 'inputs'
module.rebuild(design, cache, output)
print(output)
```

历史 Git 对象与 dev 依赖须已存在；浅克隆须先取得清单中的完整历史对象。展开的新路径需要真实冻结和发布后方可执行，不能复用本机已开始运行的目录或覆盖历史结果。
