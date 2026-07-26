"""Dynamic role generation based on task description.

Analyzes task descriptions and generates optimal agent roles
using keyword extraction and template matching.
"""

from __future__ import annotations

import functools
import re
from typing import Any

from minicode.multi_agent.types import AgentRole


# Built-in role templates
ROLE_TEMPLATES: dict[str, AgentRole] = {
    "research": AgentRole(
        name="ResearchAgent",
        description="Information gathering and analysis specialist",
        expertise=["web_search", "information_synthesis", "data_analysis"],
        tools=["web_search", "web_fetch", "grep_files", "read_file"],
        responsibilities=["Gather relevant information", "Analyze sources", "Summarize findings"],
        system_prompt="""You are a research specialist. Your job is to gather and analyze information.
Use web_search and web_fetch to find relevant data.
Always cite your sources and provide structured summaries.
Be thorough but concise.""",
    ),
    "code": AgentRole(
        name="CoderAgent",
        description="Code writing and implementation specialist",
        expertise=["python", "javascript", "code_generation", "debugging"],
        tools=["write_file", "edit_file", "patch_file", "read_file", "run_command"],
        responsibilities=["Write clean code", "Implement features", "Fix bugs"],
        system_prompt="""You are a coding specialist. Write clean, well-documented code.
Follow best practices and existing code style.
Always test your code when possible.
Use tools to read existing code before making changes.""",
    ),
    "test": AgentRole(
        name="TesterAgent",
        description="Testing and quality assurance specialist",
        expertise=["unit_testing", "integration_testing", "test_design"],
        tools=["test_runner", "read_file", "run_command"],
        responsibilities=["Write test cases", "Execute tests", "Report coverage"],
        system_prompt="""You are a testing specialist. Write comprehensive tests.
Cover edge cases and error conditions.
Use test_runner to execute tests and report results.
Focus on both positive and negative test cases.""",
    ),
    "review": AgentRole(
        name="ReviewerAgent",
        description="Code review and quality assessment specialist",
        expertise=["code_review", "security_analysis", "performance_optimization"],
        tools=["read_file", "code_review", "diff_viewer"],
        responsibilities=["Review code quality", "Identify issues", "Suggest improvements"],
        system_prompt="""You are a code review specialist. Review code for quality, security, and performance.
Be constructive and specific in your feedback.
Identify potential bugs, security issues, and optimization opportunities.""",
    ),
    "architect": AgentRole(
        name="ArchitectAgent",
        description="System architecture and design specialist",
        expertise=["system_design", "api_design", "database_design"],
        tools=["read_file", "write_file", "list_files", "file_tree"],
        responsibilities=["Design system architecture", "Define interfaces", "Evaluate tradeoffs"],
        system_prompt="""You are an architecture specialist. Design scalable and maintainable systems.
Consider tradeoffs between simplicity and flexibility.
Document your design decisions clearly.""",
    ),
    "devops": AgentRole(
        name="DevOpsAgent",
        description="Deployment and infrastructure specialist",
        expertise=["ci_cd", "docker", "cloud_deployment", "monitoring"],
        tools=["run_command", "docker_helper", "read_file"],
        responsibilities=["Set up CI/CD", "Configure deployment", "Monitor systems"],
        system_prompt="""You are a DevOps specialist. Set up deployment pipelines and infrastructure.
Use Docker and CI/CD tools effectively.
Ensure systems are reliable and scalable.""",
    ),
    "document": AgentRole(
        name="DocumentAgent",
        description="Documentation and communication specialist",
        expertise=["technical_writing", "documentation", "communication"],
        tools=["read_file", "write_file", "edit_file"],
        responsibilities=["Write documentation", "Create READMEs", "Document APIs"],
        system_prompt="""You are a documentation specialist. Write clear and comprehensive documentation.
Make complex concepts accessible.
Ensure documentation stays in sync with code.""",
    ),
}

# Keywords that map to roles
ROLE_KEYWORDS: dict[str, list[str]] = {
    "research": ["research", "search", "find", "gather", "analyze", "investigate", "study", "explore"],
    "code": ["code", "implement", "write", "develop", "program", "build", "create", "fix bug"],
    "test": ["test", "testing", "verify", "validate", "check", "assert", "coverage"],
    "review": ["review", "audit", "inspect", "check", "evaluate", "assess", "critique"],
    "architect": ["design", "architecture", "structure", "pattern", "system", "framework"],
    "devops": ["deploy", "docker", "ci/cd", "pipeline", "infrastructure", "host", "server"],
    "document": ["document", "readme", "doc", "explain", "describe", "guide", "tutorial"],
}


class RoleAnalyzer:
    """Analyzes task descriptions and generates optimal agent roles."""
    
    def __init__(self, custom_templates: dict[str, AgentRole] | None = None):
        self._templates = dict(ROLE_TEMPLATES)
        if custom_templates:
            self._templates.update(custom_templates)
    
    @functools.lru_cache(maxsize=128)
    def _analyze_cached(self, task_lower: str, max_roles: int) -> tuple[str, ...]:
        """缓存角色分析结果，返回匹配的角色名称元组"""
        scores: dict[str, int] = {}
        for role_name, keywords in ROLE_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in task_lower)
            if score > 0:
                scores[role_name] = score

        if not scores:
            return ("research", "code")

        sorted_roles = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return tuple(name for name, _ in sorted_roles[:max_roles])

    def analyze(self, task: str, max_roles: int = 3) -> list[AgentRole]:
        """Analyze a task and generate appropriate agent roles.

        Args:
            task: The task description
            max_roles: Maximum number of roles to generate

        Returns:
            List of agent roles sorted by relevance
        """
        role_names = self._analyze_cached(task.lower(), max_roles)

        roles: list[AgentRole] = []
        for role_name in role_names:
            if role_name in self._templates:
                template = self._templates[role_name]
                role = AgentRole(
                    name=template.name,
                    description=template.description,
                    expertise=list(template.expertise),
                    tools=list(template.tools),
                    responsibilities=list(template.responsibilities),
                    system_prompt=template.system_prompt,
                    max_steps=template.max_steps,
                )
                roles.append(role)

        return roles
    
    def add_custom_role(self, name: str, role: AgentRole) -> None:
        """Add a custom role template.
        
        Args:
            name: Role identifier
            role: The role definition
        """
        self._templates[name] = role
    
    def get_role(self, name: str) -> AgentRole | None:
        """Get a role template by name.
        
        Args:
            name: Role identifier
            
        Returns:
            The role or None
        """
        return self._templates.get(name)
    
    def list_roles(self) -> list[str]:
        """List all available role names.
        
        Returns:
            List of role names
        """
        return list(self._templates.keys())
