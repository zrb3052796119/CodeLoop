from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from minicode.agent_reflection import ReflectionEngine
from minicode.agent_runtime import AgentTurnRuntime, create_agent_turn_runtime
from minicode.conversation import (
    ConversationSessionConflict,
    ConversationTurnService,
)
from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory import MemoryManager, MemoryScope
from minicode.run_journal import RunJournal
from minicode.session import SessionWriteConflictError
from minicode.task_ledger import TaskLedger
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep, ChatMessage, ModelAdapter
from minicode.user_evidence import (
    TrustedUserEvidenceSeed,
    looks_like_correction,
    normalize_trusted_user_evidence,
)

_TURN_1 = "turn_" + "1" * 32
_TURN_2 = "turn_" + "2" * 32
_TURN_3 = "turn_" + "3" * 32


class _FinalModel(ModelAdapter):
    model_id = "test-final-model"

    def __init__(self, content: str = "done") -> None:
        self.content = content

    def next(
        self,
        _messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        del on_stream_chunk
        return AgentStep(type="assistant", content=self.content)


class _Permissions:
    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def get_summary(self) -> list[str]:
        return []


class _SafeRuntime:
    system_prompt = "SYSTEM"
    skill_routing = None

    def __init__(self, reply: str = "safe reply") -> None:
        self.permissions = _Permissions()
        self.tools = ToolRegistry([])
        self.reply = reply

    def execute(
        self,
        messages: list[dict[str, Any]],
        _observation: object,
        **_kwargs: object,
    ) -> list[dict[str, Any]]:
        return [*messages, {"role": "assistant", "content": self.reply}]

    def dispose(self) -> None:
        self.tools.dispose()


class _CapturingRuntimeFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _SafeRuntime:
        self.calls.append(dict(kwargs))
        return _SafeRuntime()


class _RealRuntimeFactory:
    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        workspace: Path,
        trusted_user_evidence: tuple[object, ...] = (),
        **kwargs: Any,
    ) -> AgentTurnRuntime:
        self.calls.append(
            {
                **kwargs,
                "workspace": workspace,
                "trusted_user_evidence": trusted_user_evidence,
            }
        )
        return AgentTurnRuntime(
            workspace=workspace,
            runtime={"reflectionSynthesizerMode": "rule"},
            tools=ToolRegistry([]),
            permissions=_Permissions(),
            memory_manager=self.manager,
            model=_FinalModel(),
            skill_routing=None,
            system_prompt="SYSTEM",
            trusted_user_evidence=trusted_user_evidence,
        )


@pytest.fixture
def conversation_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def _service(
    workspace: Path,
    data_dir: Path,
    runtime_factory: object,
) -> ConversationTurnService:
    return ConversationTurnService(
        workspace,
        runtime_factory=runtime_factory,  # type: ignore[arg-type]
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )


def _stable_constraint_seed(statement: str) -> object:
    from minicode.user_evidence import TrustedUserEvidenceSeed

    return TrustedUserEvidenceSeed.stable_constraint(statement)


def _structured_claim_types(manager: MemoryManager) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for memory_file in manager.memories.values():
        for entry in memory_file.entries:
            structured = entry.metadata.get("structured_reflection", {})
            for claim in structured.get("claims", []):
                claims.append((str(claim.get("claim_type")), entry.approval_status))
    return claims


def test_task_ledger_projects_only_explicit_long_lived_constraints() -> None:
    durable_english = (
        "Project-wide constraint: the public parse API must remain stable across "
        "releases."
    )
    durable_chinese = "项目长期约束：所有版本都不得更改 Memory receipt schema。"
    ledger = TaskLedger.from_messages(
        [
            {"role": "system", "content": "SYSTEM"},
            {
                "role": "user",
                "content": (
                    f"Refactor the parser.\n{durable_english}\n{durable_chinese}\n"
                    "For this turn, you must only inspect files.\n"
                    "For the current task, do not edit production code.\n"
                    "You must run pytest before finalizing.\n"
                    "当前任务必须只跑定向测试。"
                ),
            },
        ]
    )

    assert ledger is not None
    assert tuple(ledger.durable_constraints) == (
        durable_english,
        durable_chinese,
    )
    assert "You must run pytest before finalizing." in ledger.constraints


