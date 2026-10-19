"""Prospective common final checks on delivered copies, under one parent deadline.

This is a bounded evaluator for non-adversarial generated tests, not a sandbox
against arbitrary hostile Python. A host completion receipt plus exit status and
process cleanup is required; pytest console text and generated XML are not proof.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
# An editable checkout may already be later in sys.path, behind a delivered
# scripts package. Load the trusted guardian first; stage() then selects the
# delivered workspace for all minicode imports.
sys.path.insert(0, str(ROOT))
from scripts.three_arm_processes import ProcessEvidence, stop_trial  # noqa: E402

REGRESSION_PATH = "tests/test_eval_regression.py"
JUDGE_POLICY = {
    "version": "common-generated-regression-v1",
    "totalSeconds": 120,
    "regressionPath": "tests/test_eval_regression.py",
    "minimumPassed": 1,
    "requireEveryCollectedPassed": True,
    "allowSkipOrXfail": False,
    "separateUnfilteredRegressionRun": True,
    "deliveredCopiesOnly": True,
    "receiptExitAndCleanupRequired": True,
    "agentBudgetSeconds": 1800,
}


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    temporary.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_counts() -> dict:
    return {
        k: 0 for k in ("collected", "passed", "failed", "errors", "skipped", "xfailed")
    }


class RegressionReports:
    def __init__(self):
        self.nodes = []
        self.reports = {}
        self.collection_errors = 0
        self.collection_skips = 0
        self.finished = False
        self.exit_status = None

    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        self.collection_errors += int(report.failed)
        self.collection_skips += int(report.skipped)

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, {})[report.when] = {
            "outcome": report.outcome,
            "xfail": hasattr(report, "wasxfail"),
        }

    def pytest_sessionfinish(self, session, exitstatus):
        self.finished = True
        self.exit_status = int(exitstatus)

    def receipt(self):
        counts = empty_counts()
        counts["collected"] = len(self.nodes)
        counts["errors"] = self.collection_errors
        counts["skipped"] = self.collection_skips
        complete = len(set(self.nodes)) == len(self.nodes)
        for node in self.nodes:
            phases = self.reports.get(node, {})
            complete &= set(phases) == {"setup", "call", "teardown"}
            xfail = any(v["xfail"] for v in phases.values())
            counts["xfailed"] += int(xfail)
            counts["failed"] += int(phases.get("call", {}).get("outcome") == "failed")
            counts["errors"] += sum(
                v["outcome"] == "failed" for k, v in phases.items() if k != "call"
            )
            counts["skipped"] += int(
                any(v["outcome"] == "skipped" for v in phases.values()) and not xfail
            )
            counts["passed"] += int(
                set(phases) == {"setup", "call", "teardown"}
                and all(v["outcome"] == "passed" for v in phases.values())
                and not xfail
            )
        return {
            "counts": counts,
            "items": [
                {"nodeId": node, "phases": self.reports.get(node, {})}
                for node in self.nodes
            ],
            "collectionErrors": self.collection_errors,
            "collectionSkips": self.collection_skips,
            "sessionFinished": self.finished,
            "sessionExitStatus": self.exit_status,
            "allPhasesComplete": complete,
        }


def _origins(workspace: Path) -> dict:
    seen, foreign = [], []
    for name, module in sys.modules.items():
        if name == "minicode" or name.startswith("minicode."):
            filename = getattr(module, "__file__", None)
            if filename:
                seen.append(name)
                if not Path(filename).resolve().is_relative_to(workspace / "minicode"):
                    foreign.append(name)
    return {"checkedModules": len(seen), "foreignModules": sorted(foreign)}


def stage(spec_path: Path) -> int:
    """Trusted bootstrap writes completion only after Python/pytest returns."""
    spec = json.loads(spec_path.read_text())
    workspace = Path(spec["workspace"]).resolve()
    sys.path.insert(0, str(workspace))
    os.chdir(workspace)
    receipt = {
        "schemaVersion": 1,
        "nonce": spec["nonce"],
        "kind": spec["kind"],
        "workspace": str(workspace),
        "normalReturn": False,
        "testSha256": spec.get("testSha256"),
    }
    plugin = None
    code = 1
    with ProcessEvidence(spec["processEvidence"]):
        try:
            if (workspace / "minicode").is_dir():
                origin = importlib.util.find_spec("minicode")
                if (
                    origin is None
                    or not origin.origin
                    or Path(origin.origin).resolve().parent != workspace / "minicode"
                ):
                    raise RuntimeError("wrong delivered import root")
            if spec["kind"] == "oracle":
                sys.argv = [spec["oraclePath"], str(workspace), spec["responsePath"]]
                try:
                    runpy.run_path(spec["oraclePath"], run_name="__main__")
                    code = 0
                except SystemExit as result:
                    code = (
                        int(result.code)
                        if isinstance(result.code, int)
                        else 0
                        if result.code is None
                        else 1
                    )
            else:
                import pytest

                plugin = RegressionReports()
                plugins = [plugin]
                guard_path = workspace / "conftest.py"
                if guard_path.exists():
                    loader = importlib.util.spec_from_file_location(
                        "frozen_generated_test_guard", guard_path
                    )
                    guard = importlib.util.module_from_spec(loader)
                    loader.loader.exec_module(guard)
                    plugins.append(guard)
                config = workspace / "pyproject.toml"
                if not config.exists():
                    config = Path(spec["emptyConfig"])
                # No inherited -k/-m/addopts: every generated test must run.
                args = [
                    "-q",
                    "--noconftest",
                    "-p",
                    "no:cacheprovider",
                    "--rootdir",
                    str(workspace),
                    "-c",
                    str(config),
                    "-o",
                    "addopts=",
                    str(workspace / REGRESSION_PATH),
                ]
                code = int(pytest.main(args, plugins=plugins))
            receipt["normalReturn"] = True
        except BaseException as error:
            receipt["errorClass"] = type(error).__name__
        receipt["exitCode"] = code
        receipt["origins"] = _origins(workspace)
        if receipt["origins"]["foreignModules"]:
            code = receipt["exitCode"] = 1
        if plugin is not None:
            receipt.update(plugin.receipt())
        write_json(Path(spec["receiptPath"]), receipt)
    return code


def _copy_delivered(source: Path, destination: Path, deadline: float) -> None:
    def copy_file(src, dst):
        if time.monotonic() >= deadline:
            raise TimeoutError("judge deadline during snapshot")
        if not Path(src).resolve().is_relative_to(source):
            raise ValueError("delivered snapshot link escapes workspace")
        result = shutil.copy2(src, dst)
        if time.monotonic() >= deadline:
            raise TimeoutError("judge deadline during snapshot")
        return result

    shutil.copytree(source, destination, copy_function=copy_file)


def _run_stage(
    kind, *, workspace, root, oracle_path, response_path, env, deadline, total_seconds
):
    started = time.monotonic()
    directory = root / kind
    directory.mkdir()
    result = {
        "status": "not_started",
        "passed": False,
        "durationSeconds": 0.0,
        "counts": empty_counts() if kind == "regression" else None,
        "exitCode": None,
        "receiptSha256": None,
        "cleanup": None,
        "testSha256": None,
        "snapshot": str(directory / "workspace"),
    }
    proc = None
    try:
        remaining = deadline - time.monotonic()
        reserve = min(12.0, total_seconds / 4)
        if remaining <= reserve:
            result["status"] = "deadline_exhausted"
            return result
        if kind == "regression":
            source = workspace / REGRESSION_PATH
            if not source.is_file():
                result["status"] = "missing"
                return result
            result["testSha256"] = digest(source)
        _copy_delivered(workspace, directory / "workspace", deadline)
        nonce = uuid.uuid4().hex
        receipt_path = directory / "receipt.json"
        process_path = directory / "processes.jsonl"
        empty_config = directory / "pytest.ini"
        empty_config.write_text("[pytest]\n")
        spec = {
            "kind": kind,
            "nonce": nonce,
            "workspace": result["snapshot"],
            "oraclePath": str(oracle_path),
            "responsePath": str(response_path),
            "receiptPath": str(receipt_path),
            "processEvidence": str(process_path),
            "emptyConfig": str(empty_config),
            "testSha256": result["testSha256"],
        }
        write_json(directory / "stage.json", spec)
        remaining = deadline - time.monotonic() - reserve
        if remaining <= 0:
            result["status"] = "deadline_exhausted"
            return result
        child_env = {
            **env,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": result["snapshot"],
        }
        for key in (
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "PYTHONINSPECT",
        ):
            child_env.pop(key, None)
        child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        child_env["PYTHONNOUSERSITE"] = "1"
        with (directory / "output.log").open("w") as log:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "stage",
                    str(directory / "stage.json"),
                ],
                cwd=directory,
                env=child_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            # Persist the launched identity even if bootstrap import fails.
            with process_path.open("a") as evidence:
                evidence.write(json.dumps({"pid": proc.pid, "pgid": proc.pid}) + "\n")
                evidence.flush()
                os.fsync(evidence.fileno())
            try:
                result["exitCode"] = proc.wait(timeout=remaining)
                result["status"] = "failed"
            except subprocess.TimeoutExpired:
                result["status"] = "timeout"
        remaining = max(0.001, deadline - time.monotonic())
        phase_limit = max(0.001, min(5.0, remaining / 3))
        result["cleanup"] = stop_trial(
            proc.pid,
            process_path,
            freeze_timeout_seconds=phase_limit,
            kill_confirm_timeout_seconds=phase_limit,
        )
        try:
            proc.wait(timeout=max(0.001, min(0.1, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            result["cleanup"]["cleanupComplete"] = False
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            result["receiptSha256"] = digest(receipt_path)
            if isinstance(receipt.get("counts"), dict):
                result["counts"] = receipt["counts"]
            valid = (
                receipt.get("schemaVersion") == 1
                and receipt.get("nonce") == nonce
                and receipt.get("kind") == kind
                and receipt.get("workspace") == result["snapshot"]
                and receipt.get("testSha256") == result["testSha256"]
                and receipt.get("normalReturn") is True
                and receipt.get("exitCode") == 0
                and receipt.get("origins", {}).get("foreignModules") == []
            )
            if kind == "regression":
                counts = result["counts"]
                valid = (
                    valid
                    and receipt.get("sessionFinished") is True
                    and receipt.get("sessionExitStatus") == 0
                    and receipt.get("allPhasesComplete") is True
                    and counts["collected"] >= 1
                    and counts["passed"] == counts["collected"]
                    and all(
                        counts[k] == 0
                        for k in ("failed", "errors", "skipped", "xfailed")
                    )
                )
            result["passed"] = bool(
                valid
                and result["exitCode"] == 0
                and result["cleanup"]["cleanupComplete"]
                and time.monotonic() < deadline
                and result["status"] != "timeout"
            )
            if result["passed"]:
                result["status"] = "passed"
        elif result["status"] != "timeout":
            result["status"] = "missing_receipt"
        if not result["cleanup"]["cleanupComplete"]:
            result["status"] = "cleanup_incomplete"
        if time.monotonic() >= deadline:
            result["passed"] = False
            result["status"] = "deadline_exhausted"
    except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
        result["status"] = (
            "deadline_exhausted" if isinstance(error, TimeoutError) else "judge_error"
        )
        result["errorClass"] = type(error).__name__
    finally:
        if proc is not None and result["cleanup"] is None:
            remaining = max(0.003, deadline - time.monotonic())
            result["cleanup"] = stop_trial(
                proc.pid,
                directory / "processes.jsonl",
                timeout_seconds=min(5.0, remaining / 3),
            )
            result["passed"] = False
        result["durationSeconds"] = time.monotonic() - started
        result["finishedAtSeconds"] = time.monotonic() - (deadline - total_seconds)
        if result["finishedAtSeconds"] >= total_seconds:
            result["passed"] = False
            result["status"] = "deadline_exhausted"
        if result["cleanup"] is not None:
            write_json(directory / "cleanup.json", result["cleanup"])
        write_json(directory / "result.json", result)
    return result


def run_judgement(
    workspace: Path,
    root: Path,
    oracle_path: Path,
    response_path: Path,
    *,
    env=None,
    total_seconds=120,
    started_at=None,
) -> dict:
    """Called once after agent cleanup; copying and cleanup share this deadline."""
    started = time.monotonic() if started_at is None else started_at
    root.mkdir()
    deadline = started + total_seconds
    common = dict(
        workspace=workspace.resolve(),
        root=root,
        oracle_path=oracle_path.resolve(),
        response_path=response_path.resolve(),
        env=dict(os.environ if env is None else env),
        deadline=deadline,
        total_seconds=total_seconds,
    )
    oracle = _run_stage("oracle", **common)
    if oracle["cleanup"] is not None and not oracle["cleanup"].get("cleanupComplete"):
        regression = {
            "status": "not_run_previous_cleanup_incomplete",
            "passed": False,
            "durationSeconds": 0.0,
            "counts": empty_counts(),
            "exitCode": None,
            "receiptSha256": None,
            "cleanup": None,
            "testSha256": None,
            "snapshot": str(root / "regression/workspace"),
        }
        (root / "regression").mkdir()
        write_json(root / "regression/result.json", regression)
    else:
        regression = _run_stage("regression", **common)
    seconds = time.monotonic() - started
    timed_out = seconds >= total_seconds
    if timed_out:
        regression["passed"] = False
        regression["status"] = "deadline_exhausted"
        write_json(root / "regression/result.json", regression)
    result = {
        "schemaVersion": 1,
        "policyVersion": JUDGE_POLICY["version"],
        "limitSeconds": total_seconds,
        "durationSeconds": seconds,
        "deadlineExceeded": timed_out,
        "oracle": oracle,
        "regressionTest": regression,
        "cleanupComplete": all(
            s["cleanup"] is None or s["cleanup"].get("cleanupComplete") is True
            for s in (oracle, regression)
        ),
    }
    write_json(root / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("stage",))
    parser.add_argument("spec", type=Path)
    args = parser.parse_args()
    raise SystemExit(stage(args.spec.resolve()))
