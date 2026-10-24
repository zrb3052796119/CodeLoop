# Hot-store budget 诊断与测试证明包

本包纠正原200ms总caller wall-time保证及其不稳定测试；**不是生产预算逻辑缺陷修复**。仅测试、README与误导注释改变，生产AST一致。原Phase1首次全量的详细失败trace在打包时仍待输出，已保留其FAILED进度记录；定向重跑通过不能替代该全量结果。

- `diagnosis/` 保存未插桩的基线红例、候选整文件红例、隔离运行meta/实际导入SHA，以及等待请求、实际等待、熔断日志成本观测。请求19.40ms实际等待29.14ms，说明10ms余量不足；未声称具体CPU负载是唯一原因。
- `final/` 保存最终同字节三个节点的基线/修订/三故障日志，完整347项邻接验证日志、运行meta、SHA、最终三文件补丁和完整最终test字节。
- `faults/` 是可独立应用的三个最小生产源码差分，逐一重建了实际测试的故障源码：nested预算重置、每次给完整190ms等待、迟到连接仍发命令。每个故障1fail/2pass；这是测试强度证据，不是历史bug的PROVEN证据。
- `independent-review/` 保存独立审查报告与小探针脚本/原始输出：3 个最终测试函数直接调用通过，模块属性与绑定恢复、预算上下文恢复、无遗留 `memory-redis` worker。探针沿用真实 pytest MonkeyPatch 清理；未修改生产代码，也不是全量结果或新 gate。脚本中的本机工作树路径用于溯源，异机复现需指向重建的检出。
- `original-full-runs/` 记录两份冻结源码Git身份/运行meta及目标节点进度，**不包含完整全量日志或尚未得到的失败trace**。
- `diagnostic-harness/` 保留当时的脚本原字节，其中绝对临时路径仅为溯源。复现时用下面Git身份重建，不把本机路径当下载地址。

`manifest.json` 固定gzip SHA、每个成员的SHA和长度。压缩使用mtime0、空filename；tar成员排序、mtime/uid/gid固定。打包后已逐成员解压比对。当前共 55 个成员、原始 165968 字节，压缩后 35663 字节；补入独立审查时，原有 52 个成员原字节及 SHA 均未变化。未打包5个重复生产树、pycache、缓存、重复stdout摘要、完整故障模块或早期原型；最终测试与最小差分足够重建。没有重写已有原始日志。

## 验证和展开

先按manifest核对`proofs.tar.gz`的SHA-256，然后展开到一个空目录：

```sh
mkdir /tmp/codeloop-hot-store-proofs
tar -xzf artifacts/governance-trial-2026-10-24/hot-store-budget-proofs/proofs.tar.gz -C /tmp/codeloop-hot-store-proofs
```

包内最终test SHA为`bfabf364f440601ee190c9145f4be7527e701b8d81d8f24c39388ef5b92f6046`。不同阶段、重叠测试和故障运行不可相加当成独立覆盖。

## 从本地Git重建

使用已安装项目dev依赖的同一Python（原证据为Python3.12.8）和本地提交，无需API或远端模型：

1. 在新的独立检出重建候选`eb17c62a5510b6746c5fc677618303c9b83b96e6`，应用`final/final-owned-diff.patch`；此补丁只改测试、README、源码注释。也可在基线`9f9369421486f018c837f84d21bd727aed6ab326`复制`final/tests/test_memory_hot_store.py`，保留基线生产源码。
2. 为每次运行设置独立HOME及指向该检出的PYTHONPATH，清除MINI_CODE_*及API凭证，按`final/*-meta.json`使用seed20261024和同一三个节点命令。原同字节基线/修订各3pass。
3. 每个故障使用上述已应用最终补丁的独立干净检出，分别`git apply faults/reset-nested.patch`、`git apply faults/full-wait.patch`或`git apply faults/late-command.patch`，然后运行相同三个节点。**不要把三个差分同时应用**。预期每次仅相应预算或late-command断言失败；导入错误或准备失败不算证据。

三个节点：

```text
tests/test_memory_hot_store.py::test_nested_budget_caps_cumulative_wait_across_multiple_commands
tests/test_memory_hot_store.py::test_connection_wait_is_bounded_and_late_ping_cannot_issue_command
tests/test_memory_hot_store.py::test_late_client_construction_never_pings_after_timeout
```

最终记录：70项hot-store测试及两个直接邻接文件合计347pass，CI E/F Ruff（ignoreE501）和diff检查通过；不包含新的全量验收结论。
