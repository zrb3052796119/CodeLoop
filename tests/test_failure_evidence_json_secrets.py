"""JSON strings must be redacted at actual diagnostic persistence boundaries."""
import hashlib
import json

from minicode import subagent_model_routing
from minicode.agent_budget import AgentTurnBudget
from minicode.failure_evidence import redact_diagnostic_text
from minicode.subagent_journal import SubagentRunJournal, new_subagent_id
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.task import task_tool
from minicode.types import AgentStep, ModelAdapter
from minicode.workflow_workspace import WorkflowWorkspaceTransaction

SECRET_KEYS = (
    "password", "passwd", "pwd", "api_key", "apiKey", "access_token",
    "refreshToken", "authorization", "clientSecret", "credentials",
    "private_key", "token", "dbPassword", "cookie",
)


def test_json_secret_fields_are_redacted_in_real_tool_input_and_output_files(tmp_path):
    run_dir = tmp_path / ("run_" + "8" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()
    for index, key in enumerate(SECRET_KEYS):
        value = f"ordinary-private-value-{index}"
        payload = json.dumps({key: value, "test_name": "test_real_behavior", "status": "failed"})
        journal.append_tool_evidence(child, invocation_id=f"tool-{index}", tool_name="write_file",
                                     phase="started", tool_input={"path": "app.py", "content": payload})
        journal.append_tool_evidence(child, invocation_id=f"tool-{index}", tool_name="write_file",
                                     phase="finished", output=payload, is_error=True)
    path = next(run_dir.rglob("*.tools.jsonl"))
    raw = path.read_text()
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 2 * len(SECRET_KEYS)
    for index, key in enumerate(SECRET_KEYS):
        assert f"ordinary-private-value-{index}" not in raw
        for content in (rows[index * 2]["input"]["content"], rows[index * 2 + 1]["output"]):
            assert json.loads(content) == {key: "[REDACTED]", "test_name": "test_real_behavior", "status": "failed"}


def test_json_escapes_unicode_key_and_multiline_value_preserve_neighbor_fields():
    secret = 'ordinary-quoted-"piece"\\path\nsecond-line\r\ttail'
    payload = '{\n  "pass\\u0077ord": ' + json.dumps(secret) + ',\n  "test_name": "test_real_behavior"\n}'
    safe = redact_diagnostic_text(payload, max_chars=8000)
    assert json.loads(safe) == {"password": "[REDACTED]", "test_name": "test_real_behavior"}
    assert "second-line" not in safe and "ordinary-quoted" not in safe
    assert '"pass\\u0077ord"' in safe  # surrounding source is not reserialized
    assert '\n  "test_name": "test_real_behavior"\n}' in safe


def test_json_secret_nested_value_is_one_complete_span():
    payload = '{"credentials": {"user": "ordinary-user", "parts": ["private-part", {"x": 1}]}, "status": "failed"}'
    safe = redact_diagnostic_text(payload, max_chars=8000)
    assert json.loads(safe) == {"credentials": "[REDACTED]", "status": "failed"}


def test_redaction_preserves_normal_code_test_strings_and_metric_descriptors():
    code = '''def test_password_policy():
    expected = {"password_policy": "minimum length", "token_count": 12, "api_key_name": "primary"}
    actual = {"status": "failed", "message": "expected status == ready", "input_tokens": 123}
    assert actual["status"] == "failed"
'''
    assert redact_diagnostic_text(code, max_chars=8000) == code
    expression = 'config = {"password": settings.password, "status": get_status()}\n'
    assert redact_diagnostic_text(expression, max_chars=8000) == expression
    assert redact_diagnostic_text('{"name": "test_secret_parser", "message": "normal test output"}', max_chars=8000) == '{"name": "test_secret_parser", "message": "normal test output"}'


def test_real_headless_child_read_result_redacts_json_before_budget_exit(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "ordinary-headless-private-value"
    (workspace / "response.json").write_text(json.dumps({"password": secret, "status": "failed"}))
    run_dir = tmp_path / ("run_" + "9" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()

    class Sink:
        def open_subagent_journal(self): return journal
        def emit(self, *_a, **_k): pass

    class Model(ModelAdapter):
        calls = 0
        def next(self, messages, **kwargs):
            self.calls += 1
            assert self.calls == 1, "budget must prevent another model call"
            return AgentStep(type="tool_calls", calls=[{
                "id": "read-json", "toolName": "read_file", "input": {"path": "response.json"},
            }])

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([read_file_tool]))
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: Model())
    result = task_tool.run({"description": "read response", "prompt": "Read response.json",
                            "agent_type": "explore", "_subagent_id": child},
                           ToolContext(cwd=str(workspace), _runtime={"model": "fake"}, _event_sink=Sink(),
                                       _agent_budget=AgentTurnBudget(max_model_calls=1, max_total_tokens=500000)))
    assert not result.ok
    assert journal.list_runs()[0].outcome == "budget_exceeded"
    path = next(run_dir.rglob("*.tools.jsonl"))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["tool.started", "tool.finished"]
    assert records[1]["isError"] is False  # real read succeeded before turn budget exit
    assert '"status": "failed"' in records[1]["output"]
    assert "[REDACTED]" in records[1]["output"]
    assert secret not in path.read_text()


def test_real_failed_candidate_redacts_current_and_baseline_diff_without_restore(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = 'CONFIG = {"password": "ordinary-baseline-private", "attempt": 1}\n'
    after = 'CONFIG = {"password": "ordinary-candidate-private", "attempt": 2}\n'
    (workspace / "app.py").write_text(before)
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()
    with WorkflowWorkspaceTransaction(str(workspace), baseline_scope_paths=("app.py",)) as transaction:
        isolated = transaction.workspace_root
        (isolated / "app.py").write_text(after)
        candidate = transaction.capture_failure_candidate(("app.py",))
        path = journal.persist_failure_candidate(child, candidate)
    assert not isolated.exists()
    assert (workspace / "app.py").read_text() == before
    raw = path.read_text()
    saved = json.loads(raw)
    item = saved["files"][0]
    assert saved["promotable"] is False
    assert "ordinary-baseline-private" not in raw
    assert "ordinary-candidate-private" not in raw
    assert item["redacted"] is True
    assert item["sha256"] == hashlib.sha256(after.encode()).hexdigest()
    assert item["baselineSha256"] == hashlib.sha256(before.encode()).hexdigest()
    assert '"attempt": 2' in item["content"]
    assert '-CONFIG = {"password": "[REDACTED]", "attempt": 1}' in item["diff"]
    assert '+CONFIG = {"password": "[REDACTED]", "attempt": 2}' in item["diff"]


def test_already_truncated_json_string_does_not_leak_from_real_tool_log(tmp_path):
    root = tmp_path / ("run_" + "b" * 32)
    root.mkdir()
    journal = SubagentRunJournal(root)
    child = new_subagent_id()
    # Both a normal cutoff and a cutoff after an escaped quote are valid
    # prefixes of JSON string values returned by an upstream read_file limit.
    for index, tail in enumerate(("ordinary-private-tail", 'ordinary-private-\\"quoted-tail')):
        output = 'FILE: response.json\nTRUNCATED: yes\n{"status":"failed","password":"' + tail
        journal.append_tool_evidence(child, invocation_id=f"read-{index}", tool_name="read_file",
                                     phase="finished", output=output)
    rows = [json.loads(line) for line in next(root.rglob("*.tools.jsonl")).read_text().splitlines()]
    assert len(rows) == 2
    for row in rows:
        assert "ordinary-private" not in row["output"]
        assert row["output"].endswith('"[REDACTED: TRUNCATED JSON STRING]"')
        assert '"status":"failed"' in row["output"]
