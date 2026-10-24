"""Reconstruct the five-session/six-form deposition audit without a model.

Run ``python -m scripts.memory_deposition_yield_audit --out report.json``.
The original yield capture records tool names/outcomes, but not complete tool
arguments or outputs. These explicit fixtures reconstruct its five scenarios;
this is real ReflectionEngine/MemoryPipeline execution, not a live-model rerun
or an exact replay. All storage is fresh and isolated. Existing experiment or
user memory directories are never reset or opened by this script.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import append_trace_event

POLICY = (
    "# 项目验证规范\n\n"
    "本项目唯一支持的质量检查方式是 `./tools/check.sh`。\n"
    "禁止使用 pytest 作为验证手段。\n"
)
PROMPTS = (
    "阅读 policy.md 了解本项目的验证规范，然后执行规范要求的验证并报告结果",
    "用 pytest 运行这个项目的测试并告诉我结果",
    "运行 ./tools/check.sh 验证项目，如果有问题就修复它并再次验证",
    "我叫王跑跑，以后这个项目里的回答都用中文，注释也用中文写",
    "这个项目依赖哪些第三方库？读一下 requirements.txt 确认后告诉我",
)
FORMS = (
    "policy", "single_tool_failure", "permission_denied", "syntax_repair",
    "user_preference", "declared_dependency",
)
_QUERIES = {
    "policy": "project verification policy tools/check.sh pytest",
    "single_tool_failure": "pytest collected 0 items no tests ran",
    "permission_denied": "run_command permission_denied workspace policy",
    "syntax_repair": "src/app.py SyntaxError expected colon tools/check.sh",
    "user_preference": "王跑跑 中文 回答 注释 用户偏好",
    "declared_dependency": "httpx requirements.txt dependencies",
}


def _tool(
    trace: list[dict[str, Any]], name: str, arguments: dict[str, Any],
    output: str, *, error: bool = False, error_type: str = "",
) -> None:
    call_id = f"call-{len(trace) + 1}"
    files = [arguments["path"]] if "path" in arguments else []
    append_trace_event(trace, {
        "type": "tool_call", "call_id": call_id, "tool_name": name,
        "input": arguments, "files": files,
    })
    append_trace_event(trace, {
        "type": "tool_result", "call_id": call_id, "tool_name": name,
        "status": "error" if error else "success", "is_error": error,
        "output_summary": output, "files": files,
    })
    if error_type:
        append_trace_event(trace, {
            "type": "error", "call_id": call_id, "tool_name": name,
            "error_type": error_type, "message": output,
        })


def reconstructed_sessions() -> list[dict[str, Any]]:
    """Return the five original prompts with bounded, inspectable traces."""
    traces: list[list[dict[str, Any]]] = [[] for _ in PROMPTS]
    for index in (0, 1):
        _tool(traces[index], "read_file", {"path": "policy.md"}, POLICY)
    _tool(traces[0], "run_command", {"command": "bash tools/check.sh"}, "ALL CHECKS PASSED")
    _tool(
        traces[1], "run_command", {"command": "pytest -q"},
        "collected 0 items\nno tests ran in 0.25s", error=True,
        error_type="TimeoutError",
    )
    _tool(
        traces[1], "run_command", {"command": "bash -lc 'pytest -q'"},
        "error[permission_denied]: Tool run_command request was denied by "
        "workspace policy (command_requires_approval).",
        error=True, error_type="PermissionError",
    )
    _tool(
        traces[2], "run_command", {"command": "./tools/check.sh"},
        'File "src/app.py", line 1\n    def main() -> str\n'
        "SyntaxError: expected colon", error=True, error_type="SyntaxError",
    )
    _tool(traces[2], "read_file", {"path": "src/app.py"}, 'def main() -> str\n    return "ok"')
    _tool(
        traces[2], "edit_file", {
            "path": "src/app.py", "old_string": "def main() -> str",
            "new_string": "def main() -> str:",
        }, "edited",
    )
    _tool(traces[2], "run_command", {"command": "./tools/check.sh"}, "ALL CHECKS PASSED")
    # Preserve the important mixed-work case: a user preference alongside an
    # accepted technical approach must still reach USER scope.
    _tool(traces[3], "read_file", {"path": "src/app.py"}, 'def main() -> str:\n    return "ok"')
    _tool(
        traces[3], "edit_file", {
            "path": "policy.md", "old_string": POLICY,
            "new_string": POLICY + "\n项目负责人是王跑跑。回答和注释使用中文。\n",
        }, "edited",
    )
    _tool(traces[3], "run_command", {"command": "bash tools/check.sh"}, "ALL CHECKS PASSED")
    _tool(traces[4], "read_file", {"path": "requirements.txt"}, "httpx>=0.27\n")
    sessions = []
    for number, (prompt, trace) in enumerate(zip(PROMPTS, traces), start=1):
        append_trace_event(trace, {"type": "task_result", "status": "success"})
        sessions.append({"session": number, "prompt": prompt, "trace": trace})
    return sessions


@contextmanager
def _audit_environment(mode: str) -> Iterator[None]:
    overrides = {
        "MINI_CODE_MEMORY_DEPOSITION_MODE": mode,
        "MINI_CODE_MEMORY_DEPOSITION_USER_PREFERENCES": "1",
        "MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES": "1",
        "MINI_CODE_MEMORY_DEPOSITION_PENDING_DRAFTS": "1",
        "MINI_CODE_MEMORY_PENDING_DRAFT_DAYS": "7",
        "MINI_CODE_MEMORY_DEPOSITION_DRAFT_TTL_DAYS": "14",
        "MINI_CODE_MEMORY_REDIS_URL": "",
        "MINI_CODE_REFLECTION_SYNTHESIZER_MODE": "rule",
    }
    with patch.dict(os.environ, overrides):
        yield


class _FunnelHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "MemoryPipeline: deposition candidates=" in message:
            self.lines.append(message)


def _entry_form(entry: Any) -> str | None:
    text = entry.content.lower()
    if entry.scope == MemoryScope.USER and entry.category == "user_preference":
        return "user_preference"
    claims = entry.metadata.get("structured_reflection", {}).get("claims", [])
    types = {claim.get("claim_type") for claim in claims}
    if "dependency" in types and "httpx" in text:
        return "declared_dependency"
    if "permission_denied" in text:
        return "permission_denied"
    if "collected 0 items" in text or "no tests ran" in text:
        return "single_tool_failure"
    if "syntaxerror" in text:
        return "syntax_repair"
    if types & {"constraint", "verification_rule"} and "check.sh" in text:
        return "policy"
    return None


def _entries(manager: MemoryManager) -> list[Any]:
    return [
        entry for scope in (MemoryScope.PROJECT, MemoryScope.USER)
        for entry in manager.memories[scope].entries
    ]


def _render_at(
    manager: MemoryManager, workspace: Path, now: float,
) -> dict[str, Any]:
    """Use the real prompt path at a later clock time without aging records."""
    rendered_ids: set[str] = set()
    draft_ids: set[str] = set()
    snippets: dict[str, str] = {}
    with patch("time.time", return_value=now):
        for form, query in _QUERIES.items():
            pipeline = MemoryPipeline(manager)
            pipeline.initialize(workspace_path=str(workspace), reflection_engine=ReflectionEngine())
            messages = pipeline.inject(
                query, [], [{"role": "system", "content": "Audit system."}],
                context_usage=0.0, max_memories=20, max_total_tokens=4000,
                max_tokens_per_memory=800,
            )
            rendered_ids.update(pipeline.injected_memory_ids)
            draft_ids.update(pipeline.injected_draft_ids)
            snippets[form] = messages[0]["content"]
    return {
        "canonical_ids": sorted(rendered_ids), "draft_ids": sorted(draft_ids),
        "prompts": snippets,
    }


def _run_mode(root: Path, mode: str) -> dict[str, Any]:
    workspace = root / mode / "workspaces" / "yieldtest"
    workspace.mkdir(parents=True)
    (workspace / "policy.md").write_text(POLICY, encoding="utf-8")
    (workspace / "requirements.txt").write_text("httpx>=0.27\n", encoding="utf-8")
    manager = MemoryManager(project_root=workspace, data_root=root / mode / "data")
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), reflection_engine=ReflectionEngine())
    session_results = []
    for session in reconstructed_sessions():
        result = pipeline.write_result(session["prompt"], session["trace"])
        session_results.append({
            "session": session["session"], "prompt": session["prompt"],
            "write_reasons": list(result.reason_codes),
            "project_facts_status": result.project_facts_effect.status,
        })

    entries = _entries(manager)
    pending_before = {
        entry.id: deepcopy(entry.to_dict()) for entry in entries
        if entry.approval_status == "pending"
    }
    now = max([time.time(), *(entry.updated_at for entry in entries)]) + 1
    fresh = _render_at(manager, workspace, now)
    aged = _render_at(manager, workspace, now + 8 * 86400)
    pending_after = {
        entry.id: entry.to_dict() for entry in _entries(manager)
        if entry.id in pending_before
    }
    forms: dict[str, dict[str, Any]] = {}
    for form in FORMS:
        matching = [entry for entry in entries if _entry_form(entry) == form]
        ids = {entry.id for entry in matching}
        durable = [
            entry.id for entry in matching
            if entry.approval_status == "approved"
            and not entry.metadata.get("deposition_draft")
        ]
        drafts = [entry.id for entry in matching if entry.metadata.get("deposition_draft")]
        aged_drafts = sorted(ids.intersection(aged["draft_ids"]))
        forms[form] = {
            "stored": bool(matching), "durable_ids": durable,
            "stored_draft_ids": drafts, "aged_prompt_draft_ids": aged_drafts,
            "fresh_prompt_draft_ids": sorted(ids.intersection(fresh["draft_ids"])),
            "captured_durable_or_draft": bool(durable or drafts or aged_drafts),
            "captured_immediately": bool(durable or drafts),
            "entries": [
                {
                    "id": entry.id, "scope": entry.scope.value,
                    "approval_status": entry.approval_status,
                    "lifecycle_status": entry.lifecycle_status,
                    "content": entry.content, "metadata": entry.metadata,
                } for entry in matching
            ],
        }
    captured = sum(form["captured_durable_or_draft"] for form in forms.values())
    return {
        "sessions": session_results, "forms": forms,
        "captured_forms": captured, "total_forms": len(FORMS),
        "capture_rate": captured / len(FORMS),
        "captured_immediately": sum(form["captured_immediately"] for form in forms.values()),
        "stored_forms": sum(form["stored"] for form in forms.values()),
        "pending_entries_unchanged": pending_before == pending_after,
        "pending_entries_checked": len(pending_before),
        "fresh_render": fresh, "eight_day_render": aged,
    }


def run_audit(root: Path, modes: tuple[str, ...] = ("strict", "capture", "inject")) -> dict[str, Any]:
    """Run in a newly created directory; refuse to reset an existing path."""
    if any(mode not in {"strict", "capture", "inject"} for mode in modes):
        raise ValueError("modes must be strict, capture, or inject")
    root.mkdir(parents=True, exist_ok=False)
    handler = _FunnelHandler()
    logger = logging.getLogger("minicode.memory_pipeline")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    report: dict[str, Any] = {
        "audit_kind": "reconstructed_real_pipeline", "live_model_calls": 0,
        "original_reference": ".experiment-work/deposition-experiment-v1/report.md#七round-4",
        "reconstructed_sessions": reconstructed_sessions(),
        "limitations": [
            "Original captures preserve prompts and tool outcomes, but not complete input/output traces; fixtures are reconstructed.",
            "This verifies the deposition pipeline and prompt rendering, not live-agent behavior or model response quality.",
            "Tool events are fixture inputs; no shell command or model request is executed. Legacy Project Facts are reported per session and excluded from lesson-form capture counts.",
            "Eight-day rendering advances the clock only; pending records are not edited or approved.",
            "Policy approval follows current production evidence rules and is reported separately from storage capture.",
        ],
        "modes": {},
    }
    try:
        for mode in modes:
            start = len(handler.lines)
            with _audit_environment(mode):
                result = _run_mode(root, mode)
            result["funnel_logs"] = handler.lines[start:]
            report["modes"][mode] = result
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, help="New directory only; omitted uses disposable temporary storage")
    args = parser.parse_args(argv)
    if args.work_dir is None:
        with tempfile.TemporaryDirectory(prefix="minicode-yield-audit-") as temporary:
            report = run_audit(Path(temporary) / "audit")
    else:
        report = run_audit(args.work_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inject = report["modes"]["inject"]
    print(f"Reconstructed pipeline audit: {inject['captured_forms']}/{inject['total_forms']} forms; report: {args.out}")
    return 0 if inject["captured_forms"] == len(FORMS) and inject["pending_entries_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
