"""The evaluator authorizes host-derived probes through the original check.

Real isolated snapshots and managed pytest execute; the local backend only
avoids nesting OS sandboxes. No model or evaluation-task material is used.
"""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from scripts import run_three_arm_real as live


BASE = ("python", "-m", "pytest", "-q", "test_public.py")
STOCK = "def public_value(): return 7\ndef test_stock(): assert public_value() == 7\n"
PROBE = (
    "from pathlib import Path\n"
    "from test_public import public_value\n"
    "def test_original_is_preserved():\n"
    f"    assert Path('test_public.py').read_text() == {STOCK!r}\n"
    "    assert public_value() == 7\n"
)


@pytest.fixture
def guarded_probe(tmp_path, monkeypatch):
    workspace = tmp_path.resolve()
    (workspace / "candidate.py").write_text("value = 0\n")
    (workspace / "test_public.py").write_text(STOCK)
    (workspace / "test_unapproved.py").write_text(STOCK)
    transaction = WorkflowWorkspaceTransaction(str(workspace))
    (transaction.cwd / "candidate.py").write_text("value = 1\n")
    frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
    executions, calls, grants = [], [], []

    class LocalBackend:
        def wrap_command(self, *, argv, **_kwargs):
            executions.append(tuple(argv))
            return argv

    class ObservedVerifier(DeterministicVerifier):
        def run(self, **kwargs):
            calls.append(kwargs)
            return super().run(**kwargs)

    verifier = ObservedVerifier(sandbox_backend=LocalBackend(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda _self, program, args, cwd: grants.append((program, tuple(args), cwd)))
    options = {
        "probe": ReviewerProbe.create(criterion_ids=["stock_preservation"], scenario="original helper is available", code=PROBE),
        "frozen": frozen, "check": VerificationCheck("public", "tests", BASE, 10),
        "test_path": "test_public.py", "verifier": verifier, "read_roots": (".",), "forbidden_paths": (".git", ".env"),
        "run_kwargs": {"workflow_id": "wf_probe_scope", "task_id": "task_probe_scope",
            "execution_assignment_id": "asg_probe_scope", "patch_digest": frozen.patch_digest,
            "permissions": PermissionManager(str(workspace), prompt=None), "permission_cwd": str(workspace)},
    }
    try:
        with ExitStack() as stack:
            live.install_verification_guards(stack, live.VerificationScope(BASE), workspace)
            yield workspace, transaction, verifier, options, calls, executions, grants
    finally:
        transaction.close()


def test_actual_host_probe_passes_original_scope_and_preserves_stock_import(guarded_probe):
    workspace, transaction, _verifier, options, calls, executions, grants = guarded_probe
    receipt = run_isolated_review_probe(**options)
    assert receipt["status"] == "passed", receipt
    assert receipt["testReport"]["collected"] == receipt["testReport"]["passed"] == 1
    assert receipt["evidence"]["argv"] == [*BASE[:-1], "__minicode_review_probe__.py"]
    assert receipt["executionBinding"]["baseCheck"]["argv"] == list(BASE)
    assert receipt["executionBinding"]["derivedCheck"]["argv"] == receipt["evidence"]["argv"]
    assert len(executions) == len(calls) == len(grants) == 1
    assert grants == [(BASE[0], BASE[1:], str(workspace))]
    assert (workspace / "test_public.py").read_text() == STOCK
    assert (transaction.cwd / "test_public.py").read_text() == STOCK
    assert not (workspace / "__minicode_review_probe__.py").exists()
    assert not Path(calls[0]["cwd"]).exists()


@pytest.mark.parametrize("attack", ["direct", "duck", "subclass", "cwd", "changed_check", "extra_check"])
def test_derived_command_cannot_borrow_or_forge_host_authority(guarded_probe, attack):
    workspace, _transaction, verifier, options, calls, executions, _grants = guarded_probe
    assert run_isolated_review_probe(**options)["status"] == "passed"
    host_call = calls[0]
    kwargs = {**host_call, "cwd": str(workspace)}
    capability = host_call["permissions"]
    if attack == "direct":
        kwargs["permissions"] = options["run_kwargs"]["permissions"]
    elif attack == "duck":
        class FakeCapability:
            def __getattr__(self, name):
                return getattr(capability, name)
        kwargs["permissions"] = FakeCapability()
    elif attack == "subclass":
        class SubclassCapability(type(capability)):
            pass
        kwargs["permissions"] = SubclassCapability(capability._parent, capability._base, capability._derived, capability._cwd)
    elif attack == "cwd":
        kwargs["permission_cwd"] = str(workspace.parent)
    elif attack == "changed_check":
        kwargs["checks"] = (replace(kwargs["checks"][0], timeout_seconds=11),)
    else:
        kwargs["checks"] = (*kwargs["checks"], options["check"])
    with pytest.raises((live.ScopeDenied, ValueError)):
        verifier.run(**kwargs)
    assert len(executions) == 1, "denied commands must never reach the subprocess backend"


def test_real_host_capability_does_not_authorize_an_unpublished_base_check(guarded_probe):
    _workspace, _transaction, _verifier, options, _calls, executions, grants = guarded_probe
    options["check"] = replace(options["check"], argv=(*BASE[:-1], "test_unapproved.py"))
    options["test_path"] = "test_unapproved.py"
    with pytest.raises(live.ScopeDenied):
        run_isolated_review_probe(**options)
    assert not executions and not grants
