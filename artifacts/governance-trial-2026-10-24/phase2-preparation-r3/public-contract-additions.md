# 公开任务契约修订草案（第3版）

仅供review的启动后规格有效性修订；不是原预注册的一部分。当前r2的task/oracle/schedule没有修改，也未启动新的试次。

依据冻结tasks `1e134e338af2b19b78a54ded0ecf0bdcccdfc3c45803cf3c60bab5dc960e8cd5` 与manifest `32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332`。全部16题已审；未读取第4条以后的模型输出。

## 已解决的技术决策

- finalization初始状态只允许completed、cancelled_after_commit、timed_out、timed_out_after_commit、projection_failed。来源：历史reference commit `56d8cd85a14a22355bc2a981eaa479b0542ebb3f` 的 `minicode/workflow_finalization.py:150–157`，文件SHA `5d23b0e966806b648c36170e55eb449bef433739bd24c4bb66e07ed62d71c911` 与study source inventory匹配；当前已有workflow契约同集合。只引用接口校验，不向受测agent提供实现。
- frozen-change明确作为历史接口兼容维护题；公开既有观测/故障注入seams并保持oracle字节不变，不再以自由架构设计解释此题。

## 独立审查后的第3版修订

第2版收到ICR-1至ICR-4四项意见，原稿、standalone附录及changes_requested审查逐字节保留于contract-draft-history/v2。当前稿补齐图片八类事件与原始runtime分类、冻结副本注入调用形态、全部read的主线程/assert/70秒协议，并删除缓存题额外新增的缺失用量行为要求。尚待独立复核关项，不将作者修订自称为审查通过。所有原prompt、oracle及冻结tasks来源哈希均未变化。

## 用法

`public-task-appendices.json`的appendices含完整16段可独立追加文本；先核验原prompt哈希，再以两个换行追加对应段落。文件也给出预期不变的oracle哈希与拟追加prompt哈希。这只是可review产物，未应用、未freeze、未调用模型。

六题明确命名缺口、两题较弱约定的原分类不变；read题计数/覆盖协议属于本次扩展静态审查发现，不据此声称原预注册完整。

## read-router

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

入口本身公开可查；JUnit计数方式与精确调用门槛属于此前未完全明示的验收协议。不得提供facts答案或探针源码。

Public maintenance-task contract (historical interface compatibility)

最终响应只提交一个JSON对象，不添加前言、解释段落或独立代码块。保留题目给出的facts键和编码；probe_source是完整pytest模块的字符串，evidence是至少两个不同位置的{path,line,quote}记录。行号从1开始，quote须与该行精确匹配，并位于诊断实际调用的相关函数体内。场景数量按pytest收集后的独立testcase计算（参数化展开计数），不是按assert语句数量计算。下面列明入口的最小实际调用次数；允许通过公开上层入口间接调用。不得mock被检查的核心逻辑以伪造覆盖。当前源码上的全部case须通过，不得skip/xfail、导入/收集错误或真实联网；要求历史对照的题目，同一模块必须在历史实现上产生断言失败，单纯导入/收集错误不算。事实值、预期数值及探针实现由你根据当前源码判断，契约不提供答案。
诊断观测约定：用于上述调用次数及函数体evidence覆盖的核心实现调用，必须发生在运行pytest的主线程；计数器只在该线程安装sys.setprofile，不追踪工作线程。外围网络/模型mock可使用其他线程，但不能把全部计数目标移到线程池后仍要求该主线程观测器计数。probe_source在语法树上至少含一个Python assert语句；仅用pytest.raises而没有assert不符合此提交格式。每个当前/历史副本的诊断子进程分别最多70秒；这不增加整个外部验收阶段的已有时间上限。
本题最低独立pytest testcase数量：5。必须实际调用：minicode/agent_router.py:route_task至少5次；minicode/smart_router.py:route_and_switch至少1次。允许的证据path：minicode/agent_router.py, minicode/smart_router.py。

## read-deadline

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

入口本身公开可查；JUnit计数方式与精确调用门槛属于此前未完全明示的验收协议。不得提供facts答案或探针源码。

