from __future__ import annotations

import builtins
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from minicode import memory_hot_store as hot
from minicode.memory import MemoryScope
from minicode.memory_hot_store import MemoryHotStore


@pytest.fixture(autouse=True)
def isolated_turn_budget():
    token = hot._turn_budget.set(None)
    yield
    hot._turn_budget.reset(token)


@pytest.fixture
def redis_client(monkeypatch):
    import fakeredis
    import redis

    client = fakeredis.FakeRedis(decode_responses=True)
    constructor = Mock(return_value=client)
    monkeypatch.setattr(redis.Redis, "from_url", constructor)
    return client, constructor


@pytest.fixture
def completion_waits(monkeypatch):
    """Observe production wait requests while retaining real timeout behavior."""
    waits = []

    class ObservedCompletion(threading.Event):
        def wait(self, timeout=None):
            budget = hot._turn_budget.get()
            assert budget is not None
            assert timeout is not None and 0 <= timeout <= budget.remaining
            assert timeout <= hot._WAIT_BUDGET_SECONDS
            waits.append(timeout)
            return super().wait(timeout)

    monkeypatch.setattr(hot, "threading", SimpleNamespace(
        Event=ObservedCompletion, Thread=threading.Thread, Lock=threading.Lock,
    ))
    return waits


def test_disabled_store_never_imports_redis_or_starts_a_thread(monkeypatch):
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        assert name != "redis" and not name.startswith("redis.")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    monkeypatch.setattr(threading.Thread, "start", Mock(side_effect=AssertionError))
    store = MemoryHotStore(None)
    assert not store.available
    assert not store.put_draft("project", "p", {"id": "one"}, 60)
    assert store.load_active_drafts("project", "p") == []
    assert store.get_adjudication("key") is None
    assert store.put_adjudication("key", {"result": []}) is None
    assert not store.acquire_lease("curator", 60)


def test_missing_optional_dependency_fuses_once(monkeypatch, caplog):
    real_import = builtins.__import__
    imports = []

    def missing(name, *args, **kwargs):
        if name == "redis":
            imports.append(name)
            raise ImportError("optional dependency absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    store = MemoryHotStore("redis://localhost/0")
    assert store.available
    assert store.get_adjudication("key") is None
    assert not store.available
    assert store.get_adjudication("key") is None
    assert imports == ["redis"]
    assert sum("hot store disabled" in record.message for record in caplog.records) == 1


def test_first_use_pings_once_and_disables_redis_retries(redis_client, monkeypatch):
    client, constructor = redis_client
    ping = Mock(wraps=client.ping)
    monkeypatch.setattr(client, "ping", ping)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="project")
    assert store.available
    constructor.assert_not_called()
    store.put_adjudication("key", {"ids": ["one"]})
    assert store.get_adjudication("key") == {"ids": ["one"]}
    ping.assert_called_once_with()
    constructor.assert_called_once()
    options = constructor.call_args.kwargs
    assert options["socket_connect_timeout"] == 0.2
    assert options["socket_timeout"] == 0.05
    assert options["retry_on_timeout"] is False
    assert options["retry_on_error"] == []
    assert options["retry"].get_retries() == 0


def test_url_cannot_override_timeouts_or_enable_retries(redis_client):
    _, constructor = redis_client
    store = MemoryHotStore(
        "redis://localhost/0?socket_timeout=9&socket_connect_timeout=9"
        "&retry_on_timeout=True&retry=bad&retry_on_error=TimeoutError&db=2"
    )
    store.get_adjudication("key")
    assert store.available
    assert constructor.call_args.args[0] == "redis://localhost/0?db=2"


def test_draft_roundtrip_ttl_and_namespace_isolation(redis_client):
    client, _ = redis_client
    store = MemoryHotStore("redis://localhost/0")
    entry = {"id": "draft-one", "content": "保留完整内容", "metadata": {"a": 1}}
    assert store.put_draft("project", "first", entry, 60)
    assert store.put_draft("global", "first", {"id": "global"}, 60)
    assert store.put_draft("project", "second", {"id": "other"}, 60)
    assert store.load_active_drafts("project", "first") == [entry]
    key = "minicode:mem:first:draft:project:draft-one"
    assert 0 < client.ttl(key) <= 60
    client.pexpire(key, 1)
    time.sleep(0.01)
    assert store.load_active_drafts("project", "first") == []
    assert store.load_active_drafts("global", "first") == [{"id": "global"}]
    assert store.load_active_drafts("project", "second") == [{"id": "other"}]


