from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import ActionType, IntentType, ParsedIntent, parse_intent
from minicode.prompt import build_system_prompt
from minicode.skill_router import SkillRouter


def _skill(name: str, description: str, source: str = "project", **extra) -> dict:
    return {
        "name": name,
        "qualified_name": extra.pop("qualified_name", name),
        "description": description,
        "path": f"/tmp/{name}/SKILL.md",
        "source": source,
        **extra,
    }


def _registry(*capabilities: tuple[str, CapabilityDomain, CapabilityScope]) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, domain, scope in capabilities:
        registry.register(
            CapabilityMetadata(
                name=name,
                domain=domain,
                scope=scope,
                description=f"{name} capability",
                tags=["tool", name],
            ),
            lambda **_: None,
        )
    return registry


def test_explain_read_routes_code_reading_skills_first() -> None:
    skills = [
        _skill("systematic-debugging", "Debug runtime errors and failing tests"),
        _skill("codebase-explanation", "Explain code architecture and tool calling flow across files"),
        _skill("test-driven-development", "Write tests before implementation"),
    ]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=2)

    assert not result.used_fallback
    assert result.selected[0].name == "codebase-explanation"
    assert "systematic-debugging" not in [skill.name for skill in result.selected[:1]]


def test_debug_pytest_routes_debugging_skills_first() -> None:
    skills = [
        _skill("codebase-explanation", "Explain architecture and code flow"),
        _skill("pytest-debugging", "Debug pytest failures, errors, and test execution"),
        _skill("documentation-writing", "Write README documentation"),
    ]
    intent = parse_intent("debug error in pytest failure tests/test_agent_loop.py")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=2)

    assert not result.used_fallback
    assert result.selected[0].name == "pytest-debugging"
    assert any("keyword:pytest" in reason for reason in result.selected[0].reasons)


def test_capability_domain_matches_add_score() -> None:
    skills = [
        _skill("filesystem-workflow", "Inspect file paths and search code safely"),
        _skill("neutral-workflow", "General task handling"),
    ]
    intent = ParsedIntent(
        raw_input="ambiguous task",
        intent_type=IntentType.UNKNOWN,
        action_type=ActionType.UNKNOWN,
        confidence=0.0,
    )
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=5)

    assert not result.used_fallback
    assert result.selected[0].name == "filesystem-workflow"
    assert "capability-domain:file" in result.selected[0].reasons
    assert "capability-domain:search" in result.selected[0].reasons


def test_top_k_limits_selected_skills() -> None:
    skills = [_skill(f"explain-skill-{i}", "Explain code and file structure") for i in range(10)]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY))

    result = SkillRouter().route(skills, intent, registry, top_k=5)

    assert not result.used_fallback
    assert len(result.selected) == 5


def test_fallback_preserves_all_skills_when_no_strong_match() -> None:
    skills = [
        _skill("alpha", "Unrelated workflow"),
        _skill("beta", "Another unrelated workflow"),
    ]
    intent = ParsedIntent(
        raw_input="???",
        intent_type=IntentType.UNKNOWN,
        action_type=ActionType.UNKNOWN,
        confidence=0.0,
    )
    registry = CapabilityRegistry()

    result = SkillRouter().route(skills, intent, registry, top_k=1)

    assert result.used_fallback
    assert [skill.name for skill in result.selected] == ["alpha", "beta"]


def test_directory_recall_routes_code_understanding_before_debugging() -> None:
    skills = [
        _skill(
            "codebase-explanation",
            "Explain code architecture and tool calling flow across files",
            qualified_name="code-understanding/codebase-explanation",
            directory="code-understanding",
            directory_description="Skills for reading and explaining code architecture",
            domains=["code", "file", "search", "analysis"],
            scopes=["readonly"],
            tools=["read_file", "grep_files", "load_skill"],
            keywords=["agent_loop", "architecture", "tool calling"],
        ),
        _skill(
            "pytest-debugging",
            "Debug pytest failures and test execution",
            qualified_name="debugging/pytest-debugging",
            directory="debugging",
            directory_description="Skills for debugging failures and runtime errors",
            domains=["code", "file", "execution"],
            scopes=["readonly", "destructive"],
            tools=["read_file", "run_command"],
            keywords=["pytest", "failure", "debug"],
        ),
    ]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("load_skill", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=1)

    assert not result.used_fallback
    assert result.selected_directories[0].name == "code-understanding"
    assert result.selected[0].qualified_name == "code-understanding/codebase-explanation"
    assert result.selected[0].tools == ["read_file", "grep_files", "load_skill"]
    assert "tool-domain:file" in result.selected[0].reasons


