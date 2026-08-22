import hashlib

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import ActionType, IntentType, ParsedIntent, parse_intent
from minicode.prompt import build_system_prompt
from minicode.run_events import project_skill_routing_event
from minicode.skill_router import SkillRouter, required_skill_names_for_routing


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


def test_available_capabilities_do_not_create_relevance_for_unknown_intent() -> None:
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

    assert result.used_fallback
    assert result.selected == []
    assert result.capability_domains == []
    assert result.capability_scopes == []


def test_top_k_limits_selected_skills() -> None:
    skills = [
        _skill(
            f"explain-skill-{i}",
            "Explain code and file structure",
            keywords=["agent_loop"],
        )
        for i in range(10)
    ]
    intent = parse_intent("explain how agent_loop.py handles tool calls")
    registry = _registry(("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY))

    result = SkillRouter().route(skills, intent, registry, top_k=5)

    assert not result.used_fallback
    assert len(result.selected) == 5


def test_explicit_skill_reference_outranks_semantic_recommendations() -> None:
    skills = [
        _skill(
            "minicode-study",
            "Fix code, inspect inventory modules, and run project tests",
            source="user",
            qualified_name="code-skills/minicode-study",
            keywords=["fix", "code", "inventory", "tests"],
        ),
        _skill(
            "acceptance-review",
            "Review a focused acceptance change",
            qualified_name="acceptance-review",
        ),
    ]

    routing = SkillRouter().route(
        skills,
        parse_intent(
            "Use the acceptance-review Skill to fix code in inventory.py and run tests"
        ),
        _registry(),
        top_k=2,
    )

    assert routing.selected[0].qualified_name == "acceptance-review"
    assert routing.selected[0].explicitly_requested
    assert required_skill_names_for_routing(routing) == ["acceptance-review"]
    assert not routing.selected[1].explicitly_requested


def test_semantic_skill_recommendations_are_advisory_not_final_blockers() -> None:
    routing = SkillRouter().route(
        [_skill("pytest-debugging", "Debug pytest failures", keywords=["pytest"])],
        parse_intent("debug a pytest failure"),
        _registry(),
    )

    assert not routing.used_fallback
    assert required_skill_names_for_routing(routing) == []
    projected = routing.to_dict()
    assert projected["selected_skills"][0]["explicitly_requested"] is False
    assert required_skill_names_for_routing(projected) == []


def test_legacy_routing_without_explicit_markers_keeps_old_required_projection() -> None:
    legacy = {
        "used_fallback": False,
        "selected": [{"qualified_name": "legacy/demo"}],
    }

    assert required_skill_names_for_routing(legacy) == ["legacy/demo"]


def test_fallback_abstains_when_no_task_evidence_matches() -> None:
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
    assert result.selected == []


def test_unrelated_chinese_chat_does_not_route_from_tool_availability() -> None:
    skills = [
        _skill(
            "pytest-debugging",
            "Debug pytest failures and runtime errors",
            tools=["read_file", "run_command"],
            domains=["code", "file", "execution"],
        ),
        _skill(
            "memory-audit",
            "Audit persistent memory behavior",
            tools=["read_file", "grep_files"],
            domains=["memory", "file", "analysis"],
        ),
    ]
    intent = parse_intent("给我讲个笑话")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry)

    assert intent.intent_type == IntentType.UNKNOWN
    assert result.used_fallback
    assert result.selected == []


def test_unrelated_small_talk_does_not_route_via_coincidental_keyword_overlap() -> None:
    """A skill's own example text can coincidentally share a common word
    (e.g. "tell") with an unrelated message. A bare keyword match must not
    create relevance on its own once the intent itself is UNKNOWN — that
    would silently defeat the fallback/abstain guarantee the other unknown-
    intent tests rely on."""
    skills = [
        _skill(
            "design-review",
            "Review existing code for design issues without changing it",
            keywords=["design review", "code smell", "inconsistency"],
            examples=["Look at this module and tell me if the design has problems"],
            tools=["read_file", "grep_files"],
            domains=["code", "analysis"],
        ),
    ]
    intent = parse_intent("Tell me a joke")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )

    result = SkillRouter().route(skills, intent, registry)

    assert intent.intent_type == IntentType.UNKNOWN
    assert result.used_fallback
    assert result.selected == []


