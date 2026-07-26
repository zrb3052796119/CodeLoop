"""Agent Reach tools for external platform access.

Built-in tools for high-frequency platforms:
- web_fetch: Fetch any webpage as markdown
- web_search: Search the web via Jina AI
- github_search: Search GitHub repositories
- github_read: Read GitHub repository files
- rss_read: Read RSS feeds
"""

from __future__ import annotations

import functools
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from ipaddress import ip_address
from typing import Any

from minicode.tooling import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------

_BLOCKED_HOST_PREFIXES = frozenset({
    "localhost", "127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.",
    "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "0.0.0.0", "::1", "fe80:", "fc00:", "fd00:", "169.254.",
})

_MAX_CACHE_ENTRIES = 256


# ---------------------------------------------------------------------------
# Cache and retry utilities
# ---------------------------------------------------------------------------

_reach_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> str | None:
    if key in _reach_cache:
        value, timestamp = _reach_cache[key]
        if time.time() - timestamp < _CACHE_TTL:
            return value
        del _reach_cache[key]
    return None


def _set_cached(key: str, value: str) -> None:
    _reach_cache[key] = (value, time.time())
    # Prevent unbounded cache growth
    if len(_reach_cache) > _MAX_CACHE_ENTRIES:
        oldest = min(_reach_cache, key=lambda k: _reach_cache[k][1])
        del _reach_cache[oldest]


# ---------------------------------------------------------------------------
# SSRF Protection
# ---------------------------------------------------------------------------

