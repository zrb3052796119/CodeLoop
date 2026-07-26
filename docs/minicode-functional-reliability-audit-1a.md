# MiniCode Functional Reliability Audit 1A

## 1. 总结论

审计完整通过，含义是证据链和范围符合要求，并不代表产品所有功能通过。
从 185 项正式、条件与内部能力中，124 项为 `pass`、44 项为
`partial`、7 项为 `fail`、1 项为 `unavailable`、6 项为 `blocked`、
3 项为 `not_reachable`，没有未归类项。

最重要的产品结论是：

- Reliability 1B-1A 已使 full-profile `http_request` 的审批、destination
  safety、redirect、deadline 与 bounded response 全部通过确定性和安装态
  验证；最终重认证未启用真实外部网络。
- Reliability 1B-1C 已使内置 `web_search` 使用固定、串行、各一次的
  Baidu→DuckDuckGo provider chain，共享 15 秒 monotonic deadline，
  复用 bounded safe transport，并准确区分空结果、challenge、markup
  drift、HTTP status 与网络失败。
- Reliability 1B-1B 已使 core-profile `web_fetch` 复用固定容量 DNS、
  destination validation、IP pinning、逐跳 redirect、单总 deadline 和
  bounded response；本批未运行可选真实外网 smoke。
- 未配置任何 MCP 搜索 Tool，因此 MCP search 是 `unavailable`，不是
  `web_search` 失败的原因。
- 当前仍有 1 个 P0、4 个 P1、1 个 P2 和 1 个 P3。SEC-001、SEC-003、
  WEB-001、WEB-002 已关闭；SEC-004 仅保留 archive decompression 部分。

完整机器可读证据位于
`artifacts/minicode-functional-capability-matrix.json`。
本文保留原 Audit 1A 与后续批次的历史快照；涉及“当前”状态时，以最下方
最新的 Reliability 1B-1C addendum 和机器矩阵为准。

## 2. 审计时间、系统、Python 与安装方式

- Reliability 1B-1A 最终结构化审计：2026-07-25 14:50:50
  （Asia/Shanghai）。
- 系统：macOS 15.5，arm64。
- Python：CPython 3.13.13。
- 源码态与隔离 wheel 安装态均执行；最终矩阵记录
  `installedWheel=true`、`browserVerified=true`、`liveNetwork=false`。
- Reliability 1B-1A wheel 由 packaging test 在隔离临时目录构建、安装并在
  非源码 cwd 验证，完成后按要求清理；不把历史 1A wheel SHA 误记为本批
  构建值。
- wheel 从非源码工作目录导入，实际模块来自隔离 venv 的
  `site-packages/minicode`。
- 运行时依赖仍为 `[]`，未下载第三方运行时依赖。

## 3. 修改前 baseline、full pytest 与 gold

- Active baseline：`memory-retrieval-production-v35`。
- 保护文件：56/56；`candidateMatches=true`；
  `currentFiles.matches=true`；v1–v35 `manifestIntegrity=true`。
- v35 manifest SHA-256：
  `bc2f16ee8f19dc7d59b878e35324486acd0cd110f16602ed722d3f4163572fc4`。
- 修改前完整 pytest：
  `2960 passed, 2 skipped, 3 warnings in 235.59s`。
- Accepted semantic gold：
  SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`，
  size `3033592`，mtime_ns `1784135857000000000`。

## 4. 能力总数

- 能力矩阵：185 项。
- 运行时注册 Tool：53 项。
- 默认 core Tool：26 项。
- full-only utility Tool：27 项。
- AST 发现的命名 `ToolDefinition`：59 个，另含动态 MCP 构造点。
- Console script：4 个。
- TUI slash-command usage：31 个。
- 静态发现的 HTTP route literal：27 个。
- Dashboard 主页面：8 个；Memory 子页面：6 个。
- 正式问题：7 个。

## 5. 状态统计

| 状态 | 数量 |
| --- | ---: |
| `pass` | 124 |
| `partial` | 44 |
| `fail` | 7 |
| `unavailable` | 1 |
| `blocked` | 6 |
| `not_reachable` | 3 |
| `not_tested` | 0 |

`partial` 主要来自 full-only utility 的条件可达性、安全预算尚未完全证明，
以及存在单元/fixture 证据但缺少独立 live 证据的内部控制模块。

## 6. 正式入口可达性图

```text
minicode-py ────────> TUI ──────────────┐
minicode-headless ─> Headless ──────────┤
minicode-gateway ──> /run + Chat ───────┼─> AgentRuntime
Dashboard ─────────> REST/SSE + Chat ───┘       │
                                                ├─> ToolRegistry
                                                ├─> Session/Turn/RunJournal
                                                ├─> Memory/Skill
                                                └─> optional MCP clients

