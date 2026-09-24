"""Prompt-layer guidance markers for task-tool delegation heuristics."""

from minicode.subagent_types import AGENT_TYPES
from minicode.tools.task import task_tool


def test_task_tool_description_carries_delegation_heuristics() -> None:
    text = task_tool.description
    assert "conclusion, not the file dumps" in text
    assert "Once you have delegated a search, do not also run it yourself" in text
    assert "single-fact lookup" in text
    assert "self-contained" in text
    assert "'medium' or 'very thorough'" in text


def test_system_prompt_forbids_duplicating_delegated_search() -> None:
    from minicode.prompt import build_system_prompt

    text = build_system_prompt("/tmp", [], {"skills": []})
    assert "do not repeat that search yourself" in text


def test_explore_system_prompt_defines_breadth_levels() -> None:
    text = AGENT_TYPES["explore"]["system_prompt"]
    assert "very thorough" in text
    assert "alternate naming" in text
    assert "medium" in text
