"""Minimal smoke test for CyberneticOrchestrator — verifies all 15 controllers init."""
from __future__ import annotations

from unittest.mock import MagicMock

from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory_hybrid import HybridActivation
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.progress_controller import ProgressAction, ProgressDecision
from minicode.reflection_llm import StructuredClientFactoryResult


class TestOrchestratorInit:
    def test_initialize_all_controllers(self):
        mock_model = MagicMock()
        mock_model.model_id = "test-model"
        mock_tools = MagicMock()
        orch = CyberneticOrchestrator()
        orch.initialize(mock_model, mock_tools)
        assert orch._initialized
        assert orch.feedback is not None
        assert orch.stability is not None
        assert orch.adaptive_tuner is not None
        assert orch.state_observer is not None
        assert orch.decoupling is not None
        assert orch.predictive is not None
        assert orch.progress is not None
        assert orch.cost_control is not None
        assert orch.memory_ctrl is not None
        assert orch.model_ctrl is not None
        assert orch.smart_router is not None
        assert orch.model_switcher is not None

    def test_router_feedback_is_scoped_to_the_workspace(self, tmp_path):
        orch = CyberneticOrchestrator()
        orch._workspace = str(tmp_path)

        orch.initialize(
            MagicMock(model_id="test-model"),
            MagicMock(),
        )

        assert orch.smart_router.learner._storage == (
            tmp_path / ".mini-code" / "router_feedback.json"
        )

    def test_wire_healing(self):
        orch = CyberneticOrchestrator()
        orch.healing = None
        orch.wire_healing(tool_scheduler=MagicMock(), compactor=None)
        assert orch.healing is not None

    def test_rule_mode_does_not_create_reflection_model_client(self, monkeypatch):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("rule mode must not create an LLM client")

        monkeypatch.setattr(
            "minicode.reflection_llm.create_structured_generation_client",
            fail_if_called,
        )
        orch = CyberneticOrchestrator()

        orch.initialize(
            MagicMock(model_id="test-model"), MagicMock(), {"reflectionSynthesizerMode": "rule"}
        )

        assert orch.reflection._llm_config.mode == "rule"
        assert orch.reflection._llm_synthesizer is None

    def test_shadow_mode_uses_isolated_reflection_client(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(
            "minicode.reflection_llm.create_structured_generation_client",
            lambda runtime, config: StructuredClientFactoryResult(
                client=client,
                unavailable_reason=None,
                is_remote=False,
            ),
        )
        orch = CyberneticOrchestrator()

        orch.initialize(
            MagicMock(model_id="test-model"),
            MagicMock(),
            {
                "reflectionSynthesizerMode": "llm_shadow",
                "reflectionModel": "local-reflection",
            },
        )

        assert orch.reflection._llm_config.mode == "llm_shadow"
        assert orch.reflection._llm_synthesizer is not None

    def test_shadow_metrics_recorder_requires_both_shadow_mode_and_opt_in(
        self, monkeypatch, tmp_path
    ):
        client = MagicMock()
        monkeypatch.setattr(
            "minicode.reflection_llm.create_structured_generation_client",
            lambda runtime, config: StructuredClientFactoryResult(
                client=client,
                unavailable_reason=None,
                is_remote=False,
            ),
        )
        metrics_path = tmp_path / "shadow.jsonl"
        orch = CyberneticOrchestrator()

        orch.initialize(
            MagicMock(model_id="test-model"),
            MagicMock(),
            {
                "reflectionSynthesizerMode": "llm_shadow",
                "reflectionModel": "local-reflection",
                "reflectionShadowMetricsEnabled": True,
                "reflectionShadowMetricsPath": str(metrics_path),
            },
        )

        assert orch.reflection._shadow_metrics_recorder is not None
        assert orch.reflection._shadow_metrics_recorder.path == metrics_path

    def test_production_llm_mode_never_constructs_shadow_metrics_recorder(
        self, monkeypatch, tmp_path
    ):
        client = MagicMock()
        monkeypatch.setattr(
            "minicode.reflection_llm.create_structured_generation_client",
            lambda runtime, config: StructuredClientFactoryResult(
                client=client,
                unavailable_reason=None,
                is_remote=False,
            ),
        )
        orch = CyberneticOrchestrator()

        orch.initialize(
            MagicMock(model_id="test-model"),
            MagicMock(),
            {
                "reflectionSynthesizerMode": "llm",
                "reflectionModel": "local-reflection",
                "reflectionShadowMetricsEnabled": True,
                "reflectionShadowMetricsPath": str(tmp_path / "shadow.jsonl"),
            },
        )

        assert orch.reflection._shadow_metrics_recorder is None

    def test_wire_memory_reuses_configured_reflection_engine(self, monkeypatch):
        pipeline = MagicMock()
        pipeline_type = MagicMock(return_value=pipeline)
        monkeypatch.setattr("minicode.memory_pipeline.MemoryPipeline", pipeline_type)
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock()

        orch.wire_memory(MagicMock())

        assert pipeline.initialize.call_args.kwargs["reflection_engine"] is orch.reflection

    def test_wire_memory_passes_evidence_gated_hybrid_runtime(self, monkeypatch):
        pipeline = MagicMock()
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock(model_id="deepseek-chat")
        orch._runtime = {
            "memoryHybridEnabled": True,
            "memoryHybridModelPath": "/models/e5",
            "memoryHybridEvidencePath": "/evidence/promotion.json",
            "memoryHybridVerifierModel": "deepseek-chat",
        }

        orch.wire_memory(MagicMock())

        kwargs = pipeline.initialize.call_args.kwargs
        assert kwargs["enable_vector"] is True
        assert kwargs["hybrid_model_path"] == "/models/e5"
        assert kwargs["hybrid_evidence_path"] == "/evidence/promotion.json"
        assert kwargs["model_adapter"] is orch._last_model

    def test_wire_memory_passes_remote_embedding_authorization(self, monkeypatch):
        pipeline = MagicMock()
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock(model_id="deepseek-chat")
        orch._runtime = {
            "memoryHybridEnabled": True,
            "memoryHybridEmbeddingProvider": "qwen",
            "allowRemoteMemoryEmbedding": True,
            "memoryHybridEvidencePath": "/evidence/qwen-promotion.json",
            "memoryHybridVerifierModel": "deepseek-chat",
        }

        orch.wire_memory(MagicMock())

        kwargs = pipeline.initialize.call_args.kwargs
        assert kwargs["hybrid_embedding_provider"] == "qwen"
        assert kwargs["allow_remote_memory_embedding"] is True
        assert kwargs["hybrid_model_path"] is None

    def test_wire_memory_can_use_dedicated_hybrid_verifier_model(self, monkeypatch):
        pipeline = MagicMock()
        verifier = MagicMock(model_id="deepseek-chat")
        factory = MagicMock(return_value=verifier)
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        monkeypatch.setattr(
            "minicode.model_registry.create_dedicated_model_adapter", factory
        )
        monkeypatch.setattr(
            "minicode.memory_hybrid.assess_hybrid_activation",
            lambda **_kwargs: HybridActivation(
                requested=True,
                active=True,
                reason="activated",
                evidence={"verifier": {"model_id": "deepseek-chat"}},
                embedding_provider="qwen",
            ),
        )
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock(model_id="deepseek-v4-pro")
        orch._runtime = {
            "model": "deepseek-v4-pro",
            "memoryHybridEnabled": True,
            "memoryHybridModelPath": "/models/e5",
            "memoryHybridEvidencePath": "/evidence/promotion.json",
            "memoryHybridVerifierModel": "deepseek-v4-pro",
        }

        orch.wire_memory(MagicMock())

        factory.assert_called_once()
        assert factory.call_args.args[0] == "deepseek-chat"
        assert pipeline.initialize.call_args.kwargs["model_adapter"] is verifier
        assert (
            pipeline.initialize.call_args.kwargs["hybrid_verifier_binding"]
            == "evidence_bound_override"
        )

    def test_evidence_bound_verifier_uses_canonical_promotion_profile(
        self, monkeypatch
    ):
        pipeline = MagicMock()
        verifier = MagicMock(model_id="deepseek-chat")
        factory = MagicMock(return_value=verifier)
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        monkeypatch.setattr(
            "minicode.model_registry.create_dedicated_model_adapter", factory
        )
        monkeypatch.setattr(
            "minicode.memory_hybrid.assess_hybrid_activation",
            lambda **_kwargs: HybridActivation(
                requested=True,
                active=True,
                reason="activated",
                evidence={"verifier": {"model_id": "deepseek-chat"}},
                embedding_provider="qwen",
            ),
        )
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock(model_id="deepseek-v4-pro")
        orch._runtime = {
            "model": "deepseek-v4-pro",
            "temperature": 0.8,
            "maxOutputTokens": 1200,
            "modelMaxRetries": 4,
            "modelTimeoutSeconds": 15,
            "memoryHybridEnabled": True,
            "memoryHybridEvidencePath": "/evidence/promotion.json",
            "memoryHybridVerifierModel": "deepseek-chat",
        }

        orch.wire_memory(MagicMock())

        verifier_runtime = factory.call_args.args[2]
        assert verifier_runtime["temperature"] == 0
        assert verifier_runtime["maxOutputTokens"] == 6000
        assert verifier_runtime["modelMaxRetries"] == 1
        assert verifier_runtime["modelTimeoutSeconds"] == 90

    def test_evidence_bound_deepseek_verifier_uses_its_own_transport(self, monkeypatch):
        pipeline = MagicMock()
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        monkeypatch.setattr(
            "minicode.memory_hybrid.assess_hybrid_activation",
            lambda **_kwargs: HybridActivation(
                requested=True,
                active=True,
                reason="activated",
                evidence={"verifier": {"model_id": "deepseek-chat"}},
                embedding_provider="qwen",
            ),
        )
        monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        orch._last_model = MagicMock(model_id="qwen3.6-flash")
        orch._runtime = {
            "model": "qwen3.6-flash",
            "provider": "custom",
            "customBaseUrl": "https://qwen.synthetic.invalid/v1",
            "customApiKey": "synthetic-qwen-key",
            "deepseekBaseUrl": "https://deepseek.synthetic.invalid/v1",
            "deepseekApiKey": "synthetic-deepseek-key",
            "memoryHybridEnabled": True,
            "memoryHybridVerifierModel": "deepseek-chat",
        }

        orch.wire_memory(MagicMock())

        verifier = pipeline.initialize.call_args.kwargs["model_adapter"]
        assert isinstance(verifier, OpenAIModelAdapter)
        assert verifier.runtime["model"] == "deepseek-chat"
        assert verifier.runtime["openaiBaseUrl"] == (
            "https://deepseek.synthetic.invalid/v1"
        )
        assert verifier.runtime["openaiApiKey"] == "synthetic-deepseek-key"
        assert verifier.runtime["temperature"] == 0
        assert verifier.runtime["maxOutputTokens"] == 6000
        assert verifier.runtime["modelMaxRetries"] == 1
        assert verifier.runtime["modelTimeoutSeconds"] == 90
        assert "qwen.synthetic.invalid" not in str(verifier.runtime)
        assert "synthetic-qwen-key" not in str(verifier.runtime)

    def test_same_model_name_does_not_allow_wrong_primary_transport(self, monkeypatch):
        pipeline = MagicMock()
        monkeypatch.setattr(
            "minicode.memory_pipeline.MemoryPipeline",
            MagicMock(return_value=pipeline),
        )
        monkeypatch.setattr(
            "minicode.memory_hybrid.assess_hybrid_activation",
            lambda **_kwargs: HybridActivation(
                requested=True,
                active=True,
                reason="activated",
                evidence={"verifier": {"model_id": "deepseek-chat"}},
                embedding_provider="qwen",
            ),
        )
        monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
        orch = CyberneticOrchestrator()
        orch.reflection = MagicMock()
        # The label matches the evidence, but the primary transport does not.
        orch._last_model = MagicMock(model_id="deepseek-chat")
        orch._runtime = {
            "model": "deepseek-chat",
            "provider": "custom",
            "customBaseUrl": "https://qwen.synthetic.invalid/v1",
            "customApiKey": "synthetic-qwen-key",
            "deepseekBaseUrl": "https://deepseek.synthetic.invalid/v1",
            "deepseekApiKey": "synthetic-deepseek-key",
            "memoryHybridEnabled": True,
            "memoryHybridVerifierModel": "deepseek-chat",
        }

        orch.wire_memory(MagicMock())

        verifier = pipeline.initialize.call_args.kwargs["model_adapter"]
        assert isinstance(verifier, OpenAIModelAdapter)
        assert verifier is not orch._last_model
        assert verifier.runtime["openaiBaseUrl"] == (
            "https://deepseek.synthetic.invalid/v1"
        )
        assert verifier.runtime["openaiApiKey"] == "synthetic-deepseek-key"

    def test_legacy_reflection_fallback_still_emits_trace_v2_events(self):
        orch = CyberneticOrchestrator()
        orch.memory_pipeline = MagicMock()

        orch.reflect_on_task("Legacy task", step=2, tool_error_count=1)

        trace = orch.memory_pipeline.write.call_args.args[1]
        assert [event["event_id"] for event in trace] == [
            "event-000001",
            "event-000002",
            "event-000003",
        ]
        assert all(event["trace_schema_version"] == 2 for event in trace)

    def test_memory_maintenance_runs_only_at_task_end(self):
        orch = CyberneticOrchestrator()
        orch.memory_pipeline = MagicMock()
        scheduler = MagicMock()

        orch.step_end(
            scheduler,
            context_manager=None,
            step=1,
            tool_error_count=0,
            saw_tool_result=False,
            max_steps=3,
        )

        orch.memory_pipeline.maintain.assert_not_called()

        orch.task_end()

        orch.memory_pipeline.maintain.assert_called_once_with()

    def test_step_end_projects_real_progress_measurements_into_summary(self):
        orch = CyberneticOrchestrator()
        orch.progress = MagicMock()
        orch.progress.decide.return_value = ProgressDecision(
            action=ProgressAction.CONTINUE,
            health_score=0.8,
            stall_score=0.1,
            reasons=["healthy"],
        )

        summary = orch.step_end(
            MagicMock(),
            context_manager=None,
            step=5,
            tool_error_count=2,
            saw_tool_result=True,
            max_steps=50,
            completed_step_count=3,
            failed_step_count=2,
            tool_call_count=7,
            step_made_progress=False,
            elapsed_seconds=4.25,
            tests_passed=None,
        )

        signal = orch.progress.decide.call_args.args[0]
        assert signal.total_steps == 5
        assert signal.completed_steps == 3
        assert signal.failed_steps == 2
        assert signal.tool_calls == 7
        assert signal.tool_errors == 2
        assert signal.output_changed is False
        assert signal.elapsed_seconds == 4.25
        assert summary["progress_decision"]["action"] == "continue"
