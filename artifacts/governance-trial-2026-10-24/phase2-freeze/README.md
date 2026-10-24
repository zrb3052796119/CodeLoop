# Phase 2 首次调用前冻结记录

本目录保存 2026-10-25 的冻结记录，此时 **32 个试次均未调用模型、未产生结果**。生产源码和 runner 来自已通过 P0 完整验收的 `9f9369421486f018c837f84d21bd727aed6ab326`；之后的文档提交不改变其字节。题目、验收与顺序见[准备包](../phase2-preparation/README.md)，实际执行条件见[预注册文档](../../../docs/real-task-comparison-2026-10-24.md)。

`manifest.json` 为现有 runner 原样输出，SHA-256 为 `987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be`。`freeze-record.json` 记录源码身份、首次冻结拒绝原因以及成功冻结所用的三个显式角色上限；`freeze.log` 是成功进程输出。这里只存模型名和端点摘要，不存凭证或端点明文。

首次冻结发现缺失设置代表角色轮数无限，不能把遗留的 12/8/15 常数称为实际默认值。随后显式设置预注册的 explore12 / plan8 / general15，上限、题目、oracle、顺序均未更换。两组 token、调用数和费用上限仍为 null；共享任务期限为 1800 秒。此实验因此仅比较显式 bounded 条件，不能冒充未配置默认产品的实测。

冻结源码 ZIP 暂保留在本机执行目录，SHA 已由 runner 写入 manifest；此小型记录不复制完整源码。可从上述 Git 提交恢复生产和 runner 的原字节，并按 manifest 的 production/harness SHA 验证。完整运行原件将在实验结束后统一归档；不得覆盖 manifest、重跑失败 cell 或改写原始结果。

文件摘要：

```json
{
  "freeze-record.json": {
    "bytes": 1538,
    "sha256": "825344704ca965720ee61a7ab9ebe2847971a20708ecd92f37ceee9e909221b4"
  },
  "freeze.log": {
    "bytes": 57,
    "sha256": "526995fcb1ff119fe6d3e1b15013c7e4849cbee01df0c8b4d5e50ed7afa56726"
  },
  "manifest.json": {
    "bytes": 6615,
    "sha256": "987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be"
  }
}
```
