"""Task tool — spawn a sub-agent to handle complex multi-step tasks.

Inspired by Claude Code's Task tool which launches an independent agent loop
with its own context window, isolated from the main conversation.

The sub-agent runs a full agent loop (model + tools) with:
- Its own system prompt tailored to the task type
- A filtered tool set based on the agent type
- A turn limit to prevent runaway execution
- Result summarized back into the parent context
"""
from __future__ import annotations

import concurrent.futures
import inspect
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import AgentTurnDeadlineExceeded, run_agent_turn
from minicode.run_events import (
    emit_event_safely,
    emit_skill_routing_safely,
)
from minicode.skill_router import required_skill_names_for_routing
from minicode.subagent_journal import SubagentRunJournal, new_subagent_id
from minicode.subagent_lifecycle import (
    ASYNC_AGENT_TYPES,
    SubagentLifecycleError,
    SubagentLifecycleNotFound,
    SubagentWorkerCancelled,
)
from minicode.subagent_observation import (
    SUBAGENT_EVENT_TYPE,
    project_subagent_event,
)
from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import (
    ToolCapability,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolMetadata,
    ToolResult,
)
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.verification_observation import project_verification


# ---------------------------------------------------------------------------
# Agent type definitions
# ---------------------------------------------------------------------------

# Maximum agent nesting depth. The top-level agent loop runs at depth 0.
#
# Depth 1 is a normal sub-agent. Depth 2 exists only for the structured
# `workflow` orchestrator's plan/execute/review phases, whose tool sets never
# include the `task` tool itself. General-purpose agents at depth 1 still have
# `task` removed, so this remains bounded: no path can grow a sub-agent tree
# deeper than two orchestration levels.
MAX_AGENT_DEPTH = 2

# Agent types whose sub-agents may run in the concurrent batch. These are the
# read-only types: their tool sets contain no writers, they run with
# `prompt=None` so they never raise a permission prompt from a worker thread,
# and they cannot touch the working tree. `general` is deliberately excluded —
# it can write files and inherits the parent's permission prompt, so two of
# them in flight could interleave edits and prompt concurrently.
CONCURRENCY_SAFE_AGENT_TYPES = frozenset({"explore", "plan"})

_DEFAULT_TASK_TIMEOUT_SECONDS = 120.0
_MIN_TASK_TIMEOUT_SECONDS = 0.001
_MAX_TASK_TIMEOUT_SECONDS = 86_400.0


class _TaskDeadlineExceeded(RuntimeError):
    """One sub-agent task exceeded its cooperative wall-clock budget."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"sub-agent deadline exceeded after {timeout_seconds:g}s"
        )


def _task_timeout_seconds(context: object) -> float:
    runtime = getattr(context, "_runtime", None)
    runtime_value = None
    if isinstance(runtime, dict):
        settings = runtime.get("task")
        runtime_value = (
            runtime.get("taskTimeoutSeconds")
            or runtime.get("agentTaskTimeoutSeconds")
            or (
                settings.get("timeoutSeconds")
                if isinstance(settings, dict)
                else None
            )
        )
    raw = os.environ.get("MINICODE_TASK_TIMEOUT_SECONDS") or runtime_value
    try:
        parsed = float(raw) if raw not in (None, "") else _DEFAULT_TASK_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        parsed = _DEFAULT_TASK_TIMEOUT_SECONDS
    if not parsed > 0 or parsed != parsed:
        parsed = _DEFAULT_TASK_TIMEOUT_SECONDS
    return min(
        _MAX_TASK_TIMEOUT_SECONDS,
        max(_MIN_TASK_TIMEOUT_SECONDS, parsed),
    )


def _raise_task_deadline(context: object) -> None:
    token = getattr(context, "_cancellation_token", None)
    checker = getattr(token, "raise_if_requested", None)
    if callable(checker):
        checker()
    deadline = getattr(context, "_task_deadline_monotonic", None)
    if isinstance(deadline, (int, float)) and time.monotonic() >= deadline:
        timeout = getattr(context, "_task_timeout_seconds", None)
        if not isinstance(timeout, (int, float)):
            timeout = _task_timeout_seconds(context)
        raise _TaskDeadlineExceeded(float(timeout))

AGENT_TYPES = {
    "explore": {
        "name": "Explore",
        "description": "Fast, read-only agent for codebase exploration and search",
        "system_prompt": (
            "You are an exploration agent. Your job is to quickly search and "
            "understand codebases. You should be fast and focused on finding "
            "relevant files and understanding structure. "
            "You can only use read-only tools. "
            "When done, provide a concise summary of your findings."
        ),
        "allowed_tools": {
            "read_file", "list_files", "grep_files", "file_tree",
            "find_symbols", "find_references", "get_ast_info", "load_skill",
            "subagent_note_read", "subagent_note_write", "subagent_note_list",
        },
        # A real exploration needs a tree/grep pass plus several reads before
        # it can summarize; 5 turns ran out mid-survey.
        "max_turns": 12,
    },
    "plan": {
        "name": "Plan",
        "description": "Thorough agent for gathering context and understanding code",
        "system_prompt": (
            "You are a planning agent. Your job is to thoroughly understand "
            "the codebase and task before acting. Read multiple files, trace "
            "code paths, and build a complete mental model. "
            "You can only use read-only tools. "
            "When done, provide a detailed analysis with actionable recommendations."
        ),
        "allowed_tools": {
            "read_file", "list_files", "grep_files", "file_tree",
            "find_symbols", "find_references", "get_ast_info", "code_review",
            "load_skill", "subagent_note_read", "subagent_note_write",
            "subagent_note_list",
        },
        "max_turns": 8,
    },
    "general": {
        "name": "General",
        "description": "Full-featured agent for complex multi-step tasks",
        "system_prompt": (
            "You are a general-purpose coding agent. You can read, write, "
            "and modify code. Follow best practices and explain your changes. "
            "Break complex tasks into smaller steps. "
            "When done, provide a summary of what you did and any important findings."
        ),
        "allowed_tools": None,  # None = all tools allowed
        "max_turns": 15,
    },
    "workflow": {
        "name": "Workflow",
        "description": (
            "Structured plan -> execute -> review collaboration across "
            "isolated sub-agents sharing one turn budget"
        ),
        "system_prompt": "",
        "allowed_tools": None,
        "max_phases": 4,
    },
}


def _validate(input_data: dict) -> dict:
    action = input_data.get("action", "run")
    if action not in {"run", "spawn", "poll", "cancel"}:
        raise ValueError("action must be one of: run, spawn, poll, cancel")

    if action in {"poll", "cancel"}:
        subagent_id = input_data.get("subagent_id")
        if not isinstance(subagent_id, str) or not subagent_id.strip():
            raise ValueError(f"subagent_id is required for action={action}")
        value = {
            "action": action,
            "subagent_id": subagent_id.strip(),
        }
        if action == "poll":
            wait_seconds = input_data.get("wait_seconds", 10.0)
            if (
                isinstance(wait_seconds, bool)
                or not isinstance(wait_seconds, (int, float))
                or not 0 <= float(wait_seconds) <= 30
            ):
                raise ValueError("wait_seconds must be between 0 and 30")
            value["wait_seconds"] = float(wait_seconds)
        return value

    description = input_data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    
    agent_type = input_data.get("agent_type", "general")
    if agent_type not in AGENT_TYPES:
        valid = ", ".join(AGENT_TYPES.keys())
        raise ValueError(f"agent_type must be one of: {valid}. Got: {agent_type}")
    if action == "spawn" and agent_type not in ASYNC_AGENT_TYPES:
        raise ValueError(
            "asynchronous spawn is restricted to read-only explore or plan "
            "agents; use action=run for general or workflow"
        )

    # `description` is specified as 3-5 words for display. Silently using it
    # as the sub-agent's entire task brief produces near-useless runs, so a
    # real prompt is required rather than defaulted.
    raw_prompt = input_data.get("prompt")
    prompt = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
    if not prompt:
        raise ValueError(
            "prompt is required: give the sub-agent a full, self-contained task "
            "description (it cannot see this conversation)"
        )

    parallel_explore = input_data.get("parallel_explore", False)
    if parallel_explore is None:
        parallel_explore = False
    if not isinstance(parallel_explore, bool):
        raise ValueError("parallel_explore must be a boolean")

    return {
        "action": action,
        "description": description.strip(),
        "agent_type": agent_type,
        "prompt": prompt,
        "parallel_explore": parallel_explore,
    }


def _sub_agent_progress_callbacks(context, agent_type: str):
    """Live tool progress for the parent UI, or ``(None, None)``.

    Routed to the presentation channel only — never to the Run journal or the
    approval session, whose tool tracking belongs to the parent Turn. Names
    are prefixed with the agent type so concurrent read-only sub-agents stay
    distinguishable in an interleaved stream.
    """
    presentation = getattr(context, "_presentation", None)
    if presentation is None:
        return None, None

    from minicode.conversation_presentation import (
        emit_tool_finished_safely,
        emit_tool_started_safely,
    )

    def on_start(tool_name: str, _tool_input: object) -> None:
        emit_tool_started_safely(presentation, f"{agent_type}▸{tool_name}")

    def on_result(tool_name: str, _output: object, is_error: bool) -> None:
        emit_tool_finished_safely(
            presentation, f"{agent_type}▸{tool_name}", is_error=is_error
        )

    return on_start, on_result


def _open_subagent_journal(context) -> SubagentRunJournal | None:
    """Open the parent Run's sidecar journal without exposing the Run writer."""
    sink = getattr(context, "_event_sink", None)
    opener = getattr(sink, "open_subagent_journal", None)
    if not callable(opener):
        return None
    try:
        journal = opener()
    except Exception:  # noqa: BLE001 - journaling is optional observation
        return None
    return journal if isinstance(journal, SubagentRunJournal) else None


