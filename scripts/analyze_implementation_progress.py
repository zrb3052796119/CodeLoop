#!/usr/bin/env python3
"""Frozen successor analysis: unchanged task-level statistics, direct workflow recipe.

The small engineering runs cannot enter this analyzer. Provider costs with missing
receipts remain unavailable, and every formal failure stays in its assigned group.
"""

from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path
import sys
from types import FunctionType
import zipfile

try:
    from scripts import analyze_three_arm_real as legacy
    from scripts.audit_implementation_progress import Audit
except ModuleNotFoundError:
    import analyze_three_arm_real as legacy
    from audit_implementation_progress import Audit

ARMS = legacy.ARMS
AnalysisError = legacy.AnalysisError
nonnegative = legacy.nonnegative
sha = legacy.sha
REGRESSION_PROTOCOL = "direct-workflow-regression-v1"


def validate_regression_result(row):
    """Check the prospective common judge fields without rescoring old runs."""
    trial_id = row["id"]
    passed = row.get("regressionTestPassed")
    result = row.get("regressionTest")
    if type(passed) is not bool or not isinstance(result, dict):
        raise AnalysisError(f"Missing regression test result: {trial_id}")
    if type(result.get("passed")) is not bool or result["passed"] != passed:
        raise AnalysisError(f"Regression test flags disagree: {trial_id}")
    if not isinstance(result.get("status"), str) or not result["status"]:
        raise AnalysisError(f"Missing regression test status: {trial_id}")
    counts = result.get("counts")
    if not isinstance(counts, dict):
        raise AnalysisError(f"Missing regression test counts: {trial_id}")
    for key in ("collected", "passed", "failed", "errors", "skipped", "xfailed"):
        nonnegative(counts.get(key), f"regressionTest.counts.{key}", integer=True)
    if "xpassed" in counts:
        nonnegative(counts["xpassed"], "regressionTest.counts.xpassed", integer=True)
    nonnegative(result.get("durationSeconds"), "regressionTest.durationSeconds")
    if passed and not (
        result["status"] == "passed"
        and counts["collected"] >= 1
        and counts["passed"] == counts["collected"]
        and all(counts[key] == 0 for key in ("failed", "errors", "skipped", "xfailed"))
        and counts.get("xpassed", 0) == 0
    ):
        raise AnalysisError(f"Regression pass lacks all-passed tests: {trial_id}")


