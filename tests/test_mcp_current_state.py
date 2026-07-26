from __future__ import annotations

import copy
from collections.abc import Collection, Iterator
from datetime import datetime, timezone
import threading

import pytest

from minicode.mcp_current_state import (
    McpCurrentStateRegistry,
    normalize_mcp_current_state_snapshot,
)
from minicode.mcp_observation import mcp_server_key


SERVER_KEY = "mcpsrv_0123456789abcdef0123456789abcdef"


def _fixed_clock() -> datetime:
    return datetime(2026, 7, 18, 1, 2, 3, tzinfo=timezone.utc)


def _token_factory() -> callable:
    lock = threading.Lock()
    value = 0

    def next_token() -> str:
        nonlocal value
        with lock:
            value += 1
            return f"instance-{value:04d}"

    return next_token


def _valid_snapshot_payload() -> dict[str, object]:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-a",
    )
    handle = registry.register(SERVER_KEY, probe=lambda: True)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True
    return registry.snapshot().to_dict()


def test_registry_projects_idle_starting_ready_then_unregisters() -> None:
    clock_values = iter(
        datetime(2026, 7, 18, 1, minute, tzinfo=timezone.utc)
        for minute in range(8)
    )
    registry = McpCurrentStateRegistry(
        clock=lambda: next(clock_values),
        token_factory=lambda: "instance-a",
    )

    handle = registry.register(SERVER_KEY, probe=lambda: True)
    assert handle is not None
    assert registry.snapshot().to_dict()["servers"] == [
        {
            "serverKey": SERVER_KEY,
            "state": "idle",
            "activeInstanceCount": 1,
            "protocol": None,
            "failureKind": None,
            "updatedAt": "2026-07-18T01:00:00Z",
        }
    ]

    assert registry.mark_starting(handle) is True
    assert registry.snapshot().servers[0].state == "starting"

    assert registry.mark_ready(handle, protocol="newline-json") is True
    assert registry.snapshot().servers[0].state == "ready"

    assert registry.unregister(handle) is True
    assert registry.snapshot().servers == ()


def test_snapshot_contract_normalizes_only_closed_safe_schema() -> None:
    payload = _valid_snapshot_payload()

    assert normalize_mcp_current_state_snapshot(payload) == payload

    mutations = []
    for key, value in (
        ("schemaVersion", True),
        ("stateVersion", 2),
        ("scope", "global"),
        ("current", "heartbeat"),
        ("checkedAt", "not-a-time"),
    ):
        changed = copy.deepcopy(payload)
        changed[key] = value
        mutations.append(changed)
    extra = copy.deepcopy(payload)
    extra["serverName"] = "secret-server"
    mutations.append(extra)
    bad_coverage = copy.deepcopy(payload)
    bad_coverage["coverage"]["heartbeat"] = 0
    mutations.append(bad_coverage)
    bad_server = copy.deepcopy(payload)
    bad_server["servers"][0]["pid"] = 1234
    mutations.append(bad_server)
    bad_diagnostic = copy.deepcopy(payload)
    bad_diagnostic["coverage"]["limited"] = True
    bad_diagnostic["diagnostics"] = [{"code": ["probe_failed"], "count": 1}]
    mutations.append(bad_diagnostic)
    inconsistent_coverage = copy.deepcopy(payload)
    inconsistent_coverage["coverage"]["limited"] = True
    mutations.append(inconsistent_coverage)

    for changed in mutations:
        assert normalize_mcp_current_state_snapshot(changed) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("serverKey", "server-name"),
        ("state", "closed"),
        ("state", ["ready"]),
        ("activeInstanceCount", True),
        ("protocol", "http"),
        ("protocol", ["newline-json"]),
        ("failureKind", "secret failure text"),
        ("failureKind", {"kind": "timeout"}),
        ("updatedAt", "/Users/person/project"),
    ],
)
def test_snapshot_contract_rejects_invalid_server_values(
    field: str,
    value: object,
) -> None:
    payload = _valid_snapshot_payload()
    payload["servers"][0][field] = value

    assert normalize_mcp_current_state_snapshot(payload) is None


