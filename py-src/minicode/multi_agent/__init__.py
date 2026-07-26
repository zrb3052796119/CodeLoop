"""Multi-Agent Orchestration system for MiniCode.

Provides collaborative multi-agent execution with support for:
- Sequential, Parallel, Hierarchical, Consensus, and Tool-Mediated patterns
- Dynamic role generation based on task description
- Shared memory and message queue for inter-agent communication
- Adaptive workflow adjustment based on execution progress
"""

from __future__ import annotations

from minicode.multi_agent.types import (
    AgentRole,
    AgentMessage,
    AgentResult,
    ExecutionTrace,
    WorkflowAdjustment,
)
from minicode.multi_agent.orchestrator import Orchestrator
from minicode.multi_agent.patterns import (
    SequentialPattern,
    ParallelPattern,
    HierarchicalPattern,
    ConsensusPattern,
    ToolMediatedPattern,
)
from minicode.multi_agent.role_analyzer import RoleAnalyzer
from minicode.multi_agent.shared_memory import SharedMemory
from minicode.multi_agent.message_queue import MessageQueue
from minicode.multi_agent.adaptive_workflow import AdaptiveWorkflow

__all__ = [
    "AgentRole",
    "AgentMessage",
    "AgentResult",
    "ExecutionTrace",
    "WorkflowAdjustment",
    "Orchestrator",
    "SequentialPattern",
    "ParallelPattern",
    "HierarchicalPattern",
    "ConsensusPattern",
    "ToolMediatedPattern",
    "RoleAnalyzer",
    "SharedMemory",
    "MessageQueue",
    "AdaptiveWorkflow",
]
