from __future__ import annotations

import pytest

from minicode.memory_path_identity import (
    complete_identity_occurs,
    extract_complete_path_identities,
    normalize_path_identity,
    path_identity_basename,
    redact_complete_paths,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (r"C:\repo\src\config.py", "C:/repo/src/config.py"),
        (r"\\server\share\src\config.py", "//server/share/src/config.py"),
        ("pyproject.toml", "pyproject.toml"),
        ("src/@scope/config.py", "src/@scope/config.py"),
        ("服务/配置.py", "服务/配置.py"),
    ),
)
def test_structured_path_identity_preserves_complete_cross_platform_names(
    raw: str,
    expected: str,
) -> None:
    assert normalize_path_identity(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("(src/a.py).", ("src/a.py",)),
        ("src/a.py).", ("src/a.py",)),
        ("src/a.py: do X", ("src/a.py",)),
        ("修改src/a.py的配置", ("src/a.py",)),
        ("检查 `服务/配置.py` 的逻辑", ("服务/配置.py",)),
        ("Inspect `src/file name.py`.", ()),
    ),
)
def test_free_text_scanner_preserves_complete_path_spans(
    text: str,
    expected: tuple[str, ...],
) -> None:
    assert extract_complete_path_identities(text) == expected


def test_apostrophes_in_prose_are_not_treated_as_path_quotes() -> None:
    text = "we'll inspect src/a.py after it's built"

    assert extract_complete_path_identities(text) == ("src/a.py",)
    redacted = redact_complete_paths(text)
    assert "we'll inspect" in redacted
    assert "after it's built" in redacted


def test_natural_language_quotation_is_not_redacted_as_one_path() -> None:
    text = 'He said "please disable retries in src/a.py now".'

    assert extract_complete_path_identities(text) == ("src/a.py",)
    redacted = redact_complete_paths(text)
    assert "disable retries" in redacted


def test_structured_path_with_spaces_remains_supported() -> None:
    assert normalize_path_identity("src/file name.py") == "src/file name.py"


@pytest.mark.parametrize(
    "text",
    (
        "Run `tools/bin/lint checkout`.",
        "Run `./bin/tool disable retries`.",
    ),
)
def test_ambiguous_path_shaped_command_preserves_operation_text(text: str) -> None:
    assert extract_complete_path_identities(text) == ()
    redacted = redact_complete_paths(text)
    assert "checkout" in redacted or "disable retries" in redacted


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (r"C:\repo\src\config.py", "config.py"),
        (r"\\server\share\src\config.py", "config.py"),
        ("src/@scope/config.py", "config.py"),
        ("pyproject.toml", "pyproject.toml"),
    ),
)
def test_cross_platform_basename_uses_shared_identity_rules(
    raw: str,
    expected: str,
) -> None:
    assert path_identity_basename(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected_path", "preserved"),
    (
        (
            "Run `pytest tests/unit/test_api.py -q`.",
            "tests/unit/test_api.py",
            "pytest",
        ),
        (
            "Use `python scripts/build.py --fast`.",
            "scripts/build.py",
            "python",
        ),
        ("Review `git diff src/a.py`.", "src/a.py", "git diff"),
    ),
)
def test_backtick_command_is_not_redacted_as_one_path(
    text: str,
    expected_path: str,
    preserved: str,
) -> None:
    assert extract_complete_path_identities(text) == (expected_path,)
    assert preserved in redact_complete_paths(text)


@pytest.mark.parametrize(
    ("text", "expected_paths", "preserved"),
    (
        ("Run `./scripts/check.sh --fast`.", (), "scripts/check.sh"),
        ("Run `tools/bin/lint src/a.py`.", ("src/a.py",), "tools/bin/lint"),
        (
            "Run `scripts/check.py tests/unit`.",
            ("tests/unit",),
            "scripts/check.py",
        ),
    ),
)
def test_path_shaped_executable_is_preserved_as_command_semantics(
    text: str,
    expected_paths: tuple[str, ...],
    preserved: str,
) -> None:
    assert extract_complete_path_identities(text) == expected_paths
    assert preserved in redact_complete_paths(text)


@pytest.mark.parametrize(
    "prefix",
    ("\u034f", "\u200d", "\ufe0f", "β", "💣"),
)
def test_unicode_marks_and_symbols_are_not_path_boundaries(prefix: str) -> None:
    assert extract_complete_path_identities(f"{prefix}src/a.py") == ()


@pytest.mark.parametrize(
    "text",
    ("name(src/a.py)", "evil[src/a.py]", "foo`src/a.py`"),
)
def test_glued_wrappers_are_not_path_boundaries(text: str) -> None:
    assert extract_complete_path_identities(text) == ()
    assert not complete_identity_occurs("src/a.py", text)


def test_ambiguous_unquoted_unicode_path_is_not_cut_out_of_cjk_prose() -> None:
    text = "检查源码/配置.py的逻辑"

    assert extract_complete_path_identities(text) == ()
    assert redact_complete_paths(text) == text


@pytest.mark.parametrize(
    "suffix",
    (
        "\u034f",
        "\ufe0f",
        "\u20dd",
        "\u0338",
        "\u200dbackup",
        "#backup",
        ":backup",
        ",backup",
        ".bak",
        "/hidden.py",
        "备份",
        "β",
        "２",
    ),
)
def test_glued_suffix_never_completes_a_path_identity(suffix: str) -> None:
    assert not complete_identity_occurs("src/config.py", f"src/config.py{suffix}")


@pytest.mark.parametrize(
    "text",
    (
        "src/config.py",
        "Review src/config.py now.",
        "Review (src/config.py).",
        "Review `src/config.py`.",
    ),
)
def test_real_token_boundaries_complete_a_path_identity(text: str) -> None:
    assert complete_identity_occurs("src/config.py", text)


@pytest.mark.parametrize(
    "text",
    (
        "./src/a.py",
        r"src\a.py",
        "Review (src/a.py).",
        "Review `src/a.py`.",
        r"C:\repo\src\a.py",
        r"\\server\share\src\a.py",
    ),
)
def test_every_extracted_identity_is_matchable_on_the_same_surface(text: str) -> None:
    identities = extract_complete_path_identities(text)

    assert identities
    assert all(complete_identity_occurs(identity, text) for identity in identities)
