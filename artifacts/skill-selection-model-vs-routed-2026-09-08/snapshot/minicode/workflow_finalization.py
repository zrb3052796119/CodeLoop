"""Read-only projection of a sealed centrally scheduled workflow run.

This module deliberately has no access to workflow execution capabilities.
It may checkpoint cancellation, persist a scheduler audit, emit an observation,
and render a truthful bounded ``ToolResult`` from detached authority facts.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from minicode.central_scheduler import (
    DispatchPlan,
    DispatchStage,
    SchedulerTransition,
    TaskProfile,
    WorkflowExecutionOutcome,
)
from minicode.scheduler_artifacts import SchedulerArtifact
from minicode.scheduler_audit import project_scheduler_audit
from minicode.scheduler_trace import (
    SCHEDULER_TRACE_MARKER,
    project_scheduler_trace,
    render_scheduler_trace,
)
from minicode.subagent_result import (
    SUBAGENT_RESULT_MARKER,
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.tooling import ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.verification_observation import project_verification

__all__ = ("finalize_workflow",)


_WORKFLOW_REVIEW_VERSION = 1
_WORKFLOW_OUTPUT_MAX_CHARS = 8000
_WORKFLOW_RESULT_SUMMARY_MAX_CHARS = 1000


def _canonical_json(value: object, *, label: str) -> str:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be canonical JSON data") from exc


def _optional_canonical_json(value: object | None, *, label: str) -> str | None:
    return None if value is None else _canonical_json(value, label=label)


@dataclass(frozen=True, slots=True)
class _PhaseResultSnapshot:
    """The scalar-only part of a phase result needed after execution seals."""

    ok: bool
    output: str

    @classmethod
    def from_result(cls, result: ToolResult) -> _PhaseResultSnapshot:
        if type(result) is not ToolResult:
            raise TypeError("phase result must be a ToolResult")
        if type(result.ok) is not bool or type(result.output) is not str:
            raise TypeError("phase result scalars are invalid")
        return cls(ok=result.ok, output=result.output)

    def to_result(self) -> ToolResult:
        return ToolResult(ok=self.ok, output=self.output)


@dataclass(frozen=True, slots=True)
class _WorkflowObservation:
    """Content-free facts handed to the host observation adapter."""

    result_contract_status: str
    outcome: str
    phase_count: int
    result_truncated: bool
    duration_ms: int
    model_turns: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class _WorkflowFinalizationPorts:
    """The complete side-effect surface available after Scheduler execution."""

    checkpoint: Callable[[], None]
    persist_audit: Callable[[object], object] | None
    observe: Callable[[_WorkflowObservation], None]


@dataclass(frozen=True, slots=True)
class _WorkflowFinalizationSnapshot:
    """Detached facts captured only after the Scheduler reaches a terminal state."""

    description: str
    subagent_id: str
    contract_mode: str
    profile: TaskProfile
    dispatch_plan: DispatchPlan
    scheduler_outcome: WorkflowExecutionOutcome
    transitions: tuple[SchedulerTransition, ...]
    artifacts: tuple[SchedulerArtifact, ...] | None
    explore_results: tuple[_PhaseResultSnapshot, ...]
    plan_result: _PhaseResultSnapshot | None
    execute_result: _PhaseResultSnapshot | None
    review_result: _PhaseResultSnapshot | None
    analyst_report_json: str | None
    execution_artifact_json: str | None
    verification_result_json: str | None
    typed_review_json: str
    approval_request_id: str | None
    commit_ready_status: str
    commit_error_text: str | None
    production_audit_required: bool
    initial_finalization_status: str
    started_at: float

    def __post_init__(self) -> None:
        """Reject mutable or open-ended values even on direct construction."""
        exact_strings = {
            "description": self.description,
            "subagent_id": self.subagent_id,
            "contract_mode": self.contract_mode,
            "typed_review_json": self.typed_review_json,
            "commit_ready_status": self.commit_ready_status,
            "initial_finalization_status": self.initial_finalization_status,
        }
        if any(type(value) is not str for value in exact_strings.values()):
            raise TypeError("finalization snapshot string facts must be exact strings")
        if self.contract_mode not in {"legacy", "v2"}:
            raise ValueError("finalization contract mode is invalid")
        if self.initial_finalization_status not in {
            "completed",
            "cancelled_after_commit",
            "timed_out",
            "timed_out_after_commit",
            "projection_failed",
        }:
            raise ValueError("initial finalization status is invalid")
        if not isinstance(self.profile, TaskProfile):
            raise TypeError("finalization profile is invalid")
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError("finalization dispatch plan is invalid")
        if not isinstance(self.scheduler_outcome, WorkflowExecutionOutcome):
            raise TypeError("finalization scheduler outcome is invalid")
        if type(self.transitions) is not tuple or any(
            not isinstance(item, SchedulerTransition) for item in self.transitions
        ):
            raise TypeError("finalization transitions must be an immutable tuple")
        if self.artifacts is not None and (
            type(self.artifacts) is not tuple
            or any(not isinstance(item, SchedulerArtifact) for item in self.artifacts)
        ):
            raise TypeError("finalization artifacts must be an immutable tuple")
        if type(self.explore_results) is not tuple or any(
            type(item) is not _PhaseResultSnapshot for item in self.explore_results
        ):
            raise TypeError("finalization explore results must be immutable")
        for phase_result in (
            self.plan_result,
            self.execute_result,
            self.review_result,
        ):
            if (
                phase_result is not None
                and type(phase_result) is not _PhaseResultSnapshot
            ):
                raise TypeError("finalization phase result is invalid")
        for document in (
            self.analyst_report_json,
            self.execution_artifact_json,
            self.verification_result_json,
        ):
            if document is not None and type(document) is not str:
                raise TypeError("finalization document snapshot is invalid")
        for optional_text in (self.approval_request_id, self.commit_error_text):
            if optional_text is not None and type(optional_text) is not str:
                raise TypeError("finalization optional text fact is invalid")
        if type(self.production_audit_required) is not bool:
            raise TypeError("production_audit_required must be boolean")
        if type(self.started_at) is not float or not math.isfinite(self.started_at):
            raise TypeError("started_at must be a finite float timestamp")

    @classmethod
    def capture(
        cls,
        *,
        description: str,
        subagent_id: str,
        contract_mode: str,
        profile: TaskProfile,
        dispatch_plan: DispatchPlan,
        scheduler_outcome: WorkflowExecutionOutcome,
        transitions: Iterable[SchedulerTransition],
        artifacts: Iterable[SchedulerArtifact] | None,
        explore_results: Iterable[ToolResult],
        plan_result: ToolResult | None,
        execute_result: ToolResult | None,
        review_result: ToolResult | None,
        analyst_report: dict[str, object] | None,
        execution_artifact: dict[str, object] | None,
        verification_result: dict[str, object] | None,
        typed_review: dict[str, object],
        approval_request_id: str | None,
        commit_ready_status: str,
        commit_error_text: str | None,
        production_audit_required: bool,
        initial_finalization_status: str,
        started_at: float,
    ) -> _WorkflowFinalizationSnapshot:
        """Seal mutable runner values behind the read-only finalization seam."""
        sealed_transitions = tuple(transitions)
        sealed_artifacts = None if artifacts is None else tuple(artifacts)
        if any(
            not isinstance(item, SchedulerTransition) for item in sealed_transitions
        ):
            raise TypeError(
                "finalization transitions must be SchedulerTransition values"
            )
        if sealed_artifacts is not None and any(
            not isinstance(item, SchedulerArtifact) for item in sealed_artifacts
        ):
            raise TypeError("finalization artifacts must be SchedulerArtifact values")
        if type(production_audit_required) is not bool:
            raise TypeError("production_audit_required must be boolean")
        if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
            raise TypeError("started_at must be a numeric wall-clock timestamp")

        def phase(value: ToolResult | None) -> _PhaseResultSnapshot | None:
            return None if value is None else _PhaseResultSnapshot.from_result(value)

        return cls(
            description=str(description),
            subagent_id=str(subagent_id),
            contract_mode=str(contract_mode),
            profile=profile,
            dispatch_plan=dispatch_plan,
            scheduler_outcome=scheduler_outcome,
            transitions=sealed_transitions,
            artifacts=sealed_artifacts,
            explore_results=tuple(
                _PhaseResultSnapshot.from_result(result) for result in explore_results
            ),
            plan_result=phase(plan_result),
            execute_result=phase(execute_result),
            review_result=phase(review_result),
            analyst_report_json=_optional_canonical_json(
                analyst_report,
                label="analyst report",
            ),
            execution_artifact_json=_optional_canonical_json(
                execution_artifact,
                label="execution artifact",
            ),
            verification_result_json=_optional_canonical_json(
                verification_result,
                label="verification result",
            ),
            typed_review_json=_canonical_json(
                typed_review,
                label="typed review",
            ),
            approval_request_id=approval_request_id,
            commit_ready_status=str(commit_ready_status),
            commit_error_text=(
                None if commit_error_text is None else str(commit_error_text)
            ),
            production_audit_required=production_audit_required,
            initial_finalization_status=initial_finalization_status,
            started_at=float(started_at),
        )

    @classmethod
    def capture_projection_failure(
        cls,
        *,
        description: str,
        subagent_id: str,
        contract_mode: str,
        profile: TaskProfile,
        dispatch_plan: DispatchPlan,
        scheduler_outcome: WorkflowExecutionOutcome,
        transitions: Iterable[SchedulerTransition],
        artifacts: Iterable[SchedulerArtifact] | None,
        approval_request_id: str | None,
        commit_ready_status: str,
        commit_error_text: str | None,
        production_audit_required: bool,
        started_at: float,
    ) -> _WorkflowFinalizationSnapshot:
        """Retain durable Scheduler facts when rich snapshotting fails.

        This path deliberately drops all Worker-authored documents and prose.
        The finalizer can still persist the authoritative trace/audit and must
        report a failed projection for an already committed change.
        """
        return cls(
            description=str(description),
            subagent_id=str(subagent_id),
            contract_mode=str(contract_mode),
            profile=profile,
            dispatch_plan=dispatch_plan,
            scheduler_outcome=scheduler_outcome,
            transitions=tuple(transitions),
            artifacts=None if artifacts is None else tuple(artifacts),
            explore_results=(),
            plan_result=None,
            execute_result=None,
            review_result=None,
            analyst_report_json=None,
            execution_artifact_json=None,
            verification_result_json=None,
            typed_review_json=_canonical_json(
                {
                    "reviewVersion": _WORKFLOW_REVIEW_VERSION,
                    "verdict": "inconclusive",
                    "blockingFindings": [
                        "error[snapshot_projection_failed]: rich finalization "
                        "facts were unavailable"
                    ],
                    "warnings": [],
                },
                label="typed review",
            ),
            approval_request_id=approval_request_id,
            commit_ready_status=str(commit_ready_status),
            commit_error_text=(
                None if commit_error_text is None else str(commit_error_text)
            ),
            production_audit_required=production_audit_required,
            initial_finalization_status="projection_failed",
            started_at=float(started_at),
        )


class _FinalizationDeadlineExceeded(RuntimeError):
    """A normalized host deadline observed after Scheduler execution."""


@dataclass(slots=True)
class _ProjectionState:
    """Ephemeral projection status; it carries no execution capability."""

    finalization_status: str = "completed"
    audit_status: str = "pending"
    scheduler_trace: dict[str, object] | None = None
    trace_projection_failed: bool = False


@dataclass(frozen=True, slots=True)
class _Documents:
    analyst_report: dict[str, object] | None
    execution_artifact: dict[str, object] | None
    verification_result: dict[str, object] | None
    typed_review: dict[str, object]


def _load_json_object(
    content: str | None,
    *,
    label: str,
    required: bool = False,
) -> dict[str, object] | None:
    if content is None:
        if required:
            raise ValueError(f"{label} is unavailable")
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _documents(snapshot: _WorkflowFinalizationSnapshot) -> _Documents:
    typed_review = _load_json_object(
        snapshot.typed_review_json,
        label="typed review",
        required=True,
    )
    if typed_review is None:
        raise ValueError("typed review is unavailable")
    return _Documents(
        analyst_report=_load_json_object(
            snapshot.analyst_report_json,
            label="analyst report",
        ),
        execution_artifact=_load_json_object(
            snapshot.execution_artifact_json,
            label="execution artifact",
        ),
        verification_result=_load_json_object(
            snapshot.verification_result_json,
            label="verification result",
        ),
        typed_review=typed_review,
    )


def _phase_results(
    snapshot: _WorkflowFinalizationSnapshot,
) -> tuple[_PhaseResultSnapshot, ...]:
    return (
        *snapshot.explore_results,
        *(
            result
            for result in (
                snapshot.plan_result,
                snapshot.execute_result,
                snapshot.review_result,
            )
            if result is not None
        ),
    )


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


def _workflow_result_counts(
    results: tuple[_PhaseResultSnapshot, ...],
) -> tuple[int, int]:
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


def _observe(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    *,
    result_contract_status: str,
    outcome: str,
    results: tuple[_PhaseResultSnapshot, ...],
    phase_count: int,
    result_truncated: bool,
) -> None:
    model_turns, tool_calls = _workflow_result_counts(results)
    observation = _WorkflowObservation(
        result_contract_status=result_contract_status,
        outcome=outcome,
        phase_count=phase_count,
        result_truncated=result_truncated,
        duration_ms=max(0, int((time.time() - snapshot.started_at) * 1000)),
        model_turns=model_turns,
        tool_calls=tool_calls,
    )
    try:
        ports.observe(observation)
    except Exception:  # noqa: BLE001,S110 - telemetry is explicitly optional
        pass


def _derive_workflow_result(
    *,
    subagent_id: str,
    workflow_status: str,
    execute_result: ToolResult,
    phase_results: list[ToolResult],
    typed_review: dict[str, object],
    analyst_report: dict[str, object] | None = None,
    execution_artifact: dict[str, object] | None = None,
    verification_result: dict[str, object] | None = None,
    structured_mode: bool = False,
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
    has_scheduler_artifacts = structured_mode or any(
        item is not None
        for item in (analyst_report, execution_artifact, verification_result)
    )
    if has_scheduler_artifacts:
        canonical_findings = (
            analyst_report.get("findings", [])
            if isinstance(analyst_report, dict)
            else []
        )
        finding_statements = [
            str(item.get("statement", "")).strip()
            for item in canonical_findings
            if isinstance(item, dict) and str(item.get("statement", "")).strip()
        ]
        change_set = (
            execution_artifact.get("changeSet")
            if isinstance(execution_artifact, dict)
            else None
        )
        changed_files = (
            change_set.get("files", []) if isinstance(change_set, dict) else []
        )
        if changed_files:
            summary = (
                f"Central scheduler {workflow_status} a frozen change set for "
                f"{len(changed_files)} file(s)."
            )
        elif finding_statements:
            summary = "; ".join(finding_statements)[:_WORKFLOW_RESULT_SUMMARY_MAX_CHARS]
        else:
            summary = f"Central scheduler ended the workflow as {workflow_status}."
    else:
        summary = (
            str(execute_report["summary"])[:_WORKFLOW_RESULT_SUMMARY_MAX_CHARS]
            if execute_report is not None
            else str(execute_result.output or "workflow completed")[
                :_WORKFLOW_RESULT_SUMMARY_MAX_CHARS
            ]
        )

    files: list[dict[str, str]] = []
    seen_files: set[tuple[str, str]] = set()
    risks: list[str] = []
    source_reports = [] if has_scheduler_artifacts else phase_reports
    for report in source_reports:
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
    if has_scheduler_artifacts:
        change_set = (
            execution_artifact.get("changeSet")
            if isinstance(execution_artifact, dict)
            else None
        )
        changed_files = (
            change_set.get("files", []) if isinstance(change_set, dict) else []
        )
        for item in changed_files:
            if not isinstance(item, dict):
                continue
            pair = (str(item.get("path", "")), str(item.get("action", "unknown")))
            if not pair[0] or pair in seen_files or len(files) >= 40:
                continue
            seen_files.add(pair)
            files.append({"path": pair[0][:500], "action": pair[1]})
        if isinstance(analyst_report, dict):
            if not files:
                for finding in analyst_report.get("findings", []):
                    if not isinstance(finding, dict):
                        continue
                    for evidence in finding.get("evidence", []):
                        if not isinstance(evidence, dict):
                            continue
                        pair = (str(evidence.get("path", "")), "read")
                        if not pair[0] or pair in seen_files or len(files) >= 40:
                            continue
                        seen_files.add(pair)
                        files.append({"path": pair[0][:500], "action": "read"})
            for unknown in analyst_report.get("unknowns", []):
                text = f"Unknown: {str(unknown).strip()}"[:500]
                if text != "Unknown: " and text not in risks and len(risks) < 20:
                    risks.append(text)
            for risk in analyst_report.get("risks", []):
                if not isinstance(risk, dict):
                    continue
                text = (
                    f"{risk.get('severity', 'unknown')!s}: "
                    f"{str(risk.get('statement', '')).strip()}"
                )[:500]
                if text and text not in risks and len(risks) < 20:
                    risks.append(text)
        if isinstance(execution_artifact, dict):
            for blocker in execution_artifact.get("blockers", []):
                text = f"Executor blocker: {str(blocker).strip()}"[:500]
                if (
                    text != "Executor blocker: "
                    and text not in risks
                    and len(risks) < 20
                ):
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
    deterministic_status = (
        verification_result.get("status")
        if isinstance(verification_result, dict)
        else None
    )
    verification_status = (
        str(deterministic_status)
        if deterministic_status in {"passed", "failed", "inconclusive"}
        else "not_run"
        if has_scheduler_artifacts and execution_artifact is None
        else "passed"
        if workflow_status == "completed" and verdict == "approved"
        else "failed"
        if verdict == "changes_required"
        else "inconclusive"
    )
    verification_checks = (
        [
            (
                f"{check.get('checkId', 'check')!s}: "
                f"{check.get('status', 'inconclusive')!s} "
                f"[{check.get('artifactRef', 'evidence unavailable')!s}]"
            )[:500]
            for check in verification_result.get("checks", [])[:20]
            if isinstance(check, dict)
        ]
        if isinstance(verification_result, dict)
        else []
        if has_scheduler_artifacts and execution_artifact is None
        else [f"workflow review verdict: {verdict}"]
    )
    report_content = json.dumps(
        {
            "resultVersion": 1,
            "summary": summary or "workflow completed without a summary",
            "files": files,
            "risks": risks,
            "verification": {
                "status": verification_status,
                "checks": verification_checks,
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


def _project_terminal_result(
    snapshot: _WorkflowFinalizationSnapshot,
    documents: _Documents,
    phase_results: tuple[_PhaseResultSnapshot, ...],
    source: _PhaseResultSnapshot | None,
    *,
    outcome: str,
    verification_status: str,
    checks: list[str],
) -> dict[str, object]:
    structured = snapshot.contract_mode == "v2"
    if structured:
        return _derive_workflow_result(
            subagent_id=snapshot.subagent_id,
            workflow_status=outcome,
            execute_result=(
                source.to_result()
                if source is not None
                else ToolResult(ok=False, output="scheduler result unavailable")
            ),
            phase_results=[result.to_result() for result in phase_results],
            typed_review=documents.typed_review,
            analyst_report=documents.analyst_report,
            execution_artifact=documents.execution_artifact,
            verification_result=documents.verification_result,
            structured_mode=True,
        )
    source_report = (
        extract_subagent_result(source.output) if source is not None else None
    )
    summary = (
        str(source_report["summary"])
        if source_report is not None
        else str(source.output)[:4000]
        if source is not None
        else "The central scheduler completed without dispatching a Worker."
    )
    report_content = json.dumps(
        {
            "resultVersion": 1,
            "summary": summary,
            "files": (
                list(source_report.get("files", []))
                if source_report is not None
                else []
            ),
            "risks": (
                list(source_report.get("risks", []))
                if source_report is not None
                else []
            ),
            "verification": {
                "status": verification_status,
                "checks": checks,
            },
        },
        ensure_ascii=False,
    )
    projected = project_subagent_result(
        report_content,
        subagent_id=snapshot.subagent_id,
        agent_type="workflow",
        outcome=outcome,
        fallback_summary=summary,
    )
    projected["contractStatus"] = "derived"
    return projected


def _check_finalization_deadline(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    state: _ProjectionState,
) -> bool:
    try:
        ports.checkpoint()
    except TurnCancellationRequested:
        if not snapshot.scheduler_outcome.committed:
            raise
        state.finalization_status = "cancelled_after_commit"
        return False
    except _FinalizationDeadlineExceeded:
        state.finalization_status = (
            "timed_out_after_commit"
            if snapshot.scheduler_outcome.committed
            else "timed_out"
        )
        return False
    return True


def _project_trace_authority(
    snapshot: _WorkflowFinalizationSnapshot,
    state: _ProjectionState,
) -> str:
    state.scheduler_trace = project_scheduler_trace(
        snapshot.dispatch_plan,
        snapshot.scheduler_outcome,
        list(snapshot.transitions),
    )
    return render_scheduler_trace(state.scheduler_trace)


def _project_trace_and_audit(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    state: _ProjectionState,
) -> str:
    try:
        scheduler_trace_text = _project_trace_authority(snapshot, state)
    except BaseException as exc:
        # A one-shot host interrupt after promotion must not skip the durable
        # audit. Retry only this bounded pure projection once.
        if not isinstance(exc, Exception) and snapshot.scheduler_outcome.committed:
            try:
                scheduler_trace_text = _project_trace_authority(snapshot, state)
            except BaseException:  # noqa: BLE001 - committed truth survives
                state.trace_projection_failed = True
        elif not isinstance(exc, Exception):
            raise
        else:
            state.trace_projection_failed = True
        if state.trace_projection_failed:
            state.finalization_status = "projection_failed"
            scheduler_trace_text = (
                "=== SCHEDULER TRACE UNAVAILABLE ===\nprojection_failed"
            )
    if state.scheduler_trace is None:
        state.trace_projection_failed = True
        state.finalization_status = "projection_failed"

    state.audit_status = (
        "projection_or_persistence_failed"
        if state.scheduler_trace is None
        else "pending"
    )
    if state.scheduler_trace is not None:
        try:
            if snapshot.artifacts is None:
                raise RuntimeError("scheduler artifact snapshot is inconsistent")
            scheduler_audit = project_scheduler_audit(
                profile=snapshot.profile,
                plan=snapshot.dispatch_plan,
                trace=state.scheduler_trace,
                artifacts=snapshot.artifacts,
                contract_mode=snapshot.contract_mode,
            )
            _check_finalization_deadline(snapshot, ports, state)
            if ports.persist_audit is None:
                state.audit_status = "not_configured"
            else:
                persisted = ports.persist_audit(scheduler_audit)
                state.audit_status = (
                    "persisted" if persisted is True else "persistence_failed"
                )
                # Cancellation/deadline is a status fact, never permission to
                # erase the bounded durable audit projection.
                _check_finalization_deadline(snapshot, ports, state)
        except TurnCancellationRequested:
            if not snapshot.scheduler_outcome.committed:
                raise
            state.finalization_status = "cancelled_after_commit"
            state.audit_status = "projection_or_persistence_failed"
        except BaseException as exc:
            if (
                not isinstance(exc, Exception)
                and not snapshot.scheduler_outcome.committed
            ):
                raise
            state.audit_status = "projection_or_persistence_failed"
    return scheduler_trace_text


def _compact_result(
    value: dict[str, object],
    *,
    audit_status: str,
) -> dict[str, object]:
    verification = value.get("verification")
    verification_status = (
        verification.get("status") if isinstance(verification, dict) else "inconclusive"
    )
    return {
        "resultVersion": value["resultVersion"],
        "subagentId": value["subagentId"],
        "agentType": value["agentType"],
        "outcome": value["outcome"],
        "contractStatus": "derived",
        "summary": (
            "Workflow result projection compacted; inspect the durable "
            "scheduler audit for complete evidence."
            if audit_status == "persisted"
            else "Workflow result projection compacted; final audit "
            "persistence could not be confirmed."
        ),
        "files": [],
        "risks": [],
        "verification": {
            "status": verification_status,
            "checks": [],
        },
    }


def _project_compact_trace(
    scheduler_trace: Mapping[str, object],
    count: int,
) -> dict[str, object]:
    projected = dict(scheduler_trace)
    raw_transitions = scheduler_trace.get("transitions")
    if not isinstance(raw_transitions, list):
        raise TypeError("scheduler trace transitions are unavailable")
    source = [dict(item) for item in raw_transitions if isinstance(item, dict)]
    if len(source) != len(raw_transitions) or not source:
        raise RuntimeError("scheduler trace transitions are invalid")
    if count < len(source):
        repair_indices = [
            index for index, item in enumerate(source)
            if item.get("outcome") == "repairing"
        ]
        if repair_indices:
            # The durable audit retains every failed revision. The bounded
            # parent-facing projection may show the final effective chain,
            # but must never splice old approvals into the revised patch.
            last_repair = repair_indices[-1]
            source = [
                item for index, item in enumerate(source)
                if index > last_repair
                or item.get("stage") in {"analyst", "human", "adjudicator"}
            ]
    required_indices = {len(source) - 1}
    completed_by_stage: dict[str, int] = {}
    dispatched_by_identity: dict[tuple[str, str, str, str], int] = {}
    completed_identities: list[tuple[str, str, str, str]] = []
    attempt_indices: dict[tuple[str, str, str, str], list[int]] = {}
    commit_requested_index: int | None = None
    for index, item in enumerate(source):
        stage = str(item["stage"])
        outcome = str(item["outcome"])
        identity = (
            stage,
            str(item["assignmentId"]),
            str(item["attemptId"]),
            str(item["agentRunId"]),
        )
        if outcome == "dispatched":
            dispatched_by_identity[identity] = index
        if stage not in {"parent", "commit"}:
            attempt_indices.setdefault(identity, []).append(index)
        if outcome == "completed":
            completed_by_stage[stage] = index
            if stage not in {"parent", "commit"}:
                completed_identities.append(identity)
        if stage == "commit" and outcome == "commit_requested":
            commit_requested_index = index
    required_indices.update(completed_by_stage.values())
    for identity in completed_identities:
        dispatch_index = dispatched_by_identity.get(identity)
        if dispatch_index is None:
            raise RuntimeError("completed scheduler stage has no dispatch")
        required_indices.add(dispatch_index)
    if commit_requested_index is not None:
        required_indices.add(commit_requested_index)
    elif bool(scheduler_trace.get("committed")):
        raise RuntimeError("committed scheduler trace has no commit request")
    for index in tuple(required_indices):
        item = source[index]
        stage = str(item["stage"])
        outcome = str(item["outcome"])
        if stage in {"parent", "commit"} or outcome == "dispatched":
            continue
        identity = (
            stage,
            str(item["assignmentId"]),
            str(item["attemptId"]),
            str(item["agentRunId"]),
        )
        dispatch_index = dispatched_by_identity.get(identity)
        if dispatch_index is not None:
            required_indices.add(dispatch_index)
        elif outcome not in {"timed_out", "waiting_human"}:
            raise RuntimeError("required scheduler terminal has no dispatch")

    target_count = max(count, len(required_indices))
    diagnostic_outcomes = {
        "blocked",
        "failed",
        "inconclusive",
        "rejected",
        "retrying",
        "timed_out",
        "waiting_human",
    }
    selected_indices = set(required_indices)
    optional_groups = sorted(
        (
            tuple(indices)
            for indices in attempt_indices.values()
            if not set(indices).issubset(required_indices)
        ),
        key=lambda indices: (
            any(
                str(source[index]["outcome"]) in diagnostic_outcomes
                for index in indices
            ),
            max(indices),
        ),
        reverse=True,
    )
    for indices in optional_groups:
        missing = set(indices) - selected_indices
        if len(selected_indices) + len(missing) > target_count:
            continue
        selected_indices.update(missing)
    selected = [dict(source[index]) for index in sorted(selected_indices)]
    parent_decision_id: str | None = None
    for sequence, transition in enumerate(selected, start=1):
        transition["sequence"] = sequence
        transition["parentDecisionId"] = parent_decision_id
        parent_decision_id = str(transition["decisionId"])
    projected["transitions"] = selected
    return projected


def _status_text(
    snapshot: _WorkflowFinalizationSnapshot,
    state: _ProjectionState,
    *,
    trace_projection: str,
    result_projection: str,
) -> str:
    status_document = {
        "finalizationVersion": 1,
        "changesCommitted": snapshot.scheduler_outcome.committed,
        "commitReadyStatus": snapshot.commit_ready_status,
        "finalizationStatus": state.finalization_status,
        "auditStatus": state.audit_status,
        "traceProjection": trace_projection,
        "resultProjection": result_projection,
    }
    return "=== WORKFLOW FINALIZATION v1 ===\n" + json.dumps(
        status_document,
        sort_keys=True,
    )


def _project_final_tool_result(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    state: _ProjectionState,
    result: ToolResult,
) -> ToolResult:
    _check_finalization_deadline(snapshot, ports, state)
    failed = (
        state.finalization_status != "completed"
        or snapshot.commit_ready_status
        in {"persistence_failed", "projection_failed", "recorder_missing"}
        or (
            snapshot.contract_mode == "v2"
            and snapshot.production_audit_required
            and state.audit_status == "not_configured"
        )
        or state.audit_status
        in {
            "persistence_failed",
            "projection_or_persistence_failed",
        }
    )
    prefix = ""
    if failed:
        prefix = (
            "error[workflow_finalization_incomplete]: "
            f"changesCommitted={str(snapshot.scheduler_outcome.committed).lower()}, "
            f"finalizationStatus={state.finalization_status}, "
            f"auditStatus={state.audit_status}\n\n"
        )

    projected_result = extract_subagent_result(result.output)
    marker_index = result.output.rfind("\n\n" + SCHEDULER_TRACE_MARKER)
    if marker_index < 0:
        marker_index = result.output.rfind("\n\n" + SUBAGENT_RESULT_MARKER)
    narrative = result.output[:marker_index] if marker_index >= 0 else result.output

    rendered_result = (
        render_subagent_result(projected_result)
        if projected_result is not None
        else None
    )
    result_projection = "full" if rendered_result is not None else "absent"
    selected_trace: str | None = None
    selected_status: str | None = None

    def select_trace() -> None:
        nonlocal selected_trace, selected_status
        if state.scheduler_trace is None or state.trace_projection_failed:
            candidate_status = _status_text(
                snapshot,
                state,
                trace_projection="failed",
                result_projection=result_projection,
            )
            authority_parts = [candidate_status]
            if rendered_result is not None:
                authority_parts.append(rendered_result)
            authority = prefix + "\n\n".join(authority_parts)
            if len(authority) <= _WORKFLOW_OUTPUT_MAX_CHARS:
                selected_trace = ""
                selected_status = candidate_status
            return
        raw_transitions = state.scheduler_trace.get("transitions")
        if not isinstance(raw_transitions, list):
            raise TypeError("scheduler trace transitions are unavailable")
        transition_count = len(raw_transitions)
        seen_projection_sizes: set[int] = set()
        for count in range(transition_count, 0, -1):
            trace_document = _project_compact_trace(state.scheduler_trace, count)
            projected_transitions = trace_document.get("transitions")
            if not isinstance(projected_transitions, list):
                raise TypeError("scheduler trace projection is invalid")
            projection_size = len(projected_transitions)
            if projection_size in seen_projection_sizes:
                continue
            seen_projection_sizes.add(projection_size)
            candidate_projection = (
                "full" if projection_size == transition_count else "compacted"
            )
            try:
                candidate_trace = render_scheduler_trace(trace_document)
            except ValueError:
                continue
            candidate_status = _status_text(
                snapshot,
                state,
                trace_projection=candidate_projection,
                result_projection=result_projection,
            )
            authority_parts = [candidate_status, candidate_trace]
            if rendered_result is not None:
                authority_parts.append(rendered_result)
            authority = prefix + "\n\n".join(authority_parts)
            if len(authority) <= _WORKFLOW_OUTPUT_MAX_CHARS:
                selected_trace = candidate_trace
                selected_status = candidate_status
                return

    select_trace()
    if selected_trace is None and projected_result is not None:
        rendered_result = render_subagent_result(
            _compact_result(projected_result, audit_status=state.audit_status)
        )
        result_projection = "compacted"
        select_trace()
    if selected_trace is None or selected_status is None:
        raise RuntimeError("workflow authority projection exceeds output cap")

    authority_parts = [selected_status]
    if selected_trace:
        authority_parts.append(selected_trace)
    if rendered_result is not None:
        authority_parts.append(rendered_result)
    authority_output = prefix + "\n\n".join(authority_parts)
    narrative_budget = _WORKFLOW_OUTPUT_MAX_CHARS - len(authority_output) - 2
    if narrative and narrative_budget > 0:
        if len(narrative) > narrative_budget:
            notice = "... [workflow narrative truncated]"
            narrative = (
                narrative[: max(0, narrative_budget - len(notice))] + notice
                if narrative_budget >= len(notice)
                else ""
            )
        finalized_output = (
            narrative + "\n\n" + authority_output if narrative else authority_output
        )
    else:
        finalized_output = authority_output
    return ToolResult(
        ok=result.ok and not failed,
        output=finalized_output,
        backgroundTask=result.backgroundTask,
        awaitUser=result.awaitUser,
        verification=result.verification if not failed else None,
        _must_deliver=snapshot.scheduler_outcome.committed,
        _continue_parent=(
            snapshot.scheduler_outcome.committed
            and result.ok
            and not failed
            and not result.awaitUser
        ),
    )


def _fallback_tool_result(
    snapshot: _WorkflowFinalizationSnapshot,
    state: _ProjectionState,
) -> ToolResult:
    fallback_status = {
        "finalizationVersion": 1,
        "changesCommitted": snapshot.scheduler_outcome.committed,
        "commitReadyStatus": snapshot.commit_ready_status,
        "finalizationStatus": "projection_failed",
        "auditStatus": state.audit_status,
        "traceProjection": "failed",
        "resultProjection": "failed",
    }
    output = (
        "error[workflow_finalization_incomplete]: "
        f"changesCommitted={str(snapshot.scheduler_outcome.committed).lower()}, "
        "finalizationStatus=projection_failed\n\n"
        "=== WORKFLOW FINALIZATION v1 ===\n"
        + json.dumps(fallback_status, sort_keys=True)
    )
    return ToolResult(
        ok=False,
        output=output[:_WORKFLOW_OUTPUT_MAX_CHARS],
        _must_deliver=snapshot.scheduler_outcome.committed,
    )


def _finalize_tool_result(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    state: _ProjectionState,
    result: ToolResult,
) -> ToolResult:
    try:
        return _project_final_tool_result(snapshot, ports, state, result)
    except TurnCancellationRequested:
        if not snapshot.scheduler_outcome.committed:
            raise
        state.finalization_status = "cancelled_after_commit"
        return _fallback_tool_result(snapshot, state)
    except BaseException as exc:
        if not isinstance(exc, Exception) and not snapshot.scheduler_outcome.committed:
            raise
        return _fallback_tool_result(snapshot, state)


def _render_terminal_result(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
    state: _ProjectionState,
    documents: _Documents,
    phase_results: tuple[_PhaseResultSnapshot, ...],
    scheduler_trace_text: str,
) -> ToolResult:
    outcome = snapshot.scheduler_outcome
    structured = snapshot.contract_mode == "v2"
    explore_results = snapshot.explore_results
    plan_result = snapshot.plan_result
    execute_result = snapshot.execute_result
    review_result = snapshot.review_result

    if outcome.status == "timed_out":
        failure_stage = (
            outcome.failure_stage.value
            if outcome.failure_stage is not None
            else "unknown"
        )
        timeout_contract_status = "unavailable"
        timeout_suffix = ""
        if structured:
            structured_result = _project_terminal_result(
                snapshot,
                documents,
                phase_results,
                execute_result or plan_result,
                outcome="failed",
                verification_status="inconclusive",
                checks=["global_deadline_exceeded"],
            )
            timeout_contract_status = "derived"
            timeout_suffix = "\n\n" + render_subagent_result(structured_result)
        _observe(
            snapshot,
            ports,
            result_contract_status=timeout_contract_status,
            outcome="failed",
            results=phase_results,
            phase_count=len(phase_results),
            result_truncated=False,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(
                ok=False,
                output=(
                    "error[sub_agent_deadline_exceeded]: central workflow "
                    f"timed out at stage {failure_stage}; no isolated changes "
                    "were promoted\n\n" + scheduler_trace_text + timeout_suffix
                ),
            ),
        )

    if outcome.status == "waiting_human":
        structured_result = _project_terminal_result(
            snapshot,
            documents,
            phase_results,
            plan_result,
            outcome="failed",
            verification_status="inconclusive",
            checks=[
                outcome.terminal_reason or "human approval required before execution"
            ],
        )
        human_reason = outcome.terminal_reason or "human_approval_required"
        analysis_text = (
            json.dumps(
                documents.analyst_report,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.analyst_report is not None
            else (plan_result.output if plan_result is not None else "")[:4000]
        )
        output = (
            f"[Workflow {snapshot.description} waiting_human]\n"
            "  Phases: Analyst -> Human gate\n\n"
            f"error[human_approval_required]: {human_reason}; execution was not "
            "started.\n"
            + (
                f"approvalRequestId: {snapshot.approval_request_id}\n"
                if snapshot.approval_request_id is not None
                else ""
            )
            + "\n=== ANALYSIS ===\n"
            f"{analysis_text[:4000]}\n\n"
            + scheduler_trace_text
            + "\n\n"
            + render_subagent_result(structured_result)
        )
        _observe(
            snapshot,
            ports,
            result_contract_status="derived",
            outcome="failed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + (1 if plan_result else 0),
            result_truncated=False,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(ok=False, output=output, awaitUser=True),
        )

    if snapshot.profile.kind.value == "read_only" and outcome.status == "completed":
        parent_only = DispatchStage.PARENT in outcome.completed_stages
        structured_result = _project_terminal_result(
            snapshot,
            documents,
            phase_results,
            plan_result,
            outcome="completed",
            verification_status="not_run",
            checks=[],
        )
        analysis_text = (
            json.dumps(
                documents.analyst_report,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.analyst_report is not None
            else plan_result.output
            if plan_result is not None
            else "Parent-only route selected; no Worker was dispatched."
        )
        output = (
            f"[Workflow {snapshot.description} completed]\n"
            f"  Phases: {'Parent' if parent_only else 'Analyst'}\n\n"
            "=== ANALYSIS ===\n"
            f"{analysis_text[:5000]}\n\n"
            + scheduler_trace_text
            + "\n\n"
            + render_subagent_result(structured_result)
        )
        _observe(
            snapshot,
            ports,
            result_contract_status="derived",
            outcome="completed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + (1 if plan_result else 0),
            result_truncated=False,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(ok=True, output=output),
        )

    if outcome.failure_stage is DispatchStage.ANALYST:
        failed_result = plan_result or _PhaseResultSnapshot(
            ok=False,
            output="plan unavailable",
        )
        failure_contract_status = "unavailable"
        failure_suffix = ""
        if structured:
            structured_result = _project_terminal_result(
                snapshot,
                documents,
                phase_results,
                failed_result,
                outcome="failed",
                verification_status="not_run",
                checks=[outcome.terminal_reason or "analysis failed"],
            )
            failure_contract_status = "derived"
            failure_suffix = "\n\n" + render_subagent_result(structured_result)
        failure_narrative = (
            json.dumps(
                documents.analyst_report,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.analyst_report is not None
            else (
                "Structured Analyst result was unavailable; see the Scheduler "
                f"reason code: {outcome.terminal_reason}."
                if structured
                else failed_result.output[:8000]
            )
        )
        _observe(
            snapshot,
            ports,
            result_contract_status=failure_contract_status,
            outcome="failed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + 1,
            result_truncated=len(failed_result.output) > 8000,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(
                ok=False,
                output=(
                    "[Workflow plan phase failed]\n\n"
                    + failure_narrative[:8000]
                    + "\n\n"
                    + scheduler_trace_text
                    + failure_suffix
                ),
            ),
        )

    if outcome.failure_stage is DispatchStage.EXECUTOR:
        failed_result = execute_result or _PhaseResultSnapshot(
            ok=False,
            output=outcome.terminal_reason or "execution unavailable",
        )
        failure_contract_status = "unavailable"
        failure_suffix = ""
        if structured:
            structured_result = _project_terminal_result(
                snapshot,
                documents,
                phase_results,
                failed_result,
                outcome="failed",
                verification_status="not_run",
                checks=[outcome.terminal_reason or "execution failed"],
            )
            failure_contract_status = "derived"
            failure_suffix = "\n\n" + render_subagent_result(structured_result)
        failure_narrative = (
            json.dumps(
                documents.execution_artifact,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.execution_artifact is not None
            else (
                "Structured Executor result was unavailable; see the Scheduler "
                f"reason code: {outcome.terminal_reason}."
                if structured
                else failed_result.output[:8000]
            )
        )
        _observe(
            snapshot,
            ports,
            result_contract_status=failure_contract_status,
            outcome="failed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + 2,
            result_truncated=len(failed_result.output) > 8000,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(
                ok=False,
                output=(
                    "[Workflow execute phase failed]\n\n"
                    + failure_narrative[:8000]
                    + "\n\n"
                    + scheduler_trace_text
                    + failure_suffix
                ),
            ),
        )

    if outcome.failure_stage is DispatchStage.VERIFIER:
        documents.typed_review.clear()
        documents.typed_review.update(
            _workflow_failure_review(outcome.terminal_reason or "verification_failed")
        )
        failed_execute = execute_result or _PhaseResultSnapshot(
            ok=False,
            output="execution result unavailable",
        )
        structured_result = _derive_workflow_result(
            subagent_id=snapshot.subagent_id,
            workflow_status="failed",
            execute_result=failed_execute.to_result(),
            phase_results=[result.to_result() for result in phase_results],
            typed_review=documents.typed_review,
            analyst_report=(documents.analyst_report if structured else None),
            execution_artifact=(documents.execution_artifact if structured else None),
            verification_result=(documents.verification_result if structured else None),
            structured_mode=structured,
        )
        verification_text = json.dumps(
            documents.verification_result
            or {
                "resultType": "VerificationResult.v1",
                "status": "inconclusive",
                "patchDigest": outcome.patch_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        execution_text = (
            json.dumps(
                documents.execution_artifact,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.execution_artifact is not None
            else failed_execute.output[:3000]
        )
        output = (
            f"[Workflow {snapshot.description} failed]\n"
            "  Phases: explore -> plan -> execute -> verifier\n\n"
            "=== DETERMINISTIC VERIFICATION ===\n"
            f"{verification_text}\n\n"
            "=== EXECUTE ===\n"
            f"{execution_text[:3000]}\n\n"
            + scheduler_trace_text
            + "\n\n"
            + render_subagent_result(structured_result)
        )
        _observe(
            snapshot,
            ports,
            result_contract_status="derived",
            outcome="failed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + 2,
            result_truncated=False,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(ok=False, output=output),
        )

    if DispatchStage.REVIEWER not in snapshot.dispatch_plan.stages:
        workflow_ok = outcome.status == "completed"
        structured_result = _project_terminal_result(
            snapshot,
            documents,
            phase_results,
            execute_result,
            outcome="completed" if workflow_ok else "failed",
            verification_status="passed" if workflow_ok else "inconclusive",
            checks=[
                "deterministic verification: "
                + str(
                    (documents.verification_result or {}).get(
                        "verificationId",
                        outcome.terminal_reason or "unavailable",
                    )
                )
            ],
        )
        verification_text = json.dumps(
            documents.verification_result or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        execution_text = (
            json.dumps(
                documents.execution_artifact,
                ensure_ascii=False,
                sort_keys=True,
            )
            if structured and documents.execution_artifact is not None
            else (execute_result.output if execute_result else "")[:4000]
        )
        output = (
            f"[Workflow {snapshot.description} "
            f"{'completed' if workflow_ok else 'failed'}]\n"
            "  Phases: Executor -> Verifier\n\n"
            "=== DETERMINISTIC VERIFICATION ===\n"
            f"{verification_text}\n\n"
            "=== EXECUTE ===\n"
            f"{execution_text[:4000]}\n\n"
            + scheduler_trace_text
            + "\n\n"
            + render_subagent_result(structured_result)
        )
        _observe(
            snapshot,
            ports,
            result_contract_status="derived",
            outcome="completed" if workflow_ok else "failed",
            results=phase_results,
            phase_count=(1 if explore_results else 0) + (1 if execute_result else 0),
            result_truncated=False,
        )
        return _finalize_tool_result(
            snapshot,
            ports,
            state,
            ToolResult(ok=workflow_ok, output=output),
        )

    plan_result = plan_result or _PhaseResultSnapshot(
        ok=False,
        output="analysis result unavailable",
    )
    execute_result = execute_result or _PhaseResultSnapshot(
        ok=False,
        output="execution result unavailable",
    )
    review_result = review_result or _PhaseResultSnapshot(
        ok=False,
        output="review result unavailable",
    )
    typed_review = documents.typed_review
    if outcome.status == "failed" and outcome.failure_stage is DispatchStage.REVIEWER:
        review_failure = outcome.terminal_reason or "review_gate_rejected"
        scheduler_finding = (
            f"error[{review_failure}]: the Scheduler rejected the review gate; "
            "no isolated changes were promoted"
        )
        if typed_review.get("verdict") == "approved":
            typed_review.clear()
            typed_review.update(_workflow_failure_review(review_failure))
            typed_review["blockingFindings"] = [scheduler_finding]
        else:
            existing_findings = typed_review.get("blockingFindings", [])
            typed_review["blockingFindings"] = [
                *(
                    list(existing_findings)
                    if isinstance(existing_findings, list)
                    else []
                ),
                scheduler_finding,
            ]
    elif snapshot.commit_error_text is not None:
        typed_review.clear()
        typed_review.update(_workflow_failure_review("workflow_commit_failed"))
        typed_review["blockingFindings"] = [
            (
                "error[workflow_commit_failed]: approved isolated changes were "
                "not applied to the parent workspace "
                f"({snapshot.commit_error_text})"
            )
        ]

    duration_ms = max(0, int((time.time() - snapshot.started_at) * 1000))
    workflow_ok = bool(outcome.status == "completed")
    workflow_status = "completed" if workflow_ok else "failed"
    header = (
        f"[Workflow {snapshot.description} {workflow_status}]\n"
        "  Phases: explore -> plan -> execute -> review\n"
        f"  Duration: {duration_ms / 1000:.1f}s"
    )
    review_authority = json.dumps(
        typed_review,
        ensure_ascii=False,
        sort_keys=True,
    )
    research_text = "\n\n".join(result.output for result in explore_results)
    if structured:
        section_specs = (
            ("=== REVIEW VERDICT ===", review_authority, 3000),
            (
                "=== DETERMINISTIC VERIFICATION ===",
                json.dumps(
                    documents.verification_result or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                3000,
            ),
            (
                "=== EXECUTION RESULT ===",
                json.dumps(
                    documents.execution_artifact or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                3000,
            ),
            (
                "=== ANALYST REPORT ===",
                json.dumps(
                    documents.analyst_report or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                2000,
            ),
        )
    else:
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
        output = output[: _WORKFLOW_OUTPUT_MAX_CHARS - len(suffix)] + suffix

    all_phase_results = (
        *explore_results,
        plan_result,
        execute_result,
        review_result,
    )
    structured_result = _derive_workflow_result(
        subagent_id=snapshot.subagent_id,
        workflow_status=workflow_status,
        execute_result=execute_result.to_result(),
        phase_results=[result.to_result() for result in all_phase_results],
        typed_review=typed_review,
        analyst_report=documents.analyst_report if structured else None,
        execution_artifact=documents.execution_artifact if structured else None,
        verification_result=documents.verification_result if structured else None,
        structured_mode=structured,
    )
    artifact_suffix = (
        "\n\n"
        + scheduler_trace_text
        + "\n\n"
        + render_subagent_result(structured_result)
    )
    if len(output) + len(artifact_suffix) > _WORKFLOW_OUTPUT_MAX_CHARS:
        result_truncated = True
        truncation_notice = "\n\n... [workflow output truncated]"
        narrative_limit = max(
            0,
            _WORKFLOW_OUTPUT_MAX_CHARS - len(artifact_suffix) - len(truncation_notice),
        )
        output = output[:narrative_limit] + truncation_notice
    output += artifact_suffix

    _observe(
        snapshot,
        ports,
        result_contract_status="derived",
        outcome=workflow_status,
        results=all_phase_results,
        phase_count=(1 if explore_results else 0) + 3,
        result_truncated=result_truncated,
    )
    return _finalize_tool_result(
        snapshot,
        ports,
        state,
        ToolResult(
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
        ),
    )


def finalize_workflow(
    snapshot: _WorkflowFinalizationSnapshot,
    ports: _WorkflowFinalizationPorts,
) -> ToolResult:
    """Render one terminal result without retaining any execution capability."""
    if not isinstance(snapshot, _WorkflowFinalizationSnapshot):
        raise TypeError("snapshot must be a workflow finalization snapshot")
    if not isinstance(ports, _WorkflowFinalizationPorts):
        raise TypeError("ports must be workflow finalization ports")

    state = _ProjectionState(
        finalization_status=snapshot.initial_finalization_status,
    )
    try:
        scheduler_trace_text = _project_trace_and_audit(snapshot, ports, state)
        documents = _documents(snapshot)
        return _render_terminal_result(
            snapshot,
            ports,
            state,
            documents,
            _phase_results(snapshot),
            scheduler_trace_text,
        )
    except BaseException:
        if not snapshot.scheduler_outcome.committed:
            raise
        return _fallback_tool_result(snapshot, state)
