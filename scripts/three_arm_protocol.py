"""Host evidence for the preregistered collaboration recipe, not task scoring."""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch
import uuid

from evaluation_transport import evaluation_context

_PARENT_ACTOR: ContextVar[str | None] = ContextVar("evaluation_parent_actor", default=None)

ARMS = ("single", "auto", "explicit")
EXPLICIT_INSTRUCTION = """\n\n执行方式（本组的固定协作协议）：
主 Agent 只负责派发与汇总，工具仅有 task 和 todo_write。源码阅读、修改和验证由子 Agent 完成。
1. 第一步就用 task 调用 agent_type='plan'、action='run' 同步分析。委派中完整转述本题目标、授权路径和验证命令。要求从相关文件开始聚焦定位，不做全仓库目录巡检；返回定位证据、实现建议、风险，摘要不超过1200个中文字符或500个英文单词。
2. 分析结果送达后立即用 task 的 agent_type='workflow' 委派实现。传入本题完整目标和实际分析结果，准确声明宿主允许的 write_paths 及提供的 verification_commands，由工作流完成修改与验证；不要重新派分析，也不要把实现推回主 Agent。
3. 主 Agent 汇总实际结果。若协作失败，按剩余预算修复委派参数或如实返回失败；不得只声称已委派。
所有分析、实现、交接和重试共用本次总预算；不得开启额外模型或读取工作区以外的参考答案。
"""


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm not in ARMS:
        raise ValueError("unknown evaluation arm")
    return prompt + (EXPLICIT_INSTRUCTION if arm == "explicit" else "")


