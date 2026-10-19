"""Enforced phase behavior is checked at actual tool and provider boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import AgentTurnDeadlineExceeded, run_agent_turn
from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


def controller(*, calls=40, tokens=500000, steps=37, now=0, deadline=600, analysis=False):
    budget = AgentTurnBudget(max_model_calls=calls, max_total_tokens=tokens)
    return ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=steps,
                                         now=now, deadline=deadline, analysis_complete=analysis), budget


def call(name, identity="c", **args):
    return {"id": identity, "toolName": name, "input": args}


def test_survey_allocation_is_latched_and_real_writes_do_not_reset_it():
    policy, budget = controller()
    assert policy.investigation_calls == 6
    for _ in range(6):
        assert policy.request(budget.snapshot(), estimated_input=5000, now=0).phase is ImplementationPhase.INVESTIGATE
        policy.admitted()
    assert policy.request(budget.snapshot(), estimated_input=5000, now=0).phase is ImplementationPhase.IMPLEMENT
    policy.observe_change(True)
    assert not policy.allowed_name("list_files")
    assert policy.calls == 6
    for i in range(8):
        assert not policy.admit_batch([call("read_file", str(i), path="app.py")])
    assert not policy.admit_batch([call("read_file", path="app.py")])
    policy.observe_change(False)
    assert policy.allowed_name("read_file")
    policy.observe_change(True)
    assert policy.allowed_name("read_file")
    assert policy.phase is ImplementationPhase.IMPLEMENT


@pytest.mark.parametrize("kind", ["time", "tokens", "calls"])
def test_survey_stops_at_any_budget_boundary(kind):
    policy, budget = controller()
    now = 120 if kind == "time" else 0
    if kind == "tokens":
        budget.record_model_call(input_tokens=100000)
    if kind == "calls":
        policy.calls = 6
    assert policy.request(budget.snapshot(), estimated_input=5000, now=now).phase is ImplementationPhase.IMPLEMENT


def test_local_timeout_keeps_healthy_request_intact_before_next_phase_view():
    policy, budget = controller(analysis=True)
    first = policy.request(budget.snapshot(), estimated_input=5000, now=121)
    assert first.deadline == 301
    policy.admitted()
    policy.timeout(now=301)
    assert policy.phase is ImplementationPhase.IMPLEMENT
    next_request = policy.request(budget.snapshot(), estimated_input=5000, now=301)
    assert next_request.deadline == 481
    assert next_request.deadline > policy.final_start
    assert next_request.preserve_calls == 2
    policy.admitted()
    policy.timeout(now=481)
    final = policy.request(budget.snapshot(), estimated_input=5000, now=481)
    assert final.phase is ImplementationPhase.FINALIZE
    assert final.deadline == policy.handoff_start == pytest.approx(600 - (600 - 481) / 3)
    assert not policy.allowed_name("edit_file")


def test_placeholder_byte_changes_never_reset_phase_work_counters():
    policy, _ = controller(analysis=True)
    for i in range(8):
        assert not policy.admit_batch([call("read_file", str(i), path="app.py")])
        policy.observe_change(True)  # even a host-observed write earns no new reads
    assert policy.allowed_name("read_file")
    for _ in range(20):
        policy.observe_change(True)
    assert policy.focused_read_batches == 8
    assert policy.allowed_name("read_file")


@pytest.mark.parametrize("reason", ["time", "tokens", "calls"])
def test_reserved_finalization_is_executable_and_cannot_reopen_implementation(reason):
    policy, budget = controller()
    now = 480 if reason == "time" else 0
    if reason == "tokens":
        budget.record_model_call(input_tokens=475000)
    if reason == "calls":
        policy.calls = 35
    request = policy.request(budget.snapshot(), estimated_input=5000, now=now)
    assert request.phase is ImplementationPhase.FINALIZE
    assert request.preserve_calls == 1
    assert policy.handoff_start == 600 - min(45, (600 - now) / 3)
    assert request.deadline == min(now + 180, policy.handoff_start)
    assert policy.allowed_name("executor_submit")
    assert policy.allowed_name("test_runner")
    assert not policy.allowed_name("read_file")
    assert not policy.allowed_name("edit_file")
    policy.observe_change(True)
    assert policy.phase is ImplementationPhase.FINALIZE
    policy.admitted()
    assert policy.request(budget.snapshot(), estimated_input=5000, now=now + 1).preserve_calls == 0
    policy.admitted()
    assert policy.exhausted


@pytest.mark.parametrize("args", [
    {"command": "python", "args": ["-c", "open('app.py').read()"]},
    {"command": "cat app.py"},
    {"command": "python -m pytest && cat app.py"},
    {"command": "ruff", "args": ["check", "--fix"]},
])
def test_shell_cannot_bypass_inspection_or_write_limits(args):
    policy, _ = controller(analysis=True)
    assert policy.admit_batch([call("run_command", **args)])
    assert not policy.admit_batch([call("run_command", command="python", args=["-m", "pytest", "tests/test_app.py"])])


def test_one_request_cannot_read_an_unbounded_batch():
    policy, _ = controller()
    rejected = policy.admit_batch([call("read_file", str(i), path=f"part{i}.py") for i in range(10)])
    assert set(rejected) == {str(i) for i in range(4, 10)}
    assert policy.admit_batch([call("read_file", path="app.py", limit=20000)])


def test_analysis_receipt_skips_executor_survey_and_low_budget_is_not_rounded_up():
    policy, budget = controller(analysis=True)
    assert policy.request(budget.snapshot(), estimated_input=5000, now=0).phase is ImplementationPhase.IMPLEMENT
    assert not policy.allowed_name("file_tree")
    low, budget = controller(calls=1, steps=1)
    request = low.request(budget.snapshot(), estimated_input=10, now=0)
    assert request.phase is ImplementationPhase.IMPLEMENT
    assert request.preserve_calls == 0
    low.admitted()
    assert low.exhausted


def test_atomic_estimated_tail_floor_survives_parallel_admission():
    budget = AgentTurnBudget(max_total_tokens=1000, max_model_calls=10)
    def reserve():
        try:
            return budget.reserve_model_call(300, preserve_total_tokens=600)
        except AgentBudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(4)))
    assert sum(value is not None for value in reservations) == 1
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().reserved_total_tokens == 300


class PersistentReader(ModelAdapter):
    def __init__(self):
        self.count = 0

    def next(self, messages, tools_override=None, **kwargs):
        self.count += 1
        return AgentStep(type="tool_calls", calls=[call("read_file", str(self.count), path="app.py")])


def test_noncompliant_reader_stops_at_work_budget_without_forced_edits(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    model = PersistentReader()
    reads = []
    def read(values, context):
        reads.append(values)
        return read_file_tool.run(values, context)
    read_tool = replace(read_file_tool, run=read)
    messages = run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([read_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=20)
    assert len(reads) <= 18  # 20 requests, including two reserved final rounds
    assert model.count <= 20
    assert (tmp_path / "app.py").read_text() == "value = 1\n"
    assert any("implementation_phase_limit" in str(m.get("content")) for m in messages)
    assert messages[-1].get("_partial")


def test_submit_stops_worker_and_balances_later_declined_write(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    submit = ToolDefinition(name="executor_submit", description="host-bound test submit", input_schema={"type": "object"},
        validator=lambda v: v, run=lambda v,c: ToolResult(ok=True, output="declaration received; verification pending", _implementation_submitted=True))
    class Submitting(ModelAdapter):
        def next(self, messages, **kwargs):
            assert not any(m.get("toolName") == "executor_submit" for m in messages), "extra model call after submission"
            return AgentStep(type="tool_calls", calls=[call("executor_submit", "submit"), call("edit_file", "revert", path="app.py", old="value = 1", new="value = 0")])
    messages = run_agent_turn(bounded_implementation_phases=True, model=Submitting(), tools=ToolRegistry([submit, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=10)
    assert (tmp_path / "app.py").read_text() == "value = 1\n"
    pairs = {m["toolUseId"]: m for m in messages if m.get("role") == "tool_result"}
    assert set(pairs) == {"submit", "revert"}
    assert not pairs["submit"]["isError"] and pairs["revert"]["isError"]


def test_phase_request_timeout_preserves_the_next_execution_phase(tmp_path):
    from test_agent_model_events import RecordingSink
    (tmp_path / "app.py").write_text("value = 1\n")
    model = SimpleNamespace(count=0)
    deadlines = []
    def next_step(messages, deadline_monotonic=None, **kwargs):
        model.count += 1
        deadlines.append(deadline_monotonic)
        if model.count == 1:
            raise ModelCallDeadlineExceeded("local phase request elapsed")
        if model.count == 2:
            return AgentStep(type="tool_calls", calls=[call("edit_file", path="app.py", old="value = 1", new="value = 2")])
        return AgentStep(type="assistant", content="Changed value; independent checks pending.")
    model.next = next_step
    sink = RecordingSink()
    run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Change app.py value from 1 to 2."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=20, event_sink=sink)
    assert model.count == 3 and (tmp_path / "app.py").read_text() == "value = 2\n"
    assert deadlines[1] > deadlines[0]
    assert len([e for e in sink.events if e.event_type == "model.started"]) == 3
    assert len([e for e in sink.events if e.event_type in {"model.completed", "model.failed"}]) == 3


def test_global_expiry_and_readonly_are_not_converted_into_implementation(tmp_path):
    import time
    with pytest.raises(AgentTurnDeadlineExceeded):
        run_agent_turn(bounded_implementation_phases=True, model=PersistentReader(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
            messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
            implementation_task=True, enable_work_chain=False, deadline_monotonic=time.monotonic()-1)
    class ReadOnly(ModelAdapter):
        def next(self, messages, **kwargs):
            assert not any(m.get("_implementation_phase") for m in messages)
            assert "tools_override" not in kwargs
            return AgentStep(type="assistant", content="Reviewed without changing files.")
    run_agent_turn(bounded_implementation_phases=True, model=ReadOnly(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Review only; no changes."}], cwd=str(tmp_path),
        implementation_task=False, enable_work_chain=False)


@pytest.mark.parametrize("calls", [1, 2, 3])
def test_small_allowance_can_make_one_supported_edit_without_exceeding_calls(tmp_path, calls):
    from minicode.task_outcome import AgentOutcomeCapture
    (tmp_path / "app.py").write_text("value = 1\n")
    model = SimpleNamespace(count=0)
    def next_step(messages, **kwargs):
        model.count += 1
        if model.count == 1:
            return AgentStep(type="tool_calls", calls=[call("edit_file", path="app.py", old="value = 1", new="value = 2")])
        return AgentStep(type="assistant", content="Updated value; independent verification remains pending.")
    model.next = next_step
    capture = AgentOutcomeCapture()
    run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([edit_file_tool]),
        messages=[{"role": "user", "content": "Change app.py from 1 to 2."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=calls,
        agent_budget=AgentTurnBudget(max_model_calls=calls), outcome_capture=capture)
    assert "value = 2" in (tmp_path / "app.py").read_text()
    assert model.count <= calls
    assert not capture.outcome.goal_achieved


def test_primary_phase_budget_keeps_its_committed_receipt_and_summary_slot(tmp_path):
    receipt = ToolResult(ok=True, output="host committed receipt", _must_deliver=True, _continue_parent=True)
    tool = ToolDefinition(name="task", description="committed host task", input_schema={"type": "object"},
                          validator=lambda v:v, run=lambda v,c:receipt)
    model = SimpleNamespace(count=0)
    def next_step(messages, **kwargs):
        model.count += 1
        if model.count == 1:
            return AgentStep(type="tool_calls", calls=[call("task")])
        return AgentStep(type="assistant", content="Committed changes; remaining checks are unverified.")
    model.next = next_step
    messages = run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=2,
        enable_bounded_completion=True, agent_budget=AgentTurnBudget(max_model_calls=2))
    assert model.count == 2
    assert any(m.get("content") == receipt.output for m in messages)


def test_rejected_commands_do_not_remove_an_authorized_edit(tmp_path):
    from minicode.permissions import PermissionManager
    from minicode.tooling import ToolDefinition, ToolResult
    (tmp_path / "app.py").write_text("value = 1\n")
    command = ToolDefinition(name="run_command", description="verification only", input_schema={"type": "object"},
        validator=lambda v: v, run=lambda v,c: ToolResult(ok=True, output="never execute the rejected command"))
    class Correcting(ModelAdapter):
        count = 0
        def next(self, messages, tools_override=None, **kwargs):
            self.count += 1
            if self.count <= 2:
                return AgentStep(type="tool_calls", calls=[call("run_command", str(self.count), command="python", args=["-c", "print('read')"])])
            if self.count == 3:
                return AgentStep(type="tool_calls", calls=[call("edit_file", "fix", path="app.py", old="value = 1", new="value = 2")])
            return AgentStep(type="assistant", content="Changed value; independent checks pending.")
    model = Correcting()
    messages = run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([command, edit_file_tool]),
        messages=[{"role": "user", "content": "Change app.py value from 1 to 2."}], cwd=str(tmp_path),
        permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}),
        implementation_task=True, enable_work_chain=False, max_steps=20, implementation_analysis_complete=True)
    assert (tmp_path / "app.py").read_text() == "value = 2\n"
    results = {m["toolUseId"]:m for m in messages if m.get("role") == "tool_result"}
    assert results["1"]["isError"] and results["2"]["isError"]
    assert not results["fix"]["isError"]


@pytest.mark.parametrize("efforts,expected", [((),None), (("low","high","max"),"low")])
def test_short_phase_uses_only_advertised_effort_and_keeps_model_state(tmp_path, efforts, expected):
    seen = []
    class Survey(ModelAdapter):
        supported_reasoning_efforts = efforts
        def next(self, messages, **kwargs):
            seen.append(kwargs)
            return AgentStep(type="assistant", content="Specific blocker; no unsupported edit.")
    model = Survey()
    run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry([edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=20)
    assert model.supported_reasoning_efforts == efforts
    assert len(seen) == 1
    assert seen[0].get("reasoning_effort_override") == expected


def test_implementation_uses_advertised_high_effort(tmp_path):
    seen = []
    class Implementing(ModelAdapter):
        supported_reasoning_efforts = ("low","high","max")
        def next(self, messages, **kwargs):
            seen.append(kwargs)
            return AgentStep(type="assistant", content="Specific blocker; no unsupported edit.")
    run_agent_turn(bounded_implementation_phases=True, model=Implementing(), tools=ToolRegistry([edit_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py."}], cwd=str(tmp_path),
        implementation_task=True, enable_work_chain=False, max_steps=20, implementation_analysis_complete=True)
    assert len(seen) == 1
    assert seen[0]["reasoning_effort_override"] == "high"


@pytest.mark.parametrize("include_editor", [False,True])
@pytest.mark.parametrize("output_limit", [512,8192])
def test_primary_reserved_summary_uses_short_tool_free_request(tmp_path, include_editor, output_limit):
    seen = []
    class Closing(ModelAdapter):
        supported_reasoning_efforts = ("low","high","max")
        count = 0
        def next(self, messages, **kwargs):
            self.count += 1
            seen.append(kwargs)
            if self.count == 1:
                return AgentStep(type="tool_calls", calls=[call("todo_write", todos=[])])
            return AgentStep(type="assistant", content="Partial implementation; independent verification pending.")
    todo = ToolDefinition(name="todo_write", description="bookkeeping",input_schema={"type":"object"},validator=lambda v:v,
        run=lambda v,c:ToolResult(ok=True,output="recorded"))
    model = Closing()
    tools=[todo,edit_file_tool] if include_editor else [todo,ToolDefinition(name="task",description="coordinator",input_schema={"type":"object"},validator=lambda v:v,run=lambda v,c:ToolResult(ok=True,output="unused"))]
    messages=run_agent_turn(bounded_implementation_phases=True, model=model, tools=ToolRegistry(tools),runtime={"maxOutputTokens":output_limit},
        messages=[{"role":"user","content":"Fix app.py."}],cwd=str(tmp_path),implementation_task=True,
        enable_work_chain=False,max_steps=2,enable_bounded_completion=True,
        agent_budget=AgentTurnBudget(max_model_calls=2))
    assert model.count == 2
    assert seen[-1]["disable_tools"] is True
    assert seen[-1]["max_output_tokens_override"] == min(2048,output_limit)
    assert seen[-1]["reasoning_effort_override"] == "low"
    assert seen[-1]["max_retries_override"] == 0
    assert "Partial implementation; independent verification pending." in messages[-1]["content"]
    assert messages[-1]["_partial"]


def test_native_finalization_projects_and_enforces_only_the_bound_submission():
    policy,budget=controller(analysis=True)
    policy.request(budget.snapshot(),estimated_input=1000,now=480)
    native=ToolDefinition(name="executor_submit",description="bound submission",input_schema={"type":"object"},validator=lambda v:v,run=lambda v,c:ToolResult(ok=True,output="pending",_implementation_submitted=True))
    note=ToolDefinition(name="subagent_note_write",description="legacy note",input_schema={"type":"object"},validator=lambda v:v,run=lambda v,c:ToolResult(ok=True,output="unused"))
    view=policy.tool_view(ToolRegistry([native,note,edit_file_tool]))
    assert [tool.name for tool in view.list()] == ["executor_submit"]
    rejected=policy.admit_batch([call("subagent_note_write","note"),call("executor_submit","submit")])
    assert set(rejected) == {"note"}
    assert policy.phase is ImplementationPhase.FINALIZE


def test_native_handoff_blocks_legacy_noise_and_delivers_in_the_existing_three_calls(tmp_path):
    from minicode.permissions import PermissionManager
    (tmp_path/"app.py").write_text("value = 1\n")
    notes=[];views=[]
    native=ToolDefinition(name="executor_submit",description="bound test submission",input_schema={"type":"object"},validator=lambda v:v,run=lambda v,c:ToolResult(ok=True,output="declaration received; verification pending",_implementation_submitted=True))
    note=ToolDefinition(name="subagent_note_write",description="legacy note",input_schema={"type":"object"},validator=lambda v:v,run=lambda v,c:(notes.append(v) or ToolResult(ok=True,output="unused")))
    class Finishing(ModelAdapter):
        count=0
        def next(self,messages,tools_override=None,**kwargs):
            self.count+=1
            views.append([t.name for t in tools_override.list()])
            if self.count==1:return AgentStep(type="tool_calls",calls=[call("edit_file","fix",path="app.py",old="value = 1",new="value = 2")])
            if self.count==2:return AgentStep(type="tool_calls",calls=[call("subagent_note_write","noise")])
            return AgentStep(type="tool_calls",calls=[call("executor_submit","submit")])
    model=Finishing()
    ms=run_agent_turn(bounded_implementation_phases=True, model=model,tools=ToolRegistry([edit_file_tool,note,native]),cwd=str(tmp_path),
        messages=[{"role":"user","content":"Change app.py value to 2."}],permissions=PermissionManager(str(tmp_path),prompt=lambda _:{"decision":"allow_once"}),
        implementation_task=True,enable_work_chain=False,max_steps=3,agent_budget=AgentTurnBudget(max_model_calls=3))
    assert model.count==3
    assert views[1:]==[["executor_submit"],["executor_submit"]]
    assert not notes
    results={m["toolUseId"]:m for m in ms if m.get("role")=="tool_result"}
    assert not results["fix"]["isError"] and results["noise"]["isError"] and not results["submit"]["isError"]
    assert (tmp_path/"app.py").read_text()=="value = 2\n"
