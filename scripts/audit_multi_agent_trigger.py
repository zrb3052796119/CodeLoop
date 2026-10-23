#!/usr/bin/env python3
"""Independent eight-cell adoption audit; never import or execute a runner."""
from __future__ import annotations

import argparse
import ast
import json
import math
from pathlib import Path
import random
import re
import stat
import zipfile

try:
    from scripts import audit_multi_agent_gen2 as base
except ModuleNotFoundError:
    import audit_multi_agent_gen2 as base

prior, legacy = base.prior, base.legacy
canonical_digest, safe_path, sha256 = base.canonical_digest, base.safe_path, base.sha256
CASES = (
    "trigger-record-pagination", "trigger-import-retry", "trigger-cart-discounts",
    "trigger-stock-allocation", "trigger-report-timezones", "trigger-config-overlay",
    "trigger-limit-clamp", "trigger-extension-case",
)
NATIVE_RESULT_TOOLS = frozenset({
    "completion_review_submit", "completion_probe_migration_submit",
    "completion_probe_adjudication_submit", "completion_coverage_audit_submit",
    "completion_coverage_inspection_submit",
})
NEGATIVE_CASES = CASES[-2:]
EXPECTATIONS = {case: "negative" if case in NEGATIVE_CASES else "positive" for case in CASES}
PROTOCOL = "multi-agent-trigger-v1"
GATE_POLICY = {
    "expectedTrials": 8, "opportunityTrials": 6, "negativeTrials": 2,
    "triggerRateDenominator": 8, "minimumDeliveredTriggerTrials": 2,
    "maximumNegativeLaunchRequests": 0, "requireAllTerminal": True,
    "requireIndependentAudit": True, "requireNegativeLaunchClassificationComplete": True,
}
GUIDANCE_POLICY = {
    "version": "complete-parent-provider-body-before-send-ledger-call-v1",
    "bodyHash": "sha256-json-sort_keys-compact-utf8",
    "static": "actual-parent-request-system-prompt-and-task-schema",
    "dynamic": "actual-parent-request-message-containing-complete-host-delegation-sentence",
    "choice": "same-physical-ledger-call-response-and-toolCalls",
    "missing": "unknown-not-absent",
}
LIMITS = {
    "maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5", "wallSeconds": 600,
    "maxOutputTokens": 8192, "requestTimeoutSeconds": 300,
    "idleTimeoutSeconds": 120, "connectTimeoutSeconds": 30,
}