class CollaborationEvidence:
    """Trace real leaf execution and the host-owned result transport seam.

    Synchronous results carry an acknowledgement bound to the actual task result
    object, invoked only after parent transcript insertion. Workflow leaves additionally
    require the containing workflow result to be delivered. Model claims alone
    do not create any run or request evidence.
    """

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.records: list[dict] = []
        self.delivered: set[str] = set()
        self.stack = ExitStack()

    def _save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"runs": self.records, "deliveredIds": sorted(self.delivered)}, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def __enter__(self):
        import minicode.tools.task as task_module
        from minicode.subagent_result import extract_subagent_result

        from minicode.subagent_lifecycle import AsyncSubagentLifecycle
        original_async_ack = AsyncSubagentLifecycle.acknowledge_delivery

        def async_delivered(registry, ids):
            original_async_ack(registry, ids)
            with self.lock:
                self.delivered.update(ids)
                self._save()

        self.stack.enter_context(patch.object(AsyncSubagentLifecycle, "acknowledge_delivery", async_delivered))
        original = task_module._run

        def observed(data, context):
            data = dict(data)
            actor_id = data.setdefault("_subagent_id", "sub_" + uuid.uuid4().hex)
            kind = data.get("agent_type")
            role = getattr(context, "_logical_agent_role", None) or kind
            record = {
                "subagentId": actor_id,
                "agentType": kind,
                "role": str(getattr(role, "value", role)),
                "startedEpoch": time.time(),
                "isContainer": kind == "workflow",
                "parentSubagentId": _PARENT_ACTOR.get(),
            }
            with self.lock:
                self.records.append(record)
                self._save()
            parent_token = _PARENT_ACTOR.set(actor_id)
            try:
                with evaluation_context(subagent_id=actor_id, role=record["role"], agentType=kind):
                    result = original(data, context)
                projected = extract_subagent_result(result.output)
                record.update({
                    "returnedEpoch": time.time(), "returned": True,
                    "toolOk": result.ok, "projected": projected,
                    "resultSha256": hashlib.sha256(result.output.encode()).hexdigest(),
                })
                original_delivery_ack = result._delivery_ack

                def delivered():
                    if callable(original_delivery_ack):
                        original_delivery_ack()
                    with self.lock:
                        record.setdefault("deliveredEpoch", time.time())
                        record["deliveryEvidence"] = "task_result_owner_ack"
                        self.delivered.add(actor_id)
                        self._save()

                result._delivery_ack = delivered
                return result
            except BaseException as error:
                record.update({"returnedEpoch": time.time(), "returned": False, "errorClass": type(error).__name__})
                raise
            finally:
                _PARENT_ACTOR.reset(parent_token)
                with self.lock:
                    self._save()

        self.stack.enter_context(patch.object(task_module, "_run", observed))
        self.stack.enter_context(patch.object(task_module, "_SYNC_RUN", observed))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def result_ids(value, depth=0) -> set[str]:
    from minicode.subagent_result import extract_subagent_result
    if depth > 12:
        return set()
    if isinstance(value, str):
        projected = extract_subagent_result(value)
        if projected is not None:
            return {projected["subagentId"]}
        try:
            return result_ids(json.loads(value), depth + 1)
        except (ValueError, TypeError):
            return set()
    if isinstance(value, dict):
        if value.get("resultVersion") == 1:
            from minicode.subagent_result import render_subagent_result
            try:
                validated = extract_subagent_result(render_subagent_result(value))
            except (TypeError, ValueError):
                validated = None
            if validated is not None:
                return {validated["subagentId"]}
        return set().union(*(result_ids(v, depth + 1) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(result_ids(v, depth + 1) for v in value))
    return set()


def collaboration_metrics(records: list[dict], requests: list[dict], messages: list[dict], delivered_ids=()) -> dict:
    delivered_ids = set(delivered_ids)
    for message in messages:
        if message.get("role") in ("tool_result", "tool") and message.get("toolName") == "task":
            delivered_ids.update(result_ids(message.get("content")))
    containers = [r for r in records if r.get("isContainer")]
    delivered_workflows = [r for r in containers if r.get("returned") and r["subagentId"] in delivered_ids]
    leafs = []
    for record in records:
        calls = [c for c in requests if c.get("subagentId") == record["subagentId"]]
        if record.get("isContainer") or not any(c.get("event") == "completed" for c in calls):
            continue
        direct_delivery = record["subagentId"] in delivered_ids
        workflow_delivery = any(
            workflow["subagentId"] == record.get("parentSubagentId")
            and workflow["startedEpoch"] <= record["startedEpoch"]
            and record.get("returnedEpoch", float("inf")) <= workflow.get("returnedEpoch", 0)
            for workflow in delivered_workflows
        )
        leafs.append({**record, "modelCalls": len(calls), "delivered": bool(record.get("returned") and (direct_delivery or workflow_delivery)), "workflowDelivered": workflow_delivery})
    read_leafs = [r for r in leafs if r["role"] in ("plan", "explore") and r["delivered"]]
    implementation = [r for r in leafs if r["role"].lower() == "executor" and r["delivered"] and r["workflowDelivered"]]
    ordered = any(a.get("deliveredEpoch", a.get("returnedEpoch", float("inf"))) <= b["startedEpoch"] for a in read_leafs for b in implementation)
    protocol = bool(read_leafs and implementation and delivered_workflows and ordered)
    return {
        "actualLeafRuns": len(leafs),
        "leafModelCalls": sum(1 for c in requests if any(not r.get("isContainer") and r["subagentId"] == c.get("subagentId") for r in records)),
        "deliveredLeafResults": sum(r["delivered"] for r in leafs),
        "successfulLeafRuns": sum(r.get("toolOk") is True and isinstance(r.get("projected"), dict) and r["projected"].get("outcome") == "completed" and not r["projected"].get("partial", False) for r in leafs),
        "readAnalysisLeafRuns": len(read_leafs),
        "implementationLeafRuns": len(implementation),
        "collaborationExecuted": any(r["delivered"] for r in leafs),
        "collaborationProtocolSatisfied": protocol,
        "deliveredWorkflowResults": len(delivered_workflows),
    }
