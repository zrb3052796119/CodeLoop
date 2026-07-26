from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from minicode.web.change_feed import DashboardChangeFeed
from minicode.web.event_stream import (
    DashboardEventStream,
    EventStreamBusy,
    InvalidEventCursor,
)
from minicode.run_journal import stable_workspace_id


RESOURCE_NAMES = (
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
)


def _snapshot(
    suffix: str,
    *,
    overrides: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, object]:
    resources = {
        name: {"status": "live", "revision": f"rev_{suffix * 64}"}
        for name in RESOURCE_NAMES
    }
    for name, (status, revision_suffix) in (overrides or {}).items():
        resources[name] = {
            "status": status,
            "revision": f"rev_{revision_suffix * 64}",
        }
    return {
        "schemaVersion": 2,
        "generatedAt": "2026-07-19T00:00:00.000Z",
        "mode": "read-only",
        "pollAfterMs": 2_000,
        "resources": resources,
        "diagnostics": [],
    }


class _ControlledFeed:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.current = snapshot
        self.calls = 0
        self.sampled = threading.Condition()

    def snapshot(self) -> dict[str, object]:
        with self.sampled:
            self.calls += 1
            value = self.current
            self.sampled.notify_all()
            return value

    def wait_for_calls(self, count: int) -> None:
        with self.sampled:
            assert self.sampled.wait_for(lambda: self.calls >= count, timeout=2)


class _ManualSamplerWait:
    def __init__(self) -> None:
        self._advance = threading.Event()
        self.waiting = threading.Event()

    def __call__(self, stop: threading.Event, _seconds: float) -> bool:
        self.waiting.set()
        while not stop.is_set():
            if self._advance.wait(0.05):
                self._advance.clear()
                self.waiting.clear()
                return False
        return True

    def advance(self) -> None:
        assert self.waiting.wait(timeout=2)
        self._advance.set()

    def release(self) -> None:
        self._advance.set()


def _decode_frame(frame: bytes) -> tuple[str | None, str | None, dict[str, object] | None]:
    event_id = None
    event_type = None
    data = None
    for line in frame.decode("utf-8").splitlines():
        if line.startswith("id: "):
            event_id = line.removeprefix("id: ")
        elif line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))
    return event_id, event_type, data


def _event_sequence(event_id: str) -> int:
    match = re.fullmatch(r"evt_[0-9a-f]{32}_([0-9a-f]{16})", event_id)
    assert match is not None
    return int(match.group(1), 16)


def test_initial_sample_is_only_a_baseline_then_one_change_is_invalidated() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    subscription = stream.subscribe()
    try:
        ready = subscription.next_batch(timeout=0)
        assert len(ready) == 1
        ready_id, ready_type, ready_data = _decode_frame(ready[0])
        assert ready_type == "stream.ready"
        assert ready_data is not None
        assert ready_data["type"] == "stream.ready"
        assert ready_data["streamId"].startswith("stream_")

        assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)

        feed.current = _snapshot(
            "a", overrides={"permissions": ("live", "b")}
        )
        sampler_wait.advance()
        feed.wait_for_calls(2)

        changed = subscription.next_batch(timeout=0)
        assert len(changed) == 1
        changed_id, changed_type, changed_data = _decode_frame(changed[0])
        assert ready_id != changed_id
        assert changed_type == "resources.changed"
        assert changed_data == {
            "schemaVersion": 2,
            "type": "resources.changed",
            "generatedAt": changed_data["generatedAt"],
            "resources": [
                {
                    "name": "permissions",
                    "status": "live",
                    "revision": f"rev_{'b' * 64}",
                }
            ],
        }
    finally:
        subscription.close()
        sampler_wait.release()
        stream.close()


def test_retained_cursor_replays_only_later_events_in_sequence_order() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    live = stream.subscribe()
    try:
        live.next_batch(timeout=0)  # ready
        feed.current = _snapshot("a", overrides={"memory": ("live", "b")})
        sampler_wait.advance()
        feed.wait_for_calls(2)
        first = live.next_batch(timeout=1)[0]
        first_id, _, _ = _decode_frame(first)
        assert first_id is not None

        feed.current = _snapshot(
            "a",
            overrides={"memory": ("live", "b"), "runs": ("partial", "c")},
        )
        sampler_wait.advance()
        feed.wait_for_calls(3)
        second = live.next_batch(timeout=1)[0]
        second_id, _, _ = _decode_frame(second)
        assert second_id is not None
        live.close()

        resumed = stream.subscribe(first_id)
        try:
            replay = resumed.next_batch(timeout=0)
            assert replay == (second,)
            assert _decode_frame(replay[0])[0] == second_id
        finally:
            resumed.close()
    finally:
        live.close()
        sampler_wait.release()
        stream.close()