Public maintenance-task contract (historical interface compatibility)

最终响应只提交一个JSON对象，不添加前言、解释段落或独立代码块。保留题目给出的facts键和编码；probe_source是完整pytest模块的字符串，evidence是至少两个不同位置的{path,line,quote}记录。行号从1开始，quote须与该行精确匹配，并位于诊断实际调用的相关函数体内。场景数量按pytest收集后的独立testcase计算（参数化展开计数），不是按assert语句数量计算。下面列明入口的最小实际调用次数；允许通过公开上层入口间接调用。不得mock被检查的核心逻辑以伪造覆盖。当前源码上的全部case须通过，不得skip/xfail、导入/收集错误或真实联网；要求历史对照的题目，同一模块必须在历史实现上产生断言失败，单纯导入/收集错误不算。事实值、预期数值及探针实现由你根据当前源码判断，契约不提供答案。
诊断观测约定：用于上述调用次数及函数体evidence覆盖的核心实现调用，必须发生在运行pytest的主线程；计数器只在该线程安装sys.setprofile，不追踪工作线程。外围网络/模型mock可使用其他线程，但不能把全部计数目标移到线程池后仍要求该主线程观测器计数。probe_source在语法树上至少含一个Python assert语句；仅用pytest.raises而没有assert不符合此提交格式。每个当前/历史副本的诊断子进程分别最多70秒；这不增加整个外部验收阶段的已有时间上限。
本题最低独立pytest testcase数量：2。必须实际调用：minicode/model_call_control.py:bounded_request_timeout至少3次。允许的证据path：minicode/model_call_control.py。

## read-atomic-replace

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

入口本身公开可查；JUnit计数方式与精确调用门槛属于此前未完全明示的验收协议。不得提供facts答案或探针源码。

Public maintenance-task contract (historical interface compatibility)

最终响应只提交一个JSON对象，不添加前言、解释段落或独立代码块。保留题目给出的facts键和编码；probe_source是完整pytest模块的字符串，evidence是至少两个不同位置的{path,line,quote}记录。行号从1开始，quote须与该行精确匹配，并位于诊断实际调用的相关函数体内。场景数量按pytest收集后的独立testcase计算（参数化展开计数），不是按assert语句数量计算。下面列明入口的最小实际调用次数；允许通过公开上层入口间接调用。不得mock被检查的核心逻辑以伪造覆盖。当前源码上的全部case须通过，不得skip/xfail、导入/收集错误或真实联网；要求历史对照的题目，同一模块必须在历史实现上产生断言失败，单纯导入/收集错误不算。事实值、预期数值及探针实现由你根据当前源码判断，契约不提供答案。
诊断观测约定：用于上述调用次数及函数体evidence覆盖的核心实现调用，必须发生在运行pytest的主线程；计数器只在该线程安装sys.setprofile，不追踪工作线程。外围网络/模型mock可使用其他线程，但不能把全部计数目标移到线程池后仍要求该主线程观测器计数。probe_source在语法树上至少含一个Python assert语句；仅用pytest.raises而没有assert不符合此提交格式。每个当前/历史副本的诊断子进程分别最多70秒；这不增加整个外部验收阶段的已有时间上限。
本题最低独立pytest testcase数量：1。必须实际调用：minicode/conversation_turn_store.py:_write至少1次。允许的证据path：minicode/conversation_turn_store.py, minicode/deletion_store.py。

## read-closing-reminder

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

入口本身公开可查；JUnit计数方式与精确调用门槛属于此前未完全明示的验收协议。不得提供facts答案或探针源码。

Public maintenance-task contract (historical interface compatibility)

