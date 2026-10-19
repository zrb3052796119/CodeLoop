"""Offline schema 4 role-policy freeze and real production allocation checks."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import run_implementation_progress_live as live
from scripts import audit_implementation_progress as auditor
from tests import test_implementation_progress_live as runner_fixtures
from tests import test_implementation_progress_audit as audit_fixtures

assert (
    Path(live.__file__).resolve().parent.parent
    == Path(__file__).resolve().parent.parent
)
GENERAL = "MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS"


@pytest.fixture(autouse=True)
def isolated_knobs_and_no_network(monkeypatch, tmp_path):
    from minicode import config, subagent_types

    assert (
        Path(subagent_types.__file__).resolve()
        == (live.ROOT / "minicode/subagent_types.py").resolve()
    )
    for role in ("EXPLORE", "PLAN", "GENERAL"):
        monkeypatch.delenv(f"MINI_CODE_SUBAGENT_{role}_MAX_TURNS", raising=False)
    private = tmp_path / "private.env"
    monkeypatch.setattr(config, "MINI_CODE_ENV_PATH", private)

    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must never connect to a provider")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    return private


def private_env(path, body):
    path.write_text(body)
    path.chmod(0o600)


def manifest():
    limits, policy = live.role_turn_configuration()
    return {"schemaVersion": 5, "roleTurnLimits": limits, "roleTurnPolicy": policy}


def test_absent_default_does_not_become_configured_override():
    from minicode.subagent_types import configured_max_turns, resolve_max_turns

    observed = manifest()
    assert observed["roleTurnLimits"] == {}
    assert observed["roleTurnPolicy"]["defaults"] == {
        "explore": 12,
        "plan": 8,
        "general": 15,
    }
    with live.frozen_role_turn_overrides(observed):
        assert GENERAL not in live.os.environ
        assert configured_max_turns("general") is None
        assert resolve_max_turns("general") is None


def test_explicit_default_equal_value_is_still_configured(monkeypatch):
    from minicode.subagent_types import configured_max_turns

    monkeypatch.setenv(GENERAL, "15")
    observed = manifest()
    assert observed["roleTurnLimits"] == {"general": 15}
    assert observed["roleTurnPolicy"]["bindings"]["general"] == {
        "source": "process_env",
        "override": "valid",
    }
    with live.frozen_role_turn_overrides(observed):
        assert configured_max_turns("general") == 15


@pytest.mark.parametrize(
    "value,limit,state",
    [
        ("4", 4, "valid"),
        ("", 15, "invalid"),
        ("bad", 15, "invalid"),
        ("201", 15, "invalid"),
    ],
)
def test_private_knob_value_and_invalid_fallback_are_preserved(
    isolated_knobs_and_no_network, value, limit, state
):
    from minicode.subagent_types import configured_max_turns

    private_env(
        isolated_knobs_and_no_network,
        f"{GENERAL}={value}\nUNRELATED_SECRET=must-not-appear\n",
    )
    observed = manifest()
    assert observed["roleTurnLimits"] == {"general": limit}
    assert observed["roleTurnPolicy"]["bindings"]["general"] == {
        "source": "private_env",
        "override": state,
    }
    assert "must-not-appear" not in json.dumps(observed)
    with live.frozen_role_turn_overrides(observed):
        assert configured_max_turns("general") == limit


@pytest.mark.parametrize(
    "value,limit,state",
    [("8", 8, "valid"), ("bad", 15, "invalid"), ("", 15, "invalid")],
)
def test_present_env_shadows_private_even_when_invalid(
    monkeypatch, isolated_knobs_and_no_network, value, limit, state
):
    private_env(isolated_knobs_and_no_network, f"{GENERAL}=4\n")
    monkeypatch.setenv(GENERAL, value)
    observed = manifest()
    assert observed["roleTurnLimits"] == {"general": limit}
    assert observed["roleTurnPolicy"]["bindings"]["general"] == {
        "source": "process_env",
        "override": state,
    }


def test_only_target_numeric_knobs_are_requested_from_private_store(monkeypatch):
    from minicode import env_file

    observed = []

    def read(path, *, allowed_keys):
        observed.append(set(allowed_keys))
        return {}

    monkeypatch.setattr(env_file, "read_private_env_file", read)
    monkeypatch.setenv(GENERAL, "15")
    manifest()
    assert observed == [
        {"MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "MINI_CODE_SUBAGENT_PLAN_MAX_TURNS"}
    ]


def test_scoped_freeze_pins_absence_and_restores_reader_after_failure(
    monkeypatch, isolated_knobs_and_no_network
):
    from minicode import subagent_types

    original = subagent_types._read_max_turns_override
    observed = manifest()
    with pytest.raises(RuntimeError, match="trial failure"):
        with live.frozen_role_turn_overrides(observed):
            private_env(isolated_knobs_and_no_network, f"{GENERAL}=2\n")
            assert subagent_types.configured_max_turns("general") is None
            raise RuntimeError("trial failure")
    assert subagent_types._read_max_turns_override is original
    assert subagent_types.configured_max_turns("general") == 2
    assert GENERAL not in live.os.environ


def test_effective_value_or_source_drift_is_rejected(
    monkeypatch, isolated_knobs_and_no_network
):
    private_env(isolated_knobs_and_no_network, f"{GENERAL}=15\n")
    observed = manifest()
    monkeypatch.setenv(GENERAL, "15")  # Same value, changed source must not slip in.
    with pytest.raises(ValueError, match="roleTurnPolicy"):
        live.validate_role_turn_configuration(observed)
    monkeypatch.setenv(GENERAL, "16")
    with pytest.raises(ValueError, match="roleTurnLimits"):
        live.validate_role_turn_configuration(observed)


def _real_assignment():
    from minicode.agent_budget import AgentTurnBudget
    from minicode.multi_agent_contracts import parse_workflow_request
    from minicode.tooling import ToolContext
    from minicode.workflow_runner import (
        _build_worker_assignment,
        _executor_followup_calls,
    )

    request = parse_workflow_request(
        {
            "requestVersion": 1,
            "profile": {
                "taskId": "task_roles",
                "kind": "change",
                "risk": "low",
                "complex": False,
                "parentHasSufficientContext": True,
            },
            "scope": {
                "readRoots": ["."],
                "writePaths": ["allowed.txt"],
                "forbiddenPaths": [".env", ".git"],
            },
            "criteria": [{"id": "ac1", "kind": "behavior", "text": "Update target."}],
            "verificationChecks": [
                {
                    "checkId": "check_tests",
                    "kind": "tests",
                    "argv": ["pytest", "-q"],
                    "criterionIds": ["ac1"],
                }
            ],
        },
        legacy_task_id="task_roles",
    )
    budget = AgentTurnBudget(
        max_model_calls=40, max_total_tokens=500_000, max_cost_usd="5"
    )
    budget.reserve_model_call()
    preserve_calls = _executor_followup_calls(request.task_profile)
    assignment = _build_worker_assignment(
        request,
        SimpleNamespace(workflow_id="wf_roles", assignment_id="asg_roles"),
        context=ToolContext(cwd=".", _task_timeout_seconds=600),
        agent_budget=budget,
        role="executor",
        mode="implement",
        objective="Update allowed.txt.",
        preserve_model_calls=preserve_calls,
    )
    snapshot = budget.snapshot()
    available_calls = (
        snapshot.limit_model_calls - snapshot.used_model_calls - preserve_calls
    )
    return assignment, snapshot, available_calls


@pytest.mark.parametrize(
    "raw,expected", [(None, None), ("15", 16), ("bad", 16), ("3", 4)]
)
def test_frozen_configuration_reaches_real_executor_assignment_without_raising_shared_budget(
    monkeypatch, raw, expected
):
    if raw is not None:
        monkeypatch.setenv(GENERAL, raw)
    with live.frozen_role_turn_overrides(manifest()):
        assignment, budget, available_calls = _real_assignment()
    assert assignment.budget.max_model_calls == (
        available_calls if expected is None else expected
    )
    assert budget.limit_model_calls == 40
    assert budget.limit_total_tokens == 500_000
    assert budget.limit_cost_usd == "5"


def test_freeze_records_schema4_and_run_and_child_reject_drift(tmp_path, monkeypatch):
    source, output = tmp_path / "history", tmp_path / "engineering"
    runner_fixtures.fixture_source(source)
    live.prepare(output, "engineering", source)
    runner_fixtures.offline_runtime(monkeypatch)
    live.freeze(output)
    observed = live.shared.read_json(output / "manifest.json")
    assert observed["schemaVersion"] == 5
    assert observed["roleTurnLimits"] == {}
    assert live.validate_successor(output) == observed
    module = live.runner(output)
    monkeypatch.setenv(GENERAL, "15")
    with pytest.raises(ValueError, match="roleTurnLimits"):
        module.run_suite(output, set())
    with pytest.raises(ValueError, match="roleTurnLimits"):
        module.execute_trial(output, "never-execute")


def test_formal_rejects_equal_values_with_different_policy_source(
    tmp_path, monkeypatch, isolated_knobs_and_no_network
):
    source, small, formal = (
        tmp_path / "history",
        tmp_path / "small",
        tmp_path / "formal",
    )
    runner_fixtures.fixture_source(source)
    live.prepare(small, "engineering", source)
    live.prepare(formal, "formal", source)
    runner_fixtures.offline_runtime(monkeypatch)
    private_env(isolated_knobs_and_no_network, f"{GENERAL}=15\n")
    live.freeze(small)
    monkeypatch.setattr(live, "gate", lambda _: {"passed": True})
    monkeypatch.setenv(GENERAL, "15")
    with pytest.raises(
        ValueError, match="engineering/formal contract differs: roleTurnPolicy"
    ):
        live.freeze(formal, small)
    assert not (formal / "manifest.json").exists()


def upgraded_audit_fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path, observed, results = audit_fixtures.experiment(tmp_path)
    with zipfile.ZipFile(path / "frozen-code.zip") as bundle:
        sources = {n: bundle.read(n) for n in bundle.namelist()}
    sources["scripts/run_implementation_progress_live.py"] += (
        "\n_LEGACY_ROLE_OVERRIDE_CEILING = "
        + repr(live._LEGACY_ROLE_OVERRIDE_CEILING)
        + "\nROLE_TURN_POLICY = " + repr(live.ROLE_TURN_POLICY) + "\n"
    ).encode()
    sources["minicode/subagent_types.py"] = (
        live.ROOT / "minicode/subagent_types.py"
    ).read_bytes()
    with zipfile.ZipFile(path / "frozen-code.zip", "w") as bundle:
        for name, body in sources.items():
            bundle.writestr(name, body)
    observed.update(manifest())
    observed["schemaVersion"] = (
        4  # Historical role-policy audit fixture stays schema 4.
    )
    observed["frozenCodeArchiveSha256"] = audit_fixtures.digest(
        (path / "frozen-code.zip").read_bytes()
    )
    observed["productionSha256"] = audit_fixtures.digest(
        b"".join(
            n.encode() + b"\0" + sources[n]
            for n in sorted(sources)
            if n.startswith("minicode/") and n.endswith(".py")
        )
    )
    observed["harnessSha256"] = audit_fixtures.digest(
        b"".join(
            n.encode() + b"\0" + sources["scripts/" + n]
            for n in observed["harnessFiles"]
        )
    )
    audit_fixtures.save(path, observed, results)
    return path, observed, results


def test_independent_audit_accepts_schema4_and_preserves_legacy_schema3(tmp_path):
    path, _, _ = upgraded_audit_fixture(tmp_path / "v4")
    result = auditor.Audit(path).run()
    assert result["valid"], result["issues"]
    (tmp_path / "v3").mkdir()
    old_path, _, _ = audit_fixtures.experiment(tmp_path / "v3")
    old_result = auditor.Audit(old_path).run()
    assert old_result["valid"], old_result["issues"]


@pytest.mark.parametrize(
    "mutation",
    [
        "injected_default",
        "wrong_resolved",
        "wrong_defaults",
        "unknown_binding",
        "extra_secret",
        "wrong_formula",
    ],
)
def test_independent_audit_rejects_inconsistent_role_metadata(tmp_path, mutation):
    path, observed, results = upgraded_audit_fixture(tmp_path)
    if mutation == "injected_default":
        observed["roleTurnLimits"] = {"general": 15}
    elif mutation == "wrong_resolved":
        observed["roleTurnPolicy"]["resolved"]["general"] = 99
    elif mutation == "wrong_defaults":
        observed["roleTurnPolicy"]["defaults"]["general"] = 99
    elif mutation == "unknown_binding":
        observed["roleTurnPolicy"]["bindings"]["general"]["source"] = "unknown"
    elif mutation == "extra_secret":
        observed["roleTurnPolicy"]["bindings"]["general"]["raw"] = (
            "never-real-credential"
        )
    else:
        observed["roleTurnPolicy"]["defaultStructuredExecutor"] = "unlimited"
    audit_fixtures.save(path, observed, results)
    assert not auditor.Audit(path).run()["valid"]


def test_unreadable_private_store_is_not_treated_as_absent(monkeypatch):
    from minicode import env_file, subagent_types

    def unreadable(*args, **kwargs):
        raise PermissionError("Do not persist this private path or value")

    monkeypatch.setattr(env_file, "read_private_env_file", unreadable)
    observed = manifest()
    assert observed["roleTurnLimits"] == {"explore": 12, "plan": 8, "general": 15}
    assert observed["roleTurnPolicy"]["bindings"]["general"] == {
        "source": "private_env_unreadable",
        "override": "invalid",
    }
    assert "private path" not in json.dumps(observed)
    with live.frozen_role_turn_overrides(observed):
        assert subagent_types.configured_max_turns("general") == 15
        assert _real_assignment()[0].budget.max_model_calls == 16


@pytest.mark.parametrize("raw", ["15", "bad"])
def test_audit_accepts_configured_value_and_invalid_default_cap(
    tmp_path, monkeypatch, raw
):
    monkeypatch.setenv(GENERAL, raw)
    path, _, _ = upgraded_audit_fixture(tmp_path)
    result = auditor.Audit(path).run()
    assert result["valid"], result["issues"]


def test_new_policy_cannot_be_relabelled_as_schema3(tmp_path):
    path, observed, results = upgraded_audit_fixture(tmp_path)
    observed["schemaVersion"] = 3
    observed.pop("roleTurnPolicy")
    audit_fixtures.save(path, observed, results)
    assert not auditor.Audit(path).run()["valid"]


def test_frozen_default_reaches_shared_runner_env_without_a_spurious_executor_cap(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "history", tmp_path / "engineering"
    runner_fixtures.fixture_source(source)
    live.prepare(output, "engineering", source)
    runner_fixtures.offline_runtime(monkeypatch)
    live.freeze(output)
    observed = live.shared.read_json(output / "manifest.json")
    # This is the historical shared runner's exact role-environment mapping.
    # The current production Assignment is then computed, not a fake allocator.
    with monkeypatch.context() as injected:
        for role, value in observed["roleTurnLimits"].items():
            injected.setenv(f"MINI_CODE_SUBAGENT_{role.upper()}_MAX_TURNS", str(value))
        assignment, budget, available_calls = _real_assignment()
    assert assignment.budget.max_model_calls == available_calls
    assert budget.limit_model_calls == 40


def test_audit_accepts_conservative_private_store_failure(tmp_path, monkeypatch):
    from minicode import env_file

    def unreadable(*args, **kwargs):
        raise OSError("private store unavailable")

    monkeypatch.setattr(env_file, "read_private_env_file", unreadable)
    path, _, _ = upgraded_audit_fixture(tmp_path)
    result = auditor.Audit(path).run()
    assert result["valid"], result["issues"]


@pytest.mark.parametrize("value", [True, 1.0])
def test_injected_role_values_must_preserve_json_types(monkeypatch, value):
    monkeypatch.setenv(GENERAL, "1")
    observed = manifest()
    observed["roleTurnLimits"]["general"] = value
    with pytest.raises(ValueError, match="roleTurnLimits"):
        with live.frozen_role_turn_overrides(observed):
            pytest.fail("A different environment string must never reach production")


def test_schema_downgrade_is_blocked_before_run_or_child_execution(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "history", tmp_path / "engineering"
    runner_fixtures.fixture_source(source)
    live.prepare(output, "engineering", source)
    runner_fixtures.offline_runtime(monkeypatch)
    live.freeze(output)
    observed = live.shared.read_json(output / "manifest.json")
    observed["schemaVersion"] = 3
    observed.pop("roleTurnPolicy")
    live.shared.write_json(output / "manifest.json", observed)
    monkeypatch.setenv(GENERAL, "2")
    module = live.runner(output)
    for action in (
        lambda: live.validate_successor(output),
        lambda: module.run_suite(output, set()),
        lambda: module.execute_trial(output, "never-execute"),
    ):
        with pytest.raises(ValueError, match="requires manifest schema 5"):
            action()
