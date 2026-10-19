"""Decide when the parent should delegate a coding turn.

A second agent earns its cost when the change spans more than one source
file, or when the user asked for a separate implementer and reviewer. A
single-file edit is faster and no worse when the parent does it. Routing
that is off, and a host-owned implementation spec, stay with the parent:
there is no separate child model to hand the work to, or the parent loop
already owns the review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_CODE_PATH = re.compile(
    r"(?<![\w./-])((?:[\w.-]{1,80}/){0,8}[\w.-]{1,80}\."
    r"(?:py|ts|tsx|js|jsx|go|rs|java|rb|c|cc|cpp|h|hpp))\b"
)
_CHANGE = re.compile(
    r"\b(implement|fix|add|change|update|modify|write|refactor|create|edit)\b",
    re.IGNORECASE,
)
_BROAD_SURVEY = re.compile(
    r"\b(codebase|repository|across the (?:repo|project|codebase)|"
    r"all modules|whole project)\b",
    re.IGNORECASE,
)
_EXPLICIT_COLLABORATION = re.compile(
    r"\b(sub-?agents?|separate reviewer|agent_type=.workflow)\b",
    re.IGNORECASE,
)
_TRIVIAL = re.compile(
    r"\b(typo|one function|single function|single file|one file)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegationDecision:
    mode: str
    write_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ParentDelegationGate:
    """One coaching result before the parent edits a collaborative change."""

    pending: bool
    message: str


def source_paths(task_text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_CODE_PATH.findall(task_text or "")))


def decide_delegation(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
) -> DelegationDecision:
    """Return solo, workflow, or explore for one user request."""

    text = task_text or ""
    paths = source_paths(text)
    if implementation_owned or not routing_enabled:
        return DelegationDecision("solo", paths, "parent owns this turn")
    if _TRIVIAL.search(text):
        return DelegationDecision("solo", paths, "trivial edit")
    if _EXPLICIT_COLLABORATION.search(text) and _CHANGE.search(text):
        return DelegationDecision("workflow", paths, "explicit collaboration")
    if len(paths) >= 2 and _CHANGE.search(text):
        return DelegationDecision("workflow", paths, "multiple source files")
    if _BROAD_SURVEY.search(text) and not _CHANGE.search(text):
        return DelegationDecision("explore", paths, "broad read-only survey")
    if _CHANGE.search(text) or len(paths) == 1:
        return DelegationDecision("solo", paths, "local change")
    return DelegationDecision("solo", paths, "local request")


def delegation_prompt_note(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
) -> str:
    """A per-turn prompt line. Empty when the static guide is enough."""

    if implementation_owned:
        return (
            "This turn is a host-owned implementation. Implement and verify it "
            "yourself. Do not call task."
        )
    decision = decide_delegation(
        task_text,
        routing_enabled=routing_enabled,
        implementation_owned=False,
    )
    if decision.mode == "workflow":
        shown = ", ".join(decision.write_paths) or "the files named in the request"
        return (
            "This request is collaborative work "
            f"({shown}). Delegate it with task(agent_type='workflow', "
            "write_paths=[...], verification_commands=[...]) before editing. "
            "Do not also call plan or explore for the same change."
        )
    if decision.mode == "explore":
        return (
            "This request is a broad read-only survey. Use "
            "task(agent_type='explore') and do not start an implementation workflow."
        )
    if decision.reason == "local change":
        return (
            "This request is a small local change. Implement and verify it "
            "yourself. Do not start a workflow."
        )
    return ""


def delegation_coach_message(decision: DelegationDecision) -> str:
    path_arg = json.dumps(list(decision.write_paths)) if decision.write_paths else "[...]"
    if len(decision.write_paths) >= 2:
        lead = (
            "This change covers multiple source files ("
            + ", ".join(decision.write_paths)
            + "). "
        )
    elif decision.write_paths:
        lead = (
            "This change asks for a separate implementer ("
            + ", ".join(decision.write_paths)
            + "). "
        )
    else:
        lead = "This change asks for a separate implementer. "
    return (
        lead
        + "With sub-agent routing enabled, delegate it before editing:\n"
        'task(agent_type="workflow", '
        'prompt="<objective, observed failure, constraints, acceptance>", '
        f"write_paths={path_arg}, "
        'verification_commands=[["<the command that checks this change>"]])\n'
        "Do not also start a plan or explore agent for this change. "
        "A later edit is allowed if that workflow cannot run."
    )


def parent_delegation_gate(
    task_text: str,
    *,
    routing_enabled: bool,
    implementation_owned: bool,
    agent_depth: int,
) -> ParentDelegationGate:
    """Coach the parent once. Nested workers and owned specs are not coached."""

    if agent_depth != 0 or implementation_owned:
        return ParentDelegationGate(False, "")
    decision = decide_delegation(
        task_text,
        routing_enabled=routing_enabled,
        implementation_owned=False,
    )
    if decision.mode != "workflow":
        return ParentDelegationGate(False, "")
    return ParentDelegationGate(True, delegation_coach_message(decision))


def task_text_from_messages(messages: list) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""