class Audit(base.Audit):
    def design(self, manifest, cases):
        for key, expected in {
            "schemaVersion": 1, "protocolVersion": PROTOCOL, "phase": "trigger-gen2",
            "runner": "scripts/run_multi_agent_trigger.py", "arms": ["auto"], "repeats": 1,
            "model": "glm-5.3-flash", "temperature": 0, "concurrency": 1,
            "scheduleSeed": 20261023, "limits": LIMITS, "gatePolicy": GATE_POLICY,
            "triggerExpectations": EXPECTATIONS, "guidanceEvidencePolicy": GUIDANCE_POLICY,
            "implementationPhasePolicy": "production-continuous-default",
            "requestEvidencePolicy": GUIDANCE_POLICY["version"],
        }.items():
            self.equal(manifest.get(key), expected, "design_" + key)
        self.equal(list(cases), list(CASES), "design_cases")
        schedule = [{"case": case, "arm": "auto", "repeat": 1, "id": f"{case}-r1-auto"} for case in CASES]
        random.Random(20261023).shuffle(schedule)
        self.equal(manifest.get("schedule"), schedule, "frozen_schedule_order")
        self.check(not manifest.get("explicitRecipe"), "unexpected_explicit_recipe", "Auto must choose its own tools")
        return ["auto"]

    def engineering_gate(self, manifest):
        self.equal(manifest.get("engineeringGate"), None, "trigger_no_historical_gate")
        self.equal(manifest.get("reusedCalibrationCases", []), [], "trigger_no_reused_cases")

    def archived_hashes(self, manifest):
        registration = manifest.get("registrationFiles", {})
        required_materials = {"tasks.json", "analysis-plan.md", "execution-host.zip", "execution-host-inventory.json",
                              "environment.json", "public-preflight/receipt.json",
                              "materials/source-manifest.json", "materials/offline-proof.json", "materials/reference-fixes.json"}
        self.check(isinstance(registration, dict) and required_materials <= set(registration),
                   "registration_missing_materials", str(sorted(required_materials - set(registration))))
        for name, expected_hash in registration.items():
            path = self.directory / safe_path(name)
            regular = not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(self.directory.resolve())
            self.check(regular, "registration_nonregular", name)
            if not regular:
                continue
            self.equal(legacy.digest(path.read_bytes()), expected_hash, "registration_hash_" + name)
        archive = self.directory / "execution-host.zip"
        inventory_path = self.directory / "execution-host-inventory.json"
        self.equal(legacy.digest(archive.read_bytes()), manifest.get("executionHostArchiveSha256"), "archive_hash")
        self.equal(legacy.digest(inventory_path.read_bytes()), manifest.get("executionHostInventorySha256"), "host_inventory_hash")
        inventory = legacy.read_json(inventory_path)
        self.equal(inventory.get("schemaVersion"), 1, "host_inventory_schema")
        records = inventory["files"]
        self.check(isinstance(records, list) and bool(records), "empty_host_inventory", "Host inventory must be nonempty")
        expected_paths = [safe_path(item["path"]) for item in records]
        self.equal(expected_paths, sorted(set(expected_paths)), "host_inventory_order_unique")
        self.equal(inventory.get("treeSha256"), canonical_digest(records), "host_inventory_tree_hash")
        self.host_tree_sha = canonical_digest(records)
        self.equal(manifest.get("executionHostTreeSha256"), self.host_tree_sha, "manifest_host_tree_hash")
        self.host_inventory = {item["path"]: item for item in records}
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            names = [safe_path(info.filename) for info in infos]
            self.equal(len(names), len(set(names)), "duplicate_archive_entries")
            self.equal(sorted(names), expected_paths, "host_archive_coverage")
            actual = []
            for info in sorted(infos, key=lambda item: item.filename):
                self.check(not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16),
                           "host_archive_nonregular", info.filename)
                content = bundle.read(info)
                actual.append({"path": info.filename, "size": len(content), "sha256": legacy.digest(content)})
            self.equal(actual, records, "host_archive_inventory")
            self.guidance = archived_guidance(bundle)
            source = bundle.read("scripts/run_multi_agent_trigger.py")
            self.equal(prior.literal_object(source, "PROTOCOL_VERSION"), PROTOCOL, "frozen_protocol")
            self.equal(list(prior.literal_object(source, "CASES")), list(CASES), "frozen_cases")
            self.equal(prior.literal_object(source, "GATE_POLICY"), GATE_POLICY, "frozen_gate_policy")
            self.equal(prior.literal_object(source, "REQUEST_EVIDENCE_POLICY"), GUIDANCE_POLICY["version"], "frozen_request_evidence_policy")
            for literal, field in (("LIMITS", "limits"), ("MODEL", "model"), ("SEED", "scheduleSeed"),
                                   ("ROLE_TURN_LIMITS", "roleTurnLimits")):
                self.equal(manifest.get(field), prior.literal_object(source, literal), "frozen_" + field)
            harness = prior.literal_names(source, "HARNESS_FILES")
            self.equal(len(harness), len(set(harness)), "duplicate_harness_files")
            self.equal(manifest.get("harnessFiles"), harness, "harness_file_list")
            required = {"__init__.py", "run_multi_agent_trigger.py", "audit_multi_agent_trigger.py",
                        "run_implementation_progress_live.py", "audit_implementation_progress.py",
                        "run_three_arm_real.py", "audit_three_arm_real.py", "implementation_judge.py",
                        "three_arm_protocol.py", "evaluation_transport.py", "analyze_multi_agent_trigger.py",
                        "prepare_multi_agent_trigger_tasks.py"}
            self.check(required <= set(harness), "missing_harness_dependencies", str(sorted(required - set(harness))))
            self.equal(legacy.digest(b"".join(name.encode() + b"\0" + bundle.read("scripts/" + name) for name in harness)),
                       manifest.get("harnessSha256"), "harness_hash")
            production = sorted(name for name in names if name.startswith("minicode/") and name.endswith(".py"))
            self.check(bool(production), "empty_production_archive", "No production Python sources")
            self.equal(legacy.digest(b"".join(name.encode() + b"\0" + bundle.read(name) for name in production)),
                       manifest.get("productionSha256"), "production_hash")
            policy = prior.literal_object(bundle.read("scripts/implementation_judge.py"), "JUDGE_POLICY")
            self.equal(manifest.get("judgementPolicy"), {**policy, "agentBudgetSeconds": 600}, "frozen_judgement_policy")
            self.equal(policy.get("totalSeconds"), 120, "judge_total_limit")
            self.equal(policy.get("regressionPath"), "tests/test_eval_regression.py", "judge_regression_path")
            self.equal(manifest.get("roleTurnLimits"), {role: 0 for role in ("plan", "explore", "general")},
                       "role_turn_limits")
            self.owner_ack = b"task_result_owner_ack" in bundle.read("scripts/three_arm_protocol.py")
            self.rejection_protocol = b"admission_rejections" in bundle.read("scripts/evaluation_transport.py")
            self.check(self.owner_ack, "missing_owner_ack_protocol", "Durable task delivery is required")
            self.check(self.rejection_protocol, "missing_admission_evidence", "Pre-HTTP refusals must remain auditable")
            self.public_fixture_preflight(manifest, bundle, "shared-public-checks-source-context-and-fixtures-v2")
        self.equal(legacy.digest((self.directory / "analysis-plan.md").read_bytes()), manifest.get("analysisPlanSha256"), "analysis_plan_hash")
        self.equal(legacy.digest((self.directory / "environment.json").read_bytes()), manifest.get("environmentSha256"), "environment_hash")
        host = self.directory / "private/execution-host"
        if host.exists():
            actual_files = []
            for path in sorted(host.rglob("*")):
                self.check(not path.is_symlink(), "retained_host_symlink", str(path))
                if path.is_symlink():
                    continue
                if path.is_file():
                    data = path.read_bytes()
                    actual_files.append({"path": path.relative_to(host).as_posix(), "size": len(data), "sha256": legacy.digest(data)})
            self.equal(actual_files, records, "retained_execution_host_inventory")

    def execution_host(self, row, manifest):
        identity = row["id"]
        path = self.directory / "private" / identity / "execution-host.json"
        receipt = legacy.read_json(path)
        self.equal(receipt.get("schemaVersion"), 1, "execution_host_schema", identity)
        self.equal(receipt.get("trialId"), identity, "execution_host_trial", identity)
        self.equal(receipt.get("manifestSha256"), legacy.digest((self.directory / "manifest.json").read_bytes()), "execution_host_manifest", identity)
        for key in ("executionHostInventorySha256", "executionHostArchiveSha256", "productionSha256", "harnessSha256"):
            self.equal(receipt.get(key), manifest.get(key), "execution_host_" + key, identity)
        self.equal(receipt.get("executionHostTreeSha256"), self.host_tree_sha, "execution_host_tree", identity)
        recorded = receipt.get("recordedAtEpoch")
        self.check(type(recorded) in (int, float) and math.isfinite(recorded) and recorded > 0,
                   "execution_host_timestamp", str(recorded), identity)
        host = Path(receipt.get("hostRoot", ""))
        self.equal(receipt.get("entrypoint"), str(host / "scripts/run_multi_agent_trigger.py"), "execution_host_entrypoint", identity)
        self.equal(host, (self.directory / "private/execution-host").resolve(), "execution_host_retained_root", identity)
        self.check(isinstance(receipt.get("hostRoot"), str) and Path(receipt["hostRoot"]).is_absolute(), "execution_host_root", str(receipt.get("hostRoot")), identity)
        self.check(isinstance(receipt.get("preregisteredCommit"), str) and re.fullmatch(r"[0-9a-f]{40}", receipt["preregisteredCommit"]) is not None,
                   "execution_host_registration", "Missing committed preregistration identity", identity)
        modules = receipt.get("loadedModules", {})
        self.check(isinstance(modules, dict) and bool(modules), "execution_host_modules_missing", identity, identity)
        if not isinstance(modules, dict):
            raise ValueError("invalid loaded-module receipt")
        for name, module in modules.items():
            self.check(name in ("minicode", "scripts") or name.startswith(("minicode.", "scripts.")),
                       "execution_host_module_name", name, identity)
            location = Path(module["path"])
            try:
                relative = location.relative_to(host).as_posix()
            except ValueError:
                self.issue("execution_host_foreign_module", str(location), identity)
                continue
            expected = self.host_inventory.get(relative)
            self.check(expected is not None, "execution_host_unknown_module", relative, identity)
            if expected is not None:
                self.equal(module.get("sha256"), expected["sha256"], "execution_host_module_hash", identity)
        self.committed_registration(receipt, manifest, identity)
        start_path = path.parent / "execution-start.json"
        if start_path.exists():
            start = legacy.read_json(start_path)
            recorded = receipt.get("recordedAtEpoch")
            self.check(type(recorded) in (int, float) and recorded <= start["epoch"], "execution_host_receipt_late", str(recorded), identity)

    def provenance(self, manifest, tasks):
        source = legacy.read_json(self.directory / 'materials/source-manifest.json')
        proof_path = self.directory / 'materials/offline-proof.json'
        fixes_path = self.directory / 'materials/reference-fixes.json'
        proof, fixes = legacy.read_json(proof_path), legacy.read_json(fixes_path)
        for key, value in {'schemaVersion': 1, 'generation': 'trigger-v1', 'origin': 'synthetic', 'modelCalls': 0}.items():
            self.equal(source.get(key), value, 'synthetic_' + key)
        self.equal(tasks.get('generation'), 'trigger-v1', 'task_generation')
        self.equal(fixes.get('generation'), 'trigger-v1', 'reference_generation')
        self.equal(source.get('taskSha256'), manifest.get('taskSha256'), 'source_task_hash')
        self.equal(source.get('offlineProofSha256'), legacy.digest(proof_path.read_bytes()), 'offline_proof_hash')
        self.equal(source.get('referenceFixesSha256'), legacy.digest(fixes_path.read_bytes()), 'reference_fixes_hash')
        shared = tasks['shared_files']
        self.equal(source.get('sharedFiles'), {safe_path(n): legacy.digest(s.encode()) for n, s in shared.items()}, 'shared_file_hashes')
        self.equal(manifest.get('publicBaselineHashes'), {
            c['id']: {safe_path(n): legacy.digest(s.encode()) for n, s in sorted({**shared, **c['files']}.items())}
            for c in tasks['cases']}, 'public_baseline_hashes')
        self.equal(manifest.get('oracleHashes'), {c['id']: legacy.digest(c['oracle_source'].encode()) for c in tasks['cases']}, 'oracle_hashes')
        for name, rows, key in [('sources', source['cases'], 'id'), ('references', fixes['cases'], 'id'), ('proofs', proof, 'case')]:
            self.equal([r[key] for r in rows], list(CASES), name + '_coverage')
        for case, origin, reference, evidence in zip(tasks['cases'], source['cases'], fixes['cases'], proof):
            identity = case['id']
            self.equal(case.get('triggerExpectation'), EXPECTATIONS[identity], 'task_expectation', identity)
            self.equal(origin.get('triggerExpectation'), EXPECTATIONS[identity], 'source_expectation', identity)
            provenance = case['provenance']
            self.equal(provenance.get('origin'), 'synthetic', 'case_origin', identity)
            self.check(not any(k in provenance for k in ('baselineCommit', 'referenceCommit')), 'synthetic_historical_claim', identity, identity)
            paths = [safe_path(p) for p in case['sourcePaths']]
            self.check(bool(paths) and len(paths) == len(set(paths)) and all(p.startswith('minicode/') and p.endswith('.py') for p in paths), 'source_paths', str(paths), identity)
            self.equal(set(case['authorized_paths']), set(paths) | {'tests/test_eval_regression.py'}, 'case_write_scope', identity)
            self.equal(set(case['files']), set(paths) | {'tests/test_public.py'}, 'public_case_files', identity)
            self.equal(set(reference['files']), set(paths), 'reference_scope', identity)
            self.check('tests/test_eval_regression.py' not in shared, 'baseline_regression_present', identity, identity)
            self.equal(origin['sourceBlobs'], {p: {'baselineSha256': legacy.digest(case['files'][p].encode()), 'referenceSha256': legacy.digest(reference['files'][p].encode())} for p in paths}, 'source_blob_hashes', identity)
            for key, value in [('publicTestSha256', legacy.digest(case['files']['tests/test_public.py'].encode())), ('oracleSha256', legacy.digest(case['oracle_source'].encode()))]:
                self.equal(origin.get(key), value, 'source_' + key, identity)
                self.equal(evidence.get(key), value, 'proof_' + key, identity)
            for name in ('baselinePublic', 'referencePublic'):
                self.equal(evidence[name].get('returncode'), 0, name, identity)
            baseline = checked_oracle(evidence['baselineOracle'], expected_pass=False)
            fixed = checked_oracle(evidence['referenceOracle'], expected_pass=True)
            self.check(baseline is not None, 'target_baseline_not_proven', identity, identity)
            self.check(fixed is not None, 'reference_not_proven', identity, identity)
            if baseline is not None and fixed is not None:
                self.equal([(c['label'], c['expected']) for c in baseline], [(c['label'], c['expected']) for c in fixed], 'oracle_check_contract_changed', identity)

    def collaboration(self, root, row, records, manifest):
        identity = row['id']
        path = root / 'collaboration.json'
        evidence = legacy.read_json(path) if path.exists() else {'runs': [], 'deliveredIds': []}
        runs, delivered = evidence['runs'], set(evidence.get('deliveredIds', []))
        by_id = {r['subagentId']: r for r in runs}
        self.equal(len(by_id), len(runs), 'duplicate_subagent_identity', identity)
        terminal_acks = evidence.get('asyncTerminalAcks', [])
        ack_by_id = {ack['subagentId']: ack for ack in terminal_acks}
        self.equal(len(ack_by_id), len(terminal_acks), 'duplicate_terminal_ack_identity', identity)
        for actor, ack in ack_by_id.items():
            self.check(actor not in by_id and queued_cancel_ack_valid(ack, delivered, records),
                       'invalid_queued_cancel_ack', str(actor), identity)
        normalized = []
        for run in runs:
            actor = run['subagentId']
            self.equal(bool(run.get('isContainer')), run.get('agentType') == 'workflow', 'container_role', identity)
            if run.get('returned'):
                self.check(run.get('returnedEpoch', -math.inf) >= run.get('startedEpoch', math.inf), 'leaf_return_order', actor, identity)
                projected = run.get('projected')
                self.check(projected is None or legacy.valid_projection(projected), 'invalid_host_projection', actor, identity)
                if isinstance(projected, dict):
                    self.equal(projected.get('subagentId'), actor, 'projection_identity', identity)
                    self.equal(projected.get('agentType'), run.get('agentType'), 'projection_agent_type', identity)
            parent = run.get('parentSubagentId')
            self.check(not parent or parent in by_id, 'unbound_parent_identity', str(parent), identity)
            normalized.append(normalize_delivery(run, delivered))
        for actor in delivered:
            acknowledged = (owner_ack_valid(by_id[actor], delivered) if actor in by_id
                            else queued_cancel_ack_valid(ack_by_id.get(actor, {}), delivered, records))
            self.check(acknowledged, 'delivery_without_owner_ack', str(actor), identity)
        for request in records:
            actor = request.get('subagentId')
            if actor:
                self.check(actor in by_id, 'unbound_leaf_request', str(actor), identity)
                if actor in by_id:
                    self.equal(request.get('actorId'), actor, 'leaf_request_actor', identity)
                    self.equal(request.get('agentType'), by_id[actor].get('agentType'), 'leaf_request_agent_type', identity)
                    self.equal(str(request.get('role', '')).lower(), str(by_id[actor].get('role', '')).lower(), 'leaf_request_role', identity)
        for rejection in self.admission_by_trial.get(identity, []):
            actor, sub = rejection.get('actorId'), rejection.get('subagentId')
            bound = (sub in by_id and actor == sub and rejection.get('agentType') == by_id[sub].get('agentType') and str(rejection.get('role')).lower() == str(by_id[sub].get('role')).lower()) if sub else actor == 'main'
            if not bound:
                rejection['auditValid'] = False
                self.issue('unbound_rejection_actor', str(actor), identity)
        computed = prior.direct_collaboration(normalized, records, list(delivered))
        for key, expected in computed.items():
            self.equal(row.get(key), expected, 'collaboration_' + key, identity)
        trigger = trigger_evidence(runs, records, delivered)
        if trigger['deliveredTrigger']:
            self.check(trigger['launchRequests'] > 0, 'delivered_child_without_parent_launch', identity, identity)
        trigger['queuedCancellationAckIds'] = sorted(actor for actor, ack in ack_by_id.items() if actor not in by_id and queued_cancel_ack_valid(ack, delivered, records))
        self._trigger = trigger
        self._guidance = self.parent_requests(root, records, manifest, identity)
        return computed

    def parent_requests(self, root, records, manifest, identity):
        observed, expected_paths = [], set()
        for record in records:
            if record.get('subagentId'):
                continue
            self.equal(record.get('actorId'), 'main', 'parent_request_actor', identity)
            relative = f"parent-requests/{record['call']}.json"
            expected_paths.add(relative)
            self.equal(record.get('parentRequestPath'), relative, 'parent_request_path', identity)
            path = root / relative
            self.check(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'parent_request_nonregular', relative, identity)
            if not path.is_file():
                continue
            data = path.read_bytes()
            capture = json.loads(data)
            self.equal(legacy.digest(data), record.get('parentRequestSha256'), 'parent_request_file_hash', identity)
            self.equal(capture.get('schemaVersion'), 1, 'parent_request_schema', identity)
            self.equal(capture.get('callId'), record['call'], 'parent_request_call', identity)
            self.equal(capture.get('routeSha256'), record.get('routeSha256'), 'parent_request_route', identity)
            for key in ('actorId', 'subagentId', 'role', 'agentType'):
                self.equal(capture.get('actor', {}).get(key), record.get(key), 'parent_request_actor_' + key, identity)
            body = capture['body']
            digest = canonical_digest(body)
            self.equal(capture.get('bodySha256'), digest, 'parent_request_body_hash', identity)
            self.equal(record.get('parentRequestBodySha256'), digest, 'parent_request_ledger_body_hash', identity)
            epoch = capture.get('capturedAtEpoch')
            self.check(type(epoch) in (int, float) and math.isfinite(epoch) and record['startedEpoch'] <= epoch <= record['startedEpoch'] + record.get('seconds', manifest['limits']['wallSeconds']) + 1, 'parent_request_time', str(epoch), identity)
            self.equal(body.get('model'), manifest['model'], 'parent_body_model', identity)
            self.equal(body.get('temperature'), 0, 'parent_body_temperature', identity)
            self.equal(body.get('max_tokens'), record.get('maxOutputTokens'), 'parent_body_output_limit', identity)
            names = [t['function']['name'] for t in body.get('tools', [])]
            self.equal(names, record.get('toolNames', []), 'parent_body_tool_names', identity)
            observed.append(guidance_observation(body, record, self.guidance))
        actual_paths = {p.relative_to(root).as_posix() for p in (root / 'parent-requests').glob('*.json')}
        self.equal(actual_paths, expected_paths, 'parent_request_coverage', identity)
        if observed:
            first = observed[0]
            self.check(first['taskAvailable'] and first['directWriteAvailable'], 'auto_initial_tool_capability', str(first['call']), identity)
            self.check(first['staticSystemGuidePresent'] and first['staticTaskGuidePresent'], 'auto_initial_static_guidance', str(first['call']), identity)
        hints = [r for r in observed if r['dynamicHintPresent']]
        return {'requests': observed, 'dynamicHintObserved': bool(hints), 'firstDynamicHintCall': hints[0]['call'] if hints else None,
                'firstHintResponseTools': hints[0]['responseTools'] if hints else None,
                'launchAtOrAfterFirstHint': any(r['launchRequests'] for r in observed if hints and r['call'] >= hints[0]['call']) if hints else None,
                'interpretation': 'Observed presentation and subsequent choices only; no causal or unexpressed-intent inference.'}

    def trial(self, row, case, manifest):
        super().trial(row, case, manifest)
        self.trials[-1].update(case=row['case'], repeat=row['repeat'], scopePassed=row.get('scopePassed'), triggerEvidence=self._trigger, guidanceEvidence=self._guidance)


