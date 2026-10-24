#!/usr/bin/env python3
"""Frozen paired real-model evaluation; synthetic fixture oracles stay outside agent roots.

This harness changes no production code. All calls pass through the production
runtime, adapter, tool dispatcher and (when selected) workflow scheduler.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
DEFAULT = ROOT / "artifacts/single-vs-multi-2026-10-13"
LOCAL_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "file_tree",
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
    "maxTokens": 200000,
    "maxModelCalls": 32,
    "maxCostUsd": "5",
    "wallSeconds": 240,
    "maxOutputTokens": 4096,
    "requestTimeoutSeconds": 60,
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
    return runtime


def harness_sha():
    return sha(Path(__file__).read_bytes())


def freeze(output, repeats):
    runtime = frozen_runtime()
    if runtime["model"] != "glm-5.3-flash":
        raise ValueError("Frozen price card is only valid for glm-5.3-flash.")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    tasks_path = output / "tasks.json"
    tasks = read_json(tasks_path)["cases"]
    schedule = []
    rng = random.Random(20261013)
    for repeat in range(1, repeats + 1):
        ordered = list(tasks)
        rng.shuffle(ordered)
        for i, case in enumerate(ordered):
            arms = ["single", "multi"] if (i + repeat) % 2 else ["multi", "single"]
            for arm in arms:
                schedule.append(
                    {
                        "case": case["id"],
                        "repeat": repeat,
                        "arm": arm,
                        "id": f"{case['id']}-r{repeat}-{arm}",
                    }
                )
    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "taskSha256": sha(tasks_path.read_bytes()),
        "productionSha256": production_sha(),
        "harnessSha256": harness_sha(),
        "model": runtime["model"],
        "routeSha256": route_hash(runtime),
        "limits": LIMITS,
        "price": PRICE,
        "temperature": 0,
        "repeats": repeats,
        "schedule": schedule,
        "concurrency": 1,
        "comparison": "single actor vs production automatic delegation enabled (intention-to-treat)",
        "tokenBudgetSemantics": "Shared production admission limit; final response may overshoot. Calls and wall time have hard caps.",
        "isolation": "Fresh disposable workspace, empty skills/profiles/memory, no MCP/network tools, same scope approval and independent oracle.",
        "singlePromptAdaptation": "Remove unavailable subagent usage section; preserve other production instructions.",
    }
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


class CallLedger:
    """Record adapter requests directly, avoiding aggregated child double counting."""

    def __init__(self, path, model, deadline, runtime=None):
        self.path, self.model, self.deadline = path, model, deadline
        self.runtime = runtime
        self.lock = threading.Lock()
        self.count = 0

    def append(self, row):
        with self.path.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def wrap(self, original):
        @wraps(original)
        def measured(adapter, *args, **kwargs):
            if adapter.runtime.get("model") != self.model:
                raise RuntimeError("evaluation_model_route_drift")
            if self.runtime is not None:
                import run_north_star_live as north

                if north._runtime_profile_from_adapter(
                    adapter, self.runtime
                ) != north._runtime_profile_from_runtime(self.runtime):
                    raise RuntimeError("evaluation_model_route_drift")
            if time.monotonic() >= self.deadline:
                raise TimeoutError("evaluation_deadline_exceeded")
            with self.lock:
                if self.count >= LIMITS["maxModelCalls"]:
                    raise RuntimeError("evaluation_call_budget_exhausted")
                self.count += 1
                call = self.count
                self.append(
                    {
                        "event": "started",
                        "call": call,
                        "model": self.model,
                        "thread": threading.current_thread().name,
                    }
                )
            start = time.monotonic()
            kwargs["max_retries_override"] = 0
            kwargs["deadline_monotonic"] = min(
                kwargs.get("deadline_monotonic") or self.deadline, self.deadline
            )
            kwargs["max_output_tokens_override"] = min(
                kwargs.get("max_output_tokens_override") or LIMITS["maxOutputTokens"],
                LIMITS["maxOutputTokens"],
            )
            try:
                step = original(adapter, *args, **kwargs)
                row = {
                    "event": "completed",
                    "call": call,
                    "seconds": time.monotonic() - start,
                    "usage": asdict(step.usage) if step.usage is not None else None,
                    "toolCalls": [
                        {"name": c["toolName"], "arguments": c["input"]}
                        for c in step.calls
                    ],
                    "response": step.content,
                }
            except BaseException as error:
                with self.lock:
                    self.append(
                        {
                            "event": "failed",
                            "call": call,
                            "seconds": time.monotonic() - start,
                            "errorClass": type(error).__name__,
                        }
                    )
                raise
            with self.lock:
                self.append(row)
            return step

        return measured


def ledger_summary(path):
    records = []
    damaged = False
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            records.append(json.loads(line))
        except (ValueError, TypeError):
            # A hard-killed process can leave its final append incomplete.
            damaged = True
    started = {r["call"] for r in records if r["event"] == "started"}
    completed = {r["call"]: r for r in records if r["event"] == "completed"}
    usage = [
        r["usage"]
        for r in completed.values()
        if isinstance(r.get("usage"), dict)
        and r["usage"].get("source") == "provider"
        and all(
            type(r["usage"].get(k)) is int and r["usage"][k] >= 0
            for k in ("input_tokens", "output_tokens")
        )
        and (
            r["usage"].get("cache_read_tokens") is None
            or (
                type(r["usage"]["cache_read_tokens"]) is int
                and 0 <= r["usage"]["cache_read_tokens"] <= r["usage"]["input_tokens"]
            )
        )
    ]
    complete = len(usage) == len(started) and bool(started) and not damaged
    inputs = sum(u.get("input_tokens") or 0 for u in usage)
    outputs = sum(u.get("output_tokens") or 0 for u in usage)
    cache = sum(u.get("cache_read_tokens") or 0 for u in usage)
    cost = (
        (inputs - cache) * PRICE["perMillionInput"]
        + outputs * PRICE["perMillionOutput"]
        + cache * PRICE["perMillionCacheRead"]
    ) / 1000000
    task_calls = [
        c
        for r in completed.values()
        for c in r.get("toolCalls", [])
        if c["name"] == "task"
    ]
    return {
        "modelCalls": len(started),
        "usageComplete": complete,
        "inputTokens": inputs,
        "outputTokens": outputs,
        "cacheReadTokens": cache,
        "totalTokens": inputs + outputs,
        "estimatedCostCny": cost if complete else None,
        "knownCostLowerBoundCny": cost,
        "delegationAttempts": len(task_calls),
        "delegationTypes": [
            c["arguments"].get("agent_type")
            if isinstance(c.get("arguments"), dict)
            else None
            for c in task_calls
        ],
        "failedCalls": len(started - completed.keys()),
        "ledgerDamaged": damaged,
    }


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


def runtime_metadata(path):
    parts = Path(path).parts
    return "__pycache__" in parts or parts[0] in {
        ".mini-code",
        ".mini-code-memory",
        ".mini-code-memory-local",
        ".minicode-memory-authority.lock",
        ".pytest_cache",
    }


def changed_paths(before, after):
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
        and (path in before or not runtime_metadata(path))
    )


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


def execute_trial(output, trial_id):
    from minicode.agent_runtime import (
        create_agent_turn_runtime,
        prepare_conversation_messages,
        final_response_text,
    )
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.openai_adapter import OpenAIModelAdapter
    from minicode.memory import MemoryManager
    from minicode import tools as tool_module
    from minicode.permissions import PermissionMode
    from minicode.auto_mode import AutoModeChecker
    import run_north_star_live as north
    from run_multi_agent_cost_live import _BoundedWorkflowApprovalProvider

    manifest = read_json(output / "manifest.json")
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
    if (root / "calls.jsonl").exists():
        raise RuntimeError(
            "trial_has_prior_call_evidence; preserve it instead of rerunning"
        )
    workspace = Path(tempfile.mkdtemp(prefix="minicode-paired-")).resolve()
    write_json(root / "workspace.json", {"path": str(workspace)})
    for name, contents in case["files"].items():
        target = workspace / north._safe_relative(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    before = workspace_files(workspace)
    journal = RunJournal(workspace, data_dir=root / "journal")
    start = time.monotonic()
    ledger = CallLedger(
        root / "calls.jsonl",
        runtime_config["model"],
        start + LIMITS["wallSeconds"],
        runtime_config,
    )
    original_factory = tool_module.create_default_tool_registry
    original_next = OpenAIModelAdapter.next
    error_class = None
    response = ""
    runtime = None
    budget = None
    subagents = []
    approval = _BoundedWorkflowApprovalProvider(tuple(case["authorized_paths"]))
    owned_registries = []

    def registry(*args, **kwargs):
        kwargs["include_mcp"] = False
        kwargs["include_user_interaction"] = False
        registry_ = original_factory(*args, **kwargs)
        owned_registries.append(registry_)
        selected = registry_.filtered(
            lambda t: (
                t.name in LOCAL_TOOLS and (trial["arm"] == "multi" or t.name != "task")
            ),
            skills=[],
        )
        # Filtered registries borrow core definitions. The trial's task
        # observer must not change another role or a later runtime's scope.
        task = selected.find("task")
        return selected.overlay([replace(task)]) if task is not None else selected

    def memory(*args, **kwargs):
        kwargs["data_root"] = root / "memory"
        return MemoryManager(*args, **kwargs)

    prompt = (
        case["prompt"]
        + "\n\n宿主预授权的可修改文件仅有："
        + json.dumps(case["authorized_paths"], ensure_ascii=False)
        + "。可以运行本地 unittest/pytest/compileall 验证；其余路径只读。工作完成后直接返回结果。"
    )
    with ExitStack() as stack:
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
                    "MINI_CODE_TURN_BUDGET_TOKENS": str(LIMITS["maxTokens"]),
                    "MINI_CODE_TURN_BUDGET_MODEL_CALLS": str(LIMITS["maxModelCalls"]),
                    "MINI_CODE_TURN_BUDGET_COST_USD": LIMITS["maxCostUsd"],
                    "MINICODE_TASK_TIMEOUT_SECONDS": str(LIMITS["wallSeconds"]),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MINI_CODE_MODEL_MODE": "real",
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
        stack.enter_context(
            patch.object(OpenAIModelAdapter, "next", ledger.wrap(original_next))
        )
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
                    deadline_monotonic=ledger.deadline,
                )
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
    duration = time.monotonic() - start
    after = workspace_files(workspace)
    (root / "response.txt").write_text(response)
    write_json(root / "subagents.json", subagents)
    oracle_path = root / "oracle.py"
    oracle_path.write_text(case["oracle_source"])
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(oracle_path),
                str(workspace),
                str(root / "response.txt"),
            ],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=20,
        )
        oracle_passed = result.returncode == 0
        (root / "oracle.log").write_text(result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        oracle_passed = False
        error_class = error_class or "OracleTimeout"
    changed = changed_paths(before, after)
    forbidden = [p for p in changed if p not in case["authorized_paths"]]
    metrics = ledger_summary(root / "calls.jsonl")
    row = {
        **trial,
        **metrics,
        "category": case["category"],
        "durationSeconds": duration,
        "success": oracle_passed and error_class is None and not forbidden,
        "oraclePassed": oracle_passed,
        "errorClass": error_class,
        "changedFiles": changed,
        "unauthorizedChanges": forbidden,
        "budget": budget,
        "subagentRuns": len(subagents),
        "taskErrorCodes": approval.task_error_codes,
        "approvalDecisions": approval.records,
    }
    write_json(output / "trials" / (trial_id + ".json"), row)
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


def run_suite(output, selected=None):
    manifest = read_json(output / "manifest.json")
    if (
        manifest["harnessSha256"] != harness_sha()
        or manifest["productionSha256"] != production_sha()
        or manifest["taskSha256"] != sha((output / "tasks.json").read_bytes())
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
            try:
                code = proc.wait(timeout=LIMITS["wallSeconds"])
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                code = -signal.SIGKILL
        if not destination.exists():
            row = {
                **trial,
                **ledger_summary(root / "calls.jsonl"),
                "success": False,
                "durationSeconds": time.monotonic() - started,
                "errorClass": "HardTimeout" if code < 0 else "ChildFailure",
                "category": next(
                    x["category"]
                    for x in read_json(output / "tasks.json")["cases"]
                    if x["id"] == trial["case"]
                ),
            }
            write_json(destination, row)
        row = read_json(destination)
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
    parser.add_argument("mode", choices=["freeze", "run", "child"])
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trial", action="append")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "freeze":
        freeze(output, args.repeats)
    elif args.mode == "child":
        execute_trial(output, args.trial[0])
    else:
        run_suite(output, set(args.trial or []))


if __name__ == "__main__":
    main()
