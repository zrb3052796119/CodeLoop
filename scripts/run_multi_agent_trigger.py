#!/usr/bin/env python3
"""Freeze and run eight new synthetic automatic-delegation trials once.

Preparation and freezing are offline. Provider execution requires the exact
registration commit and runs only from the verified archived execution host.
Historical runners and their frozen experiments are never modified.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import run_multi_agent_gen2 as foundation  # noqa: E402
import evaluation_transport as transport  # noqa: E402
import three_arm_protocol as protocol  # noqa: E402
from three_arm_protocol import CollaborationEvidence  # noqa: E402

shared = foundation.shared
DEFAULT = ROOT / "artifacts/multi-agent-trigger-2026-10-23/run"
PROTOCOL_VERSION = "multi-agent-trigger-v1"
CASES = (
    "trigger-record-pagination", "trigger-import-retry", "trigger-cart-discounts",
    "trigger-stock-allocation", "trigger-report-timezones", "trigger-config-overlay",
    "trigger-limit-clamp", "trigger-extension-case",
)
ARMS = ("auto",)
SEED = 20261023
MODEL = "glm-5.3-flash"
LIMITS = {
    "maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5",
    "wallSeconds": 600, "maxOutputTokens": 8192,
    "requestTimeoutSeconds": 300, "idleTimeoutSeconds": 120,
    "connectTimeoutSeconds": 30,
}
ROLE_TURN_LIMITS = {"explore": 0, "plan": 0, "general": 0}
ROLE_TURN_POLICY = "explicit-zero-role-ceilings-shared-budget-v1"
HARNESS_FILES = (
    "__init__.py", "run_three_arm_real.py", "evaluation_transport.py",
    "three_arm_protocol.py", "three_arm_scope.py", "three_arm_scope_metadata.py",
    "three_arm_processes.py", "run_north_star_live.py", "run_multi_agent_cost_live.py",
    "analyze_three_arm_real.py", "run_implementation_progress_live.py",
    "audit_three_arm_real.py", "audit_implementation_progress.py",
    "analyze_implementation_progress.py", "implementation_judge.py",
    "public_verification_fixtures.py", "run_multi_agent_gen2.py",
    "audit_multi_agent_gen2.py", "analyze_multi_agent_gen2.py",
    "run_multi_agent_trigger.py", "audit_multi_agent_trigger.py",
    "analyze_multi_agent_trigger.py", "prepare_multi_agent_trigger_tasks.py",
)
JUDGE_POLICY = {**foundation.progress.JUDGE_POLICY, "agentBudgetSeconds": 600}
GATE_POLICY = {
    "expectedTrials": 8, "opportunityTrials": 6, "negativeTrials": 2,
    "triggerRateDenominator": 8, "minimumDeliveredTriggerTrials": 2,
    "maximumNegativeLaunchRequests": 0,
    "requireAllTerminal": True, "requireIndependentAudit": True,
    "requireNegativeLaunchClassificationComplete": True,
}
REQUEST_EVIDENCE_POLICY = "complete-parent-provider-body-before-send-ledger-call-v1"
GUIDANCE_EVIDENCE_POLICY = {
    "version": "complete-parent-provider-body-before-send-ledger-call-v1",
    "bodyHash": "sha256-json-sort_keys-compact-utf8",
    "static": "actual-parent-request-system-prompt-and-task-schema",
    "dynamic": "actual-parent-request-message-containing-complete-host-delegation-sentence",
    "choice": "same-physical-ledger-call-response-and-toolCalls",
    "missing": "unknown-not-absent",
}
ANALYSIS_PLAN = """# Trigger-gen2 preregistration

Eight new synthetic cases, six delegation opportunities and two direct-edit
negative controls. Each auto cell runs once, serially, in seed-20261023 order.
Case labels and private oracles stay on the host. Public instructions do not
require delegation. The parent retains task and direct tools, with production
continuous execution and no legacy bounded phase quotas. All roles inherit
GLM-5.3-Flash, temperature 0, retries 0, and share 40 physical requests, 500000
tokens and 600 execution seconds. Output/request/idle/connect limits are
8192 tokens / 300 / 120 / 30 seconds; the USD 5 safety ceiling is retained.

