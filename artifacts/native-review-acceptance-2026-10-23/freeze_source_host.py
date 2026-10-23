"""Freeze tested source on a separate ref; never change the working index/tree."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
REF = "refs/heads/codex/native-review-validation-host-20261023"
HARNESS_SHA256 = {
    "scripts/native_review_bounded_validation.py": "cda645d2c092498611672fd766e869f958b65d43c420adf2973608e3710bd38e",
    "scripts/audit_native_review_bounded.py": "efb104ff2b079f7c51ff01aa345f4fc10e6b4df697bdf7ba5521ececad9374d6",
}


def git(*args, data=None, env=None):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], input=data, env=env)


def main():
    gate = json.loads((EVIDENCE / "exact-host-regression-exit.json").read_text())
    assert gate["exitCode"] == 0, "full exact-host gate must pass before freezing"
    tested = json.loads((EVIDENCE / "exact-host-regression-input.json").read_text())
    snapshot = Path(tested["snapshot"])
    closure = json.loads((EVIDENCE / "test-dependency-closure.json").read_text())
    for name, digest in {**tested["productionFiles"], **closure["sha256"]}.items():
        assert hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == digest, name
    baseline = ROOT / "artifacts/multi-agent-trigger-2026-10-23/run-v1/execution-host.zip"
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == tested["baselineArchiveSha256"]
    with zipfile.ZipFile(baseline) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    overlays = json.loads((EVIDENCE / "host-overlay.json").read_text())["paths"]
    for name, digest in HARNESS_SHA256.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "harness drift: " + name
    for name in overlays:
        contents[name] = (snapshot / name).read_bytes() if name.startswith("minicode/") else (ROOT / name).read_bytes()
        if name.startswith("minicode/"):
            assert contents[name] == (ROOT / name).read_bytes(), "source drift: " + name
    for name in closure["files"]:
        contents[name] = (snapshot / name).read_bytes()
        assert contents[name] == (ROOT / name).read_bytes(), "test drift: " + name
    parent = git("rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="native-review-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        git("read-tree", parent, env=env)
        entries = []
        tracked = git("ls-tree", "-r", "--name-only", parent, "minicode").decode().splitlines()
        for name in tracked:
            if name not in contents:
                entries.append(f"0 {'0' * 40}\t{name}\n")
        for name, data in sorted(contents.items()):
            object_id = git("hash-object", "-w", "--stdin", data=data).decode().strip()
            entries.append(f"100644 {object_id}\t{name}\n")
        git("update-index", "--index-info", data="".join(entries).encode(), env=env)
        tree = git("write-tree", env=env).decode().strip()
        message = ("test(review): freeze bounded validation host\n\n"
            "Preserve the original trigger host baseline, including its pre-existing\n"
            "Memory snapshot; overlay only R1/R2 and adjudication repairs. Record\n"
            "the exact tested fixtures and new validation harness. This dedicated\n"
            "ref does not modify the working branch, index, or user edits.\n")
        commit = git("commit-tree", tree, "-p", parent, data=message.encode()).decode().strip()
        git("update-ref", REF, commit, "0" * 40)
    receipt = {"sourceHostCommit": commit, "ref": REF, "parent": parent, "tree": tree,
        "baselineArchiveSha256": tested["baselineArchiveSha256"], "overlayPaths": overlays,
        "testFiles": closure["files"], "workingBranchAndIndexUnchanged": True}
    (EVIDENCE / "source-host-commit.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(commit)


if __name__ == "__main__":
    main()