def test_chinese_explain_routes_code_understanding_without_debug_leakage() -> None:
    skills = [
        _skill(
            "codebase-explanation",
            "Explain code architecture and tool calling flow across files",
            qualified_name="code-understanding/codebase-explanation",
            directory="code-understanding",
            directory_description="Skills for reading and explaining code architecture",
            domains=["code", "file", "search", "analysis"],
            scopes=["readonly"],
            tools=["read_file", "grep_files", "load_skill"],
            keywords=["agent_loop", "architecture", "tool calling"],
        ),
        _skill(
            "pytest-debugging",
            "Debug pytest failures and test execution",
            qualified_name="debugging/pytest-debugging",
            directory="debugging",
            directory_description="Skills for debugging failures and runtime errors",
            domains=["code", "file", "execution"],
            scopes=["readonly", "destructive"],
            tools=["read_file", "run_command"],
            keywords=["pytest", "failure", "debug"],
        ),
    ]
    intent = parse_intent("解释 agent_loop.py 为什么会调用工具")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("load_skill", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=1)

    assert intent.intent_type == IntentType.EXPLAIN
    assert intent.action_type == ActionType.READ
    assert result.selected[0].qualified_name == "code-understanding/codebase-explanation"


def test_debug_pytest_routes_debugging_directory() -> None:
    skills = [
        _skill(
            "codebase-explanation",
            "Explain code architecture and file flow",
            qualified_name="code-understanding/codebase-explanation",
            directory="code-understanding",
            directory_description="Skills for explaining code architecture",
            domains=["code", "file", "search", "analysis"],
            scopes=["readonly"],
            tools=["read_file", "grep_files"],
            keywords=["explain", "architecture"],
        ),
        _skill(
            "pytest-debugging",
            "Debug pytest failures, errors, and test execution",
            qualified_name="debugging/pytest-debugging",
            directory="debugging",
            directory_description="Skills for debugging pytest failures",
            domains=["code", "file", "execution"],
            scopes=["readonly", "destructive"],
            tools=["read_file", "run_command"],
            keywords=["pytest", "failure", "debug"],
        ),
    ]
    intent = parse_intent("debug error in pytest failure tests/test_agent_loop.py")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=1)

    assert not result.used_fallback
    assert result.selected_directories[0].name == "debugging"
    assert result.selected[0].qualified_name == "debugging/pytest-debugging"
    assert result.tool_affinity["debugging/pytest-debugging"] > 0


def test_read_task_penalizes_destructive_tool_affinity() -> None:
    skills = [
        _skill(
            "read-only-explanation",
            "Explain code by reading and searching files",
            qualified_name="code-understanding/read-only-explanation",
            directory="code-understanding",
            directory_description="Read and explain code",
            domains=["code", "file", "search"],
            scopes=["readonly"],
            tools=["read_file", "grep_files"],
        ),
        _skill(
            "command-heavy-explanation",
            "Explain code by running shell commands",
            qualified_name="code-understanding/command-heavy-explanation",
            directory="code-understanding",
            directory_description="Read and explain code",
            domains=["code", "file", "execution"],
            scopes=["readonly", "destructive"],
            tools=["read_file", "run_command"],
        ),
    ]
    intent = parse_intent("explain agent_loop.py")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=2)

    assert result.selected[0].qualified_name == "code-understanding/read-only-explanation"
    assert any("tool-scope-penalty:destructive" in reason for reason in result.selected[1].reasons)


def test_prompt_uses_routed_skill_section() -> None:
    skills = [
        _skill("codebase-explanation", "Explain code architecture and file flow"),
        _skill("pytest-debugging", "Debug pytest failures"),
        _skill("systematic-debugging", "Debug runtime errors with a structured workflow"),
    ]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )
    routing = SkillRouter().route(skills, intent, registry, top_k=1)

    prompt = build_system_prompt(
        "/tmp/project",
        [],
        {
            "skills": routing.selected_skill_dicts(),
            "skill_routing": routing.to_dict(),
        },
    )

    assert "Routed skills for intent: explain/read" in prompt
    assert "Routed skills:" in prompt
    assert "Capability domains: file, search" in prompt
    assert "codebase-explanation" in prompt
    assert "pytest-debugging" not in prompt
    assert "systematic-debugging" not in prompt


def test_prompt_uses_routed_directory_section() -> None:
    skills = [
        _skill(
            "codebase-explanation",
            "Explain code architecture and file flow",
            qualified_name="code-understanding/codebase-explanation",
            directory="code-understanding",
            directory_description="Skills for reading and explaining code architecture",
            domains=["code", "file", "search", "analysis"],
            scopes=["readonly"],
            tools=["read_file", "grep_files", "load_skill"],
            keywords=["agent_loop", "architecture"],
        ),
        _skill(
            "pytest-debugging",
            "Debug pytest failures",
            qualified_name="debugging/pytest-debugging",
            directory="debugging",
            directory_description="Skills for debugging failures",
            domains=["code", "execution"],
            scopes=["destructive"],
            tools=["run_command"],
            keywords=["pytest", "debug"],
        ),
    ]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("load_skill", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )
    routing = SkillRouter().route(skills, intent, registry, top_k=1)

    prompt = build_system_prompt(
        "/tmp/project",
        [],
        {
            "skills": routing.selected_skill_dicts(),
            "skill_routing": routing.to_dict(),
        },
    )

    assert "Routed skill directories:" in prompt
    assert "code-understanding: Skills for reading and explaining code architecture" in prompt
    assert "code-understanding/codebase-explanation" in prompt
    assert "likely tools: read_file, grep_files, load_skill" in prompt
    assert "debugging/pytest-debugging" not in prompt
