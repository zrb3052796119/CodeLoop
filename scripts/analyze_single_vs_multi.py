#!/usr/bin/env python3
"""Analyze a complete frozen 8-task, 3-repeat real-model paired experiment.

No model/provider access. Repeated trials are clustered by task. Incomplete
schedules fail validation before creating output; incomplete usage remains
unknown and blocks cost comparisons, never becoming a zero-cost result.
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
from typing import Any

import numpy as np

ARMS = ("single", "multi")
SEED = 20261013
BOOTSTRAPS = 20000
METRICS = ("success", "durationSeconds", "estimatedCostCny")


class AnalysisError(ValueError):
    """Frozen experiment is not yet suitable for analysis."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, field: str, *, integer: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (integer and int(value) != value)
    ):
        raise AnalysisError(
            f"Invalid nonnegative {'integer' if integer else 'number'}: {field}"
        )
    return float(value)


def validate(manifest: dict, results: dict) -> list[dict]:
    schedule = manifest.get("schedule", [])
    trials = results.get("trials", [])
    if manifest.get("repeats") != 3 or len(schedule) != 48:
        raise AnalysisError(
            "This preregistered analysis requires 8 tasks × 3 repeats × 2 arms = 48 trials."
        )
    expected = {}
    for row in schedule:
        if row.get("id") in expected or row.get("arm") not in ARMS:
            raise AnalysisError("Duplicate schedule id or invalid arm.")
        expected[row["id"]] = row
    cases = sorted({row["case"] for row in schedule})
    cells = Counter((row["case"], row["repeat"], row["arm"]) for row in schedule)
    required = set(itertools.product(cases, range(1, 4), ARMS))
    if len(cases) != 8 or set(cells) != required or any(n != 1 for n in cells.values()):
        raise AnalysisError("Schedule is not a balanced complete 8 × 3 × 2 design.")
    if results.get("expectedTrials") != 48:
        raise AnalysisError("results.expectedTrials differs from frozen schedule.")
    found = {}
    categories = {}
    for row in trials:
        trial_id = row.get("id")
        if trial_id in found or trial_id not in expected:
            raise AnalysisError("Duplicate or unknown result trial id.")
        for key in ("case", "repeat", "arm"):
            if row.get(key) != expected[trial_id][key]:
                raise AnalysisError(f"Frozen assignment mismatch: {trial_id}/{key}")
        if (
            type(row.get("success")) is not bool
            or type(row.get("usageComplete")) is not bool
        ):
            raise AnalysisError(f"Missing boolean success/usageComplete: {trial_id}")
        category = row.get("category")
        if not isinstance(category, str) or not category:
            raise AnalysisError(f"Missing task category: {trial_id}")
        if categories.setdefault(row["case"], category) != category:
            raise AnalysisError(
                f"Task category differs across repeated trials: {trial_id}"
            )
        number(row.get("durationSeconds"), "durationSeconds")
        for key in (
            "inputTokens",
            "outputTokens",
            "cacheReadTokens",
            "totalTokens",
            "modelCalls",
            "delegationAttempts",
        ):
            number(row.get(key), key, integer=True)
        if row["totalTokens"] != row["inputTokens"] + row["outputTokens"]:
            raise AnalysisError(f"Token total mismatch: {trial_id}")
        if row["cacheReadTokens"] > row["inputTokens"]:
            raise AnalysisError(f"Cache tokens exceed input tokens: {trial_id}")
        if row.get("subagentRuns") is not None:
            number(row["subagentRuns"], "subagentRuns", integer=True)
        if row["usageComplete"]:
            number(row.get("estimatedCostCny"), "estimatedCostCny")
            price = manifest.get("price", {})
            price_keys = ("perMillionInput", "perMillionOutput", "perMillionCacheRead")
            if all(key in price for key in price_keys):
                expected_cost = (
                    (row["inputTokens"] - row["cacheReadTokens"]) * price[price_keys[0]]
                    + row["outputTokens"] * price[price_keys[1]]
                    + row["cacheReadTokens"] * price[price_keys[2]]
                ) / 1_000_000
                if not math.isclose(
                    row["estimatedCostCny"], expected_cost, rel_tol=1e-9, abs_tol=1e-12
                ):
                    raise AnalysisError(
                        f"Cost does not match frozen price and usage: {trial_id}"
                    )
        elif row.get("estimatedCostCny") is not None:
            raise AnalysisError(
                f"Incomplete provider usage cannot have complete estimated cost: {trial_id}"
            )
        if row.get("knownCostLowerBoundCny") is not None:
            number(row["knownCostLowerBoundCny"], "knownCostLowerBoundCny")
        if row["arm"] == "single" and (
            row["delegationAttempts"] or row.get("subagentRuns", 0)
        ):
            raise AnalysisError(
                f"Single arm delegated, invalidating the comparison: {trial_id}"
            )
        found[trial_id] = row
    missing = sorted(set(expected) - set(found))
    if missing:
        raise AnalysisError(
            f"Incomplete results ({len(found)}/48); no report or inference produced. Missing: {', '.join(missing)}"
        )
    return [found[row["id"]] for row in schedule]


