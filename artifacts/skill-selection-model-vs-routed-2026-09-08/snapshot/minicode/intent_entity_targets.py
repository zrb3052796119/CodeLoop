"""Literal file names and bounded references, independent of action authority.

Callers must mask quoted/protected prose before extracting ordinary targets.
These helpers return entity candidates only; they never infer an operation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence


_FILE_EXTENSIONS = (
    r"py|pyi|js|mjs|cjs|ts|jsx|tsx|java|go|rs|cpp|cc|c|h|hpp|"
    r"md|mdx|rst|txt|json|jsonl|yaml|yml|toml|ini|cfg|conf|"
    r"log|csv|tsv|sql|sh|bash|zsh|ps1|bat|html|htm|css|scss|"
    r"svg|png|jpe?g|gif|webp|ico|xml|lock|ipynb|env"
)
_DOT_FILES = (
    r"\.(?:coveragerc|gitignore|gitattributes|dockerignore|editorconfig|"
    r"npmrc|yarnrc|pypirc|prettierrc|babelrc|eslintrc|env(?:\.[A-Za-z0-9_-]+)?)"
)
# ASCII tokens may touch Chinese prose. Unicode names remain supported when
# separated from prose, or when an ASCII directory establishes the path start.
# Require a known extension or dotfile rather than treating arbitrary dotted
# prose, version numbers or hostnames as files.
FILE_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9_./\\-])("
    rf"(?:[A-Za-z0-9_./\\-]+\.(?:{_FILE_EXTENSIONS})|"
    rf"[A-Za-z0-9_.-]+[/\\][\w/\\._-]+\.(?:{_FILE_EXTENSIONS})|"
    rf"(?<=[\s`'\"“‘（(])[\w/\\._-]+\.(?:{_FILE_EXTENSIONS})|"
    rf"[^\W\x00-\x7f]+(?:[_-][\w-]+)?\.(?:{_FILE_EXTENSIONS})|"
    rf"(?:[A-Za-z0-9_./\\-]+[/\\])?{_DOT_FILES})"
    rf")(?![A-Za-z0-9_/\\-]|\.[A-Za-z0-9_])",
    re.IGNORECASE,
)

_REFERENCE_SWITCH_RE = re.compile(
    r"(?:换到|切到|转到|另一个任务|另一个问题|先别管|别管|不是(?:它|这个|那个))|"
    r"\b(?:switch\s+to|different\s+(?:task|issue)|not\s+(?:it|that))\b",
    re.IGNORECASE,
)
_FILE_ANAPHOR_RE = re.compile(
    r"(?:这|那)(?:一)?(?:份|个|段|条)?(?:文件|文档|日志|快照|导出(?:快照|文件)|配置)|"
    r"(?:该|上述|前述)(?:文件|文档|日志|快照|配置)|"
    r"(?:这|那)(?:一)?(?:行|段)|"
    r"\b(?:this|that|the\s+same)\s+(?:file|document|log|snapshot|line|paragraph)\b",
    re.IGNORECASE,
)
_ORDINAL_FILE_RE = re.compile(
    r"(?P<latter>后一个文件|后者|\blatter\s+file\b)|"
    r"(?P<former>前一个文件|前者|\bformer\s+file\b)|"
    r"(?P<first>第一个文件|\bfirst\s+file\b)|"
    r"(?P<second>第二个文件|\bsecond\s+file\b)|"
    r"(?P<last>最后一个文件|\blast\s+file\b)",
    re.IGNORECASE,
)
_TEST_EXTENSION_RE = re.compile(
    r"^\s*(?:请|帮我)?\s*(?:(?:再|另外|额外)\s*(?:加|补|添加|增加)|追加)"
    r"[^。！？;；\n]{0,80}(?:测试|用例|测例)|"
    r"^\s*(?:please\s+)?(?:add|include)\s+(?:another|one\s+more|an?\s+additional)"
    r"\b[^.!?;\n]{0,80}\b(?:test|case)\b",
    re.IGNORECASE,
)
_TEST_REQUEST_RE = re.compile(
    r"(?:添加|增加|新增|补充|编写|创建|生成|加|补|写)"
    r"[^。！？;；\n]{0,64}(?:测试|用例|测例)|"
    r"\b(?:add|write|create|implement|generate|extend|include)\b"
    r"[^.!?;\n]{0,64}\b(?:tests?|cases?)\b",
    re.IGNORECASE,
)
_TEST_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:请|帮我|麻烦)(?:先)?\s*)?(?:给|为|针对|对|在)\s*$|"
    r"^\s*(?:please\s+)?(?:add|write|create|implement|generate|extend|include)\s+"
    r"(?:(?:some|more|new|additional|unit|regression|boundary)\s+)*"
    r"(?:tests?|test\s+cases?)\s+(?:for|in|to)\s*(?:the\s+)?$|"
    r"^\s*(?:please\s+)?in\s+(?:the\s+)?$",
    re.IGNORECASE,
)
_FILE_LIST_SEPARATOR_RE = re.compile(
    r"^\s*(?:和|与|及|、|,|，|\band\b|\bor\b)\s*$", re.IGNORECASE
)


def extract_file_tokens(text: str) -> tuple[str, ...]:
    """Return visible file literals in source order without duplicate names."""

    return tuple(
        dict.fromkeys(match.group(1) for match in FILE_TOKEN_RE.finditer(text))
    )


def has_file_reference(text: str, *, test_extension: bool = False) -> bool:
    """Whether this text contains one of the bounded reference grammars."""

    return bool(
        _FILE_ANAPHOR_RE.search(text)
        or _ORDINAL_FILE_RE.search(text)
        or (test_extension and _TEST_EXTENSION_RE.search(text))
    )


def explicit_test_subject_files(text: str) -> tuple[str, ...]:
    """Return files explicitly targeted by an earlier test-writing request.

    A filename elsewhere in the same turn (for example an old result log)
    cannot supply a new test write target. The caller masks protected prose
    first. Unknown target syntax deliberately abstains.
    """

    targets: list[str] = []
    for fragment in re.split(r"[。！？;；\n]|(?<=[.!?])\s+", text):
        if not _TEST_REQUEST_RE.search(fragment):
            continue
        matches = tuple(FILE_TOKEN_RE.finditer(fragment))
        for index, match in enumerate(matches):
            if not _TEST_SUBJECT_PREFIX_RE.search(fragment[: match.start()]):
                continue
            targets.append(match.group(1))
            previous = match
            for following in matches[index + 1 :]:
                if not _FILE_LIST_SEPARATOR_RE.fullmatch(
                    fragment[previous.end() : following.start()]
                ):
                    break
                targets.append(following.group(1))
                previous = following
    return tuple(dict.fromkeys(targets))


def select_referenced_files(
    text: str,
    candidates: Sequence[str],
    *,
    test_extension: bool = False,
) -> tuple[str, ...]:
    """Resolve a narrow current reference without selecting an ambiguous file.

    ``test_extension`` is permission to resolve a test *target*, not to create
    a test operation. The caller must already have a current test operation
    and a compatible, safe test context before enabling it.
    """

    files = tuple(dict.fromkeys(str(candidate) for candidate in candidates))
    if not files or len(text) > 240 or extract_file_tokens(text):
        return ()
    if _REFERENCE_SWITCH_RE.search(text):
        return ()
    positional = tuple(_ORDINAL_FILE_RE.finditer(text))
    if positional:
        if len(positional) != 1:
            return ()
        reference = positional[0]
        # Relative former/latter references require an actual pair, whereas
        # an explicit first/second/last index is defined for longer lists.
        if reference.group("latter") or reference.group("former"):
            if len(files) != 2:
                return ()
            return (files[1] if reference.group("latter") else files[0],)
        if reference.group("second"):
            return (files[1],) if len(files) >= 2 else ()
        return (files[-1] if reference.group("last") else files[0],)
    if len(files) != 1:
        return ()
    if _FILE_ANAPHOR_RE.search(text):
        return files
    if test_extension and _TEST_EXTENSION_RE.search(text):
        return files
    return ()
