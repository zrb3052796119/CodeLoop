# 视觉法官落地：inspect_image 工具 + visual-judge Profile

## 设计（按可行性文档的轻路线）

**新只读工具 `inspect_image(path, question)`**（`minicode/tools/inspect_image.py`）：
- 输入校验 fail-closed：扩展名白名单（png/jpg/jpeg/webp）+ **魔数校验**（PNG `\x89PNG`、JPEG `\xFF\xD8`、WebP `RIFF....WEBP`）+ 大小上限 8MB + `resolve_tool_path(context, path, "read")` 复用既有工作区路径边界（内部状态路径拒绝、权限门走 read 意图）；**不收网络 URL**（防 SSRF/外泄）。
- 视觉调用：文件 → base64 data URI → 一次 OpenAI 兼容 `/chat/completions`（`{base_url}/chat/completions`，Bearer key，temperature 0，max_tokens 1024）。**模型必须显式配置**：`runtime["visionModel"]` 或 `runtime["subagentModels"]["visual"]`——两者皆无则 `error[vision_model_unconfigured]`，**绝不回落到默认文本模型**（否则图片发给文本模型）。凭据与端点复用子代理路由字段（`subagentBaseUrl`/`subagentApiKey`），HTTPS-only 校验，缺失即明确报错。
- 返回有界文本（≤4000 字符）+ 用量行；超时受 `MINICODE_MODEL_TIMEOUT` 约束。
- 注册：`_CORE_TOOLS` 加 `_with_capability(inspect_image_tool, "analysis", "external")`。

**白名单与 Profile**：`READ_ONLY_TOOL_WHITELIST` 显式并入 `inspect_image`（不进 explore/plan 默认集——通用探索器保持纯文本，只有法官 Profile 引用）；本仓库放 `.mini-code/agents/visual-judge.md`（allowed_tools: inspect_image, read_file, list_files, todo_write, subagent_note_*；正文=逐页严格 JSON 判决契约："只判不改"）——既 dogfood 又当示例。

**测试**（`tests/test_inspect_image.py`，HTTP 全 mock）：
1. 校验链：坏扩展名/坏魔数（改了扩展名的文本文件）/超限/路径逃逸/网络 URL 拒绝；
2. 配置 fail-closed：无 visionModel/visual 键 → 明确错误；缺 base_url/key/非 HTTPS → 明确错误；
3. 请求形状：data URI 正确、question 在文本块、temperature/max_tokens 如设；
4. 返回有界 + 用量行；HTTP 500 → 明确错误不崩溃；
5. 白名单/注册表/profile 三点贯通（visual-judge 类型可加载、子代理注册表含 inspect_image）。
E2E：ScriptedModel 子代理（类型 visual-judge）的注册表里 inspect_image 可见（视觉调用本身 mock）。

**Live 冒烟（1 次调用）**：脚本生成一张测试 PNG（PIL/手写最小 PNG），构造显式 runtime（visionModel 从现有子代理配置读 qwen-vl 系列——若用户 env 无该模型条目则如实标注未冒烟，不擅自改用户配置）。

## 任务与提交

1. **Task 1（TDD）**：工具 + 校验/配置/请求/返回测试。提交 `feat(tools): inspect_image read-only vision tool with fail-closed routing`。
2. **Task 2（TDD）**：白名单并入 + 注册 + `.mini-code/agents/visual-judge.md` + 贯通测试。提交 `feat(subagent): visual-judge profile over inspect_image`。
3. **Task 3**：live 冒烟（或如实标注）+ `docs/visual-judge-feasibility.md` 追加"已实现"节（用法、配置、边界）+ 全量回归 ≥ 10451 + 质量门。提交 `docs: visual judge shipped with usage and smoke result`。

## 验收

1. 新测试全绿；全量与门不低于基线。
2. 单测证明：所有校验/配置路径 fail-closed、请求形状正确、法官类型从加载到派发贯通。
3. live 冒烟结果（成功输出或明确的未执行原因）写入文档。

## Self-Review

- 关键决策：模型显式配置不回落默认（避免图发文本模型）；复用子代理凭据字段不新开通道；网络 URL 一律拒绝。✓
- 落点核实：`resolve_tool_path` 签名与意图参数、`_with_capability` 注册模式、白名单定义处。✓
- 花钱边界：仅 Task 3 的 1 次视觉调用（且仅在配置就绪时）。✓
