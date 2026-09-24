"""Offline review probe: child commit versus completion of the parent goal."""
import json
import tempfile
import threading

from minicode.agent_loop import run_agent_turn
from minicode.agent_runtime import final_response_text
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelAdapter

class ScriptedModel(ModelAdapter):
    def __init__(self):
        self.calls = 0

    def next(self, messages, on_stream_chunk=None):
        self.calls += 1
        if self.calls == 1:
            return AgentStep(type="tool_calls", calls=[
                {"id": "commit_first", "toolName": "workflow_fixture", "input": {}}
            ])
        return AgentStep(type="assistant", content="Synthesize exploration and continue second task.")


def main():
    release = threading.Event()
    started = threading.Event()
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    def exploration(_identity, cancellation):
        started.set()
        release.wait(3)
        return ToolResult(ok=True, output="Independent findings still need synthesis.")
    spawned = lifecycle.spawn(agent_type="explore", runner=exploration)
    assert started.wait(1)
    model = ScriptedModel()
    authority = "first workflow committed; changesCommitted=true"
    registry = ToolRegistry([ToolDefinition(
        name="workflow_fixture", description="Return the production committed-result contract.",
        input_schema={"type": "object"}, validator=lambda value: value,
        run=lambda value, context: ToolResult(ok=True, output=authority, _must_deliver=True),
    )])
    try:
        with tempfile.TemporaryDirectory(prefix="minicode-parent-review-") as workdir:
            messages = run_agent_turn(
                model=model, tools=registry,
                messages=[{"role": "system", "content": "Review fixture."},
                          {"role": "user", "content": "Finish first task, then second task, and synthesize the parallel investigation."}],
                cwd=workdir, enable_work_chain=False, subagent_lifecycle=lifecycle,
            )
        barrier = lifecycle.finalization_barrier()
        projected_history = final_response_text([
            {"role": "assistant", "content": "Previous turn answer."},
            {"role": "user", "content": "Now implement the next change."},
            {"role": "assistant_tool_call", "toolName": "task", "toolUseId": "commit_first"},
            {"role": "tool_result", "toolName": "task", "toolUseId": "commit_first", "content": authority},
        ])
        print(json.dumps({
            "parent_model_calls": model.calls,
            "parent_returned_role": messages[-1]["role"],
            "parent_returned_text": messages[-1]["content"],
            "async_barrier_ready_at_parent_return": barrier["ready"],
            "pending_subagent_count": len(barrier["pending"]),
            "multi_turn_final_response_projection": projected_history,
        }, indent=2))
    finally:
        release.set()
        lifecycle.shutdown()

if __name__ == "__main__":
    main()