最终响应只提交一个JSON对象，不添加前言、解释段落或独立代码块。保留题目给出的facts键和编码；probe_source是完整pytest模块的字符串，evidence是至少两个不同位置的{path,line,quote}记录。行号从1开始，quote须与该行精确匹配，并位于诊断实际调用的相关函数体内。场景数量按pytest收集后的独立testcase计算（参数化展开计数），不是按assert语句数量计算。下面列明入口的最小实际调用次数；允许通过公开上层入口间接调用。不得mock被检查的核心逻辑以伪造覆盖。当前源码上的全部case须通过，不得skip/xfail、导入/收集错误或真实联网；要求历史对照的题目，同一模块必须在历史实现上产生断言失败，单纯导入/收集错误不算。事实值、预期数值及探针实现由你根据当前源码判断，契约不提供答案。
诊断观测约定：用于上述调用次数及函数体evidence覆盖的核心实现调用，必须发生在运行pytest的主线程；计数器只在该线程安装sys.setprofile，不追踪工作线程。外围网络/模型mock可使用其他线程，但不能把全部计数目标移到线程池后仍要求该主线程观测器计数。probe_source在语法树上至少含一个Python assert语句；仅用pytest.raises而没有assert不符合此提交格式。每个当前/历史副本的诊断子进程分别最多70秒；这不增加整个外部验收阶段的已有时间上限。
本题最低独立pytest testcase数量：5。必须实际调用：minicode/tools/task.py:_run至少5次。允许的证据path：minicode/tools/task.py。

## read-image-boundaries

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

入口本身公开可查；JUnit计数方式与精确调用门槛属于此前未完全明示的验收协议。不得提供facts答案或探针源码。

Public maintenance-task contract (historical interface compatibility)

最终响应只提交一个JSON对象，不添加前言、解释段落或独立代码块。保留题目给出的facts键和编码；probe_source是完整pytest模块的字符串，evidence是至少两个不同位置的{path,line,quote}记录。行号从1开始，quote须与该行精确匹配，并位于诊断实际调用的相关函数体内。场景数量按pytest收集后的独立testcase计算（参数化展开计数），不是按assert语句数量计算。下面列明入口的最小实际调用次数；允许通过公开上层入口间接调用。不得mock被检查的核心逻辑以伪造覆盖。当前源码上的全部case须通过，不得skip/xfail、导入/收集错误或真实联网；要求历史对照的题目，同一模块必须在历史实现上产生断言失败，单纯导入/收集错误不算。事实值、预期数值及探针实现由你根据当前源码判断，契约不提供答案。
诊断观测约定：用于上述调用次数及函数体evidence覆盖的核心实现调用，必须发生在运行pytest的主线程；计数器只在该线程安装sys.setprofile，不追踪工作线程。外围网络/模型mock可使用其他线程，但不能把全部计数目标移到线程池后仍要求该主线程观测器计数。probe_source在语法树上至少含一个Python assert语句；仅用pytest.raises而没有assert不符合此提交格式。每个当前/历史副本的诊断子进程分别最多70秒；这不增加整个外部验收阶段的已有时间上限。
本题最低独立pytest testcase数量：7。必须实际调用：minicode/tools/inspect_image.py:_run至少7次。允许的证据path：minicode/tools/inspect_image.py。 本题不使用不存在的前功能实现作为历史反例。必须从真实inspect_image返回结果中观察以下八类事件，不能仅在测试文字中描述：网络URL拒绝（network_url_rejected）、不支持的扩展名拒绝（unsupported_image_extension）、伪装图片内容拒绝（not_an_image）、超大小图片拒绝（image_too_large）、未配置任何视觉模型、已配置视觉模型但无凭证、已有模型与凭证但地址不是HTTPS，以及成功的仅visual角色路由。后三个配置拒绝事件使用工具现有vision_model_unconfigured结果，分别在下述原始runtime状态下观察；保留工具真实ok/输出，不伪造结果。
配置观测直接读取调用时context._runtime，不使用规范化后的有效配置：hasModel由visionModel或字典subagentModels中的visual真值确定；hasKey由subagentApiKey真值确定；httpsRoute按字符串subagentBaseUrl.startswith("https://")确定。缺模型场景要求hasModel为false；缺凭证场景要求hasModel为true且hasKey为false；非HTTPS场景要求hasModel、hasKey均为true且httpsRoute为false。成功visual-only场景要求subagentModels是字典且visual为真，同时visionModel为空，并观察真实成功结果；请求本身必须mock为离线。可以使用无效占位凭证，不能提供或读取真实秘密。八类事件需要分别观测，但原有数值下限保持至少7个独立pytest testcase、_run至少7次；一个case可以包含多次真实工具调用。