class _SubagentJournalSink:
    """Count model calls and optionally write projected sidecar events.

    A model response may contain multiple tool calls, so counting
    ``assistant_tool_call`` messages overstates turns. ``model.started`` is
    emitted exactly once per admitted provider call and is therefore the
    authoritative turn counter. When a sidecar journal is available, this
    sink also keeps its strict, bounded event stream.
    """

    def __init__(
        self,
        journal: SubagentRunJournal | None,
        subagent_id: str,
    ) -> None:
        self._journal = journal
        self._subagent_id = subagent_id
        self._sequence = 0
        self.model_turns = 0

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        if event_type == "model.started":
            self.model_turns += 1
        self._sequence += 1
        if self._journal is None or self._sequence > 500:
            return
        try:
            self._journal.append_event(
                self._subagent_id,
                sequence=self._sequence,
                event_type=event_type,
                step=step,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 - sub-journal never alters execution
            pass


def _written_memory_recorder(context):
    """Bind a nested loop's written lesson to the parent Run, if possible.

    The nested loop cannot share the parent event sink (its lifecycle events
    would corrupt the parent journal), but the sink's write-only
    ``record_written_memory_ids`` method is exactly the narrow channel needed
    for a later explicit user verdict to reach the lesson.
    """
    sink = getattr(context, "_event_sink", None)
    record = getattr(sink, "record_written_memory_ids", None)
    if not callable(record):
        return None

    recorded_ids: list[str] = []

    def record_one(entry_id: str) -> None:
        if isinstance(entry_id, str) and entry_id and entry_id not in recorded_ids:
            recorded_ids.append(entry_id)
            # The sidecar is a snapshot, so publish the cumulative set rather
            # than replacing an earlier claim with the latest one.
            record(list(recorded_ids))

    return record_one


def _emit_subagent_observation(context, **fields) -> None:
    """Record one bounded summary of this sub-agent run in the parent Run.

    The nested loop's own events are deliberately NOT forwarded: readers such
    as `skill_evidence` require exactly one `task.outcome` / `skill.routed`
    per Run, so replaying a sub-agent's lifecycle into the parent stream would
    disqualify the Run from the Skill evidence ledger entirely.
    """
    sink = getattr(context, "_event_sink", None)
    if sink is None:
        return
    try:
        payload = project_subagent_event(**fields)
        if payload is None:
            return
        emit_event_safely(
            sink,
            SUBAGENT_EVENT_TYPE,
            step=getattr(context, "_step", None),
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - observation must never affect the result
        pass


def _subagent_result_instruction(result_key: str) -> str:
    return (
        "\n\nBefore your final response, call subagent_note_write with result "
        f"mailbox key `{result_key}` and content containing ONLY one JSON "
        "object with this exact shape: "
        '{"resultVersion":1,"summary":"...","files":'
        '[{"path":"relative/or/absolute/path","action":'
        '"read|created|modified|deleted|unknown"}],"risks":["..."],'
        '"verification":{"status":"passed|failed|not_run|inconclusive",'
        '"checks":["command or check"]}}. '
        "Use empty arrays when there are no files, risks, or checks. Do not "
        "add fields. This typed hand-back is separate from your concise "
        "human-readable <final> response."
    )


def _read_subagent_report(mailbox: object, result_key: str) -> object:
    reader = getattr(mailbox, "read", None)
    if not callable(reader):
        return None
    try:
        note = reader(result_key)
    except Exception:  # noqa: BLE001 - malformed hand-back becomes fallback
        return None
    return getattr(note, "content", None) if note is not None else None


def _build_sub_agent_system_prompt(
    *,
    cwd: str,
    agent_def: dict,
    task_prompt: str,
    tools,
    permissions,
) -> tuple[str, object | None]:
    """Compose the sub-agent's system prompt: the shared project/Skill-aware
    base plus this agent type's role and hand-back protocol.

    Falls back to the standalone role text if the shared builder is
    unavailable for any reason — a sub-agent with a plain prompt is far
    better than a sub-agent that cannot start.
    """
    role_text = (
        agent_def["system_prompt"]
        + f"\n\nCurrent cwd: {cwd}"
        + "\n\nIMPORTANT: When you have completed your task, end with <final> and provide your findings."
        + " Do not ask the user questions — work autonomously with the tools available."
        + " Be concise and focused."
    )
    routing = None
    try:
        from minicode.capability_registry import get_registry
        from minicode.intent_parser import parse_intent
        from minicode.prompt import build_system_prompt
        from minicode.skill_router import build_skill_router

        routing = build_skill_router(cwd).route(
            tools.get_skills(), parse_intent(task_prompt), get_registry()
        )
        try:
            permission_summary = permissions.get_summary()
        except Exception:  # noqa: BLE001 - summary is advisory only
            permission_summary = []
        skills_for_prompt = (
            tools.get_skills()
            if getattr(routing, "used_fallback", False)
            else routing.selected_skill_dicts()
        )
        base = build_system_prompt(
            cwd,
            permission_summary,
            {
                "skills": skills_for_prompt,
                "skill_routing": routing.to_dict(),
                "mcpServers": tools.get_mcp_servers(),
                "memory_context": "",
                "user_interaction_available": False,
            },
        )
    except Exception:  # noqa: BLE001 - never block the sub-agent on prompt assembly
        return role_text, None
    return f"{base}\n\n---\n\n{role_text}", routing


_WORKFLOW_EXPLORATION_FACETS = (
    (
        "architecture",
        "Map the modules, entry points, and ownership relevant to this task. "
        "Read the key files and explain the dependency flow.",
    ),
    (
        "tests",
        "Find the test layout, fixtures, and exact verification commands "
        "relevant to this task.",
    ),
    (
        "risks",
        "Find callers, configuration, migrations, and other places that "
        "could break when this task is implemented.",
    ),
)

_WORKFLOW_REVIEW_VERSION = 1
_WORKFLOW_REVIEW_VERDICTS = frozenset(
    {"approved", "changes_required", "inconclusive"}
)
_WORKFLOW_REVIEW_MAX_ITEMS = 6
_WORKFLOW_REVIEW_MAX_ITEM_CHARS = 200
_WORKFLOW_REVIEW_MAX_JSON_CHARS = 3000
_WORKFLOW_OUTPUT_MAX_CHARS = 8000
_WORKFLOW_MAX_PHASES = AGENT_TYPES["workflow"]["max_phases"]


def _normalize_workflow_review(content: object) -> tuple[dict[str, object] | None, str | None]:
    """Validate the reviewer's mailbox verdict as bounded authority.

    Reviewer prose is presentation only. The parent workflow accepts a result
    exclusively from this exact versioned JSON shape so a normally completed
    reviewer that reports blockers cannot be mistaken for approval.
    """
    if not isinstance(content, str) or not content.strip():
        return None, "review_verdict_missing"
    if len(content) > _WORKFLOW_REVIEW_MAX_JSON_CHARS:
        return None, "review_verdict_oversized"
    try:
        raw = json.loads(content)
    except (TypeError, ValueError):
        return None, "review_verdict_invalid_json"
    expected_fields = {
        "reviewVersion",
        "verdict",
        "blockingFindings",
        "warnings",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        return None, "review_verdict_invalid_shape"
    verdict = raw.get("verdict")
    blocking = raw.get("blockingFindings")
    warnings = raw.get("warnings")
    if (
        raw.get("reviewVersion") != _WORKFLOW_REVIEW_VERSION
        or verdict not in _WORKFLOW_REVIEW_VERDICTS
        or not isinstance(blocking, list)
        or not isinstance(warnings, list)
        or len(blocking) > _WORKFLOW_REVIEW_MAX_ITEMS
        or len(warnings) > _WORKFLOW_REVIEW_MAX_ITEMS
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > _WORKFLOW_REVIEW_MAX_ITEM_CHARS
            for item in [*blocking, *warnings]
        )
        or (verdict == "approved" and bool(blocking))
        or (verdict == "changes_required" and not blocking)
    ):
        return None, "review_verdict_invalid_shape"
    return {
        "reviewVersion": _WORKFLOW_REVIEW_VERSION,
        "verdict": verdict,
        "blockingFindings": list(blocking),
        "warnings": list(warnings),
    }, None


def _read_workflow_review(mailbox: object, key: str) -> tuple[dict[str, object] | None, str | None]:
    if mailbox is None:
        return None, "review_verdict_mailbox_unavailable"
    reader = getattr(mailbox, "read", None)
    if not callable(reader):
        return None, "review_verdict_mailbox_unavailable"
    try:
        note = reader(key)
    except Exception:  # noqa: BLE001 - malformed authority fails closed
        return None, "review_verdict_unavailable"
    if note is None:
        return None, "review_verdict_missing"
    return _normalize_workflow_review(getattr(note, "content", None))


def _workflow_failure_review(reason: str) -> dict[str, object]:
    return {
        "reviewVersion": _WORKFLOW_REVIEW_VERSION,
        "verdict": "inconclusive",
        "blockingFindings": [f"error[{reason}]: workflow review was not authoritative"],
        "warnings": [],
    }


def _bounded_workflow_section(label: str, content: str, limit: int) -> tuple[str, bool]:
    text = str(content or "")
    if len(text) <= limit:
        return f"{label}\n{text}", False
    suffix = "\n... [section truncated]"
    return f"{label}\n{text[: max(0, limit - len(suffix))]}{suffix}", True


def _workflow_result_counts(results: list[ToolResult]) -> tuple[int, int]:
    model_turns = 0
    tool_calls = 0
    for result in results:
        match = re.search(
            r"Model turns: (\d+) \(tool calls: (\d+)\)",
            result.output,
        )
        if match is not None:
            model_turns += int(match.group(1))
            tool_calls += int(match.group(2))
    return model_turns, tool_calls


def _workflow_model_turn_limit(agent_budget: object) -> int | None:
    snapshot = getattr(agent_budget, "snapshot", None)
    if not callable(snapshot):
        return None
    try:
        current = snapshot()
    except Exception:
        return None
    limit = getattr(current, "limit_model_calls", None)
    used = getattr(current, "used_model_calls", None)
    if (
        isinstance(limit, int)
        and not isinstance(limit, bool)
        and isinstance(used, int)
        and not isinstance(used, bool)
    ):
        return max(0, limit - used)
    return None


def _derive_workflow_result(
    *,
    subagent_id: str,
    workflow_status: str,
    execute_result: ToolResult,
    phase_results: list[ToolResult],
    typed_review: dict[str, object],
) -> dict[str, object]:
    """Aggregate typed phase hand-backs without trusting prose as evidence."""
    phase_reports = [
        report
        for report in (
            extract_subagent_result(result.output) for result in phase_results
        )
        if report is not None
    ]
    execute_report = extract_subagent_result(execute_result.output)
    summary = (
        str(execute_report["summary"])
        if execute_report is not None
        else str(execute_result.output or "workflow completed")[:4000]
    )

    files: list[dict[str, str]] = []
    seen_files: set[tuple[str, str]] = set()
    risks: list[str] = []
    for report in phase_reports:
        for item in report.get("files", []):
            if not isinstance(item, dict):
                continue
            pair = (str(item.get("path", "")), str(item.get("action", "unknown")))
            if not pair[0] or pair in seen_files or len(files) >= 40:
                continue
            seen_files.add(pair)
            files.append({"path": pair[0][:500], "action": pair[1]})
        for item in report.get("risks", []):
            text = str(item).strip()[:500]
            if text and text not in risks and len(risks) < 20:
                risks.append(text)
    for field in ("blockingFindings", "warnings"):
        values = typed_review.get(field, [])
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item).strip()[:500]
            if text and text not in risks and len(risks) < 20:
                risks.append(text)

    verdict = str(typed_review.get("verdict", "inconclusive"))
    verification_status = (
        "passed"
        if workflow_status == "completed" and verdict == "approved"
        else "failed"
        if verdict == "changes_required"
        else "inconclusive"
    )
    report_content = json.dumps(
        {
            "resultVersion": 1,
            "summary": summary or "workflow completed without a summary",
            "files": files,
            "risks": risks,
            "verification": {
                "status": verification_status,
                "checks": [f"workflow review verdict: {verdict}"],
            },
        },
        ensure_ascii=False,
    )
    projected = project_subagent_result(
        report_content,
        subagent_id=subagent_id,
        agent_type="workflow",
        outcome=workflow_status,
        fallback_summary=summary,
    )
    projected["contractStatus"] = "derived"
    return projected


def _emit_workflow_observation(
    context,
    *,
    subagent_id: str,
    result_contract_status: str,
    journal: SubagentRunJournal | None,
    outcome: str,
    results: list[ToolResult],
    phase_count: int,
    started: float,
    model_turn_limit: int | None,
    result_truncated: bool,
) -> None:
    model_turns, tool_calls = _workflow_result_counts(results)
    duration_ms = max(0, int((time.time() - started) * 1000))
    _emit_subagent_observation(
        context,
        subagent_id=subagent_id,
        result_contract_status=result_contract_status,
        agent_type="workflow",
        outcome=outcome,
        model_turns=model_turns,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
        model_turn_limit=model_turn_limit,
        phase_count=phase_count,
        max_phases=_WORKFLOW_MAX_PHASES,
        result_truncated=result_truncated,
    )
    if journal is not None:
        budget = getattr(context, "_agent_budget", None)
        snapshot = getattr(budget, "snapshot", None)
        try:
            journal.finish(
                subagent_id,
                outcome=(
                    outcome
                    if outcome in {"completed", "failed", "budget_exceeded"}
                    else "failed"
                ),
                model_turns=model_turns,
                tool_calls=tool_calls,
                duration_ms=duration_ms,
                max_turns=_WORKFLOW_MAX_PHASES,
                result_truncated=result_truncated,
                budget=snapshot() if callable(snapshot) else None,
            )
        except Exception:  # noqa: BLE001 - sidecar observation is optional
            pass


class _WorkflowIsolationError(RuntimeError):
    """The workflow snapshot could not be created or committed safely."""


class _WorkflowWorkspaceTransaction:
    """Run workflow phases in a disposable snapshot and commit on approval.

    A Git-backed workspace is snapshotted from its *current worktree* through
    an alternate index, so the user's pre-existing staged, unstaged and
    untracked source state becomes the transaction baseline without modifying
    the real index or refs.  Non-Git workspaces use a filesystem copy.  The
    isolated snapshot is always initialized as a standalone repository; an
    approved delta is applied to the parent only after ``git apply --check``.
    """

    def __init__(self, cwd: str) -> None:
        self.source_cwd = Path(cwd).resolve(strict=True)
        if not self.source_cwd.is_dir():
            raise _WorkflowIsolationError("workflow cwd is not a directory")
        self._temporary = tempfile.TemporaryDirectory(prefix="minicode-workflow-")
        self._temp_root = Path(self._temporary.name)
        self.source_root = self._discover_git_root() or self.source_cwd
        try:
            relative_cwd = self.source_cwd.relative_to(self.source_root)
        except ValueError as exc:
            self.close()
            raise _WorkflowIsolationError(
                "workflow cwd escaped its source root"
            ) from exc
        self.workspace_root = self._temp_root / "workspace"
        try:
            if self._is_git_workspace():
                self._snapshot_git_worktree()
            else:
                self._snapshot_directory()
            self._validate_snapshot_symlinks()
            self.cwd = self.workspace_root / relative_cwd
            if not self.cwd.is_dir():
                raise _WorkflowIsolationError(
                    "isolated workflow cwd was not materialized"
                )
            self._initialize_isolated_repository()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "_WorkflowWorkspaceTransaction":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            self._temporary = None
            temporary.cleanup()

    def _discover_git_root(self) -> Path | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.source_cwd), "rev-parse", "--show-toplevel"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise _WorkflowIsolationError(f"git discovery failed: {exc}") from exc
        if result.returncode != 0:
            return None
        try:
            root = Path(result.stdout.decode("utf-8").strip()).resolve(strict=True)
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return root if root.is_dir() else None

    def _is_git_workspace(self) -> bool:
        return self.source_root != self.source_cwd or (
            self.source_root / ".git"
        ).exists()

    @staticmethod
    def _command_error(args: list[str], result: subprocess.CompletedProcess) -> str:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = stderr[-600:] if stderr else f"exit {result.returncode}"
        return f"{args[0]} {args[1] if len(args) > 1 else ''}: {detail}"

    @classmethod
    def _run_checked(
        cls,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd),
                env=env,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise _WorkflowIsolationError(
                f"{args[0]} could not execute: {exc}"
            ) from exc
        if result.returncode != 0:
            raise _WorkflowIsolationError(cls._command_error(args, result))
        return result

    def _snapshot_git_worktree(self) -> None:
        alternate_index = self._temp_root / "snapshot.index"
        archive_path = self._temp_root / "snapshot.tar"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(alternate_index)
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(self.source_root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        read_tree = ["git", "read-tree", "HEAD"] if head.returncode == 0 else [
            "git",
            "read-tree",
            "--empty",
        ]
        self._run_checked(read_tree, cwd=self.source_root, env=environment)
        self._run_checked(
            ["git", "add", "-A", "--", "."],
            cwd=self.source_root,
            env=environment,
        )
        tree = self._run_checked(
            ["git", "write-tree"],
            cwd=self.source_root,
            env=environment,
        ).stdout.decode("ascii").strip()
        self._run_checked(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={archive_path}",
                tree,
            ],
            cwd=self.source_root,
            env=environment,
        )
        self.workspace_root.mkdir(parents=True)
        with tarfile.open(archive_path, mode="r") as archive:
            archive.extractall(self.workspace_root, filter="data")

    def _snapshot_directory(self) -> None:
        shutil.copytree(
            self.source_root,
            self.workspace_root,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git"),
        )

    def _validate_snapshot_symlinks(self) -> None:
        """Reject links that could route isolated writes into the parent."""
        for directory, dirnames, filenames in os.walk(
            self.workspace_root,
            followlinks=False,
        ):
            base = Path(directory)
            for name in [*dirnames, *filenames]:
                candidate = base / name
                if not candidate.is_symlink():
                    continue
                try:
                    resolved = candidate.resolve(strict=False)
                    contained = resolved.is_relative_to(self.workspace_root)
                except (OSError, RuntimeError, ValueError):
                    contained = False
                if not contained:
                    raise _WorkflowIsolationError(
                        f"snapshot contains an escaping symlink: "
                        f"{candidate.relative_to(self.workspace_root)}"
                    )

    def _initialize_isolated_repository(self) -> None:
        self._run_checked(["git", "init", "-q"], cwd=self.workspace_root)
        self._run_checked(
            ["git", "add", "-A", "-f", "--", "."],
            cwd=self.workspace_root,
        )
        self._run_checked(
            [
                "git",
                "-c",
                "user.name=MiniCode Workflow",
                "-c",
                "user.email=workflow@minicode.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "workflow baseline",
            ],
            cwd=self.workspace_root,
        )
        self._baseline = self._run_checked(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace_root,
        ).stdout.decode("ascii").strip()

    def remap_text(self, text: str) -> str:
        """Replace parent absolute paths before giving the task to agents."""
        mapped = str(text)
        replacements = sorted(
            {
                (str(self.source_root), str(self.workspace_root)),
                (str(self.source_cwd), str(self.cwd)),
            },
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        for source, target in replacements:
            mapped = mapped.replace(source, target)
        return mapped

    def commit(self) -> None:
        """Apply the approved isolated delta to the unchanged parent."""
        self._run_checked(
            ["git", "add", "-A", "-f", "--", "."],
            cwd=self.workspace_root,
        )
        patch = self._run_checked(
            [
                "git",
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                self._baseline,
                "--",
            ],
            cwd=self.workspace_root,
        ).stdout
        if not patch:
            return
        apply_args = ["git", "apply", "--binary", "--whitespace=nowarn", "-"]
        self._run_checked(
            [*apply_args[:2], "--check", *apply_args[2:]],
            cwd=self.source_root,
            input_bytes=patch,
        )
        self._run_checked(
            apply_args,
            cwd=self.source_root,
            input_bytes=patch,
        )


def _run_parallel_exploration(
    input_data: dict,
    context,
    child_context,
    mailbox,
) -> list[ToolResult]:
    """Run three read-only explore agents concurrently before planning.

    Each facet writes its findings to a dedicated mailbox key. Explore is
    concurrency-safe, has no permission prompt, and shares the parent Turn
    budget and mailbox, so this is the one place where workflow phases are
    intentionally parallel.
    """
    task_prompt = input_data["prompt"]
    description = input_data["description"]

    def explore_facet(key: str, angle: str) -> tuple[str, ToolResult]:
        note_key = f"workflow_explore_{key}"
        result = _run(
            {
                "description": f"explore:{description}:{key}",
                "agent_type": "explore",
                "prompt": (
                    f"{angle}\n\nTASK CONTEXT:\n{task_prompt}\n\n"
                    "Do not modify files. Write your findings to the shared "
                    f"mailbox with subagent_note_write using key {note_key}. "
                    "End with <final> and a concise summary."
                ),
            },
            child_context,
        )
        return key, result

    results_by_key: dict[str, ToolResult] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(_WORKFLOW_EXPLORATION_FACETS),
        thread_name_prefix="mc-workflow-explore",
    ) as pool:
        futures = [
            pool.submit(explore_facet, key, angle)
            for key, angle in _WORKFLOW_EXPLORATION_FACETS
        ]
        for future in concurrent.futures.as_completed(futures):
            key, result = future.result()
            results_by_key[key] = result
            if mailbox is not None:
                try:
                    mailbox.write(
                        f"workflow_explore_{key}",
                        (
                            result.output
                            if result.ok
                            else f"exploration failed: {result.output[:1000]}"
                        ),
                        author="workflow",
                    )
                except Exception:  # noqa: BLE001 - notes are optional
                    pass
    return [results_by_key[key] for key, _ in _WORKFLOW_EXPLORATION_FACETS]