def test_failed_transition_and_dead_ready_probe_fail_closed() -> None:
    alive = True
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=_token_factory(),
    )
    failed = registry.register(SERVER_KEY, probe=lambda: False)
    ready = registry.register(SERVER_KEY, probe=lambda: alive)
    assert failed is not None and ready is not None
    assert registry.mark_failed(failed, failure_kind="command_not_found") is True
    assert registry.mark_ready(ready, protocol="content-length") is True

    first = registry.snapshot().servers[0]
    assert first.state == "ready"
    assert first.active_instance_count == 2

    alive = False
    second = registry.snapshot().servers[0]
    assert second.state == "failed"
    assert second.failure_kind == "process_exit"


def test_same_server_instances_unregister_independently_and_workspaces_isolate() -> None:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=_token_factory(),
    )
    key_a = mcp_server_key("/workspace/a", "shared")
    key_b = mcp_server_key("/workspace/b", "shared")
    first = registry.register(key_a, probe=lambda: True)
    second = registry.register(key_a, probe=lambda: True)
    other = registry.register(key_b, probe=lambda: True)
    assert first is not None and second is not None and other is not None
    assert registry.mark_ready(first, protocol="newline-json") is True
    assert registry.mark_starting(second) is True

    snapshot = registry.snapshot()
    assert [server.server_key for server in snapshot.servers] == sorted([key_a, key_b])
    by_key = {server.server_key: server for server in snapshot.servers}
    assert by_key[key_a].active_instance_count == 2
    assert by_key[key_a].state == "ready"
    assert by_key[key_b].state == "idle"

    assert registry.unregister(first) is True
    remaining = {server.server_key: server for server in registry.snapshot().servers}
    assert remaining[key_a].active_instance_count == 1
    assert remaining[key_a].state == "starting"


def test_handles_cannot_cross_registries_or_mutate_reused_tokens() -> None:
    first_registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "same-token",
    )
    second_registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "same-token",
    )
    stale = first_registry.register(SERVER_KEY, probe=lambda: True)
    other = second_registry.register(SERVER_KEY, probe=lambda: True)
    assert stale is not None and other is not None

    assert second_registry.mark_starting(stale) is False
    assert second_registry.snapshot().servers[0].state == "idle"
    assert first_registry.unregister(stale) is True
    replacement = first_registry.register(SERVER_KEY, probe=lambda: True)
    assert replacement is not None
    assert first_registry.mark_ready(stale, protocol="newline-json") is False
    assert first_registry.snapshot().servers[0].state == "idle"


def test_aggregate_precedence_and_tie_break_are_deterministic() -> None:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=iter(("token-a", "token-b", "token-c")).__next__,
    )
    idle = registry.register(SERVER_KEY, probe=lambda: True)
    failed = registry.register(SERVER_KEY, probe=lambda: True)
    starting = registry.register(SERVER_KEY, probe=lambda: True)
    assert idle is not None and failed is not None and starting is not None
    assert registry.mark_failed(failed, failure_kind="timeout") is True
    assert registry.mark_starting(starting) is True

    snapshot = registry.snapshot().servers[0]
    assert snapshot.state == "starting"
    assert snapshot.protocol is None
    assert snapshot.failure_kind is None

    tie_registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=iter(("tie-a", "tie-b")).__next__,
    )
    tie_a = tie_registry.register(SERVER_KEY, probe=lambda: True)
    tie_b = tie_registry.register(SERVER_KEY, probe=lambda: True)
    assert tie_a is not None and tie_b is not None
    tie_registry.mark_failed(
        tie_a,
        failure_kind="timeout",
        protocol="content-length",
    )
    tie_registry.mark_failed(
        tie_b,
        failure_kind="other",
        protocol="newline-json",
    )
    tied = tie_registry.snapshot().servers[0]
    assert tied.failure_kind == "other"
    assert tied.protocol == "newline-json"


