from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from minicode.memory_authority_io import MemoryAuthorityLimitError
from minicode.memory_blob_store import (
    MAX_MEMORY_BLOB_BYTES,
    MEMORY_BLOB_DIRECTORY_NAME,
    MemoryBlobReference,
    MemoryBlobStore,
    canonical_memory_blob_bytes,
)
from minicode.memory_store import (
    MemoryStoreConflict,
    MemoryStoreUnsafePath,
)


def test_put_and_read_round_trip_uses_canonical_content_address(tmp_path: Path) -> None:
    root = tmp_path / "scope"
    store = MemoryBlobStore(root)
    value = {"z": [2, 1], "message": "记忆", "a": {"enabled": True}}

    reference = store.put_json(value)

    expected = '{"a":{"enabled":true},"message":"记忆","z":[2,1]}'.encode()
    assert reference.digest == hashlib.sha256(expected).hexdigest()
    assert reference.size_bytes == len(expected)
    assert (store.root / reference.filename).read_bytes() == expected
    assert store.read_json(reference) == value
    assert store.read_json(reference.digest) == value


def test_same_content_is_idempotent_and_does_not_replace_inode(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    reference = store.put_json({"receipt": [1, 2, 3]})
    path = store.root / reference.filename
    before = path.stat()

    replayed = store.put_json({"receipt": [1, 2, 3]})
    after = path.stat()

    assert replayed == reference
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert list(store.root.glob("*.json")) == [path]
    assert list(store.root.glob("*.tmp")) == []


def test_concurrent_same_content_publish_is_idempotent(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")

    with ThreadPoolExecutor(max_workers=8) as executor:
        references = list(
            executor.map(lambda _: store.put_json({"operation": "same"}), range(32))
        )

    assert len(set(references)) == 1
    reference = references[0]
    assert list(store.root.glob("*.json")) == [store.root / reference.filename]
    assert not list(store.root.glob(".*.tmp"))
    assert store.read_json(reference) == {"operation": "same"}


def test_exact_blob_limit_is_accepted_and_larger_value_is_rejected(
    tmp_path: Path,
) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    exact = "x" * (MAX_MEMORY_BLOB_BYTES - 2)

    reference = store.put_json(exact)

    assert reference.size_bytes == MAX_MEMORY_BLOB_BYTES
    assert store.read_json(reference) == exact
    with pytest.raises(MemoryAuthorityLimitError, match="memory_blob_limit_exceeded"):
        store.put_json(exact + "x")


@pytest.mark.parametrize(
    "value",
    [
        {"number": float("nan")},
        {"number": float("inf")},
        {"number": 1 << 53},
        {"bad": "\ud800"},
        {"tuple": (1, 2)},
    ],
)
def test_put_rejects_values_outside_strict_json_contract(
    tmp_path: Path,
    value: object,
) -> None:
    store = MemoryBlobStore(tmp_path / "scope")

    with pytest.raises(ValueError):
        store.put_json(value)

    assert not store.root.exists()


def test_existing_corrupt_content_at_digest_conflicts_without_replacement(
    tmp_path: Path,
) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    value = {"receipt": "expected"}
    encoded = canonical_memory_blob_bytes(value)
    digest = hashlib.sha256(encoded).hexdigest()
    store.root.mkdir(parents=True)
    target = store.root / f"{digest}.json"
    target.write_bytes(b'{"receipt":"corrupt"}')
    before = target.read_bytes()

    with pytest.raises(MemoryStoreConflict):
        store.put_json(value)

    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        b'{"b":2, "a":1}',
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b"\xff",
        b"",
    ],
)
def test_read_rejects_noncanonical_or_invalid_blob_bytes(
    tmp_path: Path,
    content: bytes,
) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    store.root.mkdir(parents=True)
    digest = hashlib.sha256(content).hexdigest()
    path = store.root / f"{digest}.json"
    path.write_bytes(content)

    with pytest.raises(MemoryStoreConflict):
        store.read_json(digest)


