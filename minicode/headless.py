"""MiniCode Headless Runner — non-interactive, one-shot execution.

Inspired by Hermes Agent's headless mode for CI/CD pipelines and
automated workflows.

Usage:
  # Run a single prompt and exit
  python -m minicode.headless "帮我分析这个项目的结构"

  # Pipe input
  echo "解释这段代码" | python -m minicode.headless

  # In Docker
  docker compose run --rm headless "修复这个 bug"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from minicode.agent_runtime import (
    AgentRuntimeConfigError,
    create_agent_turn_runtime,
    prepare_conversation_messages,
)
from minicode.run_events import emit_skill_routing_safely
from minicode.run_lifecycle import observe_run

if TYPE_CHECKING:
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.run_lifecycle import JournalFactory


def run_headless(
    prompt: str | None = None,
    *,
    run_source: str = "headless",
    run_journal_factory: JournalFactory | None = None,
    run_observation_enabled: bool = True,
    mcp_current_state_registry: McpCurrentStateRegistry | None = None,
) -> str:
    """Run a single agent turn in headless mode and return the response.

    Args:
        prompt: The user message to send. If None, reads from stdin.

    Returns:
        The assistant's response text.
    """
    from minicode.logging_config import setup_logging, get_logger

    setup_logging(level=os.environ.get("MINI_CODE_LOG_LEVEL", "WARNING"))
    logger = get_logger("headless")

    # Read prompt from stdin if not provided
    if prompt is None:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        else:
            print("Usage: python -m minicode.headless <prompt>", file=sys.stderr)
            sys.exit(1)

    prompt = prompt.strip()
    if not prompt:
        print("Error: empty prompt", file=sys.stderr)
        sys.exit(1)

    cwd = str(Path.cwd())

    agent_runtime = None
    execution_started = False
    try:
        with observe_run(
            workspace=cwd,
            source=run_source,
            title=prompt,
            session_id=None,
            journal_factory=run_journal_factory,
            enabled=run_observation_enabled,
        ) as observation:
            try:
                # The shared runtime preserves the former memory_manager=memory_mgr
                # Agent-loop delegation while keeping entrypoint composition thin.
                agent_runtime = create_agent_turn_runtime(
                    workspace=Path(cwd),
                    prompt=prompt,
                    mcp_current_state_registry=mcp_current_state_registry,
                )
            except AgentRuntimeConfigError as error:
                print(f"Config error: {error.cause}", file=sys.stderr)
                sys.exit(1)
            emit_skill_routing_safely(observation, agent_runtime.skill_routing)
            messages = prepare_conversation_messages(
                [],
                system_prompt=agent_runtime.system_prompt,
                user_message=prompt,
            )

            try:
                logger.info("Headless task execution started.")
            except Exception:  # noqa: BLE001 - logging is non-essential
                pass
            execution_started = True
            result_messages = agent_runtime.execute(messages, observation)

            last_assistant = next(
                (m for m in reversed(result_messages) if m["role"] == "assistant"),
                None,
            )
            assistant_content = (
                last_assistant.get("content")
                if isinstance(last_assistant, dict)
                else None
            )
            observation.assistant_completed(
                content_present=(
                    isinstance(assistant_content, str) and bool(assistant_content)
                ),
                content_length=(
                    len(assistant_content) if isinstance(assistant_content, str) else 0
                ),
            )
            response = (
                last_assistant["content"] if last_assistant else "(no response)"
            )
        return response
    except Exception as exc:  # noqa: BLE001
        if not execution_started:
            raise
        try:
            logger.error("Headless task execution failed.")
        except Exception:  # noqa: BLE001 - logging is non-essential
            pass
        return f"Error: {exc}"
    finally:
        if agent_runtime is not None:
            agent_runtime.dispose()


def main() -> None:
    """CLI entry point for headless mode."""
    # Get prompt from command line args or stdin
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    response = run_headless(prompt)
    print(response)


if __name__ == "__main__":
    main()
