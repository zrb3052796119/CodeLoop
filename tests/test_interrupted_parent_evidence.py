"""Exercise the real runtime/loop; only the model response sequence is local."""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import stat

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_runtime import AgentTurnRuntime
from minicode.context_manager import ContextManager
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep, ModelAdapter


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("interrupted_parent_diagnostic",
    ROOT / "scripts/run_requirement_completion_diagnostic.py")
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline parent evidence regression attempted network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class LocalModel(ModelAdapter):
    def __init__(self, failure=None, before_final=None):
        self.failure = failure
        self.before_final = before_final
        self.calls = 0

    def next(self, messages, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentStep(type="tool_calls", calls=[{"id": "generic-write", "toolName": "write_file",
                "input": {"path": "marker.txt", "content": "saved generic candidate\n"}}])
        if self.calls == 2:
            return AgentStep(type="tool_calls", calls=[{"id": "generic-check", "toolName": "check_marker", "input": {}}])
        assert self.calls == 3, "evidence retention cannot trigger another model call"
        if self.before_final:
            self.before_final()
        if self.failure is not None:
            raise self.failure
        return AgentStep(type="assistant", content="A generic normal result.")


def runtime_fixture(tmp_path, model, *, with_manager=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def check_marker(_value, context):
        assert (Path(context.cwd) / "marker.txt").read_text() == "saved generic candidate\n"
        return ToolResult(ok=True, output="GENERIC_CHECK_PASSED")

    registry = ToolRegistry([write_file_tool, ToolDefinition(name="check_marker",
        description="Check the generic local marker", input_schema={"type": "object"},
        validator=lambda value: value, run=check_marker)])
    manager = ContextManager(model="default") if with_manager else None
    runtime = AgentTurnRuntime(workspace=workspace, runtime={}, tools=registry,
        permissions=None, memory_manager=None, model=model, skill_routing=None,
        system_prompt="Generic parent context", context_manager=manager,
        allow_user_interaction=False, agent_budget=AgentTurnBudget())
    initial = [{"role": "system", "content": "PARENT_ONLY_CONTEXT"},
               {"role": "user", "content": "Save a marker and check it."}]
    return runtime, initial


def invoke(runtime, initial, root):
    # Old frozen runners have no observer. Let the same actual runtime run
    # without it, so the known baseline fails on missing retained evidence,
    # rather than an unrelated import/attribute/setup error.
    factory = getattr(diagnostic, "ParentExecutionEvidenceArchive", lambda _root: nullcontext())
    with factory(root) as archive:
        with observe_run(workspace=runtime.workspace, source="headless", title="Offline interruption",
                         enabled=False) as observation:
            try:
                return runtime.execute(initial, observation, defer_curator_maintenance=True), archive
            finally:
                runtime.dispose()


@pytest.mark.parametrize("failure", [KeyboardInterrupt("original interrupt"), SystemExit("original exit")])
def test_real_runtime_preserves_current_parent_tools_and_reraises(tmp_path, failure):
    model = LocalModel(failure)
    runtime, initial = runtime_fixture(tmp_path, model)
    root = tmp_path / "private-trial"
    with pytest.raises(type(failure)) as caught:
        invoke(runtime, initial, root)
    assert caught.value is failure
    assert model.calls == 3
    assert (runtime.workspace / "marker.txt").read_text() == "saved generic candidate\n"
    assert len(initial) == 2 and not any(m["role"] == "tool_result" for m in initial)
    assert (root / "interrupted-transcript.json").is_file(), "exception skipped current parent evidence"
    saved = json.loads((root / "interrupted-transcript.json").read_bytes())
    assert saved == runtime.context_manager.messages
    results = {m.get("toolUseId"): m for m in saved if m.get("role") == "tool_result"}
    assert {"generic-write", "generic-check"} <= set(results)
    assert "GENERIC_CHECK_PASSED" in results["generic-check"]["content"]
    assert any(m.get("role") == "assistant_tool_call" for m in saved)
    receipt = json.loads((root / "execution-evidence.json").read_bytes())
    assert receipt["available"] is True and receipt["errors"] == []
    assert receipt["source"] == "parent_runtime_context_manager"
    assert receipt["executionErrorClass"] == type(failure).__name__
    assert receipt["sha256"] == hashlib.sha256((root / "interrupted-transcript.json").read_bytes()).hexdigest()
    assert receipt["messageCount"] == len(saved) and receipt["roles"]["tool_result"] == 2
    assert "GENERIC_CHECK_PASSED" not in json.dumps(receipt)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((root / name).stat().st_mode) == 0o600 for name in diagnostic.INTERRUPTED_EVIDENCE_FILES)
    assert not (root / "response.txt").exists()
    assert not (root / "transcript.json").exists()


def test_normal_result_is_unchanged_and_does_not_create_interruption_evidence(tmp_path):
    model = LocalModel()
    runtime, initial = runtime_fixture(tmp_path, model)
    root = tmp_path / "private-trial"
    messages, archive = invoke(runtime, initial, root)
    assert messages[-1]["content"] == "A generic normal result."
    assert archive.receipt is None
    assert not any((root / name).exists() for name in diagnostic.INTERRUPTED_EVIDENCE_FILES)
    assert not (root / "response.txt").exists()


def test_nested_runtime_failure_cannot_replace_parent_context(tmp_path):
    failure = KeyboardInterrupt("nested interruption")
    child_path = tmp_path / "child"
    child_path.mkdir()
    child, child_initial = runtime_fixture(child_path, LocalModel(failure))
    child_initial[0]["content"] = "CHILD_ONLY_CONTEXT"

    def nested():
        with observe_run(workspace=child.workspace, source="headless", title="Child", enabled=False) as observation:
            child.execute(child_initial, observation, defer_curator_maintenance=True)

    parent, initial = runtime_fixture(tmp_path, LocalModel(before_final=nested))
    root = tmp_path / "private-trial"
    with pytest.raises(KeyboardInterrupt) as caught:
        invoke(parent, initial, root)
    assert caught.value is failure
    saved = json.loads((root / "interrupted-transcript.json").read_bytes())
    assert saved == parent.context_manager.messages
    assert "PARENT_ONLY_CONTEXT" in json.dumps(saved)
    assert "CHILD_ONLY_CONTEXT" not in json.dumps(saved)
    child.dispose()


def test_retention_failure_is_recorded_without_replacing_original_error(tmp_path, monkeypatch, caplog):
    failure = KeyboardInterrupt("original execution failure")
    runtime, initial = runtime_fixture(tmp_path, LocalModel(failure))
    original = diagnostic.ParentExecutionEvidenceArchive._write_private

    def deny_transcript(self, name, value):
        if name == "interrupted-transcript.json":
            raise OSError("sensitive error text must not reach metadata")
        return original(self, name, value)

    monkeypatch.setattr(diagnostic.ParentExecutionEvidenceArchive, "_write_private", deny_transcript)
    root = tmp_path / "private-trial"
    with pytest.raises(KeyboardInterrupt) as caught:
        invoke(runtime, initial, root)
    assert caught.value is failure
    receipt = json.loads((root / "execution-evidence.json").read_bytes())
    assert receipt["available"] is False
    assert receipt["errors"] == [{"stage": "transcript", "class": "OSError"}]
    assert "sensitive error text" not in (root / "execution-evidence.json").read_text()
    assert "sensitive error text" not in caplog.text
    assert "OSError" in caplog.text


def test_missing_context_manager_does_not_fall_back_to_initial_input(tmp_path):
    failure = KeyboardInterrupt()
    runtime, initial = runtime_fixture(tmp_path, LocalModel(failure), with_manager=False)
    root = tmp_path / "private-trial"
    with pytest.raises(KeyboardInterrupt):
        invoke(runtime, initial, root)
    receipt = json.loads((root / "execution-evidence.json").read_bytes())
    assert receipt["available"] is False
    assert receipt["errors"] == [{"stage": "transcript", "class": "ValueError"}]
    assert not (root / "interrupted-transcript.json").exists()


def test_receipt_storage_failure_keeps_original_error_and_content_free_log(tmp_path, monkeypatch, caplog):
    failure = KeyboardInterrupt("original failure")
    runtime, initial = runtime_fixture(tmp_path, LocalModel(failure))

    def unavailable(*_args):
        raise OSError("DO_NOT_LOG_PRIVATE_STORAGE_DETAIL")

    monkeypatch.setattr(diagnostic.ParentExecutionEvidenceArchive, "_write_private", unavailable)
    root = tmp_path / "private-trial"
    archive = diagnostic.ParentExecutionEvidenceArchive(root)
    with pytest.raises(KeyboardInterrupt) as caught:
        with archive, observe_run(workspace=runtime.workspace, source="headless", title="Storage failure",
                                  enabled=False) as observation:
            try:
                runtime.execute(initial, observation, defer_curator_maintenance=True)
            finally:
                runtime.dispose()
    assert caught.value is failure
    assert archive.errors == [{"stage": "transcript", "class": "OSError"},
                              {"stage": "receipt", "class": "OSError"}]
    assert "transcript: OSError" in caplog.text and "receipt: OSError" in caplog.text
    assert "DO_NOT_LOG_PRIVATE_STORAGE_DETAIL" not in caplog.text


@pytest.mark.parametrize("name", ["interrupted-transcript.json", "execution-evidence.json"])
def test_existing_evidence_blocks_reentry_without_overwrite(tmp_path, name):
    root = tmp_path / "private-trial"
    root.mkdir()
    target = root / name
    target.write_bytes(b"ORIGINAL EVIDENCE")
    with pytest.raises(ValueError, match="prior execution evidence"):
        with diagnostic.ParentExecutionEvidenceArchive(root):
            pytest.fail("must reject before execution")
    assert target.read_bytes() == b"ORIGINAL EVIDENCE"


def test_late_symlink_cannot_overwrite_external_file(tmp_path):
    root = tmp_path / "private-trial"
    outside = tmp_path / "outside.txt"
    outside.write_text("ORIGINAL OUTSIDE")
    failure = KeyboardInterrupt()
    model = LocalModel(failure, before_final=lambda: (root / "interrupted-transcript.json").symlink_to(outside))
    runtime, initial = runtime_fixture(tmp_path, model)
    with pytest.raises(KeyboardInterrupt) as caught:
        invoke(runtime, initial, root)
    assert caught.value is failure
    assert outside.read_text() == "ORIGINAL OUTSIDE"
    receipt = json.loads((root / "execution-evidence.json").read_bytes())
    assert receipt["available"] is False
    assert receipt["errors"] == [{"stage": "transcript", "class": "FileExistsError"}]
