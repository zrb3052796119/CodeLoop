"""Type definitions for Multi-Agent Orchestration system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable


class MessageType(Enum):
    """Types of messages between agents."""
    TASK = "task"
    RESULT = "result"
    QUERY = "query"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    ERROR = "error"
    STATUS = "status"


class AgentStatus(Enum):
    """Status of an agent execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class AgentRole:
    """Definition of an agent's role in a multi-agent system."""
    name: str
    description: str
    expertise: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    system_prompt: str = ""
    max_steps: int = 30
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "expertise": self.expertise,
            "tools": self.tools,
            "responsibilities": self.responsibilities,
            "max_steps": self.max_steps,
        }


@dataclass
class AgentMessage:
    """Message passed between agents."""
    msg_type: MessageType
    from_agent: str
    to_agent: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: __import__('time').time())
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.msg_type.value,
            "from": self.from_agent,
            "to": self.to_agent,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentResult:
    """Result of an agent's execution."""
    agent_id: str
    role: str
    status: AgentStatus
    output: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    token_usage: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        return self.status == AgentStatus.COMPLETED and not self.error


@dataclass
class ExecutionTrace:
    """Trace of a multi-agent execution."""
    task: str
    pattern: str
    roles: list[AgentRole] = field(default_factory=list)
    results: list[AgentResult] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    adjustments: list[WorkflowAdjustment] = field(default_factory=list)
    
    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        if self.start_time:
            return (__import__('time').time() - self.start_time) * 1000
        return 0.0
    
    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        successful = sum(1 for r in self.results if r.success)
        return successful / len(self.results)
    
    @property
    def total_tokens(self) -> int:
        return sum(r.token_usage for r in self.results)


@dataclass
class WorkflowAdjustment:
    """Adjustment made to a workflow during execution."""
    reason: str
    action: str
    affected_agents: list[str] = field(default_factory=list)
    new_roles: list[AgentRole] = field(default_factory=list)
    timestamp: float = field(default_factory=lambda: __import__('time').time())


@dataclass
class MemoryEvent:
    """Event recorded in shared memory."""
    key: str
    value: Any
    agent_id: str
    timestamp: float
    operation: str  # "write", "read", "delete"
