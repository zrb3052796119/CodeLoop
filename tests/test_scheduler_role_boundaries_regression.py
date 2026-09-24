"""Exercise real Worker capability setup and frozen adjudication inputs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.tools.task as task_module
from minicode.permissions import PermissionDeniedError
from minicode.scheduler_artifacts import SchedulerArtifactError
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolResult


def _capture_worker(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def run_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "fixture completed"}]

    def ordinary_prompt(**kwargs):
        captured["ordinary_prompt_used"] = True
        return kwargs["agent_def"]["system_prompt"], None

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    monkeypatch.setattr(task_module, "_build_sub_agent_system_prompt", ordinary_prompt)
    monkeypatch.setattr(
        "minicode.subagent_model_routing.create_subagent_model_adapter",
        lambda *args, **kwargs: SimpleNamespace(runtime={"model": "fake"}),
    )
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *args, **kwargs: None,
    )
    return captured


@pytest.mark.parametrize("role", ["analyst", "reviewer", "adjudicator"])
def test_every_scheduler_reader_enforces_assignment_and_input_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    tmp_path = tmp_path.resolve()
    (tmp_path / "allowed").mkdir()
    captured = _capture_worker(monkeypatch)
    result = task_module._run(
        {"agent_type": "plan", "description": role, "prompt": "Read frozen inputs."},
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _workflow_phase=True,
            _logical_agent_role=role,
            _logical_agent_scope={
                "read_roots": ("allowed",),
                "write_paths": (),
                "forbidden_paths": ("allowed/private.txt",),
            },
            _logical_agent_workspace_root=str(tmp_path),
        ),
    )

    assert result.ok, result.output
    permissions = captured["permissions"]
    permissions.ensure_path_access(str(tmp_path / "allowed/public.txt"), "read")
    for relative in ("outside.txt", "allowed/private.txt", "allowed/.env"):
        with pytest.raises(PermissionDeniedError):
            permissions.ensure_path_access(str(tmp_path / relative), "read")
    with pytest.raises(PermissionDeniedError):
        permissions.ensure_edit(str(tmp_path / "allowed/public.txt"), "patch")
    with pytest.raises(PermissionDeniedError):
        permissions.ensure_command("python", ["-V"], str(tmp_path / "allowed"))
    assert not captured.get("ordinary_prompt_used", False)
    assert captured["memory_manager"] is None
    assert "load_skill" not in captured["tools"].list_all()


@pytest.mark.parametrize(
    ("role", "agent_type"),
    [
        ("unknown", "plan"),
        ("", "plan"),
        ([], "plan"),
        ("adjudicator", "explore"),
        ("executor", "plan"),
        ("analyst", "general"),
        ("reviewer", "workflow"),
    ],
)
def test_invalid_role_type_pairs_fail_before_worker_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: object,
    agent_type: str,
) -> None:
    captured = _capture_worker(monkeypatch)
    # No scope is intentionally supplied: invalid pairs must never fall back
    # to the ordinary Plan/General permissions or start a workflow.
    result = task_module._run(
        {"agent_type": agent_type, "description": "invalid", "prompt": "fixture"},
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _workflow_phase=True,
            _logical_agent_role=role,
        ),
    )
    assert not result.ok
    assert "scheduler_role_invalid" in result.output
    assert captured == {}


def test_ordinary_plan_remains_available_without_scheduler_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_worker(monkeypatch)
    result = task_module._run(
        {"agent_type": "plan", "description": "plan", "prompt": "Read local code."},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _workflow_phase=True),
    )
    assert result.ok, result.output
    assert captured["ordinary_prompt_used"] is True
    assert "load_skill" in captured["tools"].list_all()


def test_adjudicator_can_read_frozen_evidence_but_not_unassigned_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the existing structured request/report builders, while retaining
    # the real scheduler, runner, immutable Store and Assignment channel.
    spec = importlib.util.spec_from_file_location(
        "scheduler_role_routing_fixtures",
        Path(__file__).with_name("test_workflow_v2_structured_routing.py"),
    )
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    evidence_reads: list[tuple[str, str]] = []
    handoffs: list[dict] = []
    evidence_errors: list[str] = []
    roles: list[str] = []

    def worker(input_data: dict, context: ToolContext) -> ToolResult:
        role = fixture._worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            fixture._write_analyst_result(
                input_data,
                context,
                prepare_change=True,
                risks=[{
                    "severity": "high",
                    "statement": "Fixture requires risk adjudication.",
                    "evidenceRefs": ["claim_route"],
                }],
            )
            return ToolResult(ok=True, output="analysis found a risk")
        assert role == "adjudicator"
        package = fixture._prompt_json(input_data["prompt"], "CONFLICT PACKAGE")
        assignment = fixture._prompt_json(input_data["prompt"], "ASSIGNMENT")
        for claim in package["claims"]:
            for ref in claim["evidenceRefs"]:
                try:
                    artifact = context._subagent_mailbox.read(ref)
                    assert artifact is not None
                    document = json.loads(artifact.content)
                    evidence_reads.append((ref, document["resultType"]))
                    assert ref in assignment["inputRefs"]
                except SchedulerArtifactError as error:
                    evidence_errors.append(str(error))
        for ref in assignment["inputRefs"]:
            if ref.startswith("artifact_"):
                artifact = context._subagent_mailbox.read(ref)
                assert artifact is not None
                document = json.loads(artifact.content)
                if document.get("resultType") == "AnalystHandoff.v1":
                    handoffs.append(document)
        with pytest.raises(SchedulerArtifactError, match="not readable"):
            context._subagent_mailbox.read("artifact_unassigned")
        fixture._write_adjudication_result(input_data, context, decision="insufficient")
        return ToolResult(ok=True, output="fixture stops after evidence inspection")

    target = tmp_path / "allowed.txt"
    target.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(task_module, "_run", worker)
    result = fixture._run(
        tmp_path, fixture._request(risk="medium", complex=True), monkeypatch,
        commit_audit_waived=True,
    )
    assert roles == ["analyst", "adjudicator"]
    assert not result.ok
    assert target.read_text(encoding="utf-8") == "before\n"
    assert not evidence_errors
    assert {kind for _, kind in evidence_reads} == {
        "EvidenceSnapshot.v1", "WorkflowRequestArtifact.v1"
    }
    assert len(handoffs) == 1
    assert handoffs[0]["report"]["risks"][0]["severity"] == "high"
