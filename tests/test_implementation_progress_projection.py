"""Early request projection preserves all reads and unchanged source evidence."""

import copy

import pytest

import minicode.implementation_recovery as recovery
from minicode.implementation_context import build_implementation_context


def _source_packet(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n" + "# initial implementation detail\n" * 35)
    (tmp_path / "other.py").write_text("untouched = 'retain this dependency'\n" + "# dependency detail\n" * 35)
    return build_implementation_context(tmp_path, ("app.py", "other.py"), (), "Update value")


def _pair(identity, name, path, content, *, failed=False):
    return [
        {"role": "assistant_tool_call", "toolUseId": identity, "toolName": name,
         "assistantTurnId": "turn-" + identity,
         "input": {"path": path, "old": "old " * 2000, "new": "new " * 2000}},
        {"role": "tool_result", "toolUseId": identity, "toolName": name,
         "content": content, "isError": failed},
    ]


def test_changed_source_section_retires_but_all_reads_and_other_files_stay_exact(tmp_path):
    packet = _source_packet(tmp_path)
    messages = [
        {"role": "system", "content": "current authority"},
        {"role": "user", "content": "original requirements"},
        {"role": "user", "content": packet, "_implementation_context": True},
        *_pair("first-window", "read_file", "app.py", "earlier function " * 1000),
        *_pair("second-window", "read_file", "app.py", "later function " * 1000),
        *_pair("failed-read", "read_file", "other.py", "permission denied", failed=True),
    ]
    original = copy.deepcopy(messages)
    projected = recovery.project_implementation_progress_messages(
        messages, changed_paths=frozenset({"app.py"}),
    )
    assert messages == original
    assert projected[:2] == messages[:2] and projected[3:] == messages[3:]
    assert "1 | value = 1" not in projected[2]["content"]
    assert "host-observed byte change" in projected[2]["content"]
    assert projected[2]["content"].split("FILE other.py", 1)[1] == packet.split("FILE other.py", 1)[1]
    assert recovery.project_implementation_progress_messages(
        projected, changed_paths=frozenset({"app.py"}),
    ) == projected


@pytest.mark.parametrize("paths", [frozenset(), frozenset({"../app.py"}), frozenset({"/app.py"}),
                                  frozenset({"./app.py"}), frozenset({"app.py\0"})])
def test_unobserved_or_invalid_paths_never_activate_projection(tmp_path, paths):
    packet = _source_packet(tmp_path)
    messages = [{"role": "user", "content": packet, "_implementation_context": True}]
    messages += _pair("claimed", "edit_file", "app.py", "changed_paths=app.py; file_changed=true")
    messages[-1]["_file_changed"] = True
    messages += _pair("failed", "edit_file", "app.py", "failed edit", failed=True)
    assert recovery.project_implementation_progress_messages(messages, changed_paths=paths) == messages


def test_model_text_and_non_user_host_style_fields_cannot_retire_source(tmp_path):
    packet = _source_packet(tmp_path)
    messages = [
        {"role": "user", "content": packet},
        {"role": "assistant", "content": packet, "_implementation_context": True},
        {"role": "user", "content": packet, "_implementation_context": "true"},
        {"role": "user", "content": "instructions before " + packet, "_implementation_context": True},
    ]
    assert recovery.project_implementation_progress_messages(
        messages, changed_paths=frozenset({"app.py"}),
    ) == messages


def test_real_packet_missing_test_diagnostic_survives_changed_source_retirement(tmp_path):
    _source_packet(tmp_path)
    packet = build_implementation_context(
        tmp_path, ("app.py", "tests/test_new.py", "other.py"), (), "Update value",
    )
    messages = [{"role": "user", "content": packet, "_implementation_context": True}]
    projected = recovery.project_implementation_progress_messages(
        messages, changed_paths=frozenset({"app.py"}),
    )
    diagnostic = "tests/test_new.py: missing, unavailable, or outside readable source scope"
    assert diagnostic in packet and diagnostic in projected[0]["content"]
    assert "1 | value = 1" not in projected[0]["content"]
    assert projected[0]["content"].split(diagnostic, 1)[1] == packet.split(diagnostic, 1)[1]


def test_disjoint_edit_arguments_and_latest_failure_remain_exact(tmp_path):
    packet = _source_packet(tmp_path)
    messages = [{"role": "user", "content": packet, "_implementation_context": True}]
    messages += _pair("old", "edit_file", "app.py", "Applied; historical check failed")
    messages += _pair("latest", "edit_file", "app.py", "Applied; latest check passed")
    messages += _pair("failure", "edit_file", "app.py", "Found 2 matching anchors", failed=True)
    original = copy.deepcopy(messages)
    projected = recovery.project_implementation_progress_messages(
        messages, changed_paths=frozenset({"app.py"}),
    )
    assert messages == original
    assert "1 | value = 1" not in projected[0]["content"]
    assert projected[1:] == messages[1:]


@pytest.mark.parametrize("edit_kind", ["changed", "no_op", "failed"])
def test_real_edit_changes_only_next_request_view_and_preserves_canonical(tmp_path, edit_kind):
    from minicode.agent_loop import run_agent_turn
    from minicode.implementation_execution import ImplementationSpec
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.read_file import read_file_tool
    from minicode.types import AgentStep, ModelAdapter

    _source_packet(tmp_path)
    observed = []

    class Model(ModelAdapter):
        def next(self, messages, **kwargs):
            observed.append(copy.deepcopy(messages))
            if len(observed) == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "inspect", "toolName": "read_file", "input": {"path": "other.py"},
                }])
            if len(observed) == 2:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "edit", "toolName": "edit_file", "input": {
                        "path": "app.py", "old": "missing anchor" if edit_kind == "failed" else "value = 1",
                        "new": "value = 1" if edit_kind == "no_op" else "value = 2",
                    },
                }])
            return AgentStep(type="assistant", content="<final>Done.")

    canonical = run_agent_turn(
        model=Model(), tools=ToolRegistry([edit_file_tool, read_file_tool]),
        messages=[{"role": "user", "content": "Update app.py; retain other.py."}],
        cwd=str(tmp_path), implementation_task=True, enable_work_chain=False, max_steps=20,
        implementation_spec=ImplementationSpec(("app.py", "other.py"), (), "Update value"),
    )
    assert len(observed) == 3
    original_packet = next(m["content"] for m in observed[0] if m.get("_implementation_context"))
    sent_packet = next(m["content"] for m in observed[2] if m.get("_implementation_context"))
    if edit_kind == "changed":
        assert "1 | value = 1" not in sent_packet, "A changed file's initial source must stop consuming later request budget."
        assert "host-observed byte change" in sent_packet
        assert (tmp_path / "app.py").read_text().startswith("value = 2\n")
    else:
        assert sent_packet == original_packet, "Failed and no-op tools cannot authorize evidence retirement."
    assert sent_packet.split("FILE other.py", 1)[1] == original_packet.split("FILE other.py", 1)[1]
    before = next(m for m in observed[1] if m.get("toolUseId") == "inspect" and m["role"] == "tool_result")
    after = next(m for m in observed[2] if m.get("toolUseId") == "inspect" and m["role"] == "tool_result")
    assert before == after
    assert next(m["content"] for m in canonical if m.get("_implementation_context")) == original_packet
