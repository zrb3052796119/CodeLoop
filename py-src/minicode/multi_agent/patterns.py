"""Orchestration patterns for multi-agent systems.

Implements five core patterns:
1. Sequential - Agents work one after another
2. Parallel - Agents work simultaneously
3. Hierarchical - Manager coordinates workers
4. Consensus - Agents debate and reach agreement
5. ToolMediated - Agents collaborate via shared tools
"""

from __future__ import annotations

import concurrent.futures
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from minicode.multi_agent.types import (
    AgentRole,
    AgentResult,
    AgentStatus,
    ExecutionTrace,
    MessageType,
    AgentMessage,
)
from minicode.multi_agent.shared_memory import SharedMemory
from minicode.multi_agent.message_queue import MessageQueue


class OrchestrationPattern(ABC):
    """Base class for orchestration patterns."""
    
    def __init__(self, shared_memory: SharedMemory | None = None, message_queue: MessageQueue | None = None):
        self.shared_memory = shared_memory or SharedMemory()
        self.message_queue = message_queue or MessageQueue()
        self._metrics_hook: callable | None = None
    
    def set_metrics_hook(self, hook: callable | None) -> None:
        """Set a metrics collection hook.
        
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
    
    @abstractmethod
    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
    ) -> ExecutionTrace:
        """Execute the pattern.
        
        Args:
            task: The main task description
            roles: List of agent roles
            agent_factory: Function to create an agent instance
            
        Returns:
            Execution trace
        """
        pass
    
    def _create_trace(self, task: str, pattern: str) -> ExecutionTrace:
        """Create a new execution trace."""
        return ExecutionTrace(
            task=task,
            pattern=pattern,
            start_time=time.time(),
        )
    
    def _run_agent(
        self,
        agent_id: str,
        role: AgentRole,
        task: str,
        agent_factory: callable,
    ) -> AgentResult:
        """Run a single agent.
        
        Args:
            agent_id: Unique agent identifier
            role: Agent role definition
            task: Task for this agent
            agent_factory: Factory function
            
        Returns:
            Agent execution result
        """
        start = time.time()
        try:
            # Register agent in message queue
            self.message_queue.register_agent(agent_id)
            
            # Create and run agent
            agent = agent_factory(agent_id, role, self.shared_memory, self.message_queue)
            output = agent.run(task)
            
            duration = (time.time() - start) * 1000
            return AgentResult(
                agent_id=agent_id,
                role=role.name,
                status=AgentStatus.COMPLETED,
                output=output,
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return AgentResult(
                agent_id=agent_id,
                role=role.name,
                status=AgentStatus.FAILED,
                output="",
                error=str(e),
                duration_ms=duration,
            )


class SequentialPattern(OrchestrationPattern):
    """Sequential execution pattern.

    Agents work one after another, each building on previous results.
    """

    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
    ) -> ExecutionTrace:
        trace = self._create_trace(task, "sequential")
        trace.roles = roles

        previous_output = task

        for i, role in enumerate(roles):
            agent_id = f"{role.name}_{i}"

            # Build task with previous context
            if i > 0:
                agent_task = (
                    f"Previous agent output:\n{previous_output}\n\n"
                    f"Your task ({role.description}):\n{task}"
                )
            else:
                agent_task = task

            result = self._run_agent(agent_id, role, agent_task, agent_factory)
            trace.results.append(result)

            if result.success:
                previous_output = result.output
                self.shared_memory.write(f"agent_{i}_output", result.output, agent_id)
            else:
                # Stop on failure
                break

        trace.end_time = time.time()
        return trace


class ParallelPattern(OrchestrationPattern):
    """Parallel execution pattern.

    Multiple agents work simultaneously, results aggregated at the end.
    """

    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
        max_workers: int = 4,
    ) -> ExecutionTrace:
        trace = self._create_trace(task, "parallel")
        trace.roles = roles

        # Pre-build task strings to avoid repeated f-string formatting
        task_prefixes = [f"Your task ({role.description}):\n{task}" for role in roles]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, role in enumerate(roles):
                agent_id = f"{role.name}_{i}"
                future = executor.submit(self._run_agent, agent_id, role, task_prefixes[i], agent_factory)
                futures[future] = agent_id

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                trace.results.append(result)

                if result.success:
                    self.shared_memory.write(
                        f"agent_{result.agent_id}_output",
                        result.output,
                        result.agent_id,
                    )

        trace.end_time = time.time()
        return trace


class HierarchicalPattern(OrchestrationPattern):
    """Hierarchical execution pattern.
    
    Manager agent coordinates multiple worker agents.
    """
    
    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
    ) -> ExecutionTrace:
        trace = self._create_trace(task, "hierarchical")
        trace.roles = roles
        
        if not roles:
            trace.end_time = time.time()
            return trace
        
        # First role is the manager
        manager_role = roles[0]
        worker_roles = roles[1:]
        
        # Phase 1: Manager delegates tasks
        manager_id = f"{manager_role.name}_manager"
        delegation_task = (
            f"You are the manager. Analyze this task and delegate to workers:\n{task}\n\n"
            f"Available workers: {', '.join(r.name for r in worker_roles)}\n"
            f"Provide specific instructions for each worker."
        )
        
        manager_result = self._run_agent(manager_id, manager_role, delegation_task, agent_factory)
        trace.results.append(manager_result)
        
        if not manager_result.success:
            trace.end_time = time.time()
            return trace
        
        # Store manager's plan
        self.shared_memory.write("manager_plan", manager_result.output, manager_id)
        
        # Phase 2: Workers execute
        worker_results = []
        for i, role in enumerate(worker_roles):
            agent_id = f"{role.name}_worker_{i}"
            worker_task = (
                f"Manager's plan:\n{manager_result.output}\n\n"
                f"Your specific task ({role.description}):\n{task}"
            )
            result = self._run_agent(agent_id, role, worker_task, agent_factory)
            worker_results.append(result)
            trace.results.append(result)
        
        # Phase 3: Manager reviews
        review_task = (
            f"Review the following worker outputs and provide final synthesis:\n\n"
            + "\n\n".join(
                f"--- {r.agent_id} ---\n{r.output}"
                for r in worker_results
                if r.success
            )
        )
        
        review_result = self._run_agent(manager_id, manager_role, review_task, agent_factory)
        trace.results.append(review_result)
        
        trace.end_time = time.time()
        return trace


class ConsensusPattern(OrchestrationPattern):
    """Consensus execution pattern.
    
    Multiple agents analyze the same problem and reach consensus.
    """
    
    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
        max_rounds: int = 3,
    ) -> ExecutionTrace:
        trace = self._create_trace(task, "consensus")
        trace.roles = roles
        
        if not roles:
            trace.end_time = time.time()
            return trace
        
        # Round 1: Initial analysis
        analyses: dict[str, str] = {}
        for i, role in enumerate(roles):
            agent_id = f"{role.name}_{i}"
            agent_task = (
                f"Analyze this problem independently and provide your perspective:\n{task}\n\n"
                f"Your role: {role.description}"
            )
            result = self._run_agent(agent_id, role, agent_task, agent_factory)
            trace.results.append(result)
            
            if result.success:
                analyses[agent_id] = result.output
                self.shared_memory.write(f"analysis_{agent_id}", result.output, agent_id)
        
        # Round 2+: Debate and refine
        for round_num in range(2, max_rounds + 1):
            if len(analyses) < 2:
                break
            
            # Each agent reviews others' analyses
            for i, role in enumerate(roles):
                agent_id = f"{role.name}_{i}"
                other_analyses = {
                    k: v for k, v in analyses.items()
                    if k != agent_id
                }
                
                if not other_analyses:
                    continue
                
                review_task = (
                    f"Round {round_num}. Review these other analyses and refine your position:\n\n"
                    + "\n\n".join(f"--- {k} ---\n{v}" for k, v in other_analyses.items())
                    + f"\n\nYour original analysis:\n{analyses.get(agent_id, '')}\n\n"
                    f"Provide an updated analysis considering other perspectives."
                )
                
                result = self._run_agent(agent_id, role, review_task, agent_factory)
                trace.results.append(result)
                
                if result.success:
                    analyses[agent_id] = result.output
                    self.shared_memory.write(f"analysis_{agent_id}_r{round_num}", result.output, agent_id)
        
        # Final: Synthesize consensus
        if analyses:
            consensus_task = (
                f"Synthesize a consensus from these analyses:\n\n"
                + "\n\n".join(f"--- {k} ---\n{v}" for k, v in analyses.items())
                + "\n\nProvide the final consensus position."
            )
            
            # Use first role as synthesizer
            synthesizer = roles[0]
            consensus_result = self._run_agent(
                f"{synthesizer.name}_consensus",
                synthesizer,
                consensus_task,
                agent_factory,
            )
            trace.results.append(consensus_result)
        
        trace.end_time = time.time()
        return trace


class ToolMediatedPattern(OrchestrationPattern):
    """Tool-mediated execution pattern.
    
    Agents collaborate indirectly through shared tools/memory.
    Minimal direct communication.
    """
    
    def execute(
        self,
        task: str,
        roles: list[AgentRole],
        agent_factory: callable,
    ) -> ExecutionTrace:
        trace = self._create_trace(task, "tool_mediated")
        trace.roles = roles
        
        # Initialize shared workspace
        self.shared_memory.write("task", task, "system")
        self.shared_memory.write("workspace", {}, "system")
        self.shared_memory.write("results", {}, "system")
        
        # Each agent works independently, reading/writing shared memory
        for i, role in enumerate(roles):
            agent_id = f"{role.name}_{i}"
            
            # Agent reads current state and contributes
            agent_task = (
                f"Task: {task}\n\n"
                f"Your role: {role.description}\n\n"
                f"Read the shared workspace and contribute your expertise. "
                f"Write your contributions back to the workspace. "
                f"Do not communicate directly with other agents."
            )
            
            result = self._run_agent(agent_id, role, agent_task, agent_factory)
            trace.results.append(result)
            
            if result.success:
                # Store result in shared memory
                current_results = self.shared_memory.read("results", {})
                current_results[agent_id] = result.output
                self.shared_memory.write("results", current_results, agent_id)
        
        # Final aggregation
        final_results = self.shared_memory.read("results", {})
        if final_results:
            self.shared_memory.write(
                "final_output",
                "\n\n".join(f"--- {k} ---\n{v}" for k, v in final_results.items()),
                "system",
            )
        
        trace.end_time = time.time()
        return trace