def test_chinese_memory_and_skill_routing_audit_routes_matching_skill() -> None:
    skills = [
        _skill(
            "memory-skill-routing-audit",
            "Review persistent memory and skill routing architecture",
            keywords=["audit", "memory", "skill", "routing", "evolution"],
            tools=["read_file", "grep_files"],
            domains=["memory", "code", "analysis"],
        ),
        _skill(
            "pytest-debugging",
            "Debug pytest failures and runtime errors",
            keywords=["pytest", "failure", "debug"],
            tools=["read_file", "run_command"],
            domains=["code", "execution"],
        ),
    ]
    intent = parse_intent(
        "你仔细审查一下这个项目的持久化记忆和skill路由这一部分，"
        "看看能否有效的提高此agent的自进化。"
    )
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=1)

    assert intent.intent_type == IntentType.REVIEW
    assert intent.action_type == ActionType.ANALYZE
    assert {"memory", "skill", "routing", "evolution"} <= set(intent.keywords)
    assert not result.used_fallback
    assert result.selected[0].name == "memory-skill-routing-audit"


def test_skill_examples_are_used_as_task_relevance_evidence() -> None:
    skills = [
        _skill(
            "workflow-a",
            "Structured subsystem assessment",
            examples=["Audit persistent memory and skill routing design"],
        ),
        _skill(
            "workflow-b",
            "General purpose workflow",
            examples=["Summarize a meeting"],
        ),
    ]
    intent = parse_intent("audit persistent memory and skill routing design")

    result = SkillRouter().route(skills, intent, CapabilityRegistry(), top_k=1)

    assert not result.used_fallback
    assert result.selected[0].name == "workflow-a"


def test_compatibility_only_skill_is_not_routed_for_known_intent() -> None:
    skills = [
        _skill(
            "memory-skill-routing-audit",
            "Review persistent memory and skill routing architecture",
            keywords=["memory", "skill", "routing"],
            directory="auditing",
            directory_description="Skills for subsystem review",
            domains=["memory", "code", "analysis"],
            scopes=["readonly"],
            tools=["read_file", "grep_files"],
        ),
        _skill(
            "readme-authoring",
            "Write project README documentation",
            keywords=["readme", "documentation"],
            directory="documentation",
            directory_description="Skills for README and documentation",
            domains=["code", "file", "search"],
            scopes=["readonly"],
            tools=["read_file", "grep_files"],
        ),
    ]
    intent = parse_intent("审查持久化记忆和技能路由")
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )

    result = SkillRouter().route(skills, intent, registry)

    assert [skill.name for skill in result.selected] == [
        "memory-skill-routing-audit"
    ]


def test_matched_directory_does_not_make_unrelated_sibling_relevant() -> None:
    skills = [
        _skill(
            "memory-skill-routing-audit",
            "Review persistent memory and skill routing architecture",
            keywords=["memory", "skill", "routing"],
            directory="auditing",
            directory_description="Skills for subsystem review",
        ),
        _skill(
            "release-note-formatting",
            "Format release notes and changelogs",
            keywords=["release", "changelog"],
            directory="auditing",
            directory_description="Skills for subsystem review",
        ),
    ]
    intent = parse_intent("审查持久化记忆和技能路由")

    result = SkillRouter().route(
        skills,
        intent,
        CapabilityRegistry(),
    )

    assert [skill.name for skill in result.selected] == [
        "memory-skill-routing-audit"
    ]


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
            keywords=["agent_loop"],
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
            keywords=["agent_loop"],
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
        _skill(
            "codebase-explanation",
            "Explain code architecture and file flow",
            keywords=["agent_loop"],
        ),
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


