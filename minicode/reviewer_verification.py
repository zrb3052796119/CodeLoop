"""Assignment-owned, independent checks of immutable review candidates.

The scheduler supplies the isolated execution callback. Model input contains
test source and semantic claims, never a command, path or evidence receipt.
Conclusive probes remain regression obligations across candidate revisions.
These checks establish execution evidence, not natural-language completeness.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import threading

from minicode.capability_contracts import (
    CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope,
)
from minicode.multi_agent_contracts import Assignment, WorkerRole
from minicode.review_probe_correction import (
    correction_artifact_id, validate_probe_correction_execution,
    validate_probe_correction_history, validate_probe_correction_sources, validate_pytest_case_report,
)
from minicode.review_probe_source import (
    MAX_REVIEW_PROBE_CRITERIA, MAX_REVIEW_PROBE_CRITERION_ID_CHARS, MAX_REVIEW_PROBE_SCENARIO_CHARS,
    review_probe_source_artifact_id,
)
from minicode.reviewer_result_submission import _channel_live
from minicode.task_deadline import raise_task_deadline
from minicode.tooling import ToolDefinition, ToolExecutionAbandoned, ToolResult


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ARTIFACT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,127}")


def _json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    encoded.encode("utf-8")
    return encoded


class ReviewCoverageError(ValueError):
    """Read-only host diagnostics; never an approval or changed probe binding."""

    def __init__(self, diagnostics: dict):
        self._encoded_diagnostics = _json(diagnostics)
        super().__init__(self._encoded_diagnostics)

    @property
    def diagnostics(self) -> dict:
        return json.loads(self._encoded_diagnostics)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be nonempty text without NUL")
    value.encode("utf-8")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("review candidate digest is invalid")
    return value


def _checkpoint(context: object) -> None:
    abandoned = getattr(context, "_tool_abandoned", None)
    if abandoned is not None and abandoned.is_set():
        raise ToolExecutionAbandoned("review probe owner was abandoned")
    raise_task_deadline(context)


@dataclass(frozen=True, slots=True)
class ReviewerProbe:
    probe_id: str
    criterion_ids: tuple[str, ...]
    scenario: str
    code: str
    source_digest: str

    @classmethod
    def create(cls, *, criterion_ids: Sequence[str], scenario: str, code: str):
        if (not isinstance(criterion_ids, (list, tuple))
                or not 1 <= len(criterion_ids) <= MAX_REVIEW_PROBE_CRITERIA
                or any(not isinstance(cid, str) or not cid.strip() or "\x00" in cid
                       or len(cid) > MAX_REVIEW_PROBE_CRITERION_ID_CHARS for cid in criterion_ids)
                or len(set(criterion_ids)) != len(criterion_ids)):
            raise ValueError("criterionIds must be distinct known criterion IDs")
        ids = tuple(sorted(criterion_ids))
        scenario = _text(scenario, "scenario")
        if len(scenario) > MAX_REVIEW_PROBE_SCENARIO_CHARS:
            raise ValueError("review scenario must contain at most 1200 characters")
        code = _text(code, "code")
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError) as error:
            raise ValueError("review probe must be valid Python test source") from error
        functions = (ast.FunctionDef, ast.AsyncFunctionDef)
        tests = [node for node in tree.body if isinstance(node, functions)
                 and node.name.startswith("test_")]
        tests.extend(method for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name.startswith("Test")
                     for method in node.body if isinstance(method, functions)
                     and method.name.startswith("test_"))
        if not tests:
            raise ValueError("review probe must define a pytest test function or Test class")
        source_digest = "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
        identity = _json({"criterionIds": list(ids), "scenario": scenario,
                          "sourceDigest": source_digest})
        return cls("review_probe_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                   ids, scenario, code, source_digest)

    def to_dict(self) -> dict:
        return {"probeVersion": 1, "resultType": "ReviewProbeSource.v1",
                "probeId": self.probe_id, "criterionIds": list(self.criterion_ids),
                "scenario": self.scenario, "code": self.code,
                "sourceDigest": self.source_digest}


@dataclass(frozen=True, slots=True)
class ReviewerProbeCorrection:
    replacement: ReviewerProbe
    corrects_probe_id: str
    reason: str
    source_evidence: tuple[dict, ...]


def _receipt(probe: ReviewerProbe, patch_digest: str, value: object) -> dict:
    """Validate the host callback's bytes without inferring evidence from prose."""
    if not isinstance(value, dict):
        raise ValueError("review execution did not return a receipt")
    result = json.loads(_json(value))
    if (result.get("probeId") != probe.probe_id
            or result.get("patchDigest") != patch_digest
            or result.get("status") not in {"passed", "failed", "inconclusive"}):
        raise ValueError("review receipt is not bound to this probe and candidate")
    for field, expected in (("sourceDigest", probe.source_digest),
                            ("criterionIds", list(probe.criterion_ids)),
                            ("scenario", probe.scenario)):
        if field in result and result[field] != expected:
            raise ValueError("review receipt source binding is inconsistent")
        result[field] = expected
    for field in ("sourceRef", "receiptRef", "evidenceRef"):
        if field in result and (not isinstance(result[field], str)
                                or _ARTIFACT.fullmatch(result[field]) is None):
            raise ValueError("review receipt artifact reference is invalid")
    if "executionBinding" in result:
        from minicode.review_probe_execution import validate_review_probe_execution_binding

        # The ledger validates retained source/command identity. External
        # authorization remains the scheduler's locked-check validation.
        validate_review_probe_execution_binding(result, probe_source_digest=probe.source_digest)
    evidence = result.get("evidence")
    if result["status"] == "inconclusive":
        # Unavailable/unfinished execution is retained but never grants coverage.
        return result
    if not isinstance(evidence, dict):
        raise ValueError("review receipt lacks real command evidence")
    report = result.get("testReport")
    counts = ("collected", "passed", "failed", "errors", "skipped")
    valid_report = (
        isinstance(report, dict) and set(report) in ({*counts, "reportDigest"}, {*counts, "reportDigest", "caseReport"},
            {*counts, "reportDigest", "caseReport", "executionReport"})
        and all(type(report[field]) is int and report[field] >= 0 for field in counts)
        and isinstance(report["reportDigest"], str) and _DIGEST.fullmatch(report["reportDigest"]) is not None
        and sum(report[field] for field in counts[1:]) == report["collected"]
        and ("caseReport" not in report or validate_pytest_case_report(report))
    )
    argv = evidence.get("argv")
    resolved = evidence.get("resolvedExecutable")
    exit_code = evidence.get("exitCode")
    from minicode.scheduler_audit import _validate_verification_stream

    if (type(evidence.get("evidenceVersion")) is not int or evidence["evidenceVersion"] != 1
            or evidence.get("kind") != "tests"
            or not isinstance(evidence.get("checkId"), str) or not evidence["checkId"]
            or not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
            or evidence.get("commandDigest") != "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest()
            or evidence.get("status") != result["status"]
            or type(exit_code) is not int
            or type(evidence.get("durationMs")) is not int or evidence["durationMs"] < 0
            or not _validate_verification_stream(evidence.get("stdout"))
            or not _validate_verification_stream(evidence.get("stderr"))):
        raise ValueError("review receipt command evidence is inconsistent")
    if (evidence.get("failureReason") is not None
            or not isinstance(resolved, str) or not Path(resolved).is_absolute()
            or (result["status"] == "passed" and exit_code != 0)
            or (result["status"] == "failed" and exit_code != 1)
            or not valid_report or report["collected"] < 1
            or report["errors"] != 0 or report["skipped"] != 0
            or (result["status"] == "passed" and (report["passed"] < 1 or report["failed"] != 0))
            or (result["status"] == "failed" and report["failed"] < 1)):
        # pytest usage/collection/infrastructure failures are not counterexamples.
        result["status"] = "inconclusive"
        result["reason"] = "probe_execution_not_conclusive"
    return result