minicode-cron ─────> Headless runner（条件任务）
```

默认 Tool 表面是 core 26 项。full profile 才会加入 27 项 utility Tool。
MCP Tool 还要求隔离配置中存在并成功发现对应 server descriptor/resource/prompt。

## 7. ToolRegistry 完整清单与结果

所有 53 个已注册 Tool 的 schema 都可 JSON 序列化，且均存在于安装 wheel。
`installed` 列全部为 `pass`。

| Tool | Profile | Deterministic | Live | Safety | Status | Issue |
| --- | --- | --- | --- | --- | --- | --- |
| `ask_user` | core | partial | n/a | partial | pass | — |
| `base64_decode` | full | pass | n/a | partial | partial | — |
| `base64_encode` | full | partial | n/a | partial | partial | TOOL-003 |
| `batch_copy` | core | partial | n/a | partial | pass | — |
| `batch_delete` | core | partial | n/a | partial | pass | — |
| `batch_move` | core | partial | n/a | partial | pass | — |
| `code_review` | core | pass | n/a | partial | pass | — |
| `csv_create` | full | pass | n/a | partial | partial | — |
| `csv_parse` | full | pass | n/a | partial | partial | — |
| `current_time` | full | pass | n/a | partial | partial | — |
| `diff_viewer` | core | pass | n/a | partial | pass | — |
| `edit_file` | core | pass | n/a | partial | pass | — |
| `file_line_count` | core | pass | n/a | partial | pass | TOOL-002 |
| `file_tree` | core | pass | n/a | partial | pass | — |
| `find_references` | core | partial | n/a | partial | pass | — |
| `find_symbols` | core | partial | n/a | partial | pass | — |
| `get_ast_info` | core | pass | n/a | partial | pass | — |
| `git` | core | pass | n/a | partial | pass | — |
| `grep_files` | core | pass | n/a | partial | pass | — |
| `gzip_compress` | full | partial | n/a | fail | fail | SEC-002 |
| `gzip_decompress` | full | partial | n/a | fail | fail | SEC-002, SEC-004 |
| `hash` | full | partial | n/a | partial | partial | TOOL-003 |
| `hmac` | full | pass | n/a | partial | partial | — |
| `http_request` | full | pass | blocked | pass | pass | — |
| `json_format` | full | pass | n/a | partial | partial | — |
| `json_parse` | full | pass | n/a | partial | partial | — |
| `line_count` | full | partial | n/a | partial | partial | TOOL-003 |
| `list_files` | core | pass | n/a | partial | pass | — |
| `load_skill` | core | pass | n/a | partial | pass | — |
| `patch_file` | core | pass | n/a | partial | pass | — |
| `propose_skill` | core | pass | n/a | partial | pass | — |
| `random_string` | full | pass | n/a | partial | partial | — |
| `read_file` | core | pass | n/a | fail | fail | TOOL-001 |
| `regex_replace` | full | pass | n/a | partial | partial | — |
| `regex_test` | full | pass | n/a | partial | partial | — |
| `run_command` | core | pass | n/a | partial | pass | — |
| `tar_create` | full | partial | n/a | fail | fail | SEC-002 |
| `tar_extract` | full | partial | n/a | partial | partial | — |
| `task` | core | pass | n/a | partial | pass | — |
| `test_runner` | core | pass | n/a | partial | pass | — |
| `text_dedupe` | full | partial | n/a | partial | partial | TOOL-003 |
| `text_join` | full | partial | n/a | partial | partial | TOOL-003 |
| `text_sort` | full | partial | n/a | partial | partial | TOOL-003 |
| `timestamp_convert` | full | pass | n/a | partial | partial | — |
| `todo_write` | core | partial | n/a | partial | pass | — |
| `url_decode` | full | pass | n/a | partial | partial | — |
| `url_encode` | full | partial | n/a | partial | partial | TOOL-003 |
| `uuid_generate` | full | pass | n/a | partial | partial | — |
| `web_fetch` | core | pass | blocked | pass | pass | — |
| `web_search` | core | pass | blocked | pass | pass | — |
| `write_file` | core | pass | n/a | partial | pass | — |
| `zip_create` | full | partial | n/a | fail | fail | SEC-002 |
| `zip_extract` | full | partial | n/a | partial | partial | — |

源码中的 `modify_file` 被 `create_default_tool_registry()` 明确移除，状态为
`not_reachable`，不能算作正式 Tool。动态 MCP Tool 及四个资源/Prompt Tool
在空 MCP 配置下为 `blocked`，不是永久不可达。

## 8. TUI 结果

- `minicode-py --help` 在安装态正常退出。
- 非法参数返回 argparse exit code 2 和安全错误。
- 31 个 slash-command usage 从 `SLASH_COMMANDS` 自动发现。
- TTY 输入、提交、失败、历史 Session、Permission、取消和 finished-turn
  commit 由确定性 TTY harness 覆盖。
- 入口/Tool/TTY/packaging 聚焦组：97/97 通过。
- 未人工盲测终端；没有调用真实模型。

## 9. Headless 结果

- `run_headless()` 与 `AgentRuntime` 正式接线存在。
- MockModel、Tool call/error、Model error、取消、Run lifecycle、exactly-once
  观察均有确定性测试。
- 没有 Provider credential 时的真实模型 live 能力为 `blocked`，不是产品
  `fail`。
- `minicode-cron --once --dry-run` 对缺失配置安全返回“无任务”，未执行
  prompt。

## 10. Gateway 结果

- `/health`、`/api/v1/health`、`/run`、Chat POST、Turn status/cancel、错误
  envelope、body/Content-Type/Origin/loopback 边界均有正式测试。
- 静态发现了 root、assets 与 24 个 `/api/v1/...` route literal。
- 安装态 Gateway 从非源码目录启动并被真实浏览器访问。
- HTTP/SSE 测试使用隔离 loopback 端口；临时 Gateway clean shutdown 后端口
  不再监听。
- 一次请求不得重复 Run 的契约由现有 lifecycle/entrypoint tests 覆盖。

## 11. Dashboard 结果

- 8 个主路由：Overview、Runs、Sessions、Memory、Skills、Connections、Ops、
  System 均在安装态浏览器完成加载。
- 6 个 Memory 子路由：Overview、Scopes、Approvals、Retrieval、Injection、
  Lifecycle 均完成加载。
- REST authority、SSE、replay/reset、polling fallback、Chat、Permission、
  Memory Approval、Session/Project Memory 删除、Data Health 均有回归证据。
- 1280、700、480 px 下 `scrollWidth == viewportWidth`。
- 480 px Chat Dock 与导航覆盖层可关闭；主面板恢复为 0–480 px。
- 浏览器 console warning/error 为 0。
- 没有发送 Chat 或删除动作；只对本批隔离浏览器 fixture 提交一次 Deny，
  未写入用户数据。

## 12. Agent/Model 结果

- MockModel 普通回答、单/多 Tool call、Tool error、Model error、retry、
  ModelSwitcher、context recovery、cancellation 与 Permission wait/deny 有
  确定性测试。
- Run lifecycle、assistant completed exactly-once、usage/duration/canonical
  cost projection 有正式测试。
- 已知模型、未知模型、cache usage 和 malformed usage 由 pricing/usage tests
  覆盖；Dashboard 不自行推算 Cost。
- RunJournal 的安全 projection 不保留 Prompt、Assistant 正文或 Tool
  输入输出。
- 真实 Provider 配置在隔离环境中被主动移除，live 状态为 `blocked`。

## 13. Session、Turn 与 RunJournal 结果

- Session/Turn/RunJournal/Memory 聚焦组：304/304 通过。
- Session 覆盖新建、增量/full consolidation、generation/stale delta、
  corruption、并发/跨进程锁、restart、Workspace 隔离和删除。
- Turn 覆盖 accepted、running、cancel_requested、committing、completed、
  failed、cancelled、interrupted、lost-response status、幂等和 cancel/commit
  竞态。
- RunJournal 覆盖 lifecycle、event 顺序/白名单、corruption isolation、
  pagination/cursor、retention、Workspace 隔离和安全 projection。
- 这些通过项不抵消本报告发现的 Tool/Web/Memory intake 缺口。

## 14. Memory 结果

- 文件存储、Project/User/Local scope、四 tier、retrieval、ranking、
  injection、reflection、approval、deletion、restart 和 Dashboard projection
  均分别建项。
- 隔离输入“**小花是我唯一的好朋友。**”经过当前
  `MemoryPipeline.write()` 后，没有产生持久条目，也无法搜索到“小花”。
- 单独的 `web_search` timeout trace 产生了 pending、inactive 的
  `error_pattern` reflection。
- 因此 error reflection 成功不能被描述成普通对话事实记忆成功。
- 新 Session 不能通过 `web_search` 伪造“小花”的答案；当前缺少已批准、
  可检索、可注入的持久事实。
- safe/approved/active、held/rejected、stale revision、approval restart 与
  Project Memory 删除均有现有测试。

## 15. Skill 结果

- 四类来源分别入矩阵：用户 MiniCode、项目 MiniCode、项目
  Claude-compatible、用户 Claude-compatible。
- `SKILL.md` 发现、普通文件和 `.DS_Store` 忽略、symlink escape、metadata、
  routing、selected/unselected、`load_skill`、`propose_skill` 和 wheel 行为
  有测试。
- Skill/fake-MCP/Permission 聚焦组共 197/197 通过。
- Skill 内容不会进入安全 Run projection。

## 16. MCP 结果

- fake stdio MCP 的 start/ready/call/failure/close、timeout、process exit、
  current-state registry、Workspace scope、restart 与 historical/current
  分离均有测试。
- 当前隔离配置为空；没有读取用户 MCP 配置、command、args、env 或
  credential。
- 动态 MCP Tool、`list_mcp_resources`、`read_mcp_resource`、
  `list_mcp_prompts`、`get_mcp_prompt` 因无 server descriptor/resource/
  prompt 为 `blocked`。
- 可选 MCP search 未配置，为 `unavailable`。
- MCP 不可用不能推出 MiniCode 整体断网。

## 17. Permission 与安全结果

- path/edit/command review、allow/deny/cancel/timeout/capacity、revision、
  Workspace scope、stale request、review truncation、diff fidelity 和终态
  不可变均有正式测试。
- 现有 archive extraction traversal/zip-slip/tar-slip 测试通过。
- archive **creation** 路径绕过、archive decompression budget 和 raw
  traceback 泄漏问题仍在；SEC-001 与 `http_request` response budget 已由
  Reliability 1B-1A 关闭，SEC-003 与 `web_fetch` response budget 已由
  Reliability 1B-1B 关闭。
- 安全扫描未发现 pickle load、`eval` 或 `shell=True`。命中的
  `asyncio.create_subprocess_exec` 是参数化 API；JavaScript `.exec()` 是
 正则执行。
- 最终矩阵不含绝对 HOME、Bearer、API key 名、假 secret、Prompt、Tool
  output 或 Traceback。

## 18. Web 专项结果

当前调用图：

```text
Agent -> ToolRegistry -> web_search -> strict input/provider config
                    │                 -> Baidu once ---------------------┐
                    │                 -> optional DuckDuckGo once -------┤
                    │                    provider-specific HTMLParser     │
                    │                    + safe result URL projection     │
                    \-> web_fetch -> normalize -> execute_safe_get ------┤
                    \-> http_request -> normalize/permission             │
                                      -> execute_safe_http ---------------┤
                                                          shared safe transport
                                                          bounded resolver
                                                          IP pinning/TLS
                                                          per-hop redirect check
                                                          bounded response
