#!/usr/bin/env python3
"""Successor engineering gate and regression evaluation; historical runs stay frozen.

Only this entry point changes the collaboration recipe. The original runner is
loaded into a private module, retaining its production initialization, permissions,
durable transport, external oracle and process guardian without changing its file
or the normal imported module. No provider call is made by prepare/freeze/gate.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time
from types import FunctionType, ModuleType, SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import run_three_arm_real as shared  # noqa: E402
from scripts.implementation_judge import JUDGE_POLICY, run_judgement  # noqa: E402
from scripts.public_verification_fixtures import (  # noqa: E402
    PUBLIC_FIXTURE_POLICY, bind_public_fixtures, bind_verifier_public_inputs, public_fixture_manifest,
    fixture_receipt_writer, run_public_preflight, validate_public_preflight,
)

DEFAULT = ROOT / "artifacts/implementation-progress-2026-10-14"
SOURCE = ROOT / "artifacts/three-arm-real-2026-10-13"
PROTOCOL_VERSION = "direct-workflow-regression-v1"
SMALL_CASES = (
    "real-vision-budget",
    "real-remediation-context",
    "real-concurrent-context",
)
FORMAL_ARMS = ("single", "auto", "explicit")
SMALL_ARMS = ("single", "explicit")
SEED = 20261014
IMPLEMENTATION_CAPABILITIES = "shared-public-checks-source-context-and-fixtures-v2"

# Historical bounded evaluator metadata; this is not a production runtime cap.
# The auditor reads this literal when production no longer declares a ceiling.
_LEGACY_ROLE_OVERRIDE_CEILING = 200
ROLE_TURN_POLICY = {
    "version": "configured-overrides-shared-executor-v1",
    "overridePrecedence": "process-env-before-private-env;invalid-present-env-shadows-private",
    "overrideRange": [1, 200],
    "defaultStructuredExecutor": "remaining-shared-physical-calls-minus-followup",
    "configuredStructuredExecutor": "min(general-configured-cap-plus-one,remaining-minus-followup)",
    "invalidConfiguredValue": "production-default-as-configured-cap",
    "unreadablePrivateStore": "production-default-as-configured-cap",
    "unboundedSharedExecutor": "resolved-general-plus-one",
    "followup": "one-parent-response-plus-mandatory-reviewer-resolved-plan-plus-one",
    "assignmentIncludesClosingPhysicalCall": True,
    "sharedBudgetsUnchanged": True,
}


def role_turn_configuration() -> tuple[dict, dict]:
    """Freeze the legacy bounded evaluation policy, not production defaults.

    Historical schema-4/5 trials explicitly use their finite role policy.
    Continuous diagnostics use a separate manifest and never call this policy.
    """
    from minicode import subagent_types
    from minicode.config import MINI_CODE_ENV_PATH
    from minicode.env_file import read_private_env_file

    if not callable(getattr(subagent_types, "configured_max_turns", None)):
        raise ValueError("schema 4 requires configured production role overrides")
    roles = ("explore", "plan", "general")
    names = {role: f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS" for role in roles}
    if subagent_types._MAX_TURNS_ENV_NAMES != names:
        raise ValueError("production role override contract changed")
    # Preserve this evaluator's frozen precedence and invalid-value fallback.
    # Read the trusted private store only for missing knobs, never other secrets.
    missing = {name for name in names.values() if name not in os.environ}
    private_unreadable = False
    try:
        private = (
            read_private_env_file(MINI_CODE_ENV_PATH, allowed_keys=missing)
            if missing
            else {}
        )
    except Exception:
        # This bounded evaluator keeps its historical finite fallback. The
        # production resolver instead reports an unreadable explicit policy.
        private_unreadable = True
        private = dict.fromkeys(missing, "")
    defaults, resolved, bindings, overrides = {}, {}, {}, {}
    for role, name in names.items():
        default = subagent_types.AGENT_TYPES[role]["max_turns"]
        if type(default) is not int or default <= 0:
            raise ValueError("invalid production role default")
        source = (
            "process_env"
            if name in os.environ
            else "private_env_unreadable"
            if private_unreadable
            else "private_env"
            if name in private
            else "default"
        )
        raw = os.environ[name] if name in os.environ else private.get(name)
        value = None
        if raw is not None:
            try:
                parsed = int(raw.strip(), 10)
                if 1 <= parsed <= _LEGACY_ROLE_OVERRIDE_CEILING:
                    value = parsed
            except ValueError:
                pass
        if raw is not None:
            overrides[role] = default if value is None else value
        defaults[role] = default
        resolved[role] = default if value is None else value
        bindings[role] = {
            "source": source,
            "override": "valid"
            if value is not None
            else "absent"
            if raw is None
            else "invalid",
        }
    return overrides, {
        **ROLE_TURN_POLICY,
        "defaults": defaults,
        "resolved": resolved,
        "bindings": bindings,
    }


def validate_role_turn_configuration(manifest: dict) -> None:
    # Historical schema 3 remains readable by the independent auditor, but is
    # executable only through its own frozen entry point. A current archive
    # cannot disable these guards by relabelling its manifest as schema 3.
    if manifest.get("schemaVersion") != 5:
        raise ValueError("this successor runner requires manifest schema 5")
    overrides, policy = role_turn_configuration()
    for key, observed in (("roleTurnLimits", overrides), ("roleTurnPolicy", policy)):
        # Python equality conflates bool/int/float; their environment strings
        # have different production parsing, so keep JSON types exact here.
        if json.dumps(manifest.get(key), sort_keys=True) != json.dumps(
            observed, sort_keys=True
        ):
            raise ValueError(f"frozen role configuration changed: {key}")


@contextmanager
def frozen_role_turn_overrides(manifest: dict):
    """Pin knob resolution only inside one private runner's trial lifecycle."""
    validate_role_turn_configuration(manifest)
    from minicode import subagent_types

    values = dict(manifest["roleTurnLimits"])
    names = {name: role for role, name in subagent_types._MAX_TURNS_ENV_NAMES.items()}
    original = subagent_types._read_max_turns_override

    def frozen_read(name: str) -> str | None:
        if name not in names:
            return original(name)
        value = values.get(names[name])
        return None if value is None else str(value)

    # No default role value is inserted into os.environ. This includes general:
    # production can still distinguish no override from an explicit default=15.
    # Workflow threads use this same module; the child interpreter owns this
    # narrowly scoped patch and restores it even if trial execution raises.
    with patch.object(subagent_types, "_read_max_turns_override", frozen_read):
        yield