class ReviewerProbeLedger:
    """Append-only host observations; no model-facing delete or replace port."""

    def __init__(self):
        self._lock = threading.RLock()
        self._owner: tuple[str, str] | None = None
        self._definitions: dict[str, ReviewerProbe] = {}
        self._active: dict[str, ReviewerProbe] = {}
        self._attempts: list[str] = []
        self._current: dict[tuple[str, str], str] = {}
        self._retired: set[str] = set()
        self._corrections: list[str] = []

    def _bind(self, assignment: Assignment) -> None:
        owner = (assignment.workflow_id, assignment.task_id)
        with self._lock:
            if self._owner is not None and self._owner != owner:
                raise ValueError("review probe ledger belongs to another workflow")
            self._owner = owner

    def probes(self) -> tuple[ReviewerProbe, ...]:
        with self._lock:
            return tuple(self._active.values())

    def record(self, probe: ReviewerProbe, patch_digest: str, receipt: dict) -> dict:
        _digest(patch_digest)
        if not isinstance(probe, ReviewerProbe) or probe != ReviewerProbe.create(
            criterion_ids=probe.criterion_ids, scenario=probe.scenario, code=probe.code,
        ):
            raise ValueError("review probe definition is invalid")
        result = _receipt(probe, patch_digest, receipt)
        encoded = _json(result)
        with self._lock:
            if probe.probe_id in self._retired:
                raise ValueError("corrected probe remains archived and cannot be reactivated")
            existing = self._definitions.get(probe.probe_id)
            if existing is not None and existing != probe:
                raise ValueError("review probe definition is immutable")
            self._definitions[probe.probe_id] = probe
            self._attempts.append(encoded)
            self._current[(patch_digest, probe.probe_id)] = encoded
            if result["status"] in {"passed", "failed"}:
                self._active[probe.probe_id] = probe
        return json.loads(encoded)

    def current_receipts(self, patch_digest: str) -> tuple[dict, ...]:
        _digest(patch_digest)
        with self._lock:
            return tuple(json.loads(value) for (digest, _), value in self._current.items()
                         if digest == patch_digest)

    def feedback(self) -> str:
        with self._lock:
            return _json({"probes": [probe.to_dict() for probe in self._definitions.values()],
                          "attempts": [json.loads(value) for value in self._attempts],
                          "activeProbeIds": list(self._active), "retiredProbeIds": sorted(self._retired),
                          "corrections": [json.loads(value) for value in self._corrections]})

    def corrections(self) -> tuple[dict, ...]:
        with self._lock:
            return tuple(json.loads(value) for value in self._corrections)

    def approval_coverage(self, *, patch_digest: str, criterion_id: str,
                          probe_ids: Sequence[str]) -> tuple[str, ...]:
        _digest(patch_digest)
        if (not isinstance(probe_ids, (tuple, list)) or not probe_ids
                or any(not isinstance(value, str) for value in probe_ids)
                or len(set(probe_ids)) != len(probe_ids)):
            raise ValueError("coverage needs distinct current probe IDs")
        with self._lock:
            invalid = []
            for probe_id in probe_ids:
                probe = self._active.get(probe_id)
                definition = self._definitions.get(probe_id)
                current = self._current.get((patch_digest, probe_id))
                status = json.loads(current)["status"] if current is not None else None
                reasons = []
                if definition is None:
                    reasons.append("unknown")
                else:
                    if probe_id in self._retired:
                        reasons.append("retired")
                    elif probe is None:
                        reasons.append("inactive")
                    if criterion_id not in definition.criterion_ids:
                        reasons.append("criterion_mismatch")
                    if current is None:
                        reasons.append("stale")
                    elif status != "passed":
                        reasons.append("not_passed")
                if reasons:
                    invalid.append({"probeId": probe_id, "reasons": reasons,
                        "registeredCriterionIds": list(definition.criterion_ids) if definition else [],
                        "currentStatus": status})
            if invalid:
                available = []
                for probe in self._active.values():
                    current = self._current.get((patch_digest, probe.probe_id))
                    if current is not None and json.loads(current)["status"] == "passed":
                        available.append({"probeId": probe.probe_id,
                                          "criterionIds": list(probe.criterion_ids)})
                raise ReviewCoverageError({"errorCode": "review_probe_coverage_mismatch",
                    "patchDigest": patch_digest, "criterionId": criterion_id,
                    "invalidProbeReferences": invalid, "availablePassedProbes": available})
        return tuple(probe_ids)

    def require_all_current_passed(self, patch_digest: str) -> tuple[dict, ...]:
        _digest(patch_digest)
        with self._lock:
            if not self._active:
                raise ValueError("approval requires at least one real independent probe")
            results = []
            for probe in self._active.values():
                current = self._current.get((patch_digest, probe.probe_id))
                if current is None or json.loads(current)["status"] != "passed":
                    raise ValueError("all retained review probes must pass this candidate")
                results.append(json.loads(current))
            return tuple(results)

    def replay(self, *, patch_digest: str, run_probe: Callable, context: object) -> tuple[dict, ...]:
        _digest(patch_digest)
        results = []
        with self._lock:
            for probe in self.probes():
                _checkpoint(context)
                # Discard an older pass for this revision before a new attempt.
                self._current.pop((patch_digest, probe.probe_id), None)
                receipt = run_probe(probe, context)
                _checkpoint(context)
                results.append(self.record(probe, patch_digest, receipt))
        return tuple(results)


