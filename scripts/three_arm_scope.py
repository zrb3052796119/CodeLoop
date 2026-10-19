"""Identical evaluator-owned command/read boundary for all three trial arms.

Only the frozen case verifier or the new regression-test file may be executed.
This prevents direct command/path access to external answers; it is not an OS
sandbox for arbitrary code contained in an authorized Python test.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Mapping, Sequence


REGRESSION_TEST = "tests/test_eval_regression.py"
_SHELL_CONTROL = re.compile(r"[;&|<>`$\n\r\x00]")
_READ_TOOLS = frozenset({"read_file", "list_files", "file_tree", "grep_files", "file_line_count", "find_symbols", "find_references"})


class ScopeDenied(PermissionError):
    """A request exceeds the published evaluation command/path scope."""


def workspace_path(workspace: str | Path, value: str, *, recursive=False) -> Path:
    """Resolve against the actual context/snapshot root, including symlinks."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or not isinstance(value, str) or not value or "\x00" in value:
        raise ScopeDenied("invalid workspace path")
    candidate = Path(value)
    if ".." in candidate.parts or value.startswith("~"):
        raise ScopeDenied("path traversal is outside evaluation scope")
    try:
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        target.relative_to(root)
        if recursive and target.is_dir():
            # grep reads file symlinks, and file_tree follows directory links.
            # Follow only checked internal links and visit each physical
            # directory once, including linked descendants and hidden files.
            pending, visited = [target], set()
            while pending:
                directory = pending.pop()
                if directory in visited:
                    continue
                visited.add(directory)
                for child in directory.iterdir():
                    resolved = child.resolve()
                    resolved.relative_to(root)
                    if resolved.is_dir():
                        pending.append(resolved)
    except (OSError, RuntimeError, ValueError) as error:
        raise ScopeDenied("path or symlink escapes evaluation workspace") from error
    return target


def _split(command: str | Sequence[str], args: Sequence[str] | None) -> tuple[str, ...]:
    if isinstance(command, str):
        if _SHELL_CONTROL.search(command):
            raise ScopeDenied("shell operators and expansions are not allowed")
        try:
            tokens = (command, *args) if args else tuple(shlex.split(command))
        except ValueError as error:
            raise ScopeDenied("invalid command quoting") from error
    elif isinstance(command, (list, tuple)) and args is None:
        tokens = tuple(command)
    else:
        raise ScopeDenied("command must be an argv or a simple command string")
    if not tokens or any(
        not isinstance(token, str) or not token or _SHELL_CONTROL.search(token)
        for token in tokens
    ):
        raise ScopeDenied("invalid command token or shell operator")
    return tokens


def _pytest_argv(tokens: tuple[str, ...], workspace: str | Path):
    executable, *arguments = tokens
    trusted_python = executable in {"python", "python3", sys.executable}
    if Path(executable).is_absolute():
        trusted_python = Path(executable).resolve() == Path(sys.executable).resolve()
    if trusted_python and arguments[:2] == ["-m", "pytest"]:
        arguments = arguments[2:]
    elif executable != "pytest":
        raise ScopeDenied("only the published pytest commands are allowed")
    normalized, signature, targets = [], [], []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if re.fullmatch(r"-[qv]{1,3}", token):
            normalized.append(token)  # Output verbosity does not change tests.
        elif token == "-k" and index + 1 < len(arguments):
            expression = arguments[index + 1]
            normalized.extend((token, expression))
            signature.extend((token, expression))
            index += 1
        else:
            if token.startswith(("-", "@")) or any(c in token for c in "*?[]") or "::" in token:
                raise ScopeDenied("extra options, plugins, configs and selectors are not allowed")
            target = workspace_path(workspace, token)
            if target.suffix != ".py":
                raise ScopeDenied("verification targets must be explicit Python test files")
            relative = target.relative_to(Path(workspace).resolve()).as_posix()
            normalized.append(relative)
            signature.append(relative)
            targets.append(relative)
        index += 1
    if not targets:
        raise ScopeDenied("test discovery outside the frozen target list is not allowed")
    return (sys.executable, "-m", "pytest", *normalized), tuple(signature), tuple(targets)


