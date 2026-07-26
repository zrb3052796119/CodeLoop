"""Tests for session persistence and resume functionality."""

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from minicode.session import (
    AutosaveManager,
    cleanup_old_sessions,
    create_new_session,
    delete_session,
    format_session_list,
    format_session_resume,
    get_latest_session,
    list_sessions,
    load_session,
    save_session,
)
from minicode.web.read_model import DashboardReadModel


@pytest.fixture
def temp_session_dir(tmp_path):
    """Create a temporary session directory."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    with patch("minicode.session.SESSIONS_DIR", sessions_dir), \
         patch("minicode.session.MINI_CODE_DIR", tmp_path):
        yield sessions_dir


def test_create_new_session(temp_session_dir):
    """Test creating a new empty session."""
    workspace = "/tmp/test-workspace"
    session = create_new_session(workspace=workspace)
    
    assert session.session_id is not None
    assert len(session.session_id) == 12
    assert session.workspace == workspace
    assert session.messages == []
    assert session.transcript_entries == []
    assert session.created_at > 0
    assert session.updated_at > 0


def test_save_and_load_session(temp_session_dir):
    """Test saving and loading a session."""
    session = create_new_session(workspace="/tmp/test")
    session.messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    session.transcript_entries = [
        {"id": 1, "kind": "user", "body": "Hello"},
        {"id": 2, "kind": "assistant", "body": "Hi there!"},
    ]
    
    save_session(session)
    
    # Verify file was created
    session_file = temp_session_dir / f"{session.session_id}.json"
    assert session_file.exists()
    
    # Load and verify
    loaded = load_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert len(loaded.messages) == 2
    assert len(loaded.transcript_entries) == 2
    assert loaded.workspace == "/tmp/test"


def test_full_save_replace_failure_preserves_last_valid_session(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "durable old value"}]
    save_session(session, force_full=True)
    session_path = temp_session_dir / f"{session.session_id}.json"
    original = session_path.read_bytes()
    original_generation = session._persistence_generation

    session.messages.append(
        {"role": "assistant", "content": "must not partially replace"}
    )
    with patch("os.replace", side_effect=OSError("replace interrupted")):
        with pytest.raises(OSError, match="replace interrupted"):
            save_session(session, force_full=True)

    assert session_path.read_bytes() == original
    assert session._persistence_generation == original_generation
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [
        {"role": "user", "content": "durable old value"}
    ]
    assert list(temp_session_dir.glob(f".{session_path.name}.*.tmp")) == []


def test_retained_pre_full_delta_cannot_rollback_new_authoritative_base(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "A"}]
    session.transcript_entries = [{"id": 1, "kind": "user", "body": "A"}]
    session.history = ["A"]
    session.permissions_summary = {"stage": "A"}
    session.skills = [{"name": "A"}]
    session.mcp_servers = [{"name": "A"}]
    save_session(session, force_full=True)

    session.messages.append({"role": "assistant", "content": "B"})
    session.transcript_entries.append(
        {"id": 2, "kind": "assistant", "body": "B"}
    )
    session.history = ["A", "B"]
    session.permissions_summary = {"stage": "B"}
    session.skills = [{"name": "B"}]
    session.mcp_servers = [{"name": "B"}]
    save_session(session)
    delta_path = (
        temp_session_dir
        / "deltas"
        / session.session_id
        / "delta_0000.json"
    )
    retained_delta = delta_path.read_bytes()

    session.messages.append({"role": "user", "content": "C"})
    session.transcript_entries.append({"id": 3, "kind": "user", "body": "C"})
    session.history = ["A", "B", "C"]
    session.permissions_summary = {"stage": "C"}
    session.skills = [{"name": "C"}]
    session.mcp_servers = [{"name": "C"}]
    real_unlink = Path.unlink

    def retain_old_delta(path: Path, *args, **kwargs):
        if path == delta_path:
            raise PermissionError("old delta is retained")
        return real_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", retain_old_delta):
        save_session(session, force_full=True)

    assert delta_path.read_bytes() == retained_delta
    authoritative_base = json.loads(
        (temp_session_dir / f"{session.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == authoritative_base["messages"]
    assert reloaded.transcript_entries == authoritative_base["transcript_entries"]
    assert reloaded.history == authoritative_base["history"]
    assert reloaded.permissions_summary == authoritative_base["permissions_summary"]
    assert reloaded.skills == authoritative_base["skills"]
    assert reloaded.mcp_servers == authoritative_base["mcp_servers"]
    assert reloaded.updated_at == authoritative_base["updated_at"]
    assert reloaded.metadata.message_count == 3
    assert reloaded.metadata.last_message == "C"

    session.messages.append({"role": "assistant", "content": "D"})
    save_session(session)
    assert delta_path.read_bytes() == retained_delta
    assert delta_path.with_name("delta_0001.json").exists()


def test_retained_delta_number_is_not_reused_after_full_cleanup_failure(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "old delta"})
    save_session(session)
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    retained_bytes = delta_path.read_bytes()
    session.messages.append({"role": "user", "content": "new base"})
    real_unlink = Path.unlink

    def retain_delta(path: Path, *args, **kwargs):
        if path == delta_path:
            raise PermissionError("retain delta")
        return real_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", retain_delta):
        save_session(session, force_full=True)

    session.messages.append({"role": "assistant", "content": "new delta"})
    save_session(session)
    assert delta_path.read_bytes() == retained_bytes
    assert delta_path.with_name("delta_0001.json").exists()
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "base",
        "old delta",
        "new base",
        "new delta",
    ]


def test_partial_delta_cleanup_computes_safe_next_sequence(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    for content in ("delta zero", "delta one", "delta two"):
        session.messages.append({"role": "assistant", "content": content})
        save_session(session)

    delta_dir = temp_session_dir / "deltas" / session.session_id
    retained_path = delta_dir / "delta_0001.json"
    retained_bytes = retained_path.read_bytes()
    real_unlink = Path.unlink

    def retain_middle_delta(path: Path, *args, **kwargs):
        if path == retained_path:
            raise PermissionError("retain middle delta")
        return real_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", retain_middle_delta):
        save_session(session, force_full=True)

    assert not (delta_dir / "delta_0000.json").exists()
    assert retained_path.read_bytes() == retained_bytes
    assert not (delta_dir / "delta_0002.json").exists()
    session.messages.append({"role": "user", "content": "after cleanup"})
    save_session(session)
    assert retained_path.read_bytes() == retained_bytes
    assert (delta_dir / "delta_0002.json").exists()


def test_repeated_full_cleanup_failure_remains_reloadable_across_restart(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "A"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "B"})
    save_session(session)
    delta_dir = temp_session_dir / "deltas" / session.session_id
    real_unlink = Path.unlink

    def retain_all_deltas(path: Path, *args, **kwargs):
        if path.parent == delta_dir and path.name.startswith("delta_"):
            raise PermissionError("retain every delta")
        return real_unlink(path, *args, **kwargs)

    session.messages.append({"role": "user", "content": "C"})
    with patch.object(Path, "unlink", retain_all_deltas):
        save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "D"})
    with patch.object(Path, "unlink", retain_all_deltas):
        save_session(session, force_full=True)

    restarted = load_session(session.session_id)
    assert restarted is not None
    assert [message["content"] for message in restarted.messages] == [
        "A",
        "B",
        "C",
        "D",
    ]
    restarted.messages.append({"role": "user", "content": "E"})
    save_session(restarted)
    assert (delta_dir / "delta_0000.json").exists()
    assert (delta_dir / "delta_0001.json").exists()
    final = load_session(session.session_id)
    assert final is not None
    assert [message["content"] for message in final.messages] == [
        "A",
        "B",
        "C",
        "D",
        "E",
    ]


def test_legacy_generation_zero_base_and_delta_load_then_upgrade(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "legacy base"}]
    session.history = ["legacy base"]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "legacy delta"})
    session.history.append("legacy delta")
    save_session(session)

    session_path = temp_session_dir / f"{session.session_id}.json"
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    base = json.loads(session_path.read_text(encoding="utf-8"))
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    base.pop("persistence_generation")
    delta.pop("persistence_generation")
    session_path.write_text(json.dumps(base), encoding="utf-8")
    delta_path.write_text(json.dumps(delta), encoding="utf-8")

    legacy = load_session(session.session_id)
    assert legacy is not None
    assert legacy._persistence_generation == 0
    assert [message["content"] for message in legacy.messages] == [
        "legacy base",
        "legacy delta",
    ]
    legacy.messages.append({"role": "user", "content": "upgraded base"})
    legacy.history.append("upgraded base")
    retained_legacy_delta = delta_path.read_bytes()
    real_unlink = Path.unlink

    def retain_legacy_delta(path: Path, *args, **kwargs):
        if path == delta_path:
            raise PermissionError("retain legacy delta")
        return real_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", retain_legacy_delta):
        save_session(legacy, force_full=True)

    upgraded_base = json.loads(session_path.read_text(encoding="utf-8"))
    assert upgraded_base["persistence_generation"] == 1
    assert delta_path.read_bytes() == retained_legacy_delta
    upgraded = load_session(session.session_id)
    assert upgraded is not None
    assert upgraded._persistence_generation == 1
    assert [message["content"] for message in upgraded.messages] == [
        "legacy base",
        "legacy delta",
        "upgraded base",
    ]
    assert upgraded.history == ["legacy base", "legacy delta", "upgraded base"]


@pytest.mark.parametrize(
    "invalid_generation",
    [True, False, -1, 2**31, 1.5, "1", None],
)
def test_invalid_base_persistence_generation_is_rejected(
    temp_session_dir,
    invalid_generation,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session_path = temp_session_dir / f"{session.session_id}.json"
    base = json.loads(session_path.read_text(encoding="utf-8"))
    base["persistence_generation"] = invalid_generation
    session_path.write_text(json.dumps(base), encoding="utf-8")

    assert load_session(session.session_id) is None


def test_old_generation_delta_is_ignored_without_reusing_sequence(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "authoritative base"}]
    session.history = ["authoritative base"]
    save_session(session, force_full=True)
    delta_dir = temp_session_dir / "deltas" / session.session_id
    delta_dir.mkdir(parents=True)
    stale_path = delta_dir / "delta_0000.json"
    stale_path.write_text(
        json.dumps(
            {
                "persistence_generation": 0,
                "msg_offset": 1,
                "messages": [{"role": "assistant", "content": "stale"}],
                "session_state": "stale invalid state is never consulted",
            }
        ),
        encoding="utf-8",
    )

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [
        {"role": "user", "content": "authoritative base"}
    ]
    assert reloaded.history == ["authoritative base"]
    assert reloaded._delta_save_count == 1
    reloaded.messages.append({"role": "assistant", "content": "current"})
    save_session(reloaded)
    assert stale_path.exists()
    assert (delta_dir / "delta_0001.json").exists()


def test_current_generation_delta_is_applied_once_on_each_reload(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "delta"})
    save_session(session)
    base = json.loads(
        (temp_session_dir / f"{session.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    delta = json.loads(
        (
            temp_session_dir
            / "deltas"
            / session.session_id
            / "delta_0000.json"
        ).read_text(encoding="utf-8")
    )
    assert delta["persistence_generation"] == base["persistence_generation"]

    first = load_session(session.session_id)
    second = load_session(session.session_id)
    assert first is not None and second is not None
    expected = [
        {"role": "user", "content": "base"},
        {"role": "assistant", "content": "delta"},
    ]
    assert first.messages == expected
    assert second.messages == expected


def test_state_only_current_generation_delta_remains_valid(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.history = ["state-only history"]
    session.permissions_summary = {"allow": ["read"]}
    session.skills = [{"name": "state-skill"}]
    session.mcp_servers = [{"name": "state-mcp"}]
    save_session(session)

    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    assert "messages" not in delta
    assert "transcripts" not in delta
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [{"role": "user", "content": "base"}]
    assert reloaded.history == ["state-only history"]
    assert reloaded.permissions_summary == {"allow": ["read"]}
    assert reloaded.skills == [{"name": "state-skill"}]
    assert reloaded.mcp_servers == [{"name": "state-mcp"}]
    assert reloaded.metadata.message_count == 1


@pytest.mark.parametrize("invalid_timestamp", [float("nan"), float("inf"), -float("inf"), True])
def test_invalid_delta_state_timestamp_rejects_the_entire_delta_atomically(
    temp_session_dir,
    invalid_timestamp,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    session.history = ["base state"]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "must be rejected"})
    session.history = ["invalid delta state"]
    save_session(session)
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    delta["session_state"]["updated_at"] = invalid_timestamp
    delta_path.write_text(json.dumps(delta), encoding="utf-8")

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [{"role": "user", "content": "base"}]
    assert reloaded.history == ["base state"]
    assert reloaded._delta_save_count == 1


@pytest.mark.parametrize("invalid_timestamp", [float("nan"), float("inf"), -float("inf"), True])
def test_invalid_delta_record_timestamp_rejects_the_entire_delta_atomically(
    temp_session_dir,
    invalid_timestamp,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "must be rejected"})
    save_session(session)
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    delta["ts"] = invalid_timestamp
    delta_path.write_text(json.dumps(delta), encoding="utf-8")

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [{"role": "user", "content": "base"}]
    assert reloaded._delta_save_count == 1


@pytest.mark.parametrize(
    "invalid_generation",
    [True, False, -1, 2**31, 1.5, "1", None],
)
def test_invalid_delta_persistence_generation_is_skipped_without_reuse(
    temp_session_dir,
    invalid_generation,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "invalid delta"})
    save_session(session)
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    delta["persistence_generation"] = invalid_generation
    delta_path.write_text(json.dumps(delta), encoding="utf-8")

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [{"role": "user", "content": "base"}]
    assert reloaded._delta_save_count == 1


def test_current_generation_delta_with_mismatched_session_id_is_rejected(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "wrong session"})
    save_session(session)
    delta_path = (
        temp_session_dir / "deltas" / session.session_id / "delta_0000.json"
    )
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    delta["session_id"] = "another-session"
    delta_path.write_text(json.dumps(delta), encoding="utf-8")

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [{"role": "user", "content": "base"}]
    assert reloaded._delta_save_count == 1


@pytest.mark.parametrize("session_id", ["../escape", "a/b", ".hidden", "x" * 129])
def test_load_session_rejects_invalid_or_traversal_ids(
    temp_session_dir,
    session_id,
) -> None:
    assert load_session(session_id) is None


def test_delta_replace_failure_preserves_tracking_and_retries_without_duplicates(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "first"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "second"})
    real_replace = os.replace

    def fail_delta(source, destination):
        if Path(destination).parent.name == session.session_id:
            raise OSError("delta replace interrupted")
        return real_replace(source, destination)

    with patch("os.replace", side_effect=fail_delta):
        with pytest.raises(OSError, match="delta replace interrupted"):
            save_session(session)

    assert session._last_saved_msg_count == 1
    assert session._delta_save_count == 0
    assert load_session(session.session_id).messages == [
        {"role": "user", "content": "first"}
    ]

    save_session(session)
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]


def test_index_replace_failure_preserves_valid_index_and_can_retry(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "first"}]
    save_session(session, force_full=True)
    index_path = temp_session_dir.parent / "sessions_index.json"
    original_index = index_path.read_bytes()
    real_replace = os.replace

    session.messages.append({"role": "assistant", "content": "second"})

    def fail_index(source, destination):
        if Path(destination) == index_path:
            raise OSError("index replace interrupted")
        return real_replace(source, destination)

    with patch("os.replace", side_effect=fail_index):
        with pytest.raises(OSError, match="index replace interrupted"):
            save_session(session)

    assert index_path.read_bytes() == original_index
    assert json.loads(index_path.read_text(encoding="utf-8"))[
        session.session_id
    ]["message_count"] == 1

    save_session(session)
    assert json.loads(index_path.read_text(encoding="utf-8"))[
        session.session_id
    ]["message_count"] == 2


def test_atomic_temp_cleanup_error_does_not_mask_original_replace_error(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "durable"}]
    save_session(session, force_full=True)
    session_path = temp_session_dir / f"{session.session_id}.json"
    original = session_path.read_bytes()
    session.messages.append({"role": "assistant", "content": "new"})
    real_unlink = Path.unlink

    def fail_temp_cleanup(path: Path, *args, **kwargs):
        if path.name.startswith(f".{session_path.name}."):
            raise PermissionError("temporary cleanup failed")
        return real_unlink(path, *args, **kwargs)

    with patch("minicode.session.os.replace", side_effect=OSError("replace failed")):
        with patch.object(Path, "unlink", fail_temp_cleanup):
            with pytest.raises(OSError, match="replace failed"):
                save_session(session, force_full=True)

    assert session_path.read_bytes() == original


def test_two_in_process_session_saves_retain_both_index_entries(
    temp_session_dir,
) -> None:
    first = create_new_session(workspace="/tmp/first")
    second = create_new_session(workspace="/tmp/second")
    first.messages = [{"role": "user", "content": "first"}]
    second.messages = [{"role": "user", "content": "second"}]
    boundary = threading.Barrier(2)
    errors: list[BaseException] = []

    from minicode import session as session_module

    real_load_index = session_module._load_session_index

    def load_index_at_racing_boundary():
        index = real_load_index()
        try:
            boundary.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return index

    def save_in_thread(session) -> None:
        try:
            save_session(session, force_full=True)
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    with patch(
        "minicode.session._load_session_index",
        side_effect=load_index_at_racing_boundary,
    ):
        threads = [
            threading.Thread(target=save_in_thread, args=(first,)),
            threading.Thread(target=save_in_thread, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert (temp_session_dir / f"{first.session_id}.json").exists()
    assert (temp_session_dir / f"{second.session_id}.json").exists()
    index = json.loads(
        (temp_session_dir.parent / "sessions_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(index) == {first.session_id, second.session_id}
    assert {metadata.session_id for metadata in list_sessions()} == {
        first.session_id,
        second.session_id,
    }


def test_concurrent_save_delete_keeps_index_json_and_files_consistent(
    temp_session_dir,
) -> None:
    deleted_session = create_new_session(workspace="/tmp/deleted")
    deleted_session.messages = [{"role": "user", "content": "delete me"}]
    save_session(deleted_session, force_full=True)
    saved_session = create_new_session(workspace="/tmp/saved")
    saved_session.messages = [{"role": "user", "content": "keep me"}]
    boundary = threading.Barrier(2)
    errors: list[BaseException] = []
    delete_results: list[bool] = []

    from minicode import session as session_module

    real_load_index = session_module._load_session_index

    def load_index_at_racing_boundary():
        index = real_load_index()
        try:
            boundary.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return index

    def save_in_thread() -> None:
        try:
            save_session(saved_session, force_full=True)
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    def delete_in_thread() -> None:
        try:
            delete_results.append(delete_session(deleted_session.session_id))
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    with patch(
        "minicode.session._load_session_index",
        side_effect=load_index_at_racing_boundary,
    ):
        threads = [
            threading.Thread(target=save_in_thread),
            threading.Thread(target=delete_in_thread),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert delete_results == [True]
    index = json.loads(
        (temp_session_dir.parent / "sessions_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(index) == {saved_session.session_id}
    assert not (temp_session_dir / f"{deleted_session.session_id}.json").exists()
    assert (temp_session_dir / f"{saved_session.session_id}.json").exists()


def test_dashboard_reader_sees_complete_old_or_new_session_during_replace(
    temp_session_dir,
) -> None:
    workspace = temp_session_dir.parent / "workspace"
    workspace.mkdir()
    session = create_new_session(workspace=str(workspace))
    session.messages = [{"role": "user", "content": "old complete value"}]
    save_session(session, force_full=True)
    session.messages.append(
        {"role": "assistant", "content": "new complete value"}
    )
    session_path = temp_session_dir / f"{session.session_id}.json"
    entered_replace = threading.Event()
    release_replace = threading.Event()
    real_replace = os.replace
    errors: list[BaseException] = []

    def block_session_replace(source, destination):
        if Path(destination) == session_path:
            entered_replace.set()
            assert release_replace.wait(timeout=5)
        return real_replace(source, destination)

    def save_in_thread() -> None:
        try:
            save_session(session, force_full=True)
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    with patch("minicode.session.os.replace", side_effect=block_session_replace):
        thread = threading.Thread(target=save_in_thread)
        thread.start()
        assert entered_replace.wait(timeout=5)
        before = DashboardReadModel(
            workspace=workspace,
            data_dir=temp_session_dir.parent,
        ).session_detail(session.session_id, limit=50)
        release_replace.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    assert [message["content"] for message in before["messages"]] == [
        "old complete value"
    ]
    after = DashboardReadModel(
        workspace=workspace,
        data_dir=temp_session_dir.parent,
    ).session_detail(session.session_id, limit=50)
    assert [message["content"] for message in after["messages"]] == [
        "old complete value",
        "new complete value",
    ]


def test_corrupt_delta_is_skipped_without_reusing_its_sequence(
    temp_session_dir,
) -> None:
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    delta_dir = temp_session_dir / "deltas" / session.session_id
    delta_dir.mkdir(parents=True)
    (delta_dir / "delta_0000.json").write_text("{broken", encoding="utf-8")

    resumed = load_session(session.session_id)
    assert resumed is not None
    assert resumed.messages == [{"role": "user", "content": "base"}]
    assert resumed._delta_save_count == 1
    resumed.messages.append({"role": "assistant", "content": "after resume"})
    save_session(resumed)

    assert (delta_dir / "delta_0000.json").read_text(encoding="utf-8") == "{broken"
    assert (delta_dir / "delta_0001.json").exists()
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == [
        {"role": "user", "content": "base"},
        {"role": "assistant", "content": "after resume"},
    ]


def test_load_nonexistent_session(temp_session_dir):
    """Test loading a session that doesn't exist."""
    loaded = load_session("nonexistent")
    assert loaded is None


