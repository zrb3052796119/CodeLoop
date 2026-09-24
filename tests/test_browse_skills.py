from __future__ import annotations

import hashlib
import re

import pytest

from minicode.skills import discover_skills

from minicode.tooling import (
    SkillLoadPolicy,
    ToolContext,
)
from minicode.tools.browse_skills import (
    create_browse_skill_directory_tool,
    create_search_skills_tool,
)
from minicode.tools.load_skill import create_load_skill_tool


def _skill(
    name: str,
    *,
    directory: str = "",
    directory_description: str = "",
    description: str | None = None,
    keywords: tuple[str, ...] = (),
) -> dict:
    qualified = f"{directory}/{name}" if directory else name
    return {
        "name": name,
        "qualified_name": qualified,
        "description": description or f"{name} description",
        "directory": directory,
        "directory_description": directory_description,
        "keywords": list(keywords),
        "path": f"/skills/{qualified}/SKILL.md",
        "source": "project",
        "content_digest": hashlib.sha256(qualified.encode()).hexdigest(),
        "routing_identity_digest": hashlib.sha256(
            f"{qualified}-identity".encode()
        ).hexdigest(),
    }


def _catalog() -> list[dict]:
    return [
        _skill("one", directory="db", directory_description="database work"),
        _skill("two", directory="db"),
        _skill("three", directory="docs"),
        _skill("rooty"),
    ]


def _catalog_scoped_policy(catalog, denied=()) -> SkillLoadPolicy:
    return SkillLoadPolicy(
        denied_qualified_names=frozenset(denied),
        reason_code="model-catalog",
        allow_catalog_scope=True,
    )


def _run(tool, input_data, context):
    return tool.run(tool.validator(input_data), context)


def test_browse_applies_policy_before_listing_and_counting(tmp_path) -> None:
    catalog = _catalog()
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    policy = _catalog_scoped_policy(catalog, denied=("db/two",))
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    result = _run(tool, {"directory_names": ["db"]}, context)

    assert result.ok is True
    assert "## db (1 skills)" in result.output
    assert "- db/one:" in result.output
    assert "db/two" not in result.output


def test_search_applies_the_same_policy_filter(tmp_path) -> None:
    catalog = _catalog()
    tool = create_search_skills_tool(str(tmp_path), catalog)
    policy = _catalog_scoped_policy(catalog, denied=("db/two",))
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    result = _run(tool, {"query": "description"}, context)

    assert result.ok is True
    assert "db/two" not in result.output
    assert "- db/one [db]:" in result.output
    assert "- rooty [root]:" in result.output


def test_deny_all_closes_discovery_with_a_clear_reason(tmp_path) -> None:
    catalog = _catalog()
    policy = SkillLoadPolicy(reason_code="user-denied-all-skills")
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    for tool in (
        create_browse_skill_directory_tool(str(tmp_path), catalog),
        create_search_skills_tool(str(tmp_path), catalog),
    ):
        input_data = (
            {"directory_names": ["db"]}
            if tool.name == "browse_skill_directory"
            else {"query": "db"}
        )
        result = _run(tool, input_data, context)
        assert result.ok is False
        assert "Skill discovery is closed for this turn" in result.output
        assert "user-denied-all-skills" in result.output


def test_invalid_policy_closes_discovery(tmp_path) -> None:
    catalog = _catalog()
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=object())

    result = _run(tool, {"directory_names": ["db"]}, context)

    assert result.ok is False
    assert "invalid turn policy" in result.output


def test_missing_policy_keeps_legacy_open_behavior(tmp_path) -> None:
    catalog = _catalog()
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)

    result = _run(tool, {"directory_names": ["db"]}, ToolContext(cwd=str(tmp_path)))

    assert result.ok is True
    assert "- db/one:" in result.output
    assert "- db/two:" in result.output