def test_instance_response_and_diagnostic_budgets_are_hard_and_bounded() -> None:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=_token_factory(),
        max_instances=2,
        max_response_servers=1,
        max_diagnostics=2,
    )
    first = registry.register(SERVER_KEY, probe=lambda: True)
    second = registry.register(
        "mcpsrv_abcdefabcdefabcdefabcdefabcdefab",
        probe=lambda: True,
    )
    rejected = registry.register(
        "mcpsrv_ffffffffffffffffffffffffffffffff",
        probe=lambda: True,
    )
    assert first is not None and second is not None
    assert rejected is None

    snapshot = registry.snapshot()
    assert len(snapshot.servers) == 1
    assert len(snapshot.diagnostics) <= 2
    assert snapshot.coverage.limited is True
    assert {item.code for item in snapshot.diagnostics} <= {
        "instance_budget_exceeded",
        "response_budget_exceeded",
        "diagnostic_budget_exceeded",
    }


def test_snapshot_is_immutable_and_json_projection_is_a_deep_copy() -> None:
    payload = _valid_snapshot_payload()
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-a",
    )
    handle = registry.register(SERVER_KEY, probe=lambda: True)
    assert handle is not None
    snapshot = registry.snapshot()

    with pytest.raises((AttributeError, TypeError)):
        snapshot.servers += ()

    projected = snapshot.to_dict()
    projected["servers"].clear()
    projected["coverage"]["limited"] = True
    assert len(snapshot.servers) == 1
    assert snapshot.coverage.limited is False
    assert payload["servers"]


def test_probe_baseexception_is_isolated_and_secret_text_is_not_projected() -> None:
    secret = "SECRET-command-env-stderr-path"
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "opaque-token-secret",
    )

    def broken_probe() -> bool:
        raise KeyboardInterrupt(secret)

    handle = registry.register(SERVER_KEY, probe=broken_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True

    rendered = repr(registry.snapshot().to_dict())
    assert "failed" in rendered
    assert "probe_failed" in rendered
    assert secret not in rendered
    assert "opaque-token-secret" not in rendered


def test_clock_and_token_factory_faults_are_fixed_and_content_free() -> None:
    def broken_clock() -> datetime:
        raise SystemExit("clock-secret")

    def broken_token() -> str:
        raise KeyboardInterrupt("token-secret")

    clock_registry = McpCurrentStateRegistry(
        clock=broken_clock,
        token_factory=lambda: "instance-a",
    )
    handle = clock_registry.register(SERVER_KEY, probe=lambda: True)
    assert handle is not None
    clock_rendered = repr(clock_registry.snapshot().to_dict())
    assert "clock_unavailable" in clock_rendered
    assert "clock-secret" not in clock_rendered

    token_registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=broken_token,
    )
    assert token_registry.register(SERVER_KEY, probe=lambda: True) is None
    token_rendered = repr(token_registry.snapshot().to_dict())
    assert "token_factory_unavailable" in token_rendered
    assert "token-secret" not in token_rendered


