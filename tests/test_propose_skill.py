from pathlib import Path

import pytest

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityMetadata,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.skill_authoring import propose_skill
from minicode.tooling import ToolContext
from minicode.tools import create_default_tool_registry
from minicode.tools.propose_skill import create_propose_skill_tool


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


def test_default_registries_bind_propose_skill_to_their_own_capability_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    interactive = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "core"},
        include_mcp=False,
        include_user_interaction=True,
    )
    non_interactive = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "core"},
        include_mcp=False,
        include_user_interaction=False,
    )
    proposal = {
        "name": "user-clarification",
        "description": "Ask the user to clarify an ambiguous request.",
        "domains": ["communication"],
        "scopes": ["external"],
        "tools": ["ask_user"],
    }

    try:
        non_interactive_result = non_interactive.execute(
            "propose_skill",
            proposal,
            ToolContext(cwd=str(tmp_path)),
        )
        interactive_result = interactive.execute(
            "propose_skill",
            proposal,
            ToolContext(cwd=str(tmp_path)),
        )
        non_interactive_again = non_interactive.execute(
            "propose_skill",
            proposal,
            ToolContext(cwd=str(tmp_path)),
        )
    finally:
        interactive.dispose()
        non_interactive.dispose()

    assert non_interactive_result.ok
    assert interactive_result.ok
    assert non_interactive_again.ok
    assert "- ask_user: unknown" in non_interactive_result.output
    assert "- ask_user: known (communication/external)" in interactive_result.output
    assert "- ask_user: unknown" in non_interactive_again.output


def test_filtered_registry_rebinds_propose_skill_to_the_filtered_tool_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    full = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "core"},
        include_mcp=False,
        include_user_interaction=False,
    )
    filtered = full.filtered(lambda tool: tool.name != "task")
    proposal = {
        "name": "delegating-skill",
        "description": "Delegate a bounded coding task to a sub-agent.",
        "domains": ["execution"],
        "scopes": ["external"],
        "tools": ["task"],
    }

    try:
        full_result = full.execute(
            "propose_skill",
            proposal,
            ToolContext(cwd=str(tmp_path)),
        )
        filtered_result = filtered.execute(
            "propose_skill",
            proposal,
            ToolContext(cwd=str(tmp_path)),
        )
    finally:
        filtered.dispose()
        full.dispose()

    assert full.find("task") is not None
    assert filtered.find("task") is None
    assert full_result.ok
    assert filtered_result.ok
    assert "- task: known (execution/external)" in full_result.output
    assert "- task: unknown" in filtered_result.output


def test_propose_skill_requires_an_explicit_capability_registry(
    tmp_path: Path,
) -> None:
    spec = {"name": "explicit-registry", "description": "Require explicit tools."}

    with pytest.raises(TypeError):
        propose_skill(tmp_path, spec)
    with pytest.raises(TypeError):
        create_propose_skill_tool(str(tmp_path))


def test_registry_binding_failure_disposes_started_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    disposed: list[bool] = []

    monkeypatch.setattr(
        "minicode.tools.create_mcp_backed_tools",
        lambda **_kwargs: {
            "tools": [],
            "servers": [],
            "dispose": lambda: disposed.append(True),
        },
    )

    def fail_snapshot(_tools) -> CapabilityRegistry:
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(
        "minicode.capability_registry.build_tool_capability_snapshot",
        fail_snapshot,
    )

    with pytest.raises(RuntimeError, match="snapshot failed"):
        create_default_tool_registry(str(tmp_path), runtime={"mcpServers": {}})

    assert disposed == [True]


class _FakePreselMatcher:
    """Scripted DirectoryPreselMatcher stand-in with fixed cosine scores."""

    def __init__(self, scores, threshold=0.6):
        self._scores = dict(scores)
        self.signal_threshold = threshold
        self.rank_queries = []
        self.rank_directories = []

    def rank(self, query, by_directory, *, call_context=None):
        self.rank_queries.append(query)
        self.rank_directories.append(list(by_directory))
        return dict(self._scores)


