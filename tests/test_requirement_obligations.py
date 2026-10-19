"""Source preservation and explicit consumer boundaries, without model calls."""

from copy import deepcopy

import pytest

from minicode.requirement_obligations import build_obligation_manifest, validate_obligation_manifest


def parents(objective, ranges=None):
    lines = objective.splitlines()
    ranges = ranges or [(1, len(lines))]
    return [{"id": f"req_{i}", "startLine": first, "endLine": last,
             "text": "\n".join(lines[first - 1:last])}
            for i, (first, last) in enumerate(ranges)]


def manifest(objective, ranges=None):
    return build_obligation_manifest(objective, parents(objective, ranges))


def assert_lossless(value):
    original = value["originalObjective"]
    ranges = []
    for parent in value["parents"]:
        children = [row for row in value["obligations"] if row["parentId"] == parent["id"]]
        assert "".join(row["text"] for row in children) == parent["text"]
        assert children[0]["startOffset"] == parent["startOffset"]
        assert children[-1]["endOffset"] == parent["endOffset"]
        for left, right in zip(children, children[1:]):
            assert left["endOffset"] == right["startOffset"]
        for row in children:
            assert original[row["startOffset"]:row["endOffset"]] == row["text"]
            ranges.append((row["startOffset"], row["endOffset"]))
            for symbol in row["requiredSymbols"]:
                assert original[symbol["startOffset"]:symbol["endOffset"]] == symbol["name"]
                assert row["startOffset"] <= symbol["startOffset"] < symbol["endOffset"] <= row["endOffset"]
    for offset, char in enumerate(original):
        if not char.isspace():
            assert sum(first <= offset < last for first, last in ranges) == 1


def test_normal_and_rejection_clauses_keep_exact_parent_authority():
    objective = "- 返回有序库存，拒绝负数；保留所有权、权限，以及审计记录。"
    value = manifest(objective)
    assert [r["text"].strip() for r in value["obligations"]] == [
        "- 返回有序库存，", "拒绝负数；", "保留所有权、", "权限，", "以及审计记录。"]
    assert all(r["parentId"] == "req_0" for r in value["obligations"])
    assert_lossless(value)


def test_wrapped_prose_crlf_and_headings_are_preserved():
    objective = "# Stock rules\r\n\r\n1. Return available\r\nstock and reject invalid identifiers;\r\npreserve caller ownership.\r\n"
    value = manifest(objective)
    assert len(value["obligations"]) == 3
    assert "# Stock rules\r\n\r\n1." in value["obligations"][0]["text"]
    assert "available\r\nstock" in value["obligations"][0]["text"]
    assert_lossless(value)


def test_inline_code_formulas_and_call_arguments_are_not_split():
    objective = "Use `Pair(a,b); x and y` with $f(a,b)$ and \\(g(x,y)\\); return max(a,b), reject invalid input."
    value = manifest(objective)
    texts = [row["text"] for row in value["obligations"]]
    assert any("`Pair(a,b); x and y`" in t and "$f(a,b)$" in t for t in texts)
    assert any(r"\(g(x,y)\)" in t for t in texts)
    assert any("max(a,b)," in t for t in texts)
    assert_lossless(value)


@pytest.mark.parametrize("closing", ["```\n", ""])
def test_code_fence_remains_one_opaque_obligation(closing):
    objective = "Examples must work;\n```python\nreal = 'ordinary text'\nx = f(a,b); y = g(x)\n\n" + closing
    value = manifest(objective)
    code = [r for r in value["obligations"] if "```python" in r["text"]]
    assert len(code) == 1 and "x = f(a,b); y = g(x)" in code[0]["text"]
    assert code[0]["requiredSymbols"] == []
    assert_lossless(value)


def test_literal_inventory_stays_grouped_and_every_nonempty_line_is_owned():
    objective = "# Implement these checks\n\n- `tests/test_one.py::test_case[a,b]`\n- `tests/test_two.py::test_case`\n"
    value = manifest(objective, [(1, 1), (3, 4)])
    assert len(value["obligations"]) == 2
    assert_lossless(value)


def test_explicit_real_source_and_destination_bind_names_and_exact_unicode_offsets():
    objective = "真实 StockService.apply 应将实际执行的结果传入 Ledger.record，\n而不是只让 `format_note` 单测通过。"
    value = manifest(objective)
    names = [s["name"] for row in value["obligations"] for s in row["requiredSymbols"]]
    assert names == ["StockService.apply", "Ledger.record"]
    assert_lossless(value)