optional MCP search -> independent configured MCP process（当前不存在）
```

- `web_search`：默认 Baidu→DuckDuckGo；总预算 15 秒、每 provider 最多
  6 秒；每个最多一次、无 retry；两个独立 streaming parser 区分正常、
  显式空页、challenge 与 markup drift；HTTP 403/429/4xx/5xx 和安全网络
  状态使用封闭分类。
- `web_fetch`：30 秒单总 monotonic deadline；最多 3 次 redirect；初始和
  每个 target 均重新 normalize/validate/pin；1 MiB wire budget、64 KiB
  read size、identity encoding 和低基数错误。
- `http_request`：支持文档化方法和 bounded header/body；公网 HTTPS
  mutation 每次审批，非公网 destination fail-closed，GET/HEAD redirect
  逐跳重验并 pin，response 以 64 KiB 分块、1 MiB 总量读取。
- 三者互相独立，也都不依赖 MCP。

确定性 fixture 覆盖两个 provider 的正常中英文结果、显式空页、changed
markup、challenge、unsafe/private result URL、HTTP status、fallback、
response budget、DNS/redirect/TLS 和 output redaction；fallback 通过真实
`web_search_tool`/ToolRegistry 生产入口执行。

## 19. MiniCode 是否整体可以联网

**网络能力存在，但真实外部 provider 的可达性与页面结构不是稳定契约。**

同一最终 smoke 中：

- Example HTTPS：HTTP 200。
- 百度搜索页：HTTP 200。
- full-profile `http_request` GET Example：HTTP 200。
- `web_search` DuckDuckGo：timeout。
- `web_fetch` Example：最终安装态真实 Tool 调用返回 200；审计器修正期间
  的另一独立尝试出现过 timeout。

以上是历史 Audit 1A 的 live 证据。Reliability 1B-1C 的最终验收没有启用
外网，只以确定性 fixture、loopback safe transport 和隔离 wheel 为硬门。
因此不能从本批推断 provider 永久在线，也不能把 MCP search 未配置误报为
内置 `web_search` 失败。

## 20. web_search、web_fetch、http_request、MCP search 状态

| 能力 | 当前结论 | 原因 |
| --- | --- | --- |
| `web_search` | pass deterministic/installed/safety/truthfulness | 固定 Baidu→DuckDuckGo 有界 fallback、共享 safe transport、独立 parser 和低基数真实错误；可选 live 未运行 |
| `web_fetch` | pass deterministic/installed/safety/truthfulness | 共享 bounded resolver、destination pin、逐跳 redirect 与 bounded response；可选 live 未运行 |
| `http_request` | pass deterministic/installed/safety/truthfulness | safe GET、一次性 mutation 审批、destination pin、bounded response 均已认证；最终 audit 未启用外网 |
| MCP search | unavailable | 隔离配置中不存在搜索 MCP Tool |

## 21. Installed wheel 结果

- `python -m build --wheel` 在本机失败：已安装的 `build` 包没有
  `build.__main__`。这是工具环境限制，未修改项目。
- 项目兼容命令 `pip wheel --no-deps --no-build-isolation` 成功。
- wheel 在隔离 venv 安装成功，版本 0.1.0。
- 从非源码 cwd 导入 `minicode`，来源是 venv `site-packages`。
- 4 个 console script 可在 wheel metadata/venv 中发现。
- classic CLI help、非法参数、Cron 空配置和 Dashboard Gateway/browser
  均在安装态验证。
- 53 个注册 Tool 全部在 wheel 环境中发现。

## 22. 原始 Audit 1A Live smoke（历史证据）

以下是原始 Audit 1A 的历史 live 证据；Reliability 1B-1A 最终矩阵明确
使用 `liveNetwork=false`，没有用新的外部网络结果覆盖它。原 smoke 时间：
2026-07-25 01:15:15（Asia/Shanghai）。
总耗时：12,201 ms；每个真实 Tool 子进程最大 9 秒；无 API Key、无付费
模型、无响应正文持久化。

| Probe | Provider | 结果 | HTTP | 耗时 |
| --- | --- | --- | ---: | ---: |
| Search | built-in `web_search` / DuckDuckGo HTML | timeout | — | 9,012 ms |
| Ordinary HTTPS | Example HTTPS | pass | 200 | 660 ms |
| Accessible search page | Baidu search | pass | 200 | 357 ms |
| Fetch | built-in `web_fetch` / Example HTTPS | pass | 200 | 793 ms |
| HTTP Tool | full `http_request` / Example HTTPS | pass | 200 | 1,376 ms |

live 波动没有加入默认 pytest。审计器的 provider/profile/ID 缺陷修正各自
要求重新生成矩阵；报告保留最后一次修正后结果，并透明记录修正期间真实
`web_fetch` 调用既出现过成功也出现过 timeout，不用某次结果替代稳定性结论。

## 23. P0 问题

### SEC-001 — `tool.http_request`

**Closed by Reliability 1B-1A.** 原始隔离 loopback fixture 收到恰好一个
未审批 POST；最终同类 fixture 返回
`blocked:destination_blocked` 且收包数为 0。公网 HTTPS mutation 现在使用
一次性 network approval、审批后 destination/fingerprint 重绑定和发送前
最终 cancellation checkpoint。Deny/Cancel/expiry/close/unavailable 均为
零发送。

### SEC-002 — `tool.gzip_compress`（同影响 tar/zip creation）

- 用户影响：full-profile archive creation 可越权写出 Workspace。
- 最小复现：隔离 Workspace 中将 destination 设为 `../escape.gz`；
  tar/zip 同样复现。
- Expected：source/destination 全部经过 Workspace/Permission resolver。
- Actual：`gzip_compress`、`tar_create`、`zip_create` 创建了 sibling 文件。
- 证据：`minicode/tools/archive_utils.py`、审计 runner。
- 稳定复现：是；环境依赖：否。
- 修复批次：Reliability 1B-2。
- 必须先补 RED：`..`、absolute、symlink、deny/cancel 全部零越界写入。

### SEC-003 — `tool.web_fetch`

**Closed by Reliability 1B-1B.** 原始真实 Tool RED 允许
`http://172.17.0.1/...`、调用 urllib opener 并回显 secret URL。最终
`web_fetch` 对初始和每个 redirect target 复用 `validate_destination()`、
共享 4/8/12 bounded resolver 和固定 IP transport；IPv4、IPv6、
IPv4-mapped IPv6、mixed DNS、rebinding 和 public-to-private redirect
均在目标发送前 fail-closed。