def checked_oracle(evidence: dict, *, expected_pass: bool) -> list | None:
    try:
        value = json.loads(evidence['stdout'])
        checks = value['checks']
        if evidence['returncode'] != (0 if expected_pass else 1) or value['passed'] is not expected_pass or not checks:
            return None
        labels = [c['label'] for c in checks]
        if len(labels) != len(set(labels)) or not all(isinstance(label, str) and label for label in labels):
            return None
        if any(type(c['passed']) is not bool or c['passed'] != (c['actual'] == c['expected']) for c in checks):
            return None
        failed = [c['label'] for c in checks if not c['passed']]
        if value['errors'] != failed or bool(failed) == expected_pass:
            return None
        return checks
    except (KeyError, TypeError, ValueError):
        return None


def queued_cancel_ack_valid(ack: dict, delivered: set, records: list[dict]) -> bool:
    """A queued cancellation may be ACKed before any worker observer runs."""
    actor = ack.get('subagentId')
    snapshot, epoch = ack.get('snapshot'), ack.get('ackEpoch')
    return bool(
        actor in delivered and isinstance(actor, str) and actor
        and not any(r.get('subagentId') == actor for r in records)
        and ack.get('ownerActorId') == 'main'
        and type(epoch) in (int, float) and math.isfinite(epoch) and epoch > 0
        and isinstance(snapshot, dict) and snapshot.get('terminal') is True
        and snapshot.get('status') == 'cancelled' and snapshot.get('result') is None
        and snapshot.get('subagentId') == actor
        and snapshot.get('agentType') == ack.get('agentType')
        and ack.get('agentType') in {'explore', 'plan'}
        and ack.get('snapshotSha256') == canonical_digest(snapshot)
    )