def describe(values: list[float]) -> dict:
    arr = np.asarray(values, dtype=float)
    return {
        "n": len(arr),
        "mean": float(arr.mean()),
        "sd": float(arr.std(ddof=1)) if len(arr) > 1 else None,
        "p50": float(np.quantile(arr, 0.5)),
        "p95": float(np.quantile(arr, 0.95)),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "total": float(arr.sum()),
    }


def exact_sign_flip(differences: list[float] | np.ndarray) -> float:
    arr = np.asarray(differences, dtype=float)
    observed = abs(float(arr.mean()))
    signs = np.array(list(itertools.product((-1, 1), repeat=len(arr))))
    statistics = np.abs((signs * arr).mean(axis=1))
    tolerance = 1e-12 * max(1.0, observed)
    return float(np.mean(statistics >= observed - tolerance))


def holm(pvalues: list[float | None]) -> list[float | None]:
    # Missing preregistered tests retain their place in the family as p=1.
    ordered = sorted(
        range(len(pvalues)), key=lambda i: 1.0 if pvalues[i] is None else pvalues[i]
    )
    adjusted: list[float | None] = [None] * len(pvalues)
    previous = 0.0
    for rank, index in enumerate(ordered):
        raw = 1.0 if pvalues[index] is None else float(pvalues[index])
        previous = max(previous, min(1.0, (len(pvalues) - rank) * raw))
        if pvalues[index] is not None:
            adjusted[index] = previous
    return adjusted


def paired(single: list[float], multi: list[float], resamples: np.ndarray) -> dict:
    a, b = np.asarray(single), np.asarray(multi)
    delta = b - a
    bootstrap_delta = delta[resamples].mean(axis=1)
    ratio = float(b.mean() / a.mean()) if a.mean() > 0 else None
    sampled_a, sampled_b = a[resamples].mean(axis=1), b[resamples].mean(axis=1)
    ratio_ci = None
    if bool(np.all(sampled_a > 0)):
        ratio_ci = np.quantile(sampled_b / sampled_a, [0.025, 0.975]).tolist()
    return {
        "nTasks": len(a),
        "singleMean": float(a.mean()),
        "multiMean": float(b.mean()),
        "deltaMultiMinusSingle": float(delta.mean()),
        "delta95CI": np.quantile(bootstrap_delta, [0.025, 0.975]).tolist(),
        "ratioMultiOverSingle": ratio,
        "ratio95CI": ratio_ci,
        "pairedTaskDifferences": delta.tolist(),
        "exactSignFlipP": exact_sign_flip(delta),
        "allDifferencesZero": bool(np.all(delta == 0)),
    }


def lower_cost(row: dict) -> float | None:
    if row["usageComplete"]:
        return row["estimatedCostCny"]
    return row.get("knownCostLowerBoundCny")


