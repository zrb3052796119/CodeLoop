"""Regressions for defects found in the 2026-08-31 live multi-agent runs.

Four production defects surfaced when real workflows ran against real
providers:

1. A committed workflow ends through the authority-delivery path with no
   closing assistant text; headless and the one-shot CLI printed an empty
   response instead of the authoritative tool_result report.
2. Deadline/budget-failed sub-agents journaled ``modelTurns: 0`` and
   ``toolCalls: 0`` even though the run had made real provider and tool
   calls before failing.
3. The verifier's trusted-executable chain only auto-discovers root-owned
   binaries on ``os.defpath``, which locks conda/venv toolchains under
   ``$HOME`` out of verification entirely.
4. Default turn budget and task deadline were too tight for a real
   repository survey (fixed in their own modules; the default assertions
   live beside the other reliability contracts).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.headless as headless_module
import minicode.tools.task as task_module
from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_runtime import final_response_text
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.subagent_journal import SubagentRunJournal
from minicode.task_deadline import DEFAULT_TASK_TIMEOUT_SECONDS
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool
from minicode.verifier_sandbox import (
    default_verifier_trusted_executables,
    resolve_verifier_host_capabilities,
    user_registered_trusted_executables,
)


# --- Fix 1: the authoritative tool_result is the deliverable final answer ---


def test_final_response_text_prefers_the_last_assistant_text() -> None:
    messages = [
        {"role": "user", "content": "run it"},
        {"role": "assistant", "content": "working"},
        {"role": "assistant", "content": "done: summary"},
    ]

    assert final_response_text(messages) == "done: summary"


def test_final_response_text_falls_back_to_authoritative_tool_result() -> None:
    # The exact message shape the authority-delivery return path produces:
    # an assistant_tool_call plus its tool_result, with no plain assistant
    # text anywhere in the turn.
    messages = [
        {"role": "user", "content": "add multiply"},
        {
            "role": "assistant_tool_call",
            "toolUseId": "toolu_1",
            "toolName": "task",
            "input": {"agent_type": "workflow"},
        },
        {
            "role": "tool_result",
            "toolUseId": "toolu_1",
            "toolName": "task",
            "content": "workflow committed: changesCommitted=true",
        },
    ]

    assert (
        final_response_text(messages) == "workflow committed: changesCommitted=true"
    )


def test_final_response_text_returns_none_without_text_or_tool_result() -> None:
    assert final_response_text([]) is None
    assert final_response_text([{"role": "user", "content": "hi"}]) is None


def test_headless_returns_workflow_report_without_a_final_assistant_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = "workflow completed: changesCommitted=true, 2 files promoted"

    class FakeRuntime:
        system_prompt = "system"
        skill_routing = SimpleNamespace(
            selected_skill_dicts=lambda: [],
            to_dict=lambda: {},
        )

        def execute(self, _messages, _observation):
            return [
                {"role": "user", "content": "add multiply"},
                {
                    "role": "assistant_tool_call",
                    "toolUseId": "toolu_1",
                    "toolName": "task",
                    "input": {"agent_type": "workflow"},
                },
                {
                    "role": "tool_result",
                    "toolUseId": "toolu_1",
                    "toolName": "task",
                    "content": report,
                },
            ]

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        headless_module,
        "create_agent_turn_runtime",
        lambda **_kwargs: FakeRuntime(),
    )
    monkeypatch.setattr(
        headless_module,
        "prepare_conversation_messages",
        lambda messages, *, system_prompt, user_message: [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    monkeypatch.chdir(tmp_path)

    response = headless_module.run_headless(
        "add multiply via workflow",
        run_observation_enabled=False,
    )

    assert response == report


# --- Fix 2: failed sub-agents keep their real model/tool call counts ---


def test_journal_sink_counts_model_and_tool_starts() -> None:
    sink = task_module._SubagentJournalSink(None, "sub_" + "a" * 32)

    for _ in range(3):
        sink.emit("model.started", step=1, payload={})
    sink.emit("model.failed", step=1, payload={})
    for _ in range(5):
        sink.emit("tool.started", step=1, payload={})

    assert sink.model_turns == 3
    assert sink.tool_calls == 5


def test_deadline_failed_sub_agent_journals_real_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _event_sink=SimpleNamespace(open_subagent_journal=lambda: journal),
    )

    def turn_that_surveys_then_misses_the_deadline(**kwargs):
        sink = kwargs["event_sink"]
        for _ in range(4):
            sink.emit("model.started", step=1, payload={})
        for _ in range(6):
            sink.emit("tool.started", step=1, payload={})
        raise task_module.AgentTurnDeadlineExceeded("agent turn deadline exceeded")

    monkeypatch.setattr(
        task_module, "run_agent_turn", turn_that_surveys_then_misses_the_deadline
    )

    result = task_tool.run(
        {
            "action": "run",
            "agent_type": "explore",
            "prompt": "Survey the repository layout and summarize the modules.",
            "description": "survey",
        },
        context,
    )

    assert result.ok is False
    assert "sub_agent_deadline_exceeded" in result.output
    (summary,) = journal.list_runs()
    assert summary.outcome == "failed"
    assert summary.model_turns == 4
    assert summary.tool_calls == 6


def test_budget_failed_sub_agent_journals_real_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / ("run_" + "b" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _event_sink=SimpleNamespace(open_subagent_journal=lambda: journal),
    )

    def turn_that_spends_then_exceeds_budget(**kwargs):
        sink = kwargs["event_sink"]
        for _ in range(2):
            sink.emit("model.started", step=1, payload={})
        sink.emit("tool.started", step=1, payload={})
        raise AgentBudgetExceeded(
            "token budget exhausted",
            AgentTurnBudget.from_runtime({}).snapshot(),
        )

    monkeypatch.setattr(
        task_module, "run_agent_turn", turn_that_spends_then_exceeds_budget
    )

    result = task_tool.run(
        {
            "action": "run",
            "agent_type": "explore",
            "prompt": "Survey the repository layout and summarize the modules.",
            "description": "survey",
        },
        context,
    )

    assert result.ok is False
    assert "agent_budget_exceeded" in result.output
    (summary,) = journal.list_runs()
    assert summary.outcome == "budget_exceeded"
    assert summary.model_turns == 2
    assert summary.tool_calls == 1


# --- Fix 3: user-registered trusted executables (env channel) ---


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_user_registered_trusted_executables_validates_entries(
    tmp_path: Path,
) -> None:
    good = _make_executable(tmp_path / "pytest-verify")

    env = {
        "MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES": (
            f"pytest={good}, relative=bin/pytest, missing={tmp_path / 'nope'}, "
            "noeq, bad/name=/usr/bin/make"
        )
    }

    assert user_registered_trusted_executables(env) == {"pytest": str(good)}
    assert user_registered_trusted_executables({}) == {}
    assert user_registered_trusted_executables(
        {"MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES": "  "}
    ) == {}


def test_registered_entry_overrides_default_and_survives_home_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The live failure: a venv python under $HOME is exactly what the default
    # discovery refuses to trust, and no override existed.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    venv_python = _make_executable(fake_home / "python3")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv(
        "MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES", f"python3={venv_python}"
    )

    registered = user_registered_trusted_executables()
    assert registered == {"python3": str(venv_python)}

    _backend, trusted = resolve_verifier_host_capabilities({"model": "fake"})
    assert trusted.get("python3") == str(venv_python)

    defaults = default_verifier_trusted_executables()
    assert str(venv_python) not in defaults.values()

    workspace = tmp_path / "repo"
    workspace.mkdir()
    verifier = DeterministicVerifier(
        trusted_executables={"python3": str(venv_python)},
        explicit_trusted_executables=registered,
    )
    resolved, failure = verifier._resolve_trusted_executable(
        str(venv_python), cwd=str(workspace)
    )
    assert failure is None
    assert resolved == str(venv_python)


def test_unregistered_home_executable_is_still_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    stranger = _make_executable(fake_home / "stranger")
    monkeypatch.setenv("HOME", str(fake_home))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    verifier = DeterministicVerifier(trusted_executables={"stranger": str(stranger)})
    resolved, failure = verifier._resolve_trusted_executable(
        str(stranger), cwd=str(workspace)
    )

    assert resolved is None
    assert failure == "trusted_executable_unavailable"


def test_workspace_executable_stays_rejected_even_when_registered(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    inside = _make_executable(workspace / "run_tests.sh")

    verifier = DeterministicVerifier(
        trusted_executables={"run_tests": str(inside)},
        explicit_trusted_executables={"run_tests": str(inside)},
    )
    resolved, failure = verifier._resolve_trusted_executable(
        str(inside), cwd=str(workspace)
    )

    # Verification must never execute repository bytes, explicit or not.
    assert resolved is None
    assert failure == "trusted_executable_unavailable"


def test_read_roots_cover_only_non_system_toolchain_prefixes() -> None:
    verifier = DeterministicVerifier()

    assert verifier._read_roots_for("/usr/bin/make") == ()
    assert verifier._read_roots_for("/usr/local/bin/ruff") == ("/usr/local",)


def test_seatbelt_profile_grants_registered_toolchain_prefix_read(
    tmp_path: Path,
) -> None:
    from minicode.verifier_sandbox import DarwinSeatbeltVerifierSandbox

    launcher = tmp_path / "sandbox-exec"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    cwd = tmp_path / "workspace"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    toolchain = tmp_path / "toolchain"
    (toolchain / "bin").mkdir(parents=True)
    backend = DarwinSeatbeltVerifierSandbox(str(launcher))

    wrapped = backend.wrap_command(
        argv=("/usr/bin/make",),
        cwd=str(cwd),
        verifier_home=str(home),
        extra_read_roots=(str(toolchain),),
    )

    profile = wrapped[2]
    assert f"(subpath \"{toolchain.resolve()}\")" in profile
    # Network denial and the write surface stay untouched by the extension.
    assert "(deny network*)" in profile
    write_clause = profile.split("(allow file-write*")[1]
    assert str(toolchain.resolve()) not in write_clause


# --- Fix 4: defaults sized for real repositories ---


def test_default_turn_budget_does_not_reimpose_cumulative_limits() -> None:
    budget = AgentTurnBudget.from_runtime({})

    assert budget.max_total_tokens is None
    assert budget.max_model_calls is None
    assert budget.max_cost_usd is None


def test_default_task_does_not_invent_a_deadline() -> None:
    assert DEFAULT_TASK_TIMEOUT_SECONDS is None


# --- P3: parallel verification checks ---------------------------------------


def test_verification_checks_run_concurrently_and_keep_request_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent checks must overlap in time yet sequence their evidence.

    Uses the real structured-routing harness: three checks whose stubbed
    verifier sleeps briefly prove wall-time overlap, and the projected
    verification keeps the request-order evidence chain the audit replays.
    """

    import time as _time

    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    import test_workflow_v2_structured_routing as routing

    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    execution_windows: list[tuple[float, float]] = []
    lock = __import__("threading").Lock()

    def worker(input_data, context):
        role = routing._worker_role(input_data)
        if role == "analyst":
            routing._write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            executor_prompt_text = input_data["prompt"]
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            routing._write_executor_declaration(input_data, context)
        else:
            routing._write_reviewer_result(input_data, context)
        return routing.ToolResult(ok=True, output="ok")

    def slow_verifier(self, **request):
        started = _time.monotonic()
        _time.sleep(0.4)
        with lock:
            execution_windows.append((started, _time.monotonic()))
        return routing._verification_result(request)

    monkeypatch.setattr(routing.task_module, "_run", worker)
    monkeypatch.setattr(routing.DeterministicVerifier, "run", slow_verifier)

    request = routing._request()
    request["verificationChecks"] = [
        {
            "checkId": f"check_{index}",
            "kind": "tests",
            "argv": ["pytest", "-q", f"t{index}.py"],
            "criterionIds": ["ac1"],
        }
        for index in range(3)
    ]

    started = _time.monotonic()
    result = routing._run(
        tmp_path,
        request,
        monkeypatch,
        commit_audit_waived=True,
    )
    elapsed = _time.monotonic() - started

    assert result.ok is True
    # Pairwise overlap is the concurrency proof. The wall clock only rejects
    # a hang; a loaded runner can sit a little above the 1.2s serial floor.
    assert len(execution_windows) == 3
    for (start_a, end_a), (start_b, end_b) in zip(
        execution_windows, execution_windows[1:]
    ):
        assert start_b < end_a
    assert elapsed < 2.0