def lifecycle_ack_valid(run: dict, delivered: set) -> bool:
    """A cancelled terminal ACK is valid evidence even with no returned result."""
    snapshot = run.get('asyncDeliverySnapshot')
    times = [run.get('startedEpoch'), run.get('deliveredEpoch')]
    if run.get('returnedEpoch') is not None:
        times.insert(1, run['returnedEpoch'])
    if not (run.get('subagentId') in delivered and isinstance(snapshot, dict)
            and all(type(t) in (int, float) and math.isfinite(t) for t in times)
            and times == sorted(times)
            and snapshot.get('terminal') is True and snapshot.get('status') in {'completed', 'failed', 'cancelled'}
            and snapshot.get('subagentId') == run.get('subagentId')
            and snapshot.get('agentType') == run.get('agentType')
            and run.get('asyncDeliveryOwnerActorId') == (run.get('parentSubagentId') or 'main')
            and run.get('asyncDeliverySnapshotSha256') == canonical_digest(snapshot)):
        return False
    result = snapshot.get('result')
    if result is None:
        return snapshot['status'] == 'cancelled'
    return (isinstance(result, dict) and type(result.get('ok')) is bool
            and isinstance(result.get('output'), str) and type(result.get('truncated')) is bool)


def owner_ack_valid(run: dict, delivered: set) -> bool:
    if run.get('deliveryEvidence') == 'async_lifecycle_owner_ack':
        return lifecycle_ack_valid(run, delivered)
    return prior.owner_delivered(run, delivered)


