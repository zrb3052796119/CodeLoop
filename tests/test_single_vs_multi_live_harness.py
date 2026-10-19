"""Provider-free checks of experiment controls, not effectiveness evidence."""

from __future__ import annotations

from copy import deepcopy
import json
import time

import pytest

from scripts import run_single_vs_multi_live as live
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.types import AgentStep, ModelUsage


@pytest.fixture(autouse=True)
def use_real_adapter_with_scripted_transport(monkeypatch):
    monkeypatch.setenv("MINI_CODE_MODEL_MODE", "real")


def config():
    return {
        "model": "glm-5.3-flash",
        "provider": "custom",
        "customBaseUrl": "https://example.invalid/v1",
        "customApiKey": "test-only",
        "subagentRoutingEnabled": False,
        "modelMaxRetries": 0,
        "reflectionSynthesizerMode": "rule",
        "memoryHybridEnabled": False,
        "intentSemanticClassifier": {"enabled": False},
        "skillCandidateReranker": {"enabled": False},
        "agentTurnBudget": {
            k: live.LIMITS[k] for k in ("maxTokens", "maxModelCalls", "maxCostUsd")
        },
    }


@pytest.mark.parametrize("arm", ["single", "multi"])
def test_real_runtime_smoke_uses_local_scope_and_provider_receipts(
    tmp_path, monkeypatch, arm
):
    """Exercise real factory, ToolCall dictionary, edit approval, oracle and ledger."""
    from minicode.skills import discover_skills
    from minicode import cybernetic_supervisor

    original_discovery = discover_skills
    user_state = tmp_path / "user-supervisor.json"
    user_state.write_text("keep user state")
    monkeypatch.setattr(cybernetic_supervisor, "SUPERVISOR_STATE_PATH", user_state)
    monkeypatch.setattr(live, "frozen_runtime", lambda: deepcopy(config()))
    case = {
        "id": "smoke",
        "category": "small-fix",
        "files": {"value.py": "VALUE = 1\n", "protected.txt": "keep\n"},
        "prompt": "Change VALUE to 2 in value.py.",
        "authorized_paths": ["value.py"],
        "oracle_source": 'import pathlib, sys\nassert (pathlib.Path(sys.argv[1]) / "value.py").read_text() == "VALUE = 2\\n"\n',
    }
    live.write_json(tmp_path / "tasks.json", {"cases": [case]})
    live.freeze(tmp_path, 1)
    calls = []

    def scripted(adapter, messages, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "edit1",
                        "toolName": "write_file",
                        "input": {"path": "value.py", "content": "VALUE = 2\n"},
                    }
                ],
                usage=ModelUsage(10, 5, 0, 0, "provider"),
            )
        return AgentStep(
            type="assistant",
            content="Completed.",
            kind="final",
            usage=ModelUsage(12, 3, 0, 0, "provider"),
        )

    monkeypatch.setattr(OpenAIModelAdapter, "next", scripted)
    live.execute_trial(tmp_path, f"smoke-r1-{arm}")
    row = live.read_json(tmp_path / "trials" / f"smoke-r1-{arm}.json")
    assert row["success"], row
    assert row["modelCalls"] == len(calls) == 2
    assert row["totalTokens"] == 30
    assert row["usageComplete"] is True
    assert row["changedFiles"] == ["value.py"]
    contract = live.read_json(
        tmp_path / "private" / f"smoke-r1-{arm}" / "runtime-contract.json"
    )
    assert ("task" in contract["tools"]) == (arm == "multi")
    assert not {"load_skill", "ask_user"} & set(contract["tools"])
    assert all(k["max_retries_override"] == 0 for k in calls)
    assert original_discovery is discover_skills
    assert user_state.read_text() == "keep user state"
    assert (
        tmp_path
        / "private"
        / f"smoke-r1-{arm}"
        / "state"
        / "cybernetic_supervisor.json"
    ).is_file()


def test_created_deleted_symlink_and_binary_files_are_in_scope_audit(tmp_path):
    (tmp_path / "existing").write_text("old")
    before = live.workspace_files(tmp_path)
    (tmp_path / "existing").unlink()
    (tmp_path / "new.bin").write_bytes(b"\xff\x00")
    (tmp_path / "link").symlink_to("/outside")
    assert live.changed_paths(before, live.workspace_files(tmp_path)) == [
        "existing",
        "link",
        "new.bin",
    ]


def test_workflow_cannot_bypass_external_write_scope():
    assert live.workflow_scope(
        {"agent_type": "workflow", "write_paths": ["new.py"]}, ["new.py"]
    )
    assert not live.workflow_scope(
        {"agent_type": "workflow", "write_paths": ["protected.py"]}, ["new.py"]
    )
    assert not live.workflow_scope(
        {"agent_type": "workflow", "write_paths": ["../new.py"]}, ["new.py"]
    )
    assert not live.workflow_scope(
        {
            "agent_type": "workflow",
            "workflow_request": {"scope": {"writePaths": ["."]}},
        },
        ["new.py"],
    )


def test_failed_and_missing_usage_never_become_zero_cost_success(tmp_path):
    ledger = tmp_path / "calls.jsonl"
    rows = [
        {"event": "started", "call": 1},
        {
            "event": "completed",
            "call": 1,
            "usage": {
                "source": "provider",
                "input_tokens": None,
                "output_tokens": None,
            },
            "toolCalls": [],
        },
        {"event": "started", "call": 2},
        {"event": "failed", "call": 2},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"event":')
    result = live.ledger_summary(ledger)
    assert result["modelCalls"] == 2
    assert result["failedCalls"] == 1
    assert result["usageComplete"] is False
    assert result["estimatedCostCny"] is None
    assert result["ledgerDamaged"] is True


def test_same_model_on_different_endpoint_is_rejected_before_request(tmp_path):
    from minicode.model_registry import create_model_adapter

    adapter = create_model_adapter(config()["model"], None, config())
    adapter.runtime["openaiBaseUrl"] = "https://different.invalid/v1"
    ledger = live.CallLedger(
        tmp_path / "calls.jsonl", config()["model"], time.monotonic() + 30, config()
    )

    def forbidden(*args, **kwargs):
        pytest.fail("No network call is allowed after route drift")

    with pytest.raises(RuntimeError, match="route_drift"):
        ledger.wrap(forbidden)(adapter, [])
    assert ledger.count == 0


def test_children_inherit_exact_parent_route():
    from minicode.subagent_model_routing import create_subagent_model_adapter
    import run_north_star_live as north

    expected = north._runtime_profile_from_runtime(config())
    for role in ("explore", "plan", "general"):
        adapter = create_subagent_model_adapter(config(), role, tools=None)
        assert north._runtime_profile_from_adapter(adapter, config()) == expected