def test_plain_and_backtick_actual_symbols_survive_wrap_without_binding_mentions():
    objective = "The real\n`StockService.apply` must forward results into Ledger.record, and helper `pretty_name` is optional."
    value = manifest(objective)
    assert [s["name"] for r in value["obligations"] for s in r["requiredSymbols"]] == [
        "StockService.apply", "Ledger.record"]
    assert_lossless(value)


@pytest.mark.parametrize("objective, names", [
    ("Execute the real `dispatch`; preserve results.", ["dispatch"]),
    ("`dispatch` must actually execute, and report its result.", ["dispatch"]),
    ("Expose `dispatch` and mention `Helper.format`.", []),
    ("Do not execute real `Danger.erase`; real `Safe.read` must call Store.lookup.", ["Safe.read", "Store.lookup"]),
    ("无需真实 Dangerous.erase；真实 Safe.read 应传给 Audit.record。", ["Safe.read", "Audit.record"]),
    ("Use real Source.consume. An example can send data to Example.fake.", ["Source.consume"]),
    ("1. Actually invoke `checkout` to call `Ledger.reserve` and assert the returned stock.", ["checkout", "Ledger.reserve"]),
    ("保存该轮实际 rendered entry IDs；描述真实 `verification` 字段。", []),
    ("Mention the actual `Helper.format` implementation in the documentation.", []),
    ("真实 run_command_exit 标记只用于结果，不是一个消费者。", []),
    ("Do not\nexecute real `Danger.erase`; actually invoke `Safe.read`.", ["Safe.read"]),
    ("不实际执行 Dangerous.erase；真实 Safe.read 应传给 Audit.record。", ["Safe.read", "Audit.record"]),
])
def test_symbol_binding_is_local_and_not_a_blanket_mention_classifier(objective, names):
    value = manifest(objective)
    assert [s["name"] for r in value["obligations"] for s in r["requiredSymbols"]] == names
    assert_lossless(value)


def test_stable_ids_distinguish_repeated_source_and_validate_every_field():
    objective = "Return stock; reject invalid input.\n\nReturn stock; reject invalid input."
    parent = parents(objective, [(1, 1), (3, 3)])
    first = build_obligation_manifest(objective, parent)
    assert first == build_obligation_manifest(objective, parent)
    assert len({row["id"] for row in first["obligations"]}) == 4
    assert validate_obligation_manifest(objective, parent, first) == first
    for change in ("text", "range", "symbol", "omission", "parent", "objective", "bool", "float"):
        altered = deepcopy(first)
        if change == "text":
            altered["obligations"][0]["text"] = "A convenient paraphrase"
        elif change == "range":
            altered["obligations"][0]["startOffset"] += 1
        elif change == "symbol":
            altered["obligations"][0]["requiredSymbols"] = [{"name": "invented"}]
        elif change == "omission":
            altered["obligations"].pop()
        elif change == "parent":
            altered["obligations"][0]["parentId"] = "req_foreign"
        elif change == "objective":
            altered["originalObjective"] += "extra"
        elif change == "bool":
            altered["manifestVersion"] = True
        else:
            altered["obligations"][0]["startOffset"] = 0.0
        with pytest.raises(ValueError, match="manifest differs"):
            validate_obligation_manifest(objective, parent, altered)


@pytest.mark.parametrize("damage", ["omitted", "duplicate", "overlap", "paraphrase", "reordered"])
def test_parent_omissions_or_rewriting_cannot_reduce_original_obligations(damage):
    objective = "Return stock.\nReject invalid input."
    parent = parents(objective, [(1, 1), (2, 2)])
    if damage == "omitted":
        parent.pop()
    elif damage == "duplicate":
        parent[1]["id"] = parent[0]["id"]
    elif damage == "overlap":
        parent[1]["startLine"] = 1
    elif damage == "paraphrase":
        parent[1]["text"] = "Return stock."
    else:
        parent.reverse()
    with pytest.raises(ValueError):
        build_obligation_manifest(objective, parent)


def test_existing_parent_partition_can_omit_blank_heading_gap_not_source():
    objective = "# Rules\n\nReturn stock; reject invalid input."
    parent = parents(objective)
    parent[0]["text"] = "# Rules\nReturn stock; reject invalid input."
    value = build_obligation_manifest(objective, parent)
    assert value["parents"][0]["text"] == objective
    assert_lossless(value)


def test_unclosed_inline_markup_cannot_swallow_a_fenced_source_block():
    objective = "An unmatched $ and ` before the example;\n```text\nx = '$, and ;'\n```\nThen preserve it."
    value = manifest(objective)
    code = [r for r in value["obligations"] if "```text" in r["text"]]
    assert len(code) == 1 and "x = '$, and ;'" in code[0]["text"]
    assert_lossless(value)