def test_delete_session(temp_session_dir):
    """Test deleting a session."""
    session = create_new_session(workspace="/tmp/test")
    save_session(session)
    
    # Delete
    result = delete_session(session.session_id)
    assert result is True
    
    # Verify file is gone
    session_file = temp_session_dir / f"{session.session_id}.json"
    assert not session_file.exists()
    
    # Try deleting again
    result = delete_session(session.session_id)
    assert result is False


def test_list_sessions(temp_session_dir):
    """Test listing all sessions."""
    # Create multiple sessions
    sessions = []
    for i in range(3):
        session = create_new_session(workspace=f"/tmp/test-{i}")
        session.messages = [{"role": "user", "content": f"Message {i}"}]
        save_session(session)
        sessions.append(session)
    
    # List and verify
    listed = list_sessions()
    assert len(listed) == 3
    
    # Should be sorted by updated_at (newest first)
    assert listed[0].updated_at >= listed[1].updated_at


def test_get_latest_session(temp_session_dir):
    """Test getting the most recent session."""
    # Create sessions for different workspaces
    session1 = create_new_session(workspace="/tmp/workspace1")
    save_session(session1)
    
    session2 = create_new_session(workspace="/tmp/workspace2")
    save_session(session2)
    
    # Get latest for workspace2
    latest = get_latest_session(workspace="/tmp/workspace2")
    assert latest is not None
    assert latest.session_id == session2.session_id
    
    # Get latest without filter
    latest_any = get_latest_session()
    assert latest_any is not None


