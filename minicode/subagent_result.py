"""Strict, bounded result contract for every ``task`` sub-agent.

The sub-agent reports task content through the turn-scoped mailbox. Identity,
agent type and outcome are added by the parent runtime, so model text cannot
spoof the observation keys used to join a ToolResult, parent Run event and
sidecar sub-run journal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping


SUBAGENT_RESULT_VERSION = 1
SUBAGENT_RESULT_MARKER = "=== SUBAGENT RESULT v1 ==="

_SUBAGENT_ID_RE = re.compile(r"^sub_[0-9a-f]{32}$")
_AGENT_TYPES = frozenset({"explore", "plan", "general", "workflow"})
_OUTCOMES = frozenset(
    {"completed", "failed", "depth_rejected", "budget_exceeded"}
)
_ACTIONS = frozenset({"read", "created", "modified", "deleted", "unknown"})
_VERIFICATION_STATUSES = frozenset(
    {"passed", "failed", "not_run", "inconclusive"}
)
_REPORT_FIELDS = frozenset(
    {"resultVersion", "summary", "files", "risks", "verification"}
)
_PROJECTED_FIELDS = frozenset(
    {
        "resultVersion",
        "subagentId",
        "agentType",
        "outcome",
        "contractStatus",
        "summary",
        "files",
        "risks",
        "verification",
    }
)
_MAX_JSON_CHARS = 20_000
_MAX_SUMMARY_CHARS = 4_000
_MAX_FILES = 40
_MAX_PATH_CHARS = 500
_MAX_RISKS = 20
_MAX_RISK_CHARS = 500
_MAX_CHECKS = 20
_MAX_CHECK_CHARS = 500


def _bounded_nonempty(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    return text


def _normalize_report(content: object) -> dict[str, object] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    if len(content) > _MAX_JSON_CHARS:
        return None
    try:
        raw = json.loads(content)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(raw, dict)
        or set(raw) != _REPORT_FIELDS
        or raw.get("resultVersion") != SUBAGENT_RESULT_VERSION
    ):
        return None
    summary = _bounded_nonempty(raw.get("summary"), _MAX_SUMMARY_CHARS)
    files = raw.get("files")
    risks = raw.get("risks")
    verification = raw.get("verification")
    if (
        summary is None
        or not isinstance(files, list)
        or len(files) > _MAX_FILES
        or not isinstance(risks, list)
        or len(risks) > _MAX_RISKS
        or not isinstance(verification, dict)
        or set(verification) != {"status", "checks"}
    ):
        return None

    normalized_files: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "action"}:
            return None
        path = _bounded_nonempty(item.get("path"), _MAX_PATH_CHARS)
        action = item.get("action")
        if path is None or action not in _ACTIONS:
            return None
        normalized_files.append({"path": path, "action": str(action)})

    normalized_risks: list[str] = []
    for item in risks:
        risk = _bounded_nonempty(item, _MAX_RISK_CHARS)
        if risk is None:
            return None
        normalized_risks.append(risk)

    status = verification.get("status")
    checks = verification.get("checks")
    if (
        status not in _VERIFICATION_STATUSES
        or not isinstance(checks, list)
        or len(checks) > _MAX_CHECKS
    ):
        return None
    normalized_checks: list[str] = []
    for item in checks:
        check = _bounded_nonempty(item, _MAX_CHECK_CHARS)
        if check is None:
            return None
        normalized_checks.append(check)

    return {
        "resultVersion": SUBAGENT_RESULT_VERSION,
        "summary": summary,
        "files": normalized_files,
        "risks": normalized_risks,
        "verification": {
            "status": status,
            "checks": normalized_checks,
        },
    }


def project_subagent_result(
    report_content: object,
    *,
    subagent_id: str,
    agent_type: str,
    outcome: str,
    fallback_summary: str,
) -> dict[str, object]:
    """Validate a model report and bind parent-owned correlation fields.

    Missing or malformed reports degrade to an explicit, evidence-free
    fallback. They never invent files, risks or verification claims.
    """
    if _SUBAGENT_ID_RE.fullmatch(subagent_id) is None:
        raise ValueError("sub-agent id is invalid")
    if agent_type not in _AGENT_TYPES:
        raise ValueError("agent type is invalid")
    if outcome not in _OUTCOMES:
        raise ValueError("sub-agent outcome is invalid")

    report = _normalize_report(report_content)
    contract_status = "reported"
    if report is None:
        contract_status = "fallback"
        summary = str(fallback_summary or "").strip()[:_MAX_SUMMARY_CHARS]
        report = {
            "resultVersion": SUBAGENT_RESULT_VERSION,
            "summary": summary or "(sub-agent completed without a typed report)",
            "files": [],
            "risks": [],
            "verification": {
                "status": "inconclusive",
                "checks": [],
            },
        }
    return {
        "resultVersion": SUBAGENT_RESULT_VERSION,
        "subagentId": subagent_id,
        "agentType": agent_type,
        "outcome": outcome,
        "contractStatus": contract_status,
        "summary": report["summary"],
        "files": report["files"],
        "risks": report["risks"],
        "verification": report["verification"],
    }


def _normalize_projected_result(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != _PROJECTED_FIELDS:
        return None
    identity = value.get("subagentId")
    agent_type = value.get("agentType")
    outcome = value.get("outcome")
    contract_status = value.get("contractStatus")
    if (
        not isinstance(identity, str)
        or _SUBAGENT_ID_RE.fullmatch(identity) is None
        or agent_type not in _AGENT_TYPES
        or outcome not in _OUTCOMES
        or contract_status not in {"reported", "fallback", "derived"}
    ):
        return None
    report = {key: value[key] for key in _REPORT_FIELDS}
    normalized_report = _normalize_report(
        json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    )
    if normalized_report is None:
        return None
    return {
        "resultVersion": SUBAGENT_RESULT_VERSION,
        "subagentId": identity,
        "agentType": agent_type,
        "outcome": outcome,
        "contractStatus": contract_status,
        "summary": normalized_report["summary"],
        "files": normalized_report["files"],
        "risks": normalized_report["risks"],
        "verification": normalized_report["verification"],
    }


def render_subagent_result(result: Mapping[str, object]) -> str:
    normalized = _normalize_projected_result(result)
    if normalized is None:
        raise ValueError("projected sub-agent result is invalid")
    return SUBAGENT_RESULT_MARKER + "\n" + json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def extract_subagent_result(output: object) -> dict[str, object] | None:
    if not isinstance(output, str):
        return None
    before, marker, after = output.rpartition(SUBAGENT_RESULT_MARKER)
    del before
    if not marker:
        return None
    try:
        raw = json.loads(after.strip())
    except (TypeError, ValueError):
        return None
    return _normalize_projected_result(raw)


__all__ = [
    "SUBAGENT_RESULT_MARKER",
    "SUBAGENT_RESULT_VERSION",
    "extract_subagent_result",
    "project_subagent_result",
    "render_subagent_result",
]
