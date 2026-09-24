from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_authority_io import (
    is_memory_entry_id,
    parse_memory_authority_json,
    validate_memory_authority_value,
)
from minicode.memory_approval import MemoryApprovalAuthority
from minicode.memory_store import MemoryStoreConflict


@pytest.mark.parametrize(
    "value",
    [
        "\ud800",
        {"nested": ["valid", "\udfff"]},
        {"\ud800": "value"},
    ],
)
def test_authority_validator_rejects_strings_without_a_utf8_encoding(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="memory_authority_string_not_utf8"):
        validate_memory_authority_value(value)


@pytest.mark.parametrize(
    "source",
    [
        r'{"value":"\ud800"}',
        r'{"\udfff":"value"}',
    ],
)
def test_strict_authority_parser_rejects_escaped_lone_surrogates(
    source: str,
) -> None:
    with pytest.raises(ValueError, match="memory_authority_string_not_utf8"):
        parse_memory_authority_json(source)


def test_memory_id_predicates_are_total_for_non_utf8_strings() -> None:
    assert is_memory_entry_id("\ud800") is False
    assert MemoryApprovalAuthority._valid_memory_id("\ud800") is False


def test_manager_rejects_ambiguous_legacy_audit_projection(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    (scope_root / "approval_audit.json").write_text(
        '{"scope":"project","records":[],"records":[{}]}',
        encoding="utf-8",
    )

    with pytest.raises(
        MemoryStoreConflict,
        match="Memory approval audit is invalid",
    ):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )


def test_compatibility_audit_loader_rejects_ambiguous_memory_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir()
    data_root.mkdir()
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir()
    (scope_root / "memory.json").write_text(
        '{"entries":[],"approval_audit":[],"approval_audit":[{}]}',
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        manager._load_approval_audit(MemoryScope.PROJECT)


@pytest.mark.parametrize("error_type", [TypeError, RecursionError])
@pytest.mark.parametrize(
    ("loader_name", "filename", "expected_message"),
    [
        (
            "_load_approval_audit_projection",
            "approval_audit.json",
            "Memory approval audit is invalid",
        ),
        (
            "_load_approval_audit",
            "memory.json",
            "Memory authority is invalid",
        ),
    ],
)
def test_audit_loaders_map_strict_parser_failures_to_store_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    loader_name: str,
    filename: str,
    expected_message: str,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir()
    data_root.mkdir()
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir()
    (scope_root / filename).write_text("{}", encoding="utf-8")

    def fail_strict_parse(_source: str) -> object:
        raise error_type("synthetic strict parser failure")

    monkeypatch.setattr(
        memory_mod,
        "parse_memory_authority_json",
        fail_strict_parse,
    )

    loader = getattr(manager, loader_name)
    with pytest.raises(MemoryStoreConflict, match=expected_message):
        loader(MemoryScope.PROJECT)
