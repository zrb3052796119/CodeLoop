"""Outstanding Adjudicator conditions must prevent execution and audit authority."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.scheduler_artifacts import SchedulerArtifact
from minicode.scheduler_audit import (
    SchedulerAuditValidationError,
    project_scheduler_audit,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolResult


@pytest.fixture
def routing():
    spec = importlib.util.spec_from_file_location(
        "conditional_adjudication_fixtures",
        Path(__file__).with_name("test_workflow_v2_structured_routing.py"),
    )
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture


def _run_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routing,
    *,
    decision: str,
    conditions: list[str],
):
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = routing._worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            routing._write_analyst_result(
                input_data,
                context,
                prepare_change=True,
                risks=[{
                    "severity": "high",
                    "statement": "This medium-risk route needs adjudication.",
                    "evidenceRefs": ["claim_route"],
                }],
            )
        elif role == "adjudicator":
            assignment = routing._prompt_json(input_data["prompt"], "ASSIGNMENT")
            package = routing._prompt_json(input_data["prompt"], "CONFLICT PACKAGE")
            context._subagent_mailbox.write(
                input_data["_result_key"],
                json.dumps({
                    "resultVersion": 1,
                    "assignmentId": assignment["assignmentId"],
                    "conflictId": package["conflictId"],
                    "decision": decision,
                    "resolvedClaims": [{
                        "claimId": claim["claimId"],
                        "resolution": "Resolved using the frozen source evidence.",
                        "evidenceRefs": claim["evidenceRefs"][:1],
                    } for claim in package["claims"]],
                    "conditions": conditions,
                    "confidence": "high",
                }),
                author="adjudicator",
            )
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            routing._write_executor_declaration(input_data, context)
        else:
            assert role == "reviewer"
            routing._write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        roles.append("verifier")
        return routing._verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)
    result = routing._run(
        tmp_path,
        routing._request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        # Use the same explicit host audit waiver as the existing workflow
        # fixture; the real audit projection and parent promotion still run.
        commit_audit_waived=True,
    )
    return result, roles, audits


def _replay(audit, *, artifacts=None):
    return project_scheduler_audit(
        profile=audit.profile,
        plan=audit.plan,
        trace=audit.trace,
        artifacts=audit.artifacts if artifacts is None else artifacts,
        contract_mode="v2",
    )


def test_conditional_continuation_stops_before_parent_writes_and_keeps_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, routing,
) -> None:
    conditions = ["Obtain explicit human approval before any file changes."]
    result, roles, audits = _run_conflict(
        tmp_path, monkeypatch, routing, decision="select_b", conditions=conditions,
    )

    assert not result.ok
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert roles == ["analyst", "adjudicator"]
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["committed"] is False
    assert trace["terminalReason"] == "adjudication_conditions_unresolved"
    assert len(audits) == 1
    verdicts = [
        document
        for artifact in audits[0].artifacts
        if (document := json.loads(artifact.content)).get("resultType")
        == "AdjudicationVerdict.v1"
    ]
    assert len(verdicts) == 1
    assert verdicts[0]["conditions"] == conditions
    assert _replay(audits[0]).trace == trace


def test_unconditional_continuation_commits_but_conditional_audit_cannot_authorize_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, routing,
) -> None:
    result, roles, audits = _run_conflict(
        tmp_path, monkeypatch, routing, decision="select_b", conditions=[],
    )
    assert result.ok, result.output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    assert roles == ["analyst", "adjudicator", "executor", "verifier", "reviewer"]
    assert len(audits) == 1
    assert _replay(audits[0]).trace["committed"] is True

    conditional_artifacts = []
    for artifact in audits[0].artifacts:
        document = json.loads(artifact.content)
        if document.get("resultType") == "AdjudicationVerdict.v1":
            document["conditions"] = ["Obtain human approval before writing."]
            artifact = SchedulerArtifact(
                artifact.artifact_id,
                json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        conditional_artifacts.append(artifact)
    with pytest.raises(
        SchedulerAuditValidationError,
        match="completed Analyst did not pass its gates",
    ):
        _replay(audits[0], artifacts=tuple(conditional_artifacts))


@pytest.mark.parametrize(("decision", "reason"), [
    ("select_a", "adjudicated_reclassification_required"),
    ("synthesize", "adjudication_decision_not_binding"),
    ("insufficient", "adjudication_inconclusive"),
    ("human_required", "adjudication_human_required"),
])
def test_other_decisions_remain_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, routing,
    decision: str, reason: str,
) -> None:
    result, roles, audits = _run_conflict(
        tmp_path, monkeypatch, routing, decision=decision, conditions=[],
    )
    assert not result.ok
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert roles == ["analyst", "adjudicator"]
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["terminalReason"] == reason
    assert len(audits) == 1
    assert _replay(audits[0]).trace["committed"] is False
