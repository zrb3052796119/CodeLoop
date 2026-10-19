#!/usr/bin/env python3
"""One preordered single/auto/explicit observation, not a generalization study.

Only the reviewed scripts entrypoint may prepare, freeze or run trials; copied
drafts remain limited to plan/offline tests.
No historical diagnostic result is reused as a comparison trial.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import zipfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "scripts/run_continuous_agent_diagnostic.py").is_file())
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import run_continuous_agent_diagnostic as continuous  # noqa: E402

shared, progress = continuous.shared, continuous.progress
ENTRY = ROOT / "scripts/run_continuous_three_arm.py"
PROTOCOL = "continuous-three-arm-pilot-v1"
SEED = 20261017
ARMS = ("single", "auto", "explicit")
CASE = continuous.CASE
LIMITS = dict(continuous.LIMITS)
ROLE_OVERRIDES = dict(continuous.ROLE_OVERRIDES)
HARNESS_FILES = (*continuous.HARNESS_FILES, ENTRY.name, "audit_continuous_three_arm.py")
DEFAULT = ROOT / "artifacts/requirement-review-2026-10-17/continuous-three-arm-pilot-v1"
COMMON_REQUIREMENT = progress.REGRESSION_REQUIREMENT.replace(
    "请在本次执行预算内先运行新测试并修复。", "请先运行新测试并修复。"
).replace("单列耗时，不增加你的1800秒执行预算。", "单列耗时；该时限只适用于执行结束后的外部验收。")
COMMON_EXECUTION = (
    "\n\n三组共同执行条件：不设置累计 token、调用次数、费用或执行墙钟上限。"
    "单次模型请求不设总时长上限，保留 120 秒空闲、30 秒连接和 131072 输出 token 的模型能力上限。"
    "权限、取消、真实验证和交付要求保持有效。\n"
)
EXPLICIT_RECIPE = progress.EXPLICIT_INSTRUCTION.replace("有界修复", "持续修复").replace(
    "所有模型角色、等待和重试共用本次预算。", "所有角色共用上述执行条件与真实计量。"
)
ANALYSIS_PLAN = """# Continuous three-arm pilot v1

One original retained task; three fresh independent baseline workspaces. Exactly
one single, auto and explicit trial, ordered before execution by seed 20261017,
serially. Auto means the model may choose task delegation; routing classifier
stays disabled in all arms. No rerun, replacement task or best-of selection.
No cumulative tokens/calls/cost/execution-wall cap. No request-total deadline;
131072 output, idle120/connect30 and normal cancellation remain. Same frozen
GLM-5.3-Flash route, temperature, production reasoning policy and provider controls.
Same shared ImplementationSpec, public fixtures/checks, regression requirement,
unchanged external judge and 120-second post-execution judgement window.
Protocol v3 public preflight must include the real host pytest machine report.

