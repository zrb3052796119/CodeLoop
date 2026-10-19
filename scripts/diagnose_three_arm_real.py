#!/usr/bin/env python3
"""Descriptive role diagnostics after complete, independently audited trials.

Never execute archived code, models, or oracles. No outcome selection, new
significance tests, timing speedup estimates, or changes to frozen scoring.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
import zipfile

if __package__:
    from .audit_three_arm_real import Audit, projection_ids
else:
    from audit_three_arm_real import Audit, projection_ids

ARMS = ("single", "auto", "explicit")


class DiagnosticError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def integer(value):
    return type(value) is int and value >= 0


def role_of(record):
    value = record.get("role")
    return value.lower() if isinstance(value, str) and value else "unknown"


def workflow_prompt_limit(archive_path):
    """Recognize the frozen validator's narrow constant/strip/guard contract.

    This is deliberately not a Python interpreter. A changed or ambiguous
    source shape remains unknown rather than inheriting this version's limit.
    """
    task_path = "minicode/tools/task.py"
    contract_path = "minicode/multi_agent_contracts.py"
    result = {"maxChars": None, "normalization": None, "verified": False,
              "source": "frozen-code.zip:" + task_path}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for path in (task_path, contract_path):
                if archive.namelist().count(path) != 1:
                    raise ValueError("missing_or_ambiguous_source")
            task_bytes, contract_bytes = archive.read(task_path), archive.read(contract_path)
        task, contract = ast.parse(task_bytes), ast.parse(contract_bytes)
        result.update(taskSourceSha256=digest(task_bytes), contractSourceSha256=digest(contract_bytes))

        def one_assignment(module, name):
            writes = [n for n in ast.walk(module)
                      if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)]
            assignments = [n for n in module.body if isinstance(n, ast.Assign)
                           and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                           and n.targets[0].id == name]
            if len(writes) != 1 or len(assignments) != 1:
                raise ValueError("ambiguous_constant")
            return assignments[0].value

        def same(node, expression):
            return ast.dump(node) == ast.dump(ast.parse(expression, mode="eval").body)

        imported = [(node.module, node.level, alias.name) for node in task.body if isinstance(node, ast.ImportFrom)
                    for alias in node.names if (alias.asname or alias.name) == "_MAX_TEXT"]
        if imported != [("minicode.multi_agent_contracts", 0, "_MAX_TEXT")]:
            raise ValueError("unrecognized_constant_source")
        if any(isinstance(n, ast.Name) and n.id == "_MAX_TEXT" and isinstance(n.ctx, ast.Store)
               for n in ast.walk(task)):
            raise ValueError("reassigned_import")
        base = one_assignment(contract, "_MAX_TEXT")
        limit = one_assignment(task, "_WORKFLOW_PROMPT_MAX_CHARS")
        if not (isinstance(base, ast.Constant) and type(base.value) is int
                and isinstance(limit, ast.BinOp) and isinstance(limit.op, ast.Sub)
                and isinstance(limit.left, ast.Name) and limit.left.id == "_MAX_TEXT"
                and isinstance(limit.right, ast.Constant) and type(limit.right.value) is int
                and 0 <= limit.right.value < base.value):
            raise ValueError("unrecognized_constant_expression")
        validators = [node for node in task.body if isinstance(node, ast.FunctionDef) and node.name == "_validate"]
        if len(validators) != 1 or validators[0].decorator_list:
            raise ValueError("unrecognized_validator")
        body = validators[0].body
        for name in ("agent_type", "raw_prompt", "prompt"):
            one_assignment(validators[0], name)
        agent_types = [n for n in body if isinstance(n, ast.Assign) and len(n.targets) == 1
                       and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "agent_type"
                       and same(n.value, "input_data.get('agent_type', 'general')")]
        guards = [n for n in body if isinstance(n, ast.If) and same(
            n.test, "agent_type == 'workflow' and len(prompt) > _WORKFLOW_PROMPT_MAX_CHARS")]
        raw = [n for n in body if isinstance(n, ast.Assign) and len(n.targets) == 1
               and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "raw_prompt"
               and same(n.value, "input_data.get('prompt')")]
        stripped = [n for n in body if isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "prompt"
                    and same(n.value, "raw_prompt.strip() if isinstance(raw_prompt, str) else ''")]
        if not (len(guards) == len(raw) == len(stripped) == len(agent_types) == 1
                and agent_types[0].lineno < raw[0].lineno < stripped[0].lineno < guards[0].lineno
                and len(guards[0].body) == 1 and isinstance(guards[0].body[0], ast.Raise)):
            raise ValueError("unrecognized_prompt_guard")
        raised = guards[0].body[0].exc
        if not (isinstance(raised, ast.Call) and isinstance(raised.func, ast.Name)
                and raised.func.id == "ValueError" and any(
                    isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and "error[workflow_prompt_oversized]" in n.value for n in ast.walk(raised))):
            raise ValueError("unrecognized_prompt_error")
        result.update(maxChars=base.value - limit.right.value, normalization="strip", verified=True)
    except (OSError, KeyError, ValueError, SyntaxError, zipfile.BadZipFile) as error:
        result["reason"] = str(error) if type(error) is ValueError else type(error).__name__
    return result


def workflow_request_counts(requests, limit):
    """Model-proposed tool calls only: never infer execution or rejection."""
    task_calls = [c for r in requests for c in r.get("toolCalls", []) if c.get("name") == "task"]
    workflows = [c["arguments"] for c in task_calls if isinstance(c.get("arguments"), dict)
                 and c["arguments"].get("agent_type") == "workflow"]
    prompts = [c["prompt"] for c in workflows if isinstance(c.get("prompt"), str)]
    bound = limit.get("maxChars")
    known = limit.get("verified") is True and integer(bound) and bound > 0 and limit.get("normalization") == "strip"
    return {"requestedTaskCalls": len(task_calls), "requestedWorkflowCalls": len(workflows),
            "workflowPromptStringRequests": len(prompts),
            "workflowPromptUnavailableRequests": len(workflows) - len(prompts),
            "oversizedWorkflowPromptRequests": sum(len(p.strip()) > bound for p in prompts) if known else None}


def read_ledger(path):
    """Read a transactionally consistent view; never create or migrate a DB."""
    if not path.exists():
        return [], []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        requests = [{**json.loads(data), "call": call, "event": status}
                    for call, status, data in db.execute(
                        "SELECT call,status,data FROM requests ORDER BY call")]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rejections = ([{"rejection": identity, **json.loads(data)}
                       for identity, data in db.execute(
                           "SELECT rejection,data FROM admission_rejections ORDER BY rejection")]
                      if "admission_rejections" in tables else [])
        return requests, rejections
    finally:
        db.close()


def request_metrics(requests, price):
    """Count every physical attempt; retain partial receipt components as known."""
    inputs = outputs = cached = token_receipts = full_receipts = 0
    input_receipts = output_receipts = cache_receipts = 0
    lower = 0.0
    for request in requests:
        usage = request.get("usage") or {}
        i, o, c = (usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
        ki, ko = integer(i), integer(o)
        kc = ki and integer(c) and c <= i
        input_receipts += ki
        output_receipts += ko
        cache_receipts += kc
        token_receipts += ki and ko
        full_receipts += ki and ko and kc
        inputs += i if ki else 0
        outputs += o if ko else 0
        cached += c if kc else 0
        if ki:
            lower += ((i - c) * price["perMillionInput"] + c * price["perMillionCacheRead"]
                      if kc else i * min(price["perMillionInput"], price["perMillionCacheRead"])) / 1e6
        if ko:
            lower += o * price["perMillionOutput"] / 1e6
    count = len(requests)
    # Individual durations include network, queueing, generation and streaming;
    # no summed duration is emitted or compared with trial wall time.
    elapsed = [r["seconds"] for r in requests if type(r.get("seconds")) in (int, float)
               and math.isfinite(r["seconds"]) and r["seconds"] >= 0]
    return {
        "physicalRequests": count,
        "completedRequests": sum(r["event"] == "completed" for r in requests),
        "failedRequests": sum(r["event"] == "failed" for r in requests),
        "inFlightRequests": sum(r["event"] == "started" for r in requests),
        "knownInputTokens": inputs, "knownOutputTokens": outputs,
        "knownTotalTokens": inputs + outputs, "knownCacheReadTokens": cached,
        "inputReceiptRequests": input_receipts, "outputReceiptRequests": output_receipts,
        "tokenReceiptRequests": token_receipts, "cacheReceiptRequests": cache_receipts,
        "completeReceiptRequests": full_receipts,
        "completeReceiptFraction": full_receipts / count if count else None,
        "unknownTokenReceiptRequests": count - token_receipts,
        "apiEquivalentCostCny": lower if full_receipts == count else None,
        "apiEquivalentCostLowerBoundCny": lower,
        "requestElapsedSeconds": {
            "observedRequests": len(elapsed), "median": median(elapsed) if elapsed else None,
            "maximum": max(elapsed) if elapsed else None,
        },
    }


def role_details(requests, rejections, collaboration, messages, *, owner_ack):
    """Separate leaf execution from orchestration containers and result transport."""
    runs = collaboration.get("runs", [])
    delivered = set(collaboration.get("deliveredIds", []))
    for message in messages:
        if message.get("role") in {"tool", "tool_result"} and (
            not owner_ack or message.get("toolName") == "task"
        ):
            delivered.update(projection_ids(message.get("content")))
    workflows = {r["subagentId"]: r for r in runs
                 if r.get("isContainer") and r.get("returned") and r["subagentId"] in delivered}
    by_role = defaultdict(Counter)
    for run in runs:
        role, actor = role_of(run), run["subagentId"]
        counts = by_role[role]
        if run.get("isContainer"):
            counts["workflowContainers"] += 1
            counts["deliveredWorkflowResults"] += actor in workflows
            continue
        counts["attemptedLeafRuns"] += 1
        own_requests = [r for r in requests if r.get("subagentId") == actor]
        counts["admittedLeafRuns"] += bool(own_requests)
        actual = any(r["event"] == "completed" for r in own_requests)
        counts["completedResponseLeafRuns"] += actual
        counts["actualLeafRuns"] += actual
        workflow = workflows.get(run.get("parentSubagentId"))
        via_workflow = bool(workflow and workflow["startedEpoch"] <= run["startedEpoch"]
                            and run.get("returnedEpoch", math.inf) <= workflow.get("returnedEpoch", 0))
        direct = bool(actual and run.get("returned") and actor in delivered)
        through_container = bool(actual and run.get("returned") and not direct and via_workflow)
        counts["directlyDeliveredLeafResults"] += direct
        counts["deliveredViaWorkflowLeafResults"] += through_container
        counts["deliveredLeafResults"] += direct or through_container
    for rejection in rejections:
        by_role[role_of(rejection)]["admissionRejections"] += 1
    # These are requested tool arguments, not proof of execution or unchanged
    # file bytes. The same read after a modification can be entirely necessary.
    seen_actor, seen_other = set(), defaultdict(set)
    for request in requests:
        role, actor = role_of(request), request.get("subagentId") or "main"
        for call in request.get("toolCalls", []):
            by_role[role]["requestedToolCalls"] += 1
            if call.get("name") != "read_file":
                continue
            fingerprint = json.dumps(call.get("arguments"), sort_keys=True, ensure_ascii=False)
            by_role[role]["requestedFileReads"] += 1
            by_role[role]["identicalReadRequestsWithinActor"] += (actor, fingerprint) in seen_actor
            by_role[role]["readRequestsWithPriorOtherActor"] += bool(seen_other[fingerprint] - {actor})
            seen_actor.add((actor, fingerprint))
            seen_other[fingerprint].add(actor)
    return {role: dict(counts) for role, counts in by_role.items()}


def validate_complete(directory, expected_trials):
    manifest = read_json(directory / "manifest.json")
    results = read_json(directory / "results.json")
    schedule, rows = manifest.get("schedule", []), results.get("trials", [])
    scheduled = [r["id"] for r in schedule]
    ids = [r["id"] for r in rows]
    if not (len(schedule) == len(rows) == expected_trials == results.get("expectedTrials")
            and len(set(ids)) == len(ids) and set(ids) == set(scheduled)):
        raise DiagnosticError(f"Requires all {expected_trials} scheduled terminal results; no partial report")
    if results.get("manifestSha256") != digest((directory / "manifest.json").read_bytes()):
        raise DiagnosticError("Results are not bound to the current manifest")
    saved = read_json(directory / "audit-summary.json")
    if not (saved.get("valid") is True and saved.get("mode") == "final"
            and saved.get("expectedTrials") == saved.get("auditedTrials") == expected_trials):
        raise DiagnosticError("Requires an existing valid final audit covering every trial")
    fresh = Audit(directory).run()
    if not fresh.get("valid") or fresh.get("trials") != saved.get("trials"):
        raise DiagnosticError("Evidence changed or no longer passes the saved independent audit")
    price = manifest["price"]
    for key in ("perMillionInput", "perMillionOutput", "perMillionCacheRead"):
        if type(price.get(key)) not in (int, float) or not math.isfinite(price[key]) or price[key] < 0:
            raise DiagnosticError("Invalid API-equivalent price basis")
    if price.get("currency", "CNY") != "CNY":
        raise DiagnosticError("Only the frozen CNY price basis is supported")
    return manifest, rows, fresh


def diagnose(directory, *, expected_trials=48):
    directory = Path(directory).resolve()
    manifest, rows, audit = validate_complete(directory, expected_trials)
    prompt_limit = workflow_prompt_limit(directory / "frozen-code.zip")
    audited = {r["id"]: r for r in audit["trials"]}
    role_requests, role_counts, arm_requests = defaultdict(list), defaultdict(Counter), defaultdict(list)
    trial_reports = []
    for row in rows:
        root = directory / "private" / row["id"]
        requests, rejections = read_ledger(root / "transport.sqlite")
        collaboration = read_json(root / "collaboration.json") if (root / "collaboration.json").exists() else {}
        messages = read_json(root / "transcript.json") if (root / "transcript.json").exists() else []
        details = role_details(requests, rejections, collaboration, messages,
                               owner_ack=audit["deliveryEvidenceMode"] == "task_result_owner_ack")
        computed = audited[row["id"]]["recomputedCollaboration"]
        for key in ("actualLeafRuns", "deliveredLeafResults", "deliveredWorkflowResults"):
            if sum(value.get(key, 0) for value in details.values()) != computed[key]:
                raise DiagnosticError(f"Role delivery totals disagree with audit: {row['id']}/{key}")
        arm_requests[row["arm"]].extend(requests)
        for request in requests:
            role_requests[(row["arm"], role_of(request))].append(request)
        for role, counts in details.items():
            role_counts[(row["arm"], role)].update(counts)
        role_names = sorted(set(details) | {role_of(r) for r in requests})
        trial_reports.append({
            "id": row["id"], "arm": row["arm"], "case": row["case"], "repeat": row["repeat"],
            "success": row["success"], "protocolSatisfied": row["collaborationProtocolSatisfied"],
            "roles": {role: {**request_metrics([r for r in requests if role_of(r) == role], manifest["price"]),
                              **workflow_request_counts([r for r in requests if role_of(r) == role], prompt_limit),
                              **details.get(role, {})} for role in role_names},
            "admissionRejections": len(rejections),
            # The audited source carries details; omit free-form model/error
            # prose from this diagnostic artifact rather than interpreting it.
            "stopEvidence": [{k: e[k] for k in ("code", "authority", "source", "call", "sequence", "rejection") if k in e}
                             for e in audited[row["id"]]["diagnostics"]["stopEvidence"]],
            "terminalOutcomes": audited[row["id"]]["diagnostics"]["terminalOutcomes"],
        })
    arms = {}
    for arm in ARMS:
        selected = [r for r in trial_reports if r["arm"] == arm]
        roles = sorted({role for a, role in set(role_requests) | set(role_counts) if a == arm})
        arms[arm] = {
            "trials": len(selected), "originalSuccesses": sum(r["success"] for r in selected),
            **request_metrics(arm_requests[arm], manifest["price"]),
            **workflow_request_counts(arm_requests[arm], prompt_limit),
            "admissionRejections": sum(r["admissionRejections"] for r in selected),
            "roles": {role: {**request_metrics(role_requests[(arm, role)], manifest["price"]),
                              **workflow_request_counts(role_requests[(arm, role)], prompt_limit),
                              **dict(role_counts[(arm, role)])} for role in roles},
            "stopEvidenceTrialCounts": dict(Counter(
                label for r in selected for label in {
                    f"{e['code']} [{e['authority']}]" for e in r["stopEvidence"]
                }
            )),
            "terminalOutcomeTrialCounts": dict(Counter(code for r in selected for code in r["terminalOutcomes"])),
        }
    return {
        "diagnosticVersion": 2, "mode": "exploratory_descriptive", "trials": trial_reports,
        "workflowPromptLimit": prompt_limit,
        "trialCount": len(rows), "model": manifest["model"], "price": manifest["price"],
        "inputs": {name: digest((directory / name).read_bytes()) for name in ("manifest.json", "results.json", "audit-summary.json")},
        "diagnosticScriptSha256": digest(Path(__file__).read_bytes()), "arms": arms,
        "notes": [
            "All terminal trials, including failures, remain included; original success labels are unchanged. No new significance tests.",
            "Physical requests include completed, failed and in-flight attempts. Admission rejections have no provider request or fee.",
            "Known token components include partial receipts; absent components are unknown, not zero usage. Cost with missing usage/cache is only a lower bound.",
            "Prices are standard API-equivalent estimates, not actual incremental coding-subscription charges.",
            "Request elapsed time includes queueing, network and streaming; its median/maximum is not trial wall time or pure reasoning time, and is never summed to claim speedup.",
            "Identical read requests compare full argument JSON within each trial, not file contents or executed tools; repeated reads may be necessary after edits.",
            "Read ordering uses request admission order, not concurrent tool execution order. Cross-actor repetition does not establish waste or model intent.",
            "A delivered result can report failure; workflow containers are separate from leaves and never inherit child request charges.",
            "Attempted leaves may have no admitted request; admitted leaves may have only failed/in-flight requests. actualLeafRuns is the frozen completed-response definition, not all attempted collaboration.",
            "Stop evidence codes may co-occur; response_only_unconfirmed remains unconfirmed. Oracle failure is an outcome, not an inferred stopping cause.",
            "Task/workflow counts are model-proposed tool calls retained in provider responses, not executed tools or confirmed rejections. Oversized prompt counts use the verified frozen validator's stripped-character limit; an unrecognized limit is null, never assumed. Missing/non-string prompts are separately unclassifiable.",
        ],
    }


def markdown(report):
    lines = ["# 角色与停止原因诊断", "", f"完整终态：{report['trialCount']}；独立审计通过。仅作探索性描述，保留全部失败样本和原始计分。", "",
             "| 组 | 角色 | 物理请求 | 完整回执 | 已知输入 / 输出 Token | API 等价费用 CNY | 发出请求叶子 / 有完成请求叶子 / 已送达 |", "|---|---|---:|---:|---:|---:|---:|"]
    for arm, data in report["arms"].items():
        for role, r in data["roles"].items():
            cost = f"{r['apiEquivalentCostCny']:.6f}" if r["apiEquivalentCostCny"] is not None else f"≥{r['apiEquivalentCostLowerBoundCny']:.6f}（下界）"
            lines.append(f"| {arm} | {role} | {r['physicalRequests']} | {r['completeReceiptRequests']}/{r['physicalRequests']} | {r['knownInputTokens']} / {r['knownOutputTokens']} | {cost} | {r.get('admittedLeafRuns', 0)} / {r.get('completedResponseLeafRuns', 0)} / {r.get('deliveredLeafResults', 0)} |")
    lines += ["", "已知 Token 数只累加已收到的字段；缺失回执不代表零用量。费用为标准 API 等价估算，不是订阅账户增量扣费。", "",
              "| 组 | 原始成功 / 全部终态 | 准入拒绝（不计请求费用） | 停止证据涉及 trial 数（可重叠） |", "|---|---:|---:|---|"]
    for arm, data in report["arms"].items():
        reasons = "; ".join(f"{k}: {v}" for k, v in sorted(data["stopEvidenceTrialCounts"].items())) or "无"
        lines.append(f"| {arm} | {data['originalSuccesses']}/{data['trials']} | {data['admissionRejections']} | {reasons} |")
    lines += ["", "| 组 | 角色 | read_file 请求 | 同一 Agent 相同参数重读 | 先前其他 Agent 请求过相同参数 |", "|---|---|---:|---:|---:|"]
    for arm, data in report["arms"].items():
        for role, r in data["roles"].items():
            lines.append(f"| {arm} | {role} | {r.get('requestedFileReads', 0)} | {r.get('identicalReadRequestsWithinActor', 0)} | {r.get('readRequestsWithPriorOtherActor', 0)} |")
    lines += ["", "重读只表示模型请求的完整参数相同；不证明工具执行成功、文件内容相同或这次读取无效。跨 Agent 顺序取请求准入顺序，不能据此推断模型意图。", "",
              "JSON 中提供请求耗时的中位数和最大值；它包含服务排队、网络、生成与流传输，不等于推理耗时，也不能相加代替墙钟时间证明提速。", "",
              "发出请求的叶子可能只有失败或未终结请求；有完成请求的叶子使用冻结协议的 actualLeafRuns 定义。交付可以是失败回执；JSON 分别列出直接交付和经 workflow 容器送达，容器不重复归属子调用费用。停止证据沿用 audit-summary.json 的 code/authority/source；模型文本推测仍标记为未确认。"]
    limit = report["workflowPromptLimit"]
    ceiling = f"{limit['maxChars']} 字符（strip 后）" if limit["verified"] else "未知（归档约束无法可靠识别）"
    lines += ["", f"冻结 workflow 提示上限：{ceiling}。", "",
              "| 组 | 角色 | 模型提出的 task 请求 | 其中 workflow 请求 | 模型提出的超长 workflow 请求 | workflow 提示缺失或非字符串 |",
              "|---|---|---:|---:|---:|---:|"]
    for arm, data in report["arms"].items():
        for role, r in data["roles"].items():
            oversized = r["oversizedWorkflowPromptRequests"]
            lines.append(f"| {arm} | {role} | {r['requestedTaskCalls']} | {r['requestedWorkflowCalls']} | {oversized if oversized is not None else '未知'} | {r['workflowPromptUnavailableRequests']} |")
    lines += ["", "以上仅计 provider 响应中保留的工具请求，包括可能未执行或先因其他参数失败的请求；不等于实际工具执行次数或已确认的超长拒绝次数。上限仅静态读取冻结源码归档，绝不执行归档代码；无法识别时超长计数为 null。"]
    return "\n".join(lines) + "\n"


def write_report(directory, report):
    directory = Path(directory)
    for name, content in (("role-diagnostics.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
                          ("role-diagnostics.md", markdown(report))):
        path = directory / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-trials", type=int, default=48,
                        help="Complete terminal count; default is the frozen 48-trial experiment")
    args = parser.parse_args(argv)
    try:
        report = diagnose(args.input, expected_trials=args.expected_trials)
        write_report(args.input, report)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(2, f"Role diagnostics refused: {type(error).__name__}: {error}\n")
    print(json.dumps({"trialCount": report["trialCount"], "mode": report["mode"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
