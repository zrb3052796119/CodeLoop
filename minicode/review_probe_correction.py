"""Pure, conservative evidence checks for explicit probe-harness corrections."""

from __future__ import annotations

import ast
import copy
import hashlib
import json

from minicode.review_probe_source import validate_review_probe_source


REVIEW_PROBE_CORRECTION_PREFIX = "artifact_review_probe_correction_"
# Keep the pure receipt validator and managed file reader on one byte envelope.
# Parametrized test IDs may be long; never truncate their identities.
MAX_PYTEST_REPORT_BYTES = 1024 * 1024


class ProbeCorrectionMismatch(ValueError):
    """Actionable, bounded differences; the original acceptance rule is unchanged."""

    def __init__(self, message: str, *, old_source: dict, differences: list[dict]):
        self._diagnostics = {
            "code": "review_probe_correction_mismatch",
            "originalProbeId": old_source["probeId"],
            "sourceRecovery": {"tool": "review_probe_read", "probeId": old_source["probeId"],
                               "action": "source"},
            "differences": differences,
        }
        super().__init__(message + "\ndiagnostics=" + json.dumps(
            self._diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    @property
    def diagnostics(self) -> dict:
        return copy.deepcopy(self._diagnostics)


def _first_difference(category: str, old: tuple, new: tuple, *, index: int | None = None) -> dict:
    if index is None:
        index = next((i for i, pair in enumerate(zip(old, new)) if pair[0] != pair[1]), min(len(old), len(new)))

    def text_at(values):
        if index >= len(values):
            return None
        value = values[index]
        return value if isinstance(value, str) else repr(value)

    original, replacement = text_at(old), text_at(new)
    first_char = 0
    if original is not None and replacement is not None:
        first_char = next((i for i, pair in enumerate(zip(original, replacement)) if pair[0] != pair[1]),
                          min(len(original), len(replacement)))
    start = max(0, first_char - 80)

    def preview(values):
        text = text_at(values)
        if text is None:
            return {"present": False}
        excerpt = text[start:].encode('utf-8')[:600].decode('utf-8', errors='ignore')
        return {"present": True, "value": excerpt, "startChar": start,
                "truncated": start > 0 or len(excerpt) < len(text)}

    return {"category": category, "index": index, "originalCount": len(old),
            "replacementCount": len(new), "firstDifferingChar": first_char,
            "original": preview(old), "replacement": preview(new)}


def _assertion_location(source: dict, index: int) -> dict:
    tree = ast.parse(source["code"])
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    assertions = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    if index >= len(assertions):
        return {}
    node = assertions[index]
    scopes, parent = [], parents.get(id(node))
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes.append(parent.name)
        parent = parents.get(id(parent))
    scope = "::".join(reversed(scopes)).encode('utf-8')[:320].decode('utf-8', errors='ignore')
    return {"line": node.lineno, "scope": scope}


def correction_artifact_id(document: dict) -> str:
    material = "\0".join(document[key] for key in (
        "workflowId", "assignmentId", "oldProbeId", "newProbeId", "newReceiptRef"))
    return REVIEW_PROBE_CORRECTION_PREFIX + hashlib.sha256(material.encode()).hexdigest()[:32]


def validate_pytest_case_report(report: object) -> bool:
    """Validate host classification and its binding to the original JUnit bytes."""
    if not isinstance(report, dict):
        return False
    if "executionReport" in report:
        from minicode.pytest_execution_evidence import validate_execution_report
        if not validate_execution_report(report):
            return False
    classification = report.get("caseReport")
    if not isinstance(classification, dict) or set(classification) != {"digest", "cases"}:
        return False
    cases = classification["cases"]
    if not isinstance(cases, list) or not cases:
        return False
    seen, passed, failed = set(), 0, 0
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"nodeId", "outcome", "failureKind"}:
            return False
        node_id, outcome, kind = case["nodeId"], case["outcome"], case["failureKind"]
        if (not isinstance(node_id, str) or not node_id
                or len(node_id) > MAX_PYTEST_REPORT_BYTES or node_id in seen):
            return False
        seen.add(node_id)
        if outcome == "passed" and kind is None:
            passed += 1
        elif outcome == "failed" and isinstance(kind, str) and kind in {"assertion", "exception", "unknown"}:
            failed += 1
        else:
            return False
    if (any(type(report.get(key)) is not int for key in ("collected", "passed", "failed", "errors", "skipped"))
            or report["collected"] != len(cases) or report["passed"] != passed or report["failed"] != failed
            or report["errors"] != 0 or report["skipped"] != 0):
        return False
    try:
        encoded = json.dumps({"junitDigest": report["reportDigest"], "cases": cases}, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False
    return (len(encoded) <= MAX_PYTEST_REPORT_BYTES
            and classification["digest"] == "sha256:" + hashlib.sha256(encoded).hexdigest())


def _source_fingerprint(source: dict) -> tuple:
    if not validate_review_probe_source(source):
        raise ValueError("probe correction source identity is invalid")
    try:
        tree = ast.parse(source["code"])
    except (ValueError, SyntaxError) as exc:
        raise ValueError("probe correction source must be valid Python") from exc
    identities = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            identities.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            identities.extend(node.name + "::" + child.name for child in node.body
                              if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                              and child.name.startswith("test_"))
    if not identities or len(set(identities)) != len(identities):
        raise ValueError("probe correction must retain distinct test identities")
    assertions = [ast.dump(node.test, include_attributes=False) for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    observed_names = {node.id for assertion in ast.walk(tree) if isinstance(assertion, ast.Assert)
                      for node in ast.walk(assertion.test) if isinstance(node, ast.Name)}
    called_names = {name.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                    for name in ast.walk(node.func) if isinstance(name, ast.Name)}
    protected_names = observed_names | called_names

    class CallArguments(ast.NodeTransformer):
        def visit_Call(self, node):
            node = self.generic_visit(node)
            # Harness inputs can change; callee, expression structure and
            # assignment destinations cannot be removed or replaced by a stub.
            node.args = [ast.Constant(value="<harness-argument>") for _arg in node.args]
            node.keywords = [ast.keyword(arg=item.arg, value=ast.Constant(value="<harness-argument>"))
                             for item in node.keywords]
            return node

    def expression(node):
        return ast.dump(CallArguments().visit(copy.deepcopy(node)), include_attributes=False)

    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    calls, bindings, flow = [], [], []
    import_dependencies = set(protected_names)

    def preserve_binding(node):
        normalized = CallArguments().visit(copy.deepcopy(node))
        bindings.append(ast.dump(normalized, include_attributes=False))
        import_dependencies.update(item.id for item in ast.walk(normalized)
                                   if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parent = parents.get(id(node))
            ancestor = parent
            nested_argument = False
            while ancestor is not None:
                if isinstance(ancestor, ast.Call):
                    nested_argument = True
                    break
                ancestor = parents.get(id(ancestor))
            binding = ()
            if isinstance(parent, ast.Assign):
                binding = tuple(ast.dump(target, include_attributes=False) for target in parent.targets)
            elif isinstance(parent, (ast.AnnAssign, ast.NamedExpr)):
                binding = (ast.dump(parent.target, include_attributes=False),)
            if not nested_argument:
                calls.append((expression(node), binding))
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(isinstance(item, ast.Name) and item.id in protected_names
                   for target in targets for item in ast.walk(target)):
                preserve_binding(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in called_names:
            preserve_binding(node)
        if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith,
                             ast.Try, ast.TryStar, ast.Return, ast.Break, ast.Continue, ast.comprehension)):
            shallow = copy.deepcopy(node)
            for field in ("body", "orelse", "finalbody"):
                if hasattr(shallow, field):
                    setattr(shallow, field, [])
            if isinstance(shallow, (ast.Try, ast.TryStar)):
                for handler in shallow.handlers:
                    handler.body = []
            flow.append(expression(shallow))
    # Also preserve pytest expectations and skipping controls, including aliases.
    aliases = {"pytest"}
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(item.asname or item.name for item in node.names if item.name == "pytest")
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            imported.update((item.asname or item.name, item.name) for item in node.names)
    controls = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Attribute, ast.Name)):
            continue
        target = node.func if isinstance(node, ast.Call) else node
        parts = []
        while isinstance(target, ast.Attribute):
            parts.insert(0, target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            name = imported.get(target.id) if not parts else (parts[-1] if target.id in aliases else None)
            if name in {"raises", "fail", "skip", "skipif", "xfail", "parametrize"}:
                controls.append(ast.dump(node, include_attributes=False))
    if not assertions and not any("raises" in control or "fail" in control for control in controls):
        raise ValueError("probe correction needs preserved observable assertions")
    imports = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        scope, parent = [], parents.get(id(node))
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope.append((type(parent).__name__, parent.name))
            parent = parents.get(id(parent))
        for alias in node.names:
            local = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
            imports.append((local, type(node).__name__, getattr(node, 'module', None),
                            getattr(node, 'level', None), alias.name, tuple(reversed(scope))))
    return (tuple(identities), tuple(assertions), tuple(controls), tuple(calls), tuple(bindings), tuple(flow),
            frozenset(import_dependencies), tuple(imports))


def validate_probe_correction_sources(old_source: dict, new_source: dict) -> None:
    if old_source.get("probeId") == new_source.get("probeId"):
        raise ValueError("probe correction requires distinct replacement source")
    if old_source.get("criterionIds") != new_source.get("criterionIds"):
        raise ValueError("probe correction must retain the same criteria")
    old, new = _source_fingerprint(old_source), _source_fingerprint(new_source)
    # Existing subject calls must remain, in order and with their result
    # bindings. New setup calls (for example chdir) and argument expressions
    # may change, subject to explicit host-observed source rationale.
    remaining = iter(new[3])
    calls_preserved = all(any(item == prior for item in remaining) for prior in old[3])
    if not calls_preserved or old[:3] != new[:3] or old[4:6] != new[4:6]:
        differences = []
        for index, category in ((0, "testIdentities"), (1, "assertions"), (2, "pytestControls"),
                                (4, "observedBindings"), (5, "controlFlow")):
            if old[index] == new[index]:
                continue
            difference = _first_difference(category, old[index], new[index])
            if category == "assertions":
                difference["originalLocation"] = _assertion_location(old_source, difference["index"])
                difference["replacementLocation"] = _assertion_location(new_source, difference["index"])
            differences.append(difference)
        if not calls_preserved:
            # Match the same subsequence rule as admission, not raw index
            # equality: additional setup calls are still permitted.
            cursor = 0
            for index, prior in enumerate(old[3]):
                while cursor < len(new[3]) and new[3][cursor] != prior:
                    cursor += 1
                if cursor == len(new[3]):
                    differences.append(_first_difference("subjectCalls", old[3], new[3], index=index))
                    break
                cursor += 1
        raise ProbeCorrectionMismatch(
            "probe correction must preserve test identities and assertion predicates/expectations",
            old_source=old_source, differences=differences)
    # Keep the origins of the original observed dependencies. New unrelated
    # setup imports are allowed, but rebinding an unchanged call to a different
    # module/member (including a local shadow or wildcard) is not a fixture fix.
    old_imports = tuple(binding for binding in old[7] if binding[0] in old[6] or binding[0] == '*')
    new_imports = tuple(binding for binding in new[7] if binding[0] in old[6] or binding[0] == '*')
    if old_imports != new_imports:
        raise ProbeCorrectionMismatch("probe correction must preserve observed import bindings",
            old_source=old_source, differences=[_first_difference("importBindings", old_imports, new_imports)])


def validate_probe_correction_history(receipts: list | tuple, patch_digest: str) -> None:
    if not receipts or not any(receipt.get("patchDigest") == patch_digest for receipt in receipts):
        raise ValueError("probe correction requires current-candidate failed execution")
    for receipt in receipts:
        evidence, report = receipt.get("evidence"), receipt.get("testReport")
        if (receipt.get("status") != "failed" or not isinstance(evidence, dict)
                or evidence.get("status") != "failed" or type(evidence.get("exitCode")) is not int
                or evidence["exitCode"] != 1 or evidence.get("failureReason") is not None
                or not validate_pytest_case_report(report)):
            raise ValueError("passed, inconclusive or unclassified probe history cannot be corrected")
        # Exception class describes execution, not whether a fixture supplied
        # the intended inputs. Either classified failure may be explicitly
        # corrected under the source/identity/execution/publication gates;
        # unexplained failures still cannot authorize retirement.
        if any(case["outcome"] == "failed" and case["failureKind"] not in {"assertion", "exception"}
               for case in report["caseReport"]["cases"]):
            raise ValueError("unknown failures cannot be retired as fixture corrections")


def validate_probe_correction_execution(old_receipts: list | tuple, new_receipt: dict, patch_digest: str,
                                       *, independently_adjudicated: bool = False) -> None:
    """Validate execution lineage, without granting completion approval.

    Only a caller that validated a separate source-bound probe_error authority
    can retain a classified exception as an unsatisfied correction successor.
    Ordinary author corrections keep their original admission boundary.
    """
    validate_probe_correction_history(old_receipts, patch_digest)
    report, evidence = new_receipt.get("testReport"), new_receipt.get("evidence")
    if (new_receipt.get("patchDigest") != patch_digest
            or new_receipt.get("status") not in {"passed", "failed"}
            or not isinstance(evidence, dict) or evidence.get("status") != new_receipt["status"]
            or type(evidence.get("exitCode")) is not int
            or evidence["exitCode"] != (0 if new_receipt["status"] == "passed" else 1)
            or evidence.get("failureReason") is not None or not validate_pytest_case_report(report)
            or any(case["outcome"] == "failed" and case["failureKind"] not in (
                       {"assertion", "exception"} if independently_adjudicated else {"assertion"})
                   for case in report["caseReport"]["cases"])):
        raise ValueError("replacement probe needs a conclusive current-candidate execution")
    current = [receipt for receipt in old_receipts if receipt["patchDigest"] == patch_digest]
    for old in current:
        if (old["evidence"].get("argv") != evidence.get("argv")
                or old["evidence"].get("commandDigest") != evidence.get("commandDigest")
                or [case["nodeId"] for case in old["testReport"]["caseReport"]["cases"]]
                   != [case["nodeId"] for case in report["caseReport"]["cases"]]):
            raise ValueError("probe correction must retain the actual test identities and approved command")
