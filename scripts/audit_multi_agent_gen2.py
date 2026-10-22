#!/usr/bin/env python3
"""Independently audit gen-2 frozen inputs and surviving trial evidence.

This reader never imports a runner, extracts or executes archived source, runs
an oracle, contacts a provider, or turns missing usage into zero usage.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path, PurePosixPath
import random
import re
import stat
import subprocess
import zipfile

try:
    from scripts import audit_implementation_progress as prior
except ModuleNotFoundError:
    import audit_implementation_progress as prior

legacy = prior.legacy
CASES = ("gen2-read-context", "gen2-registry-lifetime", "gen2-summary-budget")
BASE = "9aaa6a434ae0a86766d28f8b1b91529451a31eb8"
REFERENCE = "ad20e4a893248a747e3b3ac741c280ded413ff54"
PROTOCOL = "multi-agent-gen2-v1"


def canonical_digest(value) -> str:
    return legacy.digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError("invalid evidence relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("unsafe evidence relative path")
    return value


def sha256(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class Audit(prior.Audit):
    """Reuse the independent transport, delivery, scope and judge validators."""

    def design(self, manifest, cases):
        self.equal(manifest.get("schemaVersion"), 1, "schema_version")
        self.equal(manifest.get("protocolVersion"), PROTOCOL, "protocol_version")
        self.equal(manifest.get("phase"), "gen2", "phase")
        self.equal(manifest.get("runner"), "scripts/run_multi_agent_gen2.py", "runner_entrypoint")
        self.equal(manifest.get("arms"), ["single", "explicit"], "design_arms")
        self.equal(manifest.get("repeats"), 1, "design_repeats")
        self.equal(manifest.get("model"), "glm-5.3-flash", "design_model")
        self.equal(manifest.get("temperature"), 0, "design_temperature")
        self.equal(manifest.get("concurrency"), 1, "design_concurrency")
        self.equal(manifest.get("limits"), {
            "maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5",
            "wallSeconds": 600, "maxOutputTokens": 8192, "requestTimeoutSeconds": 300,
            "idleTimeoutSeconds": 120, "connectTimeoutSeconds": 30,
        }, "design_limits")
        self.equal(list(cases), list(CASES), "design_cases")
        self.equal(manifest.get("scheduleSeed"), 20261023, "design_seed")
        expected = [{"case": case, "arm": arm, "repeat": 1, "id": f"{case}-r1-{arm}"}
                    for case in CASES for arm in ("single", "explicit")]
        random.Random(20261023).shuffle(expected)
        self.equal(manifest.get("schedule"), expected, "frozen_schedule_order")
        recipe = str(manifest.get("explicitRecipe", "")).lower()
        self.check(all(text in recipe for text in ("coordinator-only", "task/todo_write", "direct workflow")),
                   "explicit_recipe", "Expected the same coordinator-only direct workflow treatment")
        return ["single", "explicit"]

    def engineering_gate(self, manifest):
        # This is a new six-cell experiment, not promotion of the old six cells.
        self.equal(manifest.get("engineeringGate"), None, "gen2_no_historical_gate")
        self.equal(manifest.get("reusedCalibrationCases", []), [], "gen2_no_reused_cases")

    def public_fixture_preflight(self, manifest, bundle, capabilities):
        super().public_fixture_preflight(manifest, bundle, capabilities)
        report = legacy.read_json(self.directory / "public-preflight/receipt.json")
        self.equal(report.get("pytestReportRequired"), True, "public_pytest_report_required")
        for row in report.get("cases", []):
            pytest = row.get("pytestReport")
            valid = isinstance(pytest, dict)
            cases = pytest.get("caseReport", {}).get("cases", []) if valid else []
            valid = bool(valid and cases and len({item.get("nodeId") for item in cases}) == len(cases)
                         and all(item.get("outcome") == "passed" and item.get("failureKind") is None for item in cases)
                         and pytest.get("collected") == pytest.get("passed") == len(cases)
                         and all(pytest.get(key) == 0 for key in ("failed", "errors", "skipped")))
            if valid:
                material = {"junitDigest": pytest.get("reportDigest"), "cases": cases}
                digest = legacy.digest(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
                valid = pytest["caseReport"].get("digest") == "sha256:" + digest
            self.check(valid, "public_pytest_case_evidence", row.get("case"), row.get("case"))

    def archived_hashes(self, manifest):
        registration = manifest.get("registrationFiles", {})
        required_materials = {"tasks.json", "analysis-plan.md", "execution-host.zip", "execution-host-inventory.json",
                              "environment.json", "public-preflight/receipt.json",
                              "materials/source-manifest.json", "materials/offline-proof.json"}
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
            source = bundle.read("scripts/run_multi_agent_gen2.py")
            self.equal(prior.literal_object(source, "PROTOCOL_VERSION"), PROTOCOL, "frozen_protocol")
            self.equal(list(prior.literal_object(source, "CASES")), list(CASES), "frozen_cases")
            for literal, field in (("LIMITS", "limits"), ("MODEL", "model"), ("SEED", "scheduleSeed"),
                                   ("ROLE_TURN_LIMITS", "roleTurnLimits")):
                self.equal(manifest.get(field), prior.literal_object(source, literal), "frozen_" + field)
            harness = prior.literal_names(source, "HARNESS_FILES")
            self.equal(len(harness), len(set(harness)), "duplicate_harness_files")
            self.equal(manifest.get("harnessFiles"), harness, "harness_file_list")
            required = {"__init__.py", "run_multi_agent_gen2.py", "audit_multi_agent_gen2.py",
                        "run_implementation_progress_live.py", "audit_implementation_progress.py",
                        "run_three_arm_real.py", "audit_three_arm_real.py", "implementation_judge.py",
                        "three_arm_protocol.py", "evaluation_transport.py"}
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

    def provenance(self, manifest, tasks):
        source = legacy.read_json(self.directory / "materials/source-manifest.json")
        proof_path = self.directory / "materials/offline-proof.json"
        proof = legacy.read_json(proof_path)
        self.equal(source.get("baselineCommit"), BASE, "historical_baseline_commit")
        self.equal(source.get("referenceCommit"), REFERENCE, "historical_reference_commit")
        self.equal(source.get("modelCalls"), 0, "offline_no_model_calls")
        self.equal(source.get("taskSha256"), manifest.get("taskSha256"), "source_task_hash")
        self.equal(source.get("offlineProofSha256"), legacy.digest(proof_path.read_bytes()), "offline_proof_hash")
        self.equal(tasks.get("generation"), "gen-2", "task_generation")
        shared = tasks["shared_files"]
        self.equal(source.get("sharedFiles"), {safe_path(name): legacy.digest(content.encode()) for name, content in shared.items()},
                   "historical_shared_file_hashes")
        self.check(bool(shared) and "pyproject.toml" in shared and any(name.startswith("minicode/") for name in shared),
                   "historical_source_missing", "Expected the actual baseline package and project metadata")
        cases = tasks["cases"]
        self.equal(manifest.get("publicBaselineHashes"), {
            case["id"]: {name: legacy.digest(content.encode()) for name, content in sorted({**shared, **case["files"]}.items())}
            for case in cases}, "public_baseline_hashes")
        self.equal(manifest.get("oracleHashes"), {case["id"]: legacy.digest(case["oracle_source"].encode()) for case in cases}, "oracle_hashes")
        self.equal(source.get("cases"), [{"id": case["id"], "provenance": case["provenance"]} for case in cases], "case_provenance")
        self.equal([row.get("case") for row in proof], [case["id"] for case in cases], "offline_proof_coverage")
        for case, evidence in zip(cases, proof):
            identity = case["id"]
            provenance = case["provenance"]
            self.equal(provenance.get("baselineCommit"), BASE, "case_baseline_commit", identity)
            self.equal(provenance.get("referenceCommit"), REFERENCE, "case_reference_commit", identity)
            source_paths = [safe_path(name) for name in case["sourcePaths"]]
            self.equal(len(source_paths), len(set(source_paths)), "duplicate_source_path", identity)
            self.check(bool(source_paths) and all(name.startswith("minicode/") and name.endswith(".py") for name in source_paths),
                       "case_source_paths", str(source_paths), identity)
            self.equal(set(case["authorized_paths"]), set(source_paths) | {"tests/test_eval_regression.py"}, "case_write_scope", identity)
            self.equal(set(case["files"]), {"tests/test_public.py"}, "public_file_boundary", identity)
            self.check("tests/test_eval_regression.py" not in shared, "regression_already_exists", identity, identity)
            blobs = provenance["sourceBlobs"]
            self.equal(set(blobs), set(source_paths), "source_blob_coverage", identity)
            changed = False
            for name in source_paths:
                self.equal(blobs[name].get("baselineSha256"), legacy.digest(shared[name].encode()), "baseline_blob", identity)
                self.check(sha256(blobs[name].get("referenceSha256")), "reference_blob_hash", name, identity)
                changed |= blobs[name].get("referenceSha256") != blobs[name].get("baselineSha256")
            self.check(changed, "reference_has_no_source_change", identity, identity)
            self.equal(evidence.get("baselineCommit"), BASE, "proof_baseline_commit", identity)
            self.equal(evidence.get("referenceCommit"), REFERENCE, "proof_reference_commit", identity)
            self.equal(evidence.get("oracleSha256"), legacy.digest(case["oracle_source"].encode()), "proof_oracle_hash", identity)
            self.equal(evidence.get("publicTestSha256"), legacy.digest(case["files"]["tests/test_public.py"].encode()), "proof_public_hash", identity)
            for key in ("baselinePublic", "referencePublic", "referenceOracle"):
                self.equal(evidence[key].get("returncode"), 0, "offline_" + key, identity)
            failed = evidence["baselineOracle"]
            self.equal(failed.get("returncode"), 1, "offline_baseline_must_fail", identity)
            try:
                before = json.loads(failed["stdout"])
                after = json.loads(evidence["referenceOracle"]["stdout"])
                target_failed = (isinstance(before, dict) and isinstance(after, dict)
                                 and before.get("passed", before.get("ok")) is False
                                 and bool(before.get("errors", before.get("failures")))
                                 and after.get("passed", after.get("ok")) is True
                                 and not after.get("errors", after.get("failures")))
            except (ValueError, TypeError, KeyError):
                target_failed = False
            self.check(target_failed, "offline_target_failure_unproven", "Require target-specific JSON failure then acceptance", identity)
            diagnostic = str(failed.get("stdout", "")) + str(failed.get("stderr", ""))
            self.check(not any(text in diagnostic for text in ("ModuleNotFoundError", "ImportError:", "SyntaxError:", "No such file or directory")),
                       "offline_failure_not_behavioral", "Setup/import failures cannot prove a bug", identity)

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
        self.equal(receipt.get("entrypoint"), str(host / "scripts/run_multi_agent_gen2.py"), "execution_host_entrypoint", identity)
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

    def committed_registration(self, receipt, manifest, identity):
        """Read Git objects only; no runner or archived command is executed."""
        commit = receipt.get("preregisteredCommit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            return
        checked = getattr(self, "_checked_commits", {})
        if commit in checked:
            recorded = receipt.get("recordedAtEpoch")
            self.check(type(recorded) in (int, float) and checked[commit] <= recorded,
                       "preregistration_after_execution", str(checked[commit]), identity)
            return
        try:
            repository = Path(subprocess.check_output(["git", "-C", str(self.directory), "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip()).resolve()
            relative = self.directory.resolve().relative_to(repository).as_posix()
            for name in ["manifest.json", *manifest["registrationFiles"]]:
                frozen = subprocess.check_output(["git", "-C", str(repository), "show", f"{commit}:{relative}/{safe_path(name)}"], stderr=subprocess.DEVNULL)
                self.equal(legacy.digest(frozen), legacy.digest((self.directory / name).read_bytes()), "preregistered_git_blob_" + name, identity)
            epoch = int(subprocess.check_output(["git", "-C", str(repository), "show", "-s", "--format=%ct", commit], text=True, stderr=subprocess.DEVNULL).strip())
            recorded = receipt.get("recordedAtEpoch")
            self.check(type(recorded) in (int, float) and epoch <= recorded, "preregistration_after_execution", str(epoch), identity)
            checked[commit] = epoch
            self._checked_commits = checked
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.issue("preregistration_git_unreadable", type(error).__name__ + ": " + str(error), identity)

    def trial(self, row, case, manifest):
        self.execution_host(row, manifest)
        # The generation number is not a waiver of the prior host-judged
        # oracle + submitted-test conjunction. Select that existing validator
        # explicitly; archived metadata is never rewritten or relabelled.
        super().trial(row, case, {**manifest, "schemaVersion": 5})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    summary = Audit(args.input, allow_partial=args.allow_partial).run()
    legacy.write_report(args.input, summary)
    print(json.dumps({key: summary[key] for key in ("valid", "mode", "expectedTrials", "auditedTrials")}))
    return 0 if summary["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