def _run_workflow(
    input_data: dict,
    context,
    depth: int,
    subagent_id: str,
) -> ToolResult:
    """Run a workflow against a disposable workspace transaction."""
    agent_budget = getattr(context, "_agent_budget", None)
    if agent_budget is None:
        runtime = getattr(context, "_runtime", None)
        agent_budget = AgentTurnBudget.from_runtime(
            runtime if isinstance(runtime, dict) else {}
        )
        context = replace(context, _agent_budget=agent_budget)
    workflow_journal = _open_subagent_journal(context)
    if workflow_journal is not None:
        try:
            workflow_journal.start(
                subagent_id=subagent_id,
                agent_type="workflow",
                max_turns=_WORKFLOW_MAX_PHASES,
                budget=agent_budget.snapshot(),
            )
        except Exception:  # noqa: BLE001 - sidecar observation is optional
            workflow_journal = None
    if depth >= MAX_AGENT_DEPTH:
        # The phase runner owns the established typed depth-failure response
        # and observation projection.  No workspace snapshot is needed.
        return _run_workflow_phases(
            input_data,
            context,
            depth,
            subagent_id=subagent_id,
            workflow_journal=workflow_journal,
            transaction=None,
        )

    isolation_started = time.time()
    try:
        with _WorkflowWorkspaceTransaction(context.cwd) as transaction:
            isolated_input = dict(input_data)
            isolated_input["prompt"] = transaction.remap_text(input_data["prompt"])
            isolated_context = replace(context, cwd=str(transaction.cwd))
            return _run_workflow_phases(
                isolated_input,
                isolated_context,
                depth,
                subagent_id=subagent_id,
                workflow_journal=workflow_journal,
                transaction=transaction,
            )
    except (_TaskDeadlineExceeded, AgentTurnDeadlineExceeded) as exc:
        agent_budget = getattr(context, "_agent_budget", None)
        if agent_budget is None:
            runtime = getattr(context, "_runtime", None)
            agent_budget = AgentTurnBudget.from_runtime(
                runtime if isinstance(runtime, dict) else {}
            )
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[],
            phase_count=0,
            started=isolation_started,
            model_turn_limit=_workflow_model_turn_limit(agent_budget),
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=f"error[sub_agent_deadline_exceeded]: {exc}",
        )
    except _WorkflowIsolationError as exc:
        agent_budget = getattr(context, "_agent_budget", None)
        if agent_budget is None:
            runtime = getattr(context, "_runtime", None)
            agent_budget = AgentTurnBudget.from_runtime(
                runtime if isinstance(runtime, dict) else {}
            )
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[],
            phase_count=0,
            started=isolation_started,
            model_turn_limit=_workflow_model_turn_limit(agent_budget),
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=(
                "error[workflow_isolation_unavailable]: workflow execution "
                f"did not start because a safe workspace snapshot could not "
                f"be established ({exc})"
            ),
        )