def arm_summary(rows: list[dict]) -> dict:
    success = sum(row["success"] for row in rows)
    complete = all(row["usageComplete"] for row in rows)
    costs = [lower_cost(row) for row in rows]
    available_costs = [value for value in costs if value is not None]
    known_cost = sum(available_costs) if available_costs else None
    subagents = [row.get("subagentRuns") for row in rows]
    return {
        "trials": len(rows),
        "successes": success,
        "successRate": success / len(rows),
        "durationSeconds": describe([row["durationSeconds"] for row in rows]),
        "usageComplete": complete,
        "usageCompleteTrials": sum(row["usageComplete"] for row in rows),
        "costCny": describe([row["estimatedCostCny"] for row in rows])
        if complete
        else None,
        "knownCostLowerBoundCny": known_cost,
        "costBoundAvailableTrials": sum(x is not None for x in costs),
        "costCnyPerSuccess": known_cost / success if complete and success else None,
        "knownTokenTotals": {
            key: sum(row[key] for row in rows)
            for key in ("inputTokens", "outputTokens", "cacheReadTokens", "totalTokens")
        },
        "tokenTotalsSemantics": "complete_provider_usage"
        if complete
        else "known_lower_bound",
        "modelCalls": describe([row["modelCalls"] for row in rows]),
        "delegationAttempts": sum(row["delegationAttempts"] for row in rows),
        "trialsWithDelegationAttempt": sum(
            row["delegationAttempts"] > 0 for row in rows
        ),
        "delegationAttemptRate": sum(row["delegationAttempts"] > 0 for row in rows)
        / len(rows),
        "knownSubagentRuns": sum(x for x in subagents if x is not None),
        "subagentRunsSemantics": "journal subtask run records, including workflow containers and role leaves; not a count of distinct agents",
        "subagentRunCountAvailableTrials": sum(x is not None for x in subagents),
        "knownTrialsWithSubagentRuns": sum(x is not None and x > 0 for x in subagents),
        "failures": dict(
            Counter(
                row.get("errorClass") or "OracleOrScopeFailure"
                for row in rows
                if not row["success"]
            )
        ),
        "taskErrorCodes": dict(
            Counter(code for row in rows for code in row.get("taskErrorCodes", []))
        ),
    }


def analyze(manifest: dict, results: dict) -> dict:
    trials = validate(manifest, results)
    cases = sorted({row["case"] for row in trials})
    task_rows = []
    for case in cases:
        by_arm = {
            arm: [r for r in trials if r["case"] == case and r["arm"] == arm]
            for arm in ARMS
        }
        entry = {"case": case, "category": by_arm["single"][0]["category"]}
        for arm, rows in by_arm.items():
            entry[arm] = {
                "successes": sum(r["success"] for r in rows),
                "trials": len(rows),
                "success": float(np.mean([r["success"] for r in rows])),
                "durationSeconds": float(np.mean([r["durationSeconds"] for r in rows])),
                "estimatedCostCny": (
                    float(np.mean([r["estimatedCostCny"] for r in rows]))
                    if all(r["usageComplete"] for r in rows)
                    else None
                ),
                "modelCalls": float(np.mean([r["modelCalls"] for r in rows])),
                "trialsWithDelegationAttempt": sum(
                    r["delegationAttempts"] > 0 for r in rows
                ),
                "knownSubagentRuns": sum(r.get("subagentRuns") or 0 for r in rows),
            }
        task_rows.append(entry)
    resamples = np.random.default_rng(SEED).integers(
        0, len(cases), size=(BOOTSTRAPS, len(cases))
    )
    comparisons = {}
    for metric in METRICS:
        values = {arm: [row[arm][metric] for row in task_rows] for arm in ARMS}
        if any(value is None for values_arm in values.values() for value in values_arm):
            comparisons[metric] = {
                "available": False,
                "reason": "Incomplete provider usage; complete cost comparison and inference blocked.",
                "exactSignFlipP": None,
                "holmAdjustedP": None,
            }
        else:
            comparisons[metric] = {
                "available": True,
                **paired(values["single"], values["multi"], resamples),
            }
    adjusted = holm([comparisons[m]["exactSignFlipP"] for m in METRICS])
    for metric, pvalue in zip(METRICS, adjusted):
        comparisons[metric]["holmAdjustedP"] = pvalue
    return {
        "schemaVersion": 1,
        "design": {
            "tasks": 8,
            "repeats": 3,
            "arms": 2,
            "trials": 48,
            "model": manifest.get("model"),
            "unit": "task (within-task repeated trials averaged)",
            "bootstrapSeed": SEED,
            "bootstrapResamples": BOOTSTRAPS,
            "signFlipPermutations": 256,
            "comparison": manifest.get("comparison"),
            "scope": "Model-chosen delegation with identical bounded host approval callbacks for preauthorized write paths; excludes no-approval headless direct-edit budget escalation.",
            "price": manifest.get("price"),
            "limits": manifest.get("limits"),
        },
        "arms": {
            arm: arm_summary([row for row in trials if row["arm"] == arm])
            for arm in ARMS
        },
        "comparisons": comparisons,
        "tasks": task_rows,
        "categories": {
            category: {
                arm: arm_summary(
                    [r for r in trials if r["category"] == category and r["arm"] == arm]
                )
                for arm in ARMS
            }
            for category in sorted({r["category"] for r in trials})
        },
    }


