"""Core orchestrator for multi-agent systems.

Coordinates agent execution using various patterns and manages
the overall multi-agent workflow.
"""

from __future__ import annotations

import random
import time
from typing import Any

from minicode.multi_agent.types import (
    AgentRole,
    ExecutionTrace,
    AgentResult,
    AgentStatus,
)
from minicode.multi_agent.patterns import (
    OrchestrationPattern,
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


class Orchestrator:
    """Orchestrates multi-agent execution.
    
    Manages the overall workflow:
    1. Analyzes task and generates roles
    2. Selects appropriate pattern
    3. Executes agents
    4. Monitors and adapts
    5. Returns results
    """
    
    PATTERNS = {
        "sequential": SequentialPattern,
        "parallel": ParallelPattern,
        "hierarchical": HierarchicalPattern,
        "consensus": ConsensusPattern,
        "tool_mediated": ToolMediatedPattern,
    }
    
    def __init__(
        self,
        role_analyzer: RoleAnalyzer | None = None,
        adaptive_workflow: AdaptiveWorkflow | None = None,
        shared_memory: SharedMemory | None = None,
        message_queue: MessageQueue | None = None,
        experiment_mode: bool = False,
        seed: int = 42,
    ):
        self.role_analyzer = role_analyzer or RoleAnalyzer()
        self.adaptive_workflow = adaptive_workflow or AdaptiveWorkflow()
        self.shared_memory = shared_memory or SharedMemory()
        self.message_queue = message_queue or MessageQueue()
        self._agent_factory: callable | None = None
        self.experiment_mode = experiment_mode
        self.seed = seed
        self._metrics_hook: callable | None = None
        if experiment_mode:
            random.seed(seed)
    
    def set_agent_factory(self, factory: callable) -> None:
        """Set the agent factory function.
        
        Args:
            factory: Function that creates agent instances
        """
        self._agent_factory = factory
    
    def set_metrics_hook(self, hook: callable) -> None:
        """Set a metrics collection hook for experiment mode.
        
        Args:
            hook: Callable that receives (event_name: str, data: dict)
        """
        self._metrics_hook = hook
    
    def _emit_metric(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit a metric event if hook is set."""
        if self._metrics_hook:
            try:
                self._metrics_hook(event_name, data)
            except Exception:
                pass
    
    def execute(
        self,
        task: str,
        pattern: str = "sequential",
        roles: list[AgentRole] | None = None,
        max_roles: int = 3,
        adaptive: bool = True,
        max_adjustments: int = 3,
    ) -> ExecutionTrace:
        """Execute a multi-agent task.
        
        Args:
            task: The task description
            pattern: Orchestration pattern name
            roles: Pre-defined roles (None to auto-generate)
            max_roles: Maximum number of roles to generate
            adaptive: Enable adaptive workflow
            max_adjustments: Maximum workflow adjustments
            
        Returns:
            Execution trace
        """
        exec_start = time.time()
        
        if self._agent_factory is None:
            raise RuntimeError("Agent factory not set. Call set_agent_factory() first.")
        
        self._emit_metric("orchestrator.execute.start", {
            "pattern": pattern,
            "max_roles": max_roles,
            "adaptive": adaptive,
            "experiment_mode": self.experiment_mode,
            "seed": self.seed,
        })
        
        # Generate roles if not provided
        if roles is None:
            roles = self.role_analyzer.analyze(task, max_roles=max_roles)
        
        self._emit_metric("orchestrator.roles.generated", {
            "role_count": len(roles),
            "role_names": [r.name for r in roles],
        })
        
        # Get pattern class
        pattern_cls = self.PATTERNS.get(pattern)
        if pattern_cls is None:
            raise ValueError(f"Unknown pattern: {pattern}. Available: {list(self.PATTERNS.keys())}")
        
        # Create pattern instance with shared resources
        pattern_instance = pattern_cls(
            shared_memory=self.shared_memory,
            message_queue=self.message_queue,
        )
        pattern_instance.set_metrics_hook(self._metrics_hook)
        
        # Execute with adaptive loop
        trace = pattern_instance.execute(task, roles, self._agent_factory)
        
        if adaptive:
            trace = self._adaptive_loop(
                trace, task, pattern_instance, roles,
                max_adjustments,
            )
        
        exec_duration = time.time() - exec_start
        self._emit_metric("orchestrator.execute.complete", {
            "duration_seconds": exec_duration,
            "agent_count": len(trace.results),
            "success_rate": trace.success_rate,
            "total_tokens": trace.total_tokens,
            "adjustments": len(trace.adjustments),
        })
        
        return trace
    
    def _adaptive_loop(
        self,
        initial_trace: ExecutionTrace,
        task: str,
        pattern: OrchestrationPattern,
        roles: list[AgentRole],
        max_adjustments: int,
    ) -> ExecutionTrace:
        """Run adaptive adjustment loop.
        
        Args:
            initial_trace: Initial execution trace
            task: Original task
            pattern: Pattern instance
            roles: Current roles
            max_adjustments: Maximum adjustments
            
        Returns:
            Final execution trace
        """
        trace = initial_trace
        
        while self.adaptive_workflow.should_continue(trace, max_adjustments):
            adjustment = self.adaptive_workflow.monitor(trace)
            
            if adjustment is None:
                break
            
            trace.adjustments.append(adjustment)
            
            # Apply adjustment
            if adjustment.action == "add_specialist":
                new_roles = adjustment.new_roles
                if new_roles:
                    # Re-run with additional roles
                    combined_roles = roles + new_roles
                    new_trace = pattern.execute(task, combined_roles, self._agent_factory)
                    trace.results.extend(new_trace.results)
                    trace.end_time = new_trace.end_time
            
            elif adjustment.action == "reallocate_tasks":
                # Re-run failed tasks
                new_roles = self.adaptive_workflow.generate_next_roles(task, trace)
                if new_roles:
                    new_trace = pattern.execute(task, new_roles, self._agent_factory)
                    trace.results.extend(new_trace.results)
                    trace.end_time = new_trace.end_time
            
            elif adjustment.action == "insert_validation":
                # Add validation step
                validator_role = AgentRole(
                    name="ValidatorAgent",
                    description="Validates outputs and checks for errors",
                )
                validation_task = (
                    f"Validate the following outputs for errors and inconsistencies:\n\n"
                    + "\n\n".join(
                        f"--- {r.agent_id} ---\n{r.output}"
                        for r in trace.results
                        if r.success
                    )
                )
                validation_result = pattern._run_agent(
                    "validator", validator_role, validation_task, self._agent_factory,
                )
                trace.results.append(validation_result)
        
        return trace
    
    def get_final_output(self, trace: ExecutionTrace) -> str:
        """Extract final output from execution trace.
        
        Args:
            trace: Execution trace
            
        Returns:
            Final output string
        """
        if not trace.results:
            return "No results."
        
        # Try to get final aggregated output from shared memory
        final_output = self.shared_memory.read("final_output")
        if final_output:
            return str(final_output)
        
        # Fallback: concatenate successful results
        successful = [r for r in trace.results if r.success]
        if successful:
            return "\n\n".join(
                f"=== {r.agent_id} ({r.role}) ===\n{r.output}"
                for r in successful
            )
        
        # Last resort: return all results
        return "\n\n".join(
            f"=== {r.agent_id} ({r.role}) ===\n{r.output}"
            for r in trace.results
        )
    
    def get_pattern_names(self) -> list[str]:
        """Get available pattern names.

        Returns:
            List of pattern names
        """
        return list(self.PATTERNS.keys())


def create_minicode_orchestrator(
    model: Any,
    tools: Any,
    cwd: str = ".",
) -> Orchestrator:
    """Create an orchestrator pre-configured for MiniCode.

    Args:
        model: ModelAdapter instance
        tools: ToolRegistry instance
        cwd: Working directory

    Returns:
        Configured Orchestrator instance
    """
    from minicode.context_isolation import ContextSandbox
    from minicode.multi_agent_agent import MultiAgentWrapper

    orchestrator = Orchestrator()

    def agent_factory(agent_id: str, role: AgentRole,
                      shared_memory: SharedMemory, message_queue: MessageQueue):
        sandbox = ContextSandbox(total_token_budget=150000)
        return MultiAgentWrapper(
            agent_id=agent_id,
            role=role,
            model=model,
            tools=tools,
            shared_memory=shared_memory,
            message_queue=message_queue,
            context_sandbox=sandbox,
        )

    orchestrator.set_agent_factory(agent_factory)
    return orchestrator
