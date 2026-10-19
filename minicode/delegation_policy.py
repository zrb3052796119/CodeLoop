"""Decide when the parent should delegate a coding turn.

A second agent earns its cost when the change spans more than one source
file, when one file is a multi-step redesign that a separate reviewer can
check, or when the user asked for that split. A read-only question stays
with the parent even if it names several files or mentions editing. A
one-function fix stays with the parent too. Routing that is off, and a
host-owned implementation spec, stay with the parent: there is no separate
child model to hand the work to, or the parent loop already owns the review.

The cues are lexical. They are not a parse of the task.
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
    r"\b(implement|fix|add|change|update|modify|write|refactor|create|edit|"
    r"redesign|rewrite|overhaul|re-architect|rearchitect)\b",
    re.IGNORECASE,
)
_REDESIGN = re.compile(
    r"\b(redesign|rewrite|overhaul|re-architect|rearchitect)\b",
    re.IGNORECASE,
)
# A second behavior, not the redesign verb alone. "Rewrite hold()" stays solo.
_REDESIGN_SCOPE = re.compile(
    r"\b(then|invariants?|edge cases?|state machine|multiple steps|several steps)\b",
    re.IGNORECASE,
)
_HOW_TO_CHANGE = re.compile(
    r"\bhow (?:do|can|should|would) (?:i|we)\s+"
    r"(?:(?!implement\b|fix\b|add\b|change\b|update\b|modify\b|write\b|"
    r"refactor\b|create\b|edit\b|redesign\b|rewrite\b|overhaul\b)\w+\s+){0,6}"
    r"(implement|fix|add|change|update|modify|write|refactor|create|edit|"
    r"redesign|rewrite|overhaul)\b",
    re.IGNORECASE,
)
_LEADING_CHANGE = re.compile(
    r"^\s*(?:please\s+)?(?:implement|fix|add|change|update|modify|write|"
    r"refactor|create|edit|redesign|rewrite|overhaul)\b",
    re.IGNORECASE,
)
_READONLY_CUE = re.compile(
    r"("
    r"\b(?:explain|describe|compare|summarize)\b"
    r"|\bwhat (?:do|does|is|are)\b"
    r"|\bhow does\b"
    r"|\bwhy does\b"
    r"|\btell me\b"
    r"|don't\s+(?:edit|change|modify|write|touch|rewrite)"
    r"|do not\s+(?:edit|change|modify|write|touch|rewrite)"
    r"|without\s+(?:editing|changing|modifying)"
    r"|read-?only"
    r"|just\s+(?:explain|tell|show|read|answer)"
    r"|no changes"
    r")",
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
    r"\b(typo|one function|single function|single file|one file|docstring)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DelegationDecision:
    mode: str
    write_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ParentDelegationGate:
    """Hold parent writes until a workflow call for this change succeeds."""

    pending: bool
    message: str
    write_paths: tuple[str, ...] = ()


_PARENT_WRITE_TOOLS = frozenset({
    "edit_file",
    "write_file",
    "patch_file",
    "modify_file",
    "executor_test",
})
_SHELLS = frozenset({"bash", "sh", "node", "powershell", "pwsh", "cmd"})
_PACKAGE_MUTATORS = frozenset({
    "pip", "pip3", "npm", "make", "cmake", "cargo", "go", "dotnet",
})
_READONLY_GIT = frozenset({"status", "diff", "log", "show", "rev-parse"})


def source_paths(task_text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_CODE_PATH.findall(task_text or "")))


def _is_readonly(text: str) -> bool:
    """A question or an explanation, including one that mentions editing."""

    if _HOW_TO_CHANGE.search(text) or _LEADING_CHANGE.search(text):
        return False
    if text.rstrip().endswith("?"):
        return True
    return _READONLY_CUE.search(text) is not None


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
    if _is_readonly(text):
        if _BROAD_SURVEY.search(text):
            return DelegationDecision("explore", paths, "broad read-only survey")
        return DelegationDecision("solo", paths, "read-only question")
    if _EXPLICIT_COLLABORATION.search(text) and _CHANGE.search(text):
        return DelegationDecision("workflow", paths, "explicit collaboration")
    if paths and _REDESIGN.search(text) and _REDESIGN_SCOPE.search(text):
        return DelegationDecision("workflow", paths, "single-file redesign")
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
    if decision.mode == "workflow" and decision.reason == "single-file redesign":
        shown = ", ".join(decision.write_paths) or "the file named in the request"
        return (
            "This request is a single-file redesign "
            f"({shown}). Delegate it with task(agent_type='workflow', "
            "write_paths=[...], verification_commands=[...]) so a separate "
            "reviewer checks the behavior. Do not also call plan or explore "
            "for the same change."
        )
    if decision.mode == "workflow":
        shown = ", ".join(decision.write_paths) or "the files named in the request"
        return (
            "This request is collaborative work "
            f"({shown}). Delegate it with task(agent_type='workflow', "
            "write_paths=[...], verification_commands=[...]) before editing. "
            "Do not also call plan or explore for the same change."
        )
    if decision.reason == "read-only question":
        return (
            "This request is a read-only question. Answer it from the named "
            "files. Do not start a workflow."
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
    if decision.reason == "single-file redesign":
        shown = ", ".join(decision.write_paths) or "the file in the request"
        lead = f"This single-file redesign benefits from a separate reviewer ({shown}). "
    elif len(decision.write_paths) >= 2:
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
        "edit_file, write_file, and shell writes stay blocked until this "
        "workflow call succeeds."
    )


def _path_key(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _union_paths(existing: object, required: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            key = _path_key(cleaned)
            if cleaned and key not in seen:
                paths.append(cleaned)
                seen.add(key)
    for path in required:
        key = _path_key(path)
        if key and key not in seen:
            paths.append(path)
            seen.add(key)
    return paths


def complete_workflow_write_paths(tool_input: dict, required: tuple[str, ...]) -> None:
    """Authorize every source file the user named, without dropping the model's paths.

    An explicit workflow_request is updated in place. Convenience write_paths
    are filled when that request is absent. The two shapes stay mutually exclusive.
    """

    if tool_input.get("agent_type") != "workflow" or not required:
        return
    request = tool_input.get("workflow_request")
    if isinstance(request, dict):
        scope = request.get("scope")
        if not isinstance(scope, dict):
            scope = {}
            request["scope"] = scope
        scope["writePaths"] = _union_paths(scope.get("writePaths"), required)
        return
    tool_input["write_paths"] = _union_paths(tool_input.get("write_paths"), required)


def handoff_blocks_tool(tool_name: str, tool_input: object) -> bool:
    """True when this call could write source before the workflow handoff."""

    if tool_name in _PARENT_WRITE_TOOLS:
        return True
    if tool_name != "run_command" or not isinstance(tool_input, dict):
        return False
    command = str(tool_input.get("command") or "").strip()
    raw_args = tool_input.get("args")
    args = raw_args if isinstance(raw_args, list) else []
    blob = " ".join([command, *[str(arg) for arg in args]])
    if ">" in blob:
        return True
    if command in _SHELLS or command in _PACKAGE_MUTATORS:
        return True
    if command in {"python", "python3"}:
        return not (
            len(args) >= 2
            and str(args[0]) == "-m"
            and str(args[1]) in {"pytest", "unittest"}
        )
    if command == "git":
        subcommand = str(args[0]) if args else ""
        return subcommand not in _READONLY_GIT
    if command == "sed" and any(
        str(arg) == "-i" or str(arg).startswith("-i") for arg in args
    ):
        return True
    return False


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
    return ParentDelegationGate(
        True, delegation_coach_message(decision), decision.write_paths,
    )


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