def test_draft_scope_and_id_cannot_expand_scan_pattern(redis_client):
    store = MemoryHotStore("redis://localhost/0")
    for scope, entry_id in [("*", "one:*"), ("project", "two"), ("[a]", "three")]:
        assert store.put_draft(scope, "p", {"id": entry_id}, 60)
    assert store.load_active_drafts("*", "p") == [{"id": "one:*"}]
    assert store.load_active_drafts("[a]", "p") == [{"id": "three"}]


def test_enum_and_string_draft_scopes_are_identical(redis_client):
    client, _ = redis_client
    store = MemoryHotStore("redis://localhost/0")
    assert store.put_draft(MemoryScope.PROJECT, "p", {"id": "one"}, 60)
    assert client.exists("minicode:mem:p:draft:project:one")
    assert store.load_active_drafts("project", "p") == [{"id": "one"}]
    assert store.put_draft("project", "p", {"id": "two"}, 60)
    assert store.load_active_drafts(MemoryScope.PROJECT, "p") == [
        {"id": "one"}, {"id": "two"}
    ]


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({}, id="missing-id"),
        pytest.param({"id": ""}, id="empty-id"),
        pytest.param({"id": None}, id="null-id"),
        pytest.param({"id": 7}, id="numeric-id"),
        pytest.param([], id="non-object"),
        pytest.param({"id": "bad", "content": object()}, id="unserializable"),
        pytest.param({"id": "bad", "score": float("nan")}, id="nan"),
        pytest.param({"id": "bad", "score": float("inf")}, id="infinity"),
    ],
)
def test_invalid_draft_is_skipped_without_connecting_or_fusing(
    redis_client, monkeypatch, caplog, entry
):
    client, constructor = redis_client
    commands = Mock(wraps=client.execute_command)
    monkeypatch.setattr(client, "execute_command", commands)
    store = MemoryHotStore("redis://localhost/0")

    assert not store.put_draft("project", "p", entry, 60)
    assert store.available
    constructor.assert_not_called()
    commands.assert_not_called()
    assert caplog.records

    valid = {"id": "valid", "content": "Still usable"}
    assert store.put_draft("project", "p", valid, 60)
    assert store.load_active_drafts("project", "p") == [valid]
    assert store.available


@pytest.mark.parametrize("ttl", [0, -1, "60", True, 1.5, None])
@pytest.mark.parametrize("operation", ["draft", "adjudication", "lease"])
def test_invalid_ttl_is_skipped_without_connecting_or_fusing(
    redis_client, monkeypatch, caplog, ttl, operation
):
    client, constructor = redis_client
    commands = Mock(wraps=client.execute_command)
    monkeypatch.setattr(client, "execute_command", commands)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="p")

    if operation == "draft":
        assert not store.put_draft("project", "p", {"id": "draft"}, ttl)
    elif operation == "adjudication":
        assert store.put_adjudication("key", {"ids": ["one"]}, ttl) is None
    else:
        assert not store.acquire_lease("curator", ttl)
    assert store.available
    constructor.assert_not_called()
    commands.assert_not_called()
    assert caplog.records

    if operation == "draft":
        assert store.put_draft("project", "p", {"id": "draft"}, 60)
        assert store.load_active_drafts("project", "p") == [{"id": "draft"}]
    elif operation == "adjudication":
        store.put_adjudication("key", {"ids": ["one"]}, 60)
        assert store.get_adjudication("key") == {"ids": ["one"]}
    else:
        assert store.acquire_lease("curator", 60)
    assert store.available