@pytest.mark.parametrize(
    "statement",
    [
        "This turn must only inspect files.",
        "For the current task, do not edit production code.",
        "You must run pytest before finalizing.",
        "当前任务必须只跑定向测试。",
        "The parser must return a dictionary.",
    ],
)
def test_task_ledger_does_not_promote_turn_local_or_bare_must_to_durable(
    statement: str,
) -> None:
    ledger = TaskLedger.from_messages([{"role": "user", "content": statement}])

    assert ledger is not None
    assert tuple(ledger.durable_constraints) == ()


def test_real_agent_turn_emits_trusted_stable_constraint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    statement = (
        "Project-wide constraint: the public parse API must remain stable across "
        "releases."
    )
    captured: list[dict[str, Any]] = []

    def capture_reflection(self: object, **kwargs: Any) -> None:
        del self
        captured.append(dict(kwargs))

    monkeypatch.setattr(
        CyberneticOrchestrator,
        "reflect_on_task",
        capture_reflection,
    )

    from minicode.agent_loop import run_agent_turn

    run_agent_turn(
        model=_FinalModel(
            "I choose to keep the public parse API stable because compatibility "
            "matters."
        ),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": f"Refactor the parser. {statement}"},
        ],
        cwd=str(workspace),
        memory_manager=MemoryManager(project_root=workspace),
        runtime={"reflectionSynthesizerMode": "rule"},
        trusted_user_evidence=(_stable_constraint_seed(statement),),
        max_steps=2,
    )

    assert len(captured) == 1
    events = captured[0]["execution_trace"]
    user_events = [event for event in events if event.get("type") == "user_constraint"]
    assert len(user_events) == 1
    assert user_events[0]["content"] == statement


def test_assistant_decision_never_extends_the_user_constraint_statement() -> None:
    statement = (
        "For this project, the public parse API must remain stable across releases."
    )
    result = ReflectionEngine().reflect(
        "Refactor the parser",
        [
            {"event_id": "event-1", "type": "user_constraint", "content": statement},
            {
                "event_id": "event-2",
                "type": "assistant_step",
                "content": (
                    "I choose to keep the public parse API stable because "
                    "compatibility matters."
                ),
            },
            {"event_id": "event-3", "type": "task_result", "status": "success"},
        ],
    )

    assert result.task_evidence is not None
    user_decisions = [
        decision
        for decision in result.task_evidence.decisions
        if decision.source_kind == "user_constraint"
    ]
    assert len(user_decisions) == 1
    assert user_decisions[0].statement == statement