def delivery_valid(run: dict, delivered: set) -> bool:
    """Adoption requires an actual returned result, beyond a terminal status ACK."""
    normalized = dict(run)
    if run.get('deliveryEvidence') == 'async_lifecycle_owner_ack':
        if not lifecycle_ack_valid(run, delivered):
            return False
        result = run['asyncDeliverySnapshot'].get('result')
        if not isinstance(result, dict) or result['ok'] != run.get('toolOk'):
            return False
        if not result['truncated'] and legacy.digest(result['output'].encode()) != run.get('resultSha256'):
            return False
        normalized['deliveryEvidence'] = 'task_result_owner_ack'
    return prior.owner_delivered(normalized, delivered)


def normalize_delivery(run: dict, delivered: set) -> dict:
    result = dict(run)
    if delivery_valid(run, delivered):
        result['deliveryEvidence'] = 'task_result_owner_ack'
    return result


def native_request(record: dict) -> bool:
    names = set(record.get('toolNames') or [])
    return 'task' not in names and bool(NATIVE_RESULT_TOOLS & names)


def classify_task_requests(records: list[dict]) -> dict:
    launches, controls, ambiguous, unresolved, native_calls = [], [], [], [], []
    for record in records:
        if record.get('subagentId'):
            continue
        if native_request(record):
            native_calls.append(record.get('call'))
            continue
        if record.get('event') != 'completed':
            unresolved.append(record.get('call'))
        calls = record.get('toolCalls', [])
        if not isinstance(calls, list):
            unresolved.append(record.get('call'))
            continue
        for call in calls:
            if not isinstance(call, dict):
                unresolved.append(record.get('call'))
                continue
            if call.get('name') != 'task':
                continue
            args = call.get('arguments')
            entry = {'call': record.get('call'), 'toolCallId': call.get('id'), 'arguments': args}
            action = args.get('action', 'run') if isinstance(args, dict) else None
            if isinstance(action, str) and action in {'run', 'spawn'}:
                launches.append(entry)
            elif isinstance(action, str) and action in {'poll', 'cancel'} and isinstance(args.get('subagent_id'), str) and args['subagent_id'].strip() and set(args) <= {'action', 'subagent_id', 'wait_seconds'}:
                controls.append(entry)
            else:
                ambiguous.append(entry)
    return {'launchRequests': len(launches), 'launches': launches, 'controlRequests': len(controls),
            'ambiguousTaskRequests': len(ambiguous), 'ambiguous': ambiguous, 'unresolvedParentCalls': sorted(set(unresolved)),
            'launchClassificationComplete': not ambiguous and not unresolved, 'nativeReviewCalls': native_calls}


