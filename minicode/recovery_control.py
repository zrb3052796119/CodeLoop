"""Turn-local recovery guard for denied actions and failed-tool loops.

The permission layer remains the execution authority.  This module only
prevents the model from spending more tool calls on an action that the same
Turn has already proved to be denied, and enforces a smaller local failure
budget than the Turn's disaster ceiling.  A direct invocation is deliberately
a different shape from a shell-wrapped invocation so legitimate recovery stays
available.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Mapping


_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "powershell",
    "pwsh",
    "sh",
    "zsh",
}
_TEST_TARGET_RE = re.compile(
    r"(?<![a-z0-9_])tests(?:[./][a-z0-9_.*:\-\[\]]+)+",
    re.IGNORECASE,
)
_PERMISSION_DENIED_PREFIX = "error[permission_denied]:"


@dataclass(frozen=True, slots=True)
class SuppressedAction:
    fingerprint: str
    message: str


@dataclass(frozen=True, slots=True)
class RecoveryStop:
    reason_code: str
    message: str
    permission_denials: int
    suppressed_attempts: int
    consecutive_failed_steps: int
    user_action_required: bool


def _stable_projection(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _command_fingerprint(tool_input: Mapping[str, Any]) -> str:
    command = str(tool_input.get("command") or "").strip()
    args = tool_input.get("args")
    normalized_args = [str(value) for value in args] if isinstance(args, list) else []
    parsed_command: list[str] = []
    if not normalized_args and command:
        try:
            parsed_command = shlex.split(command)
        except ValueError:
            parsed_command = []
    executable = os.path.basename(
        parsed_command[0] if parsed_command else command
    ).casefold()
    command_args = normalized_args or parsed_command[1:]
    control_flags = {"-c", "-lc", "-command", "/c", "/command"}
    raw_shell_snippet = not normalized_args and any(
        character in command for character in "|&;<>()$`"
    )
    explicit_shell_wrapper = executable in _SHELL_EXECUTABLES and any(
        value.casefold() in control_flags for value in command_args
    )
    if not explicit_shell_wrapper and not raw_shell_snippet:
        return "run_command:direct:" + _stable_projection(tool_input)

    if raw_shell_snippet and not explicit_shell_wrapper:
        payload = command
    else:
        control_index = next(
            (
                index
                for index, value in enumerate(command_args)
                if value.casefold() in control_flags
            ),
            -1,
        )
        payload = " ".join(command_args[control_index + 1 :]).strip()
    for _ in range(3):
        try:
            tokens = shlex.split(payload)
        except ValueError:
            break
        if (
            len(tokens) >= 3
            and os.path.basename(tokens[0]).casefold() in _SHELL_EXECUTABLES
            and tokens[1].casefold() in control_flags
        ):
            payload = " ".join(tokens[2:]).strip()
            continue
        break
    payload = re.sub(
        r"^\s*cd\s+(?:\"[^\"]*\"|'[^']*'|[^;&]+?)\s*(?:&&|;)\s*",
        "",
        payload,
        flags=re.IGNORECASE,
    )
    payload = re.sub(r"\s+", " ", payload).strip().casefold()
    runner = (
        "unittest"
        if "unittest" in payload
        else "pytest"
        if "pytest" in payload
        else "shell"
    )
    targets = sorted(set(match.casefold() for match in _TEST_TARGET_RE.findall(payload)))
    if targets:
        return f"run_command:shell-wrapper:{runner}:{'|'.join(targets)}"
    return "run_command:shell-wrapper:" + payload


def action_fingerprint(call: Mapping[str, Any]) -> str:
    """Project a tool request into a stable denial-equivalence class."""
    tool_name = str(call.get("toolName") or "")
    tool_input = call.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_name == "run_command":
        return _command_fingerprint(tool_input)
    if tool_name in {"edit_file", "modify_file", "patch_file", "write_file"}:
        return "file_edit:" + str(tool_input.get("path") or "")
    return f"{tool_name}:{_stable_projection(tool_input)}"


class RecoveryGuard:
    """Suppress denied equivalents and bound failed recovery for one Turn."""

    def __init__(self) -> None:
        self._denied: Counter[str] = Counter()
        self._suppressed: Counter[str] = Counter()
        self._consecutive_failed_steps = 0
        self._recent_failed_steps: deque[bool] = deque(maxlen=10)
        self._strategy_switch_issued = False
        self._pending_stop: RecoveryStop | None = None

    def suppression_for(self, call: Mapping[str, Any]) -> SuppressedAction | None:
        fingerprint = action_fingerprint(call)
        if self._denied[fingerprint] < 1:
            return None
        self._suppressed[fingerprint] += 1
        tool_name = str(call.get("toolName") or "tool")
        if tool_name == "run_command":
            guidance = (
                "error[repeated_blocked_action]: Equivalent denied action "
                "suppressed before execution. Do not "
                "add bash/zsh wrappers, pipes, redirections, cd, sudo, or other "
                "privilege escalation. Use one materially different direct "
                "run_command request with an explicit executable, args, and an "
                "in-workspace cwd."
            )
        else:
            guidance = (
                "error[repeated_blocked_action]: Equivalent denied action "
                "suppressed before execution. Use a "
                "materially different path or an already-authorized tool action; "
                "do not repeat the denied target through another wrapper."
            )
        return SuppressedAction(fingerprint=fingerprint, message=guidance)

    def observe(self, call: Mapping[str, Any], *, ok: bool, output: str) -> None:
        if not ok and output.casefold().startswith(_PERMISSION_DENIED_PREFIX):
            self._denied[action_fingerprint(call)] += 1

    def denial_count(self, call: Mapping[str, Any]) -> int:
        """Return prior permission denials for this equivalence class."""
        return self._denied[action_fingerprint(call)]

    @property
    def consecutive_failed_steps(self) -> int:
        return self._consecutive_failed_steps

    def complete_step(self, executed_outcomes: list[bool]) -> str | None:
        """Observe one model step and optionally require a strategy switch.

        Suppressed calls are intentionally excluded.  Permission repetition has
        its own tighter circuit, while this budget covers distinct real tool
        failures.  Any real success is treated as progress and resets the
        consecutive-failure budget.
        """
        if not executed_outcomes or self._pending_stop is not None:
            return None
        failed_step = not any(executed_outcomes)
        self._recent_failed_steps.append(failed_step)
        if not failed_step:
            self._consecutive_failed_steps = 0
            self._strategy_switch_issued = False
            return None

        self._consecutive_failed_steps += 1
        if self._consecutive_failed_steps >= 5:
            self._pending_stop = self._general_failure_stop(
                reason_code="consecutive_tool_failures"
            )
            return None
        if (
            len(self._recent_failed_steps) == self._recent_failed_steps.maxlen
            and sum(self._recent_failed_steps) >= 8
        ):
            self._pending_stop = self._general_failure_stop(
                reason_code="failure_window_exhausted"
            )
            return None
        if self._consecutive_failed_steps == 3 and not self._strategy_switch_issued:
            self._strategy_switch_issued = True
            return (
                "error[strategy_switch_required]: Three consecutive tool steps "
                "failed. Do not retry the same approach. Re-observe the available "
                "evidence and choose materially different tools or inputs. Two "
                "more failed steps will open the recovery circuit."
            )
        return None

    def _general_failure_stop(self, *, reason_code: str) -> RecoveryStop:
        return RecoveryStop(
            reason_code=reason_code,
            message=(
                "error[recovery_exhausted]: Recovery circuit opened after the "
                "strategy switch failed to produce progress. Stop retrying and "
                "report the blocker with the evidence already observed."
            ),
            permission_denials=sum(self._denied.values()),
            suppressed_attempts=sum(self._suppressed.values()),
            consecutive_failed_steps=self._consecutive_failed_steps,
            user_action_required=True,
        )

    def stop_decision(self) -> RecoveryStop | None:
        """Return a pending general stop or an exhausted denial circuit."""
        if self._pending_stop is not None:
            return self._pending_stop
        if not self._suppressed:
            return None
        suppressed_attempts = max(self._suppressed.values())
        if suppressed_attempts < 2:
            return None
        return RecoveryStop(
            reason_code="repeated_denied_action",
            message=(
                "error[recovery_exhausted]: Recovery circuit opened after "
                "repeated equivalent denied actions. No further equivalent "
                "tool requests were executed. Use a materially different "
                "authorized approach or ask the user to resolve the blocker."
            ),
            permission_denials=sum(self._denied.values()),
            suppressed_attempts=suppressed_attempts,
            consecutive_failed_steps=self._consecutive_failed_steps,
            user_action_required=True,
        )
