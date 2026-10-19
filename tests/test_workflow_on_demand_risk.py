"""Host filesystem facts, not model path labels, bound review exemptions."""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolResult
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module
from test_workflow_on_demand_review import _run_request
from test_workflow_v2_structured_routing import (
    _request, _worker_role, _write_executor_declaration, _write_reviewer_result,
)


@pytest.mark.parametrize("case", [
    "directory", "directory-with-suffix", "new-file", "permissions", "payments",
    "credentials", "acceptance-symlink", "acceptance-parent-symlink", "acceptance-hardlink",
    "keyword-selector",
])
def test_host_scope_prevents_directory_sensitive_and_test_alias_exemptions(tmp_path, monkeypatch, case):
    scope = {"directory": "src", "directory-with-suffix": "src.py", "new-file": "new.py",
             "permissions": "src/permissions.py", "payments": "payments.py",
             "credentials": "Credentials.ts", "keyword-selector": "tests/test_acceptance.py"}.get(case, "allowed.py")
    changed = scope + "/changed.py" if case.startswith("directory") else scope
    target = tmp_path / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    if case != "new-file":
        target.write_text("def test_value():\n    assert False\n")
    acceptance = "test_acceptance.py"
    if case == "keyword-selector":
        acceptance = "acceptance.py"
        (tmp_path / acceptance).write_text("# A helper, not the selected acceptance test.\n")
    elif case == "acceptance-symlink":
        (tmp_path / acceptance).symlink_to("allowed.py")
    elif case == "acceptance-hardlink":
        os.link(target, tmp_path / acceptance)
    elif case == "acceptance-parent-symlink":
        (tmp_path / "checks").mkdir()
        (tmp_path / "checks/test_acceptance.py").write_text("def test_value():\n    assert True\n")
        (tmp_path / "linked_checks").symlink_to("checks", target_is_directory=True)
        acceptance = "linked_checks/test_acceptance.py"
    else:
        (tmp_path / acceptance).write_text("def test_value():\n    assert True\n")
    roles = []

    def worker(data, context):
        role = _worker_role(data)
        roles.append(role)
        if role == "executor":
            Path(context.cwd, changed).write_text("def test_value():\n    assert True\n")
            _write_executor_declaration(data, context)
        else:
            _write_reviewer_result(data, context)
        return ToolResult(ok=True, output="Completed.")

    def command_runner(argv, **kwargs):
        return subprocess.run([sys.executable, "-m", *argv], **kwargs)

    request = _request(risk="low", complex=False)
    request["scope"]["writePaths"] = [scope]
    argv = ["pytest", "-q", *(["-k"] if case == "keyword-selector" else []), acceptance]
    request["verificationChecks"][0]["argv"] = argv
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_args: None)
    verifier = DeterministicVerifier(command_runner=command_runner)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    result = _run_request(tmp_path, request, event_sink=None,
        user_request="Update the file.\nAcceptance command: `" + " ".join(argv) + "`")
    trace = extract_scheduler_trace(result.output)
    assert trace["ruleId"] != "route.verified_change", result.output
    # Hardlink snapshots may break aliases and consequently fail the test;
    # they must nevertheless retain the independent review gate at admission.
    if result.ok:
        assert "reviewer" in roles


@pytest.mark.parametrize("review_request", [
    "请审核代码，验收通过后再交付", "请让另一个 agent 独立审查这次修改。",
    "Have a second agent review it before delivery.", "请审计这次修改。",
])
def test_original_user_review_language_keeps_review_gate(tmp_path, monkeypatch, review_request):
    from test_workflow_on_demand_review import test_bounded_low_risk_change_earns_delivery_without_a_reviewer
    test_bounded_low_risk_change_earns_delivery_without_a_reviewer(
        tmp_path, monkeypatch,
        review_request + "\nAcceptance command: `pytest -q test_acceptance.py`", True, True,
    )


def test_final_frozen_diff_rechecks_exact_files_instead_of_directory_prefixes(tmp_path):
    from minicode.multi_agent_contracts import parse_workflow_request
    from minicode.workflow_review_policy import validate_unreviewed_change_set
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction

    (tmp_path / "src.py").mkdir()
    (tmp_path / "src.py/child.py").write_text("before\n")
    raw = _request(risk="low", complex=False)
    raw["scope"]["writePaths"] = ["src.py"]
    request = parse_workflow_request(raw, legacy_task_id="task_directory_final_check")
    transaction = WorkflowWorkspaceTransaction(str(tmp_path))
    try:
        Path(transaction.cwd, "src.py/child.py").write_text("after\n")
        frozen = transaction.freeze(allowed_paths=("src.py",), paths_relative_to_cwd=True)
        with pytest.raises(ValueError, match="host-confirmed review exemption"):
            validate_unreviewed_change_set(request, frozen.change_set_document)
    finally:
        transaction.close()
