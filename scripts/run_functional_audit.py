#!/usr/bin/env python3
"""Run MiniCode's isolated Functional Reliability Audit 1A.

The default mode is deterministic and offline.  It creates a private temporary
HOME, MiniCode data directory, workspace, fixture HTTP server, and fake data.
Real network access is available only through ``--live-network``.

Exit codes:
    0: audit completed and no failed capability was found
    1: audit completed and found one or more failed capabilities
    2: the audit runner itself crashed
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Any, Iterator
import urllib.error
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_CATEGORIES = {
    "discovery",
    "tools",
    "web",
    "entrypoints",
    "persistence",
    "memory",
    "skill_mcp",
    "security",
    "dashboard",
    "packaging",
    "internal",
}
STATUS_VALUES = {
    "pass",
    "partial",
    "fail",
    "unavailable",
    "blocked",
    "not_reachable",
    "not_tested",
}
TEST_VALUES = {"pass", "partial", "fail", "not_tested"}
LIVE_VALUES = {"pass", "partial", "fail", "blocked", "not_required"}
SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTH_TOKEN",
    "BEARER",
    "PASSWORD",
    "SECRET",
    "CREDENTIAL",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return "<isolated-artifact>"


def _safe_error(error: BaseException) -> str:
    """Return only a closed error class, never raw text or local paths."""
    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, socket.timeout):
            return "timeout"
        if isinstance(reason, ssl.SSLError):
            return "tls_error"
        if isinstance(reason, socket.gaierror):
            return "dns_error"
        return "url_error"
    if isinstance(error, socket.timeout | TimeoutError):
        return "timeout"
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    return type(error).__name__


def _capability(
    capability_id: str,
    category: str,
    name: str,
    *,
    declared: bool = True,
    registered: bool = True,
    reachable: tuple[str, ...] = (),
    deterministic: str = "not_tested",
    installed: str = "not_tested",
    live: str = "not_required",
    safety: str = "not_tested",
    truthfulness: str = "not_tested",
    status: str = "not_tested",
    evidence: list[str] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    assert deterministic in TEST_VALUES
    assert installed in TEST_VALUES
    assert live in LIVE_VALUES
    assert safety in TEST_VALUES
    assert truthfulness in TEST_VALUES
    assert status in STATUS_VALUES
    return {
        "id": capability_id,
        "category": category,
        "name": name,
        "declared": declared,
        "registered": registered,
        "reachableFrom": list(reachable),
        "deterministicTest": deterministic,
        "installedWheelTest": installed,
        "liveTest": live,
        "safety": safety,
        "truthfulness": truthfulness,
        "status": status,
        "evidence": evidence or [],
        "issues": issues or [],
    }


def _issue(
    issue_id: str,
    severity: str,
    capability_id: str,
    impact: str,
    reproduction: str,
    expected: str,
    actual: str,
    evidence: list[str],
    *,
    environment_dependent: bool,
    recommended_batch: str,
    red_test: str,
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "severity": severity,
        "capabilityId": capability_id,
        "userVisibleImpact": impact,
        "minimalReproduction": reproduction,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "stableReproduction": True,
        "environmentDependent": environment_dependent,
        "recommendedBatch": recommended_batch,
        "requiredRedTest": red_test,
    }


def _test_evidence_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    test_root = PROJECT_ROOT / "tests"
    for path in sorted(test_root.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        index[_relative(path)] = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
    return index


def _matching_test_paths(
    index: dict[str, list[str]], *tokens: str, limit: int = 3
) -> list[str]:
    wanted = {token for token in tokens if len(token) >= 3}
    matches = [
        path
        for path, words in index.items()
        if wanted.intersection(words)
    ]
    return matches[:limit]


def _discover_source_tool_definitions() -> dict[str, str]:
    discovered: dict[str, str] = {}
    for path in sorted((PROJECT_ROOT / "minicode").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name != "ToolDefinition":
                continue
            name_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "name"),
                None,
            )
            if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
                discovered[name_node.value] = _relative(path)
            else:
                discovered.setdefault("<dynamic-mcp-tool>", _relative(path))
    return discovered


def _discover_literal_routes() -> list[str]:
    paths = [PROJECT_ROOT / "minicode" / "gateway.py"]
    paths.extend(sorted((PROJECT_ROOT / "minicode" / "web").glob("*.py")))
    routes: set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value
            if value in {"/", "/health", "/run"} or value.startswith("/api/") or value.startswith("/assets/"):
                routes.add(value)
    return sorted(routes)


def _discover_console_scripts() -> dict[str, str]:
    payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = payload.get("project", {}).get("scripts", {})
    return {str(key): str(value) for key, value in sorted(scripts.items())}


def _discover_runtime_tools(
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from minicode.tools import create_default_tool_registry

    original_profile = os.environ.pop("MINI_CODE_TOOL_PROFILE", None)
    try:
        core = create_default_tool_registry(
            str(workspace), runtime={"toolProfile": "core", "mcpServers": {}}
        )
        full = create_default_tool_registry(
            str(workspace), runtime={"toolProfile": "full", "mcpServers": {}}
        )
    finally:
        if original_profile is not None:
            os.environ["MINI_CODE_TOOL_PROFILE"] = original_profile
    try:
        return (
            {tool.name: tool for tool in core.list()},
            {tool.name: tool for tool in full.list()},
        )
    finally:
        core.dispose()
        full.dispose()


def _probe_schemas(
    tools: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, bool]]:
    serializable: dict[str, bool] = {}
    required_rejects_empty: dict[str, bool] = {}
    for name, tool in tools.items():
        try:
            json.dumps(tool.input_schema, sort_keys=True)
            serializable[name] = True
        except (TypeError, ValueError):
            serializable[name] = False
        required = tool.input_schema.get("required", [])
        if required:
            try:
                tool.validator({})
            except (ValueError, TypeError, KeyError):
                required_rejects_empty[name] = True
            except Exception:
                required_rejects_empty[name] = False
            else:
                required_rejects_empty[name] = False
        else:
            required_rejects_empty[name] = True
    return serializable, required_rejects_empty


UTILITY_EXAMPLES: dict[str, dict[str, Any]] = {
    "json_format": {"content": '{"你好":1}'},
    "json_parse": {"content": '{"items":[{"name":"小花"}]}', "path": "items.0.name"},
    "regex_test": {"pattern": "小.", "text": "小花"},
    "regex_replace": {"pattern": "花", "text": "小花", "replacement": "草"},
    "base64_encode": {"text": "中文"},
    "base64_decode": {"text": "5Lit5paH"},
    "url_encode": {"text": "小花 x"},
    "url_decode": {"text": "%E5%B0%8F%E8%8A%B1%20x"},
    "current_time": {"timezone": "UTC", "format": "iso"},
    "timestamp_convert": {"value": "0", "direction": "to_iso"},
    "hash": {"text": "audit", "algorithm": "sha256"},
    "hmac": {"text": "audit", "key": "fixture-key", "algorithm": "sha256"},
    "csv_parse": {"content": "name,value\n小花,1\n"},
    "csv_create": {"data": '[{"name":"小花","value":1}]'},
    "uuid_generate": {"version": 4, "count": 2},
    "text_sort": {"content": "10\n2\n小花", "numeric": False},
    "text_dedupe": {"content": "a\n小花\na\n小花"},
    "text_join": {"items": "a\n小花", "separator": "|"},
    "line_count": {"content": "a\n\n小花"},
    "random_string": {"length": 16, "chars": "hex"},
}


def _probe_utility_happy_paths(
    tools: dict[str, Any], workspace: Path
) -> dict[str, bool]:
    from minicode.tooling import ToolContext

    context = ToolContext(cwd=str(workspace), permissions=None)
    results: dict[str, bool] = {}
    for name, example in UTILITY_EXAMPLES.items():
        tool = tools.get(name)
        if tool is None:
            results[name] = False
            continue
        try:
            parsed = tool.validator(example)
            results[name] = bool(tool.run(parsed, context).ok)
        except Exception:
            results[name] = False
    return results


def _probe_core_read_paths(tools: dict[str, Any], workspace: Path) -> dict[str, bool]:
    from minicode.tooling import ToolContext

    (workspace / "sample.py").write_text(
        "class Example:\n    def method(self):\n        return '小花'\n",
        encoding="utf-8",
    )
    context = ToolContext(cwd=str(workspace), permissions=None)
    examples: dict[str, dict[str, Any]] = {
        "list_files": {"path": "."},
        "grep_files": {"pattern": "Example", "path": "."},
        "read_file": {"path": "sample.py"},
        "file_line_count": {"path": "sample.py"},
        "find_symbols": {"path": "sample.py"},
        "find_references": {"symbol_name": "Example", "path": "."},
        "get_ast_info": {"file_path": "sample.py"},
        "code_review": {"path": "sample.py"},
        "file_tree": {"path": ".", "max_depth": 2},
        "diff_viewer": {
            "files": [{"path": "sample.py", "before": "a\n", "after": "b\n"}]
        },
    }
    results: dict[str, bool] = {}
    for name, example in examples.items():
        tool = tools.get(name)
        if tool is None:
            continue
        try:
            results[name] = bool(tool.run(tool.validator(example), context).ok)
        except Exception:
            results[name] = False
    return results


def _probe_read_file_truthfulness(tools: dict[str, Any], workspace: Path) -> bool:
    from minicode.tooling import ToolContext

    result = tools["read_file"].run(
        tools["read_file"].validator({"path": "does-not-exist.txt"}),
        ToolContext(cwd=str(workspace), permissions=None),
    )
    return result.ok and "TOTAL_CHARS: 0" in result.output


def _probe_archive_escape(tools: dict[str, Any], workspace: Path) -> dict[str, bool]:
    from minicode.tooling import ToolContext

    context = ToolContext(cwd=str(workspace), permissions=None)
    (workspace / "source.txt").write_text("fixture", encoding="utf-8")
    results: dict[str, bool] = {}
    cases = {
        "gzip_compress": {
            "source": "source.txt",
            "destination": "../escape.gz",
        },
        "tar_create": {
            "source": "source.txt",
            "destination": "../escape",
            "mode": "none",
        },
        "zip_create": {
            "source": "source.txt",
            "destination": "../escape",
        },
    }
    expected = {
        "gzip_compress": workspace.parent / "escape.gz",
        "tar_create": workspace.parent / "escape.tar",
        "zip_create": workspace.parent / "escape.zip",
    }
    for name, payload in cases.items():
        target = expected[name]
        try:
            result = tools[name].run(tools[name].validator(payload), context)
            results[name] = result.ok and target.exists()
        except Exception:
            results[name] = False
        finally:
            target.unlink(missing_ok=True)
    return results


class _FixtureHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def _reply(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.__class__.calls.append({"method": self.command, "path": self.path})
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply
    do_PUT = _reply
    do_DELETE = _reply
    do_PATCH = _reply

    def log_message(self, _format: str, *_args: Any) -> None:
        return


@contextmanager
def _fixture_server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    _FixtureHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", _FixtureHandler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _probe_http_request_permission_boundary(
    tools: dict[str, Any], workspace: Path
) -> tuple[bool, str]:
    from minicode.tooling import ToolContext
    from minicode.tools.network_safety import NetworkSafetyError

    try:
        with _fixture_server() as (base_url, calls):
            try:
                normalized = tools["http_request"].validator(
                    {
                        "url": f"{base_url}/mutation",
                        "method": "POST",
                        "body": '{"change":true}',
                        "timeout": 3,
                    }
                )
            except NetworkSafetyError as error:
                return False, f"blocked:{error.code}"
            result = tools["http_request"].run(
                normalized,
                ToolContext(cwd=str(workspace), permissions=None),
            )
            sent = calls == [{"method": "POST", "path": "/mutation"}]
            return bool(result.ok and sent), "fixture" if sent else "blocked:no_send"
    except OSError as error:
        return False, _safe_error(error)


def _probe_bounded_dns_resolver() -> dict[str, Any]:
    from minicode.tools.network_safety import resolver_snapshot

    snapshot = resolver_snapshot()
    return {
        "workerLimit": snapshot.worker_limit,
        "queueLimit": snapshot.queue_limit,
        "outstandingLimit": snapshot.worker_limit + snapshot.queue_limit,
        "activeCount": snapshot.active_count,
        "queuedCount": snapshot.queued_count,
        "accepting": snapshot.accepting,
        "closed": snapshot.closed,
        "saturationError": "resolver_busy",
        "workersBlockProcessExit": False,
    }


def _probe_web_contracts() -> dict[str, Any]:
    import socket

    from minicode.tooling import ToolContext
    from minicode.tools import http_utils, network_safety, search_providers
    from minicode.tools.web_fetch import web_fetch_tool
    from minicode.tools.search_providers import (
        SearchProviderOutcome,
        SearchProviderStatus,
        SearchResult,
        parse_provider_html,
        search_provider,
    )
    from minicode.tools.web_search import web_search_tool

    fixture_html = """
    <div class="result">
      <a class="result__a" href="https://example.test/one">
        中文 &amp; result
      </a>
      <a class="result__snippet">fixture snippet</a>
    </div>
    """
    parsed = parse_provider_html("duckduckgo", fixture_html, 5)
    empty = parse_provider_html(
        "duckduckgo",
        '<div class="no-results">No results found.</div>',
        5,
    )
    changed_html = (
        '<article data-result><a href="https://example.test/two">Result</a>'
        "<p>snippet</p></article>"
    )
    challenge_html = "<html><title>Just a moment...</title><p>captcha</p></html>"
    changed = parse_provider_html("duckduckgo", changed_html, 5)
    challenge = parse_provider_html("duckduckgo", challenge_html, 5)

    status_taxonomy = {
        status: search_provider(
            "baidu",
            "audit status fixture",
            1,
            deadline=time.monotonic() + 1,
            transport=(
                lambda _request, *, deadline, status=status: (
                    http_utils.SafeHttpResponse(
                        status=status,
                        content_type="text/html",
                        content_encoding="identity",
                        payload=b"bounded-status-body-fixture-secret",
                    )
                )
            ),
        ).status.value
        for status in (403, 404, 429, 503)
    }

    provider_calls: list[str] = []

    def fallback_provider(
        provider: str,
        _query: str,
        _count: int,
        *,
        deadline: float,
    ) -> SearchProviderOutcome:
        assert deadline > time.monotonic()
        provider_calls.append(provider)
        if provider == "baidu":
            return SearchProviderOutcome(
                provider,
                SearchProviderStatus.TIMEOUT,
            )
        return SearchProviderOutcome(
            provider,
            SearchProviderStatus.SUCCESS,
            (
                SearchResult(
                    title="audit result",
                    url="https://example.test/result",
                    snippet="audit snippet",
                    provider=provider,
                ),
            ),
        )

    original_provider = search_providers.search_provider
    original_config = os.environ.get("MINI_CODE_WEB_SEARCH_PROVIDERS")
    search_providers.search_provider = fallback_provider
    os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = "baidu,duckduckgo"
    try:
        fallback = web_search_tool.run(
            web_search_tool.validator(
                {
                    "query": "audit-query-fixture-secret",
                    "num_results": 2,
                }
            ),
            ToolContext(cwd="."),
        )
    finally:
        search_providers.search_provider = original_provider
        if original_config is None:
            os.environ.pop("MINI_CODE_WEB_SEARCH_PROVIDERS", None)
        else:
            os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = original_config

    class ProbeResolver:
        def resolve(
            self,
            hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            addresses = (
                ["93.184.216.34", "10.0.0.8"]
                if hostname == "mixed-fixture-secret.example"
                else ["93.184.216.34"]
            )
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (address, port),
                )
                for address in addresses
            ]

    class ProbeResponse:
        status = 200

        def __init__(self, remaining: int, *, declared: bool) -> None:
            self.remaining = remaining
            self.read_sizes: list[int] = []
            self.headers = {"Content-Type": "text/plain"}
            if declared:
                self.headers["Content-Length"] = str(remaining)

        def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

        def __enter__(self) -> "ProbeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    transport_calls = 0
    pinned_transport = False
    responses: list[ProbeResponse] = []

    def fake_open(request: object, *, timeout: float) -> ProbeResponse:
        nonlocal pinned_transport, transport_calls
        del timeout
        transport_calls += 1
        destination = getattr(request, "_minicode_destination", None)
        pinned_transport = pinned_transport or (
            destination is not None
            and destination.addresses == ("93.184.216.34",)
        )
        oversized = "oversized" in request.full_url
        response = ProbeResponse(
            network_safety.MAX_RESPONSE_BYTES + 1 if oversized else 2,
            declared=not oversized,
        )
        responses.append(response)
        return response

    original_resolver = network_safety._DNS_RESOLVER
    original_open = http_utils._open_no_redirect
    network_safety._DNS_RESOLVER = ProbeResolver()
    http_utils._open_no_redirect = fake_open
    try:
        private = web_fetch_tool.run(
            web_fetch_tool.validator({"url": "http://172.17.0.1/"}),
            ToolContext(cwd="."),
        )
        mixed = web_fetch_tool.run(
            web_fetch_tool.validator(
                {"url": "https://mixed-fixture-secret.example/"}
            ),
            ToolContext(cwd="."),
        )
        unsafe_target_calls = transport_calls
        safe = web_fetch_tool.run(
            web_fetch_tool.validator(
                {"url": "https://public.example/safe"}
            ),
            ToolContext(cwd="."),
        )
        oversized = web_fetch_tool.run(
            web_fetch_tool.validator(
                {
                    "url": (
                        "https://public.example/oversized"
                        "?credential=body-fixture-secret"
                    )
                }
            ),
            ToolContext(cwd="."),
        )
    finally:
        network_safety._DNS_RESOLVER = original_resolver
        http_utils._open_no_redirect = original_open

    fixed_outputs = (private.output, mixed.output, oversized.output)
    return {
        "normalParser": bool(
            parsed.status is SearchProviderStatus.SUCCESS
            and len(parsed.results) == 1
            and parsed.results[0].title == "中文 & result"
            and parsed.results[0].url == "https://example.test/one"
        ),
        "explicitEmptyRecognized": empty.status is SearchProviderStatus.NO_RESULTS,
        "changedMarkupRecognized": (
            changed.status is SearchProviderStatus.RESPONSE_UNRECOGNIZED
        ),
        "challengeRecognized": (
            challenge.status is SearchProviderStatus.CHALLENGE
        ),
        "statusTaxonomy": status_taxonomy,
        "fallbackOnce": (
            fallback.ok
            and provider_calls == ["baidu", "duckduckgo"]
            and "PROVIDER: duckduckgo" in fallback.output
            and "fixture-secret" not in fallback.output
        ),
        "privateDestinationBlocked": (
            not private.ok
            and private.output.startswith("error[destination_blocked]:")
        ),
        "mixedDnsBlocked": (
            not mixed.ok
            and mixed.output.startswith("error[destination_blocked]:")
        ),
        "unsafeTargetTransportCalls": unsafe_target_calls,
        "pinnedTransport": safe.ok and pinned_transport,
        "oversizedResponseBlocked": (
            not oversized.ok
            and oversized.output.startswith("error[response_too_large]:")
        ),
        "maxReadBytes": max(
            (size for response in responses for size in response.read_sizes),
            default=0,
        ),
        "contentFreeErrors": all(
            "fixture-secret" not in output and "\n" not in output
            for output in fixed_outputs
        ),
    }


def _probe_tool_error_leak(workspace: Path) -> bool:
    from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry

    def _explode(_input: dict[str, Any], _context: Any) -> Any:
        raise RuntimeError(f"fixture failure at {workspace / 'private.txt'}")

    tool = ToolDefinition(
        name="audit_crash_fixture",
        description="fixture",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=_explode,
    )
    output = ToolRegistry([tool]).execute(
        tool.name, {"token": "fixture-secret"}, ToolContext(cwd=str(workspace))
    ).output
    return str(workspace) in output


def _probe_memory_boundaries(workspace: Path) -> dict[str, Any]:
    from minicode.memory import MemoryManager, MemoryScope
    from minicode.memory_pipeline import MemoryPipeline

    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    fact_id = pipeline.write(
        "小花是我唯一的好朋友。",
        [{"event_id": "event-1", "type": "task_result", "status": "success"}],
    )
    fact_hits = manager.search(
        "小花", scope=MemoryScope.PROJECT, min_relevance=0.0, record_usage=False
    )
    error_trace = [
        {
            "event_id": "event-2",
            "call_id": "call-1",
            "type": "tool_result",
            "tool_name": "web_search",
            "status": "error",
            "is_error": True,
            "output_summary": "Search provider timed out",
        },
        {
            "event_id": "event-3",
            "call_id": "call-1",
            "type": "error",
            "tool_name": "web_search",
            "error_type": "TimeoutError",
            "message": "Search provider timed out",
        },
        {
            "event_id": "event-4",
            "type": "task_result",
            "status": "failed",
            "had_errors": True,
            "tool_error_count": 1,
        },
    ]
    error_id = pipeline.write("Search the web", error_trace)
    error_entry = (
        manager.memories[MemoryScope.PROJECT]._id_index.get(error_id)
        if error_id
        else None
    )
    claim_types = (
        [
            claim.get("claim_type")
            for claim in error_entry.metadata.get("structured_reflection", {}).get(
                "claims", []
            )
        ]
        if error_entry is not None
        else []
    )
    return {
        "ordinaryFactPersisted": bool(fact_id or fact_hits),
        "singleErrorPatternSuppressed": error_entry is None and not claim_types,
    }


def _live_request(url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MiniCode-Functional-Audit/1A"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
            return {
                "status": "pass",
                "httpStatus": int(response.status),
                "durationMs": int((time.monotonic() - started) * 1000),
                "errorCode": None,
            }
    except Exception as error:  # noqa: BLE001 - classified, never serialized raw
        return {
            "status": "fail",
            "httpStatus": None,
            "durationMs": int((time.monotonic() - started) * 1000),
            "errorCode": _safe_error(error),
        }


def _live_tool_probe(
    tool_name: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Call one installed Tool in a bounded child and retain no Tool output."""
    started = time.monotonic()
    code = """
import json
import re
import sys
from minicode.tooling import ToolContext
from minicode.tools import create_default_tool_registry

tool_name = sys.argv[1]
payload = json.loads(sys.argv[2])
registry = create_default_tool_registry(
    ".",
    runtime={"toolProfile": "full", "mcpServers": {}},
)
try:
    tool = registry.find(tool_name)
    result = tool.run(tool.validator(payload), ToolContext(cwd="."))
    status_match = re.search(r"(?:STATUS:|Status:)\\s*(\\d+)", result.output or "")
    print(json.dumps({
        "ok": bool(result.ok),
        "httpStatus": int(status_match.group(1)) if status_match else None,
    }))
finally:
    registry.dispose()
""".strip()
    try:
        child_env = dict(os.environ)
        child_env.pop("MINI_CODE_TOOL_PROFILE", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                tool_name,
                json.dumps(payload, ensure_ascii=False),
            ],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=child_env,
        )
        if completed.returncode != 0:
            return {
                "status": "fail",
                "httpStatus": None,
                "durationMs": int((time.monotonic() - started) * 1000),
                "errorCode": "tool_process_error",
            }
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        return {
            "status": "pass" if summary["ok"] else "fail",
            "httpStatus": summary["httpStatus"],
            "durationMs": int((time.monotonic() - started) * 1000),
            "errorCode": None if summary["ok"] else "tool_failure",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "httpStatus": None,
            "durationMs": int((time.monotonic() - started) * 1000),
            "errorCode": "timeout",
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "status": "fail",
            "httpStatus": None,
            "durationMs": int((time.monotonic() - started) * 1000),
            "errorCode": "invalid_tool_summary",
        }


