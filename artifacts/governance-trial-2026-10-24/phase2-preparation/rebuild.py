"""Rebuild registered historical inputs from Git; never freeze or call a model.

This is an artifact unpacker, not an evaluation runner. Execution uses the
existing scripts/run_three_arm_real.py. Historical Git objects must be present.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile


PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe artifact path: {name}")
    return path


def checked_file(name, expected):
    content = (PACKAGE / relative(name)).read_bytes()
    if digest(content) != expected:
        raise ValueError(f"artifact digest mismatch: {name}")
    return content


def snapshot(tree, cache):
    commit = tree["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("historical source must use a complete Git SHA")
    if commit not in cache:
        files = {}
        with tarfile.open(fileobj=io.BytesIO(git("archive", commit, "minicode"))) as archive:
            for member in archive:
                path = relative(member.name)
                if path.parts[0] != "minicode" or path.suffix != ".py":
                    continue
                if not member.isfile():
                    raise ValueError(f"nonregular Python source: {member.name}")
                files[path.as_posix()] = archive.extractfile(member).read()
        cache[commit] = (files, {name: digest(data) for name, data in files.items()},
                         git("rev-parse", f"{commit}:minicode").decode().strip())
    files, inventory, tree_sha = cache[commit]
    if (tree_sha != tree["minicodeTree"] or len(files) != tree["pythonFileCount"]
            or digest(canonical(inventory)) != tree["pythonInventorySha256"]):
        raise ValueError(f"source inventory mismatch: {commit}")
    return files, inventory


def verify(design):
    sums = json.loads((PACKAGE / "SHA256SUMS.json").read_text())
    actual = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*")
              if p.is_file() and p.name != "SHA256SUMS.json" and "__pycache__" not in p.parts}
    if set(sums) != actual:
        raise ValueError("package membership changed")
    for name, expected in sums.items():
        checked_file(name, expected)
    cache = {}
    for case in design["cases"]:
        checked_file(case["promptPath"], case["promptSha256"])
        patch = checked_file(case["referencePatchPath"], case["referencePatchSha256"])
        acceptance = case["acceptance"]
        checked_file(acceptance["oraclePath"], acceptance["oracleSha256"])
        test_path = str(relative(acceptance["historical_test_path"]))
        test = git("show", f"{case['referenceRef']}:{test_path}")
        if digest(test) != acceptance["historical_test_sha256"]:
            raise ValueError(f"historical test mismatch: {case['id']}")
        expected_patch = git("diff", "--binary", case["historicalParentRef"],
                             case["referenceRef"], "--", *case["sourcePaths"], test_path)
        if patch != expected_patch:
            raise ValueError(f"historical patch mismatch: {case['id']}")
        for proof in case["offlineProof"].values():
            checked_file(proof["logPath"], proof["logSha256"])
        for tree in case["sourceTrees"].values():
            snapshot(tree, cache)
    return cache


def rebuild(design, cache, output):
    if output.exists():
        raise ValueError("output must be new; existing evidence is never overwritten")
    output.mkdir(parents=True, mode=0o700)
    for commit, (files, _, _) in cache.items():
        for name, content in files.items():
            target = output / "sources" / commit / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    expanded = copy.deepcopy(design)
    expanded["format"] = "expanded-runner-design-v1"
    expanded["portableManifestSha256"] = digest((PACKAGE / "manifest.json").read_bytes())
    for case in expanded["cases"]:
        case["prompt"] = (PACKAGE / case["promptPath"]).read_text()
        case["sourceInventories"] = {}
        for role in ("baseline", "reference"):
            commit = case["sourceTrees"][role]["commit"]
            case[f"{role}SourceDirectory"] = str(output / "sources" / commit)
            case["sourceInventories"][role] = cache[commit][1]
        oracle = (PACKAGE / case["acceptance"]["oraclePath"]).read_text()
        if "historicalParent" in case["sourceTrees"]:
            commit = case["sourceTrees"]["historicalParent"]["commit"]
            directory = str(output / "sources" / commit)
            case["oracleDependencyDirectories"] = {directory: cache[commit][1]}
            if repr("@@ORACLE_PARENT@@") not in oracle:
                raise ValueError("registered private parent source is unused")
            oracle = oracle.replace(repr("@@ORACLE_PARENT@@"), repr(directory))
        if "@@ORACLE_PARENT@@" in oracle:
            raise ValueError("unresolved oracle parent source")
        target = output / "private-oracles" / f"{case['id']}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(oracle)
        case["acceptance"]["oraclePath"] = str(target)
        case["acceptance"]["oracleSha256"] = digest(target.read_bytes())
    design_path = output / "expanded-design.json"
    design_path.write_text(json.dumps(expanded, ensure_ascii=False, indent=2) + "\n")
    subprocess.run([sys.executable, str(ROOT / "scripts/run_three_arm_real.py"),
                    "prepare", "--output", str(output / "execution"),
                    "--design", str(design_path), "--analysis-plan", str(PACKAGE / "protocol.md")],
                   cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true", help="verify package and Git bytes, without writing inputs")
    modes.add_argument("--output", type=Path, help="new external directory for sources and prepared execution")
    args = parser.parse_args()
    design = json.loads((PACKAGE / "manifest.json").read_text())
    cache = verify(design)
    if args.output:
        output = args.output.resolve()
        if output == ROOT or ROOT in output.parents:
            raise ValueError("reconstructed source trees must stay outside the repository")
        rebuild(design, cache, output)
    print(json.dumps({"verifiedTasks": len(design["cases"]), "verifiedSourceCommits": len(cache),
                      "recordedReferenceChecks": design["offlineSummary"]["referenceCheckInstances"],
                      "providerCalls": 0, "formalFreezePerformed": False}))


if __name__ == "__main__":
    main()
