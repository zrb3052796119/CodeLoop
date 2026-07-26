from __future__ import annotations

import socket
import subprocess
import sys
import threading
import textwrap
import time
from pathlib import Path

import pytest

import minicode.tools.network_safety as network_safety
from minicode.tools.bounded_resolver import BoundedResolver, ResolverError


def _wait_until(
    predicate,
    *,
    timeout: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_consecutive_dns_timeouts_keep_a_fixed_resolver_thread_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    counter_lock = threading.Lock()
    entered = 0
    before_ids = {
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    }

    def blocking_getaddrinfo(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal entered
        with counter_lock:
            entered += 1
        release.wait()
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", blocking_getaddrinfo)
    error_codes: list[str] = []
    try:
        for index in range(25):
            with pytest.raises(network_safety.NetworkSafetyError) as failure:
                network_safety.validate_destination(
                    f"https://blocked-{index}.public.example/",
                    deadline=time.monotonic() + 0.01,
                )
            error_codes.append(failure.value.code)

        retained = [
            thread
            for thread in threading.enumerate()
            if thread.ident not in before_ids and thread.is_alive()
        ]
        assert error_codes == ["timeout"] * 25
        assert entered <= 4
        assert len(retained) <= 4
        fixed_thread_ids = {thread.ident for thread in retained}

        additional_codes: list[str] = []
        for index in range(100):
            with pytest.raises(network_safety.NetworkSafetyError) as failure:
                network_safety.validate_destination(
                    f"https://additional-{index}.public.example/",
                    deadline=time.monotonic() + 0.01,
                )
            additional_codes.append(failure.value.code)
        retained_after_additional = {
            thread.ident
            for thread in threading.enumerate()
            if thread.ident not in before_ids and thread.is_alive()
        }

        assert additional_codes == ["timeout"] * 100
        assert entered <= 4
        assert retained_after_additional == fixed_thread_ids
    finally:
        release.set()
        snapshot = getattr(network_safety, "resolver_snapshot", None)
        if callable(snapshot):
            assert _wait_until(lambda: snapshot().active_count == 0)
        else:
            assert _wait_until(
                lambda: not any(
                    thread.ident not in before_ids and thread.is_alive()
                    for thread in threading.enumerate()
                )
            )


def test_worker_and_pending_capacity_stay_bounded_under_saturation() -> None:
    release = threading.Event()
    entered_lock = threading.Lock()
    entered = 0

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal entered
        with entered_lock:
            entered += 1
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=2,
        queue_limit=3,
        resolver=blocking_resolver,
    )
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def submit(index: int) -> None:
        try:
            resolver.resolve(
                f"bounded-{index}.public.example",
                443,
                deadline=time.monotonic() + 2,
            )
        except ResolverError as error:
            result = error.code
        else:
            result = "ok"
        with outcome_lock:
            outcomes.append(result)

    callers = [threading.Thread(target=submit, args=(index,)) for index in range(5)]
    try:
        callers[0].start()
        callers[1].start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 2)
        for caller in callers[2:]:
            caller.start()
        assert _wait_until(lambda: resolver.snapshot().queued_count == 3)

        started = time.monotonic()
        saturated_codes: list[str] = []
        for index in range(100):
            with pytest.raises(ResolverError) as failure:
                resolver.resolve(
                    f"saturated-{index}.public.example",
                    443,
                    deadline=time.monotonic() + 1,
                )
            saturated_codes.append(failure.value.code)
        elapsed = time.monotonic() - started

        snapshot = resolver.snapshot()
        assert saturated_codes == ["resolver_busy"] * 100
        assert snapshot.active_count == snapshot.worker_limit == 2
        assert snapshot.queued_count == snapshot.queue_limit == 3
        assert snapshot.active_count + snapshot.queued_count == 5
        assert entered == 2
        assert elapsed < 0.5
    finally:
        release.set()
        for caller in callers:
            caller.join(timeout=2)
        resolver.close()

    assert outcomes == ["ok"] * 5
    assert _wait_until(lambda: resolver.snapshot().active_count == 0)


