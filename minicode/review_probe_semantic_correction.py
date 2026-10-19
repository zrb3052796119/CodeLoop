"""Structural admission for independently adjudicated probe mistakes.

These validators do not establish the correct expectation or fixture. That judgement
requires a separate, objective-bound semantic authority and a real failed
receipt. Each correction mode preserves its protected observations and structure.
"""

from __future__ import annotations

import ast
import builtins
import copy
import math

from minicode.review_probe_source import validate_review_probe_source


def validate_semantic_probe_correction(old: dict, new: dict) -> dict:
    if not validate_review_probe_source(old) or not validate_review_probe_source(new):
        raise ValueError("semantic correction requires exact retained source identities")
    if (old["probeId"] == new["probeId"] or old["criterionIds"] != new["criterionIds"]
            or old["scenario"] != new["scenario"]):
        raise ValueError("semantic correction must preserve criteria and scenario with distinct source")
    try:
        before, after = ast.parse(old["code"]), ast.parse(new["code"])
    except (ValueError, SyntaxError) as exc:
        raise ValueError("semantic correction requires valid Python sources") from exc
    old_asserts = [node for node in ast.walk(before) if isinstance(node, ast.Assert)]
    new_asserts = [node for node in ast.walk(after) if isinstance(node, ast.Assert)]
    if not old_asserts or len(old_asserts) != len(new_asserts):
        raise ValueError("semantic correction must preserve every assertion")
    changed = []
    for left, right in zip(old_asserts, new_asserts, strict=True):
        if ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False):
            continue
        a, b = left.test, right.test
        if (not isinstance(a, ast.Compare) or not isinstance(b, ast.Compare)
                or len(a.ops) != 1 or len(b.ops) != 1
                or not isinstance(a.ops[0], ast.Eq) or not isinstance(b.ops[0], ast.Eq)):
            raise ValueError("only an equality assertion's expected literal may be corrected")
        try:
            old_value, new_value = ast.literal_eval(a.comparators[0]), ast.literal_eval(b.comparators[0])
        except (ValueError, TypeError, SyntaxError) as exc:
            raise ValueError("expected values must be scalar literals, never current outputs") from exc
        numeric = type(old_value) in {int, float} and type(new_value) in {int, float}
        same_kind = type(old_value) is type(new_value) and type(old_value) in {str, bool, type(None)}
        if (not (numeric or same_kind) or old_value == new_value
                or any(type(v) is float and not math.isfinite(v) for v in (old_value, new_value))):
            raise ValueError("expected literal corrections require finite numeric or same-kind scalar values")
        changed.append({"line": left.lineno, "oldExpected": old_value, "newExpected": new_value})
        a.comparators[0] = ast.Constant(value="<independently-adjudicated-expectation>")
        b.comparators[0] = ast.Constant(value="<independently-adjudicated-expectation>")
    if not changed or ast.dump(before, include_attributes=False) != ast.dump(after, include_attributes=False):
        raise ValueError("semantic correction must preserve test identities, inputs, calls, control flow and all other assertions")
    return {"changedAssertions": changed}


def validate_adjudicated_probe_correction(old: dict, new: dict) -> dict:
    """Select a structural gate; no gate supplies semantic authority.

    Expected-value mistakes preserve the experiment. Fixture/input mistakes
    preserve all acceptance assertions under the existing fixture contract.
    The independent objective-bound judgment and real execution are separate.
    """
    if old.get("scenario") != new.get("scenario"):
        raise ValueError("independent correction must preserve the original scenario")
    try:
        return {"mode": "expected_literal", **validate_semantic_probe_correction(old, new)}
    except ValueError as literal_error:
        from minicode.review_probe_correction import validate_probe_correction_sources
        try:
            validate_probe_correction_sources(old, new)
        except ValueError as fixture_error:
            if _local_binding_correction(old, new):
                return {"mode": "fixture_bindings"}
            if _keyword_addition_correction(old, new):
                return {"mode": "fixture_keywords"}
            from minicode.review_probe_expression_correction import validate_adjudicated_expression_correction
            expression = validate_adjudicated_expression_correction(old, new)
            if expression is not None:
                return expression
            raise ValueError("replacement must pass either unchanged-experiment expected literal correction "
                             "or assertion-preserving fixture correction (including binding an unbound local "
                             "name, adding static call keywords, correcting literal observed-call inputs, "
                             "or replacing a zero-argument accessor call with a same-receiver property read): "
                             + str(fixture_error)) from literal_error
        return {"mode": "fixture_inputs"}


