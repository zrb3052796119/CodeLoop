#!/usr/bin/env python3
"""Read frozen evidence independently; never execute archived code or rerun oracles."""

from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import math
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from pathlib import Path

ARMS = ("single", "auto", "explicit")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def integer(value):
    return type(value) is int and value >= 0


def valid_projection(value):
    """Independently recognize the frozen v1 host result contract, not spawn IDs."""
    required = {
        "resultVersion",
        "subagentId",
        "agentType",
        "outcome",
        "contractStatus",
        "summary",
        "files",
        "risks",
        "verification",
    }
    if not isinstance(value, dict) or set(value) not in (
        required,
        required | {"partial"},
    ):
        return False
    if not (
        value["resultVersion"] == 1
        and isinstance(value["subagentId"], str)
        and re.fullmatch(r"sub_[0-9a-f]{32}", value["subagentId"])
        and isinstance(value["agentType"], str)
        and re.fullmatch(r"[a-z][a-z0-9_-]*", value["agentType"])
        and value["outcome"]
        in {"completed", "failed", "depth_rejected", "budget_exceeded"}
        and value["contractStatus"] in {"reported", "fallback", "derived"}
        and type(value.get("partial", False)) is bool
        and isinstance(value["summary"], str)
        and 0 < len(value["summary"].strip()) <= 4000
    ):
        return False
    files, risks, verification = value["files"], value["risks"], value["verification"]
    return (
        isinstance(files, list)
        and len(files) <= 40
        and all(
            isinstance(f, dict)
            and set(f) == {"path", "action"}
            and isinstance(f["path"], str)
            and 0 < len(f["path"].strip()) <= 500
            and f["action"] in {"read", "created", "modified", "deleted", "unknown"}
            for f in files
        )
        and isinstance(risks, list)
        and len(risks) <= 20
        and all(isinstance(r, str) and 0 < len(r.strip()) <= 500 for r in risks)
        and isinstance(verification, dict)
        and set(verification) == {"status", "checks"}
        and verification["status"] in {"passed", "failed", "not_run", "inconclusive"}
        and isinstance(verification["checks"], list)
        and len(verification["checks"]) <= 20
        and all(
            isinstance(c, str) and 0 < len(c.strip()) <= 500
            for c in verification["checks"]
        )
    )


def projection_ids(value, depth=0):
    if depth > 12:
        return set()
    if isinstance(value, str):
        _, marker, after = value.rpartition("=== SUBAGENT RESULT v1 ===")
        try:
            return projection_ids(
                json.loads(after.strip() if marker else value), depth + 1
            )
        except (ValueError, TypeError):
            return set()
    if valid_projection(value):
        return {value["subagentId"]}
    children = (
        value.values()
        if isinstance(value, dict)
        else value
        if isinstance(value, list)
        else []
    )
    return set().union(*(projection_ids(child, depth + 1) for child in children))


def independent_collaboration(runs, requests, delivered_ids, messages, owner_ack=False):
    """Reconstruct actual responses and handoffs without importing runner helpers."""
    delivered = set(delivered_ids)
    for message in messages:
        if message.get("role") in {"tool", "tool_result"} and (
            not owner_ack or message.get("toolName") == "task"
        ):
            delivered.update(projection_ids(message.get("content")))
    workflows = {
        r["subagentId"]: r
        for r in runs
        if r.get("isContainer") and r.get("returned") and r["subagentId"] in delivered
    }
    actual, handed_off, implemented, analysis = [], [], [], []
    successful = 0
    leaf_ids = {r["subagentId"] for r in runs if not r.get("isContainer")}
    for run in runs:
        actor = run["subagentId"]
        if actor not in leaf_ids or not any(
            q.get("subagentId") == actor and q.get("event") == "completed"
            for q in requests
        ):
            continue
        actual.append(run)
        projection = run.get("projected")
        successful += bool(
            run.get("toolOk") is True
            and isinstance(projection, dict)
            and projection.get("outcome") == "completed"
            and not projection.get("partial", False)
        )
        workflow = workflows.get(run.get("parentSubagentId"))
        via_workflow = bool(
            workflow
            and workflow["startedEpoch"] <= run["startedEpoch"]
            and run.get("returnedEpoch", math.inf) <= workflow.get("returnedEpoch", 0)
        )
        if run.get("returned") and (actor in delivered or via_workflow):
            handed_off.append(run)
            if run["role"] in {"plan", "explore"}:
                analysis.append(run)
            if run["role"].lower() == "executor" and via_workflow:
                implemented.append(run)
    ordered = any(
        (
            a.get("deliveredEpoch", a.get("returnedEpoch", math.inf))
            if owner_ack
            else a.get("returnedEpoch", math.inf)
        )
        <= e["startedEpoch"]
        for a in analysis
        for e in implemented
    )
    return {
        "actualLeafRuns": len(actual),
        "leafModelCalls": sum(r.get("subagentId") in leaf_ids for r in requests),
        "deliveredLeafResults": len(handed_off),
        "successfulLeafRuns": successful,
        "readAnalysisLeafRuns": len(analysis),
        "implementationLeafRuns": len(implemented),
        "collaborationExecuted": bool(handed_off),
        "collaborationProtocolSatisfied": bool(
            analysis and implemented and workflows and ordered
        ),
        "deliveredWorkflowResults": len(workflows),
    }