def validate(manifest, results):
    protocol = manifest.get("protocolVersion")
    regression_required = protocol == REGRESSION_PROTOCOL
    if (
        manifest.get("phase") != "formal"
        or protocol not in ("direct-workflow-v1", REGRESSION_PROTOCOL)
        or (regression_required and manifest.get("schemaVersion") != 5)
        or (not regression_required and manifest.get("schemaVersion") == 5)
    ):
        raise AnalysisError(
            "Only the formal direct-workflow regression is an effect comparison"
        )
    if (
        not isinstance(manifest.get("engineeringGate"), dict)
        or manifest["engineeringGate"].get("passed") is not True
    ):
        raise AnalysisError("Formal experiment lacks a passed engineering gate")
    schedule = manifest.get("schedule", [])
    repeats = manifest.get("repeats")
    if type(repeats) is not int or repeats < 1 or not schedule:
        raise AnalysisError("Missing frozen schedule or positive repeat count")
    cases = sorted({row["case"] for row in schedule})
    if len(cases) != 8 or repeats != 2 or len(schedule) != 48:
        raise AnalysisError(
            "Formal regression requires eight tasks, two repeats and 48 trials"
        )
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
            "deliveredWorkflowResults",
            "actualAnalystLeafRuns",
            "deliveredAnalystLeafResults",
            "externalAnalysisLeafRuns",
            "externalLeafRuns",
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
        for subset, parent in (
            ("actualAnalystLeafRuns", "actualLeafRuns"),
            ("deliveredAnalystLeafResults", "actualAnalystLeafRuns"),
            ("externalAnalysisLeafRuns", "externalLeafRuns"),
            ("externalLeafRuns", "actualLeafRuns"),
        ):
            if row[subset] > row[parent]:
                raise AnalysisError(f"Invalid role subset: {trial_id}/{subset}")
        protocol = bool(
            row["implementationLeafRuns"] >= 1
            and row["deliveredWorkflowResults"] >= 1
            and row["externalLeafRuns"] == 0
        )
        if row["collaborationProtocolSatisfied"] != protocol:
            raise AnalysisError(f"Direct workflow protocol fields disagree: {trial_id}")
        if protocol and (row["actualLeafRuns"] < 1 or row["deliveredLeafResults"] < 1):
            raise AnalysisError(f"Direct workflow lacks delivered Executor: {trial_id}")
        if row["leafModelCalls"] > row["modelCalls"]:
            raise AnalysisError(f"Leaf calls exceed total model calls: {trial_id}")
        expected_success = row["oracleSuccess"] and row["scopePassed"]
        if regression_required:
            validate_regression_result(row)
            expected_success = expected_success and row["regressionTestPassed"]
        if row["arm"] == "explicit":
            expected_success = (
                expected_success and row["collaborationProtocolSatisfied"]
            )
        if row["success"] != expected_success:
            raise AnalysisError(
                f"Success does not match oracle/scope/protocol/regression: {trial_id}"
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


def bound(function, **overrides):
    """Reuse unchanged computation with private globals; do not mutate legacy imports."""
    return FunctionType(
        function.__code__,
        {**function.__globals__, **overrides},
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )


def arm_summary(rows):
    result = legacy.arm_summary(rows)
    result["regressionTestsPassed"] = (
        sum(row["regressionTestPassed"] for row in rows)
        if all(type(row.get("regressionTestPassed")) is bool for row in rows)
        else None
    )
    for key in (
        "deliveredWorkflowResults",
        "actualAnalystLeafRuns",
        "deliveredAnalystLeafResults",
        "externalAnalysisLeafRuns",
        "externalLeafRuns",
    ):
        result[key] = sum(row[key] for row in rows)
    return result


def analyze(manifest, results):
    summary = bound(legacy.analyze, validate=validate, arm_summary=arm_summary)(
        manifest, results
    )
    summary["design"].update(
        protocolVersion=manifest["protocolVersion"],
        phase="formal",
        calibrationTasksReused=manifest.get("reusedCalibrationCases", []),
        interpretation="Regression on reused historical tasks; not an independent generalization set",
    )
    return summary


def report(summary):
    text = legacy.report(summary)
    old = "按冻结配方派发真实分析叶子，再派发 workflow 实现叶子并接收结果。"
    new = "按冻结配方直接派发 workflow，由实际 Executor 实现并经所属 workflow 接收结果；内部 Analyst 按生产策略可选，不额外派外部 Plan。"
    old_contract = "协作协议合规只要求真实分析与实现调用及其结果送达/消费，不要求生成代码已经正确；"
    new_contract = "协作协议合规要求真实 Executor 调用及结果经其所属 workflow 送达，不要求 Analyst 必跑或生成代码已经正确；"
    if text.count(old) != 1 or text.count(old_contract) != 1:
        raise AnalysisError(
            "Shared report wording changed; review the frozen successor adapter"
        )
    text = text.replace(old, new).replace(old_contract, new_contract)
    if summary["design"]["protocolVersion"] == REGRESSION_PROTOCOL:
        old_success = "完整成功要求 oracle 通过且写范围合规；explicit 另须满足冻结协作协议。"
        new_success = (
            "完整成功要求原独立 oracle、新增回归测试均通过且写范围合规；"
            "explicit 另须满足冻结协作协议。"
        )
        table_header = "| --- | ---: | ---: | ---: |\n"
        if text.count(old_success) != 1 or table_header not in text:
            raise AnalysisError("Shared success report wording changed")
        values = [
            f"{summary['arms'][arm]['regressionTestsPassed']}/{summary['arms'][arm]['trials']}"
            for arm in ARMS
        ]
        text = text.replace(old_success, new_success).replace(
            table_header,
            table_header + "| 新增回归测试通过 | " + " | ".join(values) + " |\n",
            1,
        )
    note = (
        "本轮是修复后的历史任务回归对照，包含工程校准中使用过的3道题；不是独立泛化集。"
        "工程6条、旧实验和此前失败试跑均未混入正式48条。\n\n"
        "耗时覆盖全部成功与失败试次，表示策略占用的执行墙钟；耗时较短不等于更早交付成功修复，不能把各请求时长相加当提速证据。\n\n"
    )
    if summary["design"]["protocolVersion"] == REGRESSION_PROTOCOL:
        note += (
            "三组使用共同的新增测试裁判：必须至少收集 1 项测试且全部真实 passed，"
            "skip、xfail 或 xpass 不算通过。宿主在 Agent 停止并清理后运行原隐藏/基线验收及新增测试；"
            "交付副本复制、两类验收、回执与进程清理共用总上限 120 秒，"
            "不计入 600 秒 Agent 执行预算或模型调用预算。"
            "oracleSuccess 保留原独立验收含义；新增要求仅适用于本协议，"
            "不重判历史工程轮次。\n\n"
        )
    if all(arm["successes"] == 0 for arm in summary["arms"].values()):
        note += "**全部成功率为零，存在地板效应。** 成功差的退化 [0,0] 区间不能证明组别等效，也不能据此确认协作有效或无效。\n\n"
    return text.replace(
        "# 真实仓库三组配对评测\n\n", "# 实现进展修复后的回归对照\n\n" + note, 1
    )


def write_bundle(summary, output):
    return bound(legacy.write_bundle, report=report)(summary, output)


def load_and_analyze(directory: Path):
    manifest_path, results_path = (
        directory / "manifest.json",
        directory / "results.json",
    )
    manifest = json.loads(manifest_path.read_text())
    results = json.loads(results_path.read_text())
    if results.get("manifestSha256") != sha(manifest_path):
        raise AnalysisError("Result manifest hash mismatch")
    if manifest.get("taskSha256") != sha(directory / "tasks.json"):
        raise AnalysisError("Frozen tasks hash mismatch")
    # Validate the entire design before reading private records or generating output.
    validate(manifest, results)
    audit = Audit(directory, allow_partial=False).run()
    if (
        audit.get("valid") is not True
        or audit.get("mode") != "final"
        or audit.get("auditedTrials") != 48
    ):
        raise AnalysisError("Complete valid independent final audit is required")
    with zipfile.ZipFile(directory / "frozen-code.zip") as archive:
        for name in (
            "analyze_implementation_progress.py",
            "analyze_three_arm_real.py",
            "audit_implementation_progress.py",
            "audit_three_arm_real.py",
        ):
            if (
                archive.read("scripts/" + name)
                != (Path(__file__).parent / name).read_bytes()
            ):
                raise AnalysisError(
                    "Frozen analysis/audit implementation differs: " + name
                )
    summary = analyze(manifest, results)
    summary["provenance"] = {
        "manifestSha256": sha(manifest_path),
        "resultsSha256": sha(results_path),
        "analysisScriptSha256": sha(Path(__file__)),
        "sharedAnalysisScriptSha256": sha(Path(legacy.__file__)),
        "taskSha256": manifest["taskSha256"],
        "productionSha256": manifest["productionSha256"],
        "auditSha256": legacy.hashlib.sha256(
            json.dumps(audit, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "engineeringGate": manifest["engineeringGate"],
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        summary = load_and_analyze(args.input)
        if not args.validate_only:
            write_bundle(summary, args.output or args.input)
        print(
            json.dumps(
                {
                    "valid": True,
                    "trials": summary["design"]["trials"],
                    "mechanismGate": summary["mechanismGate"],
                    "wroteBundle": not args.validate_only,
                }
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(
            json.dumps({"valid": False, "error": str(error), "wroteBundle": False}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