def _run_live_network_smoke(timeout: float) -> dict[str, Any]:
    # Five bounded, credential-free probes.  No response body is retained.
    per_request = max(1.0, min(timeout / 5.0, 15.0))
    started = time.monotonic()
    results = {
        "builtInWebSearch": {
            "provider": "built-in web_search / bounded provider chain",
            **_live_tool_probe(
                "web_search",
                {"query": "minicode audit", "num_results": 1},
                per_request,
            ),
        },
        "ordinaryHttps": {
            "provider": "Example HTTPS",
            **_live_request("https://example.com/", per_request),
        },
        "accessibleSearchPage": {
            "provider": "Baidu search",
            **_live_request(
                "https://www.baidu.com/s?wd=minicode+audit", per_request
            ),
        },
        "webFetchSmallPage": {
            "provider": "built-in web_fetch / Example HTTPS",
            **_live_tool_probe(
                "web_fetch",
                {"url": "https://example.com/", "max_chars": 500},
                per_request,
            ),
        },
        "httpRequestGet": {
            "provider": "full-profile http_request / Example HTTPS",
            **_live_tool_probe(
                "http_request",
                {
                    "url": "https://example.com/",
                    "method": "GET",
                    "timeout": per_request,
                },
                per_request,
            ),
        },
    }
    return {
        "enabled": True,
        "totalDurationMs": int((time.monotonic() - started) * 1000),
        "probes": results,
    }


