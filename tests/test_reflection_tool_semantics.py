from __future__ import annotations

import ast
from pathlib import Path

from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_evidence import (
    TOOL_SEMANTICS,
    extract_tool_file_roles,
    tool_semantic,
)
from minicode.tools import create_default_tool_registry


def test_registered_directory_and_file_readers_preserve_file_roles(tmp_path) -> None:
    registry = create_default_tool_registry(
        str(tmp_path),
        include_mcp=False,
        include_user_interaction=False,
    )
    registered = set(registry.list_all())
    reader_paths = {
        "list_files": "src",
        "file_tree": "src",
        "read_file": "src/app.py",
        "file_line_count": "src/app.py",
    }
    assert set(reader_paths) <= registered

    for tool_name, path in sorted(reader_paths.items()):
        roles = extract_tool_file_roles(
            tool_name,
            {"path": path},
            event_type="tool_call",
        )
        assert roles == {
            "files_read": [path],
            "files_changed": [],
            "referenced_files": [],
        }, tool_name


def test_registered_directory_readers_are_routine_listing_work() -> None:
    for tool_name in ("list_files", "file_tree"):
        result = ReflectionEngine().reflect(
            "Inspect the source tree",
            [
                {
                    "event_id": "event-1",
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "input": {"path": "src"},
                },
                {"event_id": "event-2", "type": "task_result", "status": "success"},
            ],
        )

        assert "routine_directory_listing" in result.value_decision.reason_codes


def test_historical_tool_aliases_are_explicit_and_not_registry_names(tmp_path) -> None:
    registry = create_default_tool_registry(
        str(tmp_path),
        include_mcp=False,
        include_user_interaction=False,
    )
    registered = set(registry.list_all())
    expected_aliases = {
        "search_files": "grep_files",
        "list_directory": "list_files",
        "directory_tree": "file_tree",
        "modify_file": "write_file",
        "execute_command": "run_command",
    }

    for alias, canonical_name in expected_aliases.items():
        semantic = tool_semantic(alias)
        assert semantic is not None
        assert semantic.legacy_alias is True
        assert semantic.canonical_name == canonical_name
        assert alias not in registered

    for current_name in ("grep_files", "list_files", "file_tree", "run_command"):
        assert TOOL_SEMANTICS[current_name].legacy_alias is False


def test_historical_command_alias_preserves_referenced_file_role() -> None:
    assert extract_tool_file_roles(
        "pytest",
        {"path": "tests/test_checkout.py"},
        event_type="tool_call",
    ) == {
        "files_read": [],
        "files_changed": [],
        "referenced_files": ["tests/test_checkout.py"],
    }


def test_known_and_unknown_tool_mix_is_not_classified_as_routine_listing() -> None:
    result = ReflectionEngine().reflect(
        "Inspect with a future tool",
        [
            {"event_id": "event-1", "type": "tool_call", "tool_name": "list_files"},
            {"event_id": "event-2", "type": "tool_call", "tool_name": "future_tool"},
            {"event_id": "event-3", "type": "task_result", "status": "success"},
        ],
    )

    assert "routine_directory_listing" not in result.value_decision.reason_codes


def test_production_modules_cannot_bypass_memory_pipeline_prompt_ownership() -> None:
    package_root = Path(__file__).resolve().parents[1] / "minicode"
    definition_modules = {
        package_root / "memory.py",
        package_root / "memory_injector.py",
    }
    forbidden_symbols = {"MemoryInjector", "inject_memory_into_prompt"}
    violations: list[str] = []

    for path in sorted(package_root.rglob("*.py")):
        if path in definition_modules:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                for symbol in sorted(imported & forbidden_symbols):
                    violations.append(f"{path.relative_to(package_root)}:{node.lineno}:import {symbol}")
            elif isinstance(node, ast.Call):
                called_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if called_name in forbidden_symbols:
                    violations.append(
                        f"{path.relative_to(package_root)}:{node.lineno}:call {called_name}"
                    )

    assert violations == []
