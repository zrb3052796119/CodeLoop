# TLS 诊断与安全运行配置

当前干净进程验收通过；历史 `self-signed certificate` 的根因未证实、现在未复现。没有足够证据将历史失败归因于端点证书、系统 CA 缺失或代理注入中的任一种，也不能声称历史网络问题已被永久修复。

实际端点为 `https://open.bigmodel.cn/api/coding/paas/v4/chat/completions`，其 SHA-256 与 trigger run-v1 冻结的 resolvedTransportRouteSha256 一致。诊断使用冻结评测原解释器 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`，以 `env -i`、`-I -B` 创建全新进程，调用生产 `_open_verified_url_controlled` → `open_verified_url`，保留 connect timeout metadata。仅发送没有 Authorization、没有正文的 HEAD，不调用模型。

## 已证实的事实

1. Python 3.12.8 / OpenSSL 3.0.15 的默认 CA 文件路径不存在，`ssl.create_default_context()` 初始为 0 个根证书，且该解释器没有 certifi。但生产 `minicode.tls` 早已在空 store 时加载 `/etc/ssl/cert.pem` 的 128 个根证书。历史冻结 host 中的 `tls.py` 与当前文件 SHA-256 完全相同：`520bcd51096831bb60fcd22e0ec66a136dc1ff31b4e3fb31d65ed70dd16c719d`。所以“默认 Python CA 路径不存在”不能单独解释历史生产 transport 失败。
2. 在获得网络权限的实际进程里，urllib 读到 macOS 系统 HTTP/HTTPS 代理 `127.0.0.1:1082`，不是环境变量里的代理。受限诊断进程看不到该系统配置且 DNS 被限制；这一次 `gaierror` 与历史 `CERTIFICATE_VERIFY_FAILED` 是不同现象。没有更改代理、绕过代理或安装代理证书。
3. 精确 chat/completions 路径在两种干净环境中都完成校验并返回预期的未认证 HTTP 401：默认生产 fallback 与显式 `SSL_CERT_FILE=/etc/ssl/cert.pem`。第三次显式 CA 预检保存了公开 peer 证书与验证链。三次记录均为 `checkHostname=true`、`verifyMode=2`（CERT_REQUIRED）；401 证明 TLS/HTTP 边界可达，不代表认证或付费模型运行通过。
4. 当前透过代理的已验证证书是 `*.bigmodel.cn`，颁发者为 `TrustAsia DV TLS RSA CA 2024`，TLS 1.3。验证链为 `*.bigmodel.cn` → `TrustAsia DV TLS RSA CA 2024` → `TrustAsia TLS RSA Root CA` → `Certum Trusted Network CA`。叶证书 SHA-256 为 `b725bf2cd9db8f27a5d7ece57ab835ef6f1488fa6662955a0db18ae9efb248d2`，有效期 2026-05-06 至 2026-11-20。当前链不是一个不受信任的自签叶证书；根哈希 `5c58468d55f58e497e743982d2b50010b6d165374acf83a7d4a32db768c4408e` 确实存在于系统包。
5. 历史失败记录没有保留当时的 peer 证书链或代理路由快照。当前成功不能追溯证明当时返回了哪个证书，也不足以认定代理曾注入证书。

## 最小安全处理

评测入口显式携带：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem
```

该路径是机器已有的系统 CA 包，本次 SHA-256 为 `9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994`。`ssl.create_default_context()` 原生支持该环境变量，现有 `minicode.tls` 机制已经足够；无需修改 adapter 或 TLS 实现，无需增加 certifi 依赖、安装根证书或关闭主机名/链验证。这项配置固定信任来源并消除默认空 store 的环境歧义，不能称为已证明修复历史自签链故障。

在新评测开始前，用同一解释器运行只读预检：

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -I -B scripts/diagnose_bigmodel_tls.py --output artifacts/native-review-acceptance-2026-10-23/tls-preflight-before-run.json
```

脚本不加载运行时凭证、不发送模型正文，保留 TLS 验证；已有输出拒绝覆盖。正式评测应继承相同 `SSL_CERT_FILE` 与原解释器。若正式格中 TLS 再次失败，按原规则记录失败和 unknown usage，不补跑、不绕过证书检查。本诊断没有触碰历史冻结材料，也没有启动任何付费模型调用。

## 机器证据

- [tls-default-clean.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/tls-default-clean.json>)：默认 fallback，精确端点，HTTP 401。
- [tls-system-ca-clean.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/tls-system-ca-clean.json>)：显式系统 CA，精确端点，HTTP 401。
- [tls-system-ca-peer-clean.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/tls-system-ca-peer-clean.json>)：显式系统 CA，完整已验证公开证书链摘要，HTTP 401。
- [tls-local-evidence.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/tls-local-evidence.json>)：解释器/冻结路由/TLS 源码一致性、CA 包哈希及可信根成员证明。
- [diagnose_bigmodel_tls.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/scripts/diagnose_bigmodel_tls.py>)：可重复的无认证预检脚本。
