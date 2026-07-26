"""Tests for Agent Reach tools.

Tests the built-in reach tools without requiring network access
by mocking urllib requests.
"""

from __future__ import annotations

import json
import base64
from unittest.mock import patch, MagicMock
from io import BytesIO

import sys
sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.tools.reach_tools import (
    web_fetch_reach_tool,
    web_search_reach_tool,
    github_search_tool,
    github_read_tool,
    rss_read_tool,
    get_reach_tools,
)
from minicode.tooling import ToolContext


class MockResponse:
    """Mock urllib response."""
    def __init__(self, content: bytes, status: int = 200):
        self._content = content
        self.status = status
    
    def read(self):
        return self._content
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# web_fetch_reach tests
# ---------------------------------------------------------------------------

def test_web_fetch_success():
    """Test successful web fetch."""
    html_content = b"# Test Page\n\nThis is a test page content."
    
    with patch("urllib.request.urlopen", return_value=MockResponse(html_content)):
        result = web_fetch_reach_tool.run(
            {"url": "https://example.com", "max_chars": 10000},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "Test Page" in result.output
    assert "example.com" in result.output


def test_web_fetch_invalid_url():
    """Test web fetch with invalid URL."""
    result = web_fetch_reach_tool.validator({"url": "not-a-url"})
    # Should not raise - validator allows any string URL
    assert result["url"] == "not-a-url"


def test_web_fetch_empty_url():
    """Test web fetch with empty URL."""
    try:
        web_fetch_reach_tool.validator({"url": ""})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "required" in str(e).lower()


# ---------------------------------------------------------------------------
# web_search_reach tests
# ---------------------------------------------------------------------------

def test_web_search_success():
    """Test successful web search."""
    search_results = b"Result 1: https://example.com/page1\n\nResult 2: https://example.com/page2"
    
    with patch("urllib.request.urlopen", return_value=MockResponse(search_results)):
        result = web_search_reach_tool.run(
            {"query": "python tutorial", "num_results": 2},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "python tutorial" in result.output
    assert "example.com" in result.output


def test_web_search_no_results():
    """Test web search with no results."""
    empty_results = b"No results found"
    
    with patch("urllib.request.urlopen", return_value=MockResponse(empty_results)):
        result = web_search_reach_tool.run(
            {"query": "xyzabc123nonexistent"},
            ToolContext(cwd="/tmp"),
        )
    
    # Should still return ok, just with no results
    assert result.ok


# ---------------------------------------------------------------------------
# github_search tests
# ---------------------------------------------------------------------------

def test_github_search_success():
    """Test successful GitHub search."""
    api_response = json.dumps({
        "total_count": 2,
        "items": [
            {
                "full_name": "user/repo1",
                "html_url": "https://github.com/user/repo1",
                "stargazers_count": 1000,
                "language": "Python",
                "description": "A test repo",
            },
            {
                "full_name": "user/repo2",
                "html_url": "https://github.com/user/repo2",
                "stargazers_count": 500,
                "language": "JavaScript",
                "description": "Another test repo",
            },
        ]
    }).encode()
    
    with patch("urllib.request.urlopen", return_value=MockResponse(api_response)):
        result = github_search_tool.run(
            {"query": "web framework", "sort": "stars", "per_page": 5},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "user/repo1" in result.output
    assert "1,000" in result.output
    assert "Python" in result.output


def test_github_search_no_results():
    """Test GitHub search with no results."""
    api_response = json.dumps({
        "total_count": 0,
        "items": []
    }).encode()
    
    with patch("urllib.request.urlopen", return_value=MockResponse(api_response)):
        result = github_search_tool.run(
            {"query": "xyzabc123nonexistent"},
            ToolContext(cwd="/tmp"),
        )
    
    assert not result.ok
    assert "No repositories found" in result.output


# ---------------------------------------------------------------------------
# github_read tests
# ---------------------------------------------------------------------------

def test_github_read_file():
    """Test reading a file from GitHub."""
    content = base64.b64encode(b"# README\n\nThis is a test readme.").decode()
    api_response = json.dumps({
        "name": "README.md",
        "size": 30,
        "content": content,
    }).encode()
    
    with patch("urllib.request.urlopen", return_value=MockResponse(api_response)):
        result = github_read_tool.run(
            {"repo": "user/repo", "path": "README.md"},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "README" in result.output
    assert "user/repo" in result.output


def test_github_read_directory():
    """Test listing directory contents from GitHub."""
    api_response = json.dumps([
        {"name": "src", "type": "dir", "size": 0},
        {"name": "README.md", "type": "file", "size": 100},
        {"name": "setup.py", "type": "file", "size": 50},
    ]).encode()
    
    with patch("urllib.request.urlopen", return_value=MockResponse(api_response)):
        result = github_read_tool.run(
            {"repo": "user/repo", "path": "src"},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "src" in result.output
    assert "README.md" in result.output


def test_github_read_not_found():
    """Test reading non-existent file."""
    from urllib.error import HTTPError
    
    with patch("urllib.request.urlopen", side_effect=HTTPError(
        "https://api.github.com", 404, "Not Found", {}, None
    )):
        result = github_read_tool.run(
            {"repo": "user/nonexistent", "path": "file.txt"},
            ToolContext(cwd="/tmp"),
        )
    
    assert not result.ok
    assert "404" in result.output or "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# rss_read tests
# ---------------------------------------------------------------------------

def test_rss_read_success():
    """Test successful RSS feed reading."""
    rss_content = b"""<?xml version="1.0"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item>
<title>Article 1</title>
<link>https://example.com/1</link>
<description>First article description</description>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
</item>
<item>
<title>Article 2</title>
<link>https://example.com/2</link>
<description>Second article description</description>
<pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
</item>
</channel>
</rss>"""
    
    with patch("urllib.request.urlopen", return_value=MockResponse(rss_content)):
        result = rss_read_tool.run(
            {"url": "https://example.com/feed.xml", "max_items": 5},
            ToolContext(cwd="/tmp"),
        )
    
    assert result.ok
    assert "Test Feed" in result.output
    assert "Article 1" in result.output
    assert "Article 2" in result.output
    assert "example.com/1" in result.output


def test_rss_read_empty_feed():
    """Test reading empty RSS feed."""
    rss_content = b"""<?xml version="1.0"?>
<rss version="2.0">
<channel>
<title>Empty Feed</title>
</channel>
</rss>"""
    
    with patch("urllib.request.urlopen", return_value=MockResponse(rss_content)):
        result = rss_read_tool.run(
            {"url": "https://example.com/empty.xml"},
            ToolContext(cwd="/tmp"),
        )
    
    assert not result.ok
    assert "No items found" in result.output


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_get_reach_tools():
    """Test that all reach tools are returned."""
    tools = get_reach_tools()
    
    assert len(tools) == 5
    
    tool_names = {t.name for t in tools}
    expected = {
        "web_fetch_reach",
        "web_search_reach",
        "github_search",
        "github_read",
        "rss_read",
    }
    assert tool_names == expected


def test_reach_tools_are_read_only():
    """Test that all reach tools are marked as read-only."""
    tools = get_reach_tools()
    
    for tool in tools:
        assert tool.is_read_only, f"{tool.name} should be read-only"
        assert tool.is_concurrency_safe, f"{tool.name} should be concurrency-safe"


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_web_fetch_validation():
    """Test web fetch input validation."""
    valid = web_fetch_reach_tool.validator({"url": "https://example.com"})
    assert valid["url"] == "https://example.com"
    assert valid["max_chars"] == 10000
    
    valid_custom = web_fetch_reach_tool.validator({"url": "https://example.com", "max_chars": 5000})
    assert valid_custom["max_chars"] == 5000


def test_github_search_validation():
    """Test GitHub search input validation."""
    valid = github_search_tool.validator({"query": "python"})
    assert valid["query"] == "python"
    assert valid["sort"] == "stars"
    assert valid["per_page"] == 5
    
    # Test per_page cap
    valid_large = github_search_tool.validator({"query": "python", "per_page": 20})
    assert valid_large["per_page"] == 10  # Should be capped


def test_rss_read_validation():
    """Test RSS read input validation."""
    valid = rss_read_tool.validator({"url": "https://example.com/feed.xml"})
    assert valid["url"] == "https://example.com/feed.xml"
    assert valid["max_items"] == 5
    
    # Test max_items cap
    valid_large = rss_read_tool.validator({"url": "https://example.com/feed.xml", "max_items": 50})
    assert valid_large["max_items"] == 20  # Should be capped


if __name__ == "__main__":
    # Run all tests
    import traceback
    
    tests = [
        test_web_fetch_success,
        test_web_fetch_empty_url,
        test_web_search_success,
        test_web_search_no_results,
        test_github_search_success,
        test_github_search_no_results,
        test_github_read_file,
        test_github_read_directory,
        test_github_read_not_found,
        test_rss_read_success,
        test_rss_read_empty_feed,
        test_get_reach_tools,
        test_reach_tools_are_read_only,
        test_web_fetch_validation,
        test_github_search_validation,
        test_rss_read_validation,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