def test_unknown_directory_returns_a_correctable_error(tmp_path) -> None:
    catalog = _catalog()
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(tool, {"directory_names": ["nope"]}, context)

    assert result.ok is False
    assert "Unknown skill directory: 'nope'" in result.output
    assert "'db'" in result.output
    assert "'docs'" in result.output
    assert 'Use "" for root-level skills' in result.output


def test_root_level_skills_are_discoverable_via_empty_directory_name(
    tmp_path,
) -> None:
    catalog = _catalog()
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(tool, {"directory_names": [""]}, context)

    assert result.ok is True
    assert "## (root) (1 skills)" in result.output
    assert "- rooty:" in result.output
    assert "db/one" not in result.output


def test_browse_pages_by_skill_offset_within_the_char_budget(tmp_path) -> None:
    catalog = [
        _skill(
            f"skill-{index:02d}",
            directory="bulk",
            description="x" * 120,
        )
        for index in range(10)
    ]
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
        _runtime={"skillSelection": {"skillPageCharBudget": 500}},
    )

    first = _run(tool, {"directory_names": ["bulk"]}, context)

    assert first.ok is True
    assert "TRUNCATED: yes" in first.output
    marker = "call browse_skill_directory again with offset "
    next_offset = int(first.output.split(marker)[1].splitlines()[0])
    assert 0 < next_offset < 10
    # The budget caps page size: roughly 500 chars, not all 10 entries.
    assert "skill-09" not in first.output

    seen = {
        line.split(":")[0].removeprefix("- ")
        for line in first.output.splitlines()
        if line.startswith("- ")
    }
    second = _run(
        tool,
        {"directory_names": ["bulk"], "offset": next_offset},
        context,
    )
    assert second.ok is True
    assert f"OFFSET: {next_offset}" in second.output
    for line in second.output.splitlines():
        if line.startswith("- "):
            assert line.split(":")[0].removeprefix("- ") not in seen


def test_browse_limit_caps_skills_per_call(tmp_path) -> None:
    catalog = [_skill(f"s{index}", directory="bulk") for index in range(5)]
    tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(tool, {"directory_names": ["bulk"], "limit": 2}, context)

    assert result.ok is True
    assert (
        "TRUNCATED: yes - call browse_skill_directory again with offset 2"
        in result.output
    )
    listed = [line for line in result.output.splitlines() if line.startswith("- ")]
    assert len(listed) == 2


def test_search_preferred_directories_cannot_crowd_out_global_matches(
    tmp_path,
) -> None:
    catalog = [
        _skill(
            f"query-{index}",
            directory="db",
            description="database query tuning",
        )
        for index in range(5)
    ]
    catalog.append(_skill("sql-helper", description="database query helper"))
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(
        tool,
        {"query": "database query", "directory_names": ["db"], "limit": 4},
        context,
    )

    assert result.ok is True
    assert "Matches in preferred directories:" in result.output
    assert "Other directories / global:" in result.output
    # Global matches remain in the first page even when preferred scores win;
    # any spare slots can be filled from the preferred side.
    assert "- sql-helper [root]:" in result.output
    preferred_section = result.output.split("Other directories / global:")[0]
    assert preferred_section.count("- db/") == 3


def test_search_without_preferred_directories_is_a_single_ranked_list(
    tmp_path,
) -> None:
    catalog = _catalog()
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(tool, {"query": "description"}, context)

    assert result.ok is True
    assert "preferred directories" not in result.output
    assert "- db/one [db]:" in result.output


def test_search_is_honest_when_nothing_matches(tmp_path) -> None:
    catalog = _catalog()
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(tool, {"query": "zzzqxx-nomatch"}, context)

    assert result.ok is True
    assert "No matching skills found" in result.output
    assert "browse_skill_directory" in result.output
    assert "- db/" not in result.output


def test_search_unknown_preferred_directory_is_correctable(tmp_path) -> None:
    catalog = _catalog()
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    result = _run(
        tool,
        {"query": "db", "directory_names": ["nope"]},
        context,
    )

    assert result.ok is False
    assert "Unknown skill directory: 'nope'" in result.output