## 24. P1 问题

### WEB-001 — `tool.web_search`

**Closed by Reliability 1B-1C.** 固定 Baidu→DuckDuckGo provider 顺序在
第一个 provider timeout/DNS/403/429/5xx/challenge/drift/no-results 时，
只要总 deadline 尚存就调用第二个 provider 一次。首个合法结果立即停止，
每个 provider 最多一次，无 retry/backoff；非法配置和 unsafe destination
在网络发送前 fail-closed。

### SEC-004 — `tool.gzip_decompress` / archive remainder

- 影响：archive decompression 仍可在提取期间消耗无界资源。
- 复现：使用高压缩比 archive fixture。
- Expected：extracted bytes/member/time 在分配和写入前有总预算。
- Actual：HTTP response paths 均已使用 64 KiB 分块和 1 MiB 总量关闭；
  archive extraction 仍无 aggregate budget。
- 证据：`minicode/tools/archive_utils.py`。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-2。
- RED：超限 archive 在 byte/member/time budget 终止。

### TOOL-001 — `tool.read_file`

- 影响：不存在/不可读文件会被说成成功的空文件。
- 复现：读取 Workspace 内不存在路径。
- Expected：`ok=false` 和稳定 not_found/unreadable code。
- Actual：`ok=true`、`TOTAL_CHARS: 0`。
- 证据：`minicode/tools/read_file.py`、审计 runner。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-2。
- RED：missing/denied/directory/binary/true-empty 必须区分。