def test_queue_wait_consumes_the_callers_original_deadline() -> None:
    release = threading.Event()
    entered_lock = threading.Lock()
    entered = 0

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal entered
        with entered_lock:
            entered += 1
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=blocking_resolver,
    )
    active_outcome: list[str] = []
    queued_outcome: list[str] = []

    def active_call() -> None:
        resolver.resolve(
            "active.public.example",
            443,
            deadline=time.monotonic() + 2,
        )
        active_outcome.append("ok")

    def queued_call() -> None:
        try:
            resolver.resolve(
                "queued-secret.public.example",
                443,
                deadline=time.monotonic() + 0.05,
            )
        except ResolverError as error:
            queued_outcome.append(error.code)

    active = threading.Thread(target=active_call)
    queued = threading.Thread(target=queued_call)
    try:
        active.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        queued.start()
        assert _wait_until(lambda: resolver.snapshot().queued_count == 1)
        queued.join(timeout=1)

        assert not queued.is_alive()
        assert queued_outcome == ["timeout"]
        assert resolver.snapshot().queued_count == 0
        assert entered == 1

        release.set()
        active.join(timeout=2)
        assert active_outcome == ["ok"]
        assert entered == 1
    finally:
        release.set()
        active.join(timeout=2)
        queued.join(timeout=2)
        resolver.close()


def test_abandoned_active_result_is_discarded_until_worker_really_returns() -> None:
    release = threading.Event()

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=blocking_resolver,
    )
    outcome: list[str] = []

    def call() -> None:
        try:
            resolver.resolve(
                "abandoned-secret.public.example",
                443,
                deadline=time.monotonic() + 0.05,
            )
        except ResolverError as error:
            outcome.append(error.code)

    caller = threading.Thread(target=call)
    try:
        caller.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        caller.join(timeout=1)

        assert outcome == ["timeout"]
        assert resolver.snapshot().active_count == 1
        assert resolver.snapshot().queued_count == 0

        release.set()
        assert _wait_until(lambda: resolver.snapshot().active_count == 0)
        snapshot_text = repr(resolver.snapshot())
        assert "abandoned-secret" not in snapshot_text
        assert "93.184.216.34" not in snapshot_text
    finally:
        release.set()
        caller.join(timeout=2)
        resolver.close()


def test_resolver_recovers_capacity_without_rebuilding_the_worker_pool() -> None:
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def recovering_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            assert release_first.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", port, 0, 0),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=recovering_resolver,
    )
    first_outcome: list[str] = []

    def first_call() -> None:
        try:
            resolver.resolve(
                "first-recovery.public.example",
                443,
                deadline=time.monotonic() + 0.05,
            )
        except ResolverError as error:
            first_outcome.append(error.code)

    caller = threading.Thread(target=first_call)
    try:
        caller.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        fixed_workers = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("minicode-dns-resolver-")
        }
        caller.join(timeout=1)
        assert first_outcome == ["timeout"]

        release_first.set()
        assert _wait_until(lambda: resolver.snapshot().active_count == 0)
        answers = resolver.resolve(
            "second-recovery.public.example",
            443,
            deadline=time.monotonic() + 1,
        )
        workers_after_recovery = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("minicode-dns-resolver-")
        }

        assert calls == 2
        assert answers[0][4][0] == "2606:2800:220:1:248:1893:25c8:1946"
        assert workers_after_recovery == fixed_workers
    finally:
        release_first.set()
        caller.join(timeout=2)
        resolver.close()


