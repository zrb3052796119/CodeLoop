"""Replay identical public-loop tests against the saved source pair and candidate."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
output = Path(__file__).resolve().parent
test = root / "tests/test_progress_delegation.py"
nodes = [
    "test_actual_implementation_loop_without_task_never_suggests_delegation[0]",
    "test_actual_loop_suggests_once_after_eight_batches_even_for_one_file",
]
records = {}
for variant in ("before", "after"):
    with tempfile.TemporaryDirectory(prefix="progress-counterexample-") as temporary:
        checkout = Path(temporary)
        shutil.copytree(root / "minicode", checkout / "minicode",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if variant == "before":
            for name in ("coding_progress.py", "agent_loop.py"):
                shutil.copyfile(output / "progress-before" / name, checkout / "minicode" / name)
        shutil.copyfile(test, checkout / test.name)
        driver = (
            "import pathlib,sys;sys.path.insert(0,sys.argv[1]);"
            "import minicode.agent_loop as loop,minicode.coding_progress as progress;"
            "assert pathlib.Path(loop.__file__).parent==pathlib.Path(sys.argv[1])/'minicode';"
            "assert pathlib.Path(progress.__file__).parent==pathlib.Path(sys.argv[1])/'minicode';"
            "import pytest;sys.exit(pytest.main(sys.argv[2:]))"
        )
        command = [sys.executable, "-I", "-B", "-c", driver, str(checkout),
            "-q", "-p", "no:cacheprovider", *[f"{test.name}::{node}" for node in nodes]]
        completed = subprocess.run(command, cwd=checkout, capture_output=True, text=True,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, timeout=60)
        log = completed.stdout + completed.stderr
        (output / f"progress-counterexample-{variant}.txt").write_text(log)
        records[variant] = {
            "returncode": completed.returncode,
            "sourceSha256": {name: hashlib.sha256((checkout / "minicode" / name).read_bytes()).hexdigest()
                for name in ("coding_progress.py", "agent_loop.py")},
            "samePublicLoopTests": nodes,
            "testSha256": hashlib.sha256(test.read_bytes()).hexdigest(),
            "importOriginsVerified": True,
        }
        if variant == "before":
            assert completed.returncode == 1 and "2 failed" in log
            assert "AssertionError" in log and "TypeError" not in log and "ImportError" not in log
        else:
            assert completed.returncode == 0 and "2 passed" in log
records["scope"] = "Working-tree source pair saved before Phase 2; not a pristine HEAD claim. No provider requests."
(output / "progress-counterexample.json").write_text(json.dumps(records, indent=2) + "\n")
print(json.dumps({variant: records[variant]["returncode"] for variant in ("before", "after")}))