def test_ready_probe_runs_outside_lock_and_cannot_overwrite_newer_transition() -> None:
    entered = threading.Event()
    release = threading.Event()
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-a",
    )

    def blocking_probe() -> bool:
        entered.set()
        assert release.wait(timeout=5)
        return False

    handle = registry.register(SERVER_KEY, probe=blocking_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True
    snapshots = []
    thread = threading.Thread(target=lambda: snapshots.append(registry.snapshot()))
    thread.start()
    assert entered.wait(timeout=5)

    assert registry.mark_starting(handle) is True
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert snapshots[0].servers[0].state == "starting"


def test_concurrent_register_transition_snapshot_unregister_has_no_races() -> None:
    registry = McpCurrentStateRegistry(
        token_factory=_token_factory(),
        max_instances=128,
    )
    barrier = threading.Barrier(9)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(50):
                handle = registry.register(SERVER_KEY, probe=lambda: True)
                if handle is None:
                    continue
                registry.mark_starting(handle)
                registry.mark_ready(
                    handle,
                    protocol="content-length" if index % 2 else "newline-json",
                )
                registry.snapshot()
                registry.unregister(handle)
        except BaseException as exc:  # captured only for the stress assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert registry.snapshot().servers == ()


def test_registry_does_not_persist_or_modify_runtime_files(tmp_path) -> None:
    protected = []
    for name in ("config.json", "run-journal.json", "session.json", "memory.json"):
        path = tmp_path / name
        path.write_text(f"secret-{name}", encoding="utf-8")
        protected.append(
            (path, path.read_bytes(), path.stat().st_mtime_ns)
        )
    before_names = sorted(path.name for path in tmp_path.iterdir())
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "internal-token",
    )
    handle = registry.register(SERVER_KEY, probe=lambda: True)
    assert handle is not None
    registry.mark_starting(handle)
    registry.mark_ready(handle, protocol="newline-json")
    registry.snapshot()
    registry.unregister(handle)

    assert sorted(path.name for path in tmp_path.iterdir()) == before_names
    for path, content, mtime_ns in protected:
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns


def test_scoped_snapshot_does_not_probe_unmatched_workspace_instances() -> None:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-other-workspace",
    )
    visible_key = mcp_server_key("/workspace/visible", "shared")
    unmatched_key = mcp_server_key("/workspace/unmatched", "shared")
    probe_calls = 0

    def unmatched_probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    handle = registry.register(unmatched_key, probe=unmatched_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True

    scoped = registry.snapshot_for(frozenset({visible_key}))

    assert probe_calls == 0
    assert scoped.servers == ()
    assert scoped.diagnostics == ()
    assert scoped.coverage.limited is False


def test_unattributable_global_diagnostics_are_not_returned_by_scoped_snapshot() -> None:
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-unmatched",
    )
    selected_key = mcp_server_key("/workspace/selected", "configured")
    unmatched_key = mcp_server_key("/workspace/unmatched", "configured")

    assert registry.register("not-a-server-key", probe=lambda: True) is None
    assert registry.mark_starting(object()) is False

    def broken_probe() -> bool:
        raise RuntimeError("Authorization=unmatched-secret")

    handle = registry.register(unmatched_key, probe=broken_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True
    global_snapshot = registry.snapshot()
    assert {item.code for item in global_snapshot.diagnostics} == {
        "invalid_registration",
        "invalid_transition",
        "probe_failed",
    }

    scoped = registry.snapshot_for(frozenset({selected_key}))

    assert scoped.servers == ()
    assert scoped.diagnostics == ()
    assert scoped.coverage.limited is False


def test_selected_probe_failure_remains_visible_and_safe() -> None:
    secret = "Bearer selected-probe-secret"
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-selected",
    )
    selected_key = mcp_server_key("/workspace/selected", "configured")

    def broken_probe() -> bool:
        raise RuntimeError(secret)

    handle = registry.register(selected_key, probe=broken_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True

    scoped = registry.snapshot_for(frozenset({selected_key}))

    assert scoped.servers[0].state == "failed"
    assert scoped.servers[0].failure_kind == "other"
    assert scoped.diagnostics[0].to_dict() == {"code": "probe_failed", "count": 1}
    assert scoped.coverage.limited is True
    assert secret not in repr(scoped.to_dict())


def test_empty_allowlist_is_exact_even_when_registry_contains_other_workspaces() -> None:
    probe_calls = 0
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=lambda: "instance-other",
    )

    def other_probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        raise RuntimeError("other-workspace-secret")

    handle = registry.register(
        mcp_server_key("/workspace/other", "configured"),
        probe=other_probe,
    )
    assert handle is not None
    assert registry.mark_ready(handle, protocol="content-length") is True
    assert registry.register("invalid-key", probe=lambda: True) is None

    scoped = registry.snapshot_for(frozenset())

    assert probe_calls == 0
    assert scoped.servers == ()
    assert scoped.diagnostics == ()
    assert scoped.coverage.limited is False
    assert normalize_mcp_current_state_snapshot(scoped.to_dict()) == scoped.to_dict()


