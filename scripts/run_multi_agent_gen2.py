#!/usr/bin/env python3
"""Independent, preregistered six-cell evaluation on an archived execution host.

Prepare/freeze are offline. Run/child require a Git commit containing every
frozen registration byte. Actual execution and the common judge are reused in
private modules; none of the historical preparation, freezing or gate is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import run_implementation_progress_live as progress  # noqa: E402
from scripts.public_verification_fixtures import (  # noqa: E402
    PUBLIC_FIXTURE_POLICY, public_fixture_manifest, run_public_preflight,
    validate_public_preflight,
)

shared = progress.shared
DEFAULT = ROOT / "artifacts/multi-agent-gen2-2026-10-23/run"
PROTOCOL_VERSION = "multi-agent-gen2-v1"
CASES = ("gen2-read-context", "gen2-registry-lifetime", "gen2-summary-budget")
ARMS = ("single", "explicit")
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
)
JUDGE_POLICY = {**progress.JUDGE_POLICY, "agentBudgetSeconds": 600}
GATE_POLICY = {
    "minimumTotalSuccesses": 4, "minimumSuccessesPerArm": 2,
    "minimumSuccessesPerCase": 1, "expectedTrials": 6,
    "requireAllTerminal": True, "requireIndependentAudit": True,
}
ANALYSIS_PLAN = """# Gen-2 preregistration

Three new cases, one single and one explicit-workflow trial per case. Six cells
are run serially once in the frozen seed-20261023 order. Model GLM-5.3-Flash,
temperature 0, shared 40 physical requests, 500000 tokens and 600 execution
seconds. Output/request/idle/connect limits are 8192 tokens / 300 / 120 / 30
seconds, with the existing USD 5 safety ceiling. Provider retries are disabled.
Role overrides are explicitly zero (no extra cumulative role ceiling); the
shared budget and production assignments still apply. The external common judge
has a separate 120-second deadline and must pass hidden acceptance and every
collected generated regression test, without skip/xfail or filtered collection.

Retain every failure, timeout, refusal and unknown usage. Never replace cases,
change models, rerun a cell with durable evidence or select a favorable repeat.
All six terminal results and independent scope/usage/cleanup/protocol audit are
required. The gate is total >=4/6, each arm >=2/3, each case >=1/2, with actual
authorized source changes and workflow delivery evidence for explicit cells.
These new cases and this frozen mixed-workspace host are not comparable with
older generations and do not identify a causal Phase-1 effect. No automatic
formal expansion or additional arm is authorized by this entry point.