@dataclass(frozen=True)
class VerificationScope:
    verification_argv: Sequence[str]

    def __post_init__(self):
        object.__setattr__(self, "verification_argv", _split(self.verification_argv, None))

    def require_command(
        self, command_or_argv: str | Sequence[str], *, workspace: str | Path,
        args: Sequence[str] | None = None, cwd: str | None = None,
    ) -> tuple[str, ...]:
        """Return trusted executable argv or raise; never execute a shell."""
        root = Path(workspace).resolve(strict=True)
        if cwd is not None and workspace_path(root, cwd) != root:
            raise ScopeDenied("verification cwd must be the context workspace root")
        allowed = _pytest_argv(_split(self.verification_argv, None), root)[1]
        actual, signature, _targets = _pytest_argv(_split(command_or_argv, args), root)
        if signature not in {allowed, (REGRESSION_TEST,)}:
            raise ScopeDenied("use the exact published verification command or pytest tests/test_eval_regression.py")
        return actual

    def test_runner_argv(self, input_data: Mapping, *, workspace: str | Path) -> tuple[str, ...]:
        """Replace test_runner's broad discovery with one published command.

        The caller should display/execute this argv through its guarded runner;
        do not call production test_runner afterward (it constructs another argv).
        """
        if input_data.get("framework", "auto") not in {"auto", "pytest"}:
            raise ScopeDenied("use run_command with the published pytest command")
        if input_data.get("coverage") or input_data.get("pattern"):
            raise ScopeDenied("coverage/pattern overrides are outside the frozen verifier")
        root = Path(workspace).resolve(strict=True)
        requested = workspace_path(root, input_data.get("path", "."))
        relative = requested.relative_to(root).as_posix()
        frozen, _signature, targets = _pytest_argv(_split(self.verification_argv, None), root)
        if relative == REGRESSION_TEST:
            return self.require_command(("pytest", "-q", REGRESSION_TEST), workspace=root)
        if relative in {".", "tests", *targets}:
            return self.require_command(frozen, workspace=root)
        raise ScopeDenied("test_runner target is not in the published verifier; use run_command")

    def require_verification(self, checks, *, workspace: str | Path) -> None:
        """Call at DeterministicVerifier.run using its actual copied-tree cwd."""
        if not checks:
            raise ScopeDenied("verification checks are required")
        for check in checks:
            argv = check.get("argv") if isinstance(check, Mapping) else getattr(check, "argv", None)
            self.require_command(argv, workspace=workspace)

    def require_tool_read(self, tool_name: str, input_data: Mapping, *, workspace: str | Path) -> None:
        if tool_name in _READ_TOOLS:
            workspace_path(
                workspace, input_data.get("path", "."),
                recursive=tool_name in {"grep_files", "file_tree", "list_files", "find_symbols", "find_references"},
            )
        elif tool_name == "get_ast_info":
            workspace_path(workspace, input_data.get("file_path"))
        elif tool_name == "diff_viewer":
            for entry in input_data.get("files", []):
                if entry.get("before") == "__file__" or entry.get("after") == "__file__":
                    workspace_path(workspace, entry.get("path"))


def verification_environment(base: Mapping[str, str], *, workspace: str | Path) -> dict[str, str]:
    """Host-owned pytest settings: no external plugin/env/config discovery.

    Local conftest.py and the frozen workspace pyproject.toml remain available.
    The fallback /dev/null is an evaluator-owned empty configuration, never an
    input path accepted from the model. HOME is deliberately left untouched.
    """
    root = Path(workspace).resolve(strict=True)
    result = {
        key: value for key, value in base.items()
        if key not in {
            "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONSTARTUP",
            "PYTHONINSPECT", "PYTHONUSERBASE", "PYTHONHOME",
        }
    }
    config = workspace_path(root, "pyproject.toml")
    config_path = str(config) if config.is_file() else os.devnull
    result.update({
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_ADDOPTS": shlex.join(("-c", config_path, f"--rootdir={root}", f"--confcutdir={root}")),
    })
    return result
