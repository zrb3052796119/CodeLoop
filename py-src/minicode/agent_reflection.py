"""Agent self-reflection system.

Provides post-task reflection to improve future performance:
- Success/failure analysis
- Strategy effectiveness review
- Error pattern recognition
- Memory recording for future reference
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from minicode.logging_config import get_logger
from minicode.memory import MemoryManager, MemoryScope

logger = get_logger("agent_reflection")


@dataclass
class ReflectionResult:
    """Result of a reflection cycle."""

    task_summary: str
    success: bool
    key_decisions: list[str]
    errors_encountered: list[str]
    lessons_learned: list[str]
    suggested_improvements: list[str]
    confidence: float
    timestamp: float = field(default_factory=time.time)

    def to_memory_entry(self) -> dict[str, Any]:
        """Convert to a memory entry for persistence."""
        return {
            "content": self._format_content(),
            "category": "reflection",
            "tags": ["self-reflection", "lessons-learned"]
            + (["success"] if self.success else ["failure"]),
            "metadata": {
                "confidence": self.confidence,
                "key_decisions": self.key_decisions,
                "errors": self.errors_encountered,
                "improvements": self.suggested_improvements,
            },
        }

    def _format_content(self) -> str:
        parts = [
            f"Task Reflection (Success: {self.success})",
            f"Summary: {self.task_summary}",
            "",
            "Key Decisions:",
        ]
        for d in self.key_decisions:
            parts.append(f"  - {d}")

        if self.errors_encountered:
            parts.extend(["", "Errors Encountered:"])
            for e in self.errors_encountered:
                parts.append(f"  - {e}")

        parts.extend(["", "Lessons Learned:"])
        for lesson in self.lessons_learned:
            parts.append(f"  - {lesson}")

        if self.suggested_improvements:
            parts.extend(["", "Suggested Improvements:"])
            for i in self.suggested_improvements:
                parts.append(f"  - {i}")

        return "\n".join(parts)


class ReflectionEngine:
    """Engine for agent self-reflection."""

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        min_confidence_threshold: float = 0.5,
    ):
        self.memory = memory_manager
        self.min_confidence = min_confidence_threshold

    def reflect(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        metrics: Any | None = None,
    ) -> ReflectionResult:
        """Generate reflection from execution trace.

        Args:
            task_description: Original task
            execution_trace: List of step records (tool calls, responses, errors)
            metrics: Optional metrics collector for performance data

        Returns:
            Reflection result
        """
        tool_calls = [s for s in execution_trace if s.get("type") == "tool_call"]
        errors = [s for s in execution_trace if s.get("type") == "error"]
        assistant_msgs = [s for s in execution_trace if s.get("type") == "assistant"]

        success = len(errors) == 0 and len(assistant_msgs) > 0

        key_decisions = self._extract_decisions(assistant_msgs)
        error_list = [e.get("content", "Unknown error") for e in errors]
        lessons = self._generate_lessons(tool_calls, errors, success)
        improvements = self._generate_improvements(tool_calls, errors, metrics)
        confidence = self._calculate_confidence(success, len(errors), len(tool_calls))

        reflection = ReflectionResult(
            task_summary=task_description[:200],
            success=success,
            key_decisions=key_decisions,
            errors_encountered=error_list,
            lessons_learned=lessons,
            suggested_improvements=improvements,
            confidence=confidence,
        )

        if self.memory and confidence >= self.min_confidence:
            self._persist_reflection(reflection)

        return reflection

    def _extract_decisions(self, assistant_msgs: list[dict[str, Any]]) -> list[str]:
        """Extract key decisions from assistant messages."""
        decisions = []
        keywords = ["decide", "choose", "select", "use ", "will ", "plan to", "start by"]
        for msg in assistant_msgs:
            content = msg.get("content", "")
            if any(kw in content.lower() for kw in keywords):
                first_sentence = content.split(".")[0].strip()
                if len(first_sentence) > 10:
                    decisions.append(first_sentence[:200])
        return decisions[:5]

    def _generate_lessons(
        self,
        tool_calls: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        success: bool,
    ) -> list[str]:
        """Generate lessons learned from execution."""
        lessons = []

        if success:
            lessons.append("Task completed successfully with the chosen approach.")
        else:
            lessons.append("Task encountered errors. Review error patterns for future avoidance.")

        tool_names = [t.get("tool_name", "unknown") for t in tool_calls]
        if tool_names:
            unique_tools = set(tool_names)
            lessons.append(f"Used {len(unique_tools)} unique tool(s): {', '.join(unique_tools)}.")

        if errors:
            error_tools = {e.get("tool_name", "unknown") for e in errors}
            lessons.append(
                f"Errors occurred with tool(s): {', '.join(error_tools)}. Consider alternative approaches."
            )

        return lessons

    def _generate_improvements(
        self,
        tool_calls: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        metrics: Any | None,
    ) -> list[str]:
        """Generate improvement suggestions."""
        improvements = []

        if len(errors) > 2:
            improvements.append("High error rate detected. Consider breaking task into smaller steps.")

        if len(tool_calls) > 10:
            improvements.append("Many tool calls used. Consider more efficient approaches or better planning.")

        if metrics and hasattr(metrics, "get_summary"):
            try:
                stats = metrics.get_summary()
                if stats.get("overall_success_rate", 1.0) < 0.7:
                    improvements.append(
                        "Low success rate. Review tool usage patterns and error recovery strategies."
                    )
            except Exception:
                pass

        return improvements

    def _calculate_confidence(
        self,
        success: bool,
        error_count: int,
        tool_count: int,
    ) -> float:
        """Calculate reflection confidence score."""
        base = 0.8 if success else 0.4
        error_penalty = min(error_count * 0.1, 0.3)
        tool_bonus = min(tool_count * 0.02, 0.1)
        return max(0.0, min(1.0, base - error_penalty + tool_bonus))

    def _persist_reflection(self, reflection: ReflectionResult) -> None:
        """Save reflection to long-term memory."""
        if self.memory is None:
            return

        entry = reflection.to_memory_entry()
        try:
            # Use add_entry if available (new API), fallback to add (old API)
            if hasattr(self.memory, "add_entry"):
                self.memory.add_entry(
                    scope=MemoryScope.PROJECT,
                    category=entry["category"],
                    content=entry["content"],
                    tags=entry["tags"],
                )
            else:
                self.memory.add(
                    content=entry["content"],
                    scope=MemoryScope.PROJECT,
                    category=entry["category"],
                    tags=entry["tags"],
                    metadata=entry["metadata"],
                )
            logger.info("Reflection persisted to memory (confidence: %.2f)", reflection.confidence)
        except Exception as e:
            logger.warning("Failed to persist reflection: %s", e)