def test_semantic_alias_boost_surfaces_concept_matches(tmp_path) -> None:
    catalog = [
        _skill("login-audit", description="authentication log review"),
        _skill("unrelated", description="garden watering schedule"),
    ]
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
    )

    # "auth" shares no literal token with the skill text but belongs to the
    # same alias concept group as "authentication".
    result = _run(tool, {"query": "auth"}, context)

    assert result.ok is True
    assert "- login-audit [root]:" in result.output
    assert "unrelated" not in result.output


def _listed_names(output: str) -> list[str]:
    return re.findall(r"^- ([^\s:]+)", output, re.MULTILINE)


def _next_offset(output: str) -> int | None:
    match = re.search(r"TRUNCATED: yes - call \w+ again with offset (\d+)", output)
    return int(match.group(1)) if match else None


@pytest.mark.parametrize("per_directory,limit", [(6, 4), (500, 50)])
def test_preferred_search_pagination_reaches_every_matching_skill(
    tmp_path, per_directory, limit
) -> None:
    catalog = [
        _skill(f"s{i:03}", directory=directory, description="query helper")
        for directory in ("preferred", "global")
        for i in range(per_directory)
    ]
    denied_name = "global/s000"
    expected = {entry["qualified_name"] for entry in catalog} - {denied_name}
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog, denied=(denied_name,)),
    )
    offset = 0
    seen = []
    for _ in range(len(expected)):
        result = _run(
            tool,
            {
                "query": "query",
                "directory_names": ["preferred"],
                "limit": limit,
                "offset": offset,
            },
            context,
        )
        assert result.ok is True
        assert len(result.output) <= 4000
        page = _listed_names(result.output)
        assert page and len(page) <= limit
        assert denied_name not in page
        seen.extend(page)
        next_offset = _next_offset(result.output)
        if next_offset is None:
            break
        assert next_offset == offset + len(page)
        offset = next_offset
    assert len(seen) == len(set(seen))
    assert set(seen) == expected


@pytest.mark.parametrize("preferred_matches", [False, True])
def test_preferred_search_limit_one_keeps_global_rescue_reachable(
    tmp_path, preferred_matches
) -> None:
    catalog = [
        _skill(
            "local",
            directory="preferred",
            description="query" if preferred_matches else "flowers",
        ),
        _skill("target", directory="global", description="query"),
    ]
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path), _skill_load_policy=_catalog_scoped_policy(catalog)
    )
    args = {"query": "query", "directory_names": ["preferred"], "limit": 1}
    first = _run(tool, args, context)
    assert first.ok is True
    if preferred_matches:
        assert _listed_names(first.output) == ["preferred/local"]
        assert _next_offset(first.output) == 1
        second = _run(tool, {**args, "offset": 1}, context)
        assert _listed_names(second.output) == ["global/target"]
        assert "TRUNCATED: no" in second.output
    else:
        assert _listed_names(first.output) == ["global/target"]
        assert "TRUNCATED: no" in first.output


def test_preferred_search_offset_stays_stable_when_limit_changes(tmp_path) -> None:
    catalog = [
        _skill(f"s{i}", directory=directory, description="query")
        for directory, count in (("preferred", 7), ("global", 5))
        for i in range(count)
    ]
    tool = create_search_skills_tool(str(tmp_path), catalog)
    context = ToolContext(
        cwd=str(tmp_path), _skill_load_policy=_catalog_scoped_policy(catalog)
    )
    seen = []
    offset = 0
    for limit in (3, 1, 5, 3):
        result = _run(
            tool,
            {
                "query": "query",
                "directory_names": ["preferred"],
                "limit": limit,
                "offset": offset,
            },
            context,
        )
        assert result.ok is True
        page = _listed_names(result.output)
        seen.extend(page)
        offset += len(page)
    assert len(seen) == 12
    assert set(seen) == {entry["qualified_name"] for entry in catalog}
    assert "TRUNCATED: no" in result.output


