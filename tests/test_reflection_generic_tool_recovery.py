"""Tool-agnostic recovery lessons for non-command tools.

The recovery contract must follow structured attempts, not a verifier-tool
allowlist.  These cases cover built-in and future/unknown tools while pinning
the false-positive boundaries needed for automatic Memory approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import minicode.memory as memory_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import (
    TraceEvidenceExtractor,
    extract_tool_file_roles,
)
from minicode.reflection_synthesis import (
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _attempt(
    index: int,
    call_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    ok: bool,
    output: str,
) -> list[dict[str, Any]]:
    roles = extract_tool_file_roles(tool_name, tool_input, event_type="tool_call")
    role_fields = {key: value for key, value in roles.items() if value}
    files = sorted({path for values in roles.values() for path in values})
    events: list[dict[str, Any]] = [
        {
            "event_id": f"e{index}",
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool_name,
            "input": tool_input,
            "files": files,
            **role_fields,
        },
        {
            "event_id": f"e{index + 1}",
            "type": "tool_result",
            "call_id": call_id,
            "tool_name": tool_name,
            "status": "success" if ok else "error",
            "is_error": not ok,
            "output_summary": output,
            "files": files,
            **role_fields,
        },
    ]
    if not ok:
        events.append(
            {
                "event_id": f"e{index + 2}",
                "type": "error",
                "call_id": call_id,
                "tool_name": tool_name,
                "error_type": "ToolError",
                "message": output,
                "files": files,
                **role_fields,
            }
        )
    return events


def _done(index: int) -> dict[str, Any]:
    return {
        "event_id": f"e{index}",
        "type": "task_result",
        "status": "success",
        "had_errors": True,
        "errors_recovered": True,
        "tool_error_count": 1,
    }


def _evaluate(trace: list[dict[str, Any]]):
    task = "Inspect and repair the project"
    evidence = TraceEvidenceExtractor().extract(task, trace)
    candidate = RuleReflectionSynthesizer().synthesize(task, evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return evidence, candidate, decision


def test_read_file_corrected_path_becomes_a_verified_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "read-failed",
            "read_file",
            {"path": "src/auth.py", "offset": 0, "limit": 200},
            ok=False,
            output="[FileNotFoundError] src/auth.py does not exist",
        ),
        *_attempt(
            10,
            "read-passed",
            "read_file",
            {"path": "backend/src/auth.py", "offset": 0, "limit": 200},
            ok=True,
            output="FILE: backend/src/auth.py\nclass AuthService: ...",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    recovery = evidence.recoveries[0]
    assert recovery.epistemic_status == "confirmed"
    assert "src/auth.py" in recovery.action
    assert "backend/src/auth.py" in recovery.action
    assert recovery.action.startswith("Verified recovery: use the corrected read_file invocation")
    assert "do not reuse the failed invocation" in recovery.action
    assert recovery.action.index("backend/src/auth.py") < recovery.action.rindex("src/auth.py")
    assert any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert decision.accepted is True


def test_edit_context_mismatch_records_the_successful_updated_edit() -> None:
    trace = [
        *_attempt(
            1,
            "edit-failed",
            "edit_file",
            {
                "path": "src/auth.py",
                "old_string": "def login(user):",
                "new_string": "def login(user, token):",
            },
            ok=False,
            output="context mismatch: old_string was not found",
        ),
        *_attempt(
            10,
            "read-current",
            "read_file",
            {"path": "src/auth.py", "offset": 0, "limit": 120},
            ok=True,
            output="def login(user: User):",
        ),
        *_attempt(
            20,
            "edit-passed",
            "edit_file",
            {
                "path": "src/auth.py",
                "old_string": "def login(user: User):",
                "new_string": "def login(user: User, token: str):",
            },
            ok=True,
            output="Updated src/auth.py",
        ),
        _done(30),
    ]

    evidence, _, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    method = f"{evidence.recoveries[0].action} {evidence.recoveries[0].change_summary}"
    assert "def login(user: User):" in method
    assert "def login(user: User, token: str):" in method
    assert decision.accepted is True


def test_web_search_rephrased_same_topic_becomes_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "search-failed",
            "web_search",
            {"query": "MiniCode context compaction ???", "max_results": 5},
            ok=False,
            output="query rejected: unsupported punctuation",
        ),
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode context compaction implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, _, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert "unsupported punctuation" not in evidence.recoveries[0].action
    assert "context compaction implementation" in evidence.recoveries[0].action
    assert decision.accepted is True


def test_future_unknown_tool_uses_structured_input_not_a_tool_allowlist() -> None:
    trace = [
        *_attempt(
            1,
            "inspect-failed",
            "inspect_schema_v2",
            {"resource": "lease renewal", "mode": "strict"},
            ok=False,
            output="strict mode rejected the legacy schema",
        ),
        *_attempt(
            10,
            "inspect-passed",
            "inspect_schema_v2",
            {"resource": "lease renewal", "mode": "compatible"},
            ok=True,
            output="schema inspection completed",
        ),
        _done(20),
    ]

    evidence, _, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert '"mode":"compatible"' in evidence.recoveries[0].action
    assert decision.accepted is True


def test_rate_limited_call_is_not_laundered_by_a_changed_input_success() -> None:
    trace = [
        *_attempt(
            1,
            "search-rate-limited",
            "web_search",
            {"query": "MiniCode memory retrieval status", "max_results": 5},
            ok=False,
            output="HTTP 429 Too Many Requests: rate limit exceeded",
        ),
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_rate_limit_error_type_is_enough_to_block_changed_input_laundering() -> None:
    failed = _attempt(
        1,
        "search-rate-limited",
        "web_search",
        {"query": "MiniCode memory retrieval status", "max_results": 5},
        ok=False,
        output="provider request rejected",
    )
    failed[-1]["error_type"] = "RateLimitError"
    trace = [
        *failed,
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_network_timeout_is_not_laundered_by_a_changed_input_success() -> None:
    trace = [
        *_attempt(
            1,
            "search-timed-out",
            "web_search",
            {"query": "MiniCode memory retrieval status", "max_results": 5},
            ok=False,
            output="ReadTimeout: network connection timed out",
        ),
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_connect_error_refusal_is_not_laundered_by_changed_input_success() -> None:
    failed = _attempt(
        1,
        "search-connection-refused",
        "web_search",
        {"query": "MiniCode memory retrieval status", "max_results": 5},
        ok=False,
        output="[Errno 111] Connection refused",
    )
    failed[-1]["error_type"] = "ConnectError"
    trace = [
        *failed,
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_connection_error_dns_failure_is_not_laundered_by_changed_input() -> None:
    failed = _attempt(
        1,
        "search-dns-failed",
        "web_search",
        {"query": "MiniCode memory retrieval status", "max_results": 5},
        ok=False,
        output="DNS resolution failed for provider host",
    )
    failed[-1]["error_type"] = "ConnectionError"
    trace = [
        *failed,
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_ssl_tls_handshake_failure_is_not_laundered_by_changed_input() -> None:
    failed = _attempt(
        1,
        "search-tls-failed",
        "web_search",
        {"query": "MiniCode memory retrieval status", "max_results": 5},
        ok=False,
        output="TLS handshake failed while connecting to provider",
    )
    failed[-1]["error_type"] = "SSLError"
    trace = [
        *failed,
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_proxy_connection_failure_is_not_laundered_by_changed_input() -> None:
    failed = _attempt(
        1,
        "search-proxy-failed",
        "web_search",
        {"query": "MiniCode memory retrieval status", "max_results": 5},
        ok=False,
        output="Cannot connect to proxy at synthetic-proxy.invalid",
    )
    failed[-1]["error_type"] = "ProxyError"
    trace = [
        *failed,
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


@pytest.mark.parametrize(
    "message",
    [
        "[Errno 111] Connection refused",
        "DNS resolution failed for provider host",
        "TLS handshake failed while connecting to provider",
        "Cannot connect to proxy at synthetic-proxy.invalid",
    ],
    ids=["connection-refused", "dns", "tls", "proxy"],
)
def test_wrapper_error_uses_environmental_message_fallback(message: str) -> None:
    trace = [
        *_attempt(
            1,
            "search-wrapper-failed",
            "web_search",
            {"query": "MiniCode memory retrieval status", "max_results": 5},
            ok=False,
            output=message,
        ),
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_service_unavailable_is_not_laundered_by_a_changed_input_success() -> None:
    trace = [
        *_attempt(
            1,
            "search-unavailable",
            "web_search",
            {"query": "MiniCode memory retrieval status", "max_results": 5},
            ok=False,
            output="HTTP 503 Service Unavailable",
        ),
        *_attempt(
            10,
            "search-passed",
            "web_search",
            {"query": "MiniCode memory retrieval implementation", "max_results": 5},
            ok=True,
            output="3 results",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_lock_contention_is_not_laundered_by_a_changed_input_success() -> None:
    trace = [
        *_attempt(
            1,
            "index-locked",
            "update_index_v2",
            {"resource": "shared catalog", "mode": "strict"},
            ok=False,
            output="database is locked: lock contention on shared catalog",
        ),
        *_attempt(
            10,
            "index-passed",
            "update_index_v2",
            {"resource": "shared catalog", "mode": "compatible"},
            ok=True,
            output="catalog index updated",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_sqlite_table_lock_is_not_laundered_by_changed_input_success() -> None:
    failed = _attempt(
        1,
        "index-table-locked",
        "update_index_v2",
        {"resource": "shared catalog", "mode": "strict"},
        ok=False,
        output="database table is locked: memory_entries",
    )
    failed[-1]["error_type"] = "OperationalError"
    trace = [
        *failed,
        *_attempt(
            10,
            "index-passed",
            "update_index_v2",
            {"resource": "shared catalog", "mode": "compatible"},
            ok=True,
            output="catalog index updated",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert not any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert not any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" not in decision.durable_signals


def test_timeout_argument_validation_still_yields_a_changed_input_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "fetch-invalid-timeout",
            "fetch_page_v2",
            {"resource": "lease records", "timeout": 0},
            ok=False,
            output="ValidationError: argument 'timeout' must be greater than zero",
        ),
        *_attempt(
            10,
            "fetch-passed",
            "fetch_page_v2",
            {"resource": "lease records", "timeout": 20},
            ok=True,
            output="20 lease records returned",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].epistemic_status == "confirmed"
    assert '"timeout":20' in evidence.recoveries[0].action
    assert any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" in decision.durable_signals


def test_explicit_argument_error_overrides_a_broad_connection_error_type() -> None:
    failed = _attempt(
        1,
        "fetch-invalid-endpoint",
        "fetch_endpoint_v2",
        {"resource": "lease records", "endpoint": "not-a-url"},
        ok=False,
        output="invalid argument 'endpoint': expected an HTTPS URL",
    )
    failed[-1]["error_type"] = "ConnectionError"
    trace = [
        *failed,
        *_attempt(
            10,
            "fetch-passed",
            "fetch_endpoint_v2",
            {"resource": "lease records", "endpoint": "https-endpoint"},
            ok=True,
            output="lease records returned",
        ),
        _done(20),
    ]

    evidence, candidate, decision = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    assert evidence.recoveries[0].epistemic_status == "confirmed"
    assert '"endpoint":"https-endpoint"' in evidence.recoveries[0].action
    assert any(item.command_kind == "tool_recovery" for item in evidence.verification)
    assert any(claim.claim_type == "recovery" for claim in candidate.claims)
    assert "verified_solution" in decision.durable_signals


def test_unrelated_later_search_is_not_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "search-failed",
            "web_search",
            {"query": "MiniCode context compaction", "max_results": 5},
            ok=False,
            output="provider rejected query",
        ),
        *_attempt(
            10,
            "search-unrelated",
            "web_search",
            {"query": "Shanghai weather forecast", "max_results": 5},
            ok=True,
            output="5 results",
        ),
        _done(20),
    ]

    evidence, _, decision = _evaluate(trace)

    assert evidence.recoveries == []
    assert "verified_solution" not in decision.durable_signals


def test_unchanged_generic_retry_is_not_a_recovery() -> None:
    tool_input = {"resource": "lease renewal", "mode": "strict"}
    trace = [
        *_attempt(
            1,
            "inspect-failed",
            "inspect_schema_v2",
            tool_input,
            ok=False,
            output="temporary failure",
        ),
        *_attempt(
            10,
            "inspect-passed",
            "inspect_schema_v2",
            tool_input,
            ok=True,
            output="schema inspection completed",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_same_tool_on_a_different_path_is_not_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "read-failed",
            "read_file",
            {"path": "src/auth.py"},
            ok=False,
            output="not found",
        ),
        *_attempt(
            10,
            "read-unrelated",
            "read_file",
            {"path": "docs/billing.md"},
            ok=True,
            output="billing docs",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_same_basename_in_different_directories_is_not_a_recovery() -> None:
    trace = [
        *_attempt(
            1,
            "source-config-failed",
            "read_file",
            {"path": "src/config.py", "offset": 0, "limit": 100},
            ok=False,
            output="permission denied: src/config.py",
        ),
        *_attempt(
            10,
            "test-config-passed",
            "read_file",
            {"path": "tests/config.py", "offset": 0, "limit": 100},
            ok=True,
            output="FILE: tests/config.py\nTEST_CONFIG = {}",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert evidence.recoveries == []


def test_generic_method_redacts_sensitive_structured_fields() -> None:
    trace = [
        *_attempt(
            1,
            "inspect-failed",
            "inspect_schema_v2",
            {
                "resource": "lease renewal",
                "mode": "strict",
                "api_key": "sk-synthetic-secret-123456",
            },
            ok=False,
            output="strict mode rejected the legacy schema",
        ),
        *_attempt(
            10,
            "inspect-passed",
            "inspect_schema_v2",
            {
                "resource": "lease renewal",
                "mode": "compatible",
                "api_key": "sk-another-secret-123456",
            },
            ok=True,
            output="schema inspection completed",
        ),
        _done(20),
    ]

    evidence, _, _ = _evaluate(trace)

    assert len(evidence.recoveries) == 1
    serialized = str(evidence.recoveries[0])
    assert "synthetic-secret" not in serialized
    assert "another-secret" not in serialized
    assert "[REDACTED]" in serialized


def test_generic_recovery_is_auto_approved_and_retrievable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    trace = [
        *_attempt(
            1,
            "read-failed",
            "read_file",
            {"path": "src/auth.py", "offset": 0},
            ok=False,
            output="[FileNotFoundError] src/auth.py does not exist",
        ),
        *_attempt(
            10,
            "read-passed",
            "read_file",
            {"path": "backend/src/auth.py", "offset": 0},
            ok=True,
            output="FILE: backend/src/auth.py\nclass AuthService: ...",
        ),
        _done(20),
    ]

    entry_id = pipeline.write("Inspect the authentication service", trace)

    assert entry_id is not None
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.approval_status == "approved"
    assert entry.is_active is True
    assert "backend/src/auth.py" in entry.content
    assert any(
        item.id == entry_id
        for item in manager.search("read auth file path", min_relevance=0.0)
    )


def test_root_path_recovery_with_empty_argument_is_approved_and_retrievable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the exact live North-Star recovery shape."""
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    trace = [
        *_attempt(
            1,
            "list-failed",
            "list_files",
            {"path": "config"},
            ok=False,
            output="Path does not exist: config",
        ),
        *_attempt(
            10,
            "list-passed",
            "list_files",
            {"path": ""},
            ok=True,
            output="dir app\nfile README.md",
        ),
        _done(20),
    ]

    entry_id = pipeline.write("Locate config/runtime.toml", trace)

    assert entry_id is not None
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.safety_status == "safe"
    assert entry.approval_status == "approved"
    assert entry.is_active is True
    assert 'list_files {"path":""}' in entry.content
    assert any(
        item.id == entry_id
        for item in manager.search("list files missing config path", min_relevance=0.0)
    )