The same scope, public compatibility, newly generated regression, private
oracle, physical usage, cleanup and independent audit checks apply. Every
failure, timeout, refusal and unknown usage is retained. No reruns, replacement
cases, model changes or outcome selection. This fresh synthetic population is
not comparable with earlier populations and has no causal before/after claim.
Provider-visible parent request bodies are persisted before sending, bound to
physical ledger call IDs; the ledger retains the subsequent model decisions.
"""

# These helpers have no protocol-dependent globals.
canonical = foundation.canonical
safe_relative = foundation.safe_relative
inventory_for = foundation.inventory_for
environment_fingerprint = foundation.environment_fingerprint
assert_host_imports = foundation.assert_host_imports
PUBLIC_FIXTURE_POLICY = foundation.PUBLIC_FIXTURE_POLICY
public_fixture_manifest = foundation.public_fixture_manifest
run_public_preflight = foundation.run_public_preflight
validate_public_preflight = foundation.validate_public_preflight


def schedule_for() -> list[dict]:
    rows = [{"case": case, "arm": "auto", "repeat": 1, "id": f"{case}-r1-auto"}
            for case in CASES]
    random.Random(SEED).shuffle(rows)
    return rows


def static_contract() -> dict:
    return {
        "schemaVersion": 1, "protocolVersion": PROTOCOL_VERSION, "phase": "trigger-gen2",
        "cases": list(CASES), "arms": list(ARMS), "repeats": 1,
        "scheduleSeed": SEED, "schedule": schedule_for(), "concurrency": 1,
        "model": MODEL, "temperature": 0, "limits": LIMITS, "price": shared.PRICE,
        "roleTurnLimits": ROLE_TURN_LIMITS, "roleTurnPolicy": ROLE_TURN_POLICY,
        "judgementPolicy": JUDGE_POLICY, "gatePolicy": GATE_POLICY,
        "harnessFiles": list(HARNESS_FILES), "runner": "scripts/run_multi_agent_trigger.py",
        "implementationCapabilities": foundation.progress.IMPLEMENTATION_CAPABILITIES,
        "autoRecipe": "Parent retains direct tools and task; no forced delegation",
        "implementationPhasePolicy": "production-continuous-default",
        "requestEvidencePolicy": REQUEST_EVIDENCE_POLICY,
        "guidanceEvidencePolicy": GUIDANCE_EVIDENCE_POLICY,
        "triggerExpectations": {case: "positive" if index < 6 else "negative"
                                for index, case in enumerate(CASES)},
        "comparison": "New synthetic trigger population; no historical or causal comparison",
    }


def _foundation():
    """Isolate reused host-registration mechanics under this complete contract.

    No shared singleton is patched. Every protocol-bearing hook is replaced;
    the historical CLI, case schedule, gate and entry point cannot be selected.
    """
    spec = importlib.util.spec_from_file_location(
        "_trigger_registration_foundation", Path(foundation.__file__))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen host registration foundation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("runner", "validate_tasks", "validate", "host_files", "validate_offline_proof"):
        setattr(module, "_original_" + name, getattr(module, name))
    module.__file__ = str(Path(__file__).resolve())
    for name in (
        "ROOT", "DEFAULT", "PROTOCOL_VERSION", "CASES", "ARMS", "SEED", "MODEL",
        "LIMITS", "ROLE_TURN_LIMITS", "ROLE_TURN_POLICY", "HARNESS_FILES", "JUDGE_POLICY",
        "GATE_POLICY", "ANALYSIS_PLAN", "static_contract", "schedule_for", "validate_tasks",
        "prompt_for_arm", "runner", "probe_host", "validate", "validate_offline_proof", "host_files", "verify_archive",
        "verify_host", "unpack_host", "run_public_preflight", "validate_public_preflight",
    ):
        setattr(module, name, globals()[name])
    return module


def validate_tasks(document: dict) -> None:
    # Reuse validation of safe public maps, generated-test absence and argv,
    # but bind it to this protocol's case set, never gen2's constants.
    if document.get("generation") != "trigger-v1":
        raise ValueError("tasks must belong to the new synthetic trigger-v1 generation")
    if [case.get("id") for case in document.get("cases", [])] != list(CASES):
        raise ValueError("tasks must contain the eight fixed trigger cases in order")
    _foundation()._original_validate_tasks(document)
    if [case.get("triggerExpectation") for case in document["cases"]] != ["positive"] * 6 + ["negative"] * 2:
        raise ValueError("trigger labels must be six positive then two negative controls")


def prepare(output: Path, tasks: Path, *, plan: Path | None = None,
            materials: Path | None = None) -> None:
    _foundation().prepare(output, tasks, plan=plan, materials=materials)


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm != "auto":
        raise ValueError("trigger protocol permits only the auto arm")
    return prompt + foundation.progress.REGRESSION_REQUIREMENT.replace("1800", "600")


class ParentRequestTransport(transport.EvaluationTransport):
    """Keep complete parent inputs at the existing physical-send boundary."""

    def __enter__(self):
        super().__enter__()
        original = self._original_open

        def captured_open(request, **kwargs):
            state = transport._CURRENT_CALL.get()
            if state is None or type(state.get("call")) is not int:
                raise RuntimeError("parent request capture lacks physical ledger binding")
            if state["actor"].get("role") == "main":
                body = json.loads(request.data)
                directory = Path(self.config.ledger_path).parent / "parent-requests"
                directory.mkdir(mode=0o700, exist_ok=True)
                record = {
                    "schemaVersion": 1, "callId": state["call"],
                    "actor": state["actor"], "capturedAtEpoch": time.time(),
                    "routeSha256": self.config.expected_route_sha256,
                    "bodySha256": shared.sha(canonical(body)), "body": body,
                }
                path = directory / f"{state['call']}.json"
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(canonical(record) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self.ledger.update(state["call"], {
                    "parentRequestPath": path.relative_to(directory.parent).as_posix(),
                    "parentRequestSha256": shared.sha(path.read_bytes()),
                    "parentRequestBodySha256": record["bodySha256"],
                })
            return original(request, **kwargs)

        self._original_open = captured_open
        return self


class TriggerCollaborationEvidence(CollaborationEvidence):
    """Record the actual async owner acknowledgement, not polling alone."""

    def __init__(self, path: Path):
        super().__init__(path)
        self.async_terminal_acks: list[dict] = []

    def _save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({
            "runs": self.records, "deliveredIds": sorted(self.delivered),
            "asyncTerminalAcks": self.async_terminal_acks,
        }, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def __enter__(self):
        super().__enter__()
        from minicode.subagent_lifecycle import AsyncSubagentLifecycle
        original = AsyncSubagentLifecycle.acknowledge_delivery

        def acknowledged(registry, ids):
            original(registry, ids)  # validates ownership and terminal status
            snapshots = {identity: registry.poll(identity, wait_seconds=0) for identity in ids}
            owner = protocol._PARENT_ACTOR.get()
            with self.lock:
                recorded = {record["subagentId"] for record in self.records}
                terminal_acked = {ack["subagentId"] for ack in self.async_terminal_acks}
                for identity in ids:
                    if identity not in recorded and identity not in terminal_acked:
                        # A queued job can be cancelled before _run creates an
                        # execution record. Preserve its real ACK separately;
                        # do not manufacture a worker start or returned result.
                        self.async_terminal_acks.append({
                            "subagentId": identity, "agentType": snapshots[identity]["agentType"],
                            "ownerActorId": owner or "main", "ackEpoch": time.time(),
                            "snapshot": snapshots[identity],
                            "snapshotSha256": shared.sha(canonical(snapshots[identity])),
                        })
                        terminal_acked.add(identity)
                for record in self.records:
                    identity = record["subagentId"]
                    if (identity not in snapshots
                            or record.get("parentSubagentId") != owner):
                        continue
                    record.setdefault("deliveredEpoch", time.time())
                    record["deliveryEvidence"] = "async_lifecycle_owner_ack"
                    record["asyncDeliverySnapshot"] = snapshots[identity]
                    record["asyncDeliverySnapshotSha256"] = shared.sha(canonical(snapshots[identity]))
                    record["asyncDeliveryOwnerActorId"] = owner or "main"
                self._save()

        self.stack.enter_context(patch.object(AsyncSubagentLifecycle, "acknowledge_delivery", acknowledged))
        return self


def collaboration_metrics(records: list[dict], requests: list[dict], messages: list[dict], delivered_ids=()) -> dict:
    normalized = []
    for record in records:
        snapshot = record.get("asyncDeliverySnapshot")
        result = snapshot.get("result") if isinstance(snapshot, dict) else None
        output_matches = (isinstance(result, dict)
                          and type(result.get("ok")) is bool
                          and result["ok"] == record.get("toolOk")
                          and isinstance(result.get("output"), str)
                          and type(result.get("truncated")) is bool
                          and (result["truncated"]
                               or shared.sha(result["output"].encode()) == record.get("resultSha256")))
        if (record.get("deliveryEvidence") == "async_lifecycle_owner_ack"
                and isinstance(snapshot, dict) and snapshot.get("terminal") is True
                and snapshot.get("status") in {"completed", "failed", "cancelled"}
                and snapshot.get("agentType") == record.get("agentType")
                and snapshot.get("subagentId") == record.get("subagentId")
                and record.get("asyncDeliveryOwnerActorId") == (record.get("parentSubagentId") or "main")
                and output_matches
                and record.get("asyncDeliverySnapshotSha256") == shared.sha(canonical(snapshot))):
            record = {**record, "deliveryEvidence": "task_result_owner_ack"}
        normalized.append(record)
    return foundation.progress.collaboration_metrics(normalized, requests, messages, delivered_ids)


def runner(output: Path, *, commit: str | None = None):
    module = _foundation()._original_runner(output, commit=commit)
    module.EvaluationTransport = ParentRequestTransport
    module.CollaborationEvidence = TriggerCollaborationEvidence
    module.collaboration_metrics = collaboration_metrics
    return module


def host_files() -> dict[str, bytes]:
    return _foundation()._original_host_files()


def verify_archive(output: Path) -> dict:
    return foundation.verify_archive(output)


def verify_host(root: Path, inventory: dict) -> None:
    foundation.verify_host(root, inventory)


def unpack_host(output: Path, destination: Path) -> dict:
    return foundation.unpack_host(output, destination)


def probe_host(root: Path, output: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(root / "scripts/run_multi_agent_trigger.py"),
         "_probe", "--output", str(output)], cwd=root, text=True, capture_output=True,
        timeout=60, check=True,
    )
    return json.loads(result.stdout)


def freeze(output: Path) -> dict:
    return _foundation().freeze(output)


def validate(output: Path, *, host_root: Path | None = None) -> dict:
    manifest = _foundation()._original_validate(output, host_root=host_root)
    if "materials/reference-fixes.json" not in manifest["registrationFiles"]:
        raise ValueError("registration omits the private synthetic reference fixes")
    return manifest


def validate_offline_proof(output: Path, document: dict) -> None:
    _foundation()._original_validate_offline_proof(output, document)
    source = shared.read_json(output / "materials/source-manifest.json")
    references = output / "materials/reference-fixes.json"
    if (source.get("schemaVersion") != 1 or source.get("generation") != "trigger-v1"
            or source.get("origin") != "synthetic" or source.get("modelCalls") != 0
            or not references.is_file()):
        raise ValueError("new synthetic source manifest and private reference fixes are required")
    for key, path in (
        ("taskSha256", output / "tasks.json"),
        ("offlineProofSha256", output / "materials/offline-proof.json"),
        ("referenceFixesSha256", references),
    ):
        if source.get(key) != shared.sha(path.read_bytes()):
            raise ValueError("synthetic source material hash mismatch: " + key)


def require_committed_registration(output: Path, commit: str | None) -> str:
    return _foundation().require_committed_registration(output, commit)


def next_trial(output: Path, manifest: dict) -> str | None:
    return foundation.next_trial(output, manifest)


def dispatch(output: Path, mode: str, commit: str | None, *, trial: str | None = None,
             execution_host: Path | None = None) -> None:
    commit = require_committed_registration(output, commit)
    manifest = validate(output)
    following = next_trial(output, manifest)
    if mode == "child" and (not trial or trial != following):
        raise ValueError("child must execute exactly the next frozen schedule cell")
    if following is not None and any((output / "private" / following / name).exists()
                                    for name in ("execution-host.json", "transport.sqlite", "execution-start.json")):
        raise ValueError("next trial has durable evidence; recover it explicitly, never rerun")
    host = (output / "private/execution-host").resolve()
    if execution_host is None:
        unpack_host(output, host)
        argv = [sys.executable, "-I", "-B", str(host / "scripts/run_multi_agent_trigger.py"),
                mode, "--output", str(output), "--preregistered-commit", commit,
                "--execution-host", str(host)]
        if trial:
            argv += ["--trial", trial]
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        os.execve(sys.executable, argv, env)
        raise AssertionError("execve returned")
    if execution_host.resolve() != host or ROOT.resolve() != host:
        raise ValueError("execution entry point is outside the frozen host")
    validate(output, host_root=host)
    module = runner(output, commit=commit)
    runtime = module.frozen_runtime()
    if (module.production_sha() != manifest["productionSha256"]
            or module.harness_sha() != manifest["harnessSha256"]
            or module.route_hash(runtime) != manifest["routeSha256"]
            or module.resolved_transport_route(runtime) != manifest["resolvedTransportRouteSha256"]):
        raise ValueError("actual execution host or model route drifted")
    if mode == "child":
        assert_host_imports(host)
        _foundation().execution_host_receipt(output, trial, commit, manifest)
        module.execute_trial(output, trial)
    else:
        module.run_suite(output)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "freeze", "run", "child", "_probe"))
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--materials", type=Path)
    parser.add_argument("--trial")
    parser.add_argument("--preregistered-commit")
    parser.add_argument("--execution-host", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.mode == "prepare":
        if not args.tasks:
            parser.error("prepare requires --tasks")
        prepare(output, args.tasks.resolve(), plan=args.plan, materials=args.materials)
    elif args.mode == "freeze":
        freeze(output)
    elif args.mode == "_probe":
        module = runner(output)
        runtime = module.frozen_runtime()
        assert_host_imports(ROOT)
        print(json.dumps({"model": runtime["model"], "routeSha256": module.route_hash(runtime),
                          "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
                          "productionSha256": module.production_sha(), "harnessSha256": module.harness_sha(),
                          "environment": environment_fingerprint()}))
    else:
        dispatch(output, args.mode, args.preregistered_commit, trial=args.trial,
                 execution_host=args.execution_host)


if __name__ == "__main__":
    main()
