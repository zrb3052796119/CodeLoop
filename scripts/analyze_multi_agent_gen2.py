#!/usr/bin/env python3
"""Six-cell continuation gate and descriptive request costs, never superiority."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

try:
    from scripts.audit_multi_agent_gen2 import Audit, CASES
except ModuleNotFoundError:
    from audit_multi_agent_gen2 import Audit, CASES

ARMS = ("single", "explicit")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assess(manifest: dict, rows: list[dict], audit: dict) -> dict:
    cells = Counter((row.get("case"), row.get("repeat"), row.get("arm")) for row in rows)
    expected = {(case, 1, arm) for case in CASES for arm in ARMS}
    passed = [row for row in rows if row.get("success") is True
              and row.get("oracleSuccess") is True and row.get("regressionTestPassed") is True
              and row.get("scopePassed") is True and bool(row.get("changedSourceFiles"))]
    by_arm = Counter(row["arm"] for row in passed)
    by_case = Counter(row["case"] for row in passed)
    explicit = [row for row in rows if row.get("arm") == "explicit"]
    conditions = {
        "gen2Protocol": manifest.get("phase") == "gen2" and manifest.get("protocolVersion") == "multi-agent-gen2-v1",
        "sixTerminalRows": len(rows) == 6 and set(cells) == expected and all(n == 1 for n in cells.values()),
        "validFinalAudit": audit.get("valid") is True and audit.get("mode") == "final"
            and audit.get("auditedTrials") == 6 and audit.get("expectedTrials") == 6,
        "cleanScopeAndReceipts": len(rows) == 6 and all(
            row.get("scopePassed") is True and row.get("processCleanup", {}).get("cleanupComplete") is True
            and row.get("usageComplete") is True and row.get("unknownUsageCalls") == 0
            and not row.get("ledgerDamaged") and not row.get("recoveryEvidenceErrors") for row in rows),
        "atLeastFourRealRepairs": len(passed) >= 4,
        "eachArmAtLeastTwo": all(by_arm[arm] >= 2 for arm in ARMS),
        "eachCaseAtLeastOne": all(by_case[case] >= 1 for case in CASES),
        "allExplicitExecutorDelivered": len(explicit) == 3 and all(
            row.get("collaborationProtocolSatisfied") is True and row.get("implementationLeafRuns", 0) >= 1
            for row in explicit),
    }
    return {"schemaVersion": 1, "passed": all(conditions.values()), "conditions": conditions,
            "realRepairCount": len(passed), "repairsByArm": {arm: by_arm[arm] for arm in ARMS},
            "repairsByCase": {case: by_case[case] for case in CASES},
            "interpretation": "Gen-2 engineering continuation gate only; incomparable with all historical results; no superiority or model-only causality claim."}


def request_costs(directory: Path, rows: list[dict]) -> dict:
    roles = defaultdict(lambda: {"requests": 0, "knownTokens": 0, "unknownTokenRequests": 0,
                                  "requestSeconds": 0.0, "missingDurationRequests": 0})
    reached, edited = set(), set()
    totals = Counter()
    for row in rows:
        path = directory / "private" / row["id"] / "transport-summary.json"
        if not path.exists():
            continue
        requests = json.loads(path.read_text()).get("requests", [])
        for request in requests:
            role = str(request.get("role") or "unknown").lower()
            key = row["arm"] + ":" + role
            value = roles[key]
            value["requests"] += 1
            totals[row["arm"]] += 1
            usage = request.get("usage", {})
            inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
            if (usage.get("source") == "provider" and type(inputs) is int and type(outputs) is int
                    and inputs >= 0 and outputs >= 0):
                value["knownTokens"] += inputs + outputs
            else:
                value["unknownTokenRequests"] += 1
            seconds = request.get("seconds")
            if type(seconds) in (int, float) and seconds >= 0:
                value["requestSeconds"] += seconds
            else:
                value["missingDurationRequests"] += 1
            if role == "executor":
                reached.add(row["id"])
                if any(call.get("name") in {"edit_file", "write_file"} for call in request.get("toolCalls", [])):
                    edited.add(row["id"])
    for arm in ARMS:
        for role in ("main", "plan", "analyst", "executor"):
            roles[arm + ":" + role]
    executor_calls = roles["explicit:executor"]["requests"]
    total = totals["explicit"]
    return {"byArmAndRole": dict(sorted(roles.items())), "executorReachedTrials": sorted(reached),
            "executorEditRequestTrials": sorted(edited),
            "explicitNonExecutorRequestFraction": (total - executor_calls) / total if total else None,
            "notes": "Tokens remain known lower bounds when unknownTokenRequests>0; requestSeconds sums request latency, not phase wall time. Edit requests are attempts, not accepted writes. Non-Executor share is descriptive, not a protocol-tax gate."}


def analyze(directory: Path) -> dict:
    manifest_bytes = (directory / "manifest.json").read_bytes()
    results_bytes = (directory / "results.json").read_bytes()
    manifest, results = json.loads(manifest_bytes), json.loads(results_bytes)
    audit = Audit(directory, allow_partial=False).run()
    rows = results.get("trials", [])
    gate = assess(manifest, rows, audit)
    gate.update(manifestSha256=digest(manifest_bytes), resultsSha256=digest(results_bytes),
                auditSha256=digest(json.dumps(audit, sort_keys=True, ensure_ascii=False).encode()))
    costs = request_costs(directory, rows)
    for name, value in (("gen2-audit.json", audit), ("gen2-gate.json", gate), ("gen2-request-costs.json", costs)):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Gen-2 工程六格结果", "", "本代与所有历史数字不可比；六格只判断继续/停止，不判断统计优越性。", "",
             "判定：" + ("通过工程门，可继续 Phase 2。" if gate["passed"] else "未通过工程门；当前模型、任务集、预算和协作策略组合尚未证明收益。"), "",
             f"成功 {gate['realRepairCount']}/6；single {gate['repairsByArm']['single']}/3；explicit {gate['repairsByArm']['explicit']}/3。", "",
             "| 题目 | 组别 | 成功 | 请求 | 已知 token | Agent 秒 | 新增测试 |", "| --- | --- | --- | ---: | ---: | ---: | --- |"]
    for row in rows:
        token_prefix = "" if row.get("tokenUsageComplete") else "≥"
        lines.append(f"| {row['case']} | {row['arm']} | {row.get('success')} | {row.get('modelCalls')} | {token_prefix}{row.get('totalTokens')} | {row.get('durationSeconds', 0):.1f} | {row.get('regressionTestPassed')} |")
    lines += ["", "未满足条件：" + ("、".join(name for name, passed in gate["conditions"].items() if not passed) or "无"), "",
              f"Executor 实际开始模型请求的试次：{len(costs['executorReachedTrials'])}；发出编辑工具请求的试次：{len(costs['executorEditRequestTrials'])}。", "",
              "| 组别/角色 | 请求数 | 已知 token | 未知用量请求 | 请求耗时合计/秒 |", "| --- | ---: | ---: | ---: | ---: |"]
    for role, value in costs["byArmAndRole"].items():
        lines.append(f"| {role} | {value['requests']} | {value['knownTokens']} | {value['unknownTokenRequests']} | {value['requestSeconds']:.1f} |")
    fraction = costs["explicitNonExecutorRequestFraction"]
    lines += ["", "explicit 非 Executor 请求占比：" + (f"{fraction:.1%}" if fraction is not None else "无可用请求") + "。该比例为描述指标，不等同全部协议税。",
              "请求耗时合计不能当作阶段墙钟时间；未知用量保留为未知，已知 token 只作下界。所有失败保留，未补抽或选优。", ""]
    (directory / "gen2-report.md").write_text("\n".join(lines))
    return gate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.input.resolve()), ensure_ascii=False))