class ReviewerCheckCapability:
    def __init__(self, *, assignment: Assignment, channel, ledger: ReviewerProbeLedger,
                 patch_digest: str, run_probe: Callable[[ReviewerProbe, object], dict],
                 record_correction: Callable | None = None):
        if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.REVIEWER:
            raise ValueError("review checks require a Reviewer Assignment")
        if not isinstance(ledger, ReviewerProbeLedger) or not callable(run_probe) or not _channel_live(channel):
            raise ValueError("review checks require live host capabilities")
        ledger._bind(assignment)
        self._assignment = assignment
        self._channel = channel
        self._ledger = ledger
        self._patch_digest = _digest(patch_digest)
        self._run_probe = run_probe
        if record_correction is not None and not callable(record_correction):
            raise ValueError("correction publisher must be a host capability")
        self._record_correction = record_correction
        self._closed = False
        self._criterion_ids = {item.criterion_id for item in assignment.acceptance_criteria}
        self.tool = ToolDefinition(
            name="review_test",
            description=("Independently test a public requirement on an isolated copy of the frozen candidate. "
                         "Supply a concrete scenario and an independent complete pytest module defining test_ functions; "
                         "the host installs it at an authorized test target only in the disposable copy and runs "
                         "the existing approved command. Source cannot "
                         "select paths or commands, alter the candidate, or authorize writeback. Real failed and "
                         "passed probes are retained and rerun on repaired candidates; invalid execution is not coverage. "
                         "To explicitly correct a test fixture with classified assertion or exception failures, supply correctsProbeId, "
                         "correctionReason and sourceEvidence together. Test identities and assertion predicates "
                         "must remain unchanged. Both executions and their source are retained; unexplained failures "
                         "cannot be replaced, and a failing corrected probe still blocks approval. "
                         "Use review_probe_read with action=list to find retained IDs, then action=source, "
                         "receipt or correction to recover exact evidence in pages."),
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {"criterionIds": {"type": "array", "minItems": 1,
                    "maxItems": len(self._criterion_ids), "uniqueItems": True,
                    "items": {"type": "string", "maxLength": MAX_REVIEW_PROBE_CRITERION_ID_CHARS,
                              "enum": sorted(self._criterion_ids)}},
                    "scenario": {"type": "string", "minLength": 1, "maxLength": MAX_REVIEW_PROBE_SCENARIO_CHARS},
                    "code": {"type": "string", "minLength": 1},
                    "correctsProbeId": {"type": "string", "minLength": 1, "maxLength": 128},
                    "correctionReason": {"type": "string", "minLength": 1, "maxLength": 1200},
                    "sourceEvidence": {"type": "array", "minItems": 1, "maxItems": 20,
                        "items": {"type": "object", "additionalProperties": False,
                            "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 1200},
                                "startLine": {"type": "integer", "minimum": 1},
                                "endLine": {"type": "integer", "minimum": 1}},
                            "required": ["path", "startLine", "endLine"]}}},
                "required": ["criterionIds", "scenario", "code"]},
            validator=self._validate, run=self._run,
            capability_profile=CapabilityProfile(domain=CapabilityDomain.EXECUTION, scope=CapabilityScope.WRITE,
                effects=CapabilityEffects(read_only=False, destructive=False, external=False,
                                          executes_code=True, delegates=False),
                purposes=("Check a frozen candidate in a disposable test workspace",)),
        )
        self.read_tool = ToolDefinition(
            name="review_probe_read",
            description=("Read retained probe evidence belonging to this live Reviewer workflow. "
                         "action=list discovers IDs, status and record counts (offset counts probes; at most 20 per page). "
                         "action=source returns original complete code in Unicode-character pages. "
                         "action=receipt or correction returns immutable JSON text for recordIndex (default 0), "
                         "also in Unicode-character pages. For these actions offset defaults to 0, limit to 2000 "
                         "and nextOffset identifies the next page; concatenate text and verify digest. "
                         "All failed and retired evidence remains readable. Receipts retain the captured output; "
                         "a truncated stdout/stderr preview is not the full log. This tool does not execute tests "
                         "or change coverage, and cannot read arbitrary artifacts or other workflows."),
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["list", "source", "receipt", "correction"]},
                    "probeId": {"type": "string", "pattern": r"^review_probe_[0-9a-f]{24}$",
                                "description": "Required except for list; discover exact IDs with action=list."},
                    "recordIndex": {"type": "integer", "minimum": 0,
                                    "description": "Only receipt/correction; stable index within this probe's records."},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 4000,
                              "description": "Unicode characters, or probes for list (capped at 20)."}},
                "required": ["action"]},
            validator=self._validate_read, run=self._read,
            capability_profile=CapabilityProfile(domain=CapabilityDomain.MEMORY, scope=CapabilityScope.READONLY,
                effects=CapabilityEffects(read_only=True, destructive=False, external=False,
                                          executes_code=False, delegates=False),
                purposes=("Recover this workflow's immutable probe source and execution evidence",)),
        )

    @staticmethod
    def _validate_read(value):
        if (not isinstance(value, dict) or not {"action"} <= set(value)
                or not set(value) <= {"action", "probeId", "recordIndex", "offset", "limit"}
                or not isinstance(value["action"], str)
                or value["action"] not in {"list", "source", "receipt", "correction"}):
            raise ValueError("review_probe_read requires a known action and only its declared fields")
        action = value["action"]
        if action == "list":
            if "probeId" in value or "recordIndex" in value:
                raise ValueError("list uses offset/limit, without probeId or recordIndex")
        elif (not isinstance(value.get("probeId"), str)
              or re.fullmatch(r"review_probe_[0-9a-f]{24}", value["probeId"]) is None):
            raise ValueError("probeId must be an exact retained ID; discover IDs with action=list")
        if action == "source" and "recordIndex" in value:
            raise ValueError("source is immutable and has no recordIndex")
        for field in ("offset", "recordIndex", "limit"):
            if field in value and (type(value[field]) is not int or value[field] < (1 if field == "limit" else 0)):
                raise ValueError(f"{field} must be a nonnegative integer (limit must be positive)")
        if value.get("limit", 1) > 4000:
            raise ValueError("limit must be at most 4000 Unicode characters")
        return dict(value)

    @staticmethod
    def _recovery(probe_id):
        return {"tool": "review_probe_read", "list": {"action": "list"},
                "source": {"action": "source", "probeId": probe_id},
                "receipts": {"action": "receipt", "probeId": probe_id, "recordIndex": 0},
                "paging": "Use offset/limit in Unicode characters and follow nextOffset until null."}

    def _read(self, request, context):
        self._active(context)
        request = self._validate_read(request)
        action, offset = request["action"], request.get("offset", 0)
        with self._ledger._lock:
            self._active(context)
            if action == "list":
                probes = list(self._ledger._definitions.values())
                if offset > len(probes):
                    return ToolResult(ok=False, output="error[review_probe_read_invalid]: offset exceeds probe count")
                end = min(offset + min(request.get("limit", 20), 20), len(probes))
                rows = []
                for probe in probes[offset:end]:
                    current = self._ledger._current.get((self._patch_digest, probe.probe_id))
                    rows.append({"probeId": probe.probe_id, "criterionCount": len(probe.criterion_ids),
                        "sourceRef": review_probe_source_artifact_id(probe.probe_id),
                        "sourceDigest": probe.source_digest, "sourceChars": len(probe.code),
                        "active": probe.probe_id in self._ledger._active,
                        "retired": probe.probe_id in self._ledger._retired,
                        "currentStatus": json.loads(current)["status"] if current is not None else None,
                        "receiptCount": len(self._probe_records(probe.probe_id, "receipt")),
                        "correctionCount": len(self._probe_records(probe.probe_id, "correction"))})
                payload = {"action": "list", "probes": rows, "offset": offset, "totalProbes": len(probes),
                           "nextOffset": end if end < len(probes) else None}
            else:
                probe = self._ledger._definitions.get(request["probeId"])
                if probe is None:
                    return ToolResult(ok=False, output="error[review_probe_read_invalid]: unknown probeId in this workflow; use action=list")
                index = request.get("recordIndex", 0)
                if action == "source":
                    content = probe.code
                else:
                    records = self._probe_records(probe.probe_id, action)
                    if index >= len(records):
                        return ToolResult(ok=False, output="error[review_probe_read_invalid]: recordIndex exceeds this probe's records")
                    content = records[index]
                if offset > len(content):
                    return ToolResult(ok=False, output="error[review_probe_read_invalid]: offset exceeds retained text length")
                end = min(offset + request.get("limit", 2000), len(content))
                payload = {"action": action, "probeId": probe.probe_id, "recordIndex": index,
                    "format": "text" if action == "source" else "json", "offset": offset,
                    "totalChars": len(content), "text": content[offset:end],
                    "digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "nextOffset": end if end < len(content) else None}
            self._active(context)
        return ToolResult(ok=True, output=_json(payload))

    def _probe_records(self, probe_id, action):
        # Called under the ledger lock. Stable per-probe indices address the
        # append-only original records, never a reconstructed success summary.
        if action == "receipt":
            return [value for value in self._ledger._attempts if json.loads(value)["probeId"] == probe_id]
        return [value for value in self._ledger._corrections
                if probe_id in (json.loads(value)["document"]["oldProbeId"],
                                json.loads(value)["document"]["newProbeId"])]

    def _correction_error(self, error, probe_id):
        # Typed structural diagnostics already include their source reader;
        # preserve that public error exactly, without duplicating its payload.
        message = f"error[review_probe_correction_rejected]: {error}"
        if not isinstance(getattr(error, "diagnostics", None), dict):
            message += "\nrecovery=" + _json(self._recovery(probe_id))
        return ToolResult(ok=False, output=message)

    def _validate(self, value):
        fields = {"criterionIds", "scenario", "code"}
        correction = {"correctsProbeId", "correctionReason", "sourceEvidence"}
        if not isinstance(value, dict) or set(value) not in (fields, fields | correction):
            raise ValueError("review_test requires criterionIds, scenario, code and all correction fields together")
        probe = ReviewerProbe.create(criterion_ids=value["criterionIds"], scenario=value["scenario"], code=value["code"])
        if any(cid not in self._criterion_ids for cid in probe.criterion_ids):
            raise ValueError("review_test criterion is outside this Assignment")
        if not correction.intersection(value):
            return probe
        probe_id, reason = _text(value["correctsProbeId"], "correctsProbeId"), _text(value["correctionReason"], "correctionReason")
        if _ARTIFACT.fullmatch(probe_id) is None or len(reason) > 1200:
            raise ValueError("probe correction ID or reason is invalid")
        rows = value["sourceEvidence"]
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 20
                or any(not isinstance(row, dict) or set(row) != {"path", "startLine", "endLine"}
                       or not isinstance(row["path"], str) or not row["path"].strip()
                       or len(row["path"]) > 1200 or "\x00" in row["path"]
                       or type(row["startLine"]) is not int or type(row["endLine"]) is not int
                       or row["startLine"] < 1 or row["endLine"] < row["startLine"] for row in rows)):
            raise ValueError("probe correction requires concrete source line anchors")
        return ReviewerProbeCorrection(probe, probe_id, reason, tuple(json.loads(_json(rows))))

    def _active(self, context):
        _checkpoint(context)
        if (self._closed or not _channel_live(self._channel)
                or getattr(context, "_subagent_mailbox", None) is not self._channel):
            raise ValueError("review_test capability is closed or belongs to another Assignment")

    def _run(self, probe: ReviewerProbe, context) -> ToolResult:
        self._active(context)
        if isinstance(probe, ReviewerProbeCorrection):
            return self._correct(probe, context)
        # Validate even direct host calls, not only ToolRegistry's validator.
        if (not isinstance(probe, ReviewerProbe) or any(cid not in self._criterion_ids for cid in probe.criterion_ids)):
            raise ValueError("review_test requires validated input")
        with self._ledger._lock:
            if probe.probe_id in self._ledger._retired:
                raise ValueError("probe was explicitly corrected; use the retained replacement")
            self._ledger._current.pop((self._patch_digest, probe.probe_id), None)
            receipt = self._run_probe(probe, context)
            self._active(context)
            result = self._ledger.record(probe, self._patch_digest, receipt)
        return ToolResult(ok=result["status"] == "passed", output=_json({**result,
            "recovery": self._recovery(probe.probe_id)}))

    def _correct(self, request: ReviewerProbeCorrection, context) -> ToolResult:
        # Hold the ledger lock through the actual run and publication. No
        # parallel replay/correction can alter eligibility after the proof.
        with self._ledger._lock:
            self._active(context)
            replacement = request.replacement
            if self._record_correction is None:
                raise ValueError("probe correction requires a host publisher")
            try:
                validated = self._validate({"criterionIds": list(replacement.criterion_ids),
                    "scenario": replacement.scenario, "code": replacement.code,
                    "correctsProbeId": request.corrects_probe_id, "correctionReason": request.reason,
                    "sourceEvidence": list(request.source_evidence)})
                if validated != request:
                    raise ValueError("probe correction requires validated input")
                old = self._ledger._active.get(request.corrects_probe_id)
                if old is None or replacement.probe_id in self._ledger._retired:
                    raise ValueError("correction needs an active original and a non-retired replacement")
                prior = [json.loads(value) for value in self._ledger._attempts
                         if json.loads(value)["probeId"] == old.probe_id]
                validate_probe_correction_sources(old.to_dict(), replacement.to_dict())
                validate_probe_correction_history(prior, self._patch_digest)
            except ValueError as error:
                return self._correction_error(error, request.corrects_probe_id)
            receipt = self._run_probe(replacement, context)
            self._active(context)
            result = self._ledger.record(replacement, self._patch_digest, receipt)
            try:
                validate_probe_correction_execution(prior, result, self._patch_digest)
            except ValueError as error:
                return self._correction_error(error, request.corrects_probe_id)
            publication = self._record_correction(request, old, prior, result, context)
            # The publisher validates scope/anchors and persists before return.
            # A cancellation after durable publication must not split the
            # corresponding in-memory transition into a contradictory state.
            self._validate_correction_publication(publication, request, old, prior, result)
            self._ledger._corrections.append(_json(publication))
            self._ledger._active.pop(old.probe_id)
            self._ledger._retired.add(old.probe_id)
            return ToolResult(ok=result["status"] == "passed", output=_json({**result,
                "correctionRef": publication["correctionRef"], "correctedProbeId": old.probe_id,
                "recovery": self._recovery(replacement.probe_id)}))

    def _validate_correction_publication(self, publication, request, old, prior, result):
        if not isinstance(publication, dict) or set(publication) != {"correctionRef", "document"}:
            raise ValueError("host correction publication is invalid")
        document = publication["document"]
        expected = {"correctionVersion": 1, "resultType": "ReviewProbeCorrection.v1",
            "workflowId": self._assignment.workflow_id, "taskId": self._assignment.task_id,
            "assignmentId": self._assignment.assignment_id, "patchDigest": self._patch_digest,
            "oldProbeId": old.probe_id, "newProbeId": request.replacement.probe_id,
            "oldSourceRef": review_probe_source_artifact_id(old.probe_id),
            "newSourceRef": result.get("sourceRef"), "oldReceiptRefs": [r.get("receiptRef") for r in prior],
            "newReceiptRef": result.get("receiptRef"), "reason": request.reason}
        if (not isinstance(document, dict) or set(document) != {*expected, "sourceEvidence"}
                or any(document.get(key) != value for key, value in expected.items())
                or publication["correctionRef"] != correction_artifact_id(document)):
            raise ValueError("host correction publication does not bind the exact executions")
        rows = document["sourceEvidence"]
        if (not isinstance(rows, list) or len(rows) != len(request.source_evidence)
                or any(not isinstance(row, dict) or set(row) != {*anchor, "sourceDigest"}
                       or any(row.get(key) != value for key, value in anchor.items())
                       or not isinstance(row.get("sourceDigest"), str)
                       or _DIGEST.fullmatch(row["sourceDigest"]) is None
                       for row, anchor in zip(rows, request.source_evidence))):
            raise ValueError("host correction requires actual source hashes for every anchor")

    def close(self) -> None:
        self._closed = True
        self._run_probe = None
        self._record_correction = None


__all__ = ["ReviewerProbe", "ReviewerProbeCorrection", "ReviewerProbeLedger", "ReviewerCheckCapability",
           "ReviewCoverageError"]
