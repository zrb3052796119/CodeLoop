"""Narrow AST admission for independently adjudicated probe expression errors.

This checks the shape of a correction, not its meaning or correctness. The
caller still needs objective/source authority and actual replacement execution.
Ordinary reviewer-authored fixture corrections do not use this interface.
"""

from __future__ import annotations

import ast
import copy
import math

from minicode.review_probe_source import validate_review_probe_source


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _walk_paths(node, path=()):
    yield path, node
    for field, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            yield from _walk_paths(value, (*path, field))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, ast.AST):
                    yield from _walk_paths(child, (*path, field, index))


def _at(tree, path):
    try:
        for part in path:
            tree = tree[part] if isinstance(part, int) else getattr(tree, part)
        return tree
    except (AttributeError, IndexError, TypeError):
        return None


def _set(tree, path, value):
    parent = _at(tree, path[:-1])
    if isinstance(path[-1], int):
        parent[path[-1]] = value
    else:
        setattr(parent, path[-1], value)


def _literal(node):
    if any(isinstance(item, (ast.Call, ast.Starred))
           or isinstance(item, ast.Dict) and any(key is None for key in item.keys)
           for item in ast.walk(node)):
        return False
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False

    def finite_data(value):
        if value is None or type(value) in {str, int, bool}:
            return True
        if type(value) is float:
            return math.isfinite(value)
        if type(value) in {list, tuple, set}:
            return all(finite_data(item) for item in value)
        if type(value) is dict:
            return all(finite_data(key) and finite_data(item) for key, item in value.items())
        return False

    return finite_data(value)


def _inline_inputs(before, after):
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    changed = False
    # Only the observed side of an assert comparison admits input changes.
    # Expected expressions, comparison operators and surrounding reads remain
    # exact; adding/rebinding calls is not an input-literal correction.
    for path, assertion in _walk_paths(before):
        if not isinstance(assertion, ast.Assert) or not isinstance(assertion.test, ast.Compare):
            continue
        base = (*path, "test", "left")
        for relative, old_call in _walk_paths(assertion.test.left):
            if not isinstance(old_call, ast.Call):
                continue
            call_path = (*base, *relative)
            new_call = _at(after, call_path)
            if (not isinstance(new_call, ast.Call) or _dump(old_call.func) != _dump(new_call.func)
                    or len(old_call.args) != len(new_call.args)
                    or [item.arg for item in old_call.keywords] != [item.arg for item in new_call.keywords]):
                continue
            pairs = [(('args', index), old, new) for index, (old, new)
                     in enumerate(zip(old_call.args, new_call.args))]
            pairs.extend((('keywords', index, 'value'), old.value, new.value)
                         for index, (old, new) in enumerate(zip(old_call.keywords, new_call.keywords))
                         if old.arg is not None)
            for suffix, old, new in pairs:
                if _dump(old) == _dump(new) or not (_literal(old) and _literal(new)):
                    continue
                marker = ast.Constant(value="<independently-adjudicated-literal-input>")
                _set(left, (*call_path, *suffix), marker)
                _set(right, (*call_path, *suffix), copy.deepcopy(marker))
                changed = True
    return changed and _dump(left) == _dump(right)


def _accessor(before, after):
    normalized = copy.deepcopy(after)
    changed = False
    for path, old in _walk_paths(before):
        if (not isinstance(old, ast.Call) or not isinstance(old.func, ast.Attribute)
                or old.args or old.keywords or old.func.attr.startswith("__")):
            continue
        new = _at(after, path)
        if (not isinstance(new, ast.Attribute) or new.attr.startswith("__")
                or not isinstance(new.ctx, ast.Load) or _dump(old.func.value) != _dump(new.value)):
            continue
        _set(normalized, path, copy.deepcopy(old))
        changed = True
    return changed and _dump(before) == _dump(normalized)


def validate_adjudicated_expression_correction(old: dict, new: dict) -> dict | None:
    """Return the admitted structural mode, or None for any unmatched change."""
    if (not validate_review_probe_source(old) or not validate_review_probe_source(new)
            or old["probeId"] == new["probeId"] or old["criterionIds"] != new["criterionIds"]
            or old["scenario"] != new["scenario"]):
        return None
    try:
        before, after = ast.parse(old["code"]), ast.parse(new["code"])
    except (ValueError, SyntaxError):
        return None
    from minicode.review_probe_correction import _source_fingerprint

    try:
        original, replacement = _source_fingerprint(old), _source_fingerprint(new)
    except ValueError:
        return None
    if original[0] != replacement[0] or original[2] != replacement[2]:
        return None
    old_asserts = [_dump(node) for node in ast.walk(before) if isinstance(node, ast.Assert)]
    new_asserts = [_dump(node) for node in ast.walk(after) if isinstance(node, ast.Assert)]
    if old_asserts and _inline_inputs(before, after):
        return {"mode": "fixture_inline_inputs"}
    if old_asserts == new_asserts and _accessor(before, after):
        return {"mode": "fixture_accessor"}
    return None
