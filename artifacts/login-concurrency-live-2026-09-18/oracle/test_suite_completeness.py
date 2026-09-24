#!/usr/bin/env python3
"""Independent, real-subprocess acceptance check for CodeLoop's test runner.

Usage:
    python3 test_suite_completeness.py --repo /path/to/repo --expect baseline
    python3 test_suite_completeness.py --repo /path/to/fixed/repo --expect fixed

No production code or subprocess call is mocked. Each generated test writes an
execution marker, so acceptance does not rely only on the wrapper's summary.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def make_suite(directory: Path, *, late_failure: bool) -> None:
    directory.mkdir()
    (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for index in range(1, 13):
        assertion = "False, 'late failure sentinel'" if late_failure and index == 12 else "True"
        (directory / f"test_{index:03d}.py").write_text(
            "from pathlib import Path\n\n"
            f"def test_case_{index:03d}():\n"
            "    Path(__file__).with_suffix('.ran').write_text('executed')\n"
            f"    assert {assertion}\n",
            encoding="utf-8",
        )


def clear_markers(directory: Path) -> None:
    for marker in directory.glob("*.ran"):
        marker.unlink()


def markers(directory: Path) -> list[str]:
    return sorted(path.stem for path in directory.glob("*.ran"))


def wrapper_counts(output: str) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for name in ("Passed", "Failed", "Errors", "Skipped"):
        match = re.search(rf"\b{name}:\s+(\d+)", output)
        counts[name.lower()] = int(match.group(1)) if match else None
    return counts


def run_check(repo: Path, expectation: str) -> dict[str, Any]:
    # Isolate pytest behavior from ambient user addopts and third-party plugins.
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ.pop("PYTEST_ADDOPTS", None)
    sys.path.insert(0, str(repo))
    runner = importlib.import_module("minicode.tools.test_runner")
    tooling = importlib.import_module("minicode.tooling")
    loaded = Path(runner.__file__).resolve()
    expected_module = repo / "minicode" / "tools" / "test_runner.py"
    if loaded != expected_module.resolve():
        raise RuntimeError(f"Wrong production module loaded: {loaded}")

    report: dict[str, Any] = {
        "repo": str(repo),
        "production_module": str(loaded),
        "python": sys.executable,
        "expectation": expectation,
        "mocked_execution": False,
    }
    with tempfile.TemporaryDirectory(prefix="codeloop-suite-oracle-") as temporary:
        root = Path(temporary).resolve()
        failing = root / "late_failure"
        passing = root / "all_passing"
        make_suite(failing, late_failure=True)
        make_suite(passing, late_failure=False)

        # Establish that the generated workload really contains 12 executable
        # tests and the last one fails under pytest itself.
        witness = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(failing)],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        witness_output = witness.stdout + witness.stderr
        report["direct_pytest_witness"] = {
            "returncode": witness.returncode,
            "executed": markers(failing),
            "output": witness_output,
        }
        witness_valid = (
            witness.returncode == 1
            and len(markers(failing)) == 12
            and "1 failed, 11 passed" in witness_output
            and "late failure sentinel" in witness_output
        )
        if not witness_valid:
            report["accepted"] = False
            report["error"] = "Generated workload did not reproduce its intended failure."
            return report

        def invoke(target: Path, suite: Path) -> dict[str, Any]:
            clear_markers(suite)
            result = runner._run(
                {
                    "path": str(target.relative_to(root)),
                    "framework": "pytest",
                    "verbose": True,
                    "coverage": False,
                    "pattern": None,
                    "timeout": 30,
                },
                tooling.ToolContext(cwd=str(root), permissions=None),
            )
            return {
                "ok": result.ok,
                "verification": result.verification,
                "counts": wrapper_counts(result.output),
                "executed": markers(suite),
                "output": result.output,
            }

        late = invoke(failing, failing)
        full = invoke(passing, passing)
        single = invoke(failing / "test_001.py", failing)
        report["cases"] = {
            "late_failure": late,
            "all_passing": full,
            "single_file_scope": single,
        }
        checks = {
            "direct_pytest_witness_is_valid": witness_valid,
            "single_file_does_not_expand_scope": (
                single["ok"] is True
                and single["counts"]["passed"] == 1
                and single["counts"]["failed"] == 0
                and single["executed"] == ["test_001"]
                and single["verification"]["outcome"] == "passed"
            ),
        }
        if expectation == "baseline":
            checks.update(
                baseline_falsely_reports_late_failure_as_success=(
                    late["ok"] is True
                    and late["counts"]["passed"] == 10
                    and late["counts"]["failed"] == 0
                    and len(late["executed"]) == 10
                    and "test_012" not in late["executed"]
                    and late["verification"]["outcome"] == "passed"
                ),
                baseline_omits_two_passing_test_files=(
                    full["ok"] is True
                    and full["counts"]["passed"] == 10
                    and len(full["executed"]) == 10
                ),
            )
        else:
            checks.update(
                detects_failure_in_twelfth_file=(
                    late["ok"] is False
                    and late["counts"]["passed"] == 11
                    and late["counts"]["failed"] == 1
                    and len(late["executed"]) == 12
                    and late["verification"]["outcome"] == "failed"
                ),
                executes_all_twelve_passing_test_files=(
                    full["ok"] is True
                    and full["counts"]["passed"] == 12
                    and full["counts"]["failed"] == 0
                    and len(full["executed"]) == 12
                    and full["verification"]["outcome"] == "passed"
                ),
            )
        report["checks"] = checks
        report["accepted"] = all(checks.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--expect", choices=("baseline", "fixed"), default="fixed")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_check(args.repo.expanduser().resolve(), args.expect)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {
        "accepted": report["accepted"],
        "expectation": report["expectation"],
        "checks": report.get("checks", {}),
        "counts": {
            name: {"ok": case["ok"], **case["counts"], "executed": len(case["executed"])}
            for name, case in report.get("cases", {}).items()
        },
    }
    if "error" in report:
        summary["error"] = report["error"]
    print(json.dumps(summary, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