@pytest.mark.parametrize("key", ["", None, 7])
@pytest.mark.parametrize("operation", ["get_adjudication", "put_adjudication", "acquire_lease"])
def test_invalid_key_is_skipped_without_connecting_or_fusing(
    redis_client, monkeypatch, caplog, key, operation
):
    client, constructor = redis_client
    commands = Mock(wraps=client.execute_command)
    monkeypatch.setattr(client, "execute_command", commands)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="p")

    if operation == "get_adjudication":
        assert store.get_adjudication(key) is None
    elif operation == "put_adjudication":
        assert store.put_adjudication(key, {"ids": ["one"]}) is None
    else:
        assert not store.acquire_lease(key, 60)
    assert store.available
    constructor.assert_not_called()
    commands.assert_not_called()
    assert caplog.records

    if operation == "acquire_lease":
        assert store.acquire_lease("curator", 60)
    else:
        store.put_adjudication("valid", {"ids": ["one"]})
        assert store.get_adjudication("valid") == {"ids": ["one"]}
    assert store.available


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([], id="non-object"),
        pytest.param({"content": object()}, id="unserializable"),
        pytest.param({"score": float("nan")}, id="nan"),
        pytest.param({"score": float("inf")}, id="infinity"),
    ],
)
def test_invalid_adjudication_value_is_skipped_without_connecting_or_fusing(
    redis_client, monkeypatch, caplog, value
):
    client, constructor = redis_client
    commands = Mock(wraps=client.execute_command)
    monkeypatch.setattr(client, "execute_command", commands)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="p")

    assert store.put_adjudication("key", value) is None
    assert store.available
    constructor.assert_not_called()
    commands.assert_not_called()
    assert caplog.records

    store.put_adjudication("key", {"ids": ["valid"]})
    assert store.get_adjudication("key") == {"ids": ["valid"]}
    assert store.available


def test_invalid_input_logs_do_not_include_credentials_or_payload(redis_client, caplog):
    _, constructor = redis_client
    store = MemoryHotStore("redis://:password-must-not-be-logged@localhost/0")
    assert not store.put_draft(
        "project", "p", {"id": "", "content": "payload-must-not-be-logged"}, 60
    )
    assert store.available
    constructor.assert_not_called()
    assert caplog.records
    assert "password-must-not-be-logged" not in caplog.text
    assert "payload-must-not-be-logged" not in caplog.text


def test_adjudication_keys_are_project_scoped_and_expire(redis_client):
    client, _ = redis_client
    first = MemoryHotStore("redis://localhost/0", project_fingerprint="first")
    second = MemoryHotStore("redis://localhost/0", project_fingerprint="second")
    first.put_adjudication("same-query", {"ids": ["first"]})
    assert second.get_adjudication("same-query") is None
    second.put_adjudication("same-query", {"ids": ["second"]}, ttl_seconds=12)
    assert first.get_adjudication("same-query") == {"ids": ["first"]}
    assert second.get_adjudication("same-query") == {"ids": ["second"]}
    assert 0 < client.ttl("minicode:mem:first:adj:same-query") <= 3600
    assert 0 < client.ttl("minicode:mem:second:adj:same-query") <= 12


def test_lease_uses_project_namespace_nx_and_millisecond_ttl(redis_client):
    client, _ = redis_client
    first = MemoryHotStore("redis://localhost/0", project_fingerprint="first")
    second = MemoryHotStore("redis://localhost/0", project_fingerprint="second")
    assert first.acquire_lease("curator", 2)
    assert not first.acquire_lease("curator", 2)
    assert second.acquire_lease("curator", 2)
    assert 0 < client.pttl("minicode:mem:first:lease:curator") <= 2000


@pytest.mark.parametrize("failure_method", ["ping", "set", "scan", "get"])
def test_redis_failure_fuses_all_future_methods(redis_client, monkeypatch, caplog, failure_method):
    client, _ = redis_client
    failure = Mock(side_effect=RuntimeError("redis://password-must-not-be-logged"))
    monkeypatch.setattr(client, failure_method, failure)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="p")
    if failure_method == "set":
        assert not store.put_draft("project", "p", {"id": "x"}, 60)
    elif failure_method == "scan":
        assert store.load_active_drafts("project", "p") == []
    else:
        assert store.get_adjudication("key") is None
    assert not store.available
    assert failure.call_count == 1
    for name in ("ping", "set", "scan", "get", "mget"):
        monkeypatch.setattr(client, name, Mock(side_effect=AssertionError("must not call")))
    assert not store.put_draft("project", "p", {"id": "x"}, 60)
    assert store.load_active_drafts("project", "p") == []
    assert store.get_adjudication("key") is None
    assert store.put_adjudication("key", {}) is None
    assert not store.acquire_lease("curator", 60)
    for name in ("ping", "set", "scan", "get", "mget"):
        getattr(client, name).assert_not_called()
    assert sum("hot store disabled" in record.message for record in caplog.records) == 1
    assert "password-must-not-be-logged" not in caplog.text