Only public file maps, prompts, scope and public commands enter trial workspaces.
Hidden oracles, reference fixes and offline evidence remain on the judge host.
The complete execution host is archived, committed and verified before dispatch.
"""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
            or path.as_posix() != name or name == "."):
        raise ValueError("unsafe relative path in frozen material")
    return name


def schedule_for() -> list[dict]:
    rows = [{"case": case, "arm": arm, "repeat": 1,
             "id": f"{case}-r1-{arm}"} for case in CASES for arm in ARMS]
    random.Random(SEED).shuffle(rows)
    return rows


def static_contract() -> dict:
    """Public adapter surface for independent audit/gate implementations."""
    return {
        "schemaVersion": 1, "protocolVersion": PROTOCOL_VERSION, "phase": "gen2",
        "cases": list(CASES), "arms": list(ARMS), "repeats": 1,
        "scheduleSeed": SEED, "schedule": schedule_for(), "concurrency": 1,
        "model": MODEL, "temperature": 0, "limits": LIMITS, "price": shared.PRICE,
        "roleTurnLimits": ROLE_TURN_LIMITS, "roleTurnPolicy": ROLE_TURN_POLICY,
        "judgementPolicy": JUDGE_POLICY, "gatePolicy": GATE_POLICY,
        "harnessFiles": list(HARNESS_FILES), "runner": "scripts/run_multi_agent_gen2.py",
        "implementationCapabilities": progress.IMPLEMENTATION_CAPABILITIES,
        "explicitRecipe": "Coordinator-only parent (task/todo_write): direct workflow",
        "comparison": "Independent gen2 engineering gate; not comparable with previous generations",
    }


def validate_tasks(document: dict) -> None:
    cases = document.get("cases", [])
    if [c.get("id") for c in cases] != list(CASES):
        raise ValueError("tasks must contain the three fixed gen2 cases in order")
    common = document.get("shared_files", {})
    if not isinstance(common, dict):
        raise ValueError("shared_files must be a public file map")
    for case in cases:
        if (not isinstance(case.get("prompt"), str) or not case["prompt"].strip()
                or not isinstance(case.get("category"), str) or not case["category"].strip()
                or not isinstance(case.get("oracle_source"), str) or not case["oracle_source"].strip()
                or not isinstance(case.get("files"), dict)):
            raise ValueError("case requires prompt/category/files/private oracle")
        files = {**common, **case["files"]}
        for name, content in files.items():
            safe_relative(name)
            if not isinstance(content, str):
                raise ValueError("public file contents must be text")
        if "tests/test_eval_regression.py" in files:
            raise ValueError("generated regression test must not exist in the baseline")
        paths = case.get("authorized_paths", [])
        if (not isinstance(paths, list) or len(paths) != len(set(paths))
                or "tests/test_eval_regression.py" not in paths
                or not any(p.startswith("minicode/") and p.endswith(".py") for p in paths)):
            raise ValueError("case needs explicit production and generated-test write paths")
        for name in paths:
            safe_relative(name)
        argv = case.get("verification_argv")
        if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
                or "pytest" not in argv or any(a == "-k" or a.startswith("-k=") for a in argv)):
            raise ValueError("case needs an unfiltered public pytest command")
        shared.VerificationScope(argv)


def prepare(output: Path, tasks: Path, *, plan: Path | None = None,
            materials: Path | None = None) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("prepare needs a new empty output directory")
    encoded = tasks.read_bytes()
    validate_tasks(json.loads(encoded))
    output.mkdir(parents=True, exist_ok=True)
    (output / "tasks.json").write_bytes(encoded)
    (output / "analysis-plan.md").write_text(plan.read_text() if plan else ANALYSIS_PLAN)
    for name in ("source-manifest.json", "offline-proof.json"):
        source = tasks.parent / name
        if source.is_file():
            (output / "materials").mkdir(exist_ok=True)
            (output / "materials" / name).write_bytes(source.read_bytes())
    if materials is not None:
        if not materials.is_dir():
            raise ValueError("materials must be a directory of offline preregistration evidence")
        for path in sorted(materials.rglob("*")):
            if path.is_symlink():
                raise ValueError("offline materials may not contain symlinks")
            if path.is_file():
                relative = safe_relative(path.relative_to(materials).as_posix())
                if relative == "tasks.json":
                    continue
                target = output / "materials" / relative
                if target.exists() and target.read_bytes() != path.read_bytes():
                    raise ValueError("conflicting offline material: " + relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())


def prompt_for_arm(prompt: str, arm: str) -> str:
    if arm not in ARMS:
        raise ValueError("gen2 has only single/explicit arms")
    return (prompt + progress.REGRESSION_REQUIREMENT.replace("1800", "600")
            + (progress.EXPLICIT_INSTRUCTION if arm == "explicit" else ""))


@contextmanager
def role_overrides(manifest: dict):
    if manifest.get("roleTurnLimits") != ROLE_TURN_LIMITS:
        raise ValueError("frozen gen2 role turn limits changed")
    with patch.dict(os.environ, {
        f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS": str(value)
        for role, value in ROLE_TURN_LIMITS.items()
    }):
        from minicode.subagent_types import configured_max_turns
        if any(configured_max_turns(role) is not None for role in ROLE_TURN_LIMITS):
            raise ValueError("production role-zero semantics changed")
        yield


def runner(output: Path, *, commit: str | None = None):
    spec = importlib.util.spec_from_file_location(
        "_gen2_implementation_runner", ROOT / "scripts/run_implementation_progress_live.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load common implementation executor")
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    isolated.validate_successor = lambda path: validate(path, host_root=ROOT)
    isolated.frozen_role_turn_overrides = role_overrides
    isolated.prompt_for_arm = prompt_for_arm
    isolated.SUCCESSOR_HARNESS_FILES = HARNESS_FILES
    isolated.JUDGE_POLICY = JUDGE_POLICY
    module = isolated.runner(output)
    module.__file__ = str(Path(__file__).resolve())
    module.LIMITS = dict(LIMITS)
    original_runtime = module.frozen_runtime

    def frozen_runtime():
        runtime = original_runtime()
        runtime.update(model=MODEL, temperature=0, subagentRoutingEnabled=False,
                       subagentModels={})
        return runtime

    module.frozen_runtime = frozen_runtime
    original_popen = module.subprocess.Popen

    def launch_child(argv, *args, **kwargs):
        if len(argv) > 2 and argv[2] == "child":
            if not commit:
                raise ValueError("model execution requires --preregistered-commit")
            argv = [argv[0], "-I", "-B", *argv[1:], "--preregistered-commit", commit,
                    "--execution-host", str(ROOT)]
        return original_popen(argv, *args, **kwargs)

    module.subprocess = SimpleNamespace(**{**vars(module.subprocess), "Popen": launch_child})
    original_wait = module.wait_trial_process

    def wait_with_cleanup(proc, trial_root, **kwargs):
        try:
            return original_wait(proc, trial_root, **kwargs)
        except BaseException:
            cleanup = module.stop_trial(proc.pid, trial_root / "processes.jsonl")
            shared.write_json(trial_root / "process-cleanup.json", cleanup)
            proc.wait(timeout=10)
            raise

    module.wait_trial_process = wait_with_cleanup
    return module


def environment_fingerprint() -> dict:
    packages = sorted({(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                       if d.metadata.get("Name")})
    return {"pythonExecutable": str(Path(sys.executable).resolve()), "pythonVersion": sys.version,
            "platform": platform.platform(), "packages": [list(item) for item in packages]}


def host_files() -> dict[str, bytes]:
    package_paths = list((ROOT / "minicode").rglob("*"))
    if any(p.is_symlink() for p in package_paths):
        raise ValueError("execution host package may not contain symlinks")
    paths = [p for p in package_paths if p.is_file()
             and "__pycache__" not in p.parts and p.name != ".DS_Store"
             and p.suffix not in {".pyc", ".pyo", ".orig", ".bak"}]
    paths += [ROOT / "scripts" / name for name in HARNESS_FILES]
    paths += [ROOT / "pyproject.toml"]
    files = {}
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.is_file():
            raise ValueError("execution host requires regular files: " + str(path.relative_to(ROOT)))
        name = safe_relative(path.relative_to(ROOT).as_posix())
        if path.name == ".env" or path.suffix in {".pem", ".key"}:
            raise ValueError("private material cannot enter the execution host")
        files[name] = path.read_bytes()
    return files


def inventory_for(files: dict[str, bytes]) -> dict:
    rows = [{"path": name, "size": len(data), "sha256": shared.sha(data)}
            for name, data in sorted(files.items())]
    return {"schemaVersion": 1, "files": rows, "treeSha256": shared.sha(canonical(rows))}


def verify_archive(output: Path) -> dict:
    inventory = shared.read_json(output / "execution-host-inventory.json")
    rows = inventory.get("files", [])
    if (inventory.get("schemaVersion") != 1 or not rows
            or inventory.get("treeSha256") != shared.sha(canonical(rows))
            or [r["path"] for r in rows] != sorted({r["path"] for r in rows})):
        raise ValueError("invalid execution host inventory")
    expected = {safe_relative(r["path"]): r for r in rows}
    with zipfile.ZipFile(output / "execution-host.zip") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("execution host archive membership changed")
        for item in archive.infolist():
            safe_relative(item.filename)
            if item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("execution host archive contains a non-regular entry")
            data = archive.read(item)
            record = expected[item.filename]
            if len(data) != record["size"] or shared.sha(data) != record["sha256"]:
                raise ValueError("execution host archive content changed")
    return inventory


def verify_host(root: Path, inventory: dict) -> None:
    if root.is_symlink():
        raise ValueError("execution host root may not be a symlink")
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("execution host contains a symlink")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = path.read_bytes()
    if inventory_for(actual) != inventory:
        raise ValueError("unpacked execution host differs from its inventory")


def unpack_host(output: Path, destination: Path) -> dict:
    inventory = verify_archive(output)
    if destination.exists():
        verify_host(destination, inventory)
        return inventory
    destination.mkdir(parents=True)
    with zipfile.ZipFile(output / "execution-host.zip") as archive:
        for row in inventory["files"]:
            target = destination / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(row["path"]))
    verify_host(destination, inventory)
    return inventory


def probe_host(root: Path, output: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(root / "scripts/run_multi_agent_gen2.py"),
         "_probe", "--output", str(output)], cwd=root, text=True, capture_output=True,
        timeout=60, check=True,
    )
    return json.loads(result.stdout)


def freeze(output: Path) -> dict:
    if any((output / name).exists() for name in ("manifest.json", "private", "execution-host.zip")):
        raise ValueError("freeze refuses an existing freeze or prior execution evidence")
    document = shared.read_json(output / "tasks.json")
    validate_tasks(document)
    validate_offline_proof(output, document)
    module = runner(output)
    runtime = module.frozen_runtime()
    route = module.route_hash(runtime)
    resolved_route = module.resolved_transport_route(runtime)
    preflight = run_public_preflight(document, output / "public-preflight", runtime=runtime,
                                    require_pytest_report=True)
    if not preflight["passed"]:
        raise ValueError("public baseline preflight failed")
    files = host_files()
    inventory = inventory_for(files)
    shared.write_json(output / "execution-host-inventory.json", inventory)
    with zipfile.ZipFile(output / "execution-host.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with tempfile.TemporaryDirectory(prefix="minicode-gen2-freeze-") as temporary:
        host = Path(temporary) / "host"
        unpack_host(output, host)
        probe = probe_host(host, output)
    if (probe["model"] != MODEL or probe["routeSha256"] != route
            or probe["resolvedTransportRouteSha256"] != resolved_route):
        raise ValueError("private configuration resolves differently from the frozen execution host")
    shared.write_json(output / "environment.json", probe["environment"])
    public_hashes = {
        case["id"]: {name: shared.sha(content.encode()) for name, content in sorted(
            {**document.get("shared_files", {}), **case["files"]}.items())}
        for case in document["cases"]
    }
    registration = {p.relative_to(output).as_posix(): shared.sha(p.read_bytes())
                    for p in sorted(output.rglob("*")) if p.is_file()}
    manifest = {
        **static_contract(), "createdAt": datetime.now(timezone.utc).isoformat(),
        "taskSha256": registration["tasks.json"],
        "analysisPlanSha256": registration["analysis-plan.md"],
        "executionHostArchiveSha256": registration["execution-host.zip"],
        "executionHostInventorySha256": registration["execution-host-inventory.json"],
        "executionHostTreeSha256": inventory["treeSha256"],
        "environmentSha256": registration["environment.json"],
        "productionSha256": probe["productionSha256"], "harnessSha256": probe["harnessSha256"],
        "routeSha256": route, "resolvedTransportRouteSha256": resolved_route,
        "publicVerificationFixturePolicy": PUBLIC_FIXTURE_POLICY,
        "publicVerificationFixtures": public_fixture_manifest(document),
        "publicPreflightSha256": registration["public-preflight/receipt.json"],
        "publicBaselineHashes": public_hashes,
        "oracleHashes": {c["id"]: shared.sha(c["oracle_source"].encode()) for c in document["cases"]},
        "registrationFiles": registration,
        "guardian": "Shared 600-second execution deadline; 30-second setup; cleanup before common 120-second judge",
        "tokenBudgetSemantics": "Shared estimate admission; full provider usage retained even if estimates overshoot",
    }
    shared.write_json(output / "manifest.json", manifest)
    validate(output)
    return manifest


def validate(output: Path, *, host_root: Path | None = None) -> dict:
    manifest = shared.read_json(output / "manifest.json")
    if any(manifest.get(key) != value for key, value in static_contract().items()):
        raise ValueError("frozen gen2 contract changed")
    required = {"tasks.json", "analysis-plan.md", "execution-host.zip", "execution-host-inventory.json",
                "environment.json", "public-preflight/receipt.json",
                "materials/source-manifest.json", "materials/offline-proof.json"}
    registration = manifest.get("registrationFiles", {})
    if not required <= registration.keys():
        raise ValueError("registration omits required frozen materials")
    for name, digest in registration.items():
        safe_relative(name)
        path = output / name
        if (path.is_symlink() or not path.resolve().is_relative_to(output.resolve())
                or shared.sha(path.read_bytes()) != digest):
            raise ValueError("frozen registration material changed: " + name)
    for field, name in (("taskSha256", "tasks.json"), ("analysisPlanSha256", "analysis-plan.md"),
                        ("executionHostArchiveSha256", "execution-host.zip"),
                        ("executionHostInventorySha256", "execution-host-inventory.json"),
                        ("environmentSha256", "environment.json"),
                        ("publicPreflightSha256", "public-preflight/receipt.json")):
        if manifest.get(field) != registration[name]:
            raise ValueError("inconsistent registration hash: " + field)
    inventory = verify_archive(output)
    if inventory["treeSha256"] != manifest.get("executionHostTreeSha256"):
        raise ValueError("execution host tree hash changed")
    document = shared.read_json(output / "tasks.json")
    validate_tasks(document)
    validate_offline_proof(output, document)
    validate_public_preflight(document, output / "public-preflight/receipt.json",
                              manifest["publicPreflightSha256"], require_pytest_report=True)
    if host_root is not None:
        verify_host(host_root, inventory)
        if environment_fingerprint() != shared.read_json(output / "environment.json"):
            raise ValueError("frozen Python dependency environment changed")
        assert_host_imports(host_root)
    return manifest


def validate_offline_proof(output: Path, document: dict) -> None:
    source_path = output / "materials/source-manifest.json"
    proof_path = output / "materials/offline-proof.json"
    if not source_path.is_file() or not proof_path.is_file():
        raise ValueError("frozen source manifest and offline counterexample proof are required")
    proof = shared.read_json(proof_path)
    if not isinstance(proof, list) or [row.get("case") for row in proof] != list(CASES):
        raise ValueError("offline counterexample proof has the wrong case set")
    for case, row in zip(document["cases"], proof):
        if (row.get("oracleSha256") != shared.sha(case["oracle_source"].encode())
                or row.get("publicTestSha256") != shared.sha(case["files"]["tests/test_public.py"].encode())
                or any(row.get(name, {}).get("returncode") != code for name, code in (
                    ("baselinePublic", 0), ("baselineOracle", 1),
                    ("referencePublic", 0), ("referenceOracle", 0)))):
            raise ValueError("offline baseline/reference counterexample proof is not valid")
        try:
            before = json.loads(row["baselineOracle"]["stdout"])
            after = json.loads(row["referenceOracle"]["stdout"])
            targeted = (before.get("passed", before.get("ok")) is False
                        and bool(before.get("errors", before.get("failures")))
                        and after.get("passed", after.get("ok")) is True
                        and not after.get("errors", after.get("failures")))
        except (KeyError, TypeError, ValueError, AttributeError):
            targeted = False
        if not targeted:
            raise ValueError("offline proof must demonstrate a target assertion failure, not an import/setup error")


def require_committed_registration(output: Path, commit: str | None) -> str:
    """Check actual Git objects, not a flag or a mutable claimed commit field."""
    if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("model execution requires --preregistered-commit <full commit SHA>")
    manifest = validate(output)
    repository = Path(subprocess.check_output(
        ["git", "-C", str(output), "rev-parse", "--show-toplevel"], text=True).strip()).resolve()
    relative = output.resolve().relative_to(repository).as_posix()
    subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", commit, "HEAD"],
                   check=True, capture_output=True)
    for name in ["manifest.json", *manifest["registrationFiles"]]:
        frozen = subprocess.check_output(["git", "-C", str(repository), "show", f"{commit}:{relative}/{name}"])
        if frozen != (output / name).read_bytes():
            raise ValueError("preregistered commit does not contain frozen material: " + name)
    return commit


def assert_host_imports(root: Path) -> dict:
    loaded = {}
    for name, module in list(sys.modules.items()):
        if name == "minicode" or name.startswith("minicode.") or name == "scripts" or name.startswith("scripts."):
            location = getattr(module, "__file__", None)
            if location and not Path(location).resolve().is_relative_to(root.resolve()):
                raise ValueError("execution imported code outside the frozen host: " + name)
            if location:
                path = Path(location).resolve()
                loaded[name] = {"path": str(path), "sha256": shared.sha(path.read_bytes())}
    return loaded


def next_trial(output: Path, manifest: dict) -> str | None:
    missing = None
    for row in manifest["schedule"]:
        result = output / "trials" / (row["id"] + ".json")
        if result.exists():
            if missing is not None:
                raise ValueError("trial results do not form the frozen schedule prefix")
            recorded = shared.read_json(result)
            if recorded.get("id") != row["id"] or not recorded.get("processCleanup", {}).get("cleanupComplete"):
                raise ValueError("previous trial is malformed or has incomplete cleanup")
            judgement = recorded.get("judgement")
            if judgement is not None and not judgement.get("cleanupComplete"):
                raise ValueError("previous trial judge cleanup is incomplete")
        elif missing is None:
            missing = row["id"]
    return missing


def execution_host_receipt(output: Path, trial: str, commit: str, manifest: dict) -> None:
    root = output / "private" / trial
    if any((root / name).exists() for name in ("execution-host.json", "transport.sqlite", "execution-start.json")):
        raise ValueError("trial has durable evidence; never rerun or overwrite it")
    root.mkdir(parents=True, exist_ok=True)
    shared.write_json(root / "execution-host.json", {
        "schemaVersion": 1, "trialId": trial, "preregisteredCommit": commit,
        "manifestSha256": shared.sha((output / "manifest.json").read_bytes()),
        **{key: manifest[key] for key in ("executionHostInventorySha256", "executionHostTreeSha256",
            "executionHostArchiveSha256", "productionSha256", "harnessSha256")},
        "hostRoot": str(ROOT), "entrypoint": str(Path(__file__).resolve()),
        "pid": os.getpid(), "recordedAtEpoch": time.time(),
        "loadedModules": assert_host_imports(ROOT),
    })


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
        argv = [sys.executable, "-I", "-B", str(host / "scripts/run_multi_agent_gen2.py"),
                mode, "--output", str(output), "--preregistered-commit", commit,
                "--execution-host", str(host)]
        if trial:
            argv += ["--trial", trial]
        # exec preserves the guardian's pid/process ownership and avoids an
        # untracked wrapper process. The verified host remains as audit evidence.
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
        execution_host_receipt(output, trial, commit, manifest)
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
