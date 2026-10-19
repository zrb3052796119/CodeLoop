"""Keep existing phase/native precedence when only call count is bounded."""
from pathlib import Path
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from test_reviewer_result_submission import _bound


def test_phase_controls_still_precede_native_with_unlimited_tokens(tmp_path: Path) -> None:
    _, store, channel, submit, _ = _bound()
    tools = ToolRegistry([edit_file_tool, submit])
    budget = AgentTurnBudget(max_model_calls=1)
    observed=[]

    class Model:
        def next(self, messages, **options):
            observed.append(([tool.name for tool in options['tools_override'].list()],
                options['max_output_tokens_override']))
            return AgentStep(type='assistant', kind='final', content='The assigned work remains incomplete.',
                usage=ModelUsage(input_tokens=200,output_tokens=8,source='provider'))

    try:
        run_agent_turn(model=Model(),tools=tools,
            messages=[{'role':'user','content':'Implement the assigned change.'}],cwd=str(tmp_path),
            runtime={'model':'local-scripted','maxOutputTokens':256}, agent_budget=budget,
            max_steps=1,enable_work_chain=False,enable_bounded_completion=False,
            enable_implementation_phases=True,bounded_implementation_phases=True,
            implementation_task=True,implementation_analysis_complete=True,
            host_native_result_tool=submit,subagent_mailbox=channel)
        assert len(observed)==1
        assert 'edit_file' in observed[0][0], observed
        assert observed[0][1]==256, observed
        assert budget.snapshot().used_model_calls==1
        assert budget.snapshot().used_total_tokens==208
    finally:
        channel.close()