def trigger_evidence(runs: list[dict], records: list[dict], delivered: set) -> dict:
    classified = classify_task_requests(records)
    responded = {r.get('subagentId') for r in records if r.get('event') == 'completed'}
    workflows = {r['subagentId']: r for r in runs if r.get('agentType') == 'workflow' and r.get('isContainer') and not r.get('parentSubagentId') and delivery_valid(r, delivered)}
    actual, accepted = [], []
    for run in runs:
        if run.get('isContainer') or run.get('subagentId') not in responded:
            continue
        actual.append(run['subagentId'])
        parent = run.get('parentSubagentId')
        workflow = workflows.get(parent)
        via_workflow = bool(workflow and run.get('returned') is True and workflow['startedEpoch'] <= run.get('startedEpoch', -math.inf) <= run.get('returnedEpoch', math.inf) <= workflow['returnedEpoch'])
        if (not parent and delivery_valid(run, delivered)) or via_workflow:
            accepted.append(run['subagentId'])
    return {**classified, 'actualChildIds': actual, 'deliveredChildIds': accepted, 'deliveredTrigger': bool(accepted)}


def archived_guidance(bundle: zipfile.ZipFile) -> dict:
    constants = lambda path: [n.value for n in ast.walk(ast.parse(bundle.read(path))) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    dynamic = [s.strip() for s in constants('minicode/coding_progress.py') if s.startswith(' Consider task delegation if')]
    system = [s for s in constants('minicode/prompt.py') if '## Sub-agent (task tool) usage guide\n' in s]
    tree = ast.parse(bundle.read('minicode/tools/task.py'))
    descriptions = [k.value.value for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords if k.arg == 'description' and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str) and 'workflow' in k.value.value and 'explore' in k.value.value]
    if len(dynamic) != 1 or len(system) != 1 or len(descriptions) != 1:
        raise ValueError('Frozen guidance literals are not uniquely identifiable')
    block = system[0].split('## Sub-agent (task tool) usage guide\n', 1)[1].split('\n- If a permission decision', 1)[0]
    return {'dynamic': dynamic[0], 'system': '## Sub-agent (task tool) usage guide\n' + block, 'task': descriptions[0]}


