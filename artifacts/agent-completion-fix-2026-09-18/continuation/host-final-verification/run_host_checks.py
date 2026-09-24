"""Execute the explicitly scoped host-only deterministic verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time


OUTPUT = Path(__file__).resolve().parent
SCOPE = json.loads((OUTPUT / "scope.json").read_text())
ROOT = Path(SCOPE["root"])
PYTHON = SCOPE["python"]
TESTS = SCOPE["task_tests"] + SCOPE["existing_tests"]
FILES = SCOPE["production_files"] + SCOPE["task_tests"]


def snapshot() -> dict[str, str]:
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in FILES
    }


def main() -> int:
    before = snapshot()
    (OUTPUT / "hashes-before.json").write_text(json.dumps(before, indent=2) + "\n")
    commands = [
        ("tests", [PYTHON, "-m", "pytest", *TESTS, "-q", "--tb=short"]),
        ("lint", [PYTHON, "-m", "ruff", "check", *FILES, "--select=E,F", "--ignore=E501"]),
        ("compile", [PYTHON, "-m", "compileall", "-q", *FILES]),
        ("diff-check", ["git", "diff", "--check", "--", *FILES]),
    ]
    results = {}
    for name, command in commands:
        started = time.monotonic()
        process = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=180,
            check=False,
        )
        result = {
            "command": command,
            "cwd": str(ROOT),
            "returncode": process.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        results[name] = result
        (OUTPUT / f"{name}.stdout.txt").write_text(process.stdout)
        (OUTPUT / f"{name}.stderr.txt").write_text(process.stderr)
        (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({"check": name, **result}), flush=True)
    after = snapshot()
    hash_record = {
        "before": before,
        "after": after,
        "all_scoped_source_and_test_files_unchanged": before == after,
    }
    (OUTPUT / "hash-comparison.json").write_text(json.dumps(hash_record, indent=2) + "\n")
    print(json.dumps({"all_scoped_source_and_test_files_unchanged": before == after}))
    return 0 if before == after and all(result["returncode"] == 0 for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
