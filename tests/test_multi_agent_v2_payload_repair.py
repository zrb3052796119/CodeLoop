"""Bounded format repair for weak-model role result payloads.

``repair_role_payload`` only strips format noise (markdown fences, prose
around the JSON, BOM/zero-width characters).  Semantic validation is never
relaxed: whatever it returns must still pass the unchanged strict
``parse_role_result``.
"""

from __future__ import annotations

import json

from minicode.multi_agent_contracts import (
    MAX_ROLE_PAYLOAD_REPAIR_BYTES,
    repair_role_payload,
)

_CLEAN = {"resultVersion": 1, "assignmentId": "asg_12345678", "status": "completed"}


def test_clean_json_object_is_returned_unchanged() -> None:
    text = json.dumps(_CLEAN)
    assert repair_role_payload(text) == text


def test_markdown_fenced_json_is_unwrapped() -> None:
    payload = json.dumps(_CLEAN, indent=2)
    text = f"Here is the report:\n```json\n{payload}\n```\nHope this helps."
    repaired = repair_role_payload(text)
    assert repaired is not None
    assert json.loads(repaired) == _CLEAN


def test_prose_around_json_is_trimmed() -> None:
    payload = json.dumps(_CLEAN)
    text = f"I analyzed the repository. {payload} That is my final answer."
    assert repair_role_payload(text) == payload


def test_first_parseable_object_wins_when_multiple_spans_exist() -> None:
    first = json.dumps({"resultVersion": 1, "note": "first"})
    second = json.dumps({"resultVersion": 1, "note": "second"})
    text = f"example: {{not json}} then {first} and later {second}"
    assert repair_role_payload(text) == first


def test_braces_inside_strings_do_not_break_balance() -> None:
    document = {"statement": "use {path} and } literally", "escaped": 'a\\"b'}
    text = f"```\n{json.dumps(document)}\n```"
    repaired = repair_role_payload(text)
    assert repaired is not None
    assert json.loads(repaired) == document


def test_bom_and_zero_width_characters_are_stripped() -> None:
    payload = json.dumps(_CLEAN)
    text = "\ufeff\u200b" + payload + "\u200d"
    assert repair_role_payload(text) == payload


def test_unbalanced_json_stays_fail_closed() -> None:
    assert repair_role_payload('{"resultVersion": 1, "status":') is None


def test_prose_without_json_stays_fail_closed() -> None:
    assert repair_role_payload("I could not produce the document.") is None


def test_array_wrapped_object_is_unwrapped() -> None:
    # A single-object array wrapper is format noise too; the extracted object
    # must still pass strict contract validation afterwards.
    inner = json.dumps(_CLEAN)
    assert repair_role_payload(f"[{inner}]") == inner


def test_non_string_input_is_refused() -> None:
    assert repair_role_payload(None) is None
    assert repair_role_payload({"resultVersion": 1}) is None


def test_oversized_input_is_not_repaired() -> None:
    big = json.dumps({"resultVersion": 1, "blob": "x" * MAX_ROLE_PAYLOAD_REPAIR_BYTES})
    assert len(big.encode("utf-8")) > MAX_ROLE_PAYLOAD_REPAIR_BYTES
    assert repair_role_payload(big) is None


def test_pathological_nesting_is_bounded_and_fail_closed() -> None:
    text = "{" * 5000 + "}" * 5000
    assert repair_role_payload(text) is None