def test_adjacent_correct_feedback_binds_next_message_to_canonical_runtime(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="Change parse() to return a tuple.",
        session_id=None,
        turn_id=_TURN_1,
    )
    service.record_feedback(_TURN_1, "correct")
    correction = "That tuple change was wrong; restore the dictionary return."

    second = service.turn(
        message=correction,
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    seeds = factory.calls[1]["trusted_user_evidence"]
    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.schema_version == 1
    assert seed.kind == "followup_correction"
    assert seed.origin == "explicit_correction_followup"
    assert seed.statement == correction
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", seed.statement_digest)
    assert seed.current_turn_id == _TURN_2
    assert seed.current_run_id == second.run_id
    assert seed.prior_turn_id == _TURN_1
    assert seed.prior_run_id == first.run_id

    service.turn(
        message="That was wrong; use a mapping instead.",
        session_id=first.session_id,
        turn_id=_TURN_3,
    )
    assert factory.calls[2].get("trusted_user_evidence", ()) == ()


@pytest.mark.parametrize("signal", [None, "accept", "reject"])
def test_non_correction_feedback_never_binds_a_followup(
    conversation_storage: tuple[Path, Path],
    signal: str | None,
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="First request",
        session_id=None,
        turn_id=_TURN_1,
    )
    if signal is not None:
        service.record_feedback(_TURN_1, signal)

    service.turn(
        message="That was wrong; use a dictionary return instead.",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert factory.calls[1].get("trusted_user_evidence", ()) == ()


def test_first_turn_has_no_followup_correction_seed(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)

    service.turn(
        message="No, use a dictionary instead of a tuple.",
        session_id=None,
        turn_id=_TURN_1,
    )

    assert factory.calls[0].get("trusted_user_evidence", ()) == ()


@pytest.mark.parametrize(
    "followup",
    [
        "Please inspect auth.py and summarize it.",
        "Should I run the tests now?",
        "What is not covered by README?",
        "Actually, can you explain the architecture?",
        "Use pytest to run the tests.",
        "Change the README title.",
        "Return a list of open TODOs.",
        "Keep going with the next module.",
        "Set up pre-commit hooks.",
        "The API should use JSON.",
        "请使用pytest跑测试。",
        "缓存应该是项目级的。",
        "Actually, explain the architecture.",
        "Actually, start a new task.",
        "Actually, thanks for the answer.",
        "Actually I have another question.",
        "其实，解释一下架构。",
        "实际上，开始下一个任务。",
        "实际上，这个回答是对的。",
        "That should be all, thanks.",
        "This should be a separate task.",
        "It should be sunny tomorrow.",
        "I use pytest instead of unittest in a new project.",
        "The README is concise, not verbose.",
    ],
)
def test_correct_feedback_does_not_launder_an_ordinary_followup_into_correction(
    conversation_storage: tuple[Path, Path],
    followup: str,
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="Review the parser implementation.",
        session_id=None,
        turn_id=_TURN_1,
    )
    service.record_feedback(_TURN_1, "correct")

    service.turn(
        message=followup,
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert factory.calls[1].get("trusted_user_evidence", ()) == ()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Correction: use a dictionary return.", True),
        ("That tuple change was wrong; restore the dictionary return.", True),
        ("Correction: use a dictionary instead of a tuple.", True),
        ("Correction: the cache is per project, not global.", True),
        ("纠正：缓存是项目级的。", True),
        ("纠正：不是全局缓存，而是项目级缓存。", True),
        ("Use pytest to run the tests.", False),
        ("Change the README title.", False),
        ("Return a list of open TODOs.", False),
        ("Keep going with the next module.", False),
        ("Set up pre-commit hooks.", False),
        ("The API should use JSON.", False),
        ("请使用pytest跑测试。", False),
        ("缓存应该是项目级的。", False),
        ("Actually, explain the architecture.", False),
        ("Actually, start a new task.", False),
        ("Actually, thanks for the answer.", False),
        ("Actually I have another question.", False),
        ("其实，解释一下架构。", False),
        ("实际上，开始下一个任务。", False),
        ("实际上，这个回答是对的。", False),
        ("That should be all, thanks.", False),
        ("This should be a separate task.", False),
        ("It should be sunny tomorrow.", False),
        ("I use pytest instead of unittest in a new project.", False),
        ("The README is concise, not verbose.", False),
    ],
)
def test_correction_grammar_requires_explicit_revision_evidence(
    statement: str,
    expected: bool,
) -> None:
    assert looks_like_correction(statement) is expected


def test_non_adjacent_correct_feedback_does_not_bind_a_later_message(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="First request",
        session_id=None,
        turn_id=_TURN_1,
    )
    service.turn(
        message="Second request",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )
    service.record_feedback(_TURN_1, "correct")

    service.turn(
        message="That earlier answer was wrong; use a dictionary instead.",
        session_id=first.session_id,
        turn_id=_TURN_3,
    )

    assert factory.calls[2].get("trusted_user_evidence", ()) == ()


def test_correction_join_rejects_a_journal_record_with_the_wrong_run_id(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    first_service = _service(workspace, data_dir, _CapturingRuntimeFactory())
    first = first_service.turn(
        message="Review the parser implementation.",
        session_id=None,
        turn_id=_TURN_1,
    )
    first_service.record_feedback(_TURN_1, "correct")
    inner = RunJournal(workspace, data_dir=data_dir)

    class WrongIdentityJournal:
        def __getattr__(self, name: str) -> object:
            return getattr(inner, name)

        def get_run(self, run_id: str) -> object:
            record = inner.get_run(run_id)
            if run_id != first.run_id or record is None:
                return record
            return SimpleNamespace(
                id="run_" + "f" * 32,
                status=record.status,
                source=record.source,
                session_id=record.session_id,
            )

    factory = _CapturingRuntimeFactory()
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda _resolved: WrongIdentityJournal(),  # type: ignore[arg-type]
    )

    service.turn(
        message="Correction: use a dictionary return.",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert factory.calls[0].get("trusted_user_evidence", ()) == ()


@pytest.mark.parametrize(
    ("length", "expected_count"),
    [(500, 1), (501, 0)],
)
def test_correction_followup_has_a_closed_500_character_boundary(
    conversation_storage: tuple[Path, Path],
    length: int,
    expected_count: int,
) -> None:
    workspace, data_dir = conversation_storage
    factory = _CapturingRuntimeFactory()
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="First request",
        session_id=None,
        turn_id=_TURN_1,
    )
    service.record_feedback(_TURN_1, "correct")
    followup = "纠正：" + "错" * (length - 3)

    service.turn(
        message=followup,
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    seeds = factory.calls[1].get("trusted_user_evidence", ())
    assert len(seeds) == expected_count
    if expected_count:
        assert seeds[0].statement == followup


def test_real_agent_turn_persists_trusted_constraint_as_approved_memory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    statement = (
        "Project-wide constraint: the public parse API must remain stable across "
        "releases."
    )

    from minicode.agent_loop import run_agent_turn

    run_agent_turn(
        model=_FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": f"Refactor the parser. {statement}"},
        ],
        cwd=str(workspace),
        memory_manager=manager,
        runtime={"reflectionSynthesizerMode": "rule"},
        trusted_user_evidence=(_stable_constraint_seed(statement),),
        max_steps=2,
    )

    assert ("constraint", "approved") in _structured_claim_types(manager)


def test_secret_bearing_trusted_constraint_is_never_auto_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    secret = "provider-secret-123"
    statement = (
        "Project-wide constraint: API_KEY="
        f"{secret} must always be used for parser checks."
    )
    captured_traces: list[list[dict[str, Any]]] = []
    original_reflect = ReflectionEngine.reflect

    def capture_then_reflect(
        engine: ReflectionEngine,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ):
        captured_traces.append([dict(event) for event in execution_trace])
        return original_reflect(
            engine,
            task_description,
            execution_trace,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(ReflectionEngine, "reflect", capture_then_reflect)

    from minicode.agent_loop import run_agent_turn

    run_agent_turn(
        model=_FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": f"Refactor the parser. {statement}"},
        ],
        cwd=str(workspace),
        memory_manager=manager,
        runtime={"reflectionSynthesizerMode": "rule"},
        trusted_user_evidence=(_stable_constraint_seed(statement),),
        max_steps=2,
    )

    assert captured_traces
    assert secret not in str(captured_traces)
    assert ("constraint", "approved") not in _structured_claim_types(manager)
    assert secret not in str(manager.memories)


def test_trusted_seed_batch_revalidates_exact_type_content_and_duplicates() -> None:
    statement = (
        "Project-wide constraint: the public parse API must remain stable."
    )
    valid = TrustedUserEvidenceSeed.stable_constraint(statement)
    mutated = TrustedUserEvidenceSeed.stable_constraint(statement)
    object.__setattr__(mutated, "statement", statement + " forged")

    class ForgedSeed(TrustedUserEvidenceSeed):
        def to_trace_event(self) -> dict[str, object]:
            return {
                "type": "user_correction",
                "content": "forged model text",
            }

    forged = ForgedSeed(
        kind=valid.kind,
        statement=valid.statement,
        origin=valid.origin,
        statement_digest=valid.statement_digest,
    )

    assert normalize_trusted_user_evidence((valid,), agent_depth=0) == (valid,)
    assert normalize_trusted_user_evidence((mutated,), agent_depth=0) == ()
    assert normalize_trusted_user_evidence((forged,), agent_depth=0) == ()
    assert normalize_trusted_user_evidence((valid, valid), agent_depth=0) == ()
    assert normalize_trusted_user_evidence((valid,), agent_depth=1) == ()


def test_real_loop_rejects_correction_seed_bound_to_another_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: list[list[dict[str, Any]]] = []

    def capture_reflection(self: object, **kwargs: Any) -> None:
        del self
        captured.append(list(kwargs["execution_trace"]))

    monkeypatch.setattr(CyberneticOrchestrator, "reflect_on_task", capture_reflection)
    seed = TrustedUserEvidenceSeed.followup_correction(
        "Correction: use a dictionary instead.",
        current_turn_id=_TURN_2,
        current_run_id="run_" + "2" * 32,
        prior_turn_id=_TURN_1,
        prior_run_id="run_" + "1" * 32,
    )

    from minicode.agent_loop import run_agent_turn

    run_agent_turn(
        model=_FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {
                "role": "user",
                "content": seed.statement,
                "_conversation_turn_id": _TURN_2,
            },
        ],
        cwd=str(workspace),
        memory_manager=MemoryManager(project_root=workspace),
        runtime={"reflectionSynthesizerMode": "rule"},
        trusted_user_evidence=(seed,),
        max_steps=2,
    )

    assert captured
    assert all(
        event.get("type") != "user_correction"
        for event in captured[0]
    )


