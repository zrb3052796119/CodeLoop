"""Measurement round: continuation markers, savings benchmark, A/B harness."""

from pathlib import Path


def test_observation_v5_marks_continuation():
    from minicode.subagent_observation import (
        normalize_subagent_payload,
        project_subagent_event,
    )

    payload = project_subagent_event(
        agent_type="explore",
        outcome="completed",
        model_turns=2,
        tool_calls=0,
        duration_ms=800,
        max_turns=12,
        result_truncated=False,
        subagent_id="sub_" + "1" * 32,
        result_contract_status="reported",
        continued_from_subagent_id="sub_" + "2" * 32,
    )
    assert payload is not None
    assert payload["subagentVersion"] == 5
    assert payload["continuedFromSubagentId"] == "sub_" + "2" * 32
    assert payload["resultPartial"] is False

    plain = project_subagent_event(
        agent_type="explore",
        outcome="completed",
        model_turns=2,
        tool_calls=1,
        duration_ms=800,
        max_turns=12,
        result_truncated=False,
        subagent_id="sub_" + "1" * 32,
        result_contract_status="reported",
    )
    assert plain["subagentVersion"] == 3
    assert "continuedFromSubagentId" not in plain

    bad = normalize_subagent_payload(
        {**payload, "continuedFromSubagentId": "not-an-id"}
    )
    assert bad is None


def test_journal_start_records_continuation(tmp_path):
    from minicode.subagent_journal import SubagentRunJournal

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    journal.start(
        subagent_id="sub_" + "1" * 32,
        agent_type="explore",
        max_turns=12,
        budget=None,
        continued_from="sub_" + "2" * 32,
    )
    content = (run_dir / "subagent-runs" / ("sub_" + "1" * 32 + ".jsonl")).read_text(
        encoding="utf-8"
    )
    assert "continuedFromSubagentId" in content
    assert "sub_" + "2" * 32 in content


def test_journal_start_without_continuation_has_no_marker(tmp_path):
    from minicode.subagent_journal import SubagentRunJournal

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    journal.start(
        subagent_id="sub_" + "1" * 32,
        agent_type="explore",
        max_turns=12,
        budget=None,
    )
    content = (run_dir / "subagent-runs" / ("sub_" + "1" * 32 + ".jsonl")).read_text(
        encoding="utf-8"
    )
    assert "continuedFromSubagentId" not in content


def test_journal_accepts_custom_profile_types(tmp_path):
    from minicode.subagent_journal import SubagentRunJournal

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    journal.start(
        subagent_id="sub_" + "1" * 32,
        agent_type="test-locator",
        max_turns=8,
        budget=None,
    )
    content = (run_dir / "subagent-runs" / ("sub_" + "1" * 32 + ".jsonl")).read_text(
        encoding="utf-8"
    )
    assert '"agentType": "test-locator"' in content or '"agentType":"test-locator"' in content


def test_journal_still_rejects_invalid_type_names(tmp_path):
    import pytest
    from minicode.subagent_journal import SubagentJournalError, SubagentRunJournal

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    with pytest.raises(SubagentJournalError):
        journal.start(
            subagent_id="sub_" + "1" * 32,
            agent_type="bad type!",
            max_turns=8,
            budget=None,
        )


# ---------------------------------------------------------------------------
# Task 2: deterministic continuation-savings benchmark
# ---------------------------------------------------------------------------


def test_continuation_savings_benchmark_produces_report(tmp_path):
    import importlib

    script = importlib.import_module("scripts.evaluate_continuation_savings")
    report = script.main(out_root=tmp_path)

    assert report["initial"]["tool_calls"] == 3
    assert report["initial"]["model_turns"] == 4
    assert report["continuation"]["tool_calls"] == 0
    assert report["continuation"]["model_turns"] == 1
    assert report["fresh"]["tool_calls"] == report["initial"]["tool_calls"]
    assert report["savingsRatio"]["tool_calls"] > 0.5
    assert (tmp_path / "report.json").exists()


def test_continuation_benchmark_marks_continuation_run(tmp_path):
    import importlib

    script = importlib.import_module("scripts.evaluate_continuation_savings")
    events: list[tuple[str, object]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    from minicode.subagent_sessions import SubagentSessionStore
    from minicode.tooling import ToolContext

    context = ToolContext(
        cwd=str(script.build_fixture_repo(tmp_path / "repo")),
        _runtime={"model": "fake"},
        _event_sink=RecordingSink(),
        _subagent_sessions=SubagentSessionStore(),
    )
    report = script.run_scenario(context)
    completed = [p for t, p in events if t == "subagent.completed"]
    versions = [p.get("subagentVersion") for p in completed]
    assert 5 in versions  # the continuation run carries the v5 marker
    assert report["continuation"]["is_continuation"] is True
