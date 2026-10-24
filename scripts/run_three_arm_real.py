#!/usr/bin/env python3
"""Frozen three-arm real-repository evaluation with streamed provider receipts.

This harness changes no production code. All calls pass through the production
runtime, adapter, tool dispatcher and (when selected) workflow scheduler.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import shlex
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from evaluation_transport import EvaluationTransport, TransportConfig, SharedLedger, route_fingerprint  # noqa: E402
from three_arm_protocol import ARMS, CollaborationEvidence, collaboration_metrics, prompt_for_arm  # noqa: E402
from three_arm_scope import VerificationScope, ScopeDenied, verification_environment  # noqa: E402
from three_arm_scope_metadata import changed_paths  # noqa: E402
from three_arm_processes import ProcessEvidence, stop_trial  # noqa: E402

DEFAULT = ROOT / "artifacts/three-arm-real-2026-10-13"
LOCAL_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "file_tree",
        "file_line_count",
        "get_ast_info",
        "find_symbols",
        "find_references",
        "grep_files",
        "edit_file",
        "write_file",
        "run_command",
        "test_runner",
        "diff_viewer",
        "todo_write",
        "task",
        "subagent_note_read",
        "subagent_note_write",
    }
)
LIMITS = {
    "maxTokens": 500000,
    "maxModelCalls": 40,
    "maxCostUsd": "5",
    "wallSeconds": 1800,
    "maxOutputTokens": 8192,
    "requestTimeoutSeconds": 300,
    "idleTimeoutSeconds": 120,
    "connectTimeoutSeconds": 30,
}
PRICE = {
    "currency": "CNY",
    "perMillionInput": 0.8,
    "perMillionOutput": 2.8,
    "perMillionCacheRead": 0.23,
    "source": "https://open.bigmodel.cn/pricing",
    "retrievedOn": "2026-10-13",
    "basis": "standard API-equivalent list-price estimate for coding subscription route; not actual incremental account charges",
    "codingRoute": True,
    "promotionNote": "Official FAQ also mentions half-price promotion; no account-specific discount assumed.",
}
STUDY = None


def configure_study(output):
    """Load an optional registered task design in this runner process only."""
    global STUDY, LIMITS, PRICE, DEFAULT, HARNESS_FILES
    path = output / "study-design.json"
    if not path.exists():
        return
    STUDY = read_json(path)
    if STUDY["arms"] != ["single", "auto"]:
        raise ValueError("registered study requires single/auto arms")
    DEFAULT = output
    LIMITS = {key: STUDY["limits"][key] for key in (
        "maxTokens", "maxModelCalls", "maxCostUsd", "wallSeconds",
        "requestTimeoutSeconds", "idleTimeoutSeconds", "connectTimeoutSeconds",
    )}
    LIMITS["maxOutputTokens"] = STUDY["runtime"]["maxOutputTokens"]
    PRICE = {"known": False, "currency": None, "reason": "configured route price unknown"}
    HARNESS_FILES = tuple(dict.fromkeys((*HARNESS_FILES, "implementation_judge.py")))


def prepare_study(output, design_path, analysis_plan):
    """Materialize verified public baselines and private oracles; never call a model."""
    if output.exists():
        raise ValueError("study output must be new; never replace existing evidence")
    design = read_json(design_path)
    if design["arms"] != ["single", "auto"] or design["runtime"]["model"] != "glm-5.3-flash":
        raise ValueError("unexpected registered arms or model")
    ids = [case["id"] for case in design["cases"]]
    schedule = design["schedule"]
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", value) for value in [*ids, *(t["id"] for t in schedule)]):
        raise ValueError("unsafe case or trial identity")
    if len(set(ids)) != len(ids) or len({t["id"] for t in schedule}) != len(schedule):
        raise ValueError("duplicate case or trial")
    expected = {(case, arm, repeat) for case in ids for arm in design["arms"]
                for repeat in range(1, design["repeats"] + 1)}
    if {(t["case"], t["arm"], t["repeat"]) for t in schedule} != expected or len(schedule) != len(expected):
        raise ValueError("schedule does not cover the registered cells exactly once")
    tasks, support = [], {}
    output.mkdir(parents=True, mode=0o700)
    for case in design["cases"]:
        files = {}
        for name, expected_sha in case["sourceInventories"]["baseline"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or not name.startswith("minicode/") or relative.suffix != ".py":
                raise ValueError("public baseline must contain only relative minicode Python sources")
            content = (Path(case["baselineSourceDirectory"]) / relative).read_bytes()
            if sha(content) != expected_sha:
                raise ValueError("baseline source hash mismatch")
            files[name] = content.decode()
        oracle = Path(case["acceptance"]["oraclePath"]).read_bytes()
        if sha(oracle) != case["acceptance"]["oracleSha256"]:
            raise ValueError("private oracle hash mismatch")
        oracle_source = oracle.decode()
        for index, (directory, inventory) in enumerate(case.get("oracleDependencyDirectories", {}).items()):
            target_root = output / "oracle-support" / case["id"] / str(index)
            for name, expected_sha in inventory.items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
                    raise ValueError("unsafe oracle dependency path")
                content = (Path(directory) / relative).read_bytes()
                if sha(content) != expected_sha:
                    raise ValueError("oracle dependency hash mismatch")
                target = target_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                support[target.relative_to(output).as_posix()] = expected_sha
            oracle_source = oracle_source.replace(repr(directory), repr(str(target_root)))
        readonly = case["category"] == "read"
        tasks.append({"id": case["id"], "category": case["category"], "prompt": case["prompt"],
                      "files": files, "authorized_paths": case["authorized_paths"],
                      "oracle_source": oracle_source, "generatedRegressionRequired": not readonly,
                      "verification_argv": [] if readonly else ["python3", "-m", "pytest", "-q", "tests/test_eval_regression.py"]})
    write_json(output / "tasks.json", {"shared_files": {}, "cases": tasks})
    write_json(output / "study-design.json", design)
    write_json(output / "oracle-support-hashes.json", support)
    shutil.copyfile(analysis_plan, output / "analysis-plan.md")
    print(json.dumps({"prepared": True, "tasks": len(tasks), "trials": len(schedule), "providerCalls": 0}))


def validate_study_files(output, manifest):
    if STUDY is None:
        return
    for field, name in (("studyDesignSha256", "study-design.json"),
                        ("oracleSupportSha256", "oracle-support-hashes.json")):
        if manifest[field] != sha((output / name).read_bytes()):
            raise RuntimeError("frozen study material changed")
    expected_support = {}
    tasks = read_json(output / "tasks.json")
    registered = {case["id"]: case for case in STUDY["cases"]}
    if tasks.get("shared_files") or {c["id"] for c in tasks["cases"]} != set(registered) or len(tasks["cases"]) != len(registered):
        raise RuntimeError("prepared task membership differs from registration")
    for task in tasks["cases"]:
        case = registered[task["id"]]
        if any(task[field] != case[field] for field in ("prompt", "category", "authorized_paths")):
            raise RuntimeError("prepared task contract differs from registration")
        inventory = {name: sha(content.encode()) for name, content in task["files"].items()}
        if inventory != case["sourceInventories"]["baseline"]:
            raise RuntimeError("prepared baseline differs from registration")
        readonly = case["category"] == "read"
        argv = [] if readonly else ["python3", "-m", "pytest", "-q", "tests/test_eval_regression.py"]
        if task["generatedRegressionRequired"] != (not readonly) or task["verification_argv"] != argv:
            raise RuntimeError("prepared verification policy differs from registration")
        oracle = task["oracle_source"]
        for index, (directory, inventory) in enumerate(case.get("oracleDependencyDirectories", {}).items()):
            target_root = output / "oracle-support" / case["id"] / str(index)
            oracle = oracle.replace(repr(str(target_root)), repr(directory))
            expected_support.update({(target_root / name).relative_to(output).as_posix(): digest
                                     for name, digest in inventory.items()})
        if sha(oracle.encode()) != case["acceptance"]["oracleSha256"]:
            raise RuntimeError("prepared oracle differs from registration")
    support = read_json(output / "oracle-support-hashes.json")
    if support != expected_support:
        raise RuntimeError("prepared oracle support differs from registration")
    for name, expected_sha in support.items():
        if sha((output / name).read_bytes()) != expected_sha:
            raise RuntimeError("frozen private oracle source changed")


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def production_sha():
    return sha(
        b"".join(
            p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes()
            for p in sorted((ROOT / "minicode").rglob("*.py"))
        )
    )


def route_hash(runtime):
    from minicode.model_registry import build_provider_config

    config = build_provider_config(runtime["model"], runtime)
    return sha(
        json.dumps(
            {
                "model": config.model,
                "provider": config.provider.value,
                "baseUrl": config.base_url,
            },
            sort_keys=True,
        ).encode()
    )


def frozen_runtime():
    from minicode.config import load_runtime_config

    runtime = load_runtime_config(str(ROOT))
    runtime.update(
        subagentRoutingEnabled=False,
        modelMaxRetries=0,
        modelTimeoutSeconds=LIMITS["requestTimeoutSeconds"],
        maxOutputTokens=LIMITS["maxOutputTokens"],
        temperature=0,
        reflectionSynthesizerMode="rule",
        memoryHybridEnabled=False,
        allowRemoteMemoryEmbedding=False,
        recoveryLessonShadowEnabled=False,
        agentTurnBudget={
            k: LIMITS[k] for k in ("maxTokens", "maxModelCalls", "maxCostUsd")
        },
        intentSemanticClassifier={"enabled": False},
        skillCandidateReranker={"enabled": False},
    )
    for key in (
        "agentTurnBudgetTokens",
        "agentTurnBudgetModelCalls",
        "agentTurnBudgetCostUsd",
    ):
        runtime.pop(key, None)
    if STUDY is not None:
        registered = STUDY["runtime"]
        if runtime["model"] != registered["model"]:
            raise ValueError("configured model differs from registered model")
        runtime.update(subagentModels={}, subagentRoutingEnabled=False)
    return runtime


HARNESS_FILES = (
    "run_three_arm_real.py", "evaluation_transport.py", "three_arm_protocol.py",
    "three_arm_scope.py", "three_arm_scope_metadata.py", "three_arm_processes.py",
    "run_north_star_live.py", "run_multi_agent_cost_live.py", "analyze_three_arm_real.py",
)


def harness_sha():
    return sha(b"".join(name.encode() + b"\0" + (ROOT / "scripts" / name).read_bytes() for name in HARNESS_FILES))


def resolved_transport_route(runtime):
    from minicode.model_registry import create_model_adapter
    from minicode.tooling import ToolRegistry
    adapter = create_model_adapter(runtime["model"], tools=ToolRegistry([]), runtime=runtime)
    return route_fingerprint(adapter.runtime)


def transport_metrics(snapshot):
    receipts = snapshot["requests"]
    known = [r for r in receipts if r.get("usage", {}).get("source") == "provider"]
    lower = 0.0
    for row in known if PRICE.get("known", True) else []:
        usage = row["usage"]
        inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        cached = usage.get("cache_read_tokens")
        # Unknown cache split: all cached is a monetary lower bound, not zero.
        cached_for_lower = inputs if cached is None else cached
        lower += ((inputs - cached_for_lower) * PRICE["perMillionInput"] + cached_for_lower * PRICE["perMillionCacheRead"] + outputs * PRICE["perMillionOutput"]) / 1e6
    complete = snapshot["usageComplete"] and snapshot["cacheUsageComplete"]
    task_calls = [c for row in receipts for c in row.get("toolCalls", []) if c["name"] == "task"]
    return {
        "modelCalls": snapshot["started"], "usageComplete": complete,
        "admissionRejectedCount": snapshot.get("admissionRejectedCount", 0),
        "tokenUsageComplete": snapshot["usageComplete"], "cacheUsageComplete": snapshot["cacheUsageComplete"],
        "unknownUsageCalls": snapshot["unknownUsageCalls"],
        "inputTokens": snapshot["inputTokensKnown"], "outputTokens": snapshot["outputTokensKnown"],
        "totalTokens": snapshot["inputTokensKnown"] + snapshot["outputTokensKnown"],
        "cacheReadTokens": snapshot["cacheReadTokensKnown"],
        "estimatedCostCny": lower if complete and PRICE.get("known", True) else None,
        "knownCostLowerBoundCny": lower if known and PRICE.get("known", True) else None,
        "estimatedCostUsd": None,
        "currencyPriceKnown": PRICE.get("known", True),
        "delegationAttempts": len(task_calls),
        "delegationTypes": [c["arguments"].get("agent_type") if isinstance(c.get("arguments"), dict) else None for c in task_calls],
        "failedCalls": snapshot["failed"], "inFlightCalls": snapshot["inFlight"],
        "ledgerDamaged": False,
    }


def freeze(output, repeats):
    from minicode.subagent_types import resolve_max_turns
    if (output / "manifest.json").exists():
        raise ValueError("Manifest exists; never overwrite an already frozen experiment.")
    runtime = frozen_runtime()
    if runtime["model"] != "glm-5.3-flash":
        raise ValueError("Frozen price card is only valid for glm-5.3-flash.")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    tasks_path = output / "tasks.json"
    tasks = read_json(tasks_path)["cases"]
    schedule = []
    rng = random.Random(20261013)
    # Full six-permutation blocks, then cyclic rotations: every arm occupies
    # each ordinal position within one block of every other arm.
    block_count = len(tasks) * repeats
    all_orders = list(itertools.permutations(ARMS))
    orders = all_orders * (block_count // 6)
    remainder = block_count % 6
    tail = [ARMS[i:] + ARMS[:i] for i in range(3)]
    reverse = tuple(reversed(ARMS))
    tail += [reverse[i:] + reverse[:i] for i in range(3)]
    orders += tail[:remainder]
    rng.shuffle(orders)
    block_index = 0
    for repeat in range(1, repeats + 1):
        ordered = list(tasks)
        rng.shuffle(ordered)
        for case in ordered:
            arms = orders[block_index]
            block_index += 1
            for arm in arms:
                schedule.append(
                    {
                        "case": case["id"],
                        "repeat": repeat,
                        "arm": arm,
                        "id": f"{case['id']}-r{repeat}-{arm}",
                    }
                )
    if STUDY is not None:
        from minicode.subagent_types import MAX_AGENT_DEPTH
        if {role: resolve_max_turns(role) for role in ("explore", "plan", "general")} != STUDY["runtime"]["roleMaxTurns"]:
            raise ValueError("registered role limits differ from current production defaults")
        if MAX_AGENT_DEPTH != STUDY["runtime"]["maxAgentDepth"]:
            raise ValueError("registered depth differs from production default")
        schedule = deepcopy(STUDY["schedule"])
        repeats = STUDY["repeats"]
    archive = output / "frozen-code.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "minicode").rglob("*.py")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in HARNESS_FILES:
            bundle.write(ROOT / "scripts" / name, "scripts/" + name)
        bundle.write(DEFAULT / "analysis-plan.md", "analysis-plan.md")
    manifest = {
        "schemaVersion": 2,
        "frozenCodeArchiveSha256": sha(archive.read_bytes()),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "taskSha256": sha(tasks_path.read_bytes()),
        "analysisPlanSha256": sha((DEFAULT / "analysis-plan.md").read_bytes()),
        "productionSha256": production_sha(),
        "harnessSha256": harness_sha(),
        "model": runtime["model"],
        "routeSha256": route_hash(runtime),
        "limits": LIMITS,
        "price": PRICE,
        "temperature": 0,
        "roleTurnLimits": {role: resolve_max_turns(role) for role in ("explore", "plan", "general")},
        "repeats": repeats,
        "schedule": schedule,
        "concurrency": 1,
        "guardian": "At most 30 seconds for setup, then hard stop at the shared 1800 second execution deadline. Independent oracle runs afterward and is timed separately.",
        "resolvedTransportRouteSha256": resolved_transport_route(runtime),
        "arms": list(ARMS),
        "comparison": "single vs automatic vs explicit read-analysis then workflow implementation",
        "explicitRecipe": "Coordinator-only parent (task/todo_write): synchronous focused plan result, then workflow Executor; all reading/writing/testing delegated to real leaves.",
        "minimumProtocolCoverage": 0.8,
        "tokenBudgetSemantics": "Shared production admission plus durable transport input-estimate/output reservation; actual provider tokens may overshoot estimates. Calls and wall time are hard-capped.",
        "isolation": "Fresh disposable workspace, empty skills/profiles/memory, no MCP/network tools, same scope approval and independent oracle.",
        "singlePromptAdaptation": "Remove unavailable subagent usage section; preserve other production instructions.",
    }
    if STUDY is not None:
        manifest.update(
            studyDesignSha256=sha((output / "study-design.json").read_bytes()),
            oracleSupportSha256=sha((output / "oracle-support-hashes.json").read_bytes()),
            arms=STUDY["arms"], comparison="single vs bounded automatic delegation on registered historical tasks",
            externalJudgeSeconds=STUDY["limits"]["externalJudgeSeconds"],
            tokenBudgetSemantics="No economic token/call/currency cap; shared wall deadline and role/depth bounds retained.",
            isolation="Private host oracles and logical tool scope; authorized Python is not an OS sandbox.",
        )
        validate_study_files(output, manifest)
    destination = output / "manifest.json"
    if destination.exists():
        raise ValueError(
            "Manifest exists; never overwrite an already frozen experiment."
        )
    write_json(destination, manifest)
    print(
        json.dumps({"frozen": True, "model": runtime["model"], "trials": len(schedule)})
    )


def single_prompt(prompt):
    start = prompt.index("## Sub-agent (task tool) usage guide")
    end = prompt.index("Structured response protocol:", start)
    return (
        prompt[:start] + "## Execution mode\n"
        "Complete the task directly using the available local tools. "
        "Delegation is unavailable. Inspect, implement, verify and review your own work.\n\n"
        + prompt[end:]
    )


def workspace_files(workspace):
    """Capture bytes, links and special files without following symlinks."""
    result = {}
    for folder, directories, files in os.walk(workspace, followlinks=False):
        for name in [
            *files,
            *(n for n in directories if (Path(folder) / n).is_symlink()),
        ]:
            path = Path(folder) / name
            relative = path.relative_to(workspace).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                result[relative] = ("symlink", os.readlink(path))
            elif stat.S_ISREG(mode):
                result[relative] = ("file", sha(path.read_bytes()))
            else:
                result[relative] = ("special", stat.S_IFMT(mode))
    return result


def workflow_scope(call_input, authorized_paths):
    if not isinstance(call_input, dict) or call_input.get("agent_type") != "workflow":
        return True
    request = call_input.get("workflow_request")
    scope = request.get("scope") if isinstance(request, dict) else None
    paths = (
        scope.get("writePaths")
        if isinstance(scope, dict)
        else call_input.get("write_paths")
    )
    import run_north_star_live as north

    try:
        return (
            isinstance(paths, list)
            and bool(paths)
            and all(
                north._safe_relative(path).as_posix() in authorized_paths
                for path in paths
            )
        )
    except (TypeError, ValueError):
        return False



def independent_registry(owner, arm, *, readonly=False):
    # Production core definitions are shared singletons. Evaluator wrappers
    # must belong only to this registry or a parent's policy leaks into leaves.
    from minicode.tooling import ToolRegistry
    return ToolRegistry([
        replace(owner.find(name)) for name in owner.list_all()
        if name in LOCAL_TOOLS and (arm != "single" or name != "task")
        and (not readonly or name not in {"edit_file", "write_file", "run_command", "test_runner"})
    ], skills=[])



def coordinator_registry(registry, arm):
    if arm == "explicit":
        return registry.filtered(lambda tool: tool.name in {"task", "todo_write"}, skills=[])
    return registry

def guard_registry(registry, scope):
    from minicode.tooling import ToolResult
    def denied(error):
        return ToolResult(ok=False, output="error[evaluation_scope_denied]: " + str(error))
    for name in ("read_file", "list_files", "file_tree", "grep_files", "diff_viewer", "file_line_count", "get_ast_info", "find_symbols", "find_references"):
        tool = registry.find(name)
        if tool is None:
            continue
        def read(data, context, *, _name=name, _run=tool.run):
            try:
                scope.require_tool_read(_name, data, workspace=context.cwd)
            except ScopeDenied as error:
                return denied(error)
            return _run(data, context)
        tool.run = read
    command = registry.find("run_command")
    if command is not None:
        original_command = command.run
        def run(data, context):
            try:
                argv = scope.require_command(data["command"], args=data.get("args"), cwd=data.get("cwd"), workspace=context.cwd)
            except ScopeDenied as error:
                return denied(error)
            return original_command({**data, "command": argv[0], "args": list(argv[1:]), "cwd": context.cwd, "background": False}, context)
        command.run = run
        tests = registry.find("test_runner")
        if tests is not None:
            def run_tests(data, context):
                try:
                    argv = scope.test_runner_argv(data, workspace=context.cwd)
                except ScopeDenied as error:
                    return denied(error)
                return run({"command": argv[0], "args": list(argv[1:]), "cwd": context.cwd, "timeout": data.get("timeout", 120)}, context)
            tests.run = run_tests
    return registry


def install_verification_guards(stack, scope, workspace):
    import minicode.tools.run_command as commands
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.review_probe_execution import review_probe_authorized_checks
    original_verifier = DeterministicVerifier.run
    def verify(verifier, **kwargs):
        approval_root = kwargs.get("permission_cwd")
        if approval_root is not None and Path(approval_root).resolve() != workspace:
            raise ScopeDenied("workflow verifier approval root is not this trial workspace")
        try:
            authorized_checks = review_probe_authorized_checks(
                kwargs["checks"], kwargs.get("permissions"), approval_root,
            )
        except ValueError as error:
            raise ScopeDenied(str(error)) from error
        scope.require_verification(authorized_checks, workspace=kwargs["cwd"])
        return original_verifier(verifier, **kwargs)
    stack.enter_context(patch.object(DeterministicVerifier, "run", verify))
    for name in ("_run_with_pipes", "_run_with_pty"):
        original = getattr(commands, name)
        def sanitized(*args, _run=original, **kwargs):
            kwargs["environment"] = verification_environment(kwargs["environment"], workspace=kwargs["cwd"])
            return _run(*args, **kwargs)
        stack.enter_context(patch.object(commands, name, sanitized))
    original_prepare = DeterministicVerifier._prepare_managed_command
    def prepare(verifier, **kwargs):
        environment = kwargs["environment"]
        sanitized = verification_environment(environment, workspace=kwargs["cwd"])
        environment.clear()
        environment.update(sanitized)
        return original_prepare(verifier, **kwargs)
    stack.enter_context(patch.object(DeterministicVerifier, "_prepare_managed_command", prepare))

def execute_trial(output, trial_id):
    from minicode.agent_runtime import (
        create_agent_turn_runtime,
        prepare_conversation_messages,
        final_response_text,
    )
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.memory import MemoryManager
    from minicode import tools as tool_module
    from minicode.permissions import PermissionMode
    from minicode.auto_mode import AutoModeChecker
    import run_north_star_live as north
    from run_multi_agent_cost_live import _BoundedWorkflowApprovalProvider

    manifest = read_json(output / "manifest.json")
    validate_study_files(output, manifest)
    trial = next(x for x in manifest["schedule"] if x["id"] == trial_id)
    case = next(
        x for x in read_json(output / "tasks.json")["cases"] if x["id"] == trial["case"]
    )
    runtime_config = frozen_runtime()
    assert route_hash(runtime_config) == manifest["routeSha256"]
    assert production_sha() == manifest["productionSha256"]
    assert harness_sha() == manifest["harnessSha256"]
    assert manifest["limits"] == LIMITS and manifest["price"] == PRICE
    assert sha((output / "tasks.json").read_bytes()) == manifest["taskSha256"]
    root = output / "private" / trial_id
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    if (root / "transport.sqlite").exists():
        raise RuntimeError(
            "trial_has_prior_call_evidence; preserve it instead of rerunning"
        )
    workspace = Path(tempfile.mkdtemp(prefix="minicode-real-three-arm-")).resolve()
    write_json(root / "workspace.json", {"path": str(workspace)})
    case_files = {**read_json(output / "tasks.json").get("shared_files", {}), **case["files"]}
    for name, contents in case_files.items():
        target = workspace / north._safe_relative(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    before = workspace_files(workspace)
    write_json(root / "baseline-files.json", before)
    journal = RunJournal(workspace, data_dir=root / "journal")
    start = time.monotonic()
    execution_epoch = time.time()
    transport_config = TransportConfig(
        ledger_path=str((root / "transport.sqlite").resolve()),
        expected_model=runtime_config["model"],
        expected_route_sha256=manifest["resolvedTransportRouteSha256"],
        deadline_epoch=(execution_epoch + LIMITS["wallSeconds"]
                        if LIMITS["wallSeconds"] is not None else None),
        max_model_calls=LIMITS["maxModelCalls"],
        max_total_tokens=LIMITS["maxTokens"],
        max_output_tokens=LIMITS["maxOutputTokens"],
        connect_timeout_seconds=LIMITS["connectTimeoutSeconds"],
        idle_timeout_seconds=LIMITS["idleTimeoutSeconds"],
        request_timeout_seconds=LIMITS["requestTimeoutSeconds"],
    )
    write_json(root / "transport-config.json", asdict(transport_config))
    write_json(root / "execution-start.json", {"epoch": execution_epoch, "deadlineEpoch": transport_config.deadline_epoch})
    transport = EvaluationTransport(transport_config)
    collaboration = CollaborationEvidence(root / "collaboration.json")
    original_factory = tool_module.create_default_tool_registry
    error_class = None
    response = ""
    runtime = None
    budget = None
    subagents = []
    approval = _BoundedWorkflowApprovalProvider(tuple(case["authorized_paths"]))
    owned_registries = []
    readonly = STUDY is not None and case["category"] == "read"
    verification_scope = VerificationScope(case["verification_argv"] or
                                           ("python3", "-m", "pytest", "-q", "tests/test_eval_regression.py"))

    def registry(*args, **kwargs):
        kwargs["include_mcp"] = False
        kwargs["include_user_interaction"] = False
        registry_ = original_factory(*args, **kwargs)
        is_parent = not owned_registries
        owned_registries.append(registry_)
        selected = independent_registry(registry_, trial["arm"], readonly=readonly)
        if is_parent:
            selected = coordinator_registry(selected, trial["arm"])
        return guard_registry(selected, verification_scope)

    def memory(*args, **kwargs):
        kwargs["data_root"] = root / "memory"
        return MemoryManager(*args, **kwargs)

    prompt = (
        case["prompt"]
        + "\n\n宿主预授权的可修改文件仅有："
        + json.dumps(case["authorized_paths"], ensure_ascii=False)
        + "。其余路径只读。run_command 只允许下面的公开验证命令，以及 python3 -m pytest -q tests/test_eval_regression.py；不要使用 shell 拼接或其他外部命令。可通过 read_file/grep_files 等工具阅读源码。test_runner 映射到相同验证。工作完成后直接返回结果。"
    )
    if case.get("verification_argv"):
        prompt += "\n项目可见验证命令：" + shlex.join(case["verification_argv"])
    if readonly:
        prompt = case["prompt"] + "\nThis checkout is read-only. Submit the diagnostic as final-response code; the host runs it in private copies."
    prompt = prompt_for_arm(prompt, trial["arm"])
    messages = []
    with ExitStack() as stack:
        stack.enter_context(ProcessEvidence(root / "processes.jsonl"))
        stack.enter_context(transport)
        stack.enter_context(collaboration)
        install_verification_guards(stack, verification_scope, workspace)
        # Keep module-level persistence paths local without changing HOME or
        # changing the credential route that was resolved before this point.
        state_root = root / "state"
        for target in (
            "minicode.config.MINI_CODE_DIR",
            "minicode.context_manager.MINI_CODE_DIR",
            "minicode.logging_config.MINI_CODE_DIR",
            "minicode.memory.MINI_CODE_DIR",
        ):
            stack.enter_context(patch(target, state_root))
        stack.enter_context(
            patch(
                "minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH",
                state_root / "cybernetic_supervisor.json",
            )
        )
        stack.enter_context(
            patch("minicode.logging_config.LOG_FILE", state_root / "minicode.log")
        )
        stack.enter_context(
            patch(
                "minicode.permissions.MINI_CODE_PERMISSIONS_PATH",
                state_root / "permissions.json",
            )
        )
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "MINI_CODE_TURN_BUDGET_TOKENS": str(LIMITS["maxTokens"] if LIMITS["maxTokens"] is not None else 0),
                    "MINI_CODE_TURN_BUDGET_MODEL_CALLS": str(LIMITS["maxModelCalls"] if LIMITS["maxModelCalls"] is not None else 0),
                    "MINI_CODE_TURN_BUDGET_COST_USD": str(LIMITS["maxCostUsd"] if LIMITS["maxCostUsd"] is not None else 0),
                    "MINICODE_TASK_TIMEOUT_SECONDS": str(LIMITS["wallSeconds"] if LIMITS["wallSeconds"] is not None else 0),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MINI_CODE_MODEL_MODE": "real",
                    **{f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS": str(limit) for role, limit in manifest["roleTurnLimits"].items()},
                },
            )
        )
        stack.enter_context(
            patch(
                "minicode.config.load_runtime_config",
                side_effect=lambda *a, **k: deepcopy(runtime_config),
            )
        )
        stack.enter_context(
            patch("minicode.tools.create_default_tool_registry", side_effect=registry)
        )
        stack.enter_context(patch("minicode.skills.discover_skills", return_value=[]))
        stack.enter_context(patch("minicode.tools.discover_skills", return_value=[]))
        stack.enter_context(
            patch(
                "minicode.subagent_profiles.load_agent_profiles", return_value=({}, [])
            )
        )
        stack.enter_context(
            patch("minicode.tools.task.load_agent_profiles", return_value=({}, []))
        )
        stack.enter_context(patch("minicode.prompt._maybe_read", return_value=""))
        stack.enter_context(
            patch("minicode.permissions._read_permission_store", return_value={})
        )
        stack.enter_context(patch("minicode.memory.MemoryManager", side_effect=memory))
        stack.enter_context(north._working_directory(workspace))
        try:
            with observe_run(
                workspace=workspace,
                source="headless",
                title=case["id"],
                journal_factory=lambda _: journal,
                enabled=True,
            ) as observation:
                runtime = create_agent_turn_runtime(
                    workspace=workspace,
                    prompt=prompt,
                    include_mcp=False,
                    allow_user_interaction=False,
                    event_sink=observation,
                    deadline_monotonic=transport.deadline,
                )
                if STUDY is not None:
                    actual_budget = runtime.agent_budget.snapshot()
                    if any(value is not None for value in (actual_budget.limit_total_tokens,
                                                          actual_budget.limit_model_calls,
                                                          actual_budget.limit_cost_usd)):
                        raise RuntimeError("registered unlimited economic budget was not propagated")
                if north._runtime_profile_from_adapter(
                    runtime.model, runtime_config
                ) != north._runtime_profile_from_runtime(runtime_config):
                    raise RuntimeError("evaluation_model_route_drift")
                runtime.permissions.auto_checker = AutoModeChecker(
                    PermissionMode.DEFAULT
                )
                runtime.permissions.prompt = lambda request: (
                    north._isolated_write_approval(
                        workspace,
                        request,
                        tuple(Path(p) for p in case["authorized_paths"]),
                    )
                )
                if trial["arm"] == "single":
                    runtime.system_prompt = single_prompt(runtime.system_prompt)
                task = runtime.tools.find("task")
                if task is not None:
                    original_task = task.run

                    def observed_task(call_input, context):
                        if not workflow_scope(call_input, case["authorized_paths"]):
                            from minicode.tooling import ToolResult

                            denied = ToolResult(
                                ok=False,
                                output="error[evaluation_write_scope_denied]: workflow write paths must be within the host-authorized file list.",
                            )
                            approval.capture_task_result(denied)
                            return denied
                        approval.capture_task_call(call_input)
                        if (
                            isinstance(call_input, dict)
                            and call_input.get("agent_type") == "workflow"
                            and not call_input.get("workflow_request")
                            and isinstance(call_input.get("write_paths"), list)
                        ):
                            approval._delegations[-1]["writePaths"] = call_input[
                                "write_paths"
                            ]
                            approval._delegations[-1]["taskId"] = "*"
                        result = original_task(call_input, context)
                        approval.capture_task_result(result)
                        return result

                    task.run = observed_task
                    original_approval = approval.consume_approval

                    def consume_approval(**kwargs):
                        if (
                            approval._delegations
                            and approval._delegations[-1]["taskId"] == "*"
                        ):
                            approval._delegations[-1]["taskId"] = kwargs["task_id"]
                        return original_approval(**kwargs)

                    approval.consume_approval = consume_approval
                    runtime.runtime["_workflowHumanApprovalProvider"] = approval
                write_json(
                    root / "runtime-contract.json",
                    {
                        "tools": runtime.tools.list_all(),
                        "systemPrompt": runtime.system_prompt,
                        "model": runtime_config["model"],
                        "routeSha256": route_hash(runtime_config),
                    },
                )
                messages = prepare_conversation_messages(
                    [], system_prompt=runtime.system_prompt, user_message=prompt
                )
                messages = runtime.execute(messages, observation)
                response = final_response_text(messages) or ""
                write_json(root / "transcript.json", messages)
                subagents = [
                    asdict(x) for x in journal.list_subagent_runs(observation.run_id)
                ]
        except BaseException as error:
            error_class = type(error).__name__
            # Locally retained stack locations aid harness diagnosis without secret exception text.
            import traceback

            write_json(
                root / "error.json",
                {
                    "class": error_class,
                    "frames": [
                        {"file": f.filename, "line": f.lineno, "name": f.name}
                        for f in traceback.extract_tb(error.__traceback__)
                    ],
                },
            )
        finally:
            if runtime is not None:
                budget = asdict(runtime.agent_budget.snapshot())
                runtime.dispose()
            for owner in owned_registries:
                owner.dispose()
    (root / "response.txt").write_text(response)
    write_json(root / "subagents.json", subagents)
    write_json(root / "execution.json", {
        "durationSeconds": time.monotonic() - start,
        "errorClass": error_class,
        "budget": budget,
        "taskErrorCodes": approval.task_error_codes,
        "approvalDecisions": approval.records,
    })


def empty_transport_snapshot():
    return {
        "requests": [], "started": 0, "completed": 0, "failed": 0, "inFlight": 0,
        "admissionRejections": [], "admissionRejectedCount": 0,
        "providerUsageCalls": 0, "unknownUsageCalls": 0,
        "usageComplete": False, "cacheUsageComplete": False,
        "inputTokensKnown": 0, "outputTokensKnown": 0, "cacheReadTokensKnown": 0,
    }


def intervention_metrics(root):
    """Count retained host events without calling policy approval human input."""
    counts, errors = {}, 0
    files = list((root / "journal").rglob("events.ndjson"))
    for path in files:
        try:
            for line in path.read_text().splitlines():
                event = json.loads(line)
                kind = event["type"]
                counts[kind] = counts.get(kind, 0) + 1
        except (OSError, ValueError, KeyError, TypeError):
            errors += 1
    return {
        "journalAvailable": bool(files), "journalParseErrors": errors,
        "journalEventCounts": counts,
        "permissionRequestEvents": counts.get("permission.requested", 0) if files and not errors else None,
        "permissionDecisionEvents": counts.get("permission.decided", 0) if files and not errors else None,
        "humanApprovalCount": None, "humanCorrectionCount": None,
        "humanEventCapture": "unavailable_unattended_host_policy_is_not_human_input",
    }


def finalize_trial(output, trial, *, process_code=0, process_seconds=0.0, stopped_epoch=None):
    """Judge delivered files outside the agent deadline, including killed trials."""
    root = output / "private" / trial["id"]
    case = next(c for c in read_json(output / "tasks.json")["cases"] if c["id"] == trial["case"])
    recovery_errors = []
    def optional(name, default):
        path = root / name
        try:
            return read_json(path) if path.exists() else default
        except (OSError, ValueError, TypeError):
            recovery_errors.append(name)
            return default

    execution = optional("execution.json", {})
    start = optional("execution-start.json", {})
    cleanup = optional("process-cleanup.json", {"cleanupComplete": True})
    stopped_epoch = cleanup.get("deadlineReachedEpoch") or stopped_epoch or time.time()
    duration = execution.get("durationSeconds", max(0.0, stopped_epoch - start["epoch"]) if start else process_seconds)
    error_class = execution.get("errorClass")
    if not execution:
        error_class = "HardTimeout" if process_code < 0 else "ChildFailure"
    snapshot = empty_transport_snapshot()
    if (root / "transport.sqlite").exists():
        ledger = SharedLedger(TransportConfig(**read_json(root / "transport-config.json")))
        snapshot = ledger.snapshot()
        ledger.export_jsonl(root / "calls.jsonl")
    write_json(root / "transport-summary.json", snapshot)
    collaboration = optional("collaboration.json", {"runs": [], "deliveredIds": []})
    messages = optional("transcript.json", [])
    collaboration_summary = collaboration_metrics(collaboration["runs"], snapshot["requests"], messages, collaboration.get("deliveredIds", []))
    workspace_info = optional("workspace.json", {})
    workspace = Path(workspace_info["path"]) if workspace_info else None
    before = {p: tuple(v) for p, v in optional("baseline-files.json", {}).items()}
    oracle_passed = False
    oracle_error = None
    verification_changes = []
    changed = []
    forbidden = []
    judgement_start = time.monotonic()
    judgement = None
    if workspace is not None and workspace.is_dir() and before and cleanup["cleanupComplete"]:
        before_oracle = workspace_files(workspace)
        oracle_path = root / "oracle.py"
        oracle_path.write_text(case["oracle_source"])
        response_path = root / "response.txt"
        if not response_path.exists():
            response_path.write_text("")
        try:
            if STUDY is not None:
                from scripts.implementation_judge import run_judgement
                judgement = run_judgement(
                    workspace, root / "judgement", oracle_path, response_path,
                    total_seconds=STUDY["limits"]["externalJudgeSeconds"],
                    started_at=judgement_start,
                    require_regression=case["generatedRegressionRequired"],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                oracle_passed = judgement["oracle"]["passed"] and judgement["cleanupComplete"]
            else:
                result = subprocess.run(
                    [sys.executable, "-B", str(oracle_path), str(workspace), str(response_path)],
                    capture_output=True, text=True, cwd=root, timeout=120,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(root / "judge-pycache")},
                )
                oracle_passed = result.returncode == 0
                (root / "oracle.log").write_text(result.stdout + result.stderr)
        except subprocess.TimeoutExpired:
            oracle_error = "OracleTimeout"
        after = workspace_files(workspace)
        verification_changes = changed_paths(before_oracle, after)
        changed = sorted(set(changed_paths(before, before_oracle)) | set(changed_paths(before, after)))
        forbidden = [p for p in changed if p not in case["authorized_paths"]]
        scope_passed = not forbidden
    else:
        scope_passed = False
        oracle_error = "ProcessCleanupIncomplete" if not cleanup["cleanupComplete"] else "MissingWorkspaceOrBaseline"
    row = {
        **trial, **transport_metrics(snapshot), **collaboration_summary,
        "category": case["category"], "durationSeconds": duration,
        "processSeconds": process_seconds, "judgeSeconds": time.monotonic() - judgement_start,
        "success": oracle_passed and scope_passed and (trial["arm"] != "explicit" or collaboration_summary["collaborationProtocolSatisfied"]),
        "oracleSuccess": oracle_passed, "oraclePassed": oracle_passed, "scopePassed": scope_passed,
        "errorClass": error_class, "oracleErrorClass": oracle_error,
        "recoveryEvidenceErrors": recovery_errors, "processCleanup": cleanup,
        "changedFiles": changed, "unauthorizedChanges": forbidden,
        "verificationInducedChanges": verification_changes,
        "budget": execution.get("budget"), "subagentRuns": len(optional("subagents.json", [])),
        "taskErrorCodes": execution.get("taskErrorCodes", []),
        "approvalDecisions": execution.get("approvalDecisions", []),
    }
    if STUDY is not None:
        requires_regression = case["generatedRegressionRequired"]
        regression_passed = judgement["regressionTest"]["passed"] if judgement else False
        source_changed = any(p.startswith("minicode/") and p in case["authorized_paths"] for p in changed)
        row.update(
            generatedRegressionRequired=requires_regression,
            regressionTest=judgement["regressionTest"] if judgement else None,
            regressionTestPassed=regression_passed, authorizedSourceChanged=source_changed,
            interventions=intervention_metrics(root),
            hostPolicyApprovalRecords=execution.get("approvalDecisions", []),
            knownTotalTokens=row["totalTokens"],
        )
        if not row["tokenUsageComplete"]:
            row["totalTokens"] = None
        row["success"] = bool(row["success"] and (not requires_regression or (regression_passed and source_changed)))
    write_json(output / "trials" / (trial["id"] + ".json"), row)
    return row


def wait_trial_process(proc, root, *, setup_seconds=30):
    """Use an optional setup limit; always honor a supplied execution deadline.

    The default retains bounded benchmark hydration. An explicitly unbounded
    continuation passes None so host archive validation has no hidden wall cap.
    """
    setup_deadline = time.time() + setup_seconds if setup_seconds is not None else None
    execution_deadline = None
    execution_started = False
    while proc.poll() is None:
        if not execution_started and (root / "execution-start.json").exists():
            execution_deadline = read_json(root / "execution-start.json")["deadlineEpoch"]
            execution_started = True
        deadline = execution_deadline if execution_started else setup_deadline
        remaining = deadline - time.time() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            reached = time.time()
            cleanup = stop_trial(proc.pid, root / "processes.jsonl")
            cleanup["deadlineReachedEpoch"] = reached
            cleanup["cleanupSeconds"] = time.time() - reached
            write_json(root / "process-cleanup.json", cleanup)
            proc.wait(timeout=10)
            return -signal.SIGKILL
        try:
            return proc.wait(timeout=min(0.25, remaining) if remaining is not None else 0.25)
        except subprocess.TimeoutExpired:
            pass
    return proc.returncode


def run_suite(output, selected=None):
    manifest = read_json(output / "manifest.json")
    validate_study_files(output, manifest)
    if (
        manifest["harnessSha256"] != harness_sha()
        or manifest["productionSha256"] != production_sha()
        or manifest["taskSha256"] != sha((output / "tasks.json").read_bytes())
        or manifest["analysisPlanSha256"] != sha((DEFAULT / "analysis-plan.md").read_bytes())
        or manifest["limits"] != LIMITS
        or manifest["price"] != PRICE
    ):
        raise RuntimeError("Frozen experiment contract has changed.")
    for trial in manifest["schedule"]:
        if selected and trial["id"] not in selected:
            continue
        destination = output / "trials" / (trial["id"] + ".json")
        if destination.exists():
            continue
        root = output / "private" / trial["id"]
        root.mkdir(parents=True, exist_ok=True)
        if (root / "transport.sqlite").exists():
            raise RuntimeError("unfinished trial has durable evidence; recover it explicitly, never rerun or overwrite")
        started = time.monotonic()
        with (root / "process.log").open("w") as log:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "child",
                    "--output",
                    str(output),
                    "--trial",
                    trial["id"],
                ],
                stdout=log,
                stderr=log,
                cwd=ROOT,
                start_new_session=True,
            )
            code = wait_trial_process(proc, root)
        stopped_epoch = time.time()
        if not (root / "process-cleanup.json").exists():
            cleanup = stop_trial(proc.pid, root / "processes.jsonl")
            cleanup["cleanupSeconds"] = time.time() - stopped_epoch
            write_json(root / "process-cleanup.json", cleanup)
        row = finalize_trial(output, trial, process_code=code, process_seconds=time.monotonic() - started, stopped_epoch=stopped_epoch)
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "id",
                        "success",
                        "durationSeconds",
                        "modelCalls",
                        "totalTokens",
                        "delegationAttempts",
                        "errorClass",
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        checkpoint_results(output, manifest)
        if not row["processCleanup"]["cleanupComplete"]:
            raise RuntimeError("trial process cleanup incomplete; stopping suite before further execution")
    checkpoint_results(output, manifest)


def checkpoint_results(output, manifest):
    rows = [
        read_json(output / "trials" / (x["id"] + ".json"))
        for x in manifest["schedule"]
        if (output / "trials" / (x["id"] + ".json")).exists()
    ]
    write_json(
        output / "results.json",
        {
            "manifestSha256": sha((output / "manifest.json").read_bytes()),
            "expectedTrials": len(manifest["schedule"]),
            "trials": rows,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "freeze", "run", "child"])
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--trial", action="append")
    parser.add_argument("--design", type=Path)
    parser.add_argument("--analysis-plan", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        if args.design is None or args.analysis_plan is None:
            parser.error("prepare requires --design and --analysis-plan")
        prepare_study(output, args.design.resolve(), args.analysis_plan.resolve())
        return
    configure_study(output)
    if args.mode == "freeze":
        freeze(output, args.repeats)
    elif args.mode == "child":
        execute_trial(output, args.trial[0])
    else:
        run_suite(output, set(args.trial or []))


if __name__ == "__main__":
    main()
