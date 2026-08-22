from minicode.recovery_control import action_fingerprint


def _call(command: str, args: list[str]) -> dict:
    return {
        "toolName": "run_command",
        "input": {"command": command, "args": args},
    }


def test_nested_shell_wrapper_and_direct_shell_wrapper_share_fingerprint() -> None:
    nested = _call(
        "/bin/zsh",
        ["-lc", 'bash -lc "cd . && ruff check src"'],
    )
    direct_wrapper = _call("bash", ["-lc", "ruff check src"])

    assert action_fingerprint(nested) == action_fingerprint(direct_wrapper)


def test_raw_shell_snippet_matches_explicit_wrapper_shape() -> None:
    raw_snippet = _call("cd . && ruff check src", [])
    explicit_wrapper = _call("bash", ["-lc", "ruff check src"])

    assert action_fingerprint(raw_snippet) == action_fingerprint(explicit_wrapper)


def test_materially_different_shell_payload_is_not_suppressed() -> None:
    lint = _call("bash", ["-lc", "ruff check src"])
    tests = _call("bash", ["-lc", "pytest tests/test_runtime.py"])

    assert action_fingerprint(lint) != action_fingerprint(tests)


def test_direct_argv_recovery_is_distinct_from_shell_wrapper() -> None:
    wrapper = _call("bash", ["-lc", "python -m unittest tests.test_runtime"])
    direct = _call(
        "python",
        ["-m", "unittest", "tests.test_runtime"],
    )

    assert action_fingerprint(wrapper) != action_fingerprint(direct)
