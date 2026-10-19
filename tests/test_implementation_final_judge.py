"""Real local pytest/process checks for the prospective common final judge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from scripts import implementation_judge as judge
from scripts import run_implementation_progress_live as live


def delivered(
    tmp_path,
    source="def test_regression():\n    assert True\n",
    oracle="raise SystemExit(0)\n",
):
    workspace = tmp_path / "delivered"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "minicode").mkdir()
    (workspace / "minicode/__init__.py").write_text("")
    (workspace / "minicode/target.py").write_text("VALUE = 'delivered'\n")
    if source is not None:
        (workspace / judge.REGRESSION_PATH).write_text(source)
    oracle_path, response = tmp_path / "oracle.py", tmp_path / "response.txt"
    oracle_path.write_text(oracle)
    response.write_text("done")
    return workspace, oracle_path, response


def run(
    tmp_path,
    source="def test_regression():\n    assert True\n",
    *,
    oracle="raise SystemExit(0)\n",
    seconds=10,
    env=None,
):
    workspace, oracle_path, response = delivered(tmp_path, source, oracle)
    return judge.run_judgement(
        workspace,
        tmp_path / "judgement",
        oracle_path,
        response,
        total_seconds=seconds,
        env=env,
    )


def test_real_generated_test_runs_unfiltered_with_copy_imports_and_host_receipt(
    tmp_path,
):
    source = "from minicode.target import VALUE\ndef test_name_excluded_by_public_selector():\n    assert VALUE == 'delivered'\n"
    result = run(
        tmp_path,
        source,
        env={
            **os.environ,
            "PYTEST_ADDOPTS": "-k nonexistent",
            "PYTEST_PLUGINS": "does_not_exist",
        },
    )
    regression = result["regressionTest"]
    assert result["oracle"]["passed"] and regression["passed"], result
    assert regression["counts"] == {
        "collected": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
    }
    receipt = json.loads((tmp_path / "judgement/regression/receipt.json").read_text())
    assert (
        receipt["normalReturn"]
        and receipt["sessionFinished"]
        and receipt["allPhasesComplete"]
    )
    assert receipt["origins"]["checkedModules"] >= 2
    assert receipt["origins"]["foreignModules"] == []
    assert result["durationSeconds"] < 10


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        "def test_bad():\n    assert False\n",
        "raise RuntimeError('collection error')\n",
        "import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n",
        "import pytest\n@pytest.mark.xfail\ndef test_xfail(): assert False\n",
        "import pytest\n@pytest.mark.xfail\ndef test_xpass(): pass\n",
        "import pytest\n@pytest.fixture\ndef broken():\n    yield\n    raise RuntimeError('teardown')\ndef test_teardown(broken): pass\n",
    ],
)
def test_hidden_pass_does_not_mask_missing_empty_failed_or_incomplete_generated_tests(
    tmp_path, source
):
    result = run(tmp_path, source)
    assert result["oracle"]["passed"]
    assert not result["regressionTest"]["passed"], result
    assert result["cleanupComplete"]


def test_generated_xml_or_exit_zero_without_host_completion_is_not_evidence(tmp_path):
    result = run(
        tmp_path,
        "from pathlib import Path\nimport os\nPath('fake.xml').write_text('<testsuite tests=\"1\" failures=\"0\"/>')\nos._exit(0)\n",
    )
    assert result["regressionTest"]["exitCode"] == 0
    assert result["regressionTest"]["status"] == "missing_receipt"
    assert not result["regressionTest"]["passed"]


def test_fake_pass_console_cannot_override_real_test_failure(tmp_path):
    result = run(
        tmp_path,
        "def test_failure():\n    print('1 passed in 0.01s')\n    assert False\n",
    )
    assert not result["regressionTest"]["passed"]
    assert result["regressionTest"]["counts"]["failed"] == 1
    assert (
        "1 passed in 0.01s"
        in (tmp_path / "judgement/regression/output.log").read_text()
    )


def test_public_baseline_and_config_selectors_cannot_filter_new_test_names(tmp_path):
    workspace, oracle, response = delivered(
        tmp_path,
        "def test_new_behavior():\n    assert True\n",
        "import subprocess, sys\nraise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_baseline.py', '-k', 'baseline_only']))\n",
    )
    (workspace / "tests/test_baseline.py").write_text(
        "def test_baseline_only():\n    assert True\n"
    )
    (workspace / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-k baseline_only"\n'
    )
    result = judge.run_judgement(
        workspace,
        tmp_path / "judgement",
        oracle,
        response,
        total_seconds=10,
    )
    assert result["oracle"]["passed"], result
    assert result["regressionTest"]["passed"], result
    assert result["regressionTest"]["counts"]["collected"] == 1
    assert result["regressionTest"]["counts"]["passed"] == 1


def test_oracle_exit_zero_without_bootstrap_return_is_not_success(tmp_path):
    result = run(tmp_path, oracle="import os\nos._exit(0)\n")
    assert result["oracle"]["status"] == "missing_receipt"
    assert not result["oracle"]["passed"]
    assert result["regressionTest"]["passed"]


def test_generated_test_changes_only_its_delivered_copy(tmp_path):
    result = run(
        tmp_path,
        "from pathlib import Path\ndef test_mutate():\n    Path('minicode/target.py').write_text('VALUE = 999\\n')\n",
    )
    assert result["regressionTest"]["passed"], result
    assert (
        tmp_path / "delivered/minicode/target.py"
    ).read_text() == "VALUE = 'delivered'\n"


def test_hanging_generated_test_is_killed_with_its_child_within_common_deadline(
    tmp_path,
):
    source = "import subprocess,sys,time\nfrom pathlib import Path\ndef test_hang():\n    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)\n    Path('spawned.pid').write_text(str(child.pid))\n    time.sleep(60)\n"
    started = time.monotonic()
    result = run(tmp_path, source, seconds=3)
    assert not result["regressionTest"]["passed"]
    assert result["regressionTest"]["status"] == "timeout", result
    assert result["cleanupComplete"]
    assert time.monotonic() - started < 3.5
    pid = int((tmp_path / "judgement/regression/workspace/spawned.pid").read_text())
    probe = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
    )
    assert probe.returncode != 0 or probe.stdout.strip().startswith("Z")


def test_oracle_and_generated_stage_share_one_limit(tmp_path):
    result = run(tmp_path, oracle="import time\ntime.sleep(60)\n", seconds=1)
    assert not result["oracle"]["passed"]
    assert not result["regressionTest"]["passed"]
    assert result["durationSeconds"] < 1.5


def test_failed_oracle_cleanup_blocks_second_stage(monkeypatch, tmp_path):
    called = []

    def first(kind, **kwargs):
        called.append(kind)
        return {"passed": False, "cleanup": {"cleanupComplete": False}}

    monkeypatch.setattr(judge, "_run_stage", first)
    result = run(tmp_path)
    assert called == ["oracle"]
    assert result["regressionTest"]["status"] == "not_run_previous_cleanup_incomplete"
    assert not result["cleanupComplete"]


@pytest.mark.parametrize("arm", ["single", "auto", "explicit"])
def test_every_arm_receives_same_generated_test_requirement(arm):
    prompt = live.prompt_for_arm("Task", arm)
    assert live.REGRESSION_REQUIREMENT in prompt
    assert "skip" in prompt and "xfail" in prompt and "120秒" in prompt


def test_deadline_crossed_after_stages_keeps_raw_results_consistent(
    tmp_path, monkeypatch
):
    """A late parent clock tick must fail closed without contradicting stage evidence."""
    workspace, oracle, response = delivered(tmp_path)
    ticks = iter([100.0, 221.0])
    monkeypatch.setattr(judge.time, "monotonic", lambda: next(ticks))

    def completed(kind, **options):
        result = {"passed": True, "status": "passed", "cleanup": None}
        directory = options["root"] / kind
        directory.mkdir()
        judge.write_json(directory / "result.json", result)
        return result

    monkeypatch.setattr(judge, "_run_stage", completed)
    result = judge.run_judgement(workspace, tmp_path / "judgement", oracle, response)
    raw = json.loads((tmp_path / "judgement/regression/result.json").read_text())
    assert result["deadlineExceeded"] is True
    assert result["regressionTest"] == raw
    assert raw["passed"] is False and raw["status"] == "deadline_exhausted"


def test_original_oracle_pytest_exitcode_enum_is_success(tmp_path):
    # Real oracle_source uses SystemExit(pytest.main(...)); ExitCode is IntEnum.
    result = run(
        tmp_path, oracle="import pytest\nraise SystemExit(pytest.ExitCode.OK)\n"
    )
    assert result["oracle"]["passed"] is True
    assert result["oracle"]["exitCode"] == 0
    assert result["regressionTest"]["passed"] is True


@pytest.mark.parametrize("kind", ["oracle", "regression"])
def test_direct_stage_entry_uses_own_guardian_when_root_is_already_later(
    tmp_path, kind
):
    workspace, oracle, response = delivered(tmp_path)
    # Like the real delivered tree, this regular package shadows a later
    # editable checkout and does not contain the evaluator guardian.
    (workspace / "scripts").mkdir()
    (workspace / "scripts/__init__.py").write_text("")
    guardian_path = (judge.ROOT / "scripts/three_arm_processes.py").resolve()
    checks = (
        "from pathlib import Path\n"
        "from minicode.target import VALUE\n"
        "import scripts.three_arm_processes as guardian\n"
        "assert VALUE == 'delivered'\n"
        f"assert Path(guardian.__file__).resolve() == Path({str(guardian_path)!r})\n"
    )
    oracle.write_text(checks)
    (workspace / judge.REGRESSION_PATH).write_text(
        "def test_import_origins():\n"
        + "".join("    " + line + "\n" for line in checks.splitlines())
    )
    receipt_path = tmp_path / "receipt.json"
    spec_path = tmp_path / "stage.json"
    empty_config = tmp_path / "pytest.ini"
    empty_config.write_text("[pytest]\n")
    judge.write_json(
        spec_path,
        {
            "kind": kind,
            "nonce": "offline-import-order",
            "workspace": str(workspace.resolve()),
            "oraclePath": str(oracle.resolve()),
            "responsePath": str(response.resolve()),
            "receiptPath": str(receipt_path),
            "processEvidence": str(tmp_path / "processes.jsonl"),
            "emptyConfig": str(empty_config),
            "testSha256": None,
        },
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(workspace), str(judge.ROOT))),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    result = subprocess.run(
        [sys.executable, "-B", judge.__file__, "stage", str(spec_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(receipt_path.read_text())
    assert receipt["normalReturn"] is True and receipt["exitCode"] == 0
    assert receipt["origins"]["foreignModules"] == []
    assert receipt["origins"]["checkedModules"] >= 2
    if kind == "regression":
        assert receipt["counts"]["collected"] == receipt["counts"]["passed"] == 1
