"""Offline registration/host counterexamples; no test dispatches a provider."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from scripts import run_multi_agent_gen2 as gen2


def task_document() -> dict:
    return {
        "shared_files": {"minicode/__init__.py": "", "minicode/a.py": "VALUE = 1\n"},
        "cases": [{"id": name, "category": "bounded implementation", "prompt": "Fix the public defect.",
                   "files": {"tests/test_public.py": "def test_public(): assert True\n"},
                   "authorized_paths": ["minicode/a.py", "tests/test_eval_regression.py"],
                   "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
                   "oracle_source": "PRIVATE_ORACLE_" + name}
                  for name in gen2.CASES],
    }


def prepared(tmp_path: Path) -> Path:
    pack, output = tmp_path / "pack", tmp_path / "registration"
    pack.mkdir()
    tasks = task_document()
    gen2.shared.write_json(pack / "tasks.json", tasks)
    gen2.shared.write_json(pack / "source-manifest.json", {"test": "offline source metadata"})
    proof = [{"case": c["id"], "oracleSha256": gen2.shared.sha(c["oracle_source"].encode()),
              "publicTestSha256": gen2.shared.sha(c["files"]["tests/test_public.py"].encode()),
              **{name: {"returncode": code} for name, code in (
                  ("baselinePublic", 0), ("baselineOracle", 1),
                  ("referencePublic", 0), ("referenceOracle", 0))}}
             for c in tasks["cases"]]
    for row in proof:
        row["baselineOracle"]["stdout"] = json.dumps({"passed": False, "errors": ["target behavior"]})
        row["referenceOracle"]["stdout"] = json.dumps({"passed": True, "errors": []})
    gen2.shared.write_json(pack / "offline-proof.json", proof)
    gen2.prepare(output, pack / "tasks.json", materials=pack)
    return output


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    output = prepared(tmp_path)
    files = {
        "minicode/__init__.py": b"",
        "minicode/workflow_admission.py": b"PHASE_ONE_ADMISSION = True\n",
        "minicode/workflow_brief.py": b"PHASE_ONE_BRIEF = True\n",
        "scripts/run_multi_agent_gen2.py": b"print('ARCHIVED_HOST')\n",
        "scripts/__init__.py": b"", "pyproject.toml": b"[project]\nname='test-host'\n",
    }
    monkeypatch.setattr(gen2, "host_files", lambda: files)
    monkeypatch.setattr(gen2, "runner", lambda *a, **k: SimpleNamespace(
        frozen_runtime=lambda: {"model": gen2.MODEL}, route_hash=lambda _: "a" * 64,
        resolved_transport_route=lambda _: "b" * 64))

    def public_preflight(document, destination, **kwargs):
        assert kwargs["require_pytest_report"] is True
        gen2.shared.write_json(destination / "receipt.json", {"passed": True})
        return {"passed": True}

    def probe(root, _output):
        assert (root / "minicode/workflow_admission.py").read_bytes() == files["minicode/workflow_admission.py"]
        return {"model": gen2.MODEL, "routeSha256": "a" * 64,
                "resolvedTransportRouteSha256": "b" * 64,
                "productionSha256": "c" * 64, "harnessSha256": "d" * 64,
                "environment": {"python": "offline test"}}

    monkeypatch.setattr(gen2, "run_public_preflight", public_preflight)
    monkeypatch.setattr(gen2, "validate_public_preflight", lambda *a, **k: None)
    monkeypatch.setattr(gen2, "probe_host", probe)
    gen2.freeze(output)
    return output


def test_fixed_six_cells_are_deterministic_and_not_old_schedule():
    rows = gen2.schedule_for()
    assert rows == gen2.schedule_for()
    assert len(rows) == 6
    assert {(r["case"], r["arm"], r["repeat"]) for r in rows} == {
        (case, arm, 1) for case in gen2.CASES for arm in gen2.ARMS}
    assert gen2.static_contract()["scheduleSeed"] == 20261023
    assert gen2.LIMITS["wallSeconds"] == 600
    assert gen2.JUDGE_POLICY["agentBudgetSeconds"] == 600
    assert gen2.JUDGE_POLICY["totalSeconds"] == 120


def test_prepare_preserves_private_materials_without_duplicate_task_package(tmp_path):
    output = prepared(tmp_path)
    assert (output / "tasks.json").read_bytes() == (tmp_path / "pack/tasks.json").read_bytes()
    assert (output / "materials/offline-proof.json").read_bytes() == (tmp_path / "pack/offline-proof.json").read_bytes()
    assert not (output / "materials/tasks.json").exists()
    with pytest.raises(ValueError, match="new empty"):
        gen2.prepare(output, tmp_path / "pack/tasks.json")


@pytest.mark.parametrize("mutation", ["old_case", "prepopulated_test", "escape", "filtered"])
def test_prepare_rejects_changed_task_contract(tmp_path, mutation):
    document = task_document()
    if mutation == "old_case":
        document["cases"][0]["id"] = "real-vision-budget"
    elif mutation == "prepopulated_test":
        document["shared_files"]["tests/test_eval_regression.py"] = "def test_already_done(): pass"
    elif mutation == "escape":
        document["cases"][0]["files"]["../oracle.py"] = "escape"
    else:
        document["cases"][0]["verification_argv"] += ["-k", "passing"]
    source = tmp_path / "tasks.json"
    gen2.shared.write_json(source, document)
    with pytest.raises(ValueError):
        gen2.prepare(tmp_path / "out", source)
    assert not (tmp_path / "out").exists()


def test_freeze_registers_archive_and_all_proof_bytes_without_old_prepare(frozen):
    manifest = gen2.validate(frozen)
    assert manifest["model"] == "glm-5.3-flash"
    assert manifest["roleTurnLimits"] == {"explore": 0, "plan": 0, "general": 0}
    assert {"materials/offline-proof.json", "materials/source-manifest.json"} <= manifest["registrationFiles"].keys()
    with zipfile.ZipFile(frozen / "execution-host.zip") as archive:
        assert "minicode/workflow_admission.py" in archive.namelist()
        assert "minicode/workflow_brief.py" in archive.namelist()
        assert not any("oracle" in name or name.endswith("tasks.json") for name in archive.namelist())
    with pytest.raises(ValueError, match="existing freeze"):
        gen2.freeze(frozen)


def test_freeze_rejects_missing_or_non_counterexample_proof_before_runtime(tmp_path, monkeypatch):
    output = prepared(tmp_path)
    proof = gen2.shared.read_json(output / "materials/offline-proof.json")
    proof[0]["baselineOracle"]["returncode"] = 0
    gen2.shared.write_json(output / "materials/offline-proof.json", proof)
    monkeypatch.setattr(gen2, "runner", lambda *_a, **_k: pytest.fail("runtime created before offline proof"))
    with pytest.raises(ValueError, match="counterexample"):
        gen2.freeze(output)
    (output / "materials/offline-proof.json").unlink()
    with pytest.raises(ValueError, match="required"):
        gen2.freeze(output)


def test_host_collection_includes_untracked_python_resources_and_declarations(tmp_path, monkeypatch):
    monkeypatch.setattr(gen2, "ROOT", tmp_path)
    monkeypatch.setattr(gen2, "HARNESS_FILES", ("__init__.py", "run_multi_agent_gen2.py"))
    for name, content in {
        "minicode/workflow_admission.py": "NEW_UNTRACKED_PHASE1 = True",
        "minicode/workflow_brief.py": "NEW_UNTRACKED_PHASE1 = True",
        "minicode/web/static/index.html": "<html>resource</html>",
        "minicode/__pycache__/bad.pyc": "ignore",
        "minicode/.DS_Store": "ignore", "minicode/agent_loop.py.orig": "ignore",
        "minicode/agent_loop.py.bak": "ignore",
        "scripts/__init__.py": "", "scripts/run_multi_agent_gen2.py": "# entry",
        "pyproject.toml": "# dependency declaration",
    }.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    names = gen2.host_files()
    assert "minicode/workflow_admission.py" in names
    assert "minicode/workflow_brief.py" in names
    assert "minicode/web/static/index.html" in names
    assert "scripts/__init__.py" in names and "pyproject.toml" in names
    assert not any("__pycache__" in name for name in names)
    assert not any(name.endswith((".orig", ".bak", ".DS_Store")) for name in names)


@pytest.mark.parametrize("change", ["bytes", "extra", "traversal", "duplicate"])
def test_archive_membership_and_content_are_enforced(frozen, change):
    archive_path = frozen / "execution-host.zip"
    if change == "bytes":
        host = frozen.parent / "host"
        gen2.unpack_host(frozen, host)
        (host / "minicode/workflow_brief.py").write_text("DRIFT")
        with pytest.raises(ValueError, match="differs"):
            gen2.unpack_host(frozen, host)
        return
    name = {"extra": "extra.py", "traversal": "../escape.py", "duplicate": "pyproject.toml"}[change]
    with zipfile.ZipFile(archive_path, "a") as archive:
        if change == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr(name, "changed")
        else:
            archive.writestr(name, "changed")
    with pytest.raises(ValueError, match="membership"):
        gen2.verify_archive(frozen)


def commit_registration(output: Path) -> str:
    repo = output.parent
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", output.name], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Offline Test", "-c",
                    "user.email=offline@example.test", "commit", "-qm", "fixture registration"], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_real_git_object_guard_requires_committed_exact_bytes(frozen):
    commit = commit_registration(frozen)
    assert gen2.require_committed_registration(frozen, commit) == commit
    manifest = gen2.shared.read_json(frozen / "manifest.json")
    manifest["createdAt"] = "changed after preregistration"
    gen2.shared.write_json(frozen / "manifest.json", manifest)
    with pytest.raises(ValueError, match="does not contain"):
        gen2.require_committed_registration(frozen, commit)


@pytest.mark.parametrize("mode", ["run", "child"])
def test_missing_commit_rejects_before_runtime_or_workspace(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(gen2, "runner", lambda *_a, **_k: pytest.fail("must not initialize paid runtime"))
    with pytest.raises(ValueError, match="preregistered-commit"):
        gen2.dispatch(tmp_path, mode, None, trial=gen2.schedule_for()[0]["id"])
    assert not (tmp_path / "private").exists()


def test_guard_rejects_a_commit_without_registration(frozen):
    repo = frozen.parent
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Offline Test", "-c",
                    "user.email=offline@example.test", "commit", "--allow-empty", "-qm", "before registration"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    with pytest.raises(subprocess.CalledProcessError):
        gen2.require_committed_registration(frozen, commit)


def test_bootstrap_really_executes_archive_after_live_source_changes(frozen, monkeypatch):
    commit = commit_registration(frozen)
    live = frozen.parent / "live/scripts/run_multi_agent_gen2.py"
    live.parent.mkdir(parents=True)
    live.write_text("raise AssertionError('LIVE_SOURCE_MUST_NOT_RUN')\n")
    observed = []

    class ReplacedProcess(Exception):
        pass

    def execute(_binary, argv, env):
        assert "-I" in argv and "-B" in argv
        assert "PYTHONPATH" not in env
        result = subprocess.run(argv, env=env, text=True, capture_output=True, check=True)
        observed.append(result.stdout.strip())
        raise ReplacedProcess

    monkeypatch.setattr(gen2.os, "execve", execute)
    with pytest.raises(ReplacedProcess):
        gen2.dispatch(frozen, "run", commit)
    assert observed == ["ARCHIVED_HOST"]
    assert (frozen / "private/execution-host/minicode/workflow_brief.py").is_file()


def test_child_and_resume_cannot_skip_or_repeat_cells(frozen):
    commit = commit_registration(frozen)
    first, second = gen2.schedule_for()[:2]
    with pytest.raises(ValueError, match="next frozen"):
        gen2.dispatch(frozen, "child", commit, trial=second["id"])
    root = frozen / "private" / first["id"]
    gen2.shared.write_json(root / "execution-host.json", {"prior": "attempt"})
    with pytest.raises(ValueError, match="durable evidence"):
        gen2.dispatch(frozen, "run", commit)


def test_resume_requires_a_clean_terminal_prefix(frozen):
    manifest = gen2.validate(frozen)
    first, second = manifest["schedule"][:2]
    gen2.shared.write_json(frozen / "trials" / (second["id"] + ".json"),
                           {"id": second["id"], "processCleanup": {"cleanupComplete": True}})
    with pytest.raises(ValueError, match="prefix"):
        gen2.next_trial(frozen, manifest)
    gen2.shared.write_json(frozen / "trials" / (first["id"] + ".json"),
                           {"id": first["id"], "processCleanup": {"cleanupComplete": False}})
    with pytest.raises(ValueError, match="incomplete cleanup"):
        gen2.next_trial(frozen, manifest)


def test_runtime_knobs_are_fixed_and_do_not_mutate_historical_runner(tmp_path, monkeypatch):
    from minicode import config
    historical = deepcopy(gen2.shared.LIMITS)
    monkeypatch.setattr(config, "load_runtime_config", lambda *_: {
        "model": "private-model", "temperature": 1, "subagentRoutingEnabled": True,
        "subagentModels": {"general": "different-model"}, "modelMaxRetries": 9,
        "agentTurnBudgetModelCalls": 999})
    module = gen2.runner(tmp_path)
    runtime = module.frozen_runtime()
    assert module.LIMITS == gen2.LIMITS
    assert runtime["model"] == "glm-5.3-flash" and runtime["temperature"] == 0
    assert runtime["subagentRoutingEnabled"] is False and runtime["subagentModels"] == {}
    from minicode.subagent_model_routing import resolve_subagent_model_route
    for role in gen2.ROLE_TURN_LIMITS:
        route = resolve_subagent_model_route(runtime, role)
        assert route.enabled is False and route.provider == "inherit" and route.model == gen2.MODEL
    assert runtime["modelMaxRetries"] == 0
    assert runtime["agentTurnBudget"]["maxModelCalls"] == 40
    assert "agentTurnBudgetModelCalls" not in runtime
    assert gen2.shared.LIMITS == historical and historical["wallSeconds"] == 1800
    for arm in gen2.ARMS:
        assert "1800" not in gen2.prompt_for_arm("PUBLIC", arm)
        assert "600" in gen2.prompt_for_arm("PUBLIC", arm)


def test_child_launcher_forwards_commit_and_same_isolated_python(tmp_path, monkeypatch):
    module = gen2.runner(tmp_path, commit="a" * 40)
    calls = []
    # launch_child retains the real constructor; inspect its argument capture
    # through a fresh adapter built while the constructor is replaced.
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    module = gen2.runner(tmp_path, commit="a" * 40)
    module.subprocess.Popen([sys.executable, str(gen2.ROOT / "scripts/run_multi_agent_gen2.py"),
                             "child", "--output", str(tmp_path), "--trial", "cell"], cwd=gen2.ROOT)
    argv = calls[0][0]
    assert argv[1:3] == ["-I", "-B"]
    assert argv[-4:] == ["--preregistered-commit", "a" * 40, "--execution-host", str(gen2.ROOT)]


def test_role_settings_shadow_private_defaults_and_restore_environment(monkeypatch):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "1")
    from minicode.subagent_types import configured_max_turns
    with gen2.role_overrides({"roleTurnLimits": gen2.ROLE_TURN_LIMITS}):
        assert all(configured_max_turns(role) is None for role in gen2.ROLE_TURN_LIMITS)
    assert os.environ["MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS"] == "1"


def test_actual_shared_execute_receives_gen2_transport_and_only_public_workspace(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    document = task_document()
    gen2.shared.write_json(output / "tasks.json", document)
    first = gen2.schedule_for()[0]
    module = gen2.runner(output)
    manifest = {**gen2.static_contract(), "routeSha256": "a" * 64,
                "resolvedTransportRouteSha256": "b" * 64,
                "productionSha256": "c" * 64, "harnessSha256": "d" * 64,
                "taskSha256": gen2.shared.sha((output / "tasks.json").read_bytes()),
                "publicVerificationFixturePolicy": gen2.PUBLIC_FIXTURE_POLICY,
                "publicVerificationFixtures": [], "publicPreflightSha256": "e" * 64}
    gen2.shared.write_json(output / "manifest.json", manifest)
    monkeypatch.setattr(gen2, "validate", lambda *_a, **_k: manifest)
    # The common executor closure uses its private successor globals.
    execute_globals = module.execute_trial.__globals__
    monkeypatch.setitem(execute_globals, "validate_public_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "frozen_runtime", lambda: {"model": gen2.MODEL})
    monkeypatch.setattr(module, "route_hash", lambda _: "a" * 64)
    monkeypatch.setattr(module, "production_sha", lambda: "c" * 64)
    monkeypatch.setattr(module, "harness_sha", lambda: "d" * 64)
    captured = {}

    class BeforeProvider(Exception):
        pass

    def capture_transport(**kwargs):
        captured.update(kwargs)
        raise BeforeProvider

    monkeypatch.setattr(module, "TransportConfig", capture_transport)
    before = gen2.time.time()
    try:
        with pytest.raises(BeforeProvider):
            module.execute_trial(output, first["id"])
        start = gen2.shared.read_json(output / "private" / first["id"] / "workspace.json")
        workspace = Path(start["path"] if "path" in start else start["workspace"])
        assert (workspace / "minicode/a.py").read_text() == "VALUE = 1\n"
        assert not any("PRIVATE_ORACLE" in p.read_text() for p in workspace.rglob("*") if p.is_file())
        assert captured["max_model_calls"] == 40
        assert captured["max_total_tokens"] == 500000
        assert captured["max_output_tokens"] == 8192
        assert before + 600 <= captured["deadline_epoch"] <= gen2.time.time() + 600
        assert captured["request_timeout_seconds"] == 300
    finally:
        location = output / "private" / first["id"] / "workspace.json"
        if location.exists():
            record = gen2.shared.read_json(location)
            shutil.rmtree(record.get("path", record.get("workspace", "")), ignore_errors=True)


def test_import_failure_is_not_a_valid_offline_counterexample(tmp_path):
    output = prepared(tmp_path)
    proof = gen2.shared.read_json(output / "materials/offline-proof.json")
    proof[0]["baselineOracle"].update(stdout="", stderr="ImportError: target unavailable")
    gen2.shared.write_json(output / "materials/offline-proof.json", proof)
    with pytest.raises(ValueError, match="import/setup"):
        gen2.validate_offline_proof(output, task_document())
