"""Complete path-identity parsing shared by memory recall and admission."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class PathIdentitySpan:
    start: int
    end: int
    identity: str


_QUOTED_PATH_RE = re.compile(
    r"(?<![\w])(?P<quote>[`'\"])(?P<body>[^`'\"\r\n]*?)(?P=quote)(?![\w])"
)
_ASCII_PATH_RE = re.compile(
    r"(?:[/\\]{2}|[A-Za-z]:[/\\]|(?:\.\.?[/\\])|[/\\]|"
    r"[A-Za-z0-9_@.+-]+[/\\])"
    r"(?:[A-Za-z0-9_@.+-]+[/\\])*[A-Za-z0-9_@.+-]+"
)
_TOKEN_RE = re.compile(r"\S+")
_LEADING_WRAPPERS = "([{<"
_TRAILING_WRAPPERS = ")]}>"
_TRAILING_SENTENCE_PUNCTUATION = ",;:!?，。；：！？"


def _normalized_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def normalize_path_identity(
    value: object,
    *,
    max_chars: int = 256,
    allow_spaces: bool = True,
    require_separator: bool = False,
) -> str:
    """Return one complete normalized path or ``""`` when identity is unsafe."""

    text = _normalized_text(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    if (
        not text
        or len(text) > max_chars
        or "://" in text
        or (require_separator and "/" not in text)
    ):
        return ""
    for index, character in enumerate(text):
        category = unicodedata.category(character)
        if category.startswith("C"):
            return ""
        if character == ":":
            if index != 1 or not text[0].isascii() or not text[0].isalpha():
                return ""
            continue
        if character.isspace():
            if allow_spaces and character == " ":
                continue
            return ""
        if (
            category[0] in {"L", "M", "N"}
            or character in "/._-@+"
        ):
            continue
        return ""
    if text.startswith("//"):
        components = text[2:].split("/")
        if len(components) < 3:
            return ""
    elif len(text) > 1 and text[1] == ":":
        # A drive identity must be absolute.  ``C:repo/file.py`` is relative
        # to the drive's current directory and therefore is not a stable file
        # identity for deterministic retrieval evidence.
        if len(text) < 4 or text[2] != "/":
            return ""
        components = text[3:].split("/")
    elif text.startswith("/"):
        components = text[1:].split("/")
    else:
        components = text.split("/")
    if any(not component for component in components):
        return ""
    return text


def path_identity_basename(value: object) -> str:
    """Return the basename of one already-complete cross-platform identity."""

    normalized = normalize_path_identity(value)
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def _trim_unquoted_token(
    token: str,
    start: int,
) -> tuple[str, int, int]:
    previous = ""
    while token and token != previous:
        previous = token
        while token and token[0] in _LEADING_WRAPPERS:
            token = token[1:]
            start += 1
        while token and token[-1] in _TRAILING_WRAPPERS:
            token = token[:-1]
        while token and token[-1] in _TRAILING_SENTENCE_PUNCTUATION:
            token = token[:-1]
        # A final ASCII period is prose punctuation; an internal period remains
        # part of the filename and is validated as identity.
        while token.endswith("."):
            token = token[:-1]
    return token, start, start + len(token)


def complete_path_spans(
    value: object,
    *,
    max_text_chars: int = 4_000,
    max_path_chars: int = 256,
) -> tuple[PathIdentitySpan, ...]:
    """Extract complete path spans without restarting after an invalid prefix."""

    text = _normalized_text(value)
    if not text or len(text) > max_text_chars:
        return ()
    spans: list[PathIdentitySpan] = []
    quoted_ranges: list[tuple[int, int]] = []
    command_prefix_ranges: list[tuple[int, int]] = []
    for match in _QUOTED_PATH_RE.finditer(text):
        body = match.group("body")
        whitespace_indexes = [
            index for index, character in enumerate(body) if character.isspace()
        ]
        if whitespace_indexes and match.group("quote") == "`":
            # A code span containing spaces is ambiguous between a path with
            # spaces and a command plus arguments.  Never erase the full span:
            # doing so could remove decisive operation or polarity text such
            # as ``./bin/tool disable retries``.  Treat the first token as a
            # possible executable and let the bounded ASCII scanner discover
            # only later, independently delimited path arguments.  Paths with
            # spaces remain supported by structured metadata, where the value
            # is unambiguously one identity.
            parts = body.split()
            first = parts[0] if parts else ""
            if first:
                command_prefix_ranges.append(
                    (match.start("body"), match.start("body") + len(first))
                )
            continue
        if whitespace_indexes:
            # Natural-language quotations frequently contain a path.  Treating
            # the whole quotation as one filename would erase action and
            # polarity evidence during redaction. The inner scanner may still
            # recover independently delimited, unambiguous path tokens.
            continue
        quoted_ranges.append(match.span())
        identity = normalize_path_identity(
            body,
            max_chars=max_path_chars,
            allow_spaces=True,
            require_separator=True,
        )
        if identity:
            spans.append(PathIdentitySpan(match.start(), match.end(), identity))

    ascii_ranges: list[tuple[int, int]] = []
    for match in _ASCII_PATH_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in quoted_ranges):
            continue
        if any(
            match.start() < end and match.end() > start
            for start, end in command_prefix_ranges
        ):
            continue
        token, start, end = _trim_unquoted_token(match.group(0), match.start())
        identity = normalize_path_identity(
            token,
            max_chars=max_path_chars,
            allow_spaces=False,
            require_separator=True,
        )
        if not identity:
            continue
        left_is_safe = _safe_left_boundary(
            text,
            start,
            basename=False,
        ) or (start > 0 and "\u3400" <= text[start - 1] <= "\u9fff")
        right_is_safe = _safe_right_boundary(text, end) or text.startswith("的", end)
        if not left_is_safe or not right_is_safe:
            continue
        ascii_ranges.append((start, end))
        spans.append(PathIdentitySpan(start, end, identity))

    for match in _TOKEN_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in quoted_ranges):
            continue
        if any(match.start() < end and match.end() > start for start, end in ascii_ranges):
            continue
        token, start, end = _trim_unquoted_token(match.group(0), match.start())
        # ASCII paths are handled by the bounded scanner above.  This fallback
        # is only for an isolated Unicode path token.  Requiring a terminal
        # file extension prevents adjacent CJK prose such as
        # ``源码/配置.py的逻辑`` from becoming part of the identity; callers can
        # quote extensionless or otherwise ambiguous Unicode paths.
        non_ascii = tuple(character for character in token if not character.isascii())
        if (
            not non_ascii
            or any(not ("\u3400" <= character <= "\u9fff") for character in non_ascii)
            or not re.search(r"\.[A-Za-z0-9]{1,16}$", token)
        ):
            continue
        identity = normalize_path_identity(
            token,
            max_chars=max_path_chars,
            allow_spaces=False,
            require_separator=True,
        )
        if identity:
            spans.append(PathIdentitySpan(start, end, identity))

    spans.sort(key=lambda item: (item.start, item.end, item.identity))
    return tuple(spans)


def extract_complete_path_identities(
    value: object,
    *,
    max_text_chars: int = 4_000,
    max_path_chars: int = 256,
) -> tuple[str, ...]:
    """Return unique normalized path identities in source order."""

    return tuple(
        dict.fromkeys(
            span.identity
            for span in complete_path_spans(
                value,
                max_text_chars=max_text_chars,
                max_path_chars=max_path_chars,
            )
        )
    )


def redact_complete_paths(value: object, *, max_text_chars: int = 4_000) -> str:
    """Replace complete path spans with spaces for relation-token analysis."""

    text = _normalized_text(value)
    if not text or len(text) > max_text_chars:
        return text
    characters = list(text)
    for span in complete_path_spans(text, max_text_chars=max_text_chars):
        characters[span.start : span.end] = " " * (span.end - span.start)
    return "".join(characters)


_LEFT_IDENTITY_DELIMITERS = frozenset("`'\"([{<")
_RIGHT_IDENTITY_DELIMITERS = frozenset("`'\")]}>")
_RIGHT_SENTENCE_DELIMITERS = frozenset(".,;:!?，。；：！？")


def _safe_left_boundary(text: str, position: int, *, basename: bool) -> bool:
    if position == 0:
        return True
    before = text[position - 1]
    if basename and before == "/":
        return True
    if before.isspace():
        return True
    if before not in _LEFT_IDENTITY_DELIMITERS:
        return False
    # Do not split a path out of a glued token such as ``name(src/a.py)``.
    return position == 1 or text[position - 2].isspace()


def _safe_right_boundary(text: str, position: int) -> bool:
    if position == len(text) or text[position].isspace():
        return True
    cursor = position
    while cursor < len(text) and (
        text[cursor] in _RIGHT_IDENTITY_DELIMITERS
        or text[cursor] in _RIGHT_SENTENCE_DELIMITERS
    ):
        cursor += 1
    # Punctuation is a delimiter only when it actually terminates a token.
    # Glued variants such as ``config.py:backup`` or ``config.py,backup`` are
    # different identities and must not grant deterministic file authority.
    return cursor > position and (cursor == len(text) or text[cursor].isspace())


def complete_identity_occurs(
    identity: object,
    text: object,
    *,
    case_sensitive: bool = True,
    basename: bool = False,
    max_text_chars: int = 12_000,
) -> bool:
    """Match one identity without accepting descendants, suffixes, or marks."""

    return bool(
        complete_identity_occurrence_spans(
            identity,
            text,
            case_sensitive=case_sensitive,
            basename=basename,
            max_text_chars=max_text_chars,
        )
    )


def complete_identity_occurrence_spans(
    identity: object,
    text: object,
    *,
    case_sensitive: bool = True,
    basename: bool = False,
    max_text_chars: int = 12_000,
) -> tuple[tuple[int, int], ...]:
    """Return complete identity spans in normalized text source order.

    Callers that use span offsets for relation proofs must keep
    ``case_sensitive=True``. Unicode case-folding can change string length; the
    insensitive mode is retained only for boolean approximate-recall checks.
    """

    normalized_identity = normalize_path_identity(identity)
    if not normalized_identity:
        return ()
    if "/" in normalized_identity:
        expected = (
            normalized_identity
            if case_sensitive
            else normalized_identity.casefold()
        )
        return tuple(
            (span.start, span.end)
            for span in complete_path_spans(text, max_text_chars=max_text_chars)
            if (
                span.identity if case_sensitive else span.identity.casefold()
            )
            == expected
        )

    needle = _normalized_text(identity).replace("\\", "/")
    haystack = _normalized_text(text).replace("\\", "/")
    if not case_sensitive:
        needle = needle.casefold()
        haystack = haystack.casefold()
    cursor = 0
    spans: list[tuple[int, int]] = []
    while (position := haystack.find(needle, cursor)) >= 0:
        after_index = position + len(needle)
        if _safe_left_boundary(
            haystack,
            position,
            basename=basename,
        ) and _safe_right_boundary(haystack, after_index):
            spans.append((position, after_index))
        cursor = position + 1
    return tuple(spans)
