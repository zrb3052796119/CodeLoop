#!/usr/bin/env python3
"""Independent bounded-review reader: never import a runner or execute a model/oracle."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import stat
import subprocess
import zipfile
try:
    from scripts import audit_multi_agent_trigger as base
except ModuleNotFoundError:
    import audit_multi_agent_trigger as base

prior, legacy = base.prior, base.legacy
canonical_digest, safe_path, sha256 = base.canonical_digest, base.safe_path, base.sha256
archived_guidance, checked_oracle = base.archived_guidance, base.checked_oracle
CASES = ('trigger-stock-allocation', 'trigger-config-overlay')
EXPECTATIONS = {name: 'positive' for name in CASES}
PROTOCOL = 'native-review-bounded-v1'
GUIDANCE_POLICY = base.GUIDANCE_POLICY
TLS_TRUST = {'path': '/etc/ssl/cert.pem', 'sha256': '9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994', 'certificateVerification': True}
LIMITS = {'maxTokens': 500000, 'maxModelCalls': 40, 'maxCostUsd': '5', 'wallSeconds': 600,
    'maxOutputTokens': 8192, 'requestTimeoutSeconds': 300, 'idleTimeoutSeconds': 120, 'connectTimeoutSeconds': 30}
GATE_POLICY = {'expectedTrials': 2, 'gatedCase': 'trigger-stock-allocation',
    'descriptiveCase': 'trigger-config-overlay', 'acceptedReviewTerminals': ['approved', 'rejected'],
    'budgetExhaustedPasses': False, 'approvedRequiresAcceptedFinal': True,
    'requireAllTerminal': True, 'requireIndependentAudit': True}
REVIEW_POLICY = {'mode': 'bounded', 'phaseLimits': {'review': [8, 90.0],
    'inspection': [8, 150.0], 'reconciliation': [4, 60.0]},
    'closingRequests': 2, 'adapterRetries': 0, 'reentryResetsAllowance': False}
BASELINE_ARCHIVE_SHA256 = 'a118a493563821da50b8d2e15385f9cdc686ee433c36e44b8f0a3990b88d7d3d'
ORIGINAL_MANIFEST_SHA256 = 'a740668dd53ca4ce15b1f07156ae498ee335b2c8873c5ff5b5b344d83b1d91ab'
ORIGINAL_TASK_SHA256 = '78550c227d36e719514f55c3d540a721a11aad8535de34a6a5155d57c8553937'
PRODUCTION_OVERLAYS = (
    'minicode/agent_budget.py', 'minicode/workflow_runner.py', 'minicode/agent_loop.py',
    'minicode/anthropic_adapter.py', 'minicode/implementation_completion_review.py',
    'minicode/completion_coverage_inspection.py', 'minicode/completion_coverage_audit.py',
    'minicode/completion_probe_migration.py', 'minicode/probe_failure_adjudication.py',
    'minicode/tooling.py', 'minicode/completion_review_limits.py',
)


class Audit(base.Audit):
    def design(self, manifest, cases):
        expected = {'schemaVersion': 1, 'protocolVersion': PROTOCOL, 'phase': 'native-review-bounded',
            'runner': 'scripts/native_review_bounded_validation.py', 'arms': ['auto'], 'repeats': 1,
            'model': 'glm-5.3-flash', 'temperature': 0, 'concurrency': 1, 'scheduleSeed': 20261023,
            'limits': LIMITS, 'gatePolicy': GATE_POLICY, 'reviewPolicy': REVIEW_POLICY, 'tlsTrust': TLS_TRUST,
            'implementationPhasePolicy': 'production-continuous-default',
            'requestEvidencePolicy': GUIDANCE_POLICY['version'],
            'nativeObservationPolicy': 'existing-host-events-and-allowance-selection-v1',
            'hostProvenancePolicy': 'original-trigger-archive-plus-committed-explicit-overlay-v1',
            'baselineArchiveSha256': BASELINE_ARCHIVE_SHA256, 'originalTaskSha256': ORIGINAL_TASK_SHA256}
        for key, value in expected.items():
            self.equal(manifest.get(key), value, 'design_' + key)
        self.equal(list(cases), list(CASES), 'design_cases')
        self.equal(manifest.get('schedule'), [{'case': c, 'arm': 'auto', 'repeat': 1, 'id': f'{c}-r1-auto'} for c in CASES], 'frozen_schedule_order')
        self.check(not manifest.get('explicitRecipe'), 'unexpected_explicit_recipe', 'Direct auto tools retained')
        return ['auto']

    def engineering_gate(self, manifest):
        self.equal(manifest.get('engineeringGate'), None, 'no_inherited_trigger_gate')

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
                source = bundle.read("scripts/native_review_bounded_validation.py")
                self.equal(prior.literal_object(source, "PROTOCOL_VERSION"), PROTOCOL, "frozen_protocol")
                self.equal(list(prior.literal_object(source, "CASES")), list(CASES), "frozen_cases")
                self.equal(prior.literal_object(source, "GATE_POLICY"), GATE_POLICY, "frozen_gate_policy")
                self.equal(prior.literal_object(source, "REVIEW_POLICY"), REVIEW_POLICY, "frozen_review_policy")
                self.equal(prior.literal_object(source, "TLS_TRUST"), TLS_TRUST, "frozen_tls_trust")
                phase_caps = prior.literal_object(bundle.read("minicode/completion_review_limits.py"), "PHASE_LIMITS")
                self.equal({k: list(v) for k, v in phase_caps.items()}, REVIEW_POLICY['phaseLimits'], "production_review_phase_caps")
                self.equal(prior.literal_object(source, "REQUEST_EVIDENCE_POLICY"), GUIDANCE_POLICY["version"], "frozen_request_evidence_policy")
                for literal, field in (("LIMITS", "limits"), ("MODEL", "model"), ("SEED", "scheduleSeed"),
                                       ("ROLE_TURN_LIMITS", "roleTurnLimits")):
                    self.equal(manifest.get(field), prior.literal_object(source, literal), "frozen_" + field)
                harness = prior.literal_names(source, "HARNESS_FILES")
                self.equal(len(harness), len(set(harness)), "duplicate_harness_files")
                self.equal(manifest.get("harnessFiles"), harness, "harness_file_list")
                required = {"__init__.py", "native_review_bounded_validation.py", "audit_native_review_bounded.py",
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
            self.host_provenance(manifest)

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
            self.equal(receipt.get("entrypoint"), str(host / "scripts/native_review_bounded_validation.py"), "execution_host_entrypoint", identity)
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
            self.exact_reuse(manifest, tasks)

    def host_provenance(self, manifest):
        source_path = self.directory / 'materials/host-source.json'
        source = legacy.read_json(source_path)
        self.equal(legacy.digest(source_path.read_bytes()), manifest.get('hostSourceSha256'), 'host_source_hash')
        self.equal(source.get('sourceHostCommit'), manifest.get('sourceHostCommit'), 'source_host_commit')
        self.equal(source.get('baselineArchiveSha256'), BASELINE_ARCHIVE_SHA256, 'host_baseline_identity')
        baseline_path = self.directory / 'materials/original-execution-host.zip'
        self.equal(legacy.digest(baseline_path.read_bytes()), BASELINE_ARCHIVE_SHA256, 'host_baseline_bytes')
        required = set(PRODUCTION_OVERLAYS) | {'scripts/native_review_bounded_validation.py', 'scripts/audit_native_review_bounded.py'}
        overlays = source.get('overlayPaths', [])
        self.equal(set(overlays), required, 'explicit_overlay_scope')
        self.equal(len(overlays), len(set(overlays)), 'unique_overlay_paths')
        commit = source.get('sourceHostCommit')
        self.check(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit) is not None,
                   'source_commit_identity', str(commit))
        repository = subprocess.check_output(['git', '-C', str(self.directory), 'rev-parse', '--show-toplevel'], text=True).strip()
        with zipfile.ZipFile(baseline_path) as baseline, zipfile.ZipFile(self.directory / 'execution-host.zip') as current:
            expected_names = set(baseline.namelist()) | required
            self.equal(set(current.namelist()), expected_names, 'host_exact_baseline_plus_overlay')
            self.equal(set(source['files']), expected_names, 'host_origin_inventory')
            for name in sorted(expected_names):
                record = source['files'][name]
                content = current.read(name)
                self.equal(legacy.digest(content), record.get('sha256'), 'host_origin_hash_' + name)
                if name in required:
                    self.equal(record.get('origin'), 'commit', 'host_origin_' + name)
                    self.equal(record.get('sourceHostCommit'), commit, 'host_origin_commit_' + name)
                    original = subprocess.check_output(['git', '-C', repository, 'show', f'{commit}:{name}'])
                else:
                    self.equal(record.get('origin'), 'baseline_archive', 'host_origin_' + name)
                    original = baseline.read(name)
                self.equal(legacy.digest(content), legacy.digest(original), 'host_source_bytes_' + name)
        for name in ('materials/host-source.json', 'materials/original-execution-host.zip'):
            self.check(name in manifest.get('registrationFiles', {}), 'unregistered_host_source', name)

    def exact_reuse(self, manifest, tasks):
        original = self.directory / 'materials/original-trigger'
        manifest_raw, task_raw = (original / 'manifest.json').read_bytes(), (original / 'tasks.json').read_bytes()
        self.equal(legacy.digest(manifest_raw), ORIGINAL_MANIFEST_SHA256, 'original_manifest_bytes')
        self.equal(legacy.digest(task_raw), ORIGINAL_TASK_SHA256, 'original_task_bytes')
        old_manifest, old_tasks = json.loads(manifest_raw), json.loads(task_raw)
        selected = {**old_tasks, 'cases': [next(c for c in old_tasks['cases'] if c['id'] == n) for n in CASES]}
        self.equal(tasks, selected, 'exact_original_task_objects')
        self.equal([r for r in old_manifest['schedule'] if r['case'] in CASES], manifest['schedule'], 'original_schedule_projection')
        for name, key in (('offline-proof.json', 'case'), ('reference-fixes.json', 'id'), ('source-manifest.json', 'id')):
            raw = (original / name).read_bytes()
            self.equal(legacy.digest(raw), old_manifest['registrationFiles']['materials/' + name], 'original_proof_' + name)
            old = json.loads(raw)
            old_rows = old if isinstance(old, list) else old['cases']
            value = legacy.read_json(self.directory / 'materials' / name)
            rows = value if isinstance(value, list) else value['cases']
            self.equal(rows, [next(r for r in old_rows if r[key] == n) for n in CASES], 'exact_selected_proof_' + name)
        required = {'materials/original-trigger/' + name for name in ('manifest.json', 'tasks.json', 'offline-proof.json', 'reference-fixes.json', 'source-manifest.json')}
        self.check(required <= set(manifest.get('registrationFiles', {})), 'unregistered_original_source', str(required))

    def trial(self, row, case, manifest):
        super().trial(row, case, manifest)
        identity = row['id']
        root = self.directory / 'private' / identity
        observation = root / 'native-review-observation.jsonl'
        self.check(observation.is_file() and not observation.is_symlink(), 'native_observation_missing', identity, identity)
        events = [json.loads(line) for line in observation.read_text().splitlines()]
        self.equal([e.get('sequence') for e in events], list(range(1, len(events) + 1)), 'native_observation_sequence', identity)
        self.check(all(type(e.get(k)) in (int, float) and math.isfinite(e[k]) for e in events for k in ('epoch', 'monotonic')), 'native_observation_clock', identity, identity)
        self.check(all(a['monotonic'] <= b['monotonic'] for a, b in zip(events, events[1:])), 'native_observation_order', identity, identity)
        self.check(bool(events) and events[0]['kind'] == 'observer_started'
            and events[0]['payload'] == {'mode': 'bounded', 'trialId': identity}, 'native_observer_start', identity, identity)
        requests = legacy.read_json(root / 'transport-summary.json')['requests']
        native = native_observation(events, requests, row)
        self.check(native['chainCount'] <= 1, 'native_chain_reset', str(native['chainCount']), identity)
        self.check(native['terminalConsistent'], 'native_terminal_inconsistent', str(native['terminal']), identity)
        self.check(native['stageCapsConsistent'], 'native_stage_caps_changed', identity, identity)
        self.check(native['nativeClassificationComplete'], 'native_request_phase_missing', identity, identity)
        self.trials[-1]['nativeReview'] = native
        self.trials[-1]['nativeObservationSha256'] = legacy.digest(observation.read_bytes())


def native_observation(events, requests, row):
    """Recompute diagnostics from existing host events and actual physical requests."""
    chains = [e for e in events if e['kind'] == 'chain_created' and e['payload'].get('agentDepth') == 0]
    phases = [e for e in events if e['kind'] == 'phase_selected' and e['payload'].get('agentDepth') == 0]
    host = [e for e in events if e['kind'] == 'host_event' and not (e['payload'].get('actor') or {}).get('subagentId')]
    started = [e for e in host if e['payload']['eventType'] == 'implementation.completion_review_started']
    decisions = [e for e in host if e['payload']['eventType'] == 'implementation.completion_decided']
    outcomes = [e for e in host if e['payload']['eventType'] == 'task.outcome']
    matched_start = started[-1] if started else None
    decision = next((e for e in reversed(decisions) if matched_start and e['sequence'] > matched_start['sequence']
        and all(e['payload']['data'].get(k) == matched_start['payload']['data'].get(k)
            for k in ('executorId', 'attempt', 'patchDigest'))), None)
    data = decision['payload']['data'] if decision else {}
    terminal = data.get('completionReview')
    consistent = (terminal is None or (terminal == 'approved' and data.get('status') == 'approved' and data.get('requirementsAccepted') is True)
        or (terminal == 'rejected' and data.get('status') == 'changes_required' and data.get('requirementsAccepted') is False)
        or (terminal == 'budget_exhausted' and data.get('status') == 'inconclusive' and data.get('requirementsAccepted') is False
            and data.get('reason') == 'completion_review_budget_exhausted'))
    phases_by_name = {name: [] for name in REVIEW_POLICY['phaseLimits']}
    native_requests, unknown_phase = [], []
    for record in requests:
        tools = set(record.get('toolNames', []))
        native = tools & base.NATIVE_RESULT_TOOLS
        if not native or record.get('subagentId'):
            continue
        previous = [e for e in phases if e['epoch'] <= record['startedEpoch']]
        phase = previous[-1]['payload']['phase'] if previous else None
        item = {'call': record['call'], 'phase': phase, 'terminalTools': sorted(native), 'event': record['event'],
            'startedEpoch': record['startedEpoch'], 'seconds': record.get('seconds'), 'usage': record['usage']}
        native_requests.append(item)
        if phase in phases_by_name:
            phases_by_name[phase].append(item)
        else:
            unknown_phase.append(record['call'])
    stop = next((e for e in reversed(events) if e['kind'] == 'observer_finished'), None)
    cleanup_epoch = row.get('processCleanup', {}).get('deadlineReachedEpoch')
    stages = {}
    all_caps = True
    for name, (calls, seconds) in REVIEW_POLICY['phaseLimits'].items():
        selections = [e for e in phases if e['payload']['phase'] == name]
        deadlines = {e['payload']['deadline'] for e in selections}
        all_caps = (all_caps and len(deadlines) <= 1
            and all(e['payload']['stageCalls'] == calls and e['payload']['deadline'] <= e['payload']['chainDeadline']
                    and e['payload']['deadline'] <= selections[0]['monotonic'] + seconds for e in selections))
        elapsed, censored = 0.0, False
        for entry in selections:
            boundary = next((e for e in events if e['sequence'] > entry['sequence'] and (
                (e['kind'] == 'phase_selected' and e['payload'].get('agentDepth') == 0)
                or (e['kind'] == 'host_event' and e['payload']['eventType'] == 'implementation.completion_decided'
                    and not (e['payload'].get('actor') or {}).get('subagentId')) or e['kind'] == 'observer_finished')), None)
            if boundary:
                elapsed += max(0.0, boundary['monotonic'] - entry['monotonic'])
                if boundary['kind'] == 'observer_finished':
                    censored = True
            else:
                censored = True
                if cleanup_epoch is not None:
                    elapsed += max(0.0, cleanup_epoch - entry['epoch'])
        physical = phases_by_name[name]
        known = [r for r in physical if r['usage']['source'] == 'provider']
        stages[name] = {'entered': bool(selections), 'activeSeconds': elapsed if selections else None,
            'censored': censored, 'deadline': next(iter(deadlines)) if len(deadlines) == 1 else None,
            'physicalRequests': len(physical), 'unknownUsageCalls': len(physical) - len(known),
            'knownTokens': sum(r['usage']['input_tokens'] + r['usage']['output_tokens'] for r in known),
            'callIds': [r['call'] for r in physical], 'requestLimit': calls, 'secondsLimit': seconds}
        all_caps = all_caps and len(physical) <= calls
    accepted = bool(terminal == 'approved' and consistent and outcomes and outcomes[-1]['payload']['data'].get('completionSucceeded') is True
        and outcomes[-1]['payload']['data'].get('outcomeStatus') == 'success' and row.get('errorClass') is None and stop)
    def within_event(event):
        last_phase = next((e for e in reversed(phases) if e['sequence'] < event['sequence']), None)
        return bool(len(chains) == 1 and event['monotonic'] <= chains[0]['payload']['deadline']
            and last_phase and event['monotonic'] <= last_phase['payload']['deadline'])

    def actual_native(event):
        value = event['payload']['data']
        beginning = next((e for e in reversed(started) if e['sequence'] < event['sequence'] and all(
            e['payload']['data'].get(k) == value.get(k) for k in ('executorId', 'attempt', 'patchDigest'))), None)
        return bool(beginning and any(beginning['epoch'] <= r['startedEpoch'] <= event['epoch']
            and r['event'] == 'completed' for r in native_requests))

    authority = decision if decision and actual_native(decision) else None
    # Same exclusions as completion_repair_decision.delivered_repair_matches.
    # All nested findings, probes, check receipts and source bindings stay exact.
    ignored = {'completionVersion', 'executorId', 'attempt', 'continuesSameExecutor',
        'evidenceRef', 'repairDecisionRef', 'evidenceDirectory'}
    if decision and authority is None and terminal == 'rejected':
        substance = {k: v for k, v in data.items() if k not in ignored}
        authority = next((e for e in reversed(decisions) if e['sequence'] < decision['sequence']
            and e['payload']['data'].get('executorId') == data.get('executorId')
            and e['payload']['data'].get('patchDigest') == data.get('patchDigest')
            and {k: v for k, v in e['payload']['data'].items() if k not in ignored} == substance
            and actual_native(e) and within_event(e)), None)
    within = bool(authority and within_event(authority))
    delayed_handoff = bool(decision and authority is not None and authority is not decision and not within_event(decision))
    native_before_decision = authority is not None
    return {'terminal': terminal, 'status': data.get('status'), 'reason': data.get('reason'),
        'requirementsAccepted': data.get('requirementsAccepted'), 'acceptedFinal': accepted,
        'terminalConsistent': bool(consistent), 'nativeReviewActuallyExecuted': native_before_decision,
        'nativeAuthoritySequence': authority['sequence'] if authority else None,
        'nativeAuthorityReused': authority is not None and authority is not decision,
        'delayedHandoff': delayed_handoff,
        'decisionWithinChain': within, 'chainCount': len(chains), 'stageCapsConsistent': bool(all_caps),
        'nativeClassificationComplete': not unknown_phase, 'unclassifiedNativeCalls': unknown_phase,
        'chain': chains[0]['payload'] if len(chains) == 1 else None,
        'stages': stages, 'nativePhysicalRequests': len(native_requests), 'requests': native_requests,
        'hostDecisions': [{'sequence': e['sequence'], 'epoch': e['epoch'], 'data': e['payload']['data']} for e in decisions],
        'interpretation': 'Stage wall times include tools and local work; censored spans are not completed stages. Provider unknown usage stays unknown.'}


def assess(audit):
    rows = audit.get('trials', [])
    stock = next((r for r in rows if r.get('case') == CASES[0]), {})
    result = stock.get('nativeReview', {})
    complete_usage = len(rows) == 2 and all(r.get('usageComplete') is True and r.get('unknownUsageCalls') == 0 for r in rows)
    conditions = {'validFinalAudit': audit.get('valid') is True and audit.get('mode') == 'final'
            and audit.get('expectedTrials') == audit.get('auditedTrials') == 2,
        'stockActualNativeReview': result.get('nativeReviewActuallyExecuted') is True,
        'stockTerminalDecision': result.get('terminal') in {'approved', 'rejected'},
        'stockWithinLimits': result.get('decisionWithinChain') is True and result.get('stageCapsConsistent') is True,
        'approvedHasAcceptedFinal': result.get('terminal') != 'approved' or result.get('acceptedFinal') is True}
    return {'passed': all(conditions.values()), 'conditions': conditions, 'stockTerminal': result.get('terminal'),
        'configDescriptiveOnly': True, 'repairSuccessIsNotAcceptance': True,
        'usageVerification': 'complete' if complete_usage else 'unverifiable',
        'usageDoesNotAddAConfigBehavioralGate': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--allow-partial', action='store_true')
    parser.add_argument('--output', type=Path, help='Non-frozen report path; stdout if omitted')
    args = parser.parse_args(argv)
    report = Audit(args.input, allow_partial=args.allow_partial).run()
    report['gate'] = assess(report)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        if args.output.exists():
            raise ValueError('report output already exists')
        args.output.write_text(encoded)
    else:
        print(encoded, end='')
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