def _keyword_addition_correction(old: dict, new: dict) -> bool:
    """Admit missing static keywords only under independent semantic authority.

    Syntax cannot establish that a keyword belongs to fixture setup rather than
    the business behavior under test. The objective/source/current-failure
    adjudication must establish that separately; this gate preserves everything
    except added keyword arguments at the exact original call sites.
    """
    from minicode.review_probe_correction import _source_fingerprint

    if (not validate_review_probe_source(old) or not validate_review_probe_source(new)
            or old["criterionIds"] != new["criterionIds"] or old["scenario"] != new["scenario"]):
        return False
    # Keep the existing test identities, assertions and pytest controls, without
    # weakening their ordinary author-correction validator.
    if _source_fingerprint(old)[:3] != _source_fingerprint(new)[:3]:
        return False
    before, after = ast.parse(old["code"]), ast.parse(new["code"])

    def dump(node):
        return ast.dump(node, include_attributes=False)

    original = dump(before)
    parents = {id(child): (parent, field) for parent in ast.walk(before)
               for field, value in ast.iter_fields(parent)
               for child in (value if isinstance(value, list) else [value])
               if isinstance(child, ast.AST)}
    old_calls = [node for node in ast.walk(before) if isinstance(node, ast.Call)]
    new_calls = [node for node in ast.walk(after) if isinstance(node, ast.Call)]
    if len(old_calls) != len(new_calls):
        return False
    changed = False
    for left, right in zip(old_calls, new_calls, strict=True):
        if dump(left) == dump(right):
            continue
        ancestor = left
        while id(ancestor) in parents:
            ancestor, field = parents[id(ancestor)]
            if isinstance(ancestor, ast.Assert) or field == "decorator_list":
                return False
        if (dump(left.func) != dump(right.func)
                or [dump(arg) for arg in left.args] != [dump(arg) for arg in right.args]
                or any(isinstance(arg, ast.Starred) for arg in left.args)):
            return False
        old_names = [keyword.arg for keyword in left.keywords]
        new_names = [keyword.arg for keyword in right.keywords]
        if (None in old_names or None in new_names or len(set(old_names)) != len(old_names)
                or len(set(new_names)) != len(new_names) or not set(old_names) < set(new_names)):
            return False
        retained = [keyword for keyword in right.keywords if keyword.arg in old_names]
        if [dump(keyword) for keyword in retained] != [dump(keyword) for keyword in left.keywords]:
            return False
        for keyword in right.keywords:
            if keyword.arg in old_names:
                continue
            # literal_eval rejects names, calls, comprehensions and unpacking;
            # reject Call explicitly too (it accepts the special set() case).
            if any(isinstance(node, (ast.Call, ast.Starred)) for node in ast.walk(keyword.value)):
                return False
            try:
                ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                return False
        right.keywords = copy.deepcopy(left.keywords)
        changed = True
    return changed and original == dump(after)


def _local_binding_correction(old: dict, new: dict) -> bool:
    """Admit a local fixture binding repair without changing any observation.

    A separate objective-bound judgment remains mandatory. This only admits an
    alias to an already bound local, or renames an unused assignment destination
    to an otherwise unbound name. Assertions, calls, inputs and flow stay exact.
    """
    if (not validate_review_probe_source(old) or not validate_review_probe_source(new)
            or old["criterionIds"] != new["criterionIds"] or old["scenario"] != new["scenario"]):
        return False
    before, after = ast.parse(old["code"]), ast.parse(new["code"])
    def dump(tree):
        return ast.dump(tree, include_attributes=False)
    original = dump(before)
    # Module bindings and builtins must not be shadowed as a supposed typo fix.
    module_names = set(dir(builtins))
    for node in before.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        else:
            module_names.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                                and isinstance(n.ctx, ast.Store))
            for n in ast.walk(node):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    if any(a.name == "*" for a in n.names):
                        return False
                    module_names.update(a.asname or a.name.split(".")[0] for a in n.names)
    old_functions = [n for n in ast.walk(before) if isinstance(n, ast.FunctionDef)]
    for function_index, old_function in enumerate(old_functions):
        nodes = [n for statement in old_function.body for n in ast.walk(statement)]
        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                              ast.Lambda, ast.Global, ast.Nonlocal)) for n in nodes):
            continue
        stores = [n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
        loads = {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        parameters = {n.arg for n in ast.walk(old_function.args) if isinstance(n, ast.arg)}
        imported = {a.asname or a.name.split(".")[0] for n in nodes
                    if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        bound = set(stores) | parameters | imported | module_names
        unbound = loads - bound
        changed_functions = [n for n in ast.walk(after) if isinstance(n, ast.FunctionDef)]
        if len(changed_functions) != len(old_functions):
            return False
        changed_function = changed_functions[function_index]
        prior_bound = set(parameters)
        for index, statement in enumerate(changed_function.body):
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Name)
                    and statement.targets[0].id in unbound and statement.value.id in prior_bound):
                normalized = copy.deepcopy(after)
                functions = [n for n in ast.walk(normalized) if isinstance(n, ast.FunctionDef)]
                del functions[function_index].body[index]
                if dump(normalized) == original:
                    return True
            if index < len(old_function.body):
                prior_bound.update(n.id for n in ast.walk(old_function.body[index])
                                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        for index, statement in enumerate(old_function.body):
            if not isinstance(statement, ast.Assign) or index >= len(changed_function.body):
                continue
            replacement = changed_function.body[index]
            if not isinstance(replacement, ast.Assign):
                continue
            old_targets = [n for target in statement.targets for n in ast.walk(target)
                           if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            new_targets = [n for target in replacement.targets for n in ast.walk(target)
                           if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            if len(old_targets) != len(new_targets):
                continue
            for target_index, (old_target, new_target) in enumerate(zip(old_targets, new_targets)):
                if (old_target.id in loads or stores.count(old_target.id) != 1
                        or new_target.id not in unbound):
                    continue
                normalized = copy.deepcopy(after)
                functions = [n for n in ast.walk(normalized) if isinstance(n, ast.FunctionDef)]
                assignment = functions[function_index].body[index]
                targets = [n for target in assignment.targets for n in ast.walk(target)
                           if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
                targets[target_index].id = old_target.id
                if dump(normalized) == original:
                    return True
    return False