### SEC-005 — `security.workspace`

- 影响：Tool crash 可把绝对路径和 traceback 暴露给模型/用户。
- 复现：隔离 fixture Tool 抛出含 Workspace path 的异常。
- Expected：closed error code + redacted diagnostic。
- Actual：`ToolRegistry.execute` 返回 raw exception 与 traceback excerpt。
- 证据：`minicode/tooling.py`、审计 runner。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-2。
- RED：输入、路径、token、env、traceback 全部脱敏。

### MEM-001 — `memory.conversation_fact`

- 影响：普通用户事实不能跨 Session 持久化/检索，但错误反思可 pending。
- 复现：输入“小花是我唯一的好朋友。”，再搜索“小花”。
- Expected：明确的可审批持久事实，含 scope/provenance。
- Actual：无事实；独立 web_search error trace 产生 pending error_pattern。
- 证据：`minicode/memory_pipeline.py`、Batch 9 roadmap、审计 runner。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-3。
- RED：两隔离 Session 证明事实保存、审批、检索、注入且不调用 web_search。

## 25. P2 问题

### WEB-002 — `tool.web_search`

**Closed by Reliability 1B-1C.** Baidu 与 DuckDuckGo 各有独立的 bounded
`HTMLParser`；显式空页为 `no_results`，验证码/验证页为 `challenge`，
不认识或改变的结构为 `response_unrecognized`。403、429、其他 4xx、
5xx、DNS、TLS、timeout、redirect 和 response budget 均使用固定分类，
query、exception、body、header、redirect 与本机路径不进入失败输出。