# --- P3: structured workflows accept on-demand parallel exploration ---------


def test_structured_workflow_inlines_parallel_explore_into_analyst_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    import test_workflow_v2_structured_routing as routing

    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    analyst_prompts: list[str] = []
    executor_prompt_text = ""
    explore_dispatches: list[str] = []

    original_run = routing.task_module._run

    def worker(input_data, context):
        nonlocal executor_prompt_text
        if input_data.get("agent_type") == "explore":
            explore_dispatches.append(input_data.get("description", ""))
            return routing.ToolResult(
                ok=True, output=f"facet survey: {input_data.get('description')}"
            )
        role = routing._worker_role(input_data)
        if role == "analyst":
            analyst_prompts.append(input_data["prompt"])
            routing._write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            executor_prompt_text = input_data["prompt"]
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            routing._write_executor_declaration(input_data, context)
        else:
            routing._write_reviewer_result(input_data, context)
        return routing.ToolResult(ok=True, output="ok")

    def verifier(self, **request):
        return routing._verification_result(request)

    monkeypatch.setattr(routing.task_module, "_run", worker)
    monkeypatch.setattr(routing.DeterministicVerifier, "run", verifier)

    result = routing.task_module.task_tool.run(
        routing.task_module.task_tool.validator(
            {
                "description": "fix with survey",
                "prompt": "Update allowed.txt content.",
                "agent_type": "workflow",
                "parallel_explore": True,
                "write_paths": ["allowed.txt"],
                "verification_commands": [["pytest", "-q"]],
            }
        ),
        routing.ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=routing.SubagentMailbox(),
            _central_workflow_v2_enforced=True,
            _workflow_commit_audit_waived=True,
            _event_sink=type(
                "AuditSink",
                (),
                {
                    "emit": staticmethod(lambda *a, **k: None),
                    "record_scheduler_audit": staticmethod(lambda audit: True),
                    "record_scheduler_commit_ready": staticmethod(
                        lambda ready: True
                    ),
                },
            )(),
        ),
    )

    assert result.ok is True, result.output[:400]
    assert explore_dispatches, "fan-out explores were not dispatched"
    # The narrow scope takes the scoped route, so the survey is inlined into
    # the Executor prompt (the only Worker) instead of an Analyst prompt.
    assert analyst_prompts == []
    trace = routing.extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["ruleId"] == "route.scoped_change"
    assert "PRE-SURVEY" in executor_prompt_text


