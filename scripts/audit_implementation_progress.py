#!/usr/bin/env python3
"""Independent direct-workflow audit; never import the runner or execute evidence."""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import math
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from pathlib import Path

try:
    from scripts import audit_three_arm_real as legacy
except ModuleNotFoundError:
    import audit_three_arm_real as legacy

ENGINEERING_CASES = (
    "real-vision-budget",
    "real-remediation-context",
    "real-concurrent-context",
)


def owner_delivered(run: dict, delivered_ids: set[str]) -> bool:
    """Only a durable host callback proves delivery in this protocol version."""
    times = [run.get(k) for k in ("startedEpoch", "returnedEpoch", "deliveredEpoch")]
    return bool(
        run.get("subagentId") in delivered_ids
        and run.get("returned") is True
        and run.get("deliveryEvidence") == "task_result_owner_ack"
        and isinstance(run.get("resultSha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", run["resultSha256"])
        and all(type(t) in (int, float) and math.isfinite(t) for t in times)
        and times[0] <= times[1] <= times[2]
    )


def direct_collaboration(runs: list, requests: list, delivered_ids: list) -> dict:
    """Recompute roles and own-container handoffs without the target helper."""
    delivered_ids = set(delivered_ids)
    containers = {
        r["subagentId"]: r
        for r in runs
        if r.get("isContainer") and r.get("agentType") == "workflow"
    }
    delivered_workflows = {
        actor: r for actor, r in containers.items() if owner_delivered(r, delivered_ids)
    }
    leaves = {r["subagentId"]: r for r in runs if not r.get("isContainer")}
    responded = {q.get("subagentId") for q in requests if q.get("event") == "completed"}
    actual = [r for actor, r in leaves.items() if actor in responded]
    handed_off, implementation, analysis, analysts_delivered = [], [], [], []
    for run in actual:
        workflow = delivered_workflows.get(run.get("parentSubagentId"))
        via_workflow = bool(
            workflow
            and run.get("returned") is True
            and workflow["startedEpoch"] <= run["startedEpoch"]
            and run.get("returnedEpoch", math.inf) <= workflow["returnedEpoch"]
        )
        if run.get("returned") is True and (
            owner_delivered(run, delivered_ids) or via_workflow
        ):
            handed_off.append(run)
            role = str(run.get("role", "")).lower()
            if role in {"plan", "explore", "analyst"}:
                analysis.append(run)
            if role == "executor" and via_workflow:
                implementation.append(run)
            if role == "analyst" and via_workflow:
                analysts_delivered.append(run)
    external = [r for r in actual if r.get("parentSubagentId") not in containers]
    return {
        "actualLeafRuns": len(actual),
        "leafModelCalls": sum(q.get("subagentId") in leaves for q in requests),
        "deliveredLeafResults": len(handed_off),
        "successfulLeafRuns": sum(
            r.get("toolOk") is True
            and isinstance(r.get("projected"), dict)
            and r["projected"].get("outcome") == "completed"
            and not r["projected"].get("partial", False)
            for r in actual
        ),
        "readAnalysisLeafRuns": len(analysis),
        "implementationLeafRuns": len(implementation),
        "collaborationExecuted": bool(handed_off),
        "collaborationProtocolSatisfied": bool(
            implementation and delivered_workflows and not external
        ),
        "deliveredWorkflowResults": len(delivered_workflows),
        "actualAnalystLeafRuns": sum(
            str(r.get("role", "")).lower() == "analyst" for r in actual
        ),
        "deliveredAnalystLeafResults": len(analysts_delivered),
        "externalAnalysisLeafRuns": sum(
            str(r.get("role", "")).lower() in {"plan", "explore", "analyst"}
            for r in external
        ),
        "externalLeafRuns": len(external),
    }


def literal_names(
    source: bytes, name: str, *, pattern=r"[A-Za-z0-9_-]+\.py"
) -> list[str]:
    assignments = [
        node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]
    if len(assignments) != 1:
        raise ValueError(f"Frozen runner must declare one literal {name}")
    value = ast.literal_eval(assignments[0])
    if not isinstance(value, (tuple, list)) or not all(
        isinstance(n, str) and re.fullmatch(pattern, n) for n in value
    ):
        raise ValueError(f"Frozen {name} must contain valid literal names")
    return list(value)


def literal_object(source: bytes, name: str):
    values = [
        node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]
    if len(values) != 1:
        raise ValueError(f"Frozen source must declare one literal {name}")
    return ast.literal_eval(values[0])


class Audit(legacy.Audit):
    """Reuse receipt, price, budget, cleanup and permission checks unchanged."""

    def role_turn_policy(self, manifest, bundle):
        """Schema 4 separates host overrides from defaults; schema 3 stays historical."""
        if manifest.get("schemaVersion") not in (4, 5):
            if manifest.get("schemaVersion") == 3:
                declared = any(
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "ROLE_TURN_POLICY"
                        for t in node.targets
                    )
                    for node in ast.parse(
                        bundle.read("scripts/run_implementation_progress_live.py")
                    ).body
                )
                self.check(
                    not declared and "roleTurnPolicy" not in manifest,
                    "role_policy_schema",
                    "Do not reinterpret a schema 4 archive as historical schema 3",
                )
            return
        source = bundle.read("minicode/subagent_types.py")
        agent_types = literal_object(source, "AGENT_TYPES")
        runner_source = bundle.read("scripts/run_implementation_progress_live.py")
        historical_ceiling_present = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_MAX_TURNS_OVERRIDE_CEILING"
                for target in node.targets
            )
            for node in ast.parse(source).body
        )
        # Old archives declared this ceiling in production. New production is
        # continuous by default; only the legacy bounded evaluator owns it.
        ceiling = literal_object(
            source if historical_ceiling_present else runner_source,
            "_MAX_TURNS_OVERRIDE_CEILING"
            if historical_ceiling_present
            else "_LEGACY_ROLE_OVERRIDE_CEILING",
        )
        if type(ceiling) is not int or ceiling != 200:
            raise ValueError("Invalid historical bounded role override ceiling")
        roles = ("explore", "plan", "general")
        defaults = {role: agent_types[role]["max_turns"] for role in roles}
        if any(type(v) is not int or v <= 0 for v in defaults.values()):
            raise ValueError("Frozen production has invalid role defaults")
        names = literal_object(source, "_MAX_TURNS_ENV_NAMES")
        self.equal(
            names,
            {r: f"MINI_CODE_SUBAGENT_{r.upper()}_MAX_TURNS" for r in roles},
            "role_env_names",
        )
        self.check(
            any(
                isinstance(node, ast.FunctionDef)
                and node.name == "configured_max_turns"
                for node in ast.parse(source).body
            ),
            "missing_configured_role_resolver",
            "schema 4",
        )
        declared = literal_object(
            runner_source,
            "ROLE_TURN_POLICY",
        )
        self.equal(
            declared.get("version"),
            "configured-overrides-shared-executor-v1",
            "role_policy_version",
        )
        self.equal(declared.get("overrideRange"), [1, ceiling], "role_override_range")
        overrides = manifest.get("roleTurnLimits")
        policy = manifest.get("roleTurnPolicy")
        if not isinstance(overrides, dict) or not isinstance(policy, dict):
            raise ValueError("Missing schema 4 role configuration")
        if any(
            r not in roles or type(v) is not int or not 1 <= v <= ceiling
            for r, v in overrides.items()
        ):
            raise ValueError("Invalid explicit role override")
        bindings = policy.get("bindings")
        if not isinstance(bindings, dict) or set(bindings) != set(roles):
            raise ValueError("Invalid role source bindings")
        for role, binding in bindings.items():
            if not isinstance(binding, dict) or set(binding) != {"source", "override"}:
                raise ValueError("Invalid safe role binding fields")
            self.check(
                (
                    (
                        role in overrides
                        and binding["source"]
                        in {"process_env", "private_env", "private_env_unreadable"}
                        and (
                            (
                                binding["override"] == "valid"
                                and binding["source"] != "private_env_unreadable"
                            )
                            or (
                                binding["override"] == "invalid"
                                and overrides[role] == defaults[role]
                            )
                        )
                    )
                    or (
                        role not in overrides
                        and binding == {"source": "default", "override": "absent"}
                    )
                ),
                "role_binding_" + role,
                "Configured/default binding must agree with injected role limits",
            )
        self.equal(
            policy,
            {
                **declared,
                "defaults": defaults,
                "resolved": {r: overrides.get(r, defaults[r]) for r in roles},
                "bindings": bindings,
            },
            "role_policy_frozen_contract",
        )

    def public_fixture_preflight(self, manifest, bundle, capabilities):
        # Historical v1 archives remain auditable without invented fixture
        # fields. The frozen runner literal, not a mutable manifest label,
        # decides whether the new inputs/preflight contract is mandatory.
        if capabilities != "shared-public-checks-source-context-and-fixtures-v2":
            return
        policy = literal_object(bundle.read("scripts/public_verification_fixtures.py"), "PUBLIC_FIXTURE_POLICY")
        self.equal(manifest.get("publicVerificationFixturePolicy"), policy, "public_fixture_policy")
        tasks = legacy.read_json(self.directory / "tasks.json")
        bindings = []
        for case in tasks["cases"]:
            for item in policy["registered"]:
                if item["case"] != case["id"]:
                    continue
                content = {**tasks.get("shared_files", {}), **case["files"]}.get(item["path"])
                self.check(isinstance(content, str), "public_fixture_missing", case["id"])
                if isinstance(content, str):
                    self.equal(legacy.digest(content.encode()), item["sha256"], "public_fixture_content")
                    self.equal(len(content.encode()), item["bytes"], "public_fixture_size")
                bindings.append(item)
        self.equal(manifest.get("publicVerificationFixtures"), bindings, "public_fixture_bindings")
        raw = (self.directory / "public-preflight/receipt.json").read_bytes()
        self.equal(legacy.digest(raw), manifest.get("publicPreflightSha256"), "public_preflight_hash")
        report = json.loads(raw)
        self.equal(report.get("policy"), policy, "public_preflight_policy")
        self.equal(report.get("fixtures"), bindings, "public_preflight_fixtures")
        self.equal(report.get("taskDocumentSha256"), legacy.digest(json.dumps(tasks, sort_keys=True, separators=(",", ":")).encode()), "public_preflight_tasks")
        self.equal(report.get("passed"), True, "public_preflight_passed")
        self.equal([r.get("case") for r in report.get("cases", [])], [c["id"] for c in tasks["cases"]], "public_preflight_coverage")
        for row, case in zip(report.get("cases", []), tasks["cases"]):
            self.equal(row.get("argv"), case["verification_argv"], "public_preflight_argv")
            self.equal(row.get("status"), "passed", "public_preflight_status")
            self.equal(row.get("verification", {}).get("status"), "passed", "public_preflight_verifier_status")
            self.equal(len(row.get("evidence", [])), 1, "public_preflight_evidence_count")
            for evidence in row.get("evidence", []):
                self.equal(evidence.get("argv"), case["verification_argv"], "public_preflight_evidence_argv")
                self.equal(evidence.get("status"), "passed", "public_preflight_evidence_status")
                self.equal(evidence.get("exitCode"), 0, "public_preflight_exit")

    def archived_hashes(self, manifest):
        archive = self.directory / "frozen-code.zip"
        self.equal(
            legacy.digest(archive.read_bytes()),
            manifest["frozenCodeArchiveSha256"],
            "archive_hash",
        )
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            self.equal(len(names), len(set(names)), "duplicate_archive_entries")
            self.role_turn_policy(manifest, bundle)
            runner_source = bundle.read("scripts/run_implementation_progress_live.py")
            declarations = {
                t.id
                for n in ast.parse(runner_source).body
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name)
            }
            if "IMPLEMENTATION_CAPABILITIES" in declarations:
                capabilities = literal_object(runner_source, "IMPLEMENTATION_CAPABILITIES")
                self.equal(manifest.get("implementationCapabilities"), capabilities, "frozen_implementation_capabilities")
                self.public_fixture_preflight(manifest, bundle, capabilities)
            if "PROTOCOL_VERSION" in declarations:
                self.equal(
                    manifest.get("protocolVersion"),
                    literal_object(runner_source, "PROTOCOL_VERSION"),
                    "frozen_protocol",
                )
            if manifest.get("schemaVersion") == 5:
                self.equal(
                    manifest.get("judgementPolicy"),
                    literal_object(
                        bundle.read("scripts/implementation_judge.py"), "JUDGE_POLICY"
                    ),
                    "frozen_judgement_policy",
                )
                self.equal(
                    manifest["judgementPolicy"].get("totalSeconds"),
                    120,
                    "judge_total_limit",
                )
                self.equal(
                    manifest["judgementPolicy"].get("regressionPath"),
                    "tests/test_eval_regression.py",
                    "judge_regression_path",
                )
            production = sorted(
                n for n in names if n.startswith("minicode/") and n.endswith(".py")
            )
            self.check(
                bool(production),
                "empty_production_archive",
                "No production Python sources",
            )
            self.equal(
                legacy.digest(
                    b"".join(n.encode() + b"\0" + bundle.read(n) for n in production)
                ),
                manifest["productionSha256"],
                "production_hash",
            )
            harness = literal_names(
                bundle.read("scripts/run_implementation_progress_live.py"),
                "SUCCESSOR_HARNESS_FILES",
            )
            small = literal_names(
                bundle.read("scripts/run_implementation_progress_live.py"),
                "SMALL_CASES",
                pattern=r"[A-Za-z0-9_-]+",
            )
            self.equal(small, list(ENGINEERING_CASES), "frozen_engineering_cases")
            self.equal(len(harness), len(set(harness)), "duplicate_harness_files")
            self.equal(manifest.get("harnessFiles"), harness, "harness_file_list")
            inherited = literal_names(
                bundle.read("scripts/run_three_arm_real.py"), "HARNESS_FILES"
            )
            required = set(inherited) | {
                "run_implementation_progress_live.py",
                "run_three_arm_real.py",
                "audit_implementation_progress.py",
                "audit_three_arm_real.py",
                "three_arm_protocol.py",
                "evaluation_transport.py",
            }
            if manifest.get("schemaVersion") == 5:
                required.add("implementation_judge.py")
            self.check(
                required <= set(harness),
                "missing_harness_dependencies",
                str(sorted(required - set(harness))),
            )
            self.equal(
                legacy.digest(
                    b"".join(
                        n.encode() + b"\0" + bundle.read("scripts/" + n)
                        for n in harness
                    )
                ),
                manifest["harnessSha256"],
                "harness_hash",
            )
            self.equal(
                legacy.digest(bundle.read("analysis-plan.md")),
                manifest["analysisPlanSha256"],
                "analysis_plan_hash",
            )
            self.equal(
                legacy.digest((self.directory / "analysis-plan.md").read_bytes()),
                manifest["analysisPlanSha256"],
                "live_analysis_plan_hash",
            )
            self.owner_ack = b"task_result_owner_ack" in bundle.read(
                "scripts/three_arm_protocol.py"
            )
            self.rejection_protocol = b"admission_rejections" in bundle.read(
                "scripts/evaluation_transport.py"
            )
            self.check(
                self.owner_ack,
                "missing_owner_ack_protocol",
                "Direct workflow requires durable host delivery",
            )
            self.check(
                self.rejection_protocol,
                "missing_admission_evidence",
                "Successor requires durable pre-HTTP refusal records",
            )

    def provenance(self, manifest, tasks):
        provenance = legacy.read_json(self.directory / "source-provenance.json")
        for key in ("sourceSuite", "sourceTaskSha256", "sourceManifestSha256", "phase"):
            self.equal(provenance.get(key), manifest.get(key), "provenance_" + key)
        self.equal(
            provenance.get("preparedTaskSha256"),
            manifest["taskSha256"],
            "prepared_task_hash",
        )
        self.equal(
            provenance.get("selectedCases"),
            [c["id"] for c in tasks["cases"]],
            "selected_cases",
        )
        self.equal(
            manifest.get("reusedCalibrationCases"),
            list(ENGINEERING_CASES),
            "reused_calibration_cases",
        )
        source = Path(provenance["sourceDirectory"])
        self.equal(source.name, manifest.get("sourceSuite"), "source_suite_directory")
        source_task_bytes = (source / "tasks.json").read_bytes()
        source_manifest_bytes = (source / "manifest.json").read_bytes()
        self.equal(
            legacy.digest(source_task_bytes),
            manifest["sourceTaskSha256"],
            "source_tasks_hash",
        )
        self.equal(
            legacy.digest(source_manifest_bytes),
            manifest["sourceManifestSha256"],
            "source_manifest_hash",
        )
        self.equal(
            json.loads(source_manifest_bytes)["taskSha256"],
            manifest["sourceTaskSha256"],
            "source_frozen_tasks_hash",
        )
        source_tasks = json.loads(source_task_bytes)
        source_cases = {c["id"]: c for c in source_tasks["cases"]}
        self.check(
            len(source_cases) == len(source_tasks["cases"]) == 8,
            "source_task_count",
            "Expected eight unique historical tasks",
        )
        if manifest["phase"] == "engineering":
            source_tasks["cases"] = [source_cases[c] for c in ENGINEERING_CASES]
        self.equal(tasks, source_tasks, "historical_task_content")

    def engineering_gate(self, manifest):
        """Re-audit the six observations; never trust a prewritten pass flag."""
        prior = manifest.get("engineeringGate")
        if manifest["phase"] == "engineering":
            self.equal(prior, None, "engineering_self_gate")
            return
        self.check(
            isinstance(prior, dict),
            "missing_engineering_gate",
            "Formal phase requires six audited engineering observations",
        )
        if not isinstance(prior, dict):
            return
        directory = Path(prior["directory"])
        small_manifest = legacy.read_json(directory / "manifest.json")
        if small_manifest.get("phase") != "engineering":
            self.issue(
                "invalid_engineering_gate_phase",
                "Gate must reference an engineering phase; no recursive formal references",
            )
            return
        observed = Audit(directory, allow_partial=False).run()
        rows = legacy.read_json(directory / "results.json")["trials"]
        cells = Counter((r.get("case"), r.get("repeat"), r.get("arm")) for r in rows)
        wanted = set(itertools.product(ENGINEERING_CASES, (1,), ("single", "explicit")))
        repairs = [
            r
            for r in rows
            if r.get("oracleSuccess") is True
            and (
                small_manifest.get("schemaVersion") != 5
                or r.get("regressionTestPassed") is True
            )
            and r.get("scopePassed") is True
            and r.get("changedSourceFiles")
        ]
        by_arm = Counter(r["arm"] for r in repairs)
        by_case = Counter(r["case"] for r in repairs)
        explicit = [r for r in rows if r.get("arm") == "explicit"]
        conditions = {
            "engineeringPhase": small_manifest.get("protocolVersion")
            == "direct-workflow-v1",
            "sixTerminalRows": len(rows) == 6
            and set(cells) == wanted
            and all(n == 1 for n in cells.values()),
            "validFinalAudit": observed["valid"]
            and observed["mode"] == "final"
            and observed["auditedTrials"] == observed["expectedTrials"] == 6,
            "cleanScopeAndReceipts": len(rows) == 6
            and all(
                r.get("scopePassed") is True
                and r.get("processCleanup", {}).get("cleanupComplete") is True
                and r.get("usageComplete") is True
                and r.get("unknownUsageCalls") == 0
                and not r.get("ledgerDamaged")
                and not r.get("recoveryEvidenceErrors")
                for r in rows
            ),
            "atLeastFourRealRepairs": len(repairs) >= 4,
            "eachArmAtLeastTwo": all(by_arm[a] >= 2 for a in ("single", "explicit")),
            "eachCaseAtLeastOne": all(by_case[c] >= 1 for c in ENGINEERING_CASES),
            "allExplicitExecutorDelivered": len(explicit) == 3
            and all(
                r.get("collaborationProtocolSatisfied") is True
                and r.get("implementationLeafRuns", 0) >= 1
                for r in explicit
            ),
        }
        computed = {
            "schemaVersion": 1,
            "passed": all(conditions.values()),
            "conditions": conditions,
            "manifestSha256": legacy.digest((directory / "manifest.json").read_bytes()),
            "resultsSha256": legacy.digest((directory / "results.json").read_bytes()),
            "auditSha256": legacy.digest(
                json.dumps(observed, sort_keys=True, ensure_ascii=False).encode()
            ),
            "realRepairCount": len(repairs),
            "repairsByArm": dict(by_arm),
            "repairsByCase": dict(by_case),
        }
        for key, expected in computed.items():
            self.equal(prior.get(key), expected, "engineering_gate_" + key)
        self.check(
            computed["passed"],
            "engineering_gate_failed",
            json.dumps(conditions, sort_keys=True),
        )
        for key in (
            "productionSha256",
            "harnessSha256",
            "routeSha256",
            "resolvedTransportRouteSha256",
            "model",
            "temperature",
            "roleTurnLimits",
            "roleTurnPolicy",
            "judgementPolicy",
            "protocolVersion",
            "schemaVersion",
            "limits",
            "price",
            "sourceTaskSha256",
            "sourceManifestSha256",
        ):
            self.equal(
                manifest.get(key), small_manifest.get(key), "engineering_formal_" + key
            )

    def collaboration(self, root, row, records, manifest):
        path = root / "collaboration.json"
        evidence = (
            legacy.read_json(path)
            if path.exists()
            else {"runs": [], "deliveredIds": []}
        )
        transcript = root / "transcript.json"
        messages = legacy.read_json(transcript) if transcript.exists() else []
        # Legacy validates raw identities, receipt hashes, timestamps, transport
        # attribution and coordinator tool permissions. Its Plan-first scoring is
        # supplied only to that internal comparison; the original row is untouched.
        old_counts = legacy.independent_collaboration(
            evidence["runs"], records, evidence.get("deliveredIds", []), messages, True
        )
        super().collaboration(
            root,
            {**row, **old_counts},
            records,
            {
                **manifest,
                "explicitRecipe": "Coordinator-only parent (task/todo_write): direct workflow",
            },
        )
        by_id = {r["subagentId"]: r for r in evidence["runs"]}
        for request in records:
            actor = request.get("subagentId")
            if actor and actor in by_id:
                self.equal(
                    request.get("actorId"), actor, "leaf_request_actor", row["id"]
                )
                self.equal(
                    request.get("agentType"),
                    by_id[actor].get("agentType"),
                    "leaf_request_agent_type",
                    row["id"],
                )
                self.equal(
                    str(request.get("role", "")).lower(),
                    str(by_id[actor].get("role", "")).lower(),
                    "leaf_request_role",
                    row["id"],
                )
        delivered = set(evidence.get("deliveredIds", []))
        for actor in delivered:
            self.check(
                owner_delivered(by_id.get(actor, {}), delivered),
                "direct_delivery_without_owner_ack",
                str(actor),
                row["id"],
            )
        computed = direct_collaboration(evidence["runs"], records, list(delivered))
        for key, expected in computed.items():
            self.equal(row.get(key), expected, "collaboration_" + key, row["id"])
        return computed

    def regression_judgement(self, row, manifest):
        trial = row["id"]
        root = self.directory / "private" / trial / "judgement"
        if row.get("judgement") is None:
            self.equal(
                row.get("regressionTestPassed"), False, "regression_not_run", trial
            )
            self.equal(row.get("success"), False, "unjudged_success", trial)
            return False
        judgement = legacy.read_json(root / "result.json")
        self.equal(row.get("judgement"), judgement, "judgement_copy", trial)
        self.equal(judgement.get("limitSeconds"), 120, "judge_limit", trial)
        self.equal(
            judgement.get("policyVersion"),
            manifest["judgementPolicy"]["version"],
            "judge_policy",
            trial,
        )
        duration = judgement.get("durationSeconds")
        self.check(
            isinstance(duration, (int, float))
            and math.isfinite(duration)
            and duration >= 0,
            "judge_duration",
            str(duration),
            trial,
        )
        self.equal(
            judgement.get("deadlineExceeded"),
            duration >= 120,
            "judge_deadline_flag",
            trial,
        )
        stage_passes = {}
        for kind, field in (("oracle", "oracle"), ("regression", "regressionTest")):
            stage = judgement[field]
            self.equal(
                stage,
                legacy.read_json(root / kind / "result.json"),
                "judge_stage_copy_" + kind,
                trial,
            )
            cleanup = stage.get("cleanup")
            if cleanup is not None:
                self.equal(
                    cleanup,
                    legacy.read_json(root / kind / "cleanup.json"),
                    "judge_cleanup_copy_" + kind,
                    trial,
                )
                self.check(
                    cleanup.get("cleanupComplete") is True,
                    "judge_cleanup_incomplete",
                    kind,
                    trial,
                )
                for key in (
                    "survivorPids",
                    "evidenceErrors",
                    "processScanErrors",
                    "unconfirmedPids",
                    "identityMismatches",
                ):
                    self.equal(
                        cleanup.get(key, []),
                        [],
                        "judge_cleanup_" + kind + "_" + key,
                        trial,
                    )
            receipt_path = root / kind / "receipt.json"
            receipt_ok = False
            if receipt_path.exists():
                receipt = legacy.read_json(receipt_path)
                spec = legacy.read_json(root / kind / "stage.json")
                self.equal(
                    stage.get("receiptSha256"),
                    legacy.digest(receipt_path.read_bytes()),
                    "judge_receipt_hash_" + kind,
                    trial,
                )
                for key in ("nonce", "kind", "workspace", "testSha256"):
                    self.equal(
                        receipt.get(key),
                        spec.get(key),
                        "judge_receipt_" + kind + "_" + key,
                        trial,
                    )
                receipt_ok = (
                    receipt.get("normalReturn") is True
                    and receipt.get("exitCode") == 0
                    and receipt.get("origins", {}).get("foreignModules") == []
                )
                if kind == "regression":
                    counts = {
                        k: 0
                        for k in (
                            "collected",
                            "passed",
                            "failed",
                            "errors",
                            "skipped",
                            "xfailed",
                        )
                    }
                    counts["errors"] = receipt.get("collectionErrors", 0)
                    counts["skipped"] = receipt.get("collectionSkips", 0)
                    items = receipt.get("items", [])
                    counts["collected"] = len(items)
                    complete = len({i["nodeId"] for i in items}) == len(items)
                    for item in items:
                        phases = item["phases"]
                        complete &= set(phases) == {"setup", "call", "teardown"}
                        xfail = any(v["xfail"] for v in phases.values())
                        counts["xfailed"] += int(xfail)
                        counts["failed"] += int(
                            phases.get("call", {}).get("outcome") == "failed"
                        )
                        counts["errors"] += sum(
                            v["outcome"] == "failed"
                            for k, v in phases.items()
                            if k != "call"
                        )
                        counts["skipped"] += int(
                            any(v["outcome"] == "skipped" for v in phases.values())
                            and not xfail
                        )
                        counts["passed"] += int(
                            set(phases) == {"setup", "call", "teardown"}
                            and all(v["outcome"] == "passed" for v in phases.values())
                            and not xfail
                        )
                    self.equal(
                        receipt.get("counts"),
                        counts,
                        "regression_receipt_counts",
                        trial,
                    )
                    self.equal(
                        stage.get("counts"), counts, "regression_stage_counts", trial
                    )
                    self.equal(
                        receipt.get("allPhasesComplete"),
                        complete,
                        "regression_phase_completeness",
                        trial,
                    )
                    receipt_ok = (
                        receipt_ok
                        and complete
                        and receipt.get("sessionFinished") is True
                        and receipt.get("sessionExitStatus") == 0
                        and counts["passed"] == counts["collected"] >= 1
                        and all(
                            counts[k] == 0
                            for k in ("failed", "errors", "skipped", "xfailed")
                        )
                    )
            else:
                self.equal(
                    stage.get("receiptSha256"),
                    None,
                    "judge_missing_receipt_hash_" + kind,
                    trial,
                )
            passed = bool(
                receipt_ok
                and stage.get("exitCode") == 0
                and cleanup
                and cleanup.get("cleanupComplete") is True
                and stage.get("finishedAtSeconds", 120) < 120
            )
            if kind == "regression" and judgement.get("deadlineExceeded"):
                passed = False
            self.equal(stage.get("passed"), passed, "judge_pass_" + kind, trial)
            if passed:
                self.equal(stage.get("status"), "passed", "judge_status_" + kind, trial)
            stage_passes[kind] = passed
        self.equal(
            row.get("oracleSuccess"),
            stage_passes["oracle"],
            "oracle_host_completion",
            trial,
        )
        self.equal(
            row.get("regressionTest"),
            judgement["regressionTest"],
            "regression_copy",
            trial,
        )
        self.equal(
            row.get("regressionTestPassed"),
            stage_passes["regression"],
            "regression_pass",
            trial,
        )
        self.equal(
            judgement.get("cleanupComplete"),
            all(
                s.get("cleanup") is None or s["cleanup"].get("cleanupComplete") is True
                for s in (judgement["oracle"], judgement["regressionTest"])
            ),
            "judge_combined_cleanup",
            trial,
        )
        return stage_passes["regression"]

    def trial(self, row, case, manifest):
        if manifest.get("schemaVersion") == 5:
            regression_passed = self.regression_judgement(row, manifest)
            old_success = bool(
                row.get("oracleSuccess")
                and row.get("scopePassed")
                and (
                    row["arm"] != "explicit"
                    or row.get("collaborationProtocolSatisfied")
                )
            )
            self.equal(
                row.get("success"),
                old_success and regression_passed,
                "common_success_formula",
                row["id"],
            )
            super().trial({**row, "success": old_success}, case, manifest)
            self.trials[-1]["success"] = row["success"]
            if not regression_passed:
                self.trials[-1]["diagnostics"]["terminalOutcomes"].append(
                    "regression_tests_failed"
                )
        else:
            super().trial(row, case, manifest)
        expected = sorted(
            p
            for p in row.get("changedFiles", [])
            if p in case["authorized_paths"]
            and p.startswith("minicode/")
            and p.endswith(".py")
            and p not in row.get("verificationInducedChanges", [])
        )
        self.equal(
            row.get("changedSourceFiles"), expected, "changed_source_files", row["id"]
        )

    def design(self, manifest, cases):
        self.check(
            manifest.get("schemaVersion") in (3, 4, 5),
            "schema_version",
            "Expected schema 3, 4 or 5",
        )
        self.equal(
            manifest.get("protocolVersion"),
            "direct-workflow-regression-v1"
            if manifest.get("schemaVersion") == 5
            else "direct-workflow-v1",
            "protocol_version",
        )
        self.equal(
            manifest.get("runner"),
            "scripts/run_implementation_progress_live.py",
            "runner_entrypoint",
        )
        recipe = str(manifest.get("explicitRecipe", "")).lower()
        self.check(
            all(
                s in recipe
                for s in ("coordinator-only", "task/todo_write", "direct workflow")
            ),
            "explicit_recipe",
            "Expected coordinator-only direct-workflow recipe",
        )
        phase = manifest.get("phase")
        self.check(phase in {"engineering", "formal"}, "phase", str(phase))
        arms = ["single", "explicit"] if phase == "engineering" else list(legacy.ARMS)
        self.equal(manifest.get("arms"), arms, "design_arms")
        self.equal(
            manifest.get("repeats"),
            1 if phase == "engineering" else 2,
            "design_repeats",
        )
        self.equal(len(cases), 3 if phase == "engineering" else 8, "design_task_count")
        return arms

    def run(self):
        expected_count = None
        manifest = {}
        try:
            manifest_path = self.directory / "manifest.json"
            manifest = legacy.read_json(manifest_path)
            self.archived_hashes(manifest)
            task_path = self.directory / "tasks.json"
            self.equal(
                legacy.digest(task_path.read_bytes()),
                manifest["taskSha256"],
                "tasks_hash",
            )
            tasks = legacy.read_json(task_path)
            case_list = tasks["cases"]
            cases = {c["id"]: c for c in case_list}
            self.equal(len(cases), len(case_list), "duplicate_task")
            arms = self.design(manifest, cases)
            self.provenance(manifest, tasks)
            self.engineering_gate(manifest)
            schedule = manifest["schedule"]
            assignments = {r["id"]: r for r in schedule}
            self.equal(len(assignments), len(schedule), "duplicate_schedule")
            for identity in assignments:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identity):
                    raise ValueError("Unsafe trial identity")
            cells = Counter((r["case"], r["repeat"], r["arm"]) for r in schedule)
            expected = set(
                itertools.product(cases, range(1, manifest["repeats"] + 1), arms)
            )
            self.check(
                set(cells) == expected and all(n == 1 for n in cells.values()),
                "unbalanced_schedule",
                "Expected complete phase-specific paired schedule",
            )
            expected_count = len(schedule)
            result_path = self.directory / "results.json"
            result = (
                legacy.read_json(result_path)
                if result_path.exists()
                else {"trials": []}
            )
            if result_path.exists():
                self.equal(
                    result.get("manifestSha256"),
                    legacy.digest(manifest_path.read_bytes()),
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
            self.check(
                set(ids) == set(assignments),
                "incomplete_results",
                f"{len(rows)}/{len(schedule)} terminal rows",
                severity="warning" if self.allow_partial else "error",
            )
            self.equal(
                {p.stem for p in (self.directory / "trials").glob("*.json")},
                set(ids),
                "trial_file_coverage",
            )
            for row in rows:
                trial = row["id"]
                try:
                    assignment = assignments[trial]
                    for key in ("id", "case", "repeat", "arm"):
                        self.equal(
                            row.get(key), assignment[key], "assignment_" + key, trial
                        )
                    self.equal(
                        legacy.read_json(self.directory / "trials" / (trial + ".json")),
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
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            self.issue(
                "experiment_evidence_unreadable",
                type(error).__name__ + ": " + str(error),
            )
        # The gate hashes this summary across processes. Legacy delivery checks
        # visit a set, so evidence-equivalent warning order must be canonical.
        self.issues.sort(
            key=lambda i: (i["severity"], i["code"], i.get("trial") or "", i["detail"])
        )
        return {
            "valid": not any(i["severity"] == "error" for i in self.issues),
            "mode": "partial" if self.allow_partial else "final",
            "expectedTrials": expected_count,
            "auditedTrials": len(self.trials),
            "deliveryEvidenceMode": "task_result_owner_ack",
            "protocolVersion": manifest.get("protocolVersion"),
            "issues": self.issues,
            "trials": self.trials,
            "limits": "Only surviving evidence can be audited. No archived code, model request or oracle was executed. Missing usage remains unknown; failed task/protocol outcomes remain valid observations.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    summary = Audit(args.input, allow_partial=args.allow_partial).run()
    legacy.write_report(args.input, summary)
    report = args.input / "audit-report.md"
    report.write_text(
        report.read_text().replace(
            "# 三组评测独立数据审计", "# 实现进度评测独立数据审计", 1
        ),
        encoding="utf-8",
    )
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