### TOOL-003 — `tool.base64_encode`（同影响 7 个 utility）

- 影响：model-visible schema 与 runtime validator 行为不一致。
- 复现：对 `base64_encode`、`hash`、`line_count`、`text_dedupe`、
  `text_join`、`text_sort`、`url_encode` 传空对象。
- Expected：schema-required 字段缺失应拒绝。
- Actual：7 个 validator 将缺失值当作空值接受。
- 证据：encoding/crypto/text utility 源码与审计 runner。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-2。
- RED：表驱动 schema/validator conformance，覆盖 missing/wrong type/bool-int。

## 26. P3 问题

### TOOL-002 — `tool.file_line_count`

- 影响：只读 Tool 被错误标为非只读/非 concurrency-safe。
- 复现：读取 `ToolDefinition.is_read_only`。
- Expected：只读导航 Tool 明确暴露 read-only/concurrency-safe metadata。
- Actual：未进入中央 read-only 集合，也没有显式 metadata。
- 证据：`minicode/tooling.py`、`file_line_count.py`。
- 稳定复现：是；环境依赖：否。
- 批次：Reliability 1B-2。
- RED：每个 Tool 都必须有 read/write/destructive/concurrency metadata 契约。

## 27. 未能测试的能力及原因

- 真实付费 Provider：`blocked`；审计主动移除 credential，禁止产生费用。
- 动态 MCP Tool 和资源/Prompt Tool：`blocked`；隔离 MCP 配置为空。
- MCP search：`unavailable`；没有配置该可选能力。
- `modify_file`：`not_reachable`；正式 registry 明确移除。
- `pipeline_engine`、`timeline_memory`：源码存在但无正式入口，
  `not_reachable`。
- pyright、mypy、pip-audit：本机未安装，明确记为未执行。
- 对 live 外部网络不做稳定性断言；最终结果仅代表记录时间的机器/网络。

## 28. 推荐修复顺序

1. **Reliability 1B-2：File/command Tool correctness**
   - archive creation Workspace boundary、read_file truthfulness、
     Tool error redaction、schema conformance、metadata。
2. **Reliability 1B-3：Session/Memory persistence gaps**
   - 普通 conversational fact intake；保持 approval/scope/provenance。
3. **Reliability 1B-4：Gateway/Dashboard consistency**
   - 当前无阻断缺陷；仅在前述修复改变状态/error schema 时同步投影。
4. **Reliability 1B-5：Packaging and recovery**
   - 规范 build 工具链和 fresh-machine recovery 文档，不改变运行时功能。

不得把这些批次合并成一次大修，也不得在 1A 中实施。

## 29. 审计与重认证文件

- `scripts/run_functional_audit.py`
- `tests/functional_audit/__init__.py`
- `tests/functional_audit/test_runner.py`
- `artifacts/minicode-functional-capability-matrix.json`
- `docs/minicode-functional-reliability-audit-1a.md`

Reliability 1B-1A 另外新增 HTTP safety 实施说明与 v36 baseline 说明，
并按 v36 精确 allowlist 修改生产文件；详见
`docs/minicode-reliability-1b-1a-http-request-safety.md`。

