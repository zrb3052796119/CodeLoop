"""Read-only certification diagnosis using identical harness and real old source."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import memory_retrieval_production_baseline as baseline  # noqa: E402

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "baseline.json").read_text())
OLD = Path(FROZEN["root"])
TESTS = [
    "tests/test_memory_retrieval_production_baseline_v40.py",
    "tests/test_memory_retrieval_production_baseline_v41.py",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


integrity_failures = [
    path for path, expected in FROZEN["production_sha256"].items()
    if digest(OLD / path) != expected
]
assert not integrity_failures, integrity_failures
assert all((ROOT / path).read_bytes() == (OLD / path).read_bytes() for path in TESTS)
v39 = baseline.load_baseline_manifest("v39")
v40 = baseline.load_baseline_manifest("v40")
protected = set(v40["files"]) | set(baseline.EXPECTED_V41_ADDED_FILES)
temporary = Path(tempfile.mkdtemp(prefix="minicode-memory-freeze-old-"))
shutil.copytree(OLD / "minicode", temporary / "minicode", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(ROOT / "pyproject.toml", temporary / "pyproject.toml")
supplemental = [
    "scripts/memory_retrieval_production_baseline.py",
    "scripts/generate_memory_retrieval_production_baseline.py",
    *sorted(path for path in protected if not (OLD / path).is_file()),
]
for path in supplemental:
    (temporary / path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / path, temporary / path)
for path in TESTS:
    (temporary / path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OLD / path, temporary / path)
for number in range(1, 41):
    path = Path(f"tests/fixtures/memory_retrieval_production_freeze/v{number}.json")
    assert digest(OLD / path) == digest(ROOT / path) == baseline.PINNED_MANIFEST_SHA256[f"v{number}"]
    (temporary / path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OLD / path, temporary / path)

data = {
    "snapshot": str(OLD), "oldComparisonRoot": str(temporary),
    "python": sys.executable,
    "snapshotProductionFilesVerified": len(FROZEN["production_sha256"]),
    "snapshotIntegrityFailures": integrity_failures,
    "testHarnessSha256": {path: digest(ROOT / path) for path in TESTS},
    "commonUnsnapshottedHarness": {path: digest(ROOT / path) for path in supplemental},
    "manifestVersionsVerifiedUnchanged": list(range(1, 41)),
    "sourceRows": [], "gates": {}, "testRuns": {},
}
for path in sorted(protected):
    old_digest, new_digest = digest(temporary / path), digest(ROOT / path)
    data["sourceRows"].append({
        "path": path, "oldSha256": old_digest, "currentSha256": new_digest,
        "v39Sha256": v39["files"].get(path), "v40Sha256": v40["files"].get(path),
        "changedAfterSnapshot": old_digest != new_digest,
        "declaredV41Changed": path in baseline.EXPECTED_V41_CHANGED_FILES,
        "declaredV41Added": path in baseline.EXPECTED_V41_ADDED_FILES,
    })
for label, tree in [("old", temporary), ("current", ROOT)]:
    for version, parent, expected, additions in [
        ("v40", v39, baseline.EXPECTED_V40_CHANGED_FILES, baseline.EXPECTED_V40_ADDED_FILES),
        ("v41", v40, baseline.EXPECTED_V41_CHANGED_FILES, baseline.EXPECTED_V41_ADDED_FILES),
    ]:
        hashes = baseline._hash_files(tree, set(parent["files"]) | set(additions))
        if version == "v40":
            for path in baseline.EXPECTED_V41_CHANGED_FILES:
                if hashes.get(path) not in {None, "missing"}:
                    hashes[path] = v40["files"][path]
        changed = {path for path, expected_hash in parent["files"].items() if hashes[path] != expected_hash}
        try:
            getattr(baseline, f"build_{version}_candidate")(project_root=tree)
        except Exception as exc:
            result = {"type": type(exc).__name__, "message": str(exc)}
        else:
            result = {"type": "passed"}
        data["gates"][f"{label}-{version}"] = {
            "expectedChangedCount": len(expected), "actualChangedCount": len(changed),
            "extraChanged": sorted(changed - set(expected)),
            "missingExpectedChanged": sorted(set(expected) - changed),
            "result": result,
        }
    command = [sys.executable, "-m", "pytest", *TESTS, "-q", "--tb=short"]
    completed = subprocess.run(command, cwd=tree, capture_output=True, text=True)
    log_path = OUT / f"memory-freeze-{label}-pytest.txt"
    log_path.write_text(completed.stdout + completed.stderr)
    failed = [line.split(" - ", 1)[0][7:] for line in completed.stdout.splitlines() if line.startswith("FAILED ")]
    data["testRuns"][label] = {
        "command": command, "cwd": str(tree), "exitCode": completed.returncode,
        "failedNodeIds": failed, "log": str(log_path),
        "summary": completed.stdout.splitlines()[-1] if completed.stdout else "",
    }
data["sameFailureSet"] = data["testRuns"]["old"]["failedNodeIds"] == data["testRuns"]["current"]["failedNodeIds"]
full_suite_failures = [
    line.split(" - ", 1)[0][7:]
    for line in (OUT / "full-suite-python313.txt").read_text().splitlines()
    if line.startswith("FAILED tests/test_memory_retrieval_production_baseline_v4")
    and any(line.startswith(f"FAILED {path}::") for path in TESTS)
]
data["sameFailureSetAsFullSuite"] = full_suite_failures == data["testRuns"]["current"]["failedNodeIds"]
(OUT / "memory-freeze-diagnosis.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"sameFailureSet": data["sameFailureSet"], "runs": data["testRuns"], "gates": data["gates"]}, indent=2))