# --- Wide-survey failure-mode repairs ---------------------------------------


def test_delegation_circuit_opens_after_two_failed_readonly_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    import test_workflow_v2_structured_routing as routing

    mailbox = routing.SubagentMailbox()
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _subagent_mailbox=mailbox,
    )
    attempts = []

    def failing_worker(input_data, _context):
        attempts.append(input_data.get("agent_type"))
        return routing.ToolResult(ok=False, output="error[boom]")

    monkeypatch.setattr(task_module, "_SYNC_RUN", failing_worker)

    first = task_module.task_tool.run(
        task_module.task_tool.validator(
            {
                "action": "run",
                "agent_type": "explore",
                "prompt": "Enumerate callers of resolve_name across src/.",
                "description": "survey",
            }
        ),
        context,
    )
    second = task_module.task_tool.run(
        task_module.task_tool.validator(
            {
                "action": "run",
                "agent_type": "explore",
                "prompt": "Enumerate callers of resolve_name across src/.",
                "description": "survey",
            }
        ),
        context,
    )
    third = task_module.task_tool.run(
        task_module.task_tool.validator(
            {
                "action": "run",
                "agent_type": "explore",
                "prompt": "Enumerate callers of resolve_name across src/.",
                "description": "survey",
            }
        ),
        context,
    )

    assert not first.ok and not second.ok
    # The third dispatch never reaches the worker: the circuit directs the
    # parent to do the work itself instead of burning more steps.
    assert not third.ok
    assert "delegation_circuit_open" in third.output
    assert "grep_files" in third.output
    assert attempts == ["explore", "explore"]


