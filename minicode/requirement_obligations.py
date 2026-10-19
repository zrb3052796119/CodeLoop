"""Lossless, source-bound sub-obligations, not natural-language proof.

Punctuation and explicit conjunctions expose smaller claims without rewriting
them. Parent context remains authoritative: a fragment is never an independent
paraphrase, and an unsplit fragment is not a claim of semantic atomicity.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
import hashlib
import json
import re


_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_CONJUNCTION = re.compile(r"\b(?:and|but)\b|并且|以及|而且|但是|而不是", re.IGNORECASE)
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
_GRAMMAR_WORDS = frozenset("a an the must should shall actual actually really real genuine production "
                          "consumer entry point call execution execute invoke run through to into".split())
_ACTUAL = r"(?:真实|实际|真正|\breal\b|\bactual\b|\bactually\b|\breally\b|\bgenuine\b|\bproduction\b)"
_ACTUAL_WORD = re.compile(_ACTUAL, re.IGNORECASE)
_DIRECT_ACTUAL = re.compile(
    _ACTUAL + r"(?:的)?\s*(?:(?:入口|消费者|调用|执行|运行|entry\s*point|consumer|invoke|call|execute|run)\s*)?$",
    re.IGNORECASE,
)
_CALL = re.compile(
    r"(?:调用|执行|运行|传入|传给|交给|送入|汇入|\bcall|\binvoke|\bexecute|\brun|\bthrough|\bto|\binto)\s*$",
    re.IGNORECASE,
)
_ACTUAL_SUFFIX = re.compile(
    r"^\s*(?:(?:must|should|shall)\s+)?(?:actually|really)\s+(?:run|execute|consume|call|invoke)\b"
    r"|^\s*(?:必须|应当|应|须)?(?:真实|实际|真正)(?:调用|执行|运行|消费)", re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"(?:\b(?:not|never|without)\b|\bno\s+need\b|\binstead\s+of\b|\brather\s+than\b|"
    r"无需|不需要|不必|不要|不得|禁止|不是|而不是|非真实|不)"
    r"[^,;。；，!?！？]{0,48}$", re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[;；。!?！？]|\.(?=\s|$)")
_ACTION_PREFIX = re.compile(r"(?:\b(?:use|call|invoke|execute|run)\b|调用|执行|运行)[\s`]*(?:the\s+)?$", re.IGNORECASE)
_ACTION_SUFFIX = re.compile(r"^\s*(?:must\b|should\b|shall\b|应|须|必须|将|把|接收|消费|传)", re.IGNORECASE)
_EXPLICIT_ROLE = re.compile(r"入口|消费者|调用|执行|运行|entry\s*point|consumer|invoke|call|execute|run", re.IGNORECASE)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _protected(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return opaque inline ranges and whole fenced blocks, without evaluating."""
    fences: list[tuple[int, int]] = []
    opened: tuple[int, str] | None = None
    position = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if opened is None and match:
            opened = (position, match[1])
        elif opened is not None and match:
            marker = match[1]
            if (marker[0] == opened[1][0] and len(marker) >= len(opened[1])
                    and not line[match.end():].strip()):
                fences.append((opened[0], position + len(line)))
                opened = None
        position += len(line)
    if opened is not None:
        fences.append((opened[0], len(text)))
    protected = list(fences)
    i, fence_index = 0, 0
    while i < len(text):
        if fence_index < len(fences) and i >= fences[fence_index][0]:
            i = fences[fence_index][1]
            fence_index += 1
            continue
        if text[i] == "`":
            j = i
            while j < len(text) and text[j] == "`":
                j += 1
            marker = text[i:j]
            end = text.find(marker, j)
            if end >= 0 and (fence_index == len(fences) or end + len(marker) <= fences[fence_index][0]):
                protected.append((i, end + len(marker)))
                i = end + len(marker)
                continue
        if text.startswith((r"\(", r"\["), i):
            ending = r"\)" if text[i + 1] == "(" else r"\]"
            end = text.find(ending, i + 2)
            if end >= 0 and (fence_index == len(fences) or end + 2 <= fences[fence_index][0]):
                protected.append((i, end + 2))
                i = end + 2
                continue
        if text[i] == "$" and (i == 0 or text[i - 1] != "\\"):
            marker = "$$" if text.startswith("$$", i) else "$"
            end = text.find(marker, i + len(marker))
            if end >= 0 and (fence_index == len(fences) or end + len(marker) <= fences[fence_index][0]):
                protected.append((i, end + len(marker)))
                i = end + len(marker)
                continue
        i += 1
    return sorted(protected), fences


