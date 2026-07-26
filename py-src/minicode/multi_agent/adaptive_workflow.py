"""Adaptive workflow for dynamic multi-agent execution adjustment.

Monitors execution progress and dynamically adjusts the workflow
by adding agents, reallocating tasks, or inserting validation steps.
"""

from __future__ import annotations

import time
from typing import Any

from minicode.multi_agent.types import (
    ExecutionTrace,
    WorkflowAdjustment,
    AgentRole,
    AgentStatus,
)
from minicode.multi_agent.role_analyzer import RoleAnalyzer


class AdaptiveWorkflow:
    """Dynamically adjusts workflow based on execution progress.
    
    Monitors execution traces and makes adjustments:
    - Detects slow agents and adds resources
    - Identifies high error rates and inserts validation
    - Reallocates tasks when agents fail
    """
    
    def __init__(
        self,
        slow_threshold_ms: float = 30000.0,
        error_threshold: float = 0.5,
        role_analyzer: RoleAnalyzer | None = None,
    ):
        self.slow_threshold_ms = slow_threshold_ms
        self.error_threshold = error_threshold
        self.role_analyzer = role_analyzer or RoleAnalyzer()
    
    def monitor(self, trace: ExecutionTrace) -> WorkflowAdjustment | None:
        """Monitor execution and suggest adjustments.
        
        Args:
            trace: Current execution trace
            
        Returns:
            Adjustment or None if no adjustment needed
        """
        if not trace.results:
            return None
        
        # Check for slow agents
        slow_agents = self._detect_slow_agents(trace)
        if slow_agents:
            return WorkflowAdjustment(
                reason=f"Slow agents detected: {', '.join(slow_agents)}",
                action="add_specialist",
                affected_agents=slow_agents,
                new_roles=[
                    self.role_analyzer.get_role("code") or AgentRole(
                        name="HelperAgent",
                        description="Assistant to speed up execution",
                    )
                ],
            )
        
        # Check for high error rates
        error_rate = self._calculate_error_rate(trace)
        if error_rate > self.error_threshold:
            failed_agents = [
                r.agent_id for r in trace.results
                if r.status == AgentStatus.FAILED
            ]
            return WorkflowAdjustment(
                reason=f"High error rate: {error_rate:.1%}",
                action="insert_validation",
                affected_agents=failed_agents,
            )
        
        # Check for incomplete coverage
        if trace.success_rate < 1.0 and trace.success_rate > 0:
            return WorkflowAdjustment(
                reason=f"Partial success: {trace.success_rate:.1%}",
                action="reallocate_tasks",
                affected_agents=[
                    r.agent_id for r in trace.results
                    if not r.success
                ],
            )
        
        return None
    
    def _detect_slow_agents(self, trace: ExecutionTrace) -> list[str]:
        """Detect agents that are taking too long.
        
        Args:
            trace: Execution trace
            
        Returns:
            List of slow agent IDs
        """
        slow = []
        for result in trace.results:
            if result.duration_ms > self.slow_threshold_ms:
                slow.append(result.agent_id)
        return slow
    
    def _calculate_error_rate(self, trace: ExecutionTrace) -> float:
        """Calculate the error rate.

        Args:
            trace: Execution trace

        Returns:
            Error rate (0.0 - 1.0)
        """
        total = len(trace.results)
        if not total:
            return 0.0
        failed = sum(1 for r in trace.results if r.status == AgentStatus.FAILED)
        return failed / total
    
    def should_continue(self, trace: ExecutionTrace, max_adjustments: int = 3) -> bool:
        """Check if execution should continue with adjustments.
        
        Args:
            trace: Execution trace
            max_adjustments: Maximum number of adjustments allowed
            
        Returns:
            True if should continue
        """
        if len(trace.adjustments) >= max_adjustments:
            return False
        
        if trace.success_rate >= 1.0:
            return False
        
        return True
    
    def generate_next_roles(
        self,
        task: str,
        trace: ExecutionTrace,
    ) -> list[AgentRole]:
        """Generate additional roles for the next iteration.
        
        Args:
            task: Original task
            trace: Current execution trace
            
        Returns:
            List of new roles
        """
        # Analyze what went wrong
        failed_roles = [
            r.role for r in trace.results
            if r.status == AgentStatus.FAILED
        ]
        
        # Generate roles to address failures
        new_roles = []
        for role_name in failed_roles:
            role = self.role_analyzer.get_role(role_name.lower())
            if role:
                new_roles.append(role)
        
        # If no specific roles, add a general helper
        if not new_roles:
            helper = self.role_analyzer.get_role("code")
            if helper:
                new_roles.append(helper)
        
        return new_roles
