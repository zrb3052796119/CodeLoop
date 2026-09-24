"""Offline review probes. Real role setup/channels; model calls are stubbed.

Run from the repository root with `python artifacts/multi-agent-review-2026-09-08/reproduce_role_boundaries.py`.
All workspace mutation occurs in temporary fixtures, never production sources.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pytest
import minicode.tools.task as task_module
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolResult


def probe_permissions() -> dict:
    rows = {}
    with tempfile.TemporaryDirectory(prefix="minicode-role-review-") as temporary:
        root = Path(temporary).resolve()
        (root / "allowed").mkdir()
        (root / "outside.txt").write_text("synthetic out-of-scope data\n")
        (root / ".env").write_text("SYNTHETIC_FIXTURE=not-a-secret\n")
        for role in ("analyst", "reviewer", "adjudicator"):
            captured = {}

            def capture_turn(**kwargs):
                permissions = kwargs["permissions"]
                captured["permission_class"] = type(permissions).__name__
                captured["tools"] = sorted(tool.name for tool in kwargs["tools"].list())
                captured["memory_is_none"] = kwargs["memory_manager"] is None
                captured["path_checks"] = {}
                for relative in ("allowed", "outside.txt", ".env"):
                    try:
                        permissions.ensure_path_access(str(root / relative), "read")
                        outcome = "allowed"
                    except Exception as error:
                        outcome = getattr(error, "code", type(error).__name__)
                    captured["path_checks"][relative] = outcome
                kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
                return [{"role": "assistant", "content": "fixture complete"}]

            def ordinary_prompt(**kwargs):
                captured["ordinary_plan_prompt_builder_called"] = True
                return kwargs["agent_def"]["system_prompt"], None

            context = ToolContext(
                cwd=str(root),
                _runtime={"model": "fake"},
                _logical_agent_role=role,
                _logical_agent_scope={
                    "read_roots": ("allowed",),
                    "write_paths": (),
                    "forbidden_paths": (".env",),
                },
                _logical_agent_workspace_root=str(root),
                _workflow_phase=True,
            )
            with (
                patch.object(task_module, "run_agent_turn", capture_turn),
                patch.object(task_module, "_build_sub_agent_system_prompt", ordinary_prompt),
                patch("minicode.subagent_model_routing.create_subagent_model_adapter", return_value=SimpleNamespace(runtime={"model": "fake"})),
                patch("minicode.intent_semantic_classifier.build_semantic_intent_adapter", return_value=None),
            ):
                result = task_module._run(
                    {"description": "offline role probe", "prompt": "Read only authorized assignment inputs.", "agent_type": "plan"},
                    context,
                )
            captured["tool_result_ok"] = result.ok
            rows[role] = captured
    assert rows["analyst"]["path_checks"]["outside.txt"] != "allowed"
    assert rows["analyst"]["path_checks"]["allowed"] == "allowed"
    assert rows["reviewer"]["path_checks"]["allowed"] == "allowed"
    assert rows["reviewer"]["path_checks"][".env"] != "allowed"
    assert rows["adjudicator"]["path_checks"]["outside.txt"] == "allowed"
    assert rows["adjudicator"]["path_checks"][".env"] == "allowed"
    return rows


def probe_adjudicator_evidence() -> dict:
    spec = importlib.util.spec_from_file_location("review_routing_fixtures", REPO / "tests/test_workflow_v2_structured_routing.py")
    fixture = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(fixture)
    captured = {"evidence_reads": []}

    def worker(input_data, context):
        role = fixture._worker_role(input_data)
        if role == "analyst":
            fixture._write_analyst_result(
                input_data, context, prepare_change=True,
                risks=[{"severity": "high", "statement": "Fixture risk needs adjudication.", "evidenceRefs": ["claim_route"]}],
            )
            return ToolResult(ok=True, output="fixture analyst report")
        if role == "adjudicator":
            package = fixture._prompt_json(input_data["prompt"], "CONFLICT PACKAGE")
            for claim in package["claims"]:
                for ref in claim["evidenceRefs"]:
                    try:
                        artifact = context._subagent_mailbox.read(ref)
                        outcome = "readable" if artifact is not None else "missing"
                    except Exception as error:
                        outcome = f"{type(error).__name__}: {error}"
                    captured["evidence_reads"].append({"claim": claim["claimId"], "ref": ref, "outcome": outcome})
            # Stop at adjudication: this probe never promotes a candidate change.
            fixture._write_adjudication_result(input_data, context, decision="insufficient")
            return ToolResult(ok=True, output="fixture evidence unavailable")
        raise AssertionError(f"Unexpected worker: {role}")

    with tempfile.TemporaryDirectory(prefix="minicode-evidence-review-") as temporary:
        root = Path(temporary).resolve()
        (root / "allowed.txt").write_text("before\n")
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(task_module, "_run", worker)
            result = fixture._run(root, fixture._request(risk="medium", complex=True), monkeypatch, commit_audit_waived=True)
        captured["workflow_ok"] = result.ok
        captured["workspace_unchanged"] = (root / "allowed.txt").read_text() == "before\n"
    assert len(captured["evidence_reads"]) >= 2
    assert all("not readable" in row["outcome"] for row in captured["evidence_reads"])
    return captured


if __name__ == "__main__":
    result = {"role_permissions": probe_permissions(), "adjudicator_evidence": probe_adjudicator_evidence()}
    output = Path(__file__).with_name("role-boundaries-result.json")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