Only tools and the explicit workflow recipe differ. Same common prompt bytes.
Single cannot delegate; auto has task; explicit parent has only task/todo_write.
All failures, interruptions, cleanup and unknown provider usage remain. Tokens
with unknown usage and costs with unknown input/output/cache are null, with
separately labeled known lower bounds. Standard API equivalent pricing is not
Coding-plan actual incremental billing. Judge time is separate from execution.
Historical diagnostic costs/results remain R&D evidence outside these three rows.
This is descriptive observation on one previously-debugged task, not independent
generalization, a statistically estimated success rate, or proof of universal gains.
"""


def schedule() -> list[dict]:
    arms = list(ARMS)
    random.Random(SEED).shuffle(arms)
    return [{"case": CASE, "repeat": 1, "arm": arm, "id": f"{CASE}-r1-{arm}"} for arm in arms]


def common_prompt(prompt: str) -> str:
    return prompt + COMMON_REQUIREMENT + COMMON_EXECUTION


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm not in ARMS:
        raise ValueError("unknown comparison arm")
    return common_prompt(prompt) + (EXPLICIT_RECIPE if arm == "explicit" else "")


def static_contract() -> dict:
    return {"schemaVersion": 1, "protocolVersion": PROTOCOL, "phase": "pilot",
        "comparison": "one-task descriptive paired observation; no cumulative cap",
        "sameBudgetComparison": False, "seed": SEED, "schedule": schedule(),
        "arms": list(ARMS), "repeats": 1, "concurrency": 1,
        "limits": LIMITS, "parentMaxSteps": None, "boundedImplementationPhases": False,
        "roleTurnLimits": ROLE_OVERRIDES, "model": "glm-5.3-flash", "temperature": 0,
        "runner": "scripts/" + ENTRY.name, "harnessFiles": list(HARNESS_FILES),
        "publicPytestReportRequired": True,
        "commonRequirementSha256": shared.sha((COMMON_REQUIREMENT + COMMON_EXECUTION).encode()),
        "explicitRecipeSha256": shared.sha(EXPLICIT_RECIPE.encode()),
        # Preserve the external judge contract verbatim. Its historical
        # agentBudgetSeconds field is descriptive only; execution wallSeconds
        # is None above, and post-execution judgement uses totalSeconds=120.
        "judgementPolicy": progress.JUDGE_POLICY,
        "implementationCapabilities": progress.IMPLEMENTATION_CAPABILITIES,
        "publicVerificationFixturePolicy": progress.PUBLIC_FIXTURE_POLICY,
        "armIntervention": {"single": "shared local tools without task; delegation guide removed",
            "auto": "shared local tools including task; model chooses; router disabled",
            "explicit": "parent task/todo_write only; direct workflow recipe"}}


def validate_static(manifest: dict) -> None:
    for key, expected in static_contract().items():
        if key not in manifest or manifest[key] != expected:
            raise ValueError(f"comparison contract changed: {key}")


def require_integrated() -> None:
    if Path(__file__).resolve() != ENTRY.resolve():
        raise RuntimeError("draft_only: prepare/freeze/run/child require reviewed integration into scripts")


def _base():
    spec = importlib.util.spec_from_file_location("_continuous_three_arm_base",
                                                ROOT / "scripts/run_continuous_agent_diagnostic.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load continuous v3 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.PROTOCOL != "continuous-retained-task-diagnostic-v3" or module.LIMITS != LIMITS:
        raise ValueError("continuous v3 prerequisite changed; review the comparison contract")
    module.__file__ = str(ENTRY)
    module.ANALYSIS_PLAN, module.HARNESS_FILES = ANALYSIS_PLAN, HARNESS_FILES
    module.validate, module.prompt_for_arm = validate, prompt_for_arm
    return module


def transport_metrics(snapshot: dict) -> dict:
    result = shared.transport_metrics(snapshot)
    result["knownTokenLowerBound"] = result["totalTokens"]
    result["knownInputTokens"] = result["inputTokens"]
    result["knownOutputTokens"] = result["outputTokens"]
    result["knownCacheReadTokens"] = result["cacheReadTokens"]
    if not snapshot["usageComplete"]:
        result.update(inputTokens=None, outputTokens=None, totalTokens=None)
    if not snapshot["cacheUsageComplete"]:
        result["cacheReadTokens"] = None
    return result


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def baseline_files_sha256(document: dict) -> str:
    """Hash only the public file projection used by workspace construction."""
    matches = [case for case in document["cases"] if case["id"] == CASE]
    if len(matches) != 1:
        raise ValueError("comparison requires exactly one selected baseline")
    files = {**document.get("shared_files", {}), **matches[0]["files"]}
    projected = {}
    for name, contents in files.items():
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("invalid public baseline path")
        path = Path(name)
        relative = path.as_posix()
        if path.is_absolute() or ".." in path.parts or relative == "." or relative in projected:
            raise ValueError("ambiguous or escaping public baseline path")
        if not isinstance(contents, str):
            raise ValueError("public baseline contents must be UTF-8 text")
        projected[relative] = ["file", shared.sha(contents.encode("utf-8"))]
    if not projected:
        raise ValueError("empty public baseline")
    return shared.sha(_canonical(projected))


def _terminal_predecessor(output: Path, expected: dict) -> None:
    """Require the persisted outcome and the separate process-cleanup receipt."""
    try:
        row = shared.read_json(output / "trials" / (expected["id"] + ".json"))
        trial = output / "private" / expected["id"]
        started = shared.read_json(trial / "execution-start.json")
        cleanup = shared.read_json(trial / "process-cleanup.json")
        if (any(_canonical(row.get(key)) != _canonical(value) for key, value in expected.items())
                or type(row.get("success")) is not bool
                or "errorClass" not in row or (row["errorClass"] is not None and not isinstance(row["errorClass"], str))
                or type(row.get("durationSeconds")) not in (int, float)
                or not math.isfinite(row["durationSeconds"]) or row["durationSeconds"] < 0
                or type(row.get("modelCalls")) is not int or row["modelCalls"] < 0
                or type(started.get("epoch")) not in (int, float)
                or not math.isfinite(started["epoch"]) or started["epoch"] <= 0
                or "deadlineEpoch" not in started or started["deadlineEpoch"] is not None
                or _canonical(row.get("processCleanup")) != _canonical(cleanup)
                or cleanup.get("cleanupComplete") is not True
                or cleanup.get("phase") != "complete"
                or cleanup.get("freezeComplete") is not True
                or cleanup.get("killConfirmComplete") is not True
                or type(cleanup.get("rootPid")) is not int or cleanup["rootPid"] <= 1
                or any(cleanup.get(key) != [] for key in (
                    "survivorPids", "evidenceErrors", "processScanErrors", "lateDiscoveredPids",
                    "unconfirmedPids", "identityMismatches"))):
            raise ValueError("incomplete predecessor")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError("preordered predecessor lacks a valid terminal outcome and cleanup") from error


def assert_fresh_attempt(output: Path, trial_id: str) -> None:
    scheduled = schedule()
    order = [row["id"] for row in scheduled]
    if trial_id not in order:
        raise ValueError("unknown comparison trial")
    for prior in scheduled[:order.index(trial_id)]:
        _terminal_predecessor(output, prior)
    if (output / "trials" / (trial_id + ".json")).exists():
        raise ValueError("terminal trial cannot run again")
    trial = output / "private" / trial_id
    for name in ("transport.sqlite", "transport-summary.json", "execution-start.json", "execution.json", "transcript.json"):
        if (trial / name).exists():
            raise ValueError("prior trial evidence exists; recover/archive without a new model call")


def runner(output: Path):
    require_integrated()
    module = _base().runner(output)
    module.transport_metrics = transport_metrics
    original = module.execute_trial
    def execute(trial_output, trial_id):
        assert_fresh_attempt(Path(trial_output), trial_id)
        return original(trial_output, trial_id)
    module.execute_trial = execute
    return module


def request_policy(runtime: dict) -> dict:
    """Whitelist non-secret controls; freeze code for role-specific overrides."""
    from minicode.model_registry import create_model_adapter
    from minicode.tooling import ToolRegistry
    registry = ToolRegistry([])
    adapter = create_model_adapter(runtime["model"], tools=registry, runtime=runtime)
    resolved = adapter.runtime
    keys = ("maxOutputTokens", "temperature", "enableThinking", "requireReasoningContentReplay",
            "modelTimeoutSeconds", "modelMaxRetries", "subagentRoutingEnabled")
    provider = resolved.get("_openrouter_params", {})
    controls = {key: provider.get(key) for key in ("reasoning_effort", "temperature", "top_p",
                                                  "presence_penalty", "frequency_penalty", "seed")}
    registry.dispose()
    return {"runtime": {key: resolved.get(key) for key in keys}, "providerControls": controls,
        "supportedReasoningEfforts": list(getattr(adapter, "supported_reasoning_efforts", ())),
        "unspecifiedReasoningEffort": "provider default; no invented high value",
        "actualRequestMetadata": ["reasoningEffort", "maxOutputTokens", "requestDeadlineEpoch"],
        "policySourceSha256": {name: shared.sha((ROOT / name).read_bytes()) for name in (
            "minicode/agent_loop.py", "minicode/implementation_phases.py", "minicode/openai_adapter.py",
            "minicode/model_registry.py", "scripts/evaluation_transport.py")}}


def _current_contract(output: Path):
    base = _base()
    document, provenance, raw = base._prepared_document(output)
    baseline_sha = baseline_files_sha256(document)
    if provenance.get("baselineFilesSha256") != baseline_sha:
        raise ValueError("prepared public baseline projection changed")
    module = runner(output)
    with base.role_overrides({"roleTurnLimits": ROLE_OVERRIDES}):
        runtime = base._execution_contract(module)
    policy = request_policy(runtime)
    values = {**static_contract(), "price": module.PRICE,
        "priceMeaning": "standard API equivalent CNY; not actual Coding-plan charge",
        "requestPolicy": policy, "requestPolicySha256": shared.sha(json.dumps(policy, sort_keys=True).encode()),
        "routeSha256": module.route_hash(runtime),
        "resolvedTransportRouteSha256": module.resolved_transport_route(runtime),
        "taskSha256": shared.sha(raw), "sourceTaskSha256": provenance["sourceTaskSha256"],
        "baselineFilesSha256": baseline_sha,
        "sourceManifestSha256": provenance["sourceManifestSha256"], "sourceSuite": provenance["sourceSuite"],
        "productionSha256": module.production_sha(), "harnessSha256": module.harness_sha(),
        "analysisPlanSha256": shared.sha((output / "analysis-plan.md").read_bytes()),
        "publicVerificationFixtures": progress.public_fixture_manifest(document)}
    return values, document, runtime


def prepare(output: Path, source: Path) -> None:
    require_integrated()
    base = _base()
    base.prepare(output, source)
    document, provenance, _raw = base._prepared_document(output)
    shared.write_json(output / "source-provenance.json",
                      {**provenance, "baselineFilesSha256": baseline_files_sha256(document)})


def freeze(output: Path) -> None:
    require_integrated()
    if (output / "manifest.json").exists() or (output / "frozen-code.zip").exists():
        raise ValueError("never replace frozen comparison evidence")
    contract, document, runtime = _current_contract(output)
    preflight = progress.run_public_preflight(document, output / "public-preflight", runtime=runtime,
                                              require_pytest_report=True)
    if not preflight["passed"]:
        raise ValueError("public baseline/machine-report preflight failed; execution blocked")
    preflight_sha = shared.sha((output / "public-preflight/receipt.json").read_bytes())
    progress.validate_public_preflight(document, output / "public-preflight/receipt.json",
                                      preflight_sha, require_pytest_report=True)
    archive = output / "frozen-code.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "minicode").rglob("*.py")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in HARNESS_FILES:
            bundle.write(ROOT / "scripts" / name, "scripts/" + name)
        bundle.write(output / "analysis-plan.md", "analysis-plan.md")
    shared.write_json(output / "manifest.json", {**contract, "createdAt": datetime.now(UTC).isoformat(),
        "frozenCodeArchiveSha256": shared.sha(archive.read_bytes()),
        "publicPreflightSha256": preflight_sha})


def validate(output: Path) -> dict:
    require_integrated()
    manifest = shared.read_json(output / "manifest.json")
    validate_static(manifest)
    expected, document, _runtime = _current_contract(output)
    expected["frozenCodeArchiveSha256"] = shared.sha((output / "frozen-code.zip").read_bytes())
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"frozen comparison changed: {key}")
    progress.validate_public_preflight(document, output / "public-preflight/receipt.json",
        manifest.get("publicPreflightSha256"), require_pytest_report=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare", "freeze", "run", "child"))
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--source", type=Path, default=progress.SOURCE)
    parser.add_argument("--trial")
    args = parser.parse_args()
    if args.mode == "plan":
        print(json.dumps(static_contract(), ensure_ascii=False, indent=2))
        return
    require_integrated()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output, args.source.resolve())
    elif args.mode == "freeze":
        freeze(output)
    else:
        validate(output)
        module = runner(output)
        if args.mode == "child":
            module.execute_trial(output, args.trial)
        else:
            module.run_suite(output)


if __name__ == "__main__":
    main()
