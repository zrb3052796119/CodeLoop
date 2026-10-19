"""Narrow exclusions for new host artifacts in the three-arm scope audit.

The runner snapshots every regular file as ("file", sha256), every link as
("symlink", target), and special entries as ("special", type). Only new regular
files at documented host-owned paths can be excluded. Existing entries are
always compared, even when their names resemble caches or lock files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re


Snapshot = Mapping[str, Sequence[object]]
_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PYC = re.compile(
    r"(?P<source>[^/\\]+)\.(?:cpython-\d+|pypy\d+)"
    r"(?:-pytest-[A-Za-z0-9_.-]+)?(?:\.opt-[12])?\.pyc\Z"
)

_PYTEST_FILES = frozenset(
    {
        ".pytest_cache/.gitignore",
        ".pytest_cache/CACHEDIR.TAG",
        ".pytest_cache/README.md",
        ".pytest_cache/v/cache/nodeids",
        ".pytest_cache/v/cache/lastfailed",
        ".pytest_cache/v/cache/stepwise",
    }
)

# These exact files are produced by MemoryManager, MemoryPipeline,
# CuratorScheduler, ConversationTurnStore, and RunJournal. Their user data root
# is private, but Project and Local scope authorities remain workspace-owned.
_MEMORY_ROOTS = frozenset({".mini-code-memory", ".mini-code-memory-local"})
_MEMORY_FILES = frozenset(
    {
        "memory.json",
        "MEMORY.md",
        "approval_audit.json",
        "pipeline_state.json",
        "curator_schedule.json",
        "project_facts.json",
    }
)
_MEMORY_LOCKS = frozenset(
    {
        "memory-store.lock",
        ".curator-delivery.lock",
        ".curator-schedule.lock",
        ".curator-execution.lock",
        ".conversation-commit.lock",
        ".conversation-turn-state.lock",
    }
)


def _regular_digest(entry: Sequence[object] | None) -> str | None:
    if (
        not isinstance(entry, (tuple, list))
        or len(entry) != 2
        or entry[0] != "file"
        or not isinstance(entry[1], str)
        or _DIGEST.fullmatch(entry[1]) is None
    ):
        return None
    return entry[1]


def runtime_metadata(
    path: str,
    entry: Sequence[object] | None,
    *,
    snapshot: Snapshot | None = None,
) -> bool:
    """Recognize an eligible *new* regular artifact, never a whole directory.

    Callers must still check that the entry did not exist in the baseline.
    Bytecode needs a corresponding regular .py source in the same snapshot;
    this recognizes a conventional cache path, not authenticity of its code.
    """
    digest = _regular_digest(entry)
    if digest is None or not isinstance(path, str) or "\\" in path:
        return False
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if path == ".minicode-memory-authority.lock":
        return digest == _EMPTY_DIGEST
    if path in _PYTEST_FILES:
        return True
    if parts[0] in _MEMORY_ROOTS:
        if len(parts) == 2:
            if parts[1] in _MEMORY_FILES:
                return True
            return parts[1] in _MEMORY_LOCKS and digest == _EMPTY_DIGEST
        if len(parts) == 3 and parts[1] == "authority-blobs-v1":
            # The blob filename is the SHA-256 of its exact canonical bytes.
            return parts[2] == digest + ".json"
        return False
    if len(parts) >= 2 and parts[-2] == "__pycache__":
        match = _PYC.fullmatch(parts[-1])
        if match is None or snapshot is None:
            return False
        source = "/".join([*parts[:-2], match["source"] + ".py"])
        return _regular_digest(snapshot.get(source)) is not None
    return False


def _normalized(entry: Sequence[object] | None) -> object:
    # Persisted snapshots deserialize tuples as lists. They are the same fact.
    return tuple(entry) if isinstance(entry, (tuple, list)) else entry


def changed_paths(before: Snapshot, after: Snapshot) -> list[str]:
    """Return all non-metadata additions plus every existing-entry change."""
    result = []
    for path in before.keys() | after.keys():
        if _normalized(before.get(path)) == _normalized(after.get(path)):
            continue
        if path not in before and runtime_metadata(
            path, after.get(path), snapshot=after
        ):
            continue
        result.append(path)
    return sorted(result)