class _ExplodingMatcher:
    signal_threshold = 0.6

    def rank(self, query, by_directory, *, call_context=None):
        raise RuntimeError("embedding transport exploded")


def _disjoint_spec() -> dict:
    # Vocabulary shares nothing with the seeded directories, so any existing-
    # directory recommendation must come from the semantic signal.
    return {
        "name": "quantum-harmonics",
        "description": "Calibrate flux capacitors for warp output.",
    }


def test_semantic_match_recommends_lexically_silent_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    matcher = _FakePreselMatcher({"testing": 0.62})
    proposal = propose_skill(
        tmp_path,
        _disjoint_spec(),
        _registry(),
        presel_matcher=matcher,
    )

    # 0.62 * 3.0 = 1.86 clears the existing-directory bar alone.
    assert proposal["recommended_directory"] == "testing"
    assert proposal["recommendation_type"] == "existing_directory"
    reasons = proposal["candidate_directories"][0]["reasons"]
    assert any(reason.startswith("semantic:") for reason in reasons)


def test_without_matcher_disjoint_spec_suggests_new_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(tmp_path, _disjoint_spec(), _registry())

    assert proposal["recommendation_type"] == "new_directory"
    assert proposal["recommended_directory"] != "testing"


def test_subthreshold_semantic_stays_new_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    matcher = _FakePreselMatcher({"testing": 0.30})
    proposal = propose_skill(
        tmp_path,
        _disjoint_spec(),
        _registry(),
        presel_matcher=matcher,
    )

    # 0.30 * 3.0 = 0.90 < 1.5: a weak cosine must not trigger placement.
    assert proposal["recommendation_type"] == "new_directory"


def test_matcher_failure_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    proposal = propose_skill(
        tmp_path,
        _disjoint_spec(),
        _registry(),
        presel_matcher=_ExplodingMatcher(),
    )

    assert proposal["recommendation_type"] == "new_directory"


def test_semantic_scores_cover_all_directories_before_topk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    matcher = _FakePreselMatcher({})
    propose_skill(
        tmp_path,
        _disjoint_spec(),
        _registry(),
        presel_matcher=matcher,
    )

    # Blending happens over the whole catalog, so a semantic hit outside the
    # lexical top-k can still win placement.
    assert len(matcher.rank_directories[0]) == 3
    assert set(matcher.rank_directories[0]) == {
        "code-understanding",
        "debugging",
        "testing",
    }


def test_tool_skips_semantic_placement_when_switch_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    monkeypatch.delenv("MINI_CODE_SKILL_DIRECTORY_PRESEL", raising=False)
    called_with = {}
    original = propose_skill

    def spy(cwd, spec, registry, **kwargs):
        called_with["presel_matcher"] = kwargs.get("presel_matcher")
        return original(cwd, spec, registry, **kwargs)

    monkeypatch.setattr("minicode.tools.propose_skill.propose_skill", spy)
    tool = create_propose_skill_tool(str(tmp_path), capability_registry_provider=_registry)
    result = tool.run(
        _disjoint_spec(),
        ToolContext(cwd=str(tmp_path)),
    )

    assert result.ok
    assert called_with["presel_matcher"] is None


def test_tool_fails_closed_without_embedding_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_skill_tree(tmp_path, monkeypatch)
    monkeypatch.setenv("MINI_CODE_SKILL_DIRECTORY_PRESEL", "1")
    tool = create_propose_skill_tool(str(tmp_path), capability_registry_provider=_registry)
    result = tool.run(
        _disjoint_spec(),
        ToolContext(cwd=str(tmp_path)),
    )

    # Switch on but no embedding runtime configured: lexical-only placement.
    assert result.ok
    assert "recommendation_type: new_directory" in result.output