def stopping_code(reason):
    reason = reason.lower()
    if "budget" in reason or "预算" in reason:
        if "token" in reason:
            return "budget_token"
        if any(word in reason for word in ("model_call", "model call", "call budget")):
            return "budget_calls"
        if any(word in reason for word in ("cost", "usd")):
            return "budget_cost"
        return "budget_other"
    if "deadline" in reason or "wall" in reason:
        return "execution_deadline"
    return "completion_limited_other"


def request_deadline_diagnostic(request: dict, execution: dict, manifest: dict) -> dict:
    """Identify a recorded deadline boundary, never the reason a provider was slow."""
    values = {
        "requestDeadlineEpoch": request.get("requestDeadlineEpoch"),
        "startedEpoch": request.get("startedEpoch"),
        "seconds": request.get("seconds"),
        "executionDeadlineEpoch": execution.get("deadlineEpoch"),
        "requestTimeoutSeconds": manifest.get("limits", {}).get(
            "requestTimeoutSeconds"
        ),
    }
    result = {
        "code": "request_deadline",
        "timing": values,
        "boundaryEvidence": "insufficient_or_inconsistent_timing",
    }
    if not all(
        type(value) in (int, float) and math.isfinite(value) and value > 0
        for value in values.values()
    ):
        return result
    deadline, started, elapsed, global_deadline, limit = values.values()
    window = deadline - started
    if window <= 0 or global_deadline <= started:
        return result
    # The wrapper timer starts before the durable HTTP-start record. Permit
    # small scheduling/clock-conversion offsets, but require an elapsed deadline.
    elapsed_tolerance = min(1.0, max(0.02, window * 0.01))
    if abs(elapsed - window) > elapsed_tolerance:
        return result
    at_global = abs(deadline - global_deadline) <= 0.1
    at_request_limit = abs(window - limit) <= 1.0 and abs(elapsed - limit) <= 1.0
    if at_global and at_request_limit:
        result["boundaryEvidence"] = "coincident_deadline_boundaries"
    elif at_global and window < limit - 1.0 and elapsed < limit - 1.0:
        result.update(
            code="request_deadline_global",
            boundaryEvidence="request_deadline_matches_global_remaining_time",
        )
    elif at_request_limit and global_deadline - deadline > 1.0:
        result.update(
            code="request_deadline_per_request",
            boundaryEvidence="request_elapsed_matches_frozen_request_limit",
        )
    return result


