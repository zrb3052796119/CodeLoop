"""Independent semantic migration of unclassified v1 completion probes.

Only the host calls this capability. Ordinary reviewers cannot relabel an
already declared acceptance probe. This module records a model judgment, not
a mechanical proof of test semantics; the completion reviewer must execute
diagnostic replacements before it can activate the proposed migration.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any

from minicode.capability_contracts import (
    CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope,
)
from minicode.implementation_completion_review import (
    ImplementationCompletionReview, requirement_units,
)
from minicode.reviewer_verification import ReviewerProbe
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.task_deadline import raise_task_deadline
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


_READ_TOOLS = frozenset({"read_file", "grep_files", "list_files", "file_tree",
                         "find_symbols", "file_line_count"})
_PURPOSES = frozenset({"acceptance", "diagnostic"})
_ROW_FIELDS = {"probeId", "purpose", "reason", "objectiveEvidence", "probeEvidence", "acceptanceProbes"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be nonempty text without NUL")
    return value


def _line_evidence(value: Any, text: str, label: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} needs actual source line references")
    result = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"startLine", "endLine"}
                or type(row["startLine"]) is not int or type(row["endLine"]) is not int
                or not 1 <= row["startLine"] <= row["endLine"] <= len(text.splitlines())):
            raise ValueError(f"{label} is outside its exact original source")
        result.append(dict(row))
    return result


def _validate_decision(row: Any, legacy: dict, objective: str) -> tuple[dict, dict]:
    if (not isinstance(row, dict) or set(row) != _ROW_FIELDS
            or not isinstance(row.get("probeId"), str) or row["probeId"] not in legacy
            or not isinstance(row.get("purpose"), str) or row["purpose"] not in _PURPOSES):
        raise ValueError("each migration needs an active probe, purpose, reason, source lines and acceptanceProbes")
    source = legacy[row["probeId"]]
    reason = _nonempty(row["reason"], "classification reason")
    objective_lines = _line_evidence(row["objectiveEvidence"], objective, "objectiveEvidence")
    probe_lines = _line_evidence(row["probeEvidence"], source["code"], "probeEvidence")
    replacements = row["acceptanceProbes"]
    if not isinstance(replacements, list):
        raise ValueError("acceptanceProbes must be a list")
    if row["purpose"] == "acceptance" and replacements:
        raise ValueError("acceptance classification cannot replace its original test")
    if row["purpose"] == "diagnostic" and not replacements:
        raise ValueError("diagnostic classification requires independent acceptance replacements")
    covered, identities, validated = set(), set(), []
    for replacement in replacements:
        if not isinstance(replacement, dict) or set(replacement) != {"criterionIds", "scenario", "code"}:
            raise ValueError("acceptance replacement needs criterionIds/scenario/code")
        probe = ReviewerProbe.create(criterion_ids=replacement["criterionIds"],
            scenario=replacement["scenario"], code=replacement["code"])
        if (not set(probe.criterion_ids) <= set(source["criterionIds"])
                or probe.probe_id in identities or probe.source_digest == source["sourceDigest"]):
            raise ValueError("acceptance replacement must be distinct and cover only original criteria")
        identities.add(probe.probe_id)
        covered.update(probe.criterion_ids)
        validated.append({"criterionIds": list(probe.criterion_ids), "scenario": probe.scenario, "code": probe.code})
    if row["purpose"] == "diagnostic" and covered != set(source["criterionIds"]):
        raise ValueError("acceptance replacements must cover every original criterion")
    classified = {"probeId": source["probeId"], "sourceDigest": source["sourceDigest"],
        "purpose": row["purpose"], "reason": reason, "acceptanceProbes": validated}
    evidence = {key: value for key, value in classified.items() if key != "sourceDigest"}
    evidence.update(objectiveEvidence=objective_lines, probeEvidence=probe_lines)
    return classified, evidence


def _evidence_bytes(reference: Any, directories: list[str], kind: str) -> bytes:
    path = Path(_nonempty(reference, "host evidence reference"))
    if (not path.is_absolute() or path.is_symlink() or path != path.resolve() or not path.is_file()
            or not re.fullmatch(r"\d{5,}-" + re.escape(kind) + r"\.json", path.name)):
        raise ValueError("invalid host migration evidence path")
    roots = []
    for directory in directories:
        root = Path(_nonempty(directory, "host evidence directory"))
        if not root.is_absolute() or root.is_symlink() or root != root.resolve() or not root.is_dir():
            raise ValueError("invalid host migration evidence directory")
        roots.append(root.resolve())
    if path.parent.resolve() not in roots:
        raise ValueError("host migration evidence escaped its directory")
    return path.read_bytes()


def validate_continuation_state(spec, legacy_state, state, candidate_digest=None) -> dict:
    """Validate host checkpoint bindings and persisted complete units without a model call."""
    wrapper = json.loads(_json(state))
    if not isinstance(wrapper, dict) or set(wrapper) != {"checkpointRef", "checkpointDigest", "state"}:
        raise ValueError("invalid migration continuation wrapper")
    value = wrapper["state"]
    fields = {"migrationStateVersion", "objectiveDigest", "candidateDigest", "sourceDigests",
              "completedUnits", "messages", "messagesDigest", "evidenceDirectory",
              "priorEvidenceDirectories", "snapshotPath"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["migrationStateVersion"]) is not int or value["migrationStateVersion"] != 1
            or wrapper["checkpointDigest"] != _digest(_json(value))):
        raise ValueError("invalid migration continuation state or digest")
    if value["objectiveDigest"] != _digest(spec.objective):
        raise ValueError("migration continuation objective changed")
    if (not isinstance(value["candidateDigest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["candidateDigest"])
            or (candidate_digest is not None and value["candidateDigest"] != candidate_digest)):
        raise ValueError("migration continuation candidate changed")
    legacy = CompletionProbeMigration(spec)._legacy(legacy_state)
    if value["sourceDigests"] != {pid: p["sourceDigest"] for pid, p in legacy.items()}:
        raise ValueError("migration continuation legacy sources changed")
    messages = value["messages"]
    if (not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages)
            or value["messagesDigest"] != _digest(_json(messages))):
        raise ValueError("migration continuation messages changed")
    if not isinstance(value["priorEvidenceDirectories"], list):
        raise ValueError("invalid prior migration evidence directories")
    directories = [value["evidenceDirectory"], *value["priorEvidenceDirectories"]]
    _nonempty(value["snapshotPath"], "original migration snapshot")
    raw = _evidence_bytes(wrapper["checkpointRef"], [value["evidenceDirectory"]], "continuation")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != wrapper["checkpointDigest"] or json.loads(raw) != value:
        raise ValueError("persisted migration checkpoint changed")
    units, seen = value["completedUnits"], set()
    if not isinstance(units, list):
        raise ValueError("invalid completed migration units")
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"decision", "sourceDigest", "decisionDigest", "evidenceRef"}:
            raise ValueError("invalid completed migration unit")
        classified, evidence = _validate_decision(unit["decision"], legacy, spec.objective)
        pid = classified["probeId"]
        if (pid in seen or unit["decision"] != evidence
                or unit["sourceDigest"] != classified["sourceDigest"]
                or unit["decisionDigest"] != _digest(_json(evidence))):
            raise ValueError("migration completed unit is duplicated or changed")
        seen.add(pid)
        expected = {"objectiveDigest": value["objectiveDigest"], "candidateDigest": value["candidateDigest"],
                    **{key: unit[key] for key in ("decision", "sourceDigest", "decisionDigest")}}
        if json.loads(_evidence_bytes(unit["evidenceRef"], directories, "completed-unit")) != expected:
            raise ValueError("persisted completed migration unit changed")
    return wrapper


class CompletionProbeMigration:
    """Classify only active legacy probes, using the parent's model and budget."""

    def __init__(self, spec):
        self.spec = spec
        self.objective = spec.objective
        self.requirements = requirement_units(self.objective)
        self._directory: Path | None = None
        self._sequence = 0
        self._artifact_lock = threading.Lock()
        self.continuation_state: dict | None = None

    def _persist(self, kind: str, document: dict) -> str:
        with self._artifact_lock:
            if self._directory is None:
                self._directory = Path(tempfile.mkdtemp(prefix="minicode-probe-migration-")).resolve()
            self._sequence += 1
            path = self._directory / f"{self._sequence:05d}-{kind}.json"
            with path.open("x", encoding="utf-8") as handle:
                handle.write(_json(document))
            return str(path)

    def _legacy(self, state: dict) -> dict[str, dict]:
        if not isinstance(state, dict) or state.get("objective") != self.objective:
            raise ValueError("legacy state must retain the exact original objective")
        purposes = state.get("probePurposes", {})
        sources = state.get("probes")
        retired = state.get("retiredProbeIds", [])
        if (not isinstance(purposes, dict) or not isinstance(sources, list)
                or not isinstance(retired, list) or any(not isinstance(pid, str) for pid in retired)):
            raise ValueError("invalid legacy probe state")
        known = {unit["id"] for unit in self.requirements}
        seen, legacy = set(), {}
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("invalid legacy source")
            pid = source.get("probeId")
            if not isinstance(pid, str) or pid in seen:
                raise ValueError("legacy probe IDs must be unique")
            seen.add(pid)
            if pid in retired or pid in purposes:
                continue
            if source.get("probeVersion") != 1 or "purpose" in source:
                raise ValueError("only undeclared v1 probes are eligible for migration")
            probe = ReviewerProbe.create(criterion_ids=source.get("criterionIds"),
                scenario=source.get("scenario"), code=source.get("code"))
            if source != probe.to_dict() or not set(probe.criterion_ids) <= known:
                raise ValueError("legacy probe source identity or criteria are inconsistent")
            legacy[pid] = source
        return legacy

    def assess(self, *, legacy_state, model, tools, context, agent_budget,
               event_sink, cancellation_token, deadline_monotonic,
               continuation_state=None, checkpoint_sink=None) -> dict:
        from minicode.implementation_execution import implementation_candidate_digest

        # Copy the caller's historical ledger before giving any of it to the
        # independent context. Classification never mutates that ledger.
        state = json.loads(_json(legacy_state))
        legacy = self._legacy(state)
        host = replace(context, _agent_budget=agent_budget,
            _cancellation_token=cancellation_token, _task_deadline_monotonic=deadline_monotonic)
        raise_task_deadline(host)
        digest = implementation_candidate_digest(self.spec, host)
        continuation = (validate_continuation_state(self.spec, state, continuation_state, digest)
                        if continuation_state is not None else None)
        authority = {"originalObjective": self.objective,
            "objectiveDigest": _digest(self.objective), "candidateDigest": digest,
            "requirements": list(self.requirements), "legacyState": state}
        authority_ref = self._persist("authority", authority)
        if not legacy:
            resolution = {"resolutionVersion": 1, "objectiveDigest": _digest(self.objective),
                "candidateDigest": digest, "reviewEvidenceRef": authority_ref, "decisions": []}
            return {"status": "resolved", "resolution": resolution,
                    "evidenceDirectory": str(self._directory)}
        with WorkflowWorkspaceTransaction(host.cwd, deadline_monotonic=deadline_monotonic,
                timeout_seconds=None, operation_checkpoint=lambda: raise_task_deadline(host)) as transaction:
            denied = ImplementationCompletionReview._denied_paths(transaction, host)
            frozen = transaction.freeze(allowed_paths=self.spec.write_paths, paths_relative_to_cwd=True)
            if implementation_candidate_digest(self.spec, host) != digest:
                return self._inconclusive("Candidate changed before migration review.")
            with frozen.verification_workspace(read_roots=(".",), forbidden_paths=denied) as cwd:
                permissions = SchedulerWorkerReadPermissions(str(cwd), read_roots=(".",),
                    forbidden_paths=denied, operation_checkpoint=lambda: raise_task_deadline(host))
                result = self._review(model=model, tools=tools, host=host, cwd=str(cwd),
                    permissions=permissions, legacy=legacy, state=state, digest=digest,
                    authority_ref=authority_ref, event_sink=event_sink,
                    continuation=continuation, checkpoint_sink=checkpoint_sink)
            raise_task_deadline(host)
            if implementation_candidate_digest(self.spec, host) != digest:
                return self._inconclusive("Candidate changed during migration review.")
            return result

    def _inconclusive(self, reason: str) -> dict:
        result = {"status": "inconclusive", "reason": reason,
                  "evidenceDirectory": str(self._directory)}
        # Session messages belong to the host checkpoint, not the Executor's
        # failure feedback. The sink already delivered the complete state.
        if self.continuation_state is not None:
            result["continuationCheckpointRef"] = self.continuation_state["checkpointRef"]
            result["continuationCheckpointDigest"] = self.continuation_state["checkpointDigest"]
        result["evidenceRef"] = self._persist("inconclusive", result)
        return result

    def _review(self, *, model, tools, host, cwd, permissions, legacy, state,
                digest, authority_ref, event_sink, continuation=None, checkpoint_sink=None) -> dict:
        from minicode.agent_loop import run_agent_turn
        from minicode.context_manager import ContextManager
        from minicode.run_events import emit_event_safely

        owner = self
        review_id = "completion_review_" + hashlib.sha256(authority_ref.encode()).hexdigest()[:24]
        decision = None
        closed = False
        call_lock = threading.RLock()
        fields = _ROW_FIELDS

        restored = continuation["state"] if continuation is not None else None
        completed = {unit["decision"]["probeId"]: unit for unit in
                     (restored["completedUnits"] if restored is not None else [])}
        prior_directories = ([restored["evidenceDirectory"], *restored["priorEvidenceDirectories"]]
                             if restored is not None else [])

        def ledger():
            return {"candidateDigest": digest, "activeProbeCount": len(legacy),
                "completedProbeIds": sorted(completed), "remainingProbeIds": sorted(set(legacy) - set(completed)),
                "completedUnits": [{"probeId": pid, "purpose": unit["decision"]["purpose"],
                    "decisionDigest": unit["decisionDigest"], "evidenceRef": unit["evidenceRef"]}
                    for pid, unit in completed.items()]}

        def save_checkpoint():
            # The loop updates this manager at request boundaries and in its
            # finally. Never invent a tool result for a partially completed batch.
            actual_messages = json.loads(_json(manager.messages))
            state = {"migrationStateVersion": 1, "objectiveDigest": _digest(self.objective),
                "candidateDigest": digest, "sourceDigests": {pid: p["sourceDigest"] for pid, p in legacy.items()},
                "completedUnits": list(completed.values()), "messages": actual_messages,
                "messagesDigest": _digest(_json(actual_messages)), "evidenceDirectory": str(self._directory),
                "priorEvidenceDirectories": list(dict.fromkeys(prior_directories)), "snapshotPath": cwd}
            reference = self._persist("continuation", state)
            self.continuation_state = {"checkpointRef": reference,
                "checkpointDigest": _digest(_json(state)), "state": state}
            emit_event_safely(event_sink, "implementation.completion_migration_checkpoint", payload={
                "reviewId": review_id, "candidateDigest": digest,
                "completedCount": len(completed), "totalCount": len(legacy),
                "remainingCount": len(legacy) - len(completed), "messageCount": len(actual_messages),
                "checkpointRef": reference, "checkpointDigest": self.continuation_state["checkpointDigest"]})
            if checkpoint_sink is not None:
                checkpoint_sink(json.loads(_json(self.continuation_state)))

        class Observation:
            observation_active = event_sink is not None

            def emit(self, event_type, *, step=None, payload=None):
                if event_type == "model.started":
                    save_checkpoint()
                if not event_type.startswith(("model.", "tool.")):
                    return
                owner._persist("observation", {"reviewKind": "legacy_probe_migration",
                    "reviewId": review_id, "eventType": event_type, "step": step, "payload": payload or {}})
                if event_type in {"model.started", "model.completed", "model.failed", "model.costed",
                                  "tool.started", "tool.completed", "tool.finished", "tool.failed"}:
                    projected = {key: value for key, value in (payload or {}).items()
                        if key in {"operationId", "toolName", "usage", "durationMs",
                                   "resultType", "toolCallCount", "isError"}}
                    emit_event_safely(event_sink, "implementation.completion_review_activity", step=step,
                        payload={**projected, "role": "completion_reviewer", "reviewId": review_id,
                                 "eventType": event_type})

            def record_tool_evidence(self, *, tool_name, phase, tool_input=None,
                                     output="", is_error=False, invocation_id=None):
                return owner._persist("tool-evidence", {"reviewKind": "legacy_probe_migration",
                    "reviewId": review_id, "toolName": tool_name, "phase": phase,
                    "input": tool_input, "output": output, "isError": is_error,
                    "invocationId": invocation_id})

        def checkpoint():
            raise_task_deadline(host)
            if closed:
                raise ValueError("legacy migration capability is closed")

        def record_decision(row):
            classified, evidence = _validate_decision(row, legacy, self.objective)
            pid = classified["probeId"]
            if pid in completed:
                if completed[pid]["decision"] != evidence:
                    raise ValueError("completed migration decision is immutable")
                return
            document = {"objectiveDigest": _digest(self.objective), "candidateDigest": digest,
                "sourceDigest": classified["sourceDigest"], "decisionDigest": _digest(_json(evidence)),
                "decision": evidence}
            reference = self._persist("completed-unit", document)
            completed[pid] = {key: document[key] for key in ("decision", "sourceDigest", "decisionDigest")}
            completed[pid]["evidenceRef"] = reference
            emit_event_safely(event_sink, "implementation.completion_migration_progress", payload={
                "reviewId": review_id, "candidateDigest": digest,
                "completedCount": len(completed), "totalCount": len(legacy),
                "remainingCount": len(legacy) - len(completed), "evidenceRef": reference})

        def record(value, _context):
            checkpoint()
            if decision is not None:
                raise ValueError("legacy migration decision already submitted")
            if not isinstance(value, dict) or set(value) != {"decision"}:
                raise ValueError("migration record requires one complete decision")
            record_decision(value["decision"])
            return ToolResult(ok=True, output=_json(ledger()))

        def submit(value, _context):
            nonlocal decision
            checkpoint()
            if decision is not None:
                raise ValueError("legacy migration decision already submitted")
            if not isinstance(value, dict):
                raise ValueError("migration submit requires decisions or finalize")
            if set(value) == {"decisions"}:
                rows = value["decisions"]
                if (not isinstance(rows, list) or len(rows) != len(legacy)
                        or any(not isinstance(row, dict) for row in rows)
                        or {row.get("probeId") for row in rows} != set(legacy)):
                    raise ValueError("every active legacy probe must be classified exactly once")
                # The compatible all-at-once form validates fully before recording.
                for row in rows:
                    _classified, evidence = _validate_decision(row, legacy, self.objective)
                    if row["probeId"] in completed and completed[row["probeId"]]["decision"] != evidence:
                        raise ValueError("completed migration decision is immutable")
                for row in rows:
                    record_decision(row)
            elif set(value) != {"finalize"} or value["finalize"] is not True:
                raise ValueError("migration submit requires decisions or finalize=true")
            if set(completed) != set(legacy):
                raise ValueError("every active legacy probe must be classified exactly once; consult remaining ledger")
            classified, evidence = [], []
            for pid in legacy:
                classification, row = _validate_decision(completed[pid]["decision"], legacy, self.objective)
                classified.append(classification)
                evidence.append({**row, "sourceDigest": completed[pid]["sourceDigest"],
                                 "unitEvidenceRef": completed[pid]["evidenceRef"]})
            ref = self._persist("classification", {"migrationEvidenceVersion": 1,
                "judgmentKind": "independent_model_semantic_classification",
                "objectiveDigest": _digest(self.objective), "candidateDigest": digest,
                "authorityRef": authority_ref, "decisions": evidence})
            decision = {"resolutionVersion": 1, "objectiveDigest": _digest(self.objective),
                "candidateDigest": digest, "reviewEvidenceRef": ref, "decisions": classified}
            return ToolResult(ok=True, output=_json(decision), _implementation_submitted=True)

        def read_history(value, _context):
            checkpoint()
            if value == {}:
                return ToolResult(ok=True, output=_json(ledger()))
            if (not isinstance(value, dict) or set(value) - {"probeId", "offset", "limit"}
                    or value.get("probeId") not in legacy):
                raise ValueError("migration history requires an active legacy probeId")
            pid = value["probeId"]
            document = {"source": legacy[pid], "receipts": [r for r in state.get("receipts", [])
                if isinstance(r, dict) and r.get("probeId") == pid],
                "priorReviews": state.get("reviews", []), "corrections": state.get("corrections", [])}
            encoded = _json(document)
            offset, limit = value.get("offset", 0), value.get("limit", 20000)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 60000:
                raise ValueError("invalid migration evidence page")
            end = min(len(encoded), offset + limit)
            return ToolResult(ok=True, output=_json({"probeId": pid, "offset": offset,
                "totalCharacters": len(encoded), "content": encoded[offset:end],
                "nextOffset": end if end < len(encoded) else None}))

        profile = CapabilityProfile(domain=CapabilityDomain.MEMORY, scope=CapabilityScope.READONLY,
            effects=CapabilityEffects(read_only=True, destructive=False, external=False,
                                      executes_code=False, delegates=False),
            purposes=("Classify undeclared historical probe purpose without changing the candidate",))

        def tool(name, description, run, properties, required):
            def validated(value, context):
                try:
                    with call_lock:
                        return run(value, context)
                except ValueError as error:
                    return ToolResult(ok=False, output=_json({"errorCode": "probe_migration_invalid",
                                                              "message": str(error)}))
            return ToolDefinition(name=name, description=description,
                input_schema={"type": "object", "properties": properties,
                    "required": required, "additionalProperties": False},
                validator=lambda value: value, run=validated, capability_profile=profile)

        lines = {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "startLine": {"type": "integer"}, "endLine": {"type": "integer"}},
            "required": ["startLine", "endLine"], "additionalProperties": False}}
        replacement_schema = {"type": "object", "properties": {
            "criterionIds": {"type": "array", "items": {"type": "string"}},
            "scenario": {"type": "string"}, "code": {"type": "string"}},
            "required": ["criterionIds", "scenario", "code"], "additionalProperties": False}
        decision_schema = {"type": "object", "properties": {
            "probeId": {"type": "string", "enum": sorted(legacy)},
            "purpose": {"type": "string", "enum": sorted(_PURPOSES)}, "reason": {"type": "string"},
            "objectiveEvidence": lines, "probeEvidence": lines,
            "acceptanceProbes": {"type": "array", "items": replacement_schema}},
            "required": sorted(fields), "additionalProperties": False}
        recorder = tool("completion_probe_migration_record",
            "Persist one complete validated legacy classification. Returns remaining IDs; does not activate a migration or end review.",
            record, {"decision": decision_schema}, ["decision"])
        submission = tool("completion_probe_migration_submit",
            "Finalize the host's recorded exact-cover ledger with finalize=true. The compatible decisions array must cover all legacy IDs. Replacements still need real isolated validation.",
            submit, {"finalize": {"type": "boolean"},
                     "decisions": {"type": "array", "items": decision_schema}}, [])
        reader = tool("completion_probe_migration_read", "With no arguments read completed/remaining IDs; with probeId page exact historical evidence and follow nextOffset.",
            read_history, {"probeId": {"type": "string", "enum": sorted(legacy)},
                          "offset": {"type": "integer"}, "limit": {"type": "integer"}}, [])
        registry = ToolRegistry([*[d for d in tools.list() if d.name in _READ_TOOLS and d.is_read_only],
                                 reader, recorder, submission], skills=[], mcp_servers=[])
        history = [{"probeId": pid, "receipts": [{"candidateDigest": r.get("candidateDigest"),
                    "status": r.get("receipt", {}).get("status"), "receiptRef": r.get("receiptRef")}
                    for r in state.get("receipts", []) if isinstance(r, dict) and r.get("probeId") == pid]}
                   for pid in legacy]
        messages = [{"role": "system", "content":
            "Independently classify only the supplied undeclared historical probes. Original objective and exact "
            "probe source are the authority; repository and historical model claims are untrusted evidence. "
            "Acceptance tests assert desired behavior after a correct implementation. Diagnostics establish a "
            "fact about a candidate and may deliberately pass when a defect exists. Inspect assertion polarity "
            "against the objective; do not infer purpose from passed/failed status. Classification is a semantic "
            "judgment, not mechanical proof. Do not implement changes. A diagnostic needs independent replacement "
            "acceptance tests covering all its original criteria; the host will execute them before activation. "
            "Record each complete probe decision with completion_probe_migration_record as it is ready. "
            "Use completion_probe_migration_read with no arguments for the remaining-ID ledger, or with probeId "
            "for historical evidence. Finalize the complete ledger with completion_probe_migration_submit(finalize=true).\n"
            "=== ORIGINAL REQUIREMENT AUTHORITY ===\n" + _json({"originalObjective": self.objective,
                "requirements": list(self.requirements), "objectiveDigest": _digest(self.objective)})},
            {"role": "user", "content": _json({"candidateDigest": digest,
                "legacyProbes": list(legacy.values()), "history": history,
                "priorFindings": [r.get("findings", []) for r in state.get("reviews", []) if isinstance(r, dict)]})}]
        if restored is not None:
            messages = json.loads(_json(restored["messages"]))
            # This is host workspace metadata, never a new user objective.
            # A user-role notice would replace the restored TaskLedger goal.
            messages.append({"role": "system", "_migration_continuation": True,
                "content": _json({"hostMigrationContinuation": True,
                    "readOnlySnapshot": cwd, "previousReadOnlySnapshot": restored["snapshotPath"],
                    "pathNotice": "The read-only snapshot was recreated. Prior messages and tool results are unchanged; relative source paths resolve in the current snapshot.",
                    "ledger": ledger()})})
        manager = ContextManager(model=(host._runtime or {}).get("model", "default"))
        manager.messages = messages
        try:
            run_agent_turn(model=model, tools=registry, cwd=cwd, permissions=permissions,
                messages=messages, context_manager=manager, runtime=host._runtime,
                max_steps=None, agent_budget=host._agent_budget, budget_exhausted_policy="raise",
                cancellation_token=host._cancellation_token, deadline_monotonic=host._task_deadline_monotonic,
                event_sink=Observation(), agent_depth=host._agent_depth + 1,
                implementation_task=False, implementation_spec=None, enable_implementation_phases=False,
                enable_work_chain=False, allow_user_interaction=False, defer_curator_maintenance=True)
            checkpoint()
        finally:
            original_error = sys.exception()
            closed = True
            try:
                save_checkpoint()
            except BaseException:
                if original_error is None:
                    raise
                logging.getLogger(__name__).warning("Migration checkpoint failed during interruption", exc_info=True)
        if decision is None:
            return self._inconclusive("Independent migration reviewer ended without a valid native submission.")
        self._persist("resolution", decision)
        return {"status": "resolved", "resolution": decision, "evidenceDirectory": str(self._directory), "continuationState": self.continuation_state}


__all__ = ["CompletionProbeMigration", "validate_continuation_state"]