def test_delegation_circuit_resets_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternating fail/success outcomes never accumulate to open.

    Once the circuit is open it stays open for the turn by design — the
    directed recovery is self-execution, not another dispatch — so the reset
    semantics only matter before the threshold is reached.
    """

    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    import test_workflow_v2_structured_routing as routing

    mailbox = routing.SubagentMailbox()
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _subagent_mailbox=mailbox,
    )
    state = {"ok": True}

    def flaky_worker(input_data, _context):
        if state["ok"]:
            return routing.ToolResult(ok=True, output="survey complete")
        return routing.ToolResult(ok=False, output="error[boom]")

    monkeypatch.setattr(task_module, "_SYNC_RUN", flaky_worker)
    payload = task_module.task_tool.validator(
        {
            "action": "run",
            "agent_type": "plan",
            "prompt": "Enumerate callers of resolve_name across src/.",
            "description": "survey",
        }
    )
    state["ok"] = False
    task_module.task_tool.run(payload, context)  # failure -> 1
    state["ok"] = True
    task_module.task_tool.run(payload, context)  # success -> reset to 0
    state["ok"] = False
    task_module.task_tool.run(payload, context)  # failure -> 1
    state["ok"] = True
    still_allowed = task_module.task_tool.run(payload, context)

    assert still_allowed.ok
    assert "delegation_circuit_open" not in still_allowed.output


def test_large_enumeration_detail_note_is_relaid_to_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detail note written by the worker is relaid into the tool result.

    The read-back lives in the real ``_run`` tail, so the nested loop is
    scripted (``run_agent_turn``) rather than the whole dispatch; the result
    key is recovered from the instruction embedded in the child's system
    message, exactly where the real child reads it.
    """

    import re as _re
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    import test_workflow_v2_structured_routing as routing
    from minicode.task_outcome import canonicalize_task_outcome

    mailbox = routing.SubagentMailbox()
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _subagent_mailbox=mailbox,
    )
    enumeration = "\n".join(f"mod_{index}" for index in range(51))

    def turn(**kwargs):
        system_text = str(kwargs["messages"][0].get("content", ""))
        match = _re.search(r"result mailbox key `([A-Za-z0-9_.:-]+)`", system_text)
        assert match is not None
        result_key = match.group(1)
        sub_mailbox = kwargs.get("subagent_mailbox") or kwargs["context"]._subagent_mailbox
        sub_mailbox.write(
            result_key,
            '{"resultVersion":1,"summary":"full list in mailbox note '
            + result_key
            + '_detail","files":[],"risks":[],"verification":{"status":"not_run","checks":[]}}',
            author="explore",
        )
        sub_mailbox.write(
            f"{result_key}_detail",
            enumeration,
            author="explore",
        )
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "enumerated"}]

    monkeypatch.setattr(task_module, "run_agent_turn", turn)
    result = task_module.task_tool.run(
        task_module.task_tool.validator(
            {
                "action": "run",
                "agent_type": "explore",
                "prompt": "Enumerate callers of resolve_name across src/.",
                "description": "survey",
            }
        ),
        context,
    )

    assert result.ok
    assert "=== ENUMERATION DETAIL ===" in result.output
    assert "mod_0" in result.output and "mod_50" in result.output


def test_explore_prompt_teaches_grep_first_enumeration() -> None:
    from minicode.subagent_types import AGENT_TYPES

    prompt = AGENT_TYPES["explore"]["system_prompt"]
    assert "grep_files" in prompt
    assert "COMPLETE list" in prompt