## 30. pytest、Ruff、compile 与 node check

- 修改前 full：2960 passed，2 skipped，3 warnings，235.59s。
- 审计 runner contract：4/4 passed。
- Reliability 1B-1A 聚焦组：HTTP 69、Permission/TUI/Dashboard 113、
  Gateway/Chat/Cancel 150、Tooling/RunJournal 65、baseline/semantic 215、
  packaging 9、独立 installed-wheel smoke 1，全部通过。
- 最终 full 两轮：均为 3042 passed、2 skipped、3 个既有 benchmark mark
  warnings，分别 201.97s 与 202.00s。
- scoped Ruff：pass。
- `py_compile`：pass。
- `compileall -q minicode scripts tests`：pass。
- `node --check app.js`：pass。
- `node --check cost-format.js`：pass。
- pyright/mypy/pip-audit：未安装，未虚报。

## 31. baseline/gold 前后不变证明

- Final verifier：v36 active，58/58，candidate/current match，
  v1–v36 integrity 全 true。
- v36 parent 为 v35；精确 delta 是 5 changed、2 added、0 removed；
  manifest SHA：
  `7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a`。
- Gold SHA/size/mtime_ns 未变：
  `5629d6cf...fdd3b`，3033592，
  `1784135857000000000`。
- `web_search.py`、`web_fetch.py`、archive、Agent Loop、Memory、Session、
  MCP、gold 和性能阈值未修改。v36 只保护并接受 HTTP safety 的精确 delta。

## 32. 用户真实数据未被修改的证明

- runner 在 import MiniCode 前创建独立 HOME、`MINI_CODE_DIR` 和 Workspace。
- credential-like 环境变量在审计子环境中被移除。
- fixture Session/Run/Turn/Memory/Skill/MCP/HTTP/Gateway 全部位于临时目录。
- 审计 runner contract 证明 launcher HOME 下没有生成真实 `.mini-code`。
- 浏览器 Gateway 使用独立临时 HOME/数据目录；Chat、Approval、Deletion
  均未提交。
- 未读取用户 Session、Memory、RunJournal、Skill、MCP credential 或
  Provider credential。

## 33. 临时进程、端口和文件清理

- fixture HTTP server 在每次 probe 后 shutdown/join。
- Browser Gateway 已发送中断并关闭；审计端口 connect 返回拒绝。
- in-app browser 临时 tab 已 finalize，viewport 已 reset。
- wheel venv、wheel output、隔离 HOME/data、debug JSON 均按严格命名清理。
- 无 Gateway listener、fixture server、fake MCP、pytest 子进程或 wheel
  venv 残留。

## 34. Reliability 1B-1A 范围确认

本次只实施 `http_request` 的 network approval、destination safety、
deadline/redirect/response budget 和最小 Permission UI 投影。没有修复或
接线 `web_fetch`、`web_search`、archive、Agent Loop、Memory、Session 或
MCP，没有增加运行时依赖，也没有进入 Reliability 1B-1B。

## Post-Audit Addendum — Reliability 1B-1A.1

这是 Audit 1A 完成后发现并修复的后续问题，不是对历史审计发现时间的
重写。

- `DNS-001`，Severity P1，Status closed：原 DNS deadline 每次请求新建
  daemon thread，25 次受控 timeout 留下 25 个阻塞 resolver thread。
- Reliability 1B-1A.1 改为进程内共享的固定容量 resolver：4 workers、
  8 queued、12 outstanding；饱和时 fail-closed 为 `resolver_busy`。
- 25 次 timeout 后底层进入数和 resolver worker 数不超过 4；在同一
  resolver 上再追加 100 次 timeout 后 worker 集合不增长。
- 安装 wheel 的真实子进程在仍有一个阻塞 resolver worker 时可正常退出。
- Functional Audit 的 `tool.http_request` 证据新增了容量、饱和和 daemon
  exit 契约；SEC-001 继续 closed。
- 当前矩阵仍为 185 capabilities、10 open issues。仍保留 WEB-001、
  WEB-002、SEC-002、SEC-003、SEC-004、SEC-005、MEM-001、TOOL-001、
  TOOL-002、TOOL-003。
- active production baseline 已推进至
  `memory-retrieval-production-v37`；本批没有接线或宣称修复
  `web_fetch`、`web_search` 或 archive。

## Post-Audit Addendum — Reliability 1B-1B

这是在 1B-1A.1 之后完成的 `web_fetch` 专项修复，不重写原始 Audit 1A
和既有 live smoke 的历史时间线。

- `SEC-003` 已关闭。`web_fetch` 使用共享 bounded resolver、完整 public
  destination policy、IP-pinned HTTP/HTTPS transport 和 TLS 原 hostname。
