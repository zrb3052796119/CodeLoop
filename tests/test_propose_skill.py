from pathlib import Path

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
    register_tool_capabilities,
)
from minicode.skill_authoring import propose_skill
from minicode.tooling import ToolContext
from minicode.tools import create_default_tool_registry


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


def _seed_skill_tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root = tmp_path / ".mini-code" / "skills"
    _write_directory(
        root,
        "code-understanding",
        "Skills for reading, tracing, and explaining code architecture.",
        ["code", "file", "search", "analysis"],
        ["readonly"],
        ["explain", "architecture", "trace", "agent_loop"],
    )
    _write_skill(
        root,
        "code-understanding",
        "codebase-explanation",
        "Explain codebase architecture, agent loop flow, tool calling, and prompt construction.",
        ["code", "file", "search", "analysis"],
        ["readonly"],
        ["read_file", "grep_files", "load_skill"],
        ["agent_loop", "architecture", "tool calling", "prompt", "explain"],
    )
    _write_directory(
        root,
        "debugging",
        "Skills for debugging failures, errors, tests, and runtime issues.",
        ["code", "file", "search", "execution"],
        ["readonly", "destructive"],
        ["debug", "pytest", "failure", "traceback"],
    )
    _write_skill(
        root,
        "debugging",
        "pytest-debugging",
        "Debug pytest failures by reading error output, locating failing code, and rerunning focused tests.",
        ["code", "file", "search", "execution"],
        ["readonly", "destructive"],
        ["read_file", "grep_files", "run_command"],
        ["pytest", "failure", "assertion", "traceback", "test"],
    )
    _write_directory(
        root,
        "testing",
        "Skills for writing tests and test-driven implementation.",
        ["code", "file", "execution"],
        ["readonly", "write"],
        ["test", "tdd", "pytest"],
    )
    return root


def _write_directory(
    root: Path,
    name: str,
    description: str,
    domains: list[str],
    scopes: list[str],
    keywords: list[str],
) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL_DIR.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                f"domains: [{', '.join(domains)}]",
                f"scopes: [{', '.join(scopes)}]",
                f"keywords: [{', '.join(keywords)}]",
                "---",
                "",
                f"# {name}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_skill(
    root: Path,
    directory: str,
    name: str,
    description: str,
    domains: list[str],
    scopes: list[str],
    tools: list[str],
    keywords: list[str],
) -> None:
    skill_dir = root / directory / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                f"directory: {directory}",
                f"domains: [{', '.join(domains)}]",
                f"scopes: [{', '.join(scopes)}]",
                f"tools: [{', '.join(tools)}]",
                f"keywords: [{', '.join(keywords)}]",
                "---",
                "",
                f"# {name}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_propose_skill_routes_agent_loop_trace_to_code_understanding(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        {
            "name": "trace-agent-loop",
            "description": "Trace how agent_loop.py flows through messages and tool calls.",
            "domains": ["code", "file", "search", "analysis"],
            "scopes": ["readonly"],
            "keywords": ["agent_loop", "tool calling", "trace"],
            "tools": ["read_file", "grep_files", "load_skill"],
            "examples": ["Trace why agent_loop.py calls tools"],
        },
        _registry(
            ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
            ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
            ("load_skill", CapabilityDomain.FILE, CapabilityScope.READONLY),
        ),
    )

    assert proposal["recommended_directory"] == "code-understanding"
    assert proposal["recommendation_type"] == "existing_directory"
    assert proposal["target_path"] == ".mini-code/skills/code-understanding/trace-agent-loop/SKILL.md"
    assert proposal["new_directory_suggestion"] is None


def test_propose_skill_routes_pytest_failure_to_debugging(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        {
            "name": "pytest-fixture-debugging",
            "description": "Debug pytest fixture failures and traceback output.",
            "domains": ["code", "file", "execution"],
            "scopes": ["readonly", "destructive"],
            "keywords": ["debug", "pytest", "failure", "traceback"],
            "tools": ["read_file", "grep_files", "run_command"],
        },
        _registry(("run_command", CapabilityDomain.EXECUTION, CapabilityScope.DESTRUCTIVE)),
    )

    assert proposal["recommended_directory"] == "debugging"
    assert proposal["candidate_directories"][0]["name"] == "debugging"


def test_propose_skill_reports_duplicate_name_and_qualified_name(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        {
            "name": "codebase-explanation",
            "description": "Explain codebase architecture, agent loop flow, tool calling, and prompt construction.",
            "domains": ["code", "file", "search", "analysis"],
            "scopes": ["readonly"],
            "keywords": ["agent_loop", "architecture"],
        },
        CapabilityRegistry(),
    )

    warning_types = {warning["type"] for warning in proposal["duplicate_warnings"]}
    assert "duplicate_name" in warning_types
    assert "duplicate_qualified_name" in warning_types
    assert "similar_description" in warning_types


def test_propose_skill_validates_known_and_unknown_tools(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        {
            "name": "read-search-workflow",
            "description": "Read and search files before explaining a code path.",
            "domains": ["code", "file", "search"],
            "scopes": ["readonly"],
            "tools": ["read_file", "grep_files", "missing_tool"],
        },
        _registry(
            ("read_file", CapabilityDomain.FILE, CapabilityScope.READONLY),
            ("grep_files", CapabilityDomain.SEARCH, CapabilityScope.READONLY),
        ),
    )

    validation = {item["name"]: item for item in proposal["tool_validation"]}
    assert validation["read_file"]["known"] is True
    assert validation["read_file"]["domain"] == "file"
    assert validation["read_file"]["scope"] == "readonly"
    assert validation["missing_tool"]["known"] is False


def test_propose_skill_suggests_new_directory_when_no_strong_match(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        {
            "name": "invoice-reconciliation",
            "description": "Reconcile invoice payment records and accounting exports.",
            "domains": ["finance"],
            "scopes": ["readonly"],
            "keywords": ["invoice", "payment", "accounting"],
        },
        CapabilityRegistry(),
    )

    assert proposal["recommendation_type"] == "new_directory"
    assert proposal["confidence"] == "low"
    assert proposal["new_directory_suggestion"]["name"] == "finance-skills"
    assert proposal["target_path"] == ".mini-code/skills/finance-skills/invoice-reconciliation/SKILL.md"


def test_propose_skill_tool_is_registered_and_does_not_create_files(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    tools = create_default_tool_registry(str(tmp_path), runtime={})
    register_tool_capabilities(tools)
    tool = tools.find("propose_skill")
    assert tool is not None
    assert tool.is_read_only
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    result = tools.execute(
        "propose_skill",
        {
            "name": "trace-agent-loop",
            "description": "Trace how agent_loop.py flows through messages and tool calls.",
            "domains": ["code", "file", "search", "analysis"],
            "scopes": ["readonly"],
            "keywords": ["agent_loop", "tool calling", "trace"],
            "tools": ["read_file", "grep_files", "load_skill"],
        },
        ToolContext(cwd=str(tmp_path)),
    )

    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert result.ok
    assert "PROPOSE_SKILL_RESULT" in result.output
    assert "target_path: .mini-code/skills/code-understanding/trace-agent-loop/SKILL.md" in result.output
    assert before == after
