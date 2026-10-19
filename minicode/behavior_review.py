"""One-round behavior review for the parent-loop demo path.

The reviewer answers a single question: are the required behaviors implemented?
Evidence is one or two tests the reviewer writes and the host runs. Coverage
audit and per-probe adjudication stay on the other path; this module does not
call them.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from minicode.implementation_execution import implementation_candidate_digest
from minicode.review_probe_execution import run_isolated_review_probe, select_review_probe_check
from minicode.reviewer_verification import ReviewerProbe


_SYSTEM = (
    "BEHAVIOR_REVIEW\n"
    "You are the only reviewer. Answer one question: are the required behaviors implemented?\n"
    "Reply with one JSON object and nothing else. Keys are exactly status, gaps, files, probes.\n"
    "status is pass or fail. gaps is at most four short sentences naming missing behavior.\n"
    "files is the related workspace paths. probes is one or two pytest modules, each with name and code.\n"
    "A failing test is a gap. A passing test is a pass. Do not list obligations, line numbers, or evidence pages.\n"
    "If the packet contains an executor pushback, fix only the named test and keep the real behavior check."
)
_MAX_REPAIR_ROUNDS = 2
_TRANSPORT = (TimeoutError, ConnectionError, OSError)


def _probe_status(result):
    """Pytest's exit code is the demo-path evidence.

    The heavy review path also requires a behavior witness. This path does not:
    exit 0 passes, exit 1 is a gap, and a missing process result is a host retry.
    """
    status = result.get("status")
    if status in {"passed", "failed"}:
        return status
    if result.get("reason") != "review_probe_collection_or_runtime_unavailable":
        return "inconclusive"
    code = (result.get("evidence") or {}).get("exitCode")
    if code == 0:
        return "passed"
    if code == 1:
        return "failed"
    return "inconclusive"


def _parse_verdict(text):
    if not isinstance(text, str) or not text.strip():
        return None, "review output was empty"
    start = text.find("{")
    if start < 0:
        return None, "review output had no JSON object"
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as error:
        return None, "review JSON could not be parsed: " + error.msg
    if not isinstance(value, dict) or set(value) != {"status", "gaps", "files", "probes"}:
        return None, "review JSON must contain status, gaps, files, and probes"
    if value["status"] not in {"pass", "fail"}:
        return None, "status must be pass or fail"
    gaps, files, probes = value["gaps"], value["files"], value["probes"]
    if (not isinstance(gaps, list) or len(gaps) > 4
            or any(not isinstance(item, str) or not item.strip() or "\n" in item for item in gaps)):
        return None, "gaps must be at most four single-line sentences"
    if not isinstance(files, list) or any(not isinstance(item, str) or not item.strip() for item in files):
        return None, "files must be a list of paths"
    if not isinstance(probes, list) or not 1 <= len(probes) <= 2:
        return None, "probes must contain one or two tests"
    cleaned = []
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) != {"name", "code"}:
            return None, "each probe needs name and code"
        name, code = probe["name"], probe["code"]
        if not isinstance(name, str) or not name.startswith("test_") or not isinstance(code, str):
            return None, "probe name must start with test_"
        try:
            created = ReviewerProbe.create(criterion_ids=("behavior",), scenario=name, code=code)
        except ValueError as error:
            return None, "probe is not a pytest module: " + str(error)
        cleaned.append({"name": name, "code": created.code, "probe": created})
    return {"status": value["status"], "gaps": [item.strip() for item in gaps],
            "files": [item.strip() for item in files], "probes": cleaned}, None


class BehaviorReview:
    """Stateless reviewer packet, stateful only for the repair cap and last probes."""

    def __init__(self, spec):
        self.spec = spec
        self.repair_rounds = 0
        self.repair_probes: list[dict] = []
        self._baseline: dict[str, str] | None = None

    def assess(self, *, model, tools, context, agent_budget, event_sink,
               cancellation_token, deadline_monotonic, checks_receipt, executor_note=None):
        del tools, agent_budget, event_sink, cancellation_token
        digest = implementation_candidate_digest(self.spec, context)
        if (not isinstance(checks_receipt, dict) or checks_receipt.get("status") != "passed"
                or checks_receipt.get("patchDigest") != digest):
            return self._inconclusive(digest, "review_host_retry",
                                      ["Public checks were not current, so the review did not start."])
        packet = self._packet(context, checks_receipt, executor_note)
        parsed, transport = self._solicit(model, packet, deadline_monotonic)
        if transport:
            return self._inconclusive(digest, "review_transport_retry",
                                      ["The review transport failed and will be retried from this checkpoint."])
        if parsed is None:
            parsed, transport = self._solicit(
                model, packet + "\nFormat error: reply with the JSON object only.\n", deadline_monotonic)
            if transport:
                return self._inconclusive(digest, "review_transport_retry",
                                          ["The review transport failed and will be retried from this checkpoint."])
            if parsed is None:
                return self._inconclusive(digest, "review_format_retry",
                                          ["The review reply was not usable and will be requested again."])
        executed = self._run_probes(parsed["probes"], context, deadline_monotonic)
        if executed is None or any(row["status"] == "inconclusive" for row in executed):
            return self._inconclusive(digest, "review_host_retry",
                                      ["The review tests could not be run and will be retried."])
        failing = [row for row in executed if row["status"] == "failed"]
        evidence_fail = bool(failing)
        gaps = list(parsed["gaps"])
        if evidence_fail and not gaps:
            gaps = ["A reviewer test failed."]
        files = [path for path in parsed["files"] if path in self.spec.write_paths]
        self._baseline = self._snapshot(context.cwd)
        if not evidence_fail:
            self.repair_probes = []
            return {"completionReviewVersion": 1, "status": "approved", "patchDigest": digest,
                    "findings": [], "files": files, "repairProbes": [], "requirements": [],
                    "reason": "behavior_review_passed", "canContinue": True}
        if self.repair_rounds >= _MAX_REPAIR_ROUNDS:
            self.repair_probes = failing
            return {"completionReviewVersion": 1, "status": "needs_human", "patchDigest": digest,
                    "findings": gaps, "files": files, "repairProbes": failing, "requirements": [],
                    "reason": "repair_cap", "canContinue": False}
        self.repair_rounds += 1
        self.repair_probes = failing
        return {"completionReviewVersion": 1, "status": "changes_required", "patchDigest": digest,
                "findings": gaps, "files": files, "repairProbes": failing, "requirements": [],
                "reason": "behavior_gap", "canContinue": True}

    def _inconclusive(self, digest, reason, findings):
        return {"completionReviewVersion": 1, "status": "inconclusive", "patchDigest": digest,
                "findings": findings, "files": [], "repairProbes": list(self.repair_probes),
                "requirements": [], "reason": reason, "canContinue": True}

    def _snapshot(self, cwd):
        root = Path(cwd)
        found = {}
        for relative in self.spec.write_paths:
            path = root / relative
            found[relative] = path.read_text(encoding="utf-8") if path.is_file() else ""
        return found

    def _packet(self, context, checks_receipt, executor_note):
        current = self._snapshot(context.cwd)
        before = self._baseline or {path: "" for path in current}
        chunks = []
        for path in sorted(current):
            if before.get(path, "") == current[path]:
                continue
            chunks.append("".join(difflib.unified_diff(
                before.get(path, "").splitlines(keepends=True),
                current[path].splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path)))
        checks = checks_receipt.get("checks") if isinstance(checks_receipt.get("checks"), list) else []
        results = "\n".join(
            f"{row.get('checkId')}: {row.get('status')}" for row in checks if isinstance(row, dict)
        ) or checks_receipt.get("status", "unknown")
        lines = ["Task:\n" + self.spec.objective, "Code diff:\n" + ("".join(chunks) or "(no file changes)"),
                 "Public test results:\n" + results]
        if isinstance(executor_note, str) and executor_note.strip():
            lines.append("Executor pushback:\n" + executor_note.strip())
            if self.repair_probes:
                lines.append("Previous failing tests:\n" + "\n\n".join(
                    row.get("code", "") for row in self.repair_probes))
        return "\n\n".join(lines)

    def _solicit(self, model, packet, deadline_monotonic):
        del deadline_monotonic
        messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": packet}]
        last = None
        for _attempt in range(2):
            try:
                step = model.next(messages, disable_tools=True, json_object_override=True)
            except _TRANSPORT as error:
                last = error
                continue
            parsed, _error = _parse_verdict(getattr(step, "content", None))
            if parsed is not None:
                return parsed, False
            last = "format"
            return None, False
        return None, last is not None and last != "format"

    def _run_probes(self, probes, context, deadline_monotonic):
        target = select_review_probe_check(self.spec.checks, self.spec.test_paths)
        if target is None:
            return None
        check, test_path = target
        from minicode.permissions import PermissionManager
        from minicode.workflow_runner import _deterministic_verifier_for_context
        from minicode.workflow_workspace import WorkflowWorkspaceTransaction

        permissions = context.permissions or PermissionManager(context.cwd, prompt=None)
        try:
            with WorkflowWorkspaceTransaction(
                context.cwd, deadline_monotonic=deadline_monotonic, timeout_seconds=None,
            ) as transaction:
                frozen = transaction.freeze(allowed_paths=self.spec.write_paths, paths_relative_to_cwd=True)
                verifier = _deterministic_verifier_for_context(
                    context, trusted_git_executable=transaction.trusted_git_executable,
                    untrusted_executable_root=str(transaction.source_root))
                rows = []
                for item in probes:
                    result = run_isolated_review_probe(
                        probe=item["probe"], frozen=frozen, check=check, test_path=test_path,
                        verifier=verifier, read_roots=(".",), forbidden_paths=(),
                        run_kwargs={
                            "workflow_id": "wf_behavior_review", "task_id": "task_behavior_review",
                            "execution_assignment_id": "asg_behavior_review",
                            "patch_digest": frozen.patch_digest, "permissions": permissions,
                            "permission_cwd": context.cwd, "deadline_monotonic": deadline_monotonic,
                        })
                    rows.append({"name": item["name"], "code": item["code"],
                                 "probeId": item["probe"].probe_id,
                                 "status": _probe_status(result)})
                return rows
        except (OSError, ValueError):
            return None