def _is_safe_url(url: str) -> tuple[bool, str]:
    """Check URL is safe (not pointing to internal/private addresses).

    Performs hostname-based blocking and DNS resolution check to prevent
    SSRF attacks via DNS rebinding.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname

        if not hostname:
            return False, "Invalid URL: no hostname"

        # Only allow http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"URL scheme not allowed: {parsed.scheme}"

        # Block by hostname prefix
        if any(hostname.lower().startswith(p) for p in _BLOCKED_HOST_PREFIXES):
            return False, f"Access to internal addresses blocked: {hostname}"

        # Block localhost variants
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False, "Access to localhost blocked"

        # DNS resolution check - prevent DNS rebinding attacks
        try:
            resolved = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in resolved:
                ip_str = sockaddr[0]
                ip = ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False, f"URL resolves to internal address: {ip_str}"
        except socket.gaierror:
            pass  # DNS resolution failed, will fail at connection time

        return True, "OK"
    except Exception as e:
        return False, f"URL validation failed: {e}"


def _with_retry(max_retries: int = 2, delay: float = 1.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except urllib.error.URLError as e:
                    last_error = e
                    if attempt < max_retries:
                        time.sleep(delay * (attempt + 1))
            raise last_error
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# web_fetch - Enhanced version with markdown conversion
# ---------------------------------------------------------------------------

def _web_fetch_validate(input_data: dict) -> dict:
    url = input_data.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("url is required")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return {
        "url": url,
        "max_chars": int(input_data.get("max_chars", 10000)),
    }


def _web_fetch_run(input_data: dict, context) -> ToolResult:
    url = input_data["url"]
    max_chars = input_data["max_chars"]

    # SSRF protection
    is_safe, reason = _is_safe_url(url)
    if not is_safe:
        return ToolResult(ok=False, output=f"Security Error: {reason}\nURL: {url}")

    # Check cache
    cache_key = f"fetch:{url}:{max_chars}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return ToolResult(ok=True, output=cached)

    try:
        # Use Jina AI Reader for markdown conversion
        jina_url = f"https://r.jina.ai/{url}"

        req = urllib.request.Request(
            jina_url,
            headers={
                "User-Agent": "MiniCode-Python/0.5.0",
                "Accept": "text/markdown",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8", errors="replace")

            truncated = len(content) > max_chars
            if truncated:
                content = content[:max_chars] + f"\n\n... [Content truncated at {max_chars} chars]"

            header = f"URL: {url}\nSource: Jina AI Reader\nChars: {len(content)}\n\n"
            result = header + content
            _set_cached(cache_key, result)
            return ToolResult(ok=True, output=result)

    except urllib.error.URLError as e:
        return ToolResult(ok=False, output=f"Failed to fetch URL: {e.reason}\nURL: {url}")
    except Exception as e:
        return ToolResult(ok=False, output=f"Error fetching URL: {e}\nURL: {url}")


web_fetch_reach_tool = ToolDefinition(
    name="web_fetch_reach",
    description="Fetch any webpage and convert to clean markdown. Uses Jina AI Reader for optimal content extraction.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "max_chars": {"type": "number", "description": "Maximum characters (default: 10000)"},
        },
        "required": ["url"],
    },
    validator=_web_fetch_validate,
    run=_web_fetch_run,
)


# ---------------------------------------------------------------------------
# web_search - Enhanced with Jina AI Search
# ---------------------------------------------------------------------------

def _web_search_validate(input_data: dict) -> dict:
    query = input_data.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    return {
        "query": query.strip(),
        "num_results": int(input_data.get("num_results", 5)),
    }


def _web_search_run(input_data: dict, context) -> ToolResult:
    query = input_data["query"]
    num_results = input_data.get("num_results", 5)

    # Check cache
    cache_key = f"search:{query}:{num_results}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return ToolResult(ok=True, output=cached)

    try:
        # Use Jina AI Search
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://s.jina.ai/{encoded_query}"

        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "MiniCode-Python/0.5.0",
                "Accept": "text/markdown",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8", errors="replace")

            # Parse and format results
            lines = [f"Search results for: {query}", "=" * 60, ""]

            # Split by result entries (Jina returns markdown with URLs)
            entries = content.split("\n\n")
            count = 0
            for entry in entries:
                if count >= num_results:
                    break
                if entry.strip() and ("http://" in entry or "https://" in entry):
                    lines.append(entry.strip())
                    lines.append("")
                    count += 1

            if count == 0:
                # Fallback: return raw content
                lines.append(content[:5000])

            lines.append(f"\nTotal results shown: {count}")
            result = "\n".join(lines)
            _set_cached(cache_key, result)
            return ToolResult(ok=True, output=result)

    except urllib.error.URLError as e:
        return ToolResult(ok=False, output=f"Search failed: {e.reason}\nQuery: {query}")
    except Exception as e:
        return ToolResult(ok=False, output=f"Search error: {e}\nQuery: {query}")


web_search_reach_tool = ToolDefinition(
    name="web_search_reach",
    description="Search the web using Jina AI. Returns search results with sources and summaries. No API key required.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "num_results": {"type": "number", "description": "Number of results (default: 5)"},
        },
        "required": ["query"],
    },
    validator=_web_search_validate,
    run=_web_search_run,
)


# ---------------------------------------------------------------------------
# github_search - Search GitHub repositories
# ---------------------------------------------------------------------------

def _github_search_validate(input_data: dict) -> dict:
    query = input_data.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    return {
        "query": query.strip(),
        "sort": input_data.get("sort", "stars"),
        "order": input_data.get("order", "desc"),
        "per_page": min(int(input_data.get("per_page", 5)), 10),
    }


def _github_search_run(input_data: dict, context) -> ToolResult:
    query = input_data["query"]
    sort = input_data.get("sort", "stars")
    order = input_data.get("order", "desc")
    per_page = input_data.get("per_page", 5)

    # Check cache
    cache_key = f"gh_search:{query}:{sort}:{order}:{per_page}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return ToolResult(ok=True, output=cached)

    try:
        # GitHub Search API (no auth required for public repos)
        encoded_query = urllib.parse.quote(query)
        api_url = f"https://api.github.com/search/repositories?q={encoded_query}&sort={sort}&order={order}&per_page={per_page}"

        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "MiniCode-Python/0.5.0",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

            items = data.get("items", [])
            total = data.get("total_count", 0)

            if not items:
                return ToolResult(ok=False, output=f"No repositories found for: {query}")

            lines = [
                f"GitHub repositories for: {query}",
                f"Total results: {total}",
                "=" * 60,
                "",
            ]

            for i, repo in enumerate(items, 1):
                lines.extend([
                    f"{i}. {repo.get('full_name', 'N/A')}",
                    f"   URL: {repo.get('html_url', 'N/A')}",
                    f"   Stars: {repo.get('stargazers_count', 0):,}",
                    f"   Language: {repo.get('language', 'N/A')}",
                    f"   Description: {repo.get('description', 'N/A')}",
                    "",
                ])

            result = "\n".join(lines)
            _set_cached(cache_key, result)
            return ToolResult(ok=True, output=result)

    except urllib.error.HTTPError as e:
        return ToolResult(ok=False, output=f"GitHub API error: {e.code}\nQuery: {query}")
    except Exception as e:
        return ToolResult(ok=False, output=f"GitHub search error: {e}\nQuery: {query}")


github_search_tool = ToolDefinition(
    name="github_search",
    description="Search GitHub repositories. Returns repo name, stars, language, and description.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (e.g., 'python web framework')"},
            "sort": {"type": "string", "description": "Sort by: stars, forks, updated (default: stars)"},
            "order": {"type": "string", "description": "Order: asc, desc (default: desc)"},
            "per_page": {"type": "number", "description": "Results per page (max: 10, default: 5)"},
        },
        "required": ["query"],
    },
    validator=_github_search_validate,
    run=_github_search_run,
)


# ---------------------------------------------------------------------------
# github_read - Read GitHub repository files
# ---------------------------------------------------------------------------

def _github_read_validate(input_data: dict) -> dict:
    repo = input_data.get("repo")
    path = input_data.get("path", "")
    if not isinstance(repo, str) or not repo:
        raise ValueError("repo is required (format: owner/repo)")
    return {
        "repo": repo.strip(),
        "path": path.strip(),
        "branch": input_data.get("branch", "main"),
    }


def _github_read_run(input_data: dict, context) -> ToolResult:
    repo = input_data["repo"]
    path = input_data.get("path", "")
    branch = input_data.get("branch", "main")

    # Check cache
    cache_key = f"gh_read:{repo}:{path}:{branch}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return ToolResult(ok=True, output=cached)

    try:
        # GitHub Contents API
        if path:
            api_url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
        else:
            api_url = f"https://api.github.com/repos/{repo}/readme?ref={branch}"

        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "MiniCode-Python/0.5.0",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

            # If it's a directory listing
            if isinstance(data, list):
                lines = [
                    f"Contents of {repo}/{path} (branch: {branch})",
                    "=" * 60,
                    "",
                ]
                for item in data:
                    item_type = item.get("type", "unknown")
                    name = item.get("name", "N/A")
                    size = item.get("size", 0)
                    lines.append(f"  [{item_type:10}] {name:40} {size:>10,} bytes")
                result = "\n".join(lines)
                _set_cached(cache_key, result)
                return ToolResult(ok=True, output=result)

            # If it's a file
            content = data.get("content", "")
            import base64
            try:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                decoded = content

            name = data.get("name", path or "README")
            size = data.get("size", 0)

            header = f"File: {repo}/{name} (branch: {branch}, size: {size:,} bytes)\n{'=' * 60}\n\n"

            # Truncate if too large
            max_chars = 15000
            if len(decoded) > max_chars:
                decoded = decoded[:max_chars] + f"\n\n... [File truncated at {max_chars} chars]"

            result = header + decoded
            _set_cached(cache_key, result)
            return ToolResult(ok=True, output=result)

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ToolResult(ok=False, output=f"File not found: {repo}/{path}\nBranch: {branch}")
        return ToolResult(ok=False, output=f"GitHub API error: {e.code}\nRepo: {repo}")
    except Exception as e:
        return ToolResult(ok=False, output=f"GitHub read error: {e}\nRepo: {repo}")


github_read_tool = ToolDefinition(
    name="github_read",
    description="Read files from a GitHub repository. Supports reading specific files or listing directory contents.",
    input_schema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (format: owner/repo, e.g., 'facebook/react')"},
            "path": {"type": "string", "description": "File path within repo (empty for README)"},
            "branch": {"type": "string", "description": "Branch name (default: main)"},
        },
        "required": ["repo"],
    },
    validator=_github_read_validate,
    run=_github_read_run,
)


# ---------------------------------------------------------------------------
# rss_read - Read RSS feeds
# ---------------------------------------------------------------------------

def _rss_read_validate(input_data: dict) -> dict:
    url = input_data.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("url is required")
    return {
        "url": url.strip(),
        "max_items": min(int(input_data.get("max_items", 5)), 20),
    }


def _rss_read_run(input_data: dict, context) -> ToolResult:
    url = input_data["url"]
    max_items = input_data.get("max_items", 5)

    # SSRF protection
    is_safe, reason = _is_safe_url(url)
    if not is_safe:
        return ToolResult(ok=False, output=f"Security Error: {reason}\nURL: {url}")

    # Check cache
    cache_key = f"rss:{url}:{max_items}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return ToolResult(ok=True, output=cached)

    try:
        # Fetch RSS feed
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MiniCode-Python/0.5.0",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8", errors="replace")

        # Parse RSS (precompiled regex patterns)
        import re

        # Precompiled patterns for RSS parsing
        _RSS_CHANNEL_TITLE = re.compile(r"<channel>.*?<title>(.*?)</title>", re.DOTALL)
        _RSS_ITEM_PATTERN = re.compile(r"<item>(.*?)</item>", re.DOTALL)
        _RSS_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
        _RSS_LINK = re.compile(r"<link>(.*?)</link>")
        _RSS_DESC = re.compile(r"<description>(.*?)</description>", re.DOTALL)
        _RSS_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>")
        _HTML_TAG_RE = re.compile(r"<[^>]+>")

        # Extract channel title
        channel_title = "Unknown Feed"
        title_match = _RSS_CHANNEL_TITLE.search(content)
        if title_match:
            channel_title = _HTML_TAG_RE.sub("", title_match.group(1)).strip()

        # Extract items
        items = []
        for match in _RSS_ITEM_PATTERN.finditer(content):
            item_content = match.group(1)

            # Extract fields using precompiled patterns
            title_m = _RSS_TITLE.search(item_content)
            title = _HTML_TAG_RE.sub("", title_m.group(1)).strip() if title_m else "No title"

            link_m = _RSS_LINK.search(item_content)
            link = link_m.group(1).strip() if link_m else ""

            desc_m = _RSS_DESC.search(item_content)
            if desc_m:
                desc = _HTML_TAG_RE.sub("", desc_m.group(1)).strip()
                desc = desc[:200] + "..." if len(desc) > 200 else desc
            else:
                desc = ""

            pub_m = _RSS_PUBDATE.search(item_content)
            pub_date = pub_m.group(1).strip() if pub_m else ""

            items.append({
                "title": title,
                "link": link,
                "description": desc,
                "pub_date": pub_date,
            })

            if len(items) >= max_items:
                break

        if not items:
            return ToolResult(ok=False, output=f"No items found in RSS feed: {url}")

        lines = [
            f"RSS Feed: {channel_title}",
            f"URL: {url}",
            f"Items: {len(items)}",
            "=" * 60,
            "",
        ]

        for i, item in enumerate(items, 1):
            lines.extend([
                f"{i}. {item['title']}",
                f"   Date: {item['pub_date']}",
                f"   Link: {item['link']}",
                f"   {item['description']}",
                "",
            ])

        result = "\n".join(lines)
        _set_cached(cache_key, result)
        return ToolResult(ok=True, output=result)

    except urllib.error.URLError as e:
        return ToolResult(ok=False, output=f"Failed to fetch RSS: {e.reason}\nURL: {url}")
    except Exception as e:
        return ToolResult(ok=False, output=f"RSS read error: {e}\nURL: {url}")


rss_read_tool = ToolDefinition(
    name="rss_read",
    description="Read RSS/Atom feeds. Returns feed items with title, date, link, and description.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "RSS feed URL"},
            "max_items": {"type": "number", "description": "Maximum items (max: 20, default: 5)"},
        },
        "required": ["url"],
    },
    validator=_rss_read_validate,
    run=_rss_read_run,
)


# ---------------------------------------------------------------------------
# Tool registry helper
# ---------------------------------------------------------------------------

def get_reach_tools() -> list[ToolDefinition]:
    """Get all Agent Reach tools.

    Returns:
        List of ToolDefinition instances
    """
    return [
        web_fetch_reach_tool,
        web_search_reach_tool,
        github_search_tool,
        github_read_tool,
        rss_read_tool,
    ]


def clear_reach_cache() -> None:
    """Clear the reach tools cache."""
    _reach_cache.clear()