def test_real_loop_rejects_valid_seed_whose_text_is_not_in_current_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: list[list[dict[str, Any]]] = []

    def capture_reflection(self: object, **kwargs: Any) -> None:
        del self
        captured.append(list(kwargs["execution_trace"]))

    monkeypatch.setattr(CyberneticOrchestrator, "reflect_on_task", capture_reflection)
    stable = TrustedUserEvidenceSeed.stable_constraint(
        "Project-wide constraint: the public API must never change."
    )
    run_id = "run_" + "2" * 32
    correction = TrustedUserEvidenceSeed.followup_correction(
        "Correction: delete the public API instead.",
        current_turn_id=_TURN_2,
        current_run_id=run_id,
        prior_turn_id=_TURN_1,
        prior_run_id="run_" + "1" * 32,
    )

    from minicode.agent_loop import run_agent_turn

    run_agent_turn(
        model=_FinalModel(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "Explain README."}],
        cwd=str(workspace),
        memory_manager=MemoryManager(project_root=workspace),
        runtime={"reflectionSynthesizerMode": "rule"},
        trusted_user_evidence=(stable,),
        max_steps=2,
    )
    run_agent_turn(
        model=_FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {
                "role": "user",
                "content": "Explain README.",
                "_conversation_turn_id": _TURN_2,
            }
        ],
        cwd=str(workspace),
        memory_manager=MemoryManager(project_root=workspace),
        runtime={"reflectionSynthesizerMode": "rule"},
        event_sink=SimpleNamespace(run_id=run_id),
        trusted_user_evidence=(correction,),
        max_steps=2,
    )

    assert len(captured) == 2
    assert all(
        event.get("type") not in {"user_constraint", "user_correction"}
        for trace in captured
        for event in trace
    )