@pytest.mark.parametrize("tool_name", ["browse_skill_directory", "search_skills"])
@pytest.mark.parametrize("budget", [500, 4000])
def test_long_description_page_is_bounded_advances_and_keeps_loadable_name(
    tmp_path, monkeypatch, tool_name, budget
) -> None:
    monkeypatch.setattr("minicode.skills._home_dir", lambda: tmp_path / "home")
    directory = tmp_path / ".mini-code" / "skills" / "bulk"
    directory.mkdir(parents=True)
    (directory / "SKILL_DIR.md").write_text(
        "---\nname: bulk\ndescription: Query helpers\n---\n"
    )
    long_description = "query " + "x" * 12000
    for name, description in (
        ("a-long", long_description),
        ("z-short", "query helper"),
    ):
        child = directory / name
        child.mkdir()
        (child / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\nUse this query procedure.\n"
        )
    catalog = discover_skills(tmp_path)
    assert len(catalog) == 2
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
        _runtime={"skillSelection": {"skillPageCharBudget": budget}},
    )
    if tool_name == "browse_skill_directory":
        tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
        args = {"directory_names": ["bulk"]}
    else:
        tool = create_search_skills_tool(str(tmp_path), catalog)
        args = {"query": "query"}
    first = _run(tool, args, context)
    assert first.ok is True
    assert len(first.output) <= budget
    assert _listed_names(first.output) == ["bulk/a-long"]
    assert "description truncated; call load_skill" in first.output
    assert _next_offset(first.output) == 1
    second = _run(tool, {**args, "offset": 1}, context)
    assert second.ok is True
    assert len(second.output) <= budget
    assert _listed_names(second.output) == ["bulk/z-short"]
    assert "TRUNCATED: no" in second.output
    loaded = _run(
        create_load_skill_tool(str(tmp_path), catalog),
        {"name": _listed_names(first.output)[0]},
        context,
    )
    assert loaded.ok is True
    assert long_description in loaded.output


@pytest.mark.parametrize("tool_name", ["browse_skill_directory", "search_skills"])
def test_minimum_budget_preserves_maximal_qualified_name(tmp_path, tool_name) -> None:
    directory = "d" * 128
    catalog = [
        _skill("n" * 128, directory=directory, description="query " + "x" * 1000)
    ]
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
        _runtime={"skillSelection": {"skillPageCharBudget": 500}},
    )
    if tool_name == "browse_skill_directory":
        tool = create_browse_skill_directory_tool(str(tmp_path), catalog)
        args = {"directory_names": [directory]}
    else:
        tool = create_search_skills_tool(str(tmp_path), catalog)
        args = {"query": "query " * 1000, "directory_names": [directory]}
    result = _run(tool, args, context)
    assert result.ok is True
    assert len(result.output) <= 500
    assert _listed_names(result.output) == [catalog[0]["qualified_name"]]
    assert "description truncated; call load_skill" in result.output
    assert "TRUNCATED: no" in result.output


def test_discovery_diagnostics_respect_page_budget(tmp_path) -> None:
    catalog = [_skill("skill", directory=f"directory-{i:04}") for i in range(1000)]
    context = ToolContext(
        cwd=str(tmp_path),
        _skill_load_policy=_catalog_scoped_policy(catalog),
        _runtime={"skillSelection": {"skillPageCharBudget": 500}},
    )
    search = create_search_skills_tool(str(tmp_path), catalog)
    missing = _run(
        create_search_skills_tool(str(tmp_path), catalog[:1]),
        {"query": "zzzz-nomatch " * 1000},
        context,
    )
    assert missing.ok is True
    assert "No matching skills found" in missing.output
    assert len(missing.output) <= 500
    for tool, args in (
        (search, {"query": "skill", "directory_names": ["missing" * 1000]}),
        (
            create_browse_skill_directory_tool(str(tmp_path), catalog),
            {"directory_names": ["missing" * 1000]},
        ),
    ):
        result = _run(tool, args, context)
        assert result.ok is False
        assert "Unknown skill directory:" in result.output
        assert "Directory list truncated" in result.output
        assert len(result.output) <= 500