def _run_workflow_phases(
    input_data: dict,
    context,
    depth: int,
    *,
    subagent_id: str,
    workflow_journal: SubagentRunJournal | None,
    transaction: _WorkflowWorkspaceTransaction | None,
) -> ToolResult:
    """Run plan -> execute -> review as three bounded sub-agents.

    Every phase is a normal ``_run`` invocation, so it gets the same shared
    budget, cancellation token, permission boundary, MCP isolation, sub-run
    journal and parent observation as a directly requested sub-agent. The
    phases run sequentially in this thread, which is also why `workflow` is
    excluded from the concurrent batch.
    """
    task_prompt = input_data["prompt"]
    description = input_data["description"]
    started = time.time()
    agent_budget = getattr(context, "_agent_budget", None)
    if agent_budget is None:
        runtime = getattr(context, "_runtime", None)
        agent_budget = AgentTurnBudget.from_runtime(
            runtime if isinstance(runtime, dict) else {}
        )
    model_turn_limit = _workflow_model_turn_limit(agent_budget)

    if depth >= MAX_AGENT_DEPTH:
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="depth_rejected",
            results=[],
            phase_count=0,
            started=started,
            model_turn_limit=model_turn_limit,
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=(
                f"error[sub_agent_depth_exceeded]: workflow cannot run at "
                f"depth {depth} (max {MAX_AGENT_DEPTH})"
            ),
        )

    child_context = replace(
        context,
        _agent_depth=depth + 1,
        _agent_budget=agent_budget,
    )
    mailbox = getattr(context, "_subagent_mailbox", None)
    explore_results: list[ToolResult] = []
    if input_data.get("parallel_explore"):
        explore_results = _run_parallel_exploration(
            input_data,
            context,
            child_context,
            mailbox,
        )
        _raise_task_deadline(context)

    research_notes = (
        "\n\nResearch notes are available in the shared mailbox under "
        "workflow_explore_architecture, workflow_explore_tests and "
        "workflow_explore_risks. Read the relevant notes with "
        "subagent_note_read before finalizing the plan."
        if explore_results
        else ""
    )

    plan_result = _run(
        {
            "description": f"plan:{description}",
            "agent_type": "plan",
            "prompt": (
                "Produce a concrete, step-by-step implementation plan for "
                "this task. Do not modify files. Write the final plan to the "
                "shared mailbox with subagent_note_write using key "
                "workflow_plan. End with <final> and a numbered plan plus "
                "the files you expect to touch."
                f"{research_notes}\n\n"
                f"TASK:\n{task_prompt}"
            ),
        },
        child_context,
    )
    _raise_task_deadline(context)
    if mailbox is not None:
        try:
            mailbox.write(
                "workflow_plan",
                plan_result.output,
                author="workflow",
            )
        except Exception:  # noqa: BLE001 - notes are optional collaboration
            pass
    if not plan_result.ok:
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[*explore_results, plan_result],
            phase_count=(1 if explore_results else 0) + 1,
            started=started,
            model_turn_limit=model_turn_limit,
            result_truncated=len(plan_result.output) > 8000,
        )
        return ToolResult(
            ok=False,
            output=(
                "[Workflow plan phase failed]\n\n"
                + plan_result.output[:8000]
            ),
        )

    execute_result = _run(
        {
            "description": f"execute:{description}",
            "agent_type": "general",
            "prompt": (
                "Implement the task below according to the approved plan. "
                "Read workflow_plan from the shared mailbox with "
                "subagent_note_read when it is available. Make the changes, "
                "run verification when available, and end with <final> plus "
                "a summary of changes and verification results. Write that "
                "summary to the shared mailbox with subagent_note_write "
                "using key workflow_result.\n\n"
                f"ORIGINAL TASK:\n{task_prompt}\n\n"
                f"PLAN:\n{plan_result.output[:12000]}"
            ),
        },
        child_context,
    )
    _raise_task_deadline(context)
    if mailbox is not None:
        try:
            mailbox.write(
                "workflow_result",
                execute_result.output,
                author="workflow",
            )
        except Exception:  # noqa: BLE001 - notes are optional collaboration
            pass
    if not execute_result.ok:
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[*explore_results, plan_result, execute_result],
            phase_count=(1 if explore_results else 0) + 2,
            started=started,
            model_turn_limit=model_turn_limit,
            result_truncated=len(execute_result.output) > 8000,
        )
        return ToolResult(
            ok=False,
            output=(
                "[Workflow execute phase failed]\n\n"
                + execute_result.output[:8000]
            ),
        )

    review_key = f"workflow_review_{new_subagent_id()}"
    review_result = _run(
        {
            "description": f"review:{description}",
            "agent_type": "plan",
            "prompt": (
                "Review the completed work below against the original task. "
                "Read workflow_plan and workflow_result from the shared "
                "mailbox with subagent_note_read when available. Read the "
                "changed files and verification output. Report blocking "
                "issues first, then non-blocking suggestions. Do not modify "
                "files. Before your final response, call subagent_note_write "
                f"with review verdict key `{review_key}` and content containing "
                "ONLY a JSON object with this exact shape: "
                '{"reviewVersion":1,"verdict":"approved|changes_required|inconclusive",'
                '"blockingFindings":["..."],"warnings":["..."]}. '
                "Use approved only when there are no blocking findings; use "
                "changes_required with at least one blocking finding; use "
                "inconclusive if evidence or verification is insufficient. "
                "The workflow fails closed if this typed verdict is missing "
                "or malformed. End with <final>.\n\n"
                f"ORIGINAL TASK:\n{task_prompt}\n\n"
                f"IMPLEMENTATION SUMMARY:\n{execute_result.output[:12000]}"
            ),
        },
        child_context,
    )
    _raise_task_deadline(context)

    duration_ms = int((time.time() - started) * 1000)
    typed_review, review_error = _read_workflow_review(mailbox, review_key)
    if typed_review is None:
        typed_review = _workflow_failure_review(
            review_error or "review_verdict_invalid"
        )
    workflow_ok = bool(
        review_result.ok and typed_review["verdict"] == "approved"
    )
    if workflow_ok:
        if transaction is None:
            typed_review = _workflow_failure_review(
                "workflow_transaction_missing"
            )
            workflow_ok = False
        else:
            try:
                transaction.commit()
            except _WorkflowIsolationError as exc:
                typed_review = _workflow_failure_review(
                    "workflow_commit_failed"
                )
                typed_review["blockingFindings"] = [
                    "error[workflow_commit_failed]: approved isolated changes "
                    f"were not applied to the parent workspace ({exc})"
                ]
                workflow_ok = False
    workflow_status = "completed" if workflow_ok else "failed"
    header = (
        f"[Workflow {description} {workflow_status}]\n"
        f"  Phases: explore -> plan -> execute -> review\n"
        f"  Duration: {duration_ms / 1000:.1f}s"
    )
    review_authority = json.dumps(
        typed_review,
        ensure_ascii=False,
        sort_keys=True,
    )
    research_text = "\n\n".join(
        result.output for result in explore_results
    )
    section_specs = (
        ("=== REVIEW VERDICT ===", review_authority, 3000),
        (
            "=== REVIEW NARRATIVE ==="
            if review_result.ok
            else "=== Workflow review phase failed ===",
            review_result.output,
            1200,
        ),
        ("=== EXECUTE ===", execute_result.output, 1600),
        ("=== PLAN ===", plan_result.output, 1000),
        ("=== RESEARCH ===", research_text, 500),
    )
    sections: list[str] = []
    result_truncated = False
    for label, content, limit in section_specs:
        if label == "=== RESEARCH ===" and not content:
            continue
        section, truncated = _bounded_workflow_section(label, content, limit)
        sections.append(section)
        result_truncated = result_truncated or truncated
    output = header + "\n\n" + "\n\n".join(sections)
    if len(output) > _WORKFLOW_OUTPUT_MAX_CHARS:
        result_truncated = True
        suffix = "\n\n... [workflow output truncated]"
        output = (
            output[: _WORKFLOW_OUTPUT_MAX_CHARS - len(suffix)]
            + suffix
        )

    all_phase_results = [
        *explore_results,
        plan_result,
        execute_result,
        review_result,
    ]
    structured_result = _derive_workflow_result(
        subagent_id=subagent_id,
        workflow_status=workflow_status,
        execute_result=execute_result,
        phase_results=all_phase_results,
        typed_review=typed_review,
    )
    output = output + "\n\n" + render_subagent_result(structured_result)

    _emit_workflow_observation(
        context,
        subagent_id=subagent_id,
        result_contract_status="derived",
        journal=workflow_journal,
        outcome=workflow_status,
        results=all_phase_results,
        phase_count=(1 if explore_results else 0) + 3,
        started=started,
        model_turn_limit=model_turn_limit,
        result_truncated=result_truncated,
    )
    return ToolResult(
        ok=workflow_ok,
        output=output,
        verification=(
            project_verification(
                kind="review",
                passed=True,
                source="workflow_review",
            )
            if workflow_ok
            else None
        ),
    )