def test_production_routing_observation_identifies_the_exact_skill_digest() -> None:
    content = (
        "---\n"
        "name: memory-audit\n"
        "description: Review persistent memory.\n"
        "---\n"
        "# Memory Audit\n"
    )
    routing = SkillRouter().route(
        [
            _skill(
                "memory-audit",
                "Review persistent memory and Skill routing",
                qualified_name="project/memory-audit",
                directory="project",
                content=content,
            )
        ],
        parse_intent("review persistent memory"),
        _registry(),
    )

    payload = project_skill_routing_event(routing)

    assert payload["routingVersion"] == 2
    assert payload["selected"][0]["contentDigest"] == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def test_router_accepts_public_discover_skills_dataclass_shape() -> None:
    from minicode.skills import SkillSummary

    skill = SkillSummary(
        name="pytest-debugging",
        description="Debug pytest failures and test automation",
        path="/tmp/pytest-debugging/SKILL.md",
        source="project",
        qualified_name="debugging/pytest-debugging",
        directory="debugging",
        keywords=["pytest", "debug"],
        content_digest="a" * 64,
    )
    result = SkillRouter().route(
        [skill],
        parse_intent("debug a pytest failure"),
        _registry(),
        top_k=1,
    )

    assert not result.used_fallback
    assert result.selected[0].name == "pytest-debugging"


def test_low_confidence_intent_cannot_create_routing_signal_alone() -> None:
    skills = [
        _skill(
            "pytest-debugging",
            "Debug pytest failures and test automation",
            keywords=["pytest"],
        )
    ]
    low_confidence = ParsedIntent(
        raw_input="unclear request",
        intent_type=IntentType.TEST,
        action_type=ActionType.EXECUTE,
        confidence=0.2,
        entities={},
        keywords=[],
    )
    high_confidence = ParsedIntent(
        raw_input="run the test suite",
        intent_type=IntentType.TEST,
        action_type=ActionType.EXECUTE,
        confidence=0.9,
        entities={},
        keywords=[],
    )

    assert SkillRouter().route(skills, low_confidence, _registry()).used_fallback
    assert SkillRouter().route(skills, high_confidence, _registry()).used_fallback


def test_classified_coding_intent_needs_specific_skill_evidence() -> None:
    skills = [
        _skill(
            "structural-rename",
            "Rename modules and update code references",
            domains=["code", "file", "search"],
            scopes=["readonly", "write"],
            tools=["read_file", "grep_files"],
        ),
        _skill(
            "readme-authoring",
            "Write project README documentation",
            domains=["code", "file"],
            scopes=["readonly", "write"],
            tools=["read_file", "grep_files"],
        ),
        _skill(
            "test-driven-development",
            "Write tests before implementation",
            domains=["code", "file"],
            scopes=["readonly", "write"],
            tools=["read_file"],
        ),
    ]
    intent = parse_intent(
        "Use exactly two parallel explore agents to trace login -> authenticate "
        "-> find_session and identify shared mutable test-state risks. "
        "Do not modify files."
    )
    registry = _registry(
        ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
    )

    result = SkillRouter().route(skills, intent, registry, top_k=5)

    assert intent.intent_type == IntentType.CODE
    assert result.used_fallback
    assert result.selected == []


def test_single_weak_lexical_signal_emits_fewer_than_top_k_candidates() -> None:
    skills = [
        _skill(
            f"trace-helper-{index}",
            "Trace control flow in an existing module",
            keywords=["trace"],
        )
        for index in range(5)
    ]

    result = SkillRouter().route(
        skills,
        ParsedIntent(
            raw_input="trace the request path",
            intent_type=IntentType.REVIEW,
            action_type=ActionType.ANALYZE,
            confidence=0.9,
            keywords=["trace"],
        ),
        _registry(),
        top_k=5,
    )

    assert not result.used_fallback
    assert len(result.selected) == 1
