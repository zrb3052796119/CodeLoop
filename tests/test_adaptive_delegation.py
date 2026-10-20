"""Parent delegates collaborative edits and keeps trivial edits."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.auto_mode import PermissionMode
from minicode.permissions import PermissionManager
from minicode.delegation_policy import (
    complete_workflow_write_paths,
    decide_delegation,
    delegation_prompt_note,
    handoff_blocks_tool,
    parent_delegation_gate,
)
from minicode.prompt import build_system_prompt
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep

MULTI = "Implement hold and release in stock.py and test_public.py."
SINGLE = "Fix the off-by-one in hold() in stock.py."
GATED = {"subagentRoutingEnabled": True, "delegationWriteGate": True}


class Script:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0
        self.seen: list[list] = []

    def next(self, messages, on_stream_chunk=None):
        self.seen.append(messages)
        step = self.steps[self.calls]
        self.calls += 1
        return step


def _write(identity: str, content: str) -> AgentStep:
    return AgentStep(type="tool_calls", calls=[{
        "id": identity,
        "toolName": "write_file",
        "input": {"path": "stock.py", "content": content},
    }])


def _final() -> AgentStep:
    return AgentStep(type="assistant", content="<final>done")


def _turn(tmp_path: Path, model, *, runtime=None, agent_depth=0, tools=None, messages=None, max_steps=4):
    return run_agent_turn(
        model=model,
        tools=tools or ToolRegistry([write_file_tool]),
        messages=messages or [{"role": "user", "content": MULTI}],
        cwd=str(tmp_path),
        runtime=runtime or {"subagentRoutingEnabled": True},
        enable_work_chain=False,
        defer_curator_maintenance=True,
        max_steps=max_steps,
        agent_depth=agent_depth,
    )


def test_multi_file_change_prefers_workflow_and_a_typo_stays_solo() -> None:
    collaborative = decide_delegation(MULTI, routing_enabled=True, implementation_owned=False)
    assert collaborative.mode == "workflow"
    assert collaborative.write_paths == ("stock.py", "test_public.py")

    local = decide_delegation(SINGLE, routing_enabled=True, implementation_owned=False)
    assert local.mode == "solo"
    assert local.reason == "local change"

    typo = decide_delegation(
        "Fix the typo in stock.py and test_public.py.",
        routing_enabled=True,
        implementation_owned=False,
    )
    assert typo.mode == "solo"

    assert decide_delegation(MULTI, routing_enabled=False, implementation_owned=False).mode == "solo"
    assert decide_delegation(MULTI, routing_enabled=True, implementation_owned=True).mode == "solo"
    survey = decide_delegation(
        "How does authentication work across the repository?",
        routing_enabled=True,
        implementation_owned=False,
    )
    assert survey.mode == "explore"
    assert decide_delegation(
        "Use a subagent to implement hold in stock.py.",
        routing_enabled=True,
        implementation_owned=False,
    ).mode == "workflow"
    assert parent_delegation_gate(
        MULTI, routing_enabled=True, implementation_owned=False, agent_depth=1,
    ).pending is False

    readonly = decide_delegation(
        "What do stock.py and test_public.py do?",
        routing_enabled=True, implementation_owned=False,
    )
    assert readonly.mode == "solo" and readonly.reason == "read-only question"
    hypothetical = decide_delegation(
        "If I edit stock.py, do I also need to edit test_public.py?",
        routing_enabled=True, implementation_owned=False,
    )
    assert hypothetical.mode == "solo"
    redesign = decide_delegation(
        "Redesign hold and release in stock.py: reserve then commit, "
        "and keep the public invariants.",
        routing_enabled=True, implementation_owned=False,
    )
    assert redesign.mode == "workflow" and redesign.reason == "single-file redesign"
    assert redesign.write_paths == ("stock.py",)
    assert decide_delegation(
        "Rewrite hold() in stock.py.",
        routing_enabled=True, implementation_owned=False,
    ).mode == "solo"
    assert decide_delegation(
        "Rewrite the docstring in stock.py and test_public.py.",
        routing_enabled=True, implementation_owned=False,
    ).mode == "solo"
    assert decide_delegation(
        "How do I implement hold and release in stock.py and test_public.py?",
        routing_enabled=True, implementation_owned=False,
    ).mode == "workflow"


def test_turn_note_names_the_decision_and_the_static_guide_stays_specific() -> None:
    from minicode.tools.task import build_task_tool_for_project

    note = delegation_prompt_note(MULTI, routing_enabled=True, implementation_owned=False)
    guided = build_system_prompt("/tmp", [], {"delegation_note": note})
    assert "stock.py, test_public.py" in guided
    assert "You choose whether to delegate." in guided
    gated_note = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=False, write_gate=True,
    )
    assert "You choose whether to delegate." not in gated_note
    assert "Delegate it before editing." in gated_note
    assert "Writes stay blocked" in gated_note
    assert "Hint:" in note
    plain = build_system_prompt("/tmp", [], {"skills": []})
    assert "This turn's delegation" not in plain
    assert "single-function or local edit" in plain
    assert "task(agent_type='workflow', write_paths, verification_commands)" in plain
    assert "read-only question is answered directly" in plain
    assert "few non-sensitive" not in plain
    assert "just to avoid work" not in plain
    parent = build_task_tool_for_project("/tmp")
    assert parent.description.startswith("Use when:")
    assert "Don't use when:" in parent.description
    assert "workflow_request" not in parent.input_schema["properties"]
    assert "parallel_explore" not in parent.input_schema["properties"]
    assert "general" not in parent.input_schema["properties"]["agent_type"]["enum"]
    assert "workflow_request" in task_tool.input_schema["properties"]
    assert "general" in task_tool.input_schema["properties"]["agent_type"]["enum"]
    hidden = parent.validator({"action": "poll", "subagent_id": "child-1"})
    assert hidden["action"] == "poll"
    assert delegation_prompt_note(
        SINGLE, routing_enabled=True, implementation_owned=False,
    ).startswith("Hint: this looks like a single-function or local edit")
    assert "read-only question" in delegation_prompt_note(
        "What do stock.py and test_public.py do?",
        routing_enabled=True, implementation_owned=False,
    )
    assert "single-file redesign" in delegation_prompt_note(
        "Redesign hold and release in stock.py: reserve then commit, "
        "and keep the public invariants.",
        routing_enabled=True, implementation_owned=False,
    )
    owned = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=True,
    )
    assert owned.startswith("This turn is a host-owned implementation")
    assert "Do not call task." in owned
    open_gate = parent_delegation_gate(
        MULTI, routing_enabled=True, implementation_owned=False, agent_depth=0,
    )
    assert open_gate.pending is False and open_gate.handoff is True
    closed = parent_delegation_gate(
        MULTI, routing_enabled=True, implementation_owned=False, agent_depth=0,
        write_gate=True,
    )
    assert closed.pending is True and closed.handoff is True


def test_routed_parent_is_coached_off_the_first_multi_file_edit(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path, Script([_write("1", "x = 1\n"), _final()]), runtime=GATED,
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" in blob
    assert "stock.py" in blob and "test_public.py" in blob
    assert "Do not also start a plan or explore" in blob
    assert not (tmp_path / "stock.py").exists()


def test_repeated_edits_stay_blocked_until_workflow_succeeds(tmp_path: Path) -> None:
    messages = _turn(tmp_path, Script([
        _write("1", "x = 1\n"),
        _write("2", "x = 2\n"),
        _final(),
    ]), runtime=GATED)
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert blob.count("stay blocked until this workflow call returns") == 2
    assert not (tmp_path / "stock.py").exists()


def test_shell_write_cannot_bypass_the_handoff(tmp_path: Path) -> None:
    def explode(_input, _context):
        raise AssertionError("python3 must not run before workflow")

    shell = ToolDefinition(
        name="run_command",
        description="shell",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=explode,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1",
                "toolName": "run_command",
                "input": {
                    "command": "python3",
                    "args": ["-c", "open('stock.py','w').write('solo')"],
                },
            }]),
            _final(),
        ]),
        tools=ToolRegistry([shell, write_file_tool]),
        runtime=GATED,
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "stay blocked until this workflow call returns" in blob
    assert "unexpected error" not in blob.lower()
    assert "Please retry" not in blob
    denial = next(
        message for message in messages
        if message.get("role") == "tool_result" and message.get("toolName") == "run_command"
    )
    assert denial.get("isError") is False
    assert not (tmp_path / "stock.py").exists()
    assert handoff_blocks_tool(
        "run_command", {"command": "pytest", "args": ["-q"]},
    ) is False


def test_failed_workflow_call_unlocks_a_solo_edit(tmp_path: Path) -> None:
    def reject(_input, _context):
        return ToolResult(ok=False, output="workflow rejected")

    task = ToolDefinition(
        name="task", description="workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=reject,
    )
    script = Script([
        AgentStep(type="tool_calls", calls=[{
            "id": "1", "toolName": "task",
            "input": {"agent_type": "workflow", "prompt": "implement", "write_paths": ["stock.py"]},
        }]),
        _write("2", "x = 1\n"),
        _final(),
    ])
    messages = _turn(tmp_path, script, tools=ToolRegistry([task, write_file_tool]))
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "workflow rejected" in blob
    handoff = next(
        message["content"] for message in messages
        if message.get("role") == "tool_result" and message.get("toolName") == "task"
    )
    assert handoff.startswith("=== SCHEDULER HANDOFF v1 ===")
    assert '"ran":false' in handoff and '"ok":false' in handoff
    assert '"scheduler":"central"' in handoff
    assert handoff.index("Finish the change yourself") < handoff.index("workflow rejected")
    assert "Finish the change yourself" in blob
    assert "stock.py" in blob and "test_public.py" in blob
    assert "Do not call task(workflow) again" in blob
    assert "Please retry" not in blob
    assert "unexpected error" not in blob.lower()
    assert script.calls == 3
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_a_later_what_is_does_not_make_an_imperative_spec_read_only() -> None:
    spec = (
        "Inventory hold and release.\n"
        "Implement both operations in stock.py and test_public.py.\n"
        "The store must not change what is stored for an unknown sku.\n"
    )
    decision = decide_delegation(
        spec, routing_enabled=True, implementation_owned=False,
    )
    assert decision.mode == "workflow"
    assert decision.reason == "multiple source files"
    assert decision.write_paths == ("stock.py", "test_public.py")
    question = decide_delegation(
        "What is stored in stock.py?",
        routing_enabled=True, implementation_owned=False,
    )
    assert question.mode == "solo" and question.reason == "read-only question"


def test_chinese_prompts_use_the_same_split() -> None:
    multi = decide_delegation(
        "实现 hold 和 release，修改 stock.py 和 test_public.py。",
        routing_enabled=True, implementation_owned=False,
    )
    assert multi.mode == "workflow"
    assert multi.write_paths == ("stock.py", "test_public.py")

    named = decide_delegation(
        "请更新「模块/库存.py」和 test_public.py。",
        routing_enabled=True, implementation_owned=False,
    )
    assert named.mode == "workflow"
    assert named.write_paths == ("模块/库存.py", "test_public.py")

    trivial = decide_delegation(
        "只改一个文件：修复 stock.py 里的一个函数。",
        routing_enabled=True, implementation_owned=False,
    )
    assert trivial.mode == "solo" and trivial.reason == "trivial edit"

    typo = decide_delegation(
        "修复 stock.py 和 test_public.py 的错别字。",
        routing_enabled=True, implementation_owned=False,
    )
    assert typo.mode == "solo"

    explained = decide_delegation(
        "不要改 stock.py 和 test_public.py，只解释区别。",
        routing_enabled=True, implementation_owned=False,
    )
    assert explained.mode == "solo" and explained.reason == "read-only question"

    question = decide_delegation(
        "怎么改 stock.py 和 test_public.py？",
        routing_enabled=True, implementation_owned=False,
    )
    assert question.mode == "solo"

    survey = decide_delegation(
        "整个代码库是怎么组织的？",
        routing_enabled=True, implementation_owned=False,
    )
    assert survey.mode == "explore"


def test_chinese_multi_file_request_blocks_the_first_edit(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        messages=[{"role": "user", "content": "实现 hold，修改 stock.py 和 test_public.py。"}],
        runtime=GATED,
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" in blob
    assert not (tmp_path / "stock.py").exists()


def test_workflow_call_keeps_every_named_file(tmp_path: Path) -> None:
    seen = []

    def accept(input_data, _context):
        seen.append(dict(input_data))
        return ToolResult(ok=True, output="workflow accepted")

    task = ToolDefinition(
        name="task", description="workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=accept,
    )
    messages = _turn(
        tmp_path,
        Script([
            _write("1", "before\n"),
            AgentStep(type="tool_calls", calls=[{
                "id": "2", "toolName": "task",
                "input": {
                    "agent_type": "workflow",
                    "prompt": "implement hold and release",
                    "write_paths": ["stock.py"],
                },
            }]),
            _write("3", "after\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
        max_steps=5,
        runtime=GATED,
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "stay blocked until this workflow call returns" in blob
    assert seen[0]["write_paths"] == ["stock.py", "test_public.py"]
    assert (tmp_path / "stock.py").read_text() == "after\n"


def test_named_paths_fill_an_explicit_workflow_request() -> None:
    payload = {
        "agent_type": "workflow",
        "workflow_request": {"scope": {"writePaths": ["stock.py"]}},
    }
    complete_workflow_write_paths(payload, ("stock.py", "test_public.py"))
    assert payload["workflow_request"]["scope"]["writePaths"] == [
        "stock.py", "test_public.py",
    ]
    assert "write_paths" not in payload


def test_single_file_edit_is_not_coached(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        messages=[{"role": "user", "content": SINGLE}],
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_routing_off_keeps_the_multi_file_edit_with_the_parent(tmp_path: Path) -> None:
    _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        runtime={"subagentRoutingEnabled": False},
    )
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_a_workflow_handoff_clears_the_coach(tmp_path: Path) -> None:
    def accept(_input, _context):
        return ToolResult(ok=True, output="workflow accepted")

    task = ToolDefinition(
        name="task",
        description="workflow",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=accept,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1",
                "toolName": "task",
                "input": {"agent_type": "workflow", "prompt": "implement hold"},
            }]),
            _write("2", "x = 1\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "workflow accepted" in blob
    assert "delegate it before editing" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_plan_or_explore_does_not_replace_the_workflow_handoff(tmp_path: Path) -> None:
    def reject(_input, _context):
        raise AssertionError("explore must not start for a change that already has its files")

    task = ToolDefinition(
        name="task",
        description="explore",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=reject,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1",
                "toolName": "task",
                "input": {"agent_type": "explore", "prompt": "read both files"},
            }]),
            _write("2", "x = 1\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
        runtime=GATED,
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert blob.count("delegate it before editing") == 2
    assert not (tmp_path / "stock.py").exists()


def test_a_read_only_question_naming_two_files_is_not_coached(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        messages=[{"role": "user", "content": "What do stock.py and test_public.py do?"}],
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_single_file_redesign_is_coached_toward_a_reviewer(tmp_path: Path) -> None:
    messages = _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        runtime=GATED,
        messages=[{
            "role": "user",
            "content": (
                "Redesign hold and release in stock.py: reserve then commit, "
                "and keep the public invariants."
            ),
        }],
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "separate reviewer" in blob
    assert "stock.py" in blob
    assert not (tmp_path / "stock.py").exists()


def test_offline_demo_script_prints_solo_and_workflow() -> None:
    from scripts.demo_adaptive_delegation import evaluate

    assert evaluate() == []
    script = Path(__file__).resolve().parents[1] / "scripts" / "demo_adaptive_delegation.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "FAIL" not in completed.stdout
    assert "\tsolo\t" in completed.stdout
    assert "\tworkflow\t" in completed.stdout
    assert "no model call" in completed.stdout


def test_host_owned_implementation_still_writes_when_routing_is_on(tmp_path: Path) -> None:
    from minicode.deterministic_verifier import VerificationCheck
    from minicode.implementation_execution import ImplementationSpec

    spec = ImplementationSpec(
        ("stock.py",),
        (VerificationCheck(
            "public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20,
        ),),
        "Implement hold and release in stock.py and test_public.py.",
    )
    run_agent_turn(
        model=Script([_write("1", "owned\n"), _final()]),
        tools=ToolRegistry([write_file_tool]),
        messages=[{"role": "user", "content": spec.objective}],
        cwd=str(tmp_path),
        # Host-owned checks run python -m pytest. Bypass command approval so
        # a non-interactive runner is not asked to open a TTY.
        permissions=PermissionManager(str(tmp_path), auto_mode=PermissionMode.BYPASS),
        runtime={"subagentRoutingEnabled": True},
        implementation_task=True,
        implementation_spec=spec,
        enable_implementation_phases=False,
        enable_work_chain=False,
        defer_curator_maintenance=True,
        max_steps=3,
    )
    assert (tmp_path / "stock.py").read_text() == "owned\n"


def _rendered_scheduler_trace() -> tuple[str, str]:
    from minicode.central_scheduler import (
        CentralScheduler,
        DispatchStage,
        StageResult,
        TaskKind,
        TaskProfile,
        TaskRisk,
    )
    from minicode.scheduler_trace import project_scheduler_trace, render_scheduler_trace

    scheduler = CentralScheduler()
    profile = TaskProfile(
        task_id="task_parent_handoff",
        kind=TaskKind.CHANGE,
        risk=TaskRisk.LOW,
        scope_clear=True,
        acceptance_executable=True,
    )
    plan = scheduler.plan(profile)
    transitions: list = []

    def run_worker(assignment):
        if assignment.stage is DispatchStage.EXECUTOR:
            return StageResult(
                status="completed",
                artifact_id="artifact_executor",
                patch_digest="sha256:parent-handoff",
            )
        if assignment.stage is DispatchStage.REVIEWER:
            return StageResult(
                status="completed",
                artifact_id="artifact_review",
                patch_digest="sha256:parent-handoff",
                approved=True,
            )
        return StageResult(status="completed", artifact_id="artifact_other")

    def run_verifier(_assignment):
        return StageResult(
            status="completed",
            artifact_id="verification_parent",
            patch_digest="sha256:parent-handoff",
        )

    outcome = scheduler.execute(
        profile,
        workflow_id="wf_parent_handoff",
        run_worker=run_worker,
        run_verifier=run_verifier,
        commit=lambda _request: True,
        on_transition=transitions.append,
    )
    trace = project_scheduler_trace(plan, outcome, transitions)
    return str(plan.rule_id), render_scheduler_trace(trace)


def test_parent_workflow_result_carries_a_compact_handoff(tmp_path: Path) -> None:
    from minicode.context_compactor import ToolResultBudgetManager
    from minicode.scheduler_trace import extract_scheduler_trace

    rule_id, rendered = _rendered_scheduler_trace()

    def accept(_input, _context):
        return ToolResult(
            ok=True,
            output="executor wrote the files\n" + ("detail\n" * 40) + "\n" + rendered,
        )

    task = ToolDefinition(
        name="task", description="workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=accept,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1", "toolName": "task",
                "input": {
                    "agent_type": "workflow",
                    "prompt": "implement hold and release",
                    "write_paths": ["stock.py", "test_public.py"],
                },
            }]),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
    )
    content = next(
        message["content"] for message in messages
        if message.get("role") == "tool_result" and message.get("toolName") == "task"
    )
    assert content.startswith("=== SCHEDULER HANDOFF v1 ===")
    assert f'"ruleId":"{rule_id}"' in content
    assert '"ran":true' in content and '"ok":true' in content
    assert '"status":"completed"' in content
    assert '"scheduler":"central"' in content
    assert extract_scheduler_trace(content)["ruleId"] == rule_id

    huge = content + ("\n" + ("x" * 200)) * 30
    previewed, _saved = ToolResultBudgetManager(tmp_path / "preview").check_and_replace([
        {"role": "tool_result", "toolName": "task", "toolUseId": "1", "content": huge},
    ])
    preview = previewed[0]["content"]
    assert "=== SCHEDULER HANDOFF v1 ===" in preview
    assert f'"ruleId":"{rule_id}"' in preview
    assert '"ran":true' in preview


def test_failed_workflow_handoff_keeps_the_rule_beside_the_solo_note(tmp_path: Path) -> None:
    rule_id, rendered = _rendered_scheduler_trace()

    def reject(_input, _context):
        return ToolResult(ok=False, output="workflow rejected\n\n" + rendered)

    task = ToolDefinition(
        name="task", description="workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=reject,
    )
    messages = _turn(
        tmp_path,
        Script([
            AgentStep(type="tool_calls", calls=[{
                "id": "1", "toolName": "task",
                "input": {"agent_type": "workflow", "prompt": "implement", "write_paths": ["stock.py"]},
            }]),
            _write("2", "x = 1\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
    )
    content = next(
        message["content"] for message in messages
        if message.get("role") == "tool_result" and message.get("toolName") == "task"
    )
    assert content.startswith("=== SCHEDULER HANDOFF v1 ===")
    head, _rest = content.split("\n\n", 1)
    assert f'"ruleId":"{rule_id}"' in head
    assert '"ran":true' in head and '"ok":false' in head
    assert content.index("=== SCHEDULER HANDOFF v1 ===") < content.index("Finish the change yourself")
    assert content.index("Finish the change yourself") < content.index("workflow rejected")
    assert content.index("Finish the change yourself") < content.index("=== SCHEDULER TRACE v1 ===")
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_nested_worker_is_not_coached(tmp_path: Path) -> None:
    _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        agent_depth=1,
    )
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


def test_default_gate_lets_the_parent_write(tmp_path: Path) -> None:
    messages = _turn(tmp_path, Script([_write("1", "x = 1\n"), _final()]))
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "delegate it before editing" not in blob
    assert "stay blocked" not in blob
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"
    note = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=False,
    )
    assert note.startswith("Hint:")
    assert "stock.py, test_public.py" in note
    assert "You choose whether to delegate." in note


def test_default_gate_still_autofills_and_unlocks_after_failure(tmp_path: Path) -> None:
    seen = []

    def reject(input_data, _context):
        seen.append(dict(input_data))
        return ToolResult(ok=False, output="workflow rejected")

    task = ToolDefinition(
        name="task", description="workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=reject,
    )
    messages = _turn(
        tmp_path,
        Script([
            _write("1", "before\n"),
            AgentStep(type="tool_calls", calls=[{
                "id": "2", "toolName": "task",
                "input": {
                    "agent_type": "workflow",
                    "prompt": "implement hold and release",
                    "write_paths": ["stock.py"],
                },
            }]),
            _write("3", "after\n"),
            _final(),
        ]),
        tools=ToolRegistry([task, write_file_tool]),
        max_steps=5,
    )
    assert (tmp_path / "stock.py").read_text() == "after\n"
    assert seen[0]["write_paths"] == ["stock.py", "test_public.py"]
    assert "Finish the change yourself" in "\n".join(
        str(message.get("content")) for message in messages
    )


def test_free_choice_bakeoff_protocol_is_offline() -> None:
    from scripts.demo_free_choice_delegation import evaluate

    assert evaluate() == []


def test_ci_quarantine_keeps_delegation_and_behavior_review_tests() -> None:
    listed = Path(__file__).resolve().parents[1].joinpath("tests", "ci_known_failures.txt").read_text(encoding="utf-8")
    for name in (
        "test_adaptive_delegation.py",
        "test_behavior_review_handoff.py",
        "test_memory_portable_publish.py",
    ):
        assert name not in listed