def _run(input_data: dict, context) -> ToolResult:
    """Execute a sub-agent task.
    
    This creates an isolated agent loop with:
    - Its own message history (system + task prompt)
    - Filtered tools based on agent type
    - A turn limit
    - Result summarized for the parent context
    """
    from minicode.permissions import PermissionManager
    from minicode.subagent_model_routing import (
        SubagentModelRoutingError,
        create_subagent_model_adapter,
        resolve_subagent_model_route,
    )
    from minicode.tools import create_default_tool_registry

    agent_type = input_data["agent_type"]
    agent_def = AGENT_TYPES[agent_type]
    task_prompt = input_data["prompt"]
    reserved_subagent_id = input_data.get("_subagent_id")
    subagent_id = (
        reserved_subagent_id
        if isinstance(reserved_subagent_id, str)
        and re.fullmatch(r"sub_[0-9a-f]{32}", reserved_subagent_id)
        else new_subagent_id()
    )

    depth = getattr(context, "_agent_depth", 0)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        depth = 0
    if depth >= MAX_AGENT_DEPTH:
        limit_fields = (
            {
                "model_turn_limit": None,
                "phase_count": 0,
                "max_phases": _WORKFLOW_MAX_PHASES,
            }
            if agent_type == "workflow"
            else {"max_turns": agent_def["max_turns"]}
        )
        _emit_subagent_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            agent_type=agent_type,
            outcome="depth_rejected",
            model_turns=0,
            tool_calls=0,
            duration_ms=0,
            result_truncated=False,
            **limit_fields,
        )
        return ToolResult(
            ok=False,
            output=(
                f"error[sub_agent_depth_exceeded]: already running at sub-agent "
                f"depth {depth} (max {MAX_AGENT_DEPTH}). Sub-agents cannot spawn "
                f"further sub-agents — complete this task with the tools you have."
            ),
        )

    # One monotonic deadline covers the whole Task invocation. Workflow
    # phases receive this token through their child contexts; nested tokens
    # may shorten, but never extend, the parent's absolute deadline.
    timeout_seconds = _task_timeout_seconds(context)
    own_deadline = time.monotonic() + timeout_seconds
    parent_deadline = getattr(context, "_task_deadline_monotonic", None)
    effective_deadline = (
        min(own_deadline, float(parent_deadline))
        if isinstance(parent_deadline, (int, float))
        else own_deadline
    )
    parent_timeout = getattr(context, "_task_timeout_seconds", None)
    effective_timeout = (
        min(timeout_seconds, float(parent_timeout))
        if isinstance(parent_timeout, (int, float))
        else timeout_seconds
    )
    context = replace(
        context,
        _task_deadline_monotonic=effective_deadline,
        _task_timeout_seconds=effective_timeout,
    )
    _raise_task_deadline(context)

    if agent_type == "workflow":
        return _run_workflow(input_data, context, depth, subagent_id)

    # Try to get the model from context or fall back to creating one
    # The context object carries runtime info needed for the model adapter
    runtime = None
    model = None
    
    # Attempt to extract runtime from the ToolContext
    if hasattr(context, '_runtime') and context._runtime:
        runtime = context._runtime
    
    if not runtime:
        # Try loading from config
        try:
            from minicode.config import load_runtime_config
            runtime = load_runtime_config(context.cwd)
        except Exception:
            pass
    
    if not runtime:
        return ToolResult(
            ok=False,
            output="Cannot run sub-agent: no model configuration available. Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL."
        )

    # Validate an explicitly enabled child route before constructing MCP
    # servers, budgets, journals, or any model adapter. A missing dedicated
    # credential must fail closed: silently inheriting the parent's model or
    # leaking an exception out of the tool would both violate the routing
    # contract.
    try:
        resolve_subagent_model_route(runtime, agent_type)
    except SubagentModelRoutingError as error:
        _emit_subagent_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            agent_type=agent_type,
            outcome="failed",
            model_turns=0,
            tool_calls=0,
            duration_ms=0,
            max_turns=agent_def["max_turns"],
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=f"error[subagent_model_route_invalid]: {error}",
        )

    # Shared turn budget: prefer the parent's object, otherwise create a
    # local bounded budget for standalone tool invocations. Parallel
    # sub-agents that receive the parent budget contend on the same counters.
    agent_budget = getattr(context, "_agent_budget", None)
    if agent_budget is None:
        agent_budget = AgentTurnBudget.from_runtime(runtime)

    # Sidecar sub-run journal. The parent Run stream keeps only the bounded
    # summary event; detailed nested-loop events go here and are removed with
    # the parent Run during retention.
    subagent_journal = _open_subagent_journal(context)
    start_time = time.time()
    if subagent_journal is not None:
        try:
            subagent_journal.start(
                subagent_id=subagent_id,
                agent_type=agent_type,
                max_turns=agent_def["max_turns"],
                budget=agent_budget.snapshot(),
            )
        except Exception:  # noqa: BLE001 - journal start is optional
            subagent_journal = None

    # Create a filtered tool registry for this agent type. Read-only
    # sub-agents ask the real factory to skip MCP construction entirely, so
    # parallel explore/plan runs do not start a full set of MCP servers each.
    current_state_registry = getattr(
        context,
        "_mcp_current_state_registry",
        None,
    )
    read_only_agent = agent_def["allowed_tools"] is not None

    def _accepts_registry_option(factory: object, option: str) -> bool:
        try:
            parameters = inspect.signature(factory).parameters
        except (TypeError, ValueError):
            return False
        return (
            option in parameters
            or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        )

    registry_kwargs: dict[str, object] = {}
    if current_state_registry is not None:
        registry_kwargs["mcp_current_state_registry"] = current_state_registry
    if _accepts_registry_option(create_default_tool_registry, "include_mcp"):
        registry_kwargs["include_mcp"] = not read_only_agent
    if _accepts_registry_option(
        create_default_tool_registry,
        "include_user_interaction",
    ):
        # A nested agent has no direct conversation channel. Questions must
        # be returned to the parent as structured results, never pause the
        # child's loop as though the end user could answer it.
        registry_kwargs["include_user_interaction"] = False
    full_tools = create_default_tool_registry(
        context.cwd,
        runtime=runtime,
        **registry_kwargs,
    )
    try:
        from minicode.tooling import ToolRegistry

        allowed = agent_def["allowed_tools"]
        if allowed is not None:
            filtered_tools = [t for t in full_tools.list() if t.name in allowed]
        else:
            # `general` gets the full registry, minus this tool itself:
            # general agents are the deepest actors in any orchestration, and
            # withholding `task` is what keeps the depth-2 workflow bounded.
            # Advertising a tool whose every invocation would be refused is
            # strictly worse than omitting it.
            filtered_tools = [t for t in full_tools.list() if t.name != "task"]
        tools = ToolRegistry(
            filtered_tools,
            skills=full_tools.get_skills(),
            mcp_current_state_registry=current_state_registry,
        )

        model = create_subagent_model_adapter(
            runtime,
            agent_type,
            tools=tools,
        )
        model_runtime = dict(getattr(model, "runtime", runtime) or runtime)

        if agent_def["allowed_tools"] is not None:
            sub_permissions = PermissionManager(context.cwd, prompt=None)
        else:
            sub_permissions = PermissionManager(
                context.cwd,
                prompt=getattr(context.permissions, "prompt", None),
            )

        # Route Skills for the sub-agent's own task, exactly as the top-level
        # runtime does. Without this the sub-agent's system prompt is the
        # hardcoded blurb below and it never learns that Skills exist, so it
        # can neither see candidates nor call load_skill.
        #
        # Note run_agent_turn's `system_prompt`/`project_context` parameters
        # only feed a LayeredContext that nothing consumes; the prompt the
        # model actually receives is messages[0], so it is composed here.
        sub_system_prompt, sub_skill_routing = _build_sub_agent_system_prompt(
            cwd=context.cwd,
            agent_def=agent_def,
            task_prompt=task_prompt,
            tools=tools,
            permissions=sub_permissions,
        )
        result_key = f"subagent_result_{subagent_id}"
        mailbox = getattr(context, "_subagent_mailbox", None)
        result_instruction = (
            _subagent_result_instruction(result_key)
            if mailbox is not None
            else ""
        )
        sub_messages = [
            {
                "role": "system",
                "content": sub_system_prompt + result_instruction,
            },
            {
                "role": "user",
                "content": task_prompt,
            },
        ]

        # Give the sub-agent its own context window governor. Without this the
        # sub-agent runs with no compaction at all, so a long exploration just
        # grows the prompt until the provider rejects it.
        from minicode.context_manager import ContextManager

        sub_context_manager = ContextManager(
            model=model_runtime.get("model", "default")
        )
        sub_context_manager.messages = sub_messages

        # Share the project's memory store. Injection is a clear win — the
        # sub-agent otherwise re-derives conventions the project already
        # recorded. Reflection write-back rides along; that is acceptable
        # because automatic memories land in the pending-approval queue
        # rather than the active pool, so a narrow subtask cannot silently
        # promote low-signal "lessons" into future prompts.
        try:
            from minicode.memory import MemoryManager

            sub_memory = MemoryManager(project_root=context.cwd)
        except Exception:  # noqa: BLE001 - memory is an enhancement, not a prerequisite
            sub_memory = None

        max_turns = agent_def["max_turns"]
    except BaseException:
        try:
            full_tools.dispose()
        except BaseException:
            pass
        raise
    
    journal_outcome = "failed"
    journal_model_turns = 0
    journal_tool_calls = 0
    journal_result_truncated = False
    structured_result: dict[str, object] | None = None
    try:
        try:
            sub_on_tool_start, sub_on_tool_result = _sub_agent_progress_callbacks(
                context, agent_type
            )
            sub_event_sink = _SubagentJournalSink(subagent_journal, subagent_id)
            if sub_skill_routing is not None:
                emit_skill_routing_safely(sub_event_sink, sub_skill_routing)
            outcome_capture = AgentOutcomeCapture()
            result_messages = run_agent_turn(
                model=model,
                tools=tools,
                messages=sub_messages,
                cwd=context.cwd,
                permissions=sub_permissions,
                max_steps=max_turns,
                on_tool_start=sub_on_tool_start,
                on_tool_result=sub_on_tool_result,
                runtime=model_runtime,
                context_manager=sub_context_manager,
                memory_manager=sub_memory,
                on_memory_written=_written_memory_recorder(context),
                event_sink=sub_event_sink,
                agent_budget=agent_budget,
                budget_exhausted_policy="raise",
                abandoned_event=getattr(context, "_tool_abandoned", None),
                subagent_mailbox=getattr(context, "_subagent_mailbox", None),
                subagent_lifecycle=getattr(
                    context,
                    "_subagent_lifecycle",
                    None,
                ),
                # Cancelling the parent Turn must stop the sub-agent too;
                # otherwise it keeps calling the model after the user gave up.
                cancellation_token=getattr(context, "_cancellation_token", None),
                deadline_monotonic=getattr(
                    context,
                    "_task_deadline_monotonic",
                    None,
                ),
                agent_depth=depth + 1,
                outcome_capture=outcome_capture,
                required_skill_names=required_skill_names_for_routing(
                    sub_skill_routing
                ),
                allow_user_interaction=False,
            )
            _raise_task_deadline(context)
            # Extract final message and counts before the finally block runs,
            # so the sidecar journal records the completed outcome instead of
            # the pre-success placeholder.
            final_message = None
            for msg in reversed(result_messages):
                if msg.get("role") == "assistant" and msg.get("content", "").strip():
                    final_message = msg["content"]
                    break
            if not final_message:
                final_message = "(sub-agent completed without a final message)"
            journal_result_truncated = len(final_message) > 8000
            tool_calls_count = sum(
                1
                for m in result_messages
                if m.get("role") == "assistant_tool_call"
            )
            message_model_turns = sum(
                1
                for m in result_messages
                if m.get("role") in {"assistant", "assistant_tool_call"}
            )
            # Real loops emit one model.started event per provider call. Keep
            # the message projection only as a compatibility fallback for
            # injected test doubles and older alternate loop implementations.
            model_turns = sub_event_sink.model_turns or message_model_turns
            typed_outcome = outcome_capture.outcome
            if typed_outcome is None or typed_outcome.status != "success":
                status = (
                    typed_outcome.status
                    if typed_outcome is not None
                    else "unknown"
                )
                journal_outcome = "failed"
                journal_model_turns = model_turns
                journal_tool_calls = tool_calls_count
                _emit_subagent_observation(
                    context,
                    subagent_id=subagent_id,
                    result_contract_status="unavailable",
                    agent_type=agent_type,
                    outcome="failed",
                    model_turns=model_turns,
                    tool_calls=tool_calls_count,
                    duration_ms=int((time.time() - start_time) * 1000),
                    max_turns=max_turns,
                    result_truncated=journal_result_truncated,
                )
                return ToolResult(
                    ok=False,
                    output=(
                        f"error[sub_agent_{status}]: the nested Agent ended "
                        "without a successful typed outcome.\n\n"
                        f"{final_message[:4000]}"
                    ),
                )
            journal_outcome = "completed"
            journal_model_turns = model_turns
            journal_tool_calls = tool_calls_count
            structured_result = project_subagent_result(
                _read_subagent_report(mailbox, result_key),
                subagent_id=subagent_id,
                agent_type=agent_type,
                outcome="completed",
                fallback_summary=final_message,
            )
        except TurnCancellationRequested:
            # Cooperative cancellation is the parent Turn's decision, not a
            # sub-agent failure — let it propagate so the parent unwinds.
            raise
        except ToolExecutionAbandoned as e:
            # The parent tool timeout has already returned a result; this
            # thread's job is only to stop spending model calls and record
            # the abandoned sub-run.
            journal_outcome = "failed"
            _emit_subagent_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="unavailable",
                agent_type=agent_type,
                outcome="failed",
                model_turns=0,
                tool_calls=0,
                duration_ms=int((time.time() - start_time) * 1000),
                max_turns=max_turns,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=f"error[tool_abandoned]: {e}",
            )
        except (_TaskDeadlineExceeded, AgentTurnDeadlineExceeded) as e:
            journal_outcome = "failed"
            _emit_subagent_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="unavailable",
                agent_type=agent_type,
                outcome="failed",
                model_turns=0,
                tool_calls=0,
                duration_ms=int((time.time() - start_time) * 1000),
                max_turns=max_turns,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=f"error[sub_agent_deadline_exceeded]: {e}",
            )
        except AgentBudgetExceeded as e:
            journal_outcome = "budget_exceeded"
            # The shared snapshot includes sibling sub-agents' consumption, so
            # it cannot be attributed to this run; report zero here and keep
            # the exact shared counters in the journal's budget snapshot.
            journal_model_turns = 0
            _emit_subagent_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="unavailable",
                agent_type=agent_type,
                outcome="budget_exceeded",
                model_turns=journal_model_turns,
                tool_calls=0,
                duration_ms=int((time.time() - start_time) * 1000),
                max_turns=max_turns,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=(
                    f"error[agent_budget_exceeded]: {e.reason}. "
                    "The shared turn budget was exhausted; simplify the task "
                    "or raise the budget limit."
                ),
            )
        except Exception as e:
            journal_outcome = "failed"
            _emit_subagent_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="unavailable",
                agent_type=agent_type,
                outcome="failed",
                model_turns=0,
                tool_calls=0,
                duration_ms=int((time.time() - start_time) * 1000),
                max_turns=max_turns,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=(
                    f"Sub-agent ({agent_def['name']}) failed: "
                    f"{type(e).__name__}: {e}"
                ),
            )
    finally:
        try:
            full_tools.dispose()
        except BaseException:
            pass
        if subagent_journal is not None:
            try:
                subagent_journal.finish(
                    subagent_id,
                    outcome=journal_outcome,
                    model_turns=journal_model_turns,
                    tool_calls=journal_tool_calls,
                    duration_ms=int((time.time() - start_time) * 1000),
                    max_turns=max_turns,
                    result_truncated=journal_result_truncated,
                    budget=agent_budget.snapshot(),
                )
            except Exception:  # noqa: BLE001 - journal finish is optional
                pass

    elapsed = time.time() - start_time
    
    # Build summary. Model turns are assistant messages; tool results also
    # arrive with role="user", so counting user messages (as this once did)
    # reported neither turns nor tool calls. Both values were computed before
    # the cleanup/finally block so the sub-run journal sees the real outcome.

    header = (
        f"[Sub-agent {agent_def['name']} completed]\n"
        f"  Type: {agent_type}\n"
        f"  Model turns: {model_turns} (tool calls: {tool_calls_count})\n"
        f"  Duration: {elapsed:.1f}s\n"
        f"  Max turns: {max_turns}\n"
    )
    
    # Truncate very long results
    result_text = final_message
    MAX_RESULT_LEN = 8000
    truncated = journal_result_truncated
    if truncated:
        result_text = result_text[:MAX_RESULT_LEN] + f"\n\n... (truncated, {len(final_message)} chars total)"

    if structured_result is None:  # Defensive compatibility with alternate loops.
        structured_result = project_subagent_result(
            None,
            subagent_id=subagent_id,
            agent_type=agent_type,
            outcome="completed",
            fallback_summary=final_message,
        )

    _emit_subagent_observation(
        context,
        subagent_id=subagent_id,
        result_contract_status=str(structured_result["contractStatus"]),
        agent_type=agent_type,
        outcome="completed",
        model_turns=model_turns,
        tool_calls=tool_calls_count,
        duration_ms=int(elapsed * 1000),
        max_turns=max_turns,
        result_truncated=truncated,
    )

    return ToolResult(
        ok=True,
        output=(
            header
            + "\n"
            + result_text
            + "\n\n"
            + render_subagent_result(structured_result)
        ),
    )