- `SEC-004` 的 HTTP/`web_fetch` response 部分已关闭；正式问题只保留
  `tool.gzip_decompress` 的 archive byte/member/time budget，evidence 仅为
  `minicode/tools/archive_utils.py`，recommended batch 为 Reliability 1B-2。
- `tool.web_fetch` 的 deterministic、installed-wheel、safety、
  truthfulness 和 status 均为 `pass`，issues 为空；live 为 `blocked`
  （本批未启用可选外网 smoke）。
- 当前矩阵为 185 capabilities、123 pass、44 partial、8 fail、
  1 unavailable、6 blocked、3 not reachable 和 9 open issues：
  WEB-001、WEB-002、SEC-002、SEC-004、TOOL-001、TOOL-002、TOOL-003、
  SEC-005、MEM-001。
- active production baseline 为
  `memory-retrieval-production-v38`，parent v37，60/60 protected，
  candidate/current 和 v1–v38 integrity 全 true。Manifest SHA-256 为
  `49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`。
- 精确 v37→v38 delta：changed `minicode/tools/http_utils.py`；added to
  protection `minicode/tools/web_fetch.py`；removed none。
- 聚焦网络 161、广义兼容 391、Functional Audit contract 4、baseline
  196、semantic tests 32、packaging 9 全部通过。两轮完整 pytest 均为
  3147 passed、2 skipped、3 个既有 warning。
- official evaluator 保持 108 cases、37 gaps、Phase 3B true、remote
  calls 0、evaluation passed。Accepted gold SHA/size/mtime_ns 未变。
- `web_search` 与 archive implementation 未修改；没有进入搜索 fallback
  或 Reliability 1B-2。

## Post-Audit Addendum — Reliability 1B-1C

这是在 1B-1B 之后完成的内置 `web_search` 专项修复。原始 Audit 1A live
smoke 仍作为历史事实保留，但不再代表当前确定性产品能力。

- `WEB-001`、`WEB-002` 已关闭。内置 core/read-only `web_search` 使用固定
  Baidu→DuckDuckGo provider chain；可由封闭环境变量选择一或两个固定
  provider，非法配置零发送。
- 每次调用共享 15 秒 monotonic deadline，每 provider 最多 6 秒、串行、
  各一次、无 retry/sleep；首个合法非空结果立即停止。
- 两个 provider 使用独立 bounded `HTMLParser`，区分正常结果、显式空页、
  challenge 与 response drift。结果 URL 只做安全文本投影，不进行 DNS 或
  fetch。
- `execute_safe_get_response()` 是新增的 GET-only final-status 观察接口，
  仍复用 v38 的 destination validation、共享 bounded resolver、IP
  pinning、TLS hostname、逐跳 redirect 和 1 MiB/64 KiB response budget。
  原 `web_fetch`/`http_request` 的 `HTTP >= 400 -> http_error` 语义不变。
- 当前矩阵为 185 capabilities、124 pass、44 partial、7 fail、
  1 unavailable、6 blocked、3 not reachable 和 7 open issues：
  SEC-002、SEC-004、TOOL-001、TOOL-002、TOOL-003、SEC-005、MEM-001。
- `tool.web_search` 的 deterministic、installed-wheel、safety、
  truthfulness 和 status 均为 `pass`，issues 为空；live 为 `blocked`
  （未执行可选真实外网 smoke）。
- active production baseline 为
  `memory-retrieval-production-v39`，parent v38，62/62 protected，
  candidate/current 和 v1–v39 integrity 全 true。Manifest SHA-256 为
  `9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`。
- 精确 v38→v39 delta：changed `minicode/tools/http_utils.py`；added to
  protection `minicode/tools/search_providers.py` 和
  `minicode/tools/web_search.py`；removed none。
- 搜索聚焦 159、网络/Tool/Audit 333、广义兼容 632（另 2 skipped）、
  baseline/semantic 239、packaging 9 全部通过。最终 review 后第一轮完整
  pytest 为 3314 passed、2 skipped、3 个既有 warning；第二轮为
  2 failed、3312 passed、2 skipped、3 warnings。
- 第二轮两处失败均来自未修改的 Phase 2A evaluator：报告中 canonical
  retrieval P95 为 5.269083 ms，超过冻结的 5.0 ms；另一个断言观察到连续
  两次评估的 timing-derived gate 不一致。随后系统整体 CPU 为 84.38%
  idle。未修改 Memory、测试、阈值、manifest 或 gold，也未重跑挑选幸运
  结果。后续同合同 raw trailing-control guard 的最终 scoped/compatibility/
  wheel/baseline/static 门全绿，但没有再运行 full。因此 Reliability 1B-1C
  实现完成但最终关闭仍被阻塞。
- official evaluator 保持 108 cases、37 gaps、Phase 3B true、
  remote calls 0、evaluation passed。Accepted gold SHA/size/mtime_ns 未变。
- archive 与 Memory 等剩余 7 个问题未修改；没有进入 Reliability 1B-2。
