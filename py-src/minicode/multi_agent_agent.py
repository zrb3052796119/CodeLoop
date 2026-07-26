"""Multi-Agent Agent Wrapper for MiniCode.

Wraps agent_loop.run_agent_turn as a multi-agent compatible agent.
Each sub-agent gets its own isolated context via ContextSandbox.
"""

from __future__ import annotations

from minicode.agent_loop import run_agent_turn
from minicode.context_isolation import AgentContext, ContextSandbox
from minicode.logging_config import get_logger
from minicode.multi_agent.message_queue import MessageQueue
from minicode.multi_agent.shared_memory import SharedMemory
from minicode.multi_agent.types import AgentRole
from minicode.tooling import ToolRegistry
from minicode.types import ChatMessage, ModelAdapter

logger = get_logger("multi_agent_agent")


class MultiAgentWrapper:
    """Wraps a single agent_loop run as a multi-agent compatible agent."""

    def __init__(
        self,
        agent_id: str,
        role: AgentRole,
        model: ModelAdapter,
        tools: ToolRegistry,
        shared_memory: SharedMemory,
        message_queue: MessageQueue,
        context_sandbox: ContextSandbox,
    ):
        self.agent_id = agent_id
        self.role = role
        self.model = model
        self.tools = tools
        self.shared_memory = shared_memory
        self.message_queue = message_queue
        self.context_sandbox = context_sandbox
        self.context = context_sandbox.create_context(
            agent_type=role.name,
            allowed_tools=role.tools or [],
            max_tokens=40000,
        )

    def run(self, task: str) -> str:
        """Execute task using agent_loop.run_agent_turn."""
        system_prompt = self._build_system_prompt()
        shared_context = self._read_shared_context()

        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{shared_context}\n\nTask: {task}"},
        ]

        try:
            result_messages = run_agent_turn(
                model=self.model,
                tools=self.tools,
                messages=messages,
                cwd=self.context.cwd,
                max_steps=self.role.max_steps,
            )

            final_output = self._extract_final_output(result_messages)

            self.shared_memory.write(
                f"result_{self.agent_id}",
                {"task": task, "output": final_output, "role": self.role.name},
                self.agent_id,
            )

            return final_output
        except Exception as e:
            error_msg = f"Agent {self.agent_id} failed: {e}"
            logger.error(error_msg)
            self.shared_memory.write(
                f"error_{self.agent_id}",
                {"task": task, "error": str(e)},
                self.agent_id,
            )
            return error_msg

    def _build_system_prompt(self) -> str:
        """Build system prompt from AgentRole."""
        parts = [
            f"You are a {self.role.name}.",
            f"Description: {self.role.description}",
        ]
        if self.role.expertise:
            parts.append(f"Expertise: {', '.join(self.role.expertise)}")
        if self.role.responsibilities:
            parts.append(f"Responsibilities: {', '.join(self.role.responsibilities)}")
        if self.role.system_prompt:
            parts.append(f"\n{self.role.system_prompt}")
        parts.append("\nWork independently and return your findings.")
        return "\n".join(parts)

    def _read_shared_context(self) -> str:
        """Read relevant context from shared memory."""
        context_parts = []

        # Read previous agent results
        for key in self.shared_memory.list_keys():
            if key.startswith("result_"):
                data = self.shared_memory.read(key)
                if isinstance(data, dict):
                    agent = data.get("role", "unknown")
                    output = data.get("output", "")
                    context_parts.append(f"Previous agent ({agent}):\n{output[:500]}")

        if context_parts:
            return "Shared Context:\n" + "\n\n".join(context_parts)
        return ""

    def _extract_final_output(self, messages: list[ChatMessage]) -> str:
        """Extract final assistant output from messages."""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return str(msg["content"])
        return "No output generated."