def test_cursor_from_an_old_stream_epoch_gets_a_stream_restarted_reset() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    try:
        subscription = stream.subscribe(f"evt_{'f' * 32}_{1:016x}")
        try:
            reset = subscription.next_batch(timeout=0)
            assert len(reset) == 1
            event_id, event_type, payload = _decode_frame(reset[0])
            assert event_id is not None
            assert event_type == "stream.reset"
            assert payload == {
                "schemaVersion": 2,
                "type": "stream.reset",
                "generatedAt": payload["generatedAt"],
                "reason": "stream_restarted",
                "resources": list(RESOURCE_NAMES),
            }
            assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
        finally:
            subscription.close()
    finally:
        sampler_wait.release()
        stream.close()


def test_subscriber_budget_is_strict_and_released_by_idempotent_close() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        feed,
        sampler_wait=sampler_wait,
        max_subscribers=1,
    )
    stream.start()
    feed.wait_for_calls(1)
    first = stream.subscribe()
    try:
        with pytest.raises(EventStreamBusy):
            stream.subscribe()
        first.close()
        first.close()
        replacement = stream.subscribe()
        replacement.close()
    finally:
        first.close()
        sampler_wait.release()
        stream.close()
        stream.close()


def test_changed_resources_are_merged_sorted_and_status_changes_are_visible() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    subscription = stream.subscribe()
    try:
        ready_id, _, _ = _decode_frame(subscription.next_batch(timeout=0)[0])
        assert ready_id is not None
        feed.current = _snapshot(
            "a",
            overrides={
                "connections": ("partial", "f"),
                "runs": ("live", "b"),
                "turns": ("error", "d"),
            },
        )
        sampler_wait.advance()
        feed.wait_for_calls(2)
        changed = subscription.next_batch(timeout=1)[0]
        changed_id, event_type, payload = _decode_frame(changed)
        assert changed_id is not None
        assert event_type == "resources.changed"
        assert [item["name"] for item in payload["resources"]] == [
            "runs",
            "turns",
            "connections",
        ]
        assert [item["status"] for item in payload["resources"]] == [
            "live",
            "error",
            "partial",
        ]
        assert _event_sequence(changed_id) == _event_sequence(ready_id) + 1

        sampler_wait.advance()
        feed.wait_for_calls(3)
        assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
    finally:
        subscription.close()
        sampler_wait.release()
        stream.close()


def test_bounded_ring_resets_old_and_future_cursors_without_fake_replay() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        feed,
        sampler_wait=sampler_wait,
        ring_size=2,
    )
    stream.start()
    feed.wait_for_calls(1)
    live = stream.subscribe()
    try:
        ready_id, _, _ = _decode_frame(live.next_batch(timeout=0)[0])
        assert ready_id is not None
        frames = []
        for index, suffix in enumerate(("b", "c", "d"), start=2):
            feed.current = _snapshot(
                "a", overrides={"runs": ("live", suffix)}
            )
            sampler_wait.advance()
            feed.wait_for_calls(index)
            frames.append(live.next_batch(timeout=1)[0])
        live.close()

        old = stream.subscribe(ready_id)
        try:
            _, kind, payload = _decode_frame(old.next_batch(timeout=0)[0])
            assert kind == "stream.reset"
            assert payload["reason"] == "replay_unavailable"
        finally:
            old.close()

        second_id, _, _ = _decode_frame(frames[1])
        assert second_id is not None
        retained = stream.subscribe(second_id)
        try:
            assert retained.next_batch(timeout=0) == (frames[2],)
        finally:
            retained.close()

        last_id, _, _ = _decode_frame(frames[-1])
        assert last_id is not None
        prefix = last_id.rsplit("_", 1)[0]
        future = stream.subscribe(f"{prefix}_{999:016x}")
        try:
            _, kind, payload = _decode_frame(future.next_batch(timeout=0)[0])
            assert kind == "stream.reset"
            assert payload["reason"] == "replay_unavailable"
        finally:
            future.close()
    finally:
        live.close()
        sampler_wait.release()
        stream.close()


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "evt_bad",
        f"evt_{'g' * 32}_{1:016x}",
        f"evt_{'a' * 32}_{'0' * 17}",
        "x" * 65,
        True,
        -1,
    ],
)
def test_invalid_cursor_values_are_rejected_before_subscription(cursor) -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    try:
        with pytest.raises(InvalidEventCursor):
            stream.subscribe(cursor)
    finally:
        sampler_wait.release()
        stream.close()