# A literal list lets an independent auditor inspect the archive without executing it.
SUCCESSOR_HARNESS_FILES = (
    "run_three_arm_real.py",
    "evaluation_transport.py",
    "three_arm_protocol.py",
    "three_arm_scope.py",
    "three_arm_scope_metadata.py",
    "three_arm_processes.py",
    "run_north_star_live.py",
    "run_multi_agent_cost_live.py",
    "analyze_three_arm_real.py",
    "run_implementation_progress_live.py",
    "audit_three_arm_real.py",
    "audit_implementation_progress.py",
    "analyze_implementation_progress.py",
    "implementation_judge.py",
    "public_verification_fixtures.py",
)

REGRESSION_REQUIREMENT = """

共同交付要求（所有组相同）：必须创建 tests/test_eval_regression.py，至少含一个会实际执行的回归测试。
最终父进程会在交付源码副本上单独运行该文件，不继承公开命令的 -k/-m 筛选；每个收集到的测试都须完成 setup、call、teardown 并通过。
失败、错误、skip、xfail、xpass、空文件、缺失文件、零收集或超时均不满足交付。请在本次执行预算内先运行新测试并修复。
隐藏与既有测试、新增回归测试都通过才算任务成功；共同外部裁判总共最多120秒，单列耗时，不增加你的1800秒执行预算。
"""

EXPLICIT_INSTRUCTION = """\n\n执行方式（本组的固定协作协议）：
主 Agent 只负责派发与汇总，工具仅有 task 和 todo_write；源码修改及验证交给实际工作流。
第一步直接用 task 调用 agent_type='workflow'、action='run'，同步完成本题实现。
在 prompt 中简洁、完整转述本题目标与验收要求，遵守工具 schema 的字符上限；
准确传入宿主授权 write_paths 与公开 verification_commands，不加入参考答案或隐藏测试。
不要先派 plan/explore 或重复分析；工作流根据生产策略决定是否需要 Analyst，允许合法低风险路径直接进入 Executor。
让同一工作流完成实现、验证与有界修复。若参数被拒绝，可以修正参数后重试；不要把实现推回主 Agent。
收到工作流实际回执后汇总交付结果，失败则如实说明。所有模型角色、等待和重试共用本次预算。
"""

