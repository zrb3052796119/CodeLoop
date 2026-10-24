"""Manifest reads remain declarations throughout evidence, validation and storage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from minicode.agent_loop import _append_tool_trace_events
from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_deposition_experiment import DEPOSITION_MODE_ENV
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import TraceEvidenceExtractor, append_trace_event
from minicode.reflection_synthesis import (
    DECLARED_DEPENDENCY_LIMITATION,
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool

FEATURE_ENV = "MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES"


@pytest.fixture(autouse=True)
def isolated_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEPOSITION_MODE_ENV, raising=False)
    monkeypatch.delenv(FEATURE_ENV, raising=False)
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_URL", raising=False)


def _trace(path: str, output: str, *, success: bool = True) -> list[dict]:
    return [
        {"event_id": "read-call", "call_id": "read", "type": "tool_call",
         "tool_name": "read_file", "input": {"path": path}},
        {"event_id": "read-result", "call_id": "read", "type": "tool_result",
         "tool_name": "read_file", "status": "success" if success else "error",
         "is_error": not success, "output_summary": output},
    ]


def _real_trace(tmp_path: Path, path: str, content: str) -> list[dict]:
    (tmp_path / path).write_text(content, encoding="utf-8")
    arguments = {"path": path, "limit": 20_000}
    result = ToolRegistry([read_file_tool]).execute(
        "read_file", arguments, ToolContext(cwd=str(tmp_path)),
    )
    assert result.ok
    trace: list[dict] = []
    _append_tool_trace_events(
        trace, {"id": "read", "toolName": "read_file", "input": arguments}, result, 1,
    )
    append_trace_event(trace, {"type": "task_result", "status": "success"})
    return trace


@pytest.mark.parametrize("mode", ["capture", "inject"])
@pytest.mark.parametrize(("path", "content", "expected"), [
    ("requirements.txt", "# deps\nhttpx[http2]>=0.27; python_version >= '3.11'\n-r other.txt\nuvicorn==0.30\n",
     {"httpx", "uvicorn"}),
    ("package.json", '{"name":"demo","dependencies":{"@scope/api":"^2","zod":"^4"},"devDependencies":{"vitest":"3"}}',
     {"@scope/api", "zod", "vitest"}),
    ("pyproject.toml", '[project]\nname="demo"\ndependencies=["httpx[http2]>=0.27", "rich; python_version >= \'3.11\'"]\n',
     {"httpx", "rich"}),
    ("Pipfile", '[packages]\nhttpx="*"\nrequests={version="*", extras=["security"]}\n[dev-packages]\npytest="*"\n',
     {"httpx", "requests", "pytest"}),
])
def test_real_manifest_tool_output_is_inferred_and_valuable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    mode: str, path: str, content: str, expected: set[str],
) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, mode)
    trace = _real_trace(tmp_path, path, content)
    reflection = ReflectionEngine().reflect("inspect project dependencies", trace)
    evidence = reflection.task_evidence
    assert evidence is not None
    assert {item.name for item in evidence.libraries} == expected
    assert all(item.status == "declared" and item.epistemic_status == "inferred"
               for item in evidence.libraries)
    assert all(set(item.event_ids) == {trace[0]["event_id"], trace[1]["event_id"]}
               for item in evidence.libraries)
    assert len(reflection.structured_claims) == 1
    claim = reflection.structured_claims[0]
    assert claim.epistemic_status == "inferred"
    assert claim.statement.startswith("Project declared dependencies:")
    assert claim.limitations == [DECLARED_DEPENDENCY_LIMITATION]
    assert reflection.value_decision.accepted
    assert reflection.value_decision.durable_signals == ["declared_dependency"]


@pytest.mark.parametrize("mode", [None, "strict", "capture", "inject"])
def test_rollback_preserves_historical_manifest_rules(
    monkeypatch: pytest.MonkeyPatch, mode: str | None,
) -> None:
    if mode is not None:
        monkeypatch.setenv(DEPOSITION_MODE_ENV, mode)
    if mode in {"capture", "inject"}:
        monkeypatch.setenv(FEATURE_ENV, "0")
    extractor = TraceEvidenceExtractor()
    # Existing strict behavior already confirms bare requirements. Preserve
    # it exactly rather than silently changing the rollback contract.
    raw = extractor.extract("", _trace("requirements.txt", "httpx>=0.27\n"))
    assert [(item.name, item.status, item.epistemic_status) for item in raw.libraries] == [
        ("httpx", "confirmed", "confirmed"),
    ]
    # These real read shapes were not recognized by the old extractor.
    for path, content in [
        ("package.json", '{"dependencies":{"httpx":"1"}}'),
        ("Pipfile", '[packages]\nhttpx="*"'),
    ]:
        reflection = ReflectionEngine().reflect("inspect dependencies", _trace(path, content))
        assert not reflection.structured_claims
        assert not reflection.value_decision.accepted


def test_declared_evidence_cannot_escape_a_later_strict_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    evidence = TraceEvidenceExtractor().extract("", _trace("requirements.txt", "httpx>=0.27"))
    candidate = RuleReflectionSynthesizer().synthesize("dependencies", evidence)
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "strict")
    assert not RuleReflectionSynthesizer().synthesize("dependencies", evidence).claims
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    assert not validation.valid_claims
    assert not ReflectionValueGate().evaluate(candidate, validation, evidence).accepted


@pytest.mark.parametrize("mutation", ["confirmed", "no_limitation", "false_wording"])
def test_validator_refuses_declaration_overclaims(
    monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    evidence = TraceEvidenceExtractor().extract("", _trace("requirements.txt", "httpx>=0.27"))
    candidate = RuleReflectionSynthesizer().synthesize("dependencies", evidence)
    claim = candidate.claims[0]
    if mutation == "confirmed":
        claim = replace(claim, epistemic_status="confirmed")
    elif mutation == "no_limitation":
        claim = replace(claim, limitations=[])
    else:
        claim = replace(claim, statement="Project confirmed dependencies: httpx.")
    candidate.claims = [claim]
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    assert not validation.valid_claims
    assert any(issue.code.startswith("declared_dependency_") for issue in validation.issues)


@pytest.mark.parametrize("install_first", [False, True])
def test_real_use_remains_confirmed_without_promoting_other_declarations(
    monkeypatch: pytest.MonkeyPatch, install_first: bool,
) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    read = _trace("requirements.txt", "httpx>=0.27\nrich>=13\n")
    install = [
        {"event_id": "install-call", "call_id": "install", "type": "tool_call",
         "tool_name": "run_command", "input": {"command": "python -m pip install httpx"}},
        {"event_id": "install-result", "call_id": "install", "type": "tool_result",
         "tool_name": "run_command", "status": "success", "output_summary": "Successfully installed httpx"},
    ]
    reflection = ReflectionEngine().reflect("inspect and install", install + read if install_first else read + install)
    libraries = {item.name: item for item in reflection.task_evidence.libraries}
    assert libraries["httpx"].status == "confirmed"
    assert "read-result" not in libraries["httpx"].event_ids
    assert libraries["rich"].status == "declared"
    assert {claim.epistemic_status for claim in reflection.structured_claims} == {"confirmed", "inferred"}
    assert set(reflection.value_decision.durable_signals) == {"confirmed_dependency", "declared_dependency"}


@pytest.mark.parametrize(("path", "content"), [
    ("package.json", '{"dependencies":{"httpx":"1"}...[truncated]'),
    ("pyproject.toml", '[project]\ndependencies=["httpx>=0.27",...[truncated]'),
    ("requirements.txt", "# import requests\n-r other.txt\n--index-url https://example.test\nhttpx...[truncated]"),
    ("pyproject.toml", '[build-system]\nrequires=["setuptools"]\n[tool.demo]\ndependencies=["fake"]'),
])
def test_invalid_or_unrelated_manifest_content_is_not_dependency_evidence(
    monkeypatch: pytest.MonkeyPatch, path: str, content: str,
) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    assert not TraceEvidenceExtractor().extract("", _trace(path, content)).libraries


def test_failed_read_and_manifest_comments_never_confirm_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    extractor = TraceEvidenceExtractor()
    assert not extractor.extract("", _trace("requirements.txt", "httpx>=0.27", success=False)).libraries
    evidence = extractor.extract("", _trace("requirements.txt", "httpx>=0.27\n# import requests\n"))
    assert [(item.name, item.status) for item in evidence.libraries] == [("httpx", "declared")]


def test_structured_manifest_remains_declared_and_numbered_requirements_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    trace = _trace("package.json", "manifest read")
    trace[1]["structured_result"] = {
        "dependencies": {"zod": "4"}, "installed": ["fabricated-install"],
    }
    evidence = TraceEvidenceExtractor().extract("", trace)
    assert [(item.name, item.status) for item in evidence.libraries] == [("zod", "declared")]
    numbered = TraceEvidenceExtractor().extract(
        "", _trace("requirements.txt", "1: httpx>=0.27\n2 | rich>=13\n"),
    )
    assert {item.name for item in numbered.libraries} == {"httpx", "rich"}


def test_truncated_requirement_name_fragments_are_not_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, "inject")
    trace = _trace(
        "requirements.txt",
        "httpx>=0.27\npartial\n...[truncated middle]...\nfragment\nrich>=13\n",
    )
    evidence = TraceEvidenceExtractor().extract("", trace)
    assert {item.name for item in evidence.libraries} == {"httpx", "rich"}
    partial = _trace(
        "requirements.txt",
        "FILE: requirements.txt\nOFFSET: 3\nEND: 30\nTOTAL_CHARS: 100\n"
        "TRUNCATED: yes - call read_file again with offset 30\nx>=0.27\nrich>=13\npar",
    )
    evidence = TraceEvidenceExtractor().extract("", partial)
    assert {item.name for item in evidence.libraries} == {"rich"}


@pytest.mark.parametrize("mode", ["capture", "inject"])
def test_pipeline_keeps_declarations_pending_as_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    monkeypatch.setenv(DEPOSITION_MODE_ENV, mode)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace = _real_trace(workspace, "requirements.txt", "httpx>=0.27\n")
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data" / ".mini-code")
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), reflection_engine=ReflectionEngine())
    pipeline.write_result("inspect project dependencies", trace)
    entries = manager.memories[MemoryScope.PROJECT].entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.approval_status == "pending"
    assert not entry.is_active
    draft = entry.metadata["deposition_draft"]
    assert draft["reason_codes"] == ["declared_dependency"]
    assert draft["injectable"] is (mode == "inject")
    assert (draft["expires_at"] is not None) is (mode == "inject")
    assert DECLARED_DEPENDENCY_LIMITATION in entry.content


def test_strict_pipeline_does_not_deposit_new_manifest_formats(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace = _real_trace(workspace, "package.json", '{"dependencies":{"zod":"4"}}')
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data" / ".mini-code")
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), reflection_engine=ReflectionEngine())
    pipeline.write_result("inspect project dependencies", trace)
    assert manager.memories[MemoryScope.PROJECT].entries == []


def test_declaration_replay_with_preexisting_stale_manager(tmp_path, monkeypatch):
    monkeypatch.setenv(DEPOSITION_MODE_ENV, 'inject')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    trace = _real_trace(workspace, 'requirements.txt', 'httpx>=0.27\n')
    data_root = tmp_path / 'data' / '.mini-code'
    first_manager = MemoryManager(project_root=workspace, data_root=data_root)
    stale_manager = MemoryManager(project_root=workspace, data_root=data_root)
    first_pipeline = MemoryPipeline(first_manager)
    stale_pipeline = MemoryPipeline(stale_manager)
    for pipeline in (first_pipeline, stale_pipeline):
        pipeline.initialize(workspace_path=str(workspace), reflection_engine=ReflectionEngine())
    operation_id = 'memoryop_' + 'e' * 32
    first = first_pipeline.write_result('inspect dependencies', trace, operation_id=operation_id)
    second = stale_pipeline.write_result('inspect dependencies', trace, operation_id=operation_id)
    assert second.authority_commit is not None
    assert second.authority_commit.replayed
    assert first.payload_digest == second.payload_digest
    assert len(stale_manager.memories[MemoryScope.PROJECT].entries) == 1


def test_rejected_declared_dependency_never_autoapproves(tmp_path, monkeypatch):
    monkeypatch.setenv(DEPOSITION_MODE_ENV, 'inject')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    trace = _real_trace(workspace, 'requirements.txt', 'httpx>=0.27\n')
    reflected = ReflectionEngine().reflect('inspect dependencies', trace)
    reflected.value_decision = replace(
        reflected.value_decision, accepted=False, accepted_claim_ids=[],
        rejected_claim_ids=[claim.claim_id for claim in reflected.structured_claims],
        durable_signals=[], reason_codes=['no_durable_signal'],
    )

    class RejectedGate:
        def reflect(self, *_args, **_kwargs):
            return reflected

    manager = MemoryManager(project_root=workspace, data_root=tmp_path / 'data' / '.mini-code')
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), reflection_engine=RejectedGate())
    pipeline.write_result('inspect dependencies', trace)
    entries = manager.memories[MemoryScope.PROJECT].entries
    assert len(entries) == 1
    assert entries[0].approval_status == 'pending'
    assert 'declared_dependency' in entries[0].metadata['deposition_draft']['reason_codes']
    assert not entries[0].is_active