def test_read_rejects_digest_and_reference_size_mismatch(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    reference = store.put_json({"valid": True})
    wrong_digest = "f" * 64

    with pytest.raises(MemoryStoreConflict, match="missing"):
        store.read_json(wrong_digest)
    with pytest.raises(MemoryStoreConflict, match="size"):
        store.read_json(
            MemoryBlobReference(
                digest=reference.digest,
                size_bytes=reference.size_bytes + 1,
            )
        )


@pytest.mark.parametrize(
    "digest",
    ["", "../memory.json", "A" * 64, "0" * 63, "0" * 65, "é" * 64],
)
def test_read_rejects_noncanonical_digest_before_filesystem_access(
    tmp_path: Path,
    digest: str,
) -> None:
    root = tmp_path / "scope"
    store = MemoryBlobStore(root)

    with pytest.raises(ValueError, match="digest"):
        store.read_json(digest)

    assert not root.exists()


def test_reference_document_is_closed_and_bounded() -> None:
    reference = MemoryBlobReference(digest="0" * 64, size_bytes=123)

    assert MemoryBlobReference.from_dict(reference.to_dict()) == reference
    with pytest.raises(ValueError, match="schema"):
        MemoryBlobReference.from_dict({**reference.to_dict(), "extra": True})
    with pytest.raises(ValueError, match="size"):
        MemoryBlobReference(digest="0" * 64, size_bytes=0)
    with pytest.raises(ValueError, match="size"):
        MemoryBlobReference(digest="0" * 64, size_bytes=True)


def test_read_missing_blob_is_no_write(tmp_path: Path) -> None:
    root = tmp_path / "scope"
    store = MemoryBlobStore(root)

    with pytest.raises(MemoryStoreConflict, match="scope root is missing"):
        store.read_json("0" * 64)

    assert not root.exists()


def test_read_missing_blob_directory_is_no_write(tmp_path: Path) -> None:
    root = tmp_path / "scope"
    root.mkdir()
    store = MemoryBlobStore(root)

    with pytest.raises(MemoryStoreConflict, match="directory is missing"):
        store.read_json("0" * 64)

    assert list(root.iterdir()) == []


def test_scope_root_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "scope"
    root.symlink_to(target, target_is_directory=True)
    store = MemoryBlobStore(root)

    with pytest.raises(MemoryStoreUnsafePath):
        store.put_json({"safe": True})
    with pytest.raises(MemoryStoreUnsafePath):
        store.read_json("0" * 64)

    assert list(target.iterdir()) == []


def test_blob_directory_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "scope"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / MEMORY_BLOB_DIRECTORY_NAME).symlink_to(
        outside,
        target_is_directory=True,
    )
    store = MemoryBlobStore(root)

    with pytest.raises(MemoryStoreUnsafePath):
        store.put_json({"safe": True})
    with pytest.raises(MemoryStoreUnsafePath):
        store.read_json("0" * 64)

    assert list(outside.iterdir()) == []


def test_blob_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    value = {"safe": True}
    encoded = canonical_memory_blob_bytes(value)
    digest = hashlib.sha256(encoded).hexdigest()
    outside = tmp_path / "outside.json"
    outside.write_bytes(encoded)
    store.root.mkdir(parents=True)
    (store.root / f"{digest}.json").symlink_to(outside)

    with pytest.raises(MemoryStoreUnsafePath):
        store.read_json(digest)
    with pytest.raises(MemoryStoreUnsafePath):
        store.put_json(value)


def test_blob_directory_and_regular_root_are_rejected(tmp_path: Path) -> None:
    regular_root = tmp_path / "regular-root"
    regular_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(MemoryStoreUnsafePath):
        MemoryBlobStore(regular_root).put_json({"safe": True})

    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / MEMORY_BLOB_DIRECTORY_NAME).write_text(
        "not a directory",
        encoding="utf-8",
    )
    with pytest.raises(MemoryStoreUnsafePath):
        MemoryBlobStore(scope).put_json({"safe": True})


@pytest.mark.skipif(os.name != "posix", reason="POSIX special-file contract")
def test_fifo_blob_is_rejected_without_blocking(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    store.root.mkdir(parents=True)
    digest = "0" * 64
    os.mkfifo(store.root / f"{digest}.json")

    with pytest.raises(MemoryStoreUnsafePath):
        store.read_json(digest)


def test_blob_content_is_not_deleted_by_store_api(tmp_path: Path) -> None:
    store = MemoryBlobStore(tmp_path / "scope")
    reference = store.put_json({"durable": True})

    assert not hasattr(store, "delete")
    assert (store.root / reference.filename).is_file()
    assert json.loads((store.root / reference.filename).read_text()) == {
        "durable": True
    }