ANALYSIS_PLAN = """# 实现进展修复后的工程门与回归对照

版本 direct-workflow-regression-v1。历史实验只读保留；新目录内 engineering 与 formal 完全分离，不混合数据。
2026-10-16 起按用户授权将执行时间改为1800秒、请求上限300秒、空闲上限120秒；模型、500000 tokens和40次请求不变。旧600秒试次保持原冻结条件，新旧时间配置不合并为同预算对照。
本次延时诊断先仅运行 real-remediation-context 的 explicit 一条；完整交付通过后才运行相同配置的 single。该单条诊断不代表下述六格工程门或正式对照已经完成。
2026-10-16 续修调度修复：真实活跃 Executor 的后续修复在同一工作流、候选和模型会话内进行，宿主按保留上下文投影、共享预算与原下游预留判断是否能继续，不再只由一次正式修复上限决定。新 Assignment 的完整请求仍在原账本重新计价并原子准入；末轮输出最多4096并按实际可用余量缩小。quote只是可行性估算，不承诺下一请求必然能发出；每个新候选仍须重新通过正式验证、审查及回写门禁。诊断记录保留准入拒绝的输入估计、预留和局部额度。
本轮评价的是修复后的生产系统及固定评测传输/审批条件。统计单位仍为任务，不能把重复试次当独立任务。

## 工程验证先行

固定选择 real-vision-budget、real-remediation-context、real-concurrent-context，分别 single 与 explicit 各运行一次，共6条；不按结果替换、补选或只重跑失败组。
门槛在运行前固定：6条全部终态、独立审计有效、清理完成、写范围通过、所有物理请求的输入/输出及缓存用量完整；至少4/6通过隐藏独立oracle和共同新增测试验收，且存在实际授权非测试生产源码变化，每组至少2/3、每题至少1/2成功；3条explicit全部具有实际Executor经所属workflow送达的宿主证据。
源码变化以工作区与冻结基线的真实差异判断，只改测试不满足工程成功，裁判自身引入的变化不充当交付。oracle、权限与机制门分别记录；模型自报、工具请求或workflow容器本身不等于交付。
门不过不启动正式48次。修复后重试须建立新目录、重新冻结，保留此前失败证据。本版工程验证先运行remediation的explicit，完整交付后再运行single配对；两组均完整交付后才补跑其余4条。未运行格保持未运行，2格仅作partial审计，不算完整六格工程门通过。完整门仍要求全部6条及原成功门槛。工程门不作收益或显著性结论。

## 正式回归对照

工程门通过后，另目录运行原8个历史缺陷任务×2次重复×single/auto/explicit三组，共48条。校准题重用明确披露；这是项目内回归对照，不能称独立泛化集。历史试次和工程试次不进入正式分母。
single禁task但保留共同本地读写验证工具；auto采用生产自主调度；explicit父Agent仅task/todo_write，直接workflow，无外部Plan。workflow内Analyst按生产风险路由可选，Executor必须真实执行并经自己的workflow回到父Agent。同一workflow可以有有界修复叶子。无效参数修复不被当成真实分析。
实际叶子需至少一个completed provider响应。结果送达需持久宿主task_result_owner_ack与所属workflow时间/父ID关系；失败回执也能证明机制交付，不能冒充代码通过。新增externalLeafRuns记录不属于workflow的真实叶子，新explicit协议要求其为0。
正式完整成功=oracle通过、共同新增回归测试通过且scope通过；explicit另需协议通过。工程门的非空源码条件不会悄悄加入正式成功公式。所有失败、超时、拒绝和用量缺失全部保留。explicit协议覆盖目标100%，低于80%阻断机制收益结论，不筛选成功委派子集。

## 同模型、预算与权限

各组均为相同GLM-5.3-Flash路由，temperature=0；所有父/子共用500000 token估算准入、40次物理请求、1800秒执行硬截止；每请求最多8192输出，连接/空闲/总请求30/120/300秒，内部HTTP重试0。生产阶段在原上限内分配调查/实现/收尾，调查最多6次请求和初始20%额度；聚焦读取不再设额外8批配额，仍受真实全局预算和原路径/单批读取约束。支持low/high的路由在首次实际修改前的实现阶段使用high，修改后/调查/收尾使用low，账本记录实际档位。精确测试任务初始预留至多3轮收尾、每轮最多4096输出；真实宿主检查仍失败且原预算足以支付一次修复和一次交接时，按当前上下文与观测增长逐轮重算修复机会，不恢复调查工具；绝对交接时间边界和原总调用/token上限保持，交接已准入后不再开工。最后一轮单独供结构化提交（Executor）或普通最终答复（主Agent），可选测试不能消耗此轮；收尾前几轮已绑定实际自动检查的执行者可完成首次源码修改，已有真实失败检查时可继续源码修复，最后一轮仍只交接；收尾请求使用配置的单次请求时间，再受owner与固定交接截止约束，不按剩余槽再次切短；有条件交接可在同一响应中顺序修改、运行宿主检查并submit，但completed仍须当前候选的全部真实检查通过，失败或缺失证据则拒绝；估算token预留包含观察到的上下文增长，预算不足先减少可选测试轮。测试执行另在原deadline内预留最后答复时间，不扩任何全局预算。
执行角色共用宿主绑定的ImplementationSpec：来自原授权write_paths、公开测试argv和原任务目标；启动时通过静态AST按完整语句和相关性提供有界真实源码、公开测试/fixture和必要本地API签名；所选AST片段不在调用/分支中途截断，不用无关导入挤占实际依赖预算，不读取隐藏验收或参考答案。主Agent直接修改与Executor修改均自动得到隔离副本的真实公开测试反馈，并可用同样的executor_check/executor_test完成既定测试；工具不授予新增路径/命令权限。explicit父协调器不预取源码，Executor同样接入。宿主回写仍需原正式Verifier、审查和基线校验，基础反馈不能授权回写。失败候选和子工具结果持久化供复核，失败回执明确父工作区未回写及候选仅供诊断，不冒充交付或自动恢复到父工作区。Executor的blocked/failed交接仅规范化重复/过长诊断，保持步骤、状态及completed契约严格；父失败回执附真实实现期检查摘要、对应patchDigest与提交校验错误，明确区别正式Verifier。直接执行者若最后已知公开检查失败，宿主追加未完成回执，明确本轮已结束，保留原模型答复和工具证据；不增加模型请求或伪造检查。请求账本保留所有重试/失败用量；生产5 USD保护不变，未知用量不填零。
父Agent自己的普通请求也在准入时为当前输出及一次无工具最终答复保留token，使用当前上下文、最近provider输入及新增上下文校准；与实现阶段尾款取最大值而非重复计费，实际最后答复只保留自身输出。宿主预算耗尽作为类型化结果传过workflow，不被通用错误恢复包装成可重试故障。取消后已返回的工具诊断在取消检查前保存；普通子进程的已落盘工具诊断由父端从有界固定传输文件回收，不授予子进程父日志权限。JSON字符串密钥和上游截断值在私有持久化前脱敏。父Agent派发前按实际上下文、推理、当前调用、本批有界工具结果和最终答复估算交接预留；Executor与同workflow修复沿用该floor，父子仍共享原总预算。IMPLEMENT的目录加精确include归一到原权限内已有单文件，不递归同名文件、不增加读取次数配额。验证沙箱保持原保护目录清理；只有已登记的历史公开visual-judge fixture由宿主从冻结任务归档投影到一次性验证副本，核验前后摘要并单独绑定证据回执，不复制运行配置、不授予父/子新增路径权限，不声称chmod是OS写保护。冻结前以实际宿主verifier能力运行全部任务原公开验证命令；基线预检失败则禁止付费执行。
沿用原runner工作区快照、权限scope和独立oracle来源；skills/profile/记忆隔离、无MCP/网络工具。仅预授权源码及tests/test_eval_regression.py可写；仅公开pytest argv及新增回归测试命令可执行。三组共同工具完全相同，子角色生产allowlist保留；explicit父角色例外如上。
非空宿主审批意味着未覆盖纯headless无审批的direct-edit额度耗尽路径；transport wrapper是实验条件，不能声称生产默认HTTP性能。开始最多30秒准备，执行硬截止后冻结并清理进程，再由父进程在两份交付副本分别运行原oracle及 tests/test_eval_regression.py。共同120秒裁判截止包含复制、测试、receipt及进程清理；新增测试不继承-k/-m/addopts，至少1项且所有收集项setup/call/teardown完整通过，任何失败/error/skip/xfail/xpass、缺失、零收集、超时或清理失败均不通过。所有组可见同样要求；oracleSuccess仍只表示原oracle行为验收，regressionTest另记counts/状态/耗时/清理。验证不能修改原始交付再冒充修复；receipt+退出码+进程清理共同判定，不信XML或终端自报。本裁判针对非对抗生成测试，不是任意恶意Python沙箱；裁判时间单列，不进入1800秒Agent执行预算。

## 正式统计与费用边界

配对按任务与重复，组别次序交错随机且运行前冻结，串行运行。按每题每组跨2重复均值作任务层配对分析；主family是explicit−single的完整成功、执行耗时、完整API等价费用3对比；auto−single及explicit−auto的相同指标6个次要对比构成另一个Holm family。alpha=.05，缺费用对比保留family大小但输出null。
沿用20000次task-level percentile bootstrap，种子20261013；双侧精确符号翻转枚举2^8，报告差、95%区间和均值比。bootstrap区间不是Holm同时区间，不显著或全零区间不证明等效。没有新增成功子集、事后指标或显著性检验。
耗时为全部试次Agent执行墙钟，不能把请求耗时相加证明提速，也不等于成功交付速度。各组描述成功/总数、oracle、mean/sampleSD/P50/P95时间、物理调用、token、角色与送达覆盖。
输入/输出缺receipt时token只能报已知下界；缓存属于输入子集。精确费用必须各物理请求输入/输出及cache完整；一对组双方全部试次费用完整才计算费用比较。禁止用下界差声称省钱或将缺失填零。
费用使用冻结PRICE的标准API等价单价，Coding套餐并非实际增量扣费。正式输出需要全48终态与独立有效审计。全部计划、生产.py、新旧复用harness、任务、路由和预算冻结哈希；正式中不得改配置或挑样本。
"""


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm not in FORMAL_ARMS:
        raise ValueError("unknown evaluation arm")
    return (
        prompt
        + REGRESSION_REQUIREMENT
        + (EXPLICIT_INSTRUCTION if arm == "explicit" else "")
    )