def text_content(message: dict) -> str:
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and isinstance(x.get('text'), str))
    return ''


def guidance_observation(body: dict, record: dict, guidance: dict) -> dict:
    tools = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    messages = body.get('messages', [])
    system = '\n'.join(text_content(m) for m in messages if m.get('role') == 'system')
    hint = any(m.get('role') == 'user' and '[Coding progress checkpoint]' in text_content(m) and guidance['dynamic'] in text_content(m) for m in messages)
    return {'call': record['call'], 'taskAvailable': 'task' in tools, 'directWriteAvailable': bool({'edit_file', 'write_file'} & tools.keys()),
            'staticSystemGuidePresent': guidance['system'] in system,
            'staticTaskGuidePresent': tools.get('task', {}).get('description', '').startswith(guidance['task']),
            'requestKind': 'nativeReview' if native_request({'toolNames': list(tools)}) else 'parent',
            'dynamicHintPresent': hint and not native_request({'toolNames': list(tools)}), 'hintCopiedToRestrictedView': hint and native_request({'toolNames': list(tools)}), 'responseTools': [t.get('name') for t in record.get('toolCalls', [])],
            'responseEvent': record.get('event'), 'launchRequests': classify_task_requests([record])['launchRequests']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    summary = Audit(args.input, allow_partial=args.allow_partial).run()
    (args.input / 'trigger-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: summary[k] for k in ('valid', 'mode', 'expectedTrials', 'auditedTrials')}))
    return 0 if summary['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