def test_close_wakes_waiters_rejects_new_work_and_never_joins_blocker() -> None:
    release = threading.Event()
    entered_lock = threading.Lock()
    entered = 0

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal entered
        with entered_lock:
            entered += 1
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=blocking_resolver,
    )
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def call(hostname: str) -> None:
        try:
            resolver.resolve(
                hostname,
                443,
                deadline=time.monotonic() + 2,
            )
        except ResolverError as error:
            with outcome_lock:
                outcomes.append(error.code)

    active = threading.Thread(target=call, args=("active-close.public.example",))
    queued = threading.Thread(target=call, args=("queued-close.public.example",))
    try:
        active.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        queued.start()
        assert _wait_until(lambda: resolver.snapshot().queued_count == 1)

        started = time.monotonic()
        resolver.close()
        resolver.close()
        close_elapsed = time.monotonic() - started
        active.join(timeout=1)
        queued.join(timeout=1)

        snapshot = resolver.snapshot()
        assert close_elapsed < 0.5
        assert not active.is_alive()
        assert not queued.is_alive()
        assert sorted(outcomes) == [
            "network_unavailable",
            "network_unavailable",
        ]
        assert snapshot.accepting is False
        assert snapshot.closed is True
        assert snapshot.active_count == 1
        assert snapshot.queued_count == 0
        assert entered == 1
        with pytest.raises(ResolverError) as failure:
            resolver.resolve(
                "after-close.public.example",
                443,
                deadline=time.monotonic() + 1,
            )
        assert failure.value.code == "network_unavailable"

        release.set()
        assert _wait_until(lambda: resolver.snapshot().active_count == 0)
        assert entered == 1
    finally:
        release.set()
        active.join(timeout=2)
        queued.join(timeout=2)
        resolver.close()


