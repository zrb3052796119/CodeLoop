"""Strict content-free observations from independently executed verifiers."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any


VERIFICATION_EVENT_TYPE = "task.verified"
_FIELDS = frozenset(
    {"verificationVersion", "kind", "outcome", "source"}
)
_KINDS = frozenset({"tests", "build", "lint", "typecheck"})
_OUTCOMES = frozenset({"passed", "failed"})
_SOURCES = frozenset({"test_runner", "run_command_exit"})
_SHELL_METACHARACTERS = frozenset("|&;<>()$`\r\n")


def project_verification(
    *,
    kind: str,
    passed: bool,
    source: str,
) -> dict[str, object] | None:
    """Build one closed observation without command, output, or path content."""
    if (
        kind not in _KINDS
        or type(passed) is not bool
        or source not in _SOURCES
    ):
        return None
    return {
        "verificationVersion": 1,
        "kind": kind,
        "outcome": "passed" if passed else "failed",
        "source": source,
    }


def normalize_verification_payload(
    payload: object,
) -> dict[str, object] | None:
    """Accept only the exact public verification event contract."""
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None
    if payload.get("verificationVersion") != 1:
        return None
    if payload.get("kind") not in _KINDS:
        return None
    if payload.get("outcome") not in _OUTCOMES:
        return None
    if payload.get("source") not in _SOURCES:
        return None
    return {
        "verificationVersion": 1,
        "kind": payload["kind"],
        "outcome": payload["outcome"],
        "source": payload["source"],
    }


def normalize_tool_verification(
    tool_name: object,
    payload: object,
) -> dict[str, object] | None:
    """Require the marker source to match the Tool that actually returned it."""
    normalized = normalize_verification_payload(payload)
    if normalized is None:
        return None
    expected_tool = {
        "test_runner": "test_runner",
        "run_command_exit": "run_command",
    }.get(str(normalized["source"]))
    return normalized if tool_name == expected_tool else None


def project_command_verification(
    input_data: Mapping[str, Any],
    *,
    passed: bool,
) -> dict[str, object] | None:
    """Project an actual direct command exit into a closed verification kind."""
    if not isinstance(input_data, Mapping) or type(passed) is not bool:
        return None
    invocation = _direct_invocation(input_data)
    if invocation is None:
        return None
    command, args = invocation
    kind = _command_kind(command, args)
    if kind is None:
        return None
    return project_verification(
        kind=kind,
        passed=passed,
        source="run_command_exit",
    )


def _direct_invocation(
    input_data: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]] | None:
    raw_command = input_data.get("command")
    raw_args = input_data.get("args", [])
    if (
        not isinstance(raw_command, str)
        or not raw_command.strip()
        or not isinstance(raw_args, list)
        or any(not isinstance(item, str) for item in raw_args)
        or any(character in raw_command for character in _SHELL_METACHARACTERS)
    ):
        return None
    if raw_args:
        if raw_command != raw_command.strip() or any(
            character.isspace() for character in raw_command
        ):
            return None
        tokens = [raw_command, *raw_args]
    else:
        try:
            tokens = shlex.split(raw_command, posix=True)
        except ValueError:
            return None
    if not tokens or any("\x00" in token for token in tokens):
        return None
    command = tokens[0].casefold()
    args = tuple(token.casefold() for token in tokens[1:])
    return command, args


def _command_kind(command: str, args: tuple[str, ...]) -> str | None:
    if command == "pytest":
        return "tests"
    if command in {"python", "python3"}:
        if len(args) >= 2 and args[:2] in {
            ("-m", "pytest"),
            ("-m", "unittest"),
        }:
            return "tests"
        if len(args) >= 2 and args[:2] == ("-m", "compileall"):
            return "build"
        if len(args) >= 3 and args[:3] == ("-m", "ruff", "check"):
            return "lint"
        if len(args) >= 2 and args[:2] == ("-m", "mypy"):
            return "typecheck"
        return None
    if command == "npm":
        if args[:1] == ("test",):
            return "tests"
        if len(args) >= 2 and args[0] == "run":
            return _script_kind(args[1])
        return None
    if command == "npx" and args:
        return {
            "eslint": "lint",
            "jest": "tests",
            "tsc": "typecheck",
            "vitest": "tests",
        }.get(args[0])
    if command == "cargo" and args:
        return {
            "build": "build",
            "check": "build",
            "clippy": "lint",
            "test": "tests",
        }.get(args[0])
    if command == "go" and args:
        return {
            "build": "build",
            "test": "tests",
            "vet": "lint",
        }.get(args[0])
    if command == "make" and args:
        return _script_kind(args[0])
    if command == "cmake" and args[:1] == ("--build",):
        return "build"
    if command == "dotnet" and args:
        return {"build": "build", "test": "tests"}.get(args[0])
    if command == "ruff" and args[:1] == ("check",):
        return "lint"
    if command in {"mypy", "pyright"}:
        return "typecheck"
    return None


def _script_kind(script: str) -> str | None:
    return {
        "build": "build",
        "check": "typecheck",
        "lint": "lint",
        "test": "tests",
        "typecheck": "typecheck",
        "type-check": "typecheck",
    }.get(script)


__all__ = [
    "VERIFICATION_EVENT_TYPE",
    "normalize_verification_payload",
    "normalize_tool_verification",
    "project_command_verification",
    "project_verification",
]
