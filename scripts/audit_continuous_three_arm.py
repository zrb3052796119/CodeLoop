#!/usr/bin/env python3
"""Independent, offline-only audit of the continuous three-arm pilot.

No runner, provider, judge or archived Python is imported/executed. Task bytes
are hashed opaquely; task/oracle text, transcripts, transport response/reasoning
and tool arguments are never decoded. SQLite returns only named metadata.
This auditor never prepares, starts or retries a model run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import zipfile

PROTOCOL = "continuous-three-arm-pilot-v1"
CASE = "real-remediation-context"
ORDER = ("explicit", "single", "auto")
LIMITS = {"maxTokens": None, "maxModelCalls": None, "maxCostUsd": None,
          "wallSeconds": None, "requestTimeoutSeconds": None,
          "maxOutputTokens": 131072, "idleTimeoutSeconds": 120, "connectTimeoutSeconds": 30}
HEX = re.compile(r"[a-f0-9]{64}\Z")
PRICE = {"perMillionInput": Decimal(".8"), "perMillionOutput": Decimal("2.8"),
         "perMillionCacheRead": Decimal(".23")}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def integer(value):
    return type(value) is int and value >= 0


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def reject_constant(_value):
    raise ValueError("non-finite JSON constant")


class Audit:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.issues = []

    def issue(self, code, cell=None):
        entry = {"code": code}
        if cell is not None:
            entry["cell"] = cell
        if entry not in self.issues:
            self.issues.append(entry)

    def path(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("artifact path escapes audit root")
        return path

    def document(self, relative, *, cell=None):
        try:
            document = json.loads(self.path(relative).read_text(), parse_constant=reject_constant)
            if not isinstance(document, dict):
                raise ValueError("not an object")
            return document
        except (OSError, ValueError, TypeError):
            self.issue("missing_or_invalid_artifact", cell)
            return {}

    def mapping(self, document, key):
        value = document.get(key)
        if not isinstance(value, dict):
            self.issue("manifest_object_" + key)
            return {}
        return value

    @staticmethod
    def _projection(fields, row):
        result = {}
        for field, (kind, value) in zip(fields, zip(row[::2], row[1::2])):
            if kind is None:
                continue
            if kind in {"true", "false"}:
                value = kind == "true"
            elif kind in {"array", "object"}:
                value = json.loads(value)
            target = result
            parts = field.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return result

    def projected_document(self, relative, fields, *, cell=None, records=None):
        """Select public JSON fields inside SQLite before Python decodes values.

        Some aggregate files also carry the system prompt or judge details.
        Those fields never enter the audit's parsed objects or output.
        """
        try:
            raw = self.path(relative).read_bytes()
            with sqlite3.connect(":memory:") as connection:
                root_kind = connection.execute("SELECT json_type(?, '$')", (raw,)).fetchone()[0]
                if root_kind != "object":
                    raise ValueError("aggregate artifact must be an object")
                payload = "value" if records else "payload"
                expressions = ",".join(
                    f"json_type({payload}, '$.{name}'),json_extract({payload}, '$.{name}')"
                    for name in fields)
                query = "WITH source(payload) AS (VALUES(?)) SELECT " + expressions
                if records:
                    if connection.execute("SELECT json_type(?, ?)", (raw, "$." + records)).fetchone()[0] != "array":
                        raise ValueError("aggregate records must be an array")
                    query += f" FROM source,json_each(payload, '$.{records}')"
                else:
                    query += " FROM source"
                values = [self._projection(fields, row) for row in connection.execute(query, (raw,))]
                return values if records else values[0]
        except (OSError, ValueError, TypeError, sqlite3.Error):
            self.issue("missing_or_invalid_artifact", cell)
            return [] if records else {}

    def frozen(self, manifest):
        expected = [{"case": CASE, "repeat": 1, "arm": arm, "id": f"{CASE}-r1-{arm}"} for arm in ORDER]
        static = {"protocolVersion": PROTOCOL, "phase": "pilot", "seed": 20261017,
                  "schedule": expected, "arms": ["single", "auto", "explicit"],
                  "repeats": 1, "concurrency": 1, "limits": LIMITS,
                  "parentMaxSteps": None, "boundedImplementationPhases": False,
                  "roleTurnLimits": {"explore": 0, "plan": 0, "general": 0},
                  "model": "glm-5.3-flash", "temperature": 0,
                  "publicPytestReportRequired": True, "sameBudgetComparison": False,
                  "implementationCapabilities": "shared-public-checks-source-context-and-fixtures-v2"}
        for key, value in static.items():
            if key not in manifest or canonical(manifest[key]) != canonical(value):
                self.issue("manifest_contract_" + key)
        for key, name in (("taskSha256", "tasks.json"), ("analysisPlanSha256", "analysis-plan.md"),
                          ("frozenCodeArchiveSha256", "frozen-code.zip"),
                          ("publicPreflightSha256", "public-preflight/receipt.json")):
            try:
                if file_digest(self.path(name)) != manifest.get(key):
                    self.issue("frozen_hash_" + key)
            except (OSError, ValueError):
                self.issue("frozen_missing_" + key)
        for key in ("sourceTaskSha256", "sourceManifestSha256", "routeSha256",
                    "resolvedTransportRouteSha256", "commonRequirementSha256", "explicitRecipeSha256",
                    "baselineFilesSha256"):
            if not isinstance(manifest.get(key), str) or not HEX.fullmatch(manifest[key]):
                self.issue("manifest_identity_" + key)
        provenance = self.document("source-provenance.json")
        price = self.mapping(manifest, "price")
        if price.get("currency") != "CNY" or any(str(price.get(k)) != str(float(v)) for k, v in PRICE.items()):
            self.issue("frozen_price_card")
        judge = self.mapping(manifest, "judgementPolicy")
        for key, value in {"version": "common-generated-regression-v1", "totalSeconds": 120,
                           "regressionPath": "tests/test_eval_regression.py", "minimumPassed": 1,
                           "requireEveryCollectedPassed": True, "allowSkipOrXfail": False,
                           "separateUnfilteredRegressionRun": True, "deliveredCopiesOnly": True,
                           "receiptExitAndCleanupRequired": True, "agentBudgetSeconds": 1800}.items():
            if key not in judge or canonical(judge[key]) != canonical(value):
                self.issue("judge_policy_" + key)
        for a, b in (("preparedTaskSha256", "taskSha256"), ("sourceTaskSha256", "sourceTaskSha256"),
                     ("sourceManifestSha256", "sourceManifestSha256"),
                     ("baselineFilesSha256", "baselineFilesSha256")):
            if provenance.get(a) != manifest.get(b):
                self.issue("provenance_" + a)
        policy = self.mapping(manifest, "requestPolicy")
        if digest(json.dumps(policy, sort_keys=True).encode()) != manifest.get("requestPolicySha256"):
            self.issue("request_policy_hash")
        runtime = self.mapping(policy, "runtime")
        for key, value in {"maxOutputTokens": 131072, "modelTimeoutSeconds": None,
                           "temperature": 0, "modelMaxRetries": 0, "subagentRoutingEnabled": False}.items():
            if key not in runtime or canonical(runtime[key]) != canonical(value):
                self.issue("request_policy_" + key)
        try:
            with zipfile.ZipFile(self.path("frozen-code.zip")) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts for n in names):
                    raise ValueError("invalid archive namespace")
                production = sorted(n for n in names if n.startswith("minicode/") and n.endswith(".py"))
                if not production or digest(b"".join(n.encode() + b"\0" + archive.read(n) for n in production)) != manifest.get("productionSha256"):
                    self.issue("archive_production_hash")
                harness = manifest.get("harnessFiles", [])
                if not harness or any(not isinstance(n, str) or PurePosixPath(n).name != n for n in harness):
                    raise ValueError("invalid harness namespace")
                if digest(b"".join(n.encode() + b"\0" + archive.read("scripts/" + n) for n in harness)) != manifest.get("harnessSha256"):
                    self.issue("archive_harness_hash")
                for name, value in self.mapping(policy, "policySourceSha256").items():
                    if name not in names or digest(archive.read(name)) != value:
                        self.issue("archive_request_policy_source")
                if digest(archive.read("analysis-plan.md")) != manifest.get("analysisPlanSha256"):
                    self.issue("archive_plan_hash")
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            self.issue("invalid_frozen_archive")
        return expected

    def ledger(self, trial, manifest):
        identifier = trial["id"]
        columns = {"actorId": "actorId", "subagentId": "subagentId", "role": "role", "model": "model",
                   "route": "routeSha256", "started": "startedEpoch", "seconds": "seconds",
                   "outputCap": "maxOutputTokens", "deadline": "requestDeadlineEpoch", "tools": "toolNames",
                   "usageSource": "usage.source", "input": "usage.input_tokens", "output": "usage.output_tokens",
                   "cached": "usage.cache_read_tokens"}
        rows = []
        task_calls = 0
        try:
            path = self.path(f"private/{identifier}/transport.sqlite")
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
                query = "SELECT call,status," + ",".join("json_extract(data,'$." + key + "')" for key in columns.values()) + " FROM requests ORDER BY call"
                rows = [dict(zip(["call", "status"] + list(columns), row)) for row in connection.execute(query)]
                task_calls = connection.execute("SELECT count(*) FROM requests,json_each(requests.data,'$.toolCalls') AS tool WHERE json_extract(tool.value,'$.name')='task'").fetchone()[0]
        except (OSError, ValueError, sqlite3.Error):
            self.issue("ledger_missing_or_invalid", identifier)
        if [row["call"] for row in rows] != list(range(1, len(rows) + 1)):
            self.issue("ledger_call_sequence", identifier)
        known, cache_complete, total_in, total_out, cache_in = 0, True, 0, 0, 0
        lower = Decimal(0)
        for row in rows:
            if row["status"] not in {"completed", "failed", "started"}:
                self.issue("ledger_status", identifier)
            if row["model"] != manifest.get("model") or row["route"] != manifest.get("resolvedTransportRouteSha256"):
                self.issue("physical_model_route_drift", identifier)
            if row["outputCap"] != 131072 or row["deadline"] is not None:
                self.issue("physical_request_policy_drift", identifier)
            try:
                tools = json.loads(row["tools"]) if isinstance(row["tools"], str) else row["tools"]
            except ValueError:
                tools = None
            if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
                self.issue("physical_tools_missing", identifier)
                tools = []
            if trial["arm"] == "single" and ("task" in tools or row["subagentId"] is not None):
                self.issue("single_delegation_authority", identifier)
            if trial["arm"] == "explicit" and row["role"] == "main" and set(tools) - {"task", "todo_write"}:
                self.issue("explicit_parent_authority", identifier)
            if row["usageSource"] == "provider" and integer(row["input"]) and integer(row["output"]):
                known += 1
                total_in += row["input"]
                total_out += row["output"]
                cached = row["cached"]
                if not integer(cached) or cached > row["input"]:
                    cache_complete = False
                    cached_lower = row["input"]
                    if cached is not None:
                        self.issue("invalid_provider_cache_usage", identifier)
                else:
                    cache_in += cached
                    cached_lower = cached
                lower += ((row["input"] - cached_lower) * PRICE["perMillionInput"] + cached_lower * PRICE["perMillionCacheRead"] + row["output"] * PRICE["perMillionOutput"]) / 1000000
            elif row["usageSource"] == "provider":
                self.issue("invalid_provider_token_usage", identifier)
        complete = known == len(rows) and bool(rows)
        cache_complete = complete and cache_complete
        counts = Counter(row["status"] for row in rows)
        return rows, {"modelCalls": len(rows), "failedCalls": counts["failed"], "inFlightCalls": counts["started"],
                      "unknownUsageCalls": len(rows) - known, "tokenUsageComplete": complete,
                      "cacheUsageComplete": cache_complete, "usageComplete": complete and cache_complete,
                      "totalTokens": total_in + total_out if complete else None,
                      "inputTokens": total_in if complete else None, "outputTokens": total_out if complete else None,
                      "cacheReadTokens": cache_in if cache_complete else None,
                      "knownTokenLowerBound": total_in + total_out,
                      "estimatedCostCny": float(lower) if complete and cache_complete else None,
                      "knownCostLowerBoundCny": float(lower) if known else None,
                      "delegationAttempts": task_calls}

    def absent_collaboration_evidence(self, trial, rows, usage, *, terminal, clean):
        """The producer saves lazily: absence is valid only with positive zero-delegation evidence."""
        if (trial["arm"] not in {"single", "auto"} or not terminal or not clean or not rows
                or usage["usageComplete"] is not True or usage["delegationAttempts"] != 0
                or [row["call"] for row in rows] != list(range(1, len(rows) + 1))
                or any(row["status"] != "completed" or row["role"] != "main"
                       or row["actorId"] != "main" or row["subagentId"] is not None for row in rows)):
            return None
        prefix = f"private/{trial['id']}/"
        try:
            artifact = self.path(prefix + "collaboration.json")
            if artifact.exists():
                return None  # Present but malformed must pass the original strict reader.
            with sqlite3.connect(self.path(prefix + "transport.sqlite").as_uri() + "?mode=ro", uri=True) as db:
                complete = db.execute("""SELECT count(*) FROM requests WHERE
                    json_type(data,'$.subagentId')='null' AND json_type(data,'$.toolCalls')='array'""").fetchone()[0]
                bad_calls = db.execute("""SELECT count(*) FROM requests,json_each(requests.data,'$.toolCalls') tool
                    WHERE json_type(tool.value,'$')!='object'
                    OR coalesce(json_type(tool.value,'$.name'),'')!='text'
                    OR json_extract(tool.value,'$.name')=''
                    OR json_extract(tool.value,'$.name')='task'
                    OR json_extract(tool.value,'$.function.name')='task'""").fetchone()[0]
            if complete != len(rows) or bad_calls:
                return None
            journal = self.path(prefix + "journal")
            paths = list(journal.glob("dashboard/workspaces/*/runs/*/events.ndjson"))
            if len(paths) != 1 or any(path.is_file() for path in journal.glob("**/subagent-runs/**/*")):
                return None
            events_path = self.path(str(paths[0].relative_to(self.root)))
            with sqlite3.connect(":memory:") as db:
                db.execute("CREATE TABLE host(data TEXT)")
                with events_path.open("rb") as stream:
                    db.executemany("INSERT INTO host VALUES (?)", ((line,) for line in stream))
                events = db.execute("""SELECT json_extract(data,'$.sequence'),json_extract(data,'$.runId'),
                    json_extract(data,'$.type'),json_extract(data,'$.payload.toolName'),
                    json_extract(data,'$.payload.subagentId') FROM host ORDER BY json_extract(data,'$.sequence')""").fetchall()
            if (not events or [event[0] for event in events] != list(range(1, len(events) + 1))
                    or any(not isinstance(event[1], str) or not isinstance(event[2], str) for event in events)
                    or len({event[1] for event in events}) != 1
                    or any(event[2].startswith('subagent.') or event[3] == 'task' or event[4] is not None for event in events)):
                return None
            types = Counter(event[2] for event in events)
            if (types['run.started'] != 1 or types['run.completed'] != 1 or events[-1][2] != 'run.completed'
                    or types['model.started'] != len(rows) or types['model.completed'] != len(rows)
                    or any(types[name] for name in ('run.failed', 'run.interrupted', 'run.cancelled', 'model.failed'))):
                return None
            return {"transportCalls": len(rows), "hostEvents": len(events), "hostModelCompleted": types['model.completed'],
                    "taskCalls": 0, "physicalChildren": 0, "hostChildEvents": 0}
        except (OSError, ValueError, TypeError, sqlite3.Error):
            return None

    def collaboration(self, trial, rows, *, zero_delegation=None):
        identifier = trial["id"]
        if zero_delegation is not None:
            return {"actualDelegationObserved": False, "physicalLeafRuns": 0,
                    "ownerAcknowledgedLeafResults": 0, "ownerAcknowledgedWorkflows": 0,
                    "collaborationProtocolSatisfied": False,
                    "collaborationArtifactStatus": "absent_no_delegation_verified",
                    "zeroDelegationEvidence": zero_delegation}
        path = f"private/{identifier}/collaboration.json"
        data = self.projected_document(path, ("deliveredIds",), cell=identifier)
        records = self.projected_document(path, (
            "subagentId", "isContainer", "agentType", "role", "parentSubagentId", "returned",
            "startedEpoch", "returnedEpoch", "deliveredEpoch", "deliveryEvidence", "resultSha256",
        ), cell=identifier, records="runs")
        delivered = data.get("deliveredIds", [])
        if not isinstance(records, list) or not isinstance(delivered, list):
            self.issue("collaboration_shape", identifier)
            records, delivered = [], []
        ids = [r.get("subagentId") for r in records if isinstance(r, dict)]
        if any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
            self.issue("collaboration_identities", identifier)
        physical_ids = {row["subagentId"] for row in rows if isinstance(row["subagentId"], str)}
        completed_ids = {row["subagentId"] for row in rows if row["status"] == "completed"}
        if physical_ids - {value for value in ids if isinstance(value, str)}:
            self.issue("physical_child_missing_collaboration_record", identifier)
        def ack(record):
            times = [record.get(k) for k in ("startedEpoch", "returnedEpoch", "deliveredEpoch")]
            return (record.get("returned") is True and record.get("subagentId") in delivered
                    and record.get("deliveryEvidence") == "task_result_owner_ack"
                    and isinstance(record.get("resultSha256"), str) and bool(HEX.fullmatch(record["resultSha256"]))
                    and all(finite(t) for t in times) and times[0] <= times[1] <= times[2])
        workflows = {r["subagentId"]: r for r in records if isinstance(r, dict) and r.get("isContainer") is True and r.get("agentType") == "workflow" and isinstance(r.get("subagentId"), str)}
        delivered_workflows = {key: value for key, value in workflows.items() if ack(value)}
        leaves = [r for r in records if isinstance(r, dict) and not r.get("isContainer")
                  and any(row["subagentId"] == r.get("subagentId") for row in rows)]
        delivered_leaves = []
        implementation = []
        for record in leaves:
            completed = any(row["subagentId"] == record.get("subagentId") and row["status"] == "completed" for row in rows)
            workflow = delivered_workflows.get(record.get("parentSubagentId"))
            via = bool(workflow and record.get("returned") is True and finite(record.get("startedEpoch"))
                       and finite(record.get("returnedEpoch")) and workflow["startedEpoch"] <= record["startedEpoch"] <= record["returnedEpoch"] <= workflow["returnedEpoch"])
            if completed and (ack(record) or via):
                delivered_leaves.append(record)
            if completed and via and record.get("role") == "executor":
                implementation.append(record)
        external = [r for r in leaves if r.get("subagentId") in completed_ids
                    and r.get("parentSubagentId") not in workflows]
        return {"actualDelegationObserved": bool(physical_ids), "physicalLeafRuns": len(physical_ids),
                "ownerAcknowledgedLeafResults": len(delivered_leaves),
                "ownerAcknowledgedWorkflows": len(delivered_workflows),
                "collaborationProtocolSatisfied": bool(implementation and delivered_workflows and not external)}

    def trial(self, trial, manifest):
        identifier = trial["id"]
        row = self.projected_document(f"trials/{identifier}.json", (
            "id", "case", "repeat", "arm", "success", "regressionTestPassed", "scopePassed", "oraclePassed",
            "judgement.cleanupComplete", "processCleanup.cleanupComplete", "durationSeconds", "judgeSeconds",
            "budget.limit_total_tokens", "budget.limit_model_calls", "budget.limit_cost_usd",
            "modelCalls", "failedCalls", "inFlightCalls", "unknownUsageCalls", "tokenUsageComplete",
            "cacheUsageComplete", "usageComplete", "totalTokens", "inputTokens", "outputTokens",
            "cacheReadTokens", "knownTokenLowerBound", "estimatedCostCny", "knownCostLowerBoundCny",
            "delegationAttempts", "collaborationProtocolSatisfied",
        ), cell=identifier)
        for key, value in trial.items():
            if row.get(key) != value:
                self.issue("trial_identity", identifier)
        prefix = f"private/{identifier}/"
        start = self.document(prefix + "execution-start.json", cell=identifier)
        if not finite(start.get("epoch")) or "deadlineEpoch" not in start or start["deadlineEpoch"] is not None:
            self.issue("execution_start_or_deadline", identifier)
        runtime = self.projected_document(prefix + "runtime-contract.json", ("model", "routeSha256"), cell=identifier)
        if runtime.get("model") != manifest.get("model") or runtime.get("routeSha256") != manifest.get("routeSha256"):
            self.issue("runtime_model_route", identifier)
        config = self.document(prefix + "transport-config.json", cell=identifier)
        expected = {"expected_model": manifest.get("model"), "expected_route_sha256": manifest.get("resolvedTransportRouteSha256"),
                    "deadline_epoch": None, "max_model_calls": None, "max_total_tokens": None,
                    "request_timeout_seconds": None, "max_output_tokens": 131072,
                    "idle_timeout_seconds": 120, "connect_timeout_seconds": 30}
        for key, value in expected.items():
            if key not in config or config[key] != value:
                self.issue("transport_config_" + key, identifier)
        baseline = self.document(prefix + "baseline-files.json", cell=identifier)
        baseline_sha = digest(canonical(baseline))
        if not baseline or baseline_sha != manifest.get("baselineFilesSha256"):
            self.issue("baseline_not_original", identifier)
        cleanup = self.document(prefix + "process-cleanup.json", cell=identifier)
        clean = cleanup.get("cleanupComplete") is True and cleanup.get("survivorPids", []) == []
        if not clean:
            self.issue("cleanup_incomplete", identifier)
        if row.get("processCleanup", {}).get("cleanupComplete") is not clean:
            self.issue("row_cleanup_mismatch", identifier)
        for key in ("limit_total_tokens", "limit_model_calls", "limit_cost_usd"):
            if isinstance(row.get("budget"), dict) and row["budget"].get(key) is not None:
                self.issue("runtime_budget_not_null", identifier)
        requests, usage = self.ledger(trial, manifest)
        zero_delegation = self.absent_collaboration_evidence(trial, requests, usage,
            terminal=type(row.get("success")) is bool and all(row.get(k) == v for k, v in trial.items()), clean=clean)
        delegation = self.collaboration(trial, requests, zero_delegation=zero_delegation)
        for key, value in usage.items():
            observed = row.get(key)
            match = ((finite(observed) and math.isclose(observed, value, rel_tol=1e-9, abs_tol=1e-9))
                     if type(value) is float else type(observed) is type(value) and observed == value)
            if not match:
                self.issue("row_metering_" + key, identifier)
        if row.get("collaborationProtocolSatisfied") is not delegation["collaborationProtocolSatisfied"]:
            self.issue("row_collaboration_mismatch", identifier)
        public = row.get("regressionTestPassed") is True and row.get("scopePassed") is True
        external = row.get("oraclePassed") is True
        judged = isinstance(row.get("judgement"), dict) and row["judgement"].get("cleanupComplete") is True
        success = public and external and clean and judged and (trial["arm"] != "explicit" or delegation["collaborationProtocolSatisfied"])
        if row.get("success") is not success:
            self.issue("row_success_mismatch", identifier)
        for field in ("durationSeconds", "judgeSeconds"):
            if not finite(row.get(field)) or row[field] < 0:
                self.issue("trial_timing_missing", identifier)
        return {**trial, "terminalRowPresent": bool(row), "reportedSuccess": row.get("success") is True,
                "publicChecksPassed": public, "externalAcceptancePassed": external,
                "judgementCleanupComplete": judged, "processCleanupComplete": clean,
                "durationSeconds": row.get("durationSeconds"), "judgeSeconds": row.get("judgeSeconds"),
                "executionStartEpoch": start.get("epoch"),
                "baselineSha256": baseline_sha, **usage, **delegation}

    def run(self):
        manifest = self.document("manifest.json")
        expected = self.frozen(manifest)
        rows = [self.trial(trial, manifest) for trial in expected]
        observed = {p.name for p in self.root.glob("trials/*.json")}
        if observed != {row["id"] + ".json" for row in expected}:
            self.issue("trial_set_not_exactly_three")
        if len({row["baselineSha256"] for row in rows}) != 1:
            self.issue("baseline_drift_between_arms")
        for prior, following in zip(rows, rows[1:]):
            if (finite(prior["executionStartEpoch"]) and finite(prior["durationSeconds"])
                    and finite(following["executionStartEpoch"])
                    and prior["executionStartEpoch"] + prior["durationSeconds"] > following["executionStartEpoch"]):
                self.issue("trial_order_or_overlap", following["id"])
        return {"auditVersion": 1, "protocolVersion": PROTOCOL, "draftOnly": False,
                "valid": not self.issues, "issues": self.issues, "trials": rows,
                "allCellsTerminal": all(row["terminalRowPresent"] for row in rows),
                "limitations": ["One reused task, one observation per arm: no statistical/generalization claim.",
                    "Success aggregates are checked for consistency, not a rerun of the judge.",
                    "Unknown provider usage remains null; known lower bounds are not total costs.",
                    "No archived code or task/oracle/model-response text was executed or interpreted.",
                    "A clean audit does not authorize additional model runs or replace missing trial evidence."]}


def audit_directory(root):
    return Audit(root).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    result = audit_directory(arguments.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