def test_scoped_snapshot_input_is_validated_before_bounded_consumption() -> None:
    registry = McpCurrentStateRegistry(clock=_fixed_clock)

    class OversizedKeys(Collection[str]):
        def __contains__(self, value: object) -> bool:
            del value
            return False

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("oversized key collection must not be consumed")

        def __len__(self) -> int:
            return 2_001

    with pytest.raises(ValueError, match="input budget"):
        registry.snapshot_for(OversizedKeys())
    with pytest.raises(TypeError, match="bounded collection"):
        registry.snapshot_for(iter((SERVER_KEY,)))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid server key"):
        registry.snapshot_for(frozenset({"server-name"}))


def test_scoped_snapshot_selected_key_budget_is_deterministic() -> None:
    keys = sorted(
        [
            mcp_server_key("/workspace/selected", "alpha"),
            mcp_server_key("/workspace/selected", "bravo"),
        ]
    )
    registry = McpCurrentStateRegistry(
        clock=_fixed_clock,
        token_factory=_token_factory(),
        max_response_servers=1,
    )
    handles = [registry.register(key, probe=lambda: True) for key in keys]
    assert all(handle is not None for handle in handles)
    for handle in handles:
        assert registry.mark_ready(handle, protocol="newline-json") is True

    scoped = registry.snapshot_for(frozenset(reversed(keys)))

    assert [server.server_key for server in scoped.servers] == keys[:1]
    assert scoped.coverage.limited is True
    assert [item.to_dict() for item in scoped.diagnostics] == [
        {"code": "response_budget_exceeded", "count": 1}
    ]


def test_scoped_snapshot_concurrent_selected_and_unmatched_lifecycles_are_isolated() -> None:
    registry = McpCurrentStateRegistry(
        token_factory=_token_factory(),
        max_instances=128,
    )
    selected_key = mcp_server_key("/workspace/selected", "shared")
    unmatched_key = mcp_server_key("/workspace/unmatched", "shared")
    barrier = threading.Barrier(7)
    errors: list[BaseException] = []
    unmatched_probe_calls = 0
    probe_lock = threading.Lock()

    def selected_worker() -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(40):
                handle = registry.register(selected_key, probe=lambda: True)
                if handle is None:
                    continue
                registry.mark_starting(handle)
                registry.mark_ready(handle, protocol="newline-json")
                registry.snapshot_for(frozenset({selected_key}))
                registry.unregister(handle)
        except BaseException as exc:
            errors.append(exc)

    def unmatched_worker() -> None:
        def unmatched_probe() -> bool:
            nonlocal unmatched_probe_calls
            with probe_lock:
                unmatched_probe_calls += 1
            return True

        try:
            barrier.wait(timeout=5)
            for _ in range(40):
                handle = registry.register(unmatched_key, probe=unmatched_probe)
                if handle is None:
                    continue
                registry.mark_starting(handle)
                registry.mark_ready(handle, protocol="content-length")
                registry.snapshot_for(frozenset({selected_key}))
                registry.unregister(handle)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=selected_worker) for _ in range(3)] + [
        threading.Thread(target=unmatched_worker) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert unmatched_probe_calls == 0
    assert registry.snapshot_for(frozenset({selected_key})).servers == ()
    assert registry.snapshot_for(frozenset({unmatched_key})).servers == ()