class Audit:
    def __init__(self, directory, allow_partial=False):
        self.directory = Path(directory)
        self.allow_partial = allow_partial
        self.issues = []
        self.trials = []
        self.owner_ack = False
        self.rejection_protocol = False
        self.admission_by_trial = {}

    def issue(self, code, detail, trial=None, severity="error"):
        self.issues.append(
            {"severity": severity, "code": code, "trial": trial, "detail": detail}
        )

    def check(self, condition, code, detail, trial=None, severity="error"):
        if not condition:
            self.issue(code, detail, trial, severity)

    def equal(self, actual, expected, code, trial=None):
        self.check(
            actual == expected,
            code,
            f"recorded={actual!r}; expected={expected!r}",
            trial,
        )

    def amount(self, actual, expected, code, trial):
        matches = (
            actual is None
            if expected is None
            else (
                type(actual) in (int, float)
                and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
            )
        )
        self.check(matches, code, f"recorded={actual!r}; expected={expected!r}", trial)

    def archived_hashes(self, manifest):
        archive = self.directory / "frozen-code.zip"
        self.equal(
            digest(archive.read_bytes()),
            manifest["frozenCodeArchiveSha256"],
            "archive_hash",
        )
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if "scripts/three_arm_protocol.py" in names:
                self.owner_ack = b"task_result_owner_ack" in bundle.read(
                    "scripts/three_arm_protocol.py"
                )
            if "scripts/evaluation_transport.py" in names:
                self.rejection_protocol = b"admission_rejections" in bundle.read(
                    "scripts/evaluation_transport.py"
                )
            self.equal(len(names), len(set(names)), "duplicate_archive_entries")
            production = sorted(
                n for n in names if n.startswith("minicode/") and n.endswith(".py")
            )
            self.check(
                bool(production),
                "empty_production_archive",
                "No frozen production Python sources",
            )
            value = digest(
                b"".join(n.encode() + b"\0" + bundle.read(n) for n in production)
            )
            self.equal(value, manifest["productionSha256"], "production_hash")
            tree = ast.parse(bundle.read("scripts/run_three_arm_real.py"))
            assignments = [
                node.value
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "HARNESS_FILES"
                    for t in node.targets
                )
            ]
            if len(assignments) != 1:
                raise ValueError("Frozen runner must declare one literal HARNESS_FILES")
            harness = ast.literal_eval(assignments[0])
            if not isinstance(harness, (tuple, list)) or not all(
                isinstance(n, str) for n in harness
            ):
                raise ValueError(
                    "Frozen HARNESS_FILES is not a literal sequence of names"
                )
            self.equal(len(harness), len(set(harness)), "duplicate_harness_files")
            value = digest(
                b"".join(
                    n.encode() + b"\0" + bundle.read("scripts/" + n) for n in harness
                )
            )
            self.equal(value, manifest["harnessSha256"], "harness_hash")
            self.equal(
                digest(bundle.read("analysis-plan.md")),
                manifest["analysisPlanSha256"],
                "analysis_plan_hash",
            )

    def ledger(self, root, manifest, trial):
        self.admission_by_trial[trial] = []
        path = root / "transport.sqlite"
        if not path.exists():
            self.issue(
                "missing_ledger",
                "No physical request ledger; zero calls must be substantiated as pre-request failure",
                trial,
                "warning",
            )
            return []
        # mode=ro sees WAL evidence without changing the database or checkpointing it.
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            self.equal(
                connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
                "sqlite_integrity",
                trial,
            )
            settings = json.loads(
                connection.execute("SELECT config FROM settings WHERE id=1").fetchone()[
                    0
                ]
            )
            limits = manifest["limits"]
            for key, expected in {
                "expected_model": manifest["model"],
                "expected_route_sha256": manifest["resolvedTransportRouteSha256"],
                "max_model_calls": limits["maxModelCalls"],
                "max_total_tokens": limits["maxTokens"],
                "max_output_tokens": limits["maxOutputTokens"],
            }.items():
                self.equal(settings.get(key), expected, "ledger_config_" + key, trial)
            rows = connection.execute(
                "SELECT call,status,charge_tokens,data FROM requests ORDER BY call"
            ).fetchall()
            ids = [r[0] for r in rows]
            self.equal(
                ids, list(range(1, len(rows) + 1)), "request_sequence_gap", trial
            )
            sequence = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='requests'"
            ).fetchone()
            self.equal(
                sequence[0] if sequence else 0,
                len(rows),
                "request_sequence_deleted_tail",
                trial,
            )
            tables = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            rejection_rows = []
            if "admission_rejections" in tables:
                self.check(
                    self.rejection_protocol,
                    "unregistered_admission_table",
                    "Frozen transport did not support durable admission refusals",
                    trial,
                )
                rejection_rows = connection.execute(
                    "SELECT rejection,data FROM admission_rejections ORDER BY rejection"
                ).fetchall()
                self.equal(
                    [r[0] for r in rejection_rows],
                    list(range(1, len(rejection_rows) + 1)),
                    "rejection_sequence_gap",
                    trial,
                )
                seq = connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='admission_rejections'"
                ).fetchone()
                self.equal(
                    seq[0] if seq else 0,
                    len(rejection_rows),
                    "rejection_sequence_deleted_tail",
                    trial,
                )
            elif self.rejection_protocol:
                self.issue(
                    "missing_admission_table",
                    "Frozen transport requires durable refusal evidence",
                    trial,
                )
        finally:
            connection.close()
        records = []
        for call, status, charge, encoded in rows:
            record = json.loads(encoded)
            self.equal(record.get("event"), status, "request_status", trial)
            self.check(
                status in ("started", "completed", "failed"),
                "unknown_request_status",
                str(status),
                trial,
            )
            self.equal(record.get("model"), manifest["model"], "request_model", trial)
            self.equal(
                record.get("routeSha256"),
                manifest["resolvedTransportRouteSha256"],
                "request_route",
                trial,
            )
            usage = record.get("usage", {})
            raw = record.get("providerUsage")
            if isinstance(raw, dict):
                inputs, outputs = raw.get("prompt_tokens"), raw.get("completion_tokens")
                self.equal(
                    usage.get("input_tokens"),
                    inputs if integer(inputs) else None,
                    "provider_input_receipt",
                    trial,
                )
                self.equal(
                    usage.get("output_tokens"),
                    outputs if integer(outputs) else None,
                    "provider_output_receipt",
                    trial,
                )
                details = raw.get("prompt_tokens_details") or {}
                cache = details.get("cached_tokens")
                if not integer(cache):
                    cache = raw.get("cached_tokens")
                cache = (
                    cache
                    if integer(cache) and integer(inputs) and cache <= inputs
                    else None
                )
                self.equal(
                    usage.get("cache_read_tokens"),
                    cache,
                    "provider_cache_receipt",
                    trial,
                )
            else:
                self.check(
                    usage.get("source") != "provider",
                    "missing_raw_provider_receipt",
                    f"call={call}",
                    trial,
                )
            known = integer(usage.get("input_tokens")) and integer(
                usage.get("output_tokens")
            )
            self.equal(
                usage.get("source"),
                "provider" if known else "unavailable",
                "usage_source",
                trial,
            )
            cache = usage.get("cache_read_tokens")
            self.check(
                cache is None
                or (known and integer(cache) and cache <= usage["input_tokens"]),
                "invalid_cache",
                f"call={call}",
                trial,
            )
            expected_charge = (
                usage["input_tokens"] + usage["output_tokens"]
                if known
                else record.get("reservedTokens")
            )
            self.equal(charge, expected_charge, "charged_tokens", trial)
            records.append({**record, "call": call, "chargedTokens": charge})
        self.check(
            len(records) <= limits["maxModelCalls"],
            "request_limit",
            f"calls={len(records)}",
            trial,
        )
        self.admission_by_trial[trial] = self.validate_rejections(
            rejection_rows, settings, manifest, len(records), trial
        )
        return records

    def validate_rejections(self, rows, settings, manifest, physical_count, trial):
        records = []
        for rejection, encoded in rows:
            start_issues = len(self.issues)
            record = json.loads(encoded)
            self.equal(
                record.get("event"), "admission_rejected", "rejection_event", trial
            )
            for key in (
                "usage",
                "providerUsage",
                "call",
                "httpStatus",
                "providerResponseId",
            ):
                self.check(
                    key not in record, "rejection_contains_http_evidence", key, trial
                )
            for key in (
                "reservedTokens",
                "currentRequests",
                "currentChargedTokens",
                "estimatedInputTokens",
                "maxOutputTokens",
            ):
                self.check(
                    integer(record.get(key)),
                    "invalid_rejection_counter",
                    f"rejection={rejection}; {key}",
                    trial,
                )
            for key, expected in {
                "maxModelCalls": manifest["limits"]["maxModelCalls"],
                "maxTotalTokens": manifest["limits"]["maxTokens"],
                "deadlineEpoch": settings.get("deadline_epoch"),
                "model": manifest["model"],
                "routeSha256": manifest["resolvedTransportRouteSha256"],
            }.items():
                self.equal(record.get(key), expected, "rejection_" + key, trial)
            epoch = record.get("rejectedEpoch")
            deadline = record.get("deadlineEpoch")
            self.check(
                type(epoch) in (int, float) and math.isfinite(epoch) and epoch > 0,
                "invalid_rejection_timestamp",
                f"rejection={rejection}",
                trial,
            )
            numeric = all(
                integer(record.get(k))
                for k in (
                    "reservedTokens",
                    "currentRequests",
                    "currentChargedTokens",
                    "estimatedInputTokens",
                    "maxOutputTokens",
                    "maxModelCalls",
                    "maxTotalTokens",
                )
            )
            if (
                numeric
                and type(epoch) in (int, float)
                and type(deadline) in (int, float)
            ):
                reason = (
                    "deadline"
                    if epoch >= deadline
                    else "request_budget"
                    if record["currentRequests"] >= record["maxModelCalls"]
                    else "token_budget"
                    if record["currentChargedTokens"] + record["reservedTokens"]
                    > record["maxTotalTokens"]
                    else None
                )
                self.equal(
                    record.get("reason"), reason, "rejection_reason_threshold", trial
                )
                self.check(
                    reason is not None,
                    "rejection_without_exceeded_limit",
                    f"rejection={rejection}",
                    trial,
                )
                self.check(
                    record["currentRequests"] <= physical_count,
                    "rejection_request_snapshot",
                    f"rejection={rejection}",
                    trial,
                )
                self.equal(
                    record["reservedTokens"],
                    record["estimatedInputTokens"] + record["maxOutputTokens"],
                    "rejection_reservation",
                    trial,
                )
                self.check(
                    record["maxOutputTokens"] <= manifest["limits"]["maxOutputTokens"],
                    "rejection_output_limit",
                    f"rejection={rejection}",
                    trial,
                )
            records.append(
                {
                    "rejection": rejection,
                    **record,
                    "auditValid": self.rejection_protocol
                    and len(self.issues) == start_issues,
                }
            )
        return records

    def metrics(self, records, price):
        known = [r for r in records if r.get("usage", {}).get("source") == "provider"]
        cached = [r for r in known if r["usage"].get("cache_read_tokens") is not None]
        token_complete = bool(records) and len(known) == len(records)
        cache_complete = bool(records) and len(cached) == len(records)
        lower = 0.0
        for record in known:
            usage = record["usage"]
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
            cache = usage.get("cache_read_tokens")
            cache = inputs if cache is None else cache
            lower += (
                (inputs - cache) * price["perMillionInput"]
                + outputs * price["perMillionOutput"]
                + cache * price["perMillionCacheRead"]
            ) / 1e6
        return {
            "modelCalls": len(records),
            "unknownUsageCalls": len(records) - len(known),
            "inputTokens": sum(r["usage"]["input_tokens"] for r in known),
            "outputTokens": sum(r["usage"]["output_tokens"] for r in known),
            "cacheReadTokens": sum(r["usage"]["cache_read_tokens"] for r in cached),
            "tokenUsageComplete": token_complete,
            "cacheUsageComplete": cache_complete,
            "usageComplete": token_complete and cache_complete,
            "failedCalls": sum(r["event"] == "failed" for r in records),
            "inFlightCalls": sum(r["event"] == "started" for r in records),
            "knownCostLowerBoundCny": lower if known else None,
            "estimatedCostCny": lower if token_complete and cache_complete else None,
        }

    def collaboration(self, root, row, records, manifest):
        trial = row["id"]
        path = root / "collaboration.json"
        if path.exists():
            evidence = read_json(path)
        else:
            evidence = {"runs": [], "deliveredIds": []}
            if any(r.get("subagentId") for r in records) or row.get(
                "actualLeafRuns", 0
            ):
                self.issue(
                    "missing_collaboration_evidence",
                    "Requests exist without durable collaboration evidence",
                    trial,
                )
        runs = evidence["runs"]
        by_id = {r["subagentId"]: r for r in runs}
        self.equal(len(by_id), len(runs), "duplicate_subagent_identity", trial)
        for run in runs:
            self.equal(
                bool(run.get("isContainer")),
                run.get("agentType") == "workflow",
                "container_role",
                trial,
            )
            if run.get("returned"):
                self.check(
                    run.get("returnedEpoch", -math.inf)
                    >= run.get("startedEpoch", math.inf),
                    "leaf_return_order",
                    run["subagentId"],
                    trial,
                )
                projected = run.get("projected")
                self.check(
                    projected is None or valid_projection(projected),
                    "invalid_host_projection",
                    run["subagentId"],
                    trial,
                )
                if isinstance(projected, dict):
                    self.equal(
                        projected.get("subagentId"),
                        run["subagentId"],
                        "projection_identity",
                        trial,
                    )
                    self.equal(
                        projected.get("agentType"),
                        run.get("agentType"),
                        "projection_agent_type",
                        trial,
                    )
        for actor in evidence.get("deliveredIds", []):
            self.check(
                actor in by_id and by_id[actor].get("returned") is True,
                "unbound_delivery_id",
                str(actor),
                trial,
            )
        for request in records:
            actor = request.get("subagentId")
            if actor:
                self.check(
                    actor in by_id,
                    "unbound_leaf_request",
                    f"call={request['call']}; actor={actor}",
                    trial,
                )
        for rejection in self.admission_by_trial.get(trial, []):
            actor, subagent = rejection.get("actorId"), rejection.get("subagentId")
            bound = (
                (subagent in by_id and actor == subagent)
                if subagent
                else actor == "main"
            )
            if bound and subagent:
                bound = (
                    rejection.get("agentType") == by_id[subagent].get("agentType")
                    and str(rejection.get("role")).lower()
                    == str(by_id[subagent].get("role")).lower()
                )
            if not bound:
                rejection["auditValid"] = False
                self.issue(
                    "unbound_rejection_actor",
                    f"rejection={rejection['rejection']}; actor={actor}",
                    trial,
                )
        transcript_path = root / "transcript.json"
        messages = read_json(transcript_path) if transcript_path.exists() else []
        delivered = set(evidence.get("deliveredIds", []))
        for message in messages:
            if message.get("role") in {"tool", "tool_result"} and (
                not self.owner_ack or message.get("toolName") == "task"
            ):
                delivered.update(projection_ids(message.get("content")))
        for actor in delivered:
            run = by_id.get(actor, {})
            owner_evidence = (
                self.owner_ack
                and run.get("deliveryEvidence") == "task_result_owner_ack"
            )
            if owner_evidence:
                fingerprint = run.get("resultSha256")
                self.check(
                    isinstance(fingerprint, str)
                    and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None,
                    "invalid_delivery_hash",
                    actor,
                    trial,
                )
                self.check(
                    run.get("deliveredEpoch", -math.inf)
                    >= run.get("returnedEpoch", math.inf),
                    "delivery_before_return",
                    actor,
                    trial,
                )
                candidates = []
                for message in messages:
                    if (
                        message.get("role") in {"tool", "tool_result"}
                        and message.get("toolName") == "task"
                        and isinstance(message.get("content"), str)
                    ):
                        content = message["content"]
                        candidates.append(content)
                        candidates.extend(
                            content[: m.start()]
                            for m in re.finditer(r"\n\n\[System note: ", content)
                        )
                if not any(
                    digest(value.encode()) == fingerprint for value in candidates
                ):
                    self.issue(
                        "delivery_hash_unverified",
                        actor
                        + "; original task output may be wrapped/truncated/compacted; durable owner ack retained",
                        trial,
                        "warning",
                    )
            self.check(
                run.get("returned") is True
                and (owner_evidence or valid_projection(run.get("projected"))),
                "delivery_without_host_result",
                actor,
                trial,
            )
        computed = independent_collaboration(
            runs, records, evidence.get("deliveredIds", []), messages, self.owner_ack
        )
        for key, expected in computed.items():
            self.equal(row.get(key), expected, "collaboration_" + key, trial)
        recipe = str(manifest.get("explicitRecipe", "")).lower()
        if row["arm"] == "explicit" and (
            "coordinator-only" in recipe or "task/todo_write" in recipe
        ):
            contract_path = root / "runtime-contract.json"
            if contract_path.exists():
                contract = read_json(contract_path)
                self.equal(
                    set(contract.get("tools", [])),
                    {"task", "todo_write"},
                    "coordinator_tool_contract",
                    trial,
                )
            elif records:
                self.issue(
                    "missing_runtime_contract",
                    "Coordinator restriction cannot be checked",
                    trial,
                )
            for request in records:
                if not request.get("subagentId"):
                    forbidden = [
                        t.get("name")
                        for t in request.get("toolCalls", [])
                        if t.get("name") not in {"task", "todo_write"}
                    ]
                    self.check(
                        not forbidden,
                        "coordinator_tool_request",
                        f"call={request['call']}; forbidden={forbidden}",
                        trial,
                    )
        return computed

    def stop_diagnostics(self, root, row, records, cleanup, manifest):
        """Descriptive, potentially co-occurring evidence; never changes scoring."""
        found = []
        try:
            execution = read_json(root / "execution-start.json")
            if not isinstance(execution, dict):
                execution = {}
        except (OSError, ValueError, TypeError):
            execution = {}
        for path in sorted((root / "journal").glob("**/events.ndjson")):
            try:
                events = [
                    json.loads(line)
                    for line in path.read_text().splitlines()
                    if line.strip()
                ]
                for event in events:
                    if event.get("type") == "execution.completion_limited":
                        reason = event.get("payload", {}).get("reason")
                        if isinstance(reason, str) and reason:
                            found.append(
                                {
                                    "code": stopping_code(reason),
                                    "source": str(path.relative_to(root)),
                                    "sequence": event.get("sequence"),
                                    "evidence": reason[:500],
                                    "authority": "host_journal",
                                }
                            )
            except (OSError, ValueError, TypeError):
                self.issue(
                    "unreadable_stop_journal",
                    str(path.relative_to(root)),
                    row["id"],
                    "warning",
                )
        if cleanup.get("deadlineReachedEpoch") is not None:
            found.append(
                {
                    "code": "guardian_hard_stop",
                    "source": "process-cleanup.json",
                    "evidence": cleanup["deadlineReachedEpoch"],
                    "authority": "host_guardian",
                }
            )
        for request in records:
            if request.get("event") == "failed":
                error = request.get("errorClass") or "unknown"
                description = {
                    "code": "request_timeout"
                    if "timeout" in error.lower()
                    else "request_failure"
                }
                if "deadline" in error.lower():
                    description = request_deadline_diagnostic(
                        request, execution, manifest
                    )
                found.append(
                    {
                        **description,
                        "source": "transport.sqlite",
                        "call": request["call"],
                        "evidence": error,
                        "authority": "transport",
                    }
                )
        for rejection in self.admission_by_trial.get(row["id"], []):
            found.append(
                {
                    "code": "transport_admission_"
                    + str(rejection.get("reason", "unknown")),
                    "source": "transport.sqlite/admission_rejections",
                    "rejection": rejection["rejection"],
                    "evidence": {
                        k: rejection.get(k)
                        for k in (
                            "reason",
                            "currentRequests",
                            "currentChargedTokens",
                            "reservedTokens",
                        )
                    },
                    "authority": "validated_transport_admission"
                    if rejection["auditValid"]
                    else "invalid_admission_evidence",
                }
            )
        if row.get("errorClass"):
            found.append(
                {
                    "code": "host_exception",
                    "source": "trial.errorClass",
                    "evidence": row["errorClass"],
                    "authority": "host_result",
                }
            )
        response = root / "response.txt"
        if response.exists() and not any(
            e["code"].startswith("budget_") for e in found
        ):
            match = re.search(
                r"stopped because ([^\n]+)", response.read_text(), re.IGNORECASE
            )
            if match:
                reason = match.group(1)
                found.append(
                    {
                        "code": stopping_code(reason),
                        "source": "response.txt",
                        "evidence": reason[:500],
                        "authority": "response_only_unconfirmed",
                    }
                )
        if not found:
            found = [
                {
                    "code": "unknown",
                    "source": None,
                    "evidence": "No recorded stopping cause; token utilization alone is insufficient",
                    "authority": "unknown",
                }
            ]
        terminal = []
        if row.get("oracleSuccess") is False:
            terminal.append("oracle_failed")
        if row.get("scopePassed") is False:
            terminal.append("scope_failed")
        if (
            row["arm"] == "explicit"
            and row.get("collaborationProtocolSatisfied") is False
        ):
            terminal.append("protocol_not_satisfied")
        return {
            "stopEvidence": found,
            "terminalOutcomes": terminal,
            "originalErrorClass": row.get("errorClass"),
        }

    def trial(self, row, case, manifest):
        trial = row["id"]
        root = self.directory / "private" / trial
        records = self.ledger(root, manifest, trial)
        metrics = self.metrics(records, manifest["price"])
        metrics["totalTokens"] = metrics["inputTokens"] + metrics["outputTokens"]
        for key, value in metrics.items():
            if key.endswith("CostCny") or key == "knownCostLowerBoundCny":
                self.amount(row.get(key), value, key, trial)
            else:
                self.equal(row.get(key), value, key, trial)
        admissions = self.admission_by_trial.get(trial, [])
        if self.rejection_protocol:
            self.equal(
                row.get("admissionRejectedCount"),
                len(admissions),
                "admission_rejected_count",
                trial,
            )
        self.equal(row.get("ledgerDamaged"), False, "ledger_damaged", trial)
        cleanup = read_json(root / "process-cleanup.json")
        self.equal(row.get("processCleanup"), cleanup, "cleanup_copy", trial)
        self.equal(cleanup.get("cleanupComplete"), True, "cleanup_incomplete", trial)
        for field in ("survivorPids", "evidenceErrors", "processScanErrors"):
            self.equal(cleanup.get(field, []), [], "cleanup_" + field, trial)
        self.equal(
            row.get("recoveryEvidenceErrors"), [], "recovery_evidence_missing", trial
        )
        collaboration = self.collaboration(root, row, records, manifest)
        diagnostics = self.stop_diagnostics(root, row, records, cleanup, manifest)
        changed = row.get("changedFiles", [])
        forbidden = sorted(set(changed) - set(case["authorized_paths"]))
        self.check(
            set(row.get("verificationInducedChanges", [])) <= set(changed),
            "unaccounted_verification_changes",
            "Oracle-induced changes must remain in total changedFiles",
            trial,
        )
        self.equal(
            sorted(row.get("unauthorizedChanges", [])),
            forbidden,
            "unauthorized_list",
            trial,
        )
        if forbidden:
            self.equal(row.get("scopePassed"), False, "scope_must_fail", trial)
            self.issue(
                "scope_violation",
                "Recorded as failed scope: " + ", ".join(forbidden),
                trial,
                "warning",
            )
        expected_success = (
            row.get("oracleSuccess") is True and row.get("scopePassed") is True
        )
        if row["arm"] == "explicit":
            expected_success = (
                expected_success and row.get("collaborationProtocolSatisfied") is True
            )
        self.equal(row.get("success"), expected_success, "success_formula", trial)
        self.equal(
            row.get("oraclePassed"), row.get("oracleSuccess"), "oracle_alias", trial
        )
        if row.get("oracleSuccess"):
            self.check(
                (root / "oracle.log").exists(),
                "missing_oracle_log",
                "Passed oracle lacks output evidence",
                trial,
            )
        budget = row.get("budget")
        execution_path = root / "execution.json"
        if execution_path.exists():
            execution = read_json(execution_path)
            self.equal(budget, execution.get("budget"), "execution_budget_copy", trial)
            self.equal(
                row.get("durationSeconds"),
                execution.get("durationSeconds"),
                "execution_duration_copy",
                trial,
            )
        if isinstance(budget, dict):
            comparisons = {
                "used_model_calls": metrics["modelCalls"],
                "used_input_tokens": metrics["inputTokens"],
                "used_output_tokens": metrics["outputTokens"],
                "used_total_tokens": metrics["totalTokens"],
            }
            differences = {
                k: {"budget": budget.get(k), "provider": v}
                for k, v in comparisons.items()
                if budget.get(k) != v
            }
            complete = metrics["tokenUsageComplete"] and all(
                r["event"] == "completed" for r in records
            )
            explained_calls = (
                complete
                and bool(admissions)
                and all(r["auditValid"] for r in admissions)
                and set(differences) == {"used_model_calls"}
                and budget.get("used_model_calls")
                == metrics["modelCalls"] + len(admissions)
            )
            if complete and admissions and not differences:
                self.issue(
                    "admission_budget_count_mismatch",
                    "Durable refusals exist but production counter equals only physical requests",
                    trial,
                )
            if differences:
                self.issue(
                    "budget_calls_include_admission_rejections"
                    if explained_calls
                    else "budget_receipt_difference",
                    json.dumps(differences)
                    + (
                        f"; exactly {len(admissions)} validated pre-HTTP refusals explain only the call counter difference; physical requests and token usage unchanged"
                        if explained_calls
                        else "; all requests completed with usage: reconcile admission refusals before accepting"
                        if complete
                        else "; failed/in-flight/unavailable usage may not have settled in production budget; retain provider receipts"
                    ),
                    trial,
                    "error" if complete and not explained_calls else "warning",
                )
        else:
            self.issue(
                "missing_budget_snapshot",
                "Hard-stop recovery may lack settled production budget; provider ledger remains authoritative",
                trial,
                "warning",
            )
        summary = root / "transport-summary.json"
        if summary.exists():
            stored = read_json(summary)
            if self.rejection_protocol:
                self.equal(
                    stored.get("admissionRejectedCount"),
                    len(admissions),
                    "snapshot_admission_count",
                    trial,
                )
                self.equal(
                    stored.get("admissionRejections"),
                    [
                        {k: v for k, v in r.items() if k != "auditValid"}
                        for r in admissions
                    ],
                    "snapshot_admission_records",
                    trial,
                )
            for key, expected in {
                "started": len(records),
                "unknownUsageCalls": metrics["unknownUsageCalls"],
                "inputTokensKnown": metrics["inputTokens"],
                "outputTokensKnown": metrics["outputTokens"],
                "cacheReadTokensKnown": metrics["cacheReadTokens"],
                "usageComplete": metrics["tokenUsageComplete"],
                "cacheUsageComplete": metrics["cacheUsageComplete"],
            }.items():
                self.equal(stored.get(key), expected, "snapshot_" + key, trial)
        export = root / "calls.jsonl"
        if export.exists():
            exported = [
                json.loads(line)
                for line in export.read_text().splitlines()
                if line.strip()
            ]
            starts_by_id = Counter(
                r.get("call") for r in exported if r.get("event") == "started"
            )
            expected_starts = Counter(r["call"] for r in records)
            self.equal(starts_by_id, expected_starts, "export_start_coverage", trial)
            terminals = [r for r in exported if r.get("event") != "started"]
            self.equal(
                Counter(r.get("call") for r in terminals),
                Counter(r["call"] for r in records if r["event"] != "started"),
                "export_terminal_coverage",
                trial,
            )
            by_id = {r["call"]: r for r in records}
            for record in terminals:
                if record.get("call") in by_id:
                    self.equal(
                        record, by_id[record["call"]], "export_terminal_copy", trial
                    )
        starts = root / "execution-start.json"
        if starts.exists():
            start = read_json(starts)
            for record in records:
                epoch = record.get("startedEpoch", 0)
                self.check(
                    start["epoch"] - 1 <= epoch <= start["deadlineEpoch"],
                    "request_outside_execution",
                    f"call={record['call']}",
                    trial,
                )
        metadata = list((root / "journal").glob("**/runs/*/metadata.json"))
        self.check(
            len(metadata) <= 1,
            "multiple_root_runs",
            f"root journal runs={len(metadata)}; investigate rerun",
            trial,
        )
        self.trials.append(
            {
                "id": trial,
                "arm": row["arm"],
                **metrics,
                "success": row.get("success"),
                "protocolSatisfied": row.get("collaborationProtocolSatisfied"),
                "recomputedCollaboration": collaboration,
                "diagnostics": diagnostics,
                "admissionRejectedCount": len(admissions),
                "validatedAdmissionRejections": sum(
                    r["auditValid"] for r in admissions
                ),
            }
        )

    def run(self):
        try:
            manifest_path = self.directory / "manifest.json"
            manifest = read_json(manifest_path)
            self.archived_hashes(manifest)
            tasks_path = self.directory / "tasks.json"
            self.equal(
                digest(tasks_path.read_bytes()), manifest["taskSha256"], "tasks_hash"
            )
            cases = {c["id"]: c for c in read_json(tasks_path)["cases"]}
            schedule = manifest["schedule"]
            assignments = {r["id"]: r for r in schedule}
            self.equal(len(assignments), len(schedule), "duplicate_schedule")
            cells = Counter((r["case"], r["repeat"], r["arm"]) for r in schedule)
            expected = set(
                itertools.product(cases, range(1, manifest["repeats"] + 1), ARMS)
            )
            self.check(
                set(cells) == expected and all(n == 1 for n in cells.values()),
                "unbalanced_schedule",
                "Expected full case × repeat × arm schedule",
            )
            result_path = self.directory / "results.json"
            result = read_json(result_path) if result_path.exists() else {"trials": []}
            if result_path.exists():
                self.equal(
                    result.get("manifestSha256"),
                    digest(manifest_path.read_bytes()),
                    "result_manifest_hash",
                )
                self.equal(
                    result.get("expectedTrials"), len(schedule), "expected_trials"
                )
            rows = result["trials"]
            ids = [r["id"] for r in rows]
            self.equal(len(ids), len(set(ids)), "duplicate_trial")
            self.check(
                set(ids) <= set(assignments),
                "unknown_trial",
                str(sorted(set(ids) - set(assignments))),
            )
            missing = sorted(set(assignments) - set(ids))
            self.check(
                not missing,
                "incomplete_results",
                f"{len(rows)}/{len(schedule)} terminal rows",
                severity="warning" if self.allow_partial else "error",
            )
            files = list((self.directory / "trials").glob("*.json"))
            self.equal({p.stem for p in files}, set(ids), "trial_file_coverage")
            for row in rows:
                trial = row["id"]
                try:
                    assignment = assignments[trial]
                    for key in ("id", "case", "repeat", "arm"):
                        self.equal(
                            row.get(key), assignment[key], "assignment_" + key, trial
                        )
                    self.equal(
                        read_json(self.directory / "trials" / (trial + ".json")),
                        row,
                        "result_trial_copy",
                        trial,
                    )
                    self.trial(row, cases[row["case"]], manifest)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    sqlite3.Error,
                ) as error:
                    self.issue(
                        "trial_evidence_unreadable",
                        type(error).__name__ + ": " + str(error),
                        trial,
                    )
            for path in (self.directory / "private").glob("*/transport.sqlite"):
                trial = path.parent.name
                self.check(trial in assignments, "unscheduled_ledger", trial, trial)
                if trial not in ids and trial in assignments:
                    self.issue(
                        "unfinalized_ledger",
                        "Retain and recover requests; do not rerun this trial",
                        trial,
                        "warning" if self.allow_partial else "error",
                    )
            expected_count = len(schedule)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            self.issue(
                "experiment_evidence_unreadable",
                type(error).__name__ + ": " + str(error),
            )
            expected_count = None
        return {
            "valid": not any(i["severity"] == "error" for i in self.issues),
            "mode": "partial" if self.allow_partial else "final",
            "expectedTrials": expected_count,
            "auditedTrials": len(self.trials),
            "deliveryEvidenceMode": "task_result_owner_ack"
            if self.owner_ack
            else "legacy_canonical_projection",
            "issues": self.issues,
            "trials": self.trials,
            "limits": "Static surviving evidence cannot prove that a ledger was never replaced; contiguous request IDs, frozen hashes, result copies and root-run counts detect observable omissions. No oracle or archived code was executed.",
        }


