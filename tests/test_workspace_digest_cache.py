"""Incremental workspace digest caching contracts.

A structured workflow hashes workspace trees roughly ten times.  The shared
process-lifetime cache reuses the same filesystem file identity and change
metadata. Unproven copies are independently hashed. An unchanged tree
digests identically, changed bytes flip the digest, and the env kill-switch
forces fresh reads without changing tree identity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from minicode.workspace_digest_cache import (
    WorkspaceDigestCache,
    shared_workspace_digest_cache,
)


def _digest(root: Path) -> str:
    """Invoke the real production digest path with checkpoints disabled."""

    transaction = object.__new__(WorkflowWorkspaceTransaction)
    return WorkflowWorkspaceTransaction._filesystem_state_digest(
        transaction, root, use_operation_guard=False
    )


def _make_tree(root: Path, files: int = 40) -> None:
    (root / "pkg").mkdir(parents=True)
    for index in range(files):
        (root / "pkg" / f"mod_{index:03d}.py").write_text(
            f"x = {index}\n" + "# padding " * 20, encoding="utf-8"
        )


def test_repeated_walk_reuses_cached_file_digests(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _make_tree(root)
    cache = shared_workspace_digest_cache()
    cache.clear()

    first = _digest(root)
    hits_before, misses_before = cache.statistics
    assert misses_before == 40 and hits_before == 0

    second = _digest(root)
    hits_after, _misses_after = cache.statistics

    assert first == second
    assert hits_after - hits_before == 40


def test_stat_preserving_tree_copy_rehashes_distinct_file_identities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _make_tree(root)
    cache = shared_workspace_digest_cache()
    cache.clear()

    _digest(root)
    _hits, misses_before = cache.statistics

    # Preserved timestamps do not prove that two filesystem objects have
    # the same bytes. Unknown copies each require their own initial read.
    copy_root = tmp_path / "copy"
    shutil.copytree(root, copy_root, copy_function=shutil.copy2)
    _digest(copy_root)

    _hits2, misses_after = cache.statistics
    assert misses_after - misses_before == 40
    assert _hits2 == _hits


def test_modified_file_flips_digest_with_exactly_one_rehash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _make_tree(root, files=10)
    cache = shared_workspace_digest_cache()
    cache.clear()

    before = _digest(root)
    _hits, misses = cache.statistics
    (root / "pkg" / "mod_005.py").write_text("x = 'changed'\n", encoding="utf-8")
    after = _digest(root)
    _hits2, misses2 = cache.statistics

    assert after != before
    assert misses2 - misses == 1


def test_size_only_change_also_flips_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _make_tree(root, files=5)
    cache = shared_workspace_digest_cache()
    cache.clear()

    before = _digest(root)
    target = root / "pkg" / "mod_000.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "# more\n", encoding="utf-8"
    )
    after = _digest(root)

    assert after != before


def test_env_kill_switch_restores_rehash_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINI_CODE_WORKSPACE_DIGEST_CACHE", "0")
    root = tmp_path / "repo"
    _make_tree(root, files=8)
    cache = shared_workspace_digest_cache()
    cache.clear()

    first = _digest(root)
    second = _digest(root)

    assert first == second
    assert cache.statistics == (0, 0)


def test_env_switch_values_parse() -> None:
    for off in ("0", "false", "no", "off", "FALSE"):
        assert (
            WorkspaceDigestCache(
                enabled=None,
                env={"MINI_CODE_WORKSPACE_DIGEST_CACHE": off},
            ).enabled
            is False
        )
    assert (
        WorkspaceDigestCache(
            enabled=None, env={"MINI_CODE_WORKSPACE_DIGEST_CACHE": "1"}
        ).enabled
        is True
    )
    assert WorkspaceDigestCache(enabled=None, env={}).enabled is True


def test_entry_limit_evicts_oldest(tmp_path: Path) -> None:
    cache = WorkspaceDigestCache(entry_limit=2)
    files = []
    for name, content in (("a", "a"), ("b", "b"), ("c", "c")):
        path = tmp_path / f"{name}.txt"
        path.write_text(content, encoding="utf-8")
        files.append(path)

    cache.content_digest(files[0], relative_posix="a.txt", size=1, mtime_ns=1)
    cache.content_digest(files[1], relative_posix="b.txt", size=1, mtime_ns=2)
    cache.content_digest(files[2], relative_posix="c.txt", size=1, mtime_ns=3)

    _hits, misses = cache.statistics
    assert misses == 3

    # The oldest entry was evicted; re-requesting it rehashes.
    cache.content_digest(files[0], relative_posix="a.txt", size=1, mtime_ns=1)
    _hits2, misses2 = cache.statistics
    assert misses2 == 4 and _hits2 == 0
