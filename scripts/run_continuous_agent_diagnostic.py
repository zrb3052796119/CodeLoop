#!/usr/bin/env python3
"""One retained-task diagnostic with metering but no cumulative execution cap.

This is not a same-budget comparison. The existing isolated runner, public
fixtures, scope checks, durable receipts, cleanup and common judge are reused.
Prepare and freeze never call a model. Historical experiments are not changed.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import sys
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import run_implementation_progress_live as progress  # noqa: E402

shared = progress.shared
DEFAULT = ROOT / "artifacts/multi-agent-unbounded-2026-10-17/diagnostic-v2"
PROTOCOL = "continuous-retained-task-diagnostic-v3"
CASE = "real-remediation-context"
SCHEDULE = [{"case": CASE, "repeat": 1, "arm": "explicit", "id": CASE + "-r1-explicit"}]
LIMITS = {
    "maxTokens": None, "maxModelCalls": None, "maxCostUsd": None, "wallSeconds": None,
    "maxOutputTokens": 131072, "requestTimeoutSeconds": None,
    "idleTimeoutSeconds": 120, "connectTimeoutSeconds": 30,
}
ROLE_OVERRIDES = {role: 0 for role in ("explore", "plan", "general")}
HARNESS_FILES = (*progress.SUCCESSOR_HARNESS_FILES, "run_continuous_agent_diagnostic.py")
ANALYSIS_PLAN = """# Continuous execution diagnostic

This single explicit-workflow trial reuses the historical real-remediation-context
task. It is a diagnostic, not a same-budget or independent generalization study.
No cumulative token, physical-call, monetary or execution-wall-clock ceiling is
set. Every physical request remains metered, including failures and unknown usage.
There is no total per-request deadline. The GLM-5.3-Flash output ceiling is
131072 tokens; 120 seconds idle and 30 seconds connect remain. The diagnostic
clears MINICODE_MODEL_TIMEOUT for its process tree, restoring the caller's value
on exit. Provider context limits, permission scope, cancellation,
current-candidate checks, formal verification, review and writeback gates remain.
The parent has no max_steps ceiling; role environment overrides are explicitly 0.
There is no automatic retry of a trial that already has durable call evidence.

