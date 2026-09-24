"""Content-free safety checks for scheduler-owned verification argv."""

from __future__ import annotations

import re


_SHELL_METACHARACTERS = frozenset("|&;<>()$`\r\n")
_EXTERNAL_LOCATION_OPTIONS = frozenset(
    {
        "-c",
        "-f",
        "-p",
        "--config",
        "--confcutdir",
        "--file",
        "--makefile",
        "--prefix",
        "--pyargs",
        "--rootdir",
        "--basetemp",
        "--workspace",
    }
)
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_NO_OP_OPTIONS = frozenset(
    {
        "--collect-only",
        "--co",
        "--dry-run",
        "--fixtures",
        "--fixtures-per-test",
        "--help",
        "--if-present",
        "--markers",
        "--no-run",
        "--setup-plan",
        "--trace-config",
        "--version",
    }
)
_MAKE_DRY_RUN_OPTIONS = frozenset({"-n", "--just-print", "--recon"})


def _targets_with_closed_options(
    args: tuple[str, ...],
    *,
    exact_options: frozenset[str],
    value_option_prefixes: tuple[str, ...] = (),
) -> bool:
    return all(
        not token.startswith("-")
        or token in exact_options
        or any(token.startswith(prefix) for prefix in value_option_prefixes)
        for token in args
    )


def _matches_closed_verifier_grammar(argv: tuple[str, ...]) -> bool:
    """Recognize commands that necessarily execute a check, not list/plan it."""

    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    args = argv[1:]
    family = executable
    if executable in {"python", "python3"}:
        if len(args) < 2 or args[0] != "-m":
            return False
        module = args[1].casefold()
        remaining = args[2:]
        if module == "ruff" and remaining[:1] == ("check",):
            family, args = "ruff", remaining
        elif module in {"pytest", "unittest", "mypy", "compileall"}:
            family, args = module, remaining
        else:
            return False

    if family == "pytest":
        return _targets_with_closed_options(
            args,
            exact_options=frozenset(
                {
                    "-q",
                    "-qq",
                    "-s",
                    "-v",
                    "-vv",
                    "-x",
                    "--disable-warnings",
                    "--strict-config",
                    "--strict-markers",
                }
            ),
            value_option_prefixes=("--color=", "--maxfail=", "--tb="),
        )
    if family == "unittest":
        return _targets_with_closed_options(
            args,
            exact_options=frozenset({"-b", "-f", "-q", "-v"}),
        )
    if family == "ruff":
        return args[:1] == ("check",) and _targets_with_closed_options(
            args[1:],
            exact_options=frozenset({"-q", "--no-cache", "--quiet"}),
        )
    if family == "mypy":
        return _targets_with_closed_options(
            args,
            exact_options=frozenset(
                {
                    "--no-error-summary",
                    "--no-pretty",
                    "--pretty",
                    "--show-error-codes",
                    "--strict",
                }
            ),
        )
    if family == "pyright":
        return _targets_with_closed_options(
            args,
            exact_options=frozenset({"--stats", "--verbose", "--warnings"}),
        )
    if family == "compileall":
        return any(not token.startswith("-") for token in args) and (
            _targets_with_closed_options(
                args,
                exact_options=frozenset({"-f", "-q"}),
            )
        )
    if family == "npm":
        return args in {
            ("test",),
            ("run", "build"),
            ("run", "check"),
            ("run", "lint"),
            ("run", "test"),
            ("run", "type-check"),
            ("run", "typecheck"),
        }
    if family == "cargo":
        return args in {("build",), ("check",), ("clippy",), ("test",)}
    if family == "go":
        return bool(args) and args[0] in {"build", "test", "vet"} and all(
            not token.startswith("-") for token in args[1:]
        )
    if family == "make":
        return len(args) == 1 and args[0] in {
            "build",
            "check",
            "lint",
            "test",
            "type-check",
            "typecheck",
        }
    if family == "dotnet":
        return (
            bool(args)
            and args[0] in {"build", "test"}
            and len(args) <= 2
            and all(not token.startswith("-") for token in args[1:])
        )
    if family == "cmake":
        return args == ("--build", ".")
    return False


def verification_argv_is_safe(argv: object) -> bool:
    """Return whether argv cannot select a shell or external code location."""
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or len(argv) > 100
        or any(
            not isinstance(token, str)
            or not token
            or "\x00" in token
            or any(character in token for character in _SHELL_METACHARACTERS)
            for token in argv
        )
        or any(character.isspace() for character in argv[0])
        or "/" in argv[0]
        or "\\" in argv[0]
    ):
        return False
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for index, token in enumerate(argv):
        option, separator, option_value = token.partition("=")
        folded_option = option.casefold()
        if index > 0 and (
            folded_option in _EXTERNAL_LOCATION_OPTIONS
            or option in {"-h", "-V"}
            or folded_option in _NO_OP_OPTIONS
            or (
                executable in {"make", "gmake"}
                and folded_option in _MAKE_DRY_RUN_OPTIONS
            )
        ):
            return False
        candidate = option_value if separator else token
        normalized = candidate.replace("\\", "/")
        node_path = normalized.split("::", 1)[0]
        if (
            node_path.startswith(("/", "~"))
            or node_path.startswith("@")
            or _WINDOWS_ABSOLUTE_RE.match(candidate) is not None
            or _URI_RE.match(node_path) is not None
            or ".." in node_path.split("/")
            or node_path.casefold().startswith("file:")
        ):
            return False
    return _matches_closed_verifier_grammar(tuple(argv))


__all__ = ["verification_argv_is_safe"]