@pytest.mark.parametrize("raw", ["{invalid", "[]", "null", '{"score":NaN}', '{"score":Infinity}'])
def test_malformed_cached_json_is_a_miss_without_fusing(redis_client, raw):
    client, _ = redis_client
    client.set("minicode:mem:p:adj:key", raw)
    store = MemoryHotStore("redis://localhost/0", project_fingerprint="p")
    assert store.get_adjudication("key") is None
    assert store.available
    store.put_adjudication("key", {"ids": ["valid"]})
    assert store.get_adjudication("key") == {"ids": ["valid"]}
    assert store.available


def test_malformed_draft_rows_do_not_hide_valid_siblings_or_fuse(redis_client):
    client, _ = redis_client
    first = {"id": "first", "content": "First valid note"}
    last = {"id": "last", "content": "Last valid note"}
    rows = [json.dumps(first), "{invalid", "[]", "null", '{"id":"bad","score":NaN}', json.dumps(last)]
    for index, raw in enumerate(rows):
        client.set(f"minicode:mem:p:draft:project:{index}", raw)
    store = MemoryHotStore("redis://localhost/0")

    assert store.load_active_drafts("project", "p") == [first, last]
    assert store.available
    assert store.put_draft("project", "p", {"id": "next"}, 60)
    assert store.load_active_drafts("project", "p") == [first, last, {"id": "next"}]
    assert store.available


def test_connection_wait_is_bounded_and_late_ping_cannot_issue_command(monkeypatch, completion_waits):
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    client = Mock()
    store = MemoryHotStore("redis://localhost/0")

    def connect(_deadline):
        entered.set()
        assert release.wait(2), "test did not release the blocked worker"
        finished.set()
        return client

    connecting = Mock(side_effect=connect)
    monkeypatch.setattr(store, "_connect", connecting)
    try:
        assert store.get_adjudication("key") is None
        assert len(completion_waits) == 1
        assert entered.is_set() and not finished.is_set()
        assert not release.is_set()
        assert not store.available
        assert store.get_adjudication("again") is None
        connecting.assert_called_once()
        client.get.assert_not_called()
    finally:
        release.set()
        assert store._operation_lock.acquire(timeout=1)
        store._operation_lock.release()
    assert finished.is_set()
    client.get.assert_not_called()


def test_late_client_construction_never_pings_after_timeout(redis_client, monkeypatch, completion_waits):
    client, constructor = redis_client
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    ping = Mock(wraps=client.ping)
    monkeypatch.setattr(client, "ping", ping)

    def construct(*_args, **_kwargs):
        entered.set()
        assert release.wait(2), "test did not release the blocked worker"
        finished.set()
        return client

    constructor.side_effect = construct
    store = MemoryHotStore("redis://localhost/0")
    try:
        assert store.get_adjudication("key") is None
        assert len(completion_waits) == 1
        assert entered.is_set() and not finished.is_set()
        assert not release.is_set()
        assert not store.available
        assert store.get_adjudication("again") is None
        constructor.assert_called_once()
    finally:
        release.set()
        assert store._operation_lock.acquire(timeout=1)
        store._operation_lock.release()
    assert finished.is_set()
    ping.assert_not_called()


def test_late_redis_import_never_constructs_client_after_timeout(redis_client, monkeypatch):
    _, constructor = redis_client
    release = threading.Event()
    real_import = builtins.__import__

    def delayed_import(name, *args, **kwargs):
        if name == "redis":
            release.wait(2)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", delayed_import)
    store = MemoryHotStore("redis://localhost/0")
    try:
        assert store.get_adjudication("key") is None
        assert not store.available
    finally:
        release.set()
    assert store._operation_lock.acquire(timeout=1)
    store._operation_lock.release()
    constructor.assert_not_called()


