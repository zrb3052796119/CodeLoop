#!/usr/bin/env python3
"""Prepare real-history gen-2 tasks and prove their hidden checks offline.

No production imports, runtime construction, credentials, or model calls occur.
The public source is the actual pre-fix Git tree; reference files are exact blobs
from its actual successor commit, never a synthetic reverse mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "9aaa6a434ae0a86766d28f8b1b91529451a31eb8"
REFERENCE = "ad20e4a893248a747e3b3ac741c280ded413ff54"
CANDIDATES = ("read-context", "registry-lifetime", "summary-budget")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def source_files(revision: str) -> dict[str, str]:
    archive = subprocess.check_output(["git", "archive", "--format=zip", revision, "minicode", "pyproject.toml"], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        return {name: bundle.read(name).decode("utf-8") for name in sorted(bundle.namelist())
                if (name.startswith("minicode/") and name.endswith(".py")) or name == "pyproject.toml"}


def materialize(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def run(command: list[str], root: Path) -> dict:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root),
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for key in ("PYTEST_ADDOPTS", "PYTHONHOME"):
        environment.pop(key, None)
    result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=90)
    return {"argv": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def target_failure_proven(baseline: dict, reference: dict) -> bool:
    """Import/setup failures and arbitrary nonzero exits are not counterexamples."""
    try:
        before = json.loads(baseline["stdout"])
        after = json.loads(reference["stdout"])
        return (baseline["returncode"] == 1 and reference["returncode"] == 0
                and before.get("passed", before.get("ok")) is False
                and bool(before.get("errors", before.get("failures")))
                and after.get("passed", after.get("ok")) is True
                and not after.get("errors", after.get("failures")))
    except (ValueError, TypeError, AttributeError, KeyError):
        return False


def prepare(candidates: Path, output: Path, *, only: str | None = None) -> None:
    if output.exists():
        raise ValueError("output already exists; preserve earlier offline evidence")
    assert subprocess.check_output(["git", "rev-parse", REFERENCE + "^"], cwd=ROOT, text=True).strip() == BASE
    shared, reference = source_files(BASE), source_files(REFERENCE)
    cases, proof = [], []
    output.mkdir(parents=True)
    for name in CANDIDATES:
        if only and name != only:
            continue
        directory = candidates / name
        case = json.loads((directory / "case.json").read_text())
        oracle = (directory / "oracle.py").read_text()
        public = (directory / "test_public.py").read_text()
        case["files"] = {"tests/test_public.py": public}
        case["oracle_source"] = oracle
        sources = case["sourcePaths"]
        assert all(path in shared and path in reference for path in sources)
        assert "tests/test_eval_regression.py" in case["authorized_paths"]
        assert "tests/test_eval_regression.py" not in shared
        case["provenance"]["sourceBlobs"] = {
            path: {"baselineSha256": sha(shared[path].encode()), "referenceSha256": sha(reference[path].encode())}
            for path in sources}
        fixed = {**shared, **{path: reference[path] for path in sources}}
        evidence = {"case": case["id"], "oracleSha256": sha(oracle.encode()),
                    "publicTestSha256": sha(public.encode()), "baselineCommit": BASE,
                    "referenceCommit": REFERENCE, "referenceProjection": "baseline tree plus exact reference sourcePaths blobs"}
        with tempfile.TemporaryDirectory(prefix="gen2-proof-") as temporary:
            parent = Path(temporary)
            oracle_path = parent / "hidden_oracle.py"
            oracle_path.write_text(oracle)
            for label, files in (("baseline", shared), ("reference", fixed)):
                root = parent / label
                materialize(root, {**files, **case["files"]})
                evidence[label + "Public"] = run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "tests/test_public.py"], root)
                evidence[label + "Oracle"] = run([sys.executable, "-B", str(oracle_path), str(root), str(parent / "response.txt")], root)
            proof.append(evidence)
            write_json(output / "offline-proof.json", proof)
            if evidence["baselinePublic"]["returncode"] or evidence["referencePublic"]["returncode"]:
                raise ValueError(f"{name}: public compatibility check failed")
            if not target_failure_proven(evidence["baselineOracle"], evidence["referenceOracle"]):
                raise ValueError(f"{name}: real counterexample not proven; inspect offline-proof.json")
        cases.append(case)
    write_json(output / "tasks.json", {"generation": "gen-2", "shared_files": shared, "cases": cases})
    write_json(output / "source-manifest.json", {
        "baselineCommit": BASE, "referenceCommit": REFERENCE,
        "sharedFiles": {path: sha(content.encode()) for path, content in shared.items()},
        "cases": [{"id": case["id"], "provenance": case["provenance"]} for case in cases],
        "offlineProofSha256": sha((output / "offline-proof.json").read_bytes()),
        "taskSha256": sha((output / "tasks.json").read_bytes()),
        "modelCalls": 0,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=ROOT / "artifacts/multi-agent-gen2-2026-10-23/candidates")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=CANDIDATES)
    args = parser.parse_args()
    prepare(args.candidates, args.output, only=args.only)