def write_report(directory, summary):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 三组评测独立数据审计",
        "",
        f"模式：{summary['mode']}；证据有效：{summary['valid']}；已审计 {summary['auditedTrials']}/{summary['expectedTrials']} 个 trial。",
        "",
        "失败或协作未触发本身不代表数据无效。错误须查明后再发布正式比较；warning 保留并解释，不删除相关试次。",
        "",
    ]
    for issue in summary["issues"]:
        lines.append(
            f"- {issue['severity']} / {issue['code']} / {issue['trial'] or 'experiment'}：{issue['detail']}"
        )
    if not summary["issues"]:
        lines.append("未发现证据矛盾。")
    lines += [
        "",
        "## 独立机制与停止原因",
        "",
        "以下按原始证据重算；停止原因可共现，属于事后诊断，不改变原始 errorClass、success 或预注册检验。仅 response 提及的原因标为未独立确认；缺证据保留 unknown。",
        "请求截止按时间证据细分：request_deadline_per_request 为达到冻结的单次请求时限；request_deadline_global 为请求受全局剩余执行时间限制；request_deadline 为证据不足或两条界限重合。此处不推断网络或模型推理原因。",
        "",
        "| trial | 实际叶子 / 送达叶子 | 协议合规 | HTTP 前拒绝 | 停止证据 | 终态判定 |",
        "| --- | ---: | --- | ---: | --- | --- |",
    ]
    for trial in summary["trials"]:
        collab = trial["recomputedCollaboration"]
        diag = trial["diagnostics"]
        reasons = "; ".join(
            f"{item['code']} ({item['authority']})" for item in diag["stopEvidence"]
        )
        lines.append(
            f"| {trial['id']} | {collab['actualLeafRuns']} / {collab['deliveredLeafResults']} | {collab['collaborationProtocolSatisfied']} | {trial['admissionRejectedCount']} | {reasons} | {', '.join(diag['terminalOutcomes']) or '无失败判定'} |"
        )
    lines += [
        "",
        "冻结来源从 ZIP 独立重算，不依赖当前工作区源码；SQLite 以只读模式读取，包含失败及未完成请求的 provider 回执。费用为冻结标准 API 单价等价成本，非套餐实际扣费。",
        "",
        summary["limits"],
        "",
    ]
    (directory / "audit-report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    summary = Audit(args.input, args.allow_partial).run()
    write_report(args.input, summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in ("valid", "mode", "expectedTrials", "auditedTrials")
            }
        )
    )
    return 0 if summary["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