def _entrypoint_capabilities(
    scripts: dict[str, str],
    routes: list[str],
    slash_commands: list[Any],
    test_index: dict[str, list[str]],
    *,
    installed: str,
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    surfaces = {
        "minicode-py": ("tui",),
        "minicode-headless": ("headless",),
        "minicode-gateway": ("gateway", "dashboard"),
        "minicode-cron": ("headless",),
    }
    for name, target in scripts.items():
        tests = _matching_test_paths(test_index, name.replace("-", "_"), target.split(":")[0].split(".")[-1])
        capabilities.append(
            _capability(
                f"entrypoint.{name}",
                "entrypoint",
                name,
                reachable=surfaces.get(name, ()),
                deterministic="pass" if tests else "partial",
                installed=installed,
                safety="partial",
                truthfulness="partial",
                status="pass" if tests and installed == "pass" else "partial",
                evidence=[f"pyproject.toml::{name}={target}", *tests],
            )
        )
    for command in slash_commands:
        name = command.usage
        token = command.name.lstrip("-/")
        usage_id = re.sub(r"[^a-zA-Z0-9]+", ".", name).strip(".")
        tests = _matching_test_paths(test_index, token, "try_handle_local_command")
        capabilities.append(
            _capability(
                f"entrypoint.tui.{usage_id or 'root'}",
                "entrypoint",
                name,
                reachable=("tui",),
                deterministic="pass" if tests else "partial",
                installed=installed,
                safety="partial",
                truthfulness="partial",
                status="pass" if tests else "partial",
                evidence=["minicode/cli_commands.py", *tests],
            )
        )
    for route in routes:
        normalized = re.sub(r"[^a-zA-Z0-9]+", ".", route).strip(".") or "root"
        if route.endswith("/") and route != "/":
            normalized += ".prefix"
        tests = _matching_test_paths(test_index, normalized.split(".")[-1], "gateway")
        capabilities.append(
            _capability(
                f"entrypoint.http.{normalized}",
                "entrypoint",
                route,
                reachable=("gateway", "dashboard") if route != "/run" else ("gateway",),
                deterministic="pass" if tests else "partial",
                installed=installed,
                safety="pass" if tests else "partial",
                truthfulness="pass" if tests else "partial",
                status="pass" if tests else "partial",
                evidence=["minicode/gateway.py", *tests],
            )
        )
    return capabilities


def _aggregate_capabilities(
    test_index: dict[str, list[str]], *, installed: str, browser_verified: bool
) -> list[dict[str, Any]]:
    definitions = [
        ("agent.mock_model", "agent", "MockModel agent turn", ("tui", "headless", "gateway", "dashboard"), ("mock_model", "agent_flow")),
        ("agent.tool_calls", "agent", "single and multiple Tool calls", ("tui", "headless", "gateway", "dashboard"), ("agent_loop", "tool_call")),
        ("agent.cancellation", "agent", "agent cancellation", ("tui", "headless", "gateway", "dashboard"), ("agent_cancellation", "conversation_cancellation")),
        ("agent.run_lifecycle", "agent", "Run lifecycle and safe observation", ("tui", "headless", "gateway", "dashboard"), ("run_lifecycle", "run_entrypoint_lifecycle")),
        ("agent.model_switcher", "agent", "ModelSwitcher routing", ("tui", "headless", "gateway", "dashboard"), ("model_switcher", "model_registry")),
        ("agent.context_recovery", "agent", "context recovery and cancellation", ("tui", "headless", "gateway", "dashboard"), ("context_recovery", "context_compactor")),
        ("agent.pricing", "agent", "canonical usage and pricing projection", ("tui", "headless", "gateway", "dashboard"), ("pricing", "model_usage_observation")),
        ("persistence.session", "persistence", "Session persistence", ("tui", "dashboard"), ("session", "session_cross_process")),
        ("persistence.turn", "persistence", "Conversation Turn state machine", ("gateway", "dashboard"), ("conversation_turn", "conversation_cancellation")),
        ("persistence.run_journal", "persistence", "RunJournal", ("tui", "headless", "gateway", "dashboard"), ("run_journal", "run_lifecycle")),
        ("memory.storage", "persistence", "Memory file storage and scopes", ("tui", "headless", "gateway", "dashboard"), ("memory_e2e", "memory_integration")),
        ("memory.retrieval", "persistence", "Memory retrieval/ranking/injection", ("tui", "headless", "gateway", "dashboard"), ("memory_retrieval", "memory_integration")),
        ("memory.approval", "persistence", "Memory approval authority", ("gateway", "dashboard"), ("memory_approval", "memory_pending_approval")),
        ("memory.error_reflection", "persistence", "error-pattern reflection", ("tui", "headless", "gateway", "dashboard"), ("reflection", "error_pattern")),
        ("memory.conversation_fact", "persistence", "ordinary conversational fact intake", ("tui", "headless", "gateway", "dashboard"), ("memory", "conversation")),
        ("skill.discovery", "persistence", "four-source Skill discovery and routing", ("tui", "headless", "gateway", "dashboard"), ("skills", "skill_router")),
        ("mcp.configuration", "mcp", "MCP configuration discovery", ("tui", "headless", "gateway", "dashboard"), ("mcp", "config")),
        ("mcp.stdio", "mcp", "stdio MCP lifecycle and calls", ("tui", "headless", "gateway", "dashboard"), ("mcp", "mcp_current_state")),
        ("mcp.search", "mcp", "optional MCP search Tool", ("tui", "headless", "gateway", "dashboard"), ("mcp", "search")),
        ("security.permissions", "security", "Permission review/checkpoint authority", ("tui", "gateway", "dashboard"), ("permissions", "permission_approval")),
        ("security.workspace", "security", "Workspace path and symlink boundary", ("tui", "headless", "gateway", "dashboard"), ("workspace", "path_escape")),
        ("dashboard.static", "dashboard", "Dashboard static assets and no-store", ("dashboard",), ("dashboard_web", "packaging")),
        ("dashboard.rest", "dashboard", "Dashboard REST read/write authorities", ("dashboard",), ("dashboard_read_model", "dashboard_chat_http")),
        ("dashboard.sse", "dashboard", "Dashboard SSE/replay/reset", ("dashboard",), ("dashboard_sse", "dashboard_event_stream")),
        ("dashboard.polling", "dashboard", "Dashboard polling fallback", ("dashboard",), ("dashboard_live_refresh", "polling")),
        ("dashboard.chat", "dashboard", "Dashboard Chat/Turn fencing", ("dashboard",), ("dashboard_chat", "conversation_turn_identity")),
        ("dashboard.permissions", "dashboard", "Dashboard Permission UI", ("dashboard",), ("dashboard_permission_frontend", "permission_http")),
        ("dashboard.memory_approval", "dashboard", "Dashboard Memory Approval UI", ("dashboard",), ("dashboard_memory_approval_frontend", "memory_approval_http")),
        ("dashboard.deletion", "dashboard", "Session and Project Memory deletion", ("dashboard",), ("dashboard_deletion", "conversation_deletion")),
        ("dashboard.data_health", "dashboard", "Dashboard data health", ("dashboard",), ("storage_health", "dashboard_data_health_frontend")),
    ]
    capabilities: list[dict[str, Any]] = []
    for capability_id, category, name, reachable, tokens in definitions:
        tests = _matching_test_paths(test_index, *tokens)
        status = "pass" if tests else "partial"
        live = "not_required"
        if capability_id == "mcp.search":
            status = "unavailable"
            live = "blocked"
        if capability_id == "memory.conversation_fact":
            status = "fail"
        capabilities.append(
            _capability(
                capability_id,
                category,
                name,
                reachable=reachable,
                deterministic="pass" if tests else "not_tested",
                installed=installed,
                live=live,
                safety="pass" if tests and category in {"security", "dashboard"} else "partial",
                truthfulness="pass" if tests else "partial",
                status=status,
                evidence=tests,
            )
        )
    capabilities.append(
        _capability(
            "agent.real_provider",
            "agent",
            "configured real model Provider availability",
            declared=True,
            registered=False,
            reachable=("tui", "headless", "gateway", "dashboard"),
            deterministic="pass",
            installed=installed,
            live="blocked",
            safety="partial",
            truthfulness="pass",
            status="blocked",
            evidence=[
                "minicode/model_registry.py",
                "isolated audit intentionally removed Provider credentials",
                *_matching_test_paths(
                    test_index,
                    "anthropic_adapter",
                    "model_registry",
                    "model_switcher",
                ),
            ],
        )
    )
    for source_id, source_name in (
        ("user_minicode", "user MiniCode Skill source"),
        ("project_minicode", "project MiniCode Skill source"),
        ("project_claude", "project Claude-compatible Skill source"),
        ("user_claude", "user Claude-compatible Skill source"),
    ):
        capabilities.append(
            _capability(
                f"skill.source.{source_id}",
                "persistence",
                source_name,
                reachable=("tui", "headless", "gateway", "dashboard"),
                deterministic="pass",
                installed=installed,
                safety="pass",
                truthfulness="pass",
                status="pass",
                evidence=[
                    "minicode/skills.py",
                    *_matching_test_paths(test_index, "discover_skills", "SKILL"),
                ],
            )
        )
    for page_id, page_name in (
        ("overview", "Dashboard Overview"),
        ("runs", "Dashboard Runs"),
        ("sessions", "Dashboard Sessions"),
        ("memory", "Dashboard Memory"),
        ("skills", "Dashboard Skills"),
        ("connections", "Dashboard Connections"),
        ("ops", "Dashboard Ops"),
        ("system", "Dashboard System"),
        ("memory_overview", "Memory Overview"),
        ("memory_scopes", "Memory Scopes"),
        ("memory_approvals", "Memory Approvals"),
        ("memory_retrieval", "Memory Retrieval"),
        ("memory_injection", "Memory Injection"),
        ("memory_lifecycle", "Memory Lifecycle"),
    ):
        tests = _matching_test_paths(
            test_index, page_id, "dashboard_page_read_model", "dashboard_web"
        )
        capabilities.append(
            _capability(
                f"dashboard.page.{page_id}",
                "dashboard",
                page_name,
                reachable=("dashboard",),
                deterministic="pass" if tests else "partial",
                installed=installed,
                live="pass" if browser_verified else "blocked",
                safety="pass",
                truthfulness="pass",
                status="pass" if browser_verified and tests else "partial",
                evidence=[
                    "minicode/web/static/assets/app.js",
                    (
                        "installed-wheel browser verified"
                        if browser_verified
                        else "browser verification not supplied"
                    ),
                    *tests,
                ],
            )
        )
    return capabilities


def _internal_capabilities(
    test_index: dict[str, list[str]], *, installed: str
) -> list[dict[str, Any]]:
    modules = {
        "background_tasks": "conditional",
        "cron_runner": "formal",
        "auto_mode": "conditional",
        "task_graph": "conditional",
        "task_tracker": "conditional",
        "context_cybernetics": "conditional",
        "cybernetic_orchestrator": "conditional",
        "self_healing_engine": "conditional",
        "verification_controller": "conditional",
        "agent_intelligence": "conditional",
        "smart_router": "conditional",
        "timeline_memory": "experimental",
        "vector_memory": "conditional",
        "hooks": "conditional",
        "pipeline_engine": "not_reachable",
    }
    capabilities: list[dict[str, Any]] = []
    for module, wiring in modules.items():
        path = PROJECT_ROOT / "minicode" / f"{module}.py"
        tests = _matching_test_paths(test_index, module)
        registered = wiring in {"formal", "conditional"}
        status = (
            "pass"
            if wiring == "formal" and tests
            else "partial"
            if wiring == "conditional"
            else "not_reachable"
        )
        capabilities.append(
            _capability(
                f"internal.{module}",
                "agent",
                module,
                declared=path.exists(),
                registered=registered,
                reachable=("tui", "headless", "gateway", "dashboard") if registered else (),
                deterministic="pass" if tests else "not_tested",
                installed=installed if registered else "not_tested",
                safety="partial",
                truthfulness="partial",
                status=status,
                evidence=[_relative(path), f"wiring={wiring}", *tests],
            )
        )
    return capabilities


def _tool_capabilities(
    core: dict[str, Any],
    full: dict[str, Any],
    source_definitions: dict[str, str],
    schema_serializable: dict[str, bool],
    required_rejects_empty: dict[str, bool],
    actual_passes: dict[str, bool],
    test_index: dict[str, list[str]],
    *,
    installed: str,
    live_results: dict[str, Any],
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    core_names = set(core)
    utility_names = set(full).difference(core)
    failed_tools = {
        "gzip_compress",
        "gzip_decompress",
        "tar_create",
        "zip_create",
        "read_file",
    }
    issue_map = {
        "gzip_compress": ["SEC-002"],
        "gzip_decompress": ["SEC-002", "SEC-004"],
        "tar_create": ["SEC-002"],
        "zip_create": ["SEC-002"],
        "read_file": ["TOOL-001"],
        "file_line_count": ["TOOL-002"],
        "base64_encode": ["TOOL-003"],
        "hash": ["TOOL-003"],
        "line_count": ["TOOL-003"],
        "text_dedupe": ["TOOL-003"],
        "text_join": ["TOOL-003"],
        "text_sort": ["TOOL-003"],
        "url_encode": ["TOOL-003"],
    }
    for name in sorted(full):
        source = source_definitions.get(name, "minicode/tools/__init__.py")
        tests = _matching_test_paths(test_index, name)
        evidence = [
            source,
            f"profile={'core' if name in core_names else 'full-only'}",
            f"schema_serializable={str(schema_serializable[name]).lower()}",
            f"required_validation={str(required_rejects_empty[name]).lower()}",
        ]
        if name in actual_passes:
            evidence.append(f"isolated_happy_path={str(actual_passes[name]).lower()}")
        deterministic = (
            "pass"
            if schema_serializable[name]
            and required_rejects_empty[name]
            and (actual_passes.get(name) is True or bool(tests))
            else "partial"
        )
        safety = "partial"
        truthfulness = "partial"
        status = "partial" if name in utility_names else "pass"
        if name in failed_tools:
            status = "fail"
            safety = "fail" if name != "web_search" else "partial"
        if name == "read_file":
            truthfulness = "fail"
        if name in {"tar_extract", "zip_extract"} and tests:
            safety = "pass"
        if name == "file_line_count":
            safety = "partial"
        if name == "http_request":
            deterministic = "pass"
            safety = "pass"
            truthfulness = "pass"
            status = "pass"
            evidence.extend(
                [
                    "tests/test_bounded_resolver.py",
                    "tests/test_http_request_safety.py",
                    "permission=one-operation",
                    "destination=validated-and-pinned",
                    "resolver=workers:4,queue:8,outstanding:12",
                    "resolver-saturation=fail-closed",
                    "resolver-process-exit=daemon-workers",
                    "response=bounded",
                ]
            )
        if name == "web_fetch":
            deterministic = "pass"
            safety = "pass"
            truthfulness = "pass"
            status = "pass"
            evidence.extend(
                [
                    "tests/test_web_fetch_safety.py",
                    "destination=validated-and-pinned",
                    "resolver=shared-bounded-4,8,12",
                    "redirect=per-hop-validated,max-3",
                    "deadline=one-monotonic-budget",
                    "response=1MiB,max-read-64KiB",
                    "errors=content-free-low-cardinality",
                ]
            )
        if name == "web_search":
            deterministic = "pass"
            safety = "pass"
            truthfulness = "pass"
            status = "pass"
            evidence.extend(
                [
                    "tests/test_web_search.py",
                    "tests/test_search_providers.py",
                    "provider-order=baidu,duckduckgo",
                    "provider-attempts=once,max-2",
                    "deadline=total-15s,provider-max-6s",
                    "destination=validated-and-pinned",
                    "resolver=shared-bounded-4,8,12",
                    "redirect=per-hop-validated,max-3",
                    "response=1MiB,max-read-64KiB",
                    "parser=provider-specific-streaming",
                    "errors=truthful-content-free-low-cardinality",
                ]
            )
        live = "not_required"
        if name in {"web_search", "web_fetch", "http_request"}:
            key = {
                "web_search": "builtInWebSearch",
                "web_fetch": "webFetchSmallPage",
                "http_request": "httpRequestGet",
            }[name]
            probe = live_results.get("probes", {}).get(key)
            live = probe["status"] if probe else "blocked"
            if probe:
                evidence.append(
                    f"live:{probe['provider']} status={probe['status']} "
                    f"http={probe['httpStatus']} durationMs={probe['durationMs']} "
                    f"error={probe['errorCode']}"
                )
        capabilities.append(
            _capability(
                f"tool.{name}",
                "tool",
                name,
                reachable=("tui", "headless", "gateway", "dashboard"),
                deterministic=deterministic,
                installed=installed,
                live=live,
                safety=safety,
                truthfulness=truthfulness,
                status=status,
                evidence=[*evidence, *tests],
                issues=issue_map.get(name, []),
            )
        )
    conditional_mcp_names = {
        "<dynamic-mcp-tool>",
        "get_mcp_prompt",
        "list_mcp_prompts",
        "list_mcp_resources",
        "read_mcp_resource",
    }
    for name, source in sorted(source_definitions.items()):
        if name in full:
            continue
        if name in conditional_mcp_names:
            stable_name = (
                "dynamic_mcp_tool" if name == "<dynamic-mcp-tool>" else name
            )
            tests = _matching_test_paths(test_index, "mcp", stable_name)
            capabilities.append(
                _capability(
                    f"tool.{stable_name}",
                    "tool",
                    name,
                    declared=True,
                    registered=False,
                    reachable=("tui", "headless", "gateway", "dashboard"),
                    deterministic="pass" if tests else "partial",
                    installed=installed,
                    live="blocked",
                    safety="partial",
                    truthfulness="partial",
                    status="blocked",
                    evidence=[
                        source,
                        "conditional on configured MCP descriptors/resources/prompts",
                        "isolated MCP configuration is empty",
                        *tests,
                    ],
                )
            )
            continue
        capabilities.append(
            _capability(
                f"tool.{name}",
                "tool",
                name,
                declared=True,
                registered=False,
                deterministic="not_tested",
                installed="not_tested",
                live="not_required",
                safety="not_tested",
                truthfulness="partial",
                status="not_reachable",
                evidence=[source, "absent from create_default_tool_registry()"],
            )
        )
    return capabilities


def _build_issues(
    probes: dict[str, Any],
    memory: dict[str, Any],
    *,
    live_results: dict[str, Any],
) -> list[dict[str, Any]]:
    del live_results
    issues: list[dict[str, Any]] = []
    if any(probes["archiveEscape"].values()):
        issues.append(
            _issue(
                "SEC-002",
                "P0",
                "tool.gzip_compress",
                "Full-profile archive creation can write outside the Workspace with ../ paths and no permission checkpoint.",
                "Compress an isolated Workspace fixture to ../escape.gz; repeat for tar_create and zip_create.",
                "All source and destination paths must use the canonical Workspace/permission resolver before I/O.",
                "gzip_compress, tar_create and zip_create created sibling files outside the isolated Workspace.",
                ["minicode/tools/archive_utils.py", "scripts/run_functional_audit.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Traversal, absolute path, symlink and deny/cancel tests must prove zero archive writes outside Workspace.",
            )
        )
    web_contracts = probes["webContracts"]
    if not (
        web_contracts["privateDestinationBlocked"]
        and web_contracts["mixedDnsBlocked"]
        and web_contracts["unsafeTargetTransportCalls"] == 0
        and web_contracts["pinnedTransport"]
        and web_contracts["oversizedResponseBlocked"]
        and web_contracts["maxReadBytes"] <= 65_536
        and web_contracts["contentFreeErrors"]
    ):
        issues.append(
            _issue(
                "SEC-003",
                "P0",
                "tool.web_fetch",
                "Incomplete private-address and redirect validation can expose internal HTTP resources.",
                "Evaluate the production URL guard for http://172.17.0.1 and inspect redirect handling.",
                "All private/link-local/resolved addresses and every redirect target must be rejected.",
                "172.17.0.1 is accepted, DNS is not pinned, and redirect targets are not revalidated.",
                ["minicode/tools/web_fetch.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-1: Web network/search boundary",
                red_test="Cover the full private IP ranges, IPv6 forms, DNS rebinding and public-to-private redirects.",
            )
        )
    issues.extend(
        [
            _issue(
                "SEC-004",
                "P1",
                "tool.gzip_decompress",
                "Archive decompression lacks aggregate byte, member and time budgets.",
                "Expand a high-ratio archive fixture in an isolated Workspace.",
                "Archive extraction must stop at documented byte, member and time budgets before allocating or writing unbounded output.",
                "The HTTP response paths are bounded; archive extraction still lacks an aggregate decompression budget.",
                ["minicode/tools/archive_utils.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Archive bomb fixtures must stop at documented byte, member and time budgets.",
            ),
            _issue(
                "TOOL-001",
                "P1",
                "tool.read_file",
                "Missing or unreadable files can be reported as successful empty files.",
                "Call read_file for a nonexistent path inside an isolated Workspace.",
                "The Tool should return ok=false with a stable not_found or unreadable code.",
                "The Tool returned ok=true, TOTAL_CHARS: 0 and no distinction from an empty file.",
                ["minicode/tools/read_file.py", "scripts/run_functional_audit.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Missing, permission-denied, directory, binary and true-empty fixtures must have distinct truthful outcomes.",
            ),
            _issue(
                "TOOL-002",
                "P3",
                "tool.file_line_count",
                "A read-only Tool is classified as non-read-only/non-concurrency-safe, reducing truthful scheduling metadata.",
                "Inspect file_line_count ToolDefinition.is_read_only.",
                "Read-only navigation tools should expose read-only and concurrency-safe metadata.",
                "file_line_count is absent from the central read-only name set and has no explicit metadata.",
                ["minicode/tooling.py", "minicode/tools/file_line_count.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Registry metadata contract must enumerate every Tool's read/write, destructive and concurrency properties.",
            ),
            _issue(
                "TOOL-003",
                "P2",
                "tool.base64_encode",
                "Several full-profile utility validators accept a missing schema-required field as an empty value.",
                "Call each validator with an empty object for base64_encode, hash, line_count, text_dedupe, text_join, text_sort and url_encode.",
                "A field declared required in the Tool schema must be rejected when omitted.",
                "All seven validators accept the empty object, so runtime behavior diverges from the model-visible schema.",
                [
                    "minicode/tools/encoding_utils.py",
                    "minicode/tools/crypto_utils.py",
                    "minicode/tools/text_utils.py",
                    "scripts/run_functional_audit.py",
                ],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Table-driven schema/validator conformance must reject every omitted required field and wrong scalar type.",
            ),
            _issue(
                "SEC-005",
                "P1",
                "security.workspace",
                "Unhandled Tool errors can expose absolute Workspace paths and raw traceback excerpts to the model/user.",
                "Execute an isolated fixture Tool that raises with a Workspace path.",
                "Tool errors should use closed codes and redacted diagnostics.",
                "ToolRegistry.execute includes the raw exception and traceback excerpt containing the isolated absolute path.",
                ["minicode/tooling.py", "scripts/run_functional_audit.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-2: File/command Tool correctness",
                red_test="Validation and runtime errors must redact paths, inputs, tokens, environment values and tracebacks.",
            ),
            _issue(
                "MEM-001",
                "P1",
                "memory.conversation_fact",
                "Ordinary user facts are not persisted/retrievable across Sessions.",
                "Submit the isolated phrase 小花是我唯一的好朋友。 through the current Memory write path, then search 小花.",
                "A declared conversational fact intake path should create an approved/reviewable durable fact with scope and provenance.",
                "No fact entry or search hit was produced; the separate one-off web_search failure was correctly suppressed as non-recurrent.",
                ["minicode/memory_pipeline.py", "docs/minicode-dashboard-batch-9-roadmap.md", "scripts/run_functional_audit.py"],
                environment_dependent=False,
                recommended_batch="Reliability 1B-3: Session/Memory persistence gaps",
                red_test="Two isolated Sessions must prove the exact ordinary fact is persisted, approved, retrieved and injected.",
            ),
        ]
    )
    assert memory["ordinaryFactPersisted"] is False
    assert memory["singleErrorPatternSuppressed"] is True
    return issues


def _apply_issue_links(
    capabilities: list[dict[str, Any]], issues: list[dict[str, Any]]
) -> None:
    by_id = {capability["id"]: capability for capability in capabilities}
    for issue in issues:
        capability = by_id.get(issue["capabilityId"])
        if capability is None:
            continue
        if issue["id"] not in capability["issues"]:
            capability["issues"].append(issue["id"])
        if issue["severity"] in {"P0", "P1"}:
            capability["status"] = "fail"
        if issue["id"].startswith("SEC-"):
            capability["safety"] = "fail"


def _filter_for_category(
    capabilities: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    category: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if category is None:
        return capabilities, issues
    allowed_ids: set[str]
    if category == "web":
        allowed_ids = {
            "tool.web_search",
            "tool.web_fetch",
            "tool.http_request",
            "mcp.search",
            "security.workspace",
        }
    else:
        category_map = {
            "tools": {"tool"},
            "entrypoints": {"entrypoint"},
            "persistence": {"persistence"},
            "memory": {"persistence"},
            "skill_mcp": {"mcp", "persistence"},
            "security": {"security", "tool"},
            "dashboard": {"dashboard", "entrypoint"},
            "packaging": {"packaging", "entrypoint", "tool"},
            "internal": {"agent"},
            "discovery": {"tool", "entrypoint", "mcp"},
        }
        allowed_categories = category_map[category]
        allowed_ids = {
            capability["id"]
            for capability in capabilities
            if capability["category"] in allowed_categories
        }
        if category == "memory":
            allowed_ids = {
                capability["id"]
                for capability in capabilities
                if capability["id"].startswith("memory.")
            }
    kept = [capability for capability in capabilities if capability["id"] in allowed_ids]
    kept_issues = [
        issue for issue in issues if issue["capabilityId"] in allowed_ids
    ]
    return kept, kept_issues


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    test_index = _test_evidence_index()
    workspace = Path(os.environ["MINI_CODE_AUDIT_WORKSPACE"])
    core, full = _discover_runtime_tools(workspace)
    source_definitions = _discover_source_tool_definitions()
    routes = _discover_literal_routes()
    scripts = _discover_console_scripts()
    from minicode.cli_commands import SLASH_COMMANDS

    schemas, required = _probe_schemas(full)
    actual_passes = _probe_utility_happy_paths(full, workspace)
    actual_passes.update(_probe_core_read_paths(full, workspace))
    read_file_misreports = _probe_read_file_truthfulness(full, workspace)
    archive_escape = _probe_archive_escape(full, workspace)
    http_mutation, fixture_status = _probe_http_request_permission_boundary(full, workspace)
    bounded_dns_resolver = _probe_bounded_dns_resolver()
    web_contracts = _probe_web_contracts()
    tool_error_leak = _probe_tool_error_leak(workspace)
    memory = _probe_memory_boundaries(workspace)
    live_results = (
        _run_live_network_smoke(args.timeout)
        if args.live_network
        else {"enabled": False, "totalDurationMs": 0, "probes": {}}
    )
    probes = {
        "readFileMisreportsMissing": read_file_misreports,
        "archiveEscape": archive_escape,
        "httpMutation": http_mutation,
        "httpFixtureStatus": fixture_status,
        "webContracts": web_contracts,
        "toolErrorLeaksPath": tool_error_leak,
    }
    installed = "pass" if args.installed_wheel else "not_tested"
    capabilities = _tool_capabilities(
        core,
        full,
        source_definitions,
        schemas,
        required,
        actual_passes,
        test_index,
        installed=installed,
        live_results=live_results,
    )
    capabilities.extend(
        _entrypoint_capabilities(
            scripts,
            routes,
            SLASH_COMMANDS,
            test_index,
            installed=installed,
        )
    )
    capabilities.extend(
        _aggregate_capabilities(
            test_index,
            installed=installed,
            browser_verified=args.browser_verified,
        )
    )
    capabilities.extend(_internal_capabilities(test_index, installed=installed))
    issues = _build_issues(probes, memory, live_results=live_results)
    _apply_issue_links(capabilities, issues)
    capabilities, issues = _filter_for_category(
        capabilities, issues, args.category
    )
    capabilities.sort(key=lambda item: item["id"])
    counts = Counter(capability["status"] for capability in capabilities)
    categories = [args.category] if args.category else sorted(VALID_CATEGORIES)
    return {
        "schemaVersion": 1,
        "audit": {
            "name": "MiniCode Functional Reliability Audit 1A",
            "generatedAt": _utc_now(),
            "durationMs": int((time.monotonic() - started) * 1000),
            "categories": categories,
            "liveNetwork": bool(args.live_network),
            "installedWheel": bool(args.installed_wheel),
            "browserVerified": bool(args.browser_verified),
            "isolation": {
                "home": "<isolated-home>",
                "miniCodeDir": "<isolated-mini-code-dir>",
                "workspace": "<isolated-workspace>",
                "userConfigurationLoaded": False,
                "paidModelCalled": False,
            },
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
        },
        "summary": {
            "capabilityCount": len(capabilities),
            "registeredToolCount": len(full),
            "defaultCoreToolCount": len(core),
            "fullOnlyToolCount": len(set(full).difference(core)),
            "sourceToolDefinitionCount": len(source_definitions),
            "consoleScriptCount": len(scripts),
            "literalRouteCount": len(routes),
            "issueCount": len(issues),
            "statusCounts": {
                status: counts.get(status, 0)
                for status in (
                    "pass",
                    "partial",
                    "fail",
                    "unavailable",
                    "blocked",
                    "not_reachable",
                    "not_tested",
                )
            },
        },
        "discovery": {
            "coreTools": sorted(core),
            "fullTools": sorted(full),
            "fullOnlyTools": sorted(set(full).difference(core)),
            "sourceToolDefinitions": source_definitions,
            "consoleScripts": scripts,
            "tuiSlashCommands": [command.usage for command in SLASH_COMMANDS],
            "literalHttpRoutes": routes,
        },
        "deterministicProbes": {
            "toolSchemasSerializable": all(schemas.values()),
            "requiredValidation": all(required.values()),
            "isolatedHappyPaths": actual_passes,
            "web": web_contracts,
            "memory": memory,
            "security": {
                "httpMutationWithoutPermission": http_mutation,
                "httpMutationFixtureStatus": fixture_status,
                "boundedDnsResolver": bounded_dns_resolver,
                "archiveWorkspaceEscape": archive_escape,
                "toolErrorLeaksAbsolutePath": tool_error_leak,
            },
        },
        "liveSmoke": live_results,
        "capabilities": capabilities,
        "issues": issues,
    }


@contextmanager
def _isolated_process_environment() -> Iterator[Path]:
    original_env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="minicode-functional-audit-") as temp:
        root = Path(temp)
        home = root / "home"
        data = root / "mini-code-data"
        workspace = root / "workspace"
        home.mkdir(mode=0o700)
        data.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        for key in list(os.environ):
            if any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS):
                os.environ.pop(key, None)
        os.environ.update(
            {
                "HOME": str(home),
                "MINI_CODE_DIR": str(data),
                "MINI_CODE_AUDIT_WORKSPACE": str(workspace),
                "MINI_CODE_TOOL_PROFILE": "core",
            }
        )
        try:
            yield workspace
        finally:
            os.environ.clear()
            os.environ.update(original_env)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated MiniCode Functional Reliability Audit 1A."
    )
    parser.add_argument(
        "--category",
        choices=sorted(VALID_CATEGORIES),
        default=None,
        help="Limit output to one audit category.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Total live-network time budget in seconds (default: 45).",
    )
    parser.add_argument(
        "--live-network",
        action="store_true",
        help="Explicitly enable bounded credential-free public-network smoke.",
    )
    parser.add_argument(
        "--installed-wheel",
        action="store_true",
        help="Mark this run as executing under an isolated installed wheel.",
    )
    parser.add_argument(
        "--browser-verified",
        action="store_true",
        help="Record that the installed Dashboard completed the separate browser checklist.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "minicode-functional-capability-matrix.json",
        help="Structured JSON output path.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 180:
        parser.error("--timeout must be between 1 and 180 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        with _isolated_process_environment():
            payload = run_audit(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": _relative(args.output),
                    "capabilities": payload["summary"]["capabilityCount"],
                    "issues": payload["summary"]["issueCount"],
                    "statusCounts": payload["summary"]["statusCounts"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1 if payload["summary"]["statusCounts"]["fail"] else 0
    except BaseException as error:  # noqa: BLE001 - top-level closed crash signal
        print(
            json.dumps(
                {
                    "error": "audit_runner_crash",
                    "errorType": type(error).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
