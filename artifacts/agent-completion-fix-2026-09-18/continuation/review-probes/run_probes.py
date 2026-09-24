"""Read-only review of the completed external CodeLoop working tree.

Fixtures, logs, and results are written beside this script, never in TARGET.
Execute with the host acceptance interpreter and -I -B.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import patch


TARGET = Path("/private/tmp/codeloop-completion-recheck-20260918").resolve()
HERE = Path(__file__).resolve().parent
SOURCE = TARGET / "minicode/tools/test_runner.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(TARGET))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTEST_ADDOPTS", None)

runner = importlib.import_module("minicode.tools.test_runner")
ToolContext = importlib.import_module("minicode.tooling").ToolContext
assert Path(runner.__file__).resolve() == SOURCE
module_origins = {
    name: str(Path(module.__file__).resolve())
    for name, module in sys.modules.items()
    if name.startswith("minicode") and getattr(module, "__file__", None)
}
assert all(Path(path).is_relative_to(TARGET) for path in module_origins.values())
source_bytes = SOURCE.read_bytes()
source_sha = hashlib.sha256(source_bytes).hexdigest()
(HERE / "target-test_runner.py.snapshot").write_bytes(source_bytes)
real_run = subprocess.run


def fixture(name: str) -> Path:
    root = HERE / "fixtures" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider\n", encoding="utf-8"
    )
    return root


def execute_and_record(label: str, command: list[str], cwd: str, **kwargs):
    result = real_run(command, cwd=cwd, **kwargs)
    (HERE / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (HERE / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    return result


def run_tool(label: str, root: Path, **overrides):
    inputs = {
        "path": ".", "framework": "pytest", "verbose": False,
        "coverage": False, "pattern": None, "timeout": 30,
    }
    inputs.update(overrides)
    executions = []

    def record_actual_execution(command, *, cwd, **kwargs):
        result = execute_and_record(label, command, cwd, **kwargs)
        executions.append({"command": list(command), "returncode": result.returncode})
        return result

    # This is a transparent observer: no subprocess output is fabricated.
    with patch.object(runner.subprocess, "run", side_effect=record_actual_execution):
        result = runner.test_runner_tool.run(
            inputs, ToolContext(cwd=str(root), permissions=None)
        )
    assert len(executions) == 1, executions
    (HERE / f"{label}.tool-output.txt").write_text(result.output, encoding="utf-8")
    return {
        "inputs": inputs,
        "process": executions[0],
        "tool_ok": result.ok,
        "verification": result.verification,
    }


selected_root = fixture("selection")
for number in range(201):
    (selected_root / f"test_selected_{number:03d}.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
(selected_root / "test_other.py").write_text(
    "def test_selected_outside_filename():\n"
    "    assert False, 'this file is excluded by the requested filename filter'\n",
    encoding="utf-8",
)
discovered = runner._discover_test_files(selected_root)
selected = [file for file in discovered if "selected" in file.name]
assert len(discovered) == 202 and len(selected) == 201

flags_200, directory_200 = runner._build_pytest_command(
    selected_root, selected[:200], True, True, "selected"
)
flags_201, directory_201 = runner._build_pytest_command(
    selected_root, selected, True, True, "selected"
)
requested_flags = ["-v", "--cov", "-k"]
probe_flags = {
    "expected": "All requested flags are preserved on both sides of 200 files.",
    "command_200": flags_200,
    "command_201": flags_201,
    "directory_200": directory_200,
    "directory_201": directory_201,
    "missing_flags_200": [flag for flag in requested_flags if flag not in flags_200],
    "missing_flags_201": [flag for flag in requested_flags if flag not in flags_201],
}
probe_flags["defect_reproduced"] = (
    probe_flags["missing_flags_200"] == []
    and probe_flags["missing_flags_201"] == requested_flags
)

selection_run = run_tool("selection-actual", selected_root, pattern="selected")
explicit_command = [sys.executable, "-m", "pytest", *map(str, selected), "-k", "selected"]
explicit = execute_and_record(
    "selection-explicit-oracle", explicit_command, str(selected_root),
    capture_output=True, text=True, timeout=30,
)
# Restore the missing -k only in this diagnostic command. Production is untouched.
directory_with_pattern_command = selection_run["process"]["command"] + ["-k", "selected"]
directory_with_pattern = execute_and_record(
    "selection-directory-with-pattern", directory_with_pattern_command,
    str(selected_root), capture_output=True, text=True, timeout=30,
)
probe_selection = {
    "expected": "Only the 201 selected filenames execute; the tool reports success.",
    "discovered_files": len(discovered), "selected_files": len(selected),
    "actual": selection_run,
    "explicit_selection_returncode": explicit.returncode,
    "explicit_selection_command": explicit_command,
    "directory_with_restored_pattern_returncode": directory_with_pattern.returncode,
    "directory_with_restored_pattern_command": directory_with_pattern_command,
    "defect_reproduced": (
        selection_run["process"]["returncode"] == 1
        and selection_run["tool_ok"] is False
        and explicit.returncode == 0 and directory_with_pattern.returncode == 1
    ),
}

warning_root = fixture("warnings")
(warning_root / "test_warning.py").write_text(
    "import warnings\n\n"
    "def test_ok():\n"
    "    warnings.warn('1 failed historical request; 2 errors already recovered')\n"
    "    assert True\n", encoding="utf-8",
)
warning_run = run_tool("warning-actual", warning_root)
warning_stdout = (HERE / "warning-actual.stdout.txt").read_text(encoding="utf-8")
probe_warnings = {
    "expected": "A passing suite with a historical failure warning remains successful.",
    "actual": warning_run,
    "parsed_output": runner._parse_pytest_output(warning_stdout),
    "defect_reproduced": (
        warning_run["process"]["returncode"] == 0
        and warning_run["tool_ok"] is False
        and warning_run["verification"]["outcome"] == "failed"
    ),
}

assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_sha
report = {
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "target_root": str(TARGET), "target_source": str(SOURCE),
    "target_source_sha256": source_sha,
    "target_head": real_run(
        ["git", "rev-parse", "HEAD"], cwd=TARGET,
        capture_output=True, text=True, check=True,
    ).stdout.strip(),
    "python": sys.executable,
    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "minicode_module_origins": module_origins,
    "probes": {
        "missing_optional_flags": probe_flags,
        "selection_contract_changed": probe_selection,
        "warning_false_failure": probe_warnings,
    },
    "target_source_unchanged_during_probes": True,
    "subprocess_output_fabricated": False,
}
(HERE / "results.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({
    "target_sha256": source_sha,
    "module": runner.__file__,
    "defect_reproduced": {
        name: probe["defect_reproduced"] for name, probe in report["probes"].items()
    },
    "results": str(HERE / "results.json"),
}, indent=2))
assert all(probe["defect_reproduced"] for probe in report["probes"].values())