def collaboration_metrics(
    records: list[dict], requests: list[dict], messages: list[dict], delivered_ids=()
) -> dict:
    """Direct-workflow recipe, measured from real calls and host-owned delivery."""
    del (
        messages
    )  # Durable owner acknowledgements, never model prose, are authoritative here.
    delivered_ids = set(delivered_ids)

    def acknowledged(record: dict) -> bool:
        times = [
            record.get(k) for k in ("startedEpoch", "returnedEpoch", "deliveredEpoch")
        ]
        return bool(
            record.get("returned") is True
            and record["subagentId"] in delivered_ids
            and record.get("deliveryEvidence") == "task_result_owner_ack"
            and isinstance(record.get("resultSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", record["resultSha256"])
            and all(type(t) in (int, float) and math.isfinite(t) for t in times)
            and times[0] <= times[1] <= times[2]
        )

    containers = {
        r["subagentId"]: r
        for r in records
        if r.get("isContainer") and r.get("agentType") == "workflow"
    }
    delivered_workflows = {
        actor: r for actor, r in containers.items() if acknowledged(r)
    }
    leaves = []
    for record in records:
        calls = [r for r in requests if r.get("subagentId") == record["subagentId"]]
        if record.get("isContainer") or not any(
            r.get("event") == "completed" for r in calls
        ):
            continue
        workflow = delivered_workflows.get(record.get("parentSubagentId"))
        via_workflow = bool(
            workflow
            and record.get("returned") is True
            and workflow["startedEpoch"] <= record["startedEpoch"]
            and record.get("returnedEpoch", float("inf"))
            <= workflow.get("returnedEpoch", 0)
        )
        leaves.append(
            {
                **record,
                "delivered": acknowledged(record) or via_workflow,
                "workflowDelivered": via_workflow,
            }
        )
    analysts = [r for r in leaves if str(r.get("role", "")).lower() == "analyst"]
    external = [r for r in leaves if r.get("parentSubagentId") not in containers]
    read_roles = {"plan", "explore", "analyst"}
    implementation = [
        r
        for r in leaves
        if str(r.get("role", "")).lower() == "executor" and r["workflowDelivered"]
    ]
    leaf_ids = {r["subagentId"] for r in records if not r.get("isContainer")}
    return {
        "actualLeafRuns": len(leaves),
        "leafModelCalls": sum(r.get("subagentId") in leaf_ids for r in requests),
        "deliveredLeafResults": sum(r["delivered"] for r in leaves),
        "successfulLeafRuns": sum(
            r.get("toolOk") is True
            and isinstance(r.get("projected"), dict)
            and r["projected"].get("outcome") == "completed"
            and not r["projected"].get("partial", False)
            for r in leaves
        ),
        "readAnalysisLeafRuns": sum(
            str(r.get("role", "")).lower() in read_roles and r["delivered"]
            for r in leaves
        ),
        "implementationLeafRuns": len(implementation),
        "collaborationExecuted": any(r["delivered"] for r in leaves),
        "collaborationProtocolSatisfied": bool(
            implementation and delivered_workflows and not external
        ),
        "deliveredWorkflowResults": len(delivered_workflows),
        "actualAnalystLeafRuns": len(analysts),
        "deliveredAnalystLeafResults": sum(r["workflowDelivered"] for r in analysts),
        "externalAnalysisLeafRuns": sum(
            str(r.get("role", "")).lower() in read_roles for r in external
        ),
        "externalLeafRuns": len(external),
    }


def runner(output: Path) -> ModuleType:
    """Create isolated module globals; never patch the old runner singleton."""
    spec = importlib.util.spec_from_file_location(
        "_implementation_progress_shared_runner", ROOT / "scripts/run_three_arm_real.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DEFAULT = output
    module.ROOT = ROOT
    module.__file__ = str(
        Path(__file__).resolve()
    )  # Child processes enter this version, not the historical CLI.
    module.HARNESS_FILES = SUCCESSOR_HARNESS_FILES
    module.prompt_for_arm = prompt_for_arm
    module.collaboration_metrics = collaboration_metrics
    original_finalize = module.finalize_trial

    def finalize(*args, **kwargs):
        judgement = None
        judgement_started = time.monotonic()

        def common_judge(argv, **options):
            nonlocal judgement
            oracle_path, workspace, response_path = map(Path, argv[2:5])
            judgement = run_judgement(
                workspace,
                oracle_path.parent / "judgement",
                oracle_path,
                response_path,
                env=options.get("env"),
                total_seconds=JUDGE_POLICY["totalSeconds"],
                started_at=judgement_started,
            )
            log = oracle_path.parent / "judgement/oracle/output.log"
            return module.subprocess.CompletedProcess(
                argv,
                0 if judgement["oracle"]["passed"] else 1,
                stdout=log.read_text(errors="replace") if log.exists() else "",
                stderr="",
            )

        # Only the old finalizer's globals are cloned. Never mutate the shared
        # subprocess module used by the guardian or any other evaluator.
        proxy = SimpleNamespace(**{**vars(module.subprocess), "run": common_judge})
        judged_finalize = FunctionType(
            original_finalize.__code__,
            {**original_finalize.__globals__, "subprocess": proxy},
            original_finalize.__name__,
            original_finalize.__defaults__,
            original_finalize.__closure__,
        )
        judged_finalize.__kwdefaults__ = original_finalize.__kwdefaults__
        row = judged_finalize(*args, **kwargs)
        if judgement is None:
            regression = {
                "status": "not_run_agent_cleanup_or_workspace",
                "passed": False,
                "counts": {
                    k: 0
                    for k in (
                        "collected",
                        "passed",
                        "failed",
                        "errors",
                        "skipped",
                        "xfailed",
                    )
                },
                "durationSeconds": 0.0,
                "cleanup": None,
            }
        else:
            regression = judgement["regressionTest"]
            # Keep the old finalizer's true scope/verification wall time.
            # The common deadline began before any parent finalization work.
            judgement["durationSeconds"] = time.monotonic() - judgement_started
            judgement["deadlineExceeded"] = (
                judgement["durationSeconds"] >= JUDGE_POLICY["totalSeconds"]
            )
            if judgement["deadlineExceeded"]:
                regression["passed"] = False
                regression["status"] = "deadline_exhausted"
            trial_root = output / "private" / row["id"] / "judgement"
            shared.write_json(trial_root / "result.json", judgement)
            shared.write_json(trial_root / "regression/result.json", regression)
            if judgement["oracle"]["status"] in {"timeout", "deadline_exhausted"}:
                row["oracleErrorClass"] = "OracleTimeout"
        row["regressionTest"] = regression
        row["regressionTestPassed"] = regression["passed"]
        row["judgement"] = judgement
        row["success"] = bool(
            row["success"]
            and regression["passed"]
            and judgement
            and judgement["cleanupComplete"]
        )
        case = next(
            c
            for c in shared.read_json(output / "tasks.json")["cases"]
            if c["id"] == row["case"]
        )
        row["changedSourceFiles"] = sorted(
            p
            for p in row["changedFiles"]
            if p.startswith("minicode/")
            and p.endswith(".py")
            and p in case["authorized_paths"]
            and p not in row["verificationInducedChanges"]
        )
        shared.write_json(output / "trials" / (row["id"] + ".json"), row)
        if judgement is not None and not judgement["cleanupComplete"]:
            module.checkpoint_results(
                output, shared.read_json(output / "manifest.json")
            )
            raise RuntimeError("judge process cleanup incomplete; stopping suite")
        return row

    module.finalize_trial = finalize
    original_execute = module.execute_trial
    original_run_suite = module.run_suite

    def execute_trial(trial_output, *args, **kwargs):
        manifest = validate_successor(Path(trial_output))
        from minicode.agent_runtime import create_agent_turn_runtime
        from minicode.deterministic_verifier import VerificationCheck
        from minicode.implementation_execution import ImplementationSpec

        trial_id = args[0] if args else kwargs["trial_id"]
        trial = next(row for row in manifest["schedule"] if row["id"] == trial_id)
        document = shared.read_json(Path(trial_output) / "tasks.json")
        if (manifest.get("publicVerificationFixturePolicy") != PUBLIC_FIXTURE_POLICY
                or manifest.get("publicVerificationFixtures") != public_fixture_manifest(document)):
            raise ValueError("frozen public verification inputs changed")
        case = next(row for row in document["cases"] if row["id"] == trial["case"])
        public_inputs = bind_public_fixtures(document, case["id"])
        validate_public_preflight(document, Path(trial_output) / "public-preflight/receipt.json",
                                  manifest.get("publicPreflightSha256"))
        fixture_sink = fixture_receipt_writer(Path(trial_output) / "private" / trial_id / "public-fixture-receipts.jsonl")
        spec = ImplementationSpec(write_paths=tuple(case["authorized_paths"]),
            objective=case["prompt"], checks=(
                VerificationCheck("public_existing", "tests", tuple(case["verification_argv"]), 120),
                VerificationCheck("public_regression", "tests", ("python3", "-m", "pytest", "-q", "tests/test_eval_regression.py"), 120),
            ))
        def configured_runtime(*factory_args, **factory_kwargs):
            factory_kwargs["implementation_spec"] = spec
            return create_agent_turn_runtime(*factory_args, **factory_kwargs)

        with bind_verifier_public_inputs(public_inputs, receipt_sink=fixture_sink), frozen_role_turn_overrides(manifest), patch(
            "minicode.agent_runtime.create_agent_turn_runtime", configured_runtime
        ):
            return original_execute(trial_output, *args, **kwargs)

    def run_suite(trial_output, *args, **kwargs):
        validate_successor(Path(trial_output))
        return original_run_suite(trial_output, *args, **kwargs)

    module.execute_trial = execute_trial
    module.run_suite = run_suite
    return module


def prepare(output: Path, phase: str, source: Path = SOURCE) -> None:
    if phase not in {"engineering", "formal"}:
        raise ValueError("unknown experiment phase")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new empty directory; never replace prior evidence")
    raw = (source / "tasks.json").read_bytes()
    source_manifest = shared.read_json(source / "manifest.json")
    if shared.sha(raw) != source_manifest["taskSha256"]:
        raise ValueError("historical source tasks do not match their frozen manifest")
    tasks = json.loads(raw)
    ids = [c["id"] for c in tasks["cases"]]
    if len(ids) != 8 or len(set(ids)) != 8 or not set(SMALL_CASES) <= set(ids):
        raise ValueError("expected the eight original historical tasks")
    if phase == "engineering":
        tasks["cases"] = [
            next(c for c in tasks["cases"] if c["id"] == name) for name in SMALL_CASES
        ]
    output.mkdir(parents=True, exist_ok=True)
    shared.write_json(output / "tasks.json", tasks)
    shared.write_json(
        output / "source-provenance.json",
        {
            "sourceDirectory": str(source.resolve()),
            "sourceSuite": source.name,
            "sourceTaskSha256": shared.sha(raw),
            "sourceManifestSha256": shared.sha((source / "manifest.json").read_bytes()),
            "preparedTaskSha256": shared.sha((output / "tasks.json").read_bytes()),
            "phase": phase,
            "selectedCases": [c["id"] for c in tasks["cases"]],
        },
    )
    (output / "analysis-plan.md").write_text(ANALYSIS_PLAN, encoding="utf-8")


def schedule_for(cases: list[str], phase: str) -> list[dict]:
    if phase not in {"engineering", "formal"}:
        raise ValueError("unknown experiment phase")
    arms, repeats = (SMALL_ARMS, 1) if phase == "engineering" else (FORMAL_ARMS, 2)
    rng = random.Random(SEED)
    permutations = list(itertools.permutations(arms))
    orders = (
        permutations
        * ((len(cases) * repeats + len(permutations) - 1) // len(permutations))
    )[: len(cases) * repeats]
    rng.shuffle(orders)
    schedule = []
    for repeat in range(1, repeats + 1):
        ordered = list(cases)
        rng.shuffle(ordered)
        for case in ordered:
            for arm in orders.pop():
                schedule.append(
                    {
                        "case": case,
                        "repeat": repeat,
                        "arm": arm,
                        "id": f"{case}-r{repeat}-{arm}",
                    }
                )
    return schedule


def gate(output: Path, *, audit: dict | None = None) -> dict:
    """Engineering-only go/no-go. A successful trial is never dropped or replayed."""
    manifest = shared.read_json(output / "manifest.json")
    results = shared.read_json(output / "results.json")
    if audit is None:
        from scripts.audit_implementation_progress import Audit

        audit = Audit(output, allow_partial=False).run()
    rows = results.get("trials", [])
    expected = {(case, 1, arm) for case in SMALL_CASES for arm in SMALL_ARMS}
    cells = Counter((r.get("case"), r.get("repeat"), r.get("arm")) for r in rows)
    conditions = {
        "engineeringPhase": manifest.get("phase") == "engineering"
        and manifest.get("protocolVersion")
        == (
            "direct-workflow-v1"
            if manifest.get("schemaVersion") in (3, 4)
            else PROTOCOL_VERSION
        ),
        "sixTerminalRows": len(rows) == 6
        and set(cells) == expected
        and all(n == 1 for n in cells.values()),
        "validFinalAudit": audit.get("valid") is True
        and audit.get("mode") == "final"
        and audit.get("auditedTrials") == 6
        and audit.get("expectedTrials") == 6,
        "cleanScopeAndReceipts": len(rows) == 6
        and all(
            r.get("scopePassed") is True
            and r.get("processCleanup", {}).get("cleanupComplete") is True
            and r.get("usageComplete") is True
            and r.get("unknownUsageCalls") == 0
            and not r.get("ledgerDamaged")
            and not r.get("recoveryEvidenceErrors")
            for r in rows
        ),
    }
    passed = [
        r
        for r in rows
        if r.get("oracleSuccess") is True
        and (
            manifest.get("schemaVersion") != 5 or r.get("regressionTestPassed") is True
        )
        and r.get("scopePassed") is True
        and bool(r.get("changedSourceFiles"))
    ]
    by_arm = Counter(r["arm"] for r in passed)
    by_case = Counter(r["case"] for r in passed)
    explicit = [r for r in rows if r.get("arm") == "explicit"]
    conditions.update(
        {
            "atLeastFourRealRepairs": len(passed) >= 4,
            "eachArmAtLeastTwo": all(by_arm[a] >= 2 for a in SMALL_ARMS),
            "eachCaseAtLeastOne": all(by_case[c] >= 1 for c in SMALL_CASES),
            "allExplicitExecutorDelivered": len(explicit) == 3
            and all(
                r.get("collaborationProtocolSatisfied") is True
                and r.get("implementationLeafRuns", 0) >= 1
                for r in explicit
            ),
        }
    )
    return {
        "schemaVersion": 1,
        "passed": all(conditions.values()),
        "conditions": conditions,
        "manifestSha256": shared.sha((output / "manifest.json").read_bytes()),
        "resultsSha256": shared.sha((output / "results.json").read_bytes()),
        "auditSha256": shared.sha(
            json.dumps(audit, sort_keys=True, ensure_ascii=False).encode()
        ),
        "realRepairCount": len(passed),
        "repairsByArm": dict(by_arm),
        "repairsByCase": dict(by_case),
        "interpretation": "Engineering delivery gate only; no effect-size or generalization claim.",
    }


def freeze(output: Path, engineering: Path | None = None) -> None:
    if (output / "manifest.json").exists():
        raise ValueError("Manifest exists; use a new experiment version")
    provenance = shared.read_json(output / "source-provenance.json")
    tasks_bytes = (output / "tasks.json").read_bytes()
    if (
        shared.sha(tasks_bytes) != provenance["preparedTaskSha256"]
        or (output / "analysis-plan.md").read_text() != ANALYSIS_PLAN
    ):
        raise ValueError("prepared task/plan changed; prepare a new version")
    phase = provenance["phase"]
    historical = Path(provenance["sourceDirectory"])
    original_bytes = (historical / "tasks.json").read_bytes()
    if (
        shared.sha(original_bytes) != provenance["sourceTaskSha256"]
        or shared.sha((historical / "manifest.json").read_bytes())
        != provenance["sourceManifestSha256"]
    ):
        raise ValueError("historical task provenance changed")
    original_tasks = json.loads(original_bytes)
    if phase == "engineering":
        original_tasks["cases"] = [
            next(c for c in original_tasks["cases"] if c["id"] == name)
            for name in SMALL_CASES
        ]
    if json.loads(tasks_bytes) != original_tasks:
        raise ValueError("prepared tasks differ from the selected historical tasks")
    module = runner(output)
    runtime = module.frozen_runtime()
    if runtime["model"] != "glm-5.3-flash":
        raise ValueError("price card only applies to glm-5.3-flash")
    role_turn_limits, role_turn_policy = role_turn_configuration()
    gate_result = None
    if phase == "formal":
        if engineering is None:
            raise ValueError("formal freeze requires a completed engineering gate")
        gate_result = gate(engineering)
        if not gate_result["passed"]:
            raise ValueError("engineering gate failed; formal execution is blocked")
        small = shared.read_json(engineering / "manifest.json")
        for key, value in {
            "productionSha256": module.production_sha(),
            "harnessSha256": module.harness_sha(),
            "routeSha256": module.route_hash(runtime),
            "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
            "limits": module.LIMITS,
            "price": module.PRICE,
            "sourceTaskSha256": provenance["sourceTaskSha256"],
            "model": runtime["model"],
            "temperature": 0,
            "roleTurnLimits": role_turn_limits,
            "roleTurnPolicy": role_turn_policy,
            "schemaVersion": 5,
            "judgementPolicy": JUDGE_POLICY,
            "protocolVersion": PROTOCOL_VERSION,
            "implementationCapabilities": IMPLEMENTATION_CAPABILITIES,
            "publicVerificationFixturePolicy": PUBLIC_FIXTURE_POLICY,
            "publicVerificationFixtures": public_fixture_manifest(json.loads(tasks_bytes)),
        }.items():
            if small.get(key) != value:
                raise ValueError(f"engineering/formal contract differs: {key}")
    cases = [c["id"] for c in json.loads(tasks_bytes)["cases"]]
    if phase not in {"engineering", "formal"} or cases != provenance["selectedCases"]:
        raise ValueError("invalid prepared phase or task selection")
    if (phase == "engineering" and cases != list(SMALL_CASES)) or (
        phase == "formal" and len(cases) != 8
    ):
        raise ValueError("wrong fixed task set")
    preflight = run_public_preflight(json.loads(tasks_bytes), output / "public-preflight", runtime=runtime)
    if not preflight["passed"]:
        raise ValueError("public baseline preflight failed; paid execution is blocked")
    archive = output / "frozen-code.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "minicode").rglob("*.py")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in SUCCESSOR_HARNESS_FILES:
            bundle.write(ROOT / "scripts" / name, "scripts/" + name)
        bundle.write(output / "analysis-plan.md", "analysis-plan.md")
    manifest = {
        "schemaVersion": 5,
        "protocolVersion": PROTOCOL_VERSION,
        "judgementPolicy": JUDGE_POLICY,
        "implementationCapabilities": IMPLEMENTATION_CAPABILITIES,
        "publicVerificationFixturePolicy": PUBLIC_FIXTURE_POLICY,
        "publicVerificationFixtures": public_fixture_manifest(json.loads(tasks_bytes)),
        "publicPreflightSha256": shared.sha((output / "public-preflight/receipt.json").read_bytes()),
        "phase": phase,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "frozenCodeArchiveSha256": shared.sha(archive.read_bytes()),
        "taskSha256": shared.sha(tasks_bytes),
        "analysisPlanSha256": shared.sha((output / "analysis-plan.md").read_bytes()),
        "productionSha256": module.production_sha(),
        "harnessSha256": module.harness_sha(),
        "harnessFiles": list(SUCCESSOR_HARNESS_FILES),
        "runner": "scripts/run_implementation_progress_live.py",
        "sourceTaskSha256": provenance["sourceTaskSha256"],
        "sourceManifestSha256": provenance["sourceManifestSha256"],
        "sourceSuite": provenance["sourceSuite"],
        "reusedCalibrationCases": list(SMALL_CASES),
        "model": runtime["model"],
        "routeSha256": module.route_hash(runtime),
        "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
        "limits": module.LIMITS,
        "price": module.PRICE,
        "temperature": 0,
        "roleTurnLimits": role_turn_limits,
        "roleTurnPolicy": role_turn_policy,
        "repeats": 1 if phase == "engineering" else 2,
        "arms": list(SMALL_ARMS if phase == "engineering" else FORMAL_ARMS),
        "schedule": schedule_for(cases, phase),
        "concurrency": 1,
        "scheduleSeed": SEED,
        "explicitRecipe": "Coordinator-only parent (task/todo_write): direct workflow; optional internal Analyst; real Executor returned through its own workflow owner acknowledgement; no external analysis leaf.",
        "minimumProtocolCoverage": 0.8,
        "engineeringGate": {"directory": str(engineering.resolve()), **gate_result}
        if gate_result
        else None,
        "comparison": "Engineering delivery check"
        if phase == "engineering"
        else "Regression comparison on reused historical tasks, not an independent generalization set",
        "guardian": "Shared 1800 second execution deadline; 30 second setup; cleanup before external oracle.",
        "tokenBudgetSemantics": "Shared estimate admission; provider receipts retained even if estimates overshoot; physical requests and execution wall time hard capped.",
        "isolation": "Original runner permissions, disposable workspace, empty profiles/skills/memory and independent external oracle; bounded host approval present.",
    }
    shared.write_json(output / "manifest.json", manifest)


def validate_successor(output: Path) -> dict:
    manifest = shared.read_json(output / "manifest.json")
    if manifest.get("protocolVersion") != PROTOCOL_VERSION or manifest.get(
        "harnessFiles"
    ) != list(SUCCESSOR_HARNESS_FILES):
        raise ValueError("not this frozen successor protocol")
    validate_role_turn_configuration(manifest)
    document = shared.read_json(output / "tasks.json")
    validate_public_preflight(document, output / "public-preflight/receipt.json", manifest.get("publicPreflightSha256"))
    if (manifest.get("publicVerificationFixturePolicy") != PUBLIC_FIXTURE_POLICY
            or manifest.get("publicVerificationFixtures") != public_fixture_manifest(document)):
        raise ValueError("frozen public verification inputs changed")
    if manifest.get("implementationCapabilities") != IMPLEMENTATION_CAPABILITIES:
        raise ValueError("frozen implementation capabilities changed")
    if manifest.get("judgementPolicy") != JUDGE_POLICY:
        raise ValueError("frozen judgement policy changed")
    if manifest.get("phase") not in {"engineering", "formal"}:
        raise ValueError("invalid frozen phase")
    if manifest["frozenCodeArchiveSha256"] != shared.sha(
        (output / "frozen-code.zip").read_bytes()
    ):
        raise ValueError("frozen archive changed")
    if manifest["analysisPlanSha256"] != shared.sha(
        (output / "analysis-plan.md").read_bytes()
    ):
        raise ValueError("frozen plan changed")
    if manifest["phase"] == "formal":
        prior = manifest.get("engineeringGate")
        if not isinstance(prior, dict) or prior.get("passed") is not True:
            raise ValueError("formal run lacks passed engineering gate")
        observed = gate(Path(prior["directory"]))
        if any(
            observed.get(k) != prior.get(k)
            for k in ("passed", "manifestSha256", "resultsSha256", "auditSha256")
        ):
            raise ValueError("engineering gate evidence changed")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "freeze", "run", "child", "gate"))
    parser.add_argument("--output", type=Path, default=DEFAULT / "engineering-v1")
    parser.add_argument(
        "--phase", choices=("engineering", "formal"), default="engineering"
    )
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--engineering", type=Path)
    parser.add_argument("--trial", action="append")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output, args.phase, args.source.resolve())
    elif args.mode == "freeze":
        freeze(output, args.engineering.resolve() if args.engineering else None)
    elif args.mode == "gate":
        result = gate(output)
        shared.write_json(output / "engineering-gate.json", result)
        print(json.dumps(result, ensure_ascii=False))
        if not result["passed"]:
            raise SystemExit(2)
    else:
        validate_successor(output)
        module = runner(output)
        if args.mode == "child":
            if not args.trial or len(args.trial) != 1:
                parser.error("child requires exactly one --trial")
            module.execute_trial(output, args.trial[0])
        else:
            module.run_suite(output, set(args.trial or []))


if __name__ == "__main__":
    main()
