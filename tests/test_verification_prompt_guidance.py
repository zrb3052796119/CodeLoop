"""Prompt contract for bounded, honest coding verification.

These checks cover guidance delivered to the model, not model compliance.
"""

from pathlib import Path

import pytest

from minicode.prompt import build_system_prompt
from minicode.prompt_pipeline import SYSTEM_PROMPT_DYNAMIC_BOUNDARY


@pytest.mark.parametrize("interactive", [True, False])
def test_verification_guidance_is_present_in_each_execution_mode(
    tmp_path: Path, interactive: bool
) -> None:
    prompt = build_system_prompt(
        str(tmp_path), extras={"user_interaction_available": interactive}
    )
    static, dynamic = prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, 1)

    assert "## Coding verification scope" in static
    assert "## Coding verification scope" not in dynamic


def test_focused_checks_preserve_requested_full_suite_scope(tmp_path: Path) -> None:
    prompt = build_system_prompt(str(tmp_path))

    assert "tests relevant to the changed behavior" in prompt
    assert "applicable lint/type checks" in prompt
    assert "Expand verification for a concrete unresolved risk" in prompt
    assert "explicit project/user requirement" in prompt
    assert "Honor user-requested full-suite runs; focused checks do not replace them" in prompt


def test_missing_dependencies_require_environment_inspection_and_bounded_retry(
    tmp_path: Path,
) -> None:
    prompt = build_system_prompt(str(tmp_path))

    assert "inspect the project's development dependencies and configured environment first" in prompt
    assert "When authorized, make one justified dependency/environment repair" in prompt
    assert "retry the blocked check" in prompt
    assert "report that check as unverified with the blocker" in prompt
    assert "continue independent work" in prompt


def test_passing_results_cannot_be_manufactured_by_exclusions_or_repeated_checks(
    tmp_path: Path,
) -> None:
    prompt = build_system_prompt(str(tmp_path))

    assert "Do not repeatedly add --ignore, skip tests, or narrow collection" in prompt
    assert "Report any exclusions and their effect on the requested verification scope" in prompt
    assert "Once a check passes, do not repeat it without new changes" in prompt
    assert "relevant environment change" in prompt
    assert "Choose verification tools and their order to fit the task" in prompt


def test_final_summary_distinguishes_work_evidence_and_remaining_gaps(
    tmp_path: Path,
) -> None:
    prompt = build_system_prompt(str(tmp_path))

    assert "Before the final answer, summarize completed work" in prompt
    assert "verification evidence (commands, scope, and results)" in prompt
    assert "anything incomplete or unverified with its reason" in prompt
    assert "Do not claim full verification from partial checks" in prompt
