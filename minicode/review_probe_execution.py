"""Run independent review source through an already authorized pytest command.

Only a disposable verification clone receives a separate host-owned test module.
Candidate files stay at their original paths; the sole authorized pytest target
is replaced by that fixed sibling in a separately bound execution command.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import re
from typing import Sequence

from minicode.deterministic_verifier import VerificationCheck


_PROBE_FILENAME = "__minicode_review_probe__.py"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def derive_review_probe_check(check: VerificationCheck, test_path: str) -> tuple[VerificationCheck, str]:
    """Derive one host-controlled target without granting arbitrary argv edits."""
    if select_review_probe_check((check,), (test_path,)) is None:
        raise ValueError("review test target is not bound to the authorized command")
    path = Path(test_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("review test target must be relative to the candidate")
    probe_path = path.with_name(_PROBE_FILENAME)
    if path == probe_path:
        raise ValueError("review test target collides with the host-owned probe target")
    probe_target = probe_path.as_posix()
    return replace(check, argv=tuple(probe_target if arg == test_path else arg
                                    for arg in check.argv)), probe_target


def _check_document(check: VerificationCheck) -> dict:
    return {"checkId": check.check_id, "kind": check.kind, "argv": list(check.argv),
            "timeoutSeconds": check.timeout_seconds,
            "commandDigest": "sha256:" + hashlib.sha256("\0".join(check.argv).encode()).hexdigest()}


def _parse_bound_check(value: object) -> VerificationCheck:
    fields = {"checkId", "kind", "argv", "timeoutSeconds", "commandDigest"}
    if (not isinstance(value, dict) or set(value) != fields
            or not isinstance(value["checkId"], str) or not isinstance(value["kind"], str)
            or type(value["timeoutSeconds"]) is not int
            or not isinstance(value["argv"], list) or not value["argv"]
            or any(not isinstance(arg, str) for arg in value["argv"])):
        raise ValueError("review execution check binding is malformed")
    check = VerificationCheck(value["checkId"], value["kind"], tuple(value["argv"]), value["timeoutSeconds"])
    if _check_document(check) != value:
        raise ValueError("review execution check digest is inconsistent")
    return check


def validate_review_probe_execution_binding(receipt: dict, checks: Sequence[VerificationCheck] | None = None,
                                           *, probe_source_digest: str | None = None) -> None:
    """Validate derivation and evidence; supplied checks also establish authority.

    With checks=None this validates structure only. A caller with an immutable
    check contract must supply it; the receipt's own baseCheck is not authority.
    Historical receipts without a binding require a separate legacy policy.
    """
    binding = receipt.get("executionBinding") if isinstance(receipt, dict) else None
    fields = {"bindingVersion", "patchDigest", "baseCheck", "derivedCheck", "originalTarget",
              "probeTarget", "originalTargetDigest", "probeSourceDigest"}
    if (not isinstance(binding, dict) or set(binding) != fields
            or type(binding["bindingVersion"]) is not int or binding["bindingVersion"] != 1):
        raise ValueError("review execution binding is malformed")
    for field in ("patchDigest", "originalTargetDigest", "probeSourceDigest"):
        if not isinstance(binding[field], str) or _DIGEST.fullmatch(binding[field]) is None:
            raise ValueError("review execution binding digest is invalid")
    if binding["patchDigest"] != receipt.get("patchDigest"):
        raise ValueError("review execution binding has a different candidate")
    if probe_source_digest is not None and binding["probeSourceDigest"] != probe_source_digest:
        raise ValueError("review execution binding has a different probe source")
    base = _parse_bound_check(binding["baseCheck"])
    actual = _parse_bound_check(binding["derivedCheck"])
    expected, target = derive_review_probe_check(base, binding["originalTarget"])
    if actual != expected or binding["probeTarget"] != target:
        raise ValueError("review execution is not the fixed host-derived command")
    if checks is not None and base not in checks:
        raise ValueError("review execution base check is not authorized")
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict) or any(
            evidence.get(key) != binding["derivedCheck"][key]
            for key in ("checkId", "kind", "argv", "commandDigest")):
        raise ValueError("review execution evidence does not match its derived command")


class _ProbeCommandPermissions:
    """Authorize exactly one host derivation through the original command grant."""

    def __init__(self, parent, base: VerificationCheck, derived: VerificationCheck, cwd: str):
        self._parent, self._base, self._derived, self._cwd = parent, base, derived, cwd

    def __getattr__(self, name):
        return getattr(self._parent, name)

    def ensure_command(self, program: str, args: list[str], cwd: str) -> None:
        if (program, *args) != self._derived.argv or cwd != self._cwd:
            raise ValueError("review execution permission is limited to the derived command")
        self._parent.ensure_command(self._base.argv[0], list(self._base.argv[1:]), cwd)


def review_probe_authorized_checks(checks, permissions, permission_cwd):
    """Expose the original grant only for this exact host execution capability.

    Outer verification guards can validate the base grant while the verifier
    still receives and records the real derived command. Ordinary tool commands
    and lookalike permission objects receive no additional authority.
    """
    if type(permissions) is not _ProbeCommandPermissions:
        return checks
    if (not isinstance(permission_cwd, str) or not permission_cwd.strip()
            or permission_cwd != permissions._cwd or checks != (permissions._derived,)):
        raise ValueError("review scope bridge requires its exact checks and approval root")
    selected = select_review_probe_check((permissions._base,), permissions._base.argv)
    if selected is None or derive_review_probe_check(*selected)[0] != permissions._derived:
        raise ValueError("review scope bridge requires the fixed host command derivation")
    return (permissions._base,)


def select_review_probe_check(checks, test_paths):
    """Choose a single explicit pytest module, never broaden selectors or scope."""
    for path in reversed(tuple(test_paths)):
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        for check in checks:
            if check.kind != "tests":
                continue
            argv = check.argv
            if argv[:3] in (("python", "-m", "pytest"), ("python3", "-m", "pytest")):
                args = argv[3:]
            elif argv[:1] == ("pytest",):
                args = argv[1:]
            else:
                continue
            options = {"-q", "-qq", "-v", "-vv", "-s", "-x", "--disable-warnings",
                       "--strict-config", "--strict-markers"}
            targets = [arg for arg in args if not arg.startswith("-")]
            if (targets == [path] and all(arg in options or arg == path
                    or arg.startswith(("--color=", "--maxfail=", "--tb=")) for arg in args)):
                return check, path
    return None


def _file_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if set(relative.parts) & {".git", "__pycache__", ".pytest_cache"}:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run_isolated_review_probe(*, probe, frozen, check: VerificationCheck,
                             test_path: str, verifier, read_roots, forbidden_paths,
                             run_kwargs: dict) -> dict:
    derived_check, probe_path = derive_review_probe_check(check, test_path)
    if run_kwargs.get("patch_digest") != frozen.patch_digest:
        raise ValueError("review check must bind the actual frozen candidate")
    evidence, reports = [], []
    with frozen.verification_workspace(read_roots=read_roots, forbidden_paths=forbidden_paths) as cwd:
        root = Path(cwd).resolve()
        target = root / test_path
        if (target.is_symlink() or not target.is_file()
                or not target.resolve().is_relative_to(root)):
            raise ValueError("independent review test target is not an authorized regular file")
        original_digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        probe_target = root / probe_path
        if (probe_target.exists() or probe_target.is_symlink()
                or not probe_target.resolve().is_relative_to(root)):
            raise ValueError("host-owned review probe target collides with candidate files")
        # Exclusive creation also rejects a collision appearing after inspection.
        with probe_target.open("x", encoding="utf-8") as stream:
            stream.write(probe.code)
        before = _file_hashes(root)
        options = dict(run_kwargs)
        if options.get("permissions") is not None:
            options["permissions"] = _ProbeCommandPermissions(
                options["permissions"], check, derived_check, options.get("permission_cwd"))
        result = verifier.run(checks=(derived_check,), cwd=str(root), **options,
                              evidence_sink=lambda _key, value: evidence.append(value),
                              pytest_report_sink=reports.append)
        after = _file_hashes(root)
        changed = any(after.get(path) != digest for path, digest in before.items())
    observed = evidence[0] if len(evidence) == 1 else {}
    report = reports[0] if len(reports) == 1 else None
    status = result["status"]
    reason = None
    if changed:
        status, reason = "inconclusive", "review_probe_modified_candidate_files"
    elif (report is None or report["collected"] < 1
          or report["errors"] != 0 or report["skipped"] != 0):
        status, reason = "inconclusive", "review_probe_collection_or_runtime_unavailable"
    elif status == "passed" and (observed.get("exitCode") != 0 or report["passed"] < 1 or report["failed"] != 0):
        status, reason = "inconclusive", "review_probe_no_executed_passing_tests"
    elif status == "failed" and (observed.get("exitCode") != 1 or report["failed"] < 1):
        status, reason = "inconclusive", "review_probe_collection_or_runtime_unavailable"
    elif status == "inconclusive":
        reason = "review_probe_execution_unavailable"
    binding = {"bindingVersion": 1, "patchDigest": frozen.patch_digest,
               "baseCheck": _check_document(check), "derivedCheck": _check_document(derived_check),
               "originalTarget": test_path, "probeTarget": probe_path,
               "originalTargetDigest": original_digest, "probeSourceDigest": probe.source_digest}
    return {"probeId": probe.probe_id, "patchDigest": frozen.patch_digest,
            "status": status, "evidence": observed, "testReport": report,
            "executionBinding": binding,
            **({"reason": reason} if reason else {})}


__all__ = ["select_review_probe_check", "derive_review_probe_check", "run_isolated_review_probe",
           "validate_review_probe_execution_binding", "review_probe_authorized_checks"]