# Preserve the original public synchronous binding. Several workflow tests and
# embedders replace ``_run`` to stand in for the plan/execute/review children;
# the outer workflow itself must still enter the real orchestrator, just as it
# did when ToolDefinition.run was bound directly to this function.
_SYNC_RUN = _run


def _dispatch(input_data: dict, context) -> ToolResult:
    """Dispatch synchronous and asynchronous task lifecycle operations."""
    action = input_data.get("action", "run")
    if action == "run":
        return _SYNC_RUN(input_data, context)

    lifecycle = getattr(context, "_subagent_lifecycle", None)
    if lifecycle is None:
        return ToolResult(
            ok=False,
            output=(
                "error[sub_agent_lifecycle_unavailable]: asynchronous task "
                "operations require an active agent turn"
            ),
        )
    try:
        if action == "spawn":
            child_input = dict(input_data)
            child_input.pop("action", None)

            def _runner(subagent_id, cancel_event) -> ToolResult:
                reserved_input = dict(child_input)
                reserved_input["_subagent_id"] = subagent_id
                child_context = replace(
                    context,
                    _tool_abandoned=cancel_event,
                )
                result = _run(reserved_input, child_context)
                if (
                    cancel_event.is_set()
                    and not result.ok
                    and result.output.startswith("error[tool_abandoned]:")
                ):
                    raise SubagentWorkerCancelled()
                return result

            snapshot = lifecycle.spawn(
                agent_type=input_data["agent_type"],
                runner=_runner,
            )
        elif action == "poll":
            snapshot = lifecycle.poll(
                input_data["subagent_id"],
                wait_seconds=input_data.get("wait_seconds", 10.0),
            )
        elif action == "cancel":
            snapshot = lifecycle.cancel(input_data["subagent_id"])
        else:  # Validator owns this enum; stay fail-closed for direct callers.
            return ToolResult(
                ok=False,
                output="error[sub_agent_action_invalid]: unsupported action",
            )
    except SubagentLifecycleNotFound:
        return ToolResult(
            ok=False,
            output=(
                "error[sub_agent_not_found]: sub-agent ID is not owned by "
                "this turn"
            ),
        )
    except SubagentLifecycleError as exc:
        return ToolResult(
            ok=False,
            output=f"error[sub_agent_lifecycle_rejected]: {exc}",
        )
    return ToolResult(
        ok=True,
        output=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
    )


