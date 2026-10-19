"""Parent delegates collaborative edits and keeps trivial edits."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from minicode.agent_loop import run_agent_turn
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
    note = delegation_prompt_note(MULTI, routing_enabled=True, implementation_owned=False)
    guided = build_system_prompt("/tmp", [], {"delegation_note": note})
    assert "stock.py, test_public.py" in guided
    assert "Do not also call plan or explore" in guided
    plain = build_system_prompt("/tmp", [], {"skills": []})
    assert "This turn's delegation" not in plain
    assert "single-file or single-function" in plain
    assert "call agent_type='workflow' directly" in plain
    assert "Do not routinely run Plan first" in plain
    assert "before the parent edits those files" in task_tool.description
    assert "creates or modifies files" in task_tool.description
    assert delegation_prompt_note(
        SINGLE, routing_enabled=True, implementation_owned=False,
    ).startswith("This request is a small local change")
    assert "read-only question" in delegation_prompt_note(
        "What do stock.py and test_public.py do?",
        routing_enabled=True, implementation_owned=False,
    )
    assert "single-file redesign" in delegation_prompt_note(
        "Redesign hold and release in stock.py: reserve then commit, "
        "and keep the public invariants.",
        routing_enabled=True, implementation_owned=False,
    )
    assert "single-file redesign" in task_tool.description
    assert "read-only question" in plain
    owned = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=True,
    )
    assert owned.startswith("This turn is a host-owned implementation")
    assert "Do not call task." in owned


def test_routed_parent_is_coached_off_the_first_multi_file_edit(tmp_path: Path) -> None:
    messages = _turn(tmp_path, Script([_write("1", "x = 1\n"), _final()]))
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
    ]))
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
    )
    blob = "\n".join(str(message.get("content")) for message in messages)
    assert "stay blocked until this workflow call returns" in blob
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
    assert "Finish the change yourself" in blob
    assert "stock.py" in blob and "test_public.py" in blob
    assert "Do not call task(workflow) again" in blob
    assert script.calls == 3
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"


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
        runtime={"subagentRoutingEnabled": True},
        implementation_task=True,
        implementation_spec=spec,
        enable_implementation_phases=False,
        enable_work_chain=False,
        defer_curator_maintenance=True,
        max_steps=3,
    )
    assert (tmp_path / "stock.py").read_text() == "owned\n"


def test_nested_worker_is_not_coached(tmp_path: Path) -> None:
    _turn(
        tmp_path,
        Script([_write("1", "x = 1\n"), _final()]),
        agent_depth=1,
    )
    assert (tmp_path / "stock.py").read_text() == "x = 1\n"
