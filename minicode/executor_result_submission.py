"""Assignment-bound semantic submission without model-owned transport IDs."""

from __future__ import annotations

import hashlib
import json
from weakref import WeakKeyDictionary

from minicode.capability_contracts import (
    CapabilityDomain,
    CapabilityEffects,
    CapabilityProfile,
    CapabilityScope,
)
from minicode.failure_evidence import redact_diagnostic_text
from minicode.multi_agent_contracts import (
    Assignment,
    ImplementationOrder,
    MAX_ROLE_CONTRACT_BYTES,
    WorkerRole,
    validate_executor_declaration,
)
from minicode.scheduler_artifacts import (
    AssignmentArtifactChannel,
    SchedulerArtifactError,
)
from minicode.tooling import ToolDefinition, ToolResult


_SUBMISSION_CHANNELS = WeakKeyDictionary()


def executor_submit_channel(tool: ToolDefinition):
    """Recognize a host-created native submission, never just a tool name."""
    return _SUBMISSION_CHANNELS.get(tool.run) if tool.name == "executor_submit" else None


class ExecutorResultChannel:
    """Keep the original capability while rejecting invalid semantics early.

    Preflight validates declarations only. A completed declaration is not an
    ExecutionResult: the Scheduler must still freeze real bytes and validate
    its ChangeSet, and the Verifier still decides whether promotion is allowed.
    """

    def __init__(
        self,
        inner: AssignmentArtifactChannel,
        assignment: Assignment,
        order: ImplementationOrder,
    ) -> None:
        if assignment.role is not WorkerRole.EXECUTOR:
            raise ValueError("submission requires an Executor Assignment")
        if order.assignment_id != assignment.assignment_id:
            raise ValueError("submission order does not match Assignment")
        self._inner = inner
        self._assignment = assignment
        self._order = order
        self.validation_errors: list[str] = []
        self._completion_guard = None

    def bind_completion_guard(self, guard) -> None:
        if self._completion_guard is not None and self._completion_guard != guard:
            raise ValueError("submission channel already has a completion guard")
        self._completion_guard = guard

    def record_validation_error(self, error: Exception) -> None:
        # Legacy validators may include a rejected field name. Redact before
        # bounding so truncation cannot hide part of a credential pattern.
        self.validation_errors.append(redact_diagnostic_text(str(error), max_chars=500))
        del self.validation_errors[:-4]

    def write(self, key: str, content: str, *, author: str):
        # Exact-key authority must still fail before parsing unrelated input.
        if key != self._inner.write_key:
            raise SchedulerArtifactError(
                "Assignment may write only its exact write key"
            )
        if not isinstance(content, str):
            raise SchedulerArtifactError("Executor declaration must be JSON text")
        if len(content.encode("utf-8")) > MAX_ROLE_CONTRACT_BYTES:
            raise SchedulerArtifactError("Executor declaration exceeds the byte limit")

        def unique_object(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError("duplicate declaration field")
                result[name] = value
            return result

        try:
            payload = json.loads(content, object_pairs_hook=unique_object)
            declaration = validate_executor_declaration(
                self._assignment,
                payload,
                implementation_order=self._order,
            )
        except (ValueError, TypeError) as error:
            self.record_validation_error(error)
            raise SchedulerArtifactError(
                f"invalid Executor declaration: {error}"
            ) from error
        if self._completion_guard is not None:
            self._completion_guard(declaration)
        return self._inner.write(
            key,
            json.dumps(
                declaration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            author=author,
        )

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def create_executor_submit_tool(
    assignment: Assignment,
    order: ImplementationOrder,
    channel: ExecutorResultChannel,
) -> ToolDefinition:
    """Create one tool for one worker; no public input selects its owner."""
    if channel._assignment is not assignment or channel._order is not order:
        raise ValueError("submission capability belongs to another Assignment")

    def step_id(value: object) -> str:
        if type(value) is not int or not 1 <= value <= len(order.steps):
            raise ValueError(f"step must be an integer from 1 to {len(order.steps)}")
        return order.steps[value - 1].step_id

    def declaration_from(value: object) -> dict:
        if not isinstance(value, dict):
            raise ValueError("Executor submission must be an object")
        required = ("status", "steps", "deviations", "blockers")
        missing = [field for field in required if field not in value]
        unknown = [str(field) for field in value if field not in required]
        errors = []
        if missing:
            errors.append("missing required fields: " + ", ".join(missing))
        if unknown:
            errors.append("unknown fields: " + ", ".join(sorted(unknown)))
        if errors:
            raise ValueError("Executor submission " + "; ".join(errors))
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_ROLE_CONTRACT_BYTES:
            raise ValueError("Executor submission exceeds the byte limit")
        normalize_diagnostics = value["status"] in ("blocked", "failed")

        def diagnostic_text(text: object, maximum: int) -> str:
            if not isinstance(text, str) or not text.strip() or "\x00" in text:
                raise ValueError("diagnostic text must be a nonempty string without NUL")
            text = text.strip()
            if len(text) <= maximum:
                return text
            suffix = " [truncated sha256:" + hashlib.sha256(text.encode()).hexdigest()[:12] + "]"
            return text[:maximum - len(suffix)] + suffix

        steps = value["steps"]
        deviations = value["deviations"]
        if not isinstance(steps, list) or len(steps) > 40:
            raise ValueError("steps must be a bounded list accounting for every step")
        if not isinstance(deviations, list) or len(deviations) > 40:
            raise ValueError("deviations must be a bounded list")
        results = []
        for item in steps:
            if not isinstance(item, dict) or set(item) != {"step", "status"}:
                raise ValueError("each step must contain only step and status")
            results.append(
                {
                    "stepId": step_id(item["step"]),
                    "status": item["status"],
                    "artifactRefs": [],
                }
            )
        reasons = []
        for item in deviations:
            if not isinstance(item, dict) or set(item) != {"step", "reason", "impact"}:
                raise ValueError(
                    "each deviation must contain only step, reason, and impact"
                )
            reasons.append(
                {
                    "stepId": step_id(item["step"]),
                    "reason": item["reason"],
                    "impact": item["impact"],
                }
            )
        blockers = value["blockers"]
        if normalize_diagnostics:
            # A blocked/failed declaration carries no promotable ChangeSet.
            # Coalesce only explanatory diagnostics; step statuses, identity,
            # scope and completed declarations still use the strict contract.
            merged: dict[str, dict] = {}
            severity = {"low": 0, "medium": 1, "high": 2}
            for item in reasons:
                impact = item["impact"]
                if not isinstance(impact, str) or impact not in severity:
                    raise ValueError("ExecutionResult deviation impact is invalid")
                reason = diagnostic_text(item["reason"], 4000)
                previous = merged.get(item["stepId"])
                if previous is None:
                    merged[item["stepId"]] = {**item, "reason": reason}
                else:
                    previous["reason"] = diagnostic_text(previous["reason"] + "; " + reason, 4000)
                    if severity[impact] > severity[previous["impact"]]:
                        previous["impact"] = impact
            reasons = list(merged.values())
            if not isinstance(blockers, list) or len(blockers) > 100:
                raise ValueError("ExecutionResult blockers must be a bounded list")
            blockers = [diagnostic_text(item, 500) for item in blockers]
        declaration = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
            "orderId": order.order_id,
            "status": value["status"],
            "stepResults": results,
            "deviations": reasons,
            "selfChecks": [],
            "blockers": blockers,
        }
        return validate_executor_declaration(
            assignment, declaration, implementation_order=order
        )

    def validate(value: object) -> dict:
        try:
            declaration_from(value)
        except (ValueError, TypeError) as error:
            channel.record_validation_error(error)
            raise
        # Public input remains semantic; IDs are always regenerated in run.
        return value

    def run(value: dict, context) -> ToolResult:
        if getattr(context, "_subagent_mailbox", None) is not channel:
            return ToolResult(
                ok=False,
                output="error[executor_submit_owner]: capability belongs to another Assignment",
            )
        try:
            declaration = declaration_from(value)
            channel.write(
                channel.write_key,
                json.dumps(declaration, ensure_ascii=False),
                author="executor",
            )
        except (ValueError, TypeError, SchedulerArtifactError) as error:
            channel.record_validation_error(error)
            return ToolResult(
                ok=False, output=f"error[executor_submit_invalid]: {error}"
            )
        return ToolResult(
            ok=True,
            output='{"declarationReceived":true,"verification":"pending","writeback":"pending"}',
            _implementation_submitted=True,
        )

    _SUBMISSION_CHANNELS[run] = channel
    return ToolDefinition(
        name="executor_submit",
        description=(
            "Submit this Executor's result once before finishing. Declare every "
            "locked step by its 1-based position. Host binds the Assignment and "
            "order; successful submission awaits verification and writeback."
            " A blocked/failed submission coalesces repeated diagnostic steps "
            "at the highest impact and marks oversized diagnostic text truncated."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["completed", "blocked", "failed"],
                },
                "steps": {
                    "type": "array",
                    "description": "Each locked step occurs exactly once; completed requires all steps completed.",
                    "minItems": len(order.steps),
                    "maxItems": len(order.steps),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "step": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": len(order.steps),
                            },
                            "status": {
                                "type": "string",
                                "enum": ["completed", "skipped", "failed"],
                            },
                        },
                        "required": ["step", "status"],
                    },
                },
                "deviations": {
                    "type": "array",
                    "maxItems": 40,
                    "description": "Use one diagnostic per step. Blocked/failed duplicates are merged; completed diagnostics stay strict.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "step": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": len(order.steps),
                            },
                            "reason": {"type": "string", "minLength": 1, "maxLength": 4000},
                            "impact": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                            },
                        },
                        "required": ["step", "reason", "impact"],
                    },
                },
                "blockers": {"type": "array", "maxItems": 100, "uniqueItems": True,
                             "description": "Always required, like status, steps, and deviations. Use [] for completed; blocked/failed requires a concrete blocker.",
                             "items": {"type": "string", "minLength": 1, "maxLength": 500}},
            },
            "required": ["status", "steps", "deviations", "blockers"],
        },
        validator=validate,
        run=run,
        capability_profile=CapabilityProfile(
            domain=CapabilityDomain.COMMUNICATION,
            scope=CapabilityScope.WRITE,
            effects=CapabilityEffects(
                read_only=False,
                destructive=False,
                external=False,
                executes_code=False,
                delegates=False,
            ),
            purposes=("Submit one assignment-bound declaration",),
        ),
    )


__all__ = ["ExecutorResultChannel", "create_executor_submit_tool"]