## write-cache-tip

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

既有adapter接口及缓存标记保留；oracle经既有next接口观察请求体。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

保留 minicode.anthropic_adapter.AnthropicModelAdapter 与 _to_anthropic_messages 现有签名。缓存标记采用既有请求格式 cache_control: {type: "ephemeral"}；此前系统/工具标记以及原有退避路径保持兼容。新增会话标记只占最后一条消息的最后一个content块，适用于工具结果块；剔除标记元数据后，既有消息前缀的内容结构须保持相同。不要更改调用者传入的消息或缓存的工具schema。

## write-cache-fallback

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

公开要求disable flag/kill switch，但确切运行时键promptCache和环境变量MINI_CODE_PROMPT_CACHE在公开prompt与初始全文件均缺失；oracle严格写这些键。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

在现有 AnthropicModelAdapter 构造与 next 调用契约内实现功能。运行时配置键是 promptCache：布尔false禁用；环境变量是 MINI_CODE_PROMPT_CACHE：字符串"0"禁用。任一禁用条件生效时，请求不得携带cache_control；均未禁用时启用缓存。系统与最后一个工具块的标记形状为 {"cache_control":{"type":"ephemeral"}}，空系统继续允许原来的空字符串/None/空列表表示。缓存相关HTTP 400的现有退避路径只重试一次，并去掉请求内缓存标记；保留其他transport语义。把provider输入/输出与cache creation/read计数映射到现有usage字段（包括cache_read_tokens、cache_creation_tokens和source），保留初始实现对缺失用量子字段与source的既有处理语义。除此处明定的禁用值之外，若支持额外配置别名，不能改变上述行为。

## write-todo-isolation

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

公开要求每context隔离，未指定以ToolContext(_todo_state={tasks,counter})注入；初始ToolContext无该字段，oracle直接构造并检查传入字典。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

ToolContext需要兼容关键字 _todo_state: dict | None = None。非None时，它是此context的todo状态容器，结构为{"tasks": list[现有todo记录], "counter": int}；调用者可持有该字典并观察更新，不能把写入只放到无关的新容器。不同context传入的不同容器互不共享；同一context的后续todo_write按既有替换语义更新。未提供该容器（None）时保留既有模块级_tasks/counter兼容路径。每个agent loop显式分配自己的容器。保留todo_write_tool和现有todo输入/输出记录结构；READ_ONLY_TOOL_WHITELIST及explore/plan允许集、child registry都应包含todo_write。字段名是兼容需求，内部状态管理算法不指定。

## write-user-profiles

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

既有load/resolve接口可见；新user_root=关键字在全部公开初始文件与prompt均不存在。来源错误也被精确约束为user:filename，而公开只要求保留origin。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

保留已有profile条目、校验和返回结构。扩展两个现有入口：load_agent_profiles(project_root: str | Path, *, user_root: str | Path | None = None) -> tuple[dict[str, dict], list[str]]；resolve_agent_types(project_root: str | Path, *, user_root: str | Path | None = None) -> dict[str, dict]。user_root省略或为None时使用Path.home()；其配置目录也是 .mini-code/agents。项目与用户目录不同名合并，同名项目优先，built-in名称保持权威。加载错误须携带来源和文件名，用户错误采用可识别前缀user:<filename>，项目错误采用project:<filename>。不得把用户目录访问要求理解为可读取实验隔离HOME之外的个人资料；测试通过显式user_root或受控Path.home完成。

## write-profile-parser

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

新增模块及其历史测试均不在公开checkout；四个固定导出名无公开引用，oracle在收集期import全部四名；load函数返回结构也由隐藏测试固定。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