def _split(text: str) -> list[tuple[int, int]]:
    protected, fences = _protected(text)
    fence_starts = dict(fences)
    cuts = {0, len(text)}
    for first, last in fences:
        cuts.update((first, last))
    conjunctions = {match.start() for match in _CONJUNCTION.finditer(text)}
    opaque, i, depth = 0, 0, 0
    while i < len(text):
        if opaque < len(protected) and i >= protected[opaque][0]:
            i = protected[opaque][1]
            opaque += 1
            continue
        char = text[i]
        if char in "([{（【":
            depth += 1
        elif char in ")]}）】":
            depth = max(0, depth - 1)
        if char in "。；;！？!?" or (not depth and char in "，,、"):
            cuts.add(i + 1)
        elif (char == "." and (i + 1 == len(text) or text[i + 1].isspace())
              and (i == 0 or text[i - 1] != ".")):
            line_prefix = text[text.rfind("\n", 0, i) + 1:i + 1]
            if re.fullmatch(r"\s*\d+\.", line_prefix) is None:
                cuts.add(i + 1)
        elif not depth and i in conjunctions:
            cuts.add(i)
        i += 1
    spans: list[tuple[int, int]] = []
    points = sorted(cuts)
    for first, last in zip(points, points[1:]):
        if not text[first:last].strip():
            if spans:
                spans[-1] = (spans[-1][0], last)
            continue
        if spans and spans[-1][1] < first:
            first = spans[-1][1]
        elif not spans:
            first = 0
        # Do not manufacture a separate obligation from a list connector.
        if spans and not text[spans[-1][0]:spans[-1][1]].strip(" \r\n\t,，;；、"):
            first = spans.pop()[0]
        spans.append((first, last))
    if spans:
        spans[-1] = (spans[-1][0], len(text))
    # Fenced source is intentionally opaque; no splitting inside code examples.
    assert all(not (first < cut < last) for first, last in fence_starts.items()
               for span in spans for cut in span)
    return spans


def _symbols(text: str, base: int) -> list[dict]:
    """Bind only explicitly actual call subjects/destinations, not mentions."""
    _, fences = _protected(text)
    symbols = []
    for match in _SYMBOL.finditer(text):
        if any(first <= match.start() < last for first, last in fences):
            continue
        quoted = match.start() > 0 and text[match.start() - 1] == "`"
        if not quoted and match[0].lower() in _GRAMMAR_WORDS:
            continue
        # Sentence-local authority survives a comma or a later contrast, but
        # an unrelated later sentence cannot inherit the word 'real'.
        boundary = max((end.end() for end in _SENTENCE_END.finditer(text, 0, match.start())), default=0)
        prefix = text[boundary:match.start()].rstrip("` ")
        suffix = text[match.end():].lstrip("` ")[:96]
        actual = _DIRECT_ACTUAL.search(prefix)
        direct = (actual is not None and not _NEGATIVE.search(prefix[:actual.start()])
                  and (_EXPLICIT_ROLE.search(actual[0]) is not None
                       or _ACTION_PREFIX.search(prefix[:actual.start()]) is not None
                       or _ACTION_SUFFIX.search(suffix) is not None))
        trailing = _ACTUAL_SUFFIX.search(suffix) is not None and not _NEGATIVE.search(prefix)
        call = _CALL.search(prefix)
        prior_actual = any(not _NEGATIVE.search(prefix[:m.start()]) for m in _ACTUAL_WORD.finditer(prefix))
        destination = (call is not None and prior_actual and not _NEGATIVE.search(prefix[:call.start()])
                       and ("." in match[0] or "_" in match[0] or match[0][:1].isupper()))
        if direct or trailing or destination:
            symbols.append({"name": match[0], "startOffset": base + match.start(),
                            "endOffset": base + match.end()})
    return symbols