def test_conversation_correction_reaches_real_agent_and_approved_memory(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    manager = MemoryManager(project_root=workspace)
    factory = _RealRuntimeFactory(manager)
    service = _service(workspace, data_dir, factory)
    first = service.turn(
        message="Change parse() to return a tuple.",
        session_id=None,
        turn_id=_TURN_1,
    )
    service.record_feedback(_TURN_1, "correct")

    second = service.turn(
        message="That tuple change was wrong; restore the dictionary return.",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert ("correction", "approved") in _structured_claim_types(manager)
    journal = RunJournal(workspace, data_dir=data_dir)
    written = journal.get_written_memory_versions(second.run_id)
    assert written
    provenance = journal.get_memory_provenance(second.run_id, "written")
    assert provenance.coverage == "complete"
    assert provenance.materializations == written
    assert len(provenance.records) == 1
    assert provenance.records[0].batch.authority_versions == written
    memory_bytes = (manager.paths.project_memory / "memory.json").read_bytes()

    replay = service.turn(
        message="That tuple change was wrong; restore the dictionary return.",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert replay == second
    assert len(factory.calls) == 2
    assert (manager.paths.project_memory / "memory.json").read_bytes() == memory_bytes
    assert journal.get_memory_provenance(second.run_id, "written") == provenance


def test_session_conflict_never_persists_uncommitted_approved_correction(
    conversation_storage: tuple[Path, Path],
) -> None:
    workspace, data_dir = conversation_storage
    first_service = _service(workspace, data_dir, _CapturingRuntimeFactory())
    first = first_service.turn(
        message="Change parse() to return a tuple.",
        session_id=None,
        turn_id=_TURN_1,
    )
    first_service.record_feedback(_TURN_1, "correct")
    manager = MemoryManager(project_root=workspace)
    factory = _RealRuntimeFactory(manager)

    def stale_save(_session: object) -> None:
        raise SessionWriteConflictError("stale session")

    stale_service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        session_saver=stale_save,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    with pytest.raises(ConversationSessionConflict):
        stale_service.turn(
            message="Correction: restore the dictionary return.",
            session_id=first.session_id,
            turn_id=_TURN_2,
        )

    assert len(factory.calls[0]["trusted_user_evidence"]) == 1
    reloaded = MemoryManager(project_root=workspace)
    assert ("correction", "approved") not in _structured_claim_types(reloaded)
    assert stale_service.status(_TURN_2).status == "failed"


@pytest.mark.parametrize("fault", ["initialize", "raise_write", "returned_failure"])
def test_post_commit_memory_failure_is_durable_and_does_not_fail_the_turn(
    conversation_storage: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from minicode.memory_pipeline import MemoryPipeline
    from minicode.memory_write_contract import MemoryWriteResult

    workspace, data_dir = conversation_storage
    first_service = _service(workspace, data_dir, _CapturingRuntimeFactory())
    first = first_service.turn(
        message="Change parse() to return a tuple.",
        session_id=None,
        turn_id=_TURN_1,
    )
    first_service.record_feedback(_TURN_1, "correct")
    manager = MemoryManager(project_root=workspace)

    class PostCommitOnlyRuntime(AgentTurnRuntime):
        def execute(
            self,
            messages: list[dict[str, Any]],
            _observation: object,
            **_kwargs: object,
        ) -> list[dict[str, Any]]:
            return [*messages, {"role": "assistant", "content": "safe reply"}]

    def runtime_factory(
        *,
        trusted_user_evidence: tuple[object, ...] = (),
        **_kwargs: object,
    ) -> PostCommitOnlyRuntime:
        return PostCommitOnlyRuntime(
            workspace=workspace,
            runtime={"reflectionSynthesizerMode": "rule"},
            tools=ToolRegistry([]),
            permissions=_Permissions(),
            memory_manager=manager,
            model=_FinalModel(),
            skill_routing=None,
            system_prompt="SYSTEM",
            trusted_user_evidence=trusted_user_evidence,
        )

    if fault == "initialize":
        monkeypatch.setattr(
            MemoryPipeline,
            "initialize",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("provider-secret")
            ),
        )
    elif fault == "raise_write":
        monkeypatch.setattr(
            MemoryPipeline,
            "write_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("provider-secret")
            ),
        )
    else:
        monkeypatch.setattr(
            MemoryPipeline,
            "write_result",
            lambda _pipeline, _task, _trace, *, operation_id, **_kwargs: (
                MemoryWriteResult(
                    operation_id=operation_id,
                    payload_digest="0" * 64,
                    reason_codes=("reflection_write_failed",),
                )
            ),
        )

    service = ConversationTurnService(
        workspace,
        runtime_factory=runtime_factory,  # type: ignore[arg-type]
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    result = service.turn(
        message="Correction: restore the dictionary return.",
        session_id=first.session_id,
        turn_id=_TURN_2,
    )

    assert service.status(_TURN_2).status == "completed"
    reloaded = MemoryManager(project_root=workspace)
    assert all(not memory.entries for memory in reloaded.memories.values())
    journal = RunJournal(workspace, data_dir=data_dir)
    events = journal.list_events(result.run_id, limit=100).items
    failures = [event for event in events if event.type == "memory.persistence.failed"]
    assert len(failures) == 1
    payload = failures[0].payload
    assert set(payload) == {
        "schemaVersion",
        "phase",
        "operationId",
        "reasonCodes",
    }
    assert payload["phase"] == "committed_user_evidence"
    assert re.fullmatch(r"memoryop_[0-9a-f]{32}", payload["operationId"])
    assert payload["reasonCodes"] == [
        "reflection_write_failed" if fault == "returned_failure" else "pipeline_exception"
    ]
    assert "provider-secret" not in str(payload)


def test_canonical_conversation_runtime_persists_direct_constraint(
    conversation_storage: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, data_dir = conversation_storage
    monkeypatch.setattr(
        "minicode.config.load_runtime_config",
        lambda _workspace: {
            "model": _FinalModel.model_id,
            "reflectionSynthesizerMode": "rule",
        },
    )
    monkeypatch.setattr(
        "minicode.tools.create_default_tool_registry",
        lambda *_args, **_kwargs: ToolRegistry([]),
    )
    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        lambda **_kwargs: _FinalModel(),
    )
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "minicode.skill_candidate_reranker.build_skill_candidate_reranker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "minicode.prompt.build_system_prompt",
        lambda *_args, **_kwargs: "SYSTEM",
    )
    service = _service(workspace, data_dir, create_agent_turn_runtime)

    service.turn(
        message=(
            "Refactor the parser. Project-wide constraint: the public parse API "
            "must remain stable across releases."
        ),
        session_id=None,
        turn_id=_TURN_1,
    )

    reloaded = MemoryManager(project_root=workspace)
    assert ("constraint", "approved") in _structured_claim_types(reloaded)
    assert all(
        entry.scope is MemoryScope.PROJECT
        for memory_file in reloaded.memories.values()
        for entry in memory_file.entries
    )