def test_sampler_failure_recovers_and_does_not_emit_exception_content() -> None:
    class FailingOnceFeed(_ControlledFeed):
        def snapshot(self) -> dict[str, object]:
            with self.sampled:
                self.calls += 1
                call = self.calls
                value = self.current
                self.sampled.notify_all()
            if call == 1:
                raise RuntimeError("Bearer secret /Users/private prompt")
            return value

    feed = FailingOnceFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    subscription = stream.subscribe()
    try:
        subscription.next_batch(timeout=0)
        sampler_wait.advance()
        feed.wait_for_calls(2)  # first valid sample becomes the baseline
        assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
        feed.current = _snapshot("a", overrides={"skills": ("live", "b")})
        sampler_wait.advance()
        feed.wait_for_calls(3)
        frame = subscription.next_batch(timeout=1)[0]
        assert _decode_frame(frame)[1] == "resources.changed"
        assert b"secret" not in frame
        assert b"/Users/" not in frame
        assert len(frame) <= 4 * 1024
    finally:
        subscription.close()
        sampler_wait.release()
        stream.close()


def test_start_and_multiple_subscribers_share_exactly_one_sampler() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    stream.start()
    feed.wait_for_calls(1)
    first = stream.subscribe()
    second = stream.subscribe()
    try:
        assert feed.calls == 1
        feed.current = _snapshot("a", overrides={"sessions": ("partial", "b")})
        sampler_wait.advance()
        feed.wait_for_calls(2)
        first_frame = first.next_batch(timeout=0)[0]  # ready
        second_frame = second.next_batch(timeout=0)[0]  # ready
        assert _decode_frame(first_frame)[0] == _decode_frame(second_frame)[0]
        changed_one = first.next_batch(timeout=1)[0]
        changed_two = second.next_batch(timeout=1)[0]
        assert changed_one == changed_two
        assert feed.calls == 2
    finally:
        first.close()
        second.close()
        sampler_wait.release()
        stream.close()


def test_close_wakes_a_waiting_subscriber_and_default_sampler() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    stream = DashboardEventStream(feed)
    stream.start()
    feed.wait_for_calls(1)
    subscription = stream.subscribe()
    subscription.next_batch(timeout=0)
    entered = threading.Event()
    finished = threading.Event()
    result: list[tuple[bytes, ...]] = []

    def wait_for_event() -> None:
        entered.set()
        result.append(subscription.next_batch(timeout=60))
        finished.set()

    waiter = threading.Thread(target=wait_for_event)
    waiter.start()
    assert entered.wait(timeout=2)
    stream.close()
    assert finished.wait(timeout=2)
    waiter.join(timeout=2)
    assert result == [()]
    subscription.close()
    stream.close()