def test_blocked_daemon_resolver_does_not_prevent_interpreter_exit() -> None:
    script = textwrap.dedent(
        """
        import threading
        import time

        from minicode.tools.bounded_resolver import BoundedResolver, ResolverError

        blocker = threading.Event()

        def block_forever(_hostname, _port, **_kwargs):
            blocker.wait()
            raise AssertionError("unreachable")

        resolver = BoundedResolver(
            worker_limit=1,
            queue_limit=1,
            resolver=block_forever,
        )
        try:
            resolver.resolve(
                "child-process-secret.public.example",
                443,
                deadline=time.monotonic() + 0.05,
            )
        except ResolverError as error:
            assert error.code == "timeout"
        snapshot = resolver.snapshot()
        assert snapshot.active_count == 1
        resolver.close()
        print("child-clean-exit", snapshot.active_count, flush=True)
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=3,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "child-clean-exit 1"
    assert "child-process-secret" not in completed.stderr


def test_concurrent_call_deadlines_are_independent() -> None:
    release_slow = threading.Event()

    def mixed_resolver(
        hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        if hostname.startswith("slow-"):
            assert release_slow.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=2,
        queue_limit=2,
        resolver=mixed_resolver,
    )
    slow_outcome: list[str] = []

    def slow_call() -> None:
        try:
            resolver.resolve(
                "slow-secret.public.example",
                443,
                deadline=time.monotonic() + 0.05,
            )
        except ResolverError as error:
            slow_outcome.append(error.code)

    slow = threading.Thread(target=slow_call)
    try:
        slow.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        fast_answers = resolver.resolve(
            "fast.public.example",
            443,
            deadline=time.monotonic() + 1,
        )
        slow.join(timeout=1)

        assert fast_answers[0][4][0] == "93.184.216.34"
        assert slow_outcome == ["timeout"]
        assert resolver.snapshot().active_count == 1

        release_slow.set()
        assert _wait_until(lambda: resolver.snapshot().active_count == 0)
        snapshot = resolver.snapshot()
        assert snapshot.active_count >= 0
        assert snapshot.queued_count >= 0
    finally:
        release_slow.set()
        slow.join(timeout=2)
        resolver.close()


def test_close_and_concurrent_submit_race_never_deadlocks_or_overreleases() -> None:
    release = threading.Event()
    barrier = threading.Barrier(9)

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=2,
        queue_limit=2,
        resolver=blocking_resolver,
    )
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def submit(index: int) -> None:
        barrier.wait()
        try:
            resolver.resolve(
                f"race-{index}.public.example",
                443,
                deadline=time.monotonic() + 1,
            )
        except ResolverError as error:
            outcome = error.code
        else:
            outcome = "ok"
        with outcome_lock:
            outcomes.append(outcome)

    callers = [threading.Thread(target=submit, args=(index,)) for index in range(8)]
    try:
        for caller in callers:
            caller.start()
        barrier.wait()
        resolver.close()
        for caller in callers:
            caller.join(timeout=2)

        snapshot = resolver.snapshot()
        assert all(not caller.is_alive() for caller in callers)
        assert len(outcomes) == 8
        assert set(outcomes) <= {"network_unavailable", "resolver_busy"}
        assert snapshot.active_count >= 0
        assert snapshot.queued_count >= 0
        assert snapshot.active_count <= snapshot.worker_limit
        assert snapshot.queued_count <= snapshot.queue_limit
    finally:
        release.set()
        for caller in callers:
            caller.join(timeout=2)
        resolver.close()

    assert _wait_until(lambda: resolver.snapshot().active_count == 0)


@pytest.mark.parametrize(
    "failure",
    [
        socket.gaierror("resolver-gaierror-fixture-secret"),
        OSError("resolver-oserror-fixture-secret"),
        RuntimeError("resolver-unexpected-fixture-secret"),
    ],
)
def test_resolver_exceptions_are_redacted_and_worker_keeps_serving(
    failure: Exception,
) -> None:
    calls = 0

    def failing_once(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=failing_once,
    )
    try:
        with pytest.raises(ResolverError) as blocked:
            resolver.resolve(
                "exception-secret.public.example",
                443,
                deadline=time.monotonic() + 1,
            )
        answers = resolver.resolve(
            "recovered.public.example",
            443,
            deadline=time.monotonic() + 1,
        )

        assert blocked.value.code == "dns_error"
        assert str(blocked.value) == "dns_error"
        assert "fixture-secret" not in repr(blocked.value)
        assert "exception-secret" not in repr(resolver.snapshot())
        assert answers[0][4][0] == "93.184.216.34"
        assert calls == 2
        assert resolver.snapshot().active_count == 0
    finally:
        resolver.close()


def test_validate_destination_fails_closed_when_resolver_is_saturated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        assert release.wait(2)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=blocking_resolver,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    outcomes: list[str] = []

    def validate(hostname: str) -> None:
        try:
            network_safety.validate_destination(
                f"https://{hostname}/",
                deadline=time.monotonic() + 2,
            )
        except network_safety.NetworkSafetyError as error:
            outcomes.append(error.code)

    active = threading.Thread(target=validate, args=("active.public.example",))
    queued = threading.Thread(target=validate, args=("queued.public.example",))
    try:
        active.start()
        assert _wait_until(lambda: resolver.snapshot().active_count == 1)
        queued.start()
        assert _wait_until(lambda: resolver.snapshot().queued_count == 1)

        with pytest.raises(network_safety.NetworkSafetyError) as saturated:
            network_safety.validate_destination(
                "https://saturated-secret.public.example/",
                deadline=time.monotonic() + 1,
            )

        assert saturated.value.code == "resolver_busy"
        assert saturated.value.tool_output() == (
            "error[resolver_busy]: The DNS resolver is temporarily busy."
        )
        assert "saturated-secret" not in saturated.value.tool_output()
        assert resolver.snapshot().active_count == 1
        assert resolver.snapshot().queued_count == 1
    finally:
        resolver.close()
        release.set()
        active.join(timeout=2)
        queued.join(timeout=2)

    assert sorted(outcomes) == [
        "network_unavailable",
        "network_unavailable",
    ]