def _call_is_concurrency_safe(call_input: object) -> bool:
    """Only read-only sub-agent types may share the concurrent batch."""
    if not isinstance(call_input, dict):
        return False
    action = call_input.get("action", "run")
    if action in {"poll", "cancel"}:
        return True
    if action == "spawn":
        return call_input.get("agent_type") in ASYNC_AGENT_TYPES
    return call_input.get("agent_type") in CONCURRENCY_SAFE_AGENT_TYPES


task_tool = ToolDefinition(
    name="task",
    description=(
        "Run or manage a sub-agent for a complex task. The default action "
        "'run' waits for the final result. Use 'spawn' with read-only "
        "'explore'/'plan', then 'poll' or 'cancel' with the returned ID, "
        "when the parent should continue working concurrently. "
        "The sub-agent runs in its own isolated context with a turn limit. "
        "Use 'explore' for fast read-only codebase exploration, "
        "'plan' for thorough analysis, 'general' for full-featured "
        "multi-step work, or 'workflow' for a sequential "
        "plan -> execute -> review collaboration of three isolated "
        "sub-agents. Multiple 'explore'/'plan' sub-agents issued in one "
        "step run in parallel; 'general' and 'workflow' always run one at "
        "a time. All sub-agents share the current turn's "
        "model-call/token/cost budget. The sub-agent's final result is "
        "returned to you."
    ),
    metadata=ToolMetadata(
        name="task",
        description="Sub-agent launcher",
        capabilities={ToolCapability.CONCURRENCY_SAFE},
    ),
    concurrency_safe_for=_call_is_concurrency_safe,
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "spawn", "poll", "cancel"],
                "description": (
                    "Lifecycle operation. Defaults to synchronous 'run'. "
                    "Only explore/plan support asynchronous 'spawn'."
                ),
            },
            "subagent_id": {
                "type": "string",
                "description": "Opaque ID returned by spawn; required by poll/cancel.",
            },
            "wait_seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": 30,
                "description": (
                    "For poll only: wait this many seconds for a terminal "
                    "result before returning. Defaults to 10 to avoid busy "
                    "polling with model calls."
                ),
            },
            "description": {
                "type": "string",
                "description": "Short 3-5 word description of the task",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Full, self-contained task description for the sub-agent. "
                    "The sub-agent runs in a fresh context and cannot see this "
                    "conversation, so include every file path, constraint, and "
                    "piece of background it needs."
                ),
            },
            "agent_type": {
                "type": "string",
                "enum": ["explore", "plan", "general", "workflow"],
                "description": (
                    "Type of sub-agent: 'explore' (fast, read-only), "
                    "'plan' (thorough, read-only), 'general' (full tools, "
                    "default), or 'workflow' (plan -> execute -> review "
                    "collaboration)."
                ),
            },
            "parallel_explore": {
                "type": "boolean",
                "description": (
                    "For workflow only: run three read-only explore agents "
                    "in parallel (architecture, tests, risks) and write "
                    "their findings to the shared mailbox before planning."
                ),
            },
        },
        "oneOf": [
            {
                "required": ["description", "prompt"],
                "properties": {"action": {"enum": ["run"]}},
            },
            {
                "required": ["action", "description", "prompt", "agent_type"],
                "properties": {"action": {"enum": ["spawn"]}},
            },
            {
                "required": ["action", "subagent_id"],
                "properties": {"action": {"enum": ["poll", "cancel"]}},
            },
        ],
    },
    validator=_validate,
    run=_dispatch,
)