def fmt(value: float | None, digits: int = 3) -> str:
    return "不可用" if value is None else f"{value:.{digits}f}"


def interval(values: list[float], scale: float = 1.0, digits: int = 6) -> str:
    return f"[{values[0] * scale:.{digits}f}, {values[1] * scale:.{digits}f}]"


def token_count(arm: dict, key: str) -> str:
    prefix = "" if arm["usageComplete"] else "≥"
    return f"{prefix}{arm['knownTokenTotals'][key]}"


def render_report(summary: dict) -> str:
    a, b = (summary["arms"][arm] for arm in ARMS)
    lines = [
        "# 单 Agent 与自动委派：真实模型配对评测",
        "",
        f"模型 {summary['design']['model']}；8 个合成小仓库任务，各重复 3 次，两组共 48 次。每组 24 次均纳入统计，包括失败与超时。成功必须通过独立任务验收和写入范围检查。",
        "两组均接入相同的有界宿主 approval callback，只批准预授权写范围。本轮测量该条件下模型是否自主委派；它不覆盖没有 approval 通道的纯 headless 场景，也不覆盖该场景中 direct-edit 预算耗尽后强制升级 workflow 的路径。",
        "",
    ]
    if b["trialsWithDelegationAttempt"] == 0:
        lines += [
            "**本轮未发生实际委派，不能证明真正多 Agent 协作有收益。** 以下组间差异只描述这个审批条件下自动委派开关的表现，不能归因于多个 Agent 协作。",
            "",
        ]
    if not a["usageComplete"] or not b["usageComplete"]:
        lines += [
            "**请求用量不完整，成本不能判优。** 已知 API 等价成本和 token 下界不代表完整消耗，不能据此计算节省比例。",
            "",
        ]
    lines += [
        "| 指标 | 单 Agent | 自动委派开启 |",
        "| --- | ---: | ---: |",
        f"| 完整任务成功 | {a['successes']}/24（{a['successRate']:.1%}） | {b['successes']}/24（{b['successRate']:.1%}） |",
    ]
    for label, key in (
        ("耗时 mean ± SD（秒）", "mean"),
        ("耗时 P50（秒）", "p50"),
        ("耗时 P95（秒）", "p95"),
        ("全部耗时（秒）", "total"),
    ):
        values = [fmt(x["durationSeconds"][key]) for x in (a, b)]
        if key == "mean":
            values = [
                f"{value} ± {fmt(x['durationSeconds']['sd'])}"
                for value, x in zip(values, (a, b))
            ]
        lines.append(f"| {label} | {' | '.join(values)} |")
    cost = [
        fmt(x["costCny"]["total"], 6)
        if x["costCny"]
        else (
            f"≥{fmt(x['knownCostLowerBoundCny'], 6)}，不完整"
            if x["knownCostLowerBoundCny"] is not None
            else "缺用量，不可用"
        )
        for x in (a, b)
    ]
    lines += [
        f"| 总 API 等价成本（CNY） | {' | '.join(cost)} |",
        f"| API 等价成本 / 成功任务（CNY） | {fmt(a['costCnyPerSuccess'], 6)} | {fmt(b['costCnyPerSuccess'], 6)} |",
        f"| 已记录输入 token | {token_count(a, 'inputTokens')} | {token_count(b, 'inputTokens')} |",
        f"| 已记录输出 token | {token_count(a, 'outputTokens')} | {token_count(b, 'outputTokens')} |",
        f"| 已记录输入 + 输出 token | {token_count(a, 'totalTokens')} | {token_count(b, 'totalTokens')} |",
        f"| 模型调用总数 | {int(a['modelCalls']['total'])} | {int(b['modelCalls']['total'])} |",
        f"| 有委派尝试的 trial | {a['trialsWithDelegationAttempt']}/24 | {b['trialsWithDelegationAttempt']}/24 |",
        f"| 子任务运行记录（含 workflow 容器） | {a['knownSubagentRuns']} | {b['knownSubagentRuns']} |",
        "",
        "耗时包括主 Agent、子 Agent 与调度等待；费用按冻结官方标准 API 单价乘 provider usage 折算。实际调用使用 Coding 套餐接口，API 等价成本不是套餐实际扣费、真实边际成本或账户账单；套餐真实成本不能从本接口推得。token/费用保留已知失败消耗；任何请求缺 usage 时，该组输入、输出及合计 token 都只能作为已知下界（≥），不能称完备总用量。缓存命中按独立价格计。费用来自请求级 provider usage，不累加 workflow 汇总与角色叶子的 journal 记录，避免重复计费。",
        "",
        "## 配对效果",
        "",
        "差值均为“自动委派开启 − 单 Agent”；成功率正值更好，耗时与费用负值更好。先在每题内平均 3 次，再对 8 个任务求差和区间。",
        "",
        "| 对比 | 配对平均差 | 95% CI | 多 / 单均值比 | 精确 p | Holm p |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    labels = {
        "success": ("成功率（百分点）", 100.0),
        "durationSeconds": ("耗时（秒/次）", 1.0),
        "estimatedCostCny": ("API 等价成本（CNY/次）", 1.0),
    }
    for metric, (label, scale) in labels.items():
        comp = summary["comparisons"][metric]
        if not comp["available"]:
            lines.append(f"| {label} | 不完整，不比较 | — | — | — | — |")
        else:
            lines.append(
                f"| {label} | {comp['deltaMultiMinusSingle'] * scale:.6f} | {interval(comp['delta95CI'], scale)} | {fmt(comp['ratioMultiOverSingle'])} | {fmt(comp['exactSignFlipP'], 5)} | {fmt(comp['holmAdjustedP'], 5)} |"
            )
    lines += [
        "",
        "## 调度是否真正发生",
        "",
        f"自动委派组 {b['trialsWithDelegationAttempt']}/24 次尝试 task 委派（{b['delegationAttemptRate']:.1%}），已记录 {b['knownSubagentRuns']} 条子任务运行记录（含 workflow 容器），记录计数可用 {b['subagentRunCountAvailableTrials']}/24 次。",
        "journal 的 subagentRuns 同时包含 workflow 汇总容器与 Executor 等角色叶子，因此一次 workflow 委派可能对应多条记录；它不能解释为实际子 Agent 数，也不能用来计算费用。是否发起委派主要看 delegationAttempts > 0；发起尝试仍不等于成功执行。",
    ]
    if b["trialsWithDelegationAttempt"] == 0:
        lines.append(
            "本轮没有实际委派。观察到的组间差异不能归因于多 Agent 协作；这只能评价当前自动调度开关在这批任务上的表现，不能回答多个 Agent 真正协作时是否更有效。额外工具定义、提示词长度以及模型重复运行波动都可能影响耗时与用量。"
        )
    elif b["knownSubagentRuns"] == 0:
        lines.append(
            "有委派尝试，但未记到子任务运行记录。应先检查拒绝/失败码与记录完整性，不能把本轮差异解释为协作收益。"
        )
    else:
        lines.append(
            "本轮保留自动调度未委派的样本；测到的是预授权写范围条件下的调度策略表现，不能据事后挑选出的委派样本推断委派的因果效果。"
        )
    if not a["usageComplete"] or not b["usageComplete"]:
        lines.append(
            "本轮存在请求用量缺失，完整成本不可比：不计算费用差、节省比例或费用显著性；费用仅列已知下界。输入/输出 token 下界之间的差也不能当作真实资源节省。"
        )
    lines += [
        "",
        "## 任务分类与终态/验收失败",
        "",
        "| 任务类别 | 单成功 | 多成功 | 单均时/秒 | 多均时/秒 | 多组委派 trial |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, arms in summary["categories"].items():
        aa, bb = (arms[arm] for arm in ARMS)
        lines.append(
            f"| {category} | {aa['successes']}/{aa['trials']} | {bb['successes']}/{bb['trials']} | {fmt(aa['durationSeconds']['mean'])} | {fmt(bb['durationSeconds']['mean'])} | {bb['trialsWithDelegationAttempt']}/{bb['trials']} |"
        )
    for arm in ARMS:
        info = summary["arms"][arm]
        lines.append(
            f"\n{arm}：终态/验收失败分类 {json.dumps(info['failures'], ensure_ascii=False)}；task 错误码 {json.dumps(info['taskErrorCodes'], ensure_ascii=False)}。"
        )
    lines += [
        "",
        "以上分类来自 trial 的 errorClass，只描述终态或验收结果，不是请求级根因统计。例如 oracle_failed 表示交付未通过验收，不能单凭此标签排除此前请求超时。请求级超时、缺 usage 的分布和审计解释见 [final-audit.md](final-audit.md) 与 [exploratory-mechanisms.json](exploratory-mechanisms.json)；后者仅作事后机制描述，不改变预注册检验。",
        "",
        "受限权限题要求完成正确修改，纯拒绝不算成功；原子批处理题检查逻辑上的全有或全无，不是并发写回竞态测试。",
        "",
        "## 可支持的结论与局限",
        "",
        "这批数据描述的是 8 个固定合成小仓库任务，独立分析单位为 8 题，48 次执行不是 48 个独立任务。3 次重复用于估计每题表现。网络、缓存和服务端推理仍有波动；相同模型名称不保证服务端权重快照永久固定。",
        "宿主 approval callback 使本轮使用带审批通道的权限路径。因此零委派只说明本轮条件下未激活委派，不能写成所有 production 自动路由失效或强制 workflow 升级失败。",
        "95% 区间是任务层 bootstrap 的 percentile 区间，样本少时不稳定；精确符号翻转检验需要零假设下任务差值的符号可交换。固定题集不支持生产任务总体的强泛化。未显著不证明等效；全部成功的零差也不证明两组能力相同。",
        "",
    ]
    for metric, (label, scale) in labels.items():
        comp = summary["comparisons"][metric]
        if comp["available"]:
            verdict = (
                "在该条件检验下达到 Holm 0.05 阈值"
                if comp["holmAdjustedP"] < 0.05
                else "未达到 Holm 0.05 阈值"
            )
            lines += [
                f"- Claim：{label}平均差 {comp['deltaMultiMinusSingle'] * scale:.6f}，{verdict}。",
                f"  Source evidence：summary.json / comparisons.{metric}，task-summary.csv。Allowed wording：限本次固定题集的观察差异。Forbidden stronger wording：多 Agent 普遍更强/更差或已证明等效。Uncertainty：任务层 95% CI {interval(comp['delta95CI'], scale)}。Next check：扩大真实仓库任务、保持配对预算。Decision：weaken。",
            ]
        else:
            lines.append(
                f"- Claim：{label}数据不完整。Source evidence：usageComplete。Allowed wording：已知费用下界。Forbidden stronger wording：节省费用百分比。Uncertainty：缺请求用量。Next check：补 provider 账单证据。Decision：discard。"
            )
    return "\n".join(lines) + "\n"


def write_figures(summary: dict, destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}
    )
    destination.mkdir(parents=True, exist_ok=True)
    tasks = summary["tasks"]
    colors = ("#4C78A8", "#E68A2E")
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 4.8))
    for axis, metric, title in zip(
        axes,
        METRICS,
        (
            "Task success rate",
            "Mean duration (seconds)",
            "Mean API-equivalent cost (CNY)",
        ),
    ):
        if not summary["comparisons"][metric]["available"]:
            axis.text(
                0.5,
                0.5,
                "Cost comparison unavailable\nIncomplete provider usage",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            continue
        for i, task in enumerate(tasks):
            offset = (i - 3.5) * 0.025
            values = [task[arm][metric] for arm in ARMS]
            axis.plot(
                [offset, 1 + offset], values, color="#9D9D9D", alpha=0.55, linewidth=0.8
            )
            axis.scatter([offset, 1 + offset], values, c=colors, s=30, zorder=3)
        axis.set_xticks([0, 1], ["Single", "Auto multi"])
        axis.set_title(title)
        axis.set_xlim(-0.25, 1.25)
        axis.grid(axis="y", alpha=0.2)
        if metric == "success":
            axis.set_ylim(-0.03, 1.04)
    fig.suptitle("8 paired tasks; each point averages 3 runs", y=1.01)
    fig.text(
        0.5,
        -0.02,
        "Lines connect the same task. Failures retained. Auto multi means delegation enabled, not forced.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(
            destination / f"figure-01-paired-comparison.{suffix}",
            dpi=180,
            bbox_inches="tight",
        )
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.7))
    positions = np.arange(len(tasks))
    for offset, arm, color in (
        (-0.18, "single", colors[0]),
        (0.18, "multi", colors[1]),
    ):
        axes[0].barh(
            positions + offset,
            [task[arm]["modelCalls"] for task in tasks],
            height=0.34,
            color=color,
            label=arm,
        )
    axes[0].set_yticks(positions, [task["case"] for task in tasks])
    axes[0].set_xlabel("Mean model calls per trial (all agents)")
    axes[0].legend()
    axes[1].barh(
        positions,
        [task["multi"]["trialsWithDelegationAttempt"] for task in tasks],
        color=colors[1],
    )
    axes[1].set_yticks(positions, [task["case"] for task in tasks])
    axes[1].set_xlim(0, 3.3)
    axes[1].set_xticks([0, 1, 2, 3])
    axes[1].set_xlabel("Auto-multi trials attempting delegation / 3")
    for axis in axes:
        axis.invert_yaxis()
        axis.grid(axis="x", alpha=0.2)
    fig.suptitle("What the automatic scheduler actually did")
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(
            destination / f"figure-02-delegation-and-calls.{suffix}",
            dpi=180,
            bbox_inches="tight",
        )
    plt.close(fig)


