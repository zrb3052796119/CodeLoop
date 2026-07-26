"""Integration tests for Agent Reach tools."""

from __future__ import annotations

import sys

sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.tools.reach_tools import (
    web_fetch_reach_tool,
    web_search_reach_tool,
    github_search_tool,
    github_read_tool,
    rss_read_tool,
    get_reach_tools,
    clear_reach_cache,
)


def test_reach_tools_registered():
    """Test that all reach tools are available."""
    tools = get_reach_tools()
    tool_names = [t.name for t in tools]

    assert "web_fetch_reach" in tool_names
    assert "web_search_reach" in tool_names
    assert "github_search" in tool_names
    assert "github_read" in tool_names
    assert "rss_read" in tool_names
    assert len(tools) == 5
    print(f"   All {len(tools)} reach tools registered")


def test_github_search_validation():
    """Test GitHub search input validation."""
    result = github_search_tool.validator({"query": "python web framework"})
    assert result["query"] == "python web framework"

    try:
        github_search_tool.validator({})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    print("   GitHub search validation test passed")


def test_web_fetch_validation():
    """Test web fetch input validation."""
    result = web_fetch_reach_tool.validator({"url": "https://example.com"})
    assert result["url"] == "https://example.com"

    try:
        web_fetch_reach_tool.validator({"url": "not-a-url"})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    print("   Web fetch validation test passed")


def test_github_read_validation():
    """Test GitHub read input validation."""
    result = github_read_tool.validator({"repo": "facebook/react", "path": "README.md"})
    assert result["repo"] == "facebook/react"
    assert result["path"] == "README.md"
    assert result["branch"] == "main"

    try:
        github_read_tool.validator({})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    print("   GitHub read validation test passed")


def test_rss_read_validation():
    """Test RSS read input validation."""
    result = rss_read_tool.validator({"url": "https://example.com/feed.xml"})
    assert result["url"] == "https://example.com/feed.xml"
    assert result["max_items"] == 5

    try:
        rss_read_tool.validator({})
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    print("   RSS read validation test passed")


def test_cache_operations():
    """Test cache operations."""
    import minicode.tools.reach_tools as rt

    rt._set_cached("test_key", "test_value")
    assert rt._get_cached("test_key") == "test_value"

    clear_reach_cache()
    assert rt._get_cached("test_key") is None
    print("   Cache operations test passed")


if __name__ == "__main__":
    test_reach_tools_registered()
    test_github_search_validation()
    test_web_fetch_validation()
    test_github_read_validation()
    test_rss_read_validation()
    test_cache_operations()
    print("\n All reach integration tests passed!")