def test_real_change_feed_stream_is_workspace_isolated_and_writes_nothing(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first_wait = _ManualSamplerWait()
    second_wait = _ManualSamplerWait()
    first = DashboardEventStream(
        DashboardChangeFeed(first_workspace, data_dir=data_dir),
        sampler_wait=first_wait,
    )
    second = DashboardEventStream(
        DashboardChangeFeed(second_workspace, data_dir=data_dir),
        sampler_wait=second_wait,
    )
    first.start()
    second.start()
    assert first_wait.waiting.wait(timeout=2)
    assert second_wait.waiting.wait(timeout=2)
    first_subscription = first.subscribe()
    second_subscription = second.subscribe()
    try:
        first_subscription.next_batch(timeout=0)
        second_subscription.next_batch(timeout=0)
        before = {
            path.relative_to(tmp_path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        run = (
            data_dir
            / "dashboard"
            / "workspaces"
            / stable_workspace_id(first_workspace)
            / "runs"
            / ("run_" + "a" * 32)
        )
        run.mkdir(parents=True)
        (run / "metadata.json").write_text("private body", encoding="utf-8")
        expected_written = {
            path.relative_to(tmp_path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        first_wait.advance()
        second_wait.advance()
        assert first_subscription.next_batch(timeout=1)[0].startswith(b"id: evt_")
        assert second_subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
        after = {
            path.relative_to(tmp_path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        assert before != expected_written
        assert after == expected_written
    finally:
        first_subscription.close()
        second_subscription.close()
        first_wait.release()
        second_wait.release()
        first.close()
        second.close()


def test_epoch_format_is_stable_per_stream_and_unique_across_streams() -> None:
    first_feed = _ControlledFeed(_snapshot("a"))
    second_feed = _ControlledFeed(_snapshot("a"))
    first_wait = _ManualSamplerWait()
    second_wait = _ManualSamplerWait()
    first = DashboardEventStream(first_feed, sampler_wait=first_wait)
    second = DashboardEventStream(second_feed, sampler_wait=second_wait)
    first.start()
    second.start()
    first_feed.wait_for_calls(1)
    second_feed.wait_for_calls(1)
    first_subscription = first.subscribe()
    second_subscription = second.subscribe()
    try:
        first_id, _, first_payload = _decode_frame(
            first_subscription.next_batch(timeout=0)[0]
        )
        second_id, _, second_payload = _decode_frame(
            second_subscription.next_batch(timeout=0)[0]
        )
        assert re.fullmatch(r"stream_[0-9a-f]{32}", first_payload["streamId"])
        assert re.fullmatch(r"evt_[0-9a-f]{32}_[0-9a-f]{16}", first_id)
        assert first_payload["streamId"] != second_payload["streamId"]
        assert first_id != second_id
    finally:
        first_subscription.close()
        second_subscription.close()
        first_wait.release()
        second_wait.release()
        first.close()
        second.close()


def test_heartbeats_do_not_advance_sequence_or_enter_replay() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(feed, sampler_wait=sampler_wait)
    stream.start()
    feed.wait_for_calls(1)
    subscription = stream.subscribe()
    try:
        ready_id, _, _ = _decode_frame(subscription.next_batch(timeout=0)[0])
        assert ready_id is not None
        assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
        assert subscription.next_batch(timeout=0) == (b": heartbeat\n\n",)
        feed.current = _snapshot("a", overrides={"memory": ("live", "b")})
        sampler_wait.advance()
        feed.wait_for_calls(2)
        changed_id, _, _ = _decode_frame(subscription.next_batch(timeout=1)[0])
        assert changed_id is not None
        assert _event_sequence(changed_id) == _event_sequence(ready_id) + 1

        replay = stream.subscribe(ready_id)
        try:
            frames = replay.next_batch(timeout=0)
            assert len(frames) == 1
            assert frames[0].startswith(b"id: ")
            assert b"heartbeat" not in frames[0]
        finally:
            replay.close()
    finally:
        subscription.close()
        sampler_wait.release()
        stream.close()


def test_slow_live_subscriber_gets_reset_after_ring_overflow() -> None:
    feed = _ControlledFeed(_snapshot("a"))
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        feed,
        sampler_wait=sampler_wait,
        ring_size=1,
    )
    stream.start()
    feed.wait_for_calls(1)
    slow = stream.subscribe()
    fast = stream.subscribe()
    try:
        slow.next_batch(timeout=0)
        fast.next_batch(timeout=0)
        for call, suffix in ((2, "b"), (3, "c")):
            feed.current = _snapshot(
                "a", overrides={"connections": ("live", suffix)}
            )
            sampler_wait.advance()
            feed.wait_for_calls(call)
            fast.next_batch(timeout=1)
        _, event_type, payload = _decode_frame(slow.next_batch(timeout=0)[0])
        assert event_type == "stream.reset"
        assert payload["reason"] == "replay_unavailable"
    finally:
        slow.close()
        fast.close()
        sampler_wait.release()
        stream.close()