def test_nested_budget_caps_cumulative_wait_across_multiple_commands(monkeypatch):
    # Controlled operation time isolates budget arithmetic from OS wakeup jitter.
    # Workers, their locks, and the blocked third operation remain real.
    now = [0.0]
    calls = []
    waits = []
    completions = []
    third_entered = threading.Event()
    release_third = threading.Event()

    class ControlledCompletion(threading.Event):
        def __init__(self):
            super().__init__()
            completions.append(self)
            self.ordinal = len(completions)

        def wait(self, timeout=None):
            budget = hot._turn_budget.get()
            assert budget is not None
            assert timeout is not None and 0 <= timeout <= budget.remaining
            waits.append(timeout)
            if self.ordinal == 3:
                assert third_entered.wait(1), "third operation did not start"
                assert not self.is_set()
                assert timeout == pytest.approx(0.040)
                now[0] += timeout
                return False
            # A liveness watchdog only; the requested timeout and virtual
            # operation time, not this watchdog, determine the budget assertion.
            return super().wait(1)

    monkeypatch.setattr(hot, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(hot, "threading", SimpleNamespace(
        Event=ControlledCompletion, Thread=threading.Thread, Lock=threading.Lock,
    ))
    store = MemoryHotStore("redis://localhost/0")

    def get(key):
        calls.append(key)
        if len(calls) <= 2:
            now[0] += 0.075
        else:
            third_entered.set()
            assert release_third.wait(2), "test did not release the late result"
        return json.dumps({"ok": True})

    monkeypatch.setattr(store, "_connect", lambda _deadline: Mock(get=get))
    try:
        with store.budget():
            shared = hot._turn_budget.get()
            assert shared.remaining == pytest.approx(0.190)
            assert store.get_adjudication("one") == {"ok": True}
            assert shared.remaining == pytest.approx(0.115)
            with store.budget():
                assert hot._turn_budget.get() is shared
                assert store.get_adjudication("two") == {"ok": True}
                assert shared.remaining == pytest.approx(0.040)
            assert hot._turn_budget.get() is shared
            assert store.get_adjudication("three") is None
            assert shared.remaining == pytest.approx(0.0)
            assert not store.available
            assert store.get_adjudication("four") is None
            assert len(completions) == len(calls) == 3
        assert len(waits) == 3
        assert now[0] == pytest.approx(0.190)
    finally:
        release_third.set()
        assert store._operation_lock.acquire(timeout=1)
        store._operation_lock.release()
    assert len(calls) == 3 and not store.available


def test_begin_turn_keeps_wait_allowance_across_contexts_but_not_llm_time(monkeypatch):
    store = MemoryHotStore("redis://localhost/0")
    monkeypatch.setattr(store, "_connect", lambda _deadline: Mock(get=lambda _: '{"ok":true}'))
    store.begin_turn()
    with store.budget():
        assert store.get_adjudication("one") == {"ok": True}
    before = hot._turn_budget.get().remaining
    time.sleep(0.01)
    with store.budget():
        assert hot._turn_budget.get().remaining == before
        assert store.get_adjudication("two") == {"ok": True}
    assert 0 < hot._turn_budget.get().remaining <= before
    store.begin_turn()
    assert hot._turn_budget.get().remaining == hot._WAIT_BUDGET_SECONDS


def test_factory_uses_resolved_path_identity_and_shares_fuse(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_CODE_MEMORY_REDIS_URL", "redis://localhost/0")
    first = hot.get_memory_hot_store(tmp_path / "project")
    same = hot.get_memory_hot_store(tmp_path / "project" / ".." / "project")
    other = hot.get_memory_hot_store(tmp_path / "other")
    assert first is same
    assert first is not other
    assert first._project_fingerprint != other._project_fingerprint
    first._break(RuntimeError())
    assert not same.available
    assert other.available
    assert hot.project_fingerprint(tmp_path / "project") == hot.project_fingerprint(
        Path(str(tmp_path / "project"))
    )


@pytest.mark.parametrize("value,expected", [(None, 1209600), ("10", 10), ("0", 1209600), ("-1", 1209600), ("bad", 1209600)])
def test_draft_ttl_env_default_and_validation(monkeypatch, value, expected):
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_DRAFT_TTL", raising=False)
    if value is not None:
        monkeypatch.setenv("MINI_CODE_MEMORY_REDIS_DRAFT_TTL", value)
    assert hot.redis_draft_ttl_seconds() == expected
