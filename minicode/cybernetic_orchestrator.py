"""Cybernetic Orchestrator — facade managing all engineering cybernetics controllers.

Extracts controller lifecycle management from agent_loop.py into a single
orchestration class. The agent loop calls high-level hook methods instead of
managing 15+ controller instances directly.

Architecture:
  agent_loop.py
    └── CyberneticOrchestrator  (this module)
          ├── FeedbackController       (dual-PID outer loop)
          ├── FeedforwardController    (preemptive config)
          ├── StabilityMonitor         (health tracking)
          ├── AdaptivePIDTuner         (self-tuning PID)
          ├── StateObserver            (Kalman filters)
          ├── DecouplingController     (multi-variable control)
          ├── PredictiveController     (proactive actions)
          ├── SelfHealingEngine        (fault recovery)
          ├── ContextCyberneticsOrchestrator  (7-layer context control)
          ├── CostControlLoop          (budget PID)
          ├── CyberneticSupervisor     (aggregation)
          ├── ProgressController       (stall detection)
          ├── MemoryInjectionController
          ├── ModelSelectionController
          ├── SmartRouter              (task → model)
          ├── ReflectionEngine         (post-task learning)
          ├── ModelSwitcher            (runtime hot-swap)
          └── MemoryInjector           (memory → prompt)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from minicode.adaptive_pid_tuner import AdaptivePIDTuner
from minicode.agent_intelligence import ToolScheduler
from minicode.context_compactor import ContextCompactor
from minicode.context_cybernetics import ContextCyberneticsOrchestrator
from minicode.cost_control import CostControlLoop
from minicode.cybernetic_supervisor import CyberneticSupervisor, save_supervisor_report
from minicode.decoupling_controller import DecouplingController
from minicode.feedback_controller import FeedbackController
from minicode.feedforward_controller import FeedforwardController
from minicode.logging_config import get_logger
from minicode.memory import MemoryManager
from minicode.memory_injector import (
    MemoryInjectionController,
)
from minicode.model_registry import ModelSelectionController, ModelSelectionSignal
from minicode.predictive_controller import PredictiveController
from minicode.progress_controller import ProgressAction, ProgressController, ProgressSignal
from minicode.self_healing_engine import SelfHealingEngine
from minicode.stability_monitor import MetricSnapshot, StabilityMonitor
from minicode.state_observer import MeasurementVector, StateObserver

logger = get_logger("cybernetic_orchestrator")


class CyberneticOrchestrator:
    """Central orchestrator for all engineering cybernetics controllers.

    Usage in agent_loop.py:

        orch = CyberneticOrchestrator()
        orch.initialize(model, tools, runtime)
        orch.wire_memory(memory_mgr)
        orch.wire_healing(tool_scheduler, context_compactor)

        for step in range(max_steps):
            orch.step_start(context_manager, step, tool_error_count)
            # ... model call, tool execution ...
            orch.step_end(tool_scheduler, context_manager, step, tool_error_count)
    """

    def __init__(self):
        # Feedback layer
        self.feedback: FeedbackController | None = None
        self.feedforward: FeedforwardController | None = None
        self.stability: StabilityMonitor | None = None

        # Advanced control
        self.adaptive_tuner: AdaptivePIDTuner | None = None
        self.state_observer: StateObserver | None = None
        self.decoupling: DecouplingController | None = None
        self.predictive: PredictiveController | None = None
        self.healing: SelfHealingEngine | None = None

        # Pipeline controllers
        self.progress: ProgressController | None = None
        self.cyber_supervisor: CyberneticSupervisor | None = None

        # Context + cost (set via wire_ methods)
        self.context_cybernetics: ContextCyberneticsOrchestrator | None = None
        self.cost_control: CostControlLoop | None = None
        self.context_compactor: ContextCompactor | None = None

        # Memory + routing (set via wire_ methods)
        self.memory_ctrl: MemoryInjectionController | None = None
        self.model_ctrl: ModelSelectionController | None = None
        self.memory_pipeline: Any = None  # MemoryPipeline (unified facade)
        self.smart_router = None
        self.model_switcher = None
        self.reflection = None
        self._last_model: Any | None = None
        self._workspace: str | None = None
        self._runtime: dict[str, Any] = {}

        self._initialized = False

    # ── INITIALIZATION ──────────────────────────────────────────────

    def initialize(
        self,
        model: Any,
        tools: Any,
        runtime: dict | None = None,
    ) -> None:
        """Initialize all controllers. Call once at task start."""
        self._last_model = model
        self._runtime = dict(runtime or {})
        feedback_root = Path(self._workspace) if self._workspace else Path.cwd()
        self._workspace = str(feedback_root)
        self.feedback = FeedbackController()
        self.cyber_supervisor = CyberneticSupervisor()
        self.stability = StabilityMonitor(window_size=100)
        self.adaptive_tuner = AdaptivePIDTuner()
        self.state_observer = StateObserver()
        self.decoupling = DecouplingController()
        self.predictive = PredictiveController()
        self.progress = ProgressController()
        self.cost_control = CostControlLoop()
        self.memory_ctrl = MemoryInjectionController()
        self.model_ctrl = ModelSelectionController()

        # Import-heavy modules (lazy to avoid circular imports)
        from minicode.agent_reflection import ReflectionEngine
        from minicode.model_switcher import ModelSwitcher
        from minicode.reflection_llm import (
            LLMReflectionSynthesizer,
            ReflectionLLMConfig,
            create_structured_generation_client,
        )
        from minicode.smart_router import SmartRouter

        # Keep learned routing outcomes durable but project-scoped so task text
        # and model performance cannot contaminate unrelated workspaces.
        self.smart_router = SmartRouter(
            feedback_path=feedback_root / ".mini-code" / "router_feedback.json",
        )
        reflection_config = ReflectionLLMConfig.from_runtime(runtime)
        reflection_synthesizer = None
        reflection_unavailable_reason = None
        shadow_metrics_recorder = None
        if reflection_config.mode != "rule":
            try:
                client_result = create_structured_generation_client(
                    runtime,
                    reflection_config,
                )
                reflection_unavailable_reason = client_result.unavailable_reason
                if client_result.client is not None:
                    reflection_synthesizer = LLMReflectionSynthesizer(
                        client_result.client,
                        reflection_config,
                    )
            except Exception:
                reflection_unavailable_reason = "reflection_client_initialization_failed"
                logger.warning(
                    "Reflection synthesizer unavailable; reason=%s",
                    reflection_unavailable_reason,
                )
        if (
            reflection_config.mode == "llm_shadow"
            and reflection_config.shadow_metrics_enabled
        ):
            try:
                from minicode.model_registry import build_provider_config
                from minicode.reflection_shadow_metrics import (
                    ReflectionShadowMetricsRecorder,
                )

                reflection_model = reflection_config.model or str(
                    (runtime or {}).get("model") or "unknown"
                )
                provider = build_provider_config(
                    reflection_model, runtime or {}
                ).provider.value
                metrics_path = reflection_config.shadow_metrics_path or str(
                    Path.home()
                    / ".mini-code"
                    / "metrics"
                    / "reflection-shadow.jsonl"
                )
                shadow_metrics_recorder = ReflectionShadowMetricsRecorder(
                    metrics_path,
                    model=reflection_model,
                    provider=provider,
                    max_records=reflection_config.shadow_max_records,
                    max_file_bytes=reflection_config.shadow_max_file_bytes,
                )
            except Exception:
                logger.warning("Reflection shadow metrics unavailable")
        self.reflection = ReflectionEngine(
            memory_manager=None,
            llm_config=reflection_config,
            llm_synthesizer=reflection_synthesizer,
            llm_unavailable_reason=reflection_unavailable_reason,
            shadow_metrics_recorder=shadow_metrics_recorder,
        )
        self.model_switcher = ModelSwitcher(
            current_model=getattr(model, 'model_id', ''),
            current_runtime=runtime or {},
            current_tools=tools,
        )
        self._initialized = True
        logger.info("CyberneticOrchestrator: %d controllers initialized", 15)

    def wire_memory(
        self,
        memory_mgr: MemoryManager,
        context_usage: float = 0.0,
    ) -> None:
        """Initialize unified memory pipeline."""
        from minicode.memory_pipeline import MemoryPipeline

        self.memory_pipeline = MemoryPipeline(memory_mgr)
        model_for_pipeline = getattr(self, '_last_model', None)
        runtime = dict(getattr(self, "_runtime", {}) or {})
        hybrid_enabled = bool(runtime.get("memoryHybridEnabled", False))
        verifier_model = str(runtime.get("memoryHybridVerifierModel") or "").strip()
        current_model = str(getattr(model_for_pipeline, "model_id", "") or "").strip()
        if hybrid_enabled and verifier_model and verifier_model != current_model:
            try:
                from minicode.model_registry import create_model_adapter

                verifier_runtime = dict(runtime, model=verifier_model)
                model_for_pipeline = create_model_adapter(
                    verifier_model,
                    None,
                    verifier_runtime,
                )
            except Exception:
                model_for_pipeline = None
                logger.warning(
                    "Hybrid memory verifier model initialization failed; "
                    "canonical lexical retrieval remains active"
                )
        self.memory_pipeline.initialize(
            model_adapter=model_for_pipeline,
            workspace_path=getattr(self, '_workspace', None),
            reflection_engine=self.reflection,
            enable_vector=hybrid_enabled,
            hybrid_model_path=runtime.get("memoryHybridModelPath") or None,
            hybrid_evidence_path=runtime.get("memoryHybridEvidencePath") or None,
            hybrid_embedding_provider=str(
                runtime.get("memoryHybridEmbeddingProvider") or "local-e5"
            ),
            allow_remote_memory_embedding=bool(
                runtime.get("allowRemoteMemoryEmbedding", False)
            ),
        )

    def wire_healing(
        self,
        tool_scheduler: ToolScheduler,
        compactor: ContextCompactor | None = None,
    ) -> None:
        """Initialize SelfHealingEngine with system references."""
        self.healing = SelfHealingEngine(
            orchestrator=self.context_cybernetics,
            tool_scheduler=tool_scheduler,
            compactor=compactor,
        )

    # ── STEP HOOKS ──────────────────────────────────────────────────

    def step_start(
        self,
        context_manager: Any | None,
        step: int,
        tool_error_count: int,
        saw_tool_result: bool,
        *,
        response_time_seconds: float | None = None,
        recent_tool_error_count: int | None = None,
        recent_tool_call_count: int | None = None,
    ) -> None:
        """Called at the start of each step (before model call)."""
        if not self._initialized:
            return
        recent_errors = max(
            0,
            recent_tool_error_count
            if recent_tool_error_count is not None
            else tool_error_count,
        )
        recent_calls = max(
            0,
            recent_tool_call_count
            if recent_tool_call_count is not None
            else step - 1,
        )
        recent_success_rate = max(
            0.0,
            min(1.0, 1.0 - recent_errors / max(recent_calls, 1)),
        )

        # StateObserver: Kalman estimation
        if self.state_observer:
            measurement = MeasurementVector(
                timestamp=time.time(),
                response_time=max(0.0, response_time_seconds or 0.0),
                success_rate=recent_success_rate,
                context_length=(
                    context_manager.get_stats().total_tokens if context_manager else 0
                ),
                error_count=recent_errors,
                retry_count=recent_errors,
                tool_calls=recent_calls,
            )
            observed = self.state_observer.update(measurement)
            if observed.confidence > 0.4 and observed.system_degradation > 0.4:
                logger.warning(
                    "StateObserver: degradation=%.2f confidence=%.2f",
                    observed.system_degradation, observed.confidence,
                )

        # PredictiveController: proactive actions
        if self.predictive:
            if context_manager:
                stats = context_manager.get_stats()
                self.predictive.update("context_usage", stats.usage_percentage / 100.0)
            self.predictive.update(
                "error_rate", recent_errors / max(recent_calls, 1)
            )
            if step > 2:
                actions = self.predictive.generate_predictive_actions()
                if actions and actions[0].urgency > 0.7:
                    action = actions[0]
                    if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
                        logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)

    def step_end(
        self,
        tool_scheduler: ToolScheduler,
        context_manager: Any | None,
        step: int,
        tool_error_count: int,
        saw_tool_result: bool,
        max_steps: int,
        *,
        completed_step_count: int | None = None,
        failed_step_count: int | None = None,
        tool_call_count: int | None = None,
        step_made_progress: bool | None = None,
        elapsed_seconds: float | None = None,
        tests_passed: bool | None = None,
    ) -> dict[str, Any]:
        """Called at end of step (finally block). Returns a summary dict."""
        summary: dict[str, Any] = {}
        actual_tool_calls = max(
            0,
            tool_call_count if tool_call_count is not None else step,
        )
        actual_completed_steps = max(
            0,
            completed_step_count
            if completed_step_count is not None
            else step - tool_error_count,
        )
        actual_failed_steps = max(
            0,
            failed_step_count
            if failed_step_count is not None
            else min(step, tool_error_count),
        )
        actual_elapsed = max(0.0, elapsed_seconds or 0.0)
        actual_progress = (
            step_made_progress
            if step_made_progress is not None
            else saw_tool_result
        )
        error_rate = float(tool_error_count) / max(actual_tool_calls, 1)

        # Feedback pattern recording
        if self.feedback:
            pattern_id = f"step_{step}"
            self.feedback.record_pattern_effectiveness(
                pattern_id, tool_error_count == 0
            )

        # StabilityMonitor
        if self.stability:
            snapshot = MetricSnapshot(
                timestamp=time.time(),
                error_rate=error_rate,
                avg_latency=actual_elapsed / max(step, 1),
                context_usage=(
                    context_manager.get_stats().usage_percentage
                    if context_manager else 0.0
                ),
                active_tasks=1,
            )
            self.stability.record_snapshot(snapshot)
            if self.context_cybernetics:
                self.stability.feed_orchestrator(self.context_cybernetics)

        # Progress controller
        if self.progress:
            progress_signal = ProgressSignal(
                total_steps=step,
                completed_steps=actual_completed_steps,
                failed_steps=actual_failed_steps,
                tool_calls=actual_tool_calls,
                tool_errors=tool_error_count,
                output_changed=actual_progress,
                tests_passed=tests_passed,
                elapsed_seconds=actual_elapsed,
                max_steps=max_steps,
            )
            decision = self.progress.decide(progress_signal)
            summary["progress_decision"] = decision.to_dict()
            if decision.action in (ProgressAction.STOP, ProgressAction.REQUEST_CONFIRMATION):
                logger.warning(
                    "ProgressController: action=%s health=%.2f stall=%.2f",
                    decision.action.value, decision.health_score, decision.stall_score,
                )

        # Self-healing
        if self.healing:
            occ_idx = self.feedback._compute_oscillation() if self.feedback else 0.0
            self.healing.detect_and_heal({
                "error_rate": error_rate,
                "context_usage": (
                    context_manager.get_stats().usage_percentage / 100.0
                    if context_manager else 0.0
                ),
                "oscillation_index": occ_idx,
            })

        # Dual-PID outer loop
        if self.context_cybernetics and self.feedback:
            system_state = self.context_cybernetics.to_system_state()
            control_signal = self.feedback.observe(system_state)
            summary["control_signal"] = control_signal
            summary["system_state"] = system_state

            if control_signal.force_compaction and self.context_cybernetics.enabled:
                logger.info(
                    "Dual-PID: force_compaction stability=%.2f performance=%.2f",
                    system_state.stability_score(),
                    system_state.performance_score(),
                )

        # Supervisor aggregation
        if self.cyber_supervisor:
            snapshots = []
            if self.context_cybernetics:
                snapshots.append(
                    self.cyber_supervisor.snapshot_from_context(
                        self.context_cybernetics.get_stats()
                    )
                )
            if self.cost_control:
                snapshots.append(
                    self.cyber_supervisor.snapshot_from_cost(
                        self.cost_control.get_stats()
                    )
                )
            if tool_scheduler.last_decision:
                snapshots.append(
                    self.cyber_supervisor.snapshot_from_tool_decision(
                        tool_scheduler.last_decision.to_dict()
                    )
                )
            report = self.cyber_supervisor.report(snapshots)
            try:
                save_supervisor_report(report)
            except Exception:
                pass

        # AdaptivePIDTuner: periodic self-tuning
        if (
            self.adaptive_tuner
            and step > 0
            and step % 20 == 0
            and self.feedback
            and "system_state" in summary
        ):
            try:
                stability_error = 1.0 - system_state.stability_score()
                perf = system_state.performance_score()
                tuned = self.adaptive_tuner.tune(stability_error, dt=1.0, performance_score=perf)
                if tuned and self.context_cybernetics:
                    cp = self.context_cybernetics.pid
                    cp.kp = tuned.kp
                    cp.ki = tuned.ki
                    cp.kd = tuned.kd
            except Exception:
                pass

        return summary

    def task_end(
        self,
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        """Advance task-scoped maintenance exactly once at task finalization."""
        if not self.memory_pipeline:
            return None
        if all(
            value is None
            for value in (
                agent_budget,
                event_sink,
                cancellation_token,
                deadline_monotonic,
            )
        ):
            return self.memory_pipeline.maintain()
        return self.memory_pipeline.maintain(
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )

    # ── MEMORY INJECTION ────────────────────────────────────────────

    def inject_memories(
        self, task_description: str, current_messages: list[dict],
        current_files: list[str] | None = None,
        context_usage: float = 0.5,
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> list[dict]:
        """Inject relevant memories via unified pipeline."""
        if not self.memory_pipeline:
            return current_messages
        return self.memory_pipeline.inject(
            task_description,
            current_files,
            current_messages,
            context_usage=context_usage,
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )

    # ── REFLECTION ──────────────────────────────────────────────────

    def reflect_on_task(
        self, task_description: str, step: int, tool_error_count: int,
        execution_trace: list[dict[str, Any]] | None = None,
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Post-task reflection via unified pipeline.

        Returns the Memory entry this task produced, if any, so the caller can
        bind it to the Run. A later explicit user signal needs that binding to
        reach the lesson the turn wrote.
        """
        if not self.memory_pipeline:
            return None
        from minicode.reflection_evidence import append_trace_event

        has_structured_trace = execution_trace is not None
        trace = list(execution_trace) if has_structured_trace else []
        if not has_structured_trace:
            append_trace_event(trace, {"type": "tool_call", "count": step})
            append_trace_event(trace, {"type": "assistant", "steps": step})
        if tool_error_count > 0 and not has_structured_trace:
            append_trace_event(trace, {"type": "error", "count": tool_error_count})
        return self.memory_pipeline.write(
            task_description,
            trace,
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
            provenance=provenance,
        )

    # ── MODEL ROUTING ───────────────────────────────────────────────

    def route_and_switch(self, task_text: str, current_model_id: str) -> Any | None:
        """Route task and possibly switch model. Returns new adapter or None."""
        if not self.smart_router:
            return None
        try:
            routing, switch_result = self.smart_router.route_and_switch(
                task_text, current_model=current_model_id,
            )
            logger.info(
                "SmartRouter: model=%s tier=%s cost=$%.4f",
                routing.selected_model, routing.tier_name, routing.estimated_cost,
            )
            if switch_result and switch_result.success:
                logger.info(
                    "SmartRouter: switched %s -> %s",
                    switch_result.old_model, switch_result.new_model,
                )
                return switch_result.adapter
        except Exception:
            pass
        return None

    # ── ERROR RECOVERY ──────────────────────────────────────────────

    def try_switch_model_on_error(self, error_type: str, error_str: str) -> Any | None:
        """Attempt model switch on API error. Returns new adapter or None."""
        if not self.model_switcher or "rate" in error_str:
            return None
        try:
            result = self.model_switcher.switch_to(
                "", reason=f"{error_type}: {error_str[:80]}",
            )
            if result.success and result.adapter is not None:
                logger.info("ModelSwitcher: switched to %s", result.new_model)
                return result.adapter
        except Exception:
            pass
        return None

    # ── MODEL SELECTION ─────────────────────────────────────────────

    def recommend_model(self, task_complexity: str, current_model: str) -> None:
        """Log model recommendation for observability."""
        if not self.model_ctrl:
            return
        try:
            signal = ModelSelectionSignal(
                task_complexity=task_complexity,
                budget_pressure=0.3,
                latency_pressure=0.3,
                current_model=current_model,
            )
            decision = self.model_ctrl.decide(signal)
            logger.info(
                "ModelSelection: model=%s score=%.2f effort=%s",
                decision.model, decision.score, decision.reasoning_effort.value,
            )
        except Exception:
            pass

    # ── COST CONTROL ────────────────────────────────────────────────

    def run_cost_control(self, total_tokens: int, total_calls: int) -> None:
        """Run cost PID and apply to budget manager."""
        if not self.cost_control:
            return
        try:
            est_cost = total_tokens * 0.000015
            adj = self.cost_control.run(
                cost_usd=est_cost,
                total_tokens=total_tokens,
                total_calls=total_calls,
            )
            if self.context_compactor and hasattr(self.context_compactor, '_tool_budget') and self.context_compactor._tool_budget:
                self.cost_control.apply_to_budget_manager(self.context_compactor._tool_budget)
            elif adj and adj.budget_multiplier < 0.8:
                logger.warning("CostControl: budget tight but no compactor (mult=%.2f)", adj.budget_multiplier)
        except Exception:
            pass
