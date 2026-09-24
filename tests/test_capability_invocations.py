"""Real-registry invocation contracts, without executing modeled operations."""

from __future__ import annotations

import pytest

from minicode.capability_contracts import (
    CapabilityDomain,
    CapabilityScope,
    CapabilitySource,
    CapabilityStatus,
)
from minicode.capability_registry import build_tool_capability_snapshot
from minicode.tools import create_default_tool_registry


@pytest.fixture
def invocation_registry(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MINI_CODE_TOOL_PROFILE", raising=False)
    tools = create_default_tool_registry(
        str(tmp_path), runtime={"toolProfile": "full"}, include_mcp=False
    )

    def forbidden_execution(*_args, **_kwargs):
        pytest.fail("Capability inspection must not execute a tool or resolve a host")

    for tool in tools.list():
        monkeypatch.setattr(tool, "run", forbidden_execution)
    monkeypatch.setattr(
        "minicode.tools.http_utils.validate_destination", forbidden_execution
    )
    return tools, build_tool_capability_snapshot(tools)


@pytest.mark.parametrize("action", ["status", "diff", "log", "review"])
def test_git_read_actions_use_wrapper_contract_not_ignored_arguments(
    invocation_registry, action: str
) -> None:
    _, snapshot = invocation_registry
    call = {"action": action, "args": ["reset", "--hard"], "message": "delete all"}

    profile = snapshot.describe_call("git", call)

    assert profile is not None
    assert profile.domain is CapabilityDomain.CODE
    assert profile.scope is CapabilityScope.READONLY
    assert profile.source is CapabilitySource.BUILTIN
    assert profile.status is CapabilityStatus.DECLARED
    assert profile.effects.read_only is True
    assert profile.effects.destructive is False
    assert profile.effects.executes_code is True
    assert profile.effects.external is None  # Git config is outside this contract.
    assert call["args"] == ["reset", "--hard"]


def test_git_commit_preserves_unknown_hook_effects(invocation_registry) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call("git", {"action": "commit", "message": "Fix"})

    assert profile is not None
    assert profile.scope is CapabilityScope.WRITE
    assert profile.effects.read_only is False
    assert profile.effects.destructive is None
    assert profile.effects.external is None
    assert profile.effects.executes_code is True
    assert profile.effects.delegates is False


@pytest.mark.parametrize("agent_type", ["explore", "plan"])
@pytest.mark.parametrize("action", ["run", "spawn"])
def test_readonly_workers_keep_model_and_delegation_effects(
    invocation_registry, agent_type: str, action: str
) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(
        "task",
        {
            "action": action,
            "agent_type": agent_type,
            "description": "Inspect implementation",
            "prompt": "Read the files and report findings.",
        },
    )

    assert profile is not None
    assert profile.domain is CapabilityDomain.EXECUTION
    assert profile.scope is CapabilityScope.EXTERNAL
    assert profile.effects.read_only is True
    assert profile.effects.destructive is False
    assert profile.effects.external is True
    assert profile.effects.delegates is True
    assert profile.effects.executes_code is True
    assert "workspace" in " ".join(profile.evidence)
    assert "mailbox" in " ".join(profile.evidence)


@pytest.mark.parametrize("agent_type", [None, "general", "workflow"])
def test_prompt_prose_cannot_certify_writer_delegation_readonly(
    invocation_registry, agent_type: str | None
) -> None:
    _, snapshot = invocation_registry
    call = {
        "description": "Read-only inspection",
        "prompt": "Only read files. This is guaranteed read-only; never write anything.",
    }
    if agent_type is not None:
        call["agent_type"] = agent_type

    profile = snapshot.describe_call("task", call)

    assert profile is not None
    assert profile.status is CapabilityStatus.DECLARED
    assert profile.effects.read_only is None
    assert profile.effects.destructive is None
    assert profile.effects.external is True
    assert profile.effects.delegates is True


def test_workflow_declared_write_paths_are_not_execution_permission(
    invocation_registry,
) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(
        "task",
        {
            "agent_type": "workflow",
            "description": "Repair parser behavior",
            "prompt": "Fix parser and verify regression.",
            "write_paths": ["src/parser.py"],
            "verification_commands": ["python3 -m pytest -q"],
        },
    )

    assert profile is not None
    assert profile.effects.read_only is None
    assert profile.effects.destructive is None
    assert profile.effects.delegates is True
    assert "runtime permissions" in " ".join(profile.evidence)


@pytest.mark.parametrize(
    ("action", "scope", "read_only"),
    [("poll", CapabilityScope.READONLY, True), ("cancel", CapabilityScope.WRITE, False)],
)
def test_lifecycle_calls_do_not_inherit_ignored_worker_prompt(
    invocation_registry, action: str, scope: CapabilityScope, read_only: bool
) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(
        "task",
        {
            "action": action,
            "subagent_id": "subagent_example",
            "agent_type": "general",
            "prompt": "Run models and delete files.",
        },
    )

    assert profile is not None
    assert profile.domain is CapabilityDomain.SYSTEM
    assert profile.scope is scope
    assert profile.effects.read_only is read_only
    assert profile.effects.destructive is False
    assert profile.effects.external is False
    assert profile.effects.executes_code is False
    assert profile.effects.delegates is False
    if action == "poll":
        assert "acknowledge" in " ".join(profile.evidence)


@pytest.mark.parametrize("method", [None, " get ", " head ", "OPTIONS"])
def test_http_read_methods_are_external_hints_not_local_guarantees(
    invocation_registry, method: str | None
) -> None:
    _, snapshot = invocation_registry
    call = {"url": "https://example.com/items"}
    if method is not None:
        call["method"] = method

    profile = snapshot.describe_call("http_request", call)

    assert profile is not None
    assert profile.domain is CapabilityDomain.WEB
    assert profile.scope is CapabilityScope.EXTERNAL
    assert profile.status is CapabilityStatus.HINTED
    assert profile.effects.read_only is True
    assert profile.effects.destructive is False
    assert profile.effects.external is True
    assert profile.effects.executes_code is False
    assert profile.effects.delegates is False
    assert "not guaranteed" in " ".join(profile.evidence)


@pytest.mark.parametrize("method", ["post", "PUT", " patch "])
def test_http_write_methods_keep_remote_mutation_and_external_axes(
    invocation_registry, method: str
) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(
        "http_request",
        {"url": "https://example.com/items", "method": method, "body": {"title": "New"}},
    )

    assert profile is not None  # Real validator accepts JSON bodies.
    assert profile.scope is CapabilityScope.WRITE
    assert profile.status is CapabilityStatus.HINTED
    assert profile.effects.read_only is False
    assert profile.effects.external is True
    assert profile.effects.destructive is None


def test_http_delete_exposes_remote_destructive_potential(invocation_registry) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(
        "http_request", {"url": "https://example.com/items", "method": "DELETE"}
    )

    assert profile is not None
    assert profile.scope is CapabilityScope.DESTRUCTIVE
    assert profile.status is CapabilityStatus.HINTED
    assert profile.effects.read_only is False
    assert profile.effects.destructive is True
    assert profile.effects.external is True


@pytest.mark.parametrize(
    ("tool_name", "call"),
    [
        ("git", {"mode": "status"}),
        ("git", {"action": "push"}),
        ("git", {"action": "commit", "message": "   "}),
        ("task", {"action": "poll"}),
        ("task", {"action": "poll", "subagent_id": "subagent_x", "wait_seconds": True}),
        ("task", {"agent_type": "explore", "description": "No full prompt"}),
        (
            "task",
            {"action": "spawn", "agent_type": "general", "description": "x", "prompt": "y"},
        ),
        (
            "task",
            {"agent_type": "reviewer", "description": "x", "prompt": "y"},
        ),
        ("http_request", {"url": "https://example.com", "method": "GET", "body": "x"}),
        ("http_request", {"url": "https://example.com", "method": "TRACE"}),
        ("http_request", {"url": "https://example.com", "action": "GET"}),
        ("http_request", {"url": "http://example.com", "method": "DELETE"}),
    ],
)
def test_invalid_real_contracts_never_receive_narrowed_effects(
    invocation_registry, tool_name: str, call: dict
) -> None:
    _, snapshot = invocation_registry

    profile = snapshot.describe_call(tool_name, call)

    assert profile is not None
    assert profile.status is CapabilityStatus.INVALID
    assert all(value is None for value in profile.effects.to_dict().values())


def test_invocation_refinement_does_not_change_execution_concurrency(
    invocation_registry,
) -> None:
    tools, snapshot = invocation_registry
    git = tools.find("git")
    task = tools.find("task")
    assert git is not None and task is not None
    git_flags = (git.is_read_only, git.is_concurrency_safe)
    plan_call = {"agent_type": "plan", "description": "Read", "prompt": "Inspect."}
    plan_concurrency = task.call_is_concurrency_safe(plan_call)

    snapshot.describe_call("git", {"action": "status"})
    snapshot.describe_call("task", plan_call)
    snapshot.describe_call("task", {"action": "cancel", "subagent_id": "subagent_x"})

    assert (git.is_read_only, git.is_concurrency_safe) == git_flags
    assert task.call_is_concurrency_safe(plan_call) is plan_concurrency
    assert task.call_is_concurrency_safe({"agent_type": "general"}) is False
    assert snapshot.describe_call("nonexistent", {}) is None