The original task and authorized files/checks remain unchanged. The common new
regression-test requirement and shared external judge are reused. Public baseline
preflight must pass before execution, including the host-owned pytest machine
report and exception-classification plugin under the actual scope guards and
sandbox. A missing report blocks paid execution. The external judge's existing 120-second
limit and bounded process cleanup concern post-execution validation only.
The sole outcome is retained with all elapsed time, calls, usage, check failures,
workflow/Executor continuity and delivered changes. No same-budget benefit claim
can be made from this diagnostic or by merging it into historical results.
"""


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm != "explicit":
        raise ValueError("continuous diagnostic has only the explicit workflow trial")
    regression = progress.REGRESSION_REQUIREMENT.replace(
        "请在本次执行预算内先运行新测试并修复。", "请先运行新测试并修复。"
    ).replace(
        "单列耗时，不增加你的1800秒执行预算。",
        "单列耗时；该时限只适用于执行结束后的外部验收。",
    )
    recipe = progress.EXPLICIT_INSTRUCTION.replace("有界修复", "持续修复").replace(
        "所有模型角色、等待和重试共用本次预算。",
        "本次诊断不设置累计 token、调用次数、费用或执行墙钟上限。"
        "单次模型请求不设总时长上限，保留 120 秒空闲、30 秒连接和 131072 输出 token 的模型能力上限。",
    )
    return prompt + regression + recipe


@contextmanager
def role_overrides(manifest: dict):
    if manifest.get("roleTurnLimits") != ROLE_OVERRIDES:
        raise ValueError("diagnostic role overrides changed")
    from minicode.subagent_types import resolve_max_turns

    values = {f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS": "0" for role in ROLE_OVERRIDES}
    values.update({name: "0" for name in (
        "MINI_CODE_TURN_BUDGET_TOKENS", "MINI_CODE_TURN_BUDGET_MODEL_CALLS",
        "MINI_CODE_TURN_BUDGET_COST_USD", "MINICODE_TASK_TIMEOUT_SECONDS",
    )})
    with patch.dict(os.environ, values):
        # Runtime None must not silently inherit a caller's old request timer.
        # The context restores the original environment, including this key.
        os.environ.pop("MINICODE_MODEL_TIMEOUT", None)
        if any(resolve_max_turns(role) is not None for role in ROLE_OVERRIDES):
            raise ValueError("production does not support continuous role execution")
        yield


def runner(output: Path):
    # Reuse the successor's public-fixture/ImplementationSpec/common-judge
    # closures in a private module. Never patch its imported singleton or the
    # historical runner; child processes enter this independent CLI.
    spec = importlib.util.spec_from_file_location(
        "_continuous_progress_runner", ROOT / "scripts/run_implementation_progress_live.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared implementation runner")
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    isolated.validate_successor = validate
    isolated.frozen_role_turn_overrides = role_overrides
    isolated.prompt_for_arm = prompt_for_arm
    isolated.SUCCESSOR_HARNESS_FILES = HARNESS_FILES
    module = isolated.runner(output)
    module.__file__ = str(Path(__file__).resolve())
    module.LIMITS = dict(LIMITS)
    wait_for_trial = module.wait_trial_process

    def wait_with_cleanup(proc, trial_root, **kwargs):
        try:
            return wait_for_trial(proc, trial_root, **kwargs)
        except BaseException:
            # With no execution clock, manual cancellation must still stop
            # owned children before control returns to the operator.
            cleanup = module.stop_trial(proc.pid, trial_root / "processes.jsonl")
            shared.write_json(trial_root / "process-cleanup.json", cleanup)
            proc.wait(timeout=10)
            raise

    module.wait_trial_process = wait_with_cleanup
    return module


def prepare(output: Path, source: Path = progress.SOURCE) -> None:
    progress.prepare(output, "engineering", source)
    # The frozen task document is untouched; only the study plan differs.
    (output / "analysis-plan.md").write_text(ANALYSIS_PLAN, encoding="utf-8")


def _prepared_document(output: Path):
    provenance = shared.read_json(output / "source-provenance.json")
    raw = (output / "tasks.json").read_bytes()
    source = Path(provenance["sourceDirectory"])
    original_raw = (source / "tasks.json").read_bytes()
    if (shared.sha(raw) != provenance["preparedTaskSha256"]
            or shared.sha(original_raw) != provenance["sourceTaskSha256"]
            or shared.sha((source / "manifest.json").read_bytes()) != provenance["sourceManifestSha256"]
            or provenance["phase"] != "engineering"
            or provenance["selectedCases"] != list(progress.SMALL_CASES)
            or (output / "analysis-plan.md").read_text() != ANALYSIS_PLAN):
        raise ValueError("diagnostic task provenance or plan changed")
    original = json.loads(original_raw)
    original["cases"] = [next(case for case in original["cases"] if case["id"] == name)
                         for name in progress.SMALL_CASES]
    document = json.loads(raw)
    if document != original:
        raise ValueError("diagnostic tasks differ from the frozen historical source")
    return document, provenance, raw


def _execution_contract(module):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn

    signature = inspect.signature(run_agent_turn)
    if (signature.parameters["max_steps"].default is not None
            or signature.parameters["bounded_implementation_phases"].default is not False):
        raise ValueError("production parent still has an implicit step/phase ceiling")
    runtime = module.frozen_runtime()
    budget = AgentTurnBudget.from_runtime(runtime).snapshot()
    if any(value is not None for value in (
            budget.limit_total_tokens, budget.limit_model_calls, budget.limit_cost_usd)):
        raise ValueError("diagnostic production budget is not unlimited")
    if runtime["model"] != "glm-5.3-flash":
        raise ValueError("retained price card only applies to glm-5.3-flash")
    if (runtime.get("maxOutputTokens") != LIMITS["maxOutputTokens"]
            or "modelTimeoutSeconds" not in runtime
            or runtime["modelTimeoutSeconds"] is not None):
        raise ValueError("diagnostic request policy did not reach the runtime")
    return runtime


def freeze(output: Path) -> None:
    if (output / "manifest.json").exists() or (output / "frozen-code.zip").exists():
        raise ValueError("Use a new diagnostic directory; never overwrite frozen evidence")
    document, provenance, raw = _prepared_document(output)
    module = runner(output)
    with role_overrides({"roleTurnLimits": ROLE_OVERRIDES}):
        runtime = _execution_contract(module)
    preflight = progress.run_public_preflight(document, output / "public-preflight", runtime=runtime,
                                              require_pytest_report=True)
    if not preflight["passed"]:
        raise ValueError("public baseline preflight failed; paid execution is blocked")
    archive = output / "frozen-code.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "minicode").rglob("*.py")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in HARNESS_FILES:
            bundle.write(ROOT / "scripts" / name, "scripts/" + name)
        bundle.write(output / "analysis-plan.md", "analysis-plan.md")
    manifest = {
        "schemaVersion": 1, "protocolVersion": PROTOCOL, "phase": "diagnostic",
        "comparison": "non-comparative; cumulative execution limits removed",
        "sameBudgetComparison": False, "createdAt": datetime.now(UTC).isoformat(),
        "schedule": SCHEDULE, "arms": ["explicit"], "repeats": 1, "concurrency": 1,
        "limits": LIMITS, "parentMaxSteps": None, "boundedImplementationPhases": False,
        "roleTurnLimits": ROLE_OVERRIDES, "roleTurnPolicy": "zero means no cumulative role ceiling",
        "model": runtime["model"], "temperature": 0, "price": module.PRICE,
        "routeSha256": module.route_hash(runtime),
        "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
        "taskSha256": shared.sha(raw), "sourceTaskSha256": provenance["sourceTaskSha256"],
        "sourceManifestSha256": provenance["sourceManifestSha256"], "sourceSuite": provenance["sourceSuite"],
        "productionSha256": module.production_sha(), "harnessSha256": module.harness_sha(),
        "frozenCodeArchiveSha256": shared.sha(archive.read_bytes()),
        "analysisPlanSha256": shared.sha((output / "analysis-plan.md").read_bytes()),
        "harnessFiles": list(HARNESS_FILES), "runner": "scripts/run_continuous_agent_diagnostic.py",
        "judgementPolicy": progress.JUDGE_POLICY,
        "implementationCapabilities": progress.IMPLEMENTATION_CAPABILITIES,
        "publicVerificationFixturePolicy": progress.PUBLIC_FIXTURE_POLICY,
        "publicVerificationFixtures": progress.public_fixture_manifest(document),
        "publicPreflightSha256": shared.sha((output / "public-preflight/receipt.json").read_bytes()),
        "publicPytestReportRequired": True,
        "guardian": "30 second setup only; no cumulative execution deadline; cleanup before the unchanged external judge",
        "tokenBudgetSemantics": "Meter every request and preserve unknown usage; no cumulative admission ceiling",
    }
    shared.write_json(output / "manifest.json", manifest)


def validate(output: Path) -> dict:
    manifest = shared.read_json(output / "manifest.json")
    if manifest.get("protocolVersion") != PROTOCOL:
        raise ValueError("historical diagnostic is audit-only; use verify-frozen, not current execution")
    document, provenance, raw = _prepared_document(output)
    module = runner(output)
    with role_overrides(manifest):
        runtime = _execution_contract(module)
    expected = {
        "schemaVersion": 1, "protocolVersion": PROTOCOL, "phase": "diagnostic",
        "sameBudgetComparison": False, "schedule": SCHEDULE, "arms": ["explicit"],
        "limits": LIMITS, "parentMaxSteps": None, "boundedImplementationPhases": False,
        "roleTurnLimits": ROLE_OVERRIDES, "model": runtime["model"], "temperature": 0,
        "routeSha256": module.route_hash(runtime),
        "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
        "taskSha256": shared.sha(raw), "sourceTaskSha256": provenance["sourceTaskSha256"],
        "sourceManifestSha256": provenance["sourceManifestSha256"],
        "productionSha256": module.production_sha(), "harnessSha256": module.harness_sha(),
        "frozenCodeArchiveSha256": shared.sha((output / "frozen-code.zip").read_bytes()),
        "analysisPlanSha256": shared.sha((output / "analysis-plan.md").read_bytes()),
        "harnessFiles": list(HARNESS_FILES), "price": module.PRICE,
        "judgementPolicy": progress.JUDGE_POLICY,
        "implementationCapabilities": progress.IMPLEMENTATION_CAPABILITIES,
        "publicVerificationFixturePolicy": progress.PUBLIC_FIXTURE_POLICY,
        "publicVerificationFixtures": progress.public_fixture_manifest(document),
        "publicPytestReportRequired": True,
    }
    for key, value in expected.items():
        if key not in manifest or json.dumps(manifest[key], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ValueError(f"frozen diagnostic contract changed: {key}")
    progress.validate_public_preflight(document, output / "public-preflight/receipt.json",
                                      manifest.get("publicPreflightSha256"), require_pytest_report=True)
    return manifest


def verify_frozen(output: Path) -> dict:
    """Verify archived bytes without executing archived code or loading a route.

    Old protocols remain independently hash-checkable after current production
    changes. This checks evidence integrity, not historical runtime equivalence
    or the correctness of any model result.
    """
    manifest = shared.read_json(output / "manifest.json")
    protocol = manifest.get("protocolVersion")
    limits = dict(LIMITS)
    if protocol == "continuous-retained-task-diagnostic-v1":
        limits.update(maxOutputTokens=8192, requestTimeoutSeconds=300)
    elif protocol not in ("continuous-retained-task-diagnostic-v2", PROTOCOL):
        raise ValueError("unknown diagnostic protocol")
    if json.dumps(manifest.get("limits"), sort_keys=True) != json.dumps(limits, sort_keys=True):
        raise ValueError("archived diagnostic request policy changed")
    paths = {
        "frozenCodeArchiveSha256": output / "frozen-code.zip",
        "taskSha256": output / "tasks.json",
        "analysisPlanSha256": output / "analysis-plan.md",
        "publicPreflightSha256": output / "public-preflight/receipt.json",
    }
    actual = {key: shared.sha(path.read_bytes()) for key, path in paths.items()}
    with zipfile.ZipFile(paths["frozenCodeArchiveSha256"]) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archived path")
        production = sorted(name for name in names if name.startswith("minicode/") and name.endswith(".py"))
        harness = manifest.get("harnessFiles")
        if not production or not isinstance(harness, list) or not harness or any(
                not isinstance(name, str) or Path(name).name != name for name in harness):
            raise ValueError("invalid archived source inventory")
        actual["productionSha256"] = shared.sha(b"".join(
            name.encode() + b"\0" + bundle.read(name) for name in production))
        actual["harnessSha256"] = shared.sha(b"".join(
            name.encode() + b"\0" + bundle.read("scripts/" + name) for name in harness))
        if bundle.read("analysis-plan.md") != paths["analysisPlanSha256"].read_bytes():
            raise ValueError("archived analysis plan changed")
    for key, value in actual.items():
        if manifest.get(key) != value:
            raise ValueError(f"archived diagnostic hash mismatch: {key}")
    return {"valid": True, "protocolVersion": protocol, "auditOnly": True,
            "verifiedHashes": sorted(actual)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "freeze", "run", "child", "verify-frozen"))
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--source", type=Path, default=progress.SOURCE)
    parser.add_argument("--trial")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output, args.source.resolve())
    elif args.mode == "freeze":
        freeze(output)
    elif args.mode == "verify-frozen":
        print(json.dumps(verify_frozen(output), sort_keys=True))
    else:
        validate(output)
        module = runner(output)
        if args.mode == "child":
            if args.trial != SCHEDULE[0]["id"]:
                raise ValueError("unknown diagnostic trial")
            module.execute_trial(output, args.trial)
        else:
            module.run_suite(output)


if __name__ == "__main__":
    main()
