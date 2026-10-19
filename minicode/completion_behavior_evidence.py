"""Bind coverage claims to host-observed execution, not passing source text.

This checks execution identity only. Whether inputs and expectations establish
an obligation remains a semantic judgment; no code token is a business oracle.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from minicode.permissions import PermissionDeniedError


def source_definitions(root, names, permissions=None):
    """Resolve original-objective symbols against readable candidate definitions."""
    root = Path(root).resolve()
    found = {name: [] for name in names}
    if not found:
        return found
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if (path.is_symlink() or any(part in {".git", ".venv", "__pycache__"} for part in relative.parts)
                or path.name == "__minicode_review_probe__.py"):
            continue
        if not path.resolve().is_relative_to(root):
            continue
        try:
            if permissions is not None:
                permissions.ensure_path_access(str(path), "read")
            data = path.read_bytes()
            tree = ast.parse(data)
        except (OSError, SyntaxError, ValueError, PermissionDeniedError):
            continue
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        module = ".".join(relative.with_suffix("").parts)

        def visit(nodes, scope=()):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qualified = ".".join((*scope, node.name))
                    if not isinstance(node, ast.ClassDef):
                        aliases = {node.name, qualified, module + "." + qualified}
                        definition = {"path": relative.as_posix(), "sourceDigest": digest,
                            "qualname": qualified, "firstLine": min(
                                [node.lineno, *(item.lineno for item in node.decorator_list)])}
                        for name in names:
                            if name in aliases:
                                found[name].append(definition)
                    visit(node.body, (*scope, node.name))
                else:
                    for field in ("body", "orelse", "finalbody"):
                        visit(getattr(node, field, ()), scope)
        visit(tree.body)
    return found


def _identity_matches(call, definition):
    return call.get("identityVerified") is True and all(
        call.get(key) == definition[key] for key in ("path", "sourceDigest", "qualname", "firstLine"))


def _has_path(calls, symbols, definitions):
    """All named endpoints must occur on one actual invocation ancestry."""
    if not symbols:
        return True
    by_id = {call["callId"]: call for call in calls}
    for end in calls:
        chain, current, seen = [], end, set()
        while current is not None and current["callId"] not in seen:
            seen.add(current["callId"])
            chain.append(current)
            current = by_id.get(current.get("parentCallId"))
        pending = iter(reversed(chain))
        if all(any(any(_identity_matches(call, definition)
                       for definition in definitions.get(name, ())) for call in pending)
               for name in symbols):
            return True
    return False


def executed_witness(receipt, source, first, last, *, symbols=(), definitions=None, exact=False):
    """Return actual case/assertion identities, or a precise nonapproval reason."""
    report = receipt.get("testReport") or {}
    execution = report.get("executionReport")
    if not isinstance(execution, dict):
        return None, "The receipt has no host execution trace; passing pytest alone cannot certify this witness."
    from minicode.pytest_execution_evidence import validate_execution_report
    if not validate_execution_report(report):
        return None, "The host execution trace is invalid or incomplete."
    outcomes = {case["nodeId"]: case["outcome"]
                for case in (report.get("caseReport") or {}).get("cases", ())}
    if not any(case.get("complete") and outcomes.get(case["nodeId"]) == "passed"
               for case in execution["cases"]):
        limitations = sorted({reason for case in execution["cases"] for reason in case.get("limitations", ())})
        return None, "No passing case has complete host execution evidence: " + ", ".join(limitations or ["no passing case"])
    binding = receipt.get("executionBinding") or {}
    target = binding.get("probeTarget")
    matches = []
    for case in execution["cases"]:
        if not case.get("complete") or outcomes.get(case["nodeId"]) != "passed":
            continue
        assertions = [item for item in case["assertions"]
            if item["path"] == target and item["sourceDigest"] == source["sourceDigest"]
            and not item.get("raised")
            and ((item["startLine"], item["endLine"]) == (first, last) if exact else
                 first <= item["startLine"] <= item["endLine"] <= last)]
        if assertions and _has_path(case["calls"], symbols, definitions or {}):
            matches.extend({"nodeId": case["nodeId"], "assertionId": item["assertionId"],
                            "executionDigest": execution["digest"]} for item in assertions)
    if matches:
        return matches, None
    if symbols:
        return None, ("No passed case executes the original requirement's candidate invocation path "
            + " -> ".join(symbols) + " together with the cited assertion. Helpers or replaced functions cannot substitute.")
    return None, "No passed case actually executes the cited assertion in this exact probe source."


__all__ = ["source_definitions", "executed_witness"]