新增 minicode.subagent_profiles 模块并导出以下四名：AGENT_NAME_RE、RESERVED_AGENT_NAMES、load_agent_profiles、resolve_agent_types。AGENT_NAME_RE提供fullmatch；名称为2至32个ASCII字符，首字母a-z，其余a-z/0-9/连字符。RESERVED_AGENT_NAMES是包含现有explore、plan、general、workflow的只读集合。
入口签名：load_agent_profiles(project_root: str | Path) -> tuple[dict[str, dict], list[str]]；resolve_agent_types(project_root: str | Path) -> dict[str, dict]。加载结果以profile slug为键。条目保留description: str、system_prompt: str、allowed_tools: frozenset[str]、max_turns: int、source: "project-profile"，与既有agent type消费结构兼容。resolve入口把合法条目与现有AGENT_TYPES合并并保留built-in条目。
输入位置是<project_root>/.mini-code/agents/*.md；其他扩展名忽略。frontmatter使用独立---首尾界定和key: value行，仅允许且要求name、description、max_turns、allowed_tools四键，重复/缺失/未知键都拒绝该文件。name遵守上述slug规则；description非空且最多400字符；max_turns为1至200的十进制整数；allowed_tools是非空、以逗号/空白分隔的现有READ_ONLY_TOOL_WHITELIST子集。正文去首尾空白后非空，作为system_prompt。每个非法文件产生带文件名的错误，不能静默收窄非法tools或turns；错误应能识别非法工具名。缺目录返回空字典与空错误列表。允许缓存，但修改文件mtime/内容后必须重新反映profile名称变化。以上边界来自本研究其他冻结初始版本已公开的profile接口，仅公布数据契约，不提供解析器或缓存实现。

## write-profile-dispatch

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

profile解析接口和task既有入口均公开；需把现有profile与dispatch连接。未发现同等强度的新名称缺口。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

继续使用当前task_tool.run/_run/_validate，以及已公开load_agent_profiles/resolve_agent_types入口。自定义profile通过agent_type字段选择；现有profile提供的system_prompt、allowed_tools、max_turns传递到child执行。保留task模块的run_agent_turn可替换执行边界。非法slug由_validate以ValueError拒绝；未知类型的工具结果需包含请求的名称、可用类型和profile加载错误文件名。保留_call_is_concurrency_safe(input_data)与_capability_for(input_data)接口，合法custom只读类型的能力与并发分类须可观察，不能扩展general的写权限。

## write-profile-contracts

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

三个既有函数、关键字和类型约束可见；新增的是允许已有profile slug语义。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

沿用初始源码中 project_subagent_result、resolve_subagent_model_route、project_subagent_event 的现有签名、返回键和异常类型。自定义名称采用已经公开的profile slug规则，不能只放宽为任意非空字符串。没有custom专属路由时继承父模型配置；存在显式配置时使用既有路由机制。结果与观测仍携带既有agentType、contractStatus/结果状态和计数字段，保留非法名称的ValueError边界。无需另造新导出函数或改变记录schema。

## refactor-finalization

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

新workflow_finalization.py未提供；oracle直接创建私有Snapshot/Ports和字段、调用finalize_workflow；这些名称在公开输入均缺失。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

新增 minicode.workflow_finalization，并保留以下兼容入口（下划线名称仍是本次维护任务的测试接口）：_PhaseResultSnapshot.from_result(result: ToolResult)、_WorkflowFinalizationSnapshot.capture(**capture_fields)、_WorkflowFinalizationSnapshot(**snapshot_fields)、_WorkflowFinalizationPorts(checkpoint=..., persist_audit=..., observe=...)、finalize_workflow(snapshot, ports)。
_PhaseResultSnapshot.from_result只接受具体ToolResult类型，不能接受其子类；类型不符以TypeError拒绝。Workflow快照须为dataclass，使dataclasses.fields(snapshot)可枚举其构造字段；字段读取和直接重建应使用脱离原可变输入的值。capture_fields名单见本附录的字段表；必须包含initial_finalization_status与commit_error_text。commit_error_text只接受str或None，不能保存Exception对象；非法类型以TypeError拒绝。initial_finalization_status只允许completed、cancelled_after_commit、timed_out、timed_out_after_commit、projection_failed五个字符串；非法值以ValueError拒绝。
Ports只提供checkpoint()、persist_audit(audit)、observe(observation)三个明确能力；保留未提交阶段持久化audit触发的TurnCancellationRequested身份并向调用者传播，不能转成普通失败或吞掉。finalize_workflow负责输出与已有runner相同的最终结果语义。上述状态集合沿用既有workflow契约；执行中状态不是可接受的初始finalization状态。

capture输入字段契约：
- description、subagent_id、contract_mode：既有描述、身份和契约模式字符串。
- started_at：既有开始时间数值。
- profile、dispatch_plan、scheduler_outcome：现有scheduler对应类型对象，capture须脱离可变执行能力。
- transitions、artifacts、explore_results：现有对应序列，快照不得共享可变容器。
- plan_result、execute_result、review_result：对应ToolResult或None，遵守具体类型边界。
- analyst_report、execution_artifact、verification_result、typed_review：现有对应文档/可选值，保持schema并进行脱离。
- approval_request_id：str或None；commit_ready_status：现有状态字符串；commit_error_text：str或None；production_audit_required：bool。
- initial_finalization_status：completed、cancelled_after_commit、timed_out、timed_out_after_commit或projection_failed。

## refactor-workflow-runner

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

prompt完整列出run_workflow五参数和关键字约束。隐藏测试另要求__all__精确tuple、task模块有可monkeypatch的run_workflow同名绑定；实现为模块属性调用也可满足公开行为但不满足此测试。次级约定，不与完全未知API混为一谈。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

新模块 minicode.workflow_runner 的__all__精确为("run_workflow",)。入口签名为run_workflow(input_data, context, *, depth, subagent_id, run_subagent)，顺序与keyword-only边界如列。task模块保留一个同名run_workflow绑定作为dispatch seam；调用时传入当时task模块的_run引用，因此替换该运行器会在下一次dispatch生效。
workflow_runner不能反向import minicode.tools.task。完成抽取后tools.task不能直接import以下workflow执行模块：minicode.central_scheduler、minicode.deterministic_verifier、minicode.scheduler_artifacts、minicode.scheduler_audit、minicode.scheduler_trace、minicode.workflow_workspace。此处是公开的模块依赖边界；内部调用组织与业务算法不指定。保留原task_tool入口、返回语义与深度/subagent身份传递。

## refactor-frozen-change

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

已有WorkflowWorkspaceTransaction/_FrozenWorkflowChangeSet可见，但新FrozenWorkflowChange、freeze/promote与属性/关键字、确切错误文案均由隐藏测试固定；公开只描述能力语义。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

在 minicode.workflow_workspace 保留 WorkflowWorkspaceTransaction、WorkflowIsolationError，新增导出 FrozenWorkflowChange。transaction.freeze(*, allowed_paths) -> FrozenWorkflowChange是唯一签发入口；直接FrozenWorkflowChange()须TypeError拒绝。保留transaction.cwd/workspace_root/base_digest/close()和原有父提交状态属性。
FrozenWorkflowChange提供patch_digest: str、change_set_document: dict、diff_artifact_document: dict、verification_workspace(*, retain_git_metadata=False)上下文管理器，以及promote(*, approved_patch_digest: str)。change_set_document沿用既有文档键workspaceRef/baseDigest/patchDigest/files，其中workspaceRef固定为"isolated://workflow"，files使用现有path/action结构；两类document每次交付为脱离内部权威的副本。原transaction的change_set/frozen_diff_artifact/verification_workspace/commit不再暴露为第二条变更权威通道。批准摘要、父状态与原事务兼容约束仍按原任务执行。
可审计错误类别保持WorkflowIsolationError；稳定错误文本见error contract表。本题明确测试既有历史接口兼容，不是自由选择架构的设计题。以下观测/故障注入seam属于必须保留的兼容面：frozen._frozen_workspace_root是可定位冻结副本的Path；事务保留原有_git_args/_git_environment/_run_checked/_trusted_git_root/_baseline，并保留_copytree_with_checkpoints(self, source, destination, **kwargs)注入边界。workspace模块必须保留可替换的tempfile模块绑定；冻结副本通过tempfile.mkdtemp(prefix="frozen-workspace-", ...)分配，promotion元数据通过tempfile.TemporaryDirectory(prefix="promotion-git-", ...)分配。调用者能够在这些边界注入分配/复制失败，并通过上述路径定位副本以验证篡改后的拒绝/恢复行为。验收不更换；这里只公开其现有兼容要求，不提供复制、摘要、Git隔离、验证或promotion算法。
故障注入的调用协议也属于本题的历史兼容面：通过_copytree_with_checkpoints复制冻结副本时，destination必须是所分配临时目录的子路径，使destination.parent.name.startswith("frozen-workspace-")成立，而不是直接复制到分配根目录。该边界注入的既有WorkflowIsolationError须保持异常类型与原文本向外传播；不要把它统一改写为分配错误，OSError仍按前述错误契约处理。执行真实父仓库promotion时，继续经过事务原有_run_checked边界：参数序列包含apply且不含--check，并显式传入use_operation_guard=False；预检查不能替代该真实应用调用。这样允许现有验收在真实应用后注入父状态破坏，随后依既有公开行为检查最终父状态。该约定描述观测边界，不提供Git操作或恢复算法。

稳定错误文本（允许附带上下文，但须保留辨识片段）：
- created by WorkflowWorkspaceTransaction.freeze
- workflow change set was already frozen
- trusted Git metadata changed after initialization
- workflow frozen snapshot could not be created
- workflow patch digest changed after verification
- workflow parent state differs after promotion
- workflow promotion metadata could not be created
- workflow frozen change owner is unavailable

## refactor-workspace

Public prompt, all merged initial files, whole fixture imports, selected tests and their fixture/helper dependencies; exact fixed names, constructor/keyword forms, return structures, error matching and observer coupling. No later model outputs.

旧_TaskDeadlineExceeded/_WorkflowWorkspaceTransaction/_WorkflowIsolationError与兼容行为可见，新模块路径已给。去掉下划线作为导出名是合理可推导约定但未唯一指定；oracle固定这一命名，弱于全新无上下文API缺失。

Public maintenance-task contract (historical interface compatibility)

保留原题行为目标、授权路径、禁止事项与回归要求。下面的名称、签名、数据表示和错误接口是兼容契约，不是建议实现。新增模块可以自行选择内部算法，但不得用等价而不同名的接口替代下列被调用的入口。所有运行时/模型/网络效果必须在离线回归中隔离。交付回归必须位于 tests/test_eval_regression.py，且至少包含一个可收集、正常执行的 pytest case；不能以skip/xfail或收集错误作为验证成功。验收会在交付副本上运行，既有源文件与新测试均受授权范围约束。

新模块 minicode.workflow_workspace 导出WorkflowWorkspaceTransaction与WorkflowIsolationError；minicode.task_deadline导出TaskDeadlineExceeded。tools.task保留_WorkflowWorkspaceTransaction、_WorkflowIsolationError、_TaskDeadlineExceeded，分别是三个新导出类型的同一对象，不是包装子类。TaskDeadlineExceeded(timeout_seconds: float)保留timeout_seconds属性与既有异常文本格式“sub-agent deadline exceeded after {timeout_seconds:g}s”。workspace模块不得import minicode.tools.task。只移动责任和共享类型，保留原事务入口/异常与行为语义。

## 新注册前的review条件

- User scope decision and explicit disposition of r2; retain its raw outcomes without replacement.
- Independent review that published interface/behavior contract covers every fixed grading dependency, including injection seams.
- The enum and frozen-change compatibility decisions are resolved in this draft. Apply all approved appendix text uniformly before registration; do not silently alter an oracle.
- Freeze public contracts and oracle versions together for all tasks/arms before any new model execution; record all new hashes and baseline identity.
- Validate task/oracle mechanics offline without exposing reference implementations to the evaluated agents; disclose study authorship and post-start nature of the amendments.

原始规格审查保留于 /private/tmp/phase2-analysis/；本目录文件是修订草案。六强/两弱只表示确切接口可发现性，并不表示行为目标缺失；后续新实验仍需用户方向决定、独立规格/判据复核和完整新冻结身份。
