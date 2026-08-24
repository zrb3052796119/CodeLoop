from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_console_script_entry_points_import() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    failures = []
    for name, target in pyproject["project"]["scripts"].items():
        module_name, _, attr_name = target.partition(":")
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: cannot import {module_name}: {exc}")
            continue
        if not hasattr(module, attr_name):
            failures.append(f"{name}: {module_name}.{attr_name} does not exist")

    assert failures == []


def test_dashboard_static_assets_are_declared_as_package_data() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    patterns = set(pyproject["tool"]["setuptools"]["package-data"]["minicode.web"])

    assert "static/*.html" in patterns
    assert "static/assets/*.css" in patterns
    assert "static/assets/*.js" in patterns


def test_dashboard_assets_load_from_an_installed_wheel(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", source / "README.md")
    shutil.copytree(
        ROOT / "minicode",
        source / "minicode",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    wheel_dir = tmp_path / "wheel"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(source),
        ],
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        packaged = set(archive.namelist())
        packaged_app = archive.read("minicode/web/static/assets/app.js").decode("utf-8")
        packaged_styles = archive.read("minicode/web/static/assets/styles.css").decode("utf-8")
    assert {
        "minicode/agent_runtime.py",
        "minicode/conversation.py",
        "minicode/conversation_deletion.py",
        "minicode/conversation_presentation.py",
        "minicode/deletion_store.py",
        "minicode/file_review.py",
        "minicode/permission_approval.py",
        "minicode/memory_approval.py",
        "minicode/memory_store.py",
        "minicode/project_memory_deletion.py",
        "minicode/permission_event_contract.py",
        "minicode/tools/bounded_resolver.py",
        "minicode/tools/http_utils.py",
        "minicode/tools/network_safety.py",
        "minicode/tools/search_providers.py",
        "minicode/tools/web_fetch.py",
        "minicode/tools/web_search.py",
        "minicode/conversation_turn_store.py",
        "minicode/turn_cancellation.py",
        "minicode/run_lifecycle.py",
        "minicode/run_journal.py",
        "minicode/pricing.py",
        "minicode/working_memory.py",
        "minicode/mcp_event_contract.py",
        "minicode/mcp_current_state.py",
        "minicode/mcp_observation.py",
        "minicode/session_store.py",
        "minicode/storage_health.py",
        "minicode/web/context_aggregation.py",
        "minicode/web/change_feed.py",
        "minicode/web/event_stream.py",
        "minicode/web/chat_http.py",
        "minicode/web/chat_stream.py",
        "minicode/web/data_management_http.py",
        "minicode/web/permission_http.py",
        "minicode/web/memory_approval_http.py",
        "minicode/web/storage_health_http.py",
        "minicode/web/cost_aggregation.py",
        "minicode/web/mcp_current_projection.py",
        "minicode/web/mcp_runtime_aggregation.py",
        "minicode/web/tool_aggregation.py",
        "minicode/web/static/index.html",
        "minicode/web/static/assets/app.js",
        "minicode/web/static/assets/cost-format.js",
        "minicode/web/static/assets/styles.css",
    }.issubset(packaged)
    assert "const memoryApprovalStore" in packaged_app
    assert "PERMISSION_NETWORK_FINGERPRINT_PATTERN" in packaged_app
    assert "safePermissionNetworkHostname" in packaged_app
    assert "validateMemoryApprovalPendingPayload" in packaged_app
    assert "fetch('/api/v1/memory/approvals/pending'" in packaged_app
    assert "fetch('/api/v1/data-health'" in packaged_app
    assert "validateDataHealthPayload" in packaged_app
    assert ".memory-approval-workspace" in packaged_styles
    assert ".data-health-grid" in packaged_styles
    assert not any("dashboard_prototype" in path for path in packaged)

    installed = tmp_path / "installed"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    smoke_script = """
import json
import http.client
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import minicode
from minicode.gateway import MiniCodeGatewayHandler
from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import ConversationTurnStore, request_fingerprint
from minicode.mcp import StdioMcpClient
from minicode.mcp_current_state import (
    McpCurrentStateRegistry,
    normalize_mcp_current_state_snapshot,
)
from minicode.mcp_observation import mcp_server_key
from minicode.permission_approval import PermissionApprovalBroker
from minicode.permissions import PermissionManager as InstalledPermissionManager
from minicode.memory import MemoryApprovalPolicy, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority
from minicode.run_journal import RunJournal
from minicode.session import (
    AutosaveManager,
    create_new_session,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from minicode.session_store import SESSION_STORE_LOCK_NAME
import minicode.agent_loop as agent_loop_module
import minicode.capability_registry as capability_module
import minicode.config as config_module
import minicode.intent_parser as intent_module
import minicode.logging_config as logging_module
import minicode.memory as memory_module
import minicode.model_registry as model_module
import minicode.permissions as permissions_module
import minicode.prompt as prompt_module
import minicode.skill_router as skill_router_module
import minicode.tools as tools_module
import minicode.tools.http_utils as http_utils_module
import minicode.tools.network_safety as network_safety_module
import minicode.tools.search_providers as search_providers_module
from minicode.context_manager import ContextManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.bounded_resolver import BoundedResolver, ResolverError
from minicode.tools.write_file import write_file_tool
from minicode.tui.session_flow import consume_finished_tty_turn
from minicode.tui.state import ScreenState, TtyAppArgs
from minicode.tui.types import TranscriptEntry
from minicode.types import AgentStep, ModelUsage
from minicode.turn_cancellation import TurnCancellationToken
from minicode.web import DashboardReadModel
from minicode.web.change_feed import DashboardChangeFeed
from minicode.web.event_stream import DashboardEventStream

class InstalledModel:
    def __init__(self, *, usage=None):
        self.calls = 0
        self.usage = usage
    def next(self, messages, on_stream_chunk=None, store=None):
        self.calls += 1
        return AgentStep(type="assistant", content="installed ok", usage=self.usage)

class InstalledSink:
    def __init__(self): self.events = []
    def emit(self, event_type, *, step=None, payload=None):
        self.events.append((event_type, step, payload))

class InstalledFailingSink:
    def emit(self, *_args, **_kwargs):
        raise RuntimeError("installed observer failure")

def fail_disabled_constructor(*_args, **_kwargs):
    raise AssertionError("disabled installed path constructed Work Chain controls")

disabled_symbols = (
    "CyberneticOrchestrator",
    "ContextCompactor",
    "ContextCyberneticsOrchestrator",
    "CostControlLoop",
    "SelfHealingEngine",
    "FeedforwardController",
    "_build_work_chain_task",
)
original_symbols = {
    name: getattr(agent_loop_module, name) for name in disabled_symbols
}
original_monotonic = agent_loop_module.time.monotonic
try:
    for name in disabled_symbols:
        setattr(agent_loop_module, name, fail_disabled_constructor)
    base_messages = [
        {"role": "system", "content": "installed system"},
        {"role": "user", "content": "installed user"},
    ]
    no_context_model = InstalledModel()
    no_context = agent_loop_module.run_agent_turn(
        model=no_context_model,
        tools=ToolRegistry([]),
        messages=base_messages,
        cwd=".",
        enable_work_chain=False,
    )
    assert no_context[-1] == {"role": "assistant", "content": "installed ok"}
    assert no_context_model.calls == 1
    assert base_messages == [
        {"role": "system", "content": "installed system"},
        {"role": "user", "content": "installed user"},
    ]

    context_manager = ContextManager(model="installed-test")
    with_context_model = InstalledModel()
    with_context = agent_loop_module.run_agent_turn(
        model=with_context_model,
        tools=ToolRegistry([]),
        messages=base_messages,
        cwd=".",
        enable_work_chain=False,
        context_manager=context_manager,
    )
    assert with_context[-1] == {"role": "assistant", "content": "installed ok"}
    assert context_manager.messages is with_context
    assert with_context_model.calls == 1

    readings = iter((7.0, 7.25))
    agent_loop_module.time.monotonic = lambda: next(readings)
    usage_model = InstalledModel(
        usage=ModelUsage(
            input_tokens=120,
            output_tokens=24,
            cache_read_tokens=8,
            cache_creation_tokens=0,
            source="provider",
        )
    )
    usage_model.catalog_model_key = "openai/gpt-4o"
    installed_sink = InstalledSink()
    observed = agent_loop_module.run_agent_turn(
        model=usage_model,
        tools=ToolRegistry([]),
        messages=base_messages,
        cwd=".",
        enable_work_chain=False,
        event_sink=installed_sink,
    )
    assert observed[-1] == {"role": "assistant", "content": "installed ok"}
    assert usage_model.calls == 1
    assert [event[0] for event in installed_sink.events] == [
        "model.started", "model.completed", "model.costed",
        "working_memory.observed", "task.outcome",
    ]
    started_id = installed_sink.events[0][2]["operationId"]
    assert installed_sink.events[1][2] == {
        "operationId": started_id,
        "resultType": "assistant",
        "contentPresent": True,
        "toolCallCount": 0,
        "usage": {
            "source": "provider",
            "inputTokens": 120,
            "outputTokens": 24,
            "cacheReadTokens": 8,
            "cacheCreationTokens": 0,
        },
        "durationMs": 250,
    }
    assert installed_sink.events[2][2] == {
        "costVersion": 1,
        "operationId": started_id,
        "status": "priced",
        "quality": "provider_usage_catalog_rate",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "catalogModelKey": "openai/gpt-4o",
        "amountNanoUsd": 530000,
        "components": {
            "inputNanoUsd": 280000,
            "outputNanoUsd": 240000,
            "cacheReadNanoUsd": 10000,
            "cacheCreationNanoUsd": 0,
        },
    }
    assert installed_sink.events[3][2]["action"] == "protected"
    assert installed_sink.events[3][2]["scope"] == "process"
    assert installed_sink.events[3][2]["entries"] <= installed_sink.events[3][2]["maxEntries"]
    assert installed_sink.events[3][2]["protectedTokens"] <= installed_sink.events[3][2]["maxTokens"]
    failing_sink_model = InstalledModel()
    failing_sink_result = agent_loop_module.run_agent_turn(
        model=failing_sink_model,
        tools=ToolRegistry([]),
        messages=base_messages,
        cwd=".",
        enable_work_chain=False,
        event_sink=InstalledFailingSink(),
    )
    assert failing_sink_result == no_context
    assert failing_sink_model.calls == no_context_model.calls == 1
finally:
    agent_loop_module.time.monotonic = original_monotonic
    for name, value in original_symbols.items():
        setattr(agent_loop_module, name, value)

enabled_model = InstalledModel()
enabled_result = agent_loop_module.run_agent_turn(
    model=enabled_model,
    tools=ToolRegistry([]),
    messages=[
        {"role": "system", "content": "installed system"},
        {"role": "user", "content": "installed enabled path"},
    ],
    cwd=".",
    enable_work_chain=True,
)
assert enabled_result[-1] == {"role": "assistant", "content": "installed ok"}
assert enabled_model.calls == 1

workspace = Path(os.environ["MINI_CODE_DASHBOARD_WORKSPACE"])
assert str(Path(minicode.__file__).resolve()).startswith(
    str(Path(os.environ["PYTHONPATH"]).resolve())
)
installed_original_tool_profile = os.environ.get("MINI_CODE_TOOL_PROFILE")
os.environ["MINI_CODE_TOOL_PROFILE"] = "core"
try:
    installed_core_registry = tools_module.create_default_tool_registry(
        str(workspace), {"toolProfile": "core"}
    )
finally:
    if installed_original_tool_profile is None:
        os.environ.pop("MINI_CODE_TOOL_PROFILE", None)
    else:
        os.environ["MINI_CODE_TOOL_PROFILE"] = installed_original_tool_profile
assert installed_core_registry.find("web_fetch") is not None
assert installed_core_registry.find("web_search") is not None
assert len([
    tool for tool in installed_core_registry.list()
    if tool.name == "web_search"
]) == 1
assert installed_core_registry.find("http_request") is None

os.environ["MINI_CODE_TOOL_PROFILE"] = "full"
try:
    installed_http_registry = tools_module.create_default_tool_registry(
        str(workspace), {"toolProfile": "full"}
    )
finally:
    if installed_original_tool_profile is None:
        os.environ.pop("MINI_CODE_TOOL_PROFILE", None)
    else:
        os.environ["MINI_CODE_TOOL_PROFILE"] = installed_original_tool_profile
assert installed_http_registry.find("http_request") is not None
assert len([
    tool for tool in installed_http_registry.list()
    if tool.name == "web_search"
]) == 1
installed_http_calls = []

installed_search_transport_calls = []
installed_search_html = b'''
<div class="result">
  <a class="result__a" href="https://example.test/installed-search">
    Installed search result
  </a>
  <a class="result__snippet">Installed search snippet</a>
</div>
'''
installed_original_search_transport = (
    search_providers_module.execute_safe_get_response
)
installed_original_search_provider = search_providers_module.search_provider
installed_original_search_config = os.environ.get(
    "MINI_CODE_WEB_SEARCH_PROVIDERS"
)

def installed_search_transport(request, *, deadline):
    assert deadline > time.monotonic()
    installed_search_transport_calls.append(request.url)
    if request.url.startswith("https://www.baidu.com/"):
        raise network_safety_module.NetworkSafetyError("timeout")
    return http_utils_module.SafeHttpResponse(
        status=200,
        content_type="text/html; charset=utf-8",
        content_encoding="identity",
        payload=installed_search_html,
    )

search_providers_module.execute_safe_get_response = installed_search_transport
os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = "baidu,duckduckgo"
try:
    installed_search = installed_core_registry.execute(
        "web_search",
        {
            "query": "installed-query-fixture-secret",
            "num_results": 3,
        },
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_search.ok is True
    assert "PROVIDER: duckduckgo" in installed_search.output
    assert "RESULT_COUNT: 1" in installed_search.output
    assert "Installed search result" in installed_search.output
    assert "fixture-secret" not in installed_search.output
    assert len(installed_search_transport_calls) == 2

    installed_statuses = {
        status: search_providers_module.search_provider(
            "baidu",
            "installed-status-fixture-secret",
            1,
            deadline=time.monotonic() + 2,
            transport=lambda _request, *, deadline, status=status: (
                http_utils_module.SafeHttpResponse(
                    status=status,
                    content_type="text/html",
                    content_encoding="identity",
                    payload=b"installed-status-body-fixture-secret",
                )
            ),
        ).status.value
        for status in (403, 404, 429, 503)
    }
    assert installed_statuses == {
        403: "forbidden",
        404: "forbidden",
        429: "rate_limited",
        503: "server_error",
    }

    def installed_no_results(provider, *_args, **_kwargs):
        return search_providers_module.SearchProviderOutcome(
            provider,
            search_providers_module.SearchProviderStatus.NO_RESULTS,
        )
    search_providers_module.search_provider = installed_no_results
    installed_empty = installed_core_registry.execute(
        "web_search",
        {"query": "installed-empty-fixture-secret"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_empty.output.startswith("error[no_results]:")
    assert "fixture-secret" not in installed_empty.output

    def installed_all_failed(provider, *_args, **_kwargs):
        status = (
            search_providers_module.SearchProviderStatus.CHALLENGE
            if provider == "baidu"
            else search_providers_module.SearchProviderStatus.RATE_LIMITED
        )
        return search_providers_module.SearchProviderOutcome(provider, status)
    search_providers_module.search_provider = installed_all_failed
    installed_failed = installed_core_registry.execute(
        "web_search",
        {"query": "Bearer installed-failure-fixture-secret"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_failed.output.startswith("error[search_unavailable]:")
    assert "baidu=challenge" in installed_failed.output
    assert "duckduckgo=rate_limited" in installed_failed.output
    assert "fixture-secret" not in installed_failed.output
    assert "Bearer" not in installed_failed.output

    installed_invalid_sends = 0
    def installed_forbidden_search_send(*_args, **_kwargs):
        global installed_invalid_sends
        installed_invalid_sends += 1
        raise AssertionError("invalid config sent a request")
    search_providers_module.search_provider = installed_forbidden_search_send
    os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = "baidu,baidu"
    installed_invalid_config = installed_core_registry.execute(
        "web_search",
        {"query": "installed-invalid-config"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_invalid_config.output.startswith(
        "error[provider_config_invalid]:"
    )
    assert installed_invalid_sends == 0
finally:
    search_providers_module.execute_safe_get_response = (
        installed_original_search_transport
    )
    search_providers_module.search_provider = installed_original_search_provider
    if installed_original_search_config is None:
        os.environ.pop("MINI_CODE_WEB_SEARCH_PROVIDERS", None)
    else:
        os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = (
            installed_original_search_config
        )

class InstalledHttpResponse:
    status = 200
    headers = {"Content-Type": "application/json", "Content-Length": "11"}
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self, size=-1):
        assert 0 < size <= 64 * 1024
        return b'{"ok":true}'

original_http_open = http_utils_module._open_no_redirect
installed_web_calls = []

class InstalledWebResponse:
    status = 200
    def __init__(self, payload, content_type):
        self.payload = payload
        self.offset = 0
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self, size=-1):
        assert 0 < size <= 64 * 1024
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

installed_web_payloads = [
    (b'{"installed":"json"}', "application/json"),
    (b"<html><body>installed html</body></html>", "text/html"),
    (b"installed text", "text/plain"),
]
def installed_web_open(request, *, timeout):
    assert timeout > 0
    installed_web_calls.append(request.full_url)
    payload, content_type = installed_web_payloads.pop(0)
    destination = getattr(request, "_minicode_destination")
    assert destination.addresses == ("93.184.216.34",)
    return InstalledWebResponse(payload, content_type)

http_utils_module._open_no_redirect = installed_web_open
for installed_kind in ("json", "html", "text"):
    installed_web_result = installed_core_registry.execute(
        "web_fetch",
        {
            "url": "https://93.184.216.34/installed-" + installed_kind,
            "max_chars": 1000,
        },
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_web_result.ok is True
    assert "installed " + installed_kind in installed_web_result.output or (
        installed_kind == "json" and '"installed":"json"' in installed_web_result.output
    )
assert len(installed_web_calls) == 3

installed_private_fetch = installed_core_registry.execute(
    "web_fetch",
    {"url": "http://172.17.0.1/private"},
    ToolContext(cwd=str(workspace), permissions=None),
)
assert installed_private_fetch.output == (
    "error[destination_blocked]: The request destination is not allowed."
)
assert len(installed_web_calls) == 3

class InstalledResolverStub:
    def __init__(self, mode): self.mode = mode
    def resolve(self, _hostname, port, *, deadline):
        assert deadline > time.monotonic()
        if self.mode in {"dns_error", "timeout", "resolver_busy"}:
            raise ResolverError(self.mode)
        addresses = (
            ("93.184.216.34",)
            if self.mode == "public"
            else ("93.184.216.34", "10.0.0.8")
        )
        return [
            (
                network_safety_module.socket.AF_INET,
                network_safety_module.socket.SOCK_STREAM,
                network_safety_module.socket.IPPROTO_TCP,
                "",
                (address, port),
            )
            for address in addresses
        ]

installed_original_resolver = network_safety_module._DNS_RESOLVER
try:
    network_safety_module._DNS_RESOLVER = InstalledResolverStub("mixed")
    installed_mixed_fetch = installed_core_registry.execute(
        "web_fetch",
        {"url": "https://installed-mixed.example/"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_mixed_fetch.output.startswith("error[destination_blocked]:")
    os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = "baidu"
    installed_mixed_search = installed_core_registry.execute(
        "web_search",
        {"query": "installed-mixed-query-fixture-secret"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_mixed_search.output.startswith("error[search_unavailable]:")
    assert "baidu=redirect_blocked" in installed_mixed_search.output
    assert "fixture-secret" not in installed_mixed_search.output
    if installed_original_search_config is None:
        os.environ.pop("MINI_CODE_WEB_SEARCH_PROVIDERS", None)
    else:
        os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = (
            installed_original_search_config
        )
    for installed_resolver_code in ("dns_error", "timeout", "resolver_busy"):
        network_safety_module._DNS_RESOLVER = InstalledResolverStub(
            installed_resolver_code
        )
        installed_resolver_fetch = installed_core_registry.execute(
            "web_fetch",
            {"url": "https://installed-resolver.example/"},
            ToolContext(cwd=str(workspace), permissions=None),
        )
        assert installed_resolver_fetch.output.startswith(
            "error[" + installed_resolver_code + "]:"
        )
finally:
    network_safety_module._DNS_RESOLVER = installed_original_resolver
assert len(installed_web_calls) == 3

def installed_redirect_private_open(request, *, timeout):
    assert timeout > 0
    installed_web_calls.append(request.full_url)
    response = InstalledWebResponse(b"", "text/plain")
    response.status = 302
    response.headers = {"Location": "http://127.0.0.1/private"}
    return response

http_utils_module._open_no_redirect = installed_redirect_private_open
installed_redirect_private = installed_core_registry.execute(
    "web_fetch",
    {"url": "https://93.184.216.34/redirect"},
    ToolContext(cwd=str(workspace), permissions=None),
)
assert installed_redirect_private.output.startswith("error[redirect_blocked]:")
assert installed_web_calls == [
    "https://93.184.216.34/installed-json",
    "https://93.184.216.34/installed-html",
    "https://93.184.216.34/installed-text",
    "https://93.184.216.34/redirect",
]

def installed_http_open(request, *, timeout):
    assert timeout > 0
    installed_http_calls.append(request.full_url)
    return InstalledHttpResponse()
http_utils_module._open_no_redirect = installed_http_open
try:
    installed_safe_get = installed_http_registry.execute(
        "http_request",
        {"url": "https://93.184.216.34/safe", "method": "GET"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_safe_get.ok is True
    assert len(installed_http_calls) == 1

    installed_deny = installed_http_registry.execute(
        "http_request",
        {
            "url": "https://93.184.216.34/mutate",
            "method": "POST",
            "body": "{}",
        },
        ToolContext(
            cwd=str(workspace),
            permissions=InstalledPermissionManager(
                str(workspace),
                prompt=lambda _request: {"decision": "deny_once"},
            ),
        ),
    )
    assert installed_deny.output == (
        "error[permission_denied]: The network request was denied."
    )
    assert len(installed_http_calls) == 1

    installed_allow = installed_http_registry.execute(
        "http_request",
        {
            "url": "https://93.184.216.34/mutate",
            "method": "POST",
            "body": "{}",
        },
        ToolContext(
            cwd=str(workspace),
            permissions=InstalledPermissionManager(
                str(workspace),
                prompt=lambda _request: {"decision": "allow_once"},
            ),
        ),
    )
    assert installed_allow.ok is True
    assert len(installed_http_calls) == 2

    class InstalledLargeHttpResponse(InstalledHttpResponse):
        headers = {"Content-Type": "text/plain"}
        def __init__(self):
            self.remaining = 1024 * 1024 + 1
        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count
    http_utils_module._open_no_redirect = (
        lambda _request, *, timeout: InstalledLargeHttpResponse()
    )
    installed_web_large = installed_core_registry.execute(
        "web_fetch",
        {"url": "https://93.184.216.34/large", "max_chars": 100},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_web_large.output == (
        "error[response_too_large]: The response exceeds the safe byte limit."
    )
    os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = "baidu"
    network_safety_module._DNS_RESOLVER = InstalledResolverStub("public")
    try:
        installed_search_large = installed_core_registry.execute(
            "web_search",
            {"query": "installed-large-query-fixture-secret"},
            ToolContext(cwd=str(workspace), permissions=None),
        )
    finally:
        network_safety_module._DNS_RESOLVER = installed_original_resolver
    assert installed_search_large.output.startswith("error[search_unavailable]:")
    assert "baidu=response_too_large" in installed_search_large.output
    assert "fixture-secret" not in installed_search_large.output
    if installed_original_search_config is None:
        os.environ.pop("MINI_CODE_WEB_SEARCH_PROVIDERS", None)
    else:
        os.environ["MINI_CODE_WEB_SEARCH_PROVIDERS"] = (
            installed_original_search_config
        )
    installed_large = installed_http_registry.execute(
        "http_request",
        {"url": "https://93.184.216.34/large", "method": "GET"},
        ToolContext(cwd=str(workspace), permissions=None),
    )
    assert installed_large.output == (
        "error[response_too_large]: The response exceeds the safe byte limit."
    )

    installed_original_resolver = network_safety_module._DNS_RESOLVER
    failing_resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("installed-resolver-fixture-secret")
        ),
    )
    network_safety_module._DNS_RESOLVER = failing_resolver
    try:
        installed_dns_failure = installed_http_registry.execute(
            "http_request",
            {
                "url": "https://dns-failure.public.example/read",
                "method": "GET",
                "timeout": 1,
            },
            ToolContext(cwd=str(workspace), permissions=None),
        )
        assert installed_dns_failure.output == (
            "error[dns_error]: The request destination could not be resolved."
        )
        assert "fixture-secret" not in installed_dns_failure.output
        assert len(installed_http_calls) == 2
    finally:
        failing_resolver.close()
        network_safety_module._DNS_RESOLVER = installed_original_resolver

    installed_release_resolver = threading.Event()
    def installed_blocking_resolver(_hostname, port, **_kwargs):
        assert installed_release_resolver.wait(5)
        return [(
            network_safety_module.socket.AF_INET,
            network_safety_module.socket.SOCK_STREAM,
            network_safety_module.socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", port),
        )]
    saturated_resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=installed_blocking_resolver,
    )
    network_safety_module._DNS_RESOLVER = saturated_resolver
    installed_resolver_outcomes = []
    def installed_occupy_resolver(hostname):
        try:
            network_safety_module.validate_destination(
                "https://" + hostname + "/",
                deadline=time.monotonic() + 5,
            )
        except network_safety_module.NetworkSafetyError as error:
            installed_resolver_outcomes.append(error.code)
    installed_active_resolver = threading.Thread(
        target=installed_occupy_resolver,
        args=("active.public.example",),
    )
    installed_queued_resolver = threading.Thread(
        target=installed_occupy_resolver,
        args=("queued.public.example",),
    )
    try:
        installed_active_resolver.start()
        installed_wait_deadline = time.monotonic() + 2
        while (
            saturated_resolver.snapshot().active_count != 1
            and time.monotonic() < installed_wait_deadline
        ):
            time.sleep(0.005)
        installed_queued_resolver.start()
        installed_wait_deadline = time.monotonic() + 2
        while (
            saturated_resolver.snapshot().queued_count != 1
            and time.monotonic() < installed_wait_deadline
        ):
            time.sleep(0.005)
        installed_saturation = installed_http_registry.execute(
            "http_request",
            {
                "url": "https://saturated.public.example/read",
                "method": "GET",
                "timeout": 1,
            },
            ToolContext(cwd=str(workspace), permissions=None),
        )
        assert installed_saturation.output == (
            "error[resolver_busy]: The DNS resolver is temporarily busy."
        )
        assert saturated_resolver.snapshot().active_count == 1
        assert saturated_resolver.snapshot().queued_count == 1
        assert len(installed_http_calls) == 2
    finally:
        saturated_resolver.close()
        installed_release_resolver.set()
        installed_active_resolver.join(timeout=5)
        installed_queued_resolver.join(timeout=5)
        network_safety_module._DNS_RESOLVER = installed_original_resolver
    assert sorted(installed_resolver_outcomes) == [
        "network_unavailable",
        "network_unavailable",
    ]
finally:
    http_utils_module._open_no_redirect = original_http_open
    installed_core_registry.dispose()
    installed_http_registry.dispose()

installed_resolver_exit_script = (
    "import threading,time\\n"
    "from minicode.tools.bounded_resolver import BoundedResolver,ResolverError\\n"
    "blocker=threading.Event()\\n"
    "def block(*_args,**_kwargs): blocker.wait()\\n"
    "resolver=BoundedResolver(worker_limit=1,queue_limit=1,resolver=block)\\n"
    "try:\\n"
    " resolver.resolve('installed-exit.public.example',443,"
    "deadline=time.monotonic()+0.05)\\n"
    "except ResolverError as error:\\n"
    " assert error.code=='timeout'\\n"
    "assert resolver.snapshot().active_count==1\\n"
    "resolver.close()\\n"
)
installed_resolver_exit = subprocess.run(
    [sys.executable, "-c", installed_resolver_exit_script],
    cwd=workspace,
    text=True,
    encoding="utf-8",
    errors="replace",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=3,
    check=False,
)
assert installed_resolver_exit.returncode == 0, installed_resolver_exit.stderr

def installed_memory_tree():
    roots = (Path.home(), workspace)
    return tuple(
        sorted(
            (str(index), path.relative_to(root).as_posix())
            for index, root in enumerate(roots)
            for path in root.rglob("*")
        )
    )

installed_memory_dir = memory_module.MINI_CODE_DIR
empty_memory_dir = Path.home() / "empty-home" / ".mini-code"
memory_module.MINI_CODE_DIR = empty_memory_dir
assert not empty_memory_dir.exists()
empty_before = installed_memory_tree()
empty_server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
empty_server.dashboard_read_model = DashboardReadModel(workspace)
empty_server.memory_approval_authority = MemoryApprovalAuthority(workspace)
empty_thread = threading.Thread(target=empty_server.serve_forever, daemon=True)
empty_thread.start()
try:
    empty_base = f"http://127.0.0.1:{empty_server.server_address[1]}"
    with urllib.request.urlopen(
        empty_base + "/api/v1/memory/approvals/pending", timeout=5
    ) as response:
        empty_pending = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert empty_pending["schemaVersion"] == 1
        assert empty_pending["mode"] == "read-only"
        assert empty_pending["items"] == []
finally:
    empty_server.shutdown()
    empty_server.server_close()
    empty_thread.join(timeout=5)
assert installed_memory_tree() == empty_before
assert not empty_memory_dir.exists()
assert not (empty_memory_dir / "memory-store.lock").exists()
memory_module.MINI_CODE_DIR = installed_memory_dir

installed_memory_manager = memory_module.MemoryManager(project_root=workspace)
installed_pending_memory = installed_memory_manager.add_entry(
    MemoryScope.PROJECT,
    "note",
    "Installed wheel Memory approval contract",
    source="reflection",
    approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
)
assert installed_pending_memory is not None
assert installed_pending_memory.approval_status == "pending"
installed_rejected_memory = installed_memory_manager.add_entry(
    MemoryScope.PROJECT,
    "note",
    "Installed wheel Memory rejection contract",
    source="reflection",
    approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
)
assert installed_rejected_memory is not None
installed_memory_json = workspace / ".mini-code-memory" / "memory.json"
installed_memory_data = json.loads(installed_memory_json.read_text(encoding="utf-8"))
for installed_raw_memory in installed_memory_data["entries"]:
    installed_raw_memory.pop("approval_policy")
installed_memory_json.write_text(
    json.dumps(installed_memory_data, indent=2), encoding="utf-8"
)
installed_memory_authority = MemoryApprovalAuthority(workspace)

cross_process_start = workspace / "installed-session-store-start"
cross_process_ready = [
    workspace / "installed-session-store-ready-1",
    workspace / "installed-session-store-ready-2",
]
cross_process_code = "\\n".join(
    [
        "import sys, time",
        "from pathlib import Path",
        "from minicode.session import create_new_session, save_session",
        "start, ready, workspace, content = map(Path, sys.argv[1:5])",
        "session = create_new_session(str(workspace))",
        "session.messages = [{'role': 'user', 'content': content.name}]",
        "ready.write_text('ready', encoding='utf-8')",
        "deadline = time.monotonic() + 5",
        "while not start.exists():",
        "    assert time.monotonic() < deadline",
        "    time.sleep(0.005)",
        "save_session(session, force_full=True)",
        "print(session.session_id)",
    ]
)
cross_processes = [
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            cross_process_code,
            str(cross_process_start),
            str(ready_path),
            str(workspace),
            f"installed-process-{index}",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for index, ready_path in enumerate(cross_process_ready, start=1)
]
ready_deadline = time.monotonic() + 5
while not all(path.exists() for path in cross_process_ready):
    assert time.monotonic() < ready_deadline
    assert all(process.poll() is None for process in cross_processes)
    time.sleep(0.005)
cross_process_start.write_text("start", encoding="utf-8")
cross_process_session_ids = []
for process in cross_processes:
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    cross_process_session_ids.append(stdout.strip())
assert len(set(cross_process_session_ids)) == 2
assert set(cross_process_session_ids).issubset(
    {item.session_id for item in list_sessions()}
)
installed_index = json.loads(
    (Path(config_module.MINI_CODE_DIR) / "sessions_index.json").read_text(
        encoding="utf-8"
    )
)
assert set(cross_process_session_ids).issubset(installed_index)
assert (
    Path(config_module.MINI_CODE_DIR) / SESSION_STORE_LOCK_NAME
).read_bytes() == (b"0" if os.name == "nt" else b"")
for session_id in cross_process_session_ids:
    assert delete_session(session_id) is True
for path in [cross_process_start, *cross_process_ready]:
    path.unlink()

current_registry = McpCurrentStateRegistry()

class InstalledProcess:
    pid = 4242
    stdin = None
    stdout = None
    stderr = None
    def __init__(self): self.alive = True
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
    def kill(self): self.alive = False
    def wait(self, timeout=None):
        self.alive = False
        return 0

installed_client = StdioMcpClient(
    "installed-current-server",
    {"command": "python", "protocol": "newline-json"},
    str(workspace),
    state_registry=current_registry,
)
installed_process = InstalledProcess()
installed_client._spawn_process = lambda: setattr(
    installed_client, "process", installed_process
)
installed_client.request = lambda *_args, **_kwargs: {}
installed_client.notify = lambda *_args, **_kwargs: None
installed_client.start()
installed_current_key = mcp_server_key(workspace, "installed-current-server")
current_payload = current_registry.snapshot_for(
    frozenset({installed_current_key})
).to_dict()
assert normalize_mcp_current_state_snapshot(current_payload) == current_payload
assert current_payload["servers"] == [
    {
        "serverKey": installed_current_key,
        "state": "ready",
        "activeInstanceCount": 1,
        "protocol": "newline-json",
        "failureKind": None,
        "updatedAt": current_payload["servers"][0]["updatedAt"],
    }
]
installed_client.close()
assert current_registry.snapshot_for(frozenset({installed_current_key})).servers == ()

(workspace / ".mcp.json").write_text(
    json.dumps(
        {
            "mcpServers": {
                "installed-server": {
                    "command": "python",
                    "protocol": "content-length",
                }
            }
        }
    ),
    encoding="utf-8",
)
project_skill = workspace / ".mini-code" / "skills" / "project-skill" / "SKILL.md"
project_skill.parent.mkdir(parents=True)
project_skill.write_text(
    "---\\nname: project-skill\\ndescription: Installed project Skill.\\n---\\n",
    encoding="utf-8",
)
(project_skill.parent.parent / ".DS_Store").write_text(
    "installed-ds-store-secret", encoding="utf-8"
)
(project_skill.parent.parent / "README.md").write_text(
    "installed-readme-secret", encoding="utf-8"
)
compat_skill = workspace / ".claude" / "skills" / "compat-skill" / "SKILL.md"
compat_skill.parent.mkdir(parents=True)
compat_skill.write_text(
    "---\\nname: compat-skill\\ndescription: Installed compat Skill.\\n---\\n",
    encoding="utf-8",
)
(compat_skill.parent.parent / ".DS_Store").write_text(
    "installed-compat-ds-store-secret", encoding="utf-8"
)

class FakeTools:
    def get_skills(self): return []
    def get_mcp_servers(self): return []
    def dispose(self): pass

class FakePermissions:
    def begin_turn(self): pass
    def end_turn(self): pass
    def get_summary(self): return []

class FakeRouting:
    intent_type = "code"
    action_type = "read"
    total_skills = 0
    selected = []
    selected_skills = []
    used_fallback = True
    def selected_skill_dicts(self): return []
    def to_dict(self): return {}

class FakeLogger:
    def info(self, *_args, **_kwargs): pass
    def error(self, *_args, **_kwargs): pass

installed_session = create_new_session(str(workspace.resolve()))
session_args = TtyAppArgs(
    runtime=None,
    tools=FakeTools(),
    model=object(),
    messages=[{"role": "system", "content": "installed session system"}],
    cwd=str(workspace),
    permissions=FakePermissions(),
)
session_state = ScreenState(
    session=installed_session,
    autosave=AutosaveManager(installed_session),
    agent_lock=threading.Lock(),
)
first_session_messages = [
    {"role": "system", "content": "installed session system"},
    {"role": "user", "content": "installed turn one"},
    {"role": "assistant", "content": "installed reply one"},
]
session_state.history = ["installed turn one"]
session_state.transcript = [
    TranscriptEntry(id=1, kind="user", body="installed turn one"),
    TranscriptEntry(id=2, kind="assistant", body="installed reply one"),
]
session_state.agent_result = {"messages": first_session_messages, "done": True}
assert consume_finished_tty_turn(session_args, session_state) is True
assert consume_finished_tty_turn(session_args, session_state) is False
second_session_messages = [
    *first_session_messages,
    {"role": "user", "content": "installed turn two"},
    {"role": "assistant", "content": "installed reply two"},
]
session_state.history.append("installed turn two")
session_state.transcript.extend([
    TranscriptEntry(id=3, kind="user", body="installed turn two"),
    TranscriptEntry(id=4, kind="assistant", body="installed reply two"),
])
session_state.agent_result = {"messages": second_session_messages, "done": True}
assert consume_finished_tty_turn(session_args, session_state) is True

reload_code = (
    "import json; from minicode.session import load_session; "
    f"s=load_session({installed_session.session_id!r}); "
    "assert s is not None; print(json.dumps(s.messages))"
)
reloaded_messages = json.loads(subprocess.check_output(
    [sys.executable, "-c", reload_code],
    text=True,
))
assert reloaded_messages == second_session_messages

installed_session_path = (
    Path(config_module.MINI_CODE_DIR)
    / "sessions"
    / f"{installed_session.session_id}.json"
)
installed_session_base = json.loads(
    installed_session_path.read_text(encoding="utf-8")
)
installed_session_delta = json.loads(
    (
        Path(config_module.MINI_CODE_DIR)
        / "sessions"
        / "deltas"
        / installed_session.session_id
        / "delta_0000.json"
    ).read_text(encoding="utf-8")
)
assert installed_session_base["persistence_generation"] == 1
assert installed_session_delta["persistence_generation"] == 1
assert installed_session_delta["session_id"] == installed_session.session_id

legacy_session = create_new_session(str(workspace.resolve()))
legacy_session.messages = [{"role": "user", "content": "legacy base"}]
save_session(legacy_session, force_full=True)
legacy_session.messages.append(
    {"role": "assistant", "content": "legacy delta"}
)
save_session(legacy_session)
legacy_base_path = (
    Path(config_module.MINI_CODE_DIR)
    / "sessions"
    / f"{legacy_session.session_id}.json"
)
legacy_delta_path = (
    Path(config_module.MINI_CODE_DIR)
    / "sessions"
    / "deltas"
    / legacy_session.session_id
    / "delta_0000.json"
)
legacy_base = json.loads(legacy_base_path.read_text(encoding="utf-8"))
legacy_delta = json.loads(legacy_delta_path.read_text(encoding="utf-8"))
legacy_base.pop("persistence_generation")
legacy_delta.pop("persistence_generation")
legacy_delta.pop("session_id")
legacy_base_path.write_text(json.dumps(legacy_base), encoding="utf-8")
legacy_delta_path.write_text(json.dumps(legacy_delta), encoding="utf-8")
legacy_reloaded = load_session(legacy_session.session_id)
assert legacy_reloaded is not None
assert legacy_reloaded._persistence_generation == 0
assert [message["content"] for message in legacy_reloaded.messages] == [
    "legacy base",
    "legacy delta",
]
save_session(legacy_reloaded, force_full=True)
assert json.loads(legacy_base_path.read_text(encoding="utf-8"))[
    "persistence_generation"
] == 1
legacy_reload_code = (
    "from minicode.session import load_session; "
    f"s=load_session({legacy_session.session_id!r}); "
    "assert s is not None and s._persistence_generation == 1; "
    "assert [m['content'] for m in s.messages] == "
    "['legacy base', 'legacy delta']"
)
subprocess.check_call([sys.executable, "-c", legacy_reload_code])
assert delete_session(legacy_session.session_id) is True

def fake_agent_turn(**kwargs):
    first_operation = "modelop_" + "a" * 32
    second_operation = "modelop_" + "b" * 32
    kwargs["event_sink"].emit(
        "model.started", step=1, payload={"operationId": first_operation}
    )
    kwargs["event_sink"].emit(
        "model.completed",
        step=1,
        payload={
            "operationId": first_operation,
            "resultType": "tool_calls",
            "contentPresent": False,
            "toolCallCount": 1,
            "usage": {
                "source": "provider",
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 5,
                "cacheCreationTokens": 0,
            },
            "durationMs": 125,
        },
    )
    kwargs["event_sink"].emit(
        "model.costed",
        step=1,
        payload={
            "costVersion": 1,
            "operationId": first_operation,
            "status": "priced",
            "quality": "provider_usage_catalog_rate",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "catalogModelKey": "openai/gpt-4o",
            "amountNanoUsd": 443750,
            "components": {
                "inputNanoUsd": 237500,
                "outputNanoUsd": 200000,
                "cacheReadNanoUsd": 6250,
                "cacheCreationNanoUsd": 0,
            },
        },
    )
    kwargs["on_tool_start"]("read_file", {"path": "installed-input-secret"})
    kwargs["on_tool_result"]("read_file", "installed-output-secret", False)
    kwargs["on_tool_result"](
        "run_command", "installed-error-output-secret", True
    )
    kwargs["event_sink"].emit(
        "model.started", step=2, payload={"operationId": second_operation}
    )
    kwargs["event_sink"].emit(
        "model.completed",
        step=2,
        payload={
            "operationId": second_operation,
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
            "usage": {
                "source": "estimated",
                "inputTokens": 40,
                "outputTokens": 10,
                "cacheReadTokens": None,
                "cacheCreationTokens": None,
            },
            "durationMs": 275,
        },
    )
    kwargs["event_sink"].emit(
        "model.costed",
        step=2,
        payload={
            "costVersion": 1,
            "operationId": second_operation,
            "status": "unavailable",
            "quality": "unavailable",
            "currency": "USD",
            "catalogId": "minicode-pricing-2026-07-17-v1",
            "reason": "pricing_incomplete",
        },
    )
    context_operation = "ctxop_" + "c" * 32
    kwargs["event_sink"].emit(
        "recovery.started",
        step=2,
        payload={
            "recoveryVersion": 1,
            "contextOperationId": context_operation,
            "kind": "compactor",
            "reason": "context_overflow",
        },
    )
    kwargs["event_sink"].emit(
        "context.compacted",
        step=2,
        payload={
            "contextVersion": 1,
            "contextOperationId": context_operation,
            "path": "reactive_compactor",
            "trigger": "reactive",
            "strategy": "reactive",
            "effective": True,
            "messagesBefore": 8,
            "messagesAfter": 5,
            "messagesRemoved": 3,
            "tokensFreed": 240,
        },
    )
    kwargs["event_sink"].emit(
        "recovery.completed",
        step=2,
        payload={
            "recoveryVersion": 1,
            "contextOperationId": context_operation,
            "kind": "compactor",
            "outcome": "recovered",
            "messagesBefore": 8,
            "messagesAfter": 5,
            "tokensFreed": 240,
        },
    )
    kwargs["event_sink"].emit(
        "working_memory.observed",
        step=2,
        payload={
            "workingMemoryVersion": 1,
            "action": "protected",
            "scope": "process",
            "entries": 2,
            "maxEntries": 15,
            "protectedTokens": 18,
            "maxTokens": 4000,
        },
    )
    kwargs["event_sink"].emit(
        "mcp.runtime.observed",
        step=2,
        payload={
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "installed-server"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": True,
            "protocol": "newline-json",
        },
    )
    return [
        *kwargs["messages"], {"role": "assistant", "content": "installed response"}
    ]

agent_loop_module.run_agent_turn = fake_agent_turn
capability_module.get_registry = lambda: object()
capability_module.register_tool_capabilities = lambda _tools: None
config_module.load_runtime_config = lambda _cwd: {"model": "fake"}
intent_module.parse_intent = lambda _prompt: object()
logging_module.setup_logging = lambda **_kwargs: None
logging_module.get_logger = lambda _name: FakeLogger()
memory_module.MemoryManager = lambda **_kwargs: object()
model_module.create_model_adapter = lambda **_kwargs: object()
permissions_module.PermissionManager = lambda *_args, **_kwargs: FakePermissions()
prompt_module.build_system_prompt = lambda *_args, **_kwargs: "system"
skill_router_module.SkillRouter = lambda: type(
    "Router", (), {"route": lambda self, *_args, **_kwargs: FakeRouting()}
)()
gateway_registry_dependencies = []
def create_gateway_tools(*_args, **kwargs):
    gateway_registry_dependencies.append(
        kwargs.get("mcp_current_state_registry")
    )
    return FakeTools()
tools_module.create_default_tool_registry = create_gateway_tools

http_client = StdioMcpClient(
    "installed-server",
    {"command": "python", "protocol": "newline-json"},
    str(workspace),
    state_registry=current_registry,
)
http_process = InstalledProcess()
http_client._spawn_process = lambda: setattr(http_client, "process", http_process)
http_client.request = lambda *_args, **_kwargs: {}
http_client.notify = lambda *_args, **_kwargs: None
http_client.start()

unmatched_probe_calls = [0]
def unmatched_probe():
    unmatched_probe_calls[0] += 1
    raise RuntimeError("Authorization=installed-other-workspace-secret")
unmatched_handle = current_registry.register(
    mcp_server_key(workspace / "other-workspace", "installed-server"),
    probe=unmatched_probe,
)
assert unmatched_handle is not None
assert current_registry.mark_ready(
    unmatched_handle,
    protocol="content-length",
) is True

server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
permission_broker = PermissionApprovalBroker(workspace, timeout_seconds=5)
server.permission_approval_broker = permission_broker
server.memory_approval_authority = installed_memory_authority
server.mcp_current_state_registry = current_registry
server.dashboard_read_model = DashboardReadModel.from_environment(
    mcp_current_state_loader=lambda server_keys: (
        current_registry.snapshot_for(server_keys).to_dict()
    )
)
server.dashboard_change_feed = DashboardChangeFeed(
    workspace,
    data_dir=config_module.MINI_CODE_DIR,
    mcp_current_state_loader=lambda: current_registry.snapshot_for(
        server.dashboard_read_model.configured_mcp_server_keys()
    ).to_dict(),
    permission_revision_loader=permission_broker.revision,
)
server.dashboard_event_stream = DashboardEventStream(server.dashboard_change_feed)
server.dashboard_event_stream.start()
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with urllib.request.urlopen(base + "/health", timeout=5) as response:
        assert json.loads(response.read().decode("utf-8"))["ok"] is True
    with urllib.request.urlopen(base + "/api/v1/health", timeout=5) as response:
        assert json.loads(response.read().decode("utf-8"))["ok"] is True
    with urllib.request.urlopen(base + "/api/v1/data-health", timeout=5) as response:
        data_health = json.loads(response.read().decode("utf-8"))
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert data_health["schemaVersion"] == 1
        assert data_health["mode"] == "read-only"
        assert data_health["workspace"]["name"] == "workspace"
        assert data_health["summary"]["storeCount"] == 25
        assert data_health["maintenancePlan"]["destructiveActionsAvailable"] is False
        assert not any(
            marker in json.dumps(data_health).lower()
            for marker in (
                str(Path.home()).lower(),
                str(workspace).lower(),
                "installed-secret",
            )
        )
    installed_legacy_before = (
        installed_memory_json.read_bytes(),
        installed_memory_json.stat().st_size,
        installed_memory_json.stat().st_mtime_ns,
    )
    with urllib.request.urlopen(base + "/api/v1/memory/approvals/pending", timeout=5) as response:
        installed_memory_pending = json.loads(response.read().decode("utf-8"))
        assert {item["memoryId"] for item in installed_memory_pending["items"]} == {
            installed_pending_memory.id,
            installed_rejected_memory.id,
        }
        assert response.headers["Cache-Control"] == "no-store"
    assert (
        installed_memory_json.read_bytes(),
        installed_memory_json.stat().st_size,
        installed_memory_json.stat().st_mtime_ns,
    ) == installed_legacy_before
    installed_memory_item = next(
        item for item in installed_memory_pending["items"]
        if item["memoryId"] == installed_pending_memory.id
    )
    installed_memory_decision = urllib.request.Request(
        base + "/api/v1/memory/approvals/" + installed_pending_memory.id + "/decision",
        data=json.dumps({
            "decision": "approve",
            "reviewRevision": installed_memory_item["reviewRevision"],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(installed_memory_decision, timeout=5) as response:
        installed_memory_decided = json.loads(response.read().decode("utf-8"))
        assert installed_memory_decided["status"] == "approved"
        assert installed_memory_decided["decisionAccepted"] is True
    installed_reject_item = next(
        item for item in installed_memory_pending["items"]
        if item["memoryId"] == installed_rejected_memory.id
    )
    installed_memory_reject = urllib.request.Request(
        base + "/api/v1/memory/approvals/" + installed_rejected_memory.id + "/decision",
        data=json.dumps({
            "decision": "reject",
            "reviewRevision": installed_reject_item["reviewRevision"],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(installed_memory_reject, timeout=5) as response:
        installed_memory_rejected = json.loads(response.read().decode("utf-8"))
        assert installed_memory_rejected["status"] == "rejected"
        assert installed_memory_rejected["decisionAccepted"] is True
    permission_turn_id = "turn_55555555555555555555555555555555"
    permission_session = permission_broker.begin_turn(
        turn_id=permission_turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(permission_turn_id),
    )
    permission_outcome = {}
    def installed_permission_prompt():
        permission_session.tool_started("write_file")
        try:
            permission_outcome["result"] = permission_session.prompt({
                "schemaVersion": 1,
                "kind": "edit",
                "review": {
                    "targetPath": str(workspace / "installed-permission.txt"),
                    "diffPreview": "--- a/installed-permission.txt\\n+++ b/installed-permission.txt",
                },
            })
        finally:
            permission_session.tool_finished("write_file")
    permission_thread = threading.Thread(target=installed_permission_prompt)
    permission_thread.start()
    pending = None
    for _index in range(100):
        with urllib.request.urlopen(base + "/api/v1/permissions/pending", timeout=5) as response:
            pending = json.loads(response.read().decode("utf-8"))
        if pending["items"]:
            break
        time.sleep(0.01)
    assert pending is not None and len(pending["items"]) == 1
    permission_item = pending["items"][0]
    assert permission_item["review"]["targetPath"] == "installed-permission.txt"
    permission_decision = urllib.request.Request(
        base + "/api/v1/permissions/" + permission_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": permission_turn_id,
            "decision": "allow_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(permission_decision, timeout=5) as response:
        decided = json.loads(response.read().decode("utf-8"))
        assert decided["decisionAccepted"] is True
        assert response.headers["Cache-Control"] == "no-store"
    permission_thread.join(timeout=5)
    assert permission_outcome["result"] == {"decision": "allow_operation"}
    permission_session.close()

    def installed_write_pending(target, content, turn_digit):
        installed_turn_id = "turn_" + turn_digit * 32
        installed_session = permission_broker.begin_turn(
            turn_id=installed_turn_id,
            run_id=None,
            cancellation_token=TurnCancellationToken(installed_turn_id),
        )
        installed_manager = InstalledPermissionManager(
            str(workspace), prompt=installed_session.prompt
        )
        installed_outcome = {}
        def run_installed_write():
            installed_session.tool_started("write_file")
            try:
                installed_outcome["result"] = write_file_tool.run(
                    write_file_tool.validator({
                        "path": str(target),
                        "content": content,
                    }),
                    ToolContext(
                        cwd=str(workspace),
                        permissions=installed_manager,
                    ),
                )
            except BaseException as error:
                installed_outcome["error"] = error
            finally:
                installed_session.tool_finished("write_file")
        installed_thread = threading.Thread(target=run_installed_write)
        installed_thread.start()
        installed_pending = None
        for _index in range(100):
            with urllib.request.urlopen(
                base + "/api/v1/permissions/pending", timeout=5
            ) as response:
                installed_pending = json.loads(response.read().decode("utf-8"))
            if installed_pending["items"]:
                break
            time.sleep(0.01)
        assert installed_pending is not None and installed_pending["items"]
        return (
            installed_turn_id,
            installed_session,
            installed_thread,
            installed_outcome,
            installed_pending["items"][0],
        )

    def decide_installed_permission(item, turn_id, decision):
        request = urllib.request.Request(
            base + "/api/v1/permissions/" + item["permissionId"] + "/decision",
            data=json.dumps({
                "turnId": turn_id,
                "decision": decision,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    installed_allow_target = workspace / "installed-absolute-allow.txt"
    installed_allow_writes = []
    original_path_write_text = Path.write_text
    def count_installed_allow(self, data, *args, **kwargs):
        if self == installed_allow_target:
            installed_allow_writes.append(data)
        return original_path_write_text(self, data, *args, **kwargs)
    Path.write_text = count_installed_allow
    try:
        (
            installed_allow_turn,
            installed_allow_session,
            installed_allow_thread,
            installed_allow_outcome,
            installed_allow_item,
        ) = installed_write_pending(
            installed_allow_target,
            "installed-absolute-safe\\n",
            "9",
        )
        installed_allow_json = json.dumps(installed_allow_item)
        assert installed_allow_item["kind"] == "edit"
        assert installed_allow_item["reviewable"] is True
        assert installed_allow_item["choices"] == ["allow_once", "deny_once"]
        assert installed_allow_item["review"] == {
            "targetPath": "installed-absolute-allow.txt",
            "diffPreview": (
                "--- a/installed-absolute-allow.txt\\n"
                "+++ b/installed-absolute-allow.txt\\n"
                "@@ -0,0 +1 @@\\n"
                "+installed-absolute-safe"
            ),
            "complete": True,
            "truncated": False,
            "redacted": False,
        }
        assert str(workspace) not in installed_allow_json
        assert "[LOCAL_PATH]" not in installed_allow_json
        installed_allow_decision = decide_installed_permission(
            installed_allow_item,
            installed_allow_turn,
            "allow_once",
        )
        assert installed_allow_decision["decisionAccepted"] is True
        installed_allow_thread.join(timeout=5)
        assert not installed_allow_thread.is_alive()
        assert "error" not in installed_allow_outcome
        assert installed_allow_target.read_text(encoding="utf-8") == (
            "installed-absolute-safe\\n"
        )
        assert installed_allow_writes == ["installed-absolute-safe\\n"]
        installed_allow_session.close()
    finally:
        Path.write_text = original_path_write_text

    installed_deny_target = workspace / "installed-absolute-deny.txt"
    (
        installed_deny_turn,
        installed_deny_session,
        installed_deny_thread,
        installed_deny_outcome,
        installed_deny_item,
    ) = installed_write_pending(
        installed_deny_target,
        "installed-denied\\n",
        "a",
    )
    assert installed_deny_item["review"]["targetPath"] == (
        "installed-absolute-deny.txt"
    )
    installed_deny_decision = decide_installed_permission(
        installed_deny_item,
        installed_deny_turn,
        "deny_once",
    )
    assert installed_deny_decision["status"] == "denied"
    installed_deny_thread.join(timeout=5)
    assert not installed_deny_target.exists()
    assert isinstance(installed_deny_outcome.get("error"), RuntimeError)
    installed_deny_session.close()

    installed_secret_marker = "installed-file-diff-secret-marker"
    installed_secret_target = workspace / "installed-sensitive-file.txt"
    (
        installed_secret_turn,
        installed_secret_session,
        installed_secret_thread,
        installed_secret_outcome,
        installed_secret_item,
    ) = installed_write_pending(
        installed_secret_target,
        "API_KEY=" + installed_secret_marker + "\\n",
        "b",
    )
    installed_secret_json = json.dumps(installed_secret_item)
    assert installed_secret_item["reviewable"] is False
    assert installed_secret_item["choices"] == ["deny_once"]
    assert installed_secret_item["review"]["redacted"] is True
    assert installed_secret_marker not in installed_secret_json
    installed_secret_allow = urllib.request.Request(
        base + "/api/v1/permissions/" + installed_secret_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": installed_secret_turn,
            "decision": "allow_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(installed_secret_allow, timeout=5)
        raise AssertionError("sensitive installed file diff unexpectedly allowed")
    except urllib.error.HTTPError as error:
        assert error.code == 409
        assert json.loads(error.read().decode("utf-8"))["error"]["code"] == (
            "permission_not_reviewable"
        )
    decide_installed_permission(
        installed_secret_item,
        installed_secret_turn,
        "deny_once",
    )
    installed_secret_thread.join(timeout=5)
    assert not installed_secret_target.exists()
    assert isinstance(installed_secret_outcome.get("error"), RuntimeError)
    installed_secret_session.close()

    installed_control = chr(0x202E)
    installed_control_target = workspace / "installed-control-file.txt"
    (
        installed_control_turn,
        installed_control_session,
        installed_control_thread,
        installed_control_outcome,
        installed_control_item,
    ) = installed_write_pending(
        installed_control_target,
        "safe" + installed_control + "hidden\\n",
        "c",
    )
    installed_control_json = json.dumps(installed_control_item, ensure_ascii=False)
    assert installed_control_item["reviewable"] is False
    assert installed_control_item["choices"] == ["deny_once"]
    assert installed_control_item["review"] == {
        "targetPath": "installed-control-file.txt",
        "diffPreview": "[REDACTED SENSITIVE REVIEW]",
        "complete": True,
        "truncated": False,
        "redacted": True,
    }
    assert installed_control not in installed_control_json
    installed_control_allow = urllib.request.Request(
        base + "/api/v1/permissions/" + installed_control_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": installed_control_turn,
            "decision": "allow_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(installed_control_allow, timeout=5)
        raise AssertionError("control-character installed file diff unexpectedly allowed")
    except urllib.error.HTTPError as error:
        assert error.code == 409
        assert json.loads(error.read().decode("utf-8"))["error"]["code"] == (
            "permission_not_reviewable"
        )
    decide_installed_permission(
        installed_control_item,
        installed_control_turn,
        "deny_once",
    )
    installed_control_thread.join(timeout=5)
    assert not installed_control_thread.is_alive()
    assert not installed_control_target.exists()
    assert isinstance(installed_control_outcome.get("error"), RuntimeError)
    installed_control_session.close()

    safe_turn_id = "turn_66666666666666666666666666666666"
    safe_session = permission_broker.begin_turn(
        turn_id=safe_turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(safe_turn_id),
    )
    safe_outcome = {}
    def installed_safe_command_prompt():
        safe_session.tool_started("run_command")
        try:
            safe_outcome["result"] = safe_session.prompt({
                "schemaVersion": 1,
                "kind": "command",
                "review": {
                    "command": "pytest",
                    "args": ["-q", "tests/relative_test.py"],
                    "cwd": str(workspace),
                    "reason": "Ordinary installed command review.",
                },
            })
        finally:
            safe_session.tool_finished("run_command")
    safe_thread = threading.Thread(target=installed_safe_command_prompt)
    safe_thread.start()
    safe_pending = None
    for _index in range(100):
        with urllib.request.urlopen(base + "/api/v1/permissions/pending", timeout=5) as response:
            safe_pending = json.loads(response.read().decode("utf-8"))
        if safe_pending["items"]:
            break
        time.sleep(0.01)
    safe_item = safe_pending["items"][0]
    assert safe_item["reviewable"] is True
    assert safe_item["review"]["cwd"] == "."
    safe_decision = urllib.request.Request(
        base + "/api/v1/permissions/" + safe_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": safe_turn_id,
            "decision": "allow_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(safe_decision, timeout=5) as response:
        assert json.loads(response.read().decode("utf-8"))["status"] == "allowed"
    safe_thread.join(timeout=5)
    assert safe_outcome["result"] == {"decision": "allow_operation"}
    safe_session.close()

    sensitive_marker = "installed-sensitive-command-marker"
    sensitive_turn_id = "turn_77777777777777777777777777777777"
    permission_journal = RunJournal(
        workspace,
        data_dir=workspace / ".permission-audit-state",
    )
    permission_run = permission_journal.create_run(
        title="installed sensitive permission",
        source="gateway",
    )
    permission_journal.transition(permission_run.id, "running")
    sensitive_session = permission_broker.begin_turn(
        turn_id=sensitive_turn_id,
        run_id=permission_run.id,
        cancellation_token=TurnCancellationToken(sensitive_turn_id),
        event_sink=lambda event_type, payload: permission_journal.append_event(
            permission_run.id,
            event_type,
            payload=payload,
        ),
    )
    sensitive_outcome = {}
    def installed_sensitive_command_prompt():
        sensitive_session.tool_started("run_command")
        try:
            sensitive_outcome["result"] = sensitive_session.prompt({
                "schemaVersion": 1,
                "kind": "command",
                "review": {
                    "command": "tool",
                    "args": ["--password", sensitive_marker],
                    "cwd": str(workspace),
                    "reason": "Installed sensitive command review.",
                },
            })
        finally:
            sensitive_session.tool_finished("run_command")
    sensitive_thread = threading.Thread(target=installed_sensitive_command_prompt)
    sensitive_thread.start()
    sensitive_pending = None
    for _index in range(100):
        with urllib.request.urlopen(base + "/api/v1/permissions/pending", timeout=5) as response:
            sensitive_pending = json.loads(response.read().decode("utf-8"))
        if sensitive_pending["items"]:
            break
        time.sleep(0.01)
    sensitive_json = json.dumps(sensitive_pending)
    sensitive_item = sensitive_pending["items"][0]
    assert sensitive_item["reviewable"] is False
    assert sensitive_item["choices"] == ["deny_once"]
    assert sensitive_marker not in sensitive_json
    assert str(workspace) not in sensitive_json
    sensitive_allow = urllib.request.Request(
        base + "/api/v1/permissions/" + sensitive_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": sensitive_turn_id,
            "decision": "allow_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(sensitive_allow, timeout=5)
        raise AssertionError("sensitive command unexpectedly allowed")
    except urllib.error.HTTPError as error:
        assert error.code == 409
        error_body = error.read().decode("utf-8")
        assert json.loads(error_body)["error"]["code"] == "permission_not_reviewable"
        assert sensitive_marker not in error_body
    sensitive_deny = urllib.request.Request(
        base + "/api/v1/permissions/" + sensitive_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": sensitive_turn_id,
            "decision": "deny_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(sensitive_deny, timeout=5) as response:
        deny_body = response.read().decode("utf-8")
        assert json.loads(deny_body)["status"] == "denied"
        assert sensitive_marker not in deny_body
    sensitive_thread.join(timeout=5)
    assert sensitive_outcome["result"] == {"decision": "deny_operation"}
    sensitive_session.close()
    permission_journal.transition(permission_run.id, "completed")
    permission_events_json = json.dumps([
        item.payload
        for item in permission_journal.list_events(permission_run.id, limit=50).items
        if item.type.startswith("permission.")
    ])
    assert sensitive_marker not in permission_events_json
    assert str(workspace) not in permission_events_json
    sse = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    sse.request("GET", "/api/v1/events", headers={"Accept": "text/event-stream"})
    sse_response = sse.getresponse()
    assert sse_response.status == 200
    assert sse_response.headers["Content-Type"] == "text/event-stream; charset=utf-8"
    assert sse_response.headers.get("Content-Length") is None
    ready_lines = []
    while True:
        line = sse_response.fp.readline()
        assert line
        ready_lines.append(line)
        if line in {b"\\n", b"\\r\\n"}: break
    ready_frame = b"".join(ready_lines)
    assert b"event: stream.ready\\n" in ready_frame
    assert b'"schemaVersion":2' in ready_frame

    invalidation_secret = "installed-permission-invalidation-secret"
    invalidation_turn_id = "turn_88888888888888888888888888888888"
    invalidation_session = permission_broker.begin_turn(
        turn_id=invalidation_turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(invalidation_turn_id),
    )
    invalidation_outcome = {}
    def installed_invalidation_prompt():
        invalidation_session.tool_started("write_file")
        try:
            invalidation_outcome["result"] = invalidation_session.prompt({
                "schemaVersion": 1,
                "kind": "edit",
                "review": {
                    "targetPath": str(workspace / "invalidation.txt"),
                    "diffPreview": invalidation_secret,
                },
            })
        finally:
            invalidation_session.tool_finished("write_file")
    invalidation_thread = threading.Thread(target=installed_invalidation_prompt)
    invalidation_thread.start()
    invalidation_pending = None
    for _index in range(100):
        with urllib.request.urlopen(base + "/api/v1/permissions/pending", timeout=5) as response:
            invalidation_pending = json.loads(response.read().decode("utf-8"))
        if invalidation_pending["items"]:
            break
        time.sleep(0.01)
    permission_changed_frame = b""
    while b'"name":"permissions"' not in permission_changed_frame:
        permission_changed_lines = []
        while True:
            line = sse_response.fp.readline()
            assert line
            permission_changed_lines.append(line)
            if line in {b"\\n", b"\\r\\n"}: break
        candidate_frame = b"".join(permission_changed_lines)
        if b"event: resources.changed\\n" in candidate_frame:
            permission_changed_frame = candidate_frame
    assert b'"schemaVersion":2' in permission_changed_frame
    assert invalidation_secret.encode("utf-8") not in permission_changed_frame
    assert invalidation_turn_id.encode("ascii") not in permission_changed_frame
    invalidation_item = invalidation_pending["items"][0]
    invalidation_deny = urllib.request.Request(
        base + "/api/v1/permissions/" + invalidation_item["permissionId"] + "/decision",
        data=json.dumps({
            "turnId": invalidation_turn_id,
            "decision": "deny_once",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(invalidation_deny, timeout=5) as response:
        assert json.loads(response.read().decode("utf-8"))["status"] == "denied"
    invalidation_thread.join(timeout=5)
    assert invalidation_outcome["result"] == {"decision": "deny_operation"}
    invalidation_session.close()

    (workspace / ".mini-code-memory").mkdir(exist_ok=True)
    installed_memory = workspace / ".mini-code-memory" / "MEMORY.md"
    installed_memory.write_text(
        "installed-secret", encoding="utf-8"
    )
    changed_lines = []
    while True:
        line = sse_response.fp.readline()
        assert line
        changed_lines.append(line)
        if line in {b"\\n", b"\\r\\n"}:
            frame = b"".join(changed_lines)
            if (b"event: resources.changed\\n" in frame
                    and b'"name":"memory"' in frame):
                break
            changed_lines = []
    assert b'"name":"memory"' in frame
    assert b"installed-secret" not in frame
    changed_id = next(
        line.removeprefix(b"id: ").strip().decode("ascii")
        for line in frame.splitlines()
        if line.startswith(b"id: ")
    )
    sse.close()
    skill = workspace / ".mini-code" / "skills" / "installed" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("installed-secret", encoding="utf-8")
    time.sleep(2.2)
    replay = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    replay.request(
        "GET",
        "/api/v1/events",
        headers={"Accept": "text/event-stream", "Last-Event-ID": changed_id},
    )
    replay_response = replay.getresponse()
    replay_lines = []
    while True:
        line = replay_response.fp.readline()
        assert line
        replay_lines.append(line)
        if line in {b"\\n", b"\\r\\n"}: break
    replay_frame = b"".join(replay_lines)
    assert b"event: resources.changed\\n" in replay_frame
    assert b'"name":"skills"' in replay_frame
    assert b"installed-secret" not in replay_frame
    replay.close()
    installed_memory.unlink()
    skill.unlink()
    request = urllib.request.Request(
        base + "/run",
        data=json.dumps({"prompt": "Installed Gateway task"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        run_response = json.loads(response.read().decode("utf-8"))
        assert run_response == {"ok": True, "response": "installed response"}
    assert gateway_registry_dependencies == [current_registry]
    journal = RunJournal(workspace)
    run = journal.list_runs().items[0]
    assert run.source == "gateway"
    assert run.status == "completed"
    with urllib.request.urlopen(base + "/", timeout=5) as response:
        assert "CodeLoop · Dashboard" in response.read().decode("utf-8")
    with urllib.request.urlopen(base + "/assets/app.js", timeout=5) as response:
        installed_javascript = response.read()
        assert b"live invalidation, connection-scoped Chat presentation" in installed_javascript
        assert b"/api/v1/changes" in installed_javascript
        assert b"/api/v1/data-health" in installed_javascript
        assert b"/api/v1/permissions/pending" in installed_javascript
        assert b"const permissionStore" in installed_javascript
        assert "仅允许这一次".encode("utf-8") in installed_javascript
        assert "拒绝这一次".encode("utf-8") in installed_javascript
        assert installed_javascript.count(b"new EventSource('/api/v1/events')") == 1
        assert b"DATA.sessions" not in installed_javascript
    with urllib.request.urlopen(base + "/assets/cost-format.js", timeout=5) as response:
        assert b"BigInt(value)" in response.read()
    with urllib.request.urlopen(base + "/api/v1/changes", timeout=5) as response:
        changes = json.loads(response.read().decode("utf-8"))
        assert response.headers["Cache-Control"] == "no-store"
        assert changes["schemaVersion"] == 2
        assert changes["mode"] == "read-only"
        assert changes["pollAfterMs"] == 2000
        assert set(changes["resources"]) == {
            "runs", "sessions", "turns", "memory", "skills", "connections",
            "permissions",
        }
        assert all(
            item["revision"].startswith("rev_")
            for item in changes["resources"].values()
        )
    with urllib.request.urlopen(base + "/api/v1/snapshot", timeout=5) as response:
        snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["schemaVersion"] == 1
        assert snapshot["workspace"]["name"] == "workspace"
        assert snapshot["overview"]["runs"]["status"] == "live"
        assert snapshot["overview"]["runs"]["count"] == 1
        assert snapshot["overview"]["runs"]["coverage"]["historical"] == "partial"
        assert snapshot["overview"]["usage"]["inputTokens"] == 140
        assert snapshot["overview"]["usage"]["outputTokens"] == 30
        assert snapshot["overview"]["usage"]["durationMs"] == 400
        assert snapshot["overview"]["usage"]["costUsd"] is None
        assert snapshot["overview"]["usage"]["cost"]["status"] == "partial"
        assert snapshot["overview"]["usage"]["cost"]["value"]["amountNanoUsd"] == "443750"
        assert snapshot["overview"]["usage"]["cost"]["coverage"]["unavailableCalls"] == 1
        assert snapshot["overview"]["usage"]["tools"]["status"] == "partial"
        assert snapshot["overview"]["usage"]["tools"]["value"] == {
            "observedCalls": 2,
            "startedCalls": 1,
            "completedCalls": 2,
            "pairedCalls": 1,
            "successfulCalls": 1,
            "errorCalls": 1,
            "uniqueTools": 2,
        }
        assert snapshot["overview"]["usage"]["failures"]["value"]["affectedRuns"] == 1
        assert snapshot["overview"]["usage"]["failures"]["value"]["toolErrors"] == 1
    with urllib.request.urlopen(base + "/api/v1/runs", timeout=5) as response:
        runs = json.loads(response.read().decode("utf-8"))
        assert runs["schemaVersion"] == 1
        assert runs["mode"] == "read-only"
        assert runs["summary"]["knownTotal"] == 1
        assert runs["coverage"]["journal"] == "live"
        assert runs["coverage"]["gateway"] == "live"
        assert runs["coverage"]["scope"] == "lifecycle-model-usage-cost-tool-assistant-skill-memory-context"
        assert runs["coverage"]["context"] == "partial"
        assert runs["coverage"]["workingMemory"] == "partial"
        assert runs["coverage"]["model"] == "live"
        assert runs["coverage"]["usage"] == "live"
        assert runs["coverage"]["cost"] == "live"
        assert runs["items"][0]["id"] == run.id
        assert runs["items"][0]["cost"] == {
            "status": "partial",
            "amountNanoUsd": "443750",
            "currency": "USD",
            "pricedCalls": 1,
            "unpricedCalls": 1,
            "failedAttempts": 0,
            "limited": False,
        }
        assert runs["items"][0]["tools"] == {
            "status": "partial",
            "observedCalls": 2,
            "errorCalls": 1,
            "uniqueTools": 2,
            "limited": False,
        }
        assert runs["items"][0]["failures"] == {
            "status": "partial",
            "hasObservedFailure": True,
            "toolErrors": 1,
            "modelFailures": 0,
            "runFailed": False,
            "interrupted": False,
            "cancelled": False,
            "limited": False,
        }
    with urllib.request.urlopen(base + "/api/v1/runs/" + run.id, timeout=5) as response:
        detail = json.loads(response.read().decode("utf-8"))
        assert detail["run"]["status"] == "completed"
        assert [event["sequence"] for event in detail["events"]] == list(range(1, 20))
        assert [event["type"] for event in detail["events"]] == [
            "run.queued", "run.started", "skill.routed", "model.started", "model.completed",
            "model.costed", "tool.started", "tool.finished", "tool.finished", "model.started", "model.completed",
            "model.costed", "recovery.started", "context.compacted",
            "recovery.completed", "working_memory.observed", "mcp.runtime.observed",
            "assistant.completed", "run.completed",
        ]
        assert detail["events"][2]["details"]["selectedCount"] == 0
        assert detail["events"][3]["step"] == 1
        assert detail["events"][4]["details"]["resultType"] == "tool_calls"
        assert detail["events"][4]["details"]["usage"]["source"] == "provider"
        assert detail["events"][4]["details"]["durationMs"] == 125
        assert detail["events"][5]["details"]["amountNanoUsd"] == 443750
        assert detail["events"][6]["details"]["toolName"] == "read_file"
        assert detail["events"][7]["details"]["outcome"] == "success"
        assert detail["events"][8]["details"] == {
            "toolName": "run_command",
            "outcome": "error",
            "paired": False,
        }
        assert all(
            "operationId" not in event["details"]
            for event in detail["events"]
            if event["type"].startswith("tool.")
        )
        assert detail["events"][9]["step"] == 2
        assert detail["events"][10]["details"]["resultType"] == "assistant"
        assert detail["events"][10]["details"]["usage"]["source"] == "estimated"
        assert detail["events"][10]["details"]["durationMs"] == 275
        assert detail["events"][11]["details"]["reason"] == "pricing_incomplete"
        assert detail["events"][12]["details"] == {
            "recoveryVersion": 1,
            "kind": "compactor",
            "reason": "context_overflow",
        }
        assert detail["events"][13]["details"] == {
            "contextVersion": 1,
            "path": "reactive_compactor",
            "trigger": "reactive",
            "strategy": "reactive",
            "effective": True,
            "messagesBefore": 8,
            "messagesAfter": 5,
            "messagesRemoved": 3,
            "tokensFreed": 240,
        }
        assert detail["events"][14]["details"] == {
            "recoveryVersion": 1,
            "kind": "compactor",
            "outcome": "recovered",
            "messagesBefore": 8,
            "messagesAfter": 5,
            "tokensFreed": 240,
        }
        assert detail["events"][15]["details"] == {
            "workingMemoryVersion": 1,
            "action": "protected",
            "scope": "process",
            "entries": 2,
            "maxEntries": 15,
            "protectedTokens": 18,
            "maxTokens": 4000,
        }
        assert detail["events"][16]["details"] == {
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "installed-server"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": True,
            "protocol": "newline-json",
        }
        assert detail["events"][17]["details"] == {
            "contentPresent": True,
            "contentLength": 18,
            "kind": "returned_assistant",
        }
        assert "installed-input-secret" not in json.dumps(detail)
        assert "installed-output-secret" not in json.dumps(detail)
        assert "installed-error-output-secret" not in json.dumps(detail)
        assert detail["metrics"]["cost"]["status"] == "partial"
        assert detail["metrics"]["cost"]["value"]["amountNanoUsd"] == "443750"
        assert detail["metrics"]["cost"]["value"]["providerUsageNanoUsd"] == "443750"
        assert detail["metrics"]["cost"]["value"]["estimatedUsageNanoUsd"] == "0"
        assert detail["metrics"]["cost"]["coverage"]["pricedCalls"] == 1
        assert detail["metrics"]["cost"]["coverage"]["unavailableCalls"] == 1
        assert detail["metrics"]["tokens"]["status"] == "partial"
        assert detail["metrics"]["tokens"]["value"]["totalTokens"] == 175
        assert detail["metrics"]["duration"]["status"] == "live"
        assert detail["metrics"]["duration"]["value"]["totalMs"] == 400
        assert detail["metrics"]["toolCalls"]["status"] == "partial"
        assert detail["metrics"]["toolCalls"]["value"]["observedCalls"] == 2
        assert detail["metrics"]["toolCalls"]["value"]["errorCalls"] == 1
        assert detail["metrics"]["errors"]["value"]["affectedRuns"] == 1
        assert detail["metrics"]["errors"]["value"]["toolErrors"] == 1
        assert detail["metrics"]["context"]["status"] == "partial"
        assert detail["metrics"]["context"]["value"]["observedCompactions"] == 1
        assert detail["metrics"]["context"]["value"]["messagesRemoved"] == 3
        assert detail["metrics"]["recovery"]["value"]["recoveredAttempts"] == 1
        assert detail["metrics"]["workingMemory"]["value"]["latestObservation"]["entries"] == 2
        assert "totalErrors" not in detail["metrics"]["errors"]["value"]
    with urllib.request.urlopen(base + "/api/v1/ops", timeout=5) as response:
        ops = json.loads(response.read().decode("utf-8"))
        assert ops["mode"] == "read-only"
        assert ops["summary"]["providerCalls"] == 1
        assert ops["summary"]["estimatedCalls"] == 1
        assert ops["usage"]["combined"]["totalTokens"] == 175
        assert ops["duration"]["totalMs"] == 400
        assert ops["coverage"]["scope"] == "model-usage-duration-cost-tool-failure-context-working-memory"
        assert ops["coverage"]["context"] == "partial"
        assert ops["coverage"]["workingMemory"] == "partial"
        assert ops["summary"]["pricedCalls"] == 1
        assert ops["summary"]["unavailableCostCalls"] == 1
        assert ops["summary"]["missingCostCalls"] == 0
        assert ops["cost"]["status"] == "partial"
        assert ops["cost"]["value"]["amountNanoUsd"] == "443750"
        assert ops["costBreakdown"]["unavailableReasons"] == [
            {"reason": "pricing_incomplete", "calls": 1}
        ]
        assert ops["summary"]["observedToolCalls"] == 2
        assert ops["summary"]["successfulToolCalls"] == 1
        assert ops["summary"]["toolErrorCalls"] == 1
        assert ops["summary"]["affectedRuns"] == 1
        assert ops["summary"]["observedCompactions"] == 1
        assert ops["summary"]["workingMemorySnapshots"] == 1
        assert ops["context"]["value"]["observedCompactions"] == 1
        assert ops["workingMemory"]["value"]["latestObservation"]["entries"] == 2
        assert ops["tools"]["status"] == "partial"
        assert ops["toolBreakdown"]["outcomes"] == [
            {"outcome": "success", "calls": 1},
            {"outcome": "error", "calls": 1},
            {"outcome": "incomplete", "calls": 0},
            {"outcome": "unpaired", "calls": 1},
        ]
        assert ops["failures"]["value"]["toolErrors"] == 1
        assert ops["failureBreakdown"]["categories"][0] == {
            "category": "tool_errors",
            "count": 1,
        }
        assert "totalErrors" not in json.dumps(ops)
    with urllib.request.urlopen(base + "/api/v1/sessions", timeout=5) as response:
        sessions = json.loads(response.read().decode("utf-8"))
        assert sessions["schemaVersion"] == 1
        assert sessions["mode"] == "read-only"
        assert [item["id"] for item in sessions["items"]] == [
            installed_session.session_id
        ]
    with urllib.request.urlopen(
        base + "/api/v1/sessions/" + installed_session.session_id + "?limit=50",
        timeout=5,
    ) as response:
        session_detail = json.loads(response.read().decode("utf-8"))
        assert session_detail["mode"] == "read-only"
        assert session_detail["session"]["id"] == installed_session.session_id
        assert [(item["role"], item["content"]) for item in session_detail["messages"]] == [
            ("user", "installed turn one"),
            ("assistant", "installed reply one"),
            ("user", "installed turn two"),
            ("assistant", "installed reply two"),
        ]
    with urllib.request.urlopen(base + "/api/v1/memory", timeout=5) as response:
            memory = json.loads(response.read().decode("utf-8"))
            assert memory["schemaVersion"] == 1
            assert memory["mode"] == "read-only"
            assert memory["summary"]["total"] == 2
            assert {item["id"] for item in memory["items"]} == {
                installed_pending_memory.id,
                installed_rejected_memory.id,
            }
    with urllib.request.urlopen(base + "/api/v1/skills", timeout=5) as response:
        skills = json.loads(response.read().decode("utf-8"))
        assert skills["schemaVersion"] == 1
        assert skills["source"]["status"] == "live"
        assert skills["diagnostics"] == []
        assert skills["summary"]["total"] == 2
        assert skills["summary"]["bySource"] == {
            "project": 1,
            "user": 0,
            "compat_project": 1,
            "compat_user": 0,
        }
        assert {item["name"] for item in skills["items"]} == {
            "project-skill", "compat-skill"
        }
        encoded_skills = json.dumps(skills)
        assert "installed-ds-store-secret" not in encoded_skills
        assert "installed-compat-ds-store-secret" not in encoded_skills
        assert "installed-readme-secret" not in encoded_skills
        assert str(workspace) not in encoded_skills
    with urllib.request.urlopen(base + "/api/v1/connections", timeout=5) as response:
        connections = json.loads(response.read().decode("utf-8"))
        assert connections["gateway"]["status"] == "live"
        assert connections["summary"]["configuredMcpCount"] == 1
        assert connections["summary"]["registeredConfiguredMcpCount"] == 1
        assert connections["summary"]["activeMcpInstanceCount"] == 1
        assert connections["summary"]["liveMcpCount"] == 1
        assert connections["summary"]["observedConfiguredCount"] == 1
        assert connections["summary"]["unobservedConfiguredCount"] == 0
        assert connections["summary"]["unmatchedObservedServerCount"] == 0
        assert connections["mcpCurrent"]["status"] == "live"
        assert connections["mcpCurrent"]["current"] == "process-local"
        assert connections["mcpCurrent"]["byState"] == {
            "idle": 0,
            "starting": 0,
            "ready": 1,
            "failed": 0,
        }
        assert connections["mcpRuntime"]["status"] == "stale"
        assert connections["mcpRuntime"]["current"] == "unavailable"
        assert connections["mcpRuntime"]["historical"] == "partial"
        assert connections["mcpRuntime"]["retainedObservationCount"] == 1
        assert connections["coverage"]["retainedRuns"] == 1
        assert connections["coverage"]["scannedRuns"] == 1
        assert connections["coverage"]["limited"] is False
        assert connections["mcpServers"][0]["name"] == "installed-server"
        assert connections["mcpServers"][0]["status"] == "configured"
        assert connections["mcpServers"][0]["liveStatus"] == "ready"
        assert connections["mcpServers"][0]["current"]["state"] == "ready"
        assert connections["mcpServers"][0]["current"]["activeInstanceCount"] == 1
        assert connections["mcpServers"][0]["current"]["protocol"] == "newline-json"
        assert connections["mcpServers"][0]["current"]["reason"] is None
        assert connections["mcpServers"][0]["runtime"]["lastOutcome"] == "request_succeeded"
        assert connections["mcpServers"][0]["runtime"]["connectionAttempted"] is True
        assert connections["mcpServers"][0]["runtime"]["observedProtocol"] == "newline-json"
        assert mcp_server_key(workspace, "installed-server") not in json.dumps(connections)
        assert unmatched_probe_calls == [0]
        assert "installed-other-workspace-secret" not in json.dumps(connections)
    http_client.close()
    with urllib.request.urlopen(base + "/api/v1/connections", timeout=5) as response:
        closed_connections = json.loads(response.read().decode("utf-8"))
        assert closed_connections["summary"]["registeredConfiguredMcpCount"] == 0
        assert closed_connections["summary"]["activeMcpInstanceCount"] == 0
        assert closed_connections["summary"]["liveMcpCount"] == 0
        assert closed_connections["mcpCurrent"]["byState"] == {
            "idle": 0,
            "starting": 0,
            "ready": 0,
            "failed": 0,
        }
        assert closed_connections["mcpServers"][0]["liveStatus"] == "unavailable"
        assert closed_connections["mcpServers"][0]["current"]["reason"] == "not_registered"
        assert unmatched_probe_calls == [0]
    with urllib.request.urlopen(base + "/api/v1/system", timeout=5) as response:
        system = json.loads(response.read().decode("utf-8"))
        assert system["application"]["name"] == "minicode-py"
        assert system["application"]["version"] == "0.1.0"
        assert system["runtime"]["processMode"] == "gateway"
        assert system["features"]["runs"] == "lifecycle-model-usage-cost-tool-assistant-skill-memory-context"
        assert system["features"]["usage"] == "live"
    chat_request = urllib.request.Request(
        base + "/api/v1/chat/turns",
        data=json.dumps({
            "message": "installed chat turn",
            "turnId": "turn_33333333333333333333333333333333",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(chat_request, timeout=5) as response:
        chat = json.loads(response.read().decode("utf-8"))
        assert chat["ok"] is True
        assert chat["mode"] == "read-write"
        assert chat["turnId"] == "turn_33333333333333333333333333333333"
        assert chat["created"] is True
        assert chat["assistant"] == {
            "role": "assistant",
            "content": "installed response",
        }
        assert chat["runId"].startswith("run_")
    server.conversation_turn_service = ConversationTurnService(
        workspace,
        mcp_current_state_registry=current_registry,
    )
    with urllib.request.urlopen(
        base + "/api/v1/chat/turns/turn_33333333333333333333333333333333",
        timeout=5,
    ) as response:
        installed_status = json.loads(response.read().decode("utf-8"))
        assert installed_status["status"] == "completed"
        assert installed_status["resultAvailable"] is True
    with urllib.request.urlopen(chat_request, timeout=5) as response:
        duplicate_chat = json.loads(response.read().decode("utf-8"))
        assert duplicate_chat["sessionId"] == chat["sessionId"]
        assert duplicate_chat["assistant"] == chat["assistant"]
    cancel_completed_request = urllib.request.Request(
        base + "/api/v1/chat/turns/turn_33333333333333333333333333333333/cancel",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(cancel_completed_request, timeout=5) as response:
        completed_cancel = json.loads(response.read().decode("utf-8"))
        assert completed_cancel["status"] == "completed"
        assert completed_cancel["cancellationAccepted"] is False
    with urllib.request.urlopen(
        base + "/api/v1/sessions/" + chat["sessionId"] + "?limit=50",
        timeout=5,
    ) as response:
        chat_detail = json.loads(response.read().decode("utf-8"))
        assert [(item["role"], item["content"]) for item in chat_detail["messages"]] == [
            ("user", "installed chat turn"),
            ("assistant", "installed response"),
        ]
    chat_runs = RunJournal(workspace).list_runs(limit=100).items
    chat_run = next(item for item in chat_runs if item.id == chat["runId"])
    assert chat_run.source == "gateway"
    assert chat_run.session_id == chat["sessionId"]
    assert [item for item in chat_runs if item.session_id == chat["sessionId"]] == [chat_run]
    restart_turn_id = "turn_44444444444444444444444444444444"
    stale_store = ConversationTurnStore(
        workspace,
        data_dir=config_module.MINI_CODE_DIR,
        owner_id="1" * 32,
    )
    restart_fingerprint = request_fingerprint(
        workspace_id=stale_store.workspace_id,
        session_id=None,
        message="installed restart cancellation",
    )
    stale_store.claim(turn_id=restart_turn_id, fingerprint=restart_fingerprint)
    stale_store.mark_running(restart_turn_id)
    stale_store.request_cancel(restart_turn_id)
    stale_store.release_claim(restart_turn_id)
    server.conversation_turn_service = ConversationTurnService(
        workspace,
        turn_store=ConversationTurnStore(
            workspace,
            data_dir=config_module.MINI_CODE_DIR,
            owner_id="2" * 32,
        ),
    )
    with urllib.request.urlopen(
        base + "/api/v1/chat/turns/" + restart_turn_id,
        timeout=5,
    ) as response:
        restarted_cancel = json.loads(response.read().decode("utf-8"))
        assert restarted_cancel["status"] == "cancelled"
        assert restarted_cancel["resultAvailable"] is False
finally:
    permission_broker.close()
    http_client.close()
    current_registry.unregister(unmatched_handle)
    server.dashboard_event_stream.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
"""
    env = os.environ.copy()
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    isolated_workspace = tmp_path / "workspace"
    isolated_workspace.mkdir()
    env["HOME"] = str(isolated_home)
    env["USERPROFILE"] = str(isolated_home)
    env["MINI_CODE_DASHBOARD_WORKSPACE"] = str(isolated_workspace)
    env["PYTHONPATH"] = str(installed)
    env["PYTHONNOUSERSITE"] = "1"
    # Windows CreateProcess has a much smaller command-line limit than POSIX.
    # Keep this broad installed-wheel smoke intact, but execute it from a
    # temporary file instead of passing thousands of lines through ``-c``.
    smoke_path = tmp_path / "installed-wheel-smoke.py"
    smoke_path.write_text(smoke_script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [sys.executable, str(smoke_path)],
        cwd=isolated_workspace,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # The installed-wheel smoke intentionally exercises a broad runtime
        # surface. Hosted macOS runners can take more than 30 seconds even
        # when every assertion succeeds, so keep a real wall-clock ceiling
        # without treating runner startup variance as a product failure.
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_root_smoke_scripts_are_not_pytest_collected() -> None:
    import conftest

    root_smoke_scripts = {
        path.name
        for pattern in ("test_*.py", "*_test.py")
        for path in ROOT.glob(pattern)
    }

    # After cleanup: root smoke scripts were migrated to tests/ or deleted.
    # If any remain, they must be excluded from pytest collection.
    if root_smoke_scripts:
        assert root_smoke_scripts.issubset(set(conftest.collect_ignore))
    assert "benchmarks/*.py" in conftest.collect_ignore_glob


def test_ci_workflow_runs_release_quality_gates() -> None:
    workflow = ROOT / ".github" / "workflows" / "ci.yml"

    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")
    assert "python -m compileall -q minicode tests" in content
    assert "python -m pytest -q" in content
    assert "tests/test_packaging.py" in content


def test_cron_runner_empty_config_exits_cleanly(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing-cron.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "minicode.cron_runner",
            "--once",
            "--dry-run",
            "--config",
            str(missing_config),
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "No cron tasks configured" in completed.stdout


def test_gateway_health_endpoint_responds() -> None:
    from http.server import ThreadingHTTPServer

    from minicode.gateway import MiniCodeGatewayHandler

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"ok": True, "service": "minicode-gateway"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post_gateway_json(port: int, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/run",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_gateway_run_endpoint_returns_headless_response(monkeypatch) -> None:
    from http.server import ThreadingHTTPServer

    import minicode.headless
    from minicode.gateway import MiniCodeGatewayHandler

    calls = []

    def fake_headless(prompt: str, *, run_source: str = "headless") -> str:
        calls.append((prompt, run_source))
        return f"mock:{prompt}"

    monkeypatch.setattr(minicode.headless, "run_headless", fake_headless)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_gateway_json(server.server_address[1], {"prompt": "hello"})
        assert status == 200
        assert payload == {"ok": True, "response": "mock:hello"}
        assert calls == [("hello", "gateway")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_gateway_run_endpoint_converts_system_exit_to_json_error(
    monkeypatch, capsys
) -> None:
    from http.server import ThreadingHTTPServer

    import minicode.headless
    from minicode.gateway import MiniCodeGatewayHandler

    def fail_headless(_prompt: str, *, run_source: str = "headless") -> str:
        assert run_source == "gateway"
        raise SystemExit("missing config")

    monkeypatch.setattr(minicode.headless, "run_headless", fail_headless)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_gateway_json(server.server_address[1], {"prompt": "hello"})
        assert status == 500
        assert payload["ok"] is False
        assert "missing config" in payload["error"]
        assert "missing config" not in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
