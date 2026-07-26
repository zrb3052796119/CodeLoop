from __future__ import annotations

from copy import deepcopy

from minicode.mcp_event_contract import normalize_mcp_runtime_payload


VALID_SUCCESS = {
    "mcpVersion": 1,
    "serverKey": "mcpsrv_" + "a" * 32,
    "transport": "stdio",
    "activity": "tool_request",
    "outcome": "request_succeeded",
    "connectionAttempted": True,
    "protocol": "newline-json",
}

VALID_FAILURE = {
    "mcpVersion": 1,
    "serverKey": "mcpsrv_" + "b" * 32,
    "transport": "stdio",
    "activity": "tool_request",
    "outcome": "request_failed",
    "connectionAttempted": False,
    "protocol": "content-length",
    "failureKind": "request_error",
}


def test_normalize_accepts_success_and_returns_new_dict() -> None:
    payload = deepcopy(VALID_SUCCESS)
    normalized = normalize_mcp_runtime_payload(payload)

    assert normalized == payload
    assert normalized is not payload
    normalized["serverKey"] = "mcpsrv_" + "c" * 32
    assert payload["serverKey"] == "mcpsrv_" + "a" * 32


def test_normalize_accepts_failure_with_failure_kind() -> None:
    payload = deepcopy(VALID_FAILURE)
    normalized = normalize_mcp_runtime_payload(payload)

    assert normalized == payload
    assert normalized is not payload


def test_normalize_rejects_boolean_version_and_other_non_int_versions() -> None:
    for version in (True, False, 1.0, "1", None, 2):
        payload = {**VALID_SUCCESS, "mcpVersion": version}
        assert normalize_mcp_runtime_payload(payload) is None


def test_normalize_rejects_success_payload_with_failure_kind() -> None:
    payload = {**VALID_SUCCESS, "failureKind": "other"}
    assert normalize_mcp_runtime_payload(payload) is None


def test_normalize_rejects_failure_payload_without_failure_kind() -> None:
    for outcome in ("connection_failed", "request_failed"):
        payload = {k: v for k, v in VALID_FAILURE.items() if k != "failureKind"}
        payload["outcome"] = outcome
        assert normalize_mcp_runtime_payload(payload) is None


def test_normalize_rejects_extra_sensitive_fields() -> None:
    for key in ("command", "args", "env", "cwd", "url", "headers", "exception", "stderr", "toolInput", "toolOutput"):
        payload = {**VALID_FAILURE, key: "Bearer secret"}
        assert normalize_mcp_runtime_payload(payload) is None


def test_normalize_rejects_invalid_connection_attempted_and_protocol() -> None:
    assert normalize_mcp_runtime_payload({**VALID_SUCCESS, "connectionAttempted": 1}) is None
    assert normalize_mcp_runtime_payload({**VALID_SUCCESS, "connectionAttempted": "true"}) is None
    assert normalize_mcp_runtime_payload({**VALID_SUCCESS, "protocol": "http"}) is None


def test_normalize_rejects_unknown_keys_and_bad_server_key() -> None:
    assert normalize_mcp_runtime_payload({**VALID_SUCCESS, "unexpected": 1}) is None
    assert normalize_mcp_runtime_payload({**VALID_SUCCESS, "serverKey": "mcpsrv_" + "A" * 32}) is None
