"""Known host artifacts never create a directory-wide scope exemption."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts.three_arm_scope_metadata import changed_paths, runtime_metadata


def regular(content: bytes = b"ordinary data") -> tuple[str, str]:
    return "file", hashlib.sha256(content).hexdigest()


def test_new_agent_profiles_and_hidden_source_are_scope_changes():
    before = {".mini-code/agents/visual-judge.md": regular(b"baseline")}
    after = {
        ".mini-code/agents/visual-judge.md": regular(b"modified"),
        ".mini-code/agents/extra.md": regular(),
        ".mini-code/reference_copy.py": regular(),
        ".mini-code-memory/extra.py": regular(),
        ".mini-code-memory-local/unknown.json": regular(),
        ".pytest_cache/v/cache/source.py": regular(),
        "__pycache__/hidden.py": regular(),
    }
    assert changed_paths(before, after) == sorted(after)


def test_actual_host_memory_output_is_narrowly_excluded():
    blob = regular(b'{"events":[]}')
    after = {
        ".minicode-memory-authority.lock": regular(b""),
        ".mini-code-memory/memory-store.lock": regular(b""),
        ".mini-code-memory/curator_schedule.json": regular(),
        ".mini-code-memory/.curator-delivery.lock": regular(b""),
        ".mini-code-memory/pipeline_state.json": regular(),
        ".mini-code-memory/memory.json": regular(),
        ".mini-code-memory/approval_audit.json": regular(),
        ".mini-code-memory/.curator-schedule.lock": regular(b""),
        ".mini-code-memory/MEMORY.md": regular(),
        ".mini-code-memory/.curator-execution.lock": regular(b""),
        ".mini-code-memory/.conversation-turn-state.lock": regular(b""),
        ".mini-code-memory/.conversation-commit.lock": regular(b""),
        ".mini-code-memory/project_facts.json": regular(),
        f".mini-code-memory/authority-blobs-v1/{blob[1]}.json": blob,
        ".mini-code-memory-local/memory.json": regular(),
    }
    assert changed_paths({}, after) == []
    nested = {"nested/" + path: entry for path, entry in after.items()}
    assert changed_paths({}, nested) == sorted(nested)


def test_lock_and_content_addressed_blob_must_match_their_contract():
    malformed = {
        ".minicode-memory-authority.lock": regular(b"not a lock"),
        ".mini-code-memory/.curator-delivery.lock": regular(b"source code"),
        f".mini-code-memory/authority-blobs-v1/{'0' * 64}.json": regular(),
        ".mini-code-memory/authority-blobs-v1/unknown.json": regular(),
        ".mini-code-memory/authority-blobs-v1/source.py": regular(),
    }
    assert changed_paths({}, malformed) == sorted(malformed)


@pytest.mark.parametrize(
    "path",
    [
        ".minicode-memory-authority.lock",
        ".mini-code-memory/memory.json",
        ".pytest_cache/README.md",
        "pkg/__pycache__/app.cpython-312.pyc",
    ],
)
@pytest.mark.parametrize("kind", ["symlink", "special"])
def test_links_and_special_entries_are_never_metadata(path, kind):
    entry = (kind, "/outside" if kind == "symlink" else 4096)
    assert not runtime_metadata(path, entry)
    assert changed_paths({}, {path: entry}) == [path]


@pytest.mark.parametrize(
    "path",
    [
        ".minicode-memory-authority.lock",
        ".mini-code-memory/memory.json",
        ".pytest_cache/README.md",
        "pkg/__pycache__/app.cpython-312.pyc",
    ],
)
def test_existing_artifact_changes_and_deletions_are_always_protected(path):
    before = {path: regular(b"before")}
    assert changed_paths(before, {path: regular(b"after")}) == [path]
    assert changed_paths(before, {}) == [path]
    assert changed_paths(before, {path: ("symlink", "/outside")}) == [path]


def test_pytest_only_exempts_its_fixed_cache_files():
    paths = {
        ".pytest_cache/.gitignore",
        ".pytest_cache/CACHEDIR.TAG",
        ".pytest_cache/README.md",
        ".pytest_cache/v/cache/nodeids",
        ".pytest_cache/v/cache/lastfailed",
        ".pytest_cache/v/cache/stepwise",
    }
    assert changed_paths({}, {p: regular() for p in paths}) == []
    for path in (".pytest_cache/custom", "nested/.pytest_cache/README.md"):
        assert changed_paths({}, {path: regular()}) == [path]


def test_only_bytecode_with_existing_regular_source_is_a_cache():
    before = {"pkg/app.py": regular(), "tests/test_app.py": regular()}
    generated = {
        "pkg/__pycache__/app.cpython-312.pyc": regular(),
        "pkg/__pycache__/app.cpython-312.opt-1.pyc": regular(),
        "tests/__pycache__/test_app.cpython-312-pytest-8.3.5.pyc": regular(),
    }
    assert changed_paths(before, {**before, **generated}) == []
    assert changed_paths({}, generated) == sorted(generated)
    for name in ("app.pyc", "app.cpython-312.py", "missing.cpython-312.pyc"):
        path = "pkg/__pycache__/" + name
        assert changed_paths(before, {**before, path: regular()}) == [path]
    path = "pkg/__pycache__/app.cpython-312.pyc"
    links = {"pkg/app.py": ("symlink", "/outside"), path: regular()}
    assert changed_paths({}, links) == sorted(links)


def test_parent_link_cannot_be_hidden_as_a_cache_directory():
    after = {"pkg/__pycache__": ("symlink", "/outside")}
    assert changed_paths({}, after) == ["pkg/__pycache__"]


@pytest.mark.parametrize(
    "path",
    [
        "/.minicode-memory-authority.lock",
        "./.minicode-memory-authority.lock",
        "../.minicode-memory-authority.lock",
        ".mini-code-memory//memory.json",
        ".mini-code-memory/../memory.json",
        ".mini-code-memory\\memory.json",
    ],
)
def test_noncanonical_or_traversal_paths_are_never_ignored(path):
    assert not runtime_metadata(path, regular(b""))
    assert changed_paths({}, {path: regular(b"")}) == [path]


def test_json_roundtrip_does_not_turn_unchanged_files_into_edits():
    before = {"app.py": regular(), "link": ("symlink", "app.py")}
    assert changed_paths(json.loads(json.dumps(before)), before) == []


def test_malformed_snapshot_entries_fail_closed():
    assert not runtime_metadata(".pytest_cache/README.md", None)
    assert not runtime_metadata(".pytest_cache/README.md", ("file", "not a digest"))
    assert changed_paths({}, {".pytest_cache/README.md": ("file", "invalid")}) == [
        ".pytest_cache/README.md"
    ]
