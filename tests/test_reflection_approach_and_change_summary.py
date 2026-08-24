"""Tests for the approach-claim learning path and recovery change summaries.

These cover the two ways durable lessons were previously lost:
- cleanly successful, verified tasks wrote nothing at all;
- recovery claims said which file was touched but not what to change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import (
    TraceEvidenceExtractor,
    append_trace_event,
)


def _success_trace() -> list[dict]:
    """read -> edit -> tests pass -> assistant explanation."""
    t: list[dict] = []
    append_trace_event(t, {"type": "tool_call", "step": 1, "call_id": "c1", "tool_name": "read_file",
                           "input": {"path": "src/utils.py"}, "files": ["src/utils.py"], "files_read": ["src/utils.py"]})
    append_trace_event(t, {"type": "tool_result", "step": 1, "call_id": "c1", "tool_name": "read_file",
                           "status": "success", "is_error": False, "output_summary": "return json.load(open(path))",
                           "files": ["src/utils.py"], "files_read": ["src/utils.py"]})
    append_trace_event(t, {"type": "tool_call", "step": 2, "call_id": "c2", "tool_name": "edit_file",
                           "input": {"path": "src/utils.py", "old_string": "return json.load(open(path))",
                                     "new_string": "with open(path) as f: return json.load(f)"},
                           "files": ["src/utils.py"], "files_changed": ["src/utils.py"]})
    append_trace_event(t, {"type": "tool_result", "step": 2, "call_id": "c2", "tool_name": "edit_file",
                           "status": "success", "is_error": False, "output_summary": "edited",
                           "files": ["src/utils.py"], "files_changed": ["src/utils.py"]})
    append_trace_event(t, {"type": "tool_call", "step": 3, "call_id": "c3", "tool_name": "run_command",
                           "input": {"command": "pytest tests/test_utils.py -q"}})
    append_trace_event(t, {"type": "tool_result", "step": 3, "call_id": "c3", "tool_name": "run_command",
                           "status": "success", "is_error": False, "output_summary": "3 passed in 0.42s"})
    append_trace_event(t, {"type": "assistant", "step": 3,
                           "text": "Fixed the file handle leak by using a context manager; all tests pass."})
    return t


def _recovered_trace() -> list[dict]:
    """fail -> edit -> same command passes (the production recovery shape)."""
    t: list[dict] = []
    append_trace_event(t, {"type": "tool_call", "step": 1, "call_id": "c1", "tool_name": "run_command",
                           "input": {"command": "pytest tests/ -q"}})
    append_trace_event(t, {"type": "tool_result", "step": 1, "call_id": "c1", "tool_name": "run_command",
                           "status": "error", "is_error": True,
                           "output_summary": "ImportError: cannot import name 'retry' from 'tenacity'"})
    append_trace_event(t, {"type": "error", "step": 1, "call_id": "c1", "tool_name": "run_command",
                           "error_type": "ImportError",
                           "message": "ImportError: cannot import name 'retry' from 'tenacity'"})
    append_trace_event(t, {"type": "tool_call", "step": 2, "call_id": "c2", "tool_name": "edit_file",
                           "input": {"path": "src/api_client.py", "old": "from tenacity import retry",
                                     "new": "from tenacity import retry, stop_after_attempt"},
                           "files": ["src/api_client.py"], "files_changed": ["src/api_client.py"]})
    append_trace_event(t, {"type": "tool_result", "step": 2, "call_id": "c2", "tool_name": "edit_file",
                           "status": "success", "is_error": False, "output_summary": "edited",
                           "files": ["src/api_client.py"], "files_changed": ["src/api_client.py"]})
    append_trace_event(t, {"type": "tool_call", "step": 3, "call_id": "c3", "tool_name": "run_command",
                           "input": {"command": "pytest tests/ -q"}})
    append_trace_event(t, {"type": "tool_result", "step": 3, "call_id": "c3", "tool_name": "run_command",
                           "status": "success", "is_error": False, "output_summary": "12 passed"})
    return t


class TestFinalSummaryExtraction:
    def test_last_assistant_event_becomes_final_summary(self) -> None:
        t = _success_trace()
        evidence = TraceEvidenceExtractor().extract("fix leak", t)
        assert "context manager" in evidence.final_summary
        assert len(evidence.final_summary_event_ids) == 1

    def test_no_assistant_events_leaves_summary_empty(self) -> None:
        t = _recovered_trace()
        evidence = TraceEvidenceExtractor().extract("fix import", t)
        assert evidence.final_summary == ""
        assert evidence.final_summary_event_ids == ()


class TestApproachClaim:
    def test_verified_success_produces_approach_claim(self) -> None:
        result = ReflectionEngine().reflect(
            "Fix the file handle leak in parse_config", _success_trace()
        )
        sr = result.to_memory_entry()["metadata"]["structured_reflection"]
        approaches = [c for c in sr["claims"] if c["claim_type"] == "approach"]
        assert len(approaches) == 1
        claim = approaches[0]
        # The agent's own causal explanation is embedded.
        assert "context manager" in claim["statement"]
        # Deterministic grounding facts are embedded.
        assert "src/utils.py" in claim["statement"]
        assert "3 passed in 0.42s" in claim["statement"]
        assert claim["epistemic_status"] == "confirmed"
        assert claim["applies_when"].strip()

    def test_read_only_success_writes_nothing(self) -> None:
        t: list[dict] = []
        append_trace_event(t, {"type": "tool_call", "step": 1, "call_id": "c1", "tool_name": "read_file",
                               "input": {"path": "a.py"}, "files": ["a.py"], "files_read": ["a.py"]})
        append_trace_event(t, {"type": "tool_result", "step": 1, "call_id": "c1", "tool_name": "read_file",
                               "status": "success", "is_error": False, "output_summary": "content"})
        append_trace_event(t, {"type": "assistant", "step": 1, "text": "I read the file."})
        result = ReflectionEngine().reflect("read the file", t)
        sr = result.to_memory_entry()["metadata"]["structured_reflection"]
        assert not any(c["claim_type"] == "approach" for c in sr["claims"])

    def test_unverified_success_writes_no_approach(self) -> None:
        t: list[dict] = []
        append_trace_event(t, {"type": "tool_call", "step": 1, "call_id": "c1", "tool_name": "edit_file",
                               "input": {"path": "a.py"}, "files": ["a.py"], "files_changed": ["a.py"]})
        append_trace_event(t, {"type": "tool_result", "step": 1, "call_id": "c1", "tool_name": "edit_file",
                               "status": "success", "is_error": False, "output_summary": "edited",
                               "files": ["a.py"], "files_changed": ["a.py"]})
        result = ReflectionEngine().reflect("edit", t)
        sr = result.to_memory_entry()["metadata"]["structured_reflection"]
        assert not any(c["claim_type"] == "approach" for c in sr["claims"])

    def test_pipeline_persist_approach_claim(self, tmp_path: Path) -> None:
        mgr = MemoryManager(project_root=tmp_path)
        pipeline = MemoryPipeline(mgr)
        pipeline.initialize(None, str(tmp_path))
        entry_id = pipeline.write("Fix the file handle leak in parse_config", _success_trace())
        assert entry_id is not None
        entry = next(
            e for e in mgr.memories[MemoryScope.PROJECT].entries if e.id == entry_id
        )
        claims = entry.metadata["structured_reflection"]["claims"]
        assert [claim["claim_type"] for claim in claims] == ["approach"]
        assert "context manager" in entry.content


class TestRecoveryChangeSummary:
    def test_recovery_claim_carries_edit_excerpt(self) -> None:
        result = ReflectionEngine().reflect("fix import", _recovered_trace())
        sr = result.to_memory_entry()["metadata"]["structured_reflection"]
        recoveries = [c for c in sr["claims"] if c["claim_type"] == "recovery"]
        assert len(recoveries) == 1
        statement = recoveries[0]["statement"]
        assert "'from tenacity import retry'" in statement
        assert "'from tenacity import retry, stop_after_attempt'" in statement
        assert "src/api_client.py" in statement

    def test_pipeline_persist_recovery_with_change(self, tmp_path: Path) -> None:
        mgr = MemoryManager(project_root=tmp_path)
        pipeline = MemoryPipeline(mgr)
        pipeline.initialize(None, str(tmp_path))
        entry_id = pipeline.write("fix import", _recovered_trace())
        assert entry_id is not None
        entry = next(
            e for e in mgr.memories[MemoryScope.PROJECT].entries if e.id == entry_id
        )
        claims = entry.metadata["structured_reflection"]["claims"]
        assert [claim["claim_type"] for claim in claims] == ["recovery"]
        assert "stop_after_attempt" in entry.content


class TestShadowModeDefault:
    def test_env_restores_rule_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from minicode.reflection_llm import ReflectionLLMConfig

        monkeypatch.setenv("MINI_CODE_REFLECTION_SYNTHESIZER_MODE", "rule")
        assert ReflectionLLMConfig.from_runtime({}).mode == "rule"

    def test_runtime_config_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from minicode.reflection_llm import ReflectionLLMConfig

        monkeypatch.setenv("MINI_CODE_REFLECTION_SYNTHESIZER_MODE", "rule")
        config = ReflectionLLMConfig.from_runtime(
            {"reflectionSynthesizerMode": "llm"}
        )
        assert config.mode == "llm"