def test_cleanup_old_sessions(temp_session_dir):
    """Test cleanup of old sessions beyond limit."""
    # Create 10 sessions
    for i in range(10):
        session = create_new_session(workspace=f"/tmp/test-{i}")
        save_session(session)
    
    # Cleanup to keep only 5
    deleted = cleanup_old_sessions(max_sessions=5)
    assert deleted == 5
    
    # Verify only 5 remain
    remaining = list_sessions()
    assert len(remaining) == 5


def test_autosave_manager(temp_session_dir):
    """Test autosave manager with rate limiting."""
    session = create_new_session(workspace="/tmp/test")
    manager = AutosaveManager(session, interval=1)
    
    # Initially not dirty
    assert not manager.should_save()
    
    # Mark dirty
    manager.mark_dirty()
    
    # Should not save yet (interval not elapsed)
    assert not manager.should_save()
    
    # Force save
    manager.force_save()
    
    # Verify saved
    loaded = load_session(session.session_id)
    assert loaded is not None


def test_format_session_list(temp_session_dir):
    """Test formatting session list for display."""
    # Empty list
    result = format_session_list([])
    assert "No saved sessions" in result
    
    # With sessions
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "Hello world"}]
    session.update_metadata()
    
    result = format_session_list([session.metadata])
    assert "Saved sessions:" in result
    assert session.session_id[:8] in result


def test_format_session_resume(temp_session_dir):
    """Test formatting session info for resume."""
    session = create_new_session(workspace="/tmp/test")
    session.messages = [{"role": "user", "content": "Hello"}]
    
    result = format_session_resume(session)
    assert "Resuming session" in result
    assert session.session_id[:8] in result
    assert "/tmp/test" in result
