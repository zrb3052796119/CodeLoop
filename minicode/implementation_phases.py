"""Implementation progress with an opt-in bounded phase schedule.

Continuous execution keeps the authorized tool registry and leaves stopping to
the owner, actual shared budget and provider constraints. Bounded execution
additionally allocates investigation, implementation and handoff allowances.
Neither mode grants write authority or fabricates a completion declaration.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from minicode.coding_progress import SOURCE_READ_TOOLS
from minicode.verification_observation import project_command_verification


class ImplementationPhase(str, Enum):
    INVESTIGATE = "investigate"
    IMPLEMENT = "implement"
    FINALIZE = "finalize"


class PhaseCallDeadlineExceeded(TimeoutError):
    """A bounded request expired while the owning turn still has time."""


_BROAD_READS = SOURCE_READ_TOOLS - {"read_file", "grep_files"}
_IMPLEMENT_TOOLS = frozenset({
    "read_file", "grep_files", "edit_file", "write_file", "run_command",
    "test_runner", "executor_submit", "executor_check", "executor_test", "subagent_note_read", "subagent_note_write",
    "todo_read", "todo_write", "load_skill", "task", "implementation_context", "verification_output_read",
})
_FINAL_TOOLS = frozenset({
    "run_command", "test_runner", "executor_submit", "executor_check", "subagent_note_read",
    "subagent_note_write", "todo_read", "todo_write", "task", "verification_output_read",
})
_RESULT_DEPENDENCIES = frozenset({
    "edit_file", "write_file", "executor_check", "executor_test", "run_command", "test_runner",
})


def _exact_include_target(args: dict, *, cwd: str | None, permissions: Any) -> str | None:
    """Narrow a directory/include request to one permitted existing file."""
    include = args.get("include")
    if isinstance(include, list) and len(include) == 1:
        include = include[0]
    if (not cwd or not isinstance(include, str) or not include
            or include.startswith(".") or any(char in include for char in "/\\:*?[]{}\x00")):
        return None
    directory = args.get("path", ".")
    if not isinstance(directory, str):
        return None
    relative = Path(directory) / include
    if relative.is_absolute() or ".." in relative.parts:
        return None
    from minicode.permissions import PermissionDeniedError
    from minicode.tooling import ToolContext
    from minicode.workspace import resolve_tool_path

    try:
        root = Path(cwd).resolve()
        if not (root / relative).resolve().is_relative_to(root):
            return None
        target = resolve_tool_path(ToolContext(cwd=cwd, permissions=permissions),
                                   relative.as_posix(), "search")
        if target.is_file():
            return relative.as_posix()
    except (OSError, PermissionDeniedError):
        pass
    return None


@dataclass(frozen=True)
class PhaseRequest:
    phase: ImplementationPhase
    preserve_calls: int
    preserve_tokens: int
    output_limit: int
    deadline: float | None
    message: str | None
    handoff_only: bool = False
    conditional_handoff: bool = False


class ImplementationPhaseController:
    """Continuous work by default; explicit bounded mode retains phase quotas."""

    def __init__(self, *, snapshot: Any, max_steps: int | None, now: float,
                 deadline: float | None, analysis_complete: bool = False,
                 output_limit: int = 8192, final_response_calls: int = 0,
                 allow_test_completion: bool = False,
                 allow_checked_source_completion: bool = False,
                 allow_conditional_handoff: bool = False,
                 require_structured_handoff: bool = False,
                 request_timeout_seconds: float | str | None = None,
                 bounded: bool = False) -> None:
        if type(bounded) is not bool:
            raise ValueError("bounded phase policy must be a host boolean")
        self.bounded = bounded
        self.started = now
        self.deadline = deadline if deadline is not None and math.isfinite(deadline) else None
        try:
            request_timeout = (float(request_timeout_seconds)
                               if not isinstance(request_timeout_seconds, bool) else 0.0)
        except (TypeError, ValueError, OverflowError):
            request_timeout = 0.0
        self.request_timeout_seconds = (
            request_timeout if math.isfinite(request_timeout) and request_timeout > 0 else None
        )
        self.initial_used_tokens = getattr(snapshot, "used_total_tokens", 0)
        self.final_response_calls = final_response_calls
        limit = getattr(snapshot, "limit_model_calls", None)
        available = max(0, limit - getattr(snapshot, "used_model_calls", 0)) if limit is not None else 50
        available = max(0, available - final_response_calls)
        self.call_limit = min(max(0, max_steps) if max_steps is not None else available, available)
        self.allow_test_completion = allow_test_completion
        self.allow_checked_source_completion = allow_checked_source_completion
        self.allow_conditional_handoff = allow_conditional_handoff
        self.require_structured_handoff = require_structured_handoff
        self.final_call_limit = min(3 if allow_test_completion else 2, max(0, self.call_limit - 1))
        self.investigation_calls = min(6, max(0, (self.call_limit - self.final_call_limit) // 5))
        remaining_time = max(0.0, self.deadline - now) if self.deadline is not None else 600.0
        # The original declaration allowance bounds the eventual commitment.
        # Entering FINALIZE will lock one absolute floor from its remaining
        # owner time, rather than repeatedly borrowing half of the final reply.
        self.handoff_reserve_seconds = min(45.0, remaining_time * 0.1)
        self.handoff_start = (self.deadline - self.handoff_reserve_seconds
                              if self.deadline is not None else None)
        self.finalization_started_at: float | None = None
        self.investigation_end = now + min(120.0, remaining_time * 0.2)
        self.final_start = now + remaining_time - min(120.0, remaining_time * 0.2)
        token_limit = getattr(snapshot, "limit_total_tokens", None)
        available_tokens = max(0, token_limit - self.initial_used_tokens - getattr(snapshot, "reserved_total_tokens", 0)) if token_limit is not None else None
        self.investigation_tokens = available_tokens // 5 if available_tokens is not None else None
        self.output_limit = output_limit if type(output_limit) is int and output_limit > 0 else 8192
        self.phase = ImplementationPhase.IMPLEMENT if analysis_complete else ImplementationPhase.INVESTIGATE
        self.calls = 0
        self.final_calls = 0
        self.focused_read_batches = 0
        self.implementation_started = False
        self.investigation_read_calls = 0
        self._announced: ImplementationPhase | None = None
        self._native_final_only = False
        self.handoff_only = False
        self.conditional_handoff = False
        self._conditional_handoff_announced = False
        self._handoff_admitted = False
        self._handoff_rejected = False
        self._previous_input: int | None = None
        self._input_growth = 0
        self._verification_failed = False
        self._failed_edit_paths: set[str] = set()
        if not self.bounded:
            # The loop owns explicit step/global limits. Do not invent a local
            # 50-call ceiling or convert remaining work into a declaration
            # because an estimated percentage of time/tokens has elapsed.
            self.call_limit = None
            self.investigation_calls = 0
            self.final_call_limit = 0
            self.investigation_tokens = None
            self.investigation_end = None
            self.final_start = None
            self.handoff_reserve_seconds = 0.0
            self.handoff_start = None
            self.phase = ImplementationPhase.IMPLEMENT

    @property
    def edit_repair_pending(self) -> bool:
        return bool(self._failed_edit_paths) and not self._handoff_admitted

    @property
    def repair_pending(self) -> bool:
        """Host-observed unfinished work, without reopening an admitted handoff."""
        return (self._verification_failed or bool(self._failed_edit_paths)) and not self._handoff_admitted

    @property
    def verification_repair_pending(self) -> bool:
        """An actual host check has not passed; model claims cannot set this."""
        return self._verification_failed and not self._handoff_admitted

    def preview(self, snapshot: Any, *, estimated_input: int, now: float) -> PhaseRequest:
        """Price a proposed model view without consuming phase/closing state."""
        return copy.copy(self).request(snapshot, estimated_input=estimated_input, now=now)

    def _tail_tokens(self, estimated_input: int, output: int, future: int,
                     final_output: int) -> int:
        # Every future request includes the previous model output and tool
        # feedback. Reserve the observed growth instead of pricing every
        # remaining request as if its input stayed fixed. This is an admission
        # estimate, not a promise about unbounded provider/tool output.
        growth = max(self._input_growth, output)
        return (output + future * (estimated_input + final_output)
                + future * (future + 1) // 2 * growth)

    def _advance(self, phase: ImplementationPhase) -> None:
        order = tuple(ImplementationPhase)
        if order.index(phase) > order.index(self.phase):
            self.phase = phase

    def request(self, snapshot: Any, *, estimated_input: int, now: float) -> PhaseRequest:
        if not self.bounded:
            end = (now + self.request_timeout_seconds
                   if self.request_timeout_seconds is not None else self.deadline)
            if self.deadline is not None and end is not None:
                end = min(end, self.deadline)
            # Reserve only this response's configured output, not hypothetical
            # future repair/summary calls. Provider and atomic shared-budget
            # admission remain responsible for their actual hard constraints.
            return PhaseRequest(self.phase, 0, self.output_limit,
                                self.output_limit, end, None)
        estimated_input = max(0, estimated_input)
        if self._previous_input is not None:
            self._input_growth = max(self._input_growth, estimated_input - self._previous_input)
        self._previous_input = estimated_input
        used_tokens = getattr(snapshot, "used_total_tokens", 0) - self.initial_used_tokens
        if self.phase is ImplementationPhase.INVESTIGATE and (
            self.calls >= self.investigation_calls or now >= self.investigation_end
            or self.investigation_tokens is not None and used_tokens >= self.investigation_tokens
        ):
            self._advance(ImplementationPhase.IMPLEMENT)
        final_output = min(self.output_limit, 4096 if self.allow_test_completion else 2048)
        tail_output = final_output
        tail_tokens = self._tail_tokens(estimated_input, self.output_limit,
                                       self.final_call_limit, final_output)
        token_limit = getattr(snapshot, "limit_total_tokens", None)
        tokens_left = max(0, token_limit - getattr(snapshot, "used_total_tokens", 0) - getattr(snapshot, "reserved_total_tokens", 0)) if token_limit is not None else None
        shared_limit = getattr(snapshot, "limit_model_calls", None)
        calls_left = (shared_limit - getattr(snapshot, "used_model_calls", 0) - self.final_response_calls
                      if shared_limit is not None else self.call_limit - self.calls)
        if self.phase is not ImplementationPhase.FINALIZE and (
            self.calls >= self.call_limit - self.final_call_limit
            or calls_left <= self.final_call_limit
            or now >= self.final_start
            or tokens_left is not None and tokens_left <= tail_tokens + estimated_input
        ):
            self._advance(ImplementationPhase.FINALIZE)
        if self.phase is ImplementationPhase.FINALIZE:
            if self.finalization_started_at is None:
                self.finalization_started_at = now
                if self.deadline is not None:
                    reserve = min(self.handoff_reserve_seconds,
                                  max(0.0, self.deadline - now) / 3)
                    self.handoff_start = self.deadline - reserve
            # Closing tests are optional work; the last result request is not.
            # If feedback grew too much, retire optional rounds before their
            # admission rather than reject the entire finalization block.
            remaining = max(1, min(self.final_call_limit - self.final_calls,
                                   self.call_limit - self.calls, calls_left))
            if self.repair_pending:
                # A failed public check is unfinished work, not a reason to
                # discard usable allowance merely because the initial closing
                # estimate counted three requests. Fund only the next repair
                # and a real handoff; recompute after its actual tool feedback.
                remaining = max(1, min(2, self.call_limit - self.calls, calls_left))
                tail_output = min(self.output_limit, 2048)
            if (self._handoff_admitted
                    or self.handoff_start is not None and now >= self.handoff_start):
                remaining = 1
            while remaining > 1 and tokens_left is not None and (
                estimated_input + self._tail_tokens(estimated_input, final_output,
                                                     remaining - 1, tail_output) > tokens_left
            ):
                remaining -= 1
            self.final_call_limit = self.final_calls + remaining
            # A host-bound submit gate can consume actual write/check results
            # in this same serial response. It does not need a second model
            # request merely to repeat a completed declaration. This option
            # exists only while the original work deadline still permits the
            # repair; an admitted submit-only handoff never reopens writes.
            self.conditional_handoff = bool(
                remaining == 1 and self.repair_pending
                and self.allow_conditional_handoff
                and self.allow_checked_source_completion
                and self.require_structured_handoff
                and calls_left > 0 and self.calls < self.call_limit
                and (self.handoff_start is None or now < self.handoff_start)
            )
            self.handoff_only = remaining == 1 and not self.conditional_handoff
        message = None
        if self._announced is not self.phase:
            self._announced = self.phase
            message = {
                ImplementationPhase.INVESTIGATE: (
                    f"Host execution phase: investigate. At most {self.investigation_calls} "
                    "requests may be admitted for investigation. Reaching 20% of the "
                    "initial token/time allowance selects implementation for the next "
                    "request; an admitted request retains its bounded completion window. "
                    "Locate the necessary source and test behavior. "
                    "Implementation includes the requested regression test."
                ),
                ImplementationPhase.IMPLEMENT: (
                    "Host execution phase: implement. Broad repository navigation is "
                    "now disabled. Focused file reads remain available within the "
                    "same call/token/time allowance, including reading exact source "
                    "after failed edits. Use each batch to resolve a specific remaining fact, then implement "
                    "supported changes and the requested "
                    "tests. If required evidence is missing, submit a precise blocker; "
                    "do not invent an edit. File changes must use the authorized file tools."
                ),
                ImplementationPhase.FINALIZE: (
                    "Host execution phase: finalize. Investigation is disabled. "
                    + ("Authorized source edits remain available with automatic host checks "
                     + ("before the fixed work deadline. " if self.allow_conditional_handoff
                        else "before the last reserved request. ")
                     if self.allow_checked_source_completion else
                     "Source repair remains available while actual implementation checks fail, "
                     "before the last reserved request. ")
                    + ("The assigned regression-test deliverable can still be created or repaired "
                       + ("with executor_test before the fixed work deadline. " if self.allow_conditional_handoff
                          else "with executor_test before the last reserved request. ")
                       if self.allow_test_completion else "")
                    + ("Call executor_submit after authorized repairs in the same response if ready. "
                       "The host accepts completed only when all approved checks passed on the "
                       "current candidate; otherwise it returns the missing or failed evidence. "
                       "Submission still awaits formal verification and review. "
                       if self.require_structured_handoff and self.allow_conditional_handoff else
                       "The final request is reserved for executor_submit with the completed "
                       "result or an honest blocked/failed declaration. A successful submission "
                       "ends this worker immediately; prose cannot replace that channel."
                       if self.require_structured_handoff else
                       "Use the remaining verification rounds, then give an honest final response "
                       "describing the changes, actual check results and any remaining blocker. "
                       "The final request has no tools and is reserved for that response.")
                ),
            }[self.phase]
        if self.conditional_handoff and not self._conditional_handoff_announced:
            self._conditional_handoff_announced = True
            notice = (
                "Host conditional handoff: the remaining allowance fits one repair response, "
                "not a repair followed by a separate declaration request. Authorized file/test "
                "tools and executor_submit can run serially in this response. Submit completed "
                "after the proposed repairs; the host will accept it only if every approved "
                "check passed on the current candidate bytes. Failed or missing evidence "
                "rejects completion. The original work deadline and final verification gates "
                "still apply. If a supported repair is unavailable, submit an honest blocker."
            )
            message = f"{message}\n{notice}" if message else notice
        if self.phase is ImplementationPhase.INVESTIGATE:
            # Investigation thresholds govern the next admission, not a
            # shorter provider response window. The existing owner/handoff
            # bounds below still protect time for delivery.
            end = now + (180.0 if self.deadline is not None else 45.0)
            output = min(self.output_limit, 2048)
        elif self.phase is ImplementationPhase.IMPLEMENT:
            end = now + 180.0
            output = self.output_limit
        else:
            # FINALIZE already committed one fixed work/hand-back boundary.
            # Calls and token floors fund later requests, not new wall-clock
            # slices: dividing again can cancel a healthy test response before
            # that committed boundary and leave only seconds for its retry.
            # Use the normal implementation request ceiling; the unchanged
            # owner and fixed handoff floor below remain the hard bounds.
            end = now + (180.0 if self.deadline is not None else 45.0)
            output = min(self.output_limit, 2048) if self.handoff_only else final_output
        if self.request_timeout_seconds is not None:
            # Respect the configured provider window without lending it owner
            # or handoff time. Adapters still enforce their transport limits.
            end = now + self.request_timeout_seconds
        future = max(0, self.final_call_limit - self.final_calls - 1) if self.phase is ImplementationPhase.FINALIZE else self.final_call_limit
        if (self.phase is ImplementationPhase.FINALIZE and not self.handoff_only
                and any(self.allowed_name(name) for name in ("edit_file", "write_file", "executor_test"))):
            # A final source/test response still writes code. Retain the
            # configured capacity when this input and the existing tail fit;
            # the smaller declaration cap belongs only to submit-only work.
            output = self.output_limit
            if tokens_left is not None:
                # Reprice output replay and observed input growth together.
                # Do not discard a funded repair merely because its maximum
                # configured output would consume the reserved handoff. The
                # snapshot already excludes downstream floors/reservations.
                # If even one output token cannot fit, leave rejection to the
                # unchanged atomic admission rather than grant extra budget.
                low, high = 1, output
                while low < high:
                    proposed = (low + high + 1) // 2
                    if estimated_input + self._tail_tokens(estimated_input, proposed, future, tail_output) <= tokens_left:
                        low = proposed
                    else:
                        high = proposed - 1
                output = low
        if self.deadline is not None:
            end = min(end, self.deadline)
            if not self.handoff_only and self.handoff_start is not None:
                # Phase boundaries select the next tool view; they cannot cut
                # an already admitted healthy stream short. Still reserve a
                # declaration interval inside the unchanged owner deadline.
                # Once FINALIZE begins, repeated failures and checks all share
                # this same fixed floor; they cannot erode its promised time.
                end = min(end, self.handoff_start)
        return PhaseRequest(self.phase, future,
                            self._tail_tokens(estimated_input, output, future, tail_output),
                            output, end, message, self.handoff_only, self.conditional_handoff)

    def admitted(self) -> None:
        self.calls += 1
        if self.phase is ImplementationPhase.FINALIZE:
            self.final_calls += 1
            self._handoff_admitted = self._handoff_admitted or self.handoff_only
            self._handoff_rejected = False

    def observe_handoff_result(self, *, accepted: bool) -> None:
        """A real bound submission rejection can use the remaining allowance.

        Keep the admitted latch: subsequent requests remain submit-only. This
        records delivery state, without renewing calls, tokens or owner time.
        """
        if type(accepted) is not bool:
            raise ValueError("handoff observation must be a host boolean")
        if self._handoff_admitted:
            self._handoff_rejected = not accepted

    @property
    def exhausted(self) -> bool:
        if not self.bounded:
            return False
        return self.calls >= self.call_limit or (self._handoff_admitted and not self._handoff_rejected)

    def timeout(self, *, now: float | None = None) -> None:
        if not self.bounded:
            return
        # An expired investigation request does not reopen investigation.
        if self.phase is ImplementationPhase.INVESTIGATE:
            self._advance(ImplementationPhase.IMPLEMENT)
        elif self.phase is ImplementationPhase.IMPLEMENT:
            # A response timeout does not mean its entire phase is over. The
            # next request rechecks all existing call/token/time ceilings.
            if now is not None and now >= self.final_start:
                self._advance(ImplementationPhase.FINALIZE)
        else:
            self._advance(ImplementationPhase.FINALIZE)

    def allowed_name(self, name: str) -> bool:
        if not self.bounded:
            return True
        if self.phase is ImplementationPhase.INVESTIGATE:
            return True
        if self.phase is ImplementationPhase.IMPLEMENT:
            return name in _IMPLEMENT_TOOLS
        if self.handoff_only:
            return self._native_final_only and name == "executor_submit"
        if self.conditional_handoff:
            return name in {"edit_file", "write_file", "executor_submit"} or (
                self.allow_test_completion and name == "executor_test")
        if (self._verification_failed or self.edit_repair_pending or self.allow_checked_source_completion) and name in {"edit_file", "write_file"}:
            return True
        if self.edit_repair_pending and name == "read_file":
            return True
        if self._native_final_only:
            return name in {"executor_submit", "executor_check"} or self.allow_test_completion and name == "executor_test"
        return name in _FINAL_TOOLS or self.allow_test_completion and name == "executor_test"

    def tool_view(self, registry: Any) -> Any:
        """A per-request projection, without mutating an adapter or its cache."""
        if not self.bounded:
            # Returning the supplied registry cannot grant tools absent from
            # the current scope. Its schemas and execution guards stay intact.
            return registry
        self.require_structured_handoff = any(
            tool.name == "executor_submit" for tool in registry.list()
        )
        self._native_final_only = self.phase is ImplementationPhase.FINALIZE and self.require_structured_handoff
        filtered = registry.filtered(lambda tool: self.allowed_name(tool.name))
        definitions = []
        for tool in filtered.list():
            if tool.name == "read_file":
                schema = copy.deepcopy(tool.input_schema)
                if isinstance(schema.get("properties", {}).get("limit"), dict):
                    schema["properties"]["limit"]["maximum"] = 8000
                tool = replace(tool, input_schema=schema, description=tool.description + " Current execution-phase cap: 8000 characters.")
            definitions.append(tool)

        class RequestToolView:
            def list(self):
                return list(definitions)

        return RequestToolView()

    def admit_batch(self, calls: list[dict[str, Any]], *, cwd: str | None = None,
                    permissions: Any = None) -> dict[str, str]:
        """Validate execution as well as provider-visible tool selection."""
        if self.allow_conditional_handoff:
            submissions = [i for i, call in enumerate(calls)
                           if call.get("toolName") == "executor_submit"]
            if len(submissions) > 1 or (submissions and any(
                    call.get("toolName") in _RESULT_DEPENDENCIES
                    for call in calls[submissions[0] + 1:])):
                # No declared success may precede changes the same response
                # promised to make. Reject the malformed batch before any
                # write rather than accepting an older checked candidate.
                return {call["id"]: "error[implementation_handoff_order]: "
                        "Use at most one executor_submit, after every proposed write/check. "
                        "No tool in this batch was executed." for call in calls}
        if not self.bounded:
            return {}
        rejected: dict[str, str] = {}
        reads = 0
        for call in calls:
            name = call.get("toolName", "")
            args = call.get("input")
            reason = None
            if not self.allowed_name(name):
                reason = "tool is disabled in the current execution phase"
            elif name == "run_command" and (
                not isinstance(args, dict) or project_command_verification(args, passed=True) is None
                or any(flag in args.get("args", []) for flag in ("--fix", "--write", "-w"))
            ):
                reason = "only direct verification commands are available; use file tools for changes and reads"
            elif self.phase is ImplementationPhase.FINALIZE and name == "task" and (
                not isinstance(args, dict) or args.get("action") != "poll"
            ):
                reason = "finalization may poll existing work but cannot start a replacement workflow"
            elif name in SOURCE_READ_TOOLS:
                reads += 1
                if reads > 4 or self.phase is ImplementationPhase.INVESTIGATE and self.investigation_read_calls >= 24:
                    reason = "inspection batch limit reached"
                elif name == "read_file" and isinstance(args, dict) and (
                    type(args.get("limit", 8000)) is not int or args.get("limit", 8000) > 8000
                ):
                    reason = "phase reads are limited to 8000 body characters; request a focused line range"
                elif self.phase is ImplementationPhase.IMPLEMENT and name == "grep_files" and isinstance(args, dict) and (
                    not isinstance(args.get("path"), str) or args.get("path") in {".", "", "minicode", "tests"}
                ):
                    target = _exact_include_target(args, cwd=cwd, permissions=permissions)
                    if target is None:
                        reason = "implementation search must target an identified source or test file"
                    else:
                        # Grep includes match basenames recursively. Rewrite the
                        # executed path, rather than merely exempting that scan.
                        call["input"] = {**args, "path": target}
                if reason is None:
                    self.investigation_read_calls += int(self.phase is ImplementationPhase.INVESTIGATE)
            if reason is not None:
                rejected[call["id"]] = f"error[implementation_phase_limit]: {reason}. Submit a blocker if this prevents a supported implementation."
        if reads and len(rejected) < len(calls) and self.phase is ImplementationPhase.IMPLEMENT:
            self.focused_read_batches += 1
        if (self.phase is ImplementationPhase.FINALIZE and not self.handoff_only
                and not self.allow_conditional_handoff
                and any(call.get("toolName") in _RESULT_DEPENDENCIES
                        and call["id"] not in rejected for call in calls)):
            # Tool calls in a single model response cannot know one another's
            # results. Execute the dependency, then use the existing next slot
            # for a declaration based on its actual feedback. The last slot is
            # exempt: its dependencies were already rejected above.
            for call in calls:
                if call.get("toolName") == "executor_submit":
                    rejected[call["id"]] = (
                        "error[implementation_handoff_pending_feedback]: "
                        "This batch changes or checks the candidate. Its actual feedback "
                        "must be received before submitting in the next reserved request."
                    )
        # Invalid arguments or an exhausted read quota are individual tool
        # rejections, not evidence that the implementation budget has elapsed.
        # Keep edits available until the actual call/token/time boundary.
        return rejected

    def observe_change(self, changed: bool) -> None:
        if changed:
            self.implementation_started = True
            self._advance(ImplementationPhase.IMPLEMENT)

    def observe_verification(self, *, failed: bool) -> None:
        """Receive only the host's actual check conclusion, never model claims."""
        if type(failed) is not bool:
            raise ValueError("verification observation must be a host boolean")
        self._verification_failed = failed

    def observe_edit(self, *, path: str, anchor_failed: bool, changed: bool) -> None:
        """Host tool feedback only; successful writes retire their own error."""
        if anchor_failed:
            self._failed_edit_paths.add(path)
        elif changed:
            self._failed_edit_paths.discard(path)