def write_bundle(summary: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "analysis-report.md").write_text(render_report(summary), encoding="utf-8")
    appendix = [
        "# 统计附录",
        "",
        "统计单位为 8 题；每题每组 3 次均值。使用全部终态 trial；没有事后剔除失败样本。",
        "",
        "三个预注册对比统一使用双侧精确配对符号翻转检验，枚举 2^8=256 个符号向量，以绝对平均差至少同观察值一样极端计 p。此检验依赖零假设下差值的符号可交换，不要求正态；无法从 8 题验证该可交换性。零差与 ties 保留。",
        "",
        f"按任务成对重采样 {BOOTSTRAPS:,} 次，随机种子 {SEED}。95% CI 为 percentile [2.5%,97.5%]；单/多均值一起重采样以保留配对。成功差使用比例，报告转为百分点。比值为两组任务均值之比；任一 bootstrap 分母为零则不报比值区间。",
        "",
        "success、durationSeconds、estimatedCostCny 是同一 Holm family；费用缺失时不计算其 p，校正保留 family size=3。阈值 0.05。描述分类不做追加假设检验。费用缺失不插补零，也不只挑完整样本做费用推断。",
        "",
        "任务层 percentile bootstrap CI 不是精确符号翻转检验的反演区间，也未做 Holm 同时校正，两者不能互相替代。只有 8 题且零差较多时，可能出现 bootstrap CI 不跨 0，但 Holm 校正检验仍不显著；此时不能仅凭 CI 宣称显著。结论遵守预注册的 Holm p < 0.05 门槛，并保留小样本、固定合成任务和检验假设的限制。",
        "",
        "输入、输出、缓存与合计 token 来自请求级 provider usage；任一请求缺 usage 时，所属组汇总只是已知下界。subagentRuns 是 journal 子任务运行记录计数，包含 workflow 汇总容器与角色叶子，不等于实际子 Agent 数；不参与费用求和。",
        "",
        "trial 耗时 SD 为 sample SD（ddof=1），P50/P95 为 NumPy 线性插值分位数；这些是运行层描述，不是独立任务层显著性检验。只有 8 个固定合成任务，区间不保证外推覆盖率。预算是准入约束，末次响应 token 可能超出余额；以 manifest 为准。",
        "",
        "## 机器可读精确统计",
        "",
        "```json",
        json.dumps(
            summary["comparisons"], ensure_ascii=False, indent=2, allow_nan=False
        ),
        "```",
        "",
        "## 来源与完整性",
        "",
        "```json",
        json.dumps(summary.get("provenance", {}), ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (output / "stats-appendix.md").write_text("\n".join(appendix), encoding="utf-8")
    fields = ["case", "category"] + [
        f"{arm}_{key}" for arm in ARMS for key in summary["tasks"][0][arm]
    ]
    fields += ["delta_success", "delta_durationSeconds", "delta_estimatedCostCny"]
    with (output / "task-summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for task in summary["tasks"]:
            row = {"case": task["case"], "category": task["category"]}
            row.update(
                {
                    f"{arm}_{key}": value
                    for arm in ARMS
                    for key, value in task[arm].items()
                }
            )
            for metric in METRICS:
                a, b = (task[arm][metric] for arm in ARMS)
                row["delta_" + metric] = (
                    b - a if a is not None and b is not None else None
                )
            writer.writerow(row)
    write_figures(summary, output / "figures")
    multi = summary["arms"]["multi"]
    catalog = f"""# 图目录

两图的数据来自 results.json，经 manifest 校验后生成 summary.json 与 task-summary.csv。每张图都有同名 PNG/PDF。英文坐标避免环境中文字体缺字。

## figure-01-paired-comparison

- 用途：逐题比较成功率、耗时和估值费用，展示平均值是否被少数题主导。
- 变量：每题每组 3 次均值；连线只连接同题。没有误差条；推断 CI 见统计表，不能把点间差距误认为置信区间。
- 图注要求：8 固定任务、3 次重复、失败保留、自动委派未强制、费用是标准 API 单价等价成本而非 Coding 套餐实际扣费。
- 应观察：看同题连线方向与差距是否一致；准确效应见 summary.json/comparisons，不能只看总体柱高。
- 对判断的影响：分辨收益是否局限于特定题目；不能根据事后子集宣称总体优势。
- 限制：小合成题集；缺完整费用时对应面板明确不可比较。

## figure-02-delegation-and-calls

- 用途：解释配置开启后是否发生委派，以及总调用消耗。
- 变量：左图每题总模型调用数均值（主与子）；右图 multi 组发生委派尝试的次数/3。
- 图注要求：尝试 task 不等于启动子 Agent，更不等于工作流成功。已记录子任务运行记录共 {multi["knownSubagentRuns"]} 条（含 workflow 汇总容器与角色叶子），计数可用 {multi["subagentRunCountAvailableTrials"]}/24 条 trial。这不是实际子 Agent 数，且不用于费用求和。
- 应观察：本轮 multi 共 {multi["trialsWithDelegationAttempt"]}/24 次尝试委派；按题观察调用开销与委派是否对应。
- 对判断的影响：无实际委派时，不能以两组名称推断协作收益；有委派时仍需完整成功率和成本共同判断。
- 限制：描述性图，无事后委派子集因果分析。
"""
    (output / "figure-catalog.md").write_text(catalog, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("artifacts/single-vs-multi-2026-10-13")
    )
    parser.add_argument("--output", type=Path, help="Defaults to the input directory.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        manifest_path, results_path = (
            args.input / "manifest.json",
            args.input / "results.json",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        results = json.loads(results_path.read_text(encoding="utf-8"))
        if results.get("manifestSha256") != digest(manifest_path):
            raise AnalysisError("Result manifest hash does not match frozen manifest.")
        tasks_path = args.input / "tasks.json"
        if tasks_path.exists() and manifest.get("taskSha256") != digest(tasks_path):
            raise AnalysisError("Tasks hash does not match frozen manifest.")
        if tasks_path.exists():
            task_document = json.loads(tasks_path.read_text(encoding="utf-8"))
            category_by_case = {
                case["id"]: case["category"] for case in task_document["cases"]
            }
            for row in results.get("trials", []):
                category = category_by_case[row["case"]]
                if row.get("category") is not None and row["category"] != category:
                    raise AnalysisError(
                        f"Category differs from frozen task: {row['id']}"
                    )
                # A hard-stop fallback can lack telemetry but its task category
                # remains a known property of the frozen manifest-bound task.
                row["category"] = category
        summary = analyze(manifest, results)
        summary["provenance"] = {
            "manifestSha256": digest(manifest_path),
            "resultsSha256": digest(results_path),
            "analysisScriptSha256": digest(Path(__file__)),
            "taskSha256": manifest.get("taskSha256"),
            "productionSha256": manifest.get("productionSha256"),
        }
        if not args.validate_only:
            write_bundle(summary, args.output or args.input)
        print(
            json.dumps(
                {
                    "valid": True,
                    "trials": 48,
                    "tasks": 8,
                    "wroteBundle": not args.validate_only,
                    "successes": {
                        arm: summary["arms"][arm]["successes"] for arm in ARMS
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (AnalysisError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"valid": False, "error": str(error), "wroteBundle": False},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
