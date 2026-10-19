"""Bounded feedback for authorized coding work, independent of the work chain.

Successful inspection is evidence gathering, not an implementation milestone.
This controller only steers the next model request; it grants no permissions,
forces no edits, and makes no claims about correctness or budget reservations.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import math

from minicode.agent_budget import AgentBudgetSnapshot
from minicode.intent_parser import ActionType, IntentType, ParsedIntent


SOURCE_READ_TOOLS = frozenset(
    {
        "read_file",
        "grep_files",
        "list_files",
        "file_tree",
        "file_line_count",
        "get_ast_info",
        "find_symbols",
        "find_references",
    }
)
_CHANGE_ACTIONS = frozenset(
    {
        ActionType.CREATE,
        ActionType.UPDATE,
        ActionType.MERGE,
        ActionType.SPLIT,
        ActionType.MOVE,
        ActionType.RENAME,
    }
)
_CODING_INTENTS = frozenset(
    {
        IntentType.CODE,
        IntentType.DEBUG,
        IntentType.REFACTOR,
        IntentType.TEST,
        IntentType.CONFIGURE,
        IntentType.DOCUMENT,
    }
)


def is_implementation_task(intent: ParsedIntent | None) -> bool:
    if intent is None or intent.is_read_only():
        return False
    operations = intent.affirmed_operations
    if not intent.operations and intent.primary_operation is not None:
        operations = (intent.primary_operation,)
    return any(
        operation.intent_type in _CODING_INTENTS
        and operation.action_type in _CHANGE_ACTIONS
        and not re.search(
            r"\b(?:plan|explain|describe|discuss|analy[sz]e)\s+(?:how|to|a\s+fix)\b"
            r"|(?:只|仅)(?:做)?(?:分析|规划|讨论)|(?:给出|制定)(?:修复|修改)?(?:计划|方案)",
            operation.clause,
            re.IGNORECASE,
        )
        for operation in operations
    )


@dataclass(frozen=True)
class CodingProgressNotice:
    reason: str
    text: str
    read_batches: int
    remaining_tokens: int | None
    estimated_requests_left: int | None
    remaining_seconds: float | None = None


@dataclass
class CodingProgressTracker:
    enabled: bool
    read_batches: int = 0
    mutations: int = 0
    _last_notice_batch: int = 0
    _notice_count: int = 0
    _pressure_level: int = 0
    _routine_notice_count: int = 0
    _deadline_pressure_level: int = 0

    def observe(self, *, successful_read: bool, changed: bool, verified: bool) -> None:
        if changed or verified:
            self.read_batches = 0
            self._last_notice_batch = 0
            self.mutations += int(changed)
        elif successful_read:
            self.read_batches += 1

    def authoritative_delivery(self, *, changed: bool) -> None:
        """Retire pre-delivery inspection without replenishing notice quotas.

        Delivery alone is not evidence of a byte change: only the existing
        host mutation observer can increment that count.
        """
        self.read_batches = 0
        self._last_notice_batch = 0
        self.mutations += int(changed)

    def before_request(
        self,
        snapshot: AgentBudgetSnapshot | None,
        estimated_input: int,
        *,
        remaining_steps: int | None = None,
        remaining_seconds: float | None = None,
    ) -> CodingProgressNotice | None:
        if not self.enabled:
            return None
        if remaining_seconds is not None:
            remaining_seconds = (
                max(0.0, remaining_seconds)
                if math.isfinite(remaining_seconds) else None
            )
        remaining = None
        requests_left = None
        allowance_limits = []
        if snapshot is not None and snapshot.limit_total_tokens is not None:
            remaining = max(
                0,
                snapshot.limit_total_tokens
                - snapshot.used_total_tokens
                - snapshot.reserved_total_tokens,
            )
            # A planning target, not an atomic reservation: leave room for
            # implementation, a check and follow-up at roughly this context size.
            requests_left = remaining // max(estimated_input + 4096, 1)
            allowance_limits.append(requests_left)
        if snapshot is not None and snapshot.limit_model_calls is not None:
            calls_left = max(0, snapshot.limit_model_calls - snapshot.used_model_calls)
            allowance_limits.append(calls_left)
        if remaining_steps is not None:
            allowance_limits.append(max(0, remaining_steps))
        requests_left = min(allowance_limits) if allowance_limits else None
        # Reserve escalation for actual scarcity, not an arbitrary percentage
        # of the starting budget. Three routine notices cannot consume it.
        pressure_level = (
            2 if requests_left is not None and requests_left <= 3
            else 1 if requests_left is not None and requests_left <= 6
            else 0
        )
        budget_urgent = self.read_batches >= 3 and pressure_level > self._pressure_level
        # A deadline is independent of the read streak: edits or a delivered
        # child result do not give this turn more time to test and hand back.
        deadline_level = (
            2 if remaining_seconds is not None and remaining_seconds <= 90
            else 1 if remaining_seconds is not None and remaining_seconds <= 180
            else 0
        )
        deadline_urgent = deadline_level > self._deadline_pressure_level
        urgent = budget_urgent or deadline_urgent
        if not urgent and (
            self.read_batches < 3
            or
            self._routine_notice_count >= 3
            or self.read_batches - self._last_notice_batch < 4
        ):
            return None
        self._last_notice_batch = self.read_batches
        self._notice_count += 1
        if budget_urgent:
            self._pressure_level = pressure_level
        if deadline_urgent:
            self._deadline_pressure_level = deadline_level
        if not urgent:
            self._routine_notice_count += 1
        phase = "verification" if self.mutations else "implementation"
        budget_text = (
            f" Remaining shared token allowance: {remaining}; roughly "
            f"{requests_left} further requests after accounting for context size, "
            "a small output allowance and call/step limits. This is an estimate, not a reservation."
            if remaining is not None
            else ""
        )
        if remaining is None and requests_left is not None:
            budget_text = f" Remaining call/step allowance: {requests_left}."
        if remaining_seconds is not None:
            budget_text += (
                f" Remaining execution time: {remaining_seconds:.1f} seconds."
                " This is the current deadline, not time reserved for another request."
            )
        action_text = (
            " The implementation and verification window is now nearly exhausted. "
            "Use the remaining window to finish a supported edit, the requested "
            "regression test, and the required handoff; run the relevant check "
            "when that is your role. If a separate Verifier owns commands, "
            "finish implementation and submit your structured result for it. "
            "if this is not safe, name the precise blocker and stop the broad inspection. "
            "Do not spend the remaining window restating a plan or collecting unrelated examples."
            if urgent else ""
        )
        text = (
            f"[Coding progress checkpoint] {self.read_batches} inspection batches "
            f"have completed without an observed code change or verification. "
            f"Move toward {phase} using the evidence already returned. "
            "If the edit location and expected behavior are known, make the "
            "smallest authorized change now and run the relevant check. "
            "If a specific fact is still missing, state that gap briefly and "
            "read only what resolves it; avoid another broad repository survey. "
            "A coordinator should delegate the implementation directly to one "
            "workflow, not start another planning agent. "
            "Do not invent a change, bypass approval or verification, or claim "
            "success without evidence. If blocked, report the concrete blocker."
            + budget_text + action_text
        )
        if deadline_urgent:
            text = (
                "[Coding deadline checkpoint] The current execution window is "
                "running out. If the requested implementation, checks and handoff "
                "are already complete, deliver their existing evidence now. "
                "Otherwise finish only the remaining supported implementation, "
                "requested regression tests and required structured result. "
                "If a separate Verifier owns commands, hand the completed "
                "implementation to it using the required result channel. "
                "An acknowledged workflow receipt is the basis for the parent's "
                "final response; do not reopen its investigation or start a "
                "replacement workflow solely because of this reminder. "
                "A code edit alone does not establish completed delivery. "
                "If the remaining work cannot be completed safely, name the "
                "precise blocker and report what remains unverified. "
                "This reminder grants no permission and does not authorize "
                "invented edits, skipped verification or unsupported success claims."
                + budget_text
            )
        return CodingProgressNotice(
            "implementation_deadline_pressure"
            if deadline_urgent
            else "implementation_budget_pressure"
            if budget_urgent
            else "inspection_without_delivery",
            text,
            self.read_batches,
            remaining,
            requests_left,
            remaining_seconds,
        )
