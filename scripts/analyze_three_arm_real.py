#!/usr/bin/env python3
"""Manifest-driven paired analysis of real-task single/auto/explicit trials.

The primary family is explicit-single on success, time and complete cost.
Six prespecified secondary contrasts form a separate family. Task means, not
repeated trials, are the independent units. Missing provider usage stays unknown.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ARMS = ("single", "auto", "explicit")
METRICS = ("success", "durationSeconds", "estimatedCostCny")
PAIRS = (
    ("explicit", "single", "primary"),
    ("auto", "single", "secondary"),
    ("explicit", "auto", "secondary"),
)
SEED, BOOTSTRAPS = 20261013, 20000
COVERAGE_THRESHOLD = 0.8
MIN_EXPLICIT_LEAVES = 2


class AnalysisError(ValueError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nonnegative(value, label, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value < 0
        or (integer and value != int(value))
    ):
        raise AnalysisError(f"Invalid {label}")


def validate(manifest, results):
    schedule = manifest.get("schedule", [])
    repeats = manifest.get("repeats")
    if type(repeats) is not int or repeats < 1 or not schedule:
        raise AnalysisError("Missing frozen schedule or positive repeat count")
    cases = sorted({row["case"] for row in schedule})
    if not 2 <= len(cases) <= 16:
        raise AnalysisError("Exact task-level analysis requires 2–16 tasks")
    expected = {row["id"]: row for row in schedule}
    if len(expected) != len(schedule):
        raise AnalysisError("Duplicate schedule id")
    cells = Counter((row["case"], row["repeat"], row["arm"]) for row in schedule)
    required = set(itertools.product(cases, range(1, repeats + 1), ARMS))
    if set(cells) != required or any(count != 1 for count in cells.values()):
        raise AnalysisError("Frozen three-arm schedule is not balanced")
    if results.get("expectedTrials") != len(schedule):
        raise AnalysisError("Result expectedTrials differs from manifest")
    found, categories = {}, {}
    for row in results.get("trials", []):
        trial_id = row.get("id")
        if trial_id not in expected or trial_id in found:
            raise AnalysisError("Unknown or duplicate result id")
        for field in ("case", "repeat", "arm"):
            if row.get(field) != expected[trial_id][field]:
                raise AnalysisError(f"Frozen assignment mismatch: {trial_id}/{field}")
        for field in (
            "success",
            "oracleSuccess",
            "scopePassed",
            "usageComplete",
            "tokenUsageComplete",
            "cacheUsageComplete",
            "collaborationExecuted",
            "collaborationProtocolSatisfied",
        ):
            if type(row.get(field)) is not bool:
                raise AnalysisError(f"Missing boolean {field}: {trial_id}")
        for field in (
            "modelCalls",
            "unknownUsageCalls",
            "inputTokens",
            "outputTokens",
            "cacheReadTokens",
            "totalTokens",
            "delegationAttempts",
            "leafModelCalls",
            "successfulLeafRuns",
            "deliveredLeafResults",
            "actualLeafRuns",
            "readAnalysisLeafRuns",
            "implementationLeafRuns",
        ):
            nonnegative(row.get(field), field, integer=True)
        nonnegative(row.get("durationSeconds"), "durationSeconds")
        if row["inputTokens"] + row["outputTokens"] != row["totalTokens"]:
            raise AnalysisError(f"Token total mismatch: {trial_id}")
        if row["cacheReadTokens"] > row["inputTokens"]:
            raise AnalysisError(f"Cache read exceeds input: {trial_id}")
        if row["unknownUsageCalls"] > row["modelCalls"]:
            raise AnalysisError(f"Unknown usage calls exceed total calls: {trial_id}")
        if row["tokenUsageComplete"] and row["unknownUsageCalls"]:
            raise AnalysisError(f"Complete token usage has unknown calls: {trial_id}")
        if row["cacheUsageComplete"] and not row["tokenUsageComplete"]:
            raise AnalysisError(
                f"Complete cache usage requires token usage: {trial_id}"
            )
        if row["usageComplete"] != (
            row["tokenUsageComplete"] and row["cacheUsageComplete"]
        ):
            raise AnalysisError(f"Financial completeness flags disagree: {trial_id}")
        executed_evidence = (
            row["actualLeafRuns"] > 0
            and row["leafModelCalls"] > 0
            and row["deliveredLeafResults"] > 0
        )
        if row["collaborationExecuted"] != executed_evidence:
            raise AnalysisError(
                f"Actual execution claim lacks leaf/delivery evidence: {trial_id}"
            )
        if row["successfulLeafRuns"] > row["actualLeafRuns"]:
            raise AnalysisError(
                f"More successful leaves than actual leaves: {trial_id}"
            )
        if row["actualLeafRuns"] > row["leafModelCalls"]:
            raise AnalysisError(f"Actual leaf runs exceed leaf model calls: {trial_id}")
        if (
            row["readAnalysisLeafRuns"] + row["implementationLeafRuns"]
            > row["actualLeafRuns"]
        ):
            raise AnalysisError(
                f"Role-specific leaves exceed actual leaves: {trial_id}"
            )
        if row["arm"] == "single" and any(
            row[k]
            for k in (
                "delegationAttempts",
                "leafModelCalls",
                "successfulLeafRuns",
                "deliveredLeafResults",
                "actualLeafRuns",
                "readAnalysisLeafRuns",
                "implementationLeafRuns",
                "collaborationExecuted",
                "collaborationProtocolSatisfied",
            )
        ):
            raise AnalysisError(f"Single arm contains delegation: {trial_id}")
        if (
            row["arm"] == "explicit"
            and row["collaborationProtocolSatisfied"]
            and (
                not row["collaborationExecuted"]
                or row["actualLeafRuns"] < MIN_EXPLICIT_LEAVES
                or row["readAnalysisLeafRuns"] < 1
                or row["implementationLeafRuns"] < 1
                or row["deliveredLeafResults"] < MIN_EXPLICIT_LEAVES
            )
        ):
            raise AnalysisError(
                f"Explicit protocol requires analysis and implementation leaves with delivery: {trial_id}"
            )
        if row["leafModelCalls"] > row["modelCalls"]:
            raise AnalysisError(f"Leaf calls exceed total model calls: {trial_id}")
        expected_success = row["oracleSuccess"] and row["scopePassed"]
        if row["arm"] == "explicit":
            expected_success = (
                expected_success and row["collaborationProtocolSatisfied"]
            )
        if row["success"] != expected_success:
            raise AnalysisError(
                f"Success does not match oracle/scope/protocol: {trial_id}"
            )
        if row["usageComplete"]:
            nonnegative(row.get("estimatedCostCny"), "estimatedCostCny")
            price = manifest.get("price", {})
            keys = ("perMillionInput", "perMillionOutput", "perMillionCacheRead")
            if all(key in price for key in keys):
                cost = (
                    (row["inputTokens"] - row["cacheReadTokens"]) * price[keys[0]]
                    + row["outputTokens"] * price[keys[1]]
                    + row["cacheReadTokens"] * price[keys[2]]
                ) / 1_000_000
                if not math.isclose(
                    cost, row["estimatedCostCny"], rel_tol=1e-9, abs_tol=1e-12
                ):
                    raise AnalysisError(f"Cost differs from frozen price: {trial_id}")
        elif row.get("estimatedCostCny") is not None:
            raise AnalysisError(f"Missing usage cannot produce full cost: {trial_id}")
        if row.get("knownCostLowerBoundCny") is not None:
            nonnegative(row["knownCostLowerBoundCny"], "knownCostLowerBoundCny")
        category = row.get("category")
        if (
            not isinstance(category, str)
            or not category
            or categories.setdefault(row["case"], category) != category
        ):
            raise AnalysisError(f"Missing or inconsistent category: {trial_id}")
        found[trial_id] = row
    if len(found) != len(schedule):
        raise AnalysisError(
            f"Incomplete trial results: {len(found)}/{len(schedule)}; no report generated"
        )
    return [found[row["id"]] for row in schedule], cases, repeats


def describe(values):
    arr = np.asarray(values, dtype=float)
    return {
        "n": len(arr),
        "mean": float(arr.mean()),
        "sd": float(arr.std(ddof=1)) if len(arr) > 1 else None,
        "p50": float(np.quantile(arr, 0.5)),
        "p95": float(np.quantile(arr, 0.95)),
        "total": float(arr.sum()),
    }


def sign_flip(differences):
    arr = np.asarray(differences)
    obs = abs(float(arr.mean()))
    signs = np.asarray(list(itertools.product((-1, 1), repeat=len(arr))))
    return float(
        np.mean(np.abs((arr * signs).mean(axis=1)) >= obs - 1e-12 * max(1.0, obs))
    )


def holm(values):
    ordered = sorted(
        range(len(values)), key=lambda i: 1.0 if values[i] is None else values[i]
    )
    result, previous = [None] * len(values), 0.0
    for rank, index in enumerate(ordered):
        raw = 1.0 if values[index] is None else values[index]
        previous = max(previous, min(1.0, raw * (len(values) - rank)))
        if values[index] is not None:
            result[index] = previous
    return result


def arm_summary(rows):
    complete = all(row["usageComplete"] for row in rows)
    token_complete = all(row["tokenUsageComplete"] for row in rows)
    cache_complete = all(row["cacheUsageComplete"] for row in rows)
    costs = [
        row["estimatedCostCny"]
        if row["usageComplete"]
        else row.get("knownCostLowerBoundCny")
        for row in rows
    ]
    available = [cost for cost in costs if cost is not None]
    return {
        "trials": len(rows),
        "successes": sum(row["success"] for row in rows),
        "successRate": sum(row["success"] for row in rows) / len(rows),
        "oracleSuccesses": sum(row["oracleSuccess"] for row in rows),
        "scopeFailures": sum(not row["scopePassed"] for row in rows),
        "durationSeconds": describe([row["durationSeconds"] for row in rows]),
        "usageComplete": complete,
        "usageCompleteTrials": sum(row["usageComplete"] for row in rows),
        "tokenUsageComplete": token_complete,
        "tokenUsageCompleteTrials": sum(row["tokenUsageComplete"] for row in rows),
        "cacheUsageComplete": cache_complete,
        "cacheUsageCompleteTrials": sum(row["cacheUsageComplete"] for row in rows),
        "costCny": describe([row["estimatedCostCny"] for row in rows])
        if complete
        else None,
        "knownCostLowerBoundCny": sum(available) if available else None,
        "modelCalls": sum(row["modelCalls"] for row in rows),
        "unknownUsageCalls": sum(row["unknownUsageCalls"] for row in rows),
        "knownTokens": {
            key: sum(row[key] for row in rows)
            for key in ("inputTokens", "outputTokens", "cacheReadTokens", "totalTokens")
        },
        "tokenSemantics": "complete" if token_complete else "known_lower_bound",
        "cacheTokenSemantics": "complete" if cache_complete else "known_lower_bound",
        "delegationAttempts": sum(row["delegationAttempts"] for row in rows),
        "actualExecutionTrials": sum(row["collaborationExecuted"] for row in rows),
        "protocolSatisfiedTrials": sum(
            row["collaborationProtocolSatisfied"] for row in rows
        ),
        "protocolCoverage": sum(row["collaborationProtocolSatisfied"] for row in rows)
        / len(rows),
        "successfulLeafRuns": sum(row["successfulLeafRuns"] for row in rows),
        "actualLeafRuns": sum(row["actualLeafRuns"] for row in rows),
        "readAnalysisLeafRuns": sum(row["readAnalysisLeafRuns"] for row in rows),
        "implementationLeafRuns": sum(row["implementationLeafRuns"] for row in rows),
        "terminalFailureClasses": dict(
            Counter(
                row.get("errorClass") or "OracleScopeOrProtocolFailure"
                for row in rows
                if not row["success"]
            )
        ),
    }


def analyze(manifest, results):
    rows, cases, repeats = validate(manifest, results)
    tasks = []
    for case in cases:
        entry = {
            "case": case,
            "category": next(row["category"] for row in rows if row["case"] == case),
        }
        for arm in ARMS:
            arm_rows = [
                row for row in rows if row["case"] == case and row["arm"] == arm
            ]
            entry[arm] = {
                "success": float(np.mean([row["success"] for row in arm_rows])),
                "oracleSuccess": float(
                    np.mean([row["oracleSuccess"] for row in arm_rows])
                ),
                "durationSeconds": float(
                    np.mean([row["durationSeconds"] for row in arm_rows])
                ),
                "estimatedCostCny": float(
                    np.mean([row["estimatedCostCny"] for row in arm_rows])
                )
                if all(row["usageComplete"] for row in arm_rows)
                else None,
                "protocolCoverage": float(
                    np.mean([row["collaborationProtocolSatisfied"] for row in arm_rows])
                ),
            }
        tasks.append(entry)
    resamples = np.random.default_rng(SEED).integers(
        0, len(cases), (BOOTSTRAPS, len(cases))
    )
    comparisons = []
    for treatment, baseline, family in PAIRS:
        for metric in METRICS:
            a = [row[baseline][metric] for row in tasks]
            b = [row[treatment][metric] for row in tasks]
            comp = {
                "id": f"{treatment}-{baseline}/{metric}",
                "treatment": treatment,
                "baseline": baseline,
                "family": family,
                "metric": metric,
                "available": all(value is not None for value in a + b),
            }
            if not comp["available"]:
                comp.update(
                    reason="Incomplete provider usage in this pair; no cost comparison",
                    exactP=None,
                    holmP=None,
                )
            else:
                aa, bb = np.asarray(a), np.asarray(b)
                delta = bb - aa
                sampled_a, sampled_b = (
                    aa[resamples].mean(axis=1),
                    bb[resamples].mean(axis=1),
                )
                comp.update(
                    nTasks=len(cases),
                    treatmentMean=float(bb.mean()),
                    baselineMean=float(aa.mean()),
                    delta=float(delta.mean()),
                    delta95CI=np.quantile(
                        delta[resamples].mean(axis=1), [0.025, 0.975]
                    ).tolist(),
                    ratio=float(bb.mean() / aa.mean()) if aa.mean() > 0 else None,
                    ratio95CI=np.quantile(
                        sampled_b / sampled_a, [0.025, 0.975]
                    ).tolist()
                    if bool(np.all(sampled_a > 0))
                    else None,
                    taskDifferences=delta.tolist(),
                    exactP=sign_flip(delta),
                )
            comparisons.append(comp)
    for family in ("primary", "secondary"):
        selected = [comp for comp in comparisons if comp["family"] == family]
        for comp, adjusted in zip(
            selected, holm([comp["exactP"] for comp in selected])
        ):
            comp["holmP"] = adjusted
    arms = {
        arm: arm_summary([row for row in rows if row["arm"] == arm]) for arm in ARMS
    }
    coverage = arms["explicit"]["protocolCoverage"]
    return {
        "schemaVersion": 1,
        "design": {
            "tasks": len(cases),
            "repeats": repeats,
            "trials": len(rows),
            "model": manifest.get("model"),
            "statisticalUnit": "task after averaging repeats",
            "primaryFamilySize": 3,
            "secondaryFamilySize": 6,
            "bootstrapSeed": SEED,
            "bootstrapResamples": BOOTSTRAPS,
            "exactSignFlips": 2 ** len(cases),
            "limits": manifest.get("limits"),
            "price": manifest.get("price"),
        },
        "mechanismGate": {
            "requiredCoverage": COVERAGE_THRESHOLD,
            "targetCoverage": 1.0,
            "observedCoverage": coverage,
            "passed": coverage >= COVERAGE_THRESHOLD,
            "meaning": "Below threshold blocks mechanism-benefit claims; all trials remain in comparisons",
        },
        "arms": arms,
        "tasks": tasks,
        "comparisons": comparisons,
        "categories": {
            category: {
                arm: arm_summary(
                    [
                        row
                        for row in rows
                        if row["arm"] == arm and row["category"] == category
                    ]
                )
                for arm in ARMS
            }
            for category in sorted({row["category"] for row in rows})
        },
    }


def fmt(value, digits=3):
    return "不可用" if value is None else f"{value:.{digits}f}"


def comparison_table(comparisons):
    lines = [
        "| 对比 | 指标 | 配对差 | 95% CI | 均值比 | 精确 p | family Holm p |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: |",
    ]
    labels = {
        "success": "成功率/百分点",
        "durationSeconds": "耗时/秒",
        "estimatedCostCny": "API等价成本/CNY",
    }
    for comp in comparisons:
        prefix = (
            f"| {comp['treatment']} − {comp['baseline']} | {labels[comp['metric']]}"
        )
        if not comp["available"]:
            lines.append(prefix + " | 缺用量，不比较 | — | — | — | — |")
            continue
        scale = 100 if comp["metric"] == "success" else 1
        lo, hi = (x * scale for x in comp["delta95CI"])
        lines.append(
            prefix
            + f" | {comp['delta'] * scale:.6f} | [{lo:.6f}, {hi:.6f}] | {fmt(comp['ratio'])} | {fmt(comp['exactP'], 5)} | {fmt(comp['holmP'], 5)} |"
        )
    return lines


def report(summary):
    design, arms, gate = summary["design"], summary["arms"], summary["mechanismGate"]
    lines = [
        "# 真实仓库三组配对评测",
        "",
        f"{design['tasks']} 个任务，每组每题 {design['repeats']} 次，共 {design['trials']} 个 trial；模型 {design['model']}。失败、超时和编排失败全部保留。独立统计单位是任务。",
        "",
    ]
    if not gate["passed"]:
        lines += [
            f"**机制未充分触发，阻断协作收益结论。** explicit 配方合规覆盖为 {gate['observedCoverage']:.1%}，低于预注册 80% 门槛。组间统计仍包含全部失败，但不能解释成真正协作有效或无效。",
            "",
        ]
    else:
        lines += [
            f"explicit 配方合规覆盖 {gate['observedCoverage']:.1%}，达到 80% 最低门槛（目标 100%）；通过覆盖门槛本身不证明协作有收益。",
            "",
        ]
    if not all(arm["usageComplete"] for arm in arms.values()):
        lines += [
            "**存在费用所需用量缺失。** 输入/输出 token 或缓存拆分未完整记录时，涉及该组的成本对比不可用；其已知 API 等价成本只是下界，不能按零填补或计算节省比例。第三组缺失不阻断另两组完整费用的配对比较。",
            "",
        ]
    if not all(arm["tokenUsageComplete"] for arm in arms.values()):
        lines += [
            "部分请求的输入/输出用量缺失：相应组的 token 数标为已知下界（≥），不能据下界差判断节省。仅缺缓存拆分而输入/输出完整的组仍显示准确 token 总数。",
            "",
        ]
    lines += [
        "single 保留评测范围内的本地编码工具；auto 使用自主调度；explicit 的父 Agent 只开放 task/todo_write，按冻结配方派发真实分析叶子，再派发 workflow 实现叶子并接收结果。三组任务信息、写入范围、验证权限、主/子模型和共享总预算相同；子 Agent 另遵守生产角色权限。",
        "",
        "| 指标 | single | auto | explicit |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, values in (
        ("完整成功", [f"{arms[a]['successes']}/{arms[a]['trials']}" for a in ARMS]),
        (
            "独立 oracle 通过",
            [f"{arms[a]['oracleSuccesses']}/{arms[a]['trials']}" for a in ARMS],
        ),
        (
            "耗时 mean ± SD/秒",
            [
                f"{fmt(arms[a]['durationSeconds']['mean'])} ± {fmt(arms[a]['durationSeconds']['sd'])}"
                for a in ARMS
            ],
        ),
        (
            "耗时 P50 / P95/秒",
            [
                f"{fmt(arms[a]['durationSeconds']['p50'])} / {fmt(arms[a]['durationSeconds']['p95'])}"
                for a in ARMS
            ],
        ),
        ("模型调用", [str(arms[a]["modelCalls"]) for a in ARMS]),
        (
            "缺输入/输出用量请求 / 全部请求",
            [f"{arms[a]['unknownUsageCalls']}/{arms[a]['modelCalls']}" for a in ARMS],
        ),
        (
            "输入/输出用量完整 trial",
            [
                f"{arms[a]['tokenUsageCompleteTrials']}/{arms[a]['trials']}"
                for a in ARMS
            ],
        ),
        (
            "缓存拆分完整 trial",
            [
                f"{arms[a]['cacheUsageCompleteTrials']}/{arms[a]['trials']}"
                for a in ARMS
            ],
        ),
        (
            "费用所需用量完整 trial",
            [f"{arms[a]['usageCompleteTrials']}/{arms[a]['trials']}" for a in ARMS],
        ),
        (
            "真实协作执行 trial",
            [f"{arms[a]['actualExecutionTrials']}/{arms[a]['trials']}" for a in ARMS],
        ),
        (
            "满足 explicit 配方 trial",
            [
                "不适用",
                "不适用",
                f"{arms['explicit']['protocolSatisfiedTrials']}/{arms['explicit']['trials']}",
            ],
        ),
        (
            "已知 API 等价成本/CNY",
            [
                fmt(arms[a]["costCny"]["total"], 6)
                if arms[a]["costCny"]
                else (
                    "≥" + fmt(arms[a]["knownCostLowerBoundCny"], 6)
                    if arms[a]["knownCostLowerBoundCny"] is not None
                    else "未知"
                )
                for a in ARMS
            ],
        ),
        (
            "已知输入 token",
            [
                ("" if arms[a]["tokenUsageComplete"] else "≥")
                + str(arms[a]["knownTokens"]["inputTokens"])
                for a in ARMS
            ],
        ),
        (
            "已知输出 token",
            [
                ("" if arms[a]["tokenUsageComplete"] else "≥")
                + str(arms[a]["knownTokens"]["outputTokens"])
                for a in ARMS
            ],
        ),
    ):
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines += [
        "",
        "完整成功要求 oracle 通过且写范围合规；explicit 另须满足冻结协作协议。单列 oracle 成功用于辨别编排失败。协作协议合规只要求真实分析与实现调用及其结果送达/消费，不要求生成代码已经正确；Verifier 失败仍可计为实际协作已发生。successfulLeafRuns 仅单列描述，不参与覆盖门槛。实际叶子去重且不含 workflow 容器；费用仅取请求级 provider usage。Coding 套餐路线的标准 API 等价成本不是账户实际扣费或边际成本。",
        "",
        "## 预注册主对比：explicit − single",
        "",
    ]
    primary = [c for c in summary["comparisons"] if c["family"] == "primary"]
    lines += comparison_table(primary)
    lines += [
        "",
        "主对比的成功率正差有利，耗时和费用负差有利；三项在同一 Holm family 内校正。未显著不证明等效，小样本不保证发现收益。",
        "",
        "## 预注册次要对比",
        "",
        "下列六项另作 Holm 校正，次要发现不能替代主问题或升格为主结论。",
        "",
    ]
    lines += comparison_table(
        [c for c in summary["comparisons"] if c["family"] == "secondary"]
    )
    lines += [
        "",
        "## 边界与失败",
        "",
        "这些结果限于冻结的真实历史缺陷回放与预算；不是对所有真实软件任务的随机抽样，也不覆盖所有生产权限路径。两次重复不能消除服务波动。预算、模型名称相同不保证后端权重快照和负载不变。",
        "",
        "三组均通过评测 transport wrapper 调用真实模型，并使用有界宿主审批：写入仅限授权文件，命令仅限题目公开的 pytest 测试集合/选择器或新增 tests/test_eval_regression.py 的 pytest 验证。共享请求/用量账本、强制 usage 回执、连接/空闲/请求超时及重试设置属于评测运行条件；结果不能直接代表未加 wrapper 的生产默认运行，也未覆盖纯 headless 无审批通道时的强制 workflow 升级。耗时为 Agent 执行阶段，外部独立验收另计。",
        "",
        "任务层 bootstrap 区间未作 Holm 同时校正，也不是符号翻转检验的反演区间。CI 不跨零而 Holm 检验未显著时，结论遵守预注册 Holm 门槛；保留符号可交换和小样本限制。",
        "",
        "终态/验收分类不能直接当作请求级根因；例如交付未通过可能源于此前请求超时。",
    ]
    for arm in ARMS:
        lines.append(
            f"\n{arm}：{json.dumps(arms[arm]['terminalFailureClasses'], ensure_ascii=False)}。"
        )
    if arms["auto"]["actualExecutionTrials"] == 0:
        lines += [
            "",
            "auto 未实际执行子任务，该组差异不能归因于多个 Agent 协作，也不能据此否定未覆盖权限路径的自动升级。",
        ]
    return "\n".join(lines) + "\n"


def write_bundle(summary, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "analysis-report.md").write_text(report(summary), encoding="utf-8")
    appendix = [
        "# 统计附录",
        "",
        "任务内先平均重复，任务间成对分析。bootstrap=20,000，seed=20261013，区间为percentile 2.5%–97.5%。双侧精确符号翻转枚举 2^N 种符号，需要零假设下符号可交换；不是把所有 trial 当独立样本。",
        "",
        "主 family: explicit−single 的success/time/cost共3项。次要 family: auto−single 与 explicit−auto 的3指标共6项。分别Holm校正；次要发现不替代primary。缺cost的检验输出null、校正中占p=1保留family大小。",
        "",
        "bootstrap CI未作Holm同时校正，不是精确检验反演区间；CI与校正p看似冲突时遵守预注册Holm p<0.05。零差/ties保留；基线均值零时均值比未定义。",
        "",
        "explicit以全部trial为分母，配方合规覆盖<80%阻断机制收益结论，不能只选执行成功子集。tokenUsageComplete判断输入/输出记录是否完整；cacheUsageComplete判断缓存拆分是否完整；usageComplete要求两者都完整，才允许费用比较。仅缺缓存拆分不把完整的输入/输出总数降为下界。unknownUsageCalls/全部modelCalls包括失败与未完成请求；缺失不补零，不拿token下界差或已知成本比解释节省。",
        "",
        "费用只对该配对两组全部trial均完整时计算差、比值与区间，第三组缺失不阻断完整配对。未知缓存拆分的已知请求按全部输入为缓存计费用下界；未知请求不估作零费用。费用区间是每题平均trial费用差的任务层bootstrap区间，单位CNY/任务试次；不是全轮总费用差的区间。",
        "",
        "```json",
        json.dumps(
            summary["comparisons"], ensure_ascii=False, indent=2, allow_nan=False
        ),
        "```",
        "",
        "## 来源",
        "",
        "```json",
        json.dumps(summary.get("provenance", {}), indent=2),
        "```",
        "",
    ]
    (output / "stats-appendix.md").write_text("\n".join(appendix), encoding="utf-8")
    fields = ["case", "category"] + [
        f"{arm}_{metric}" for arm in ARMS for metric in summary["tasks"][0][arm]
    ]
    with (output / "task-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for task in summary["tasks"]:
            writer.writerow(
                {
                    "case": task["case"],
                    "category": task["category"],
                    **{
                        f"{arm}_{metric}": value
                        for arm in ARMS
                        for metric, value in task[arm].items()
                    },
                }
            )
    figs = output / "figures"
    figs.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.8))
    colors = ("#4C78A8", "#72B7B2", "#E68A2E")
    for axis, metric, title in zip(
        axes,
        METRICS,
        (
            "Task success rate",
            "Mean duration / seconds",
            "Mean API-equivalent cost / CNY",
        ),
    ):
        if metric == "estimatedCostCny" and not all(
            arm["usageComplete"] for arm in summary["arms"].values()
        ):
            axis.text(
                0.5,
                0.5,
                "Incomplete provider usage\nNo three-arm cost comparison",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            continue
        for index, task in enumerate(summary["tasks"]):
            x = np.arange(3) + (index - (len(summary["tasks"]) - 1) / 2) * 0.02
            values = [task[arm][metric] for arm in ARMS]
            axis.plot(x, values, color=".7", alpha=0.5, lw=0.8)
            axis.scatter(x, values, c=colors, s=25, zorder=3)
        axis.set_xticks(range(3), ARMS)
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle(
        f"{summary['design']['tasks']} paired real tasks; each point averages {summary['design']['repeats']} runs"
    )
    fig.tight_layout()
    for extension in ("png", "pdf"):
        fig.savefig(
            figs / f"three-arm-comparison.{extension}", dpi=180, bbox_inches="tight"
        )
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.2))
    means = [
        summary["arms"][arm]["modelCalls"] / summary["arms"][arm]["trials"]
        for arm in ARMS
    ]
    axes[0].bar(ARMS, means, color=colors)
    axes[0].set_ylabel("Mean model requests / trial (all agents)")
    axes[0].set_title("Full strategy resource use")
    axes[0].grid(axis="y", alpha=0.2)
    tasks = summary["tasks"]
    axes[1].barh(
        range(len(tasks)),
        [task["explicit"]["protocolCoverage"] for task in tasks],
        color=colors[2],
    )
    axes[1].set_yticks(range(len(tasks)), [task["case"] for task in tasks])
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 1.05)
    axes[1].set_xlabel("Explicit recipe coverage per task")
    axes[1].set_title("Execution and delivery, not code correctness")
    axes[1].grid(axis="x", alpha=0.2)
    fig.suptitle(
        f"Overall explicit recipe coverage: {summary['mechanismGate']['observedCoverage']:.1%}; minimum gate: 80%"
    )
    fig.tight_layout()
    for extension in ("png", "pdf"):
        fig.savefig(
            figs / f"three-arm-coverage-and-calls.{extension}",
            dpi=180,
            bbox_inches="tight",
        )
    plt.close(fig)
    (output / "figure-catalog.md").write_text(
        "# 图目录\n\nthree-arm-comparison.png/.pdf：每点为一题各组重复均值，线连同题；无误差条。展示成功、耗时、完整API等价成本；不完整成本面板不画零值。应观察收益是否集中于个别任务；判定仍以primary family和协作覆盖门槛为准。图不支持从成功委派子集做因果推断。\n\nthree-arm-coverage-and-calls.png/.pdf：左图包含主/子所有模型请求的每trial均值，右图逐题显示explicit配方真实执行与送达覆盖。无误差条。应区分协作未发生与协作发生但代码未通过；覆盖不足时阻断机制收益结论，不能用正确性反向定义覆盖。调用开销不等于真实账单。\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("artifacts/three-arm-real-2026-10-13")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        mp, rp = args.input / "manifest.json", args.input / "results.json"
        manifest = json.loads(mp.read_text())
        results = json.loads(rp.read_text())
        if results.get("manifestSha256") != sha(mp):
            raise AnalysisError("Result manifest hash mismatch")
        tp = args.input / "tasks.json"
        if tp.exists() and manifest.get("taskSha256") != sha(tp):
            raise AnalysisError("Frozen tasks hash mismatch")
        summary = analyze(manifest, results)
        summary["provenance"] = {
            "manifestSha256": sha(mp),
            "resultsSha256": sha(rp),
            "analysisScriptSha256": sha(Path(__file__)),
            "taskSha256": manifest.get("taskSha256"),
            "productionSha256": manifest.get("productionSha256"),
        }
        if not args.validate_only:
            write_bundle(summary, args.output or args.input)
        print(
            json.dumps(
                {
                    "valid": True,
                    "trials": summary["design"]["trials"],
                    "mechanismGate": summary["mechanismGate"],
                    "wroteBundle": not args.validate_only,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(
            json.dumps({"valid": False, "error": str(error), "wroteBundle": False}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