def build_obligation_manifest(objective: str, requirements: Sequence[Mapping]) -> dict:
    """Refine trusted parent ranges while covering every nonblank original line.

    Offsets are Python Unicode character indices, end-exclusive. Lines and
    columns are one-based; endColumn is exclusive on endLine. Whitespace is
    preserved inside each parent, including CRLF and wrapped prose.
    """
    if not isinstance(objective, str) or not objective.strip() or "\x00" in objective:
        raise ValueError("obligations require the complete original objective")
    lines = objective.splitlines(keepends=True)
    starts, cursor = [], 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)
    starts.append(cursor)
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)) or not requirements:
        raise ValueError("obligations require original parent requirements")
    parents, obligations, covered, seen = [], [], set(), set()
    objective_digest = _digest(objective)

    def location(offset: int) -> tuple[int, int]:
        index = min(bisect_right(starts, offset) - 1, len(lines) - 1)
        return index + 1, offset - starts[index] + 1

    previous_end = 0
    for parent in requirements:
        if not isinstance(parent, Mapping):
            raise ValueError("parent requirement must be a source-bound mapping")
        identity, first, last = parent.get("id"), parent.get("startLine"), parent.get("endLine")
        if (not isinstance(identity, str) or not identity or identity in seen
                or type(first) is not int or type(last) is not int
                or not previous_end < first <= last <= len(lines)):
            raise ValueError("parent requirement identities and ranges must be distinct and ordered")
        original = "".join(lines[first - 1:last])
        reported = parent.get("text")
        if (not isinstance(reported, str) or [line for line in reported.splitlines() if line.strip()]
                != [line for line in original.splitlines() if line.strip()]):
            raise ValueError("parent requirement must preserve original nonblank source lines")
        seen.add(identity)
        covered.update(range(first, last + 1))
        previous_end = last
        start, end = starts[first - 1], starts[last]
        parents.append({"id": identity, "text": original, "startLine": first, "endLine": last,
                        "startOffset": start, "endOffset": end})
        required_symbols = _symbols(original, start)
        for relative_first, relative_last in _split(original):
            begin, finish = start + relative_first, start + relative_last
            text = objective[begin:finish]
            key = _digest(_canonical([objective_digest, identity, begin, finish, text]))[7:31]
            first_line, first_column = location(begin)
            last_line, last_column = location(finish)
            obligations.append({"id": "obligation_" + key, "parentId": identity, "text": text,
                "startOffset": begin, "endOffset": finish, "startLine": first_line, "startColumn": first_column,
                "endLine": last_line, "endColumn": last_column,
                "requiredSymbols": [symbol for symbol in required_symbols
                                    if begin <= symbol["startOffset"] < finish]})
    if any(number not in covered for number, line in enumerate(lines, 1) if line.strip()):
        raise ValueError("parent requirements omit nonblank original source")
    return {"manifestVersion": 1, "objectiveDigest": objective_digest, "originalObjective": objective,
            "parents": parents, "obligations": obligations}


def validate_obligation_manifest(objective: str, requirements: Sequence[Mapping], manifest: object) -> dict:
    expected = build_obligation_manifest(objective, requirements)
    try:
        matches = isinstance(manifest, dict) and _canonical(manifest) == _canonical(expected)
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("obligation manifest differs from the complete original source and ranges")
    return expected


__all__ = ["build_obligation_manifest", "validate_obligation_manifest"]
